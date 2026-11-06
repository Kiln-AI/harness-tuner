"""MemoryWriter — the format-critical lifecycle writers
(docs/architecture.md section 8).

Sole writer of lifecycle state; one function per write. **What** each record
says — the kinds, the lifecycle enums, the `key: value` content lines, the
frozen note lines — lives in `memory_schema.py`, which this module imports
and never restates. This module owns **when** a record is written, the
transport (list / hydrate / POST / PATCH), the read-modify-write per
lifecycle event, and the cap clamps that keep an over-long write non-fatal.
Agents do their own discovery writes (`lesson`/`dead_end`/new `issue`)
through their gateway operations (`memory_create`, `memory_update`) — never
through this module.

API surface per `agent_files/skills/memory/SKILL.md`, response shapes VERIFIED
against the live server:
`POST/GET/PATCH /api/projects/{pid}/memories` (+ `/by_ids`), scopes
`"project"` / `"task::<task_id>"`. The list route returns
`{"listings": [...], "matched", "remaining", "remaining_tag_counts"}` where
rows are SUMMARIES (`content_length`, never `content`) — `MemorySummary`;
`/by_ids` returns a BARE ARRAY of full records (`content` present, null when
empty) — `MemoryRecord`. Consumers that read content must hydrate via
`_get`/`_get_many`; overview/id/tags-only consumers stay on summaries.

Every writer that takes a record id hydrates it through `_get_issue` /
`_get_experiment`, which read the record as the typed view for that kind —
so an issue writer handed an experiment id (or the reverse) refuses with
`RecordKindError` before anything is written.

Retry policy is the plain-REST row of docs/architecture.md section 10:
`KilnClient` handles the 3 quick retries internally; a write still failing after them
propagates — fatal (state integrity beats liveness). The promotion apply step layers its
own 3-call/20s ladder in the orchestrator.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError
from kiln_harness_tuner.memory_schema import (
    AUDITED_RC_TAG_PREFIX,
    BASELINE_AUDIT_TAG,
    BASELINE_JUDGE_COPY_TAG,
    CARRIED_KEYS,
    CONTENT_MAX_CHARS,
    DEFAULT_ISSUE_PRIORITY,
    EXPERIMENT_TRANSITIONS,
    FINDING_MAX_CHARS,
    HISTORY_TRUNCATION_MARKER,
    ISSUE_OVERVIEW_LEAD,
    ISSUE_TERMINAL_STATES,
    MERGE_TECHNIQUE,
    OVERVIEW_MAX_CHARS,
    PROBE_TAG,
    SCREEN_TAG,
    SETUP_DELIMITER,
    SETUP_TAIL_RESERVE_CHARS,
    STAMPABLE_STATES,
    TRUNCATION_MARKER,
    VAL_STATS_OVERVIEW,
    VAL_STATS_TAG,
    BaselineView,
    ExperimentState,
    ExperimentView,
    FixOpening,
    IssueNote,
    IssueState,
    IssueView,
    MemoryRecord,
    MemorySummary,
    MergeOpening,
    OpenUnknownContent,
    RecordKind,
    SetupHead,
    baseline_chain,
    baseline_promoted_overview,
    baseline_rc_tag,
    baseline_seeded_overview,
    canonical_question,
    clamp,
    eval_ids_from_tags,
    eval_tag,
    experiment_label_for,
    experiment_label_prefix,
    experiment_state,
    experiment_suffix,
    gate_block_note,
    head_to_head_line,
    head_value,
    held_note,
    held_stamp,
    is_experiment_head_line,
    is_promoted,
    is_rejected_at_val,
    issue_state,
    join_owned,
    kv_line,
    kv_value,
    one_line,
    open_unknown_overview,
    priority_from_tags,
    priority_tag,
    promoted_stamp,
    question_key,
    rc_built_lines,
    rejected_stamp,
    rejection_note,
    render_kv,
    set_state_prefix,
    split_owned,
    state_prefix_run,
    strip_state_suffix,
    tags_with_baseline_rc,
    tags_with_eval,
    tags_with_priority,
    technique_label,
    val_stats_line,
    verdict_overview,
)
from kiln_harness_tuner.schemas import (
    BaselineRef,
    ErrorInfo,
    EvalBuildReport,
    EvalDescription,
    ExperimentItem,
    ExperimentResult,
    MergeWork,
    OpenUnknown,
    ProbeItem,
    ProbeResult,
    PromotionDecision,
    RcChange,
    ResidualGateResult,
    ScreenResult,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # setup.py imports this module's vocabulary at RUNTIME —
    from kiln_harness_tuner.setup import (
        EvalView,
    )  # typing-only here keeps the cycle open.

_LIST_PAGE_SIZE = 50
# /by_ids ids travel in the query string — chunk hydration so a large
# constraint store never composes an over-long URL.
_BY_IDS_CHUNK_SIZE = 50

# Content-clamp budgets. Replacement writes (verdict-style content) are
# read at both ends — head carries the id-bearing lines (hypothesis/lever/rc
# + issue/eval ids), tail carries `next:` — so the clamp keeps whole head
# lines (~60% of budget) and whole tail lines (~30%) around a single marker,
# never a blind tail cut.
_CONTENT_HEAD_FRACTION = 0.6
_CONTENT_TAIL_FRACTION = 0.3

# How setup picked the seeded rc. The vocabulary is
# shared with setup.py — it decides the rule, this module owns the wording
# each rule writes (seed_baseline). A future `survey` rule adds its row there.
BASELINE_RULE_TASK_DEFAULT = "task_default"
BASELINE_RULE_MOST_RECENT = "most_recent"


class BaselineRecordMissingError(Exception):
    """No usable `baseline` record — fatal before wave 1. Either the
    record is absent, or it carries no `rc_<id>` tag to name a run config
    with. Deliberately NOT a format check over the overview: the overview is
    free prose, and the rc id lives in the tag."""


class MemoryWriteRefused(Exception):
    """A lifecycle write was refused on a precondition, before anything was
    written. Deterministic — a retry cannot help — and never a run kill on
    its own: the callers turn it into the item's `failed_infra` or log it."""


class IssueWriteRefused(MemoryWriteRefused):
    """A format-critical issue write was refused on a precondition, before
    anything was written. Never a run kill — the caller
    (`measure_issue_baseline`) turns it into that item's `failed_infra`,
    leaving every record untouched."""


class IssueBindingMismatchError(IssueWriteRefused):
    """The write would put eval-derived state onto an issue bound to a
    DIFFERENT eval."""


class IssueTerminalStateError(IssueWriteRefused):
    """The write would move an issue OUT of a terminal state (`FIXED:` /
    `WONT_FIX:`) — the doctrine `ISSUE_TERMINAL_STATES` carries for the sync
    ("never un-closes one"), applied to the one lifecycle writer that would
    otherwise bypass it."""


class ExperimentStateError(MemoryWriteRefused):
    """The write would move an experiment record along a transition
    `memory_schema.EXPERIMENT_TRANSITIONS` does not allow — a verdict over
    an abandoned arm, a restate over a result, a gate stamp on an arm with
    no result. The record is left as it is."""


class MemoryRecordNotFoundError(KeyError):
    """`_get` found no record for the id. A `KeyError` subclass so every
    existing caller's behavior is unchanged, and a named one so a caller that
    means "the id I was handed doesn't exist" can catch exactly that — not
    any stray dict lookup deeper in the write."""


class MemoryListShapeError(Exception):
    """The list route's response doesn't match the live-verified envelope
    (`listings`/`matched`/`remaining`) — fail hard, same doctrine as the
    stats response guard: silently reading a drifted shape as an empty
    listing would fake a missing baseline, skip janitor debris, and
    duplicate open-unknown records."""


# ---- cap clamps: every write passes one of these, so an overrun is never a 422 ----


def _fit_overview(text: str) -> str:
    """The single funnel every overview write passes through (`_post` and
    `_patch`), so the one-line rule holds for AGENT-authored overviews too —
    `record_verdict`'s and the setup description's alike."""
    return clamp(one_line(text), OVERVIEW_MAX_CHARS)


def _take_lines(lines: Iterable[str], budget: int) -> list[str]:
    """Whole lines, in the given order, while their joined length (incl.
    newlines) stays within budget."""
    taken: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > budget:
            break
        taken.append(line)
        used += cost
    return taken


def _fit_around_protected(lines: list[str], protected_line: str) -> str:
    """The ownership-boundary clamp. `protected_line` — the
    setup/loop delimiter — is never a drop candidate and is re-emitted in
    place: losing it would merge the loop's history into the setup-owned
    region, and the next refresh would replace the lot.

    Above it is setup-owned and separately budgeted (SETUP_HEAD_MAX_CHARS
    fits inside the head budget below, so a composed head always survives
    whole). Below it is the loop's append-only region, so the OLDEST of
    those lines drop first — same doctrine as _fit_content_keep_tail.

    Both regions mark what they lost. An over-budget head is only reachable
    on a hand-edited or legacy record, and a silent cut there would drop the
    `status: … fp: …` line without trace — which reads back as "never
    synced" and re-runs a PAID describe dispatch on every subsequent run."""
    index = lines.index(protected_line)
    head_budget = (
        CONTENT_MAX_CHARS
        - SETUP_TAIL_RESERVE_CHARS
        - len(protected_line)
        - len(TRUNCATION_MARKER)
        - 2
    )
    head = _take_lines(lines[:index], head_budget)
    head_marker = [TRUNCATION_MARKER] if len(head) < index else []
    used = (
        sum(len(line) + 1 for line in [*head, *head_marker]) + len(protected_line) + 1
    )
    tail_budget = CONTENT_MAX_CHARS - used - len(HISTORY_TRUNCATION_MARKER) - 1
    kept = _take_lines(reversed(lines[index + 1 :]), max(tail_budget, 0))
    kept.reverse()
    dropped_history = len(kept) < len(lines) - index - 1
    marker = [HISTORY_TRUNCATION_MARKER] if dropped_history else []
    return "\n".join([*head, *head_marker, protected_line, *marker, *kept])


def _fit_content(text: str | None, protected_line: str | None = None) -> str | None:
    """Replacement-write safety clamp to the API's content cap
    (CONTENT_MAX_CHARS, write-enforced). Line-preserving and structure-aware:
    whole head lines (~60% — the id-bearing lines planners re-cite) + whole
    tail lines (~30% — `next:`) around one `[… truncated …]` marker. Callers
    should not rely on it: agents are budgeted to stay under the cap
    (kiln-harness-tuner-experiment-analysis skill); this only keeps an overrun
    non-fatal.

    `protected_line`, when present in the text, switches to the
    ownership-boundary clamp above. Every write passes SETUP_DELIMITER, so
    the guarantee is global and no call site can forget it; a record without
    the delimiter is clamped exactly as before.

    Accepted limitation: the region budgets are fixed, so
    pathological shapes waste leftover space (e.g. two long lines slightly
    over cap -> head keeps its unspent budget while the whole tail line
    drops). Fine for a safety net; if it ever matters, let the tail reuse
    the head's unspent budget."""
    if text is None or len(text) <= CONTENT_MAX_CHARS:
        return text
    lines = text.splitlines()
    if protected_line is not None and protected_line in lines:
        return _fit_around_protected(lines, protected_line)
    head = _take_lines(lines, int(CONTENT_MAX_CHARS * _CONTENT_HEAD_FRACTION))
    tail = _take_lines(
        reversed(lines[len(head) :]), int(CONTENT_MAX_CHARS * _CONTENT_TAIL_FRACTION)
    )
    tail.reverse()
    if not head and not tail:
        # A single line longer than both budgets — structure can't be
        # preserved, plain tail cut.
        return clamp(text, CONTENT_MAX_CHARS)
    return "\n".join([*head, TRUNCATION_MARKER, *tail])


def _fit_content_keep_tail(text: str) -> str:
    """Append-write clamp: the newest (appended) lines MUST survive — drop
    the OLDEST lines (top) first behind a `[… earlier history truncated …]`
    marker. Correct by design for the baseline history (lineage reads
    newest-last — losing the oldest lines degrades gracefully) and for
    issue-record append lines (fixed/reopened/eval-link notes)."""
    if len(text) <= CONTENT_MAX_CHARS:
        return text
    budget = CONTENT_MAX_CHARS - len(HISTORY_TRUNCATION_MARKER) - 1
    lines = text.splitlines()
    kept = _take_lines(reversed(lines), budget)
    kept.reverse()
    if not kept:
        # Even the newest line alone busts the budget — keep its head.
        return f"{HISTORY_TRUNCATION_MARKER}\n{clamp(lines[-1], budget)}"
    return "\n".join([HISTORY_TRUNCATION_MARKER, *kept])


def _fit_content_keep_head(text: str) -> str:
    """Newest-FIRST clamp — the mirror of `_fit_content_keep_tail`, for the
    two records this module writes newest-first (the baseline's promotion
    chain and the `val` lineage). Drop the OLDEST lines (bottom) behind the
    same marker.

    `_fit_content`'s generic clamp would keep a head *and* a tail region,
    which on a newest-first record preserves the deepest ancestors while
    punching a hole through the middle of the chain — a lineage walk would
    then skip straight past usable screen references."""
    if len(text) <= CONTENT_MAX_CHARS:
        return text
    budget = CONTENT_MAX_CHARS - len(HISTORY_TRUNCATION_MARKER) - 1
    lines = text.splitlines()
    kept = _take_lines(lines, budget)
    if not kept:
        # Even the newest line alone busts the budget — keep its head.
        return f"{clamp(lines[0], budget)}\n{HISTORY_TRUNCATION_MARKER}"
    return "\n".join([*kept, HISTORY_TRUNCATION_MARKER])


def _fit_stateful(prefix: str, middle: str, suffix: str) -> str:
    """Compose `prefix + middle + suffix` within the overview cap by clamping
    ONLY the variable middle: the label prefix and the lifecycle state suffix
    (`— RUNNING` / `— FAILED …` / `— ABANDONED …` / `— REFUTED` /
    `— PROMOTED …` / `— REJECTED AT VAL …`) are the vocabulary the whole loop
    reads (`memory_schema.ExperimentState`) — they must survive truncation.

    When the prefix alone fills the room the suffix leaves, the PREFIX is cut
    and the suffix still lands whole. Otherwise the middle would get a zero
    budget, the composed text would go over the cap, and `_fit_overview`
    would cut the suffix off — the record would lose its state."""
    middle = one_line(middle)
    room = OVERVIEW_MAX_CHARS - len(suffix)
    if len(prefix) >= room:
        return f"{clamp(prefix, room)}{suffix}"
    middle = clamp(middle, room - len(prefix))
    return f"{prefix}{middle}{suffix}"


def _append_content(content: str | None, line: str) -> str:
    """Append into the LOOP-owned region. On a record with
    a setup head that boundary matters: handing the whole string to
    `_fit_content_keep_tail` would drop oldest-first — i.e. the head and the
    delimiter — the first time an over-cap append landed, silently making
    the analyst's history setup-owned."""
    head, tail = split_owned(content)
    if head is None:
        combined = f"{content}\n{line}" if content else line
        # Cap discipline for appends: the appended line must survive —
        # oldest lines drop first (see _fit_content_keep_tail).
        return _fit_content_keep_tail(combined)
    return join_owned(head, f"{tail}\n{line}" if tail else line)


def _fit_experiment_content(text: str) -> str:
    """Append-write clamp for an EXPERIMENT record. The lines
    code reads back (`memory_schema.is_experiment_head_line`: the opening,
    `rc:`, `change:`, the check findings, the gate notes) are never dropped.
    Other lines (the residual lines, the analyzer's prose) drop oldest first,
    behind one `[… earlier history truncated …]` marker where the first one
    was. `_fit_content_keep_tail` dropped the opening first, so an over-cap
    append lost the record's identity lines.

    The head lines are bounded on render, so they fit with room to spare.
    Only a hand-edited record can have more of them than the cap holds: that
    one falls back to `_fit_content_keep_tail`."""
    if len(text) <= CONTENT_MAX_CHARS:
        return text
    lines = text.splitlines()
    keep = [is_experiment_head_line(line) for line in lines]
    used = sum(len(line) + 1 for line, kept in zip(lines, keep, strict=True) if kept)
    budget = CONTENT_MAX_CHARS - used - len(HISTORY_TRUNCATION_MARKER) - 1
    if budget < 0:
        return _fit_content_keep_tail(text)
    # The newest other lines that fit, kept in place.
    for index in reversed(range(len(lines))):
        if keep[index]:
            continue
        cost = len(lines[index]) + 1
        if cost > budget:
            break
        keep[index] = True
        budget -= cost
    out: list[str] = []
    marked = False
    for line, kept in zip(lines, keep, strict=True):
        if kept:
            out.append(line)
        elif not marked:
            out.append(HISTORY_TRUNCATION_MARKER)
            marked = True
    return "\n".join(out)


def _append_experiment_content(content: str | None, lines: str | list[str]) -> str:
    """Append code-written lines to an experiment record, clamped by
    `_fit_experiment_content`."""
    added = lines if isinstance(lines, str) else "\n".join(lines)
    combined = f"{content}\n{added}" if content else added
    return _fit_experiment_content(combined)


class MemoryWriter:
    """Format-critical memory writes + the orchestrator's baseline reads,
    against one (project, task). `now` is injectable so tests pin dates."""

    def __init__(
        self,
        kiln: KilnApi,
        *,
        project_id: str,
        task_id: str,
        now: Callable[[], datetime] = datetime.now,
    ):
        self._kiln = kiln
        self.project_id = project_id
        self.task_id = task_id
        self._now = now
        # The EXP label prefix is the RUN START date, frozen at
        # construction: a run crossing midnight must not desync the label the
        # baseline record / mark_fixed citations use to reference the record
        # open_experiment wrote.
        self._run_prefix = now().strftime("%m%d")

    # ---- plumbing ----

    @property
    def _memories_path(self) -> str:
        return f"/api/projects/{self.project_id}/memories"

    @property
    def task_scope(self) -> str:
        return f"task::{self.task_id}"

    def _date(self) -> str:
        return self._now().strftime("%m-%d")  # in-line date form

    def _iso_date(self) -> str:
        # The setup head's `synced` stamp is a full date:
        # it is read months later to judge how stale a description is.
        return self._now().strftime("%Y-%m-%d")

    def exp_label(self, item_id: str) -> str:
        """The run-START-date prefix is stable for the whole run, so a
        promotion-time recomputation matches what open_experiment wrote."""
        return experiment_label_for(self._run_prefix, item_id)

    async def _post(
        self, overview: str, scope: str, content: str | None, tags: list[str]
    ) -> str:
        fitted = _fit_overview(overview)
        try:
            response = await self._kiln.call(
                "POST",
                self._memories_path,
                body={
                    "overview": fitted,
                    "scope": scope,
                    "content": _fit_content(content, SETUP_DELIMITER),
                    "tags": tags,
                },
            )
        except (KilnTransportError, KilnAPIError) as exc:
            # A lost reply, not a refused write: the POST may
            # have landed. `KilnClient` does not replay writes (that would fork
            # the `baseline`/`val` singletons); instead re-list the record's
            # own (scope, tags) and adopt the ONE row with this exact
            # overview and content.
            if isinstance(exc, KilnAPIError) and exc.status_code < 500:
                raise
            found = await self._find_posted(
                fitted, scope, tags, _fit_content(content, SETUP_DELIMITER)
            )
            if found is None:
                raise
            logger.warning(
                "memory POST reply lost (%s) — adopted the record that landed: %s",
                exc,
                found,
            )
            return found
        return str(response["id"])

    async def _find_posted(
        self, overview: str, scope: str, tags: list[str], content: str | None
    ) -> str | None:
        """The id of the record a lost-reply POST created, or None when that
        cannot be known. Overview equality alone is not a key (the 280-char
        clamp makes two long open questions collide — `write_open_unknowns`
        keys on content for that reason), so the content must match too,
        and exactly ONE record may match: with two, "the one I just
        created" is unknowable (summaries carry no timestamp), and adopting
        an older record silently is worse than failing.
        """
        try:
            rows = await self._list(scope, tags)
            same_overview = [row.id for row in rows if row.overview == overview]
            matches = [
                record.id
                for record in await self._get_many(same_overview)
                if (record.content or None) == (content or None)
            ]
        except (KilnTransportError, KilnAPIError):
            return None
        if len(matches) != 1:
            if matches:
                logger.warning(
                    "memory POST reply lost and %d records match — not adopting",
                    len(matches),
                )
            return None
        return matches[0]

    async def _patch(self, memory_id: str, body: dict[str, Any]) -> None:
        if "overview" in body:
            body["overview"] = _fit_overview(body["overview"])
        if body.get("content") is not None:
            # Safety net on EVERY content write (an over-cap write is a
            # fatal 422): append paths pre-clamp tail-preserving in
            # _append_content and pass through here unchanged. The delimiter
            # is protected unconditionally — it is only honored when actually
            # present, so no write path can forget the ownership boundary.
            body["content"] = _fit_content(body["content"], SETUP_DELIMITER)
        # PATCH bodies here are absolute values (a whole overview, a whole
        # content, a whole tag list), never deltas, so a replay is idempotent
        # — and the promotion writes run under `_promotion_write`, which
        # re-runs the whole read-modify-write on a transport error and WOULD
        # duplicate a line that had landed.
        await self._kiln.call(
            "PATCH", f"{self._memories_path}/{memory_id}", body=body, retries=True
        )

    async def _list(self, scope: str, tags: Iterable[str]) -> list[MemorySummary]:
        """Full listing for (scope, tags), paged (the API caps at 50/page).

        Live shape: rows come under `"listings"` as
        SUMMARIES — no `content` — with `matched`/`remaining` paging. A
        response without the envelope keys fails hard (MemoryListShapeError
        — never silently an empty listing). Stop when `remaining` hits 0,
        on an empty page, or once `matched` rows are collected — the
        `matched` bound is unconditional, so even an offset-ignoring server
        repeating a nonempty page can never loop forever."""
        summaries: list[MemorySummary] = []
        offset = 0
        while True:
            response = await self._kiln.call(
                "GET",
                self._memories_path,
                query_params={
                    "scope": scope,
                    "tags": list(tags),
                    "limit": _LIST_PAGE_SIZE,
                    "offset": offset,
                },
            )
            # KilnClient.call returns None for an empty 2xx body and str
            # for non-JSON — guard before .get, then require the envelope.
            if not isinstance(response, dict) or not (
                {"listings", "matched", "remaining"} <= response.keys()
            ):
                got = (
                    f"keys {sorted(response)}"
                    if isinstance(response, dict)
                    else f"a {type(response).__name__} response"
                )
                raise MemoryListShapeError(
                    f"GET {self._memories_path} did not return the verified "
                    f"'listings'/'matched'/'remaining' envelope (got {got}) — "
                    "response shape needs re-verifying against the live API"
                )
            page = response["listings"] or []
            summaries.extend(MemorySummary.from_listing(raw) for raw in page)
            remaining = int(response["remaining"] or 0)
            matched = int(response["matched"] or 0)
            if remaining <= 0 or not page or len(summaries) >= matched:
                return summaries
            offset += len(page)

    async def _get_many(self, memory_ids: Iterable[str]) -> list[MemoryRecord]:
        """Hydrate FULL records (with `content`) — `/by_ids` returns a bare
        array of them (verified against the live server). Missing ids are simply absent
        from the result. Chunked (ids ride the query string, so one call
        for hundreds of records would hit URL-length limits); input order
        preserved across chunks."""
        ids = list(memory_ids)
        records: list[MemoryRecord] = []
        for start in range(0, len(ids), _BY_IDS_CHUNK_SIZE):
            response = await self._kiln.call(
                "GET",
                f"{self._memories_path}/by_ids",
                query_params={"ids": ids[start : start + _BY_IDS_CHUNK_SIZE]},
            )
            records.extend(MemoryRecord.from_api(raw) for raw in response or [])
        return records

    async def _get(self, memory_id: str) -> MemoryRecord:
        rows = await self._get_many([memory_id])
        if not rows:
            raise MemoryRecordNotFoundError(f"memory {memory_id} not found")
        return rows[0]

    async def _get_issue(self, issue_id: str) -> IssueView:
        """Hydrate an id the caller says is an issue. `RecordKindError` when
        the record is not tagged one — the id came from an agent."""
        return IssueView.from_record(await self._get(issue_id))

    async def _get_experiment(self, exp_mem_id: str) -> ExperimentView:
        return ExperimentView.from_record(await self._get(exp_mem_id))

    # ---- reads the loop makes over the whole store ----

    async def lever_attempts(self) -> dict[str, dict[str, int]]:
        """issue id → technique → number of experiment arms ever opened on
        that (issue, lever), any outcome (the lever ladder's
        "two failed text arms → change surface class" rule needs the count).
        Read off `ExperimentView.issue_id` / `.lever`; a record from before
        those lines existed is skipped, never guessed. Merge arms
        carry no single issue and are not counted."""
        rows = await self._list(self.task_scope, [RecordKind.EXPERIMENT.value])
        attempts: dict[str, dict[str, int]] = {}
        if not rows:
            return attempts
        for record in await self._get_many(row.id for row in rows):
            view = ExperimentView.from_record(record)
            lever = view.lever
            if view.issue_id is None or lever is None or view.is_merge:
                continue
            if view.reused_rc_id is not None:
                continue  # the same attempt, analyzed at last — not a new arm
            per_issue = attempts.setdefault(view.issue_id, {})
            per_issue[lever] = per_issue.get(lever, 0) + 1
        return attempts

    async def issues_by_eval(self) -> dict[str, list[str]]:
        """eval id → the ids of the task's `issue` records bound to it (the
        `eval_<id>` tag). The code-side promotion rule maps a regressed val
        row back to its issue with this."""
        mapping: dict[str, list[str]] = {}
        for row in await self._list(self.task_scope, [RecordKind.ISSUE.value]):
            for eval_id in eval_ids_from_tags(row.tags):
                mapping.setdefault(eval_id, []).append(row.id)
        return mapping

    async def list_issues(self) -> list[IssueView]:
        """Every task-scope `issue` memory, hydrated and typed — the setup
        phase's coverage read.

        FULL records, not the listing's summaries: the sync needs each
        record's setup head (ownership + the stored fingerprint), and that
        lives in `content`. Costs one listing plus one chunked `/by_ids` —
        and nothing at all on a cold project, where the listing is empty."""
        summaries = await self._list(self.task_scope, [RecordKind.ISSUE.value])
        records = await self._get_many(s.id for s in summaries)
        return [IssueView.from_record(r) for r in records]

    # ---- baseline record (reads for the loop; write = promote below) ----

    async def require_baseline_record(self) -> MemoryRecord:
        """The fail-fast read — the error text is written for the
        *calling agent* (seeding is the onboarding agent's job).

        Only existence is checked here. The overview carries no contract at
        all, so there is nothing to parse and no dangling-record branch;
        `baseline_rc_id` owns the "can this record actually name a run
        config" half, and the startup read calls it immediately."""
        found = await self.find_baseline_record()
        if found is None:
            raise BaselineRecordMissingError(self._seed_a_baseline_msg())
        # Listing rows are summaries (no content) — hydrate the singleton:
        # baseline_lineage and promote() both read the record's chain
        # content. (find_baseline_record owns picking it — one place.)
        return await self._get(found.id)

    def _seed_a_baseline_msg(self) -> str:
        return (
            f"no usable baseline record for task {self.task_id} — run "
            "baseline setup (onboarding): use the task's default run config "
            "if one exists, else ask the user to pick or create one, then "
            "seed the singleton task-scope `baseline` memory record — tags "
            f"`baseline` and `{baseline_rc_tag('<rc_id>')}` (the rc id is "
            "read from that tag, never from the overview, which is free "
            "prose) — then re-invoke the loop. The loop never invents a "
            "baseline."
        )

    def baseline_rc_id(self, record: MemoryRecord) -> str:
        """The baseline's run-config id, read from the `rc_<id>` TAG — never
        parsed out of prose. No tag means the record cannot name a run config
        at all, and two mean an ambiguous pointer; both
        are fatal, because every downstream build and measurement
        parents from this id."""
        found = BaselineView.from_record(record).rc_ids
        if len(found) == 1:
            return found[0]
        problem = (
            f"carries no `{baseline_rc_tag('<rc_id>')}` tag"
            if not found
            else f"names {len(found)} run configs ({', '.join(found)})"
        )
        raise BaselineRecordMissingError(
            f"baseline record {record.id} {problem} — tags are "
            f"{record.tags}. " + self._seed_a_baseline_msg()
        )

    async def snapshot(self) -> dict[str, Any]:
        """Every record in the two scopes the loop reads (project + this
        task), hydrated with content: the memory store as it
        stands at one moment. The orchestrator writes it to
        `runs/<id>/snapshots/` around the planner and the promotion gate so
        the viewer's step-through can show what a decision was made FROM
        (records have created_at but no edit history — an overview restated
        later is otherwise unrecoverable)."""
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for scope in ("project", self.task_scope):
            summaries = await self._list(scope, [])
            ids = [s.id for s in summaries if s.id not in seen]
            seen.update(ids)
            by_id = {r.id: r for r in await self._get_many(ids)} if ids else {}
            for summary in summaries:
                if summary.id not in by_id and summary.id not in seen:
                    continue
                full = by_id.get(summary.id)
                records.append(
                    {
                        "id": summary.id,
                        "scope": summary.scope or scope,
                        "tags": list(summary.tags),
                        "overview": summary.overview,
                        "content": full.content if full is not None else None,
                    }
                )
        return {
            "taken_at": self._now().isoformat(timespec="seconds"),
            "records": records,
        }

    async def find_baseline_record(self) -> MemorySummary | None:
        """The `None`-returning sibling of `require_baseline_record`: the same
        `baseline` listing read, no raise — setup's decision table only asks
        whether the singleton exists. Both readers
        come through here, so the "singleton by convention, first row if
        that's ever violated" choice lives in exactly one place.

        Deliberately a SUMMARY, not a hydrated record: an existence check
        needs no content, and the loop's own `require_baseline_record`
        hydrates the record moments later — a second `/by_ids` round-trip on
        every warm run would be pure waste (the read budget)."""
        found = await self._list(self.task_scope, [RecordKind.BASELINE.value])
        return found[0] if found else None

    async def seed_baseline(self, rc_id: str, *, rule: str, rc_count: int) -> str:
        """POST the singleton `baseline` record on a task that has none.
        Returns the memory id.

        **Caller's obligation**: only after `find_baseline_record()` returned
        `None`. This writer POSTs unconditionally (setup decides, memory
        writes) — a second call forks the singleton into two `baseline`
        records, and `require_baseline_record` would then resolve one of them
        arbitrarily. `seed_baseline_if_missing` is the guard.

        No metrics — setup measures nothing, and the record never carries
        them again (val stats live in the `val` record, written
        at the first promotion). The overview is free prose; the rc id the
        loop reads is the `rc_<id>` tag, and the content is the first line of
        the promotion chain."""
        if rule == BASELINE_RULE_TASK_DEFAULT:
            provenance = "task default run config"
        elif rule == BASELINE_RULE_MOST_RECENT:
            provenance = f"most recent of {rc_count} run configs; no default set"
        else:
            raise ValueError(
                f"unknown baseline seeding rule {rule!r} — this module owns the "
                "provenance wording, so a new rule must add its string here"
            )
        return await self._post(
            baseline_seeded_overview(rc_id, self._date(), provenance),
            self.task_scope,
            rc_id,
            [RecordKind.BASELINE.value, baseline_rc_tag(rc_id)],
        )

    def baseline_lineage(self, record: MemoryRecord) -> list[str]:
        """Promotion-lineage rc ids, newest → oldest (`BaselineView.lineage`)."""
        return list(BaselineView.from_record(record).lineage)

    # ---- experiment lifecycle ----

    async def open_experiment(
        self, item: ExperimentItem, wave: int, baseline: BaselineRef
    ) -> str:
        """POST the experiment record at the exact moment code decides to
        build. Returns the memory id. The content block is
        `FixOpening` / `MergeOpening` — the lines the loop reads back after
        the verdict has replaced them (`lever_attempts`)."""
        del wave  # the wave number is already in item.id ("w4-2")
        label = self.exp_label(item.id)
        work = item.work
        if isinstance(work, MergeWork):
            # Ingredient ids are the label — no hypothesis to quote.
            opening = MergeOpening(
                ingredients=tuple((w.experiment_memory_id, w.rc_id) for w in work.wins),
                conflict_flags=tuple(work.conflict_flags),
                parent_rc_id=baseline.rc_id,
                head_to_head_group=work.head_to_head_group,
            )
            overview = _fit_stateful(
                experiment_label_prefix(label, MERGE_TECHNIQUE),
                opening.label,
                experiment_suffix(ExperimentState.RUNNING),
            )
        else:
            opening = FixOpening(
                hypothesis=work.hypothesis,
                predicted_effect=work.predicted_effect,
                parent_rc_id=work.parent_rc_id or baseline.rc_id,
                eval_id=work.eval_id,
                issue_id=work.issue_memory_id,
                technique=work.technique,
                head_to_head_group=work.head_to_head_group,
                reused_rc_id=work.existing_rc_id,
                wildcard=work.wildcard,
            )
            # The technique is bounded before the label is
            # composed, so the label never fills the overview cap.
            overview = _fit_stateful(
                experiment_label_prefix(label, technique_label(work.technique)),
                work.hypothesis,
                experiment_suffix(ExperimentState.RUNNING),
            )
        return await self._post(
            overview, self.task_scope, opening.render(), [RecordKind.EXPERIMENT.value]
        )

    async def record_probe(self, item: ProbeItem, result: ProbeResult) -> str:
        """A probe's answer, as a task-scope `lesson` carrying
        the `probe` tag: overview `PROBE <item>: <question> → YES|NO|UNCLEAR`,
        content = the question, method, evidence and the ephemeral configs.
        Code writes it (the agent only answers), so the record's shape is
        fixed and the next planner finds every probe with one tag list."""
        overview = _fit_stateful(
            f"PROBE {item.id}: ",
            item.question,
            f" → {result.answer.upper()}",
        )
        pairs = [
            ("question", item.question),
            ("answer", result.answer),
            ("method", item.method),
            ("evidence", result.evidence),
            ("drives", str(result.drives)),
        ]
        if result.ephemeral_rc_ids:
            pairs.append(("ephemeral rcs", ", ".join(result.ephemeral_rc_ids)))
        if item.for_idea:
            pairs.append(("for idea", item.for_idea))
        if result.notes:
            pairs.append(("notes", result.notes))
        return await self._post(
            overview,
            self.task_scope,
            render_kv(pairs),
            [RecordKind.LESSON.value, PROBE_TAG],
        )

    async def record_screen_result(self, result: ScreenResult) -> str | None:
        """Every screened candidate's result, as a task-scope
        record with the `screen` tag, so the next run starts from it:

        - a dead end: `dead_end` · `screen`, overview `SCREEN DEAD END:
          <label> — <reason>` — the planner lists dead ends before forming
          ideas, so the same candidate is not proposed again unchanged;
        - a survivor: `lesson` · `screen`, overview `SCREEN SURVIVOR:
          <label> — <passed>/<judged> vs baseline <b>`; under speed and
          cost `SCREEN SURVIVOR: <label> — lost <k> of <n>
          baseline passes, no loss rate above <X>% at 95%`;
        - capped: `lesson` · `screen`, overview `SCREEN CAPPED:
          <label> — capped at $X after n items: inconclusive …` — never a
          dead end.

        Content: `screen_record_pairs`. An `error` row (not screened) is no
        result and is not recorded (None)."""
        if result.verdict == "error":
            return None
        dead = result.verdict == "dead_end"
        judged = sum(v.get("n", 0) for v in result.per_eval.values())
        if dead:
            overview = _fit_stateful(
                "SCREEN DEAD END: ", f"{result.label} — {result.reason}", ""
            )
            tags = [RecordKind.DEAD_END.value, SCREEN_TAG]
        elif result.verdict == "capped":
            overview = _fit_stateful(
                "SCREEN CAPPED: ", f"{result.label} — {result.reason}", ""
            )
            tags = [RecordKind.LESSON.value, SCREEN_TAG]
        else:
            quality = (
                screen_quality_short(result)
                if result.loss_bound is not None
                else f"{result.passed}/{judged} vs baseline {result.baseline_passed}"
            )
            overview = _fit_stateful("SCREEN SURVIVOR: ", result.label, f" — {quality}")
            tags = [RecordKind.LESSON.value, SCREEN_TAG]
        dated = result.model_copy(update={"date": result.date or self._iso_date()})
        return await self._post(
            overview, self.task_scope, render_kv(screen_record_pairs(dated)), tags
        )

    async def prior_screen_results(self) -> list[ScreenResult]:
        """Every screening result earlier runs recorded (the `screen` tag),
        newest run first, each marked `prior_run`. Older records
        (dead ends only, no `result` line) are read from their
        key lines."""
        summaries = await self._list(self.task_scope, [SCREEN_TAG])
        records = await self._get_many([s.id for s in summaries])
        results = [r for r in map(parse_screen_record, records) if r is not None]
        # Stable: the listing's own order (newest first) breaks ties.
        results.sort(key=lambda r: (r.run_id, r.round_no), reverse=True)
        return results

    async def record_rc_built(
        self, exp_mem_id: str, rc_id: str, change: RcChange | None = None
    ) -> None:
        """Append `rc: <id>` (and the build's one-line `change:` summary) to
        the RUNNING record the moment the build lands. Without
        it a record abandoned between build and analysis cannot name the
        run config it built, and the next run's planner rebuilds it."""
        record = await self._get_experiment(exp_mem_id)
        self._require_move(record, ExperimentState.VERDICT, "record its build")
        lines = rc_built_lines(rc_id, change.summary if change else None)
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, lines)}
        )

    async def record_rc_rebuilt(
        self, exp_mem_id: str, rc_id: str, change: RcChange | None = None
    ) -> None:
        """The one rebuild after a generality leak (`kiln_harness_tuner.generality`):
        the RUNNING record's `rc:` and `change:` lines are REPLACED, not
        appended. Readers take the first value per key, so an appended line
        would leave the refused build as the record's run config — the one a
        later planner reuses or merges. The refused id survives only in the
        `generality:` line."""
        record = await self._get_experiment(exp_mem_id)
        self._require_move(record, ExperimentState.VERDICT, "record its rebuild")
        kept = "\n".join(
            line
            for line in (record.content or "").splitlines()
            if (pair := kv_line(line)) is None or pair[0] not in ("rc", "change")
        )
        lines = rc_built_lines(rc_id, change.summary if change else None)
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(kept or None, lines)}
        )

    async def record_generality(self, exp_mem_id: str, finding: str) -> None:
        """Append the generality check's `generality:` line to the RUNNING
        record (`kiln_harness_tuner.generality`): a leak it found, the one rebuild,
        or why it could not check. The line is carried past the verdict
        (`CARRIED_KEYS`), so the planner reads it on the finished record."""
        record = await self._get_experiment(exp_mem_id)
        self._require_move(record, ExperimentState.VERDICT, "record its check")
        line = render_kv([("generality", head_value(finding, FINDING_MAX_CHARS))])
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, line)}
        )

    async def record_judge_copy(self, exp_mem_id: str, finding: str) -> None:
        """Append the build-time judge-copy check's `judge copy:` line to the
        RUNNING record (`kiln_harness_tuner.judge_copy`): a copy it
        found, the one rebuild, or why it could not check. Carried past the
        verdict (`CARRIED_KEYS`), like the `generality:` line."""
        record = await self._get_experiment(exp_mem_id)
        self._require_move(record, ExperimentState.VERDICT, "record its check")
        line = render_kv([("judge copy", head_value(finding, FINDING_MAX_CHARS))])
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, line)}
        )

    async def record_reference_check(self, exp_mem_id: str, finding: str) -> None:
        """Append the reference check's `reference check:` line to the
        RUNNING record (`kiln_harness_tuner.reference_check`): what the
        build names and does not attach, the one rebuild under `refuse`, or
        why it could not check. Carried past the verdict (`CARRIED_KEYS`),
        like the `generality:` line."""
        record = await self._get_experiment(exp_mem_id)
        self._require_move(record, ExperimentState.VERDICT, "record its check")
        line = render_kv([("reference check", head_value(finding, FINDING_MAX_CHARS))])
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, line)}
        )

    async def record_residual(self, exp_mem_id: str, gate: ResidualGateResult) -> None:
        """Append the residual-minibatch gate's lines to the RUNNING record: the result,
        the items still failing and the guards broken — `eval: id, id; eval: id` — so
        the next planner can read those traces and target them with a different
        build."""
        record = await self._get_experiment(exp_mem_id)
        self._require_move(record, ExperimentState.VERDICT, "record its gate")
        lines = [
            f"residual gate: {gate.summary} (needed {gate.required}; seed {gate.seed})",
            f"residual still failing: {_by_eval(gate.still_failing)}",
            f"residual guards broke: {_by_eval(gate.broken_guards)}",
        ]
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, lines)}
        )

    async def record_verdict(self, exp_mem_id: str, res: ExperimentResult) -> None:
        """PATCH the analyzer's verdict over the `— RUNNING` record. A
        refuted experiment (successful=False) gets a ` — REFUTED` overview
        suffix: the numbers tail alone reads
        success-shaped in memory listings. Suffix-protected like the other
        lifecycle stamps — it survives the overview cap whole, AFTER the
        front-loaded label + numbers tail.

        The verdict content REPLACES the opening block, so the three lines
        later readers depend on are carried forward from the stored record:
        `issue:` and `technique:` (appended only when the analyzer dropped
        them) and `head_to_head_group:`, which ALWAYS lands last — inside
        `_fit_content`'s protected tail region, so it survives the over-cap
        clamp even when the analyzer re-cited it mid-content. The stored
        group is the source of truth; an analyzer variant never wins."""
        # A `(technique)` after the label is the opening's shape, which reads
        # UNKNOWN with no suffix — the verdict drops it.
        overview = verdict_overview(res.memory_overview)
        if not res.successful:
            refuted = experiment_suffix(ExperimentState.REFUTED)
            # removesuffix keeps an agent-authored suffix from doubling.
            overview = _fit_stateful("", overview.removesuffix(refuted), refuted)
        content = res.memory_content
        stored = await self._get_experiment(exp_mem_id)
        self._require_move(stored, ExperimentState.VERDICT, "record a verdict")
        carried = {
            "issue": stored.issue_id,
            "technique": stored.technique,
            "rc": stored.rc_id,
            "reused rc": stored.reused_rc_id,
            "change": stored.change_summary,
            "generality": stored.generality,
            "judge copy": stored.judge_copy,
            "reference check": stored.reference_check,
        }
        for key in CARRIED_KEYS:
            value = carried[key]
            if value is not None and kv_value(content, key) is None:
                line = render_kv([(key, value)])
                content = f"{content.rstrip()}\n{line}" if content else line
        group = stored.head_to_head_group or kv_value(content, "head_to_head_group")
        if group:
            body = "\n".join(
                line
                for line in content.splitlines()
                if (pair := kv_line(line)) is None or pair[0] != "head_to_head_group"
            ).rstrip()
            line = head_to_head_line(group)
            content = f"{body}\n{line}" if body else line
        await self._patch(exp_mem_id, {"overview": overview, "content": content})

    @staticmethod
    def _require_move(record: ExperimentView, to: ExperimentState, doing: str) -> None:
        """`EXPERIMENT_TRANSITIONS`, enforced at the writers: a result is
        final, and a stamped record is a result by definition."""
        stamped = record.stamped
        allowed = EXPERIMENT_TRANSITIONS[record.state]
        if to not in allowed or stamped:
            raise ExperimentStateError(
                f"experiment {record.id} is {record.state.value}"
                f"{' and stamped' if stamped else ''} — refusing to {doing} "
                f"(allowed moves: {sorted(s.value for s in allowed) or 'none'})"
            )

    @staticmethod
    def _require_stampable(record: ExperimentView, doing: str) -> None:
        """A gate stamp goes on a RESULT (`STAMPABLE_STATES`): the gate only
        ever compares verdicts, so anything else is a wrong id."""
        if record.state not in STAMPABLE_STATES:
            raise ExperimentStateError(
                f"experiment {record.id} is {record.state.value}, not a verdict "
                f"— refusing to {doing}"
            )

    async def _restate(
        self, exp_mem_id: str, state: ExperimentState, detail: str
    ) -> None:
        """Write a no-result state over the current one — RUNNING → FAILED /
        ABANDONED / DEAD END, FAILED → ABANDONED. Any other move is refused
        (`_require_move`), so a result is never overwritten."""
        record = await self._get_experiment(exp_mem_id)
        self._require_move(record, state, f"mark it {state.value}")
        base = strip_state_suffix(record.overview, record.state)
        # Suffix-protected clamp: the new state must survive whole even on
        # an already-full overview (the base may lose tail chars instead).
        await self._patch(
            exp_mem_id,
            {"overview": _fit_stateful("", base, experiment_suffix(state, detail))},
        )

    async def mark_failed(self, exp_mem_id: str, err: ErrorInfo) -> None:
        """Written the moment an item goes terminally failed_infra —
        no memory stays `— RUNNING` inside a live run."""
        await self._restate(exp_mem_id, ExperimentState.FAILED, err.error_class)

    async def mark_abandoned(self, exp_mem_id: str, reason: str) -> None:
        await self._restate(exp_mem_id, ExperimentState.ABANDONED, reason)

    async def mark_not_landed(self, exp_mem_id: str, detail: str) -> None:
        """A stop rule of the measurement engine ended the arm before its
        queue was done:
        the build did not do what its hypothesis said, or not well enough,
        so the rest of the split was not paid for. Not a dead end — the
        lever is untested; a different build is fair game for a later
        wave."""
        await self._restate(exp_mem_id, ExperimentState.NOT_LANDED, detail)

    async def mark_capped(self, exp_mem_id: str, detail: str) -> None:
        """The arm's measurement reached its spending cap
        (`kiln_harness_tuner.budget`): inconclusive — neither a dead end nor a
        failure. Its rows are stored; a later arm on the same rc with a
        larger cap goes on from them."""
        await self._restate(exp_mem_id, ExperimentState.CAPPED, detail)

    async def mark_dead_end(self, exp_mem_id: str, reason: str) -> None:
        """The build agent PROVED the
        lever cannot work (a `failed_expected` build — e.g. an inert provider
        knob). A decision, not retryable infra: `— FAILED (infra: …)` invites
        a later wave to retry a disproven lever, which is exactly what agents
        were writing defensive `dead_end` memories to prevent."""
        await self._restate(exp_mem_id, ExperimentState.DEAD_END, reason)

    async def janitor_abandoned_experiments(self) -> list[str]:
        """The startup janitor: `— RUNNING` overviews are crash debris from
        a dead orchestrator (one run per task at a time) — never a
        live run's cleanup. Returns the patched memory ids.

        The scan reads only summary overviews/ids — deliberately NO
        hydration round-trip; mark_abandoned's own `_get` hydrates just the
        stamped records."""
        stamped = []
        for summary in await self._list(self.task_scope, [RecordKind.EXPERIMENT.value]):
            overview = summary.overview
            if experiment_state(overview) is ExperimentState.RUNNING and not (
                is_promoted(overview) or is_rejected_at_val(overview)
            ):
                await self.mark_abandoned(
                    summary.id, f"orchestrator restart {self._date()}"
                )
                stamped.append(summary.id)
        return stamped

    async def stamp_promoted(self, exp_mem_id: str) -> None:
        """Skipped when already present: the promotion apply's retry ladder
        re-runs a write whose reply was lost, and a second stamp would read
        as two promotions."""
        record = await self._get_experiment(exp_mem_id)
        if record.promoted:
            return
        self._require_stampable(record, "stamp it promoted")
        await self._patch(
            exp_mem_id,
            {
                "overview": _fit_stateful(
                    "", record.overview, promoted_stamp(self._date())
                )
            },
        )

    async def stamp_rejected(self, exp_mem_id: str) -> None:
        """The loser's twin of stamp_promoted: every candidate a
        PromotionDecision compared but passed over — the zero-winner case
        included. Suffix-protected like the other lifecycle stamps, and
        skipped when already present so a re-entered gate can't double-stamp."""
        record = await self._get_experiment(exp_mem_id)
        if record.rejected_at_val:
            return
        self._require_stampable(record, "stamp it rejected")
        await self._patch(
            exp_mem_id,
            {
                "overview": _fit_stateful(
                    "", record.overview, rejected_stamp(self._date())
                )
            },
        )

    async def stamp_held(self, exp_mem_id: str, constraint_id: str) -> None:
        """The gate escalated on an OPEN constraint instead of judging the
        numbers. Idempotent; a result only, like the other
        stamps. The record keeps its verdict and names the constraint, so a
        later planner can resubmit the same rc once a human answers."""
        record = await self._get_experiment(exp_mem_id)
        if record.held_on is not None:
            return
        self._require_stampable(record, "stamp it held")
        await self._patch(
            exp_mem_id,
            {
                "overview": _fit_stateful(
                    "", record.overview, held_stamp(self._date(), constraint_id)
                )
            },
        )

    async def note_held(self, exp_mem_id: str, constraint_id: str) -> None:
        record = await self._get_experiment(exp_mem_id)
        if record.held_noted:
            return
        line = held_note(self._date(), constraint_id)
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, line)}
        )

    async def note_gate_block(self, exp_mem_id: str, reasons: list[str]) -> None:
        """The gate's integrity block, on the candidate's record:
        one `- blocked at the val gate <date>: <reasons> — …` line that names
        the copied phrase and the eval object. Metric-free. Skipped when a
        block line is already there. The `— REJECTED AT VAL` stamp goes with
        it (`stamp_rejected`): the run config is never resubmitted
        unchanged."""
        record = await self._get_experiment(exp_mem_id)
        if record.gate_block is not None:
            return
        line = gate_block_note(self._date(), reasons)
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, line)}
        )

    async def note_rejection(self, exp_mem_id: str, baseline_rc_id: str) -> None:
        """The rejection's content line — frozen format, METRIC-FREE by
        construction: val numbers stay confined to the `val` record,
        so this line carries status intent only and points at the run report
        for the numbers."""
        record = await self._get_experiment(exp_mem_id)
        if record.rejection_noted:
            return
        line = rejection_note(self._date(), baseline_rc_id)
        await self._patch(
            exp_mem_id, {"content": _append_experiment_content(record.content, line)}
        )

    # ---- baseline promotion (apply step (a)) ----

    async def promote(
        self, decision: PromotionDecision, winner_label: str, old: MemoryRecord
    ) -> None:
        """PATCH the singleton `baseline` record — the pointer move, and
        **metric-free**. The rc id moves to the `rc_<id>` tag,
        the winner joins the head of the promotion chain, and the overview is
        human prose nothing parses.

        `decision.rationale` — the agent's val summary — does NOT appear
        here. That is the whole point of the split: the boundary between the
        planner-visible pointer and val metrics is the record, not a
        substring of an overview. `record_val_stats` writes the numbers."""
        assert decision.winner_rc_id is not None  # callers gate on a winner
        # `winner_rc_id` is agent-supplied and only ever MATCHED against the
        # candidate set (`_apply_promotion`) — a foreign value still reaches
        # this write. Collapsed like the other agent strings: unsanitized, a
        # newline would fork one chain line into two bogus lineage entries
        # and ride into the `rc_` tag. An invented rc merely 404s; a broken
        # chain costs cache screens on every later wave.
        winner_rc = one_line(decision.winner_rc_id)
        await self._patch(
            old.id,
            {
                "overview": baseline_promoted_overview(
                    winner_rc, self._date(), winner_label
                ),
                # Newest first, clamped oldest-first — what survives is the
                # recent lineage the screen actually walks.
                "content": _fit_content_keep_head(
                    baseline_chain([winner_rc, *self.baseline_lineage(old)])
                ),
                "tags": tags_with_baseline_rc(old.tags, winner_rc),
            },
        )

    async def record_val_stats(
        self,
        decision: PromotionDecision,
        winner_label: str,
        previous_rc_id: str,
    ) -> None:
        """Append this promotion's val summary to the singleton `val` record
        — the loop's ONLY val-metrics memory write.
        Created on first use, so onboarding seeds `baseline` alone.

        Reporting-only by design: the caller treats a failure here as
        log-and-continue, because a promotion must not fail on a stats write.
        The agent-authored `rationale` is unbounded and rides in CONTENT,
        where the 4,000-char cap and the clamp already handle it — the
        overview is a numberless constant so that listing `tags=val` (which
        returns overviews) leaks nothing by itself."""
        assert decision.winner_rc_id is not None  # callers gate on a winner
        line = val_stats_line(
            winner_rc_id=decision.winner_rc_id,
            date=self._date(),
            winner_label=winner_label,
            previous_rc_id=previous_rc_id,
            rationale=decision.rationale,
        )
        found = await self._list(self.task_scope, [VAL_STATS_TAG])
        if not found:
            await self._post(
                VAL_STATS_OVERVIEW,
                self.task_scope,
                _fit_content_keep_head(line),
                [VAL_STATS_TAG],
            )
            return
        # Singleton by convention, first row if that's ever violated — the
        # same choice `find_baseline_record` documents. Worth stating: a
        # forked `val` record would split the promotion history across two
        # records, and every later write would land on whichever this picks
        # while the other silently stopped growing.
        record = await self._get(found[0].id)
        # Newest first, matching the chain — the clamp drops the OLDEST
        # promotions, and the overview is a constant we re-assert so a
        # hand-edited numbers-in-the-overview record self-heals.
        content = f"{line}\n{record.content}" if record.content else line
        await self._patch(
            record.id,
            {
                "overview": VAL_STATS_OVERVIEW,
                "content": _fit_content_keep_head(content),
            },
        )

    # ---- issue lifecycle ----

    async def mark_fixed(self, issue_id: str, citation: str) -> None:
        issue = await self._get_issue(issue_id)
        # Replaces whatever state the issue was in — a seeded issue closed by
        # a promotion reads `FIXED: issue: …`, never `FIXED: UNMEASURED: …`.
        await self._patch(
            issue_id,
            {
                "overview": set_state_prefix(issue.overview, IssueState.FIXED),
                "content": _append_content(
                    issue.content, IssueNote.fixed(self._date(), citation)
                ),
            },
        )

    async def flag_regressed(self, issue_id: str, rc_id: str) -> None:
        """Code composes the standard line either way — metric-free by
        construction (no val numbers exist in the inputs)."""
        issue = await self._get_issue(issue_id)
        if issue.state is IssueState.FIXED:
            # Exactly ONE prefix is cleared, and only FIXED:. A promotion-time
            # repro line is not a train-side measurement, so an UNMEASURED:
            # stacked behind a stale FIXED: (the shape the OLD mark_fixed
            # wrote, so it exists in live stores) must survive — clearing it
            # would report an unmeasured issue to the planner as measured.
            # record_baseline_measurement owns that flip, nothing else.
            overview = issue.overview.removeprefix(IssueState.FIXED.prefix)
            line = IssueNote.reopened(rc_id)
        else:
            overview = issue.overview
            line = IssueNote.repros(rc_id)
        await self._patch(
            issue_id,
            {"overview": overview, "content": _append_content(issue.content, line)},
        )

    async def mark_partial(self, issue_id: str, rc_id: str) -> None:
        """The promotion apply's merged write for an
        issue that both landed in the winner AND regressed on val — one
        metric-free line, replacing BOTH mark_fixed and flag_regressed for
        that issue. The issue stays open: no `FIXED:` prefix, and a stale one
        (fixed by an earlier promotion, re-targeted this run) is stripped —
        "still repros" and a FIXED: overview cannot coexist (same reopen
        semantics as flag_regressed)."""
        issue = await self._get_issue(issue_id)
        body: dict[str, Any] = {
            "content": _append_content(issue.content, IssueNote.partial(rc_id))
        }
        if issue.state is IssueState.FIXED:
            # Single strip, FIXED: only — see flag_regressed's note.
            body["overview"] = issue.overview.removeprefix(IssueState.FIXED.prefix)
        await self._patch(issue_id, body)

    @staticmethod
    def _refuse_if_terminal(issue: IssueView, doing: str) -> None:
        """`ISSUE_TERMINAL_STATES`' doctrine — the loop never un-closes an
        issue — enforced at the writers that would otherwise bypass it.

        Both writers below set the lifecycle prefix through
        `set_state_prefix`, which strips the whole prefix run. On a `FIXED:`
        record that silently becomes `WONT_FIX:`, relabelling a landed fix as
        a decision and losing the rc that fixed it. The sync honors this;
        these two are the paths a planner-supplied issue id
        reaches, where only skill prose stood between a closed issue and a
        rewrite."""
        if issue.state in ISSUE_TERMINAL_STATES:
            raise IssueTerminalStateError(
                f"issue {issue.id} is already {issue.state} — refusing to "
                f"{doing} over a terminal state (the loop never un-closes an "
                "issue; re-open it first if it should be reconsidered)"
            )

    async def mark_wont_fix(self, issue_id: str, reason: str) -> None:
        issue = await self._get_issue(issue_id)
        self._refuse_if_terminal(issue, "write a not-reproduced verdict")
        await self._patch(
            issue_id,
            {
                "overview": set_state_prefix(issue.overview, IssueState.WONT_FIX),
                "content": _append_content(
                    issue.content, IssueNote.wont_fix(self._date(), reason)
                ),
            },
        )

    async def link_eval(self, issue_id: str, rep: EvalBuildReport) -> None:
        """The durable eval↔issue link: lets later planners set
        `eval_id` + `needs_eval=false`, and promotion map a regressed val
        eval back to its issue.

        Writes the `eval_<id>` binding TAG alongside the content line
        — the one change that makes an issue the loop
        discovers mid-run bind exactly like a setup-seeded one, so the next
        startup's coverage pass doesn't seed a duplicate."""
        issue = await self._get_issue(issue_id)
        await self._patch(
            issue_id,
            {
                "content": _append_content(
                    issue.content, IssueNote.eval_built(rep.eval_id, self._date())
                ),
                "tags": tags_with_eval(issue.tags, rep.eval_id),
            },
        )

    # ---- issue records as derived children of an eval ----

    def _compose_setup_head(
        self,
        view: EvalView,
        description: str,
        fingerprint: str,
        state: IssueState,
    ) -> str:
        """The setup-owned head, replaced wholesale on refresh —
        `memory_schema.SetupHead` owns the layout and the budget."""
        source = f"spec {view.priority_spec_id}" if view.priority_spec_id else "default"
        return SetupHead(
            description=description,
            eval_id=view.id,
            eval_name=view.name,
            config_kind=view.config_kind,
            priority=view.priority,
            priority_source=source,
            # "at sync" because this word is a SNAPSHOT: every lifecycle
            # writer moves the overview prefix without touching the head, so
            # the prefix — not this line — is the authoritative state.
            state_at_sync=state.value,
            fingerprint=fingerprint,
            synced=self._iso_date(),
        ).render()

    @staticmethod
    def _issue_overview(problem: str, prefix_run: str) -> str:
        """The agent's one-line problem statement under a lifecycle prefix
        (or a stacked run of them, re-applied verbatim by `refresh_issue`).
        Prefix-protected by the clamp — the prefix is the vocabulary the
        whole loop reads."""
        return _fit_stateful(
            f"{prefix_run}{ISSUE_OVERVIEW_LEAD}", one_line(problem), ""
        )

    async def find_baseline_audit(
        self, rc_id: str, tag: str = BASELINE_AUDIT_TAG
    ) -> str | None:
        """The open baseline-audit issue for `rc_id`, or None: the audit
        runs once per baseline, not once per run. `tag`: which half of the
        audit — train copies (`baseline_audit`) or eval text
        (`baseline_judge_copy`)."""
        rows = await self._list(
            self.task_scope,
            [
                RecordKind.ISSUE.value,
                tag,
                f"{AUDITED_RC_TAG_PREFIX}{rc_id}",
            ],
        )
        for row in rows:
            if issue_state(row.overview) not in ISSUE_TERMINAL_STATES:
                return row.id
        return None

    async def record_baseline_audit(self, rc_id: str, findings: list[str]) -> str:
        """POST the issue for train-specific text the baseline already
        carries (`generality.audit_baseline`). Loop-owned (no
        setup head) and bound to no eval: the generality check is what
        measures it. Returns the memory id."""
        lines = "\n".join(f"- {one_line(f)}" for f in findings)
        content = (
            f"The baseline run config {rc_id} already carries text that copies"
            " the train set. The audit at setup found it before any build, so"
            " the generality check on builds — which reads only the text a"
            " build adds — never sees it.\n"
            f"findings:\n{lines}\n"
            "What to do: scrub it. Build a new version of that prompt, skill or"
            " code tool with each finding replaced by the general rule it"
            " stands in for, and copy none of its values into new text. Plan"
            " it as a fix arm with needs_eval false and eval_id set to an"
            " active eval the text serves, or put the scrub into the next arm"
            " that rewrites the same text. A scrub is a correctness fix, not a"
            " score fix: where the copied data helped on train, train can fall."
        )
        return await self._post(
            self._issue_overview(
                f"the baseline's own text copies train items ({len(findings)}"
                " finding(s)) — scrub it",
                IssueState.OPEN.prefix,
            ),
            self.task_scope,
            content,
            [
                RecordKind.ISSUE.value,
                priority_tag(DEFAULT_ISSUE_PRIORITY),
                BASELINE_AUDIT_TAG,
                f"{AUDITED_RC_TAG_PREFIX}{rc_id}",
            ],
        )

    async def record_baseline_judge_copy(self, rc_id: str, findings: list[str]) -> str:
        """POST the issue for eval text the baseline already carries
        (`generality.audit_baseline`). The same shape as the
        train-copy issue above. The build-time judge-copy check exempts
        text the baseline carries, so this issue is where such a phrase is
        reported. Returns the memory id."""
        lines = "\n".join(f"- {one_line(f)}" for f in findings)
        content = (
            f"The baseline run config {rc_id} already carries text that copies"
            " an eval: a run of eight words from a judge prompt, an eval"
            " config, a spec or an eval description, or an eval's full name."
            " The build-time judge-copy check exempts text the baseline"
            " carries, so builds do not find it.\n"
            f"findings:\n{lines}\n"
            "What to do: scrub it. Build a new version of that prompt, skill or"
            " code tool that states each rule in its own words and names no"
            " eval. Plan it as a fix arm with needs_eval false and eval_id set"
            " to the eval the text copies, or put the scrub into the next arm"
            " that rewrites the same text. A scrub is a correctness fix, not a"
            " score fix: where the copied text helped on train, train can fall."
        )
        return await self._post(
            self._issue_overview(
                f"the baseline's own text copies eval text ({len(findings)}"
                " finding(s)) — scrub it",
                IssueState.OPEN.prefix,
            ),
            self.task_scope,
            content,
            [
                RecordKind.ISSUE.value,
                priority_tag(DEFAULT_ISSUE_PRIORITY),
                BASELINE_JUDGE_COPY_TAG,
                f"{AUDITED_RC_TAG_PREFIX}{rc_id}",
            ],
        )

    async def seed_issue(
        self, view: EvalView, description: EvalDescription, fingerprint: str
    ) -> str:
        """POST the derived `issue` record for an active eval nothing covers.
        Returns the memory id.

        The delimiter is written immediately, even with an empty loop region,
        so the ownership boundary exists from the record's birth — every
        later appender then hits the same shape.

        `fingerprint` is passed rather than computed: it is `setup.py`'s,
        and importing it here would close the module cycle."""
        head = self._compose_setup_head(
            view, description.description, fingerprint, IssueState.UNMEASURED
        )
        return await self._post(
            self._issue_overview(description.overview, IssueState.UNMEASURED.prefix),
            self.task_scope,
            join_owned(head, ""),
            [RecordKind.ISSUE.value, priority_tag(view.priority), eval_tag(view.id)],
        )

    async def refresh_issue(
        self,
        issue_id: str,
        view: EvalView,
        description: EvalDescription,
        fingerprint: str,
    ) -> None:
        """Re-sync a setup-owned issue whose eval drifted:
        new head, priority tag replaced (priority is source-owned), one note
        appended to the loop region — and the loop region itself preserved
        byte-for-byte.

        The lifecycle prefix survives VERBATIM: a rewritten rubric does not
        reopen a FIXED: issue, so only the agent's prose is replaced. The
        whole prefix run is re-applied, not a prefix re-derived from
        `issue_state` — that answers with the first prefix only, and would
        drop the second on a stacked legacy overview."""
        issue = await self._get_issue(issue_id)
        head = self._compose_setup_head(
            view, description.description, fingerprint, issue.state
        )
        note = IssueNote.resynced(self._date())
        tail = issue.loop_tail
        await self._patch(
            issue_id,
            {
                "overview": self._issue_overview(
                    description.overview, state_prefix_run(issue.overview)
                ),
                "content": join_owned(head, f"{tail}\n{note}" if tail else note),
                "tags": tags_with_eval(
                    tags_with_priority(issue.tags, view.priority), view.id
                ),
            },
        )

    async def bind_eval(self, issue_id: str, eval_id: str, *, add_line: bool) -> None:
        """Repair an issue that should have carried a binding all along
        Additive: the tag, plus — when the record only
        mentioned the eval in prose — the missing eval content line.

        A missing priority tag is filled in too. A repaired issue is not
        setup-owned, so `refresh_issue` never touches it — this is its ONLY
        chance at the "a priority tag is always present" invariant,
        and records tagged just `["issue"]` do turn up live. The
        backfill lands BEFORE the eval tag so a repaired record ends up in
        seed_issue's tag order (`issue`, `p<n>`, `eval_<id>`)."""
        issue = await self._get_issue(issue_id)
        tags = list(issue.tags)
        if priority_from_tags(tags) is None:
            tags = tags_with_priority(tags, DEFAULT_ISSUE_PRIORITY)
        body: dict[str, Any] = {"tags": tags_with_eval(tags, eval_id)}
        if add_line:
            body["content"] = _append_content(
                issue.content, IssueNote.eval_linked(eval_id, self._date())
            )
        await self._patch(issue_id, body)

    async def flag_unmeasurable(self, issue_id: str, cause: str) -> None:
        """The eval an issue binds to is gone or has left the active set.
        Never deleted, never closed — the finding may
        still be true; one marker for both causes, the cause in the line.

        **Caller's obligation**: never on an issue in a terminal state. The
        marker REPLACES the overview prefix, so flagging a `FIXED:` issue
        would erase a decision the loop made — the mirror image of "never closes an
        issue on its own". `build_sync_plan` is the guard (a closed issue's eval
        disappearing is not news)."""
        issue = await self._get_issue(issue_id)
        await self._patch(
            issue_id,
            {
                "overview": set_state_prefix(issue.overview, IssueState.UNMEASURABLE),
                "content": _append_content(
                    issue.content, IssueNote.unmeasurable(cause, self._date())
                ),
            },
        )

    async def record_baseline_measurement(
        self,
        issue_id: str,
        *,
        reproduced: bool,
        rc_id: str,
        summary: str,
        expected_eval_id: str,
    ) -> None:
        """The measure_issue_baseline outcome: the baseline
        either fails the issue's eval — normal fuel from here — or passes it,
        in which case there is no issue to fix on this baseline.

        `expected_eval_id` is checked against the record's own bindings
        before anything is written. The planner supplies the issue id and the
        eval id independently, and a mismatched pair would stamp terminal
        state onto issue A from eval B's numbers — with a memory line that
        reads entirely plausibly. The binding tag makes that machine-checkable,
        so it is checked. An issue carrying NO `eval_`
        tag is still permitted: an unbound issue is a state the loop creates
        and later repairs, not a mismatch.

        An issue already in a **terminal** state is refused outright, by the
        same `_refuse_if_terminal` guard `mark_wont_fix` uses — see its
        docstring."""
        issue = await self._get_issue(issue_id)
        bound = issue.bound_eval_ids
        if bound and expected_eval_id not in bound:
            raise IssueBindingMismatchError(
                f"issue {issue_id} is bound to eval(s) {sorted(bound)}, not "
                f"{expected_eval_id} — refusing to write a baseline "
                "measurement from an eval that does not measure it"
            )
        self._refuse_if_terminal(issue, "write a baseline measurement")
        state = IssueState.OPEN if reproduced else IssueState.WONT_FIX
        await self._patch(
            issue_id,
            {
                "overview": set_state_prefix(issue.overview, state),
                "content": _append_content(
                    issue.content,
                    IssueNote.baseline_measurement(
                        reproduced=reproduced,
                        rc_id=rc_id,
                        date=self._date(),
                        summary=summary,
                    ),
                ),
            },
        )

    # ---- open unknowns ----

    async def write_open_unknowns(self, questions: Iterable[OpenUnknown]) -> list[str]:
        """POST-vs-PATCH rule: match existing `OPEN:` records by
        the NORMALIZED `question:` line of their hydrated content — never
        the overview: the 280-char overview clamp
        truncates long questions with `…`, so overview equality silently
        forked a new record per re-ask. Every write stores the CANONICAL
        question form (single-line, bounded — `canonical_question`), so
        the stored line and an incoming question always key identically.
        Costs the batch one listing + one (chunked)
        `/by_ids` hydration round-trip. First record wins a pre-existing
        key collision (deterministic); freshly POSTed questions join the
        map, so within-batch duplicates dedup too. Returns the written
        record ids, question order."""
        questions = list(questions)
        if not questions:
            return []
        existing: dict[str, str] = {}
        summaries = await self._list("project", [RecordKind.CONSTRAINT.value])
        for record in await self._get_many(s.id for s in summaries):
            body = OpenUnknownContent.parse(record.content)
            if body is not None and body.key not in existing:
                existing[body.key] = record.id
        ids: list[str] = []
        for q in questions:
            question = canonical_question(q.question)
            key = question_key(question)
            content = OpenUnknownContent(
                question=question,
                default=q.default_assumed,
                gates=q.gates,
                source="assumption",
                reask_when=q.reask_when,
            ).render()
            if key in existing:
                await self._patch(existing[key], {"content": content})
            else:
                existing[key] = await self._post(
                    _fit_overview(open_unknown_overview(question)),
                    "project",
                    content,
                    [RecordKind.CONSTRAINT.value],
                )
            ids.append(existing[key])
        return ids

    async def record_open_unknown(self, q: OpenUnknown) -> str:
        """Single-question convenience over write_open_unknowns."""
        (memory_id,) = await self.write_open_unknowns([q])
        return memory_id


# ---- screening results ----

# The JSON line leaves out the two latency comparisons (large; the
# `measured` line carries their numbers) and the provenance the other lines
# already carry.
_SCREEN_JSON_EXCLUDE = {"latency", "cost", "line", "prior_run"}


def screen_quality_short(result: ScreenResult) -> str:
    """ "lost 0 of 24 baseline passes, no loss rate above 12% at 95%" — a
    speed or cost screen's quality, losses only."""
    bound = (
        f", no loss rate above {result.loss_bound:.0%} at 95%"
        if result.loss_bound is not None
        else ""
    )
    return f"lost {result.broke} of {result.baseline_passed} baseline passes{bound}"


def screen_record_pairs(result: ScreenResult) -> list[tuple[str, str]]:
    """The content of a screening record, one `key: value` line each. Under
    speed and cost (`loss_bound` set) the `quality` line is the losses on
    the baseline's passes with their bound, and the fixes apart."""
    judged = sum(v.get("n", 0) for v in result.per_eval.values())
    quality = (
        f"{screen_quality_short(result)}; fixed {result.fixed} baseline"
        f" failures (noise level, not set against the losses)"
        if result.loss_bound is not None
        else f"passed {result.passed}/{judged} vs baseline {result.baseline_passed}"
        f" (fixed {result.fixed}, broke {result.broke})"
    )
    return [
        ("candidate", result.candidate_id),
        ("run config", str(result.rc_id)),
        ("label", result.label),
        ("overrides", json.dumps(result.overrides, sort_keys=True)),
        ("parent rc", str(result.parent_rc_id or "the baseline")),
        ("round", str(result.round)),
        ("round no", str(result.round_no)),
        ("items", str(result.items)),
        ("screened on evals", ", ".join(result.evals) or "none"),
        ("verdict", result.verdict),
        ("reason", result.reason or "-"),
        ("quality", quality),
        ("measured", result.line),
        ("run", result.run_id or "-"),
        ("date", result.date or "-"),
        ("baseline rc", str(result.baseline_rc_id or "-")),
        (
            "result",
            json.dumps(
                result.model_dump(mode="json", exclude=_SCREEN_JSON_EXCLUDE),
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    ]


def parse_screen_record(record: MemoryRecord) -> ScreenResult | None:
    """A `screen` record back as a `ScreenResult` marked `prior_run`, or
    None when it is not one."""
    content = record.content or ""
    measured = kv_value(content, "measured") or ""
    raw = kv_value(content, "result")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return ScreenResult.model_validate(
                    {**data, "line": measured, "prior_run": True}
                )
        except ValueError:
            pass
    if not record.overview.startswith("SCREEN DEAD END: "):
        return None
    # A dead-end record without a result line: its key lines only.
    label = record.overview.removeprefix("SCREEN DEAD END: ").split(" — ")[0]
    rc_id = kv_value(content, "run config")
    evals = kv_value(content, "screened on evals") or "none"
    try:
        size = int(kv_value(content, "round") or 1)
    except ValueError:
        size = 1
    return ScreenResult(
        candidate_id=kv_value(content, "candidate") or "?",
        label=label,
        rc_id=None if rc_id in (None, "None") else rc_id,
        round=size,
        evals=[] if evals == "none" else evals.split(", "),
        items=0,
        verdict="dead_end",
        reason=kv_value(content, "reason") or "",
        line=measured,
        prior_run=True,
    )


def _by_eval(ids: dict[str, list[str]]) -> str:
    """`e1: a, b; e2: c`, or `none`."""
    return "; ".join(f"{e}: {', '.join(v)}" for e, v in sorted(ids.items())) or "none"
