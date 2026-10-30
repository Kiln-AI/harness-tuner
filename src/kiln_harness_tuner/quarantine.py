"""The start-up split check: which items are held out, and which stored
results sit on them.

A target project can arrive with stored results on val and test items — eval
runs a person ran by hand, and the task runs those eval runs scored. This
module does not move them: held-out results stay in the project, because a
project in git would otherwise show every moved file as deleted. Two fences
keep them from agents: the
loop gateway serves agents train items only (`train_set.json`, written from
this module's split membership), and the read fence (`cli.read_fence_for`)
denies them the project's files.

What this module decides, from the project on disk:

- the split membership of every item, and so the held-out items: the ids the
  analyzer's train-read check refuses, and the train set the gateway serves;
- the stored results on held-out items (`survey_heldout`), counted for the
  start-up log line and never moved:
  - **eval runs** whose item is not in their own eval's train split and is
    in that eval's val or test split, or is held out overall;
  - **task runs** that such an eval run scored (unless an eval run on a train
    item also scored it), task runs driven from a held-out eval input
    (`eval_source`), and task runs with no source whose input text equals a
    held-out input and no train input.

An item in a train split of one eval and a val or test split of another is an
OVERLAP: it counts as train (never hide train), and is counted in the report so
the operator can fix the split design.

A held-out item = in some eval's val or test split and in no eval's train
split — or carrying a split-like tag (`val`, `test`, `val_*`,
`test_*`, any case) and in no eval's train split, even when no current eval
uses that tag: a deleted eval's test items are still test items.

`QuarantineError` refuses the start with a fix line when the split
membership cannot be decided (a filter this module does not evaluate, an
unreadable eval).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ItemKey = tuple[str, str]  # (source, id): ("eval_input", id) | ("task_run", id)
SPLIT_NAMES = ("train", "val", "test")
# A tag that names a held-out split, used by an eval or not.
# A tag whose NAME says held out: val, test, val_*, test_*,
# holdout*, heldout* — any case — whether a current eval uses it or not.
_HELDOUT_TAG_RE = re.compile(r"^(?:(?:val|test)(?:_|$)|holdout|heldout)", re.IGNORECASE)


def looks_held_out(tag: Any) -> bool:
    return bool(_HELDOUT_TAG_RE.match(str(tag)))


def heldout_tag(tags: Iterable[Any], heldout: Iterable[str] = ()) -> str | None:
    """The first held-out tag among `tags` — one in `heldout`, or one whose
    name says held out — or None."""
    known = set(heldout)
    return next((str(t) for t in tags if str(t) in known or looks_held_out(t)), None)


class QuarantineError(RuntimeError):
    """The held-out results cannot be placed or moved. A start-up error."""


@dataclass
class SplitMembership:
    """Per eval, the item keys of each split; plus the task-run dataset
    items (those are inputs, never moved)."""

    by_eval: dict[str, dict[str, set[ItemKey]]] = field(default_factory=dict)
    dataset_task_runs: set[str] = field(default_factory=set)
    # Items with a held-out tag (`heldout_tags`), used by a split or not.
    tagged_heldout: set[ItemKey] = field(default_factory=set)
    # Every held-out tag — the tag of every non-train split of
    # every eval (any split name), and every tag on an item whose name says
    # held out — minus the tags a train split uses.
    heldout_tags: set[str] = field(default_factory=set)
    # the held-out tags no current eval's split defines -> items carrying it
    orphan_tags: dict[str, int] = field(default_factory=dict)

    @property
    def train_any(self) -> set[ItemKey]:
        return {k for splits in self.by_eval.values() for k in splits["train"]}

    @property
    def heldout_named(self) -> set[ItemKey]:
        return {
            k
            for splits in self.by_eval.values()
            for name in ("val", "test")
            for k in splits[name]
        }

    @property
    def heldout(self) -> set[ItemKey]:
        """In a val or test split somewhere, or tagged like one, and in no
        train split anywhere."""
        return (self.heldout_named | self.tagged_heldout) - self.train_any

    @property
    def orphans(self) -> set[ItemKey]:
        """Held out by their tag alone: no current eval's val or test split
        holds them."""
        return self.tagged_heldout - self.heldout_named - self.train_any

    @property
    def overlap(self) -> set[ItemKey]:
        """Train for one eval, val or test for another: kept, and reported."""
        return self.heldout_named & self.train_any


@dataclass
class QuarantinePlan:
    eval_runs: list[tuple[Path, str]] = field(default_factory=list)
    task_runs: list[tuple[Path, str]] = field(default_factory=list)
    heldout_tags: set[str] = field(default_factory=set)
    heldout_items: set[ItemKey] = field(default_factory=set)
    overlap_items: set[ItemKey] = field(default_factory=set)
    orphan_items: set[ItemKey] = field(default_factory=set)
    orphan_tags: dict[str, int] = field(default_factory=dict)
    kept_shared_task_runs: int = 0

    @property
    def moves(self) -> list[tuple[Path, str]]:
        return [*self.eval_runs, *self.task_runs]


@dataclass(frozen=True)
class QuarantineReport:
    # Stored results on held-out items. They stay in the project.
    stored_eval_runs: int
    stored_task_runs: int
    heldout_items: int
    overlap_items: int
    kept_shared_task_runs: int
    heldout_tags: frozenset[str]
    orphan_items: int = 0
    orphan_tags: dict[str, int] = field(default_factory=dict)


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise QuarantineError(
            f"cannot read {path} ({exc}) — the start-up quarantine must read every"
            " eval, eval input and run to decide which results are held out.\n"
            "fix: repair or remove that file (the Kiln studio server cannot"
            " load it either), then start the run again."
        ) from exc
    if not isinstance(data, dict):
        raise QuarantineError(
            f"{path} is not a JSON object.\n"
            "fix: repair or remove that file, then start the run again."
        )
    return data


def task_dirs(project_root: Path, task_id: str) -> list[Path]:
    return sorted(
        p for p in project_root.glob(f"tasks/{task_id} - *") if p.is_dir()
    ) or sorted(p for p in project_root.glob(f"tasks/{task_id}") if p.is_dir())


def _children(folder: Path, filename: str) -> Iterable[tuple[Path, dict[str, Any]]]:
    if not folder.is_dir():
        return
    for child in sorted(folder.iterdir()):
        path = child / filename
        if child.is_dir() and path.is_file():
            yield child, _read(path)


def _split_refs(eval_def: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """split name -> (source, filter id), from the current `splits` field or
    the flat legacy fields an older project carries."""
    splits = eval_def.get("splits")
    refs: dict[str, tuple[str, str]] = {}
    if isinstance(splits, dict) and splits:
        for name, ref in splits.items():
            if isinstance(ref, dict) and ref.get("filter_id"):
                refs[str(name)] = (
                    str(ref.get("source") or "task_run"),
                    str(ref["filter_id"]),
                )
        return refs
    if eval_def.get("eval_input_filter_id"):
        refs["test"] = ("eval_input", str(eval_def["eval_input_filter_id"]))
    elif eval_def.get("eval_set_filter_id"):
        refs["test"] = ("task_run", str(eval_def["eval_set_filter_id"]))
    if eval_def.get("train_set_filter_id"):
        refs["train"] = ("task_run", str(eval_def["train_set_filter_id"]))
    if eval_def.get("val_set_filter_id"):
        refs["val"] = ("task_run", str(eval_def["val_set_filter_id"]))
    return refs


def _matches(filter_id: str, tags: Iterable[str], where: str) -> bool:
    if filter_id == "all":
        return True
    if filter_id.startswith("tag::") and len(filter_id) > 5:
        return filter_id.removeprefix("tag::") in set(tags)
    raise QuarantineError(
        f"{where} uses the split filter {filter_id!r}, which the start-up"
        " quarantine does not evaluate (it evaluates 'all' and 'tag::<name>'),"
        " so it cannot tell which stored results are on held-out items.\n"
        "fix: give that split a 'tag::<name>' filter, or run without it — the"
        " loop does not start while a held-out result could be misplaced."
    )


def membership(task_dir: Path) -> tuple[SplitMembership, dict[str, Any]]:
    """Split membership for every eval of the task, from disk. Returns the
    membership and the loaded records the plan needs."""
    eval_inputs = {
        str(d.get("id")): d
        for _p, d in _children(task_dir / "eval_inputs", "eval_input.kiln")
    }
    task_runs = {
        str(d.get("id")): (p, d)
        for p, d in _children(task_dir / "runs", "task_run.kiln")
    }
    evals = list(_children(task_dir / "evals", "eval.kiln"))
    result = SplitMembership()
    split_tags: set[str] = set()
    train_tags: set[str] = set()
    for eval_dir, eval_def in evals:
        eval_id = str(eval_def.get("id"))
        splits: dict[str, set[ItemKey]] = {name: set() for name in SPLIT_NAMES}
        refs = _split_refs(eval_def)
        for name, (source, filter_id) in refs.items():
            if filter_id.startswith("tag::"):
                (train_tags if name == "train" else split_tags).add(
                    filter_id.removeprefix("tag::")
                )
            if name not in splits:
                continue  # a split name the loop does not use: its tag is held out
            where = f"eval {eval_id} ({eval_dir.name}) split {name}"
            if source == "eval_input":
                for item_id, item in eval_inputs.items():
                    if _matches(filter_id, item.get("tags") or [], where):
                        splits[name].add(("eval_input", item_id))
            else:
                for run_id, (_p, run) in task_runs.items():
                    if _matches(filter_id, run.get("tags") or [], where):
                        splits[name].add(("task_run", run_id))
                        result.dataset_task_runs.add(run_id)
        golden = eval_def.get("eval_configs_filter_id")
        if golden:
            where = f"eval {eval_id} ({eval_dir.name}) golden set"
            for run_id, (_p, run) in task_runs.items():
                if _matches(str(golden), run.get("tags") or [], where):
                    result.dataset_task_runs.add(run_id)
        result.by_eval[eval_id] = splits
    item_tags = {
        str(t)
        for pool in (eval_inputs.values(), (r for _p, r in task_runs.values()))
        for d in pool
        for t in d.get("tags") or []
    }
    named = {t for t in item_tags if looks_held_out(t)}
    result.heldout_tags = (split_tags | named) - train_tags
    held_tags = result.heldout_tags
    result.tagged_heldout = {
        ("eval_input", item_id)
        for item_id, item in eval_inputs.items()
        if held_tags & {str(t) for t in item.get("tags") or []}
    } | {
        ("task_run", run_id)
        for run_id, (_p, run) in task_runs.items()
        if held_tags & {str(t) for t in run.get("tags") or []}
    }
    for tag in sorted(held_tags - split_tags):
        result.orphan_tags[tag] = sum(
            1
            for pool in (eval_inputs.values(), (r for _p, r in task_runs.values()))
            for d in pool
            if tag in {str(t) for t in d.get("tags") or []}
        )
    loaded = {"eval_inputs": eval_inputs, "task_runs": task_runs, "evals": evals}
    return result, loaded


def _input_text(eval_input: dict[str, Any]) -> str | None:
    data = eval_input.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if isinstance(data, dict):
        message = data.get("user_message")
        if isinstance(message, dict) and isinstance(message.get("text"), str):
            return message["text"]
    return None


def _eval_run_key(run: dict[str, Any]) -> ItemKey | None:
    if run.get("eval_input_id"):
        return ("eval_input", str(run["eval_input_id"]))
    if run.get("dataset_id"):
        return ("task_run", str(run["dataset_id"]))
    return None


def plan_quarantine(task_dir: Path) -> QuarantinePlan:
    """Decide what moves. Pure over the disk: nothing is written."""
    member, loaded = membership(task_dir)
    eval_inputs: dict[str, dict[str, Any]] = loaded["eval_inputs"]
    task_runs: dict[str, tuple[Path, dict[str, Any]]] = loaded["task_runs"]
    heldout = member.heldout
    train_any = member.train_any
    plan = QuarantinePlan(
        heldout_items=set(heldout),
        overlap_items=member.overlap,
        orphan_items=member.orphans,
        orphan_tags=dict(member.orphan_tags),
    )

    for key in heldout:
        source, item_id = key
        record = (
            eval_inputs.get(item_id)
            if source == "eval_input"
            else task_runs.get(item_id, (None, {}))[1]
        )
        plan.heldout_tags.update(str(t) for t in (record or {}).get("tags") or [])

    scored_by_moved: set[str] = set()
    scored_by_kept: set[str] = set()
    for eval_dir, eval_def in loaded["evals"]:
        eval_id = str(eval_def.get("id"))
        splits = member.by_eval.get(eval_id, {n: set() for n in SPLIT_NAMES})
        own_heldout = splits["val"] | splits["test"]
        for config_dir, _config in _children(eval_dir / "configs", "eval_config.kiln"):
            for run_dir, run in _children(config_dir / "runs", "eval_run.kiln"):
                key = _eval_run_key(run)
                scored = str(run.get("scored_run_id") or "")
                move = (
                    key is not None
                    and key not in splits["train"]
                    and (key in own_heldout or key in heldout)
                )
                if move and key is not None:
                    why = f"eval {eval_id}: item {key[1]} is not in its train split"
                    plan.eval_runs.append((run_dir, why))
                    if scored:
                        scored_by_moved.add(scored)
                elif scored:
                    scored_by_kept.add(scored)

    heldout_inputs = {i for s, i in heldout if s == "eval_input"}
    train_inputs = {i for s, i in train_any if s == "eval_input"}
    heldout_texts = {
        t for i in heldout_inputs if (t := _input_text(eval_inputs.get(i, {})))
    }
    train_texts = {
        t for i in train_inputs if (t := _input_text(eval_inputs.get(i, {})))
    }

    reasons: dict[str, str] = {}
    for run_id, (_path, run) in task_runs.items():
        if ("task_run", run_id) in plan.orphan_items:
            tag = heldout_tag(run.get("tags") or [], member.heldout_tags)
            reasons[run_id] = f"tagged {tag!r}, " + (
                "a held-out tag no eval split uses"
                if tag in member.orphan_tags
                else "the tag of a non-train split"
            )
            continue
        if run_id in member.dataset_task_runs:
            continue  # a dataset item is an input, never a result to move
        if run_id in scored_by_moved:
            if run_id in scored_by_kept:
                plan.kept_shared_task_runs += 1
                continue
            reasons[run_id] = "scored by a moved eval run"
            continue
        source = run.get("eval_source")
        if isinstance(source, dict) and source.get("source_type") == "eval_input":
            if str(source.get("source_id")) in heldout_inputs:
                reasons[run_id] = "driven from a held-out eval input"
            continue
        text = run.get("input")
        if isinstance(text, str) and text in heldout_texts and text not in train_texts:
            reasons[run_id] = "its input is a held-out item's input"

    # The whole parent/child chain of a moved run goes with it: a turn left
    # behind still carries the held-out conversation.
    parent_of = {
        rid: str(run.get("parent_task_run_id"))
        for rid, (_p, run) in task_runs.items()
        if run.get("parent_task_run_id")
    }
    children: dict[str, set[str]] = {}
    for rid, parent in parent_of.items():
        children.setdefault(parent, set()).add(rid)
    frontier = list(reasons)
    while frontier:
        rid = frontier.pop()
        for other in [parent_of.get(rid), *children.get(rid, ())]:
            if (
                other
                and other in task_runs
                and other not in reasons
                and other not in member.dataset_task_runs
                and other not in scored_by_kept
            ):
                reasons[other] = f"same conversation as {rid}"
                frontier.append(other)
    plan.task_runs = [(task_runs[rid][0], why) for rid, why in sorted(reasons.items())]
    return plan


def survey_heldout(project_root: Path, task_id: str) -> QuarantineReport:
    """Count the stored results on held-out items, and move nothing:
    held-out results stay in the project. The
    loop gateway serves agents train items only, and the read fence denies
    them the project's files, so the results on val and test stay where they
    are. Refuses (QuarantineError) when the split membership cannot be
    decided."""
    dirs = task_dirs(project_root, task_id)
    if not dirs:
        raise QuarantineError(
            f"no task directory for task {task_id} under {project_root}/tasks —"
            " the start-up check cannot read the task's splits.\n"
            "fix: check --project-path and --task-id."
        )
    plans = [plan_quarantine(d) for d in dirs]
    orphan_tags: dict[str, int] = {}
    for p in plans:
        for tag, n in p.orphan_tags.items():
            orphan_tags[tag] = orphan_tags.get(tag, 0) + n
    return QuarantineReport(
        stored_eval_runs=sum(len(p.eval_runs) for p in plans),
        stored_task_runs=sum(len(p.task_runs) for p in plans),
        heldout_items=sum(len(p.heldout_items) for p in plans),
        overlap_items=sum(len(p.overlap_items) for p in plans),
        kept_shared_task_runs=sum(p.kept_shared_task_runs for p in plans),
        heldout_tags=frozenset(t for p in plans for t in p.heldout_tags),
        orphan_items=sum(len(p.orphan_items) for p in plans),
        orphan_tags=orphan_tags,
    )


def heldout_item_ids(project_root: Path, task_id: str) -> frozenset[str]:
    """The ids of every held-out item (eval input or task run), for the
    orchestrator's own check that no train read ever returns one."""
    ids: set[str] = set()
    for task_dir in task_dirs(project_root, task_id):
        member, _loaded = membership(task_dir)
        ids.update(item_id for _source, item_id in member.heldout)
    return frozenset(ids)


def train_eval_of(project_root: Path, task_id: str, item_id: str) -> str | None:
    """The id of an eval whose TRAIN split holds this eval input, or None."""
    for task_dir in task_dirs(project_root, task_id):
        member, _loaded = membership(task_dir)
        for eval_id, splits in member.by_eval.items():
            if ("eval_input", item_id) in splits["train"]:
                return eval_id
    return None


def eval_ids(project_root: Path, task_id: str) -> frozenset[str]:
    """Every eval of the task on disk."""
    ids: set[str] = set()
    for task_dir in task_dirs(project_root, task_id):
        ids.update(
            str(d.get("id")) for _p, d in _children(task_dir / "evals", "eval.kiln")
        )
    return frozenset(ids)


def new_eval_split_problem(
    project_root: Path, task_id: str, eval_id: str, heldout: frozenset[str]
) -> str | None:
    """Why an eval that appeared after start-up may not be measured, or
    None. An eval created through a spec derives its splits on
    the server, where the hook's check of a request body cannot see them.
    So code reads the new eval's splits from disk: its train split must be
    one the quarantine can evaluate, and must hold no item the start-up
    pass found held out."""
    try:
        for task_dir in task_dirs(project_root, task_id):
            member, _loaded = membership(task_dir)
            splits = member.by_eval.get(eval_id)
            if splits is None:
                continue
            leaked = sorted(i for _s, i in splits["train"] if i in heldout)
            if leaked:
                return (
                    f"its train split holds {len(leaked)} held-out item(s)"
                    f" ({', '.join(leaked[:3])})"
                )
            return None
    except QuarantineError as exc:
        return str(exc).split("\n")[0]
    return "it is not on disk, so its splits cannot be checked"
