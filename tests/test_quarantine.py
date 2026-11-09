"""The start-up split check: the held-out items are decided
before any agent runs; stored val/test results stay in the project;
a project it cannot decide refuses the start."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_ctx
from fake_dispatcher import FakeDispatcher

from kiln_harness_tuner.orchestrator import run_quarantine
from kiln_harness_tuner.quarantine import (
    QuarantineError,
    heldout_item_ids,
    membership,
    plan_quarantine,
    survey_heldout,
)

TASK = "424242300001"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class Project:
    """A Kiln project on disk, just the files the quarantine reads."""

    def __init__(self, root: Path):
        self.root = root
        self.task = root / "tasks" / f"{TASK} - T"
        _write(root / "project.kiln", {"id": "424242300000"})
        _write(self.task / "task.kiln", {"id": TASK})

    def eval_input(
        self,
        item_id: str,
        tags: list[str],
        text: str | None = None,
        reference: dict | None = None,
    ):
        doc = {
            "id": item_id,
            "tags": tags,
            "data": {
                "type": "single_turn",
                "user_message": {"text": text or f"input {item_id}"},
            },
        }
        if reference is not None:
            doc["reference"] = reference
        _write(self.task / "eval_inputs" / item_id / "eval_input.kiln", doc)

    def eval(self, eval_id: str, prefix: str, splits: dict | None = None):
        splits = splits or {
            name: {"source": "eval_input", "filter_id": f"tag::{name}_{prefix}"}
            for name in ("train", "val", "test")
        }
        folder = self.task / "evals" / f"{eval_id} - {prefix}"
        _write(folder / "eval.kiln", {"id": eval_id, "splits": splits})
        _write(folder / "configs" / "ec1 - judge" / "eval_config.kiln", {"id": "ec1"})
        return folder

    def eval_run(self, eval_id: str, run_id: str, item_id: str, scored: str | None):
        folder = next((self.task / "evals").glob(f"{eval_id} - *"))
        path = folder / "configs" / "ec1 - judge" / "runs" / run_id / "eval_run.kiln"
        _write(
            path,
            {"id": run_id, "eval_input_id": item_id, "scored_run_id": scored},
        )
        return path.parent

    def task_run(self, run_id: str, *, source: str | None = None, **extra):
        data = {"id": run_id, "input": extra.pop("input", f"drive {run_id}")}
        if source is not None:
            data["eval_source"] = {"source_type": "eval_input", "source_id": source}
        data.update(extra)
        _write(self.task / "runs" / run_id / "task_run.kiln", data)
        return self.task / "runs" / run_id


@pytest.fixture
def project(tmp_path) -> Project:
    p = Project(tmp_path / "proj")
    p.eval("e1", "a")
    p.eval_input("tr1", ["train_a"])
    p.eval_input("va1", ["val_a"])
    p.eval_input("te1", ["test_a"], text="the test question")
    p.eval_input("free", ["unrelated"])
    # Scored results on every split, each with its task run.
    for item in ("tr1", "va1", "te1", "free"):
        p.task_run(f"run_{item}", source=item)
        p.eval_run("e1", f"er_{item}", item, scored=f"run_{item}")
    return p


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*.kiln")}


class TestPlan:
    def test_val_and_test_results_move_and_train_stays(self, project):
        plan = plan_quarantine(project.task)
        moved = {p.name for p, _why in plan.moves}
        assert moved == {"er_va1", "er_te1", "run_va1", "run_te1"}

    def test_an_unscored_drive_on_a_heldout_input_moves(self, project):
        project.task_run("orphan", source="te1")
        project.task_run("copied", input="the test question")
        project.task_run("unrelated", input="something else")
        moved = {p.name for p, _why in plan_quarantine(project.task).moves}
        assert {"orphan", "copied"} <= moved
        assert "unrelated" not in moved

    def test_a_whole_conversation_moves_together(self, project):
        project.task_run("turn1", input="x")
        project.task_run("turn2", input="y", parent_task_run_id="turn1")
        project.task_run("turn3", source="va1", parent_task_run_id="turn2")
        moved = {p.name for p, _why in plan_quarantine(project.task).moves}
        assert {"turn1", "turn2", "turn3"} <= moved

    def test_an_item_train_for_one_eval_and_test_for_another_is_kept(self, project):
        project.eval("e2", "b")
        project.eval_input("both", ["train_a", "test_b"])
        project.task_run("run_both", source="both")
        project.eval_run("e1", "er_both_e1", "both", scored="run_both")
        project.eval_run("e2", "er_both_e2", "both", scored="run_both")
        plan = plan_quarantine(project.task)
        moved = {p.name for p, _why in plan.moves}
        # e2's test row is e2's val/test data and moves; the task run is
        # shared with e1's train row and stays; the item is reported.
        assert "er_both_e2" in moved
        assert "er_both_e1" not in moved and "run_both" not in moved
        assert ("eval_input", "both") in plan.overlap_items
        assert plan.kept_shared_task_runs == 1

    def test_a_row_on_its_own_evals_train_item_stays_even_if_also_tagged_val(
        self, project
    ):
        # A mis-tagged item in BOTH e1's train and val split: its e1 row is a
        # train row and must never move ("never touch train items").
        project.eval_input("twice", ["train_a", "val_a"])
        project.task_run("run_twice", source="twice")
        project.eval_run("e1", "er_twice", "twice", scored="run_twice")
        moved = {p.name for p, _why in plan_quarantine(project.task).moves}
        assert "er_twice" not in moved and "run_twice" not in moved

    def test_dataset_task_runs_are_inputs_and_never_move(self, project):
        project.eval(
            "e3",
            "c",
            {
                "train": {"source": "task_run", "filter_id": "tag::train_c"},
                "test": {"source": "task_run", "filter_id": "tag::test_c"},
            },
        )
        project.task_run("item_test", tags=["test_c"])
        moved = {p.name for p, _why in plan_quarantine(project.task).moves}
        assert "item_test" not in moved

    def test_heldout_tags_are_every_tag_on_a_heldout_item(self, project):
        project.eval_input("te2", ["test_a", "shared_suite"])
        plan = plan_quarantine(project.task)
        assert {"val_a", "test_a", "shared_suite"} <= plan.heldout_tags
        assert "train_a" not in plan.heldout_tags

    def test_an_orphan_heldout_tag_is_held_out(self, project):
        """A val_/test_ tag that no current eval uses (a deleted eval's test
        set): its results move, an orphan task run moves itself, a train
        item with such a tag stays."""
        project.eval_input("old", ["test_deleted_eval"])
        project.task_run("run_old", source="old")
        project.eval_run("e1", "er_old", "old", scored="run_old")
        project.task_run("legacy", tags=["VAL_legacy"], input="a legacy val case")
        project.eval_input("keep", ["train_a", "test_deleted_eval"])
        project.task_run("run_keep", source="keep")
        plan = plan_quarantine(project.task)
        moved = {p.name: why for p, why in plan.moves}
        assert {"er_old", "run_old", "legacy"} <= set(moved)
        assert "a held-out tag no eval split uses" in moved["legacy"]
        assert "run_keep" not in moved
        assert plan.orphan_items == {("eval_input", "old"), ("task_run", "legacy")}
        assert "test_deleted_eval" in plan.heldout_tags

    def test_the_orphans_reach_the_train_read_check(self, project):
        from kiln_harness_tuner.quarantine import heldout_item_ids

        project.eval_input("old", ["test_deleted_eval"])
        assert "old" in heldout_item_ids(project.root, TASK)

    @pytest.mark.parametrize("filter_id", ["high_rating", "multi_filter::x&y"])
    def test_a_filter_it_cannot_evaluate_refuses_the_start(self, project, filter_id):
        project.eval(
            "e4", "d", {"test": {"source": "task_run", "filter_id": filter_id}}
        )
        with pytest.raises(QuarantineError, match="fix: "):
            plan_quarantine(project.task)

    def test_an_unreadable_record_refuses_the_start(self, project):
        (project.task / "eval_inputs" / "bad").mkdir()
        (project.task / "eval_inputs" / "bad" / "eval_input.kiln").write_text(
            "{", encoding="utf-8"
        )
        with pytest.raises(QuarantineError, match="fix: "):
            plan_quarantine(project.task)


class TestSurvey:
    def test_counts_and_moves_nothing(self, project):
        before = {p: p.read_bytes() for p in project.root.rglob("*") if p.is_file()}
        report = survey_heldout(project.root, TASK)
        assert (report.stored_eval_runs, report.stored_task_runs) == (2, 2)
        after = {p: p.read_bytes() for p in project.root.rglob("*") if p.is_file()}
        assert after == before

    def test_no_task_directory_refuses(self, tmp_path):
        with pytest.raises(QuarantineError, match="fix: "):
            survey_heldout(tmp_path, TASK)

    def test_heldout_ids(self, project):
        assert heldout_item_ids(project.root, TASK) == {"va1", "te1"}


def test_the_orchestrator_checks_the_splits_before_any_dispatch(project, tmp_path):
    """The pass runs first in the loop: the two checks have their lists
    before the first agent session, and the stored held-out results stay."""
    dispatcher = FakeDispatcher()
    ctx = make_ctx(dispatcher=dispatcher)
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(project.root), "task_id": TASK}
    )
    ctx.quarantine_root = tmp_path / "q"
    ctx.train_set_path = tmp_path / "train_set.json"
    run_quarantine(ctx)
    assert dispatcher.preflight_calls == 0
    assert list(project.task.rglob("er_te1"))
    assert ctx.analyzer.heldout_ids == {"va1", "te1"}
    train = json.loads(
        ctx.train_set_path.read_text(encoding="utf-8")
    )  # for the gateway
    assert train["eval_inputs"] == ["tr1"]
    assert {"val_a", "test_a"} <= set(train["heldout_tags"])
    (event,) = [e for e in ctx.log.events if e[0] == "quarantine"]
    assert event[1]["stored_eval_runs"] == 2
    assert "stay in the project" in event[1]["line"]


async def test_orchestrate_checks_the_splits_before_the_preflight(project, tmp_path):
    from kiln_harness_tuner.orchestrator import orchestrate

    order: list[str] = []
    dispatcher = FakeDispatcher()
    original = dispatcher.preflight

    async def preflight():
        order.append(
            "preflight:"
            + ("checked" if ctx.analyzer.heldout_ids == {"va1", "te1"} else "unchecked")
            + (":present" if list(project.task.rglob("er_te1")) else ":moved")
        )
        raise RuntimeError("stop here")

    dispatcher.preflight = preflight  # type: ignore[method-assign]
    del original
    ctx = make_ctx(dispatcher=dispatcher)
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(project.root), "task_id": TASK}
    )
    ctx.quarantine_root = tmp_path / "q"
    with pytest.raises(RuntimeError, match="stop here"):
        await orchestrate(ctx, "g", timeout_mins=60)
    assert order == ["preflight:checked:present"]


async def test_a_new_eval_that_reaches_heldout_items_is_not_measured(project, tmp_path):
    """An eval made through a spec gets its splits on the server, where the
    hook cannot see them: code reads them from disk before measuring it."""
    from fake_kiln import make_eval

    from kiln_harness_tuner.orchestrator import _active_evals

    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(project.root), "task_id": TASK}
    )
    ctx.quarantine_root = tmp_path / "q"
    run_quarantine(ctx)
    assert ctx.startup_eval_ids == {"e1"}
    # Two evals appear later: one trains on test_a (held out), one on train_a.
    project.eval(
        "e8",
        "bad",
        {"train": {"source": "eval_input", "filter_id": "tag::test_a"}},
    )
    project.eval(
        "e9",
        "good",
        {"train": {"source": "eval_input", "filter_id": "tag::train_a"}},
    )
    for eval_id in ("e1", "e8", "e9"):
        ctx.kiln.evals[eval_id] = make_eval(eval_id, f"ec_{eval_id}")
    active = {str(e["id"]) for e, _c in await _active_evals(ctx)}
    assert active == {"e1", "e9"}
    (refused,) = [f for k, f in ctx.log.events if k == "eval_split_refused"]
    assert refused["eval_id"] == "e8"
    assert "holds 1 held-out item(s) (te1)" in refused["line"]
    # Checked once.
    await _active_evals(ctx)
    assert len([k for k, _f in ctx.log.events if k == "eval_split_refused"]) == 1


class TestHeldOutTagsByPattern:
    """Any tag on any item that matches a held-out pattern — the
    non-train split tags of every eval, plus val, test, val_*, test_*,
    holdout*, heldout* — is held out, used by a current eval or not."""

    def test_holdout_and_heldout_names_are_held_out(self, project):
        project.eval_input("h1", ["holdout_2023"])
        project.eval_input("h2", ["HeldOut"])
        project.eval_input("h3", ["train_a", "holdout_2023"])  # train: stays
        for item in ("h1", "h2", "h3"):
            project.task_run(f"run_{item}", source=item)
            project.eval_run("e1", f"er_{item}", item, scored=f"run_{item}")
        plan = plan_quarantine(project.task)
        moved = {p.name for p, _why in plan.moves}
        assert {"er_h1", "er_h2", "run_h1", "run_h2"} <= moved
        assert not {"er_h3", "run_h3"} & moved
        assert plan.orphan_tags == {"HeldOut": 1, "holdout_2023": 2}

    def test_a_non_train_split_of_any_name_is_held_out(self, project):
        project.eval(
            "e5",
            "g",
            {
                "train": {"source": "eval_input", "filter_id": "tag::train_g"},
                "golden_check": {"source": "eval_input", "filter_id": "tag::kept"},
            },
        )
        project.eval_input("k1", ["kept"])
        project.task_run("run_k1", source="k1")
        project.eval_run("e5", "er_k1", "k1", scored="run_k1")
        plan = plan_quarantine(project.task)
        assert {"er_k1", "run_k1"} <= {p.name for p, _why in plan.moves}
        assert "kept" not in plan.orphan_tags  # an eval defines it

    def test_a_plain_tag_is_not_held_out(self, project):
        project.eval_input("p1", ["validated", "testing_notes", "latest"])
        project.task_run("run_p1", source="p1")
        project.eval_run("e1", "er_p1", "p1", scored="run_p1")
        plan = plan_quarantine(project.task)
        assert "er_p1" not in {p.name for p, _why in plan.moves}

    def test_each_orphan_tag_is_logged(self, project, tmp_path):
        project.eval_input("h1", ["holdout_2023"])
        ctx = make_ctx()
        ctx.target = ctx.target.model_copy(
            update={"project_path": str(project.root), "task_id": TASK}
        )
        ctx.quarantine_root = tmp_path / "q"
        run_quarantine(ctx)
        (event,) = [f for k, f in ctx.log.events if k == "heldout_tag"]
        assert event["tag"] == "holdout_2023" and event["items"] == 1
        assert "treated as held out" in event["line"]
        from kiln_harness_tuner.ui import SETUP_LINE_EVENTS

        assert "heldout_tag" in SETUP_LINE_EVENTS

    def test_a_tag_a_train_split_uses_is_never_held_out(self, project):
        """One eval trains on `val_shared`, another validates on it: the
        tag is not held out (scrubbing by it would hide train items)."""
        project.eval(
            "e6",
            "s",
            {"train": {"source": "eval_input", "filter_id": "tag::val_shared"}},
        )
        project.eval(
            "e7",
            "t",
            {"val": {"source": "eval_input", "filter_id": "tag::val_shared"}},
        )
        project.eval_input("s1", ["val_shared"])
        member, _loaded = membership(project.task)
        assert "val_shared" not in member.heldout_tags
        assert ("eval_input", "s1") not in member.heldout
