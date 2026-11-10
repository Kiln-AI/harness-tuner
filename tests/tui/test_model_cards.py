"""The Experiments section's cards and modes (functional spec 7.6 and 16;
ui_design 7.5 and 14)."""

import pytest
from model_events import (
    START_NAME,
    START_RC,
    Run,
    eval_only,
    fix,
    measure,
    merge,
    probe,
    remeasure,
    row,
    table,
    val_row,
)

from kiln_harness_tuner.schemas import ProbeResult
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.state import GaugeLine, NoteLine, StripView, plain

FIX_LATER = (("build", "later"), ("measure", "later"), ("analyze", "later"))


def wave_one(*items) -> Run:
    run = Run().standard_start()
    run.at(10).plan(1).at(12).planned(1)
    return run.wave_start(1, *items)


def test_a_queued_fix_card():
    run = wave_one(fix("w1-1", "e1", technique="tool_config"))
    card = run.card("w1-1")
    assert (card.title, card.qualifier) == ("Hold placement", "tool config")
    assert card.text == "Name the hold fields in the prompt"
    assert card.steps == FIX_LATER
    assert card.line4 == NoteLine("waiting for an agent slot")
    assert card.line5 == ()
    assert (card.subtitle, card.live) == ("w1-1", False)


def test_a_building_card_shows_the_time_in_its_state():
    run = wave_one(fix("w1-1"))
    run.at(13).state("w1-1", "building_rc")
    # An agent session books tokens, not dollars: the card shows no money.
    run.emit("spend", item_id="w1-1", source="create_experiment_rc", tokens=900)
    run.at(16).emit("phase_start", name="other")  # moves the clock only
    card = run.card("w1-1")
    assert card.steps[0] == ("build", "current")
    assert card.line4 == NoteLine("an agent is writing the change · 3m00s")
    assert (card.subtitle, card.live) == ("w1-1", True)
    run.emit("spend", item_id="w1-1", source="eval_jobs", cost_usd=0.4)
    assert run.card("w1-1").subtitle == "w1-1 · $0.40"


def test_a_rebuild_shows_the_check_that_forced_it():
    run = wave_one(fix("w1-1"))
    run.at(13).state("w1-1", "building_rc").state("w1-1", "reviewing")
    run.emit(
        "judge_copy", item_id="w1-1", rc_id="rc_a", status="copied", findings=["x"]
    )
    run.at(14).state("w1-1", "building_rc")
    card = run.card("w1-1")
    assert card.line4 == NoteLine("rebuilding once · copied text found · 0s", "warning")
    assert "w1-1: copied judge text, rebuilding once" in run.log()


def test_a_measuring_card_shows_its_strip_and_counts_so_far():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "building_rc").state("w1-1", "measuring")
    run.chunk("w1-1", "rc_a", [(False, True), (True, True), (False, False)])
    run.chunk("w1-1", "rc_a", [(True, False), (None, True)])
    card = run.card("w1-1")
    assert card.steps == (
        ("build", "done"),
        ("measure", "current"),
        ("analyze", "later"),
    )
    assert card.line4 == StripView(
        ("fixed", "held", "failing", "broke", "unknown"), 36, "5/36"
    )
    assert plain(card.line5) == "1 fixed · 1 broke so far"
    assert [s.tone for s in card.line5] == ["success", "muted", "error", "muted"]
    run.state("w1-1", "analyzing")
    assert plain(run.card("w1-1").line5) == "1 fixed · 1 broke"


def test_a_measuring_card_before_its_first_chunk():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "measuring")
    assert run.card("w1-1").line4 == NoteLine("measuring · 0s")
    run.emit("queue_drawn", item_id="w1-1", size=24, seed="s", queue=[])
    assert run.card("w1-1").line4 == StripView((), 24, "0/24")


def test_landed_success_cards():
    run = wave_one(fix("w1-1"), fix("w1-2", candidate=False), fix("w1-3"))
    run.success("w1-1", "rc_a", row())
    run.success("w1-2", "rc_b", row())
    run.state("w1-3", "analyzing").outcome("w1-3", rc_id="rc_c", successful=False)
    card = run.card("w1-1")
    assert plain(card.line5) == "● Success · goes to the gate"
    assert card.line5[0].tone == "success"
    assert card.steps[-1] == ("gate", "later")
    assert run.line5("w1-2") == "● Success"
    assert [name for name, _ in run.card("w1-2").steps] == [
        "build",
        "measure",
        "analyze",
    ]
    assert run.line5("w1-3") == "○ No win"
    assert all(state == "done" for _, state in run.card("w1-3").steps)


def test_a_card_at_the_gate_shows_its_val_progress():
    run = wave_one(fix("w1-1"))
    run.success("w1-1", "rc_a", row())
    run.gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a", end=False)
    # Before a fill lands, its eval's val size stands in (50 per eval).
    card = run.card("w1-1")
    assert card.line4 == GaugeLine(0, 100, "0 of 100 val items")
    assert card.live
    run.emit(
        "gate_fill",
        rc_id="rc_a",
        eval_id="e1",
        role="candidate",
        stored=48,
        expected=52,
        missing=4,
        clean=False,
        done=1,
        total=4,
    )
    assert run.card("w1-1").line4 == GaugeLine(48, 102, "48 of 102 val items")
    assert run.line5("w1-1") == "● Success · goes to the gate"
    header = run.model.experiments(run.t).header
    assert (
        plain(header) == "Promotion gate · wave 1 · every eval on val · 1 at the gate"
    )


def test_held_and_blocked_cards_after_the_gate():
    run = wave_one(fix("w1-1"), fix("w1-2"))
    run.success("w1-1", "rc_a", row())
    run.success("w1-2", "rc_b", row())
    run.emit(
        "open_questions",
        wave=1,
        phase="plan",
        questions=[
            {
                "question": "may a post hold links",
                "default_assumed": "no",
                "gates": "",
                "reask_when": "",
                "constraint_id": "c1",
            }
        ],
    )
    tables = (
        table("rc_a", val_row("e1", 0.6, 0.7)),
        table("rc_b", val_row("e1", 0.6, 0.7)),
    )
    run.gate(1, *tables, winner=None, blocked_on="c1", end=False)
    run.emit("gate_block", rc_id="rc_b", exp_mem_id="exp", reasons=["copies"])
    run.gate_end(1)
    assert run.line5("w1-1") == "? Held: waiting on an open question"
    assert run.card("w1-1").steps[-1] == ("gate", "later")
    assert run.line5("w1-2") == "✗ Blocked: copies eval text"
    assert "w1-1 held: waiting on the open question “may a post hold links”" in (
        run.log()
    )
    # The gate has decided: the cards are back under Running.
    header = plain(run.model.experiments(run.t).header)
    assert header == "Running · wave 1 from start · 2 landed"
    assert "w1-2 blocked: copies eval text" in run.log()


def test_a_merge_card():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1"), fix("w1-2"))
    run.success("w1-1", "rc_a", row()).success("w1-2", "rc_b", row())
    run.plan(2).planned(2)
    run.wave_start(2, merge("w2-1", "rc_a", "rc_b"), parent=START_RC)
    card = run.card("w2-1")
    assert (card.title, card.qualifier) == ("All evals", "merge")
    assert card.text == "Combine w1-1 and w1-2 on top of start"


@pytest.mark.parametrize(
    "answer,line5,tone",
    [
        ("yes", "● YES · 3 drives", "success"),
        ("no", "○ NO · 3 drives", "muted"),
        ("unclear", "○ UNCLEAR · 1 drive", "muted"),
    ],
)
def test_a_probe_card(answer, line5, tone):
    run = wave_one(probe("w1-1"))
    run.state("w1-1", "probing")
    card = run.card("w1-1")
    assert (card.title, card.qualifier) == ("Question", "probe")
    assert card.text == "Does the agent call get_container?"
    assert card.steps == (("asking", "current"), ("answered", "later"))
    assert card.line4 == NoteLine("3 drives")
    drives = 1 if answer == "unclear" else 3
    result = ProbeResult(answer=answer, evidence="seen", drives=drives)
    run.emit("item_probe", item_id="w1-1", probe=result.model_dump())
    run.outcome("w1-1")
    card = run.card("w1-1")
    assert plain(card.line5) == line5
    assert card.line5[0].tone == tone
    assert card.steps == (("asking", "done"), ("answered", "done"))
    assert f"w1-1 Does the agent call get_container? {line5[2:]}" in run.log()


def test_an_eval_build_card():
    run = wave_one(eval_only("w1-1"))
    run.state("w1-1", "building_eval")
    card = run.card("w1-1")
    assert (card.title, card.qualifier) == ("New eval", "eval build")
    assert card.text == "Build an eval for 424242500009"
    assert (card.steps, card.line4) == ((("build", "current"),), None)
    run.outcome("w1-1")
    assert run.line5("w1-1") == "✓ Eval built"
    assert "w1-1 eval build: eval built" in run.log()


@pytest.mark.parametrize(
    "detail,line5",
    [
        ("reproduced on baseline rc (3 of 5 fail)", "✓ Reproduced"),
        ("not reproduced on baseline rc (0 of 5 fail)", "○ Not reproduced"),
    ],
)
def test_an_issue_baseline_measurement_card(detail, line5):
    run = wave_one(measure("w1-1", "e2"))
    run.at(20).state("w1-1", "measuring")
    run.at(23).emit("item_detail", item_id="w1-1", detail=detail)
    card = run.card("w1-1")
    assert (card.title, card.qualifier) == ("Refusal honesty", "measure")
    assert card.text == "Measure 424242500010 on the baseline"
    assert card.line4 == NoteLine("measuring · 3m00s")
    run.outcome("w1-1")
    assert (run.line5("w1-1"), run.card("w1-1").line4) == (line5, None)


def test_a_re_measure_card():
    run = wave_one(remeasure("w1-1", START_RC))
    run.state("w1-1", "measuring")
    card = run.card("w1-1")
    assert (card.title, card.qualifier) == ("Hold placement", "re-measure")
    assert card.text == f"Complete the measurement of {START_NAME}"
    run.outcome("w1-1")
    assert run.line5("w1-1") == "✓ Measured"


def test_cut_off_cards():
    run = wave_one(fix("w1-1"), fix("w1-2"), fix("w1-3"))
    run.state("w1-1", "measuring").state("w1-3", "analyzing")
    run.emit("deadline_grace", wave=1, item_ids=["w1-3"], grace_secs=600)
    run.emit("deadline_cancel", wave=1, item_ids=["w1-1", "w1-2"])
    assert run.line5("w1-1") == "✗ Cut off: the run ended"
    assert run.card("w1-1").steps[1] == ("measure", "stopped")
    assert not run.card("w1-1").live
    assert run.line5("w1-2") == "✗ Cut off: the run ended"
    assert run.card("w1-3").live
    assert "Cut off at the deadline: w1-1, w1-2" in run.log()
    assert "Deadline: w1-3 get 10m00s to finish their analysis" in run.log()


STOPS = [
    ("not_landed", "futility", "Stopped: can't beat its parent", None),
    (
        "not_landed",
        "eval_metric",
        "Stopped: quality clearly worse",
        "Stopped: quality clearly worse than its parent",
    ),
    ("not_landed", "residual", "Stopped: fixed too few of its targets", None),
    ("not_landed", "targets", "Stopped: a named target still fails", None),
    (
        "not_landed",
        "latency_ratio",
        "Stopped: clearly slower",
        "Stopped: clearly slower than its parent",
    ),
    (
        "not_landed",
        "cost_ratio",
        "Stopped: clearly costlier",
        "Stopped: clearly costlier than its parent",
    ),
    ("not_landed", "not_better", "Stopped: not clearly faster", None),
    (
        "not_landed",
        "guard_losses",
        "Stopped: broke too many passing items",
        "Stopped: broke too many items that passed",
    ),
    ("not_landed", "invalid_output_rate", "Stopped: too many invalid outputs", None),
    ("not_landed", "max_items", "Stopped: reached its item limit", None),
    ("not_landed", "something_new", "Stopped early", None),
    ("dead_end", "lever_not_applied", "Stopped: the change did not take effect", None),
    (
        "capped",
        None,
        "Stopped: spending cap, inconclusive",
        "Stopped: reached its spending cap (inconclusive)",
    ),
]


@pytest.mark.parametrize("status,stop_kind,card,log", STOPS)
def test_verdict_words_for_every_stop(status, stop_kind, card, log):
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "measuring")
    run.outcome("w1-1", status, rc_id="rc_a", stop_kind=stop_kind)
    assert run.line5("w1-1") == f"✗ {card}"
    assert run.card("w1-1").steps[1] == ("measure", "stopped")
    assert run.log()[-1] == f"w1-1 Hold placement: {words.log_verdict(log or card)}"


def test_not_better_says_cheaper_under_cost():
    run = wave_one(fix("w1-1"))
    run.model.objective = "cost"
    run.outcome("w1-1", "not_landed", rc_id="rc_a", stop_kind="not_better")
    assert run.line5("w1-1") == "✗ Stopped: not clearly cheaper"


def test_dead_end_reason_comes_from_the_last_detail():
    run = wave_one(fix("w1-1"), fix("w1-2"))
    run.state("w1-1", "building_rc")
    run.emit("item_detail", item_id="w1-1", detail="dead end: the knob is inert")
    run.outcome("w1-1", "dead_end", rc_id="rc_a")
    assert run.line5("w1-1") == "✗ Dead end: the knob is inert"
    assert run.card("w1-1").steps[0] == ("build", "stopped")
    run.outcome("w1-2", "dead_end")
    assert run.line5("w1-2") == "✗ Dead end"


def test_an_infra_failure_names_its_error_class():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "analyzing")
    run.outcome("w1-1", "failed_infra", rc_id="rc_a", error_class="rate_limit")
    assert run.line5("w1-1") == "✗ Failed: rate limit"
    assert run.card("w1-1").steps[2] == ("analyze", "stopped")


# ---- the modes ----


def test_starting_mode_shows_the_checklist_then_the_recon_cards():
    run = Run()
    view = run.model.experiments(run.t)
    assert view.mode == "starting"
    assert [(s.status, s.label, s.text) for s in view.steps] == [
        ("busy", "Agent CLI", "testing claude -p · 40s")
    ]
    run.model.agent_cli_checked(run.t - 2)
    run.emit(
        "run_start",
        timeout_mins=180,
        deadline=run.t + 10800,
        deadline_iso="2026-11-09T17:12:00",
        budget_usd=50.0,
        objective="quality",
    )
    run.emit("phase_start", name="preflight", label="")
    run.at(1).emit("phase_end", name="preflight", elapsed_secs=60.0)
    run.emit("phase_start", name="setup", label="")
    run.emit(
        "setup_sync",
        evals_total=6,
        evals_active=5,
        seeded=2,
        refreshed=0,
        repaired=1,
        unmeasurable=0,
        unbound_unresolved=0,
    )
    run.emit("eval_unloadable", eval_id="e7", name="Old", reason="r", line="Kiln can't")
    run.emit("phase_end", name="setup", elapsed_secs=30.0)
    run.emit("baseline_found", rc_id=START_RC)
    run.emit("phase_start", name="baseline_measure", label="")
    run.at(2).emit(
        "baseline_measured",
        rc_id=START_RC,
        line="",
        evals={"e1": {"stored": 100, "expected": 100, "how": "stored"}},
    )
    run.detectability(
        warnings=["Output format can only guard: 4 failures, a win needs 6"]
    )
    run.emit("noise_estimate", estimate_usd=1.5, line="")
    run.at(3).wave_start(0, probe("r0-1"), probe("r0-2"), headline="2 probes")
    run.state("r0-1", "probing")
    run.emit(
        "item_probe",
        item_id="r0-1",
        probe={"answer": "yes", "evidence": "", "drives": 3},
    )
    run.outcome("r0-1")
    view = run.model.experiments(run.at(5).t)
    assert [(s.status, s.label, s.text) for s in view.steps] == [
        ("ok", "Agent CLI", "claude -p answered the test prompt · 38s"),
        ("ok", "Preflight", "the agents can reach Kiln"),
        ("ok", "Setup", "5 of 6 evals active · 2 evals seeded, 1 issue repaired"),
        ("warn", "", "Kiln can't"),
        ("ok", "Baseline", f"{START_NAME} · train scores read from Kiln"),
        (
            "warn",
            "Detection",
            "Output format can only guard: 4 failures, a win needs 6",
        ),
        ("busy", "Noise", "measuring run-to-run noise · about $1.50 · 3m00s"),
        ("busy", "Recon", "1 of 2 probes answered · 2m00s"),
    ]
    assert plain(view.header) == " Starting · wave 0, recon"
    assert view.subhead == "WAVE 0 · recon probes"
    assert [c.item_id for c in view.cards] == ["r0-1", "r0-2"]
    assert view.note == "Wave 1 is planned when the recon probes finish."
    run.emit("item_probe", item_id="r0-2", probe={"answer": "no", "evidence": ""})
    run.outcome("r0-2")
    run.emit("wave_end", wave=0)
    view = run.model.experiments(run.t)
    assert view.steps[-1].text == "2 probes answered"
    assert view.note == ""


def test_planning_mode_shows_the_planner_steps_over_the_last_wave():
    run = wave_one(fix("w1-1"))
    run.success("w1-1", "rc_a", row())
    run.gate(1, table("rc_a", val_row("e1", 0.6, 0.6)), winner=None)
    run.at(40).plan(2)
    view = run.model.experiments(run.at(42).t)
    assert view.mode == "planning"
    assert plain(view.header) == " Planning · wave 2 from start"
    assert [(s.status, s.label, s.text) for s in view.steps] == [
        ("busy", "Planner", "planning wave 2 · 2m00s")
    ]
    assert view.subhead == "WAVE 1 · landed"
    assert [c.item_id for c in view.cards] == ["w1-1"]
    run.at(43).planned(2)
    run.emit("candidate_budget", item_id=None, rc_id="rc_s1", usd=1.0)
    run.emit("candidate_budget", item_id=None, rc_id="rc_s2", usd=1.0)
    run.emit("queue_drawn", item_id=None, size=16, seed="s", queue=[])
    view = run.model.experiments(run.at(44).t)
    assert [(s.status, s.label, s.text) for s in view.steps] == [
        ("ok", "Planner", "3m00s · asked for a screening round first"),
        # No number until the round's event gives the loop's own.
        ("busy", "Screen", "2 candidates · 16 items each · 1m00s"),
    ]
    run.emit(
        "screening_round",
        round=4,
        line="",
        results=[
            {
                "candidate_id": "s2-1",
                "label": "a",
                "rc_id": "rc_s1",
                "round": 4,
                "evals": [],
                "items": 16,
                "verdict": "survivor",
            },
            {
                "candidate_id": "s2-2",
                "label": "b",
                "rc_id": "rc_s2",
                "round": 4,
                "evals": [],
                "items": 16,
                "verdict": "dead_end",
                "reason": "broke 3",
            },
        ],
    )
    run.plan(2)
    view = run.model.experiments(run.at(45).t)
    assert [(s.status, s.label, s.text) for s in view.steps] == [
        ("ok", "Planner", "3m00s · asked for a screening round first"),
        ("ok", "Screen 4", "2 candidates · 16 items each · 1 survive"),
        ("busy", "Planner", "again, with the screen results · 1m00s"),
    ]
    assert run.log()[-2:] == [
        "Screen round 4: 1 of 2 candidates survive",
        "s2-2 b: dropped, broke 3",
    ]
    run.emit("screening_round", line="", results=[])
    assert run.log()[-1] == "Screen round: 0 of 0 candidates survive"


def test_running_mode_counts_the_cards_by_state():
    run = wave_one(fix("w1-1"), fix("w1-2"), fix("w1-3"), probe("w1-4"), fix("w1-5"))
    run.state("w1-1", "building_rc").state("w1-2", "measuring")
    run.state("w1-3", "analyzing").state("w1-4", "probing")
    run.outcome("w1-5", "dead_end")
    view = run.model.experiments(run.at(20).t)
    assert view.mode == "running"
    assert plain(view.header) == (
        "Running · wave 1 from start · 1 building · 1 measuring · 1 asking"
        " · 1 analyzing · 1 landed"
    )
    tones = {s.text: s.tone for s in view.header}
    assert tones["1 building"] == "primary"
    assert tones["1 landed"] == "fg"
    assert view.elapsed == "8m00s"
    assert run.model.live_count() == 4


def test_done_mode_shows_the_run_summary():
    run = wave_one(fix("w1-1"), fix("w1-2"))
    run.success("w1-1", "rc_a", row())
    run.state("w1-2", "measuring")
    run.outcome("w1-2", "not_landed", rc_id="rc_b", stop_kind="futility")
    run.emit("spend", source="x", lane="provider", cost_usd=12.5)
    run.gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a")
    run.model.names.learn(
        [{"id": "rc_a", "name": "Hold fields", "description": "names them"}]
    )
    run.at(170).emit("exit", reason="wrap_up")
    view = run.model.experiments(run.t)
    done = view.done
    assert view.mode == "done"
    assert plain(view.header) == "Done · 1 wave"
    assert done.ended == "Ended early: not enough time or budget for another wave"
    assert plain(done.summary) == "2h50m · $12.50 of $50.00"
    assert done.totals == (
        ("1", "wave"),
        ("2", "experiments"),
        ("1", "promotion"),
        ("1", "stopped early"),
    )
    assert plain(done.best) == (
        "Best Confirmed  Hold fields · w1-1 · names them · measured on val"
    )
    assert done.report == "writing the report…"
    assert done.next_step == (
        "Next: run your test split on Hold fields · w1-1. The tuner never read it."
    )
    assert done.subtitle == "run 261109-1412"
    run.emit("report_written", path="/r/report.md", fallback=False)
    assert run.model.experiments(run.t).done.report == "/r/report.md"


def test_done_says_how_little_time_was_left():
    run = Run().standard_start()
    run.emit(
        "exit",
        reason="timeout",
        detail="3m46s left after setup, under the 15-minute minimum to start a wave",
    )
    assert run.model.experiments(run.t).done.ended == (
        "Time limit reached (3m46s left after setup, under the 15-minute"
        " minimum to start a wave)"
    )


def test_done_shows_only_a_timeout_detail():
    run = Run().standard_start()
    run.emit("exit", reason="quota_exhausted", detail="429 from the provider")
    assert run.model.experiments(run.t).done.ended == (
        "Stopped on a provider error: quota or authentication"
    )


def test_a_stopped_run_has_no_report():
    run = wave_one(fix("w1-1"))
    run.emit("exit", reason="interrupted")
    done = run.model.experiments(run.t).done
    assert (done.ended, done.report) == ("Stopped by you", "no report")
    assert plain(done.best) == (
        "Best Confirmed  no promotion: the run kept its starting point"
    )
    assert (
        done.next_step
        == f"Next: run your test split on {START_NAME}. The tuner never read it."
    )


def test_a_re_measure_never_names_the_rc_it_completes():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1"))
    run.success("w1-1", "rc_a", row())
    run.gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a")
    run.plan(2).planned(2)
    run.wave_start(2, remeasure("w2-1", "rc_a"), parent="rc_a")
    run.state("w2-1", "measuring").outcome("w2-1")
    assert run.model.rc_label("rc_a") == "w1-1"
    assert run.card("w2-1").text == "Complete the measurement of w1-1"
    assert plain(run.model.results().key[1]) == "■ Best Confirmed · val · w1-1"


def test_the_rebuild_names_the_finding_that_forced_it():
    run = wave_one(fix("w1-1"), fix("w1-2"))
    run.state("w1-1", "building_rc")
    run.emit("judge_copy", item_id="w1-1", rc_id="rc_a", status="copied")
    run.emit("generality", item_id="w1-1", rc_id="rc_a", status="clean")
    # Under the default `--reference-check warn` a finding is a warning only.
    run.emit(
        "reference_check", item_id="w1-1", rc_id="rc_a", mode="warn", status="found"
    )
    run.state("w1-1", "building_rc")
    assert run.card("w1-1").line4.text.startswith("rebuilding once · copied text found")
    assert run.log()[-1] == "w1-1: copied judge text, rebuilding once"
    # Under `refuse` a reference finding forces the rebuild.
    run.state("w1-2", "building_rc")
    run.emit(
        "reference_check", item_id="w1-2", rc_id="rc_b", mode="refuse", status="found"
    )
    run.state("w1-2", "building_rc")
    assert run.log()[-1] == (
        "w1-2: names a skill or tool it does not attach, rebuilding once"
    )


def test_recon_counts_only_the_probes_with_an_answer():
    run = Run().standard_start()
    run.at(3).wave_start(0, probe("r0-1"), probe("r0-2"))
    run.state("r0-1", "probing").state("r0-2", "probing")
    run.emit("item_probe", item_id="r0-1", probe={"answer": "yes", "evidence": ""})
    run.outcome("r0-1")
    run.outcome("r0-2", "failed_infra", error_class="timeout")
    run.emit("wave_end", wave=0)
    recon = run.model.experiments(run.t).steps[-1]
    assert (recon.status, recon.label, recon.text) == (
        "warn",
        "Recon",
        "1 of 2 probes answered",
    )


def test_a_probe_phase_step_counts_only_the_probes_with_an_answer():
    run = Run().standard_start()
    run.at(10).plan(1).at(12).planned(1)
    run.wave_start(1, probe("w1-1"), probe("w1-2"), probe_phase=True)
    run.emit("item_probe", item_id="w1-1", probe={"answer": "no", "evidence": ""})
    run.outcome("w1-1")
    run.emit("deadline_cancel", wave=1, item_ids=["w1-2"])
    run.emit("wave_end", wave=1, probe_phase=True)
    run.plan(1, replan=True)
    steps = run.model.experiments(run.t).steps
    assert [(s.status, s.label, s.text) for s in steps[1:]] == [
        ("warn", "Probes", "1 of 2 probes answered"),
        ("busy", "Planner", "again, with the probe answers · 0s"),
    ]


def test_a_dropped_settled_survivor_is_logged_in_user_words_with_no_screen():
    run = wave_one()
    run.planned(1)
    # The line `viability.drop_settled_rescreens` writes for the planner.
    line = (
        "screen s1-2 haiku, thinking low: not screened again — quality-neutral or"
        " better in 2 screening rounds (last run 261108-0930). Commit it as an"
        " arm (work.existing_rc_id: rc_s), or give a rescreen_reason."
    )
    run.emit("screen_candidate_dropped", wave=1, line=line)
    assert run.log()[-1] == (
        "Screen candidate s1-2 haiku, thinking low: not screened again,"
        " quality-neutral or better in earlier rounds"
    )
    # Quality-neutral or better: a plain run line, not a "No win".
    assert run.model.log[-1].glyph == "·"
    assert "screen" not in run.model.open_segments
    assert [s.kind for s in run.model.plans[1]] == ["plan"]
    run.emit("screen_candidate_dropped", wave=1, line="something else")
    assert run.log()[-1] == (
        "A screen candidate was not screened again:"
        " quality-neutral or better in earlier rounds"
    )
