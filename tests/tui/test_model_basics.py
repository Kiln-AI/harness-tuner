"""The run model's inputs and its title, goal and tracker (architecture
sections 7.1 and 7.3)."""

import subprocess
import sys
from dataclasses import replace
from datetime import datetime

import pytest
from model_events import INFO, T0, Run

from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.state import plain
from kiln_harness_tuner.tui.model.tracker import UiTracker

WALL = datetime(2026, 11, 9, 14, 0)


def test_ui_tracker_folds_on_the_event_stamp():
    live = [9999.0]
    tracker = UiTracker(
        project_id="p", task_id="t", agent_cli="claude", clock=lambda: live[0]
    )
    tracker.fold(Stamped("phase_start", {"name": "setup", "label": "x"}, 100.0, WALL))
    tracker.fold(Stamped("phase_end", {"name": "setup"}, 130.0, WALL))
    # The bare phase_end's elapsed comes from the two stamps, not the live clock.
    assert tracker.status.phase_segments[0].elapsed_secs == 30.0
    tracker.fold(Stamped("run_start", {"deadline": 10_000.0}, 100.0, WALL))
    # Between events, the live clock.
    assert tracker.snapshot().time_remaining_secs == 1.0


def test_ui_tracker_status_is_the_fold_itself():
    tracker = UiTracker(project_id="p", task_id="t", agent_cli="claude")
    tracker.fold(Stamped("spend", {"cost_usd": 2.5, "lane": "provider"}, 1.0, WALL))
    assert tracker.status is tracker.status
    assert tracker.status.spend_total_usd == 2.5
    assert tracker.snapshot() is not tracker.status


def test_unknown_and_malformed_events_are_ignored_and_bump_the_version():
    run = Run()
    version = run.model.version
    run.emit("a_kind_from_the_future", anything=1)
    run.emit("item_state", item_id="nope", state="measuring")
    run.emit("item_outcome", item_id="nope", outcome={"not": "an outcome"})
    run.emit("detectability", report={"not": "a report"})
    run.emit("gate_val_tables", tables="not a list")
    assert run.model.version == version + 5
    assert run.model.waves == [] and run.model.concerns == []


def test_an_event_the_tracker_cannot_fold_raises_for_the_drain():
    """The loop's own tracker requires some fields; the app's drain logs
    the error and skips the event (architecture 7.3)."""
    run = Run()
    version = run.model.version
    with pytest.raises(TypeError):
        run.emit("wave_start")
    assert (run.model.version, run.model.waves) == (version, [])


def test_the_title():
    model = RunModel(INFO, started=T0)
    title = model.title()
    assert (title.task, title.run) == ("Depot Ops Desk", "run 261109-1412")
    assert title.meta == "claude -p · 4 agent slots · 4 eval slots"
    model.update_info(run_id=None, agent_slots=1)
    assert model.title().run == ""
    assert model.title().meta == "claude -p · 1 agent slot · 4 eval slots"


@pytest.mark.parametrize(
    "objective,rows",
    [
        (
            "quality",
            [
                ("optimize", "Quality", "2 evals"),
                ("hold", "Every eval", "none may drop on val"),
                ("watch", "Cost, latency", "shown, never decide"),
            ],
        ),
        (
            "speed",
            [
                ("optimize", "Latency", "per request"),
                ("hold", "Quality", "equal or better on val"),
                ("watch", "Cost", "shown, never decides"),
            ],
        ),
        (
            "cost",
            [
                ("optimize", "Cost", "per request"),
                ("hold", "Quality", "equal or better on val"),
                ("watch", "Latency", "shown, never decides"),
            ],
        ),
    ],
)
def test_the_goal_follows_the_objective(objective, rows):
    run = Run(replace(INFO, objective=objective)).standard_start(objective=objective)
    goal = run.model.goal()
    assert [(r.role, r.what, r.note) for r in goal.rows] == rows


def test_the_goal_data_line():
    run = Run().standard_start()
    assert plain(run.model.goal().data) == (
        "train – · held out – (val and test) · test never read"
    )
    run.emit("train_set", line="", items=312)
    run.emit("quarantine", line="", heldout_items=208)
    assert plain(run.model.goal().data) == (
        "train 312 · held out 208 (val and test) · test never read"
    )


def test_the_model_never_imports_textual():
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, kiln_harness_tuner.tui.model.fold;"
            " print(sorted(m for m in sys.modules if m.split('.')[0] == 'textual'))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert loaded.stdout == "[]\n"


def test_the_stop_dialog_names_what_is_in_flight():
    assert words.stop_question(1, None) == (
        "1 experiment is in flight. Their work so far is lost, and no report"
        " is written."
    )
    assert words.stop_question(4, 2).startswith("4 experiments are in flight.")
    assert words.stop_question(0, 5) == (
        "The planner is at work on wave 5. Its work so far is lost, and no"
        " report is written."
    )
    assert words.stop_question(0, None) == (
        "The work so far is lost, and no report is written."
    )
    assert words.stopping_detail(4) == "cancelling 4 experiments"


def test_the_model_links_to_kiln():
    model = RunModel(INFO, started=T0)
    lineage = f"{INFO.kiln_host}/optimize/{INFO.project_id}/{INFO.task_id}"
    assert model.lineage_url() == f"{lineage}/experiment_lineage"
    assert model.lineage_url("rc_a").endswith("/experiment_lineage?selected=rc_a")


def test_learned_names_bump_the_version_only_when_they_change():
    model = RunModel(INFO, started=T0)
    version = model.version
    model.learn_names([{"id": "rc_a", "name": "Hold fields"}])
    assert (model.version, model.rc_label("rc_a")) == (version + 1, "Hold fields")
    model.learn_names([{"id": "rc_a", "name": "Hold fields"}])
    assert model.version == version + 1
