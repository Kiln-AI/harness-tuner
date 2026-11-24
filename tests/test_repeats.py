"""The `repeat` item (`kiln_harness_tuner.repeats`): an existing run config
measured again on train items. The cache bypass, the sample, the rows, the
refusals, the budget rule, the plan limits, and where the rows go. Offline:
FakeKiln only."""

from __future__ import annotations

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import ok
from fake_kiln import make_eval, make_score
from pydantic import ValidationError
from test_orchestrator import seed_active_eval, seed_baseline
from test_speed_objective import _rc

from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.noise import NOISE_NAME_PREFIX, sample_items
from kiln_harness_tuner.orchestrator import PROBE_RC_PREFIX, orchestrate
from kiln_harness_tuner.repeats import (
    REPEAT_NAME_PREFIX,
    repeat_clones,
    repeat_rows,
    rows_for,
)
from kiln_harness_tuner.schemas import (
    MAX_REPEATS_PER_PLAN,
    SPEND_LANE_LABELS,
    BaselineRef,
    BatchPlan,
    RcBuildResult,
    RepeatItem,
    lane_for_source,
)

BASELINE = BaselineRef(rc_id="rc_base")
STORED = [0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
ITEMS = [f"t{i}" for i in range(6)]


def _row(item: str, accuracy: float, **usage) -> dict:
    return {
        "dataset_id": item,
        "scores": {"accuracy": accuracy},
        "scored_run_id": usage.get("scored") or f"tr_{item}",
        "task_run_usage": {"cost": usage.get("task", 0.01)},
        "eval_usage": {"cost": usage.get("judge", 0.002)},
    }


def _ctx(stored: list[float] | None = None):
    ctx = make_ctx(gate_agent="auto")
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.kiln.run_configs = [_rc("rc_base"), _rc("rc_arm")]
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        _row(f"t{i}", v) for i, v in enumerate(stored or STORED)
    ]
    return ctx


def _clone_rows(ctx, values: list[float], clone: str = "rc_probe_1", **kw) -> None:
    # The clone drives again: new task runs.
    ctx.kiln.pending[(kw.get("eval_id", "eval_1"), clone, "train")] = [
        _row(f"t{i}", v, scored=f"{clone}_{i}") for i, v in enumerate(values)
    ]


def _item(rc_id: str = "rc_base", item_id: str = "w2-1", **kw) -> RepeatItem:
    return RepeatItem(
        id=item_id,
        type="repeat",
        rc_id=rc_id,
        eval_ids=kw.pop("eval_ids", ["eval_1"]),
        reason="the arm's gain rests on two items",
        **kw,
    )


async def _run(ctx, item: RepeatItem, wave: int = 2):
    return await execute_plan_item(ctx, item, BASELINE, ["rc_base"], wave)


def _jobs(ctx) -> list[dict]:
    return list(ctx.kiln._jobs.values())


# ---- fresh drives and fresh judgments ----


async def test_a_repeat_measures_a_clone_and_never_the_run_config_itself():
    ctx = _ctx()
    _clone_rows(ctx, [1.0, 0.0, 1.0, 0.0, 1.0, 1.0])
    outcome = await _run(ctx, _item())
    # The cache bypass: a NEW run config with the same properties.
    (clone,) = ctx.kiln.created_run_configs
    assert clone["name"].startswith(REPEAT_NAME_PREFIX)
    assert clone["run_config_properties"] == _rc("rc_base")["run_config_properties"]
    assert clone["provenance"] == {
        "notes": clone["description"],
        "derived_from_ids": ["rc_base"],
        "origin": "agent",
    }
    # One job, on the clone, narrowed to the train items with a stored row.
    (job,) = _jobs(ctx)
    assert job["key"] == ("eval_1", "rc_probe_1", "train")
    assert sorted(job["item_ids"]) == ITEMS
    assert all(key[2] == "train" for key in ctx.kiln.job_invocations)
    # The run config itself: no job, no edit, its stored rows as they were.
    assert not [c for c in ctx.kiln.calls if c[0] in ("PATCH", "PUT")]
    assert [
        r["scores"]["accuracy"]
        for r in ctx.kiln.results[("eval_1", "rc_base", "train")]
    ] == STORED
    # Pure code, and never an arm.
    assert ctx.dispatch.calls == []
    assert ctx.kiln.memory_posts == []
    assert outcome.status == "completed"
    assert outcome.result is None and outcome.experiment_memory_id is None
    assert outcome.rc_id == "rc_base"
    assert outcome.repeat.clone_rc_id == "rc_probe_1"
    assert "rc_probe_1" in ctx.policy_exempt_rcs


async def test_the_row_counts_what_changed_and_which_way():
    ctx = _ctx()
    # t0 fail -> pass, t3 pass -> fail, the rest the same.
    _clone_rows(ctx, [1.0, 0.0, 1.0, 0.0, 1.0, 1.0])
    outcome = await _run(ctx, _item())
    (row,) = outcome.repeat.rows
    assert (row.item_id, row.wave, row.rc_id) == ("w2-1", 2, "rc_base")
    assert row.is_baseline and row.repeat_index == 1
    assert (row.eval_id, row.score_key, row.score_type) == (
        "eval_1",
        "accuracy",
        "pass_fail",
    )
    assert (row.items, row.changed, row.improved, row.worsened) == (6, 2, 1, 1)
    assert row.change_rate == pytest.approx(2 / 6)
    assert row.mean_abs_change is None  # a pass/fail score
    assert row.stored_score == pytest.approx(4 / 6)
    assert row.repeat_score == pytest.approx(4 / 6)
    (detail,) = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert detail == (
        "repeat w2-1: rc_base on eval_1/accuracy — 2 of 6 changed (33%: 1 worse,"
        " 1 better); stored 0.67, repeat 0.67"
    )


async def test_better_and_worse_follow_the_scores_good_direction():
    ctx = _ctx()
    ctx.kiln.evals["eval_1"] = make_eval(
        "eval_1",
        "ec_1",
        scores=[make_score("Accuracy", direction="lower_is_better")],
    )
    _clone_rows(ctx, [1.0, 0.0, 1.0, 1.0, 1.0, 1.0])  # t0: 0 -> 1, now worse
    (row,) = (await _run(ctx, _item())).repeat.rows
    assert (row.changed, row.improved, row.worsened) == (1, 0, 1)
    assert row.direction == "lower_is_better"


async def test_a_rating_score_changes_by_value():
    ctx = _ctx([3.0, 3.0, 4.0, 5.0])
    ctx.kiln.evals["eval_1"] = make_eval(
        "eval_1", "ec_1", scores=[make_score("Accuracy", type="five_star")]
    )
    _clone_rows(ctx, [3.0, 5.0, 2.0, 5.0])
    (row,) = (await _run(ctx, _item())).repeat.rows
    assert (row.items, row.changed, row.improved, row.worsened) == (4, 2, 1, 1)
    assert row.mean_abs_change == pytest.approx((0 + 2 + 2 + 0) / 4)
    assert (row.stored_score, row.repeat_score) == (3.75, 3.75)


# ---- the sample: train items, the same ones for every run config ----


async def test_max_items_takes_the_sample_of_the_noise_check():
    ctx = _ctx()
    _clone_rows(ctx, STORED)
    outcome = await _run(ctx, _item(max_items=3))
    (job,) = _jobs(ctx)
    assert job["item_ids"] == sample_items(ITEMS, 3)
    assert outcome.repeat.rows[0].items == 3


async def test_an_arm_and_the_baseline_are_repeated_on_the_same_items():
    ctx = _ctx()
    ctx.kiln.results[("eval_1", "rc_arm", "train")] = [
        _row(f"t{i}", 1.0) for i in range(6)
    ]
    _clone_rows(ctx, STORED, clone="rc_probe_1")
    _clone_rows(ctx, [1.0] * 6, clone="rc_probe_2")
    base = await _run(ctx, _item("rc_base", "w2-1", max_items=4))
    arm = await _run(ctx, _item("rc_arm", "w2-2", max_items=4))
    (base_row,) = base.repeat.rows
    (arm_row,) = arm.repeat.rows
    assert base_row.items_key == arm_row.items_key != ""
    assert base_row.is_baseline and not arm_row.is_baseline
    first, second = _jobs(ctx)
    assert first["item_ids"] == second["item_ids"]


async def test_an_item_with_no_stored_row_is_not_driven():
    ctx = _ctx()
    # The clone would answer for t9 too: the run config has no row for it.
    ctx.kiln.pending[("eval_1", "rc_probe_1", "train")] = [
        _row(f"t{i}", 1.0) for i in [*range(6), 9]
    ]
    await _run(ctx, _item())
    (job,) = _jobs(ctx)
    assert "t9" not in job["item_ids"]


async def test_the_split_is_train_and_nothing_else():
    with pytest.raises(ValidationError):
        RepeatItem(
            id="w2-1",
            type="repeat",
            rc_id="rc_base",
            eval_ids=["eval_1"],
            split="val",
            reason="r",
        )
    with pytest.raises(ValidationError):
        _item(max_items=0)
    with pytest.raises(ValidationError):
        _item(eval_ids=[])
    assert _item(eval_ids=["eval_1", "eval_1"]).eval_ids == ["eval_1"]


# ---- what a repeat costs ----


async def test_the_estimate_comes_before_any_spend_and_the_spend_is_a_repeat():
    ctx = _ctx()
    _clone_rows(ctx, STORED)
    outcome = await _run(ctx, _item())
    kinds = [k for k, _f in ctx.log.events]
    assert kinds.index("repeat_estimate") < kinds.index("repeat_clone")
    (estimate,) = [f for k, f in ctx.log.events if k == "repeat_estimate"]
    # 6 drives at $0.01 and 6 judgments at $0.002, from the stored rows.
    assert estimate["estimate_usd"] == pytest.approx(6 * 0.01 + 6 * 0.002)
    assert estimate["line"].startswith("repeat w2-1: 6 train item(s) driven again")
    (spend,) = [f for k, f in ctx.log.events if k == "spend"]
    assert (spend["source"], spend["lane"], spend["item_id"]) == (
        "repeat",
        "provider",
        "w2-1",
    )
    assert spend["cost_usd"] == pytest.approx(6 * 0.01 + 6 * 0.002)
    assert outcome.repeat.spend_usd == pytest.approx(spend["cost_usd"])
    assert outcome.repeat.estimate_usd == pytest.approx(estimate["estimate_usd"])
    assert lane_for_source("repeat") == "provider"
    assert SPEND_LANE_LABELS[lane_for_source("repeat")] == "API keys"


async def test_an_estimate_above_the_cap_ends_the_item_capped_and_spends_nothing():
    ctx = _ctx()
    # One candidate may take a quarter of what is left: $0.05 of $0.20. The
    # estimate is $0.072.
    ctx.budget_usd = 0.20
    outcome = await _run(ctx, _item())
    assert outcome.status == "capped"
    assert outcome.infra_error is None
    repeat = outcome.repeat
    assert repeat.over_budget and repeat.rows == []
    assert "the estimate $0.07 is above the most one repeat may cost, $0.05" in (
        repeat.skipped
    )
    assert "about 4 item(s) for each eval fit (`max_items`)" in repeat.skipped
    assert ctx.kiln.created_run_configs == [] and ctx.kiln.job_invocations == []
    assert not [f for k, f in ctx.log.events if k == "spend"]
    assert ctx.train_repeats == []
    # The size the reason names does fit.
    _clone_rows(ctx, STORED)
    again = await _run(ctx, _item(item_id="w3-1", max_items=4), wave=3)
    assert again.status == "completed"


async def test_a_repeat_holds_its_estimate_while_it_runs():
    ctx = _ctx()
    _clone_rows(ctx, STORED)
    held: list[float] = []
    judge_items = ctx.analyzer.judge_items

    async def watched(*args, **kwargs):
        held.append(ctx.budget_holds.total())
        return await judge_items(*args, **kwargs)

    ctx.analyzer.judge_items = watched
    await _run(ctx, _item())
    assert held == [pytest.approx(6 * 0.01 + 6 * 0.002)]
    assert ctx.budget_holds.total() == 0.0


# ---- what code refuses ----


@pytest.mark.parametrize(
    "prefix", [REPEAT_NAME_PREFIX, NOISE_NAME_PREFIX, PROBE_RC_PREFIX]
)
async def test_a_clone_or_a_probe_fork_is_never_repeated(prefix):
    ctx = _ctx()
    ctx.kiln.run_configs.append({**_rc("rc_made"), "name": f"{prefix}1 of rc_base"})
    ctx.kiln.results[("eval_1", "rc_made", "train")] = [_row("t0", 1.0)]
    outcome = await _run(ctx, _item("rc_made"))
    assert outcome.status == "failed_infra"
    assert "is a clone or a fork that code made" in outcome.infra_error.detail
    assert ctx.kiln.created_run_configs == [] and ctx.kiln.job_invocations == []


async def test_a_repeat_of_this_runs_own_clone_is_refused():
    ctx = _ctx()
    _clone_rows(ctx, STORED)
    first = await _run(ctx, _item())
    outcome = await _run(ctx, _item(first.repeat.clone_rc_id, "w3-1"), wave=3)
    assert outcome.status == "failed_infra"
    assert len(ctx.kiln.created_run_configs) == 1


async def test_an_unknown_run_config_is_refused():
    ctx = _ctx()
    outcome = await _run(ctx, _item("rc_missing"))
    assert outcome.status == "failed_infra"
    assert outcome.infra_error.detail == "repeat: the task has no run config rc_missing"
    assert ctx.kiln.created_run_configs == []


async def test_a_run_config_with_no_stored_train_row_is_refused():
    ctx = _ctx()
    outcome = await _run(ctx, _item("rc_arm"))
    assert outcome.status == "failed_infra"
    assert "rc_arm has no stored train row" in outcome.infra_error.detail
    assert "plan a `remeasure`" in outcome.infra_error.detail
    assert ctx.kiln.created_run_configs == [] and ctx.kiln.job_invocations == []
    assert not [f for k, f in ctx.log.events if k == "spend"]


async def test_a_run_config_outside_the_model_policy_is_refused():
    ctx = _ctx()
    ctx.kiln.run_configs.append(_rc("rc_other", provider="groq"))
    ctx.kiln.results[("eval_1", "rc_other", "train")] = [_row("t0", 1.0)]
    ctx.model_policy = ModelPolicy.from_json(
        {"allow": [{"provider": "openai", "model": "*"}]}
    )
    outcome = await _run(ctx, _item("rc_other"))
    assert outcome.status == "failed_infra"
    assert "outside the model policy" in outcome.infra_error.detail
    assert ctx.kiln.created_run_configs == []
    # A run config inside the policy is repeated, and its clone is measured.
    _clone_rows(ctx, STORED)
    assert (await _run(ctx, _item("rc_base", "w2-2"))).status == "completed"


async def test_a_run_config_on_a_model_the_preflight_flagged_is_refused():
    ctx = _ctx()
    ctx.flagged_models[("openai", "m")] = "the test call failed"
    outcome = await _run(ctx, _item())
    assert outcome.status == "failed_infra"
    assert "the model preflight flagged its model" in outcome.infra_error.detail
    assert ctx.kiln.created_run_configs == []
    # The baseline chain is exempt, as for any measurement.
    ctx.policy_exempt_rcs.add("rc_base")
    _clone_rows(ctx, STORED)
    assert (await _run(ctx, _item(item_id="w2-2"))).status == "completed"


async def test_an_experiment_never_builds_on_a_clone():
    ctx = _ctx()
    _clone_rows(ctx, STORED)
    clone = (await _run(ctx, _item())).repeat.clone_rc_id
    for field in ("parent_rc_id", "existing_rc_id"):
        calls, posts = len(ctx.dispatch.calls), len(ctx.kiln.memory_posts)
        item = fix_item()
        setattr(item.work, field, clone)
        outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 3)
        assert outcome.status == "failed_infra"
        assert "never a parent, a candidate" in outcome.infra_error.detail
        assert outcome.experiment_memory_id is None
        # Before any session and before the experiment record opens.
        assert len(ctx.dispatch.calls) == calls
        assert len(ctx.kiln.memory_posts) == posts


# ---- holes ----


async def test_an_eval_whose_job_fails_gets_a_note_and_the_others_their_rows():
    ctx = _ctx()
    ctx.kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    ctx.kiln.results[("eval_2", "rc_base", "train")] = [
        _row(f"t{i}", v) for i, v in enumerate(STORED)
    ]
    _clone_rows(ctx, STORED)
    outcome = await _run(ctx, _item(eval_ids=["eval_1", "eval_2", "eval_3"]))
    assert outcome.status == "completed"
    assert [r.eval_id for r in outcome.repeat.rows] == ["eval_1"]
    notes = outcome.repeat.notes
    assert any(n.startswith("eval_3: could not be read") for n in notes)
    # eval_2's job stored nothing: a note says so, and no row misleads the
    # planner.
    assert "eval_2: the repeat stored no row to compare" in notes
    assert not [r for r in ctx.train_repeats if r.eval_id == "eval_2"]


async def test_a_repeat_that_stores_nothing_fails_and_can_be_planned_again():
    ctx = _ctx()
    outcome = await _run(ctx, _item())  # no pending row for the clone
    assert outcome.status == "failed_infra"
    assert outcome.repeat.rows == [] and ctx.train_repeats == []


# ---- more than one repeat: where the rows are ----


async def test_each_repeat_has_its_own_clone_and_the_rows_read_back_by_item():
    ctx = _ctx()
    _clone_rows(ctx, [1.0, 0.0, 1.0, 1.0, 1.0, 1.0], clone="rc_probe_1")
    _clone_rows(ctx, [0.0, 0.0, 1.0, 1.0, 1.0, 0.0], clone="rc_probe_2")
    first = await _run(ctx, _item(item_id="w2-1"), wave=2)
    second = await _run(ctx, _item(item_id="w3-1"), wave=3)
    assert [first.repeat.rows[0].repeat_index, second.repeat.rows[0].repeat_index] == [
        1,
        2,
    ]
    assert repeat_clones(ctx, "rc_base", "eval_1") == ["rc_probe_1", "rc_probe_2"]
    assert repeat_clones(ctx, "rc_arm", "eval_1") == []
    one, two = await repeat_rows(ctx, "rc_base", "eval_1")
    assert sorted(one) == sorted(two) == ITEMS
    assert (one["t0"]["scores"]["accuracy"], two["t0"]["scores"]["accuracy"]) == (
        1.0,
        0.0,
    )
    # The second repeat is compared with the STORED rows, not with the first.
    assert second.repeat.rows[0].changed == 1


# ---- the plan's limits ----


def _plan(*items) -> BatchPlan:
    return BatchPlan(items=list(items), rejected_summary="r", open_questions=[])


def test_a_plan_carries_a_few_repeats_at_most():
    items = [_item(f"rc_{i}", f"w2-{i}") for i in range(MAX_REPEATS_PER_PLAN + 1)]
    assert len(_plan(*items[:MAX_REPEATS_PER_PLAN]).items) == MAX_REPEATS_PER_PLAN
    with pytest.raises(ValidationError, match="at most 4 repeat items"):
        _plan(*items)


def test_a_plan_repeats_a_run_config_on_an_eval_one_time():
    with pytest.raises(ValidationError, match="two repeat items name run config"):
        _plan(_item(item_id="w2-1"), _item(item_id="w2-2", eval_ids=["eval_1", "e2"]))
    # The same run config on two evals, and two run configs on one eval.
    _plan(_item(item_id="w2-1"), _item(item_id="w2-2", eval_ids=["e2"]))
    _plan(_item("rc_base", "w2-1"), _item("rc_arm", "w2-2"))


def test_a_probe_phase_plan_carries_no_repeat():
    with pytest.raises(ValidationError, match="carries ONLY probe"):
        BatchPlan(
            items=[_item()], rejected_summary="r", open_questions=[], probe_phase=True
        )


# ---- where the rows go ----


async def test_the_next_planner_gets_the_rows_and_the_noise_inputs_stay():
    ctx = _ctx()
    _clone_rows(ctx, [1.0, 0.0, 1.0, 0.0, 1.0, 1.0])
    ctx.dispatch.queue("batch_plan", ok(make_plan(_item(item_id="w1-1"))))
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    first, second = ctx.dispatch.calls_to("batch_plan")
    assert first.train_repeats == []
    (row,) = second.train_repeats
    assert (row.item_id, row.rc_id, row.changed, row.items) == (
        "w1-1",
        "rc_base",
        2,
        6,
    )
    # A repeat is not the noise check: it sets neither `train_noise` nor the
    # flip rate of the stop rules.
    assert second.train_noise == []
    assert second.default_stop_rules == first.default_stop_rules
    # No experiment record, no promotion.
    assert not [k for k, _f in ctx.log.events if k == "promotion"]
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []


async def test_an_analyst_gets_the_rows_on_its_arm_its_parent_and_the_baseline():
    ctx = _ctx()
    ctx.kiln.add_memory(
        "the client drops 429 retries", "task::t1", ["issue"], "issue_1"
    )
    # A repeat of the baseline on eval_1, and one on an eval the arm does
    # not target.
    ctx.kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    ctx.kiln.results[("eval_2", "rc_base", "train")] = [_row("t0", 1.0)]
    _clone_rows(ctx, [1.0, 0.0, 1.0, 1.0, 1.0, 1.0], clone="rc_probe_1")
    await _run(ctx, _item(item_id="w1-1", eval_ids=["eval_1"]), wave=1)
    ctx.kiln.pending[("eval_2", "rc_probe_2", "train")] = [_row("t0", 1.0)]
    await _run(ctx, _item(item_id="w1-2", eval_ids=["eval_2"]), wave=1)
    assert {r.eval_id for r in ctx.train_repeats} == {"eval_1", "eval_2"}

    ctx.kiln.run_configs.append(_rc("rc_new"))
    ctx.kiln.pending[("eval_1", "rc_new", "train")] = [
        _row(f"t{i}", 1.0) for i in range(6)
    ]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="n"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 2)
    assert outcome.status == "completed"
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    (row,) = analysis.train_repeats
    assert (row.rc_id, row.eval_id, row.changed) == ("rc_base", "eval_1", 1)
    # The table itself is the arm against the stored rows of its parent.
    assert all(r.baseline_rc_id == "rc_base" for r in analysis.bundle.per_eval)
    assert not [r for r in analysis.bundle.per_eval if "probe" in r.baseline_rc_id]


async def test_rows_for_keeps_the_named_run_configs_and_evals():
    ctx = _ctx()
    _clone_rows(ctx, STORED)
    rows = (await _run(ctx, _item())).repeat.rows
    assert rows_for(rows, {"rc_base"}, {"eval_1"}) == rows
    assert rows_for(rows, {"rc_arm"}, {"eval_1"}) == []
    assert rows_for(rows, {"rc_base"}, {"eval_2"}) == []


# ---- what a finished run shows ----


async def test_the_report_table_and_the_plain_lines_show_each_repeat():
    from io import StringIO

    from rich.console import Console

    from kiln_harness_tuner.report import (
        REPEATS_APPENDIX_HEADING,
        render_repeats_appendix,
    )
    from kiln_harness_tuner.status import RunStatusTracker
    from kiln_harness_tuner.ui import PlainUi, outcome_cell

    ctx = _ctx()
    _clone_rows(ctx, [1.0, 0.0, 1.0, 0.0, 1.0, 1.0])
    done = await _run(ctx, _item())
    ctx.budget_usd = 0.01
    capped = await _run(ctx, _item("rc_base", "w3-1"), wave=3)
    assert capped.status == "capped"

    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="")
    out = StringIO()
    ui = PlainUi(tracker, console=Console(file=out, width=300))
    for kind, fields in ctx.log.events:
        if kind in ("repeat_estimate", "repeat"):
            # The loop's fan-out: the tracker first, then the display.
            tracker.event(kind, **fields)
            ui.event(kind, **fields)
    status = tracker.snapshot()
    assert [r.item_id for r in status.repeats] == ["w2-1", "w3-1"]
    text = out.getvalue()
    assert "repeat w2-1: rc_base measured again on 1 score(s)" in text
    assert "repeat w3-1 did not run: the estimate $" in text

    appendix = render_repeats_appendix(status)
    assert appendix.startswith(REPEATS_APPENDIX_HEADING)
    assert (
        "| w2-1 | `rc_base` (baseline) | `rc_probe_1` | eval_1/accuracy | 6"
        " | 2/6 (33%) | 1 / 1 | — | 0.67 → 0.67 |"
    ) in appendix
    assert "- w3-1 (`rc_base`): the estimate $" in appendix
    assert (
        render_repeats_appendix(
            RunStatusTracker(project_id="p", task_id="t", guidance="").snapshot()
        )
        == ""
    )

    assert outcome_cell(done) == "repeat: 2 of 6 changed (1 worse, 1 better)"
    assert outcome_cell(capped).startswith("repeat did not run: the estimate")


# ---- review fixes ----


async def test_the_estimate_counts_one_drive_for_each_eval_and_item():
    ctx = _ctx()
    ctx.kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    ctx.kiln.results[("eval_2", "rc_base", "train")] = [
        _row(f"t{i}", v) for i, v in enumerate(STORED)
    ]
    _clone_rows(ctx, STORED)
    _clone_rows(ctx, STORED, eval_id="eval_2")
    await _run(ctx, _item(eval_ids=["eval_1", "eval_2"]))
    (estimate,) = [f for k, f in ctx.log.events if k == "repeat_estimate"]
    # A legacy judge drives inside each eval's job: 12 drives, 12 judgments.
    assert estimate["estimate_usd"] == pytest.approx(12 * 0.01 + 12 * 0.002)


async def test_two_repeats_of_one_run_config_in_a_wave_get_their_own_clones():
    import asyncio

    ctx = _ctx()
    ctx.kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    ctx.kiln.results[("eval_2", "rc_base", "train")] = [_row("t0", 1.0)]
    first, second = await asyncio.gather(
        _run(ctx, _item(item_id="w2-1")),
        _run(ctx, _item(item_id="w2-2", eval_ids=["eval_2"])),
    )
    names = sorted(c["name"] for c in ctx.kiln.created_run_configs)
    assert names == ["REPEAT w2-1 of rc_base", "REPEAT w2-2 of rc_base"]


async def test_a_kiln_error_in_the_policy_check_fails_the_item_not_the_wave():
    from kiln_harness_tuner.kiln import KilnError

    ctx = _ctx()
    ctx.model_policy = ModelPolicy.from_json(
        {"allow": [{"provider": "openai", "model": "*"}]}
    )
    calls = 0
    real = ctx.kiln.call

    async def flaky(method, path, **kw):
        nonlocal calls
        if method == "GET" and path.endswith("/run_configs"):
            calls += 1
            if calls == 3:  # the policy check's read
                raise KilnError("502")
        return await real(method, path, **kw)

    ctx.kiln.call = flaky
    outcome = await _run(ctx, _item())
    assert outcome.status == "failed_infra"
    assert "the model policy could not be checked" in outcome.infra_error.detail


async def test_a_probe_and_a_screening_candidate_never_fork_a_clone():
    from kiln_harness_tuner.schemas import ProbeItem, ScreenCandidate
    from kiln_harness_tuner.viability import _make_candidate_rc

    ctx = _ctx()
    _clone_rows(ctx, STORED)
    clone = (await _run(ctx, _item())).repeat.clone_rc_id
    probe = ProbeItem(
        id="w3-1",
        type="probe",
        question="does it call the tool?",
        method="drive three items",
        parent_rc_id=clone,
        reason="r",
    )
    outcome = await execute_plan_item(ctx, probe, BASELINE, ["rc_base"], 3)
    assert outcome.status == "completed" and outcome.probe.answer == "unclear"
    assert "is a clone or a fork that code made" in outcome.probe.evidence
    assert ctx.dispatch.calls == []
    for cand in (
        ScreenCandidate(id="s3-1", overrides={"temperature": 0.2}, parent_rc_id=clone),
        ScreenCandidate(id="s3-2", rc_id=clone),
    ):
        rc_id, reason, dead = await _make_candidate_rc(
            ctx, cand, cand.parent_rc_id or "rc_base"
        )
        assert rc_id is None and not dead
        assert "is a clone or a fork that code made" in reason
    assert len(ctx.kiln.created_run_configs) == 1  # the repeat's clone only
