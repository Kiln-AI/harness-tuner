"""Failure cards: what the planner is given for each failed train item.

Code makes the input, the output, the expected output and a matching
success. A reader's note gives the step that lost the item and the kind of
the failure. Every id is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_gateway import BASE, PROJECT, FakeKiln, _run
from test_quarantine import TASK, Project

from kiln_harness_tuner.failure_notes import (
    FILE_NAME,
    LOST_AT_MAX_CHARS,
    append_notes,
    load_notes,
    note_for,
)
from kiln_harness_tuner.gateway import failure_cards as fc
from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.gateway.trainset import compute
from kiln_harness_tuner.schemas import FailureNote

RC = "424242100003"
OTHER_RC = "424242100009"
RESULTS = f"{BASE}/evals/e1/eval_config/ec1/run_config/{RC}/results"
EVAL = {
    "id": "e1",
    "current_config_id": "ec1",
    "output_scores": [
        {"name": "Accuracy", "type": "pass_fail", "direction": "higher_is_better"},
        {"name": "Length", "type": "five_star", "direction": "informational"},
    ],
}


INPUTS = {
    "f1": "refund for order 7",
    "f2": "invoice is wrong twice",
    "p1": "refund for order 9",
    "p2": "invoice was sent late",
    "p3": "where is my parcel",
    "va1": "SECRET val question",
}


def _row(item: str, accuracy: float | None, output: str, **extra) -> dict:
    return {
        "eval_run": {
            "id": f"er_{item}",
            "eval_input_id": item,
            "task_run_config_id": RC,
            "scores": {"accuracy": accuracy, "length": 1.0},
            "task_run_usage": {"cost": 0.01},
            **extra,
        },
        "input": INPUTS[item],
        "output": output,
        "task_run_trace": "[]",
    }


def _gateway(tmp_path, rows, role="batch_plan"):
    """A project with five train items of one eval and one val item. `rows`
    are the run config's stored results."""
    p = Project(tmp_path / "proj")
    p.eval("e1", "a")
    p.eval_input(
        "f1", ["train_a", "refund"], "refund for order 7", {"label": "approve"}
    )
    p.eval_input(
        "f2", ["train_a", "billing"], "invoice is wrong twice", {"label": "escalate"}
    )
    p.eval_input(
        "p1", ["train_a", "refund"], "refund for order 9", {"label": "approve"}
    )
    p.eval_input(
        "p2", ["train_a", "billing"], "invoice was sent late", {"label": "reply"}
    )
    p.eval_input("p3", ["train_a"], "where is my parcel", {"label": "reply"})
    p.eval_input("va1", ["val_a"], "SECRET val question", {"label": "SECRET"})
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    state = gw.State(
        project_id=PROJECT,
        task_id=TASK,
        role=role,
        train=compute(p.root, TASK),
        run_dir=run_dir,
        project_path=p.root,
    )
    kiln = FakeKiln(
        {
            ("GET", f"{BASE}/evals/e1"): EVAL,
            ("GET", RESULTS): {"results": rows},
        }
    )
    return gw.Gateway(kiln, state), kiln


ROWS = [
    _row("f1", 0.0, "deny", intermediate_outputs={"reasoning": "should approve"}),
    _row("f2", 0.0, "reply"),
    _row("p1", 1.0, "approve"),
    _row("p2", 1.0, "reply"),
    _row("p3", 1.0, "reply"),
    _row("va1", 0.0, "SECRET"),
]


def _cards(gateway, **arguments):
    return _run(
        gateway, "get_failure_cards", eval_id="e1", run_config_id=RC, **arguments
    )


class TestTheCard:
    def test_a_card_has_the_input_the_output_and_what_was_expected(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, ROWS)
        out = _cards(gateway)
        assert [c["item_id"] for c in out["cards"]] == ["f1", "f2"]
        card = out["cards"][0]
        assert card["input"] == "refund for order 7"
        assert card["output"] == "deny"
        assert json.loads(card["expected"]) == {"label": "approve"}
        assert card["judge_reason"] == "reasoning: should approve"
        assert card["failed_scores"] == ["accuracy"]
        assert card["scores"] == {"accuracy": 0.0, "length": 1.0}
        assert card["clipped"] == []
        # The gateway, not the agent, names the split.
        assert kiln.calls[-1][2] == {"split": "train"}

    def test_never_a_score_alone(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        for card in _cards(gateway)["cards"]:
            assert card["input"] and card["output"] and card["expected"]
            assert card["matching_success"] is not None
            assert Path(card["trace_path"]).is_file()

    def test_train_items_only(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        out = _cards(gateway)
        text = json.dumps(out)
        assert "SECRET" not in text and "va1" not in text
        assert out["dropped"] == 1
        assert (out["failed"], out["passed"], out["unscored"]) == (2, 3, 0)
        # No file of the held-out row is written.
        root = gateway.s.trace_root
        assert not list(root.rglob("va1*"))

    def test_a_held_out_item_asked_for_by_id_is_refused(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        for refused in ("va1", "nonexistent"):
            with pytest.raises(gw.GatewayError, match="not provably"):
                _cards(gateway, item_ids=["f1", refused])

    def test_an_item_asked_for_that_does_not_fail_has_no_card(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        out = _cards(gateway, item_ids=["p1", "f2"])
        assert [c["item_id"] for c in out["cards"]] == ["f2"]
        assert out["not_failed"] == ["p1"]
        assert out["total"] == 1

    def test_a_long_text_is_clipped_and_the_card_says_so(self, tmp_path):
        rows = [_row("f1", 0.0, "x" * 5000), *ROWS[1:]]
        gateway, _ = _gateway(tmp_path, rows)
        card = _cards(gateway)["cards"][0]
        assert len(card["output"]) == fc.OUTPUT_CHARS
        assert card["clipped"] == ["output"]
        # The trace file has all of it.
        assert "x" * 5000 in Path(card["trace_path"]).read_text()

    def test_a_page_ends_at_the_answer_cap(self, tmp_path):
        p_rows = [_row("f1", 0.0, "y" * 700), _row("f2", 0.0, "z" * 700), *ROWS[2:]]
        gateway, _ = _gateway(tmp_path, p_rows)
        first = _cards(gateway, limit=1)
        assert [c["item_id"] for c in first["cards"]] == ["f1"]
        assert first["next_offset"] == 1 and first["total"] == 2
        second = _cards(gateway, limit=1, offset=1)
        assert [c["item_id"] for c in second["cards"]] == ["f2"]
        assert second["next_offset"] is None
        assert len(json.dumps(first)) < gw.ANSWER_MAX_CHARS

    def test_ten_full_cards_fit_in_one_answer(self):
        card = gw.m.FailureCard(
            item_id="424242500001",
            scores={"accuracy": 0.0},
            failed_scores=["accuracy"],
            input="i" * fc.INPUT_CHARS,
            output="o" * fc.OUTPUT_CHARS,
            expected="e" * fc.EXPECTED_CHARS,
            judge_reason="j" * fc.JUDGE_REASON_CHARS,
            lost_at="l" * LOST_AT_MAX_CHARS,
            matching_success=gw.m.MatchingSuccess(
                item_id="424242500002",
                match="same expected output",
                input="i" * fc.MATCH_TEXT_CHARS,
                output="o" * fc.MATCH_TEXT_CHARS,
                trace_path="/" + "p" * 200,
            ),
            trace_path="/" + "p" * 200,
            trace_summary="s" * 120,
        )
        assert 10 * len(json.dumps(card.model_dump(mode="json"))) < gw.ANSWER_MAX_CHARS

    def test_a_row_with_no_readable_score_is_unscored(self, tmp_path):
        rows = [_row("f1", None, "deny"), *ROWS[1:]]
        gateway, _ = _gateway(tmp_path, rows)
        out = _cards(gateway)
        assert [c["item_id"] for c in out["cards"]] == ["f2"]
        assert out["unscored"] == 1

    def test_only_the_roles_that_plan_build_or_analyze_get_cards(self, tmp_path):
        for role in (
            "batch_plan",
            "create_experiment_rc",
            "analyze_experiment_results",
        ):
            assert "get_failure_cards" in gw.ROLE_OPS[role]
        # The baseline reviewer must not judge if an answer is right.
        gateway, _ = _gateway(tmp_path, ROWS, role="baseline_review")
        with pytest.raises(gw.GatewayError, match="not available"):
            _cards(gateway)


class TestTheMatchingSuccess:
    def test_the_same_expected_output_comes_first(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        match = _cards(gateway)["cards"][0]["matching_success"]
        assert match["item_id"] == "p1"
        assert match["match"] == "same expected output"
        assert match["input"] == "refund for order 9"
        assert match["output"] == "approve"
        assert Path(match["trace_path"]).is_file()

    def test_then_shared_tags_but_not_a_tag_every_item_has(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        # f2 expects `escalate`, which no passing item has. It shares the
        # tag `billing` with p2. `train_a` is on every item: it says nothing.
        match = _cards(gateway)["cards"][1]["matching_success"]
        assert match["item_id"] == "p2"
        assert match["match"] == "shares tags: billing"

    def test_then_the_closest_input(self):
        failure = fc.scored_row("f", False, "refund for the red shoes", None)
        passes = [
            fc.scored_row("a", True, "where is my parcel", None),
            fc.scored_row("b", True, "refund for the blue shoes", None),
        ]
        row, why = fc.matching_success(failure, passes, frozenset())
        assert row.item_id == "b"
        assert why.startswith("closest input (word overlap 0.")

    def test_a_tie_goes_to_the_lower_item_id(self):
        failure = fc.scored_row("f", False, "one two", {"reference": 1})
        passes = [
            fc.scored_row("b", True, "one two", {"reference": 1}),
            fc.scored_row("a", True, "one two", {"reference": 1}),
        ]
        assert fc.matching_success(failure, passes, frozenset())[0].item_id == "a"

    def test_no_passing_item_gives_no_matching_success(self, tmp_path):
        rows = [_row(i, 0.0, "bad") for i in ("f1", "f2", "p1")]
        gateway, _ = _gateway(tmp_path, rows)
        out = _cards(gateway)
        assert out["passed"] == 0
        assert all(c["matching_success"] is None for c in out["cards"])


class TestPassAndFail:
    def test_a_direction_decides_and_an_informational_score_is_not_read(self):
        specs = fc.score_specs(
            {
                "output_scores": [
                    {
                        "name": "Errors",
                        "type": "pass_fail",
                        "direction": "lower_is_better",
                    },
                    {
                        "name": "Stars",
                        "type": "five_star",
                        "direction": "higher_is_better",
                    },
                    {"name": "Note", "type": "pass_fail", "direction": "informational"},
                    {"name": "Odd", "type": "custom", "direction": "higher_is_better"},
                    {"name": "No direction", "type": "pass_fail"},
                ]
            }
        )
        assert set(specs) == {"errors", "stars"}
        # Lower is better: 1.0 is the bad end.
        assert fc.verdict({"errors": 1.0, "stars": 5.0}, specs) == (False, ["errors"])
        assert fc.verdict({"errors": 0.0, "stars": 2.0}, specs) == (False, ["stars"])
        assert fc.verdict({"errors": 0.0, "stars": 3.0}, specs) == (True, [])
        assert fc.verdict({"note": 0.0, "odd": 0.0}, specs) == (None, [])

    def test_an_eval_with_no_score_definition_gives_no_card(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, ROWS)
        kiln.routes[("GET", f"{BASE}/evals/e1")] = {
            "id": "e1",
            "current_config_id": "ec1",
        }
        out = _cards(gateway)
        assert out["cards"] == [] and out["unscored"] == 5


class TestTheReadersNote:
    NOTE = FailureNote(
        eval_id="e1",
        item_id="f1",
        kind="config",
        lost_at="at the final reply, after lookup_order returned the order",
        pattern="requirement-missing-from-the-prompt",
    )

    def test_a_card_is_unread_until_a_reader_writes_a_note(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        out = _cards(gateway)
        assert out["kinds"] == {"unread": 2}
        card = out["cards"][0]
        assert card["failure_kind"] is None and card["lost_at"] is None

    def test_the_note_gives_the_step_and_the_kind(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        append_notes(
            gateway.s.run_dir,
            [self.NOTE],
            run_config_id=RC,
            by="batch_plan",
            source="w1-plan",
        )
        out = _cards(gateway)
        assert out["kinds"] == {"config": 1, "unread": 1}
        card = out["cards"][0]
        assert card["failure_kind"] == "config"
        assert card["lost_at"] == self.NOTE.lost_at
        assert card["failure_pattern"] == "requirement-missing-from-the-prompt"
        assert card["read_by"] == "batch_plan"
        assert card["read_on_run_config_id"] == RC

    def test_a_note_on_the_same_run_config_wins_over_a_newer_one(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        run_dir = gateway.s.run_dir
        append_notes(
            run_dir, [self.NOTE], run_config_id=RC, by="batch_plan", source="w1-plan"
        )
        other = self.NOTE.model_copy(update={"kind": "judge", "lost_at": "elsewhere"})
        append_notes(
            run_dir,
            [other],
            run_config_id=OTHER_RC,
            by="analyze_experiment_results",
            source="w1-1",
        )
        assert _cards(gateway)["cards"][0]["failure_kind"] == "config"

    def test_a_note_from_another_run_config_names_it(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        append_notes(
            gateway.s.run_dir,
            [self.NOTE],
            run_config_id=OTHER_RC,
            by="analyze_experiment_results",
            source="w1-1",
        )
        card = _cards(gateway)["cards"][0]
        assert card["failure_kind"] == "config"
        assert card["read_on_run_config_id"] == OTHER_RC
        assert card["read_by"] == "analyze_experiment_results"

    def test_a_note_on_a_held_out_item_reaches_no_card(self, tmp_path):
        gateway, _ = _gateway(tmp_path, ROWS)
        leak = self.NOTE.model_copy(update={"item_id": "va1", "lost_at": "SECRET step"})
        append_notes(
            gateway.s.run_dir, [leak], run_config_id=RC, by="batch_plan", source="x"
        )
        assert "SECRET" not in json.dumps(_cards(gateway))


class TestTheNotesFile:
    def test_round_trip_and_the_newest_note_of_a_run_config(self, tmp_path):
        note = TestTheReadersNote.NOTE
        assert append_notes(None, [note], run_config_id=RC, by="x", source="y") == 0
        assert load_notes(tmp_path) == []
        append_notes(tmp_path, [note], run_config_id=RC, by="a", source="w1-1")
        later = note.model_copy(update={"kind": "noise"})
        append_notes(tmp_path, [later], run_config_id=RC, by="b", source="w2-1")
        notes = load_notes(tmp_path)
        assert [n.kind for n in notes] == ["config", "noise"]
        assert note_for(notes, "e1", "f1", RC).by == "b"
        assert note_for(notes, "e1", "f2", RC) is None
        assert note_for(notes, "e2", "f1", RC) is None

    def test_a_long_step_is_cut_to_one_line(self, tmp_path):
        note = FailureNote(
            eval_id="e1", item_id="f1", kind="config", lost_at="a\nb " + "c" * 900
        )
        append_notes(tmp_path, [note], run_config_id=RC, by="a", source="s")
        (stored,) = load_notes(tmp_path)
        assert "\n" not in stored.lost_at
        assert len(stored.lost_at) == LOST_AT_MAX_CHARS

    def test_a_line_that_is_not_a_note_is_skipped(self, tmp_path):
        good = {
            "eval_id": "e1",
            "item_id": "f1",
            "run_config_id": RC,
            "kind": "config",
            "lost_at": "x",
        }
        lines = [
            "not json",
            json.dumps([1, 2]),
            json.dumps({**good, "kind": "a kind code does not know"}),
            json.dumps({**good, "lost_at": ""}),
            json.dumps(good),
        ]
        (tmp_path / FILE_NAME).write_text("\n".join(lines) + "\n")
        assert [n.item_id for n in load_notes(tmp_path)] == ["f1"]

    def test_the_kind_is_one_of_the_five_failure_classes(self):
        with pytest.raises(ValueError):
            FailureNote(eval_id="e1", item_id="f1", kind="other", lost_at="x")
