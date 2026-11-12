"""Read the knowledge skill from disk into objects.

`load` never raises for a bad document: what it cannot parse becomes a
`Problem` on that document, so that one check run reports every broken file.
It raises `KnowledgeError` only when the skill folder has no `references/`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError

from kiln_harness_tuner.install_agents import NOT_AGENT_FILES, source_dir
from kiln_harness_tuner.knowledge.schema import (
    COMPONENT_DIRS,
    META_MODELS,
    ROOTS,
    CommonMeta,
    Problem,
)

SKILL_NAME = "kiln-harness-tuner-knowledge"
REFERENCES = "references"

# Inline Markdown links. Reference-style links are not used in this tree.
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:|^//")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


class KnowledgeError(RuntimeError):
    """The knowledge skill folder, or its `references/`, is missing."""


@dataclass(frozen=True)
class Link:
    target: str
    # The file the link names. None when it is external, or does not resolve
    # inside the skill folder.
    resolved: Path | None
    external: bool = False


@dataclass(frozen=True)
class Section:
    heading: str
    text: str
    links: tuple[Link, ...]


@dataclass(frozen=True)
class Document:
    path: Path  # relative to references/
    kind: str  # a COMPONENT_DIRS value, "entry" or "subfile"
    id: str
    meta: CommonMeta | None
    sections: tuple[Section, ...]
    links: tuple[Link, ...]
    chars: int
    text: str
    problems: tuple[Problem, ...]

    def section(self, heading: str) -> Section | None:
        return next((s for s in self.sections if s.heading == heading), None)


@dataclass(frozen=True)
class KnowledgeBase:
    root: Path  # the skill folder
    documents: tuple[Document, ...]
    # Every file under references/ that is not loaded as a document: a file
    # that is not Markdown, or a Markdown file outside the layout.
    other_files: tuple[Path, ...] = ()

    @property
    def references(self) -> Path:
        return self.root / REFERENCES

    def by_kind(self, kind: str) -> tuple[Document, ...]:
        return tuple(d for d in self.documents if d.kind == kind)

    def get(self, kind: str, id: str) -> Document | None:
        return next((d for d in self.by_kind(kind) if d.id == id), None)


def resolve_link(base: Path, target: str, inside: Path) -> Path | None:
    """The path `target` names, read from a file in `base`, when it exists
    inside `inside` and is spelled EXACTLY as it is on disk. Else None.

    `Path.exists()` asks the filesystem, and some answer case-insensitively,
    so a link can pass for its author and 404 on a case-sensitive checkout.
    Each part is checked against its parent's real directory listing instead.
    A target that climbs out of `inside` resolves for nobody else, so it is
    refused. `tests/test_docs.py` uses the same rule for the documentation."""
    current = base.resolve()
    for part in PurePosixPath(target).parts:
        if part == ".":
            continue
        if part == "..":
            current = current.parent
            continue
        if part.startswith("/"):
            return None  # an absolute link cannot resolve from a document
        try:
            names = os.listdir(current)
        except OSError:
            return None
        if part not in names:
            return None
        current = current / part
    if not current.is_relative_to(inside.resolve()):
        return None
    return current


def default_root() -> Path:
    """The knowledge skill folder in the installed package."""
    return source_dir() / "skills" / SKILL_NAME


def load(root: Path | None = None) -> KnowledgeBase:
    root = (root or default_root()).resolve()
    references = root / REFERENCES
    if not references.is_dir():
        raise KnowledgeError(f"no {REFERENCES}/ folder in {root}")
    documents: list[Document] = []
    other_files: list[Path] = []
    for path in sorted(p for p in references.rglob("*") if p.is_file()):
        relative = path.relative_to(references)
        if any(part in NOT_AGENT_FILES for part in relative.parts):
            continue  # the installer leaves these out, so no agent reads them
        kind = _kind(relative)
        if kind is None:
            other_files.append(relative)
            continue
        documents.append(_load_document(root, relative, kind))
    return KnowledgeBase(root, tuple(documents), tuple(other_files))


def _kind(relative: Path) -> str | None:
    """The kind of the document at `relative`, or None when it is not a
    document of the layout."""
    if relative.suffix != ".md":
        return None
    parts = relative.parts
    if len(parts) == 1:
        return "entry" if parts[0] in ROOTS else None
    if parts[0] not in COMPONENT_DIRS:
        return None
    return parts[0] if len(parts) == 2 else "subfile"


def _load_document(root: Path, relative: Path, kind: str) -> Document:
    path = root / REFERENCES / relative
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        problem = Problem("encoding", relative.as_posix(), f"not UTF-8: {exc}")
        return Document(
            path=relative,
            kind=kind,
            id=relative.stem,
            meta=None,
            sections=(),
            links=(),
            chars=path.stat().st_size,
            text="",
            problems=(problem,),
        )
    problems: list[Problem] = []
    meta: CommonMeta | None = None
    body = text
    if kind in COMPONENT_DIRS:
        meta, body, problem = _front_matter(text, kind, relative.as_posix())
        if problem:
            problems.append(problem)
    links: list[Link] = []
    sections: list[Section] = []
    heading: str | None = None
    lines: list[str] = []
    section_links: list[Link] = []

    def close() -> None:
        if heading is not None:
            sections.append(
                Section(heading, "\n".join(lines).strip(), tuple(section_links))
            )

    for line, fenced in _fence_marked(body):
        if fenced:  # text of the section, but never a heading or a link
            lines.append(line)
            continue
        if line.startswith("## "):
            close()
            heading, lines, section_links = line[3:].strip(), [], []
            continue
        found = [_link(path, t, root) for t in LINK_RE.findall(line)]
        links.extend(found)
        section_links.extend(found)
        lines.append(line)
    close()
    return Document(
        path=relative,
        kind=kind,
        id=relative.stem,
        meta=meta,
        sections=tuple(sections),
        links=tuple(links),
        chars=len(text),
        text=text,
        problems=tuple(problems),
    )


def _fence_marked(text: str) -> list[tuple[str, bool]]:
    """Each line of `text`, with True when it is part of a fenced block (its
    fence lines included).

    As in CommonMark, a fence closes only on a line of the same character, at
    least as long as the opening fence, with nothing after it."""
    out: list[tuple[str, bool]] = []
    closing: re.Pattern[str] | None = None
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if closing is None and match:
            fence = match.group(1)
            closing = re.compile(rf"[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*")
            out.append((line, True))
        elif closing is not None:
            if closing.fullmatch(line):
                closing = None
            out.append((line, True))
        else:
            out.append((line, False))
    return out


def _link(source: Path, target: str, root: Path) -> Link:
    if SCHEME_RE.match(target):
        return Link(target, None, external=True)
    bare = target.split("#", 1)[0]
    if not bare:  # a link to a heading of the same file
        return Link(target, source)
    resolved = resolve_link(source.parent, bare, root)
    if resolved is not None and not resolved.is_file():
        resolved = None
    return Link(target, resolved)


def _front_matter(
    text: str, kind: str, path: str
) -> tuple[CommonMeta | None, str, Problem | None]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text, Problem("front-matter", path, "no front matter")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return None, text, Problem("front-matter", path, "front matter is not closed")
    body = "\n".join(lines[end + 1 :])
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    # An impossible date (2026-02-30) is a ValueError from the YAML loader.
    except (yaml.YAMLError, ValueError) as exc:
        return None, body, Problem("front-matter", path, f"bad YAML: {exc}")
    if not isinstance(data, dict):
        return None, body, Problem("front-matter", path, "front matter is not a map")
    try:
        return META_MODELS[kind].model_validate(data), body, None
    except ValidationError as exc:
        message = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'front matter'}: {e['msg']}"
            for e in exc.errors()
        )
        return None, body, Problem("front-matter", path, message)
