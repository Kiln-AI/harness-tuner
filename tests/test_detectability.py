"""The pre-run detectability check (`kiln_harness_tuner.detectability`):
the floor from alpha, the refusal rule, and where the val side may go."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_ctx, make_plan
from fake_dispatcher import ok
from fake_kiln import make_eval, make_run, make_score
from test_orchestrator import seed_active_eval, seed_baseline

from kiln_harness_tuner import analysis
from kiln_harness_tuner.analysis import ScoreSpec
from kiln_harness_tuner.detectability import (
    UnderpoweredEvalsError,
    assess,
    binary_floor,
    bootstrap_ci_excludes_zero,
    build_rows,
    comparable_specs,
    fewest_wins,
    interval_wins,
    paired_ci_low,
    rating_floor,
    score_room,
    significance_floor,
    wilcoxon_all_positive_p,
)
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.report import render_detectability_appendix
from kiln_harness_tuner.schemas import (
    DetectabilityRow,
    RunStatus,
    TrainDetectability,
)
from kiln_harness_tuner.setup import SetupError

Z95 = 1.959963984540054

# ---- the floor, from alpha ----


def test_the_floor_is_computed_from_alpha():
    # Exact two-sided McNemar, every discordant pair one way: p = 2 · 0.5^k.
    assert 2 * 0.5**5 >= 0.05 > 2 * 0.5**6
    assert binary_floor(0.05) == 6
    assert binary_floor(0.01) == 8  # 2 · 0.5^8 = 0.0078
    assert binary_floor(0.1) == 5
    # The server's Wilcoxon: no p below 5 differences; 0.059 at 5, 0.036 at 6
    # (pinned against `kiln_server.statistics_lib.wilcoxon_signed_rank_p`).
    assert wilcoxon_all_positive_p(4) is None
    assert wilcoxon_all_positive_p(5) == pytest.approx(0.059058229, abs=1e-8)
    assert wilcoxon_all_positive_p(6) == pytest.approx(0.036031686, abs=1e-8)
    assert rating_floor(0.05) == 6
    assert rating_floor(0.01) > binary_floor(0.01)  # the approximation is looser


def test_the_default_floor_follows_the_significance_alpha(monkeypatch):
    assert significance_floor("pass_fail") == binary_floor(analysis.SIGNIFICANCE_ALPHA)
    assert significance_floor("five_star") == rating_floor(analysis.SIGNIFICANCE_ALPHA)
    assert fewest_wins("pass_fail", 40, 5, alpha=0.1) == 5
    assert fewest_wins("pass_fail", 40, 5) is None


def test_fewest_wins_needs_the_room_to_reach_the_floor():
    assert fewest_wins("pass_fail", 30, 5) is None
    assert fewest_wins("pass_fail", 30, 6) == 6
    assert fewest_wins("pass_fail", 30, 30) == 6
    assert fewest_wins("five_star", 5, 5) is None  # n itself below the floor
    assert fewest_wins("pass_fail", 0, 0) is None


@pytest.mark.parametrize(
    "spec,values,room",
    [
        (ScoreSpec("a", "pass_fail", "higher_is_better"), [1, 1, 0, 0, 0], 3),
        (ScoreSpec("a", "pass_fail", "lower_is_better"), [1, 1, 0, 0, 0], 2),
        (ScoreSpec("a", "pass_fail_critical", "higher_is_better"), [1, 0, -1], 2),
        (ScoreSpec("a", "five_star", "higher_is_better"), [5, 5, 4, 1], 2),
        (ScoreSpec("a", "five_star", "lower_is_better"), [1, 2, 5], 2),
    ],
)
def test_room_is_the_items_not_at_the_best_value(spec, values, room):
    assert score_room([float(v) for v in values], spec) == room


def test_the_interval_route_is_the_servers_arithmetic():
    # Pinned against `kiln_server.statistics_lib.paired_proportion_diff_ci`
    # (n11, n10, n01, n00) -> the served lower end.
    assert paired_ci_low(5, 0, 3, 2, Z95) == 0.0335
    assert paired_ci_low(5, 0, 2, 3, Z95) == -0.0493
    assert paired_ci_low(15, 0, 5, 0, Z95) == 0.0313
    assert paired_ci_low(15, 0, 4, 1, Z95) == -0.0171
    assert paired_ci_low(0, 0, 1, 0, Z95) == 1.0
    assert paired_ci_low(50, 2, 6, 2, Z95) == -0.0415
    # So the interval lies above zero on fewer wins than the floor: 3 of 5
    # failures fixed on 10 items, 1 of 1 on one item.
    assert interval_wins("pass_fail", 10, 5) == 3
    assert interval_wins("pass_fail", 1, 1) == 1
    assert fewest_wins("pass_fail", 10, 5) is None
    # A rating's bootstrap: above zero once ((n - k)/n)^n < alpha / 2.
    assert not bootstrap_ci_excludes_zero(20, 3, 0.05)
    assert bootstrap_ci_excludes_zero(20, 4, 0.05)
    assert interval_wins("five_star", 20, 10) == 4


def test_informational_scores_never_count():
    eval_def = make_eval(
        "eval_1",
        "ec_1",
        scores=[
            make_score("Accuracy"),
            make_score("Length", type="five_star", direction="informational"),
            make_score("Blob", type="custom"),
        ],
    )
    specs, notes = comparable_specs(eval_def)
    assert [s.key for s in specs] == ["accuracy"]
    assert notes == ["eval_1/blob: score type 'custom' has no statistics operation"]
    only_info = make_eval(
        "eval_2", "ec_2", scores=[make_score("Length", direction="informational")]
    )
    assert comparable_specs(only_info) == ([], [])


# ---- the refusal rule ----


def _train(
    eval_id: str = "eval_1",
    n: int = 40,
    room: int = 10,
    score_type: str = "pass_fail",
    unscored: int | None = 0,
) -> TrainDetectability:
    floor = significance_floor(score_type)
    return TrainDetectability(
        eval_id=eval_id,
        eval_name=f"Eval {eval_id}",
        score_key="accuracy",
        score_type=score_type,
        direction="higher_is_better",
        n=n,
        room=room,
        unscored=unscored,
        floor=floor,
        fewest_wins=fewest_wins(score_type, n, room),
        best_case_change=floor / n if n >= floor else None,
    )


def _rows(*pairs: tuple[TrainDetectability, int | None]) -> list[DetectabilityRow]:
    rows: list[DetectabilityRow] = []
    for train, val_n in pairs:
        rows += build_rows([train], {train.eval_id: val_n} if val_n is not None else {})
    return rows


def test_refuses_when_no_score_can_show_a_significant_val_win():
    rows = _rows((_train("eval_1"), 4), (_train("eval_2", score_type="five_star"), 5))
    warnings, refusal = assess(rows, objective="quality", min_effect=None)
    assert refusal is not None
    # Every offending score at once, then the fix line.
    assert 'eval_1 "Eval eval_1" accuracy (pass_fail) — 4 val item(s)' in refusal
    assert 'eval_2 "Eval eval_2" accuracy (five_star) — 5 val item(s)' in refusal
    assert "\nfix: add val items" in refusal
    assert "--allow-underpowered" in refusal
    assert any("val holds 4 item(s)" in w for w in warnings)


def test_one_score_that_can_show_a_win_is_enough():
    rows = _rows((_train("eval_1"), 4), (_train("eval_2"), 30))
    _warnings, refusal = assess(rows, objective="quality", min_effect=None)
    assert refusal is None


def test_a_guard_eval_at_its_ceiling_warns_and_never_refuses():
    guard = _train("eval_guard", n=40, room=0)
    target = _train("eval_1", n=40, room=12)
    rows = _rows((guard, 30), (target, 30))
    warnings, refusal = assess(rows, objective="quality", min_effect=None)
    assert refusal is None
    assert any(
        w.startswith("eval_guard/accuracy: at its ceiling on train") for w in warnings
    )
    # Even when EVERY eval sits at its ceiling: a certain train fact, but
    # the loop may still build evals; it warns.
    rows = _rows((guard, 30))
    warnings, refusal = assess(rows, objective="quality", min_effect=None)
    assert refusal is None and any("ceiling" in w for w in warnings)


def test_an_estimate_warns_and_never_refuses():
    # 30 val items (certain: enough), but at the train share of 2/40 with
    # room, only ≈2 would have room — an estimate.
    rows = _rows((_train("eval_1", n=40, room=2), 30))
    (row,) = rows
    assert row.val_room_estimate == 2
    warnings, refusal = assess(rows, objective="quality", min_effect=None)
    assert refusal is None
    assert any("val (estimate) ≈2 of 30 items have room" in w for w in warnings)


def test_an_unknown_val_size_never_refuses():
    rows = _rows((_train("eval_1"), None))
    assert rows[0].val_n is None
    assert assess(rows, objective="quality", min_effect=None)[1] is None


def test_no_comparable_score_at_all_refuses():
    warnings, refusal = assess([], objective="quality", min_effect=None)
    assert refusal is not None and refusal.startswith("No active eval has a score")
    assert "\nfix: " in refusal


@pytest.mark.parametrize("objective", ["speed", "cost"])
def test_speed_and_cost_report_and_never_refuse(objective):
    rows = _rows((_train("eval_1"), 2))
    warnings, refusal = assess(rows, objective=objective, min_effect=None)
    assert refusal is None and warnings


def test_min_effect_refuses_when_no_score_can_show_it():
    # floor 6: 6/40 = 15% on val. A 10% effect cannot show; 20% can.
    rows = _rows((_train("eval_1", n=100, room=40), 40))
    warnings, refusal = assess(rows, objective="quality", min_effect=0.10)
    assert refusal is not None and "--min-effect" in refusal
    assert "the smallest change that can show is 15%" in refusal
    assert assess(rows, objective="quality", min_effect=0.20)[1] is None
    # Train: 6/100 = 6% best case, so 10% is detectable there; 5% is not.
    warnings, _ = assess(rows, objective="quality", min_effect=0.05)
    assert any("train cannot show a change of 5% (best case 6%)" in w for w in warnings)


def test_min_effect_needs_only_one_score_that_can_show_it():
    rows = _rows((_train("eval_1", n=100, room=40), 40), (_train("eval_2", n=100), 120))
    assert assess(rows, objective="quality", min_effect=0.10)[1] is None


def test_the_refusal_is_a_setup_error():
    assert issubclass(UnderpoweredEvalsError, SetupError)


# ---- in the loop ----


def _project(tmp_path: Path, val_n: int, train_n: int = 8, evals=("eval_1",)) -> Path:
    """A project on disk: each eval's splits, `train_n` train and `val_n`
    val inputs (the split sizes the check reads)."""
    task = tmp_path / "proj" / "tasks" / "t1 - T"
    splits = {
        n: {"source": "eval_input", "filter_id": f"tag::{n}"}
        for n in ("train", "val", "test")
    }
    for eval_id in evals:
        folder = task / "evals" / f"{eval_id} - E"
        folder.mkdir(parents=True)
        (folder / "eval.kiln").write_text(
            json.dumps({"id": eval_id, "splits": splits}), encoding="utf-8"
        )
    for split, count in (("train", train_n), ("val", val_n), ("test", 3)):
        for n in range(count):
            item = task / "eval_inputs" / f"{split}{n}"
            item.mkdir(parents=True)
            (item / "eval_input.kiln").write_text(
                json.dumps(
                    {
                        "id": f"{split}{n}",
                        "tags": [split],
                        "data": {
                            "type": "single_turn",
                            "user_message": {"text": f"{split} input {n}"},
                        },
                    }
                ),
                encoding="utf-8",
            )
    return tmp_path / "proj"


def _ctx(tmp_path: Path, val_n: int, fails: int = 3, train_n: int = 8):
    ctx = make_ctx(gate_agent="auto")
    ctx.detectability_check = True
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(_project(tmp_path, val_n, train_n))}
    )
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        make_run(f"train{n}", accuracy=0.0 if n < fails else 1.0)
        for n in range(train_n)
    ]
    return ctx


async def test_the_refusal_stops_the_run_before_any_wave_spend(tmp_path):
    ctx = _ctx(tmp_path, val_n=4)
    ctx.recon = True
    with pytest.raises(UnderpoweredEvalsError) as refused:
        await orchestrate(ctx, "g", timeout_mins=180)
    assert "4 val item(s), fewer than 6" in str(refused.value)
    # No plan, no probe, no recon drive, no eval job: nothing was paid for.
    assert ctx.dispatch.calls_to("batch_plan") == []
    assert ctx.kiln.run_posts == [] and ctx.kiln.job_invocations == []
    kinds = [k for k, _f in ctx.log.events]
    assert "detectability" in kinds and "wave_start" not in kinds
    (summary,) = [f for k, f in ctx.log.events if k == "detectability"]
    assert summary["line"].endswith("— REFUSED")


async def test_allow_underpowered_goes_on_and_keeps_the_warning(tmp_path):
    ctx = _ctx(tmp_path, val_n=4)
    ctx.allow_underpowered = True
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (summary,) = [f for k, f in ctx.log.events if k == "detectability"]
    report = summary["report"]
    assert report.refusal is not None and report.overridden
    lines = [f["line"] for k, f in ctx.log.events if k == "detectability_warning"]
    assert any("--allow-underpowered goes on" in line for line in lines)
    # It lands in the report's code-computed appendix.
    status = RunStatus(
        project_id="p1",
        task_id="t1",
        guidance="",
        baseline_history=[],
        waves=[],
        spend_total_usd=0.0,
        time_remaining_secs=0.0,
        exit_reason=None,
        detectability=report,
    )
    appendix = render_detectability_appendix(status)
    assert "**Refused** (went on with `--allow-underpowered`)" in appendix
    assert "val n (val-grade)" in appendix


async def test_enough_val_items_pass_and_the_planner_gets_the_train_rows(tmp_path):
    ctx = _ctx(tmp_path, val_n=37, fails=7, train_n=12)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    (row,) = plan_input.train_detectability
    assert (row.eval_id, row.n, row.room, row.floor) == ("eval_1", 12, 7, 6)
    assert row.fewest_wins == 6 and row.best_case_change == pytest.approx(0.5)
    assert row.unscored == 0


async def test_no_val_number_reaches_the_planner_or_memory(tmp_path):
    """The val side (a split size of 37 and its ≈ estimate of 22) goes to
    the terminal, the event log and the report only. The planner's input
    and every memory write carry train numbers alone."""
    ctx = _ctx(tmp_path, val_n=37, fails=7, train_n=12)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    (summary,) = [f for k, f in ctx.log.events if k == "detectability"]
    (full,) = summary["report"].rows
    assert (full.val_n, full.val_room_estimate) == (37, 22)
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    dumped = plan_input.model_dump_json()
    keys: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            keys.update(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(dumped))
    assert not any(k.startswith("val_") or "interval" in k for k in keys)
    assert "37" not in dumped and "22" not in dumped
    assert set(type(plan_input.train_detectability[0]).model_fields) == set(
        TrainDetectability.model_fields
    )
    writes = [json.dumps(b) for b in ctx.kiln.memory_posts] + [
        json.dumps(b) for _id, b in ctx.kiln.memory_patches
    ]
    assert not any("37" in w or "val_n" in w or "detectab" in w for w in writes)


async def test_a_guard_eval_at_its_ceiling_does_not_stop_the_run(tmp_path):
    ctx = _ctx(tmp_path, val_n=20, fails=0)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    lines = [f["line"] for k, f in ctx.log.events if k == "detectability_warning"]
    assert any("at its ceiling on train" in line for line in lines)


async def test_an_all_informational_task_refuses(tmp_path):
    ctx = _ctx(tmp_path, val_n=20)
    ctx.kiln.evals["eval_1"] = make_eval(
        "eval_1", "ec_1", scores=[make_score("Accuracy", direction="informational")]
    )
    with pytest.raises(UnderpoweredEvalsError, match="No active eval has a score"):
        await orchestrate(ctx, "g", timeout_mins=180)


async def test_the_check_is_off_unless_asked(tmp_path):
    ctx = _ctx(tmp_path, val_n=2)
    ctx.detectability_check = False
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    assert "detectability" not in [k for k, _f in ctx.log.events]


def test_the_check_lines_print_in_both_displays():
    from io import StringIO

    from rich.console import Console

    from kiln_harness_tuner.status import RunStatusTracker
    from kiln_harness_tuner.ui import LiveUi, PlainUi

    kinds = (
        "detectability",
        "detectability_row",
        "detectability_warning",
        "noise_estimate",
        "noise_check",
        "noise_row",
        "noise_warning",
    )
    for ui_class in (PlainUi, LiveUi):
        out = StringIO()
        tracker = RunStatusTracker(project_id="p", task_id="t", guidance="")
        ui = ui_class(tracker, console=Console(file=out, width=200))
        for kind in kinds:
            ui.event(kind, line=f"{kind} line")
        text = out.getvalue()
        for kind in kinds:
            assert f"{kind} line" in text, (ui_class, kind)


def test_the_tracker_keeps_both_results_for_the_report():
    from kiln_harness_tuner.schemas import DetectabilityReport, NoiseCheckResult
    from kiln_harness_tuner.status import RunStatusTracker

    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="")
    report = DetectabilityReport(
        baseline_rc_id="rc_base", alpha=0.05, floor_binary=6, floor_rating=6
    )
    noise = NoiseCheckResult(baseline_rc_id="rc_base", items_per_eval=5)
    tracker.event("detectability", line="x", report=report)
    tracker.event("noise_check", line="y", result=noise)
    status = tracker.snapshot()
    assert status.detectability == report and status.noise_check == noise
    appendix = render_detectability_appendix(status)
    assert appendix.startswith("## Detectability (code-computed)")
    assert "`--noise-check 5`" in appendix
