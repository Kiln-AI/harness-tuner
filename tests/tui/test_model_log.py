"""The Log section's lines (functional spec 7.7; ui_design 7.6)."""

from model_events import Run, fix, probe, row, table, val_row


def test_run_start_start_config_and_exit():
    run = Run().standard_start()
    run.emit("detectability_warning", line="⚠ detectability: Tone can only guard")
    run.at(180).emit("exit", reason="timeout")
    run.emit("report_written", path="/r.md", fallback=False)
    assert run.log() == [
        "Run started · quality · $50.00 · 3h",
        "Starting from rc 0003",
        "Tone can only guard",
        "Run ended: Time limit reached",
        "Report written",
    ]
    first, second = run.model.log[:2]
    assert (first.divider, first.text) == (True, "start")
    assert (second.time, second.glyph, second.tone) == ("14:12", "·", "muted")


def test_a_run_with_no_budget_says_so():
    run = Run().standard_start(budget=None, timeout_mins=90)
    assert run.log()[0] == "Run started · quality · no budget · 1h30"


def test_wave_lines_name_their_parent_and_counts():
    run = Run().standard_start()
    run.at(3).wave_start(
        0, probe("r0-1"), probe("r0-2"), headline="reconnaissance — 2 probes"
    )
    run.at(6).plan(1).planned(1)
    run.wave_start(1, fix("w1-1"), headline="1 experiment")
    run.success("w1-1", "rc_a", row())
    run.gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a")
    run.plan(2).planned(2)
    run.wave_start(
        2,
        probe("w2-1"),
        parent="rc_a",
        probe_phase=True,
        headline="probe phase — 1 probe",
    )
    run.plan(2, replan=True).planned(2)
    run.wave_start(2, fix("w2-2"), parent="rc_a", wrap_up=True, headline="1 experiment")
    waves = [line.text for line in run.model.log if line.glyph == "▸"]
    assert waves == [
        "Wave 0, recon · 2 probes",
        "Wave 1 from start · 1 experiment",
        "Wave 2 probes from w1-1 · 1 probe",
        "Wave 2 from w1-1 · 1 experiment · last wave",
    ]
    dividers = [line.text for line in run.model.log if line.divider]
    assert dividers == ["start", "wave 1", "wave 2"]


def test_builds_and_the_check_findings_that_force_a_rebuild():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1"))
    run.state("w1-1", "building_rc")
    run.emit("item_change", item_id="w1-1", change={"summary": "adds a hold wrapper"})
    run.emit("generality", item_id="w1-1", rc_id="rc_a", status="leaked", leaks=["x"])
    run.state("w1-1", "building_rc")
    assert run.log()[-2:] == [
        "w1-1 built: adds a hold wrapper",
        "w1-1: train item text leaked into the change, rebuilding once",
    ]
    assert [line.glyph for line in run.model.log[-2:]] == ["+", "!"]


def test_item_state_chunk_phase_and_spend_events_add_no_lines():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1"))
    before = len(run.model.log)
    run.state("w1-1", "measuring")
    run.chunk("w1-1", "rc_a", [(False, True)])
    run.emit("item_detail", item_id="w1-1", detail="chunk 1/3")
    run.emit("spend", item_id="w1-1", source="measurement", cost_usd=0.2)
    run.emit("phase_start", name="baseline_measure", label="")
    run.emit("trial", eval_id="e1", rc_id="rc_a", landed=True, scores={})
    assert len(run.model.log) == before


def test_assumed_answers_once_per_open_question():
    run = Run().standard_start()
    question = {
        "question": "may a post hold links",
        "default_assumed": "no links",
        "gates": "",
        "reask_when": "",
        "constraint_id": "c1",
    }
    run.emit("open_questions", wave=1, phase="plan", questions=[question])
    run.emit("open_questions", wave=1, phase="replan", questions=[question])
    assert run.log()[-1] == "Assumed: no links (open question: may a post hold links)"
    assert run.log().count(run.log()[-1]) == 1
    assert run.model.log[-1].glyph == "?"


def test_health_lines():
    run = Run().standard_start()
    run.emit("tripwire", wave=2, error_class="rate_limit", failed=3)
    run.emit("phase_start", name="tripwire_backoff", wave=2, label="")
    run.emit(
        "eval_job_errors",
        eval_id="e1",
        rc_id="rc_a",
        split="train",
        attempt=1,
        error_class="rate_limit",
        errors=[{"dataset_id": "d1"}, {"dataset_id": "d2"}],
    )
    run.emit("eval_job_errors", errors=[])
    run.emit(
        "dispatch_retry",
        source="create_experiment_rc",
        item_id="w2-1",
        attempt=2,
        backoff_secs=30.0,
        error_class="timeout",
    )
    run.emit("provider_quota", line="OpenAI is out of credits", detail="402")
    run.emit("memory_write_failed", write="stamp", what="the val-gate rejection")
    assert run.log()[2:] == [
        "Tripwire on wave 2: 3 items failed with rate limit",
        "Tripwire backoff: a pause before the next wave",
        "2 eval items errored on train: rate limit",
        "w2-1: create_experiment_rc failed (timeout), retrying in 30s",
        "OpenAI is out of credits",
        "A memory write failed: the val-gate rejection",
    ]
    assert {line.glyph for line in run.model.log[3:]} == {"!"}


def memory(record_kind: str, op: str = "create", overview: str = "LESSON: x") -> dict:
    return {
        "id": "m1",
        "op": op,
        "role": "analyze_experiment_results",
        "scope": "project",
        "tags": [record_kind],
        "overview": overview,
        "record_kind": record_kind,
        "written_at": "2026-11-09T15:00:00",
        "forwarded_by": "w1-1",
    }


def test_lessons_dead_ends_and_new_issues_the_agents_wrote():
    run = Run().standard_start()
    run.emit("memory_written", **memory("lesson", overview="LESSON: name the skill"))
    run.emit("memory_written", **memory("lesson", "update", "hold_status is lost"))
    run.emit("memory_written", **memory("dead_end", overview="DEAD END: few-shot"))
    run.emit("memory_written", **memory("issue", overview="ISSUE: wrong bay"))
    run.emit("memory_written", **memory("issue", "update", "ISSUE: wrong bay"))
    run.emit("memory_written", **memory("experiment", overview="EXP: x"))
    assert run.log()[2:] == [
        "Lesson: name the skill",
        "Lesson updated: hold_status is lost",
        "Dead end: few-shot",
        "New issue: wrong bay",
    ]
    assert run.model.log[-1].glyph == "≈"


def test_gate_lines_at_the_gate_end():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"), fix("w1-2", "e2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.success("w1-2", "rc_b", row("e2", 0.8, 0.86))
    run.gate(
        1,
        table("rc_a", val_row("e1", 0.62, 0.70)),
        table("rc_b", val_row("e2", 0.80, 0.81)),
        winner="rc_a",
        end=False,
    )
    assert run.log()[-1] == "Promotion gate: 2 candidates on val"
    run.gate_end(1)
    assert run.log()[-2:] == [
        "w1-1 promoted · Hold placement +8.0 on val",
        "w1-2 rejected at val · Refusal honesty train +6.0, val +1.0",
    ]
    promoted = run.model.log[-2]
    assert (promoted.glyph, promoted.tone, promoted.bold) == ("◆", "success", True)


def test_refusals_before_the_gate():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1"), fix("w1-2"))
    run.success("w1-1", "rc_a", row()).success("w1-2", "rc_b", row())
    run.emit("promotion_blocked", winner_rc_id="rc_a", reasons=["outside the policy"])
    run.emit(
        "promotion_skipped",
        reason="deadline reached — val pass not started; wins stay pooled",
        candidate_rc_ids=["rc_b"],
    )
    assert run.log()[-2:] == [
        "w1-1 blocked before the gate: outside the policy",
        "Promotion gate skipped: deadline reached — val pass not started;"
        " wins stay pooled",
    ]
    assert run.line5("w1-1") == "✗ Blocked before the gate: model policy"
    # It never reached the gate: its gate step is not a stop.
    assert run.card("w1-1").steps[-1] == ("gate", "later")
    assert run.line5("w1-2") == "· Not judged: stays pooled"


def test_a_ui_error_is_logged_once():
    run = Run()
    run.model.note_ui_error()
    run.model.note_ui_error()
    assert run.log() == [
        "The display skipped an event it could not read; details in the run log"
    ]
    assert run.model.ui_errors == 2


def test_a_probe_line_puts_no_colon_after_a_question_mark():
    run = Run().standard_start()
    run.at(3).wave_start(0, probe("r0-1"), probe("r0-2", "Name the tools it calls"))
    for item_id, answer in (("r0-1", "yes"), ("r0-2", "unclear")):
        run.emit(
            "item_probe",
            item_id=item_id,
            probe={"answer": answer, "evidence": "", "drives": 3},
        )
    assert run.log()[-2:] == [
        "r0-1 Does the agent call get_container? YES · 3 drives",
        "r0-2 Name the tools it calls: UNCLEAR · 3 drives",
    ]


def test_a_dropped_eval_build_is_a_warning_line():
    run = Run().standard_start()
    run.emit(
        "eval_item_dropped",
        wave=1,
        item_id="w1-2",
        item_kind="eval",
        issue_memory_id="issue_2",
        line="dropped w1-2: it builds a new eval for issue issue_2, and eval"
        " building is off (experimental: --experimental-eval-building turns it on)",
    )
    (last,) = run.model.log[-1:]
    assert last.glyph == "!"
    assert last.text.startswith("dropped w1-2: it builds a new eval for issue issue_2")
