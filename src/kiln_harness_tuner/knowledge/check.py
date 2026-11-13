"""The structural checks of the knowledge system.

`check` is a pure function. The caller passes the two sets that live on the
code side: the gateway operations, and the detector registry (detector id to
smell id). So the tests can pass small sets, and this package does not import
the `smells` package.

A check reads only what a test can prove. The content rules that need
judgement (conditions, confidence, the approval rule for priors) belong to
the maintenance skill's audit.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from kiln_harness_tuner.knowledge.index import read_block, render_blocks
from kiln_harness_tuner.knowledge.load import (
    Document,
    KnowledgeBase,
    Link,
    fence_marked,
)
from kiln_harness_tuner.knowledge.schema import (
    COMPONENT_DIRS,
    ENTRY_BLOCKS,
    EVAL_STANCES,
    ID_PATTERN,
    LEVER_ID_PATTERN,
    MAX_CHARS,
    MAX_DEPTH,
    MAX_ID_CHARS,
    NO_PRIORS,
    PRIOR_SOURCES,
    REQUIRED_SECTIONS,
    ROOTS,
    TASK_SHAPES,
    LeverMeta,
    Problem,
    SmellMeta,
    TechniqueMeta,
)

PRIOR_ITEM_RE = re.compile(r"^- ", re.MULTILINE)
PRIOR_SOURCE_RE = re.compile(
    r"Source:\s*(" + "|".join(re.escape(s) for s in PRIOR_SOURCES) + r")\b"
)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# The maintenance templates mark each value to fill in as `{{NAME}}`.
PLACEHOLDER_RE = re.compile(r"\{\{[^{}\n]*(?:\}\})?")


class _Context:
    """What several rules need: the knowledge base, and the component files
    by their path under references/."""

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.references = kb.references.resolve()
        self.by_path = {self.references / d.path: d for d in kb.documents}

    def rel(self, path: Path) -> str:
        return path.relative_to(self.references).as_posix()

    def component(self, link: Link, *dirs: str) -> Document | None:
        """The component that `link` names, when it is a primary document of
        one of `dirs`."""
        if link.resolved is None:
            return None
        doc = self.by_path.get(link.resolved)
        if doc is None or doc.kind not in dirs:
            return None
        return doc

    def components(self) -> Iterable[Document]:
        return (d for d in self.kb.documents if d.kind in COMPONENT_DIRS)


def check(
    kb: KnowledgeBase,
    *,
    gateway_ops: Iterable[str],
    detectors: Mapping[str, str],
) -> list[Problem]:
    ctx = _Context(kb)
    ops = frozenset(gateway_ops)
    registry = dict(detectors)
    problems: list[Problem] = []
    for rule in RULES:
        problems.extend(rule(ctx, ops, registry))
    return sorted(problems, key=lambda p: (p.path, p.rule, p.message))


Rule = Callable[[_Context, frozenset[str], dict[str, str]], Iterable[Problem]]


def _front_matter(ctx: _Context, *_: object) -> Iterable[Problem]:
    for doc in ctx.kb.documents:
        yield from doc.problems


def _id(ctx: _Context, *_: object) -> Iterable[Problem]:
    for doc in ctx.components():
        pattern, joiners = (
            (LEVER_ID_PATTERN, "'-' or '_'")
            if doc.kind == "levers"
            else (ID_PATTERN, "'-'")
        )
        if not pattern.match(doc.id) or len(doc.id) > MAX_ID_CHARS:
            yield Problem(
                "id",
                doc.path.as_posix(),
                f"the id {doc.id!r} must be lower-case words joined by"
                f" {joiners}, at most {MAX_ID_CHARS} characters",
            )


def _sections(ctx: _Context, *_: object) -> Iterable[Problem]:
    for doc in ctx.components():
        required = REQUIRED_SECTIONS[doc.kind]
        headings = tuple(s.heading for s in doc.sections)
        if headings[: len(required)] != required:
            yield Problem(
                "sections",
                doc.path.as_posix(),
                f"the first sections must be {list(required)}; found {list(headings)}",
            )
            continue
        for section in doc.sections[: len(required)]:
            # A template puts a comment in each section for its author.
            if not HTML_COMMENT_RE.sub("", section.text).strip():
                yield Problem(
                    "sections",
                    doc.path.as_posix(),
                    f"the section {section.heading!r} is empty",
                )


def _placeholder(ctx: _Context, *_: object) -> Iterable[Problem]:
    """A `{{` outside a fence is template text that the author did not
    replace. The front matter is read too, so a template title is found."""
    for doc in ctx.kb.documents:
        for number, (line, fenced) in enumerate(fence_marked(doc.text), 1):
            if fenced:
                continue
            for placeholder in PLACEHOLDER_RE.findall(line):
                yield Problem(
                    "placeholder",
                    doc.path.as_posix(),
                    f"line {number} holds the template text {placeholder!r};"
                    " replace it",
                )


def _size(ctx: _Context, *_: object) -> Iterable[Problem]:
    for doc in ctx.kb.documents:
        limit = MAX_CHARS.get(doc.kind)
        if limit is not None and doc.chars > limit:
            yield Problem(
                "size",
                doc.path.as_posix(),
                f"{doc.chars} characters; the limit is {limit}. Split it.",
            )


def _shape(ctx: _Context, *_: object) -> Iterable[Problem]:
    for doc in ctx.components():
        applies_to = getattr(doc.meta, "applies_to", "all")
        if isinstance(applies_to, list):
            for value in applies_to:
                if value not in TASK_SHAPES:
                    yield Problem(
                        "shape",
                        doc.path.as_posix(),
                        f"{value!r} is not a task shape; use 'all' or"
                        f" {list(TASK_SHAPES)}",
                    )


def _link(ctx: _Context, *_: object) -> Iterable[Problem]:
    for doc in ctx.kb.documents:
        for link in doc.links:
            if not link.external and link.resolved is None:
                yield Problem(
                    "link",
                    doc.path.as_posix(),
                    f"{link.target} does not resolve to a file inside the skill"
                    " (spelled as on disk)",
                )


def _depths(ctx: _Context) -> dict[Path, int]:
    """Each reachable document -> its distance in links from the nearest
    root."""
    depth: dict[Path, int] = {}
    queue: deque[Path] = deque()
    for name in ROOTS:
        path = ctx.references / name
        if path in ctx.by_path:
            depth[path] = 0
            queue.append(path)
    while queue:
        path = queue.popleft()
        for link in ctx.by_path[path].links:
            target = link.resolved
            if target in ctx.by_path and target not in depth:
                depth[target] = depth[path] + 1
                queue.append(target)
    return depth


def _reachable(ctx: _Context, *_: object) -> Iterable[Problem]:
    depth = _depths(ctx)
    for path, doc in ctx.by_path.items():
        if path not in depth:
            yield Problem(
                "reachable",
                doc.path.as_posix(),
                "no chain of links from an entry file leads here",
            )


def _depth(ctx: _Context, *_: object) -> Iterable[Problem]:
    for path, level in _depths(ctx).items():
        if level > MAX_DEPTH:
            yield Problem(
                "depth",
                ctx.rel(path),
                f"{level} links from the nearest entry file; the limit is {MAX_DEPTH}",
            )


def _lever_ids(ctx: _Context) -> set[str]:
    return {d.id for d in ctx.kb.by_kind("levers")}


def _technique_lever_ids(ctx: _Context, *_: object) -> Iterable[Problem]:
    levers = _lever_ids(ctx)
    for doc in ctx.kb.by_kind("techniques"):
        if isinstance(doc.meta, TechniqueMeta):
            for lever in doc.meta.levers:
                if lever not in levers:
                    yield Problem(
                        "technique-lever-ids",
                        doc.path.as_posix(),
                        f"the lever {lever!r} has no document in levers/",
                    )


def _lever_ops(ctx: _Context, ops: frozenset[str], _: object) -> Iterable[Problem]:
    for doc in ctx.kb.by_kind("levers"):
        if isinstance(doc.meta, LeverMeta):
            for op in doc.meta.gateway_ops:
                if op not in ops:
                    yield Problem(
                        "lever-ops",
                        doc.path.as_posix(),
                        f"{op!r} is not a gateway operation",
                    )


def _technique_lever_links(ctx: _Context, *_: object) -> Iterable[Problem]:
    existing = _lever_ids(ctx)
    for doc in ctx.kb.by_kind("techniques"):
        how = doc.section("How")
        if how is None or not isinstance(doc.meta, TechniqueMeta):
            continue
        named = set(doc.meta.levers)
        linked = {
            lever.id
            for link in how.links
            if (lever := ctx.component(link, "levers")) is not None
        }
        # A lever with no document is the rule `technique-lever-ids`.
        for lever in sorted((named & existing) - linked):
            yield Problem(
                "technique-lever-links",
                doc.path.as_posix(),
                f"How does not link the lever {lever!r} that `levers` names",
            )
        for lever in sorted(linked - named):
            yield Problem(
                "technique-lever-links",
                doc.path.as_posix(),
                f"How links the lever {lever!r}, which `levers` does not name",
            )


def _technique_stance(ctx: _Context, *_: object) -> Iterable[Problem]:
    for doc in ctx.kb.by_kind("techniques"):
        section = doc.section("If it does not move the evals")
        if section and section.text and not section.text.startswith(EVAL_STANCES):
            yield Problem(
                "technique-stance",
                doc.path.as_posix(),
                f"'If it does not move the evals' must start with one of"
                f" {list(EVAL_STANCES)}",
            )


def _priors(ctx: _Context, kind: str, rule: str) -> Iterable[Problem]:
    """The `Priors` section of each `kind` document is a list of cases, each
    with a valid `Source:`, or the line `None yet.`. A document with no such
    section has no problem here: the rule `sections` decides if it needs
    one."""
    for doc in ctx.kb.by_kind(kind):
        section = doc.section("Priors")
        if section is None or not section.text or section.text == NO_PRIORS:
            continue
        items = PRIOR_ITEM_RE.split(section.text)
        if items[0].strip():
            yield Problem(
                rule,
                doc.path.as_posix(),
                f"Priors must be a list of cases, or the line {NO_PRIORS!r}",
            )
        for number, item in enumerate(items[1:], 1):
            if not PRIOR_SOURCE_RE.search(item):
                yield Problem(
                    rule,
                    doc.path.as_posix(),
                    f"prior {number} has no 'Source:' with one of"
                    f" {list(PRIOR_SOURCES)}",
                )


def _technique_priors(ctx: _Context, *_: object) -> Iterable[Problem]:
    return _priors(ctx, "techniques", "technique-priors")


def _playbook_priors(ctx: _Context, *_: object) -> Iterable[Problem]:
    # A playbook has no required `Priors` section. The V1 coverage document
    # gives some playbooks priors, so the optional section after `Budget`
    # has the form of a technique's.
    return _priors(ctx, "playbooks", "playbook-priors")


def _links_only_to(
    ctx: _Context,
    rule: str,
    kind: str,
    heading: str,
    dirs: tuple[str, ...],
    at_least_one: bool,
) -> Iterable[Problem]:
    """Each link in section `heading` of each `kind` document names a
    component of `dirs`. A link that does not resolve is the rule `link`."""
    for doc in ctx.kb.by_kind(kind):
        section = doc.section(heading)
        if section is None:
            continue
        good = 0
        for link in section.links:
            if not link.external and link.resolved is None:
                continue
            if ctx.component(link, *dirs) is None:
                yield Problem(
                    rule,
                    doc.path.as_posix(),
                    f"{heading!r} links {link.target}, which is not in"
                    f" {'/ or '.join(dirs)}/",
                )
            else:
                good += 1
        if at_least_one and not good:
            yield Problem(
                rule,
                doc.path.as_posix(),
                f"{heading!r} must link at least one file in {'/ or '.join(dirs)}/",
            )


def _alternatives(ctx: _Context, *_: object) -> Iterable[Problem]:
    return _links_only_to(
        ctx, "alternatives", "techniques", "Alternatives", ("techniques",), False
    )


def _playbook_steps(ctx: _Context, *_: object) -> Iterable[Problem]:
    return _links_only_to(
        ctx,
        "playbook-steps",
        "playbooks",
        "What to try, in order",
        ("techniques", "playbooks"),
        True,
    )


def _smell_remedies(ctx: _Context, *_: object) -> Iterable[Problem]:
    return _links_only_to(
        ctx, "smell-remedies", "smells", "Remedies", ("playbooks", "techniques"), True
    )


def _smell_detectors(
    ctx: _Context, _: object, registry: dict[str, str]
) -> Iterable[Problem]:
    listed: dict[str, set[str]] = {}
    for doc in ctx.kb.by_kind("smells"):
        meta = doc.meta
        if not isinstance(meta, SmellMeta):
            continue
        path = doc.path.as_posix()
        listed[doc.id] = set(meta.detectors)
        if "code" in meta.detected_by and not meta.detectors:
            yield Problem(
                "smell-detectors",
                path,
                "detected_by has 'code', so detectors must name its detectors",
            )
        if "code" not in meta.detected_by and meta.detectors:
            yield Problem(
                "smell-detectors",
                path,
                "detectors is set, but detected_by does not have 'code'",
            )
        for detector in meta.detectors:
            smell = registry.get(detector)
            if smell is None:
                yield Problem(
                    "smell-detectors", path, f"no registered detector {detector!r}"
                )
            elif smell != doc.id:
                yield Problem(
                    "smell-detectors",
                    path,
                    f"the detector {detector!r} is registered for the smell {smell!r}",
                )
    for detector, smell in sorted(registry.items()):
        if detector not in listed.get(smell, set()):
            yield Problem(
                "smell-detectors",
                f"smells/{smell}.md",
                f"the registered detector {detector!r} names this smell, and the"
                " smell document does not list it",
            )


def _in_a_subfile_directory(path: Path) -> bool:
    """True when `path`, relative to references/, is inside the directory of
    a component, beside its primary document."""
    return len(path.parts) > 2 and path.parts[0] in COMPONENT_DIRS


def _subfile(ctx: _Context, *_: object) -> Iterable[Problem]:
    for path in ctx.kb.other_files:
        if _in_a_subfile_directory(path):
            yield Problem("subfile", path.as_posix(), "a sub-file must be Markdown")
    for doc in ctx.kb.by_kind("subfile"):
        kind, owner_id = doc.path.parts[0], doc.path.parts[1]
        owner = ctx.kb.get(kind, owner_id)
        if owner is None:
            yield Problem(
                "subfile",
                doc.path.as_posix(),
                f"no component {kind}/{owner_id}.md owns this directory",
            )
        elif ctx.references / doc.path not in {link.resolved for link in owner.links}:
            yield Problem(
                "subfile",
                doc.path.as_posix(),
                f"{kind}/{owner_id}.md does not link this sub-file",
            )


def _index(ctx: _Context, *_: object) -> Iterable[Problem]:
    rendered = render_blocks(ctx.kb)
    for entry, blocks in ENTRY_BLOCKS.items():
        doc = ctx.by_path.get(ctx.references / entry)
        if doc is None:
            yield Problem("index", entry, "the entry file is missing")
            continue
        for block in blocks:
            current = read_block(doc.text, block)
            if current is None:
                yield Problem("index", entry, f"no generated block {block!r}")
            elif current != rendered[(entry, block)]:
                yield Problem(
                    "index",
                    entry,
                    f"the block {block!r} is stale; run"
                    " `uv run python -m kiln_harness_tuner.knowledge build`",
                )


def _stray(ctx: _Context, *_: object) -> Iterable[Problem]:
    for path in ctx.kb.other_files:
        if _in_a_subfile_directory(path):
            continue  # inside a sub-file directory: the rule `subfile`
        yield Problem(
            "stray",
            path.as_posix(),
            "this file is outside the layout: an entry file, or"
            f" {'/, '.join(COMPONENT_DIRS)}/<id>.md and their sub-files",
        )


RULES: tuple[Rule, ...] = (
    _front_matter,
    _id,
    _sections,
    _placeholder,
    _size,
    _shape,
    _link,
    _reachable,
    _depth,
    _technique_lever_ids,
    _lever_ops,
    _technique_lever_links,
    _technique_stance,
    _technique_priors,
    _playbook_priors,
    _alternatives,
    _playbook_steps,
    _smell_remedies,
    _smell_detectors,
    _subfile,
    _index,
    _stray,
)
