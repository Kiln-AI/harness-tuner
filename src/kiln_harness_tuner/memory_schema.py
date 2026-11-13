"""The memory store's record schema — what each kind of record looks like.

The memory API has four fields and nothing else structured: `overview` (one
line, ≤280 chars), `content` (≤4000 chars, nullable), `tags` (exact-match
filterable) and `scope`. Everything the loop keeps in memory is encoded into
those four by convention. This module IS that convention, in one place and in
both directions:

- **parse**: a `MemoryRecord` → a typed view per kind (`IssueView`,
  `ExperimentView`, `BaselineView`; open unknowns parse their content block
  through `OpenUnknownContent`), each with the record's lifecycle state as an
  enum and every code-read field as an attribute;
- **render**: the code-owned text blocks a writer emits (`FixOpening` /
  `MergeOpening`, `SetupHead`, `OpenUnknownContent`, the issue tail notes in
  `IssueNote`, the lifecycle prefixes and suffixes).

No I/O lives here. `memory.py` owns WHEN a record is written and the cap
clamps that keep a write non-fatal; this module owns WHAT the record says.
`setup.py` reads views through here and parse nothing
themselves.

**Parsing is lenient, rendering is strict.** The store holds legacy records,
hand-edited records and agent-authored prose, and none of it may raise: a
field that cannot be read is `None`, a line that is not `key: value` is
skipped, and an experiment's post-verdict body is kept whole as `content`
because the analyzer writes it and code reads only the few lines listed on
`ExperimentView`. Rendering, by contrast, is the frozen contract the tests
string-assert (docs/architecture.md section 8; the memory skill's conventions).

Layout of a record, by kind:

    issue        overview  [<STATE>: ]issue: <one line>
                 tags      issue · p<0-3> · eval_<id>…
                 content   <setup head (SetupHead)>
                           — setup wrote everything above; … —
                           - <loop tail notes (IssueNote), append-only>
    experiment   overview  EXP <MMDD>-<item> (<technique>): <hypothesis> — RUNNING
                           (moves: EXPERIMENT_TRANSITIONS; the technique is
                           bounded to TECHNIQUE_MAX_CHARS, and this shape
                           with no suffix reads UNKNOWN)
                           → <analyzer verdict row>[ — REFUTED]
                             [ — PROMOTED <MM-DD>][ — REJECTED AT VAL <MM-DD>]
                             [ — HELD (constraint <id>) <MM-DD>]
                           → … — FAILED (infra: <class>) | — ABANDONED (<why>)
                             | — DEAD END (<why>)
                             | — NOT LANDED (<k>/<n> targets fixed)
                             | — NOT LANDED (<k>/<n> fixed, <b>/<g> guards
                               broke)  (the discriminating block)
                             | — NOT LANDED (stopped at n=<n> by <rule>:
                               <numbers>)  (any other engine stop)
                             | — CAPPED (capped at $<x> after <n> items:
                               inconclusive — net +<a>/−<b> so far)  (the
                               spending cap)
                 tags      experiment
                 content   <opening (FixOpening | MergeOpening)>
                           + `rc:` / `change:` when the build lands
                           → analyzer body + carried CARRIED_KEYS lines
                           + `- rejected at the val gate …` after a lost val gate
    constraint   overview  OPEN: <question>          (an open unknown)
                 content   OpenUnknownContent (five lines)
    baseline     tags      baseline · rc_<id>        (the pointer is the tag)
                 content   one rc id per line, newest first (the promotion chain)
    val          overview  the numberless constant; content one line per promotion
    screen       overview  SCREEN SURVIVOR: <label> — <passed>/<judged> vs
                           baseline <b> | SCREEN DEAD END: <label> — <reason>
                           | SCREEN CAPPED: <label> — <capped line>
                 tags      lesson · screen  |  dead_end · screen
                 content   `key: value` lines (`memory.screen_record_pairs`):
                           candidate, run config, label, overrides, parent rc,
                           round, round no, items, screened on evals,
                           verdict, reason, quality, measured, run, date,
                           baseline rc, result (the result as one JSON line)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# ---- caps and the two text helpers every render shares ----

# The server's caps (memory skill): overview 280, content 4000. The server
# answers 422 (`string_too_long`) on an over-cap POST/PATCH.
OVERVIEW_MAX_CHARS = 280
CONTENT_MAX_CHARS = 4000

# The clamp markers `memory.py` writes into over-cap content. Listed here
# because a record may CONTAIN them, and `baseline_lineage` must skip them —
# reading a marker this package wrote back as an rc id is a bug.
TRUNCATION_MARKER = "[… truncated …]"
HISTORY_TRUNCATION_MARKER = "[… earlier history truncated …]"
MARKER_FIRST_TOKENS = frozenset(
    marker.split()[0] for marker in (TRUNCATION_MARKER, HISTORY_TRUNCATION_MARKER)
)


def clamp(text: str, limit: int) -> str:
    """`text` cut to `limit` chars with a trailing `…`; a non-positive budget
    yields nothing, never a negative slice keeping nearly the whole text."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def one_line(text: str) -> str:
    """Whitespace-collapsed to ONE line. The API's `overview` contract is
    "no newlines" (a stray one is a fatal 422), so every agent-authored string
    entering an overview or a one-line content field comes through here."""
    return " ".join(text.split())


# ---- wire shapes ----


@dataclass
class MemorySummary:
    """One listing row (live shape) — a SUMMARY: it has
    `content_length`, never `content`. Deliberately a distinct type from
    `MemoryRecord` so a consumer that needs content cannot silently read a
    missing field — it must hydrate via `/by_ids`."""

    id: str
    overview: str
    tags: list[str]
    scope: str
    content_length: int

    @classmethod
    def from_listing(cls, raw: dict[str, Any]) -> MemorySummary:
        return cls(
            id=str(raw["id"]),
            overview=raw.get("overview") or "",
            tags=list(raw.get("tags") or []),
            scope=raw.get("scope") or "",
            content_length=int(raw.get("content_length") or 0),
        )


@dataclass
class MemoryRecord:
    """A FULL record (with `content`) — the `/by_ids` shape (bare array of
    these; `content` is null when empty)."""

    id: str
    overview: str
    content: str | None
    tags: list[str]
    scope: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> MemoryRecord:
        return cls(
            id=str(raw["id"]),
            overview=raw.get("overview") or "",
            content=raw.get("content"),
            tags=list(raw.get("tags") or []),
            scope=raw.get("scope") or "",
        )


# A probe's answer is a `lesson` record that also carries this tag, so a
# planner can list "what has been probed" in one call.
PROBE_TAG = "probe"
# Every screening result carries this tag — a survivor as a
# `lesson`, a dead end as a `dead_end` — so a run starts from what earlier
# runs screened, with one tag list.
SCREEN_TAG = "screen"

# ---- record kinds ----


class RecordKind(StrEnum):
    """What a record IS, read from its tags. The memory skill's tag
    vocabulary; `OTHER` is anything tagged outside it."""

    ISSUE = "issue"
    DEAD_END = "dead_end"
    EXPERIMENT = "experiment"
    CONSTRAINT = "constraint"
    BASELINE = "baseline"
    VAL = "val"
    LESSON = "lesson"
    OTHER = "other"


# In precedence order: a record tagged both `issue` and `lesson` is an issue
# (the lifecycle record wins over the annotation).
KIND_PRECEDENCE: tuple[RecordKind, ...] = (
    RecordKind.ISSUE,
    RecordKind.DEAD_END,
    RecordKind.EXPERIMENT,
    RecordKind.CONSTRAINT,
    RecordKind.BASELINE,
    RecordKind.VAL,
    RecordKind.LESSON,
)


def record_kind(tags: Iterable[str], overview: str = "") -> RecordKind:
    """The kind a record's tags name. An `OPEN: ` overview with no kind tag
    is still a constraint (an open unknown written before its tag)."""
    tag_set = set(tags)
    for kind in KIND_PRECEDENCE:
        if kind.value in tag_set:
            return kind
    if overview.startswith(OPEN_UNKNOWN_PREFIX):
        return RecordKind.CONSTRAINT
    return RecordKind.OTHER


class RecordKindError(Exception):
    """A view or a writer was handed a record of the wrong kind — an issue
    writer given an experiment id, or the reverse. Raised before anything is
    written."""


def _require_kind(record: MemoryRecord, kind: RecordKind) -> None:
    if kind.value not in record.tags:
        raise RecordKindError(
            f"memory {record.id} is not tagged `{kind.value}` (tags: "
            f"{record.tags}) — refusing to read it as one"
        )


# ---- faceted tags: the one structured slot a record has ----

# The `baseline` record's run-config id lives in a FACETED TAG, never in
# prose: `rc_<id>`. Tags are the only exact-match filterable field, so an id
# that code must read back goes in one.
BASELINE_RC_TAG_PREFIX = "rc_"
# The issue the baseline audit writes (`generality.audit_baseline`)
# carries this tag and one `audited_rc_<id>` tag naming the run config whose
# own text it audited. Not `rc_<id>`: that prefix is the baseline pointer's.
BASELINE_AUDIT_TAG = "baseline_audit"
# The issue for eval text the baseline copies (judge prompts,
# eval configs, specs, eval descriptions). Its own tag, so each half of the
# audit runs once per baseline on its own.
BASELINE_JUDGE_COPY_TAG = "baseline_judge_copy"
AUDITED_RC_TAG_PREFIX = "audited_rc_"
# The machine binding between an eval and its issue memories (`eval_<eval_id>`).
# Parsed for NUMERIC ids only — Kiln ids are numeric everywhere, and reading a
# human `eval_…` word tag as a binding would flag a live issue UNMEASURABLE the
# moment no such eval existed.
EVAL_TAG_PREFIX = "eval_"
_EVAL_TAG_RE = re.compile(rf"^{EVAL_TAG_PREFIX}(?P<id>\d{{6,}})$")
# Priority is a composable tag `p0`–`p3`, source-owned (`spec.priority`).
_PRIORITY_TAG_RE = re.compile(r"^p(?P<n>[0-3])$")
# No source priority (no Spec, or a repaired issue nobody ever prioritized).
# p1 is the middle of the road: high enough that the planner sees it, low
# enough that it never outranks a human `p0`.
DEFAULT_ISSUE_PRIORITY = 1


def baseline_rc_tag(rc_id: str) -> str:
    return f"{BASELINE_RC_TAG_PREFIX}{rc_id}"


def rc_ids_from_tags(tags: Iterable[str]) -> list[str]:
    """The run-config ids a `baseline` record's tags name, in tag order.
    Prefix-strip, not a regex — and empty suffixes (a bare `"rc_"`) are
    dropped, since an empty id would compose API paths that 404."""
    return [
        stripped
        for tag in tags
        if tag.startswith(BASELINE_RC_TAG_PREFIX)
        and (stripped := tag[len(BASELINE_RC_TAG_PREFIX) :])
    ]


def tags_with_baseline_rc(tags: Iterable[str], rc_id: str) -> list[str]:
    """The pointer MOVES: every existing `rc_` tag is replaced by the new
    one, in place, and every other tag survives. Two rc tags would be an
    ambiguous pointer, which the baseline read refuses to resolve."""
    new = baseline_rc_tag(rc_id)
    replaced: list[str] = []
    for tag in tags:
        if not tag.startswith(BASELINE_RC_TAG_PREFIX):
            replaced.append(tag)
        elif new not in replaced:
            replaced.append(new)
    return replaced if new in replaced else [*replaced, new]


def eval_tag(eval_id: str) -> str:
    return f"{EVAL_TAG_PREFIX}{eval_id}"


def eval_ids_from_tags(tags: Iterable[str]) -> frozenset[str]:
    """The eval ids an issue binds to. One issue may carry several — e.g. an
    issue found on eval X that later got a narrower eval Y."""
    return frozenset(
        match["id"]
        for match in (_EVAL_TAG_RE.match(tag) for tag in tags)
        if match is not None
    )


def tags_with_eval(tags: Iterable[str], eval_id: str) -> list[str]:
    """Additive — an issue keeps every binding it already had."""
    tags = list(tags)
    tag = eval_tag(eval_id)
    return tags if tag in tags else [*tags, tag]


def priority_tag(priority: int) -> str:
    return f"p{priority}"


def priority_from_tags(tags: Iterable[str]) -> int | None:
    for tag in tags:
        match = _PRIORITY_TAG_RE.match(tag)
        if match:
            return int(match["n"])
    return None


def tags_with_priority(tags: Iterable[str], priority: int) -> list[str]:
    """Priority is source-owned: a changed
    `spec.priority` REPLACES the tag, in place, and one is always present —
    never removed outright, and a duplicate pair never survives."""
    new = priority_tag(priority)
    replaced: list[str] = []
    for tag in tags:
        if not _PRIORITY_TAG_RE.match(tag):
            replaced.append(tag)
        elif new not in replaced:
            replaced.append(new)
    return replaced if new in replaced else [*replaced, new]


# ---- the `key: value` content-line codec ----
#
# Every code-read content field is one line of the form `<key>: <value>`.
# One codec parses and renders all of them; no reader carries its own regex.

_KV_LINE_RE = re.compile(r"^(?P<key>[^:\n]+?):[ \t]*(?P<value>[^\n]*?)[ \t]*$")


def kv_line(line: str) -> tuple[str, str] | None:
    """`(key, value)` when the line is `key: value`, else None. The value is
    whitespace-trimmed and may be empty."""
    match = _KV_LINE_RE.match(line)
    return (match["key"], match["value"]) if match is not None else None


def kv_lines(content: str | None) -> list[tuple[str, str]]:
    """Every `key: value` line in order; lines of any other shape are
    skipped, never an error — agent prose sits between them."""
    if not content:
        return []
    return [pair for pair in map(kv_line, content.splitlines()) if pair is not None]


def kv_value(content: str | None, key: str) -> str | None:
    """The value of the FIRST `<key>: <value>` line whose key matches
    exactly, or None — an empty value counts as absent."""
    for found, value in kv_lines(content):
        if found == key and value:
            return value
    return None


def render_kv(pairs: Iterable[tuple[str, str]]) -> str:
    """`key: value` lines, one per pair, in order."""
    return "\n".join(f"{key}: {value}" for key, value in pairs)


# ---- issue lifecycle: the overview PREFIX ----


class IssueState(StrEnum):
    """Issue lifecycle, carried as an overview prefix — never a tag (the
    memory skill's convention). `OPEN` is the absence of a prefix."""

    OPEN = "open"
    FIXED = "fixed"
    WONT_FIX = "wont_fix"
    UNMEASURED = "unmeasured"
    UNMEASURABLE = "unmeasurable"

    @property
    def prefix(self) -> str:
        return _ISSUE_PREFIX[self]


_ISSUE_PREFIX: dict[IssueState, str] = {
    IssueState.OPEN: "",
    IssueState.FIXED: "FIXED: ",
    IssueState.WONT_FIX: "WONT_FIX: ",
    IssueState.UNMEASURED: "UNMEASURED: ",
    IssueState.UNMEASURABLE: "UNMEASURABLE: ",
}
_PREFIXED_ISSUE_STATES = tuple(s for s in IssueState if s.prefix)
# States the loop has already DECIDED. Sync never overwrites one (it "never
# closes an issue on its own" — and never un-closes one), and the writers refuse
# to move out of one.
ISSUE_TERMINAL_STATES = frozenset({IssueState.FIXED, IssueState.WONT_FIX})


def issue_state(overview: str) -> IssueState:
    for state in _PREFIXED_ISSUE_STATES:
        if overview.startswith(state.prefix):
            return state
    return IssueState.OPEN


def state_prefix_run(overview: str) -> str:
    """Every lifecycle prefix an overview leads with, in order — exactly the
    string `set_state_prefix` would strip. `refresh_issue` re-applies it
    verbatim, so a rubric rewrite cannot change lifecycle state even on a
    stacked legacy overview (`FIXED: UNMEASURED: …`, the shape an old
    `mark_fixed` wrote) where re-deriving from `issue_state`'s single answer
    would silently drop the second prefix."""
    run = ""
    rest = overview
    while True:
        for state in _PREFIXED_ISSUE_STATES:
            if rest.startswith(state.prefix):
                run += state.prefix
                rest = rest[len(state.prefix) :]
                break
        else:
            return run


def set_state_prefix(overview: str, state: IssueState = IssueState.OPEN) -> str:
    """Replace whatever lifecycle prefix an overview carries with `state`'s
    (none for OPEN). The single owner of that string surgery — strips the
    whole run, so an already-stacked legacy overview collapses to one state.
    Writers that must clear only ONE prefix say so explicitly."""
    return f"{state.prefix}{overview[len(state_prefix_run(overview)) :]}"


# `[<prefixes>]issue: <one line>` — what every issue overview reads after
# its lifecycle prefix (or a stacked run of them, re-applied verbatim by
# `refresh_issue`).
ISSUE_OVERVIEW_LEAD = "issue: "


# ---- issue content: the setup-owned head and the loop-owned tail ----
#
# `content` on an issue record has three writers — setup (the description +
# metadata head), the loop's code writers (all appenders), and loop agents
# PATCHing findings. One delimiter line splits it: everything ABOVE is
# setup-owned and replaced wholesale on every refresh; the delimiter and
# everything BELOW is loop-owned, append-only, preserved byte-for-byte and
# never parsed for meaning.
SETUP_DELIMITER = "— setup wrote everything above; lines below belong to the loop —"
# The setup head's own budget: the description is clamped so the three
# metadata lines always fit, and the loop keeps at least the reserve below.
# (Budget arithmetic against CONTENT_MAX_CHARS is asserted in the tests.)
SETUP_HEAD_MAX_CHARS = 1800
SETUP_TAIL_RESERVE_CHARS = 2000
# Per-line cap on the head's three metadata lines. Their fields (eval name,
# config type, spec id) come straight off the API and are otherwise
# unbounded; capping each line is what makes `SETUP_HEAD_MAX_CHARS` a real
# guarantee rather than a description-only one.
SETUP_HEAD_LINE_MAX_CHARS = 200
# Matched as a WHOLE LINE: agent prose that quotes the sentence mid-line must
# not be mistaken for the boundary. Earlier versions wrote one word between the
# dash and "setup", so the pattern allows one word there, and records that
# they wrote still split.
_SETUP_DELIMITER_PATTERN = (
    r"— (?:\S+ )?setup wrote everything above; lines below belong to the loop —"
)
_SETUP_DELIMITER_RE = re.compile(rf"^{_SETUP_DELIMITER_PATTERN}$", re.MULTILINE)
_SETUP_DELIMITER_LINE_RE = re.compile(_SETUP_DELIMITER_PATTERN)
_HEAD_KEYS = ("eval", "priority", "status")
_EVAL_LINE_RE = re.compile(r"^(?P<id>\S+) \((?P<name>.*), (?P<kind>[^,()]*)\)$")
_PRIORITY_LINE_RE = re.compile(r"^p(?P<n>\d) \(source: (?P<source>.*)\)$")
_STATUS_STATE_RE = re.compile(r"^(?P<state>\S+) at sync\b")
_STATUS_FP_RE = re.compile(r"\bfp: (?P<fp>[0-9a-f]{4,})\b")
_STATUS_SYNCED_RE = re.compile(r"\bsynced (?P<date>\S+)")


def split_owned(content: str | None) -> tuple[str | None, str]:
    """`(setup_head, loop_tail)`. No delimiter ⇒ `(None, whole content)`: a
    legacy or agent-authored record is treated as ENTIRELY loop-owned, so
    setup prepends its head and touches nothing else.

    Sliced on the match offsets rather than `splitlines()`/`join`, so the
    loop tail comes back byte-for-byte — it is the analyst's prose and
    promotion history, and setup never parses it for meaning."""
    if not content:
        return None, content or ""
    match = _SETUP_DELIMITER_RE.search(content)
    if match is None:
        return None, content
    return content[: match.start()].rstrip("\n"), content[match.end() :].lstrip("\n")


def join_owned(head: str, tail: str) -> str:
    """The inverse of `split_owned` — idempotent across a round trip (both
    sides strip the same separating newlines)."""
    return "\n".join(p for p in (head, SETUP_DELIMITER, tail.lstrip("\n")) if p)


@dataclass(frozen=True)
class SetupHead:
    """The setup-owned block above the delimiter:

        <description>
        eval: <eval id> (<eval name>, <config kind>)
        priority: p<n> (source: spec <spec id> | default)
        status: <state> at sync · fp: <hex> · synced <YYYY-MM-DD>

    `state_at_sync` is a SNAPSHOT: every lifecycle writer moves the overview
    prefix without touching the head, so the prefix — not this line — is the
    authoritative state. `fingerprint` is what setup reads back to decide
    whether an eval's source drifted."""

    description: str
    eval_id: str | None
    eval_name: str | None
    config_kind: str | None
    priority: int | None
    priority_source: str | None
    state_at_sync: str | None
    fingerprint: str | None
    synced: str | None

    @classmethod
    def parse(cls, head: str) -> SetupHead:
        """Lenient: for each of `eval:` / `priority:` / `status:` the LAST
        such line anywhere in the head is the metadata line (render writes
        them last, so a description quoting one of those keys mid-text can
        never answer for setup's, and a stray line after them — a trailing
        blank, an off-contract note — does not hide them). Every field is
        None when its line is absent or malformed."""
        lines = head.splitlines()
        meta_at: dict[str, int] = {}
        for index, line in enumerate(lines):
            pair = kv_line(line)
            if pair is not None and pair[0] in _HEAD_KEYS:
                meta_at[pair[0]] = index
        meta = {
            key: lines[index].partition(":")[2].strip()
            for key, index in meta_at.items()
        }
        taken = set(meta_at.values())
        lines = [line for index, line in enumerate(lines) if index not in taken]
        while lines and not lines[-1].strip():
            lines.pop()
        eval_id = eval_name = config_kind = None
        if (eval_line := meta.get("eval")) is not None:
            if (m := _EVAL_LINE_RE.match(eval_line)) is not None:
                eval_id, eval_name, config_kind = m["id"], m["name"], m["kind"]
            elif eval_line.split():
                eval_id = eval_line.split()[0]
        priority = priority_source = None
        if (priority_line := meta.get("priority")) is not None:
            if (m := _PRIORITY_LINE_RE.match(priority_line)) is not None:
                priority, priority_source = int(m["n"]), m["source"]
        state_at_sync = fingerprint = synced = None
        if (status_line := meta.get("status")) is not None:
            if (m := _STATUS_STATE_RE.match(status_line)) is not None:
                state_at_sync = m["state"]
            if (m := _STATUS_FP_RE.search(status_line)) is not None:
                fingerprint = m["fp"]
            if (m := _STATUS_SYNCED_RE.search(status_line)) is not None:
                synced = m["date"]
        return cls(
            description="\n".join(lines),
            eval_id=eval_id,
            eval_name=eval_name,
            config_kind=config_kind,
            priority=priority,
            priority_source=priority_source,
            state_at_sync=state_at_sync,
            fingerprint=fingerprint,
            synced=synced,
        )

    def render(self) -> str:
        """The frozen head, budgeted: each metadata line is capped at
        `SETUP_HEAD_LINE_MAX_CHARS` (its fields come straight off the API and
        are otherwise unbounded), and the description is clamped to what
        those three lines leave of `SETUP_HEAD_MAX_CHARS` — so the head is
        under budget WITHOUT a trailing whole-head clamp, which would truncate
        from the end and eat the very `status:` line the budget protects.

        Any description line that would read as the ownership delimiter is
        dropped: `split_owned` cuts at the FIRST such line, and a description
        carrying one would hand the rest of the head to the loop region."""
        metadata = "\n".join(
            clamp(one_line(line), SETUP_HEAD_LINE_MAX_CHARS)
            for line in (
                f"eval: {self.eval_id} ({self.eval_name}, {self.config_kind})",
                f"priority: p{self.priority} (source: {self.priority_source})",
                f"status: {self.state_at_sync} at sync · fp: {self.fingerprint} · "
                f"synced {self.synced}",
            )
        )
        description = "\n".join(
            line
            for line in self.description.splitlines()
            if not _SETUP_DELIMITER_LINE_RE.fullmatch(line)
        )
        description = clamp(description, SETUP_HEAD_MAX_CHARS - len(metadata) - 1)
        return f"{description}\n{metadata}" if description else metadata


class IssueNote:
    """The frozen one-liners the loop appends to an issue's tail (see
    docs/architecture.md section 8). Metric-free by construction: none of
    them takes a number, so no val figure can ride into a train-visible record. Every
    line starts with `- `; the tail is never parsed for meaning."""

    @staticmethod
    def fixed(date: str, citation: str) -> str:
        return f"- fixed {date} by {citation}"

    @staticmethod
    def reopened(rc_id: str) -> str:
        return f"- reopened when promoting baseline {rc_id}. Issue repros on {rc_id}."

    @staticmethod
    def repros(rc_id: str) -> str:
        return f"- repros on baseline {rc_id} (noted at promotion)."

    @staticmethod
    def partial(rc_id: str) -> str:
        return f"- landed in merge {rc_id} — reduced, not closed; still repros."

    @staticmethod
    def wont_fix(date: str, reason: str) -> str:
        return f"- WONT_FIX ({date}): {reason}"

    @staticmethod
    def eval_built(eval_id: str, date: str) -> str:
        """The durable eval↔issue link, the human-readable twin of
        the `eval_<id>` tag."""
        return f"- eval: {eval_id} (built {date}, reproduced)"

    @staticmethod
    def eval_linked(eval_id: str, date: str) -> str:
        return f"- eval: {eval_id} (linked by kiln-harness-tuner setup {date})"

    @staticmethod
    def resynced(date: str) -> str:
        return f"- eval rubric changed; description resynced {date}"

    @staticmethod
    def unmeasurable(cause: str, date: str) -> str:
        return f"- {cause} (noted {date})"

    @staticmethod
    def baseline_measurement(
        *, reproduced: bool, rc_id: str, date: str, summary: str
    ) -> str:
        verdict = "reproduced" if reproduced else "not reproduced"
        return f"- {verdict} on baseline {rc_id} {date} ({summary})"


@dataclass(frozen=True)
class IssueView:
    """A task-scope `issue` record, as the loop and setup read it."""

    id: str
    overview: str
    tags: list[str]
    content: str | None
    state: IssueState
    priority: int | None
    bound_eval_ids: frozenset[str]  # the `eval_<id>` tags
    setup_head: SetupHead | None  # None ⇒ loop-discovered, no setup-owned head
    loop_tail: str  # everything below the delimiter (or all of it), verbatim

    @classmethod
    def from_record(cls, record: MemoryRecord) -> IssueView:
        _require_kind(record, RecordKind.ISSUE)
        head, tail = split_owned(record.content)
        return cls(
            id=record.id,
            overview=record.overview,
            tags=list(record.tags),
            content=record.content,
            state=issue_state(record.overview),
            priority=priority_from_tags(record.tags),
            bound_eval_ids=eval_ids_from_tags(record.tags),
            setup_head=None if head is None else SetupHead.parse(head),
            loop_tail=tail,
        )

    @property
    def is_setup_owned(self) -> bool:
        """An issue the LOOP discovered and bound to an eval has no
        setup-owned head — its prose is the analyst's, and refresh never
        touches it."""
        return self.setup_head is not None

    @property
    def fingerprint(self) -> str | None:
        return None if self.setup_head is None else self.setup_head.fingerprint


# ---- experiment lifecycle: the overview SUFFIX ----


class ExperimentState(StrEnum):
    """The state of an experiment record, carried as an overview suffix.
    `VERDICT` is the absence of a suffix: the analyzer replaced the
    `— RUNNING` overview with its numbers row. `EXPERIMENT_TRANSITIONS`
    below says which moves the writers accept."""

    RUNNING = "running"
    VERDICT = "verdict"
    REFUTED = "refuted"
    FAILED = "failed"
    ABANDONED = "abandoned"
    DEAD_END = "dead_end"
    NOT_LANDED = "not_landed"  # Trialled on its target items,
    #   left some failing — no verdict, no train run; another build may try
    CAPPED = "capped"  # Its measurement reached its spending
    #   cap — inconclusive; the rows are kept and a larger cap may go on
    UNKNOWN = "unknown"  # Read only, never written. The overview
    #   has the opening's shape (`EXP <id> (<technique>…`) but no state
    #   suffix: the record lost its state. It is NOT read as a verdict. It
    #   takes the forward moves of RUNNING (see `experiment_state`).


# One row per state: the suffix template the writer renders. The regex the
# reader matches is DERIVED from the same text, so the two cannot drift.
# `{detail}` is the infra error class (FAILED) or the reason (ABANDONED /
# DEAD END), clamped to `SUFFIX_DETAIL_MAX_CHARS` on render.
_SUFFIX_TEMPLATE: dict[ExperimentState, str] = {
    ExperimentState.RUNNING: " — RUNNING",
    ExperimentState.REFUTED: " — REFUTED",
    ExperimentState.FAILED: " — FAILED (infra: {detail})",
    ExperimentState.ABANDONED: " — ABANDONED ({detail})",
    ExperimentState.DEAD_END: " — DEAD END ({detail})",
    ExperimentState.NOT_LANDED: " — NOT LANDED ({detail})",
    ExperimentState.CAPPED: " — CAPPED ({detail})",
    ExperimentState.VERDICT: "",
    ExperimentState.UNKNOWN: "",
}
SUFFIX_DETAIL_MAX_CHARS = 120


def _suffix_regex(template: str) -> re.Pattern[str]:
    head, has_detail, tail = template.partition("{detail}")
    pattern = re.escape(head.lstrip())
    if has_detail:
        pattern += ".*" + re.escape(tail)
    return re.compile(r"\s*" + pattern + "$")


_STATE_SUFFIX_RE: dict[ExperimentState, re.Pattern[str]] = {
    state: _suffix_regex(template)
    for state, template in _SUFFIX_TEMPLATE.items()
    if template
}
# The moves a writer accepts (`MemoryWriter._move_experiment`). RUNNING and
# FAILED mean "no result yet" and are REPLACED by the next state; a verdict,
# a refutation, an abandonment or a dead end is a result and is final. The
# two gate stamps are not states: they are appended to a result (see
# STAMPABLE_STATES) and never removed.
_FORWARD_MOVES = frozenset(
    {
        ExperimentState.VERDICT,
        ExperimentState.REFUTED,
        ExperimentState.FAILED,
        ExperimentState.ABANDONED,
        ExperimentState.DEAD_END,
        ExperimentState.NOT_LANDED,
        ExperimentState.CAPPED,
    }
)
EXPERIMENT_TRANSITIONS: dict[ExperimentState, frozenset[ExperimentState]] = {
    ExperimentState.RUNNING: _FORWARD_MOVES,
    # A record whose state cannot be read is not a result. It
    #   takes the moves of RUNNING, so the arm that owns it can still record
    #   its build and its outcome. It is not stampable: the gate stamps a
    #   verdict, and `record_verdict` writes one first.
    ExperimentState.UNKNOWN: _FORWARD_MOVES,
    ExperimentState.FAILED: frozenset({ExperimentState.ABANDONED}),
    ExperimentState.NOT_LANDED: frozenset(),
    ExperimentState.CAPPED: frozenset(),
    ExperimentState.VERDICT: frozenset(),
    ExperimentState.REFUTED: frozenset(),
    ExperimentState.ABANDONED: frozenset(),
    ExperimentState.DEAD_END: frozenset(),
}
STAMPABLE_STATES = frozenset({ExperimentState.VERDICT, ExperimentState.REFUTED})
# The two stamps the val gate appends AFTER the state: `— PROMOTED <MM-DD>`
# and its twin `— REJECTED AT VAL <MM-DD>` (without it a loser's
# overview still reads success-shaped and a memory-first planner re-promotes
# it). Matched at the end so a second gate pass can't double-stamp.
_PROMOTED_STAMP_RE = re.compile(r"\s+— PROMOTED \d{2}-\d{2}$")
_REJECTED_STAMP_RE = re.compile(r"\s+— REJECTED AT VAL \d{2}-\d{2}$")
# The third stamp: the gate did not judge the candidate on its
# numbers — it escalated on an OPEN constraint. `REJECTED AT VAL` would make
# the rc unsubmittable for good; `HELD` names the constraint, and once a
# human answers it the planner resubmits the SAME rc unchanged
# (`FixWork.existing_rc_id`), val rows and all. A val candidate must not stay
# stranded on a question that a later run's guidance answers.
_HELD_STAMP_RE = re.compile(r"\s+— HELD \(constraint (?P<id>\S+)\) \d{2}-\d{2}$")
# `EXP 0723-w4-2 (prompt): <hypothesis> — RUNNING` — the label the record
# opens with (run-start date + item id) and its technique. The
# analyzer's verdict keeps the label and drops the `(technique)`.
EXP_LABEL_LEAD = "EXP "
_EXP_LABEL_RE = re.compile(rf"^{re.escape(EXP_LABEL_LEAD)}(?P<id>\S+)")
_EXP_TECHNIQUE_RE = re.compile(
    rf"^{re.escape(EXP_LABEL_LEAD)}\S+ \((?P<technique>[^)]+)\): "
)
# The opening's shape, read without the `): ` that a cut overview can lose:
# the label, one space and an open parenthesis. Only `open_experiment` writes
# it. `record_verdict` removes a `(technique)` from the analyzer's row
# (`verdict_overview`), so a verdict never has this shape.
_EXP_OPENING_RE = re.compile(rf"^{re.escape(EXP_LABEL_LEAD)}\S+ \(")
_EXP_VERDICT_TECHNIQUE_RE = re.compile(
    rf"^(?P<label>{re.escape(EXP_LABEL_LEAD)}\S+) \([^)]*\)(?=:)"
)
MERGE_TECHNIQUE = "merge"
# A merge opening's ingredient line: `- ingredient: <memory id> (rc <rc id>)`.
_INGREDIENT_LINE_RE = re.compile(r"^- ingredient: (?P<mem>\S+) \(rc (?P<rc>\S+)\)$")


def ingredient_line(memory_id: str, rc_id: str) -> str:
    return f"- ingredient: {memory_id} (rc {rc_id})"


# The val-gate rejection's content line — metric-free, pointing at the run
# report for the numbers. Its stable head is the skip-if-present
# key: a PREFIX, because the line carries the date and a crash-retry
# crossing midnight would otherwise append a second copy.
REJECTION_LINE_PREFIX = "- rejected at the val gate "
# The HELD stamp's content line — same prefix discipline.
HELD_LINE_PREFIX = "- held at the val gate "
# The content line for a candidate the gate's integrity check (a)
# BLOCKED — it copies eval text. Same prefix discipline. It names the copied
# phrase and the eval object, so a planner can remove the phrase before it
# builds on the run config.
GATE_BLOCK_LINE_PREFIX = "- blocked at the val gate "


def experiment_suffix(state: ExperimentState, detail: str = "") -> str:
    """The suffix a state writes (see `_SUFFIX_TEMPLATE`)."""
    return _SUFFIX_TEMPLATE[state].format(detail=detail[:SUFFIX_DETAIL_MAX_CHARS])


def promoted_stamp(date: str) -> str:
    return f" — PROMOTED {date}"


def rejected_stamp(date: str) -> str:
    return f" — REJECTED AT VAL {date}"


def held_stamp(date: str, constraint_id: str) -> str:
    return f" — HELD (constraint {constraint_id}) {date}"


def experiment_state(overview: str) -> ExperimentState:
    """The state the overview's suffix names, read past any gate stamps.

    No suffix is a VERDICT only when the overview is not in the opening's
    shape. An opening with no suffix (`EXP <id> (<technique>…`) lost its
    state: it is UNKNOWN. Before, it was read as a verdict, the
    arm's own later writes were refused, and the refusal ended the run."""
    base = _strip_stamps(overview)
    for state, pattern in _STATE_SUFFIX_RE.items():
        if pattern.search(base):
            return state
    if _EXP_OPENING_RE.match(base):
        return ExperimentState.UNKNOWN
    return ExperimentState.VERDICT


def verdict_overview(overview: str) -> str:
    """The analyzer's overview with no `(technique)` after its label. The verdict "keeps
    the label and drops the technique", and code makes it so: the opening's shape with
    no suffix reads UNKNOWN."""
    return _EXP_VERDICT_TECHNIQUE_RE.sub(r"\g<label>", overview, count=1)


def _strip_stamps(overview: str) -> str:
    while True:
        stripped = _PROMOTED_STAMP_RE.sub("", overview)
        stripped = _REJECTED_STAMP_RE.sub("", stripped)
        stripped = _HELD_STAMP_RE.sub("", stripped)
        if stripped == overview:
            return overview
        overview = stripped


def is_promoted(overview: str) -> bool:
    return _PROMOTED_STAMP_RE.search(overview) is not None


def is_rejected_at_val(overview: str) -> bool:
    return _REJECTED_STAMP_RE.search(overview) is not None


def held_constraint(overview: str) -> str | None:
    """The OPEN constraint id a `— HELD` stamp names, or None."""
    match = _HELD_STAMP_RE.search(overview)
    return match["id"] if match is not None else None


def is_stamped(overview: str) -> bool:
    """Any gate stamp — the record is a result the gate has ruled on."""
    return (
        is_promoted(overview)
        or is_rejected_at_val(overview)
        or held_constraint(overview) is not None
    )


def strip_state_suffix(overview: str, state: ExperimentState) -> str:
    """The overview with `state`'s suffix removed — what the next state is
    written over. Only meaningful on an unstamped overview (a stamped one
    is final; `EXPERIMENT_TRANSITIONS` gives it no moves)."""
    if state in (ExperimentState.VERDICT, ExperimentState.UNKNOWN):
        return overview
    return _STATE_SUFFIX_RE[state].sub("", overview)


def experiment_label_for(run_prefix: str, item_id: str) -> str:
    """`"EXP <MMDD>-<item id>"` — the run-start date keeps overviews from
    colliding across runs."""
    return f"{EXP_LABEL_LEAD}{run_prefix}-{item_id}"


def _experiment_label(overview: str) -> str | None:
    """The label an overview opened with, or None when code did not open it."""
    match = _EXP_LABEL_RE.match(overview)
    return f"{EXP_LABEL_LEAD}{match['id']}" if match is not None else None


def experiment_label_prefix(label: str, technique: str) -> str:
    """`"EXP 0723-w4-2 (prompt): "` — what the opening overview starts with;
    the analyzer's verdict drops the parenthesised technique."""
    return f"{label} ({technique}): "


def rejection_note(date: str, baseline_rc_id: str) -> str:
    return (
        f"{REJECTION_LINE_PREFIX}{date} vs baseline {baseline_rc_id} "
        "— did not beat it; details in the run report."
    )


def has_rejection_note(content: str | None) -> bool:
    return any(
        line.startswith(REJECTION_LINE_PREFIX) for line in (content or "").splitlines()
    )


def held_note(date: str, constraint_id: str) -> str:
    return (
        f"{HELD_LINE_PREFIX}{date} on OPEN constraint {constraint_id} — not judged "
        "on its numbers; resubmit this rc unchanged (existing_rc_id) once answered."
    )


def gate_block_note(date: str, reasons: list[str]) -> str:
    """The block's content line: metric-free, one line, every reason."""
    return (
        f"{GATE_BLOCK_LINE_PREFIX}{date}: "
        + "; ".join(one_line(r) for r in reasons)
        + " — the run config copies eval text; remove the copied phrase before"
        " you build on it or merge it."
    )


def has_held_note(content: str | None) -> bool:
    return any(
        line.startswith(HELD_LINE_PREFIX) for line in (content or "").splitlines()
    )


# The bounds on the planner's free text in an experiment record.
# A live planner gave one arm a `technique` of several hundred characters.
# The label prefix `EXP <id> (<technique>): ` was then longer than the
# overview cap, the cap cut the `— RUNNING` suffix off, the record read back
# as a verdict, and the arm's next write was refused. Every planner value is
# now cut to one line and a bound BEFORE it is rendered, so the head lines
# and the state suffix that code reads back always fit whole. The planner's
# item keeps its full text; only the record is bounded.
TECHNIQUE_MAX_CHARS = 60  # the lever name: `prompt`, `skill`, `tool_config`
HYPOTHESIS_MAX_CHARS = 900
PREDICTED_EFFECT_MAX_CHARS = 300
HEAD_ID_MAX_CHARS = 120  # a run config, eval or memory id; a group name
CONFLICT_FLAGS_MAX_CHARS = 600
CHANGE_MAX_CHARS = 300  # the build's `change:` summary
FINDING_MAX_CHARS = 400  # a `generality:`, `judge copy:` or `reference check:`
#   finding


def head_value(text: str, limit: int) -> str:
    """One planner or agent value for a `key: value` head line: one line,
    cut to `limit`. A newline in a value would start a new line that a
    reader can parse as a different key."""
    return clamp(one_line(text), limit)


def technique_label(technique: str) -> str:
    """The technique as the record carries it, in the overview label and on
    the `technique:` line alike — so the two sources of `lever_attempts` agree."""
    return head_value(technique, TECHNIQUE_MAX_CHARS)


@dataclass(frozen=True)
class FixOpening:
    """The content `open_experiment` writes for a fix arm. The verdict
    REPLACES this block with the analyzer's body, so `record_verdict`
    carries `issue:` / `technique:` / `head_to_head_group:` forward: the
    `lever_attempts` count reads arms per `issue` × `technique`, and the
    head-to-head group must survive the verdict. Each value is bounded on render (see
    `TECHNIQUE_MAX_CHARS`)."""

    hypothesis: str
    predicted_effect: str
    parent_rc_id: str
    eval_id: str
    issue_id: str
    technique: str
    head_to_head_group: str | None = None
    reused_rc_id: str | None = None  # measures a prior run's built rc
    wildcard: bool = False  # the wave's out-of-the-box arm

    def render(self) -> str:
        pairs = [
            ("hypothesis", head_value(self.hypothesis, HYPOTHESIS_MAX_CHARS)),
            (
                "predicted effect",
                head_value(self.predicted_effect, PREDICTED_EFFECT_MAX_CHARS),
            ),
            ("parent rc", head_value(self.parent_rc_id, HEAD_ID_MAX_CHARS)),
            ("eval", head_value(self.eval_id, HEAD_ID_MAX_CHARS)),
            ("issue", head_value(self.issue_id, HEAD_ID_MAX_CHARS)),
            ("technique", technique_label(self.technique)),
        ]
        if self.head_to_head_group:
            pairs.append(
                (
                    "head_to_head_group",
                    head_value(self.head_to_head_group, HEAD_ID_MAX_CHARS),
                )
            )
        if self.reused_rc_id:
            pairs.append(
                ("reused rc", head_value(self.reused_rc_id, HEAD_ID_MAX_CHARS))
            )
        if self.wildcard:
            pairs.append(("wildcard", "yes — chosen for upside, not confidence"))
        return render_kv(pairs)


@dataclass(frozen=True)
class MergeOpening:
    """The content `open_experiment` writes for a merge arm: the
    ingredient ids ARE the label — there is no hypothesis to quote."""

    ingredients: tuple[tuple[str, str], ...]  # (experiment memory id, rc id)
    conflict_flags: tuple[str, ...]
    parent_rc_id: str
    head_to_head_group: str | None = None

    @property
    def label(self) -> str:
        return " + ".join(mem_id for mem_id, _rc in self.ingredients)

    def render(self) -> str:
        lines = [render_kv([("merge of", self.label)])]
        lines += [ingredient_line(mem_id, rc_id) for mem_id, rc_id in self.ingredients]
        tail: list[tuple[str, str]] = []
        if self.conflict_flags:
            flags = "; ".join(self.conflict_flags)
            tail.append(("conflict flags", head_value(flags, CONFLICT_FLAGS_MAX_CHARS)))
        tail.append(("parent rc", head_value(self.parent_rc_id, HEAD_ID_MAX_CHARS)))
        if self.head_to_head_group:
            tail.append(
                (
                    "head_to_head_group",
                    head_value(self.head_to_head_group, HEAD_ID_MAX_CHARS),
                )
            )
        return "\n".join([*lines, render_kv(tail)])


def head_to_head_line(group: str) -> str:
    return render_kv([("head_to_head_group", head_value(group, HEAD_ID_MAX_CHARS))])


# The opening's lines that must survive the verdict (the analyzer's content
# REPLACES the opening): `record_verdict` re-appends any of these the
# analyzer dropped, from the stored view.
CARRIED_KEYS = (
    "issue",
    "technique",
    "rc",
    "reused rc",
    "change",
    "generality",
    "judge copy",
    "reference check",
)
# Every `key: value` line of an experiment record that code reads
# back (`ExperimentView`). The append clamp (`memory._fit_experiment_content`)
# never drops one of these; it drops other lines, oldest first.
EXPERIMENT_HEAD_KEYS = frozenset(
    {
        "hypothesis",
        "predicted effect",
        "parent rc",
        "eval",
        "issue",
        "technique",
        "head_to_head_group",
        "reused rc",
        "wildcard",
        "merge of",
        "conflict flags",
        *CARRIED_KEYS,
    }
)
# The code-written experiment lines that are not `key: value` lines, but that
# code reads back by their prefix.
EXPERIMENT_LINE_PREFIXES = (
    "- ingredient: ",
    REJECTION_LINE_PREFIX,
    HELD_LINE_PREFIX,
    GATE_BLOCK_LINE_PREFIX,
)


def is_experiment_head_line(line: str) -> bool:
    """True when code reads this experiment line back (see
    `EXPERIMENT_HEAD_KEYS`)."""
    if line.startswith(EXPERIMENT_LINE_PREFIXES):
        return True
    pair = kv_line(line)
    return pair is not None and pair[0] in EXPERIMENT_HEAD_KEYS


def rc_built_lines(rc_id: str, change_summary: str | None) -> str:
    """What `record_rc_built` appends when the build lands."""
    pairs = [("rc", head_value(rc_id, HEAD_ID_MAX_CHARS))]
    if change_summary:
        pairs.append(("change", head_value(change_summary, CHANGE_MAX_CHARS)))
    return render_kv(pairs)


@dataclass(frozen=True)
class ExperimentView:
    """A task-scope `experiment` record, as the loop reads it. Every field
    below `content` is parsed from the overview or the code-owned content
    lines; the analyzer's body is left whole in `content`."""

    id: str
    overview: str
    tags: list[str]
    content: str | None
    state: ExperimentState
    promoted: bool
    rejected_at_val: bool
    held_on: str | None  # the OPEN constraint id of a `— HELD` stamp
    label: str | None  # "EXP <MMDD>-<item id>"
    technique: str | None  # the content `technique:` line (fix arms)
    overview_technique: str | None  # the `(technique)` label — RUNNING arms only
    issue_id: str | None  # content `issue:` — absent on merges and on older arms
    hypothesis: str | None
    predicted_effect: str | None
    parent_rc_id: str | None
    eval_id: str | None
    head_to_head_group: str | None
    rc_id: str | None  # content `rc:` — written when the build lands, so an
    #   abandoned record still names the run config it built
    reused_rc_id: str | None  # content `reused rc:` — this arm measured a
    #   prior run's build; not a second attempt in `lever_attempts`
    change_summary: str | None  # content `change:` — the build's one-line
    #   human summary of what differs from the parent
    generality: str | None  # content `generality:` — the leak check's
    #   finding, when it found or could not check something
    judge_copy: str | None  # content `judge copy:` — the build-time
    #   judge-copy check's finding, as for `generality`
    ingredients: tuple[tuple[str, str], ...]  # merge arms only
    rejection_noted: bool
    held_noted: bool
    # The `- blocked at the val gate …` line, after its prefix,
    #   or None — the gate blocked this run config because it copies eval text
    gate_block: str | None = None
    # Content `reference check:` — what the build names and does
    #   not attach (`kiln_harness_tuner.reference_check`), as for `generality`
    reference_check: str | None = None

    @classmethod
    def from_record(cls, record: MemoryRecord) -> ExperimentView:
        _require_kind(record, RecordKind.EXPERIMENT)
        content = record.content
        match = _EXP_TECHNIQUE_RE.match(record.overview)
        # One pass over the content: the first non-empty value per key (the
        # `kv_value` rule), the merge ingredients and the rejection note.
        first: dict[str, str] = {}
        ingredients: list[tuple[str, str]] = []
        rejection_noted = held_noted = False
        gate_block: str | None = None
        for line in (content or "").splitlines():
            pair = kv_line(line)
            if pair is not None and pair[1]:
                first.setdefault(pair[0], pair[1])
            if (m := _INGREDIENT_LINE_RE.match(line)) is not None:
                ingredients.append((m["mem"], m["rc"]))
            if line.startswith(REJECTION_LINE_PREFIX):
                rejection_noted = True
            if line.startswith(HELD_LINE_PREFIX):
                held_noted = True
            if gate_block is None and line.startswith(GATE_BLOCK_LINE_PREFIX):
                gate_block = line.removeprefix(GATE_BLOCK_LINE_PREFIX)
        return cls(
            id=record.id,
            overview=record.overview,
            tags=list(record.tags),
            content=content,
            state=experiment_state(record.overview),
            promoted=is_promoted(record.overview),
            rejected_at_val=is_rejected_at_val(record.overview),
            held_on=held_constraint(record.overview),
            label=_experiment_label(record.overview),
            technique=first.get("technique"),
            overview_technique=match["technique"] if match is not None else None,
            issue_id=first.get("issue"),
            hypothesis=first.get("hypothesis"),
            predicted_effect=first.get("predicted effect"),
            parent_rc_id=first.get("parent rc"),
            eval_id=first.get("eval"),
            head_to_head_group=first.get("head_to_head_group"),
            rc_id=first.get("rc"),
            reused_rc_id=first.get("reused rc"),
            change_summary=first.get("change"),
            generality=first.get("generality"),
            judge_copy=first.get("judge copy"),
            reference_check=first.get("reference check"),
            ingredients=tuple(ingredients),
            rejection_noted=rejection_noted,
            held_noted=held_noted,
            gate_block=gate_block,
        )

    @property
    def stamped(self) -> bool:
        return self.promoted or self.rejected_at_val or self.held_on is not None

    @property
    def lever(self) -> str | None:
        """The technique this arm attacked its issue with, for the
        `lever_attempts` count: the content line, else — for a record from before
        that line existed — the overview label, which survives
        only while the arm is RUNNING. Best-effort by design."""
        return self.technique or self.overview_technique

    @property
    def is_merge(self) -> bool:
        return bool(self.ingredients) or self.overview_technique == MERGE_TECHNIQUE


# ---- open unknowns: `OPEN: ` constraints ----

# Open unknowns are `constraint` memories with an `OPEN: ` overview prefix
# (memory skill). Answered = a human strips the prefix in place.
OPEN_UNKNOWN_PREFIX = "OPEN: "
# Canonical-question bound: the stored `question:` line is the dedup key, so
# it must survive the content clamp's head region WHOLE (whole lines within
# 60% of CONTENT_MAX_CHARS = 2400 chars) — bounded safely under that.
QUESTION_MAX_CHARS = 2000


def canonical_question(question: str) -> str:
    """The single write-boundary form of a question, feeding the dedup key,
    the overview AND the stored `question:` line — so the two key
    derivations (incoming question vs stored content line) stay symmetric
    by construction. One line, bounded, idempotent."""
    return clamp(one_line(question), QUESTION_MAX_CHARS)


def question_key(question: str) -> str:
    """The dedup key: canonical question, case-folded — so formatting
    jitter between planner waves never forks a record."""
    return canonical_question(question).casefold()


def open_unknown_overview(question: str) -> str:
    return f"{OPEN_UNKNOWN_PREFIX}{question}"


@dataclass(frozen=True)
class OpenUnknownContent:
    """The frozen five-line content of an open unknown. `source` is
    `assumption` when code writes it; a human promotes it to `human` on
    confirmation."""

    question: str
    default: str
    gates: str
    source: str
    reask_when: str

    def render(self) -> str:
        return render_kv(
            [
                ("question", self.question),
                ("default", self.default),
                ("gates", self.gates),
                ("source", self.source),
                ("re-ask when", self.reask_when),
            ]
        )

    @classmethod
    def parse(cls, content: str | None) -> OpenUnknownContent | None:
        """None when there is no `question:` line — a hand-authored
        constraint, which then never matches a dedup key (a fresh POST beats
        a wrong PATCH). Keys match case-insensitively; the first line per
        key wins."""
        found: dict[str, str] = {}
        for key, value in kv_lines(content):
            found.setdefault(key.strip().casefold(), value)
        if "question" not in found:
            return None
        return cls(
            question=found["question"],
            default=found.get("default", ""),
            gates=found.get("gates", ""),
            source=found.get("source", ""),
            reask_when=found.get("re-ask when", ""),
        )

    @property
    def key(self) -> str:
        return question_key(self.question)


# ---- the `baseline` and `val` singletons ----

# `val` is the ACCESS CLASS, not the record's name — it supports a
# query-layer rule ("the train lane never fetches `tags=val`") and
# generalizes past this one record. The identity is the overview.
VAL_STATS_TAG = "val"
# Generic and NUMBERLESS by construction, so listing `tags=val` (which
# returns overviews) leaks nothing on its own — the numbers are in content,
# which only `/by_ids` returns.
VAL_STATS_OVERVIEW = (
    "Validation metrics for each baseline promotion (val-grade; the numbers "
    "are in content, never here)"
)


def baseline_lineage(content: str | None) -> list[str]:
    """Promotion-lineage rc ids, newest → oldest —
    the record's content verbatim: one rc id per line, newest first.

    **Deliberately dumb**, and it can afford to be. The record holds no val
    data, so a bad parse leaks nothing; it costs a cache screen (the run
    re-measures instead of reusing a stored comparison). Hence: first
    whitespace-delimited token of each non-blank line, de-duplicated in
    order, no regex over prose. The clamp markers are skipped."""
    lineage: list[str] = []
    seen: set[str] = set()
    for line in (content or "").splitlines():
        tokens = line.split()
        if not tokens or tokens[0] in MARKER_FIRST_TOKENS:
            continue
        if tokens[0] not in seen:
            seen.add(tokens[0])
            lineage.append(tokens[0])
    return lineage


def baseline_chain(rc_ids: Iterable[str]) -> str:
    """The promotion chain, one rc id per line, first occurrence wins."""
    return "\n".join(dict.fromkeys(rc_ids))


def baseline_promoted_overview(rc_id: str, date: str, winner_label: str) -> str:
    """Free prose, no contract — the rc id the loop reads is the tag."""
    return f"Baseline: run config {rc_id} — promoted {date} by {winner_label}"


def baseline_seeded_overview(rc_id: str, date: str, provenance: str) -> str:
    return (
        f"Baseline: run config {rc_id} — seeded {date} by kiln-harness-tuner setup "
        f"({provenance})"
    )


def val_stats_line(
    *,
    winner_rc_id: str,
    date: str,
    winner_label: str,
    previous_rc_id: str,
    rationale: str,
) -> str:
    """One promotion's val summary — `"<rc> (<date>, <label>) ← <prev rc> —
    <rationale>"`, newest first in the record. Every agent-authored part is
    collapsed to ONE line: the record's contract is one line per promotion,
    and nothing downstream would catch a breach."""
    return (
        f"{one_line(winner_rc_id)} ({date}, {one_line(winner_label)}) "
        f"← {previous_rc_id} — {one_line(rationale)}"
    )


@dataclass(frozen=True)
class BaselineView:
    """The singleton task-scope `baseline` record. `rc_ids` is every `rc_`
    tag in tag order — exactly one is the pointer; the writer turns zero or
    two into a fatal error, with the instructive message."""

    id: str
    overview: str
    tags: list[str]
    content: str | None
    rc_ids: tuple[str, ...]
    lineage: tuple[str, ...]  # newest → oldest

    @classmethod
    def from_record(cls, record: MemoryRecord) -> BaselineView:
        _require_kind(record, RecordKind.BASELINE)
        return cls(
            id=record.id,
            overview=record.overview,
            tags=list(record.tags),
            content=record.content,
            rc_ids=tuple(rc_ids_from_tags(record.tags)),
            lineage=tuple(baseline_lineage(record.content)),
        )
