"""The thin-evidence rule: no win and no loss on one or two items.

A comparison of two run configs pairs their results item by item. Only the
items whose result DIFFERS between the two sides say which side is better.
When fewer than `MIN_DIFFERING_ITEMS` items differ on a row, that row shows
no win and no loss. A verdict that would rest on such rows is **unverified**,
and code writes the reason.

**What the minimum rests on.** One run config, measured twice on the same
items, does not give the same results twice. The default flip rate of the
stop rules is 0.10 (`stop_rules.GUARD_NOISE_DEFAULT`): about one item in
ten changes between two runs of the same config. On an eval of 16 to 24
items that is one or two items (the allowance of
`analysis.row_loss_tolerance` is 2 items at those sizes for the same
reason). So a difference of one or two items is what no change at all looks
like, and three is the smallest count that is not that. The minimum is a
floor, not a test: three items that differ are not proof of a difference.
Above the floor, the rules that were there before decide (the train rule,
the paired tests, the val gate).

**Where code applies it.**

- The train rule (`analysis.success_by_rule`): a gain on the primary target
  counts only on a row that is not thin. A row "down beyond doubt" counts
  only when it is not thin. One exception stays, on train only: an arm that
  takes all the room of a row (it fixes every item that the parent fails
  and breaks none) keeps its ticket to the val gate, as the analysis skill
  says for an eval at its ceiling. The gate has no such exception.
- The train rule under speed and cost (`analysis.success_by_speed_rule`),
  and the gate's eligibility under them
  (`orchestrator.apply_speed_eligibility`): a candidate that is clearly
  faster or cheaper, and whose quality refusal rests on fewer than the
  minimum over every compared row, is unverified, not worse. It is still
  not promoted.
- The val gate (`orchestrator.decide_single_candidate`,
  `orchestrator.val_rejection_reason`, and the candidates handed to
  `promote_best`): a candidate whose targeted rows are thin is not promoted
  and not called a loss. Its rejection line gives the reason "unverified".

**A null result is different.** "No gain" is not a claim that one side is
better, so it does not need items that differ. It needs chances: a row
supports "no gain" when at least the minimum of its items had room to
improve and none of its scores improved. A row with fewer chances than the
minimum cannot show a gain, so it cannot show the absence of one either.

A row whose counts are not known (a row stored before the count fields
existed, with no flip counts) is never thin: the rule does not guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from kiln_harness_tuner.schemas import PerEvalResult

# The fewest paired items that must differ between the two sides before a
# row can show a win or a loss (module doc: what it rests on).
MIN_DIFFERING_ITEMS = 3

# The word on every line that code writes for this rule.
UNVERIFIED = "unverified"
_ROOM_EPS = 1e-9


def differing_items(row: PerEvalResult) -> int | None:
    """The paired items whose result differs between the two sides: the
    flips of a pass/fail score, the non-zero differences of a rating. None
    when the row does not say."""
    if row.n_differing is not None:
        return row.n_differing
    if row.fail_to_pass is not None and row.pass_to_fail is not None:
        return row.fail_to_pass + row.pass_to_fail
    return None


def room_items(row: PerEvalResult) -> int | None:
    """The paired items on which the baseline side is not at the best
    value: the chances to improve. None when the row does not say."""
    if row.n_room is not None:
        return row.n_room
    if row.headroom is not None:
        return round(row.headroom * row.n_items)
    return None


def is_thin(row: PerEvalResult) -> bool:
    """Fewer than the minimum of this row's items differ. False when the
    count is not known."""
    count = differing_items(row)
    return count is not None and count < MIN_DIFFERING_ITEMS


def took_all_room(row: PerEvalResult) -> bool:
    """The candidate side fixed every item with room and broke none: its
    gain is the row's whole headroom. Pass/fail rows only (a rating row
    states no headroom)."""
    return (
        row.headroom is not None
        and row.headroom > 0
        and row.delta >= row.headroom - _ROOM_EPS
    )


class TargetEvidence(StrEnum):
    """What the rows of a target show."""

    GAIN = "gain"  # a row improved, and enough of its items differ
    NO_GAIN = "no gain"  # no row improved, and a row had the chances to
    UNVERIFIED = UNVERIFIED  # too few items differ, or too few had room


def _supports_no_gain(row: PerEvalResult) -> bool:
    """The row could have shown a gain: enough of its items differ, or
    enough had room. An unknown count keeps the answer the rule gave
    before."""
    if not is_thin(row):
        return True
    room = room_items(row)
    return room is None or room >= MIN_DIFFERING_ITEMS


def target_evidence(
    rows: Sequence[PerEvalResult], *, full_room_counts: bool = False
) -> TargetEvidence:
    """What the rows of one target show (module doc). `full_room_counts`:
    a thin row that took all its room counts as a gain (the train rule's
    exception for an eval at its ceiling)."""
    improved = [r for r in rows if r.delta > 0]
    if any(not is_thin(r) or (full_room_counts and took_all_room(r)) for r in improved):
        return TargetEvidence.GAIN
    if improved:
        return TargetEvidence.UNVERIFIED
    if any(_supports_no_gain(r) for r in rows):
        return TargetEvidence.NO_GAIN
    return TargetEvidence.UNVERIFIED


def _row_counts(row: PerEvalResult) -> str:
    """ "e1/accuracy: 2 of 40 paired items differ, 2 had room"."""
    text = (
        f"{row.eval_id}/{row.score_key}: {differing_items(row)} of {row.n_items}"
        " paired items differ"
    )
    room = room_items(row)
    return text if room is None else f"{text}, {room} had room"


def unverified_reason(rows: Sequence[PerEvalResult], what: str) -> str:
    """The reason code writes when the rows of `what` are thin, with their
    counts. `what` names the target, for example "primary target e1"."""
    thin = [r for r in rows if is_thin(r)]
    counts = "; ".join(_row_counts(r) for r in thin)
    return (
        f"{UNVERIFIED}: {what} shows no win and no loss — {counts}. A win or"
        f" a loss needs at least {MIN_DIFFERING_ITEMS} items that differ"
    )


def pooled_differing(rows: Sequence[PerEvalResult]) -> int | None:
    """The items that differ, summed over every row. None when a row does
    not say."""
    total = 0
    for row in rows:
        count = differing_items(row)
        if count is None:
            return None
        total += count
    return total


def pooled_unverified_reason(rows: Sequence[PerEvalResult], what: str) -> str | None:
    """The reason when a refusal over every row of `rows` rests on fewer
    than the minimum of items that differ, pooled. None when it does not:
    enough items differ, or a row does not say."""
    total = pooled_differing(rows)
    if total is None or total >= MIN_DIFFERING_ITEMS:
        return None
    return (
        f"{UNVERIFIED}: {what} shows no loss that can be told from noise —"
        f" {total} item(s) differ over the {len(rows)} compared row(s). A loss"
        f" needs at least {MIN_DIFFERING_ITEMS} items that differ"
    )
