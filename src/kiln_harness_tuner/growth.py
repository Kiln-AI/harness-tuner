"""The growth limit before the train measurement.

A build can add any amount of text to the harness: a long prompt, a large
skill, a data table in a code tool. A very large addition in one change has
three costs. The train measurement pays for the added text on every item.
One experiment then changes too much to say which part had the effect. And
harness text that only grows is a known failure of maintained instruction
files.

So every build is measured against its parent before any train spend: the
characters of the whole harness text (`prompt_text.whole_texts`: the
prompt, and every file of the attached skills and code tools) of the built
run config, minus those of its parent. A build that adds more than
`GROWTH_LIMIT_CHARS` is refused. The count is the net change: a build that
replaces a large skill with another of the same size adds nothing.

The limit is one constant, and it refuses only a clear case. A full-size
`SKILL.md` is about 20,000 characters, so the limit is ten of them in one
change. The skill installer's own cap on one skill bundle is 2 MiB.

On a refusal the build agent gets the finding for ONE rebuild, with the
findings of the other build checks (`items._review_build`). A rebuild that
is still over the limit ends the item `dead_end` before any train spend. A
reused run config is checked and never rebuilt. A check that cannot read
the text of either run config does not stop the item: the record says so.

Code only, no model call, no spend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from kiln_harness_tuner.prompt_text import RcText, rc_texts, whole_texts

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

# The most characters of harness text one build may add to its parent's.
GROWTH_LIMIT_CHARS = 200_000
# How many places one finding names, largest addition first.
GROWTH_PLACES = 3


def harness_chars(rc: RcText) -> int:
    """The characters of everything the run config gives the model to read."""
    return sum(len(text) for text in whole_texts(rc).values())


def growth_findings(parent: RcText, candidate: RcText) -> list[str]:
    """One finding when the candidate's harness text is more than
    `GROWTH_LIMIT_CHARS` characters longer than the parent's, else none. It
    names the places that added the most: a place the parent does not have
    counts in full."""
    limit = GROWTH_LIMIT_CHARS
    before, after = harness_chars(parent), harness_chars(candidate)
    added = after - before
    if added <= limit:
        return []
    parent_places = whole_texts(parent)
    grew = sorted(
        (
            (len(text) - len(parent_places.get(where, "")), where)
            for where, text in whole_texts(candidate).items()
        ),
        reverse=True,
    )
    places = ", ".join(
        f"{where} (+{chars:,})" for chars, where in grew[:GROWTH_PLACES] if chars > 0
    )
    return [
        f"the build adds {added:,} characters of harness text to its parent's"
        f" ({before:,} to {after:,}); the limit for one change is {limit:,}."
        f" The largest additions: {places}"
    ]


def _unread(rc: RcText) -> list[str]:
    return [
        what
        for what, got in (
            ("prompt", rc.prompt),
            ("skills' text", rc.skills),
            ("code tools' text", rc.code_tools),
        )
        if got is None
    ]


@dataclass(frozen=True)
class GrowthCheck:
    """One check's answer. `within`: the build adds no more than the limit.
    `over`: `findings` holds the one finding. `unchecked`: text of one of
    the two run configs could not be read, and `note` says which.
    `added_chars`: candidate minus parent (negative when it shrank)."""

    status: Literal["within", "over", "unchecked"]
    findings: list[str] = field(default_factory=list)
    note: str = ""
    added_chars: int = 0

    @property
    def summary(self) -> str:
        if self.status == "over":
            return self.findings[0]
        return self.note or self.status


async def check_growth(ctx: Ctx, rc_id: str, parent_rc_id: str) -> GrowthCheck:
    """Compare the harness text of `rc_id` with that of `parent_rc_id`."""
    texts = await rc_texts(ctx, list(dict.fromkeys([parent_rc_id, rc_id])))
    parent, candidate = texts[parent_rc_id], texts[rc_id]
    unread = [
        f"the {who}'s {what}"
        for who, rc in (("parent", parent), ("candidate", candidate))
        for what in _unread(rc)
    ]
    if unread:
        return GrowthCheck(
            "unchecked",
            note=f"not checked — {' and '.join(unread)} could not be read",
        )
    findings = growth_findings(parent, candidate)
    return GrowthCheck(
        "over" if findings else "within",
        findings=findings,
        added_chars=harness_chars(candidate) - harness_chars(parent),
    )
