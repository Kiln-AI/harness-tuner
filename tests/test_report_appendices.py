"""report.py: the two code-rendered appendices that say why
each arm stopped and what each screened candidate traded: every stop code
in plain words, partly or in full, the sort orders, the empty run, and an
old status file without the new fields."""

from typing import get_args

import pytest
from conftest import fix_item, make_ctx, make_tracker
from fake_dispatcher import ok
from test_report import RUN_ID, make_writer
from test_status import WAVE1_ITEMS, start_wave

from kiln_harness_tuner.report import (
    SPEND_APPENDIX_HEADING,
    STOP_APPENDIX_HEADING,
    STOP_REASONS,
    TRADEOFF_APPENDIX_HEADING,
    for_report_agent,
    measured_extent,
    render_stop_reasons_appendix,
    render_tradeoff_appendix,
    screen_label,
    stop_code,
    stop_reason_text,
)
from kiln_harness_tuner.schemas import (
    ErrorInfo,
    ItemOutcome,
    ItemStatus,
    LatencyComparison,
    MeasurementSummary,
    ReportMarkdown,
    RunStatus,
    ScreenResult,
    StopKind,
)

# ---- why each candidate stopped ----


def _measured(status="stopped", stop_kind=None, judged=18, planned=None, **kw):
    """A measurement summary; `planned` per eval, `judged` split evenly."""
    planned = {"eval_1": 40} if planned is None else planned
    share = judged // max(len(planned), 1)
    return MeasurementSummary(
        status=status,
        stop_kind=stop_kind,
        judged=judged,
        planned_per_eval=planned,
        judged_per_eval={e: share for e in planned},
        **kw,
    )


def _arm(status="not_landed", measurement=None, infra_error=None, rc="rc_arm"):
    out = ItemOutcome(
        item=fix_item(),
        status=status,
        rc_id=rc,
        experiment_memory_id="mem_exp",
        result=None,
        infra_error=infra_error,
    )
    out.measurement = measurement
    return out


def _item(outcome, live_state="done", kind="fix"):
    return ItemStatus(
        item_id="w1-1",
        kind=kind,
        label="retry-skill (issue_1)",
        live_state=live_state,
        eval_progress=None,
        outcome=outcome,
        elapsed_secs=0.0,
        spend_usd=0.0,
    )


def test_every_stop_rule_and_outcome_status_has_a_plain_sentence():
    # Every stop kind the engine can set, and every outcome status, maps to
    # a sentence: none of them falls back to the raw code.
    # `failed_infra` is read as `error`, or as `cut_off` at the deadline.
    codes = [*get_args(StopKind), "lever_not_applied", "error", "cut_off"]
    codes += [
        s
        for s in get_args(ItemOutcome.model_fields["status"].annotation)
        if s != "failed_infra"
    ]
    for code in codes:
        assert code in STOP_REASONS, code
        text = stop_reason_text(code)
        assert "internal code" not in text
        assert text.endswith(f"({code}).")


def _capped(line):
    return _arm("capped", _measured("capped", stop_kind="max_usd", stop_line=line))


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (
            _arm("completed", _measured("completed", judged=40)),
            "Ran in full: every planned train item was judged (completed).",
        ),
        (
            _arm(measurement=_measured(stop_kind="futility")),
            "Stopped early: it could not reach the bar with the items left, even"
            " if every one went its way (futility).",
        ),
        (
            _arm(measurement=_measured(stop_kind="eval_metric")),
            "Stopped early: its quality was clearly below the bar (a significant"
            " loss) (eval_metric).",
        ),
        (
            _arm(measurement=_measured(stop_kind="guard_losses")),
            "Stopped early: it failed more of the baseline's passing items than"
            " noise explains (guard_losses).",
        ),
        (
            _arm(measurement=_measured(stop_kind="residual")),
            "Stopped early: on the hard items judged first, it fixed too few of"
            " its parent's failures or broke too many of its passes (residual).",
        ),
        (
            _arm(measurement=_measured(stop_kind="targets")),
            "Stopped early: an item it was built to fix still failed (targets).",
        ),
        (
            _arm(measurement=_measured(stop_kind="not_better")),
            "Stopped early: it was not clearly cheaper or faster than the"
            " baseline (not_better).",
        ),
        (
            _arm(measurement=_measured(stop_kind="latency_ratio")),
            "Stopped early: it was clearly slower than the baseline, past the"
            " allowed ratio (latency_ratio).",
        ),
        (
            _arm(measurement=_measured(stop_kind="cost_ratio")),
            "Stopped early: it cost clearly more per request than the baseline,"
            " past the allowed ratio (cost_ratio).",
        ),
        (
            _arm(measurement=_measured(stop_kind="invalid_output_rate")),
            "Stopped early: too many of its outputs did not match the task's"
            " output format (invalid_output_rate).",
        ),
        (
            _arm("dead_end", _measured(stop_kind="lever_not_applied", judged=0)),
            "Stopped early: its change did not reach the model call, so it cannot"
            " change a result (lever_not_applied).",
        ),
        (
            _capped("stopped at n=18: $4.10 of $4.00 — rule: max_usd $4.00"),
            "Stopped at its spending cap: the result is inconclusive, not a loss"
            " (max_usd).",
        ),
        (
            _capped("stopped at n=18: $0.40 left — rule: the run budget $30.00"),
            "Stopped at the run's budget: `--budget-usd` had no room for more"
            " items; the result is inconclusive, not a loss (run_budget).",
        ),
        (
            _arm("capped", _measured("capped", stop_kind="max_items")),
            "Stopped at its item limit: the plan set the most items to judge"
            " (max_items).",
        ),
        (
            _arm(
                "failed_infra",
                infra_error=ErrorInfo(error_class="provider_error", detail="502"),
            ),
            "Stopped by an error: the numbers say nothing about the change"
            " (error: provider_error, 502).",
        ),
        (
            _arm(
                "failed_infra",
                infra_error=ErrorInfo(
                    error_class="timeout",
                    detail="run deadline reached — item cancelled in flight",
                ),
            ),
            "Cut off: the run ended before this candidate finished (cut_off).",
        ),
        (
            _arm("dead_end"),
            "Not measured: the build, or a check before any train item was"
            " judged, ended it as a dead end (dead_end).",
        ),
        (
            _arm("completed"),  # a status file written before the engine
            "Ran in full: every planned train item was judged (completed).",
        ),
        (
            _arm("skipped_not_reproduced"),
            "Not measured: the issue did not reproduce on the baseline"
            " (skipped_not_reproduced).",
        ),
    ],
)
def test_each_stop_maps_to_one_plain_sentence(outcome, expected):
    assert stop_reason_text(*stop_code(_item(outcome))) == expected


def test_an_unfinished_arm_was_cut_off_by_the_runs_end():
    item = _item(None, live_state="measuring")
    assert stop_reason_text(*stop_code(item)) == (
        "Cut off: the run ended before this candidate finished"
        " (cut_off: still measuring)."
    )
    assert measured_extent(None) == "not measured"


def test_an_unknown_stop_code_prints_raw():
    assert stop_reason_text("brand_new_rule") == (
        "Stopped by internal code `brand_new_rule` (no plain description)."
    )
    item = _item(_arm(measurement=_measured(stop_kind="brand_new_rule")))
    assert "`brand_new_rule`" in stop_reason_text(*stop_code(item))


def test_measured_extent_says_partly_or_in_full():
    assert measured_extent(_arm(measurement=_measured(judged=18))) == (
        "partly: 18 of 40 items"
    )
    assert measured_extent(_arm("completed", _measured("completed", judged=40))) == (
        "in full: 40 of 40 items"
    )
    two = _measured(judged=12, planned={"eval_2": 20, "eval_1": 20})
    assert measured_extent(_arm(measurement=two)) == (
        "partly: 12 of 40 items (`eval_1` 6/20, `eval_2` 6/20)"
    )
    assert measured_extent(_arm(measurement=_measured(judged=0))) == (
        "not measured: 0 of 40 items"
    )
    # An outcome written before the per-eval counts: the total and the chunks.
    old = MeasurementSummary(status="stopped", judged=18, chunks=2, planned_chunks=5)
    assert measured_extent(_arm(measurement=old)) == (
        "partly: 18 items judged, chunk 2 of 5"
    )
    assert measured_extent(_arm("completed")) == "in full (no item counts recorded)"
    assert measured_extent(_arm("dead_end")) == "not measured"


def test_stop_reasons_appendix_has_one_row_per_arm_and_merge():
    tracker = make_tracker()
    start_wave(
        tracker,
        items=[
            *WAVE1_ITEMS,
            {"item_id": "w1-3", "kind": "merge", "label": "merge w1-1 + w0-2"},
        ],
    )
    tracker.event(
        "item_outcome",
        item_id="w1-1",
        outcome=_arm(measurement=_measured(stop_kind="futility")),
    )
    tracker.event("item_outcome", item_id="w1-2", outcome=_arm("completed"))
    text = render_stop_reasons_appendix(tracker.snapshot())
    assert text.startswith(STOP_APPENDIX_HEADING)
    assert "| 1 | `w1-1` fix | `rc_arm` | Stopped early: it could not reach" in text
    assert "(futility). | partly: 18 of 40 items |" in text
    # The merge item never landed: the run's end cut it off.
    assert (
        "| 1 | `w1-3` merge | — | Cut off: the run ended before this candidate"
        " finished (cut_off: still planned). | not measured |"
    ) in text
    assert "`w1-2`" not in text  # an eval-only item is not an arm


def test_stop_reasons_appendix_is_left_out_without_an_arm():
    assert render_stop_reasons_appendix(make_tracker().snapshot()) == ""
    tracker = make_tracker()
    start_wave(tracker, items=[WAVE1_ITEMS[1]])
    assert render_stop_reasons_appendix(tracker.snapshot()) == ""


# ---- the screening trade-offs ----


def _ratio_of(value, metric="latency_ms"):
    if value is None:
        return None
    return LatencyComparison(
        method="paired_drive",
        split="train",
        n_items=24,
        metric=metric,
        median_ratio=value,
    )


def _screen(cid, cost, latency, *, net=None, verdict="survivor", **kw):
    fields = {
        "candidate_id": cid,
        "label": f"depot-{cid}",
        "rc_id": f"rc_{cid}",
        "round": 1,
        "round_no": 1,
        "evals": ["eval_1"],
        "items": 24,
        "baseline_passed": 24,
        "broke": 0,
        "fixed": 0,
        "verdict": verdict,
        "net": net,
        "cost": _ratio_of(cost, "cost_usd"),
        "latency": _ratio_of(latency),
    }
    fields.update(kw)
    return ScreenResult(**fields)


def _screened(objective, results):
    tracker = make_tracker()
    tracker.event("run_start", objective=objective)
    tracker.event("screening_round", round=1, results=[r.model_dump() for r in results])
    return tracker.snapshot()


def _order(text):
    return [
        line.split("`")[1] for line in text.splitlines() if line.startswith("| 1 | `")
    ]


SCREENS = [
    _screen("s1-1", 0.90, 0.95, net=0.05),
    _screen(
        "s1-2",
        0.57,
        0.40,
        net=-0.20,
        broke=7,
        verdict="dead_end",
        stop_kind="guard_losses",
    ),
    _screen("s1-3", None, 0.30, net=0.10),
    _screen("s1-4", 0.70, None, net=None),
]


def test_tradeoff_rows_cheapest_first_under_cost():
    text = render_tradeoff_appendix(_screened("cost", SCREENS))
    assert text.startswith(TRADEOFF_APPENDIX_HEADING)
    assert "rows cheapest first" in text
    assert _order(text) == ["s1-2", "s1-4", "s1-1", "s1-3"]
    # The candidate the rules refused still shows its price.
    assert (
        "| 1 | `s1-2` depot-s1-2 | 24 | lost 7 of 24, fixed 0 | 0.57× | 0.40×"
        " | dead end: it failed more of the baseline's passing items than noise"
        " explains (guard_losses) | `rc_s1-2` |"
    ) in text
    assert "net [95%]" not in text


def test_tradeoff_rows_fastest_first_under_speed():
    text = render_tradeoff_appendix(_screened("speed", SCREENS))
    assert "rows fastest first" in text
    assert _order(text) == ["s1-3", "s1-2", "s1-1", "s1-4"]


def test_tradeoff_rows_highest_net_first_under_quality():
    text = render_tradeoff_appendix(_screened("quality", SCREENS))
    assert "rows highest net change first" in text
    assert _order(text) == ["s1-3", "s1-1", "s1-2", "s1-4"]
    assert "| net [95%] |" in text
    assert "| +0.10 |" in text


def test_tradeoff_label_names_model_provider_and_thinking():
    plain = _screen(
        "s1-5",
        0.5,
        0.5,
        label="cheap tier",
        overrides={
            "model_name": "depot_small",
            "model_provider_name": "provider_a",
            "thinking_level": "low",
        },
    )
    assert screen_label(plain) == "cheap tier (depot_small@provider_a; thinking low)"
    named = plain.model_copy(update={"label": "depot_small@provider_a (thinking low)"})
    assert screen_label(named) == "depot_small@provider_a (thinking low)"


def test_tradeoff_cells_for_unjudged_capped_and_error_candidates():
    results = [
        _screen("s1-6", None, None, items=0, verdict="error", rc_id=None),
        _screen("s1-7", 0.8, 0.8, verdict="capped"),
        _screen("s1-8", None, None, items=0, verdict="dead_end", reason="refused"),
    ]
    text = render_tradeoff_appendix(_screened("cost", results))
    assert "| not judged | — | — | not screened (error) | — |" in text
    assert "| capped (inconclusive) |" in text
    assert "| dead end: refused |" in text


def test_tradeoff_appendix_without_screens():
    # As the speed appendix: left out of a quality run, one line under cost
    # and speed.
    assert render_tradeoff_appendix(make_tracker().snapshot()) == ""
    text = render_tradeoff_appendix(_screened("cost", []))
    assert (
        text == f"{TRADEOFF_APPENDIX_HEADING}\n\nNo screening round ran in this run.\n"
    )


def test_tracker_folds_screening_rounds_and_skips_an_invalid_result():
    tracker = make_tracker()
    tracker.event(
        "screening_round",
        round=1,
        results=[SCREENS[0].model_dump(), {"candidate_id": "broken"}],
    )
    tracker.event("screening_round", round=2, results=[SCREENS[1].model_dump()])
    status = tracker.snapshot()
    assert [r.candidate_id for r in status.screen_results] == ["s1-1", "s1-2"]
    assert status.screen_results[1].stop_kind == "guard_losses"


def test_an_old_run_status_without_the_new_fields_still_loads():
    old = make_tracker().snapshot().model_dump(mode="json")
    del old["screen_results"]
    outcome = _arm(measurement=_measured()).model_dump(mode="json")
    del outcome["measurement"]["judged_per_eval"]
    del outcome["measurement"]["planned_per_eval"]
    old["waves"] = [
        {
            "wave": 1,
            "should_wrap_up": False,
            "plan_headline": "1 experiment",
            "rejected_summary": "",
            "items": [
                {
                    "item_id": "w1-1",
                    "kind": "fix",
                    "label": "retry-skill (issue_1)",
                    "live_state": "done",
                    "eval_progress": None,
                    "outcome": outcome,
                    "elapsed_secs": 0.0,
                    "spend_usd": 0.0,
                }
            ],
            "promotion": None,
            "tripwire": None,
        }
    ]
    status = RunStatus.model_validate(old)
    assert status.screen_results == []
    screen = SCREENS[0].model_dump(mode="json")
    del screen["stop_kind"]
    assert ScreenResult.model_validate(screen).stop_kind is None
    assert "| partly: 18 items judged |" in render_stop_reasons_appendix(status)
    assert render_tradeoff_appendix(status) == ""


async def test_the_written_report_carries_both_appendices(tmp_path):
    ctx = make_ctx()
    writer, tracker = make_writer(tmp_path)
    tracker.event("run_start", objective="cost")
    tracker.event("screening_round", round=1, results=[SCREENS[1].model_dump()])
    start_wave(tracker)
    tracker.event(
        "item_outcome",
        item_id="w1-1",
        outcome=_arm(measurement=_measured(stop_kind="guard_losses")),
    )
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# Round report\n")))

    await writer(ctx, "wrap_up")

    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text(
        encoding="utf-8"
    )
    assert text.index(SPEND_APPENDIX_HEADING) < text.index(STOP_APPENDIX_HEADING)
    assert text.index(STOP_APPENDIX_HEADING) < text.index(TRADEOFF_APPENDIX_HEADING)
    assert "| `s1-2` depot-s1-2 | 24 | lost 7 of 24, fixed 0 | 0.57× |" in text


def test_a_rule_that_fires_on_the_last_chunk_reads_measured_in_full():
    # Every planned item was judged, then a rule refused the arm: not "early".
    tracker = make_tracker()
    start_wave(tracker, items=WAVE1_ITEMS)
    full = _measured(stop_kind="eval_metric", judged=40)
    tracker.event("item_outcome", item_id="w1-1", outcome=_arm(measurement=full))
    text = render_stop_reasons_appendix(tracker.snapshot())
    assert "Measured in full, then refused by a rule: its quality" in text
    assert "Stopped early" not in text
    assert "in full: 40 of 40 items" in text


def test_the_report_agent_gets_the_first_rows_without_per_eval_counts():
    results = [
        _screen(f"s{i}", 1.0 - i / 100, 1.0, per_eval={"eval_1": {"n": 24}})
        for i in range(30)
    ]
    status = _screened("cost", results)
    agent = for_report_agent(status)
    assert len(agent.screen_results) == 12
    assert agent.screen_results[0].candidate_id == "s29"  # the cheapest
    assert all(not r.per_eval for r in agent.screen_results)
    # The status the appendix renders from keeps every row.
    assert len(status.screen_results) == 30
    assert render_tradeoff_appendix(status).count("| 1 | `") == 30
