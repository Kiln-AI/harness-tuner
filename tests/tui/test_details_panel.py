"""The experiment details panel and the experiment picker on screen
(functional spec 7.9, ui_design 4.13 and 10). Pilot tests at 210×62 over a
recorded run."""

import signal

from app_fakes import until
from model_events import Run, fix, probe, row
from run_screen import done, on_screen, running, text, title_of, wave_one
from textual.color import Color
from textual.widgets import Button

from kiln_harness_tuner.tui.model import details as details_model
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.screens.details import (
    DetailsPanel,
    ExperimentPicker,
    FieldRow,
)
from kiln_harness_tuner.tui.screens.run import RunScreen
from kiln_harness_tuner.tui.screens.stop import StopDialog

LONG = " ".join(["Return only the hold fields from get_container."] * 30)


def footer(app) -> dict[str, str]:
    return {
        key: active.binding.description
        for key, active in app.screen.active_bindings.items()
        if active.binding.show and active.enabled
    }


def field_texts(panel: DetailsPanel) -> dict[str, str]:
    rows = panel.body.query(FieldRow)
    return {text(row.children[0]): text(row.value) for row in rows}


def closed(app) -> bool:
    return app.return_code is not None


async def opened(pilot, kind):
    """The modal of `kind` on top, once it has shown the run."""
    app = pilot.app
    await until(pilot, lambda: isinstance(app.screen, kind) and app.screen.ready)
    await pilot.pause()
    return app.screen


async def drained(harness, pilot, condition) -> None:
    """The harness drains by hand: drain until `condition` holds."""
    await until(pilot, lambda: (harness.app.drain(), condition())[1])


# ---- opening by mouse ----


async def test_a_click_on_a_card_opens_its_panel_and_esc_closes_it():
    run = running()
    async with on_screen(run) as (harness, pilot):
        app = harness.app
        await pilot.click(harness.screen.experiments.cards["w1-3"])
        panel = await opened(pilot, DetailsPanel)
        assert panel.key == (0, "w1-3")
        assert title_of(panel.query_one("#details")) == "w1-3 target: Hold placement"
        assert text(panel.status) == "● Train success · checking validation set"
        assert field_texts(panel) == {
            "Kind": "Fix",
            "Wave": "1",
            "Parent": "Start · rc 0003",
            "Targets": "Eval: Hold placement",
            "Lever": "prompt",
            # Built, measured and analyzed in one moment of the recording.
            "Time": "0s",
        }
        assert text(panel.links) == (
            "Show in Experiment Graph ↗    Compare to parent in Kiln ↗"
        )
        assert footer(app) == {
            "escape": words.CLOSE_KEY,
            "up": words.SCROLL_KEY,
            "q": words.STOP_KEY,
        }
        await pilot.press("escape")
        await until(pilot, lambda: isinstance(app.screen, RunScreen))


async def test_a_card_lights_up_on_hover_and_marks_its_details():
    run = running()
    async with on_screen(run) as (harness, pilot):
        card = harness.screen.experiments.cards["w1-3"]
        colors = harness.app.theme_variables
        assert card.styles.border_top[1] == Color.parse(colors["border-muted"])
        await pilot.hover(card)
        await pilot.pause()
        assert card.styles.border_top[1] == Color.parse(colors["text-primary"])
        await pilot.hover(harness.screen.log_section)
        await pilot.pause()
        assert card.styles.border_top[1] == Color.parse(colors["border-muted"])


async def test_a_click_on_a_panel_link_opens_it():
    run = running()
    async with on_screen(run) as (harness, pilot):
        harness.app.show_item((0, "w1-3"))
        await opened(pilot, DetailsPanel)
        await pilot.click(harness.app.screen.links, offset=(2, 0))
        assert harness.opened == [run.model.lineage_url("rc_c")]


async def test_a_key_with_no_item_opens_nothing():
    async with on_screen(running()) as (harness, pilot):
        harness.app.show_item((0, "w9-9"))
        harness.app.show_item((7, "w1-1"))
        await pilot.pause()
        assert isinstance(harness.app.screen, RunScreen)


# ---- the picker ----


async def test_e_opens_the_picker_and_enter_opens_an_item_esc_goes_back():
    run = Run().standard_start()
    run.model.agent_cli_checked(run.t - 2)
    run.at(10).plan(1).at(12).planned(1)
    run.wave_start(1, probe("w1-1"), probe("w1-2"), probe_phase=True)
    run.outcome("w1-1").outcome("w1-2")
    run.plan(1, replan=True).planned(1)
    run.wave_start(1, fix("w1-1"), fix("w1-2", "e2"))
    run.state("w1-1", "building_rc")
    async with on_screen(run) as (harness, pilot):
        app = harness.app
        await pilot.press("e")
        picker = await opened(pilot, ExperimentPicker)
        prompts = [str(option.prompt) for option in picker.options.options]
        assert [p.split("  ")[0].strip() for p in prompts] == [
            "WAVE 1",
            "w1-1 target: Hold placement",
            "w1-2 target: Refusal honesty",
            "",
            "WAVE 1 · probes",
            "w1-1 probe",
            "w1-2 probe",
        ]
        assert footer(app) == {
            "up": words.MOVE_KEY,
            "enter": words.OPEN_KEY,
            "escape": words.CLOSE_KEY,
            "q": words.STOP_KEY,
        }
        # The headers are not rows: the first item has the highlight, and
        # ↓ steps over the next header.
        assert picker.options.highlighted_option.id == "1/w1-1"
        await pilot.press("down", "down")
        assert picker.options.highlighted_option.id == "0/w1-1"
        await pilot.press("enter")
        panel = await opened(pilot, DetailsPanel)
        # The probe phase's w1-1, not the re-plan's.
        assert panel.key == (0, "w1-1") and panel.from_picker
        assert field_texts(panel)["Kind"] == "Probe"
        assert footer(app)["escape"] == words.BACK_KEY
        await pilot.press("escape")
        await until(pilot, lambda: app.screen is picker)
        assert picker.options.highlighted_option.id == "0/w1-1"
        await pilot.press("escape")
        await until(pilot, lambda: isinstance(app.screen, RunScreen))


async def test_e_shows_once_the_run_has_items():
    run = wave_one()
    async with on_screen(run) as (harness, pilot):
        assert "e" not in footer(harness.app)
        await pilot.press("e")
        assert isinstance(harness.app.screen, RunScreen)
        run.plan(2).planned(2).wave_start(2, fix("w2-1"))
        await harness.shown(pilot)
        assert footer(harness.app)["e"] == words.EXPERIMENTS_KEY


async def test_the_picker_updates_live_and_keeps_its_highlight():
    run = running()
    async with on_screen(run) as (harness, pilot):
        app = harness.app
        app.show_picker()
        picker = await opened(pilot, ExperimentPicker)
        await pilot.press("down")
        assert picker.options.highlighted_option.id == "0/w1-2"
        run.outcome("w1-2", "not_landed", rc_id="rc_b", stop_kind="futility")
        await harness.shown(pilot)
        option = picker.options.get_option("0/w1-2")
        assert str(option.prompt).endswith("✗ Stopped: can't beat its parent")
        # A new wave's items come first; the highlight stays on its item.
        run.at(30).plan(2).planned(2).wave_start(2, fix("w2-1"))
        await harness.shown(pilot)
        assert picker.options.options[1].id == "1/w2-1"
        assert picker.options.highlighted_option.id == "0/w1-2"


async def test_done_keeps_e_and_its_picker():
    async with on_screen(done()) as (harness, pilot):
        harness.fake.release.set()
        await drained(harness, pilot, lambda: harness.app.phase == "done")
        await pilot.press("e")
        await opened(pilot, ExperimentPicker)
        await pilot.press("enter")
        await opened(pilot, DetailsPanel)
        # Done: `q` does nothing in a modal, so it is not in its footer.
        assert footer(harness.app) == {
            "escape": words.BACK_KEY,
            "up": words.SCROLL_KEY,
        }
        await pilot.press("q")
        assert not closed(harness.app)


# ---- live updates ----


async def test_the_panel_updates_live_and_keeps_its_scroll_position():
    run = wave_one(fix("w1-1", hypothesis=LONG))
    run.state("w1-1", "building_rc")
    async with on_screen(run, size=(100, 30)) as (harness, pilot):
        app = harness.app
        app.show_item((0, "w1-1"))
        panel = await opened(pilot, DetailsPanel)
        await pilot.pause()
        assert panel.body.max_scroll_y > 0
        await pilot.press("down", "down", "down")
        assert panel.body.scroll_y == 3
        run.emit("spend", item_id="w1-1", source="create_experiment_rc", cost_usd=0.4)
        await harness.shown(pilot)
        assert field_texts(panel)["Spend"] == "$0.40 so far"
        assert panel.body.scroll_y == 3
        # A new section mounts the rows again, at the same place.
        run.state("w1-1", "measuring")
        run.chunk("w1-1", "rc_a", [(False, True)])
        await harness.shown(pilot)
        await pilot.pause()
        assert text(panel.status).startswith("* Measuring · 1 fixed")
        assert panel.body.scroll_y == 3
        assert "Show in Experiment Graph" in text(panel.links)


# ---- stopping and the end with a modal open ----


async def test_q_in_a_panel_opens_the_stop_dialog_and_a_stop_closes_the_app():
    run = running()
    async with on_screen(run) as (harness, pilot):
        app = harness.app
        app.show_item((0, "w1-1"))
        panel = await opened(pilot, DetailsPanel)
        await pilot.press("q")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        # Esc keeps tuning, back on the panel.
        await pilot.press("escape")
        await until(pilot, lambda: app.screen is panel)
        await pilot.press("ctrl+c")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        assert app.screen.query_one("#stop", Button).label.plain == words.STOP_TUNING
        await pilot.press("tab", "enter")
        await until(pilot, lambda: harness.session.run.stopping)
        assert app.screen is panel
        await drained(harness, pilot, lambda: closed(app))
    assert harness.fake.cancelled
    assert app.closed_on_purpose


async def test_a_signal_with_the_picker_open_stops_the_run_and_closes_the_app():
    async with on_screen(running()) as (harness, pilot):
        app = harness.app
        app.show_picker()
        await opened(pilot, ExperimentPicker)
        app.on_signal(signal.SIGTERM)
        assert harness.session.run.stopping
        await drained(harness, pilot, lambda: closed(app))


async def test_the_end_of_the_run_keeps_the_panel_open_over_done():
    run = running()
    async with on_screen(run) as (harness, pilot):
        app = harness.app
        app.show_item((0, "w1-3"))
        panel = await opened(pilot, DetailsPanel)
        await pilot.press("q")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        run.emit("exit", reason="timeout")
        harness.fake.release.set()
        await drained(harness, pilot, lambda: app.phase == "done")
        # The stop dialog asked about a run that has ended: it goes. The
        # panel stays, with Done under it.
        await until(pilot, lambda: app.screen is panel)
        assert harness.screen.has_class("-done")
        assert "q" not in footer(app)
        await pilot.press("escape")
        await until(pilot, lambda: isinstance(app.screen, RunScreen))
        await pilot.press("q")
        await until(pilot, lambda: closed(app))


async def test_a_run_that_fails_closes_the_app_over_any_modal():
    run = running()
    async with on_screen(run) as (harness, pilot):
        app = harness.app
        harness.fake.error = RuntimeError("Kiln is gone")
        app.show_picker()
        await opened(pilot, ExperimentPicker)
        await pilot.press("enter")
        await opened(pilot, DetailsPanel)
        harness.fake.release.set()
        await drained(harness, pilot, lambda: closed(app))
    assert app.closed_on_purpose


async def test_a_probe_panel_has_no_links():
    run = wave_one(probe("w1-1"))
    run.state("w1-1", "probing")
    async with on_screen(run) as (harness, pilot):
        harness.app.show_item((0, "w1-1"))
        panel = await opened(pilot, DetailsPanel)
        # A probe builds no run config of its own.
        assert not panel.links.display
        assert text(panel.status).startswith("* Asking")


async def test_a_landed_fix_shows_its_train_results():
    run = wave_one(fix("w1-1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    async with on_screen(run) as (harness, pilot):
        harness.app.show_item((0, "w1-1"))
        await opened(pilot, DetailsPanel)
        body = harness.app.screen.body
        titles = [text(w) for w in body.query(".detail-title")]
        assert titles == ["Hypothesis", "Predicted effect", "Result on train"]
        lines = [text(w) for w in body.query(".detail-line")]
        assert "Eval: Hold placement  +10.0 pp" in lines


async def test_a_panel_that_cannot_show_its_item_leaves_the_app_open(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("bad item data")

    monkeypatch.setattr(details_model, "details_view", broken)
    async with on_screen(running()) as (harness, pilot):
        harness.app.show_item((0, "w1-3"))
        await opened(pilot, DetailsPanel)
        assert harness.app.crash is None
        assert not closed(harness.app)
