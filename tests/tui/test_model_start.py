"""The Starting checklist's steps, one event family at a time (functional
spec 4 and 7.6)."""

from model_events import START_NAME, START_RC, Run, fix, probe, row, table, val_row

from kiln_harness_tuner.schemas import (
    ItemOutcome,
    ModelCheck,
    ModelPreflightResult,
    NoiseCheckResult,
    NoiseRow,
    ProbeResult,
)
from kiln_harness_tuner.tui.model.cards import start_steps
from kiln_harness_tuner.tui.model.state import plain


def steps(run: Run) -> list[tuple[str, str, str]]:
    """The checklist after its Agent CLI line, in any mode."""
    lines = start_steps(run.model, run.t)[1:]
    return [(s.status, s.label, s.text) for s in lines]


def test_the_baseline_step_while_and_after_it_is_measured():
    run = Run().start()
    run.emit("phase_start", name="baseline_measure", label="")
    run.at(1).emit(
        "baseline_progress",
        rc_id=START_RC,
        line="baseline measuring: Holds 40/120 — 1 job(s) running",
    )
    assert steps(run)[-1] == (
        "busy",
        "Baseline",
        f"{START_NAME} · Holds 40/120 — 1 job(s) running · 1m00s",
    )
    evals = {
        "e1": {"stored": 100, "expected": 100, "how": "measured"},
        "e2": {"stored": 0, "expected": 50, "how": "failed (timeout)"},
    }
    run.emit("baseline_measured", rc_id=START_RC, line="", evals=evals)
    assert steps(run)[-1] == (
        "warn",
        "Baseline",
        f"{START_NAME} · 1 of 2 evals could not be measured",
    )
    evals.pop("e2")
    run.emit("baseline_measured", rc_id=START_RC, line="", evals=evals)
    assert steps(run)[-1] == ("ok", "Baseline", f"{START_NAME} · measured on train")


def test_the_model_preflight_step():
    run = Run().start()
    checks = [
        ModelCheck(provider="anthropic", model="haiku"),
        ModelCheck(provider="openai", model="gpt", outcome="untested"),
    ]
    result = ModelPreflightResult(baseline_rc_id=START_RC, checks=checks)
    run.emit("model_preflight", line="", result=result)
    assert steps(run)[-1] == (
        "ok",
        "Models",
        "2 models the policy allows answered · 1 untested",
    )
    checks.append(ModelCheck(provider="x", model="y", outcome="flagged"))
    result = ModelPreflightResult(baseline_rc_id=START_RC, checks=checks)
    run.emit("model_preflight", line="", result=result)
    assert steps(run)[-1] == ("warn", "Models", "3 models checked · 1 flagged")
    skipped = ModelPreflightResult(baseline_rc_id=START_RC, skipped="no policy")
    run.emit("model_preflight", line="", result=skipped)
    assert steps(run)[-1] == ("skip", "Models", "skipped: no policy")


def test_the_noise_step():
    run = Run().start()
    run.emit("noise_estimate", estimate_usd=None, line="")
    assert steps(run)[-1] == ("busy", "Noise", "measuring run-to-run noise · 0s")
    noise_row = NoiseRow(
        eval_id="e1",
        score_key="pass",
        score_type="pass_fail",
        baseline_rc_id=START_RC,
        items=8,
    )
    result = NoiseCheckResult(
        baseline_rc_id=START_RC,
        items_per_eval=8,
        rows=[noise_row],
        warnings=["noisy judge"],
    )
    run.emit("noise_check", line="", result=result)
    assert steps(run)[-2:] == [
        ("warn", "Noise", "1 score judged again on 8 items each"),
        ("warn", "", "noisy judge"),
    ]
    skipped = NoiseCheckResult(baseline_rc_id=START_RC, items_per_eval=8, skipped="$0")
    run.emit("noise_check", line="", result=skipped)
    assert ("skip", "Noise", "skipped: $0") in steps(run)


def test_skipped_recon_and_the_opening_screen():
    run = Run().start(objective="speed")
    run.emit("recon_skipped", reason="the baseline is unreadable")
    run.at(1).emit("candidate_budget", item_id=None, rc_id="rc_s1", usd=1.0)
    run.emit("queue_drawn", item_id=None, size=12, seed="s", queue=[])
    view = run.model.experiments(run.at(2).t)
    assert plain(view.header) == " Starting · the opening screen"
    assert steps(run)[-2:] == [
        ("skip", "Recon", "skipped: the baseline is unreadable"),
        ("busy", "Screen", "1 candidate · 12 items each · 1m00s"),
    ]
    run.emit("screening_round", round=1, line="", results=[])
    assert steps(run)[-1] == ("ok", "Screen 1", "0 candidates · 0 survive")
    segment = run.model.timeline(run.t).segments[-1]
    assert (segment.kind, segment.wave) == ("screen", 0)
    run.emit("opening_screen_skipped", reason="no lower thinking level")
    assert steps(run)[-1] == ("skip", "Screen", "skipped: no lower thinking level")


def test_busy_steps_fail_when_the_run_ends():
    run = Run().start()
    run.emit("phase_start", name="setup", label="")
    run.emit("exit", reason="fatal:SetupError")
    run.emit("exit", reason="a second exit is ignored")
    assert steps(run)[-1] == ("fail", "Setup", "syncing the evals and issues")
    assert run.model.exit_reason == "fatal:SetupError"
    assert run.model.experiments(run.t).done.ended == "Failed: SetupError"
    run.emit("run_ended")
    assert run.model.experiments(run.t).done.report == "no report"


def test_a_failed_val_fill_counts_as_landed_with_nothing_stored():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1"))
    run.success("w1-1", "rc_a", row())
    run.gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner=None, end=False)
    run.emit("val_fill_failed", eval_id="e1", rc_id="rc_a", error_class="timeout")
    assert run.model.val_progress("rc_a") == (0, 50)
    run.emit("promotion_skipped", reason="promote_best returned no decision")
    run.gate_end(1)
    assert run.line5("w1-1") == "· Not judged: stays pooled"
    assert run.log()[-1] == "Promotion gate skipped: promote_best returned no decision"


def test_a_fatal_cancel_and_a_probe_answer_on_the_outcome():
    run = Run().standard_start()
    run.at(3).wave_start(0, probe("r0-1"), probe("r0-2"))
    run.emit("fatal_cancel", wave=0, item_ids=["r0-2"], error="RuntimeError()")
    run.state("r0-1", "probing")
    # A probe whose answer rides on its outcome, with no `item_probe` first.
    outcome = ItemOutcome(
        item=run.plan_items["r0-1"],
        status="completed",
        rc_id=None,
        experiment_memory_id=None,
        result=None,
        infra_error=None,
        probe=ProbeResult(answer="no", evidence="", drives=2),
    )
    run.emit("item_outcome", item_id="r0-1", outcome=outcome)
    assert run.line5("r0-1") == "○ NO · 2 drives"
    assert "r0-1 Does the agent call get_container? NO · 2 drives" in run.log()
    assert run.line5("r0-2") == "✗ Cut off: tuning ended"
    assert "Cut off after a fatal error: r0-2" in run.log()
    # The recon stretch ends with its last verdict.
    assert run.model.timeline(run.t).segments[-1].kind == "recon"
    assert "recon" not in run.model.open_segments
