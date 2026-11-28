"""Failure cards: what a planner gets for each failed train item.

A score alone does not say what to change. A card puts beside the score
the parts a reader needs to form a hypothesis:

- the input and the output: code, from the stored train result;
- what was expected: code, from the eval input's `reference`, and the
  judge's reason when the row has one;
- a matching success: code, a train item of the same kind that the same
  run config passes;
- the step that lost it and the kind of failure: a reader
  (`kiln_harness_tuner.failure_notes`).

This module holds the parts that are pure functions of the rows: which rows
fail, which pass, and which passing row matches a failing one. The gateway
(`ops.Gateway.get_failure_cards`) decides what is train, reads the rows and
writes the trace files. It hands this module train rows only.

**Pass and fail.** A score counts when the eval defines it with a direction
that is not `informational`, and its type has a known range
(`analysis.normalize`). An item fails when one such score is under half of
its range, in the good direction. It passes when it has such a score and
none fails. Any other row is unscored: code does not guess.

**The same kind.** A matching success is chosen in this order, and the card
says which rule chose it:

1. the same expected output (the two references are equal);
2. the most shared tags, among the tags that not every scored item has (a
   tag that every item has, such as the tag of the train split, says
   nothing);
3. the closest input, by the share of words the two inputs have in common.

Ties go to the closer input, then to the lower item id. No passing item:
no matching success.

A card is short: each text is its first part, and the trace file has the
rest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from kiln_harness_tuner.analysis import ScoreSpec, UnsupportedScoreTypeError, normalize
from kiln_harness_tuner.schemas import score_json_key

# How much of each text a card shows. Ten cards of these sizes fit in one
# answer under the gateway's cap.
INPUT_CHARS = 600
OUTPUT_CHARS = 600
EXPECTED_CHARS = 500
JUDGE_REASON_CHARS = 400
MATCH_TEXT_CHARS = 250
# A normalized score under this is a failure (`measure.draw_queue` reads a
# parent's failures with the same line).
PASS_LINE = 0.5
UNREAD = "unread"

_WORD = re.compile(r"\w+")


def score_specs(eval_def: Mapping[str, Any]) -> dict[str, ScoreSpec]:
    """The eval's scores that code can read as pass or fail, by their key in
    a result row. A score with no direction, an `informational` score and a
    score type with no known range are left out."""
    specs: dict[str, ScoreSpec] = {}
    for score in eval_def.get("output_scores") or []:
        if not isinstance(score, dict) or not score.get("name"):
            continue
        direction = score.get("direction")
        if direction not in ("higher_is_better", "lower_is_better"):
            continue
        spec = ScoreSpec(
            score_json_key(str(score["name"])), str(score.get("type")), direction
        )
        try:
            normalize(0.0, spec)
        except UnsupportedScoreTypeError:
            continue
        specs[spec.key] = spec
    return specs


def verdict(
    scores: Mapping[str, float | None], specs: Mapping[str, ScoreSpec]
) -> tuple[bool | None, list[str]]:
    """`(passed, failed score keys)` of one row. `passed` is None when the
    row has no score that code can read."""
    failed: list[str] = []
    read = 0
    for key, spec in specs.items():
        value = scores.get(key)
        if value is None:
            continue
        read += 1
        if normalize(float(value), spec) < PASS_LINE:
            failed.append(key)
    if not read:
        return None, []
    return not failed, failed


def fold_rows(
    rows: Iterable[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """One row for each item, in the order of each item's first row.

    Several stored rows for one item are replicates. The rows that were
    not skipped fold into the last of them, with each score the mean over
    the rows that carry it, as the measurement engine reads replicates
    (`analysis._runs_by_item`). An item whose rows were all skipped keeps
    its last row, so it is counted as unscored. The trace file of a card
    is then the trace of the row the card shows."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item, row in rows:
        groups.setdefault(item, []).append(row)
    folded: list[tuple[str, dict[str, Any]]] = []
    for item, group in groups.items():
        kept = [r for r in group if not r.get("skipped_reason")]
        if len(kept) <= 1:
            folded.append((item, kept[0] if kept else group[-1]))
            continue
        keys = {k for r in kept for k in (r.get("scores") or {})}
        scores: dict[str, Any] = {}
        for key in keys:
            values = [
                float(v)
                for r in kept
                if isinstance(v := (r.get("scores") or {}).get(key), (int, float))
            ]
            scores[key] = sum(values) / len(values) if values else None
        folded.append((item, {**kept[-1], "scores": scores}))
    return folded


def clip(text: str | None, limit: int) -> tuple[str | None, bool]:
    """`text` cut to `limit` characters, and whether it was cut."""
    if text is None:
        return None, False
    return (text, False) if len(text) <= limit else (text[:limit], True)


def as_text(value: Any) -> str | None:
    """A stored value as the text a card shows. A dict becomes `key: value`
    lines (a judge's intermediate outputs); any other value that is not a
    string becomes its JSON text. Nothing to show is None."""
    if value is None or value == "" or value == {}:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(
            f"{key}: {val if isinstance(val, str) else json.dumps(val, default=str)}"
            for key, val in value.items()
            if val not in (None, "")
        )
    return json.dumps(value, ensure_ascii=False, default=str)


def json_text(value: Any) -> str | None:
    """A reference as the text a card shows: a string as it is, any other
    value as JSON text, and no reference as None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return json.dumps(value, ensure_ascii=False, default=str)


def _words(text: str | None) -> frozenset[str]:
    return frozenset(_WORD.findall((text or "").lower()))


def word_overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """The share of words two inputs have in common (Jaccard), 0 to 1."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class Scored:
    """One scored train row, as the matching reads it."""

    item_id: str
    passed: bool
    input: str | None = None
    # The reference as canonical JSON text, or None when there is none.
    reference: str | None = None
    tags: frozenset[str] = frozenset()
    words: frozenset[str] = field(default=frozenset(), compare=False)


def scored_row(
    item_id: str,
    passed: bool,
    input_text: str | None,
    doc: Mapping[str, Any] | None,
) -> Scored:
    reference = (doc or {}).get("reference")
    return Scored(
        item_id=item_id,
        passed=passed,
        input=input_text,
        reference=None
        if reference is None
        else json.dumps(reference, sort_keys=True, ensure_ascii=False, default=str),
        tags=frozenset(str(t) for t in (doc or {}).get("tags") or []),
        words=_words(input_text),
    )


def common_tags(rows: Iterable[Scored]) -> frozenset[str]:
    """The tags that every one of `rows` has: they tell no two rows apart."""
    sets = [row.tags for row in rows]
    return frozenset.intersection(*sets) if sets else frozenset()


def matching_success(
    failure: Scored, passes: Iterable[Scored], ignored_tags: frozenset[str]
) -> tuple[Scored, str] | None:
    """The passing row of the same kind as `failure`, and the rule that
    chose it (see the module doc). None when nothing passes."""
    best: tuple[tuple[int, int, float], Scored, str] | None = None
    mine = failure.tags - ignored_tags
    for row in passes:
        if row.item_id == failure.item_id:
            continue
        overlap = word_overlap(failure.words, row.words)
        shared = sorted(mine & (row.tags - ignored_tags))
        if failure.reference is not None and row.reference == failure.reference:
            rank, why = (2, 0, overlap), "same expected output"
        elif shared:
            rank = (1, len(shared), overlap)
            why = f"shares tags: {', '.join(shared[:6])}"
        else:
            rank = (0, 0, overlap)
            why = f"closest input (word overlap {overlap:.2f})"
        if (
            best is None
            or rank > best[0]
            or (rank == best[0] and row.item_id < best[1].item_id)
        ):
            best = (rank, row, why)
    return None if best is None else (best[1], best[2])
