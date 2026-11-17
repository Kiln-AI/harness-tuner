"""The reference check at build time.

A build can attach, detach or replace skills and tools, and leave text that
still names what it took away. A build can detach a tool while a workflow
skill still tells the model to call it. A skill can name a skill whose
replacement is the one attached. The arm then fails on exactly those cases,
and its measurement is wasted. So code reads every build for names that
point at nothing.

Two kinds of reference, each from a known set of names, never free text:

- **a skill.** The prompt, or the text of an attached skill, names a skill
  that the run config does not attach. Only the names of skills that exist
  in the project (on disk, under `skills/`) or that the parent attaches are
  looked for: that is what makes a name a dangling reference and not an
  English word. An archived project skill is said to be archived.
- **a tool.** The prompt, or the text of an attached skill, names a tool
  function that the PARENT attaches and the candidate does not: the build
  detached it or renamed it, and the text still names it. A code tool is
  named by its `tool_function_name`; an OpenEnv, MCP or built-in tool by the
  last `::` part of its id.

**Only what the build introduced is reported.** A skill reference that the
parent already has — the same name in the same place, not attached to the
parent either — is the parent's, not the build's: every build that keeps it
would otherwise be flagged, whatever it changed. A tool finding is always
the build's, because the parent attaches the tool.

Code-tool allowlists are not compared. A code tool may call a tool that it
allowlists even when the run config does not attach that tool: Kiln resolves
a nested call from the code tool's own allowlist against the project, so a
wrapper that calls a detached tool is the intended pattern.

A name matches only on its token boundaries: the characters before and
after it are not letters, digits, `_` or `-`. So `inventory-read` is not
found inside `inventory-read-v2`, and `sql_query` is not found inside
`sql_query_v2`. A plain-word name (no `-`, `_` or digit, such as `billing`)
is an ordinary word too, so it matches only:

- in backticks;
- in quotes that a `:` does not follow (a JSON key is not a reference);
- as "the <name> skill", "the <name> tool" or "<name> skill" — the name
  before the word, so "the tool result" does not name a tool `result`.

Each text is split into tokens once: a distinctive name is a set lookup, and
the plain-word names share one pattern. The scan runs in a worker thread,
because a project's skills can carry megabytes of text and items run
concurrently.

Code only, no model call, no spend. The outcome is set by `--reference-check`
(`Ctx.reference_check`):

- `warn` (the default): the findings go on the item outcome, on a `reference
  check:` line of the experiment record and in the analysis input, and the
  item goes on to its measurement;
- `refuse`: a finding gets the build ONE rebuild, together with any
  generality or judge-copy findings (`items._review_build`). A second hit
  ends the item `dead_end` before any train spend;
- `off`: the check does not run.

A check that cannot read its inputs does not stop the item: the record says
what it could not read.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kiln_harness_tuner.prompt_text import (
    CODE_TOOL_PREFIX,
    SKILL_TOOL_PREFIX,
    RcText,
    rc_texts,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

REFERENCE_CHECK_MODES = ("warn", "refuse", "off")
# How many places one finding names before "+N more".
FINDING_PLACES = 3
# How much of the findings reaches a one-line record or event.
FINDING_CHARS = 300
# A character that can be part of a skill or tool name. A match must not
# have one on either side.
_NAME_CHAR = r"[A-Za-z0-9_-]"
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9_-]+")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def _is_distinctive(name: str) -> bool:
    """A name that is not also an ordinary word: it has a `-`, a `_` or a
    digit. `sql_query` and `inventory-read` are; `billing` is not."""
    return any(c in name for c in "-_") or any(c.isdigit() for c in name)


@functools.lru_cache(maxsize=256)
def _plain_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    """One pattern for every plain-word name in `names` (see the module
    docstring for the three forms). The matched name is the one group that
    is set."""
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    bounded = rf"(?<!{_NAME_CHAR})({alt})(?!{_NAME_CHAR})"
    return re.compile(
        rf"`({alt})`"
        rf"|[\"']({alt})[\"'](?!\s*:)"
        rf"|(?i:\bthe\s+){bounded}(?i:\s+(?:skill|tool)s?\b)"
        rf"|{bounded}(?i:\s+skills?\b)"
    )


@functools.lru_cache(maxsize=256)
def _bounded_pattern(name: str) -> re.Pattern[str]:
    """A distinctive name with a character outside `_NAME_CHAR` (a `.` in
    an MCP tool name): the tokens cannot hold it, so it gets a pattern."""
    return re.compile(rf"(?<!{_NAME_CHAR}){re.escape(name)}(?!{_NAME_CHAR})")


def referenced(texts: dict[str, str], names: Iterable[str]) -> dict[str, list[str]]:
    """name -> the places in `texts` (place -> text) that reference it, for
    every name in `names` that some text references. Each text is split
    into tokens once and searched once for all plain-word names."""
    wanted = [n for n in dict.fromkeys(names) if n]
    simple = {n for n in wanted if _is_distinctive(n) and _TOKEN.fullmatch(n)}
    plain = tuple(n for n in wanted if not _is_distinctive(n))
    odd = [n for n in wanted if n not in simple and n not in plain]
    found: dict[str, list[str]] = {}
    for where, text in texts.items():
        if not text:
            continue
        hits: set[str] = set()
        if simple:
            hits |= simple & set(_TOKEN_SPLIT.split(text))
        if plain:
            for match in _plain_pattern(plain).finditer(text):
                hits.add(next(g for g in match.groups() if g))
        hits |= {n for n in odd if _bounded_pattern(n).search(text)}
        for name in hits:
            found.setdefault(name, []).append(where)
    return found


def names_in(text: str, names: Iterable[str]) -> list[str]:
    """The names from `names` that `text` references, in the given order."""
    hits = referenced({"text": text}, names)
    return [n for n in dict.fromkeys(names) if n in hits]


@dataclass(frozen=True)
class ProjectSkills:
    """The project's skills on disk: `names` maps skill id -> name;
    `archived`: the names whose `skill.kiln` says `is_archived`."""

    names: dict[str, str] = field(default_factory=dict)
    archived: frozenset[str] = frozenset()


def project_skills(project_path: str | Path) -> ProjectSkills:
    """Every skill directory under the project's `skills/`: its name from
    `skill.kiln`, else the directory's ` - <name>` part. Empty when the
    directory cannot be listed."""
    skills_dir = Path(project_path).expanduser() / "skills"
    names: dict[str, str] = {}
    archived: set[str] = set()
    try:
        children = sorted(skills_dir.iterdir()) if skills_dir.is_dir() else []
    except OSError:
        return ProjectSkills()
    for child in children:
        if not child.is_dir():
            continue
        skill_id, _, suffix = child.name.partition(" - ")
        name = suffix
        try:
            sidecar = json.loads((child / "skill.kiln").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sidecar = None
        if isinstance(sidecar, dict) and isinstance(sidecar.get("name"), str):
            name = sidecar["name"]
        if skill_id and name:
            names[skill_id] = name
            if isinstance(sidecar, dict) and sidecar.get("is_archived") is True:
                archived.add(name)
    return ProjectSkills(names=names, archived=frozenset(archived))


def tool_function_name(tool_id: str, rc: RcText | None = None) -> str | None:
    """The name the model calls a tool by: a code tool's
    `tool_function_name` (from `rc`'s read code tools), else the last `::`
    part of an OpenEnv, MCP or built-in tool id. None for a skill, for a code
    tool that `rc` did not read, and for a name that is only digits."""
    if tool_id.startswith(SKILL_TOOL_PREFIX):
        return None
    if tool_id.startswith(CODE_TOOL_PREFIX):
        code_id = tool_id.removeprefix(CODE_TOOL_PREFIX)
        read = (rc.code_tools or {}).get(code_id) if rc is not None else None
        return read.function_name or None if read is not None else None
    name = tool_id.rsplit("::", 1)[-1]
    return name if name and not name.isdigit() else None


def _function_names(rc: RcText) -> dict[str, str]:
    """function name -> tool id, for every non-skill tool `rc` attaches."""
    names: dict[str, str] = {}
    for tool_id in rc.tool_ids:
        name = tool_function_name(tool_id, rc)
        if name:
            names.setdefault(name, tool_id)
    return names


def _scanned_texts(rc: RcText) -> dict[str, str]:
    """Where the model reads a reference -> that text: "the prompt" and
    "skill <name>, <file>". A code tool's text is not read for names: a
    wrapper's description names the tool it replaces by design."""
    texts = {"the prompt": rc.prompt or ""}
    for bundle in (rc.skills or {}).values():
        for path, text in bundle.files.items():
            texts[f"skill {bundle.name}, {path}"] = text
    return texts


def _attached_skill_names(rc: RcText, project: dict[str, str]) -> set[str]:
    return {project[i] for i in rc.skill_ids if i in project} | {
        bundle.name for bundle in (rc.skills or {}).values()
    }


def _places(places: list[str]) -> str:
    shown = ", ".join(places[:FINDING_PLACES])
    more = len(places) - FINDING_PLACES
    return shown + (f" (+{more} more)" if more > 0 else "")


def _near(name: str, attached: Iterable[str]) -> list[str]:
    """Attached names that are another version of `name`: one starts with
    the other and a `-` or `_` (`foo` and `foo-v2`)."""
    return [
        other
        for other in attached
        if other != name
        and (
            other.startswith((f"{name}-", f"{name}_"))
            or name.startswith((f"{other}-", f"{other}_"))
        )
    ]


@dataclass(frozen=True)
class ReferenceCheck:
    """One check's answer. `clean`: no dangling reference. `found`:
    `findings` name each one. `unchecked`: it could not run, and `note`
    says why. `note` on a `clean` or `found` answer names text that could
    not be read."""

    status: Literal["clean", "found", "unchecked"]
    findings: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def summary(self) -> str:
        if self.status == "found":
            first = self.findings[0][:FINDING_CHARS]
            more = (
                f" (+{len(self.findings) - 1} more)" if len(self.findings) > 1 else ""
            )
            return f"{first}{more}"
        return self.note or self.status


def reference_findings(
    candidate: RcText,
    parent: RcText | None,
    project: dict[str, str] | ProjectSkills,
) -> list[str]:
    """The findings for `candidate`, one per dangling name, skills first.
    `parent`: the run config it was built from (None when unread).
    `project`: the project's skills (or skill id -> name)."""
    if not isinstance(project, ProjectSkills):
        project = ProjectSkills(names=dict(project))
    texts = _scanned_texts(candidate)

    # 1. Skills.
    attached_skills = _attached_skill_names(candidate, project.names)
    known = dict.fromkeys(project.names.values())
    if parent is not None:
        for bundle in (parent.skills or {}).values():
            known.setdefault(bundle.name)
    wanted_skills = [n for n in known if n not in attached_skills]

    # 2. Tools the parent attaches and the candidate does not. Skipped when
    # either side's code tools are unread: a tool with no known function
    # name would read as dropped, or would be missed.
    kept: dict[str, str] = {}
    dropped: list[str] = []
    if (
        parent is not None
        and parent.code_tools is not None
        and candidate.code_tools is not None
    ):
        kept = _function_names(candidate)
        dropped = [n for n in _function_names(parent) if n not in kept]

    hits = referenced(texts, [*wanted_skills, *dropped])
    skill_hits = {n: hits[n] for n in wanted_skills if n in hits}
    if parent is not None and skill_hits:
        # The parent's own dangling references are not the build's.
        parent_attached = _attached_skill_names(parent, project.names)
        inherited = referenced(
            _scanned_texts(parent), [n for n in skill_hits if n not in parent_attached]
        )
        for name, places in inherited.items():
            left = [p for p in skill_hits[name] if p not in places]
            if left:
                skill_hits[name] = left
            else:
                del skill_hits[name]

    findings: list[str] = []
    for name in sorted(skill_hits):
        label = f"skill `{name}`" + (" (archived)" if name in project.archived else "")
        finding = f"{label} is named in {_places(skill_hits[name])} but is not attached"
        near = _near(name, sorted(attached_skills))
        if near:
            finding += " (attached: " + ", ".join(f"`{n}`" for n in near) + ")"
        findings.append(finding)
    for name in sorted(n for n in dropped if n in hits):
        finding = (
            f"tool `{name}` is named in {_places(hits[name])}; the parent"
            " attaches it and this run config does not"
        )
        near = _near(name, sorted(kept))
        if near:
            finding += " (attached: " + ", ".join(f"`{n}`" for n in near) + ")"
        findings.append(finding)
    return findings


def _unread_parts(rc: RcText, who: str) -> list[str]:
    return [
        f"{who} {what} could not be read"
        for what, got in (("skills' text", rc.skills), ("code tools", rc.code_tools))
        if got is None
    ]


async def check_references(ctx: Ctx, rc_id: str, parent_rc_id: str) -> ReferenceCheck:
    """Look for dangling references in `rc_id`, built from
    `parent_rc_id`. Code only, no model call, no spend."""
    texts = await rc_texts(ctx, list(dict.fromkeys([parent_rc_id, rc_id])))
    candidate, parent = texts[rc_id], texts[parent_rc_id]
    if not candidate.found or candidate.prompt is None:
        return ReferenceCheck(
            "unchecked", note="not checked — the candidate's prompt could not be read"
        )
    notes = _unread_parts(candidate, "the candidate's")
    if not parent.found:
        notes.append(
            "the parent could not be read, so only the project's skills were checked"
        )
    else:
        notes += _unread_parts(parent, "the parent's")
    project_path = ctx.target.project_path

    def scan() -> list[str]:
        return reference_findings(
            candidate, parent if parent.found else None, project_skills(project_path)
        )

    findings = await asyncio.to_thread(scan)
    return ReferenceCheck(
        "found" if findings else "clean", findings=findings, note="; ".join(notes)
    )
