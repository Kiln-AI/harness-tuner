"""The pre-flight setup phase (docs/architecture.md section 5): make
`uv run kiln-harness-tuner …` work on a task nobody prepared, and keep it in step on
every later invocation.

`run_setup` is the whole phase and the single entrypoint `orchestrate()` (and
any other caller) invokes: the baseline decision, the eval↔issue sync with
its description fan-out, and the split readiness audit, in that order.
Everything it can fail on is instructive; most of it is also free, but
NOT all of it — the description fan-out spends, and the split audit
deliberately runs after it (see `SetupError` and `audit_splits`).

The seam is deliberate and mirrors the existing one: **setup decides,
`memory.py` writes.** What an issue record looks like — the head layout,
the ownership delimiter, the state prefixes, the binding tag — is
`memory_schema.py`'s (`IssueView`, `SetupHead`); this module reads the
typed view and parses nothing itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.items import call_with_retries
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.memory import (
    BASELINE_RULE_MOST_RECENT,
    BASELINE_RULE_TASK_DEFAULT,
)
from kiln_harness_tuner.memory_schema import (
    BASELINE_AUDIT_TAG,
    BASELINE_JUDGE_COPY_TAG,
    DEFAULT_ISSUE_PRIORITY,
    ISSUE_TERMINAL_STATES,
    IssueState,
    IssueView,
)
from kiln_harness_tuner.schemas import (
    SUBSCRIPTION_LANE,
    DescribeEvalInput,
    EvalDescription,
    SetupReport,
)

if TYPE_CHECKING:  # orchestrator.py imports run_setup from here — a runtime
    from kiln_harness_tuner.orchestrator import Ctx  # import would close the cycle.


logger = logging.getLogger(__name__)


class SetupError(Exception):
    """Base of every fatal the setup phase raises. All three carry an
    instructive message rather than a stack trace (`cli.py` catches this
    base), and all three land before the loop's own spend — the waves, the
    val passes, the eval jobs.

    **They are not all free.** The description fan-out is real dispatch
    spend, and it happens before both of the fatals that can follow it:

    - `SetupDescribeError` IS that spend failing — by design (a cheap agent
      against an expensive loop). Its siblings' sessions are paid for
      and, since nothing is written on that path, the next invocation
      re-describes them.
    - `EvalSplitAuditError` comes after a full successful fan-out: on a cold
      wide task, 28 sessions before the run dies. That ordering is
      deliberate — a cold project must get its issue store seeded on the SAME
      invocation that tells its owner what to fix, one round trip rather than
      two. The spend is not wasted: those
      descriptions are written to memory and the next run reuses them (the
      fingerprint is unchanged), so the owner pays for them once — unless
      they answer the audit by deactivating the evals instead of adding
      splits, in which case those seeded issues were work nobody wanted.

    The alternative — auditing before the fan-out — would buy that back by
    aborting a cold project before it is seeded at all, which is the outcome
    this design rejects."""


class SetupPreconditionError(SetupError):
    """A precondition setup cannot repair — fatal, and (for every case but
    the missing-description guard in `_require_description`) raised before any
    dispatch. The message is written for a human at a terminal AND a calling
    agent; `cli.py` lands it without a traceback."""


class EvalSplitAuditError(SetupError):
    """An active eval has no train and/or val split, so the loop cannot
    measure or gate on it. Fatal, listing EVERY failing eval at once — setup
    audits, owners repair. Raised after the
    sync (and so after its spend) — see `SetupError` for why."""


class SetupDescribeError(SetupError):
    """A `describe_eval` dispatch failed. Deliberately harsher than the
    item ladder — a description agent costs pennies and a full run costs
    hours, and seeding against an incomplete requirement set means paying the
    loop's cost twice."""

    def __init__(self, eval_id: str, eval_name: str, reason: str, *, attempts: int):
        self.eval_id = eval_id
        # Counted by the ladder, never inferred here: it returns without
        # retrying on a non-`failed_infra` status AND on `parse_failure` (the
        # likeliest failure for an agent that must return a fenced envelope),
        # so anything derived from `status` misreports one of them.
        attempt = (
            "on its first attempt" if attempts == 1 else f"after {attempts} attempts"
        )
        super().__init__(
            f"could not describe eval {eval_id} ({eval_name}) {attempt} "
            f"({reason}). The loop will not start against an incomplete "
            "requirement set — every active eval needs an issue memory "
            "describing it. Nothing was written for this eval; re-invoke "
            "`kiln-harness-tuner` once the cause is addressed."
        )


@dataclass(frozen=True)
class BaselineSeedOutcome:
    """Either `ALREADY_PRESENT` (a `baseline` record existed — the loop
    never re-points it) or the seed that was written. `rule`/`rc_id` feed
    the `baseline_seeded` event."""

    seeded: bool
    rc_id: str | None = None
    rule: str | None = None
    # The default rc id the task named when it could NOT be honored (not
    # among the live run configs). `rule` is `most_recent` in that case,
    # indistinguishable from a task that never had a default — so the
    # diagnosis rides here to be logged beside `baseline_seeded`: a
    # user who DID set a default deserves to know why the loop is optimizing
    # a different rc. Deliberately not in the memory record's provenance —
    # that format is string-asserted.
    unresolvable_default: str | None = None


ALREADY_PRESENT = BaselineSeedOutcome(seeded=False)


def no_run_configs_message(task_id: str) -> str:
    """In the register of `require_baseline_record`'s
    missing-record message: name the problem, then the exact fix."""
    return (
        f"task {task_id} has no run configs, so there is nothing to optimize "
        "from. Create a run config for this task (a model + prompt the task "
        "can actually run), then re-invoke `kiln-harness-tuner`. The loop "
        "measures every experiment against a baseline run config and will "
        "not invent one."
    )


# A drifted body is quoted in the error, bounded — enough to tell a paging
# envelope from an error envelope without pasting a whole response.
_RESPONSE_EXCERPT_MAX_CHARS = 120


def _describe_response(response: Any) -> str:
    """What actually came back: type + a bounded excerpt (a dict's keys, or
    a truncated repr). The fix instruction is "re-verify the shape" — so the
    message has to show the shape.

    A `str` body is sliced BEFORE `repr` — a drifted multi-megabyte HTML
    error page is the likeliest oversized body, and there's no reason to
    materialize its repr just to keep 120 chars of it."""
    if isinstance(response, dict):
        excerpt = f"keys {sorted(map(str, response))}"
    elif isinstance(response, str):
        excerpt = repr(response[:_RESPONSE_EXCERPT_MAX_CHARS])
    else:
        excerpt = repr(response)
    if len(excerpt) > _RESPONSE_EXCERPT_MAX_CHARS:
        excerpt = excerpt[: _RESPONSE_EXCERPT_MAX_CHARS - 1] + "…"
    return f"a {type(response).__name__}: {excerpt}"


def unreadable_response_message(url_path: str, response: Any) -> str:
    """`memory.py`'s `MemoryListShapeError` doctrine, borrowed: silently
    reading a drifted response is worse than failing on it — here it would
    seed a `baseline` record naming a garbage rc, which parses fine and
    poisons the whole run."""
    return (
        f"GET {url_path} did not return the shape kiln-harness-tuner setup reads (got "
        f"{_describe_response(response)}) — the route's response shape needs "
        "re-verifying against the live API before the loop can seed a "
        "baseline from it."
    )


def _run_config_id(run_config: Any) -> str:
    """A run config's id, or `""` for any row we cannot identify — the one
    place the shape is read, so the id-less row is filtered rather than
    seeded as the string `"None"`, which would become an `rc_None` baseline
    tag naming a run config every subsequent API call 404s on."""
    if not isinstance(run_config, dict):
        return ""
    return str(run_config.get("id") or "").strip()


def _recency_key(run_config: dict[str, Any]) -> tuple[bool, float, str]:
    """Sort key for `_most_recent`, read descending. Defensive by design: a
    missing or unparseable `created_at` sorts LAST instead of raising (a
    shape we can't read must not fail a run), and naive timestamps are read
    as UTC so they can never raise against the live server's offset-aware
    ones. The id breaks ties, so the pick is deterministic."""
    raw = run_config.get("created_at")
    parsed: datetime | None = None
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (
        parsed is not None,
        parsed.timestamp() if parsed is not None else 0.0,
        _run_config_id(run_config),
    )


def _most_recent(run_configs: list[dict[str, Any]]) -> dict[str, Any]:
    """The newest run config by `created_at`, ties broken by id.
    Callers pass identifiable rows only (`_usable_run_configs`)."""
    return max(run_configs, key=_recency_key)


def _usable_run_configs(rows: list[Any]) -> list[dict[str, Any]]:
    """The rows setup can actually seed from: dicts carrying a non-empty id.
    Filtering here is what keeps `_most_recent`'s tolerance honest — every
    surviving row can be named in a `baseline` record."""
    return [row for row in rows if _run_config_id(row)]


async def seed_baseline_if_missing(ctx: Ctx) -> BaselineSeedOutcome:
    """The four-state baseline decision table.

    Raises `SetupPreconditionError` when the task has no run configs — the
    state with no mechanical answer, and the reason no "empty baseline"
    exists anywhere in the system — and likewise when a read comes back in a
    shape setup cannot use. Every failure here is instructive and free: setup
    runs before any paid work."""
    if await ctx.memory.find_baseline_record() is not None:
        # Leave it exactly as is — a newer user-set default rc does NOT move
        # the loop's baseline, and no further reads are needed.
        return ALREADY_PRESENT
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    task = await ctx.kiln.call("GET", base)
    if not isinstance(task, dict):
        raise SetupPreconditionError(unreadable_response_message(base, task))
    rc_path = f"{base}/run_configs"
    rows = await ctx.kiln.call("GET", rc_path)
    # ONLY `None` (an empty 2xx body) collapses to the documented "no run
    # configs" state — any other falsy non-list (`{}`, `""`, `0`) is drift, and
    # must not be reported as "create a run config" to a task that may have plenty.
    rows = [] if rows is None else rows
    if not isinstance(rows, list):
        raise SetupPreconditionError(unreadable_response_message(rc_path, rows))
    if not rows:
        raise SetupPreconditionError(no_run_configs_message(ctx.target.task_id))
    run_configs = _usable_run_configs(rows)
    if not run_configs:
        # Rows came back, none of them identifiable — drift, not the
        # documented zero-run-configs state; say so instead of seeding.
        raise SetupPreconditionError(unreadable_response_message(rc_path, rows))

    choice = choose_baseline_rc(task, run_configs)
    await ctx.memory.seed_baseline(
        choice.rc_id, rule=choice.rule, rc_count=len(run_configs)
    )
    return BaselineSeedOutcome(
        seeded=True,
        rc_id=choice.rc_id,
        rule=choice.rule,
        unresolvable_default=choice.unresolvable_default,
    )


@dataclass(frozen=True)
class BaselineChoice:
    """The run config a task with no `baseline` record starts from, and the
    rule that chose it. `unresolvable_default` is as on
    `BaselineSeedOutcome`."""

    rc_id: str
    rule: str
    unresolvable_default: str | None = None


def choose_baseline_rc(
    task: dict[str, Any], run_configs: list[dict[str, Any]]
) -> BaselineChoice:
    """The task's default run config when the run-config list has it, else
    the most recent. `run_configs` are `_usable_run_configs` rows, at least
    one. Pure: seeding writes the choice, and the start-up's key check only
    reads it."""
    # The default is honored only when the run-config list confirms it. A
    # dangling default (rc deleted, or not served by this route) falls
    # through to the mechanical pick rather than seeding an rc that
    # create_experiment_rc cannot parent from — the whole point is
    # that BaselineRef.rc_id is always a REAL run config. The seeded
    # provenance then reads "no default set", which is what the loop sees:
    # no *usable* default exists.
    live_rc_ids = {_run_config_id(rc) for rc in run_configs}
    default_rc_id = str(task.get("default_run_config_id") or "").strip()
    if default_rc_id in live_rc_ids:  # "" is never a member — ids are non-empty
        return BaselineChoice(default_rc_id, BASELINE_RULE_TASK_DEFAULT)
    # The pre-start survey is expected to ask here;
    # with no survey, code picks mechanically and says so in the record.
    return BaselineChoice(
        _run_config_id(_most_recent(run_configs)),
        BASELINE_RULE_MOST_RECENT,
        # A default WAS named and could not be honored — the breadcrumb the
        # record's provenance can't carry.
        unresolvable_default=default_rc_id or None,
    )


# ---------------------------------------------------------------------------
# eval ↔ issue sync
# ---------------------------------------------------------------------------

# The eval is the parent: not every eval has a Spec, and we
# want to cover them all. A Spec, where one exists, is optional enrichment —
# it often holds the best human-authored description — never the key.
SPEC_STATUS_ACTIVE = "active"
# Each eval's live config is a separate GET, so a wide task (28 evals)
# would otherwise pay 28 sequential round-trips on EVERY invocation. Bounded
# like the describe fan-out — the calls are independent and cheap.
SETUP_CONFIG_READ_CONCURRENCY = 4
# The statuses that mean an eval config is GONE, as opposed to unreadable —
# setup degrades these rather than raising on them (see `read_eval_config`).
CONFIG_GONE_STATUSES = frozenset({404, 410})
# The statuses Kiln answers a config it can't load with, such as a judge
# template this Kiln can't parse: a problem of that one eval, which the run
# leaves out (`read_eval_config`).
UNLOADABLE_STATUSES = frozenset({400, 422})
# Kiln's Spec priority range, and the range the `p<n>` tag vocabulary covers.
MIN_ISSUE_PRIORITY = 0
MAX_ISSUE_PRIORITY = 3


def _bounded_priority(raw: Any) -> int:
    """A Spec's priority, forced into p0–p3 at the read boundary.

    It becomes a `p<n>` TAG, and the tag regex only recognizes p0–p3: an
    out-of-range value would be written once and then never RECOGNIZED as a
    priority tag again, so `tags_with_priority` would leave it in place and
    add a second priority-shaped tag beside it. A negative one writes `p-1`.
    `bool` is excluded explicitly — it is an `int` in Python, and `True`
    would silently mean p1."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_ISSUE_PRIORITY
    return min(max(raw, MIN_ISSUE_PRIORITY), MAX_ISSUE_PRIORITY)


@dataclass(frozen=True)
class SpecView:
    """A Kiln Spec, flattened to the enrichment setup reads off it."""

    id: str
    status: str  # active | future | deprecated | archived
    priority: int  # 0–3
    definition: str
    issue_description: str | None  # properties.issue_description, when present

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> SpecView:
        properties = raw.get("properties")
        return cls(
            id=str(raw.get("id") or ""),
            status=str(raw.get("status") or ""),
            priority=_bounded_priority(raw.get("priority")),
            definition=str(raw.get("definition") or ""),
            issue_description=(
                properties.get("issue_description")
                if isinstance(properties, dict)
                else None
            ),
        )


@dataclass(frozen=True)
class EvalView:
    """One eval + its CURRENT config, flattened to what setup needs."""

    id: str
    name: str
    current_config_id: str | None
    train_filter: str | None  # splits["train"].filter_id
    val_filter: str | None  # splits["val"].filter_id
    config_properties: dict[str, Any] | None  # the CURRENT config only
    spec: SpecView | None
    # Kiln's reason when it can't load the current config (`read_eval_config`).
    unloadable: str | None = None

    @property
    def is_active(self) -> bool:
        """The loop's active-set rule: no
        Spec, or a Spec whose status is `active`."""
        return self.spec is None or self.spec.status == SPEC_STATUS_ACTIVE

    @property
    def in_run(self) -> bool:
        """Active, and its config loads: an eval Kiln can't load is left out
        of the run."""
        return self.is_active and self.unloadable is None

    @property
    def priority(self) -> int:
        """Spec first, then the default.

        Known limitation: priority is read from the Spec only. If priority
        (like status) moves onto the eval, this resolution order must read
        the eval's own priority first."""
        return self.spec.priority if self.spec else DEFAULT_ISSUE_PRIORITY

    @property
    def priority_spec_id(self) -> str | None:
        return self.spec.id if self.spec else None

    @property
    def config_kind(self) -> str:
        """The config's `type`, for the head's human-readable eval line.
        `g_eval` is the live legacy shape that carries no `type` key at all;
        `unknown` means no config."""
        if self.config_properties is None:
            return "unknown"
        return str(self.config_properties.get("type") or "g_eval")


@dataclass(frozen=True)
class RepairBinding:
    """One unbound issue setup can bind mechanically. `add_line` is
    rule 2: the eval was only named in prose, so the missing `- eval:` content
    line is written too."""

    issue: IssueView
    eval_id: str
    add_line: bool


@dataclass
class SyncPlan:
    """What one sync would do. Built without a single write, so the whole
    partition is testable against fixture dicts."""

    # Every eval the task serves, carried rather than counted: the split
    # audit is a pure local pass over this list (no extra REST call), and the
    # two counts the report wants are one comprehension away.
    evals: list[EvalView] = field(default_factory=list)
    seed: list[EvalView] = field(default_factory=list)
    refresh: list[tuple[EvalView, IssueView]] = field(default_factory=list)
    repair: list[RepairBinding] = field(default_factory=list)
    unmeasurable: list[tuple[IssueView, str]] = field(default_factory=list)
    unbound_unresolved: list[IssueView] = field(default_factory=list)

    @property
    def evals_total(self) -> int:
        return len(self.evals)

    @property
    def evals_active(self) -> int:
        return sum(1 for view in self.evals if view.is_active)

    @property
    def active(self) -> list[EvalView]:
        """The active set less the evals Kiln can't load — what the sync
        partitions."""
        return [view for view in self.evals if view.in_run]

    @property
    def unloadable(self) -> dict[str, tuple[str, str]]:
        """The active evals whose config Kiln can't load, by id, with their
        name and Kiln's reason. The run leaves them out, and their issues are
        left as they are: neither seeded, refreshed, repaired nor flagged."""
        return {
            view.id: (view.name, view.unloadable)
            for view in self.evals
            if view.is_active and view.unloadable is not None
        }

    @property
    def describe_targets(self) -> list[EvalView]:
        """The evals needing a fresh description — seeds plus drifted
        refreshes. Empty on a warm run, which is what keeps setup free.

        Deduplicated by eval id: an eval carrying two setup-owned issues
        (four on one eval is a shape that turns up) would otherwise dispatch
        the same description agent twice for one criterion."""
        seen: dict[str, EvalView] = {view.id: view for view in self.seed}
        for view, _issue in self.refresh:
            seen.setdefault(view.id, view)
        return list(seen.values())

    @property
    def has_writes(self) -> bool:
        """Whether applying this plan touches the store at all — the warm-path
        signal the `setup_ok` event wants.

        Deliberately NOT `is_empty`: `unbound_unresolved` is a permanent
        report-only condition (on a task with many loop-authored issue
        records, most of them can sit there forever, as designed), so a plan can be
        write-free while never being empty."""
        return bool(self.seed or self.refresh or self.repair or self.unmeasurable)

    @property
    def is_empty(self) -> bool:
        """Nothing to write AND nothing to report."""
        return not (self.has_writes or self.unbound_unresolved)


# ---- fingerprint ----


def rubric_text(properties: dict[str, Any] | None) -> str:
    """The criterion text out of an eval config, across the real config zoo:
    `code_eval.code`, `llm_judge.prompt_template` (+ system prompt), the
    legacy `type: null` g_eval's `eval_steps` (+ task description), and
    canonical JSON for everything else — a deterministic config we don't
    recognize still fingerprints, it just hashes more than it needs to.

    **Never raises.** A config shape we cannot read must not fail a run."""
    if not properties:
        return ""
    for key in ("code", "prompt_template"):
        if properties.get(key):
            return str(properties[key]) + str(properties.get("system_prompt") or "")
    steps = properties.get("eval_steps")
    if steps:
        joined = "\n".join(str(step) for step in steps)
        return joined + str(properties.get("task_description") or "")
    # Accepted debt: the fallback hashes the
    # WHOLE properties dict, so a server-side field addition to deterministic
    # config responses re-fingerprints every det eval at once, and buys a
    # paid describe fan-out for all of them. The
    # fix, if it ever bites, is a per-type key allowlist rather than a
    # narrower default.
    return json.dumps(properties, sort_keys=True, ensure_ascii=False, default=str)


def fingerprint(view: EvalView) -> str:
    """A short hash over everything a description would have to be rewritten
    for: the eval's name, which config is live, that config's rubric text,
    and the Spec's definition, issue description and priority."""
    payload = {
        "name": view.name,
        "config_id": view.current_config_id,
        "rubric": rubric_text(view.config_properties),
        "spec_definition": view.spec.definition if view.spec else None,
        "spec_issue": view.spec.issue_description if view.spec else None,
        "priority": view.spec.priority if view.spec else None,
    }
    # `default=str` for the same reason rubric_text carries it:
    # `issue_description` comes straight off the Spec's properties and is not
    # guaranteed to be a string — an unserializable value must not raise
    # here, where the whole point is that nothing about a config shape can
    # fail a run.
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


# ---- reads ----


def _require_rows(url_path: str, response: Any) -> list[Any]:
    """A list route's body, with a shape guard: `None` (an
    empty 2xx) is an empty listing; anything else non-list is drift, and
    drift must be legible rather than silently read as "nothing here"."""
    if response is None:
        return []
    if not isinstance(response, list):
        raise SetupPreconditionError(unreadable_response_message(url_path, response))
    return response


LEGACY_SPLIT_FIELDS = {"test": "eval_set_filter_id", "train": "train_set_filter_id"}
"""Split name -> the flat `Eval` field a Kiln build predating `splits` stored it in.

Kiln's own migration reads exactly these two and then clears them, so an eval loaded by
a current server answers on `splits` alone. `val` has no entry because Kiln dropped
`val_set_filter_id` from the model entirely — an eval whose val split lived only there
loads with no val split at all, which `audit_splits` reports rather than works around.
"""


def split_filter_id(raw: Any, split: str) -> str | None:
    """One of an eval row's split filter ids, from either shape the row has had.

    `splits` is where a split lives now: `{name: {"source": ..., "filter_id": ...}}`,
    added by Kiln's eval-splits work. The flat `*_set_filter_id` fields are the older
    home; they read as None against any current server, because the load-time migration
    moves them into `splits` and clears them. Reading `splits` first and falling back
    keeps both server generations working.

    The split's `source` is deliberately not read here. It names which store the filter
    runs over, and both stores are legitimate — resolving that is the server's job, not
    a shape guess this side of the API.
    """
    if not isinstance(raw, dict):
        return None
    splits = raw.get("splits")
    if isinstance(splits, dict):
        split_ref = splits.get(split)
        if isinstance(split_ref, dict):
            filter_id = split_ref.get("filter_id")
            if isinstance(filter_id, str) and filter_id:
                return filter_id
    legacy_field = LEGACY_SPLIT_FIELDS.get(split)
    if legacy_field is None:
        return None
    legacy = raw.get(legacy_field)
    return legacy if isinstance(legacy, str) and legacy else None


def eval_rows_from_response(url_path: str, response: Any) -> tuple[list[Any], int]:
    """The eval listing's rows, from either shape the route has returned.

    `GET .../evals` was a bare list until Kiln commit 38e07d4ff ("Let the eval
    list survive an eval it can't read"), which wrapped it as
    `{"evals": [...], "load_error_count": N}`. Both are read, because a
    checkout on either side of that commit is a target kiln-harness-tuner has to run
    against; every other list route still returns a bare list and keeps `_require_rows`.

    `load_error_count` is returned rather than dropped. It counts evals the
    server could not parse, and those are absent from `evals` entirely — so a
    baseline seeded from a short list is wrong in exactly the silent way this
    module's shape guards exist to prevent. The caller decides what to do with
    it; what it must not do is not know.
    """
    if response is None:
        return [], 0
    if isinstance(response, list):
        return response, 0
    if isinstance(response, dict) and isinstance(response.get("evals"), list):
        load_errors = response.get("load_error_count")
        return response["evals"], load_errors if isinstance(load_errors, int) else 0
    raise SetupPreconditionError(unreadable_response_message(url_path, response))


def active_eval_rows(
    eval_rows: list[Any], spec_rows: list[Any]
) -> list[dict[str, Any]]:
    """The eval rows that are dicts with an id, and whose spec is absent or
    has status "active". Row order is kept.

    Known limitation: an eval without a spec is treated as active. The spec
    carries the status, so such an eval has no status to read."""
    status_by_eval = {
        str(spec["eval_id"]): spec.get("status")
        for spec in spec_rows
        if isinstance(spec, dict) and spec.get("eval_id")
    }
    return [
        row
        for row in eval_rows
        if isinstance(row, dict)
        and row.get("id")
        and status_by_eval.get(str(row["id"]), SPEC_STATUS_ACTIVE) == SPEC_STATUS_ACTIVE
    ]


def measurable_evals(
    eval_rows: list[Any], spec_rows: list[Any]
) -> list[tuple[dict[str, Any], str]]:
    """The `active_eval_rows` that carry a default eval config
    (`current_config_id`), each paired with it: the loop's measurement set
    before any per-run filter. Each eval always runs its default config,
    never one the loop chooses."""
    return [
        (row, str(row["current_config_id"]))
        for row in active_eval_rows(eval_rows, spec_rows)
        if row.get("current_config_id")
    ]


async def read_eval_views(ctx: Ctx) -> list[EvalView]:
    """Evals + their Specs + each eval's CURRENT config. Only the
    config named by `current_config_id` is live; superseded configs sit
    beside it and are never read.

    The per-eval config reads are independent, so they run bounded-parallel:
    on a wide task they dominate the phase's read cost, and setup runs on
    every invocation."""
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    # Independent listings — one round-trip, not two (same argument as the
    # per-eval config fan-out below).
    eval_rows, spec_rows = await asyncio.gather(
        ctx.kiln.call("GET", f"{base}/evals"),
        ctx.kiln.call("GET", f"{base}/specs"),
    )
    evals, eval_load_errors = eval_rows_from_response(f"{base}/evals", eval_rows)
    if eval_load_errors:
        # Not fatal: the server chose to serve the list without them, and one
        # broken eval file must not block every run. But the evals it dropped
        # are measurements this run will never take, so say so where the run
        # log keeps it.
        logger.warning(
            "%s: the server could not read %d eval(s); they are missing from"
            " the active set and from every measurement this run makes",
            f"{base}/evals",
            eval_load_errors,
        )
    specs = _require_rows(f"{base}/specs", spec_rows)
    spec_by_eval: dict[str, SpecView] = {}
    for raw in specs:
        if isinstance(raw, dict) and raw.get("eval_id"):
            spec_by_eval[str(raw["eval_id"])] = SpecView.from_api(raw)
    rows = [raw for raw in evals if isinstance(raw, dict) and raw.get("id")]

    configured = [
        (raw, str(raw["current_config_id"]))
        for raw in rows
        if raw.get("current_config_id")
    ]
    read_by_eval = {
        str(raw["id"]): read
        for (raw, _config_id), read in zip(
            configured,
            await read_eval_configs(ctx.kiln, base, configured),
            strict=True,
        )
    }
    reads = [read_by_eval.get(str(raw["id"]), EvalConfigRead(None)) for raw in rows]
    return [
        EvalView(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            current_config_id=raw.get("current_config_id"),
            train_filter=split_filter_id(raw, "train"),
            val_filter=split_filter_id(raw, "val"),
            config_properties=_config_properties(read.config),
            spec=spec_by_eval.get(str(raw["id"])),
            unloadable=read.unloadable,
        )
        for raw, read in zip(rows, reads, strict=True)
    ]


@dataclass(frozen=True)
class EvalConfigRead:
    """One eval's default config, as Kiln answered for it."""

    config: dict[str, Any] | None  # None when gone or unloadable
    unloadable: str | None = None  # Kiln's reason, when it can't load it


async def read_eval_config(
    kiln: KilnApi, base: str, eval_id: str, config_id: str
) -> EvalConfigRead:
    """An eval's config, read by the one rule setup, readiness and the key
    check share. An unreadable config never fails a run — the rule applied
    to the ways a config can be MISSING.

    A "gone" status (404, 410) is a real answer: a `current_config_id`
    naming a deleted config (kiln.py never retries or softens 4xx). Setup
    then fingerprints the eval over an empty rubric — stably, and it
    re-fingerprints the moment a config is there again.

    An "unloadable" status (400, 422) is Kiln saying it can't load that
    config, as for a judge template this Kiln can't parse. That is a problem
    of the one eval, so the run leaves the eval out (`EvalView.in_run`)
    rather than stop. It is not degraded to an empty rubric: that changes
    its fingerprint, and a paid re-describe would follow on every
    invocation while the eval stays broken.

    A drifted BODY reads as no config.

    Everything else propagates, including 401/403/429 and 5xx. Those mean
    "we could not READ it", not "it is not there", and degrading them is
    expensive and silent: the eval would fingerprint over an empty rubric,
    refreshing the whole active set (a PAID describe fan-out — 28 on a wide
    task), storing the wrong fingerprint, and refreshing again once the
    read heals. Failing instead costs nothing: setup runs before any paid
    work."""
    try:
        config = await kiln.call(
            "GET", f"{base}/evals/{eval_id}/eval_config/{config_id}"
        )
    except KilnAPIError as exc:
        if exc.status_code in CONFIG_GONE_STATUSES:
            return EvalConfigRead(None)
        if exc.status_code in UNLOADABLE_STATUSES:
            return EvalConfigRead(None, unloadable=exc.kiln_message)
        raise
    return EvalConfigRead(config if isinstance(config, dict) else None)


async def read_eval_configs(
    kiln: KilnApi, base: str, pairs: Sequence[tuple[dict[str, Any], str]]
) -> list[EvalConfigRead]:
    """`read_eval_config` for each `(eval row, config id)` pair, in order,
    bounded like setup's own reads."""
    semaphore = asyncio.Semaphore(SETUP_CONFIG_READ_CONCURRENCY)

    async def read(row: dict[str, Any], config_id: str) -> EvalConfigRead:
        async with semaphore:
            return await read_eval_config(kiln, base, str(row["id"]), config_id)

    return await asyncio.gather(*(read(row, config_id) for row, config_id in pairs))


def _config_properties(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """A config's `properties`, or `None` for no config or a drifted one."""
    if config is None:
        return None
    properties = config.get("properties")
    return properties if isinstance(properties, dict) else None


_UNLOADABLE_FIX = (
    "The run leaves it out. To use it, fix its judge in Kiln, or update Kiln if",
    "the eval was made with a newer version",
)


def unloadable_eval_lines(name: str, reason: str, link: str | None = None) -> list[str]:
    """The warning for an eval Kiln can't load: Kiln's reason and the fix,
    ending with the page that fixes it when `link` is given. The lines
    after the first are indented under it."""
    return [
        f'Kiln can\'t load eval "{name}": {reason.removesuffix(".")}.',
        f"  {_UNLOADABLE_FIX[0]}",
        f"  {_UNLOADABLE_FIX[1]}" + (f": {link}" if link else "."),
    ]


async def read_issue_views(ctx: Ctx) -> list[IssueView]:
    return await ctx.memory.list_issues()


# ---- repair of unbound issues ----

EVAL_LINE_RE = re.compile(r"^- eval: (\d{6,})", re.MULTILINE)
EVAL_PROSE_RE = re.compile(r"\beval[s]? (\d{6,})\b")


def _repair_binding(
    issue: IssueView, live_eval_ids: frozenset[str]
) -> RepairBinding | None:
    """The rules, in order: exactly one distinct id from a `- eval:`
    line that exists; else exactly one distinct id from prose that exists;
    else nothing. Zero, ambiguous and dangling references all land in
    `unbound_unresolved` — setup NEVER guesses a binding."""
    content = issue.content or ""
    line_ids = frozenset(EVAL_LINE_RE.findall(content))
    for found in (line_ids, frozenset(EVAL_PROSE_RE.findall(content))):
        if len(found) == 1 and found <= live_eval_ids:
            eval_id = next(iter(found))
            return RepairBinding(
                issue=issue,
                eval_id=eval_id,
                # The `- eval:` line is written when no existing line names the
                # id actually being bound — keyed on THAT id, not on "any
                # line at all": a line naming a DEAD eval must not suppress
                # the live one's (it is exactly why rule 1 fell through).
                add_line=eval_id not in line_ids,
            )
    return None


def _unmeasurable_cause(
    bound_eval_ids: frozenset[str],
    live_eval_ids: frozenset[str],
    spec_by_eval: dict[str, SpecView],
) -> str:
    """One marker, both causes — the cause rides in the
    content line, because the planner's rule is identical either way.

    `bound_eval_ids` is the RESOLVED binding set, which for a repaired issue
    is not what its tags carry yet: reading the tags here wrote an empty
    cause (`-  (noted 08-04)`) onto exactly the record whose diagnosis this
    function exists to preserve."""
    causes = []
    deleted = sorted(bound_eval_ids - live_eval_ids)
    if deleted:
        causes.append(
            f"eval{'s' if len(deleted) > 1 else ''} {', '.join(deleted)} deleted"
        )
    for eval_id in sorted(bound_eval_ids & live_eval_ids):
        spec = spec_by_eval.get(eval_id)
        # An eval with no Spec is active by rule, so a live-but-inactive one
        # always has a Spec to name. Guarded anyway: this string is the whole
        # reason the flag is worth writing, and it must never come back bare.
        source = f"spec {spec.id} {spec.status}" if spec else "no active spec"
        causes.append(f"{source}; eval {eval_id} left the active set")
    # Defensive, like the `no active spec` fallback above: the caller only
    # asks when the binding set is non-empty, so `causes` cannot be — but an
    # empty cause line is the exact defect this function exists to prevent,
    # and it must never be reachable by a future edit either.
    return "; ".join(causes) or "no live eval binds this issue"


async def build_sync_plan(ctx: Ctx) -> SyncPlan:
    """The partition, computed from reads alone — no writes, so the
    whole decision is testable against fixture dicts."""
    # The two read groups are independent — gathered for the same reason
    # `/evals`+`/specs` and the per-eval configs are.
    evals, issues = await asyncio.gather(read_eval_views(ctx), read_issue_views(ctx))
    plan = SyncPlan(evals=evals)
    live_eval_ids = frozenset(view.id for view in evals)
    active = plan.active
    active_ids = frozenset(view.id for view in active)
    unloadable_ids = frozenset(plan.unloadable)
    spec_by_eval: dict[str, SpecView] = {
        view.id: view.spec for view in evals if view.spec is not None
    }

    # Repair FIRST, and its bindings count as bound: an
    # unbound-but-repairable issue already covers its eval, and computing
    # `seed` before the repair would post a SECOND issue for that eval —
    # the exact duplication the sync exists to prevent. Repair resolves
    # against LIVE evals, not active ones, so an issue can legitimately be
    # bound and flagged unmeasurable in the same pass (the eval exists, it
    # just left the active set) — the binding is what makes the flag's cause
    # line nameable at all.
    bindings: dict[str, frozenset[str]] = {}
    for issue in issues:
        # A baseline-audit issue is bound to no eval on purpose
        # (the generality check measures it), so it is neither repaired
        # into a binding nor reported as unbound. The same holds for
        # the audit's eval-text half (the judge-copy check measures it).
        if BASELINE_AUDIT_TAG in issue.tags or BASELINE_JUDGE_COPY_TAG in issue.tags:
            continue
        bound = issue.bound_eval_ids
        if not bound:
            repaired = _repair_binding(issue, live_eval_ids)
            if repaired is None:
                plan.unbound_unresolved.append(issue)
                continue
            if repaired.eval_id in unloadable_ids:
                continue  # left as it is until Kiln can load its eval
            plan.repair.append(repaired)
            bound = frozenset([repaired.eval_id])
        bindings[issue.id] = bound

    issues_by_eval: dict[str, list[IssueView]] = {}
    for issue in issues:
        for eval_id in bindings.get(issue.id, frozenset()):
            issues_by_eval.setdefault(eval_id, []).append(issue)

    for view in active:
        covering = issues_by_eval.get(view.id, [])
        if not covering:
            plan.seed.append(view)
            continue
        current = fingerprint(view)
        plan.refresh.extend(
            (view, issue)
            for issue in covering
            # A loop-authored issue has no setup head and is never refreshed.
            if issue.is_setup_owned and issue.fingerprint != current
        )

    # Already-flagged issues are skipped so the note is written once; issues
    # the LOOP already closed are skipped outright. `flag_unmeasurable`
    # replaces the overview prefix, so flagging a FIXED:/WONT_FIX: issue
    # would erase a decision the loop made — the mirror image of the rule
    # "setup never closes an issue on its own" — and, because the state
    # would stay terminal rather than becoming `unmeasurable`, it would
    # re-flag and re-append the cause line on every subsequent run. A closed
    # issue losing its eval is not news.
    skip_states = ISSUE_TERMINAL_STATES | {IssueState.UNMEASURABLE}
    for issue in issues:
        bound = bindings.get(issue.id, frozenset())
        # Bound to several evals ⇒ unmeasurable only when NONE is active. An
        # eval Kiln can't load is left out of this run, not out of the
        # active set, so its issues are not flagged.
        if (
            bound
            and not bound & (active_ids | unloadable_ids)
            and issue.state not in skip_states
        ):
            plan.unmeasurable.append(
                # `bound`, not the issue's tags: a repaired issue's binding
                # isn't written yet, and reading the tags here produced a
                # cause-less `-  (noted 08-04)` line.
                (issue, _unmeasurable_cause(bound, live_eval_ids, spec_by_eval))
            )
    return plan


# ---- apply ----

DescribeEvals = Callable[["Ctx", list[EvalView]], Awaitable[dict[str, EvalDescription]]]


def _require_description(
    descriptions: dict[str, EvalDescription], view: EvalView
) -> EvalDescription:
    """`describe`'s contract is one description per requested eval. A gap is
    fatal either way (never seed against an incomplete requirement
    set) — this only makes it legible, where a bare `KeyError('424242966189')`
    names nothing a reader can act on."""
    description = descriptions.get(view.id)
    if description is None:
        raise SetupPreconditionError(
            f"no description was produced for eval {view.id} ({view.name}), so "
            "its issue memory cannot be written. The loop will not seed a "
            "requirement it cannot describe — re-invoke `kiln-harness-tuner` "
            "to retry the description agent."
        )
    return description


async def apply_sync_plan(
    ctx: Ctx, plan: SyncPlan, describe: DescribeEvals
) -> SetupReport:
    """Write the plan (additive and never destructive — it
    creates memories, adds tags and appends lines; it never deletes one,
    rewrites agent-authored prose, or closes an issue).

    `describe` produces one `EvalDescription` per requested eval; `run_setup`
    passes the bounded parallel fan-out, the tests pass a stub. It is
    not called at all when nothing needs describing — the warm path's
    zero-dispatch guarantee.

    Every description is in hand before the FIRST write, so a fatal
    `SetupDescribeError` leaves the store untouched — there is no
    partially-seeded state to reason about."""
    targets = plan.describe_targets
    descriptions = await describe(ctx, targets) if targets else {}
    report = SetupReport(
        evals_total=plan.evals_total,
        evals_active=plan.evals_active,
        unbound_unresolved_issue_ids=[i.id for i in plan.unbound_unresolved],
    )
    for view in plan.seed:
        await ctx.memory.seed_issue(
            view, _require_description(descriptions, view), fingerprint(view)
        )
        report.seeded_eval_ids.append(view.id)
    for view, issue in plan.refresh:
        await ctx.memory.refresh_issue(
            issue.id, view, _require_description(descriptions, view), fingerprint(view)
        )
        report.refreshed_eval_ids.append(view.id)
    for binding in plan.repair:
        await ctx.memory.bind_eval(
            binding.issue.id, binding.eval_id, add_line=binding.add_line
        )
        report.repaired_issue_ids.append(binding.issue.id)
    for issue, cause in plan.unmeasurable:
        await ctx.memory.flag_unmeasurable(issue.id, cause)
        report.unmeasurable_issue_ids.append(issue.id)
    return report


# ---- description agents — parallel, bounded, fatal ----

# Breadth over frugality: this is a ONE-TIME cost per eval (only new and
# drifted evals ever pay it), and the sessions are small and independent.
SETUP_DESCRIBE_CONCURRENCY = 4
# One retry, then fatal — NOT the 3-rung item ladder.
SETUP_DESCRIBE_BACKOFFS = (30.0,)


async def describe_all(
    ctx: Ctx, views: list[EvalView]
) -> tuple[dict[str, EvalDescription], float]:
    """One description agent per eval, bounded-parallel. Returns the
    descriptions keyed by eval id plus what the fan-out cost, which the
    `setup_sync` event reports.

    Never batched: batching many criteria into one session
    invites cross-contamination between unrelated requirements, and each
    description is a small independent read.

    **A failed description is FATAL after one retry.** Deliberately harsher
    than the item ladder and than "skip it, retry next run": the agent
    costs pennies, a full run costs hours, and seeding a project with one eval
    silently missing means the loop optimizes against an incomplete
    requirement set — so the human pays the big cost twice, once for the blind
    run and once for the re-run.

    Failing here costs one cheap restart, but not a free one: nothing is
    written on this path, so the sibling sessions that DID succeed are paid
    for and their evals are described again next invocation. That is the
    trade, taken knowingly."""
    semaphore = asyncio.Semaphore(SETUP_DESCRIBE_CONCURRENCY)
    cost_usd = 0.0

    async def describe_one(view: EvalView) -> EvalDescription:
        nonlocal cost_usd
        async with semaphore:
            result = await call_with_retries(
                ctx.dispatch.describe_eval,
                DescribeEvalInput(
                    eval_id=view.id,
                    eval_name=view.name,
                    spec_id=view.spec.id if view.spec else None,
                ),
                backoffs=SETUP_DESCRIBE_BACKOFFS,
                sleep=ctx.sleep,
                log=ctx.log,
                source="describe_eval",
                item_id=view.id,
            )
        cost_usd += result.usage.cost_usd
        # Logged before the raise below: a failed attempt cost real money and
        # must reach the spend accounting like every other dispatch (items.py's
        # rule — `call_with_retries` returns the usage of EVERY attempt).
        ctx.log.event(
            "spend",
            source="describe_eval",
            lane=SUBSCRIPTION_LANE,
            cost_usd=result.usage.cost_usd,
            unmeasured_tokens=result.usage.unmeasured_tokens,
        )
        if result.data is None:
            error = result.error
            raise SetupDescribeError(
                view.id,
                view.name,
                f"{error.error_class}: {error.detail}"
                if error
                else result.detail or "the agent returned no description",
                attempts=result.attempts,  # the ladder's own count
            )
        return result.data

    tasks = [asyncio.create_task(describe_one(view)) for view in views]
    try:
        descriptions = await asyncio.gather(*tasks)
    except BaseException:
        # `gather` propagates the first exception but does NOT cancel its
        # siblings — left alone they outlive the
        # run that paid for them: real `claude -p` subprocesses still going
        # (the runner reaps its child on cancellation — claude_cli.py), and
        # "Task was destroyed but it is pending" at loop teardown. Cancel,
        # then drain so every cancellation actually lands before we leave.
        for task in tasks:
            task.cancel()
        # Suppressed, not propagated: if THIS coroutine is cancelled from
        # outside mid-drain, the drain would abandon the very children it
        # exists to reap. The original exception (that outside
        # `CancelledError` included, since it is what `except` caught) is
        # re-raised below either way.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return dict(zip((view.id for view in views), descriptions, strict=True)), cost_usd


# ---- split readiness audit ----


def _missing_splits(view: EvalView) -> str | None:
    """Which split an eval lacks, or None when it has both."""
    missing = [
        f"no {name}"
        for name, value in (("train", view.train_filter), ("val", view.val_filter))
        if not value
    ]
    # "no train and no val", not "no train or val" — the latter reads as
    # "one of the two", which is the other case entirely.
    return " and ".join(missing) if missing else None


def _render_missing_splits(rows: list[tuple[EvalView, str]]) -> str:
    """The message: every failure listed at once, then the fix.
    Never one eval at a time — the whole point is that an owner can fix the
    task in a single pass."""
    listing = "\n".join(
        f'  • {view.id} "{view.name}" — {missing} split' for view, missing in rows
    )
    return (
        f"{len(rows)} eval{'s' if len(rows) != 1 else ''} "
        f"{'are' if len(rows) != 1 else 'is'} missing a train/val split and "
        f"cannot be used by the loop:\n{listing}\n"
        "The loop measures experiments on train and gates promotion on val, "
        "and it does not create or repair splits. Add splits to these evals "
        "(or deactivate them via their Spec status), then re-invoke "
        "`kiln-harness-tuner`."
    )


def audit_splits(views: list[EvalView]) -> None:
    """Every eval in the run (active, and Kiln can load it) must carry both
    split filters, or the run dies here rather than three waves and real
    money in, at the first promotion gate's 422.

    A pure local pass over the already-fetched views — no extra REST call and
    no 422-probing, which is why it is not `async` (there is nothing to
    await). Setup audits; setup does not repair.

    It costs nothing itself, but it is not free to REACH: `run_setup` calls it
    after the sync, so a failure here lands after the describe fan-out's
    spend. That ordering is deliberate — see
    `SetupError` for the trade it makes."""
    rows = [
        (view, missing)
        for view in views
        if view.in_run and (missing := _missing_splits(view)) is not None
    ]
    if rows:
        raise EvalSplitAuditError(_render_missing_splits(rows))


# ---- the phase itself ----


def _leave_out_unloadable_evals(ctx: Ctx, plan: SyncPlan) -> None:
    """Record the evals Kiln can't load. The active ones are those the whole
    run leaves out (`orchestrator._active_evals`), and each is logged; an
    inactive one is not in the run, but is still not read again."""
    ctx.unloadable_eval_ids = frozenset(
        view.id for view in plan.evals if view.unloadable is not None
    )
    ctx.unloadable_evals = plan.unloadable
    for eval_id, (name, reason) in ctx.unloadable_evals.items():
        ctx.log.event(
            "eval_unloadable",
            eval_id=eval_id,
            name=name,
            reason=reason,
            line=" ".join(line.strip() for line in unloadable_eval_lines(name, reason)),
        )


async def run_setup(ctx: Ctx) -> SetupReport:
    """The whole pre-flight setup phase: baseline → sync → audit.

    The audit runs LAST, after the sync has been written, so a cold project
    gets its issue store seeded on the same invocation that tells its owner
    what to fix — one round trip, not two — and after the events, so the wave
    log records the writes that really happened before the fatal. The cost of
    that ordering (an audit failure follows the fan-out's spend) is stated on
    `SetupError`.

    Returns the `SetupReport` the run's status and report carry; raises
    `SetupError` (any subclass) for anything a human has to fix first."""
    outcome = await seed_baseline_if_missing(ctx)
    if outcome.seeded:
        ctx.log.event(
            "baseline_seeded",
            rc_id=outcome.rc_id,
            rule=outcome.rule,
            # The one diagnosis the record's provenance cannot carry: a
            # default WAS set and could not be honored.
            unresolvable_default=outcome.unresolvable_default,
        )

    describe_cost_usd = 0.0

    async def describe(ctx_: Ctx, views: list[EvalView]) -> dict[str, EvalDescription]:
        nonlocal describe_cost_usd
        descriptions, cost_usd = await describe_all(ctx_, views)
        describe_cost_usd += cost_usd
        return descriptions

    plan = await build_sync_plan(ctx)
    _leave_out_unloadable_evals(ctx, plan)
    report = await apply_sync_plan(ctx, plan, describe)
    report.baseline_rule = outcome.rule
    report.baseline_rc_id = outcome.rc_id

    # One event, two kinds: `setup_ok` is the warm path — keyed on
    # `has_writes`, never `is_empty`, because a task whose issue store carries
    # permanent report-only records is still a
    # warm, nothing-to-do run. Same fields either way, so every consumer reads
    # one shape; the kind is the signal.
    ctx.log.event(
        "setup_sync" if plan.has_writes else "setup_ok",
        evals_total=report.evals_total,
        evals_active=report.evals_active,
        seeded=len(report.seeded_eval_ids),
        refreshed=len(report.refreshed_eval_ids),
        repaired=len(report.repaired_issue_ids),
        unmeasurable=len(report.unmeasurable_issue_ids),
        unbound_unresolved=len(report.unbound_unresolved_issue_ids),
        cost_usd=describe_cost_usd,
        report=report,
    )

    audit_splits(plan.evals)
    return report
