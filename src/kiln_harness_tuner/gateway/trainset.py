"""The TRAIN item set — the only items a loop agent may ever see.

The gateway answers every read with records whose item is PROVABLY train,
and drops everything else. This module decides "provably train", once, at
start-up, from the evals' own `splits` definitions on disk:

1. Every eval's every split is evaluated. A split filter this module cannot
   evaluate — anything but `tag::<name>` — refuses the start
   (`TrainSetError`), and so does a TRAIN split whose filter is `all`: it
   selects every item, val and test included, so it cannot be the boundary.
2. A candidate train item is one in some eval's train split.
3. It is dropped when ANY of these holds:
   - it is in any eval's non-train split (whatever the split is called), so
     an item that is train for one eval and val for another is out;
   - it carries a held-out tag — the quarantine's shared set
     (`SplitMembership.heldout_tags`): the tag of any non-train split, or a
     tag named `val`, `test`, `val_*`, `test_*`, `holdout*` or `heldout*`
     (any case) whether a current eval uses it or not, minus the tags a
     train split uses.

The result is two sets (eval inputs and dataset task runs) and, per eval,
the train items of that eval. Records the run creates later on train items
(its drives, its eval runs) are admitted by the gateway through their link
to a train item, never by this set.

It is written to `runs/<run_id>/train_set.json` before the first dispatch;
the gateway reads that file and nothing else to decide what is train.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kiln_harness_tuner.quarantine import (
    _children,
    _split_refs,
    looks_held_out,
    membership,
    task_dirs,
)

TRAIN = "train"
FILE_NAME = "train_set.json"


class TrainSetError(RuntimeError):
    """The train set cannot be decided safely. A start-up refusal."""


def held_out_by_name(tag: Any) -> bool:
    """A tag whose NAME says held out — the quarantine's own rule."""
    return looks_held_out(tag)


@dataclass(frozen=True)
class TrainSet:
    eval_inputs: frozenset[str] = frozenset()
    task_runs: frozenset[str] = frozenset()  # dataset items of V1 evals
    per_eval: dict[str, frozenset[str]] = field(default_factory=dict)
    heldout_tags: frozenset[str] = frozenset()

    @property
    def ids(self) -> frozenset[str]:
        return self.eval_inputs | self.task_runs

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "eval_inputs": sorted(self.eval_inputs),
            "task_runs": sorted(self.task_runs),
            "per_eval": {e: sorted(v) for e, v in sorted(self.per_eval.items())},
            "heldout_tags": sorted(self.heldout_tags),
        }

    @staticmethod
    def from_json(data: Any) -> TrainSet:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise TrainSetError(
                "not a train set file (version 1)."
                "\nfix: start the run again — the loop writes train_set.json at"
                " start-up."
            )

        def strings(value: Any) -> frozenset[str]:
            if not isinstance(value, list):
                raise TrainSetError(
                    "a train set field is not a list."
                    "\nfix: start the run again — the loop writes train_set.json"
                    " at start-up."
                )
            return frozenset(str(v) for v in value)

        per_eval = data.get("per_eval")
        if not isinstance(per_eval, dict):
            raise TrainSetError(
                "per_eval is not an object."
                "\nfix: start the run again — the loop writes train_set.json at"
                " start-up."
            )
        return TrainSet(
            eval_inputs=strings(data.get("eval_inputs")),
            task_runs=strings(data.get("task_runs")),
            per_eval={str(e): strings(v) for e, v in per_eval.items()},
            heldout_tags=strings(data.get("heldout_tags", [])),
        )

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), indent=1) + "\n")

    @staticmethod
    def load(path: Path) -> TrainSet:
        try:
            return TrainSet.from_json(json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            raise TrainSetError(
                f"cannot read {path} ({exc})."
                "\nfix: start the run again — the loop writes train_set.json at"
                " start-up."
            ) from exc


def _tags(record: dict[str, Any]) -> set[str]:
    return {str(t) for t in record.get("tags") or []}


def _select(filter_id: str, pool: dict[str, dict[str, Any]], where: str) -> set[str]:
    if filter_id.startswith("tag::") and len(filter_id) > 5:
        tag = filter_id.removeprefix("tag::")
        return {i for i, d in pool.items() if tag in _tags(d)}
    if filter_id == "all":
        return set(pool)
    raise TrainSetError(
        f"{where} uses the split filter {filter_id!r}, which the train-set"
        " computation does not evaluate (only 'tag::<name>').\n"
        "fix: give that split a 'tag::<name>' filter; the loop does not start"
        " while the train boundary is unclear."
    )


def _compute_task(task_dir: Path) -> TrainSet:
    inputs = {
        str(d.get("id")): d
        for _p, d in _children(task_dir / "eval_inputs", "eval_input.kiln")
    }
    runs = {
        str(d.get("id")): d for _p, d in _children(task_dir / "runs", "task_run.kiln")
    }
    split_tags: set[str] = set()
    train_tags: set[str] = set()
    train: dict[str, set[tuple[str, str]]] = {}
    held: set[tuple[str, str]] = set()
    for eval_dir, eval_def in _children(task_dir / "evals", "eval.kiln"):
        eval_id = str(eval_def.get("id"))
        for name, (source, filter_id) in _split_refs(eval_def).items():
            where = f"eval {eval_id} ({eval_dir.name}) split {name!r}"
            if name == TRAIN and filter_id == "all":
                raise TrainSetError(
                    f"{where} selects 'all' items: every val and test item would"
                    " be train.\nfix: give the train split a 'tag::<name>' filter."
                )
            pool = inputs if source == "eval_input" else runs
            kind = "eval_input" if source == "eval_input" else "task_run"
            members = {(kind, i) for i in _select(filter_id, pool, where)}
            if filter_id.startswith("tag::"):
                (train_tags if name == TRAIN else split_tags).add(
                    filter_id.removeprefix("tag::")
                )
            if name == TRAIN:
                train.setdefault(eval_id, set()).update(members)
            else:
                held |= members
    # The exclusion tags are the quarantine's one shared held-out tag set
    # (non-train split tags whatever the split's name, and every tag whose
    # name says held out, minus the tags a train split uses) — so the two
    # never disagree about what is held out.
    # (A filter the quarantine cannot evaluate raises QuarantineError, with
    # its own fix line — a start-up refusal like ours.)
    heldout_tags = set(membership(task_dir)[0].heldout_tags)
    heldout_tags |= split_tags - train_tags  # the same definition, twice

    def excluded(key: tuple[str, str]) -> bool:
        kind, item = key
        record = (inputs if kind == "eval_input" else runs).get(item, {})
        return key in held or bool(_tags(record) & heldout_tags)

    per_eval = {
        e: frozenset(i for k, i in members if not excluded((k, i)))
        for e, members in train.items()
    }
    kept = {k for members in train.values() for k in members if not excluded(k)}
    return TrainSet(
        eval_inputs=frozenset(i for k, i in kept if k == "eval_input"),
        task_runs=frozenset(i for k, i in kept if k == "task_run"),
        per_eval=per_eval,
        heldout_tags=frozenset(heldout_tags),
    )


def compute(project_root: Path, task_id: str) -> TrainSet:
    """The train set of the task's project on disk (see the module doc)."""
    dirs = task_dirs(project_root, task_id)
    if not dirs:
        raise TrainSetError(
            f"no task directory for task {task_id} under {project_root}/tasks —"
            " the train set cannot be computed.\nfix: check --project-path and"
            " --task-id."
        )
    parts = [_compute_task(d) for d in dirs]
    return merge(parts)


def merge(parts: Iterable[TrainSet]) -> TrainSet:
    inputs: set[str] = set()
    runs: set[str] = set()
    per_eval: dict[str, set[str]] = {}
    tags: set[str] = set()
    for p in parts:
        inputs |= p.eval_inputs
        runs |= p.task_runs
        tags |= p.heldout_tags
        for e, v in p.per_eval.items():
            per_eval.setdefault(e, set()).update(v)
    return TrainSet(
        frozenset(inputs),
        frozenset(runs),
        {e: frozenset(v) for e, v in per_eval.items()},
        frozenset(tags),
    )
