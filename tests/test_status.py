"""status.py — run-id scheme, item kind/label/headline helpers, and the
RunStatusTracker event fold."""

from datetime import datetime

import pytest
from conftest import (
    ManualClock,
    eval_only_item,
    fix_item,
    make_result,
    make_tracker,
    measure_item,
)

from kiln_harness_tuner.schemas import (
    DroppedEvalItem,
    EvalTarget,
    ExperimentItem,
    ItemOutcome,
    MergeWork,
    ProbeCheck,
    ProbeItem,
    PromotionDecision,
    RemeasureItem,
    SetupReport,
    UnloadableEval,
    WinRef,
)
from kiln_harness_tuner.status import (
    item_kind,
    item_label,
    item_summary,
    make_run_id,
    plan_headline,
    spend_split,
)


def merge_item(item_id: str = "w2-1") -> ExperimentItem:
    return ExperimentItem(
        id=item_id,
        type="experiment",
        evaluation_target=[],
        promotion_candidate=True,
        work=MergeWork(
            wins=[
                WinRef(experiment_memory_id="EXP 0722-w1-1", rc_id="rc_a"),
                WinRef(experiment_memory_id="EXP 0722-w1-3", rc_id="rc_b"),
            ]
        ),
    )


def completed_outcome(item=None) -> ItemOutcome:
    return ItemOutcome(
        item=item or fix_item(),
        status="completed",
        rc_id="rc_cand",
        experiment_memory_id="mem_exp",
        result=make_result(),
        infra_error=None,
    )


WAVE1_ITEMS = [
    {"item_id": "w1-1", "kind": "fix", "label": "retry-skill (issue_1)"},
    {"item_id": "w1-2", "kind": "eval", "label": "build eval for issue_2"},
]


def start_wave(tracker, wave=1, items=WAVE1_ITEMS, **kwargs):
    fields = {
        "should_wrap_up": False,
        "baseline_rc_id": "rc_base",
        "plan_headline": "1 experiment, 1 eval-only",
        "rejected_summary": "nothing else worth it",
    }
    fields.update(kwargs)
    tracker.event("wave_start", wave=wave, items=items, **fields)


# ---- helpers ----


def test_run_id_scheme():
    # The run id is the start timestamp, YYMMDD-HHMM.
    assert make_run_id(datetime(2026, 7, 23, 14, 2)) == "260723-1402"


NO_DETAILS = {
    "hypothesis": None,
    "technique": None,
    "predicted_effect": None,
    "reason": None,
    "issue_memory_id": None,
    "wildcard": None,
    "needs_eval": None,
    "eval_id": None,
    "targets": None,
    "promotion_candidate": None,
    "max_usd": None,
    "merge_wins": None,
    "probe": None,
    "remeasure": None,
    "measure": None,
    "parent_rc_id": None,
    "existing_rc_id": None,
}


def test_item_kind_and_label_fix():
    item = fix_item("w1-1", parent_rc_id="rc_fork")
    assert item_kind(item) == "fix"
    assert item_label(item) == "retry-skill for 429s (issue_1)"
    assert item_summary(item) == {
        "item_id": "w1-1",
        "kind": "fix",
        "label": "retry-skill for 429s (issue_1)",
        **NO_DETAILS,
        "type": "experiment",
        "hypothesis": "retry-skill for 429s",
        "technique": "prompt",
        "predicted_effect": "+10% pass rate",
        "issue_memory_id": "issue_1",
        "wildcard": False,
        "needs_eval": False,
        "eval_id": "eval_1",
        "targets": [{"eval_id": "eval_1", "scores": None}],
        "promotion_candidate": True,
        "parent_rc_id": "rc_fork",
    }


def test_wave_start_details_are_copies_of_the_items_lists():
    # The loop appends a `needs_eval` fix's new eval to its targets during
    # the wave; the event it emitted at wave start must not change.
    item = fix_item("w1-1")
    item.evaluation_target[0].scores = ["accuracy"]
    summary = item_summary(item)
    item.evaluation_target[0].scores.append("tone")
    item.evaluation_target.append(EvalTarget(eval_id="eval_new"))
    assert summary["targets"] == [{"eval_id": "eval_1", "scores": ["accuracy"]}]


def test_wave_start_details_of_a_merge():
    item = merge_item()
    item.max_usd, item.budget_reason = 4.0, "two evals"
    assert item_summary(item) == {
        "item_id": "w2-1",
        "kind": "merge",
        "label": "EXP 0722-w1-1 + EXP 0722-w1-3",
        **NO_DETAILS,
        "type": "experiment",
        "targets": [],
        "promotion_candidate": True,
        "max_usd": 4.0,
        "merge_wins": [
            {"experiment_memory_id": "EXP 0722-w1-1", "rc_id": "rc_a"},
            {"experiment_memory_id": "EXP 0722-w1-3", "rc_id": "rc_b"},
        ],
    }


def test_wave_start_details_of_the_other_item_types():
    details = item_summary(eval_only_item("w1-3", "mem_102"))
    assert details["type"] == "eval_only"
    assert details["issue_memory_id"] == "mem_102"
    # Why the planner added it, in its words.
    assert details["reason"] == "cheap-kill check"
    remeasure = RemeasureItem(
        id="w2-4", type="remeasure", rc_id="rc_x", eval_ids=["e1", "e2"], reason="r"
    )
    assert item_summary(remeasure)["remeasure"] == {
        "rc_id": "rc_x",
        "eval_ids": ["e1", "e2"],
    }
    assert item_summary(remeasure)["reason"] == "r"
    probe = ProbeItem(
        id="w2-5",
        type="probe",
        question="does it call the tool?",
        method="attach a tool",
        parent_rc_id="rc_p",
        max_drives=2,
        for_idea="tool arm",
        reason="r",
    )
    summary = item_summary(probe)
    assert summary == {
        "item_id": "w2-5",
        "kind": "probe",
        "label": "probe: does it call the tool?",
        **NO_DETAILS,
        "type": "probe",
        "probe": {
            "question": "does it call the tool?",
            "max_drives": 2,
            "for_idea": "tool arm",
            "code": False,
        },
        "parent_rc_id": "rc_p",
    }
    probe.check = ProbeCheck(count="tool_calls")
    assert item_summary(probe)["probe"]["code"] is True


def test_item_kind_and_label_merge():
    item = merge_item()
    assert item_kind(item) == "merge"
    assert item_label(item) == "EXP 0722-w1-1 + EXP 0722-w1-3"


def test_item_kind_and_label_eval_only():
    item = eval_only_item("w1-3", issue_memory_id="mem_102")
    assert item_kind(item) == "eval"
    assert item_label(item) == "build eval for mem_102"


def test_item_kind_and_label_measure_issue_baseline():
    item = measure_item("w2-3", issue_memory_id="mem_84", eval_id="424242966189")
    assert item_kind(item) == "measure"
    assert item_label(item) == "measure baseline for mem_84 (eval 424242966189)"
    assert item_summary(item) == {
        "item_id": "w2-3",
        "kind": "measure",
        "label": "measure baseline for mem_84 (eval 424242966189)",
        **NO_DETAILS,
        "type": "measure_issue_baseline",
        "issue_memory_id": "mem_84",
        "reason": "p1 issue, eval already exists — settle it cheap",
        "measure": {"eval_id": "424242966189", "scores": None},
    }


def test_plan_headline():
    assert plan_headline([fix_item()]) == "1 experiment"
    assert plan_headline([fix_item(), merge_item()]) == "2 experiments"
    assert plan_headline([fix_item(), eval_only_item()]) == "1 experiment, 1 eval-only"
    assert plan_headline([eval_only_item()]) == "1 eval-only"
    assert plan_headline([]) == "empty plan"


def test_plan_headline_counts_baseline_measurements():
    # Counted per type, not by subtraction: with three item types a
    # `len(items) - experiments` remainder would mislabel one of them.
    assert plan_headline([measure_item()]) == "1 baseline measurement"
    assert (
        plan_headline([measure_item("w2-1"), measure_item("w2-2")])
        == "2 baseline measurements"
    )
    assert plan_headline([fix_item(), eval_only_item(), measure_item()]) == (
        "1 experiment, 1 eval-only, 1 baseline measurement"
    )


# ---- tracker fold ----


def test_wave_lifecycle_states_outcome_and_elapsed():
    clock = ManualClock(100.0)
    tracker = make_tracker(clock)
    start_wave(tracker)

    status = tracker.snapshot()
    assert [w.wave for w in status.waves] == [1]
    wave = status.waves[0]
    assert wave.plan_headline == "1 experiment, 1 eval-only"
    assert wave.rejected_summary == "nothing else worth it"
    assert [(i.item_id, i.kind, i.label, i.live_state) for i in wave.items] == [
        ("w1-1", "fix", "retry-skill (issue_1)", "planned"),
        ("w1-2", "eval", "build eval for issue_2", "planned"),
    ]

    tracker.event("item_state", item_id="w1-1", state="building_rc")
    clock.t = 130.0
    snap = tracker.snapshot()
    assert snap.waves[0].items[0].live_state == "building_rc"
    assert snap.waves[0].items[0].elapsed_secs == 30.0  # in-flight, fresh

    clock.t = 150.0
    tracker.event("item_outcome", item_id="w1-1", outcome=completed_outcome())
    clock.t = 999.0  # done items keep their landing-time elapsed
    item = tracker.snapshot().waves[0].items[0]
    assert item.live_state == "done"
    assert item.elapsed_secs == 50.0
    assert item.outcome is not None
    assert item.outcome.status == "completed"


def test_spend_accumulates_per_item_and_run_total():
    tracker = make_tracker()
    start_wave(tracker)
    tracker.event("spend", source="batch_plan", cost_usd=0.5)  # item-less
    tracker.event("spend", item_id="w1-1", source="create_experiment_rc", cost_usd=1.0)
    tracker.event("spend", item_id="w1-1", source="eval_jobs", cost_usd=0.25)
    tracker.event("spend", item_id="unknown", source="eval_jobs", cost_usd=0.25)
    status = tracker.snapshot()
    assert status.spend_total_usd == 2.0
    assert status.waves[0].items[0].spend_usd == 1.25


def test_spend_fold_retains_source_and_lane():
    # The fold keeps its dimensions — per-lane totals
    # plus the per-source × per-lane table data the report appendix renders.
    tracker = make_tracker()
    sub, prov = "subscription_equivalent", "provider"
    tracker.event("spend", source="batch_plan", lane=sub, cost_usd=0.5)
    tracker.event("spend", source="batch_plan", lane=sub, cost_usd=0.5)  # wave 2
    tracker.event("spend", source="eval_jobs", lane=prov, cost_usd=1.25)
    tracker.event("spend", source="val_pass", lane=prov, cost_usd=0.75)
    status = tracker.snapshot()
    assert status.spend_total_usd == 3.0
    assert status.spend_by_lane == {sub: 1.0, prov: 2.0}
    assert status.spend_by_source == {
        "batch_plan": {sub: 1.0},
        "eval_jobs": {prov: 1.25},
        "val_pass": {prov: 0.75},
    }


def test_spend_without_source_or_lane_folds_under_unknown():
    # Events are an audit surface first — money without dimensions is never
    # dropped from the books.
    tracker = make_tracker()
    tracker.event("spend", cost_usd=0.4)
    status = tracker.snapshot()
    assert status.spend_total_usd == 0.4
    assert status.spend_by_lane == {"unknown": 0.4}
    assert status.spend_by_source == {"unknown": {"unknown": 0.4}}


# Every source the loop emits, and which side of the Claude-Code/Kiln split it
# is on. A new `spend` emitter has to join `schemas.lane_for_source` — and this
# list is what fails when it doesn't.
LANELESS_SOURCES = [
    ("preflight", "subscription_equivalent"),
    ("describe_eval", "subscription_equivalent"),
    ("batch_plan", "subscription_equivalent"),
    ("eval_build", "subscription_equivalent"),
    ("create_experiment_rc", "subscription_equivalent"),
    ("analyze_experiment_results", "subscription_equivalent"),
    ("promote_best", "subscription_equivalent"),
    ("report_writer", "subscription_equivalent"),
    ("eval_jobs", "provider"),
    ("val_pass", "provider"),
]


@pytest.mark.parametrize("source,lane", LANELESS_SOURCES)
def test_a_laneless_spend_event_recovers_its_lane_from_the_source(source, lane):
    """61 spend events in the committed runs predate the `lane` field. The
    source has always been there and has always been unambiguous, so the fold
    derives the lane rather than parking $98.83 under "unknown"."""
    tracker = make_tracker()
    tracker.event("spend", source=source, cost_usd=1.0)
    assert tracker.snapshot().spend_by_lane == {lane: 1.0}


def test_an_event_lane_always_beats_the_derived_one():
    # The derivation is a fallback for old logs, never an override: an emitter
    # that says which lane it is on is believed.
    tracker = make_tracker()
    tracker.event(
        "spend", source="eval_jobs", lane="subscription_equivalent", cost_usd=1.0
    )
    assert tracker.snapshot().spend_by_lane == {"subscription_equivalent": 1.0}


def test_an_unknown_source_without_a_lane_stays_unknown():
    tracker = make_tracker()
    tracker.event("spend", source="mystery", cost_usd=0.5)
    assert tracker.snapshot().spend_by_lane == {"unknown": 0.5}


def test_item_spend_keeps_its_own_lane_split():
    tracker = make_tracker()
    start_wave(tracker)
    tracker.event("spend", item_id="w1-1", source="create_experiment_rc", cost_usd=2.0)
    tracker.event("spend", item_id="w1-1", source="eval_jobs", cost_usd=5.0)
    item = tracker.snapshot().waves[0].items[0]
    assert item.spend_usd == 7.0
    assert item.spend_by_lane == {
        "subscription_equivalent": 2.0,
        "provider": 5.0,
    }


def test_spend_split_reads_a_fold_into_claude_code_and_kiln():
    assert spend_split({"subscription_equivalent": 3.0, "provider": 1.0}) == (
        3.0,
        1.0,
        0.0,
    )
    # An unrecognised lane is returned as `other`, never folded into a side.
    assert spend_split({"provider": 1.0, "mystery": 0.25}) == (0.0, 1.0, 0.25)
    assert spend_split({}) == (0.0, 0.0, 0.0)


def test_time_remaining_from_run_start_deadline():
    clock = ManualClock(10.0)
    tracker = make_tracker(clock)
    assert tracker.snapshot().time_remaining_secs == 0.0  # no run_start yet
    tracker.event("run_start", timeout_mins=180, deadline=100.0)
    assert tracker.snapshot().time_remaining_secs == 90.0
    clock.t = 400.0
    assert tracker.snapshot().time_remaining_secs == 0.0  # clamped, never negative


def test_elapsed_secs_ticks_from_tracker_construction():
    # The totals row must be truthful from its first frame — the
    # run starts when the tracker is built, not when run_start arrives.
    clock = ManualClock(10.0)
    tracker = make_tracker(clock)
    assert tracker.snapshot().elapsed_secs == 0.0
    clock.t = 70.0
    assert tracker.snapshot().elapsed_secs == 60.0  # ticks with no events at all
    tracker.event("run_start", timeout_mins=180, deadline=10_800.0)
    clock.t = 130.0
    assert tracker.snapshot().elapsed_secs == 120.0


def test_timeout_mins_folded_from_run_start():
    tracker = make_tracker()
    assert tracker.snapshot().timeout_mins is None  # no run_start yet
    tracker.event("run_start", timeout_mins=60, deadline=3600.0)
    status = tracker.snapshot()
    assert status.timeout_mins == 60
    assert status.time_remaining_known is True
    # A float budget still labels the run in whole minutes (the schema's int).
    tracker.event("run_start", timeout_mins=90.0, deadline=5400.0)
    assert tracker.snapshot().timeout_mins == 90


def test_a_junk_budget_never_suppresses_a_computable_time_left():
    # Events are an audit surface first. The deadline alone decides whether
    # "time left" is knowable — a garbage `timeout_mins` must not hide a
    # figure the tracker can compute (it only costs the budget label).
    clock = ManualClock(10.0)
    tracker = make_tracker(clock)
    tracker.event("run_start", timeout_mins="soon", deadline=100.0)
    status = tracker.snapshot()
    assert status.timeout_mins is None
    assert status.time_remaining_known is True
    assert status.time_remaining_secs == 90.0


def test_a_junk_deadline_leaves_time_left_unknown():
    # The converse skew, and the worse one: without a deadline
    # `time_remaining_secs` is a meaningless 0.0, which renderers must not
    # show as "0s left" — a valid budget alone does not make it knowable.
    tracker = make_tracker()
    tracker.event("run_start", timeout_mins=180, deadline=None)
    status = tracker.snapshot()
    assert status.timeout_mins == 180
    assert status.time_remaining_known is False
    assert status.time_remaining_secs == 0.0


def test_baseline_history_seeds_and_appends_promotions():
    tracker = make_tracker()
    start_wave(tracker, wave=1)
    tracker.event(
        "promotion",
        winner_rc_id="rc_cand",
        rationale="+9%",
        decision=PromotionDecision(
            winner_rc_id="rc_cand", winner_experiment_memory_id=None, rationale="+9%"
        ),
    )
    start_wave(tracker, wave=2, baseline_rc_id="rc_cand")  # consecutive dedup
    tracker.event(
        "promotion",
        winner_rc_id=None,
        rationale="no winner",
        decision=PromotionDecision(
            winner_rc_id=None, winner_experiment_memory_id=None, rationale="no winner"
        ),
    )
    status = tracker.snapshot()
    assert status.baseline_history == ["rc_base", "rc_cand"]
    assert status.waves[0].promotion is not None
    assert status.waves[0].promotion.winner_rc_id == "rc_cand"
    assert status.waves[1].promotion is not None
    assert status.waves[1].promotion.winner_rc_id is None


def test_eval_progress_tripwire_and_exit():
    tracker = make_tracker()
    start_wave(tracker)
    tracker.event("eval_progress", item_id="w1-1", done=12, total=20)
    tracker.event("tripwire", wave=1, error_class="rate_limit", failed=3)
    tracker.event("exit", reason="tripwire")
    status = tracker.snapshot()
    assert status.waves[0].items[0].eval_progress == (12, 20)
    assert status.waves[0].tripwire == "3× rate_limit"
    assert status.exit_reason == "tripwire"


def test_snapshot_is_isolated_and_unknown_events_ignored():
    tracker = make_tracker()
    tracker.event("val_fill_failed", eval_id="eval_1", rc_id="rc_base")  # ignored
    tracker.event("item_state", item_id="w1-1", state="analyzing")  # no wave yet
    start_wave(tracker)
    snap = tracker.snapshot()
    snap.waves[0].items[0].live_state = "done"
    snap.baseline_history.append("rc_mutated")
    fresh = tracker.snapshot()
    assert fresh.waves[0].items[0].live_state == "planned"
    assert fresh.baseline_history == ["rc_base"]


def test_phase_fold_ticks_elapsed_and_clears_on_end():
    # The loop phase (planning / promotion gate / …) is live
    # state with its own snapshot-computed elapsed ticker.
    clock = ManualClock(50.0)
    tracker = make_tracker(clock)
    assert tracker.snapshot().phase is None
    tracker.event(
        "phase_start", name="planning", wave=1, label="Planning wave 1… (3-10 min)"
    )
    clock.t = 175.0
    snap = tracker.snapshot()
    assert snap.phase == "planning"
    assert snap.phase_detail == "Planning wave 1… (3-10 min)"
    assert snap.phase_elapsed_secs == 125.0
    tracker.event("phase_end", name="planning", wave=1, elapsed_secs=125.0)
    snap = tracker.snapshot()
    assert snap.phase is None
    assert snap.phase_detail == ""
    assert snap.phase_elapsed_secs == 0.0


def test_phase_start_without_label_falls_back_to_name():
    tracker = make_tracker()
    tracker.event("phase_start", name="promotion_gate")
    assert tracker.snapshot().phase_detail == "promotion_gate"


def test_item_phase_secs_folds_state_durations():
    # Where the item's wall clock went, per state.
    clock = ManualClock(100.0)
    tracker = make_tracker(clock)
    start_wave(tracker)  # both items enter "planned" at t=100
    clock.t = 130.0
    tracker.event("item_state", item_id="w1-1", state="building_rc")
    clock.t = 150.0
    tracker.event("item_state", item_id="w1-1", state="measuring")
    clock.t = 180.0
    tracker.event("item_outcome", item_id="w1-1", outcome=completed_outcome())
    clock.t = 999.0  # done items keep their landing-time durations
    item = tracker.snapshot().waves[0].items[0]
    assert item.phase_secs == {"planned": 30.0, "building_rc": 20.0, "measuring": 30.0}
    # The sibling never transitioned — snapshot shows its in-flight planned
    # stretch, still ticking.
    sibling = tracker.snapshot().waves[0].items[1]
    assert sibling.phase_secs == {"planned": 899.0}


def test_item_phase_secs_snapshot_partial_never_double_folds():
    clock = ManualClock(100.0)
    tracker = make_tracker(clock)
    start_wave(tracker)
    clock.t = 130.0
    tracker.event("item_state", item_id="w1-1", state="building_rc")
    clock.t = 140.0
    snap = tracker.snapshot().waves[0].items[0]
    assert snap.phase_secs == {"planned": 30.0, "building_rc": 10.0}  # in flight
    # The partial lives on the copy only: the real fold at outcome is exact.
    clock.t = 150.0
    tracker.event("item_outcome", item_id="w1-1", outcome=completed_outcome())
    item = tracker.snapshot().waves[0].items[0]
    assert item.phase_secs == {"planned": 30.0, "building_rc": 20.0}


def test_phase_segments_folded_from_phase_events():
    # Run-level "where did the time go" spans. In-wave
    # phases (planning, the gate) carry their wave; wave-less phases
    # (preflight, reporting) fold with wave=None.
    tracker = make_tracker()
    tracker.event("phase_start", name="planning", wave=1, label="Planning wave 1…")
    tracker.event("phase_end", name="planning", wave=1, elapsed_secs=125.0)
    tracker.event("phase_start", name="promotion_gate", wave=1, label="gate…")
    tracker.event("phase_end", name="promotion_gate", wave=1, elapsed_secs=40.5)
    tracker.event("phase_start", name="reporting", label="Writing report…")
    tracker.event("phase_end", name="reporting", elapsed_secs=90.0)
    segments = tracker.snapshot().phase_segments
    assert [(s.name, s.wave, s.elapsed_secs) for s in segments] == [
        ("planning", 1, 125.0),
        ("promotion_gate", 1, 40.5),
        ("reporting", None, 90.0),
    ]


def test_late_item_state_after_outcome_is_ignored():
    # A straggler item_state after the outcome landed must not
    # reopen the item or restart its state clock — the finding line and the
    # folded phase_secs are frozen.
    clock = ManualClock(100.0)
    tracker = make_tracker(clock)
    start_wave(tracker)
    clock.t = 150.0
    tracker.event("item_outcome", item_id="w1-1", outcome=completed_outcome())
    clock.t = 160.0
    tracker.event("item_state", item_id="w1-1", state="analyzing")  # late
    clock.t = 999.0
    item = tracker.snapshot().waves[0].items[0]
    assert item.live_state == "done"
    assert item.phase_secs == {"planned": 50.0}  # no ticking analyzing partial
    assert item.elapsed_secs == 50.0


def test_phase_end_without_elapsed_falls_back_to_tracker_clock():
    clock = ManualClock(50.0)
    tracker = make_tracker(clock)
    tracker.event("phase_start", name="reporting")
    clock.t = 80.0
    tracker.event("phase_end")  # bare — name and elapsed from tracker state
    (segment,) = tracker.snapshot().phase_segments
    assert (segment.name, segment.wave, segment.elapsed_secs) == (
        "reporting",
        None,
        30.0,
    )


def test_baseline_found_seeds_history_and_wave_start_dedups():
    tracker = make_tracker()
    tracker.event("baseline_found", rc_id="rc_base", provenance="task default")
    assert tracker.snapshot().baseline_history == ["rc_base"]
    start_wave(tracker)  # same rc — consecutive dedup
    assert tracker.snapshot().baseline_history == ["rc_base"]


def test_janitor_fold():
    tracker = make_tracker()
    assert tracker.snapshot().janitor_stamped == []
    tracker.event("janitor", count=2, memory_ids=["mem_1", "mem_2"])
    assert tracker.snapshot().janitor_stamped == ["mem_1", "mem_2"]


def test_setup_fold_carries_the_whole_report():
    # The event's counts are for the UI line; the report writer needs the ids,
    # so the full object rides along.
    tracker = make_tracker()
    assert tracker.snapshot().setup is None
    report = SetupReport(evals_total=3, evals_active=2, seeded_eval_ids=["e1"])

    tracker.event("setup_sync", seeded=1, report=report)

    assert tracker.snapshot().setup == report


def test_setup_ok_folds_the_same_way():
    # The warm path is still worth reporting: the baseline rule and any
    # unbound issues ride on the same object.
    tracker = make_tracker()
    report = SetupReport(evals_total=3, evals_active=3, baseline_rule="task_default")

    tracker.event("setup_ok", report=report)

    assert tracker.snapshot().setup == report


def test_each_eval_kiln_cant_load_is_recorded():
    tracker = make_tracker()
    tracker.event(
        "eval_unloadable", eval_id="e1", name="Tone", reason="bad", line="Kiln …"
    )
    tracker.event("eval_unloadable", eval_id="e2", reason="worse")
    assert tracker.snapshot().unloadable_evals == [
        UnloadableEval(eval_id="e1", name="Tone", reason="bad"),
        UnloadableEval(eval_id="e2", name="e2", reason="worse"),
    ]


def test_eval_building_and_dropped_eval_items_fold_for_the_report():
    tracker = make_tracker()
    assert tracker.snapshot().eval_building is False
    tracker.event("run_start", deadline=100.0, eval_building=True)
    tracker.event(
        "eval_item_dropped",
        wave=2,
        item_id="w2-1",
        item_kind="eval",
        issue_memory_id="issue_9",
        line="dropped w2-1: …",
    )
    # An event without a wave is not a record of a dropped item.
    tracker.event("eval_item_dropped", item_id="w2-2", issue_memory_id="issue_8")
    status = tracker.snapshot()
    assert status.eval_building is True
    assert status.dropped_eval_items == [
        DroppedEvalItem(item_id="w2-1", wave=2, kind="eval", issue_memory_id="issue_9")
    ]


def test_a_setup_event_without_a_report_is_ignored():
    # Events are an audit surface first: a hand-written or older line must
    # not blank the field (every handler tolerates missing fields).
    tracker = make_tracker()
    tracker.event("setup_ok", report=SetupReport(evals_total=1))
    tracker.event("setup_ok")
    assert tracker.snapshot().setup is not None


def test_item_detail_set_and_cleared_on_state_change_and_outcome():
    tracker = make_tracker()
    start_wave(tracker)
    tracker.event("item_state", item_id="w1-1", state="measuring")
    tracker.event("item_detail", item_id="w1-1", detail="measuring eval_1 (1/2)")
    assert tracker.snapshot().waves[0].items[0].detail == "measuring eval_1 (1/2)"
    # A state change invalidates the sub-detail.
    tracker.event("item_state", item_id="w1-1", state="analyzing")
    assert tracker.snapshot().waves[0].items[0].detail is None
    tracker.event("item_detail", item_id="w1-1", detail="stale")
    tracker.event("item_outcome", item_id="w1-1", outcome=completed_outcome())
    assert tracker.snapshot().waves[0].items[0].detail is None
    # Detail after done is ignored (the finding line is frozen).
    tracker.event("item_detail", item_id="w1-1", detail="late")
    assert tracker.snapshot().waves[0].items[0].detail is None


def test_promotion_skipped_noted_on_wave():
    # promote_best hard-fail → noted in RunStatus/the report.
    tracker = make_tracker()
    tracker.event("promotion_skipped", reason="early, no wave")  # ignored safely
    start_wave(tracker)
    tracker.event("promotion_skipped", reason="promote_best returned no decision")
    wave = tracker.snapshot().waves[0]
    assert wave.promotion_skipped == "promote_best returned no decision"
    assert wave.promotion is None


# ---- incident counters + budget fold ----


def test_incident_counters_fold_from_events():
    tracker = make_tracker()
    tracker.event(
        "eval_job_errors",
        eval_id="e1",
        rc_id="rc",
        split="train",
        attempt=1,
        error_class="rate_limit",
        errors=[{"dataset_id": "i1"}, {"dataset_id": "i2"}],
    )
    tracker.event(
        "eval_job_errors",
        eval_id="e1",
        rc_id="rc",
        split="train",
        attempt=2,
        error_class="rate_limit",
        errors=[{"dataset_id": "i1"}],
    )
    tracker.event("eval_job_errors", eval_id="e2", rc_id="rc", split="val", errors=[{}])
    tracker.event("eval_job_errors", eval_id="e3", rc_id="rc", split="val", errors=[])
    tracker.event("val_fill_failed", eval_id="e2", rc_id="rc", detail="zero rows")
    status = tracker.snapshot()
    assert status.eval_job_error_items == 4
    assert status.eval_job_error_classes == {
        "train rate_limit": 3,
        "val unclassified": 1,
    }
    assert status.val_fill_failures == 1


def test_budget_usd_folds_from_run_start():
    tracker = make_tracker()
    tracker.event("run_start", deadline=100.0, timeout_mins=10, budget_usd=25.0)
    assert tracker.snapshot().budget_usd == 25.0
    # Absent stays None — a run without a cost ceiling must not show one.
    assert make_tracker().snapshot().budget_usd is None


def test_promotion_blocked_lands_in_the_wave_block():
    tracker = make_tracker()
    tracker.event(
        "wave_start",
        wave=1,
        items=[{"item_id": "w1-1", "kind": "fix", "label": "x"}],
    )
    tracker.event(
        "promotion_blocked",
        winner_rc_id="rc_9",
        reasons=["eval e1 candidate-side val coverage 18/25 is below the 95% floor"],
    )
    status = tracker.snapshot()
    assert status.waves[-1].promotion_skipped.startswith("blocked: winner rc_9")
    assert "below the 95% floor" in status.waves[-1].promotion_skipped


def test_item_change_is_folded_onto_the_item():
    from kiln_harness_tuner.status import RunStatusTracker

    # The build agent's structured "what changed" rides an
    # `item_change` event and lands on the item for every UI and the report;
    # a malformed payload is ignored, never a crash.
    tracker = RunStatusTracker(
        project_id="p", task_id="t", guidance="", clock=lambda: 0.0
    )
    tracker.event("run_start", timeout_mins=10, deadline=600.0)
    tracker.event(
        "wave_start",
        wave=1,
        should_wrap_up=False,
        baseline_rc_id="rc_base",
        plan_headline="1 experiment",
        rejected_summary="",
        items=[{"item_id": "w1-1", "kind": "fix", "label": "x"}],
    )
    tracker.event(
        "item_change",
        item_id="w1-1",
        change={"summary": "s", "changes": ["a", "b"], "unchanged": ["model"]},
    )
    (item,) = tracker.snapshot().waves[0].items
    assert item.change is not None
    assert item.change.summary == "s"
    assert item.change.changes == ["a", "b"]
    tracker.event("item_change", item_id="w1-1", change="not a dict")
    assert tracker.snapshot().waves[0].items[0].change.summary == "s"
