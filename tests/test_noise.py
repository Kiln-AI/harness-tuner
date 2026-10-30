"""The run-to-run noise check (`kiln_harness_tuner.noise`,
`--noise-check N`): the cache bypass, the spend path, the judge copy, and the
comparison with the detectability floor. Offline: FakeKiln only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_ctx, make_plan
from fake_dispatcher import ok
from test_orchestrator import seed_active_eval, seed_baseline
from test_speed_objective import _rc

from kiln_harness_tuner.noise import (
    NOISE_NAME_PREFIX,
    archive_eval_config,
    binomial_two_sided_p,
    noise_adjusted_wins,
    run_noise_check,
)
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.schemas import SPEND_LANE_LABELS, lane_for_source

JUDGE = {
    "id": "ec_1",
    "name": "Judge",
    "config_type": "v2",
    "properties": {"type": "llm_judge", "prompt_template": "grade it"},
}


def _row(item: str, accuracy: float, scored: str | None = None, **usage) -> dict:
    return {
        "dataset_id": item,
        "scores": {"accuracy": accuracy},
        "scored_run_id": scored or f"tr_{item}",
        "output": {"output": f"answer {item}"},
        "task_run_usage": {"cost": usage.get("task", 0.01)},
        "eval_usage": {"cost": usage.get("judge", 0.002)},
    }


BASE = [0.0, 0.0, 1.0, 1.0, 1.0, 1.0]


def _ctx(n: int = 6, config: dict | None = None):
    ctx = make_ctx(gate_agent="auto")
    ctx.noise_check_items = n
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.kiln.run_configs = [_rc("rc_base")]
    ctx.kiln.eval_configs["ec_1"] = dict(config or JUDGE)
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        _row(f"t{i}", v) for i, v in enumerate(BASE)
    ]
    return ctx


def _clone_rows(ctx, values: list[float]) -> None:
    # The clone drives again: new task runs, new outputs.
    ctx.kiln.pending[("eval_1", "rc_probe_1", "train")] = [
        {**_row(f"t{i}", v, scored=f"clone_{i}"), "output": {"output": f"new {i}"}}
        for i, v in enumerate(values)
    ]


def _copy_rows(ctx, values: list[float], scored=lambda i: f"tr_t{i}") -> None:
    # The judge copy re-judges the baseline's own stored outputs.
    ctx.kiln.pending[("eval_1", "rc_base@ec_copy_1", "train")] = [
        _row(f"t{i}", v, scored=scored(i)) for i, v in enumerate(values)
    ]


def _jobs(ctx) -> list[dict]:
    return list(ctx.kiln._jobs.values())


async def test_the_redrive_is_a_clone_and_the_baseline_is_never_touched():
    ctx = _ctx()
    _clone_rows(ctx, [1.0, 0.0, 1.0, 0.0, 1.0, 1.0])  # 2 of 6 changed
    _copy_rows(ctx, BASE)
    result = await run_noise_check(ctx, "rc_base")
    # The cache bypass: a NEW run config with the baseline's properties.
    (clone,) = ctx.kiln.created_run_configs
    assert clone["id"] == result.clone_rc_id == "rc_probe_1"
    assert clone["name"].startswith(NOISE_NAME_PREFIX)
    assert clone["run_config_properties"] == _rc("rc_base")["run_config_properties"]
    # Stamped as the loop's own object, in the lineage under the baseline.
    assert clone["provenance"] == {
        "notes": clone["description"],
        "derived_from_ids": ["rc_base"],
        "origin": "agent",
    }
    assert "rc_probe_1" in ctx.policy_exempt_rcs
    # Jobs run on the clone and on the judge copy, narrowed to TRAIN items
    # the baseline has stored rows for — never on the baseline's own config.
    keys = {(j["key"], tuple(sorted(j["item_ids"]))) for j in _jobs(ctx)}
    items = tuple(f"t{i}" for i in range(6))
    assert keys == {
        (("eval_1", "rc_probe_1", "train"), items),
        (("eval_1", "rc_base@ec_copy_1", "train"), items),
    }
    # No run config edited, no eval config made the default, no val or test.
    assert not [
        c
        for c in ctx.kiln.calls
        if c[0] in ("PATCH", "PUT") or "set_current_eval_config" in c[1]
    ]
    assert ctx.kiln.evals["eval_1"]["current_config_id"] == "ec_1"
    assert all(key[2] == "train" for key in ctx.kiln.job_invocations)
    (row,) = result.rows
    assert (row.items, row.redrive_changed) == (6, 2)
    assert row.redrive_rate == pytest.approx(1 / 3)
    assert (row.rejudge_items, row.rejudge_changed) == (6, 0)


async def test_the_judge_copy_is_a_non_default_copy_of_the_default_config():
    ctx = _ctx()
    _clone_rows(ctx, BASE)
    _copy_rows(ctx, [1.0, 0.0, 1.0, 1.0, 1.0, 1.0])  # the judge flips one
    result = await run_noise_check(ctx, "rc_base")
    (copy,) = ctx.kiln.created_eval_configs
    assert copy["name"].startswith(NOISE_NAME_PREFIX)
    assert copy["config_type"] == "v2"
    assert copy["properties"] == JUDGE["properties"]
    assert copy["provenance"]["origin"] == "agent"
    assert copy["provenance"]["derived_from_ids"] == [JUDGE["id"]]
    (row,) = result.rows
    assert (row.rejudge_changed, row.rejudge_rate) == (1, pytest.approx(1 / 6))
    assert row.redrive_changed == 0


async def test_a_copy_that_drove_again_is_not_judge_noise():
    ctx = _ctx()
    _clone_rows(ctx, BASE)
    _copy_rows(ctx, [1.0] * 6, scored=lambda i: f"other_{i}")
    (row,) = (await run_noise_check(ctx, "rc_base")).rows
    assert row.rejudge_changed is None
    assert "not separable" in row.rejudge_note


@pytest.mark.parametrize(
    "config,note",
    [
        (
            {"id": "ec_1", "config_type": "g_eval", "properties": {"eval_steps": []}},
            "legacy judge config",
        ),
        (
            {"id": "ec_1", "config_type": "v2", "properties": {"type": "code_eval"}},
            "deterministic",
        ),
    ],
)
async def test_judge_noise_is_skipped_where_a_copy_cannot_measure_it(config, note):
    ctx = _ctx(config=config)
    _clone_rows(ctx, BASE)
    (row,) = (await run_noise_check(ctx, "rc_base")).rows
    assert ctx.kiln.created_eval_configs == []
    assert row.rejudge_changed is None and note in row.rejudge_note
    assert row.items == 6  # the re-drive still ran


async def test_the_estimate_is_printed_before_any_spend_and_the_spend_is_booked():
    ctx = _ctx()
    _clone_rows(ctx, BASE)
    _copy_rows(ctx, BASE)
    result = await run_noise_check(ctx, "rc_base")
    kinds = [k for k, _f in ctx.log.events]
    assert kinds.index("noise_estimate") < kinds.index("noise_clone")
    (estimate,) = [f for k, f in ctx.log.events if k == "noise_estimate"]
    # 6 drives at $0.01, and 6 judgments at $0.002 in each of the two passes.
    assert estimate["estimate_usd"] == pytest.approx(6 * 0.01 + 2 * 6 * 0.002)
    assert estimate["line"].startswith("noise check: 6 train item(s) re-driven")
    (spend,) = [f for k, f in ctx.log.events if k == "spend"]
    assert spend["source"] == "noise_check" and spend["lane"] == "provider"
    # The re-drive: 6 drives + 6 judgments; the re-judge: 6 judgments only
    # (its rows share the baseline's drive, which is not paid again).
    assert spend["cost_usd"] == pytest.approx(6 * 0.01 + 6 * 0.002 + 6 * 0.002)
    assert result.spend_usd == pytest.approx(spend["cost_usd"])
    assert lane_for_source("noise_check") == "provider"
    assert SPEND_LANE_LABELS[lane_for_source("noise_check")] == "Kiln"


async def test_an_estimate_past_the_run_budget_skips_the_check():
    ctx = _ctx()
    ctx.budget_usd = 0.01
    result = await run_noise_check(ctx, "rc_base")
    assert result.skipped is not None and "--budget-usd" in result.skipped
    assert ctx.kiln.created_run_configs == [] and ctx.kiln.job_invocations == []
    assert not [f for k, f in ctx.log.events if k == "spend"]
    (done,) = [f for k, f in ctx.log.events if k == "noise_check"]
    assert done["line"].startswith("noise check skipped: the estimate $")


async def test_the_sample_is_n_train_items_by_a_fixed_hash():
    ctx = _ctx(n=3)
    _clone_rows(ctx, BASE)
    _copy_rows(ctx, BASE)
    await run_noise_check(ctx, "rc_base")
    (first,) = [j for j in _jobs(ctx) if j["key"][1] == "rc_probe_1"]
    assert len(first["item_ids"]) == 3
    assert set(first["item_ids"]) <= {f"t{i}" for i in range(6)}
    again = _ctx(n=3)
    _clone_rows(again, BASE)
    _copy_rows(again, BASE)
    await run_noise_check(again, "rc_base")
    (second,) = [j for j in _jobs(again) if j["key"][1] == "rc_probe_1"]
    assert second["item_ids"] == first["item_ids"]


async def test_noise_large_for_the_room_warns_and_never_refuses():
    ctx = _ctx()
    _clone_rows(ctx, [1.0, 1.0, 0.0, 0.0, 1.0, 1.0])  # 4 of 6 changed
    _copy_rows(ctx, BASE)
    result = await run_noise_check(ctx, "rc_base")
    (row,) = result.rows
    assert row.train_room == 2 and row.noise_adjusted_wins is None
    (warning,) = result.warnings
    assert "67% of items change between two runs" in warning
    assert "even fixing all 2 item(s) with room is not enough" in warning
    lines = [f["line"] for k, f in ctx.log.events if k == "noise_warning"]
    assert lines == [f"⚠ noise: {warning}"]


def test_the_noise_adjusted_bar():
    assert binomial_two_sided_p(6, 0) == pytest.approx(0.03125)
    assert noise_adjusted_wins(0.0, 40, 40) == 6  # no noise: the floor
    # 20% of 40 items change: about 4 each way, so w wins must beat w + 4 vs 4.
    wins = noise_adjusted_wins(0.2, 40, 40)
    assert wins is not None and wins > 6
    assert binomial_two_sided_p(wins + 4, 4) < 0.05
    assert binomial_two_sided_p(wins - 1 + 4, 4) >= 0.05
    assert noise_adjusted_wins(0.2, 40, wins - 1) is None


async def test_the_planner_gets_the_noise_rows_as_train_orientation(tmp_path):
    ctx = _ctx()
    _clone_rows(ctx, [1.0, 0.0, 1.0, 1.0, 1.0, 1.0])
    _copy_rows(ctx, BASE)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    (row,) = plan_input.train_noise
    assert (row.eval_id, row.redrive_changed, row.baseline_rc_id) == (
        "eval_1",
        1,
        "rc_base",
    )


async def test_no_stored_rows_means_no_spend():
    ctx = _ctx()
    ctx.kiln.results[("eval_1", "rc_base", "train")] = []
    result = await run_noise_check(ctx, "rc_base")
    assert result.skipped == "no stored baseline train row to compare with"
    assert ctx.kiln.created_run_configs == [] and ctx.kiln.job_invocations == []


def test_the_judge_copy_leaves_the_project_after_use(tmp_path: Path):
    ctx = make_ctx()
    project = tmp_path / "proj"
    config_dir = project / "tasks" / "t1 - T" / "evals" / "e1 - E" / "configs" / "c9"
    config_dir.mkdir(parents=True)
    (config_dir.parent.parent / "eval.kiln").write_text(json.dumps({"id": "eval_1"}))
    (config_dir / "eval_config.kiln").write_text(json.dumps({"id": "ec_copy_1"}))
    (config_dir / "runs").mkdir()
    ctx.target = ctx.target.model_copy(update={"project_path": str(project)})
    ctx.quarantine_root = tmp_path / "quarantine"
    assert archive_eval_config(ctx, "eval_1", "ec_copy_1")
    assert not config_dir.exists()
    moved = list((tmp_path / "quarantine" / "archived_eval_configs").rglob("c9"))
    assert len(moved) == 1 and (moved[0] / "eval_config.kiln").is_file()
    # Another eval's config of the same id is never moved.
    assert not archive_eval_config(ctx, "eval_2", "ec_copy_1")
