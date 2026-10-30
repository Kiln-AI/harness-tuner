"""The moderation example: `posts.jsonl` and `scorer.py` are what the
committed Kiln project holds, the splits are balanced, and the scorer scores
the way its docstring says."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "moderation"
TASK = EXAMPLE / "Forum Moderation" / "tasks" / "424242783061 - Forum Moderation"
SPLITS = {"train", "val", "test"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"moderation_{name}", EXAMPLE / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scorer = _load("scorer")


def _posts() -> list[dict]:
    with open(EXAMPLE / "posts.jsonl", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_every_post_is_an_eval_input_of_the_project():
    # Nothing builds the project from `posts.jsonl` any more, so this is what
    # keeps the two the same.
    posts = _posts()
    on_disk = {path.parent.name for path in TASK.glob("eval_inputs/*/eval_input.kiln")}
    assert {post["id"] for post in posts} == on_disk
    for post in posts:
        path = TASK / "eval_inputs" / post["id"] / "eval_input.kiln"
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["data"]["user_message"]["text"] == post["text"], post["id"]
        assert record["tags"] == [post["split"]], post["id"]
        assert record["reference"] == {
            "expected_action": post["action"],
            "expected_rule": post["rule"],
        }, post["id"]


def test_the_scorer_is_the_projects_eval():
    (copy,) = TASK.glob("evals/*/configs/*/scorer.py")
    assert copy.read_bytes() == (EXAMPLE / "scorer.py").read_bytes()


def test_every_group_is_in_every_split():
    posts = _posts()
    for group in {post["group"] for post in posts}:
        splits = {p["split"] for p in posts if p["group"] == group}
        assert splits == SPLITS, group


def test_every_group_has_one_label():
    posts = _posts()
    labels = {(p["group"], p["action"], p["rule"]) for p in posts}
    assert len(labels) == len({p["group"] for p in posts})


def test_no_post_is_in_two_splits():
    texts = [" ".join(p["text"].lower().split()) for p in _posts()]
    assert len(texts) == len(set(texts))


def _score(answer, action, rule):
    output = answer if isinstance(answer, str) else json.dumps(answer)
    return scorer.score(
        output=output,
        reference_data={"expected_action": action, "expected_rule": rule},
    )


def test_scorer_right_decision_passes_both():
    assert _score({"action": "remove", "rule": "spam"}, "remove", "spam") == {
        "decision_correct": 1.0,
        "no_wrongful_removal": 1.0,
    }
    assert _score({"action": "keep", "rule": "none"}, "keep", "none") == {
        "decision_correct": 1.0,
        "no_wrongful_removal": 1.0,
    }


def test_scorer_wrong_rule_fails_the_decision_only():
    assert _score({"action": "remove", "rule": "off_topic"}, "remove", "spam") == {
        "decision_correct": 0.0,
        "no_wrongful_removal": 1.0,
    }


def test_scorer_removing_a_good_post_fails_both():
    assert _score({"action": "remove", "rule": "spam"}, "keep", "none") == {
        "decision_correct": 0.0,
        "no_wrongful_removal": 0.0,
    }


def test_scorer_unparseable_answer_fails_the_decision_only():
    assert _score("not json", "remove", "spam") == {
        "decision_correct": 0.0,
        "no_wrongful_removal": 1.0,
    }
