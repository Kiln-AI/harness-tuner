"""run_experiment_analysis — the mechanical middle of every experiment.

Code computes judgment-free numbers; agents interpret them (no verdict
labels anywhere). Four responsibilities:

- the baseline-if-needed decision tree, incl. the latest-measured-baseline
  screen: screen-out rows carry `compared_to="prior_baseline"` and are
  NEVER read as "the change hurt" — the parent is measured only when there is
  an improvement needing attribution (candidate beats the screen reference on
  at least one targeted pair);
- eval-job driving: `POST
  /api/jobs/evals/run` + `/api/jobs/wait`, retry ladder (sleep 60s, max 1
  retry = 2 attempts — errored items persist no EvalRun, so attempt 2 re-runs
  exactly the failed set; failing twice IS the deterministic signal), each
  wait capped by asyncio.wait_for. Any non-clean job gets its errors fetched
  verbatim (`GET /api/jobs/{id}/errors`) and logged as an `eval_job_errors`
  wave-log event. On attempt exhaustion `ensure_measured` returns whatever is
  stored WITH `JobHealth` coverage metadata (partial results); only the
  zero-rows case still raises `MeasurementFailed` → named holes in
  `AnalysisBundle.errors`. The bundle ships anyway (no completeness threshold
  in code — the analyzer weighs the holes and the coverage in `successful`);
- paired statistics: operation keyed off the score type via
  `POST /api/statistics`, delta sign-normalized to good-direction units by the
  per-score direction flag (a Kiln server feature — a missing flag is a hard
  error, same no-degradation doctrine as the missing-split row),
  `significant` = p < 0.05 computed in code, flip counts binary-only;
- score normalization + the any-headroom reproduction rule
  behind `normalize` / `reproduction_verdict`: the one-sided, judgment-free
  read of "does the baseline actually fail this eval" that items.py's
  `measure_issue_baseline` path settles an `UNMEASURED:` issue with.

The orchestrator wires this behind `Ctx` and reuses
`Analyzer.ensure_measured` / `Analyzer.compare_rows` for the val pass. The
baseline lineage (promotion history rc ids, newest → oldest) is parsed from
the baseline record by memory.py and passed in here —
analysis stays REST-measurement-only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any, Literal, Protocol

from kiln_harness_tuner.concurrency import first_error_or_done
from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError
from kiln_harness_tuner.latency import (
    compare_eval_run_costs,
    compare_eval_runs,
    latency_of_eval_run,
    task_cost_of_eval_run,
)
from kiln_harness_tuner.provider_errors import ProviderQuotaError, diagnose_job_errors
from kiln_harness_tuner.schemas import (
    AnalysisBundle,
    BaselineRef,
    ErrorInfo,
    EvalTarget,
    ExperimentItem,
    FixWork,
    PerEvalResult,
    TrialResult,
    score_json_key,
)

Direction = Literal["higher_is_better", "lower_is_better", "informational"]

BINARY_SCORE_TYPES = frozenset({"pass_fail", "pass_fail_critical"})
RATING_SCORE_TYPES = frozenset({"five_star"})

# Significance is a data flag computed in code — p < 0.05, not a framing.
SIGNIFICANCE_ALPHA = 0.05

logger = logging.getLogger(__name__)

# Defaults: sleep 60s between eval-job
# attempts, max 1 retry (2 attempts — errored items persist no EvalRun, so
# attempt 2 re-runs exactly the failed set; failing twice IS the deterministic
# signal), each job wait capped at 60 min (rough default; the wave-boundary
# deadline can never catch a hang — this cap is what makes the timeout class
# real).
DEFAULT_JOB_ATTEMPTS = 2
DEFAULT_JOB_RETRY_SLEEP_SECS = 60.0
DEFAULT_JOB_WAIT_TIMEOUT_SECS = 3600.0
# Class-aware ladder: rate-limited
# attempts are the most recoverable failures in the corpus (errored items
# persist no EvalRun, so each retry re-runs exactly the throttled set), so
# they get MORE attempts with exponential backoff honoring the provider's own
# retry-after hint; malformed-output attempts sleep not at all (the 60s
# cooldown buys nothing on a deterministic judge bug). quota/auth never
# retry: they circuit-break the run.
DEFAULT_RATE_LIMIT_ATTEMPTS = 4
RATE_LIMIT_BACKOFF_CAP_SECS = 300.0
# The jobs/wait long-poll expiry is layered: the SERVER 504s at
# job_wait_timeout_secs (the normal expiry path → classified `timeout`), the
# httpx read timeout sits one margin above so the server always answers
# first, and the asyncio.wait_for hard cap one margin above that — it fires
# only when server and transport both go silent.
DEFAULT_JOB_WAIT_MARGIN_SECS = 30.0


class AnalysisError(Exception):
    """Base class for hard (non-hole) measurement-lane failures."""


class DirectionFlagMissingError(AnalysisError):
    """A targeted score has no direction flag — the Kiln server's direction
    field is not set on this eval. Hard error, no degradation."""


class TargetExpansionError(AnalysisError):
    """The evaluation_target names a score that cannot be targeted (unknown
    key, or an informational score — those ride along, never targeted)."""


class MissingDefaultEvalConfigError(AnalysisError):
    """The eval has no default eval config (`current_config_id`) — contracts:
    eval-build sets the default judge on the eval; the loop never chooses."""


class UnsupportedScoreTypeError(AnalysisError):
    """A targeted score has a type with no statistics-operation mapping."""


class InconclusiveNegativeError(AnalysisError):
    """A *negative* reproduction verdict was reached without complete
    evidence: at least one targeted score was not measured on every stored
    run. Positives are unaffected — more evidence cannot
    un-reproduce an issue — so this fires only where the missing rows could
    have changed the answer. Distinct from `MeasurementFailed`, which means
    nothing is stored at all."""


class StatsResponseError(AnalysisError):
    """`POST /api/statistics` answered without a recognizable p-value key —
    a response-contract mismatch, not a legitimately-null p. Failing hard
    keeps a field rename from silently reading every row as insignificant."""


class HeldOutLeakError(AnalysisError):
    """A train read returned a held-out item. The split
    boundary moved under the loop — an eval's train split now selects an
    item that is val or test elsewhere — and the rows must not reach an
    agent. The item fails; the rows are never used."""


class ModelPolicyRefused(AnalysisError):
    """The run config's model is outside `--model-policy`. The
    loop never measures it, however it came to exist."""


class MeasurementFailed(Exception):
    """The retry ladder was exhausted for one (eval, rc, split)
    measurement AND nothing is stored (exhaustion with stored rows returns partial
    results instead). Inside the decision tree it becomes named holes; the
    val pass (orchestrator) catches it around
    `ensure_measured` — missing rows are missing evidence for `promote_best`,
    never a hard stop."""

    def __init__(self, error: ErrorInfo):
        self.error = error
        super().__init__(error.detail)


class KilnApi(Protocol):
    """The call_kiln_api-shaped surface analysis needs (KilnClient)."""

    async def call(
        self,
        method: str,
        url_path: str,
        query_params: dict[str, Any] | None = None,
        body: Any | None = None,
        *,
        timeout_secs: float | None = None,
        retries: bool = True,
    ) -> Any: ...


class EventSink(Protocol):
    """Structural twin of the orchestrator's EventLog (defined locally —
    orchestrator imports this module, so importing it back would cycle)."""

    def event(self, kind: str, **fields: object) -> None: ...


class _NullSink:
    def event(self, kind: str, **fields: object) -> None:
        del kind, fields


@dataclass(frozen=True)
class JobHealth:
    """Measurement-completeness metadata for one (eval, rc, split) job
    `n_expected` from the job record's progress totals;
    `missing_item_ids` (dataset_ids) + `error_messages` from the job-errors
    fetch. Never serialized — the wire carries the derived
    `PerEvalResult` coverage fields instead."""

    n_expected: int | None = None
    missing_item_ids: tuple[str, ...] = ()
    error_messages: tuple[str, ...] = ()
    # The LAST attempt's job id (the retry ladder may run several), so a
    # partial row can be traced back to `GET /api/jobs/{job_id}/errors`.
    # None when no job ran at all — a free cache read has no id to give.
    job_id: str | None = None

    @property
    def clean(self) -> bool:
        return not self.missing_item_ids and not self.error_messages


# Job-error messages are free text (no kind parsing) and can carry
# whole tracebacks; the event/health keep the first 500 chars.
JOB_ERROR_MESSAGE_MAX_CHARS = 500


@dataclass(frozen=True)
class ScoreSpec:
    """One expanded (score) leg of an (eval, score) measurement pair."""

    key: str  # json key into EvalRun.scores
    type: str  # five_star | pass_fail | pass_fail_critical | custom
    direction: Direction


# score_json_key moved to schemas.py (CR: the EvalTarget dedup must key
# scores the same way this module's expand_targets does); imported above and
# still re-exported under its old home for callers/tests.


def _score_direction(eval_def: dict[str, Any], score: dict[str, Any]) -> Direction:
    direction = score.get("direction")
    if direction not in ("higher_is_better", "lower_is_better", "informational"):
        raise DirectionFlagMissingError(
            f"eval {eval_def.get('id')} score {score.get('name')!r} has no "
            "direction flag (higher_is_better|lower_is_better|informational) — "
            "this eval's scores carry no direction field, which needs a newer Kiln "
            "server; refusing to guess sign normalization"
        )
    return direction


def expand_targets(eval_def: dict[str, Any], target: EvalTarget) -> list[ScoreSpec]:
    """Expand one EvalTarget to its ScoreSpecs:
    `scores=None` → every directional score on the eval, in eval order;
    an explicit list targets a subset (informational scores can't be
    targeted)."""
    scores = eval_def.get("output_scores") or []
    by_key = {score_json_key(s["name"]): s for s in scores}

    if target.scores is None:
        specs = []
        for score in scores:
            direction = _score_direction(eval_def, score)
            if direction == "informational":
                continue
            specs.append(
                ScoreSpec(score_json_key(score["name"]), score["type"], direction)
            )
        if not specs:
            # Same error as targeting an informational score explicitly: an
            # empty expansion would otherwise vanish silently — after paying
            # for the candidate measurement (expansion runs before
            # ensure_measured, so this also short-circuits the job).
            raise TargetExpansionError(
                f"eval {eval_def.get('id')} has no directional scores to "
                "target (all informational) — nothing to measure"
            )
        return specs

    specs = []
    for requested in target.scores:
        key = score_json_key(requested)
        score = by_key.get(key)
        if score is None:
            raise TargetExpansionError(
                f"eval {eval_def.get('id')} has no score {requested!r} "
                f"(known: {sorted(by_key)})"
            )
        direction = _score_direction(eval_def, score)
        if direction == "informational":
            raise TargetExpansionError(
                f"eval {eval_def.get('id')} score {requested!r} is informational "
                "— it cannot be targeted; it rides along as analyzer context"
            )
        specs.append(ScoreSpec(key, score["type"], direction))
    if not specs:
        # Same guard as the `scores=None` branch above, for the same reason:
        # an empty expansion vanishes silently AFTER the measurement is paid
        # for. An explicit `scores: []` is a planner mistake — `None` is how
        # "every directional score" is spelled.
        raise TargetExpansionError(
            f"eval {eval_def.get('id')} was targeted with an empty score list "
            "— omit `scores` to target every directional score"
        )
    return specs


def flatten_result_row(row: Any) -> Any:
    """One `/results` row, as the flat EvalRun-shaped dict this module reads.

    The route served bare EvalRun objects until Kiln split the trace off the
    score record. It now wraps each one as `{"eval_run": ..., "input": ...,
    "output": ..., "task_run_trace": ..., "task_run_usage": ...}`: the trace and
    its input/output moved onto the TaskRun named by `scored_run_id`, and the
    wrapper is where the server resolves whichever place they actually live.
    A row that is already flat is returned unchanged, so an older server still
    reads.

    The wrapper's fields win over the EvalRun's same-named ones, because they
    ARE that resolution — an EvalRun predating the split carries its own inline
    `task_run_trace` and the wrapper reproduces it, while a current record
    carries none and only the wrapper has it.

    Reading this shape wrong is silent rather than loud, which is why it is
    flattened once here instead of at each reader: `_item_id` finds no id on the
    wrapper, so every row drops out of `_runs_by_item`, and a comparison then
    pairs nothing and reports no error — a promotion gate measuring an empty set
    and calling it a clean read.
    """
    if not isinstance(row, dict):
        return row
    eval_run = row.get("eval_run")
    if not isinstance(eval_run, dict):
        return row
    flat = dict(eval_run)
    flat.update({key: value for key, value in row.items() if key != "eval_run"})
    return flat


def _item_id(run: dict[str, Any]) -> str | None:
    # dataset_id (V1 TaskRun items) and eval_input_id (V2) are mutually
    # exclusive on EvalRun — either is the pairing identity.
    return run.get("dataset_id") or run.get("eval_input_id")


def _runs_by_item(runs: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One record per item. Several stored records for one item are
    REPLICATES (a retried job, or concurrent items that
    each drove the same baseline before any row existed), and a replicate
    set is folded into one record whose scores are the per-key MEAN over
    the records carrying that key (`replicates` = how many). The last
    record's other fields survive. The last record must not win
    outright: that would make every pairing against a replicated baseline a
    draw, because replicates of one item can score differently."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        item_id = _item_id(run)
        if item_id is not None:
            groups.setdefault(item_id, []).append(run)
    indexed: dict[str, dict[str, Any]] = {}
    for item_id, records in groups.items():
        if len(records) == 1:
            indexed[item_id] = records[0]
            continue
        keys = {k for r in records for k in (r.get("scores") or {})}
        scores: dict[str, Any] = {}
        for key in keys:
            values = [
                float(v)
                for r in records
                if (v := (r.get("scores") or {}).get(key)) is not None
            ]
            scores[key] = fmean(values) if values else None
        indexed[item_id] = {**records[-1], "scores": scores, "replicates": len(records)}
    return indexed


def _fold_replicates(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """`_runs_by_item`'s folding for a one-sided read: id-bearing records
    fold per item, records with no item id pass through untouched (they
    cannot be replicates of anything nameable)."""
    folded = list(_runs_by_item(runs).values())
    folded.extend(run for run in runs if _item_id(run) is None)
    return folded


def pairable_item_count(runs: Sequence[dict[str, Any]]) -> int:
    """How many DISTINCT dataset items these EvalRun records can be paired on.

    Not `len(runs)`: `compare_rows` pairs through `_runs_by_item`, so several
    records for one item collapse to one (the retry ladder re-invokes the job)
    and a record carrying no item id drops out entirely. A record count
    overstates both — and it overstates them exactly in the partial-coverage
    case that `BatchPlanInput.eval_sizes` exists to expose."""
    return len(_runs_by_item(runs))


def _optional_float(value: Any) -> float | None:
    """A JSON number as float, or None for null/absent/non-numeric (bool is
    not a number here — a stray `true` must not read as 1.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _run_cost(run: dict[str, Any]) -> float:
    """Full per-record provider cost: subject-model (`task_run_usage`) PLUS
    judge (`eval_usage`)."""
    task_usage = run.get("task_run_usage") or {}
    eval_usage = run.get("eval_usage") or {}
    return float(task_usage.get("cost") or 0.0) + float(eval_usage.get("cost") or 0.0)


# A stored row is "this run's" when its `created_at` is at/after run start,
# minus a grace for clock skew between the server's timestamp and ours.
FRESH_RUN_GRACE = timedelta(seconds=120)


def _row_output(run: dict[str, Any]) -> str | None:
    """The task output an eval run judged, as text, when the row has it."""
    out = run.get("output")
    if isinstance(out, dict):
        out = out.get("output")
    return out if isinstance(out, str) else None


def run_is_fresh(run: dict[str, Any], started_at: datetime | None) -> bool:
    """Did THIS run pay for this EvalRun? True when `created_at` is at or
    after `started_at` (naive local, as `datetime.now()` gives it — an
    offset-aware timestamp is converted to local first). No `started_at`,
    no timestamp, or an unparseable one all count as fresh: the budget must
    never UNDER-count, so the unknown case books the row."""
    if started_at is None:
        return True
    raw = run.get("created_at")
    if not isinstance(raw, str):
        return True
    try:
        created = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if created.tzinfo is not None:
        created = created.astimezone().replace(tzinfo=None)
    return created >= started_at - FRESH_RUN_GRACE


@dataclass(frozen=True)
class SuccessVerdict:
    """`success_by_rule`'s answer: `ok` plus the reasons it is not."""

    ok: bool
    reasons: list[str]


def success_by_rule(
    item: ExperimentItem, bundle: AnalysisBundle, coverage_floor: float
) -> SuccessVerdict:
    """The code side of `ExperimentResult.successful`.

    `successful` is the ticket into the val gate, the run's most expensive
    step, and an unbounded agent judgment would be weak here — the analysis
    skill lets the agent say yes on any low-significance positive, and at
    n=25 a no-op change shows a positive delta on SOME targeted score most of
    the time. This predicate is the floor the agent cannot talk past; the agent
    keeps the interpretation and may still veto (`successful` is the AND of
    the two, never the OR):

    1. the PRIMARY target (the item's first `evaluation_target`, all its
       targeted scores) was measured, and at least one of its rows improved
       (delta > 0 in good-direction units);
    2. no row, primary or guard, regressed beyond doubt — a CI entirely below
       zero (`ci_high < 0`);
    3. every row's paired coverage is at the gate's floor, so a hole cannot
       carry a "win" made of the easy items.
    Rows are already sign-normalized, so the same test reads a
    lower_is_better score correctly."""
    reasons: list[str] = []
    rows = bundle.per_eval
    if not rows:
        return SuccessVerdict(False, ["no measured rows — nothing to judge"])
    primary = item.evaluation_target[0] if item.evaluation_target else None
    if primary is None:
        reasons.append("item names no evaluation target")
        primary_rows: list[PerEvalResult] = []
    else:
        wanted = (
            None
            if primary.scores is None
            else {score_json_key(s) for s in primary.scores}
        )
        primary_rows = [
            r
            for r in rows
            if r.eval_id == primary.eval_id
            and (wanted is None or r.score_key in wanted)
        ]
        if not primary_rows:
            reasons.append(
                f"primary target {primary.eval_id} has no measured row (hole)"
            )
        elif not any(r.delta > 0 for r in primary_rows):
            reasons.append(
                f"primary target {primary.eval_id} did not improve on any score "
                f"(deltas {[round(r.delta, 4) for r in primary_rows]})"
            )
    for r in rows:
        label = f"{r.eval_id}/{r.score_key}"
        if r.ci_high is not None and r.ci_high < 0:
            reasons.append(
                f"{label} regressed beyond doubt (delta {r.delta:+.4f}, "
                f"CI {r.ci_low:+.4f}..{r.ci_high:+.4f})"
            )
        if r.coverage is not None and r.coverage < coverage_floor:
            reasons.append(
                f"{label} paired coverage {r.coverage:.0%} is below the "
                f"{coverage_floor:.0%} floor"
            )
    return SuccessVerdict(not reasons, reasons)


def regressed_significantly(row: PerEvalResult) -> bool:
    """Quality non-inferiority's test: the row fell and the fall
    is significant — the paired test says so, or its CI lies below zero.
    Stricter than the quality objective's "beyond doubt" (CI only), because
    under the speed objective no quality loss is traded for time."""
    return row.delta < 0 and (
        row.significant or (row.ci_high is not None and row.ci_high < 0)
    )


# Under `--objective speed` quality must be EQUAL OR BETTER,
# and "equal" has to survive noise. A truly equal candidate still flips one or
# two items per eval on ~16-24 val items (judge and sampling noise), so a zero
# per-eval tolerance would fail it on some eval almost every time across eight
# evals. The per-row tolerance is this fraction of the row's items, and never
# below one item: max(1, round_half_up(0.10 × n)) — 2 items at n=16 and n=24.
# The pooled net (rule 1) has no tolerance at all: noise cancels across rows,
# a real loss does not.
SPEED_ROW_LOSS_FRACTION = 0.10


def speed_rule_text(objective: str = "speed") -> str:
    """The efficiency objectives' whole promotion rule, in words, composed
    from the same constants the code applies — handed to the planner and the
    analyst so they do not spend arms the rule will refuse."""
    better = "faster" if objective == "speed" else "cheaper"
    metric = (
        "per-item wall-clock latency of the task call"
        if objective == "speed"
        else "per-request provider cost of the task call"
    )
    return (
        f"Objective {objective}: the metric is the {metric}. An arm counts"
        f" only when BOTH hold. (1) Clearly {better}: on the same items, more"
        f" items {better} than not with an exact sign test p < 0.05, a"
        " per-item median ratio below 1, and no significant p90 regression"
        " (95% paired bootstrap on the p90 ratio not wholly above 1); fewer"
        " than 2/3 of the planned pairs is 'incomplete', never a verdict."
        " (2) Quality equal or better, over every compared eval score: none"
        " down significantly; pooled net items >= 0 (pass/fail:"
        " fail_to_pass - pass_to_fail; other scores: the normalized [0,1]"
        " change x items), each eval weighted by its priority (p0 = 4, p1 = 3,"
        " p2 = 2, p3 = 1, no priority = 1); no single eval down more than"
        f" max(1, round({SPEED_ROW_LOSS_FRACTION:.2f} x its items)) net items"
        " (2 items at 16-24 items); every score poolable. Code applies this"
        f" on train (`successful`) and on val (the gate); the most {better}"
        " qualifying candidate wins. STAGING: one measurement engine."
        " Candidate variants (model, provider, thinking level, sampling) are"
        " SCREENED in code first (BatchPlan.screen: each candidate through"
        " the engine on a small capped queue). An arm goes through the same"
        " engine on its whole targeted train split: its parent's failures"
        " and a few passes first, then the rest, in chunks; up to 30"
        " judge-free timing pairs beside the baseline; after each chunk the"
        f" stop rules (clearly slower, or not clearly {better} once timed;"
        " clearly worse quality; futility; invalid output; budgets). Only an"
        " arm that completes pays for the analysis."
    )


def row_loss_tolerance(n_items: int) -> int:
    """The largest net item loss one row may show under the speed objective."""
    return max(1, math.floor(SPEED_ROW_LOSS_FRACTION * n_items + 0.5))


def row_net_items(row: PerEvalResult) -> float | None:
    """The row's net change in item-equivalents, positive = better.

    Pass/fail rows: paired flips, fail_to_pass − pass_to_fail (the flips are
    raw 0→1 / 1→0 transitions, so a `lower_is_better` score reverses the
    sign). Other rows: both sides' scores normalized to [0,1] with
    `normalize` (the `_SCORE_DOMAINS` table, 1.0 = best whatever the
    direction), the difference times the row's items. None when the row can
    be neither — no flips and no known score domain."""
    if row.fail_to_pass is not None and row.pass_to_fail is not None:
        gain = row.fail_to_pass - row.pass_to_fail
        return float(-gain if row.direction == "lower_is_better" else gain)
    if (
        row.score_type not in _SCORE_DOMAINS
        or row.baseline_score is None
        or row.candidate_score is None
    ):
        return None
    spec = ScoreSpec(
        key=row.score_key,
        type=row.score_type,  # type: ignore[arg-type]
        direction=row.direction or "higher_is_better",  # type: ignore[arg-type]
    )
    change = normalize(row.candidate_score, spec) - normalize(row.baseline_score, spec)
    return change * row.n_items


# The pooled net under an efficiency objective is weighted by the eval's
# priority: an eval whose spec carries a priority p
# (p0 = most important … p3) weighs 4 − p — p0 = 4, p1 = 3, p2 = 2, p3 = 1;
# an eval with no priority weighs UNPRIORITISED_WEIGHT = 1. So a loss on a
# p0 eval needs four times the gain on a p3 or unprioritised eval to be
# balanced. The per-eval noise cap is NOT weighted: it is about noise in
# that eval's items, which its priority does not change.
UNPRIORITISED_WEIGHT = 1.0


def priority_weight(priority: Any) -> float:
    """The pooled-net weight of an eval with this spec priority (0–3)."""
    if isinstance(priority, bool) or not isinstance(priority, int):
        return UNPRIORITISED_WEIGHT
    if not 0 <= priority <= 3:
        return UNPRIORITISED_WEIGHT
    return float(4 - priority)


def speed_quality_reasons(
    rows: Sequence[PerEvalResult], weights: Mapping[str, float] | None = None
) -> list[str]:
    """Why quality is NOT equal-or-better under an efficiency objective —
    empty when it is. The one rule for both the train success rule and the
    val gate:

    0. no row regressed significantly (`regressed_significantly`);
    1. pooled over every row, the net item-equivalents, each weighted by its
       eval's priority (`weights`: eval id -> weight, `priority_weight`; an
       eval not named weighs `UNPRIORITISED_WEIGHT`), are >= 0;
    2. no row loses more than `row_loss_tolerance(n_items)` net items
       (unweighted);
    3. every row can be pooled (`row_net_items` is not None), and there is at
       least one row — an unmeasured quality is not an equal one."""
    reasons: list[str] = []
    if not rows:
        return ["speed: no quality row was compared — quality is unmeasured"]
    weights = weights or {}
    weighted = any(
        weights.get(r.eval_id, UNPRIORITISED_WEIGHT) != UNPRIORITISED_WEIGHT
        for r in rows
    )
    total = 0.0
    for r in rows:
        label = f"{r.eval_id}/{r.score_key}"
        if regressed_significantly(r):
            reasons.append(
                f"speed: {label} regressed significantly (delta {r.delta:+.3f},"
                f" p={r.p_value}) — quality must be equal or better"
            )
        net = row_net_items(r)
        if net is None:
            reasons.append(
                f"speed: {label} cannot be pooled (no paired flips and no known"
                f" domain for score type {r.score_type!r}) — quality unproven"
            )
            continue
        total += weights.get(r.eval_id, UNPRIORITISED_WEIGHT) * net
        allowed = row_loss_tolerance(r.n_items)
        if -net > allowed + 1e-9:
            reasons.append(
                f"speed: {label} lost {-net:.1f} net item(s) of {r.n_items},"
                f" over the {allowed}-item noise allowance"
            )
    if total < -1e-9:
        what = "priority-weighted net item(s)" if weighted else "net item(s)"
        reasons.append(
            f"speed: quality is worse on balance — {total:+.1f} {what}"
            " pooled over every compared row (must be >= 0)"
        )
    return reasons


def success_by_speed_rule(
    bundle: AnalysisBundle,
    coverage_floor: float,
    objective: str = "speed",
    weights: Mapping[str, float] | None = None,
) -> SuccessVerdict:
    """The code side of `successful` under an efficiency objective
    (`--objective speed|cost`).

    1. the paired drive pass ran and the candidate is clearly better on the
       objective's metric (`clearly_faster` of `bundle.latency` for speed,
       of `bundle.cost` for cost — the sign test, the median ratio, the p90
       rule and the sample floor in `kiln_harness_tuner.latency`);
    2. quality is equal or better (`speed_quality_reasons`, the same rule the
       val gate applies) — whatever the eval's priority;
    3. every row's paired coverage is at the floor.
    No targeted quality gain is required: the arm set out to be efficient."""
    reasons: list[str] = []
    better = "faster" if objective == "speed" else "cheaper"
    comparison = bundle.cost if objective == "cost" else bundle.latency
    if comparison is None:
        reasons.append(f"no paired pass — whether the arm is {better} is unmeasured")
    elif not comparison.clearly_faster:
        reasons.append(f"not clearly {better} on train: {comparison.note}")
    reasons += speed_quality_reasons(bundle.per_eval, weights)
    for r in bundle.per_eval:
        label = f"{r.eval_id}/{r.score_key}"
        if r.coverage is not None and r.coverage < coverage_floor:
            reasons.append(
                f"{label} paired coverage {r.coverage:.0%} is below the "
                f"{coverage_floor:.0%} floor"
            )
    return SuccessVerdict(not reasons, reasons)


def runs_cost(runs: Iterable[dict[str, Any]]) -> float:
    """Provider spend carried by a set of EvalRuns. Public because callers
    outside the comparison lane (`measure_issue_baseline`,
    which measures ONE side and never pairs) still owe the spend accounting a
    `spend` event — and must not re-derive the subject+judge split."""
    return sum(_run_cost(run) for run in runs)


# ---- the reproduction rule ----

# Float slack on the "is the mean below perfect" test: a run of exact 1.0s
# must never read as headroom because of binary representation.
_HEADROOM_EPS = 1e-9

# Each score type's (worst, best) raw value — the normalization table.
# pass_fail_critical is Kiln's -1/0/1 encoding (critical /
# fail / pass), which is why its midpoint is a plain fail, not half a pass.
_SCORE_DOMAINS: dict[str, tuple[float, float]] = {
    "pass_fail": (0.0, 1.0),
    "pass_fail_critical": (-1.0, 1.0),
    "five_star": (1.0, 5.0),
}


def normalize(value: float, spec: ScoreSpec) -> float:
    """One raw score value → [0,1], where **1.0 is best** whatever the score
    type and direction.

    Out-of-domain values are clamped rather than trusted: the [0,1] range is
    the contract the any-headroom rule reads, and clamping is
    neutral-or-conservative in both directions (a below-worst value keeps its
    headroom either way; an above-best value can only gain headroom by being
    pulled back to the maximum).

    An unmapped score type raises rather than guessing — the same gap
    `_stats_row` names for custom scores, reported as the same error."""
    domain = _SCORE_DOMAINS.get(spec.type)
    if domain is None:
        # Known limitation: custom score types have no normalization
        # domain — a (worst, best) pair — and cannot be normalized without
        # one. This is a sibling gap to the statistics-operation mapping
        # (`_stats_row`); the two must be decided together.
        raise UnsupportedScoreTypeError(
            f"score {spec.key} has type {spec.type!r} — no normalization "
            "mapping, and no statistics operation mapping either"
        )
    worst, best = domain
    scaled = (value - worst) / (best - worst)
    scaled = min(1.0, max(0.0, scaled))
    return 1.0 - scaled if spec.direction == "lower_is_better" else scaled


@dataclass(frozen=True)
class ReproductionVerdict:
    """Does the baseline actually fail the issue's eval?
    `summary` is the parenthetical of the memory line `record_baseline_
    measurement` writes — the measured numbers, verbatim, so the planner
    weighs magnitude itself (code never decides "big enough to matter")."""

    reproduced: bool
    summary: str


def _score_values(runs: Iterable[dict[str, Any]], spec: ScoreSpec) -> list[float]:
    """Every non-null value of one score across the stored runs — the same
    "a null score is unmeasured" reading `compare_rows` pairs on."""
    values = []
    for run in runs:
        value = (run.get("scores") or {}).get(spec.key)
        if value is not None:
            values.append(float(value))
    return values


def _format_mean(mean: float, has_headroom: bool) -> str:
    """The mean as it lands in the memory line. `:.3g` rather than a fixed
    2dp, and never rounded up to the perfect value while the verdict beside
    it says the issue reproduced: `accuracy 1, n=3/3 … reproduced` is exactly
    the self-contradiction the denominator work exists to end. Continuous
    per-run scores make that reachable at ANY n (a judge answering 0.9999
    three times), so precision is widened until the text is honest, and only
    then does it fall back to a bound."""
    text = f"{mean:.3g}"
    if not has_headroom:
        return text
    for precision in (3, 6, 9):
        text = f"{mean:.{precision}g}"
        if float(text) < 1.0:
            return text
    return "<1"


def reproduction_verdict(
    runs: Sequence[dict[str, Any]],
    specs: Sequence[ScoreSpec],
    *,
    n_expected: int | None = None,
) -> ReproductionVerdict:
    """**Any headroom on any targeted score ⇒ the issue reproduces**.
    Judgment-free by construction, which is what keeps it code: there is no
    headroom threshold, because picking one would be a hidden judgment call.
    The stated consequence: one flaky item on an otherwise-perfect eval marks
    the issue reproduced — the conservative direction, since a false "real
    issue" costs one wasted candidate while a false "not reproduced" silently
    closes a genuine problem.

    Evidence for a **negative** must be complete, per score. Since the
    verdict is an `any()`, "skipping" an unmeasured score is arithmetically
    identical to counting it as passing — so skipping alone buys nothing. The
    rule is therefore stated on the *verdict*, and it is asymmetric: headroom
    anywhere is conclusive (more evidence could not un-reproduce it), but
    "nothing reproduced" holds only when **every** targeted score carried a
    value on **every** stored run. Anything less raises
    `InconclusiveNegativeError` — the item fails infra and the issue stays
    `UNMEASURED:` for a later attempt, rather than being closed `WONT_FIX:`
    on evidence that was never gathered.

    Coverage is counted per score, not per job, because the two holes are
    different sizes: `JobHealth` catches a job that errored items, but a
    judge that emits one score *sometimes* produces a clean job whose score
    lands on 1 row of 24 — and a `WONT_FIX:` off that single data point is
    the same defect one level down. The denominator therefore rides the
    summary too (`n=1/24`), so the memory line is self-diagnosing whichever
    way the verdict goes. Also reachable: a score added to an eval after its
    runs were stored is null on every cached row.

    `n_expected` (from `JobHealth`) is the denominator when it exceeds the
    stored row count — the last input that would otherwise be taken on faith.
    `health.clean` says nothing about `len(runs)` vs `n_expected`, and a
    "succeeded, 0 errors" job whose progress total outruns what the results
    route returns (a capped or paged read) would otherwise print `n=10/10`
    while the split was larger: the one case where the self-diagnosing
    summary would actively lie rather than merely omit."""
    if not specs:
        # Defence in depth — `expand_targets` refuses an empty expansion on
        # both branches. Reaching here would otherwise close an issue
        # `WONT_FIX:` having measured nothing at all.
        raise InconclusiveNegativeError("no targeted scores — nothing to conclude from")
    # The honest denominator: what the job says the split holds, floored by
    # the ITEMS actually stored (a smaller/absent n_expected never shrinks
    # it; replicate rows of one item never inflate it — 160 rows over 60
    # inputs is 60, and the verdict reads the folded record per item).
    runs = _fold_replicates(runs)
    total = max(len(runs), n_expected or 0)
    if total == 0:
        # A CLEAN job that stored nothing (an empty split, say) reaches here:
        # `ensure_measured` raises `MeasurementFailed` only for the EXHAUSTED
        # ladder. Guarded explicitly because the per-score coverage test below
        # is `len(values) < total`, which is vacuously satisfied at total=0 —
        # every score would read as fully measured and the issue would be
        # closed `WONT_FIX:` on nothing at all.
        raise InconclusiveNegativeError(
            "no stored runs for this (eval, config, baseline, train) pair — "
            "nothing to conclude from"
        )
    reproduced = False
    parts = []
    incomplete = []
    for spec in specs:
        values = _score_values(runs, spec)
        if len(values) < total:
            incomplete.append(f"{spec.key} ({len(values)}/{total})")
        if not values:
            parts.append(f"{spec.key} unmeasured, n=0/{total}")
            continue
        mean = fmean(normalize(value, spec) for value in values)
        has_headroom = mean < 1.0 - _HEADROOM_EPS
        reproduced = reproduced or has_headroom
        parts.append(
            f"{spec.key} {_format_mean(mean, has_headroom)}, n={len(values)}/{total}"
        )
    summary = f"{'; '.join(parts)} — normalized, 1.00 = best"
    if not reproduced and incomplete:
        raise InconclusiveNegativeError(
            "cannot conclude 'not reproduced': nothing showed headroom, but "
            f"{', '.join(incomplete)} did not carry a value on every one of "
            f"the {total} run(s) this split holds — the missing rows could "
            f"have changed the answer ({summary})"
        )
    return ReproductionVerdict(reproduced=reproduced, summary=summary)


# Detail-size cap for partial entries: message counts scale with the corpus
# (12 → 52 items planned) — the first few carry the failure class; the rest
# is bulk. Ids stay complete (they ARE the dark set; short and load-bearing).
PARTIAL_ERROR_MAX_MESSAGES = 3


def partial_measurement_error(
    eval_id: str, rc_id: str, split: str, health: JobHealth
) -> ErrorInfo:
    """The bundle-errors entry for a partially-measured side: rows
    exist and carry coverage; this names the dark items with the fetched
    error substance (the analyzer reads it beside the rows).

    Public because it is also the *outcome* error of a lane that cannot ship
    a partial result at all: `measure_issue_baseline` writes terminal issue
    state, so an incomplete split is a failure there, not a caveat."""
    ids = ", ".join(health.missing_item_ids) or "unknown items"
    shown = health.error_messages[:PARTIAL_ERROR_MAX_MESSAGES]
    overflow = len(health.error_messages) - len(shown)
    messages = "; ".join(shown)
    if overflow > 0:
        messages += f" (and {overflow} more)"
    return ErrorInfo(
        error_class="provider_error",
        detail=(
            f"partial: eval {eval_id} rc {rc_id} split {split} — errored items "
            f"[{ids}]: {messages}"
        ),
    )


def _job_record_error(record: dict[str, Any]) -> str | None:
    """The JobRecord's job-LEVEL failure summary (`error.error`, a JobError —
    "small failure summary stamped on the record; detail lives in the error
    log"). Distinct from the per-item error log: a worker-level crash stamps
    this and produces zero per-item rows. None when absent/blank — the
    common succeeded-with-errored-items case carries no JobError at all."""
    error = record.get("error")
    if isinstance(error, dict):
        summary = error.get("error")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()[:JOB_ERROR_MESSAGE_MAX_CHARS]
    return None


def _progress_total(progress: dict[str, Any]) -> int | None:
    """The job record's expected-item total.
    LIVE-SHAPE ASSUMPTION: prefer `total`, else `success + error` when
    `success` is affirmatively present. A record carrying neither (e.g. only
    `error`) yields None — coverage degrades to absent, never wrong."""
    if progress.get("total") is not None:
        return int(progress["total"])
    if progress.get("success") is not None:
        return int(progress["success"]) + int(progress.get("error") or 0)
    return None


class Analyzer:
    """The measurement lane: candidate measurement, the decision tree,
    and paired-stats row assembly, against one (project, task)."""

    def __init__(
        self,
        kiln: KilnApi,
        *,
        project_id: str,
        task_id: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        job_wait_timeout_secs: float = DEFAULT_JOB_WAIT_TIMEOUT_SECS,
        job_wait_margin_secs: float = DEFAULT_JOB_WAIT_MARGIN_SECS,
        job_attempts: int = DEFAULT_JOB_ATTEMPTS,
        job_retry_sleep_secs: float = DEFAULT_JOB_RETRY_SLEEP_SECS,
        rate_limit_attempts: int = DEFAULT_RATE_LIMIT_ATTEMPTS,
        max_concurrent_jobs: int | None = None,
        log: EventSink | None = None,
        run_started_at: datetime | None = None,
    ):
        self._kiln = kiln
        self.project_id = project_id
        self.task_id = task_id
        self._sleep = sleep
        self.job_wait_timeout_secs = job_wait_timeout_secs
        self.job_wait_margin_secs = job_wait_margin_secs
        self.job_attempts = job_attempts
        self.job_retry_sleep_secs = job_retry_sleep_secs
        self.rate_limit_attempts = rate_limit_attempts
        # Budget honesty: rows created before this instant were
        # paid for by an earlier run (or an earlier item of this one) and are
        # `evidence_spend`, never `spend`. None = count everything (tests,
        # and callers that predate the split).
        self.run_started_at = run_started_at
        # Provider-aware concurrency cap: one semaphore over every
        # eval-job drive this analyzer runs — wave items, the val-fill
        # gather and remeasure items all share it, so "crank the concurrency
        # down" is one knob (--max-concurrent-evals). None = unlimited (the
        # historical behavior, kept for tests).
        self._job_semaphore = (
            asyncio.Semaphore(max_concurrent_jobs) if max_concurrent_jobs else None
        )
        # Mutable on purpose: cli.py builds the Analyzer before the fan-out
        # log exists and re-points this once it does (`eval_job_errors` must
        # reach the wave log).
        self.log: EventSink = log if log is not None else _NullSink()
        # Set by the orchestrator's start-up split check: every
        # held-out item id. A train read that returns one raises
        # HeldOutLeakError — the property itself, checked on the data, so a
        # moved split boundary cannot slip val/test rows to an agent.
        self.heldout_ids: frozenset[str] = frozenset()
        # Awaited with every rc before it is measured; raises
        # ModelPolicyRefused for a model outside the policy. The one choke
        # point every eval job passes through. None = no policy.
        self.rc_guard: Callable[[str], Awaitable[None]] | None = None
        # Record ids already costed this run (`fresh_runs_cost`): each
        # record's spend is booked once, by whoever reads it first.
        self._booked: set[str] = set()
        # (eval, config, rc) -> when this run first started an
        # eval job on it — which eval runs spend reconciliation attributes
        # to this run (`kiln_harness_tuner.reconcile`).
        self.started_jobs: dict[tuple[str, str, str], datetime] = {}
        # job id -> (eval id, rc id, split) of every full measurement job in
        # flight, for the setup progress lines.
        self.running_jobs: dict[str, tuple[str, str, str]] = {}
        # One measurement per (eval, config, rc, split) at a time, shared by
        # every item that needs it: concurrent arms that each find zero
        # stored baseline rows would each drive the same baseline, and waste
        # drives and judgments. Kiln's job skips items already
        # stored, so only the in-flight window leaks; this closes it. A clean
        # result is also remembered for the run so a later item reads it
        # without spinning an empty job.
        self._measure_locks: dict[tuple[str, str, str, str], asyncio.Lock] = {}
        self._measured: dict[
            tuple[str, str, str, str], tuple[list[dict[str, Any]], JobHealth]
        ] = {}

    @contextlib.asynccontextmanager
    async def _job_slot(self) -> AsyncIterator[None]:
        """One eval-job drive's concurrency slot. Held per ATTEMPT (never
        across a backoff sleep) so a throttled job doesn't starve siblings
        while it waits out its own backoff."""
        if self._job_semaphore is None:
            yield
            return
        async with self._job_semaphore:
            yield

    def job_slot(self) -> contextlib.AbstractAsyncContextManager[None]:
        """One eval-job slot for an eval job started outside the analyzer
        (`kiln_harness_tuner.job_drives`), so `--max-concurrent-evals` caps it too."""
        return self._job_slot()

    # ---- Kiln reads ----

    def _eval_path(self, eval_id: str) -> str:
        return f"/api/projects/{self.project_id}/tasks/{self.task_id}/evals/{eval_id}"

    async def fetch_eval(self, eval_id: str) -> dict[str, Any]:
        return await self._kiln.call("GET", self._eval_path(eval_id))

    def default_eval_config_id(self, eval_def: dict[str, Any]) -> str:
        """The eval's default judge (contracts: set at eval-build, never
        loop-chosen). Within any comparison every rc runs the same config."""
        config_id = eval_def.get("current_config_id")
        if not config_id:
            raise MissingDefaultEvalConfigError(
                f"eval {eval_def.get('id')} has no default eval config "
                "(current_config_id) — the loop never chooses a judge"
            )
        return config_id

    async def stored_results(
        self, eval_id: str, eval_config_id: str, rc_id: str, split: str
    ) -> list[dict[str, Any]]:
        """Free read of the EvalRun cache (measured once, read forever)."""
        response = await self._kiln.call(
            "GET",
            f"{self._eval_path(eval_id)}/eval_config/{eval_config_id}"
            f"/run_config/{rc_id}/results",
            query_params={"split": split},
        )
        rows = response.get("results") if isinstance(response, dict) else response
        runs = [flatten_result_row(row) for row in rows or []]
        if split == "train" and self.heldout_ids:
            leaked = sorted(
                {i for run in runs if (i := _item_id(run)) in self.heldout_ids}
            )
            if leaked:
                raise HeldOutLeakError(
                    f"eval {eval_id}: the train read for rc {rc_id} returned"
                    f" {len(leaked)} held-out item(s) (val or test elsewhere):"
                    f" {', '.join(leaked[:5])}. The eval's train split selects"
                    " held-out items; its rows are not used."
                )
        return runs

    # ---- eval-job driving ----

    def fresh_runs_cost(self, runs: Iterable[dict[str, Any]]) -> float:
        """Provider spend over the rows THIS run paid for (`run_is_fresh`),
        each record counted ONCE per run — the number `--budget-usd` is
        enforced against. `runs_cost` over the same rows is the evidence cost.

        "Fresh" alone would count a record every time a comparison reads it.
        The baseline rows setup measured would be booked by setup, then again
        as the parent side of every arm, and again by every measure-baseline
        item that reuses them. A record is booked by its id the first time
        it is costed and is free after that. A record with no id cannot be
        de-duplicated and is counted each time, as before."""
        total = 0.0
        for run in runs:
            if not run_is_fresh(run, self.run_started_at):
                continue
            record_id = run.get("id")
            if record_id is not None:
                if record_id in self._booked:
                    continue
                self._booked.add(str(record_id))
            total += _run_cost(run)
        return total

    async def ensure_measured(
        self, eval_id: str, eval_config_id: str, rc_id: str, split: str
    ) -> tuple[list[dict[str, Any]], JobHealth]:
        """`_measure` once per (eval, config, rc, split): concurrent callers
        await the same in-flight measurement, later callers get the clean
        result already stored for this run. A partial (non-clean) result is
        not remembered — the next caller's job tops it up."""
        if self.rc_guard is not None:
            await self.rc_guard(rc_id)
        key = (eval_id, eval_config_id, rc_id, split)
        if key in self._measured:
            self.log.event(
                "measurement_shared", eval_id=eval_id, rc_id=rc_id, split=split
            )
            return self._measured[key]
        lock = self._measure_locks.setdefault(key, asyncio.Lock())
        waited = lock.locked()
        # Inline in the caller's own task (never a detached future): a
        # cancelled caller cancels only its own measurement, and the wave
        # gather's no-orphaned-awaits doctrine keeps holding.
        async with lock:
            if key in self._measured:
                self.log.event(
                    "measurement_shared", eval_id=eval_id, rc_id=rc_id, split=split
                )
                return self._measured[key]
            if waited:
                # The first caller's job did not come back clean — this
                # caller's job tops up whatever it left dark.
                self.log.event(
                    "measurement_topup", eval_id=eval_id, rc_id=rc_id, split=split
                )
            runs, health = await self._measure(eval_id, eval_config_id, rc_id, split)
            if health.clean:
                self._measured[key] = (runs, health)
            return runs, health

    async def _measure(
        self, eval_id: str, eval_config_id: str, rc_id: str, split: str
    ) -> tuple[list[dict[str, Any]], JobHealth]:
        """Drive the eval job to completion for (eval, rc, split) and return
        (EvalRuns, JobHealth). Retry ladder (max 1 retry); any
        non-clean job gets its errors fetched + logged verbatim.
        On exhaustion, whatever is STORED is returned with the health
        metadata (partial results); MeasurementFailed survives only
        for the zero-rows case. 4xx — e.g. the 422 missing-split error —
        propagates unchanged: hard, never a hole."""
        last_error: ErrorInfo | None = None
        n_expected: int | None = None
        missing: list[str] = []
        messages: list[str] = []
        # The last job that RAN, not the last that returned cleanly: recorded
        # the moment the server accepts it, before any way the wait can fail.
        # Every failing attempt still left a job behind, and that job is the
        # one holding the errors behind the hole the attempt produced.
        last_job_id: str | None = None
        # Class-aware ladder state:
        # `max_attempts` grows to `rate_limit_attempts` once throttling is
        # diagnosed (rate-limited items are the most recoverable — the retry
        # re-runs exactly the throttled set); `next_sleep` is set per attempt
        # from the failure's class instead of the flat 60s.
        max_attempts = self.job_attempts
        next_sleep = self.job_retry_sleep_secs
        rate_limited_attempts = 0
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            if attempt > 1 and next_sleep > 0:
                await self._sleep(next_sleep)
            next_sleep = self.job_retry_sleep_secs
            # THIS attempt's job, for the orphan-cancel paths below — a wait
            # failure must cancel its own job, never a prior attempt's
            # (already terminal) one.
            attempt_job_id: str | None = None
            try:
                async with self._job_slot():
                    job_id = await self._start_job(
                        eval_id, eval_config_id, rc_id, split
                    )
                    last_job_id = attempt_job_id = job_id
                    # Visible while it runs, for progress lines.
                    self.running_jobs[job_id] = (eval_id, rc_id, split)
                    try:
                        record = await self._wait_for_job(job_id)
                    finally:
                        self.running_jobs.pop(job_id, None)
                    status = record.get("status")
                    progress = record.get("progress") or {}
                    errored = int(progress.get("error") or 0)
                    total = _progress_total(progress)
                    # max across attempts, never last-write-wins: a retry/
                    # top-up job re-runs only the missing set (cache), so
                    # its progress totals may be subset-scoped — the largest total seen
                    # is the full set.
                    if total is not None:
                        n_expected = (
                            total if n_expected is None else max(n_expected, total)
                        )
                    if status == "succeeded" and errored == 0:
                        # The post-success results read rides the same ladder:
                        # a transient 5xx/transport failure here is one failed
                        # attempt (→ partial/hole on exhaustion), never a raw
                        # crash out of the analysis.
                        runs = await self.stored_results(
                            eval_id, eval_config_id, rc_id, split
                        )
                        # Clean success stores the full set by definition —
                        # the stored row count floors any subset-scoped total.
                        return runs, JobHealth(
                            n_expected=max(n_expected or 0, pairable_item_count(runs)),
                            job_id=last_job_id,
                        )
                    # Non-clean job: fetch + log its errors verbatim
                    # (no kind parsing for the audit trail; the dataset_ids
                    # ARE the missing-item list). The dark set UNION-
                    # accumulates across attempts (a failed or empty later
                    # fetch must not erase attempt 1's named items);
                    # staleness is handled at exhaustion, where the set is
                    # re-checked against what is finally stored — an item a
                    # later attempt landed drops out there.
                    error_rows = [
                        {
                            "dataset_id": e.get("dataset_id"),
                            "error_message": str(e.get("error_message") or "")[
                                :JOB_ERROR_MESSAGE_MAX_CHARS
                            ],
                        }
                        for e in await self._fetch_job_errors(job_id)
                    ]
                # The record's job-LEVEL failure summary (JobError):
                # a worker-level crash stamps this and leaves NO per-item rows
                # — ignoring it left the classifier blind to exactly the
                # failures with no other trace.
                job_error = _job_record_error(record)
                for row in error_rows:
                    if row["dataset_id"] and row["dataset_id"] not in missing:
                        missing.append(row["dataset_id"])
                    if row["error_message"] and row["error_message"] not in messages:
                        messages.append(row["error_message"])
                if job_error and job_error not in messages:
                    messages.append(job_error)
                # Retry policy keys on THIS attempt's messages (a stale
                # attempt-1 quota row must not re-break a later clean pass);
                # the job-level summary is one of them.
                diagnosis = diagnose_job_errors(
                    [r["error_message"] for r in error_rows if r["error_message"]]
                    + ([job_error] if job_error else [])
                )
                self.log.event(
                    "eval_job_errors",
                    eval_id=eval_id,
                    rc_id=rc_id,
                    split=split,
                    attempt=attempt,
                    error_class=diagnosis.error_class,
                    job_error=job_error,
                    errors=error_rows,
                )
                logger.warning(
                    "eval job errors: eval %s rc %s split %s attempt %d — "
                    "%d errored item(s), class %s",
                    eval_id,
                    rc_id,
                    split,
                    attempt,
                    errored,
                    diagnosis.error_class,
                )
                if diagnosis.error_class in ("quota_exhausted", "auth"):
                    # Circuit breaker: terminal + operator-actionable —
                    # every further attempt anywhere in the run burns budget
                    # and cannot succeed. The exemplar carries the provider's
                    # own remedy hint verbatim.
                    logger.error(
                        "provider circuit break (%s): %s",
                        diagnosis.error_class,
                        diagnosis.exemplar,
                    )
                    raise ProviderQuotaError(
                        ErrorInfo(
                            error_class=diagnosis.error_class,  # type: ignore[arg-type]
                            detail=(
                                f"eval {eval_id} rc {rc_id} split {split}: "
                                f"{diagnosis.exemplar}"
                            ),
                        )
                    )
                if diagnosis.error_class == "rate_limit":
                    rate_limited_attempts += 1
                    max_attempts = max(max_attempts, self.rate_limit_attempts)
                    backoff = self.job_retry_sleep_secs * (
                        2 ** (rate_limited_attempts - 1)
                    )
                    next_sleep = min(
                        RATE_LIMIT_BACKOFF_CAP_SECS,
                        max(diagnosis.retry_after_secs or 0.0, backoff),
                    )
                    last_error = ErrorInfo(
                        error_class="rate_limit",
                        detail=(
                            f"eval job ended {status} with {errored} rate-limited "
                            f"items: {diagnosis.exemplar}"
                        ),
                    )
                    continue
                if diagnosis.error_class == "malformed_output":
                    # Judge/model output bugs LOOK deterministic but are flaky
                    # in practice (JSON quoting, empty completions — a slice
                    # recovers on every re-run), and eval jobs are idempotent:
                    # each retry re-runs ONLY the still-errored subset, so
                    # extra attempts are near-free. Same escalated attempt
                    # budget as rate limits, but with no cooldown — the 60s
                    # sleep buys nothing on this class. The
                    # class stays provider_error on the wire: ErrorClass has
                    # no malformed literal.
                    max_attempts = max(max_attempts, self.rate_limit_attempts)
                    next_sleep = 0.0
                last_error = ErrorInfo(
                    error_class="provider_error",
                    detail=f"eval job ended {status} with {errored} errored items"
                    + (f" — {job_error}" if job_error else ""),
                )
            except TimeoutError:
                # The wait is a pure observer — dropping it never stops the
                # job, so the orphan keeps running (and spending) server-side
                # while the retry starts a second job racing the same items.
                # Cancel it best-effort: completed items persist
                # regardless, so the retry re-runs only what is missing.
                await self._cancel_job(attempt_job_id)
                last_error = ErrorInfo(
                    error_class="timeout",
                    detail=(
                        f"eval job wait exceeded {self.job_wait_timeout_secs}s "
                        f"(eval {eval_id} rc {rc_id} split {split})"
                    ),
                )
                continue
            except KilnAPIError as exc:
                if exc.status_code == 504:
                    await self._cancel_job(attempt_job_id)  # same orphan case
                    last_error = ErrorInfo(
                        error_class="timeout", detail=f"jobs/wait 504: {exc.body!r}"
                    )
                    continue
                if exc.status_code < 500:
                    raise  # a real answer (422 missing split, 404, …)
                await self._cancel_job(attempt_job_id)  # None when start failed
                last_error = ErrorInfo(
                    error_class="provider_error",
                    detail=f"HTTP {exc.status_code}: {exc.body!r}",
                )
                continue
            except KilnTransportError as exc:
                await self._cancel_job(attempt_job_id)
                last_error = ErrorInfo(error_class="provider_error", detail=str(exc))
                continue
            except asyncio.CancelledError:
                # The CALLER was cancelled mid-wait (the run's hard deadline,
                # a sibling's fatal): the server job survives observer
                # teardown, so it would outlive the run that paid for it —
                # the eval-lane twin of the dispatcher's kill-and-reap.
                # Shielded so the cancel POST lands while this task unwinds.
                with contextlib.suppress(BaseException):
                    await asyncio.shield(self._cancel_job(attempt_job_id))
                raise

        # Attempt exhaustion: partial results over raising — return
        # whatever is stored, with the health metadata. Zero rows (or a dead
        # results route) is the only remaining MeasurementFailed.
        error = last_error or ErrorInfo(
            error_class="provider_error", detail="eval job never ran"
        )
        try:
            runs = await self.stored_results(eval_id, eval_config_id, rc_id, split)
        except (KilnAPIError, KilnTransportError):
            runs = []
        if not runs:
            logger.error(
                "measurement failed with nothing stored: eval %s rc %s split %s "
                "after %d attempt(s) — %s",
                eval_id,
                rc_id,
                split,
                attempt,
                error.detail,
            )
            raise MeasurementFailed(error)
        # The fetched dark set can be stale (a later record-less attempt may
        # have landed items; a clean job's failed results read leaves no
        # fetch at all) — only items NOT finally stored stay missing.
        stored_ids = {item_id for run in runs if (item_id := _item_id(run)) is not None}
        logger.warning(
            "measurement exhausted with partial results: eval %s rc %s split %s "
            "— %d stored, %d still dark",
            eval_id,
            rc_id,
            split,
            len(runs),
            len([i for i in missing if i not in stored_ids]),
        )
        return runs, JobHealth(
            # Stored rows floor the total here too — a subset-scoped retry
            # total must never make coverage exceed 1.0; no totals seen at
            # all stays None (absent, never wrong).
            n_expected=(
                max(n_expected, pairable_item_count(runs))
                if n_expected is not None
                else None
            ),
            missing_item_ids=tuple(i for i in missing if i not in stored_ids),
            error_messages=tuple(messages) or (error.detail,),
            job_id=last_job_id,
        )

    async def _cancel_job(self, job_id: str | None) -> None:
        """Best-effort `POST /api/jobs/{id}/cancel`. The wait
        routes are pure observers — a timeout/504/drop/cancellation tears
        down only the awaiter, never the job — so every way the lane stops
        waiting leaves a job running and spending server-side. Cancelling is
        coverage-safe: completed items persist their EvalRuns, and a retry
        (or a later `remeasure`) re-runs only what is missing. Never fails
        the caller; cancelling an already-terminal job is a server no-op."""
        if job_id is None:
            return
        try:
            await self._kiln.call("POST", f"/api/jobs/{job_id}/cancel", retries=False)
            logger.info("cancelled in-flight eval job %s", job_id)
        except (KilnAPIError, KilnTransportError) as exc:
            logger.warning("best-effort cancel of eval job %s failed: %r", job_id, exc)

    async def diagnose_drive_failure(
        self, project_path: str, rc_id: str, item_id: str
    ) -> str | None:
        """Why did a judge-free drive of (rc, item) fail? The studio server
        answers a failed `POST …/run` with a generic 500 and keeps the
        provider's text in its log; an eval job's errors carry it. So the
        failed item is re-run as a one-item train eval job, and its errors
        are classified: a quota or auth failure raises `ProviderQuotaError`
        (the run stops); anything else returns the provider's message (or
        None when the job ran clean — the drive failure was transient).
        A key-limit error can begin on the timing drives, where the loop
        cannot see it otherwise."""
        from kiln_harness_tuner.quarantine import QuarantineError, train_eval_of

        try:
            eval_id = train_eval_of(Path(project_path), self.task_id, item_id)
        except (QuarantineError, OSError):
            eval_id = None
        if eval_id is None:
            return None
        try:
            eval_config_id = self.default_eval_config_id(await self.fetch_eval(eval_id))
            async with self._job_slot():
                job_id = await self._start_job(
                    eval_id, eval_config_id, rc_id, "train", item_ids=[item_id]
                )
                record = await self._wait_for_job(job_id)
        except (KilnAPIError, KilnTransportError, AnalysisError):
            return None
        rows = await self._fetch_job_errors(job_id)
        messages = [str(r.get("error_message")) for r in rows if r.get("error_message")]
        job_error = _job_record_error(record)
        if job_error:
            messages.append(job_error)
        if not messages:
            return None
        diagnosis = diagnose_job_errors(messages)
        self.log.event(
            "drive_failure_diagnosed",
            rc_id=rc_id,
            item_id=item_id,
            error_class=diagnosis.error_class,
            message=diagnosis.exemplar[:500],
        )
        if diagnosis.error_class in ("quota_exhausted", "auth"):
            raise ProviderQuotaError(
                ErrorInfo(
                    error_class=diagnosis.error_class,  # type: ignore[arg-type]
                    detail=f"drive of rc {rc_id} on {item_id}: {diagnosis.exemplar}",
                )
            )
        return diagnosis.exemplar

    async def _fetch_job_errors(self, job_id: str) -> list[dict[str, Any]]:
        """`GET /api/jobs/{job_id}/errors` — shape:
        `[{error_message, dataset_id, run_config_id}]`. A visibility fetch:
        any failure returns [] and never fails the measurement."""
        try:
            response = await self._kiln.call("GET", f"/api/jobs/{job_id}/errors")
        except (KilnAPIError, KilnTransportError):
            return []
        return response or []

    async def _start_job(
        self,
        eval_id: str,
        eval_config_id: str,
        rc_id: str,
        split: str,
        item_ids: Sequence[str] | None = None,
    ) -> str:
        """Create the eval job and return its id, WITHOUT waiting for it.

        Split from the wait deliberately: every way the wait can fail — the
        long-poll hanging, a 504, a 5xx, a dropped connection — leaves a real
        job running on the server, and that job is precisely the one whose
        errors explain the hole its failure produced. A combined
        create-and-wait can only hand its id back on the success path, so the
        ladder would name the last attempt that *returned* rather than the
        last attempt that *ran*."""
        self.started_jobs.setdefault(
            (str(eval_id), str(eval_config_id), str(rc_id)), datetime.now()
        )
        created = await self._kiln.call(
            "POST",
            "/api/jobs/evals/run",
            retries=True,  # eval jobs are idempotent: a replay re-runs nothing
            body={
                "project_id": self.project_id,
                "task_id": self.task_id,
                "eval_id": eval_id,
                "eval_config_id": eval_config_id,
                "run_config_id": rc_id,
                "split": split,
                # A trial names the split items it wants; the
                # server narrows the job (and its progress) to them.
                **({"item_ids": list(item_ids)} if item_ids else {}),
            },
        )
        return created["job_id"]

    async def _wait_for_job(self, job_id: str) -> dict[str, Any]:
        # Layered expiry (see DEFAULT_JOB_WAIT_MARGIN_SECS): the server's 504
        # at `timeout` is the normal expiry path; the per-request client
        # timeout sits one margin above it (the default 60s client timeout
        # would kill the long-poll first); the wait_for hard cap one margin
        # above that. `retries=False`: the ladder here owns retry policy
        # — KilnClient re-waiting internally would multiply its attempts.
        records = await asyncio.wait_for(
            self._kiln.call(
                "POST",
                "/api/jobs/wait",
                body={
                    "ids": [job_id],
                    "timeout": self.job_wait_timeout_secs,
                },
                timeout_secs=self.job_wait_timeout_secs + self.job_wait_margin_secs,
                retries=False,
            ),
            timeout=self.job_wait_timeout_secs + 2 * self.job_wait_margin_secs,
        )
        return records[0]

    # ---- paired statistics ----

    async def compare_rows(
        self,
        *,
        eval_id: str,
        specs: Sequence[ScoreSpec],
        candidate_runs: Sequence[dict[str, Any]],
        baseline_runs: Sequence[dict[str, Any]],
        baseline_rc_id: str,
        compared_to: Literal["parent", "prior_baseline"],
        split: str,
        candidate_health: JobHealth | None = None,
        baseline_health: JobHealth | None = None,
    ) -> tuple[list[PerEvalResult], list[ErrorInfo]]:
        """Assemble PerEvalResult rows for one eval: pair by item id, one
        stats call per spec, judgment-free numbers only. Public — the val pass
        reuses it. Returns (rows, holes). With health passed
        for a measured side, rows carry the coverage fields
        (n_expected/coverage/missing_item_ids) plus that side's job id; a
        health-less side (free cache read) contributes nothing."""
        candidate = _runs_by_item(candidate_runs)
        baseline = _runs_by_item(baseline_runs)

        # Pair-level completeness: both sides run the same split's item set,
        # so the larger reported total is the pair's expectation; the dark
        # set is the union of both sides' errored items.
        healths = [h for h in (candidate_health, baseline_health) if h is not None]
        totals = [h.n_expected for h in healths if h.n_expected is not None]
        n_expected = max(totals) if totals else None
        missing_item_ids = (
            sorted({i for h in healths for i in h.missing_item_ids})
            if healths
            else None
        )

        paired: list[tuple[ScoreSpec, list[float], list[float]]] = []
        holes: list[ErrorInfo] = []
        for spec in specs:
            # Pair on items where BOTH sides carry a non-null value for this
            # score (a null score is unmeasured for pairing purposes).
            item_ids = sorted(
                item_id
                for item_id, run in candidate.items()
                if item_id in baseline
                and (run.get("scores") or {}).get(spec.key) is not None
                and (baseline[item_id].get("scores") or {}).get(spec.key) is not None
            )
            if not item_ids:
                # error_class is nearest-fit: this is a data-shape condition
                # (disjoint item sets / score absent from every record), but
                # ErrorClass has no such literal — the analyzer skill should
                # read "no paired items" holes by detail, not class.
                holes.append(
                    ErrorInfo(
                        error_class="provider_error",
                        detail=(
                            f"unmeasured: eval {eval_id} score {spec.key} — no "
                            f"paired items vs rc {baseline_rc_id} (split {split})"
                        ),
                    )
                )
                continue
            values_a = [float(baseline[i]["scores"][spec.key]) for i in item_ids]
            values_b = [float(candidate[i]["scores"][spec.key]) for i in item_ids]
            paired.append((spec, values_a, values_b))

        # Per-eval spend: subject + judge cost (task_run_usage.cost +
        # eval_usage.cost) over the records feeding the comparison,
        # split evenly across rows so spend_total = Σ rows. Cached-vs-fresh
        # is deliberately indistinguishable. Known under-report
        # (accounting-only): an eval whose pairs all hole
        # carries real measurement cost but contributes $0 — spend rides the
        # rows, and holes have no rows.
        all_runs = [*candidate_runs, *baseline_runs]
        eval_spend = self.fresh_runs_cost(all_runs)
        evidence_spend = runs_cost(all_runs)
        per_row_spend = eval_spend / len(paired) if paired else 0.0
        per_row_evidence = evidence_spend / len(paired) if paired else 0.0

        # Per-item latency of the same two sides, read off the
        # same records — passive (the sides may be hours apart), so it is
        # information on every row and never a speed verdict by itself.
        latency = compare_eval_runs(baseline_runs, candidate_runs, split)
        # The per-request cost of the task call on the same
        # records. With no per-request ceiling, this and `latency` are how an
        # arm's cost and speed stay visible without timing drives.
        cost = compare_eval_run_costs(baseline_runs, candidate_runs, split)
        rows = []
        for spec, values_a, values_b in paired:
            rows.append(
                await self._stats_row(
                    eval_id=eval_id,
                    spec=spec,
                    values_a=values_a,
                    values_b=values_b,
                    baseline_rc_id=baseline_rc_id,
                    compared_to=compared_to,
                    split=split,
                    spend_usd=per_row_spend,
                    evidence_spend_usd=per_row_evidence,
                    n_expected=n_expected,
                    missing_item_ids=missing_item_ids,
                    # Per-side, unlike the coverage fields above: those are
                    # pair-level unions, but a job id only ever explains its
                    # own side's dark items.
                    baseline_job_id=(
                        baseline_health.job_id if baseline_health else None
                    ),
                    candidate_job_id=(
                        candidate_health.job_id if candidate_health else None
                    ),
                )
            )
        for row in rows:
            row.latency = latency
            row.cost = cost
        return rows, holes

    async def _stats_row(
        self,
        *,
        eval_id: str,
        spec: ScoreSpec,
        values_a: list[float],
        values_b: list[float],
        baseline_rc_id: str,
        compared_to: Literal["parent", "prior_baseline"],
        split: str,
        spend_usd: float,
        evidence_spend_usd: float = 0.0,
        n_expected: int | None = None,
        missing_item_ids: list[str] | None = None,
        baseline_job_id: str | None = None,
        candidate_job_id: str | None = None,
    ) -> PerEvalResult:
        n = len(values_a)
        if spec.type in BINARY_SCORE_TYPES:
            # Scores are stored numeric; >= 0.5 is a pass (pass_fail 0/1;
            # pass_fail_critical's critical values sit below).
            outcomes_a = [1 if v >= 0.5 else 0 for v in values_a]
            outcomes_b = [1 if v >= 0.5 else 0 for v in values_b]
            body: dict[str, Any] = {
                "operation": "mcnemar_paired",
                "outcomes_a": outcomes_a,
                "outcomes_b": outcomes_b,
            }
            # Pass rates, as fractions — the absolutes behind the delta.
            baseline_score = sum(outcomes_a) / n
            candidate_score = sum(outcomes_b) / n
            delta_raw = candidate_score - baseline_score
            # The ceiling on the delta, in good-direction units: a binary
            # score cannot gain more than the baseline's failures.
            headroom = (
                baseline_score
                if spec.direction == "lower_is_better"
                else 1.0 - baseline_score
            )
            # Flip counts are raw 0→1 / 1→0 transitions, direction-agnostic:
            # under a lower_is_better binary score a "fail_to_pass" is a
            # WORSENING — the analyzer reads flips beside the sign-normalized
            # delta, which carries the direction.
            fail_to_pass = sum(
                1 for a, b in zip(outcomes_a, outcomes_b, strict=True) if a < b
            )
            pass_to_fail = sum(
                1 for a, b in zip(outcomes_a, outcomes_b, strict=True) if a > b
            )
        elif spec.type in RATING_SCORE_TYPES:
            body = {
                "operation": "compare_paired",
                "values_a": values_a,
                "values_b": values_b,
            }
            baseline_score = sum(values_a) / n
            candidate_score = sum(values_b) / n
            delta_raw = candidate_score - baseline_score
            # Rating scales carry no ceiling the row can state.
            headroom = None
            # Flip counts are binary-only — paired delta + significance
            # carry rating scales.
            fail_to_pass = None
            pass_to_fail = None
        else:
            # Known limitation: custom score types have no
            # statistics-operation mapping.
            raise UnsupportedScoreTypeError(
                f"eval {eval_id} score {spec.key} has type {spec.type!r} — no "
                "statistics operation mapping"
            )

        response = (
            await self._kiln.call("POST", "/api/statistics", body=body, retries=True)
            or {}
        )
        # Response shapes, read from the server source
        # (kiln_server/statistics_api.py):
        #   mcnemar_paired  → "p_exact" (exact two-sided McNemar) + a Newcombe
        #                     paired CI in "ci_low"/"ci_high"
        #   compare_paired  → "wilcoxon_p" (None below five non-zero paired
        #                     differences) + a paired-bootstrap CI in
        #                     "ci_low"/"ci_high"; "n_pairs_used" == 0 means
        #                     nothing was comparable and no CI is served
        # Neither carries "p_value" or any standard error. The code must
        # not accept "p_value"/"p_exact" for compare_paired: the first
        # rating-scale score would raise StatsResponseError after the
        # measurement was paid for. The guard stays:
        # a missing p key is a contract mismatch and fails hard, never a
        # silent p=None (which would read every row as insignificant).
        # The server's own "significant" is read only where the p<.05
        # rule has no p to work with (the small-n Wilcoxon gap).
        operation = body["operation"]
        p_key = "p_exact" if operation == "mcnemar_paired" else "wilcoxon_p"
        if p_key not in response and response.get("n_pairs_used") != 0:
            raise StatsResponseError(
                f"POST /api/statistics ({operation}) returned no {p_key!r} "
                f"(got keys {sorted(response)}) — response field names need "
                "verifying against the live API"
            )
        p_value = _optional_float(response.get(p_key))
        ci_low_raw = _optional_float(response.get("ci_low"))
        ci_high_raw = _optional_float(response.get("ci_high"))
        if p_value is not None:
            significant = p_value < SIGNIFICANCE_ALPHA
        elif (
            operation == "compare_paired"
            and ci_low_raw is not None
            and ci_high_raw is not None
        ):
            # The server's own rule when Wilcoxon is omitted: the bootstrap
            # CI excludes zero.
            significant = ci_low_raw > 0 or ci_high_raw < 0
        else:
            significant = False

        # Sign-normalize to good-direction units: win is always delta > 0.
        # The absolutes below are deliberately NOT normalized — they are the
        # stored scores, so on a lower_is_better row they fall while this
        # delta reads positive (the overview format marks that row `↓`).
        # The CI flips WITH the delta (and its ends swap), so ci_low > 0
        # always reads "better beyond doubt" and ci_high < 0 "worse".
        if spec.direction == "lower_is_better":
            delta = -delta_raw
            ci_low, ci_high = (
                (-ci_high_raw if ci_high_raw is not None else None),
                (-ci_low_raw if ci_low_raw is not None else None),
            )
        else:
            delta = delta_raw
            ci_low, ci_high = ci_low_raw, ci_high_raw
        return PerEvalResult(
            eval_id=eval_id,
            score_key=spec.key,
            score_type=spec.type,
            direction=spec.direction,
            split=split,
            baseline_rc_id=baseline_rc_id,
            compared_to=compared_to,
            n_items=n,
            delta=delta,
            p_value=p_value,
            significant=significant,
            ci_low=ci_low,
            ci_high=ci_high,
            headroom=headroom,
            fail_to_pass=fail_to_pass,
            pass_to_fail=pass_to_fail,
            spend_usd=spend_usd,
            evidence_spend_usd=evidence_spend_usd,
            n_expected=n_expected,
            coverage=n / n_expected if n_expected else None,
            missing_item_ids=missing_item_ids,
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            baseline_job_id=baseline_job_id,
            candidate_job_id=candidate_job_id,
        )

    # ---- the decision tree ----

    async def run_experiment_analysis(
        self,
        item: ExperimentItem,
        candidate_rc_id: str,
        baseline: BaselineRef,
        baseline_lineage: Sequence[str],
        progress: Callable[[str], None] | None = None,
    ) -> AnalysisBundle:
        """Measure the candidate on split=train per `evaluation_target`, run
        the decision tree per eval, and assemble the judgment-free
        bundle. `baseline_lineage` = promotion-history rc ids, newest →
        oldest (memory.py parses the baseline record).

        Concurrency: every target's measurement fires
        at once; per-eval comparison/stats stay sequential after its own
        jobs land. Rows/holes assemble in target order — result-equivalent
        to a sequential loop. Hard errors (the no-degradation rows)
        let every sibling finish first, then the first raises (same doctrine
        as the wave gather).

        `progress`: called with a short human line as each eval's measurement starts and
        completes ("measuring <eval>…" / "<eval> done", or "<eval> failed"
        when a hard error escapes — start/done replaces i/N now that targets
        run concurrently) — items.py forwards it as
        `item_detail` events. Per-eval ITEM counts stay unwired: jobs are
        driven by one `/api/jobs/wait` long-poll that returns only at a
        terminal state (see `_wait_for_job`), so there is no mid-flight count
        to tick without new polling machinery."""
        parent_rc_id = baseline.rc_id
        if isinstance(item.work, FixWork) and item.work.parent_rc_id:
            parent_rc_id = item.work.parent_rc_id  # the rare escape hatch

        tasks = [
            asyncio.ensure_future(
                self._measure_target(
                    target, candidate_rc_id, parent_rc_id, baseline_lineage, progress
                )
            )
            for target in item.evaluation_target
        ]
        # A hard error on one target cancels the siblings: the
        # run exits on it, so their remaining jobs would be spend on a dead run.
        error, _pending = await first_error_or_done(tasks)
        if error is not None:
            raise error
        rows: list[PerEvalResult] = []
        holes: list[ErrorInfo] = []
        for task in tasks:
            eval_rows, eval_holes = task.result()
            rows.extend(eval_rows)
            holes.extend(eval_holes)

        return AnalysisBundle(
            per_eval=rows,
            spend_total_usd=sum(row.spend_usd for row in rows),
            evidence_spend_usd=sum(row.evidence_spend_usd for row in rows),
            errors=holes,
        )

    async def _measure_target(
        self,
        target: EvalTarget,
        candidate_rc_id: str,
        parent_rc_id: str,
        baseline_lineage: Sequence[str],
        progress: Callable[[str], None] | None,
    ) -> tuple[list[PerEvalResult], list[ErrorInfo]]:
        """One target's leg of the decision tree: fetch the eval, expand
        specs, measure the candidate, compare against the reference. Progress
        lines: "measuring <eval>…" at start, then "<eval> done" on success or
        "<eval> failed" when a hard error escapes."""
        if progress is not None:
            progress(f"measuring {target.eval_id}…")
        try:
            eval_def = await self.fetch_eval(target.eval_id)
            eval_config_id = self.default_eval_config_id(eval_def)
            specs = expand_targets(eval_def, target)

            try:
                candidate_runs, candidate_health = await self.ensure_measured(
                    target.eval_id, eval_config_id, candidate_rc_id, "train"
                )
            except MeasurementFailed as fail:
                result: tuple[list[PerEvalResult], list[ErrorInfo]] = (
                    [],
                    self._holes_for(specs, target.eval_id, candidate_rc_id, fail),
                )
            else:
                rows, holes = await self._compare_against_reference(
                    eval_id=target.eval_id,
                    eval_config_id=eval_config_id,
                    specs=specs,
                    candidate_runs=candidate_runs,
                    candidate_health=candidate_health,
                    parent_rc_id=parent_rc_id,
                    baseline_lineage=baseline_lineage,
                )
                if not candidate_health.clean:
                    # Partial candidate measurement: the rows carry
                    # the coverage fields; the bundle's error detail carries
                    # the fetched substance (messages + ids).
                    holes = [
                        *holes,
                        partial_measurement_error(
                            target.eval_id, candidate_rc_id, "train", candidate_health
                        ),
                    ]
                result = (rows, holes)
        except BaseException:
            if progress is not None:
                progress(f"{target.eval_id} failed")
            raise
        if progress is not None:
            progress(f"{target.eval_id} done")
        return result

    async def _compare_against_reference(
        self,
        *,
        eval_id: str,
        eval_config_id: str,
        specs: Sequence[ScoreSpec],
        candidate_runs: Sequence[dict[str, Any]],
        candidate_health: JobHealth | None,
        parent_rc_id: str,
        baseline_lineage: Sequence[str],
    ) -> tuple[list[PerEvalResult], list[ErrorInfo]]:
        # stored parent results → attribution, free. A COMPLETE
        # stored set, that is: a partially-stored parent (the train set grew,
        # or items errored when it was measured) must not be compared
        # on the intersection: the code rule's coverage clause would veto the
        # win with nothing in the loop able to repair it. It falls through to
        # the paid path below, where `ensure_measured`'s item-grained cache
        # runs only the missing items.
        stored = await self.stored_results(
            eval_id, eval_config_id, parent_rc_id, "train"
        )
        expected = candidate_health.n_expected if candidate_health else None
        complete = bool(stored) and (
            expected is None or pairable_item_count(stored) >= expected
        )
        if complete:
            return await self.compare_rows(
                eval_id=eval_id,
                specs=specs,
                candidate_runs=candidate_runs,
                baseline_runs=stored,
                baseline_rc_id=parent_rc_id,
                compared_to="parent",
                split="train",
                candidate_health=candidate_health,
            )

        # Parent unmeasured — screen against the latest measured baseline in
        # the promotion lineage before paying. A PARTIAL parent skips
        # the screen: topping it up is the cheap, exact answer.
        reference = (
            None
            if stored
            else await self._latest_baseline_results(
                eval_id, eval_config_id, baseline_lineage, candidate_runs, specs
            )
        )
        if reference is not None:
            reference_rc_id, reference_runs = reference
            screen_rows, screen_holes = await self.compare_rows(
                eval_id=eval_id,
                specs=specs,
                candidate_runs=candidate_runs,
                baseline_runs=reference_runs,
                baseline_rc_id=reference_rc_id,
                compared_to="prior_baseline",
                split="train",
                candidate_health=candidate_health,
            )
            # Screen-out needs actual rows: an empty comparison is not
            # evidence the candidate failed the screen — fall through and
            # measure the parent instead of vacuously screening out.
            if screen_rows and not any(row.delta > 0 for row in screen_rows):
                # Screen-out: "didn't beat the latest measured baseline,
                # attribution unmeasured" — never "the change hurt".
                return screen_rows, screen_holes
            # An improvement needing attribution — pay for the parent once.
            # The screen booked the candidate's records; its rows are dropped
            # below, so their spend rides on the final rows (booked once,
            # reported once).
            screen_spend = sum(r.spend_usd for r in screen_rows)
        else:
            screen_spend = 0.0

        try:
            parent_runs, parent_health = await self.ensure_measured(
                eval_id, eval_config_id, parent_rc_id, "train"
            )
        except MeasurementFailed as fail:
            return [], self._holes_for(specs, eval_id, parent_rc_id, fail)
        rows, holes = await self.compare_rows(
            eval_id=eval_id,
            specs=specs,
            candidate_runs=candidate_runs,
            baseline_runs=parent_runs,
            baseline_rc_id=parent_rc_id,
            compared_to="parent",
            split="train",
            candidate_health=candidate_health,
            baseline_health=parent_health,
        )
        if rows and screen_spend:
            for row in rows:
                row.spend_usd += screen_spend / len(rows)
        if not parent_health.clean:
            holes = [
                *holes,
                partial_measurement_error(
                    eval_id, parent_rc_id, "train", parent_health
                ),
            ]
        return rows, holes

    async def trial(
        self, target: EvalTarget, rc_id: str, target_items: Sequence[str]
    ) -> TrialResult:
        """The residual trial: drive and judge the candidate on
        its `target_items` alone — the baseline's failing train items the
        hypothesis claims to fix — before any full split is paid for. One
        job, narrowed server-side to those items (their rows are stored like
        any other and the later full run skips them). `landed` = every
        target stored a row at or above the pass line on the primary
        targeted score. A job that stores nothing at all is infra
        (`MeasurementFailed`), never "not landed"."""
        if self.rc_guard is not None:
            await self.rc_guard(rc_id)
        eval_def = await self.fetch_eval(target.eval_id)
        eval_config_id = self.default_eval_config_id(eval_def)
        spec = expand_targets(eval_def, target)[0]
        wanted = [str(i) for i in target_items]
        async with self._job_slot():
            job_id = await self._start_job(
                target.eval_id, eval_config_id, rc_id, "train", item_ids=wanted
            )
            record = await self._wait_for_job(job_id)
        await self._trial_quota_check(target.eval_id, rc_id, job_id, record)
        runs = await self.stored_results(target.eval_id, eval_config_id, rc_id, "train")
        by_item = _runs_by_item(runs)
        scores: dict[str, float | None] = {}
        for item_id in wanted:
            row = by_item.get(item_id)
            value = (row.get("scores") or {}).get(spec.key) if row else None
            scores[item_id] = (
                normalize(float(value), spec) if value is not None else None
            )
        if all(v is None for v in scores.values()):
            error = ErrorInfo(
                error_class="provider_error",
                detail=(
                    f"trial of rc {rc_id} on {len(wanted)} target item(s) stored no "
                    f"row (job {job_id} {record.get('status')}, "
                    f"progress {record.get('progress')})"
                ),
            )
            raise MeasurementFailed(error)
        result = TrialResult(
            eval_id=target.eval_id,
            score_key=spec.key,
            target_items=wanted,
            scores=scores,
            landed=all(v is not None and v >= 0.5 for v in scores.values()),
            spend_usd=self.fresh_runs_cost(
                run for run in runs if _item_id(run) in set(wanted)
            ),
            job_id=job_id,
            latencies={
                i: ms
                for i in wanted
                if i in by_item and (ms := latency_of_eval_run(by_item[i])) is not None
            },
            costs={
                i: c
                for i in wanted
                if i in by_item and (c := task_cost_of_eval_run(by_item[i])) is not None
            },
            item_costs={i: _run_cost(by_item[i]) for i in wanted if i in by_item},
            outputs={
                i: _row_output(by_item[i])
                for i in wanted
                if i in by_item and _row_output(by_item[i]) is not None
            },
        )
        self.log.event(
            "trial",
            eval_id=target.eval_id,
            rc_id=rc_id,
            landed=result.landed,
            scores=scores,
        )
        return result

    async def judge_items(
        self,
        eval_id: str,
        eval_config_id: str,
        rc_id: str,
        item_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        """One eval job on `rc_id`, narrowed to these TRAIN items, and the
        stored rows of exactly those items (the noise check).
        Kiln skips an item already stored for this (eval config, run
        config), so a fresh answer needs a run config or an eval config
        that has never scored the item — the caller's job, not this one.
        A quota or auth failure raises `ProviderQuotaError`, as in
        `trial`; a job that stores nothing returns an empty list."""
        if self.rc_guard is not None:
            await self.rc_guard(rc_id)
        wanted = [str(i) for i in item_ids]
        async with self._job_slot():
            job_id = await self._start_job(
                eval_id, eval_config_id, rc_id, "train", item_ids=wanted
            )
            record = await self._wait_for_job(job_id)
        await self._trial_quota_check(eval_id, rc_id, job_id, record)
        runs = await self.stored_results(eval_id, eval_config_id, rc_id, "train")
        keep = set(wanted)
        return [run for run in runs if _item_id(run) in keep]

    def fresh_judge_cost(self, runs: Iterable[dict[str, Any]]) -> float:
        """`fresh_runs_cost` for rows that re-judged an output already paid
        for: only the judge's `eval_usage` is new spend. The results route
        resolves `task_run_usage` from the scored task run, which a re-judge
        shares with the row it copies, so counting it would book that drive
        twice. Each record is booked once, like `fresh_runs_cost`."""
        total = 0.0
        for run in runs:
            if not run_is_fresh(run, self.run_started_at):
                continue
            record_id = run.get("id")
            if record_id is not None:
                if record_id in self._booked:
                    continue
                self._booked.add(str(record_id))
            total += float((run.get("eval_usage") or {}).get("cost") or 0.0)
        return total

    async def _trial_quota_check(
        self, eval_id: str, rc_id: str, job_id: str, record: dict[str, Any]
    ) -> None:
        """A narrowed job that did not run clean: its errors are logged and
        classified like a full job's. A quota or auth failure raises
        `ProviderQuotaError` (the measurement engine judges
        through `trial`, so a key limit must stop the run here too)."""
        progress = record.get("progress") or {}
        if record.get("status") == "succeeded" and not int(progress.get("error") or 0):
            return
        error_rows = [
            {
                "dataset_id": e.get("dataset_id"),
                "error_message": str(e.get("error_message") or "")[
                    :JOB_ERROR_MESSAGE_MAX_CHARS
                ],
            }
            for e in await self._fetch_job_errors(job_id)
        ]
        job_error = _job_record_error(record)
        diagnosis = diagnose_job_errors(
            [r["error_message"] for r in error_rows if r["error_message"]]
            + ([job_error] if job_error else [])
        )
        self.log.event(
            "eval_job_errors",
            eval_id=eval_id,
            rc_id=rc_id,
            split="train",
            attempt=1,
            error_class=diagnosis.error_class,
            job_error=job_error,
            errors=error_rows,
        )
        if diagnosis.error_class in ("quota_exhausted", "auth"):
            raise ProviderQuotaError(
                ErrorInfo(
                    error_class=diagnosis.error_class,  # type: ignore[arg-type]
                    detail=f"eval {eval_id} rc {rc_id} split train: "
                    f"{diagnosis.exemplar}",
                )
            )

    async def _latest_baseline_results(
        self,
        eval_id: str,
        eval_config_id: str,
        baseline_lineage: Sequence[str],
        candidate_runs: Sequence[dict[str, Any]],
        specs: Sequence[ScoreSpec],
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """Walk the promotion lineage newest → oldest; return the first rc
        with full train coverage on this eval: its stored results
        carry every targeted score, non-null, for every item the candidate
        measured (a score added to the eval after that baseline was measured
        disqualifies it — a vacuous comparison is no screen). None → no
        screen — non-baseline rcs never qualify."""
        candidate_ids = {
            item_id for run in candidate_runs if (item_id := _item_id(run)) is not None
        }
        if not candidate_ids:
            return None
        for rc_id in baseline_lineage:
            runs = await self.stored_results(eval_id, eval_config_id, rc_id, "train")
            by_item = _runs_by_item(runs)
            if all(
                item_id in by_item
                and all(
                    (by_item[item_id].get("scores") or {}).get(spec.key) is not None
                    for spec in specs
                )
                for item_id in candidate_ids
            ):
                return rc_id, runs
        return None

    @staticmethod
    def _holes_for(
        specs: Sequence[ScoreSpec],
        eval_id: str,
        rc_id: str,
        fail: MeasurementFailed,
    ) -> list[ErrorInfo]:
        """One named hole per still-unmeasured (eval, score) pair."""
        return [
            ErrorInfo(
                error_class=fail.error.error_class,
                detail=(
                    f"unmeasured: eval {eval_id} score {spec.key} rc {rc_id} "
                    f"split train — {fail.error.detail}"
                ),
            )
            for spec in specs
        ]
