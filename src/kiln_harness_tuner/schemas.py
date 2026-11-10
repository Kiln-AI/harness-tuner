"""Every Pydantic model for kiln-harness-tuner, including the status models
(`ReportInput` references `RunStatus`). status.py adds the run-state
*behavior* over them.

Field comments below keep only the load-bearing notes; docs/architecture.md
carries the full rationale.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")

# ---- shared support types ----

_NON_KEY_CHARS = re.compile(r"[^a-z0-9_]")


def score_json_key(name: str) -> str:
    """Kiln's score-name → scores-dict-key convention (lowercase, spaces to
    underscores, other punctuation dropped). Lives here — not analysis.py —
    because the EvalTarget dedup below must key scores the same way
    measurement expansion does (analysis.py imports it back)."""
    return _NON_KEY_CHARS.sub("", name.strip().lower().replace(" ", "_"))


def _dedup_scores(base: list[str], extra: list[str]) -> list[str]:
    """First-seen spelling wins; duplicates keyed by `score_json_key` — the
    normalization expand_targets applies — so "Out Of Scope" and "out_of_scope"
    are one score, never two identical ScoreSpecs/rows."""
    out = list(base)
    seen = {score_json_key(s) for s in base}
    for s in extra:
        key = score_json_key(s)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


ErrorClass = Literal[
    "rate_limit",
    "quota_exhausted",
    "auth",
    "timeout",
    "provider_error",
    "parse_failure",
    "agent_crash",
]
# `quota_exhausted` / `auth`: terminal,
# operator-actionable provider conditions — a 402 out-of-credits or a bad key.
# They circuit-break the run (analysis.ProviderQuotaError) instead of riding
# the generic retry ladder: retrying credit-exhaustion errors blindly burns
# time and ends with no report.

# Spend lanes: subscription_equivalent =
# every `claude -p` dispatch's total_cost_usd — quota burn now, real cost at
# deployment; provider = eval-lane cost (subject task_run_usage + judge
# eval_usage) — real money today. Every `spend` event carries one.
SpendLane = Literal["subscription_equivalent", "provider"]
SUBSCRIPTION_LANE: SpendLane = "subscription_equivalent"
PROVIDER_LANE: SpendLane = "provider"

# What the two lanes are CALLED where a human reads them. The
# wire values above are frozen — they are written into every historical wave
# log — so the rename lives here, at the display boundary, and every renderer
# (terminal UI, report appendix) reads these rather than spelling the
# words itself.
SPEND_LANE_LABELS: dict[str, str] = {
    SUBSCRIPTION_LANE: "Claude Code",
    PROVIDER_LANE: "Kiln",
}
# The agent lane is named after the agent CLI the run dispatches through.
AGENT_CLI_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
}


def spend_lane_labels(agent_cli: str = "claude") -> dict[str, str]:
    """`SPEND_LANE_LABELS`, with the agent lane named for `agent_cli`."""
    return {
        **SPEND_LANE_LABELS,
        SUBSCRIPTION_LANE: AGENT_CLI_LABELS.get(agent_cli, agent_cli),
    }


# Which side of the split each `spend` event's `source` belongs to.
#
# This exists because the `lane` field is NEWER than the `spend` event: older
# wave logs hold spend events that carry a source but NO lane, and they would
# fold into an "unknown" bucket, sorted into neither side. The source is
# unambiguous, so the fold derives the lane from it when the event predates the
# field (status.py `_on_spend`). Event schema unchanged — emitters still send
# `lane`, and a lane on the event always wins.
#
# Kiln-side = provider tokens Kiln burns running drives/eval jobs this run
# triggered. Everything else is one `claude -p` agent session.
KILN_SPEND_SOURCES = frozenset(
    {"eval_jobs", "val_pass", "noise_check", "model_preflight"}
)
CLAUDE_CODE_SPEND_SOURCES = frozenset(
    {
        "preflight",
        "describe_eval",
        "batch_plan",
        "eval_build",
        "create_experiment_rc",
        "analyze_experiment_results",
        "promote_best",
        "report_writer",
        "integrity_review",
        "generality_review",
    }
)


def lane_for_source(source: str) -> SpendLane | None:
    """The lane a `spend` source belongs to, or None for a source this
    vocabulary does not know — which stays `unknown` in the fold rather than
    being guessed into one of the two lanes. A new emitter must be added to
    one of the two sets above (and the fold's own test names them)."""
    if source in KILN_SPEND_SOURCES:
        return PROVIDER_LANE
    if source in CLAUDE_CODE_SPEND_SOURCES:
        return SUBSCRIPTION_LANE
    return None


class ErrorInfo(BaseModel):
    error_class: ErrorClass
    detail: str


class Usage(BaseModel):
    # Defaults to zero: a failed_infra result produced before any subprocess
    # ran has nothing to report.
    cost_usd: float = 0.0
    tokens: int = 0
    wall_secs: float = 0.0
    # Tokens seen in the transcript of a session whose cost could NOT be
    # read — a `claude -p` killed at its wall-clock timeout never prints its
    # result payload. Summed beside cost so a run's books say
    # how much they are missing instead of booking the run's most expensive
    # session shape at $0.
    unmeasured_tokens: int = 0


class WinRef(BaseModel):
    experiment_memory_id: str
    rc_id: str


class TargetBlock(BaseModel):
    """The standard target block, dispatcher-injected into every prompt —
    lives on `Ctx`, not in each input model."""

    project_path: str  # on-disk Kiln project dir — for exploring .kiln files
    project_id: str
    task_id: str
    kiln_host: str


# ---- batch plan (batch-selection agent output) ----


class OpenUnknown(BaseModel):
    question: str
    default_assumed: str
    gates: str
    reask_when: str  # the re-ask condition the write format records


class FixWork(BaseModel):
    issue_memory_id: str
    hypothesis: str
    technique: str  # prompt | skill | tool_config | new_tool | … — never
    #   fine-tuning or GEPA-style optimizers
    predicted_effect: str
    needs_eval: bool
    eval_id: str | None = None  # required when needs_eval=False
    cohesion_notes: str | None = None
    head_to_head_group: str | None = None
    parent_rc_id: str | None = None  # escape hatch; absent = batch_baseline

    @model_validator(mode="after")
    def _eval_id_required_unless_building(self) -> FixWork:
        if not self.needs_eval and self.eval_id is None:
            raise ValueError("eval_id is required when needs_eval=False")
        return self

    # Reuse a run config a PRIOR run already built and measured
    #   (it lost only its analysis to that run's deadline; the abandoned
    #   experiment record names it on its `rc:` line). Set → no build session
    #   and no build spend; the item measures and analyzes that rc. The arm
    #   still opens its own experiment record, marked `reused rc:` so the
    #   lever ladder does not count it as a second attempt.
    existing_rc_id: str | None = None
    # The eval-input ids (train lane) this fix is built to flip —
    #   the baseline's failing items whose traces the hypothesis explains.
    #   Non-empty → they lead the arm's measurement queue
    #   (`kiln_harness_tuner.measure`), and the `targets` stop rule ends the
    #   arm NOT LANDED after its first chunk when any of them still fails — the rest
    #   of the split is never judged. Empty → no named block. Never test/val
    #   ids.
    target_items: list[str] = []
    # The wave's out-of-the-box arm — chosen for the size of its
    #   possible win, not for confidence in it (a progressive-disclosure
    #   skill in place of a 52 KB prompt, a pipeline, a new surface). The
    #   planner skill reserves one slot per wave for it and forbids deferring
    #   the same idea more than once; the lever ladder's "two text arms then
    #   change surface" rule does not count it. Recorded on the experiment
    #   memory so the analyzer reads it as a high-variance prior.
    wildcard: bool = False
    # The `residual` stop rule on the queue's discriminating
    #   block (the parent's failures and a few passes, judged first) is on by
    #   default for a fix arm. `residual_gate: false` opts this arm out — for
    #   a change whose effect is diffuse by design (a whole-prompt rewrite)
    #   — and needs a `residual_gate_reason`.
    residual_gate: bool = True
    residual_gate_reason: str = ""
    # This arm's stop rules for the measurement engine, merged
    #   over code's defaults (`stop_rules.merge_rules`). None = the defaults.
    stop_rules: list[StopRule] | None = None

    @model_validator(mode="after")
    def _budget_is_on_the_item(self) -> FixWork:
        _refuse_max_usd_rule(self.stop_rules, "experiment item")
        return self

    @model_validator(mode="after")
    def _opt_out_needs_a_reason(self) -> FixWork:
        if not self.residual_gate and not self.residual_gate_reason.strip():
            raise ValueError(
                "residual_gate: false needs a residual_gate_reason (why this"
                " arm's effect is diffuse)"
            )
        return self


class ResidualItem(BaseModel):
    """One item of an arm's discriminating block: an item the
    parent FAILS (`failing`) or PASSES (`guard`), and the built run config's
    normalized score on it (None = no stored row)."""

    eval_id: str
    item_id: str
    role: Literal["failing", "guard"]
    score: float | None = None

    @property
    def passes(self) -> bool:
        return self.score is not None and self.score >= 0.5

    @property
    def broke(self) -> bool:
        return self.role == "guard" and self.score is not None and self.score < 0.5


class ResidualGateResult(BaseModel):
    """The discriminating block of an arm's measurement, as the `residual`
    stop rule reads it (`kiln_harness_tuner.measure`). The record keeps
    this shape: `MemoryWriter.record_residual` writes its lines."""

    seed: str
    items: list[ResidualItem]
    fixed: int
    failing: int
    broke: int
    guards: int
    guards_unmeasured: int = 0
    required: int  # how many failing items had to flip
    passed: bool
    spend_usd: float = 0.0
    # The guard half is a test against the noise flip rate.
    #   `guard_p`: one-sided exact binomial p of `broke` or more breaks in
    #   the guards with a row, at `noise_rate`; `guards_clear`: that p is
    #   below `alpha`. None on a record written before.
    noise_rate: float | None = None
    alpha: float | None = None
    guard_p: float | None = None
    guards_clear: bool | None = None

    @property
    def guard_text(self) -> str:
        """ "1 of 4 guards broke, within noise (p=0.34)"."""
        n = self.guards - self.guards_unmeasured
        if self.guard_p is None:
            return f"{self.broke}/{self.guards} guards broke"
        verdict = "clearly above noise" if self.guards_clear else "within noise"
        return f"{self.broke} of {n} guards broke, {verdict} (p={self.guard_p:.2g})"

    @property
    def summary(self) -> str:
        return f"{self.fixed}/{self.failing} fixed, {self.guard_text}"

    def ids(self, role: str, *, passing: bool) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.items:
            if r.role == role and r.passes == passing:
                out.setdefault(r.eval_id, []).append(r.item_id)
        return {e: sorted(v) for e, v in sorted(out.items())}

    @property
    def still_failing(self) -> dict[str, list[str]]:
        return self.ids("failing", passing=False)

    @property
    def broken_guards(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.items:
            if r.broke:
                out.setdefault(r.eval_id, []).append(r.item_id)
        return {e: sorted(v) for e, v in sorted(out.items())}


class MeasurementSummary(BaseModel):
    """An arm's measurement through the engine
    (`kiln_harness_tuner.measure`), as the outcome and the report carry it."""

    status: Literal["completed", "stopped", "capped", "failed"]
    stop_kind: str | None = None
    stop_line: str = ""
    line: str = ""
    judged: int = 0
    chunks: int = 0
    planned_chunks: int = 0
    net: float | None = None  # the stratified estimate of the change
    net_ci_low: float | None = None
    net_ci_high: float | None = None
    spend_usd: float = 0.0
    budget_usd: float | None = None  # the spending cap it ran with
    budget_line: str = ""  # where the cap came from (`budget.Budget.line`)
    # Per eval, the items judged and the items the measurement
    # set out to judge (an arm's queue is its evals' whole train split) —
    # the report's stop-reason appendix. Empty on an outcome written before.
    judged_per_eval: dict[str, int] = {}
    planned_per_eval: dict[str, int] = {}


class ResidualMiss(BaseModel):
    """An arm the `residual` stop rule stopped this run, for the planner: the run
    config it built and, per eval, the items that still fail and the guards
    it broke — read their traces (`get_train_results` with `item_ids`) and
    target them with a different build."""

    item_id: str
    rc_id: str
    summary: str
    still_failing: dict[str, list[str]] = {}
    broken_guards: dict[str, list[str]] = {}


class GateBlock(BaseModel):
    """A promotion candidate the gate's integrity check (a) BLOCKED this run:
    its prompt copies eval text. `reasons` name the copied
    passages and the eval object — check (a) only, which reads the prompt
    and the eval definitions and never val: the planner gets it. The
    val-derived flags (output shape, write actions, the integrity review)
    never go in here. The run config is stamped `— REJECTED AT
    VAL` and its record carries a `- blocked at the val gate …` line. Never
    build on it, or merge it, without removing the phrase: the build-time
    judge-copy check finds an inherited phrase and ends the arm."""

    rc_id: str
    experiment_memory_id: str | None = None
    reasons: list[str] = []


class TrialResult(BaseModel):
    """One narrowed eval job's result (`Analyzer.trial`): a run config
    driven and judged on named train items only. The
    measurement engine judges every chunk through it. One row per item: the
    primary targeted score, normalized (1.0 = best), or None when the item
    stored no row (errored drive/judge)."""

    eval_id: str
    score_key: str
    target_items: list[str]
    scores: dict[str, float | None]
    landed: bool  # every target stored a row at or above the pass line
    spend_usd: float = 0.0
    job_id: str | None = None
    # The task call's latency (ms) and provider cost (USD) per
    # target, read off the same eval runs (`task_run_usage` only) — what a
    # screening stop rule on latency or cost reads.
    latencies: dict[str, float] = {}
    costs: dict[str, float] = {}
    # The candidate's output per target, when the row carries
    # one — what the engine's invalid-output rule reads.
    outputs: dict[str, str | None] = {}
    # Each row's full cost (drive + judge), whether or not this
    # run paid for it — the price the spending cap reads (`budget`).
    item_costs: dict[str, float] = {}

    @property
    def fixed(self) -> list[str]:
        return [i for i in self.target_items if (self.scores.get(i) or 0.0) >= 0.5]

    @property
    def summary(self) -> str:
        return f"{len(self.fixed)}/{len(self.target_items)} targets fixed"


class MergeWork(BaseModel):
    wins: list[WinRef]
    conflict_flags: list[str] = []
    head_to_head_group: str | None = None  # same semantics as FixWork's
    #   (a merge arm may be head-to-head with
    #   a sibling fix arm (e.g. safe re-base vs rebalance on the same
    #   surface) — at most one of a group is ever promoted or merged. The
    #   group survives the wave in the experiment memory's content.


# "Every directional score" is spelled `None`, never `[]`. Constrained at the
# schema so a planner emitting `[]` (plausible in LLM-authored JSON) gets a
# ValidationError the dispatcher repairs IN SESSION, rather than an empty
# expansion that reaches the measurement lane — where it is a hard
# `TargetExpansionError` and, on the experiment path, kills the whole run.
# `expand_targets` keeps the same guard as defence in depth.
ScoreSubset = Annotated[list[str], Field(min_length=1)]


class EvalTarget(BaseModel):
    """Targeting is (eval, score)."""

    eval_id: str
    scores: ScoreSubset | None = None  # None = all directional scores on the
    #   eval; a list targets a subset of a multi-score eval. informational
    #   scores can't be targeted; they ride along as analyzer context regardless


class ExperimentItem(BaseModel):
    id: str  # wave-unique: "w4-2"
    type: Literal["experiment"]
    evaluation_target: list[EvalTarget]
    # evals(+scores) to run after the build, on TRAIN. The full val
    #   pass is NOT a scope choice; it's the promotion stage.
    promotion_candidate: bool
    # planner INTENT: designed to become the baseline if successful. The gate
    #   is two-stage: item.promotion_candidate AND result.successful.
    work: FixWork | MergeWork
    # This arm's spending cap on its measurement
    #   (`kiln_harness_tuner.budget`), with the finding that is worth it. None =
    #   code's default. Clamped by code to a share of the remaining run
    #   budget. An arm that reaches it ends CAPPED (inconclusive).
    max_usd: float | None = Field(default=None, gt=0)
    budget_reason: str = ""

    @model_validator(mode="after")
    def _budget_needs_a_reason(self) -> ExperimentItem:
        _budget_reason_required(self.max_usd, self.budget_reason)
        return self

    @model_validator(mode="after")
    def _merge_implies_promotion_candidate(self) -> ExperimentItem:
        if isinstance(self.work, MergeWork) and not self.promotion_candidate:
            raise ValueError("MergeWork requires promotion_candidate=True")
        return self

    @model_validator(mode="after")
    def _dedup_evaluation_target(self) -> ExperimentItem:
        """Normalizing dedup: a duplicate
        eval_id would fire duplicate concurrent measurement jobs under the
        parallel target gather — duplicate spend + duplicate bundle rows.
        Fold by eval_id, first-seen order; scores=None (all directional
        scores) subsumes any subset, otherwise union in first-seen order —
        keyed by `score_json_key`, the same normalization measurement
        expansion applies, so spelling variants of one score never yield
        two identical rows/stats calls (within one target included)."""
        merged: dict[str, EvalTarget] = {}
        for target in self.evaluation_target:
            existing = merged.get(target.eval_id)
            if existing is None:
                merged[target.eval_id] = EvalTarget(
                    eval_id=target.eval_id,
                    scores=(
                        None
                        if target.scores is None
                        else _dedup_scores([], target.scores)
                    ),
                )
            elif existing.scores is not None:
                existing.scores = (
                    None
                    if target.scores is None
                    else _dedup_scores(existing.scores, target.scores)
                )
        self.evaluation_target = list(merged.values())
        return self


class EvalOnlyItem(BaseModel):
    id: str
    type: Literal["eval_only"]
    issue_memory_id: str
    reason: str


class MeasureIssueBaselineItem(BaseModel):
    """Measure an UNMEASURED: issue's EXISTING eval against the current
    baseline. Distinct from `EvalOnlyItem`, which commissions
    a NEW eval via the eval-build agent: this one runs an eval that already
    exists, in pure code, and settles whether the baseline actually fails the
    criterion the setup phase seeded.

    `scores` mirrors `EvalTarget.scores` — None = every directional score on
    the eval; a list targets a subset. Execution builds an EvalTarget from
    (eval_id, scores), so targeting semantics are the loop's existing ones."""

    id: str  # wave-unique, e.g. "w2-3"
    type: Literal["measure_issue_baseline"]
    issue_memory_id: str
    eval_id: str
    scores: ScoreSubset | None = None  # `[]` is rejected — omit for all
    reason: str


class RemeasureItem(BaseModel):
    """Re-run existing evals against an EXISTING run config — the coverage
    top-up primitive. Pure code, no
    agent dispatch: one `ensure_measured` per eval, and Kiln's item-grained
    cache re-drives only the items that never stored a row, so a rc whose
    measurement was wiped by a provider rate-limit storm gets finished
    instead of rebuilt as a duplicate.

    `split` is pinned to train: val/test are never planner-selectable
    (val is the promotion gate's only touch)."""

    id: str  # wave-unique, e.g. "w2-4"
    type: Literal["remeasure"]
    rc_id: str  # the existing run config whose measurement to complete
    eval_ids: Annotated[list[str], Field(min_length=1)]
    split: Literal["train"] = "train"
    reason: str


class ProbeItem(BaseModel):
    """A viability probe: one yes-or-no question about a
    MECHANISM, answered by a short `claude -p` session that builds an
    ephemeral run config, drives at most `max_drives` train items through it
    and reads the traces — no judge, no measurement, no experiment record.
    "Does this model emit a tool call when a tool is attached under this
    output mode?" "Does it read a skill file when one is present?" "Does
    json_instructions parse cleanly on it?" The answer lands in memory as a
    `probe`-tagged lesson the next planner reads. A full measurement can
    be paid for only to learn that a model never calls a tool under
    json_schema. A probe is the cheap way to ask before an arm is planned, and the
    wildcard arm's gate when its mechanism is unverified."""

    id: str  # wave-unique, e.g. "w2-5"
    type: Literal["probe"]
    question: str  # phrased so "yes" / "no" is the whole answer
    method: str  # what to build ephemerally, what to drive, what to look at
    parent_rc_id: str | None = None  # the config to fork; None = the baseline
    max_drives: int = Field(default=3, ge=1, le=6)
    for_idea: str | None = None  # the candidate this probe unblocks, if any
    reason: str
    # A `check` makes this a CODE probe — no agent.
    # Code forks the parent, applies the check, drives the items and counts
    # in the traces (`kiln_harness_tuner.probes`). Leave it None for a question that
    # needs reading (did the output change in a named way), which the agent answers.
    check: ProbeCheck | None = None


ProbeCount = Literal[
    "tool_calls",  # tool calls across the assistant messages of a drive
    "skill_reads",  # tool calls whose function is `skill`
    "reasoning_chars",  # chars of reasoning the model emitted
    "output_chars",  # chars of final output
    "json_valid",  # 1 when the final output parses as JSON, else 0
]


class ProbeCheck(BaseModel):
    """A code-answered probe. Code forks the parent
    run config, attaches a throwaway surface (`attach`), applies
    `overrides` to its run_config_properties, drives `max_drives` train items
    and counts `count` in each returned trace. The answer is mechanical:
    `rule` = "any" → YES when at least one drive's count is > 0 (did the
    mechanism ever engage); "all" → YES when every drive's count is > 0 (does
    it hold on every drive); default by count. `compare_to_parent` drives
    the parent on the same items and answers YES when the count MOVED
    (mean ratio ≥ 1.5 either way, or one side zero) — "does thinking_level
    high change how much it reasons". No judge, no quality claim: a YES
    says the lever moves the behaviour, the full arm says whether it
    scores. A model, provider, thinking or sampling variant is never a
    probe: those are screened in code (`BatchPlan.screen`,
    `SCREEN_ONLY_OVERRIDES`). A probe answers a mechanism
    question (does it call the tool, read the skill, parse under this
    output mode)."""

    attach: Literal["none", "probe_tool", "probe_skill"] = "none"
    overrides: dict[str, Any] = {}  # run_config_properties keys to change
    count: ProbeCount
    rule: Literal["any", "all"] | None = None
    compare_to_parent: bool = False
    select: Literal["first", "longest"] = "first"  # which train items to drive
    # "stored" answers a DATA question from the parent's stored
    #   train rows — no fork, no drive, no session; `attach`, `overrides` and
    #   `compare_to_parent` do not apply. "drives" (default): fork and drive.
    source: Literal["drives", "stored"] = "drives"

    @model_validator(mode="after")
    def _stored_is_read_only(self) -> ProbeCheck:
        if self.source == "stored" and (
            self.attach != "none" or self.overrides or self.compare_to_parent
        ):
            raise ValueError(
                "a stored probe reads the parent's stored rows: it takes no"
                " attach, overrides or compare_to_parent"
            )
        return self

    @model_validator(mode="after")
    def _variants_are_screened(self) -> ProbeCheck:
        hit = [k for k in SCREEN_ONLY_OVERRIDES if k in self.overrides]
        if hit:
            raise ValueError(
                f"a probe may not override {hit}: a model, provider, thinking or"
                " sampling variant is screened in code (BatchPlan.screen), never"
                " probed — probes are for mechanism questions"
            )
        return self

    @property
    def effective_rule(self) -> str:
        if self.rule is not None:
            return self.rule
        return "all" if self.count in ("output_chars", "json_valid") else "any"


class ProbeAnswer(BaseModel):
    """One answered probe, as the planner sees it: what was
    asked, what the traces said, and the idea it gates. Fed on every
    planning call (`BatchPlanInput.probe_results`) — the reconnaissance
    battery's answers and every probe this run has run so far."""

    item_id: str
    question: str
    answer: Literal["yes", "no", "unclear"]
    evidence: str  # clipped
    for_idea: str | None = None


class ProbeResult(BaseModel):
    """The probe agent's answer. `unclear` is a real answer (the drives did
    not settle it); `evidence` is the trace-level fact behind it, in one
    paragraph, with counts (tool calls per drive, parse failures, …)."""

    answer: Literal["yes", "no", "unclear"]
    evidence: str
    drives: int = 0
    ephemeral_rc_ids: list[str] = []  # named `PROBE …`; never a parent
    notes: str | None = None


BatchItem = Annotated[
    ExperimentItem
    | EvalOnlyItem
    | MeasureIssueBaselineItem
    | RemeasureItem
    | ProbeItem,
    Field(discriminator="type"),
]


# Run-config properties a candidate VARIANT changes — a model, a provider, a
# thinking level, a sampling parameter. Variants are screened in code
# (`BatchPlan.screen`, `kiln_harness_tuner.viability`), never probed.
SCREEN_ONLY_OVERRIDES = (
    "model_name",
    "model_provider_name",
    "thinking_level",
    "temperature",
    "top_p",
)


class ScreenCandidate(BaseModel):
    """One candidate of a screening round: a fork of the parent
    run config with `overrides` (model, provider, thinking level, sampling
    parameters, output mode) — no prompt authoring. `rc_id` re-screens a
    run config an earlier round already made (a larger round)."""

    id: str  # "s<wave>-<n>", unique in the run
    label: str = ""
    overrides: dict[str, Any] = {}
    parent_rc_id: str | None = None  # None = the baseline
    rc_id: str | None = None
    # Why a SETTLED survivor (quality-neutral or better in two
    # or more screening rounds, `BatchPlanInput.settled_survivors`) is
    # screened again. Without one, code drops the candidate: commit it.
    rescreen_reason: str = ""
    # This candidate's spending cap (`kiln_harness_tuner.budget`), with
    # the finding that is worth it. None = code's default. Clamped by code.
    max_usd: float | None = Field(default=None, gt=0)
    budget_reason: str = ""

    @model_validator(mode="after")
    def _budget_needs_a_reason(self) -> ScreenCandidate:
        _budget_reason_required(self.max_usd, self.budget_reason)
        return self


def _budget_reason_required(max_usd: float | None, reason: str) -> None:
    if max_usd is not None and not reason.strip():
        raise ValueError(
            "max_usd needs a one-line budget_reason: what finding is worth that much"
        )


def _refuse_max_usd_rule(rules: list[Any] | None, where: str) -> None:
    if any(getattr(r, "kind", None) == "max_usd" for r in rules or []):
        raise ValueError(
            f"a spending cap is not a stop rule: set `max_usd` with a"
            f" `budget_reason` on the {where}"
        )


StopKind = Literal[
    "max_items",
    "max_usd",
    "latency_ratio",
    "cost_ratio",
    "eval_metric",
    "invalid_output_rate",
    "futility",
    "residual",
    "targets",
    "not_better",
    "guard_losses",
]

# Kinds that may carry `alpha`: then they are tests, checked at looks with
# the alpha split over the looks (`kiln_harness_tuner.stop_rules`); without it the
# timing kinds are plain kill lines.
ALPHA_KINDS = (
    "eval_metric",
    "guard_losses",
    "residual",
    "latency_ratio",
    "cost_ratio",
)


class StopRule(BaseModel):
    """One declarative STOP rule of the measurement engine
    (`kiln_harness_tuner.measure`). Code checks every rule
    for every candidate after each chunk of items; a rule that fires stops
    that candidate — it is driven and judged no further. There are only
    stop rules: nothing promotes a candidate early.

    - `max_items` / `max_usd` — budgets: judged items, or the candidate's
      provider dollars. A budget stop is not a dead end.
    - `latency_ratio` / `cost_ratio` — the candidate over the reference (the
      run's baseline) on paired drives in the same window, per-item
      `median` or `p90` (`stat`); with `alpha`, a paired sign test that the
      per-item ratio is clearly above `threshold` (the default rules carry
      one), else a kill line on the statistic. `latency_ratio` is the speed
      objective's rule and `cost_ratio` is the cost objective's rule.
      There are no per-request ceilings, so there are no absolute kinds
      such as `mean_latency` or `cost_per_request`.
    - `invalid_output_rate` — share of outputs failing the output schema.
    - `eval_metric` — quality, from the stratified estimate over the judged
      items (`kiln_harness_tuner.estimate`), on `eval_id` or pooled. `vs_baseline`:
      stop when the estimated change against the parent is below
      `threshold` (0 or negative) and clearly so (one-sided test); else
      stop when the estimated pass rate is clearly below `threshold`.
    - `futility` — the candidate cannot meet the objective's train bar even
      if every item still to judge went its way (a bound, not a test).
    - `residual` — after the discriminating block (the parent's failures
      and a few passes): at least `threshold` of the failures fixed, and
      the guard breaks not clearly above `noise_rate` (a
      one-sided exact binomial test at `alpha`, one look; without them the
      run's noise rate and 0.05). Under speed and cost: no more broken
      than fixed.
    - `targets` — every item the planner named in `target_items` passes.
    - `not_better` — under speed and cost, when the timing pairs are
      complete: the candidate must be clearly better on the objective's
      metric than the baseline, and not clearly slower than its parent.
    - `guard_losses` (the default quality rule under speed and
      cost) — losses on the judged items the parent passes: stop when the
      loss rate is clearly above `threshold`, the noise flip rate (a
      one-sided exact binomial test, `alpha` required). Fixes of the
      parent's failures are not counted.

    Every threshold kind stops when its value is ABOVE `threshold`, except
    `eval_metric` and `residual`. No rule fires before `min_n`
    observations."""

    model_config = ConfigDict(extra="forbid")

    kind: StopKind
    threshold: float = 0.0
    min_n: int = Field(default=1, ge=1)
    stat: Literal["median", "p90"] = "median"
    eval_id: str | None = None
    vs_baseline: bool = False
    alpha: float | None = Field(default=None, gt=0, lt=0.5)
    # `residual` only: the noise flip rate the guard breaks
    #   are tested against. None = the run's (`stop_rules.merge_rules`).
    noise_rate: float | None = Field(default=None, ge=0, lt=1)

    @model_validator(mode="after")
    def _well_formed(self) -> StopRule:
        if self.kind == "eval_metric":
            if self.alpha is None:
                raise ValueError(
                    "an eval_metric stop rule needs `alpha`, its significance"
                    " level — code splits it over the looks"
                )
            if self.vs_baseline and self.threshold > 0:
                raise ValueError(
                    "an eval_metric vs_baseline threshold is the change against"
                    " the baseline below which the candidate stops: 0 or negative"
                )
            if not self.vs_baseline and not 0 < self.threshold <= 1:
                raise ValueError("an eval_metric threshold is a pass rate in (0, 1]")
        elif self.kind == "guard_losses":
            if self.alpha is None:
                raise ValueError(
                    "a guard_losses stop rule needs `alpha`, its significance"
                    " level — code splits it over the looks"
                )
            if not 0 <= self.threshold < 1:
                raise ValueError(
                    "a guard_losses threshold is the noise flip rate in [0, 1)"
                )
            if self.vs_baseline or self.eval_id:
                raise ValueError(
                    "a guard_losses rule is pooled over the screened evals: no"
                    " `eval_id`, no `vs_baseline`"
                )
        else:
            if self.vs_baseline or self.eval_id:
                raise ValueError(
                    f"`vs_baseline` and `eval_id` belong to eval_metric rules"
                    f" only, not {self.kind}"
                )
            if self.alpha is not None and self.kind not in ALPHA_KINDS:
                raise ValueError(f"a {self.kind} rule takes no `alpha`")
        if self.kind == "max_items" and self.threshold < 1:
            raise ValueError("a max_items budget is at least 1 item")
        if self.kind == "max_usd" and self.threshold <= 0:
            raise ValueError("a max_usd budget is positive")
        if self.kind == "invalid_output_rate" and not 0 <= self.threshold < 1:
            raise ValueError("an invalid_output_rate threshold is in [0, 1)")
        if self.kind in ("latency_ratio", "cost_ratio") and self.threshold <= 0:
            raise ValueError("a ratio threshold is positive (1.0 = the baseline)")
        if self.kind == "residual" and not 0 < self.threshold <= 1:
            raise ValueError("a residual threshold is the share of failures to fix")
        if self.noise_rate is not None and self.kind != "residual":
            raise ValueError(
                f"`noise_rate` belongs to residual rules only, not {self.kind}"
            )
        return self


class ScreenRequest(BaseModel):
    """A screening round the planner asks for: N candidates through the
    measurement engine on the SAME train items (`kiln_harness_tuner.measure`).
    `round` sets the size (per eval, `viability.SCREEN_ROUND1_PER_EVAL` ×
    2^(round−1) items of the queue — successive halving: re-screen the best
    few on a larger round); `target_evals` restricts the round to those
    evals (the candidate is then screened on those evals ONLY); `mode`
    "hard_cases" keeps only the discriminating block (the baseline's
    failures and a few passes); `per_eval_items` overrides the size."""

    candidates: list[ScreenCandidate] = Field(min_length=1)
    round: int = Field(default=1, ge=1, le=5)
    target_evals: list[str] | None = None
    per_eval_items: int | None = Field(default=None, ge=1, le=64)
    mode: Literal["stratified", "hard_cases"] = "stratified"
    # Stop rules: code's defaults for the objective, merged with these (a
    # rule of the same kind and eval replaces the default; any other is
    # added). `looks` = the chunks the round's items are split into — the
    # rules are checked after each, and a test's alpha is split over them.
    stop_rules: list[StopRule] | None = None
    looks: int = Field(default=3, ge=1, le=6)
    reason: str = ""

    @model_validator(mode="after")
    def _budget_is_per_candidate(self) -> ScreenRequest:
        _refuse_max_usd_rule(self.stop_rules, "screening candidate")
        return self


class ScreenResult(BaseModel):
    """One candidate's screening result, as the planner reads it
    (`kiln_harness_tuner.measure`). Speed and cost are paired with the baseline on
    the queue's items in the same window; quality is the real judges on the
    round's items against the baseline's stored rows on the SAME items;
    `net` is the stratified estimate of the change in pass rate over the
    screened evals' whole train split, with its 95% interval. `evals` names
    what it was screened on — never read it as screened on any other eval.
    `rank` orders this run's survivors: speed band first under speed or
    cost, then the lower loss bound, then the higher net; under quality the
    higher net."""

    candidate_id: str
    label: str
    rc_id: str | None
    round: int
    evals: list[str]
    items: int
    latency: LatencyComparison | None = None
    cost: LatencyComparison | None = None
    valid: int = 0
    outputs: int = 0
    per_eval: dict[str, dict[str, int]] = {}  # eval -> n, candidate, baseline,
    #   fixed, broke
    passed: int = 0
    baseline_passed: int = 0
    fixed: int = 0
    broke: int = 0
    hard_n: int = 0
    hard_lost: int = 0
    hard_fixed: int = 0
    # "capped": the candidate reached its spending cap —
    #   inconclusive, not a dead end; its rows are kept.
    verdict: Literal["dead_end", "survivor", "error", "capped"]
    reason: str = ""
    line: str = ""
    spend_usd: float = 0.0
    # The stop rule that ended the candidate early, and the numbers
    # ("stopped … at n=8: Strict Count 3/8 vs baseline 8/8, …"); "" = ran
    # to the end of the round.
    stop: str = ""
    # That rule's kind (`StopKind`, or `lever_not_applied`);
    #   None = no rule stopped it. The report's trade-off appendix.
    stop_kind: str | None = None
    # The stratified net change and its 95% interval, and the
    # survivor's rank among this run's survivors (1 = best).
    net: float | None = None
    net_ci_low: float | None = None
    net_ci_high: float | None = None
    rank: int | None = None
    # The spending cap it ran with, its measured cost per judged
    # item (drive + judge, median), and what a full measurement of its
    # screened evals' train split would cost at that price, timing included.
    budget_usd: float | None = None
    cost_per_item_usd: float | None = None
    expected_full_usd: float | None = None
    # Under speed and cost only (None under quality): the upper
    # 95% bound on the loss rate over the judged baseline passes
    # (`baseline_passed` judged, `broke` of them lost) — "no loss rate above
    # X%". `fixed` counts fixes of baseline failures: noise-level evidence,
    # never set against `broke`. `guards_required`: the baseline passes a
    # model or provider swap must be judged on before it is a survivor.
    loss_bound: float | None = None
    guards_required: int | None = None
    # Where the result comes from. `round_no` is the run's own
    # round counter (`round` is the size tier); `run_id` and `date` name the
    # run. `prior_run`: loaded from memory at run start — measured in
    # another run, against the baseline of that run, in another window.
    overrides: dict[str, Any] = {}
    parent_rc_id: str | None = None
    round_no: int = 0
    run_id: str = ""
    date: str = ""
    baseline_rc_id: str | None = None
    prior_run: bool = False


class SettledSurvivor(BaseModel):
    """A screening survivor that was quality-neutral or better in two or
    more screening rounds (this run's and earlier runs'). Commit it as an
    arm (`work.existing_rc_id`); code drops a re-screen of it that gives no
    `rescreen_reason`."""

    rc_id: str | None  # the newest run config screened for it
    rc_ids: list[str] = []  # every run config screened for it
    label: str
    overrides: dict[str, Any] = {}
    parent_rc_id: str | None = None
    rounds: int  # distinct (run, round) pairs it survived quality-neutral
    last_run: str = ""
    last_date: str = ""


class BatchPlan(BaseModel):
    items: list[BatchItem]
    rejected_summary: str  # brief prose; carries repeat-deferral counts forward
    #   — fed back next wave as prior_rejected_summary
    open_questions: list[OpenUnknown]
    # True = this plan is the wave's PROBE PHASE —
    # every item is a probe; the loop runs them now (minutes), then calls the
    # planner again with their answers to plan the wave. An arm that rests on
    # an unverified mechanism is planned AFTER the probe settles it, never
    # beside it: an arm planned beside its probe cannot act on the answer
    # until the next wave.
    probe_phase: bool = False
    # A screening round instead of a wave — code measures every
    # candidate variant on the screening subset and calls the planner again
    # with the table (`screening_results`). A screening plan carries no items.
    screen: ScreenRequest | None = None
    loop_exit: str | None = None  # exit signal: terminate the run NOW instead
    #   of running a wave — mutually exclusive with items (validator below;
    #   planners can conflate it with "the run should end after my wave" and
    #   drop whole composed waves)

    @model_validator(mode="after")
    def _loop_exit_means_nothing_to_do_now(self) -> BatchPlan:
        """The exit channel and a batch are mutually exclusive:
        the orchestrator honors loop_exit by terminating the run INSTEAD of
        running the wave, so a plan carrying both would silently drop its
        items, and the composed wave design would be discarded. The
        ValidationError feeds the dispatcher's one-shot in-session repair
        retry, so a confused planner self-corrects."""
        if self.screen is not None:
            if self.items or self.loop_exit is not None or self.probe_phase:
                raise ValueError(
                    "a screening plan (screen set) carries no items, no loop_exit"
                    " and no probe phase — code runs the round and asks again"
                )
            return self
        if self.probe_phase:
            if not self.items or not all(isinstance(i, ProbeItem) for i in self.items):
                raise ValueError(
                    "a probe-phase plan (probe_phase=true) carries ONLY probe"
                    " items, at least one: the loop runs them first and asks you"
                    " again, with their answers, for the wave's arms. Put the"
                    " arms in that second plan, not in this one"
                )
            if self.loop_exit is not None:
                raise ValueError("a probe-phase plan cannot also set loop_exit")
        if self.loop_exit is not None and self.items:
            raise ValueError(
                "a BatchPlan cannot carry both items and loop_exit: loop_exit"
                " means nothing-to-do-NOW — the orchestrator terminates the"
                " run instead of executing this wave, dropping your items"
                " unexecuted. If you have items, return them (drop loop_exit)"
                " and let a future round exit (when should_wrap_up arrives, or"
                " no work is left); sequencing recommendations for future waves"
                " belong in prose (rejected_summary / open_questions), never"
                " in the exit channel"
            )
        return self

    @model_validator(mode="after")
    def _head_to_head_groups_two_sided(self) -> BatchPlan:
        """Merge exclusivity must be expressible AND well-formed: a
        head_to_head_group declares mutually exclusive arms compared in
        parallel THIS wave, so a group named by a single item is meaningless.
        Both work types carry the field; the deeper rule (never merge
        two rcs from the same group) is enforced by builders reading the
        groups off experiment memories, not here — wins carry no group."""
        counts: dict[str, int] = {}
        for item in self.items:
            if isinstance(item, ExperimentItem) and item.work.head_to_head_group:
                group = item.work.head_to_head_group
                counts[group] = counts.get(group, 0) + 1
        lonely = sorted(group for group, n in counts.items() if n < 2)
        if lonely:
            raise ValueError(
                f"head_to_head_group(s) {lonely} appear on only one item — a"
                " group declares mutually exclusive arms and needs at least"
                " two items in this plan"
            )
        return self


# ---- agent result envelope (every dispatch returns one) ----


class AgentResult(BaseModel, Generic[T]):
    status: Literal["ok", "failed_expected", "failed_infra"]
    data: T | None = None  # the agent's typed output (per-method)
    error: ErrorInfo | None = None  # infra failures only (dispatcher-wrapped)
    detail: str | None = None  # the agent's own envelope prose — notably the
    #   *why* on failed_expected with data=None, which would otherwise be
    #   information-free.
    usage: Usage = Field(default_factory=Usage)  # cost budget later
    attempts: int = 1  # dispatches the retry ladder spent on this result
    #   (`call_with_retries` sets it; a bare dispatcher call is 1). Sits beside
    #   `usage`, which likewise covers EVERY attempt — both answer "what did
    #   this result cost". Callers must never re-derive it from `status`: the
    #   ladder has two terminal cases that skip the retry (`failed_expected`
    #   and `parse_failure`), and re-deriving them would mis-report the attempt
    #   count in setup's fatal message. One owner.


# ---- structured agent outputs ----


class EvalBuildReport(BaseModel):
    """The eval-build boundary. Splits live ON the eval — the id
    is the handle; the loop addresses them by split name, never by tag."""

    eval_id: str
    eval_config_id: str
    eval_type: str
    case_count: int
    reproduced: bool
    not_reproduced_reason: str | None = None
    baseline_headline: str
    priority: str  # p0–p3, set at creation


class RcChange(BaseModel):
    """What an arm changed against its parent, for HUMANS.
    Provenance on the run config is the audit trail; this is the one-screen
    explanation the live UI, the experiment memory and the report all show.
    Written by the build agent per the kiln-harness-tuner-experiment-build
    skill; every field is plain text a reader can act on."""

    summary: str  # one sentence: what is different about this run config
    changes: list[str]  # concrete differences, one per line ("prompt v3 →
    #   v4: added …", "tool X replaced by wrapper Y (adds a read-first
    #   check on PATCH)", "skill Z attached")
    unchanged: list[str] = []  # what deliberately stayed as the parent had
    #   it (model, params, other tools …) — the reader's scope check
    consistency_check: str | None = None  # the declared-text check: what
    #   was compared against what and the result (a tool's parameter
    #   description vs its code; a skill's route table vs the target items)
    smoke_items: list[str] = []  # the hypothesis's named target items the
    #   build was smoked on, when any were


class RcBuildResult(BaseModel):  # create_experiment_rc
    rc_id: str
    build_notes: str
    change: RcChange | None = None  # optional in the schema so an older
    #   agent template still parses; the skill requires it


class EvalDescription(BaseModel):
    """describe_eval's output — one eval's criterion, in
    prose a planner can act on without re-reading the rubric."""

    overview: str  # ≤240; code adds the "UNMEASURED: issue: " prefix
    description: str  # code clamps it to the SETUP_HEAD_MAX_CHARS budget


class SetupReport(BaseModel):
    """What the pre-flight setup phase did — the counts plus
    the lists that need a human: issues setup refused to guess a binding for,
    issues it flagged unmeasurable, and how the baseline was picked.

    Ids, not the event's bare counts: the report lists seeded issues by
    eval and names what needs attention, and a count is `len()` away. There
    is deliberately no `describe_failures` field — a description failure is
    fatal, so no run reaches the report carrying one."""

    evals_total: int = 0
    evals_active: int = 0
    seeded_eval_ids: list[str] = []
    refreshed_eval_ids: list[str] = []
    repaired_issue_ids: list[str] = []
    unmeasurable_issue_ids: list[str] = []
    unbound_unresolved_issue_ids: list[str] = []
    baseline_rule: str | None = None  # None = a record already existed
    baseline_rc_id: str | None = None


class UnloadableEval(BaseModel):
    """An active eval whose default config Kiln can't load, with Kiln's
    reason: the run leaves it out (`eval_unloadable` events)."""

    eval_id: str
    name: str
    reason: str


class DroppedEvalItem(BaseModel):
    """A planned item that would build a new eval, which code dropped
    before the wave ran because eval building is off
    (`eval_item_dropped` events). No eval measures its issue."""

    item_id: str
    wave: int
    kind: str  # `item_kind`: "eval" (an eval_only item) or "fix"
    issue_memory_id: str


class IssueWithoutEval(BaseModel):
    """An open issue that no eval binds, read from memory when the report
    is written — the issues setup found and the ones the run created."""

    issue_memory_id: str
    overview: str  # the record's one-line overview, prefix included


Objective = Literal["quality", "speed", "cost"]
# `--objective`: what the loop optimises. quality = the eval scores (the
# behaviour before the other objectives); speed = per-request wall-clock latency, and
# cost = per-request provider cost of the task call, each with quality equal
# or better (`analysis.speed_quality_reasons`). Speed and cost share one
# machinery; the objective only picks which paired metric decides
# (`kiln_harness_tuner.objective`).


class LatencyComparison(BaseModel):
    """Per-item wall-clock latency of two arms on the SAME items
    (`kiln_harness_tuner.latency`). Assembled by code; judgment-free
    except for the two named verdicts, whose rules are fixed in code.

    `method`: `paired_drive` = code drove both arms on the same items in the
    same window (the fair comparison, used by the speed objective);
    `eval_runs` = latencies read off stored eval runs, which may have been
    driven at different times — information, never a speed verdict."""

    method: Literal["paired_drive", "eval_runs"]
    split: str
    n_items: int
    # What is compared. `latency_ms` = the task call's wall-clock
    # time; `cost_usd` = its provider cost per request (from the same runs'
    # own usage). The `*_ms` field names are kept for both; `faster_items`,
    # `clearly_faster` read "better on this metric" (cheaper, for cost).
    metric: Literal["latency_ms", "cost_usd"] = "latency_ms"
    baseline_median_ms: float | None = None
    candidate_median_ms: float | None = None
    baseline_p90_ms: float | None = None
    candidate_p90_ms: float | None = None
    baseline_iqr_ms: float | None = None  # the spread: p75 − p25
    candidate_iqr_ms: float | None = None
    median_ratio: float | None = None  # median over items of cand / base;
    #   below 1 is faster
    faster_items: int = 0
    slower_items: int = 0
    sign_test_p: float | None = None  # exact two-sided sign test
    p90_ratio: float | None = None  # candidate p90 / baseline p90
    p90_ratio_ci_low: float | None = None  # 95% paired bootstrap
    p90_ratio_ci_high: float | None = None
    clearly_faster: bool = False  # sign test significant, more items faster,
    #   median ratio < 1, no significant p90 regression, and not incomplete
    p90_regressed: bool = False  # the p90 ratio's interval lies above 1
    note: str = ""
    # How many items the pass set out to pair, why each missing one is
    # missing (item id -> reason), and `incomplete` when the paired count is
    # under `latency.paired_floor(planned)` — then never a verdict.
    planned_items: int | None = None
    dropped_items: dict[str, str] = {}
    incomplete: bool = False


class PerEvalResult(BaseModel):
    """Assembled by analysis.py (code) — one row per expanded (eval, score)
    pair."""

    eval_id: str
    score_key: str
    split: str  # "train"|"val" — split name, never the storage tag
    baseline_rc_id: str
    compared_to: Literal["parent", "prior_baseline"]
    # parent = paired vs the parent rc (attribution); prior_baseline =
    #   screened vs the latest measured baseline in the promotion lineage
    #   — a spend gate, never read as "the change hurt"
    n_items: int
    delta: float  # candidate − baseline, sign-normalized to good-direction
    #   units (per-score direction flag) — win is always delta > 0
    p_value: float | None  # mcnemar_paired: "p_exact"; compare_paired:
    #   "wilcoxon_p" (None below five non-zero paired differences — the
    #   server then judges significance from the bootstrap CI, and so does
    #   `significant` here). Verified against the server source.
    significant: bool
    # The confidence interval on the delta, in the SAME good-direction units
    #   as `delta` (sign-normalized with it): mcnemar_paired's Newcombe paired
    #   CI on the pass-rate difference, compare_paired's paired-bootstrap CI
    #   on the mean difference. This is the precision signal the skills used
    #   to read off `stderr`, which neither operation ever returned.
    ci_low: float | None = None
    ci_high: float | None = None
    # The largest delta this row can show, in the same good-direction units:
    #   for a binary score, 1 − baseline pass rate (or the pass rate itself on
    #   a lower_is_better score); None where the scale has no known ceiling.
    #   A +15pp prediction on an 88.3% baseline
    #   was "impossible by construction" and still used as the bar. The
    #   analyzer judges magnitude against min(predicted, headroom).
    headroom: float | None = None
    stderr: float | None = None  # DEPRECATED — always None: no statistics
    #   operation returns a standard error. Read ci_low/ci_high.
    fail_to_pass: int | None
    pass_to_fail: int | None
    # paired flip counts — binary (pass/fail) scores only; None for rating scales
    spend_usd: float  # Kiln task_run_usage + eval_usage over the rows feeding
    #   this pair that THIS RUN paid for (EvalRuns created after run start —
    #   a stored row must not be re-booked on every
    #   comparison, so a cached baseline read would show a large cost)
    evidence_spend_usd: float = 0.0  # the same sum over ALL rows read, cached
    #   ones included — what the numbers rest on, not what this run spent
    #   this pair, both arms — never a local token-price table. Agent
    #   session cost is NOT in here: these rows sum to the bundle's
    #   `spend_total_usd`, which the analysis skill freezes as `bundle cost`
    #   and defines as excluding agent sessions
    # Coverage metadata (additive): populated when a
    # measured side carried JobHealth; None = no job health known (e.g. both
    # sides were free cache reads). Sub-~95% coverage ⇒ graded caution — the
    # analyzer/promote skills name the skew (holes doctrine).
    n_expected: int | None = None  # job progress totals for the pair
    coverage: float | None = None  # n_items / n_expected
    missing_item_ids: list[str] | None = None  # dataset_ids from the job-
    #   errors fetch — the dark items, named per row
    # Position on the scale, so a row says where each side STANDS and not only
    # how far it moved. Unlike `delta` these are the RAW stored scores — never
    # sign-normalized. Normalizing them would invent numbers matching no stored
    # score; the cost is that a lower_is_better row shows falling absolutes
    # beside a `+` delta, which the overview format marks with `↓`.
    baseline_score: float | None = None  # binary: pass rate; rating: mean
    candidate_score: float | None = None
    # The job behind each side's measurement, so a low-coverage row can be
    # explained: `GET /api/jobs/{job_id}/errors`. Both sides, because coverage
    # and missing_item_ids are pair-level — a candidate-side id cannot explain
    # a baseline-side hole. None when that side was a free cache read.
    baseline_job_id: str | None = None
    candidate_job_id: str | None = None
    # The score's type and direction, so the speed objective's
    # quality rule can turn every row — binary or not — into net items
    # (`analysis.row_net_items`). None on rows built before the field existed;
    # such a row cannot be pooled and makes a speed candidate ineligible.
    score_type: str | None = None
    direction: str | None = None
    # Per-item wall-clock latency of the two sides of THIS eval's
    # comparison, read off the same eval runs (`method: eval_runs`). Every
    # row of one eval carries the same comparison, like spend. Information:
    # the stored sides may have been driven at different times.
    latency: LatencyComparison | None = None
    # The per-request provider cost of the task call on the same
    # two sides, read off the same eval runs (`task_run_usage.cost`, metric
    # `cost_usd`, `method: eval_runs`). Information on every row, train and
    # val, under every objective: an arm's cost ratio stays visible without
    # timing drives. Never a cost verdict.
    cost: LatencyComparison | None = None


class AnalysisBundle(BaseModel):
    per_eval: list[PerEvalResult]
    # NO verdict labels anywhere: delta/n/p/CI per row ARE the
    #   direction + significance signal; code computes honest numbers
    #   (sign-normalized); agents interpret them
    spend_total_usd: float  # this run's provider spend behind the rows
    evidence_spend_usd: float = 0.0  # all stored rows read, cached included
    errors: list[ErrorInfo]  # measurement holes (pairs still unmeasured after
    #   the retry ladder) + `partial:` entries naming a partially-
    #   measured side's dark items with the fetched job-error substance
    #   — the bundle ships anyway; the analyzer
    #   weighs the holes and the coverage in `successful`
    # The speed objective: the paired drive pass on a fixed train
    # sample — candidate vs its parent, same items, same window. None when
    # the objective is quality (the rows still carry their passive latency).
    latency: LatencyComparison | None = None
    cost: LatencyComparison | None = None  # The same paired pass,
    #   per-request cost of the task call (metric cost_usd)


class IngredientOutcome(BaseModel):  # attribution rows
    issue_memory_id: str
    landed: bool  # did this issue's fix survive — the merge, or a fix
    #   item whose experiment won without closing its own issue
    note: str


class ExperimentResult(BaseModel):  # analyze_experiment_results (train-side)
    successful: bool  # met its own targeted goals on the train split — with
    #   item.promotion_candidate, gates the val promotion stage.
    #   May be True on a low-significance positive; the val gate is the filter
    findings: str
    # Why `successful` is False when the code rule would have passed it.
    #   Required on every veto (the analysis skill says so); code reads it:
    #   `prediction_miss` — the primary target improved but by less than the
    #   item predicted — is caution, never a verdict, and code restores
    #   `successful` (a real +3.3pp on an 88.3%
    #   baseline can be vetoed against a +15pp prediction that the 11.7pp of
    #   headroom makes impossible). Every other reason stands.
    veto_reason: (
        Literal[
            "prediction_miss",
            "dominated_by_ingredient",
            "measurement_holes",
            "secondary_regression",
            "other",
        ]
        | None
    ) = None
    memory_overview: str  # ≤280, outcome front-loaded as NUMBERS, no labels.
    #   The row format is a string contract with exactly one owner — the
    # "Frozen format" block in the `kiln-harness-tuner-experiment-analysis`
    # skill (`agent_files/skills/`).
    #   Deliberately NOT restated here: a second copy would drift from
    #   the owner.
    memory_content: str
    per_ingredient_outcomes: list[IngredientOutcome] | None = None
    # Promotion's FIXED: writes source from these rows via _landed_issue_ids.
    #   Required for MergeWork (no rows → nothing closes). OPTIONAL for
    #   FixWork, where the item's own issue closes by default — a row naming
    #   that issue with landed=False suppresses the closure (a false
    #   FIXED: hides a known cure from planners, who skip FIXED: records).
    #   Rows naming any OTHER issue on a FixWork item stay informative-but-
    #   ignored (E4)


class RegressedIssue(BaseModel):
    """An issue whose eval repros on the promoted baseline's val pass;
    code composes the standard line either way (metric-free)."""

    issue_memory_id: str
    note: str | None = None


class PromotionDecision(BaseModel):
    """promote_best — shape limits writes to the baseline + `val` records and
    metric-free issue regression flags."""

    winner_rc_id: str | None
    winner_experiment_memory_id: str | None
    rationale: str  # rationale + val metrics summary → the `val`-tagged
    #   stats record, the loop's sole val-metrics memory (the `baseline`
    #   record is metric-free). No
    #   loser notes, no issue feedback
    regressed_issues: list[RegressedIssue] = []
    # code performs the writes: closed issue → reopened; open issue →
    #   repro line only. One bit + provenance per issue, never numbers
    blocked_on: str | None = None  # the OPEN constraint memory
    #   id the gate escalated on, with winner_rc_id null. Code then stamps the
    #   candidates `— HELD (constraint <id>)` instead of `— REJECTED AT VAL`:
    #   they were not judged on their numbers, and the planner may resubmit
    #   them unchanged once the constraint is answered.


class ItemOutcome(BaseModel):
    """Gather output, drives the UI + wave log."""

    item: BatchItem
    # A completed eval_only item has no result (result=None), and
    #   execute_plan_item returns ItemOutcome for eval_only items — so the
    #   embedded item is the union. The promotion gate must type-guard before
    #   reading promotion_candidate. `measure_issue_baseline` is in the union
    #   the same way, and likewise ends `completed` with result=None.
    status: Literal[
        "completed",
        "skipped_not_reproduced",
        "failed_infra",
        "dead_end",
        "abandoned",
        "not_landed",  # the build's trial on its target items
        #   left some failing — no train run was paid for; the planner may
        #   try a different build for the same targets
        "capped",  # the measurement reached the arm's spending
        #   cap — inconclusive, not a dead end or a failure; its rows are
        #   kept, and a later arm on the rc with a larger cap goes on
    ]
    # edges pinned: a successful eval_only item ends "completed" with
    #   result=None; a tripwire abort never rewrites "failed_infra" — only the
    #   experiment memory gets "— ABANDONED"
    # "dead_end": the build agent returned failed_expected — a PROVEN
    #   product dead-end (e.g. an inert knob), not retryable infra. Filing
    #   these as failed_infra would invite a future wave to retry a
    #   disproven lever.
    rc_id: str | None
    experiment_memory_id: str | None
    result: ExperimentResult | None
    infra_error: ErrorInfo | None
    # Not set now (the engine replaced the trial); kept so an older wave log
    #   still reads.
    trial: TrialResult | None = None
    residual: ResidualGateResult | None = None  # the discriminating block,
    #   when the `residual` rule stopped the arm
    measurement: MeasurementSummary | None = None  # the engine's
    speed: LatencyComparison | None = None  # the arm's train
    #   timing — set when a timing rule of the engine stopped it
    speed_verdict: str | None = None  # the engine's stop line
    probe: ProbeResult | None = None  # a probe item's answer
    # `kiln_harness_tuner.reference_check`: what the measured run config
    #   names and does not attach — a skill, or a tool the parent attached.
    #   Empty when the check found nothing or did not run.
    reference_findings: list[str] = []


class IntegrityReviewInput(BaseModel):
    """The integrity reviewer's input: the candidate's changed
    instructions, the baseline's, and the evals' own criteria (judge
    prompts, eval config steps, spec text, eval descriptions). Never item
    data: no input, output, trace or score of any item."""

    candidate_rc_id: str
    candidate_instructions: str
    baseline_instructions: str
    judge_texts: dict[str, str]  # label ("the judge of eval X") -> text


class IntegrityReviewResult(BaseModel):
    """What the reviewer found: each flag one plain sentence naming the
    instruction and the criterion it paraphrases or games. No flags = none
    found."""

    flags: list[str] = []
    notes: str = ""


class GeneralityReviewInput(BaseModel):
    """The generality reviewer's input (`kiln_harness_tuner.generality`): a built
    run config's instructions beside its parent's, before the train
    measurement. `train_inputs` are TRAIN items only, read by code — the
    target items first — so the reviewer can see when the new text copies
    an item; `train_expected` are those items' expected outputs
    (agents can read train labels, so a build can copy one).
    Never a val or test item, a score or a trace."""

    item_id: str
    candidate_rc_id: str
    candidate_instructions: str
    parent_instructions: str
    task_instructions: str = ""  # the task's own instruction + requirements
    hypothesis: str | None = None  # what the item set out to fix
    train_inputs: list[str] = []  # "<item id>: <input>", clipped
    target_items: list[str] = []  # the ids among `train_inputs` the build
    #   targets — they come first
    # What the build added to the attached skills' text, one
    #   block per changed or new skill file — a header line, then the added
    #   lines (a diff against the skill version it replaces, or the whole
    #   file when it is new). Empty when the skills did not change.
    skill_changes: list[str] = []
    # The same for the attached code tools — their code and the
    #   description the model is shown.
    code_tool_changes: list[str] = []
    # "<item id>: <expected output>" for the items in
    #   `train_inputs` that have one, clipped.
    train_expected: list[str] = []


class GeneralityReviewResult(BaseModel):
    """`leaks`: one sentence each, quoting the added text that memorises the
    train set or games the measurement and saying why. Empty = none."""

    leaks: list[str] = []
    notes: str = ""


class ReportMarkdown(BaseModel):
    """report_writer output: the agent returns the round report as markdown;
    CODE writes `reports/kiln-harness-tuner-<run_id>.md` — the
    format-critical-write pattern. `ReportPaths` below is report.py's product
    (the path code computed and wrote), not an agent output."""

    markdown: str


class ReportPaths(BaseModel):
    report_path: str  # reports/kiln-harness-tuner-<run_id>.md (run-id scheme)


# ---- dispatch inputs — code-assembled; these ARE the structural
# ----   isolation: an agent's whole passed context is its input model + the
# ----   dispatcher-injected target block. Memory it reads itself.


class BaselineRef(BaseModel):
    """select_baseline's output — all the loop ever passes onward from the
    baseline record. Just the id: the record carries no val metrics to leak,
    and the rc id is read from its `rc_<id>` tag rather than cut out of
    prose."""

    rc_id: str


class LeverCost(BaseModel):
    """What one lever class cost to BUILD this run — measured, not quoted.
    Keyed by technique; `n` arms, means over them."""

    n: int
    mean_build_usd: float
    mean_build_secs: float


class EvalPrice(BaseModel):
    """One active eval's measured prices (`budget`)."""

    eval_id: str
    name: str = ""
    train_items: int = 0
    judge_usd_per_item: float | None = None  # median eval_usage cost
    # train_items × (drive + judge): one full measurement of this eval
    full_measurement_usd: float | None = None


class TrainDetectability(BaseModel):
    """What one (eval, score) can show on TRAIN, from the baseline's stored
    train rows (`kiln_harness_tuner.detectability`). Train-only by
    construction: the planner receives these rows, so no field may hold a
    val number. The val side lives on `DetectabilityRow`, which only the
    terminal, the event log and the report read."""

    eval_id: str
    eval_name: str = ""
    score_key: str
    score_type: str
    direction: str
    n: int  # baseline train items with a value on this score
    room: int  # of those, items not at the best value (the most any arm can fix)
    unscored: int | None = None  # train split items with no stored row; None: unknown
    floor: int  # the significance floor: the fewest wins, all one way, that
    #   can reach p < alpha (computed from `SIGNIFICANCE_ALPHA`)
    fewest_wins: int | None = None  # the floor when `room` reaches it; None:
    #   no outcome on these rows can be a significant win
    best_case_change: float | None = None  # floor / n: the smallest change,
    #   as a share of the items, that can be significant (a best case); None
    #   when n is below the floor


class DetectabilityRow(BaseModel):
    """One (eval, score) of the detectability check, train and val. NEVER
    planner input (`TrainDetectability` is): the val fields are val-grade.
    `val_room_estimate` is `val_n` × the train share with room — an
    estimate, because setup reads no val result. The two `interval` fields
    are the server interval's route (`detectability` module doc): reported,
    never counted."""

    train: TrainDetectability
    train_interval_wins: int | None = None
    val_n: int | None = None  # val split size on disk; None: unknown
    val_room_estimate: int | None = None
    val_best_case_change: float | None = None  # floor / val_n; None: val_n
    #   is below the floor (certain) or unknown
    val_interval_best_case: int | None = None
    promotion_driving: bool = False  # directional, comparable, on an active eval


class DetectabilityReport(BaseModel):
    """The pre-run detectability check's result: printed at
    setup, logged, and appended to the report with its val numbers
    labelled val-grade. Never planner input and never in memory."""

    baseline_rc_id: str
    objective: str = "quality"
    alpha: float
    floor_binary: int
    floor_rating: int
    min_effect: float | None = None
    rows: list[DetectabilityRow] = []
    skipped: list[str] = []  # scores that cannot be compared, with why
    warnings: list[str] = []
    refusal: str | None = None  # the refusal text, when the check refused
    overridden: bool = False  # refused, and --allow-underpowered went on


class NoiseRow(BaseModel):
    """Run-to-run noise on one (eval, score), measured on a sample of TRAIN
    items (`kiln_harness_tuner.noise`): the baseline re-driven and
    re-judged (a clone run config, so Kiln's item cache does not answer),
    and the stored outputs re-judged alone (a copy of the eval config),
    each compared with the baseline's stored rows on the same items.
    Train-only: the planner receives these rows."""

    eval_id: str
    eval_name: str = ""
    score_key: str
    score_type: str
    baseline_rc_id: str
    items: int  # sample items compared by the re-drive
    redrive_changed: int = 0  # items whose outcome (binary) or value changed
    redrive_rate: float | None = None
    redrive_mean_abs_change: float | None = None  # rating scales, raw units
    rejudge_items: int = 0
    rejudge_changed: int | None = None  # None: judge noise not measured
    rejudge_rate: float | None = None
    rejudge_mean_abs_change: float | None = None
    rejudge_note: str = ""  # why judge noise was not measured, when it was not
    noise_adjusted_wins: int | None = None  # the fewest real wins that show
    #   through the noise on the whole train split (an approximation)
    train_room: int | None = None


class NoiseCheckResult(BaseModel):
    """`--noise-check N`: what one run cannot see. Train-only."""

    baseline_rc_id: str
    items_per_eval: int
    clone_rc_id: str | None = None
    estimate_usd: float = 0.0
    spend_usd: float = 0.0
    rows: list[NoiseRow] = []
    warnings: list[str] = []
    skipped: str | None = None  # the whole check did not run, and why


class ModelCheck(BaseModel):
    """One (provider, model) the model preflight checked
    `kiln_harness_tuner.model_preflight`). `outcome`:

    - `ok` — the test call answered;
    - `flagged` — a hard failure that names the model or the provider (bad
      request, not found, no deployment, quota, auth, context too long), or
      a context window smaller than the baseline's prompt and tools. The
      model is left out of screening and measurement this run;
    - `untested` — the check could not decide: a failure still transient
      after the one retry, output the loop could not parse (the provider
      answered), or a failure that names neither. Nothing is excluded.

    `reason` says why, for the last two."""

    provider: str
    model: str
    baseline: bool = False  # the baseline's own model
    outcome: Literal["ok", "flagged", "untested"] = "ok"
    reason: str | None = None
    attempts: int = 0  # test calls made: 0, 1, or 2 after one retry
    cost_usd: float = 0.0
    context_window: int | None = None  # tokens, when Kiln's model list has it


class ModelPreflightResult(BaseModel):
    """The model preflight's record, for the report."""

    baseline_rc_id: str
    checks: list[ModelCheck] = []
    # The baseline's prompt and tool definitions, in estimated tokens
    # (characters / 4). None when the prompt could not be read.
    estimated_prompt_tokens: int | None = None
    # Allowed models the preflight did not test, because there were more
    # than it tests (`model_preflight.MAX_CHECKED_MODELS`).
    untested: int = 0
    spend_usd: float = 0.0
    skipped: str | None = None  # the test calls did not run, and why

    def flagged(self) -> list[ModelCheck]:
        return [c for c in self.checks if c.outcome == "flagged"]

    def inconclusive(self) -> list[ModelCheck]:
        return [c for c in self.checks if c.outcome == "untested"]


class MeasurementPrices(BaseModel):
    """What measuring costs on this task, from the baseline's stored train
    rows (`budget.measurement_prices`): the planner's input for
    each candidate's `max_usd`. A candidate on another model costs its own
    model's price per request: a screened candidate's is in its result
    (`cost_per_item_usd`)."""

    drive_usd_per_request: float | None = None  # the model's median cost
    per_eval: list[EvalPrice] = []
    timing_usd: float | None = None  # 30 timing pairs (arm + baseline)
    full_measurement_usd: float | None = None  # every active eval + timing
    remaining_run_budget_usd: float | None = None  # None: no --budget-usd
    default_budget_usd: float | None = None  # the default for a full one now
    hard_max_usd: float | None = None  # the clamp on any cap now
    rule: str = ""


class BatchPlanInput(BaseModel):
    guidance: str  # kickoff human guidance, verbatim, every wave
    baseline: BaselineRef
    should_wrap_up: bool  # the code budget judgment — the conclusion,
    #   not the clock
    eval_sizes: dict[str, int]  # eval_id -> measurable TRAIN items on the
    #   baseline: the count of stored EvalRuns, i.e. what a comparison can
    #   actually pair, not the dataset's nominal size. Advisory
    #   — the planner weighs detectability before predicting an effect. An eval
    #   whose count could not be read is absent, never zero.
    # Issue id → technique → arms ever opened on
    #   that (issue, lever), any outcome. The planner's "two failed text arms
    #   on one issue → change surface class" rule reads this, not memory
    #   prose. Empty when nothing has been tried. Computed by code each wave.
    lever_attempts: dict[str, dict[str, int]] = {}
    prior_rejected_summary: str | None = None  # None on wave 1 — the one
    #   in-context carry; everything else the planner reads from memory
    # How many arms can build/measure at once,
    #   None = unlimited, and what each lever class cost to build so far this
    #   run. Empty on wave 1; the skill carries a measured reference table.
    max_concurrent_agents: int | None = None
    lever_costs: dict[str, LeverCost] = {}
    # Every probe answered so far this run — the
    # setup reconnaissance battery (item ids `r0-…`) and each wave's probes —
    # newest last. `phase` is "replan" on the second planning call of a wave,
    # right after the probe phase this planner asked for: the answers to
    # THOSE probes are the last entries.
    probe_results: list[ProbeAnswer] = []
    phase: Literal["plan", "replan", "rescreen"] = "plan"
    # `objective`: what this run optimises (`--objective`).
    #   `baseline_latency`: the baseline's own per-item latency on the stored
    #   train eval runs (median/p90 over its items; the `candidate_*` fields
    #   are unset) — so a speed plan knows where the baseline stands.
    #   `model_policy`: the `--model-policy` rules, or None when no policy is
    #   in force. `allowed_models`: the models the server offers that the
    #   policy allows (every offered model without a policy) — an arm on any
    #   other model is refused by code before it is built or measured.
    objective: Objective = "quality"
    baseline_cost: LatencyComparison | None = None  # speed/cost: the
    #   baseline's per-request cost on the fixed speed sample (metric cost_usd)
    speed_rule: str | None = None  # under `speed`: the exact promotion rule,
    #   code-composed from the constants code applies (`speed_rule_text`)
    baseline_latency: LatencyComparison | None = None
    model_policy: dict[str, Any] | None = None
    allowed_models: list[dict[str, Any]] = []  # provider, model, name, and
    #   `thinking_levels` (the levels the server offers, lowest first)
    # The models the model preflight flagged this run, as
    #   "provider/model: reason". They are not in `allowed_models`, and code
    #   does not screen them.
    flagged_models: list[str] = []
    # Every screening round's results this run, newest last, and
    # how many more rounds this wave may run (`screen` is refused at 0).
    screening_results: list[ScreenResult] = []
    screen_rounds_left: int = 0
    # Screening results of EARLIER runs, from memory, newest
    # first per rc (`prior_run` true, with `run_id` and `date`). Their
    # quality rows are against that run's baseline; their latency is from
    # another window — orientation only, never a paired verdict.
    prior_screening_results: list[ScreenResult] = []
    settled_survivors: list[SettledSurvivor] = []
    # Code's notes on this wave's screening requests (a dropped re-screen).
    screening_notes: list[str] = []
    # The stop rules code applies to a round that sets none (objective-aware).
    default_stop_rules: list[StopRule] = []
    # This run's arms the residual-minibatch gate stopped, with
    # the items still failing and the guards broken (`ResidualMiss`).
    residual_misses: list[ResidualMiss] = []
    # This run's candidates the gate BLOCKED for copying eval
    #   text, with the reasons (`GateBlock`). Earlier runs' blocks are on
    #   their experiment records (`- blocked at the val gate …`).
    gate_blocks: list[GateBlock] = []
    # Measured prices and the spending-cap rule (`budget`).
    measurement_prices: MeasurementPrices | None = None
    # `detectability`: per active (eval, score), what the
    #   CURRENT baseline's stored train rows can show — items with room to
    #   improve, the significance floor and the fewest wins that can show
    #   at all. Train-only, recomputed every wave; a score with `room` 0
    #   cannot improve on train. `train_noise`: the `--noise-check` sample,
    #   measured once at setup on the run's first baseline (empty without
    #   the flag). Neither carries a val number.
    train_detectability: list[TrainDetectability] = []
    train_noise: list[NoiseRow] = []
    # `--experimental-eval-building`. False (the default): plan no
    #   `eval_only` item and no fix with `needs_eval: true` — code drops
    #   them before the wave runs. True: both are allowed.
    eval_building: bool = False
    # The `eval_item_dropped` lines of this run, oldest first: each item code
    #   dropped from an earlier plan because eval building is off. A dropped
    #   item never ran. Empty when nothing was dropped.
    dropped_eval_items: list[str] = []


class EvalBuildInput(BaseModel):
    """The eval-build boundary — issue id + baseline ONLY (hypothesis/technique
    never cross; the eval measures the issue, not the fix)."""

    issue_memory_id: str
    baseline: BaselineRef


class RcBuildInput(BaseModel):
    item: ExperimentItem  # eval_id resolved by this point
    baseline: BaselineRef  # work.parent_rc_id overrides baseline.rc_id in the
    #   rare escape-hatch case
    # The one rebuild after a generality leak (`kiln_harness_tuner.generality`): the
    #   refused run config and the reviewer's findings. Empty on a first build.
    rejected_rc_id: str | None = None
    generality_findings: list[str] = []
    # `kiln_harness_tuner.judge_copy`: the same one rebuild when the
    #   built run config copies eval text — each finding names the place
    #   (the prompt, a skill file, a code-tool file), the eval object and
    #   the copied passages. Empty on a first build.
    judge_copy_findings: list[str] = []
    # `kiln_harness_tuner.reference_check`, `--reference-check refuse`:
    #   the same one rebuild when the built run config names a skill or a
    #   tool it does not attach — each finding names the reference and
    #   where it is. Empty on a first build.
    reference_findings: list[str] = []


class ProbeTrainInput(BaseModel):
    """One train item to drive, handed over by code: the agents
    read no project file, so the probe gets its inputs in its input."""

    id: str
    structured_input: Any | None = None
    plaintext_input: str | None = None


class ProbeInput(BaseModel):
    """The probe agent's input: the question and the config to
    fork. `parent_rc_id` on the item overrides `baseline.rc_id`.
    `train_inputs`: train items code read from disk for it —
    the only items it may drive."""

    item: ProbeItem
    baseline: BaselineRef
    train_inputs: list[ProbeTrainInput] = []


class AnalysisInput(BaseModel):
    item: ExperimentItem  # incl. the hypothesis + predicted effect
    rc_id: str
    bundle: AnalysisBundle  # the train-side numbers table
    experiment_memory_id: str  # the analyzer PATCHes nothing — code does; the
    #   id is for reading its own open record + citations
    speed_rule: str | None = None  # set under `--objective
    #   speed` — the exact rule code applies to `successful` (and the gate)
    # `kiln_harness_tuner.reference_check`: what the measured run config
    #   names and does not attach. A failure on the cases that use such a
    #   name can be the build's breakage, not the lever.
    reference_findings: list[str] = []


class DescribeEvalInput(BaseModel):
    """describe_eval's input — one eval per session, never
    a batch (batching invites cross-contamination between
    unrelated criteria). Ids only: the agent reads the eval, its CURRENT
    config and the Spec itself."""

    eval_id: str
    eval_name: str
    spec_id: str | None = None  # None = the eval has no Spec (still active)


class CandidateValTable(BaseModel):
    """One promotion candidate's code-assembled val comparison — rows
    are candidate vs the current baseline on split=val.

    `errors` + `ineligible`: without an errors field, a failed or
    under-covered val fill would silently drop an eval from the comparison,
    and the promotion rationale could still say "no regressions" — which can
    change which config ships, in both directions. Every hole in this
    candidate's assembly is on the wire, and `ineligible` is the CODE-enforced floor:
    an active eval whose fill failed (either side) or whose coverage fell
    below `Ctx.coverage_floor` makes the candidate unpromotable this wave —
    the orchestrator blocks an ineligible winner regardless of what the
    promote_best agent decides."""

    rc_id: str
    experiment_memory_id: str
    per_eval: list[PerEvalResult]
    errors: list[ErrorInfo] = []
    ineligible: bool = False
    # (eval, score) pairs the gate could not compare and left
    # out on purpose — a custom score with no statistics operation, a score
    # with no direction flag — each with its reason. Informational, not a
    # hole: the agent (or the code rule) must know what was NOT compared.
    skipped: list[str] = []
    # The speed objective: the paired drive pass on a fixed val
    # sample, this candidate vs the current baseline in the same window.
    latency: LatencyComparison | None = None
    cost: LatencyComparison | None = None  # the same pass, cost per request
    # Code's reward-hacking checks (`kiln_harness_tuner.integrity`). A
    # flag starting "BLOCKED:" also made the table ineligible; any other is
    # a sign the promotion rationale must address in an `Integrity:` line.
    integrity_flags: list[str] = []
    # Set by the checks themselves; code applies no promotion of
    # a table without it, whichever path decided (`_apply_checked`).
    integrity_checked: bool = False
    # The findings of check (a) alone — the copied eval text,
    # read from the prompt and the eval definitions, never from val. The one
    # part of a block that may reach the planner and a train-readable
    # record (`orchestrator._record_gate_blocks`); every other flag is
    # computed from val traces and stays with the val readers.
    copy_findings: list[str] = []


class PromotionInput(BaseModel):
    candidates: list[CandidateValTable]
    baseline: BaselineRef  # the record itself the agent reads directly — the
    #   one agent allowed to
    guidance: str = ""  # the kickoff guidance, verbatim: an
    #   OPEN constraint the guidance answers is answered — the gate must not
    #   escalate on it
    objective: Objective = "quality"  # under `speed` a winner
    #   must be clearly faster on val (`CandidateValTable.latency`) with
    #   quality equal or better (`analysis.speed_quality_reasons`); code
    #   marks the rest ineligible


# ---- run status — the live run-state models; built by status.py,
# ----   defined here because ReportInput embeds RunStatus ----


class ItemStatus(BaseModel):  # one BatchPlan item, planned → measured
    item_id: str
    kind: str
    label: str
    live_state: Literal[
        "planned",
        "building_eval",
        "building_rc",
        "reviewing",  # the generality check between build and measurement
        "measuring",
        "analyzing",
        "probing",  # a probe item's agent session
        "done",
    ]
    eval_progress: tuple[int, int] | None  # from job polling, e.g. (12, 20)
    outcome: ItemOutcome | None  # set once, when it lands
    elapsed_secs: float
    spend_usd: float
    detail: str | None = None  # in-flight sub-state detail (which eval
    #   started/finished measuring) — set by `item_detail` events, cleared on
    #   every state change and at outcome
    phase_secs: dict[str, float] = {}  # cumulative seconds per live_state,
    #   folded from `item_state` transitions (where the
    #   item's wall clock went — build vs measure vs analyze)
    spend_by_lane: dict[str, float] = {}  # the item's own Claude-Code vs Kiln
    #   split — `spend_usd` is their sum. Defaulted and folded
    #   from the same `spend` events, so a stored RunStatus built before this
    #   field, and a hand-built one, both validate.
    change: RcChange | None = None  # what the arm changed, from the build
    trial: TrialResult | None = None  # an older wave log only
    #   agent (`item_change` event) — shown by every UI
    probe: ProbeResult | None = None  # a probe item's answer (`item_probe`)


class PhaseSegment(BaseModel):
    """One completed loop-phase span (preflight / planning / promotion_gate /
    tripwire_backoff / reporting), folded from the existing `phase_end`
    events — the run-level "where did the time go" record."""

    name: str
    wave: int | None = None  # phases outside a wave (preflight, reporting)
    elapsed_secs: float


class WaveStatus(BaseModel):
    wave: int
    should_wrap_up: bool
    plan_headline: str
    probe_phase: bool = False  # the wave's probe phase (or the
    #   wave-0 reconnaissance battery), run before the wave's arms are planned
    rejected_summary: str
    items: list[ItemStatus]
    promotion: PromotionDecision | None
    tripwire: str | None
    promotion_skipped: str | None = None  # a promote_best hard-fail must
    #   be noted in RunStatus/the report


class RunStatus(BaseModel):
    project_id: str
    task_id: str
    guidance: str
    baseline_history: list[str]  # rc ids, promotion order
    waves: list[WaveStatus]  # complete history, never dropped
    spend_total_usd: float
    time_remaining_secs: float
    exit_reason: str | None
    # The live totals row (and the report-appendix header) need the
    # run's clock as well as its deadline:
    elapsed_secs: float = 0.0  # snapshot-computed, like time_remaining_secs
    timeout_mins: int | None = None  # the run budget, from run_start
    # Whether a `run_start` deadline was ever seen. `time_remaining_secs` is
    # 0.0 both when the budget is exhausted and when it was never set, and
    # only this flag tells those apart — renderers must not show "0s left"
    # for a run that simply has no deadline.
    time_remaining_known: bool = False
    # The loop phase running OUTSIDE item work — "planning", "promotion_gate",
    # "tripwire_backoff", "reporting" — with its display label and a
    # snapshot-computed elapsed, so the live UI always has something ticking.
    phase: str | None = None
    phase_detail: str = ""  # the `label` the phase_start event carried
    phase_elapsed_secs: float = 0.0
    janitor_stamped: list[str] = []  # startup sweep — stamped memory ids
    # The spend fold keeps its dimensions (string keys,
    # not SpendLane: the fold tolerates unknown lanes/sources — events are an
    # audit surface first) and the run keeps its time segments:
    spend_by_lane: dict[str, float] = {}  # lane → total usd (both lanes)
    spend_by_source: dict[str, dict[str, float]] = {}  # source → lane → usd
    #   — the report appendix's per-source × per-lane table data
    # Budget honesty: the provider cost of EVERY stored row the run
    # read (cached rows from earlier runs included) — what the evidence rests
    # on, as distinct from `spend_total_usd`, which is what this run paid —
    # and the tokens of timed-out agent sessions whose cost could not be read.
    evidence_spend_usd: float = 0.0
    unmeasured_tokens: int = 0
    # The agent CLI the run dispatches through (`claude`, `codex`, `opencode`):
    # the name of the agent spend lane wherever a person reads it.
    agent_cli: str = "claude"
    phase_segments: list[PhaseSegment] = []  # completed loop-phase spans,
    #   chronological (from phase_start/phase_end events)
    setup: SetupReport | None = None  # the pre-flight setup phase's record
    #   folded from the `setup_sync`/`setup_ok` event —
    #   None only until the phase has run (or in a RunStatus built by hand)
    # The active evals Kiln can't load, which this run left out
    # (`eval_unloadable` events) — the report's `Left out:` line.
    unloadable_evals: list[UnloadableEval] = []
    # First-class hole accounting: coverage and hole facts are counted here,
    # not only as prose inside agent findings.
    budget_usd: float | None = None  # --budget-usd, from run_start (None = no cap)
    eval_job_error_items: int = 0  # errored dataset items across all eval jobs
    #   (folded from `eval_job_errors` events)
    # The same items by "<split> <error_class>", e.g. {"val rate_limit": 68}:
    # what the errors were, so the report can say why without the log.
    eval_job_error_classes: dict[str, int] = {}
    val_fill_failures: int = 0  # `val_fill_failed` events — evals that went
    #   dark at a promotion gate
    objective: Objective = "quality"  # from run_start
    # Every val-side speed comparison the gate made, newest last:
    # (wave, rc id, the comparison) — the report's speed section.
    speed_results: list[dict[str, Any]] = []
    # Every candidate the gate blocked for copying eval text,
    # with its reasons (`gate_block` events) — the report's blocks section.
    gate_blocks: list[GateBlock] = []
    # The pre-run detectability check (val numbers in it are
    # val-grade) and the `--noise-check` result — the report's
    # code-computed Detectability appendix.
    detectability: DetectabilityReport | None = None
    noise_check: NoiseCheckResult | None = None
    # The model preflight — every model checked, and the ones
    # flagged and left out of screening, with the reason.
    model_preflight: ModelPreflightResult | None = None
    # Every screening result of this run, in round order
    # (`screening_round` events) — the report's trade-off appendix.
    screen_results: list[ScreenResult] = []
    # `--experimental-eval-building`, from run_start, and the planned items
    # code dropped because it was off (`eval_item_dropped` events) — the
    # report's "Issues with no eval" section. `issues_without_eval`: the open
    # issues no eval binds, read from memory when the report is written
    # (None = not read: eval building on, or the read failed).
    eval_building: bool = False
    dropped_eval_items: list[DroppedEvalItem] = []
    issues_without_eval: list[IssueWithoutEval] | None = None


class ReportInput(BaseModel):
    """report_writer input. Code appends a code-computed spend appendix
    (a `budget … · elapsed … · exit …` header over the per-source × per-lane
    table + exclusions boilerplate, rendered from this same RunStatus) AFTER
    the returned markdown — the agent narrates spend and the run's clock, it
    never restates those numbers (a report whose only time facts were agent
    prose could not tell a 60-minute run from a 120-minute one)."""

    run_status: RunStatus  # the full structured history; memory the
    #   agent reads itself — including the `baseline` record (the pointer +
    #   promotion chain) and the `val` stats record, whose numbers are
    #   val-grade and must be labeled as such
