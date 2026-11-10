"""The run screen (functional spec sections 6 to 9, ui_design sections 6, 7,
9, 10 and 14). Pilot tests at 210×62: the app over a recorded run, its
clock on the run's own, each mode in turn."""

import logging

import pytest
from app_fakes import until
from model_events import Run, fix, measure, probe, row, table, val_row
from rich.color import ColorSystem
from run_screen import (
    done,
    gated,
    on_screen,
    running,
    subtitle_of,
    text,
    title_of,
    unspun,
    wave_one,
)
from textual.content import Content
from textual.geometry import Region
from textual.widgets import ProgressBar, Static

from kiln_harness_tuner.tui import app as app_module
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.state import LogLine, Segment, TimelineView
from kiln_harness_tuner.tui.screens.run import RunScreen
from kiln_harness_tuner.tui.theme import DARK, LIGHT
from kiln_harness_tuner.tui.widgets import timeline_rows, two_lines

LEGEND = "░ plan  ▒ screen  ▓ experiments  ◆ promotion  · gate, no winner  ┄ projected"
# From a classic 80-column terminal (the Goal needs 74 with the scrollbar)
# to the design size.
WIDTHS = [80, 100, 120, 150, 210]


def footer(app) -> dict[str, str]:
    return {
        key: active.binding.description
        for key, active in app.screen.active_bindings.items()
        if active.binding.show and active.enabled
    }


def plain_rows(rows) -> list[str]:
    return ["".join(char for char, _style in row) for row in rows]


# ---- the top of the screen ----


async def test_title_bar_names_the_task_the_run_and_the_slots():
    async with on_screen(running()) as (harness, _pilot):
        bar = harness.screen.title_bar
        assert text(bar.query_one(".title-context", Static)) == (
            "Depot Ops Desk · run 261109-1412"
        )
        assert text(bar.query_one(".title-meta", Static)) == (
            "claude -p · 4 agent slots · 4 eval slots"
        )


@pytest.mark.parametrize(
    "objective,rows",
    [
        (
            "quality",
            [
                "▲ optimize  Quality        2 evals",
                "═ hold      Every eval     none may drop on val",
                "○ watch     Cost, latency  shown, never decide",
            ],
        ),
        (
            "speed",
            [
                "▲ optimize  Latency  per request",
                "═ hold      Quality  equal or better on val",
                "○ watch     Cost     shown, never decides",
            ],
        ),
    ],
)
async def test_goal_shows_the_three_roles_and_the_data_line(objective, rows):
    run = wave_one(fix("w1-1"), objective=objective)
    run.emit("train_set", items=312)
    run.emit("quarantine", heldout_items=208)
    async with on_screen(run) as (harness, _pilot):
        goal = harness.screen.goal
        assert text(goal.body).split("\n") == rows
        assert text(goal.query_one(".goal-data > .label", Static)) == "  data      "
        assert text(goal.data) == (
            "train 312 · held out 208 (val and test) · test never read"
        )
        # At the design size the data line fits on one row, so the Goal is
        # as tall as Time & spend.
        assert goal.data.region.height == 1
        assert goal.region.height == harness.screen.clock.region.height


async def test_a_long_data_line_wraps_under_its_own_text():
    run = wave_one(fix("w1-1"))
    run.emit("train_set", items=3_120_000)
    run.emit("quarantine", heldout_items=2_080_000)
    async with on_screen(run) as (harness, _pilot):
        goal = harness.screen.goal
        label = goal.query_one(".goal-data > .label", Static)
        assert goal.data.region.height == 2
        assert goal.data.region.x == label.region.x + len("  data      ")


async def test_time_and_spend_show_two_gauges_lanes_and_the_wave():
    run = running()
    run.emit("spend", source="x", lane="provider", cost_usd=12.5)
    async with on_screen(run) as (harness, _pilot):
        clock = harness.screen.clock
        assert text(clock.time.value) == "20m00s"
        assert text(clock.time.limit) == "3h00m"
        assert (clock.time.bar.progress, clock.time.bar.total) == (1200, 10800)
        assert text(clock.spend.value) == "$12.50"
        assert text(clock.spend.limit) == "$50.00"
        assert clock.spend.bar.visible
        assert text(clock.lanes).startswith("API keys $12.50 · Claude Code $0.00")
        assert text(clock.times) == "started 14:12 · ends 17:12"
        assert title_of(clock) == "TIME & SPEND   wave 1"
        assert not clock.meta.display


async def test_with_no_budget_the_spend_bar_is_hidden():
    async with on_screen(wave_one(fix("w1-1"), budget=None)) as (harness, _pilot):
        spend = harness.screen.clock.spend
        assert text(spend.limit) == "no budget"
        assert not spend.bar.visible


async def test_the_last_wave_tag_shows_only_in_the_wrap_up_wave():
    run = wave_one(fix("w1-1"))
    async with on_screen(run) as (harness, pilot):
        clock = harness.screen.clock
        assert words.LAST_WAVE_TAG not in title_of(clock)
        run.at(40).plan(2, wrap_up=True)
        await harness.shown(pilot)
        assert title_of(clock).endswith(f" {words.LAST_WAVE_TAG} ")


# ---- the run timeline ----


def test_timeline_rows_draw_setup_waves_a_promotion_and_the_now_marker():
    view = TimelineView(
        limit=100.0,
        segments=(
            Segment("setup", 0, 0.0, 10.0),
            Segment("plan", 1, 10.0, 20.0),
            Segment("items", 1, 20.0, 40.0),
            Segment("gate", 1, 40.0, 50.0, mark="promotion"),
            Segment("plan", 2, 50.0, 60.0),
            Segment("screen", 2, 60.0, 70.0),
        ),
        projected=((80.0, 3),),
        now=70.0,
        now_label="now 15:36",
        ended=False,
        ticks=((0.0, "0"), (50.0, "1h"), (75.0, "1h30"), (100.0, "2h")),
    )
    labels, strip, axis, ticks = plain_rows(timeline_rows(view, 100))
    assert labels.split() == ["setup", "w1", "w2", "w3"]
    assert labels.index("w1") == 10 and labels.index("w3") == 80
    assert strip[:10] == "░" * 10
    assert strip[20:40] == "▓" * 20
    assert strip[40:50] == "·" * 9 + "◆"
    assert strip[60:70] == "▒" * 10
    assert strip[70:] == "┄" * 30
    assert (axis[0], axis[50], axis[75], axis[99]) == ("├", "┼", "┼", "┤")
    assert ticks[70:81] == "▲ now 15:36"
    assert ticks.split()[:2] == ["0", "1h"]
    assert "1h30" not in ticks  # it would touch the marker
    assert ticks.rstrip().endswith("2h")


def test_an_ended_timeline_puts_its_marker_before_the_end():
    view = TimelineView(
        limit=100.0,
        segments=(Segment("setup", 0, 0.0, 99.0),),
        projected=(),
        now=99.0,
        now_label="ended 17:10",
        ended=True,
        ticks=((0.0, "0"), (100.0, "2h")),
    )
    _labels, strip, _axis, ticks = plain_rows(timeline_rows(view, 100))
    assert ticks.endswith("ended 17:10 ▲")
    assert "┄" not in strip


async def test_the_timeline_section_shows_the_run_and_its_legend():
    async with on_screen(running()) as (harness, _pilot):
        section = harness.screen.timeline
        drawn = text(section.timeline).split("\n")
        assert drawn[0].split()[:2] == ["setup", "w1"]
        assert "▲ now 14:32" in drawn[3]
        assert title_of(section) == f"TUNING TIMELINE   {LEGEND}"


async def test_before_the_run_starts_the_timeline_is_blank():
    run = Run()
    async with on_screen(run) as (harness, _pilot):
        assert text(harness.screen.timeline.timeline) == ""


# ---- Results ----


async def test_results_show_one_card_per_concern_with_three_tiers():
    async with on_screen(running()) as (harness, _pilot):
        cards = harness.screen.results.cards
        assert [title_of(card) for card in cards] == [
            "Eval: Hold placement",
            "Eval: Refusal honesty",
            "Cost / request",
            "Latency median",
        ]
        # The name bold, the qualifier muted: the change of style parts them.
        latency = cards[3]._border_title
        assert [(span.start, span.end, span.style) for span in latency.spans] == [
            (0, 7, "bold"),
            (7, 14, "$text-muted"),
        ]
        hold = text(cards[0].bars).split("\n")
        assert hold[0].endswith("60.0%      ")
        assert hold[1] == ""  # no promotion yet
        assert hold[2].endswith("70.0% +10.0")
        assert subtitle_of(cards[0]) == "* 1 in flight · w1-3"
        assert subtitle_of(cards[1]) == "* 1 in flight"
        assert text(cards[2].bars).split("\n")[0] == words.NOT_MEASURED_YET
        assert subtitle_of(cards[2]) == ""


async def test_the_results_key_wraps_under_the_link_when_it_does_not_fit():
    start = "■ Start · train · starting point"
    confirmed = "■ Best Confirmed · no promotion yet"
    unconfirmed = "■ Best Unconfirmed · train estimate"
    async with on_screen(running()) as (harness, _pilot):
        results = harness.screen.results
        assert text(results.header.context) == "   ".join(
            (start, confirmed, unconfirmed)
        )
        assert not results.key.display
    async with on_screen(running(), size=(120, 40)) as (harness, _pilot):
        results = harness.screen.results
        assert text(results.header.context) == f"{start}   {confirmed}"
        assert text(results.header.meta) == "Compare in Kiln ↗"
        assert results.key.display
        assert text(results.key) == unconfirmed


async def test_results_cards_are_made_again_only_when_the_concerns_change():
    run = running()
    async with on_screen(run) as (harness, pilot):
        before = list(harness.screen.results.cards)
        run.success("w1-1", "rc_a", row("e1", 0.6, 0.75))
        await harness.shown(pilot)
        assert harness.screen.results.cards == before
        run.arm_results("w1-1", "rc_a", row("e9", 0.5, 0.6))
        run.model.eval_names["e9"] = "New check"
        await harness.shown(pilot)
        cards = harness.screen.results.cards
        assert [title_of(card) for card in cards][2] == "Eval: e9"
        assert len(cards) == 5


# ---- Experiments, mode by mode ----


async def test_starting_mode_shows_the_checklist_and_the_recon_cards():
    run = Run()
    async with on_screen(run) as (harness, pilot):
        section = harness.screen.experiments
        assert text(section.header.context) == "* Starting · testing the agent CLI"
        assert [text(row) for row in section.rows] == [
            "* Agent CLI testing claude -p · 40s"
        ]
        run.model.agent_cli_checked(run.t - 2)
        run.start()
        run.at(3).wave_start(0, probe("r0-1"), probe("r0-2"), headline="2 probes")
        await harness.shown(pilot)
        assert text(section.rows[0]).startswith("✓ Agent CLI ")
        assert text(section.subhead) == "RECON · probes"
        assert list(section.cards) == ["r0-1", "r0-2"]
        assert text(section.note) == words.RECON_NOTE
        assert section.note.display and section.subhead.display


async def test_planning_mode_shows_the_planner_steps_and_the_last_wave_cards():
    run = wave_one(fix("w1-1"))
    run.success("w1-1", "rc_a", row())
    run.gate(1, *(), winner=None)
    run.at(40).plan(2)
    async with on_screen(run.at(42)) as (harness, _pilot):
        section = harness.screen.experiments
        assert text(section.header.context) == "* Planning · wave 2 from start"
        assert [text(row) for row in section.rows] == [
            "* Planner   planning wave 2 · 2m00s"
        ]
        assert text(section.subhead) == "WAVE 1 · landed"
        assert list(section.cards) == ["w1-1"]


async def test_running_mode_shows_one_card_per_item_in_order():
    run = running()
    async with on_screen(run) as (harness, pilot):
        section = harness.screen.experiments
        assert text(section.header.context) == (
            "Running · wave 1 from start · 1 queued · 1 building · 1 measuring"
            " · 1 asking · 1 landed"
        )
        assert text(section.header.meta) == "8m00s"
        assert list(section.cards) == ["w1-1", "w1-2", "w1-3", "w1-4", "w1-5"]
        building, measuring, landed, asking, queued = section.cards.values()
        assert title_of(building) == "w1-1 target: Hold placement"
        assert text(building.hypothesis) == (
            "Hypothesis: Return only the hold fields\nfrom get_container"
        )
        # The label in the normal style, the planner's words muted.
        drawn = building.hypothesis.render()
        assert [(span.start, span.style) for span in drawn.spans] == [
            (12, "$text-muted"),
            (40, "$text-muted"),
        ]
        assert text(building.steps) == "* build  · measure  · analyze"
        assert text(building.line4.label).startswith("an agent is writing the change")
        assert building.has_class("-live") and not queued.has_class("-live")
        assert title_of(measuring) == "w1-2 target: Refusal honesty"
        assert text(measuring.steps) == "✓ build  * measure  · analyze"
        assert measuring.line4.strip.display
        assert text(measuring.line4.strip).startswith("█▓░···")
        assert text(measuring.line4.label) == "3/36"
        assert text(measuring.line5) == "1 fixed · 0 broke so far"
        assert text(landed.line5) == "● Train success · checking validation set"
        # The details marker, then the item's own spend only, above 0; no id.
        assert subtitle_of(landed) == "details ›"
        run.emit("spend", item_id="w1-2", source="measurement", cost_usd=1.6)
        await harness.shown(pilot)
        assert subtitle_of(measuring) == "details ›  $1.60"
        assert text(asking.steps) == "* asking  · answered"
        assert text(queued.line4.label) == words.QUEUED
        # A card keeps its place from the start of the wave to its verdict.
        run.outcome("w1-2", "not_landed", rc_id="rc_b", stop_kind="futility")
        await harness.shown(pilot)
        assert section.cards["w1-2"] is measuring
        assert text(measuring.line5) == "✗ Stopped: can't beat its parent"
        assert not measuring.has_class("-live")


def test_a_hypothesis_is_cut_after_two_lines():
    said = Content.assemble("Hypothesis: ", ("word " * 30, "$text-muted"))
    drawn = two_lines(said, 20)
    lines = drawn.plain.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("Hypothesis: word")
    assert lines[1].endswith("…") and len(lines[1]) <= 20
    # The cut keeps the planner's words muted, its "…" too.
    last = drawn.spans[-1]
    assert (last.end, last.style) == (len(drawn.plain), "$text-muted")


async def test_the_strip_turns_into_a_gauge_when_the_items_do_not_fit():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "building_rc").state("w1-1", "measuring")
    run.emit(
        "measure_chunk",
        item_id="w1-1",
        rc_id="rc_a",
        planned=400,
        judged=12,
        fixed=2,
        broke=0,
        cells=[["e1", f"i{n}", False, n < 2] for n in range(12)],
    )
    async with on_screen(run) as (harness, _pilot):
        line4 = harness.screen.experiments.cards["w1-1"].line4
        assert not line4.strip.display
        assert line4.bar.display
        assert (line4.bar.progress, line4.bar.total) == (12, 400)
        assert text(line4.label) == "12/400"


async def test_gate_mode_shows_val_progress_and_the_gate_verdict():
    run = gated()
    async with on_screen(run) as (harness, pilot):
        section = harness.screen.experiments
        assert text(section.header.context).startswith(
            "Promotion gate · wave 1 · every eval on val · 1 at the gate"
        )
        card = section.cards["w1-1"]
        assert text(card.steps) == "✓ build  ✓ measure  ✓ analyze  * gate"
        assert card.line4.bar.display and not card.line4.strip.display
        assert text(card.line4.label) == "0 of 100 val items"
        assert text(card.line5) == "● Train success · checking validation set"
        run.gate_end(1)
        await harness.shown(pilot)
        assert text(card.steps) == "✓ build  ✓ measure  ✓ analyze  ✓ gate"
        assert text(card.line5) == "◆ Promoted · now Best Confirmed"


async def test_a_candidate_the_gate_did_not_pick_shows_a_warning():
    run = wave_one(fix("w1-1"), fix("w1-2", "e2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.success("w1-2", "rc_b", row("e2", 0.8, 0.85))
    tables = (
        table("rc_a", val_row("e1", 0.62, 0.70)),
        table("rc_b", val_row("e2", 0.80, 0.84)),
    )
    run.at(30).gate(1, *tables, winner="rc_a")
    async with on_screen(run) as (harness, _pilot):
        card = harness.screen.experiments.cards["w1-2"]
        assert text(card.steps) == "✓ build  ✓ measure  ✓ analyze  ○ gate"
        glyph = card.steps.render().spans[-2]
        assert glyph.style == "$text-warning"
        assert text(card.line5) == "○ Not selected · w1-1 promoted"


async def test_issue_measurement_cards_show_the_time_they_measure():
    run = wave_one(measure("w1-1"))
    run.at(13).state("w1-1", "measuring")
    async with on_screen(run.at(16)) as (harness, _pilot):
        card = harness.screen.experiments.cards["w1-1"]
        assert text(card.line4.label) == "measuring · 3m00s"


async def test_done_mode_shows_the_run_summary_with_its_links():
    run = done()
    async with on_screen(run) as (harness, pilot):
        section = harness.screen.experiments
        assert not section.grid.display
        assert section.done.display
        assert title_of(section.done) == words.TUNING_COMPLETE
        assert subtitle_of(section.done) == "run 261109-1412"
        body = text(section.done.body).split("\n")
        assert body[0] == (
            "Ended early: not enough time or budget for another wave"
            " · 2h50m · $12.50 of $50.00"
        )
        assert body[1] == "1 wave · 2 experiments · 1 promotion · 1 stopped early"
        assert body[3].startswith("Best Confirmed  Hold fields · w1-1")
        assert body[4] == "Report          /state/reports/report.md ↗"
        assert len(body) == 5
        title = section.done.query_one(".next-title", Static)
        assert text(title) == "Next steps"
        await pilot.pause()
        steps = [text(row) for row in section.done.query(".next-step-text")]
        assert steps == [
            "Visualize Experiment Graph in Kiln ↗",
            "Compare w1-1 to the starting point in Kiln ↗  You may need to run"
            " evals on the 'test' dataset, which is intentionally withheld from"
            " tuning.",
            "Read Tuning Report ↗",
        ]
        assert harness.screen.has_class("-done")
        harness.fake.release.set()
        await until(
            pilot, lambda: (harness.app.drain(), harness.app.phase)[1] == "done"
        )
        await pilot.pause()
        # `e` too: Done keeps the run's items.
        assert footer(harness.app) == {
            "e": words.EXPERIMENTS_KEY,
            "c": words.COMPARE_LINK,
            "g": words.GRAPH_KEY,
            "r": words.REPORT_LINK,
            "q": words.CLOSE_KEY,
        }


async def test_before_the_report_its_step_is_muted_and_with_no_promotion_no_link():
    run = wave_one(fix("w1-1"))
    run.at(170).emit("exit", reason="timeout")
    async with on_screen(run) as (harness, pilot):
        card = harness.screen.experiments.done
        await pilot.pause()
        rows = list(card.query(".next-step-text"))
        assert [text(row) for row in rows] == [
            "Visualize Experiment Graph in Kiln ↗",
            "No experiment was promoted, so the starting point stays best.",
            "Read Tuning Report (writing…)",
        ]
        assert rows[2].render().spans[0].style == "$text-muted"
        assert text(card.body).split("\n")[4] == "Report          writing the report…"
        run.emit("report_written", path="/state/reports/r.md", fallback=False)
        await harness.shown(pilot)
        rows = list(card.query(".next-step-text"))
        assert text(rows[2]) == "Read Tuning Report ↗"


async def test_stopping_shows_in_the_experiments_header():
    run = running()
    async with on_screen(run) as (harness, pilot):
        screen = harness.screen
        screen.refresh_from(run.model, stopping=True, now=run.t)
        await pilot.pause()
        assert text(screen.experiments.header.context) == (
            "* Stopping… · cancelling 3 experiments"
        )


# ---- the Log ----


async def test_the_log_writes_new_lines_only_and_follows_the_end():
    run = running()
    async with on_screen(run) as (harness, pilot):
        log = harness.screen.log_section.lines
        assert [line.text for line in log.kept] == [line.text for line in run.model.log]
        written = len(log.lines)
        await harness.shown(pilot)
        assert len(log.lines) == written  # nothing written twice
        for n in range(80):
            run.model.log.append(LogLine("14:33", "!", "warning", f"line {n}"))
        run.model.version += 1
        await harness.shown(pilot)
        await pilot.pause()
        assert log.is_vertical_scroll_end
        # A reader who scrolled up stays where they are.
        log.scroll_to(y=0, animate=False)
        await pilot.pause()
        run.model.log.append(LogLine("14:34", "!", "warning", "one more"))
        run.model.version += 1
        await harness.shown(pilot)
        await pilot.pause()
        assert log.scroll_y == 0
        assert log.kept[-1].text == "one more"
        # Back at the end, it follows the newest line again.
        log.scroll_end(animate=False)
        await settled(pilot)
        run.model.log.append(LogLine("14:35", "!", "warning", "and one more"))
        run.model.version += 1
        await harness.shown(pilot)
        await settled(pilot)
        assert log.is_vertical_scroll_end


def long_log() -> Run:
    """A run whose log is longer than the log section is tall."""
    run = running()
    for n in range(80):
        run.model.log.append(LogLine("14:33", "!", "warning", f"line {n}"))
    run.model.version += 1
    return run


async def settled(pilot) -> None:
    for _ in range(4):
        await pilot.pause()


async def test_the_log_opens_at_its_newest_line():
    async with on_screen(long_log()) as (harness, pilot):
        await settled(pilot)
        log = harness.screen.log_section.lines
        assert log.max_scroll_y > 0
        assert log.is_vertical_scroll_end


async def test_the_log_follows_after_a_height_change():
    async with on_screen(long_log()) as (harness, pilot):
        await settled(pilot)
        log = harness.screen.log_section.lines
        await pilot.resize_terminal(210, 50)
        await settled(pilot)
        assert log.is_vertical_scroll_end
        # A reader who scrolled up stays where they are.
        log.scroll_to(y=0, animate=False)
        await pilot.pause()
        await pilot.resize_terminal(210, 62)
        await settled(pilot)
        assert log.scroll_y == 0


async def test_a_divider_and_a_line_wrap_under_their_text():
    run = running()
    run.model.log.append(
        LogLine("14:33", "✗", "error", "a long stop line " * 10, bold=False)
    )
    run.model.version += 1
    async with on_screen(run) as (harness, _pilot):
        drawn = [strip.text for strip in harness.screen.log_section.lines.lines]
        assert drawn[0].startswith("── start ─")
        stop = next(i for i, line in enumerate(drawn) if "a long stop" in line)
        assert drawn[stop].startswith("14:33 ✗ a long stop line")
        # The rest wraps under the text, never under the time.
        assert drawn[stop + 1].startswith(" " * 8)
        assert drawn[stop + 1][8] != " "


# ---- keys and links ----


async def test_the_keys_open_compare_lineage_and_the_report():
    run = running()
    async with on_screen(run) as (harness, pilot):
        model = run.model
        assert footer(harness.app) == {
            "e": words.EXPERIMENTS_KEY,
            "c": words.COMPARE_LINK,
            "g": words.GRAPH_KEY,
            "q": words.STOP_KEY,
        }
        await pilot.press("c", "g", "r")
        assert harness.opened == [model.compare_url(), model.lineage_url()]
        run.emit("report_written", path="/state/reports/r.md", fallback=False)
        await harness.shown(pilot)
        assert footer(harness.app)["r"] == words.REPORT_LINK
        await pilot.press("r")
        assert harness.opened[-1] == "/state/reports/r.md"


async def test_a_click_on_the_compare_link_opens_it():
    run = running()
    async with on_screen(run) as (harness, pilot):
        meta = harness.screen.results.header.meta
        await pilot.click(meta, offset=(2, 0))
        assert harness.opened == [run.model.compare_url()]


# ---- updates and failures ----


async def test_unchanged_views_update_no_widget(monkeypatch):
    run = running()
    async with on_screen(run) as (harness, _pilot):
        updates: list[object] = []
        real_static, real_bar = Static.update, ProgressBar.update

        def static_update(self, *args, **kwargs):
            updates.append(self)
            real_static(self, *args, **kwargs)

        def bar_update(self, **kwargs):
            updates.append(self)
            real_bar(self, **kwargs)

        monkeypatch.setattr(Static, "update", static_update)
        monkeypatch.setattr(ProgressBar, "update", bar_update)
        harness.screen.refresh_from(run.model, stopping=False, now=run.t)
        assert updates == []
        run.t += 1
        harness.screen.refresh_from(run.model, stopping=False, now=run.t)
        clock = harness.screen.clock
        assert clock.time.value in updates
        assert clock.spend.value not in updates


async def test_a_failing_section_keeps_its_last_text_and_the_rest_go_on(caplog):
    run = running()
    async with on_screen(run) as (harness, pilot):
        screen = harness.screen
        before = text(screen.clock.time.value)

        def broken(_view):
            raise RuntimeError("no clock")

        screen.clock.show = broken
        run.at(25).outcome("w1-5", "dead_end")
        with caplog.at_level(logging.ERROR, logger="kiln_harness_tuner.tui"):
            await harness.shown(pilot)
        assert "the run screen could not show its clock" in caplog.text
        assert text(screen.clock.time.value) == before
        assert text(screen.experiments.cards["w1-5"].line5).startswith("✗ Dead end")


# ---- layout and themes ----


async def test_the_narrow_layout_stacks_the_sections():
    async with on_screen(running(), size=(120, 40)) as (harness, _pilot):
        screen = harness.screen
        assert screen.has_class("-narrow")
        goal, spend = screen.goal.region, screen.clock.region
        assert spend.y > goal.y
        experiments, log = screen.experiments.region, screen.log_section.region
        assert log.y >= experiments.bottom
        assert screen.query_one("#body").max_scroll_y > 0  # it scrolls: none cut


async def test_the_wide_layout_puts_the_log_beside_experiments():
    async with on_screen(running()) as (harness, _pilot):
        screen = harness.screen
        assert screen.has_class("-wide")
        experiments, log = screen.experiments.region, screen.log_section.region
        assert log.x > experiments.right - 1
        assert screen.goal.region.y == screen.clock.region.y


@pytest.mark.parametrize("cards", [1, 30])
async def test_beside_the_log_experiments_and_the_log_are_always_as_tall(cards):
    run = wave_one(*(fix(f"w1-{n}") for n in range(1, cards + 1)))
    async with on_screen(run, size=(210, 62)) as (harness, pilot):
        screen = harness.screen
        await pilot.pause()
        experiments, log = screen.experiments.region, screen.log_section.region
        assert (experiments.y, experiments.height) == (log.y, log.height)
        body = screen.query_one("#body")
        if cards == 1:
            # Both fill the rest of the screen, and nothing scrolls.
            assert experiments.bottom == body.region.bottom
            assert body.max_scroll_y == 0
        else:
            # Both grow to the cards, and the screen scrolls.
            assert experiments.height == screen.experiments.content_height()
            assert body.max_scroll_y > 0


async def test_the_two_shrink_together_when_the_cards_go():
    run = wave_one(*(fix(f"w1-{n}") for n in range(1, 31)))
    async with on_screen(run, size=(210, 62)) as (harness, pilot):
        screen = harness.screen
        body = screen.query_one("#body")
        assert body.max_scroll_y > 0
        run.at(30).plan(2).at(31).planned(2)
        run.wave_start(2, fix("w2-1"))
        await harness.shown(pilot)
        await pilot.pause()
        experiments, log = screen.experiments.region, screen.log_section.region
        assert experiments.height == log.height
        assert body.max_scroll_y == 0


def meta_shown(section) -> tuple[str, str]:
    """Where a section's meta is drawn, and its text: on the top border
    after the name, or on its own lines inside."""
    title = title_of(section)
    name = section.section_name
    if title != name:
        assert title.startswith(f"{name}   ")
        # As drawn: Textual cuts a title that is too long with "…".
        strip = section.render_lines(Region(0, 0, section.outer_size.width, 1))[0]
        assert title in "".join(segment.text for segment in strip)
        return "border", title.removeprefix(f"{name}   ")
    lines = text(section.meta).split("\n")
    assert max(len(line) for line in lines) <= section.meta.size.width
    return "inside", "  ".join(lines)


@pytest.mark.parametrize("width", [*WIDTHS, 99, 100, 101])
async def test_the_wave_and_the_legend_are_never_cut_off(width):
    run = wave_one(fix("w1-1"))
    run.at(40).plan(2, wrap_up=True)
    async with on_screen(run, size=(width, 40)) as (harness, _pilot):
        screen = harness.screen
        where, wave = meta_shown(screen.clock)
        assert (where, wave) == ("border", f"wave 2 of 2   {words.LAST_WAVE_TAG} ")
        where, legend = meta_shown(screen.timeline)
        assert legend == LEGEND
        # On the border, beside the name, where it fits.
        assert where == ("inside" if width < 101 else "border")


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("make", [running, done])
async def test_the_compare_link_never_has_a_line_of_its_own(make, width):
    async with on_screen(make(), size=(width, 40)) as (harness, _pilot):
        results = harness.screen.results
        assert text(results.header.meta) == "Compare in Kiln ↗"
        assert text(results.header.context).startswith("■ Start")
        drawn = text(results.key).split("\n") if results.key.display else []
        assert all(len(line) <= results.key.size.width for line in drawn)


def judged_run() -> Run:
    """Four experiments, each with all 36 items judged, at 20 minutes."""
    run = wave_one(*(fix(f"w1-{n}") for n in range(1, 5)))
    for n in range(1, 5):
        run.state(f"w1-{n}", "building_rc").state(f"w1-{n}", "measuring")
        run.chunk(f"w1-{n}", f"rc_{n}", [(False, True)] * 36)
    return run.at(20)


@pytest.mark.parametrize(
    "size,result_width,experiment_width", [((210, 62), 40, 46), ((120, 40), 38, 58)]
)
async def test_the_frames_cost_the_cards_no_width(size, result_width, experiment_width):
    """The widths the cards had before the sections had frames: at 210
    columns a 36-item strip still fits."""
    async with on_screen(judged_run(), size=size) as (harness, _pilot):
        screen = harness.screen
        assert screen.results.cards[0].region.width == result_width
        cards = list(screen.experiments.cards.values())
        assert {card.region.width for card in cards} == {experiment_width}
        for card in cards:
            assert card.line4.strip.display
            assert not card.line4.bar.display
            assert text(card.line4.label) == "36/36"


def lightness(color, system: ColorSystem) -> float:
    """A color's lightness, 0 to 255, as a terminal with `system` shows it."""
    shown = color.rich_color.downgrade(system).get_truecolor()
    return 0.2126 * shown.red + 0.7152 * shown.green + 0.0722 * shown.blue


@pytest.mark.parametrize("system", [ColorSystem.TRUECOLOR, ColorSystem.EIGHT_BIT])
@pytest.mark.parametrize("theme", [DARK, LIGHT])
async def test_a_section_frame_and_a_card_border_clearly_differ(theme, system):
    async with on_screen(running(), theme=theme) as (harness, _pilot):
        _edge, frame = harness.screen.results.styles.border_top
        _edge, card = harness.screen.results.cards[0].styles.border_top
        assert abs(lightness(frame, system) - lightness(card, system)) >= 40


@pytest.mark.parametrize("make", [Run, running, gated, done])
async def test_every_mode_draws_in_the_light_theme(make):
    async with on_screen(make(), theme=LIGHT) as (harness, pilot):
        assert isinstance(harness.app.screen, RunScreen)
        assert harness.app.theme == LIGHT
        await pilot.pause(0.15)  # a turn of the spinners
        harness.app.export_screenshot()


async def test_the_spinners_turn_on_their_own_timer():
    async with on_screen(running()) as (harness, pilot):
        steps = harness.screen.experiments.cards["w1-1"].steps
        frames = set()
        for _ in range(4):
            frames.add(str(steps.render())[0])
            await pilot.pause(0.11)
        assert len(frames) > 1
        assert unspun("".join(frames)) == "*" * len(frames)


async def test_a_run_config_built_this_run_is_named_once_kiln_knows_it():
    lookups: list[str] = []

    async def lookup(info):
        lookups.append(info.task_id)
        return [{"id": "rc_c", "name": "Hold fields wrapper", "description": "d"}]

    run = running()
    async with on_screen(run, lookup=lookup) as (harness, pilot):
        await until(pilot, lambda: lookups)
        await until(
            pilot,
            lambda: (
                harness.app.drain(),
                "Hold fields wrapper" in subtitle_of(harness.screen.results.cards[0]),
            )[1],
        )
        assert subtitle_of(harness.screen.results.cards[0]) == (
            "* 1 in flight · Hold fields wrapper · w1-3"
        )
        assert lookups == ["424242783017"]


async def test_a_failed_name_lookup_keeps_the_ids_and_the_app_running():
    calls: list[int] = []

    async def lookup(_info):
        calls.append(1)
        raise RuntimeError("Kiln is gone")

    run = running()
    async with on_screen(run, lookup=lookup) as (harness, pilot):
        await until(pilot, lambda: calls)
        await harness.shown(pilot)
        assert subtitle_of(harness.screen.results.cards[0]) == "* 1 in flight · w1-3"
        # The next unknown id asks again, once 10 s have passed.
        run.success("w1-1", "rc_new", row("e1", 0.6, 0.65))
        await harness.shown(pilot)
        assert calls == [1]
        run.t += 11
        run.success("w1-5", "rc_newer", row("e1", 0.6, 0.62))
        await until(pilot, lambda: (harness.app.drain(), len(calls))[1] == 2)
        assert not harness.app.return_code


# ---- links with no browser ----


def no_browser(monkeypatch) -> list[str]:
    """`open_link` as it is with no graphical browser, or over SSH."""
    asked: list[str] = []

    def open_link(url: str) -> bool:
        asked.append(url)
        return False

    monkeypatch.setattr(app_module.opener, "open_link", open_link)
    return asked


def link_lines(harness) -> list[str]:
    return [
        line.text for line in harness.screen.log_section.lines.kept if line.glyph == "↗"
    ]


async def test_with_no_browser_the_keys_put_each_address_in_the_log(monkeypatch):
    asked = no_browser(monkeypatch)
    run = running()
    run.emit("report_written", path="/state/reports/r.md", fallback=False)
    async with on_screen(run, catch_links=False) as (harness, pilot):
        await pilot.press("c", "g", "r")
        await pilot.pause()
        compare, lineage = harness.screen.compare, run.model.lineage_url()
        assert asked == [compare, lineage, "/state/reports/r.md"]
        assert link_lines(harness) == [
            f"Open this link in your browser: {compare}",
            f"Open this link in your browser: {lineage}",
            "Open this link in your browser: /state/reports/r.md",
        ]
        # The whole address shows: a long line wraps, it is never cut.
        drawn = "".join(
            strip.text.strip() for strip in harness.screen.log_section.lines.lines
        )
        assert compare in drawn


async def test_with_no_browser_a_click_on_done_puts_the_address_in_the_log(
    monkeypatch,
):
    no_browser(monkeypatch)
    run = done()
    async with on_screen(run, catch_links=False) as (harness, pilot):
        harness.fake.release.set()
        await until(
            pilot, lambda: (harness.app.drain(), harness.app.phase)[1] == "done"
        )
        card = harness.screen.experiments.done
        graph = card.query(".next-step-text").first()
        await pilot.click(graph, offset=(2, 0))
        # The report's path on the Report row opens as the `r` key does.
        await pilot.click(card.body, offset=(len("Report          ") + 2, 4))
        await pilot.pause()
        assert link_lines(harness) == [
            f"Open this link in your browser: {run.model.lineage_url()}",
            "Open this link in your browser: /state/reports/report.md",
        ]


async def test_a_link_that_opens_adds_no_log_line(monkeypatch):
    monkeypatch.setattr(app_module.opener, "open_link", lambda _url: True)
    async with on_screen(running(), catch_links=False) as (harness, pilot):
        await pilot.press("c")
        await pilot.pause()
        assert link_lines(harness) == []


# ---- one Compare link, one failure line, one write of the log ----


async def test_the_compare_link_is_built_once_from_the_results_view():
    run = done()

    def again():
        raise AssertionError("the Compare link was built a second way")

    async with on_screen(run) as (harness, pilot):
        run.model.compare_url = again
        run.emit("spend", source="x", lane="provider", cost_usd=1.0)
        await harness.shown(pilot)
        screen = harness.screen
        compare = screen.compare
        assert text(screen.results.header.meta) == "Compare in Kiln ↗"
        await pilot.press("c")
        assert harness.opened == [compare]


async def test_a_section_that_keeps_failing_is_logged_once(caplog):
    run = running()
    async with on_screen(run) as (harness, pilot):
        screen = harness.screen

        def broken(_view):
            raise RuntimeError("no clock")

        screen.clock.show = broken
        with caplog.at_level(logging.DEBUG, logger="kiln_harness_tuner.tui"):
            for _ in range(3):
                run.t += 1
                await harness.shown(pilot)
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert [r.getMessage() for r in errors] == [
            "the run screen could not show its clock"
        ]
        assert errors[0].exc_info is not None
        debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debug) == 2


# At some widths the rounding of Experiments' 68% would hide a scrollbar's
# cell from the log; at one of these two the cell would come out of it.
@pytest.mark.parametrize("width", [210, 215])
async def test_a_scrollbar_that_comes_never_rewrites_the_log(monkeypatch, width):
    run = wave_one(fix("w1-1"))
    async with on_screen(run, size=(width, 62)) as (harness, pilot):
        screen = harness.screen
        body = screen.query_one("#body")
        log = screen.log_section.lines
        width = log.size.width
        assert body.max_scroll_y == 0
        rewrites: list[int] = []
        monkeypatch.setattr(type(log), "rewrite", lambda self: rewrites.append(1))
        run.at(30).plan(2).at(31).planned(2)
        run.wave_start(2, *(fix(f"w2-{n}") for n in range(1, 31)))
        await harness.shown(pilot)
        await pilot.pause()
        assert body.max_scroll_y > 0
        assert log.size.width == width
        assert rewrites == []
