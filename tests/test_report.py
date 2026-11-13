"""report.py — the format-critical write: the agent returns markdown, CODE
writes reports/kiln-harness-tuner-<run_id>.md; the fallback shares the path."""

import json

import pytest
from conftest import ManualClock, baseline_record, make_ctx, make_plan
from fake_dispatcher import infra, ok
from test_status import WAVE1_ITEMS, completed_outcome, make_tracker, start_wave

from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.report import (
    FALLBACK_INTRO,
    KILN_EVALS_DOCS_URL,
    MISSING_EVALS_APPENDIX_HEADING,
    SPEND_APPENDIX_HEADING,
    SPEND_APPENDIX_NOTES,
    ReportWriter,
    read_issues_without_eval,
    render_fallback_report,
    render_missing_evals_appendix,
    render_spend_appendix,
    render_spend_header,
    spend_appendix_notes,
)
from kiln_harness_tuner.schemas import (
    ErrorInfo,
    IssueWithoutEval,
    ItemOutcome,
    PromotionDecision,
    ReportMarkdown,
    SetupReport,
)
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.wave_log import FanoutLog, WaveLog

RUN_ID = "260723-1402"


def make_writer(tmp_path, tracker=None):
    tracker = tracker or make_tracker()
    return ReportWriter(tracker, RUN_ID, tmp_path / "reports"), tracker


async def test_agent_markdown_written_verbatim(tmp_path):
    ctx = make_ctx()
    writer, tracker = make_writer(tmp_path)
    start_wave(tracker)
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# Round report\n")))

    path = await writer(ctx, "wrap_up")

    assert path == str(tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md")
    # Agent markdown leads verbatim; the code-computed spend appendix follows.
    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text()
    assert text.startswith("# Round report\n")
    assert SPEND_APPENDIX_HEADING in text
    events = dict(ctx.log.events)
    assert events["report_written"] == {"path": path, "fallback": False}
    # The reporting phase brackets the dispatch — and starts
    # AFTER the snapshot, so the report never shows its own phase.
    assert events["phase_start"]["name"] == "reporting"
    assert events["phase_end"]["name"] == "reporting"
    # exit_reason was filled into the dispatched snapshot (ReportInput).
    (req,) = ctx.dispatch.calls_to("report_writer")
    assert req.run_status.exit_reason == "wrap_up"
    assert req.run_status.phase is None
    assert [w.wave for w in req.run_status.waves] == [1]


async def test_tracker_exit_reason_wins_over_argument(tmp_path):
    ctx = make_ctx()
    writer, tracker = make_writer(tmp_path)
    tracker.event("exit", reason="loop says done")
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="x")))
    await writer(ctx, "loop says done")
    (req,) = ctx.dispatch.calls_to("report_writer")
    assert req.run_status.exit_reason == "loop says done"


async def test_hard_fail_ships_code_rendered_fallback_at_same_path(tmp_path):
    ctx = make_ctx()
    writer, tracker = make_writer(tmp_path)
    start_wave(tracker)
    tracker.event("item_outcome", item_id="w1-1", outcome=completed_outcome())
    tracker.event(
        "promotion",
        decision=PromotionDecision(
            winner_rc_id="rc_cand", winner_experiment_memory_id=None, rationale="+9%"
        ),
    )
    # Loop-level ladder: 5 calls, backoffs 30/60/120/300, then fallback.
    ctx.dispatch.queue("report_writer", *[infra("provider_error")] * 5)

    path = await writer(ctx, "timeout")

    assert len(ctx.dispatch.calls_to("report_writer")) == 5
    assert ctx.sleeps == [30.0, 60.0, 120.0, 300.0]
    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text()
    assert FALLBACK_INTRO in text
    # The exit reason is stated once, by the appendix header — the fallback
    # body no longer repeats it (run-level facts are stated in one place).
    assert "exit timeout" in text
    assert "- exit: timeout" not in text
    assert "## Wave 1 — 1 experiment, 1 eval-only" in text
    assert "`w1-1` fix — retry-skill (issue_1): +18%, n=2, p=.03" in text
    assert "`w1-2` eval — build eval for issue_2: unfinished (planned)" in text
    assert "- promotion: rc_cand — +9%" in text
    events = dict(ctx.log.events)
    assert events["report_written"] == {"path": path, "fallback": True}


async def test_reporting_phase_ends_even_when_dispatcher_raises(tmp_path):
    # A raise (or Ctrl-C → CancelledError) mid-dispatch must not
    # leave a dangling phase_start in the audit trail.
    ctx = make_ctx()
    writer, _ = make_writer(tmp_path)

    def boom(_req):
        raise RuntimeError("dispatcher blew up")

    ctx.dispatch.queue("report_writer", boom)
    with pytest.raises(RuntimeError):
        await writer(ctx, "wrap_up")
    kinds = [k for k, _ in ctx.log.events]
    assert kinds.count("phase_start") == kinds.count("phase_end") == 1


async def test_retry_then_success_writes_agent_markdown(tmp_path):
    ctx = make_ctx()
    writer, _ = make_writer(tmp_path)
    ctx.dispatch.queue(
        "report_writer", infra("timeout"), ok(ReportMarkdown(markdown="ok"))
    )
    await writer(ctx, "wrap_up")
    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text()
    assert text.startswith("ok\n")  # agent markdown + appendix
    assert ctx.sleeps == [30.0]


def test_fallback_report_renders_the_setup_section():
    # What got seeded, and — the half a human has to act on —
    # what setup refused to guess a binding for.
    tracker = make_tracker()
    tracker.event(
        "setup_sync",
        report=SetupReport(
            evals_total=4,
            evals_active=3,
            seeded_eval_ids=["424242966189"],
            repaired_issue_ids=["424242515304"],
            unbound_unresolved_issue_ids=["mem_7", "mem_8"],
            baseline_rule="most_recent",
            baseline_rc_id="rc_1",
        ),
    )
    tracker.event("exit", reason="wrap_up")

    text = render_fallback_report(tracker.snapshot())

    assert "## Setup" in text
    assert "- evals: 3 active of 4" in text
    assert "- baseline seeded: `rc_1` (rule: most_recent)" in text
    assert "- issues seeded for evals: `424242966189`" in text
    assert "- issue bindings repaired: `424242515304`" in text
    assert "- issues with no eval binding — needs a human: `mem_7`, `mem_8`" in text
    # Empty lists are not rendered as empty bullets.
    assert "UNMEASURABLE" not in text


def test_fallback_report_has_no_setup_section_without_one():
    assert "## Setup" not in render_fallback_report(make_tracker().snapshot())


def test_fallback_report_drops_the_setup_section_on_a_warm_run():
    # Matches what the kiln-harness-tuner-report skill tells the agent on the other
    # report path: a phase that changed nothing is not news worth a heading.
    tracker = make_tracker()
    tracker.event("setup_ok", report=SetupReport(evals_total=3, evals_active=3))

    assert "## Setup" not in render_fallback_report(tracker.snapshot())


def test_a_warm_run_with_evals_kiln_cant_load_lists_them_under_the_evals():
    tracker = make_tracker()
    tracker.event(
        "eval_unloadable",
        eval_id="424242966189",
        name="Tone",
        reason="Invalid Jinja2 template: No filter named 'trace_overview'",
        line="Kiln can't load eval …",
    )
    tracker.event("eval_unloadable", eval_id="424242005456", name="Format", reason="x")
    tracker.event("setup_ok", report=SetupReport(evals_total=3, evals_active=3))

    lines = render_fallback_report(tracker.snapshot()).splitlines()

    evals = lines.index("- evals: 3 active of 3")
    assert lines[evals - 2 : evals + 2] == [
        "## Setup",
        "",
        "- evals: 3 active of 3",
        '- Left out: "Tone" (Kiln can\'t load it: Invalid Jinja2 template: No'
        " filter named 'trace_overview'); \"Format\" (Kiln can't load it: x)",
    ]


def test_fallback_report_has_no_left_out_line_when_every_eval_loads():
    tracker = make_tracker()
    tracker.event(
        "setup_sync",
        report=SetupReport(evals_total=1, evals_active=1, seeded_eval_ids=["e1"]),
    )
    assert "Left out" not in render_fallback_report(tracker.snapshot())


def test_fallback_report_names_tripwire_and_spend():
    tracker = make_tracker()
    tracker.event("run_start", deadline=100.0)
    tracker.event("janitor", count=1, memory_ids=["mem_stale"])
    start_wave(tracker, items=WAVE1_ITEMS)
    tracker.event("spend", item_id="w1-1", source="eval_jobs", cost_usd=1.5)
    tracker.event(
        "item_outcome",
        item_id="w1-1",
        outcome=ItemOutcome(
            item=completed_outcome().item,
            status="failed_infra",
            rc_id=None,
            experiment_memory_id=None,
            result=None,
            infra_error=ErrorInfo(error_class="rate_limit", detail="429"),
        ),
    )
    tracker.event("tripwire", error_class="rate_limit", failed=1)
    tracker.event("promotion_skipped", reason="promote_best returned no decision")
    tracker.event("exit", reason="tripwire")
    text = render_fallback_report(tracker.snapshot())
    assert "infra: rate_limit ($1.50)" in text
    assert "- tripwire: 1× rate_limit" in text
    assert "- promotion skipped: promote_best returned no decision" in text
    assert "- total spend: $1.50" in text
    assert "- baseline history: rc_base" in text
    assert (
        "- janitor: stamped 1 stale experiment record(s) — ABANDONED (mem_stale)"
        in text
    )


# ---- spend appendix ----


def spend(tracker, source, lane, cost, tokens=0):
    tracker.event("spend", source=source, lane=lane, cost_usd=cost, tokens=tokens)


async def test_spend_appendix_appended_to_agent_markdown(tmp_path):
    ctx = make_ctx()
    writer, tracker = make_writer(tmp_path)
    spend(tracker, "batch_plan", "subscription_equivalent", 0.5, tokens=12_345)
    spend(tracker, "eval_jobs", "provider", 1.25)
    spend(tracker, "val_pass", "provider", 0.75)
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# Report\nbody\n")))

    await writer(ctx, "wrap_up")

    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text()
    assert text.startswith("# Report\nbody\n")
    assert SPEND_APPENDIX_HEADING in text
    # The agents are tokens, not dollars: never in the budget.
    assert "| source | API keys | Claude Code tokens |" in text
    assert "| batch_plan | — | 12,345 |" in text
    assert "| eval_jobs | $1.25 | — |" in text
    assert "| val_pass | $0.75 | — |" in text
    assert "| **total** | $2.00 | 12,345 |" in text
    for note in SPEND_APPENDIX_NOTES:  # incl. the exclusions boilerplate
        assert note in text


async def test_spend_appendix_on_fallback_report(tmp_path):
    ctx = make_ctx()
    writer, tracker = make_writer(tmp_path)
    spend(tracker, "batch_plan", "subscription_equivalent", 0.5, tokens=800)
    ctx.dispatch.queue("report_writer", *[infra("provider_error")] * 5)
    await writer(ctx, "timeout")
    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text()
    assert FALLBACK_INTRO in text
    assert SPEND_APPENDIX_HEADING in text
    assert "| batch_plan | — | 800 |" in text


async def test_spend_appendix_excludes_report_session(tmp_path):
    # The appendix renders from the pre-report snapshot — the report agent's
    # own spend never reaches its own books (the boilerplate says so).
    ctx = make_ctx()
    writer, tracker = make_writer(tmp_path)
    spend(tracker, "batch_plan", "subscription_equivalent", 0.5, tokens=800)
    ctx.dispatch.queue(
        "report_writer", ok(ReportMarkdown(markdown="# R\n"), cost_usd=3.0)
    )
    await writer(ctx, "wrap_up")
    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text()
    assert "report_writer" not in text
    assert "| **total** | $0.00 | 800 |" in text
    # …but the spend event itself still fired, lane-tagged, for the wave log.
    (fields,) = [f for k, f in ctx.log.events if k == "spend"]
    assert fields == {
        "source": "report_writer",
        "lane": "subscription_equivalent",
        "tokens": 0,
    }


def test_render_spend_appendix_gives_unexpected_lane_its_own_column():
    # Fold tolerance: money on an unknown lane is shown, never dropped.
    tracker = make_tracker()
    spend(tracker, "batch_plan", "subscription_equivalent", 0.5)
    tracker.event("spend", source="mystery", cost_usd=0.1)  # lane-less
    text = render_spend_appendix(tracker.snapshot())
    assert "| source | API keys | unknown | Claude Code tokens |" in text
    assert "| mystery | — | $0.10 | — |" in text
    assert "| **total** | $0.00 | $0.10 | 0 |" in text


def test_render_spend_appendix_sub_cent_rows_stay_visible():
    # Money that would round to $0.00 renders as $<0.01 so cells
    # visibly reconcile with the totals they sum into.
    tracker = make_tracker()
    spend(tracker, "model_preflight", "provider", 0.002)
    spend(tracker, "batch_plan", "subscription_equivalent", 0.5)
    text = render_spend_appendix(tracker.snapshot())
    assert "| model_preflight | $<0.01 | — |" in text
    assert "| batch_plan | — | — |" in text  # no tokens reported
    assert "| **total** | $<0.01 | 0 |" in text


def test_render_spend_appendix_empty_run_renders_zero_totals():
    text = render_spend_appendix(make_tracker().snapshot())
    assert SPEND_APPENDIX_HEADING in text
    assert "| **total** | $0.00 | 0 |" in text


# ---- appendix header: budget · elapsed · exit ----


def timed_tracker(elapsed_secs=0.0, **run_start_fields):
    """A tracker whose run has been going `elapsed_secs`, with whatever
    `run_start` carried — the header's three facts all come from there."""
    clock = ManualClock()
    tracker = make_tracker(clock)
    if run_start_fields:
        tracker.event("run_start", **run_start_fields)
    clock.t = elapsed_secs
    return tracker


def test_spend_appendix_header_states_budget_elapsed_and_exit():
    # A reader must be able to tell a 60-minute run from a 120-minute one:
    # budget, elapsed time and exit reason all come from code, not agent prose.
    tracker = timed_tracker(47 * 60 + 23, deadline=9000.0, timeout_mins=120)
    tracker.event("exit", reason="wrap_up")
    status = tracker.snapshot()

    header = "budget 120 min · elapsed 47 min · exit wrap_up"
    assert render_spend_header(status) == header
    # …and it is the appendix's first CONTENT line, under the heading — the
    # appendix is concatenated after a `---` rule, so a line above the
    # heading would float free of the section it describes.
    assert render_spend_appendix(status).startswith(
        f"{SPEND_APPENDIX_HEADING}\n\n{header}\n\n|"
    )


def test_spend_appendix_header_says_how_the_time_limit_ended_the_run():
    tracker = timed_tracker(24 * 60, deadline=1200.0, timeout_mins=20)
    tracker.event(
        "exit",
        reason="timeout",
        detail="finished wave 1, then the wrap-up",
    )
    assert render_spend_header(tracker.snapshot()) == (
        "budget 20 min · elapsed 24 min · exit timeout (finished wave 1, then"
        " the wrap-up)"
    )


def test_spend_appendix_header_omits_an_unset_budget():
    # The totals row's `left` states the facts the run has, never a guessed
    # one.
    tracker = timed_tracker(30 * 60, deadline=9000.0)
    tracker.event("exit", reason="tripwire")
    assert render_spend_header(tracker.snapshot()) == "elapsed 30 min · exit tripwire"


def test_spend_appendix_header_at_startup():
    # No run_start, no exit yet: elapsed always renders (a zero is a fact),
    # the two unknowns simply aren't claimed.
    assert render_spend_header(make_tracker().snapshot()) == "elapsed 0 min"


def test_spend_appendix_header_truncates_to_whole_minutes():
    # Minutes elapsed floors, like `ui._fmt_duration` — a rounded-up header
    # would read `budget 120 min · elapsed 120 min` at 119m31s, claiming a
    # budget the run hadn't spent.
    assert render_spend_header(timed_tracker(90.0).snapshot()) == "elapsed 1 min"
    assert render_spend_header(timed_tracker(119 * 60 + 31).snapshot()) == (
        "elapsed 119 min"
    )


async def test_written_report_carries_the_header(tmp_path):
    # End-to-end wiring, not just the renderer: the header reaches the file
    # on the agent path (the fallback path shares the same append).
    ctx = make_ctx()
    tracker = timed_tracker(60 * 60, deadline=9000.0, timeout_mins=90)
    writer, _ = make_writer(tmp_path, tracker)
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# R\n")))
    await writer(ctx, "wrap_up")
    text = (tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md").read_text()
    assert "budget 90 min · elapsed 60 min · exit wrap_up" in text


async def test_orchestrate_end_to_end_wires_report_and_wave_log(tmp_path):
    ctx = make_ctx()
    baseline_record(
        ctx.kiln,
        "Baseline: run config rc_base — promoted 07-15 by EXP 0715-w1-1",
        content="rc_base",
        rc_id="rc_base",
        memory_id="mem_baseline",
    )
    tracker = RunStatusTracker(project_id="p1", task_id="t1", guidance="g")
    wave_log = WaveLog(tmp_path / "runs" / RUN_ID / "wave_log.jsonl")
    ctx.log = FanoutLog(wave_log, tracker)
    ctx.write_report = ReportWriter(tracker, RUN_ID, tmp_path / "reports")
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="nothing worth doing")))
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# done\n")))

    result = await orchestrate(ctx, "g", timeout_mins=180)

    report_path = tmp_path / "reports" / f"kiln-harness-tuner-{RUN_ID}.md"
    assert result == str(report_path)
    assert report_path.read_text().startswith("# done\n")
    wave_log.close()
    kinds = [
        json.loads(line)["kind"]
        for line in (tmp_path / "runs" / RUN_ID / "wave_log.jsonl")
        .read_text()
        .splitlines()
    ]
    assert kinds == [
        "run_start",
        "phase_start",  # MCP-lane preflight
        "phase_end",
        "preflight",
        "spend",
        "phase_start",  # setup
        "setup_ok",
        "phase_end",
        "janitor",
        "baseline_found",  # pre-flight, voiced
        "contract_ok",  # $0 API-contract preflight
        # The smell detectors on the baseline; the fake lists no run config.
        "smell_detection_skipped",
        # The baseline reviewer is off by default outside the CLI.
        "baseline_review_skipped",
        "baseline_review",
        "phase_start",  # planning wave 1
        "phase_end",
        "spend",
        "exit",
        "phase_start",  # report writing
        "phase_end",
        "spend",
        "report_written",
    ]
    (req,) = ctx.dispatch.calls_to("report_writer")
    assert req.run_status.exit_reason == "nothing worth doing"


# ---- the agent-free fallback path ----


async def test_write_fallback_ships_a_report_with_no_dispatch(tmp_path):
    writer, tracker = make_writer(tmp_path)
    tracker.event("run_start", deadline=600.0, timeout_mins=10, budget_usd=25.0)
    start_wave(tracker)
    tracker.event(
        "spend", source="eval_jobs", lane="provider", cost_usd=1.5, item_id="w1-1"
    )
    tracker.event(
        "eval_job_errors",
        eval_id="e1",
        rc_id="rc",
        split="train",
        error_class="rate_limit",
        errors=[{}, {}],
    )
    ctx = make_ctx()

    path = await writer.write_fallback(ctx, "quota_exhausted")

    assert path == str(writer.report_path)
    text = writer.report_path.read_text()
    assert "code-rendered fallback" in text
    assert "exit quota_exhausted" in text
    assert "budget $25.00" in text  # --budget-usd reaches the header
    assert "incidents: 2 errored eval item(s) (train rate_limit: 2)" in text
    # No agent session: the dispatcher was never touched, nothing was spent.
    assert ctx.dispatch.calls == []
    assert [k for k, _ in ctx.log.events] == ["report_written"]
    (event,) = [f for k, f in ctx.log.events if k == "report_written"]
    assert event["fallback"] is True


# ---- issues with no eval ----


def drop_eval_item(tracker, item_id: str, issue_id: str, wave: int = 1) -> None:
    tracker.event(
        "eval_item_dropped",
        wave=wave,
        item_id=item_id,
        item_kind="eval",
        issue_memory_id=issue_id,
        line=f"dropped {item_id}: …",
    )


def seed_issues(kiln) -> None:
    """One issue of each kind the report-time read must sort."""
    issue = ["issue", "p1"]
    kiln.add_memory("issue: tone is harsh", "task::t1", issue, memory_id="issue_2")
    kiln.add_memory(
        "UNMEASURED: issue: a new mechanism",
        "task::t1",
        issue,
        memory_id="issue_5",
    )
    # Closed, bound to an eval, or measured by its own check: left out.
    kiln.add_memory("WONT_FIX: issue: not seen", "task::t1", issue, memory_id="i_won")
    kiln.add_memory(
        "issue: measured", "task::t1", [*issue, "eval_424242000001"], memory_id="i_eval"
    )
    kiln.add_memory(
        "issue: the baseline copies train items",
        "task::t1",
        [*issue, "baseline_audit"],
        memory_id="i_audit",
    )


async def test_the_report_time_read_keeps_open_issues_no_eval_binds():
    ctx = make_ctx()
    seed_issues(ctx.kiln)

    found = await read_issues_without_eval(ctx)

    assert [(i.issue_memory_id, i.overview) for i in found] == [
        ("issue_2", "issue: tone is harsh"),
        ("issue_5", "UNMEASURED: issue: a new mechanism"),
    ]


async def test_a_failed_issue_read_is_none_and_logged(caplog):
    ctx = make_ctx()

    async def kiln_is_down(*, retries=None):
        raise KilnAPIError("GET", "/memories", 503, "down")

    ctx.memory.list_issues = kiln_is_down
    with caplog.at_level("WARNING", logger="kiln_harness_tuner.report"):
        assert await read_issues_without_eval(ctx) is None
    assert "the issue read failed" in caplog.text
    assert "HTTP 503" in caplog.text


def test_missing_evals_section_lists_each_issue_with_its_overview():
    tracker = make_tracker()
    drop_eval_item(tracker, "w1-2", "issue_2")
    drop_eval_item(tracker, "w2-1", "issue_2", wave=2)
    status = tracker.snapshot()
    status.issues_without_eval = [
        IssueWithoutEval(issue_memory_id="issue_2", overview="issue: tone is harsh"),
        IssueWithoutEval(issue_memory_id="issue_5", overview="issue: a new one"),
    ]

    assert render_missing_evals_appendix(status).splitlines() == [
        MISSING_EVALS_APPENDIX_HEADING,
        "",
        "No eval measures these issues, so the loop cannot work on them.",
        "",
        "- `issue_2`: issue: tone is harsh — code dropped its planned eval build"
        " (`w1-2` in wave 1, `w2-1` in wave 2)",
        "- `issue_5`: issue: a new one",
        "",
        "Eval building is experimental and off by default in this release, so"
        " the loop builds no eval itself. To measure an issue, create an eval for"
        " it in the Kiln app, then run the tuner again. The Kiln docs say how:"
        f" [Evals]({KILN_EVALS_DOCS_URL}).",
    ]


def test_missing_evals_section_without_the_read_uses_what_the_run_recorded():
    tracker = make_tracker()
    tracker.event(
        "setup_sync",
        report=SetupReport(unbound_unresolved_issue_ids=["issue_7", "issue_2"]),
    )
    drop_eval_item(tracker, "w1-2", "issue_2")

    lines = render_missing_evals_appendix(tracker.snapshot()).splitlines()

    assert lines[4:6] == [
        "- `issue_2` — code dropped its planned eval build (`w1-2` in wave 1)",
        "- `issue_7`",
    ]


def test_missing_evals_section_is_left_out_with_eval_building_on():
    tracker = make_tracker()
    tracker.event("run_start", deadline=100.0, eval_building=True)
    tracker.event(
        "setup_sync", report=SetupReport(unbound_unresolved_issue_ids=["issue_7"])
    )
    status = tracker.snapshot()
    status.issues_without_eval = [
        IssueWithoutEval(issue_memory_id="issue_7", overview="issue: x")
    ]
    assert render_missing_evals_appendix(status) == ""


def test_missing_evals_section_is_left_out_when_every_issue_has_an_eval():
    tracker = make_tracker()
    assert render_missing_evals_appendix(tracker.snapshot()) == ""
    tracker.event("setup_sync", report=SetupReport(evals_total=2))
    assert render_missing_evals_appendix(tracker.snapshot()) == ""
    # A dropped build for an issue the read does not list (closed by now,
    # or bound to an eval) is not a missing eval.
    drop_eval_item(tracker, "w1-1", "i_won")
    status = tracker.snapshot()
    status.issues_without_eval = []
    assert render_missing_evals_appendix(status) == ""


@pytest.mark.parametrize("agent_ok", [True, False], ids=["agent", "fallback"])
async def test_the_written_report_names_the_issues_with_no_eval(tmp_path, agent_ok):
    ctx = make_ctx()
    seed_issues(ctx.kiln)
    writer, tracker = make_writer(tmp_path)
    drop_eval_item(tracker, "w1-2", "issue_2")
    if agent_ok:
        ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# Report\n")))
    else:
        ctx.dispatch.queue("report_writer", *[infra("provider_error")] * 5)

    await writer(ctx, "wrap_up")

    text = writer.report_path.read_text()
    assert text.index(SPEND_APPENDIX_HEADING) < text.index(
        MISSING_EVALS_APPENDIX_HEADING
    )
    assert "- `issue_2`: issue: tone is harsh — code dropped its planned" in text
    assert "- `issue_5`: UNMEASURED: issue: a new mechanism" in text
    assert "i_won" not in text
    assert KILN_EVALS_DOCS_URL in text
    # The report agent reads the same facts from its input.
    status = ctx.dispatch.calls_to("report_writer")[0].run_status
    assert [d.issue_memory_id for d in status.dropped_eval_items] == ["issue_2"]
    assert [i.issue_memory_id for i in status.issues_without_eval] == [
        "issue_2",
        "issue_5",
    ]


async def test_the_fallback_path_names_the_issues_with_no_eval(tmp_path):
    ctx = make_ctx()
    seed_issues(ctx.kiln)
    writer, _ = make_writer(tmp_path)

    await writer.write_fallback(ctx, "quota_exhausted")

    assert "- `issue_5`: UNMEASURED: issue: a new mechanism" in (
        writer.report_path.read_text()
    )


async def test_with_eval_building_on_the_report_reads_no_issues(tmp_path):
    ctx = make_ctx(eval_building=True)
    seed_issues(ctx.kiln)
    writer, tracker = make_writer(tmp_path)
    tracker.event("run_start", deadline=100.0, eval_building=True)
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# Report\n")))

    await writer(ctx, "wrap_up")

    assert MISSING_EVALS_APPENDIX_HEADING not in writer.report_path.read_text()
    status = ctx.dispatch.calls_to("report_writer")[0].run_status
    assert status.issues_without_eval is None


async def test_only_the_fallback_path_reads_the_issues_without_retries(tmp_path):
    # On a fatal exit Kiln may be what failed: the fallback report must not
    # wait on the client's retry ladder.
    ctx = make_ctx()
    writer, _ = make_writer(tmp_path)
    asked = []

    async def list_issues(*, retries=None):
        asked.append(retries)
        return []

    ctx.memory.list_issues = list_issues
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# Report\n")))

    await writer(ctx, "wrap_up")
    await writer.write_fallback(ctx, "fatal:KilnTransportError")

    assert asked == [None, False]


def test_the_spend_appendix_names_the_agent_lane_for_the_agent_cli():
    notes = spend_appendix_notes("codex")
    assert notes[1].startswith("- **Codex** tokens (`subscription_equivalent`)")
    assert notes[0] == SPEND_APPENDIX_NOTES[0]
