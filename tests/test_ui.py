"""ui.py — pure renderers over RunStatus plus the PlainUi event-driven
smoke on a captured console."""

import io
from datetime import datetime

from conftest import (
    ManualClock,
    eval_only_item,
    fix_item,
    make_result,
    make_tracker,
    measure_item,
)
from rich.console import Console

from kiln_harness_tuner.schemas import (
    ErrorInfo,
    ItemOutcome,
    ItemStatus,
    RunStatus,
    WaveStatus,
)
from kiln_harness_tuner.ui import (
    PlainUi,
    baseline_seeded_line,
    janitor_line,
    memory_write_failed_line,
    outcome_cell,
    phase_done_line,
    plain_exit_line,
    retry_line,
    setup_line,
    totals_text,
)

# One worked example of the analyzer's frozen measurement row (the format
# `kiln-harness-tuner-experiment-analysis` owns). `outcome_cell` treats an overview as
# an opaque payload, so these tests would pass on anything — which is exactly why the
# fixture should be the real shape: it is the nearest example a maintainer copies from.
FROZEN_ROW = "accuracy: +18pp, base=62, cand=80, n=34, p=.04"


def outcome(status="completed", item=None, result=None, infra_error=None):
    return ItemOutcome(
        item=item or fix_item(),
        status=status,
        rc_id=None,
        experiment_memory_id=None,
        result=result,
        infra_error=infra_error,
    )


def item_status(**kwargs):
    fields = {
        "item_id": "w1-1",
        "kind": "fix",
        "label": "retry-skill for 429s (issue_1)",
        "live_state": "planned",
        "eval_progress": None,
        "outcome": None,
        "elapsed_secs": 0.0,
        "spend_usd": 0.0,
    }
    fields.update(kwargs)
    return ItemStatus(**fields)


def wave_status(**kwargs):
    fields = {
        "wave": 1,
        "should_wrap_up": False,
        "plan_headline": "2 experiments",
        "rejected_summary": "and 9 others, lower value",
        "items": [item_status()],
        "promotion": None,
        "tripwire": None,
    }
    fields.update(kwargs)
    return WaveStatus(**fields)


def run_status(**kwargs):
    fields = {
        "project_id": "p1",
        "task_id": "t1",
        "guidance": "focus evals X",
        "baseline_history": ["rc_base"],
        "waves": [wave_status()],
        "spend_total_usd": 14.2,
        "time_remaining_secs": 6120.0,
        "exit_reason": None,
    }
    fields.update(kwargs)
    return RunStatus(**fields)


# ---- outcome cells (numbers or terminal state — never a verdict mark) ----


def test_outcome_cell_completed_uses_overview_numbers():
    res = make_result(overview=f"EXP 0722-w1-1: retry-skill — {FROZEN_ROW}")
    assert outcome_cell(outcome(result=res)) == FROZEN_ROW


def test_outcome_cell_renders_no_verdict_mark_in_any_branch():
    # The ✗ used to be inferred from the tail's leading
    # character, so any overview format not opening with a signed delta
    # rendered regressions as ✓. No branch marks an outcome now — a
    # regression and an improvement differ by their numbers alone.
    regression = make_result(
        overview="EXP 0722-w1-2: tool-desc rewrite — accuracy: -2pp, n=18, p=.61"
    )
    cells = [
        outcome_cell(outcome(result=regression)),
        outcome_cell(outcome(result=make_result())),
        outcome_cell(outcome(item=eval_only_item())),
        outcome_cell(outcome(item=measure_item())),
        outcome_cell(outcome(status="skipped_not_reproduced")),
        outcome_cell(
            outcome(
                status="failed_infra",
                infra_error=ErrorInfo(error_class="rate_limit", detail="429"),
            )
        ),
        outcome_cell(outcome(status="abandoned")),
    ]
    assert all("✓" not in cell and "✗" not in cell for cell in cells)
    assert cells[0] == "accuracy: -2pp, n=18, p=.61"


def test_outcome_cell_clips_separator_less_overview():
    # No " — " separator → the whole ≤280-char overview is the fallback tail;
    # it must be clipped, not rendered whole.
    res = make_result(overview="x" * 140)
    cell = outcome_cell(outcome(result=res))
    assert cell == "x" * 59 + "…"


def test_outcome_cell_eval_only_and_terminal_states():
    assert outcome_cell(outcome(item=eval_only_item())) == "eval built"
    assert (
        outcome_cell(outcome(status="skipped_not_reproduced"))
        == "not reproduced → WONT_FIX"
    )
    assert (
        outcome_cell(
            outcome(
                status="failed_infra",
                infra_error=ErrorInfo(error_class="rate_limit", detail="429"),
            )
        )
        == "infra: rate_limit"
    )
    assert outcome_cell(outcome(status="abandoned")) == "abandoned"


def test_outcome_cell_measure_issue_baseline_is_not_an_eval_build():
    # Both verdicts end `completed` with result=None, so
    # the result-less fallback would otherwise render this as "eval built".
    assert outcome_cell(outcome(item=measure_item())) == "baseline measured"


# ---- phase / pre-flight / retry lines ----


def test_janitor_line_counts_and_preflight_pass():
    assert janitor_line(2) == "janitor: 2 stale experiment records stamped — ABANDONED"
    assert janitor_line(1) == "janitor: 1 stale experiment record stamped — ABANDONED"
    assert janitor_line(0) == "pre-flight checks passed"


def test_baseline_seeded_line():
    # Which rc, by which rule.
    assert (
        baseline_seeded_line(
            {"rc_id": "rc_1", "rule": "task_default", "unresolvable_default": None}
        )
        == "baseline seeded: rc_1 (task_default)"
    )
    # A default that could NOT be honored is the one diagnosis the memory
    # record's provenance can't carry.
    assert baseline_seeded_line(
        {"rc_id": "rc_2", "rule": "most_recent", "unresolvable_default": "rc_gone"}
    ) == (
        "baseline seeded: rc_2 (most_recent) — the task's default rc_gone is"
        " not a live run config"
    )


def test_setup_line_summarizes_the_sync():
    fields = {
        "evals_total": 28,
        "evals_active": 25,
        "seeded": 25,
        "refreshed": 0,
        "repaired": 3,
        "unmeasurable": 1,
        "unbound_unresolved": 7,
        "cost_usd": 1.5,
    }
    assert setup_line(fields) == (
        "setup: 25 of 28 evals active — 25 seeded, 3 repaired, 1 unmeasurable;"
        " 7 issue(s) need a human (no eval binding)   $1.50"
    )


def test_setup_line_on_the_warm_path():
    # `setup_ok` carries the same fields with the write counts at zero.
    assert (
        setup_line(
            {
                "evals_total": 3,
                "evals_active": 3,
                "seeded": 0,
                "refreshed": 0,
                "repaired": 0,
                "unmeasurable": 0,
                "unbound_unresolved": 0,
                "cost_usd": 0.0,
            }
        )
        == "setup: 3 of 3 evals active — in sync"
    )


def test_phase_done_line():
    assert (
        phase_done_line({"name": "planning", "wave": 3, "elapsed_secs": 252.0})
        == "planning wave 3 done (4m12s)"
    )
    assert (
        phase_done_line({"name": "setup", "elapsed_secs": 90.0}) == "setup done (1m30s)"
    )
    assert (
        phase_done_line({"name": "promotion_gate", "elapsed_secs": 61.0})
        == "promotion gate done (1m01s)"
    )
    assert (
        phase_done_line({"name": "preflight", "elapsed_secs": 3.0})
        == "MCP-lane preflight done (3s)"
    )


def test_retry_line():
    assert (
        retry_line(
            {
                "source": "batch_plan",
                "item_id": None,
                "attempt": 2,
                "backoff_secs": 60.0,
                "error_class": "provider_error",
            }
        )
        == "batch_plan attempt 2 failed (provider_error) — retrying in 1m00s"
    )
    assert retry_line(
        {
            "source": "create_experiment_rc",
            "item_id": "w1-1",
            "attempt": 1,
            "backoff_secs": 30.0,
            "error_class": "timeout",
        }
    ).startswith("w1-1: create_experiment_rc attempt 1 failed (timeout)")


def test_memory_write_failed_line_clips_the_error():
    # KilnAPIError stringifies the whole response body — one 422 validation
    # payload must not shove the active-wave block off screen. The full repr
    # stays in the JSONL wave log.
    line = memory_write_failed_line(
        {
            "write": "note",
            "what": "val-gate rejection",
            "rc_id": "rc_208",
            "exp_mem_id": "mem_9",
            "error": "KilnAPIError: " + "x" * 500,
        }
    )
    assert line.startswith(
        "memory write FAILED (note) for rc_208 / mem_9 — its val-gate"
        " rejection is incomplete: KilnAPIError: xxx"
    )
    assert line.endswith("…")
    assert len(line) < 200


def test_memory_write_failed_line_without_a_subject():
    # The event kind is generic (issue-lifecycle writes share the policy)
    # — an emitter that names no subject still gets a true line, never a
    # hardcoded claim about a rejection it wasn't writing.
    line = memory_write_failed_line(
        {"write": "stamp", "rc_id": "rc_208", "exp_mem_id": "mem_9", "error": "boom"}
    )
    assert line == (
        "memory write FAILED (stamp) for rc_208 / mem_9 —"
        " its memory record is incomplete: boom"
    )


def test_memory_write_failed_line_drops_ids_the_emitter_does_not_have():
    # The val-stats writeback has an rc and no experiment
    # record — the reason `subject` exists. It must read "for rc_cand —",
    # never "for rc_cand / None —".
    line = memory_write_failed_line(
        {
            "write": "val_stats",
            "what": "val stats record",
            "rc_id": "rc_cand",
            "error": "boom",
        }
    )
    assert line == (
        "memory write FAILED (val_stats) for rc_cand —"
        " its val stats record is incomplete: boom"
    )
    assert "None" not in line


def test_memory_write_failed_line_omits_the_clause_when_it_has_no_ids():
    # No filler noun: the two emitters have different subjects (a rejected
    # candidate, a promotion), so any stand-in would be wrong for one.
    line = memory_write_failed_line({"write": "stamp", "error": "boom"})
    assert line == (
        "memory write FAILED (stamp) — its memory record is incomplete: boom"
    )
    assert " for " not in line


# ---- totals ----


def test_totals_text():
    text = totals_text(run_status(elapsed_secs=2483.0, time_remaining_known=True))
    assert text == "$14.20 · 41m23s elapsed · 1h42m left"


def test_totals_text_at_startup():
    # Zero state renders normally — a run that has spent nothing yet says so.
    text = totals_text(
        run_status(
            spend_total_usd=0.0,
            elapsed_secs=0.0,
            time_remaining_secs=10_800.0,
            time_remaining_known=True,
        )
    )
    assert text == "$0.00 · 0s elapsed · 3h00m left"


def test_totals_text_without_a_budget():
    # No run_start deadline seen → `time_remaining_secs` is a meaningless 0.0;
    # omit the segment rather than claim the run just ran out.
    text = totals_text(run_status(elapsed_secs=90.0, time_remaining_secs=0.0))
    assert text == "$14.20 · 1m30s elapsed"


def test_totals_text_past_deadline():
    # The tracker clamps time remaining at 0 — an overrun run shows 0s left.
    text = totals_text(
        run_status(
            elapsed_secs=11_000.0, time_remaining_secs=0.0, time_remaining_known=True
        )
    )
    assert text == "$14.20 · 3h03m elapsed · 0s left"


# ---- PlainUi ----


def capture_console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=120), out


def test_plain_ui_voices_the_setup_phase():
    # The pre-flight setup phase is a multi-minute silence on a cold project
    # (one describe session per active eval) — the plain lines say what it did.
    seeded = {"rc_id": "rc_1", "rule": "task_default", "unresolvable_default": None}
    sync = {
        "evals_total": 2,
        "evals_active": 2,
        "seeded": 2,
        "refreshed": 0,
        "repaired": 0,
        "unmeasurable": 0,
        "unbound_unresolved": 0,
        "cost_usd": 0.0,
    }
    console, out = capture_console()
    with PlainUi(make_tracker(), console=console) as ui:
        ui.event("baseline_seeded", **seeded)
        ui.event("setup_sync", **sync)
        ui.event("setup_ok", **dict(sync, seeded=0))
    text = out.getvalue()
    assert "baseline seeded: rc_1 (task_default)" in text
    assert "setup: 2 of 2 evals active — 2 seeded" in text
    assert "setup: 2 of 2 evals active — in sync" in text


def test_plain_ui_prints_each_eval_kiln_cant_load():
    line = (
        'Kiln can\'t load eval "Tone": bad. The run leaves it out. To use it, fix'
        " its judge in Kiln, or update Kiln if the eval was made with a newer"
        " version."
    )
    console, out = capture_console()
    with PlainUi(make_tracker(), console=console) as ui:
        ui.event("eval_unloadable", eval_id="e1", name="Tone", reason="bad", line=line)
    assert 'Kiln can\'t load eval "Tone": bad.' in out.getvalue()


def test_plain_ui_prints_each_dropped_eval_build():
    line = (
        "dropped w1-2: it builds a new eval for issue issue_2, and eval building"
        " is off (experimental: --experimental-eval-building turns it on)"
    )
    console, out = capture_console()
    with PlainUi(make_tracker(), console=console) as ui:
        ui.event(
            "eval_item_dropped",
            wave=1,
            item_id="w1-2",
            item_kind="eval",
            issue_memory_id="issue_2",
            line=line,
        )
    assert "dropped w1-2: it builds a new eval for issue issue_2" in (
        " ".join(out.getvalue().split())
    )


def test_plain_ui_prints_one_line_per_event():
    console, out = capture_console()
    stamp = datetime(2026, 7, 30, 14, 2, 3)
    clock = ManualClock()
    tracker = make_tracker(clock)

    def emit(kind, **fields):  # cli.py's fanout order: tracker, then the UI
        tracker.event(kind, **fields)
        ui.event(kind, **fields)

    with PlainUi(tracker, console=console, now=lambda: stamp) as ui:
        emit("run_start", timeout_mins=180, deadline=10_800.0)
        emit("baseline_found", rc_id="rc_base")
        emit("janitor", count=0, memory_ids=[])
        emit("phase_start", name="planning", wave=1, label="Planning wave 1…")
        emit("phase_end", name="planning", wave=1, elapsed_secs=252.0)
        emit(
            "wave_start",
            wave=1,
            should_wrap_up=True,
            baseline_rc_id="rc_base",
            plan_headline="1 experiment",
            rejected_summary="",
            items=[{"item_id": "w1-1", "kind": "fix", "label": "retry (issue_1)"}],
        )
        emit("item_state", item_id="w1-1", state="building_rc")
        emit(  # silent
            "spend",
            cost_usd=1.5,
            item_id="w1-1",
            source="create_experiment_rc",
            lane="subscription_equivalent",
        )
        emit(
            "dispatch_retry",
            source="create_experiment_rc",
            item_id="w1-1",
            attempt=1,
            backoff_secs=30.0,
            error_class="timeout",
        )
        emit("item_detail", item_id="w1-1", detail="measuring eval_1 (1/2)")
        emit("item_outcome", item_id="w1-1", outcome=outcome(status="failed_infra"))
        emit("promotion", winner_rc_id="rc_207", rationale="+9%")
        emit("promotion_skipped", reason="promote_best returned no decision")
        emit(
            "memory_write_failed",
            write="stamp",
            what="val-gate rejection",
            rc_id="rc_208",
            exp_mem_id="mem_9",
            error="KilnAPIError(500)",
        )
        clock.t = 2483.0
        emit("wave_end", wave=1)
        emit("tripwire", wave=1, error_class="rate_limit", failed=3)
        emit("exit", reason="tripwire")
        emit("report_written", path="reports/kiln-harness-tuner-x.md")
    lines = out.getvalue().splitlines()
    # CI-log usefulness: every line is timestamped.
    assert all(line.startswith("[14:02:03] ") for line in lines)
    assert "harness-tuner: run started (timeout 180 min)" in lines[0]
    assert "baseline: rc_base" in lines[1]
    assert "pre-flight checks passed" in lines[2]  # 0 stamped → the pass line
    assert "Planning wave 1…" in lines[3]
    assert "planning wave 1 done (4m12s)" in lines[4]
    assert "wave 1: 1 experiment · baseline rc_base · wrap-up" in lines[5]
    assert "w1-1 fix: retry (issue_1) — planned" in lines[6]
    assert "w1-1: building rc…" in lines[7]
    assert (
        "w1-1: create_experiment_rc attempt 1 failed (timeout) — retrying in 30s"
        in lines[8]
    )
    assert "w1-1: measuring eval_1 (1/2)" in lines[9]
    assert "w1-1: infra: ?" in lines[10]
    assert "promotion: rc_207 — +9%" in lines[11]
    assert "promotion skipped: promote_best returned no decision" in lines[12]
    assert (
        "memory write FAILED (stamp) for rc_208 / mem_9 — its val-gate"
        " rejection is incomplete: KilnAPIError(500)"
    ) in lines[13]
    # Plain mode states the same totals at each wave boundary and
    # at exit — and nowhere else (no ticking).
    totals = (
        "— total $1.50 (Claude Code $1.50 · Kiln $0.00) · 41m23s elapsed · 2h18m left"
    )
    assert f"wave 1 done {totals}" in lines[14]
    assert "tripwire on wave 1: 3× rate_limit" in lines[15]
    assert f"exit: tripwire {totals}" in lines[16]
    assert "report: reports/kiln-harness-tuner-x.md" in lines[17]
    assert len(lines) == 18
    assert sum("total $" in line for line in lines) == 2


def test_plain_exit_line_is_the_plain_ui_exit_line():
    status = run_status(elapsed_secs=2483.0, time_remaining_known=True)
    assert plain_exit_line({"reason": "wrap_up"}, status) == (
        "exit: wrap_up — total $14.20 · 41m23s elapsed · 1h42m left"
    )
    console, out = capture_console()
    clock = ManualClock()
    tracker = make_tracker(clock)
    with PlainUi(tracker, console=console, now=lambda: datetime(2026, 7, 30)) as ui:
        tracker.event("run_start", timeout_mins=180, deadline=10_800.0)
        tracker.event("spend", cost_usd=1.5, source="planner", lane="provider")
        clock.t = 600.0
        tracker.event("exit", reason="tripwire")
        expected = plain_exit_line({"reason": "tripwire"}, tracker.snapshot())
        ui.event("exit", reason="tripwire")
    assert out.getvalue() == f"[00:00:00] {expected}\n"
    assert expected.startswith("exit: tripwire — total $1.50 (")


def test_change_lines_render_summary_changes_unchanged_and_check():
    from kiln_harness_tuner.ui import change_lines

    lines = change_lines(
        {
            "item_id": "w1-1",
            "change": {
                "summary": "wrapper gates PATCH",
                "changes": ["tool A → wrapper B"],
                "unchanged": ["prompt", "model"],
                "consistency_check": "description vs code: consistent",
            },
        }
    )
    assert lines[0].startswith("  w1-1  Δ wrapper gates PATCH")
    assert any("· tool A → wrapper B" in line for line in lines)
    assert any("= unchanged: prompt; model" in line for line in lines)
    assert any("✓ checked: description vs code: consistent" in line for line in lines)
    assert change_lines({"item_id": "w1-1"}) == []


def test_the_agent_lane_is_named_for_the_agent_cli_and_shows_unpriced_tokens():
    status = run_status(
        spend_total_usd=0.12,
        spend_by_lane={"subscription_equivalent": 0.0, "provider": 0.12},
        unmeasured_tokens=1_234_567,
        agent_cli="codex",
    )
    text = totals_text(status)
    assert "Codex $0.00 + 1.2M tokens unpriced" in text
    assert "Kiln $0.12" in text and "Claude Code" not in text
    opencode = run_status(
        spend_total_usd=0.5,
        spend_by_lane={"subscription_equivalent": 0.4, "provider": 0.1},
        agent_cli="opencode",
    )
    assert "OpenCode $0.40" in totals_text(opencode)


def test_an_agent_lane_the_budget_does_not_count_is_marked():
    status = run_status(
        spend_total_usd=0.5,
        spend_by_lane={"subscription_equivalent": 0.4, "provider": 0.1},
        agent_cost_in_budget=False,
    )
    text = totals_text(status)
    # The total is the spend the budget counts: the Kiln lane alone.
    assert text.startswith("$0.10 (Claude Code $0.40 not in budget · Kiln $0.10)")
