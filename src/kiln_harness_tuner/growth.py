"""The growth limit before the train measurement.

A build can add any amount of text to the harness: a long prompt, a large
skill, a data table in a code tool. A very large addition in one change has
three costs. The train measurement pays for the added text on every item.
One experiment then changes too much to say which part had the effect. And
harness text that only grows is a known failure of maintained instruction
files.

So every build is measured against its parent before any train spend: the
size of the whole harness (`harness_size`) of the built run config, minus
that of its parent. The size is the characters of the text the model reads
(`prompt_text.whole_texts`: the prompt, and the text files of the attached
skills and code tools), plus the bytes of the files that the text readers
leave out (a binary file, or one over `prompt_text.ASSET_MAX_BYTES`). A
build that adds more than `GROWTH_LIMIT_CHARS` is refused. The count is the
net change: a build that replaces a large skill with another of the same
size adds nothing.

The limit is one constant, and it refuses only a clear case. Public skill
guidance keeps a `SKILL.md` under about 5,000 tokens, about 20,000
characters (specs/projects/biological_bootloader/research/
harness-optimization/instructions-and-context/skill-authoring-and-
maintenance.md), so the limit is ten of them in one change. The skill
installer's own cap on one skill bundle is 2 MiB.

On a refusal the build agent gets the finding for ONE rebuild, with the
findings of the other build checks (`items._review_build`). A rebuild that
is still over the limit ends the item `dead_end` before any train spend. A
reused run config is checked and never rebuilt. A check that cannot read
the text of either run config does not stop the item: the record says so.

Code only, no model call, no spend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kiln_harness_tuner.prompt_text import (
    CODE_TOOL_RECORD,
    RcText,
    _tree_files,
    code_tool_dir,
    rc_texts,
    whole_texts,
)
from kiln_harness_tuner.skill_installer.bundle import installed_skill_dir

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

# The most characters of harness text one build may add to its parent's.
GROWTH_LIMIT_CHARS = 200_000
# How many places one finding names, largest addition first.
GROWTH_PLACES = 3


def _left_out(directory: Path | None, read: dict[str, str], parts: tuple[str, ...]):
    """`(relative path, bytes)` of each file under `directory` (or under its
    `parts` subfolders) that the text reader left out of `read`."""
    if directory is None:
        return []
    roots = [directory / p for p in parts] if parts else [directory]
    return [
        (rel, path.stat().st_size)
        for root in roots
        for path in _tree_files(root)
        if (rel := path.relative_to(directory).as_posix()) not in read
        and path.name != CODE_TOOL_RECORD
    ]


def harness_places(rc: RcText, project_path: str | Path | None = None) -> dict:
    """Where the run config gives the model something to read -> its size:
    the characters of each text (`whole_texts`), and the bytes of each file
    of an attached skill or code tool that the text readers left out.
    `project_path` None: the text only."""
    places: dict[str, int] = {w: len(t) for w, t in whole_texts(rc).items()}
    if project_path is None:
        return places
    skills_dir = Path(project_path).expanduser() / "skills"
    for skill in (rc.skills or {}).values():
        directory = installed_skill_dir(skills_dir, skill.skill_id)
        for rel, size in _left_out(directory, skill.files, ("references", "assets")):
            places[f"skill {skill.name}, {rel}"] = size
    for tool in (rc.code_tools or {}).values():
        directory = code_tool_dir(project_path, tool.tool_id)
        for rel, size in _left_out(directory, tool.files, ()):
            places[f"code tool {tool.name}, {rel}"] = size
    return places


def harness_size(rc: RcText, project_path: str | Path | None = None) -> int:
    """The size of everything the run config gives the model to read."""
    return sum(harness_places(rc, project_path).values())


def growth_findings(
    parent: RcText, candidate: RcText, project_path: str | Path | None = None
) -> list[str]:
    """One finding when the candidate's harness is more than
    `GROWTH_LIMIT_CHARS` larger than the parent's, else none. It names the
    places that added the most: a place the parent does not have counts in
    full."""
    limit = GROWTH_LIMIT_CHARS
    parent_places = harness_places(parent, project_path)
    candidate_places = harness_places(candidate, project_path)
    before, after = sum(parent_places.values()), sum(candidate_places.values())
    added = after - before
    if added <= limit:
        return []
    grew = sorted(
        (
            (size - parent_places.get(where, 0), where)
            for where, size in candidate_places.items()
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
    project = ctx.target.project_path
    try:
        findings = growth_findings(parent, candidate, project)
        added = harness_size(candidate, project) - harness_size(parent, project)
    except OSError as exc:
        return GrowthCheck(
            "unchecked",
            note=f"not checked — a skill or code-tool file could not be read ({exc})"[
                :300
            ],
        )
    return GrowthCheck(
        "over" if findings else "within", findings=findings, added_chars=added
    )
