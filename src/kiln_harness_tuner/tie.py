"""Promotion on a tie (`specs/projects/tie-promotion/`).

A candidate normally becomes the baseline only when val shows that it is
better. Some changes are a better design and move no eval: a smaller tool
list, a harness with one group removed. The knowledge system records this for
each technique, in the section `If it does not move the evals`, which starts
with one of three phrases (`knowledge.schema.EVAL_STANCES`).

The rule: **knowledge settles a tie, never a loss.** When the gate's evidence
step chooses no winner, a candidate whose technique says `Keep it.` can be
promoted when code shows that it is not worse than the baseline on any eval,
on cost and on latency. Code decides "not worse". No agent judges it.

This module holds the pure parts:

- the technique that a build names, and its phrase (`resolve_stance`);
- the "not worse" test on quality (`quality_reasons`) and on a paired cost
  or latency comparison (`metric_reasons`);
- the train-side rule that lets such an arm reach the gate (`train_verdict`,
  `keep_arm_rules`), and its text for the analyzer (`tie_rule_text`);
- the choice among several candidates that pass (`pick_winner`).

The orchestrator applies them (`orchestrator._settle_tie`). The quality
objective only: under speed and cost a winner is clearly better by
definition.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from kiln_harness_tuner.analysis import (
    _SCORE_DOMAINS,
    BINARY_SCORE_TYPES,
    UNPRIORITISED_WEIGHT,
    SuccessVerdict,
    regressed_significantly,
    row_loss_tolerance,
    row_net_items,
)
from kiln_harness_tuner.knowledge.load import KnowledgeBase, KnowledgeError, load
from kiln_harness_tuner.knowledge.schema import EVAL_STANCES
from kiln_harness_tuner.schemas import (
    CandidateValTable,
    ExperimentItem,
    LatencyComparison,
    PerEvalResult,
    StopRule,
    score_json_key,
)

# How many promotions on a tie one run makes. Each "not worse" test can miss
# a small loss, and a chain of ties could add such losses with no eval
# evidence. With a limit of one, no chain exists inside a run. A higher limit
# needs the floor of the functional spec, section 8.
MAX_TIE_PROMOTIONS = 1

# A paired cost or latency comparison is "not worse" only when its per-item
# median ratio is at most 1 + this. The same width as the speed gate's
# `SPEED_TIE_RATIO`: two configs this close are equally fast.
TIE_RATIO_BAND = 0.03

# The alpha of the "clearly worse" sign test on a paired comparison, as in
# `latency.SIGNIFICANCE_ALPHA`.
WORSE_ALPHA = 0.05

STANCE_SECTION = "If it does not move the evals"

Stance = Literal["keep", "must_move", "depends"]

_STANCE_OF_PHRASE: dict[str, Stance] = dict(
    zip(EVAL_STANCES, ("keep", "must_move", "depends"), strict=True)
)

# The default stop rules that demand a gain. An arm whose technique says
# "Keep it." is not held to them.
GAIN_RULE_KINDS = ("futility", "residual")


@dataclass(frozen=True)
class TechniqueStance:
    """The technique document that a build named, and what its
    `If it does not move the evals` section says. `stance` is None when the
    build named no document, or named one that code does not accept; `note`
    then says why."""

    technique_id: str | None
    stance: Stance | None
    note: str = ""

    @property
    def keep(self) -> bool:
        return self.stance == "keep"


def resolve_stance(
    base: KnowledgeBase | None, technique_id: str | None, lever: str | None
) -> TechniqueStance:
    """The phrase of the technique `technique_id`, read from the knowledge
    skill by code. A stance is given only when all of these hold:

    - the id names a technique document, and the document parsed;
    - the document is `active`;
    - its `levers` name the lever of the experiment (`FixWork.technique`),
      so a build cannot name a technique that its change did not use;
    - its section starts with one of the three phrases.

    Anything else is no stance. The gate then needs a gain, as it does for
    an idea that is in no document."""
    if not technique_id:
        return TechniqueStance(None, None, "the build named no technique document")
    name = technique_id.strip()
    if base is None:
        return TechniqueStance(name, None, "the knowledge skill could not be read")
    doc = base.get("techniques", name)
    if doc is None:
        return TechniqueStance(name, None, f"no technique document {name!r}")
    if doc.meta is None or doc.problems:
        return TechniqueStance(name, None, f"technique {name!r} did not parse")
    if getattr(doc.meta, "status", None) != "active":
        return TechniqueStance(name, None, f"technique {name!r} is not active")
    levers = list(getattr(doc.meta, "levers", []) or [])
    if lever not in levers:
        return TechniqueStance(
            name,
            None,
            f"technique {name!r} is for the levers {levers}, and the experiment"
            f" uses {lever!r}",
        )
    section = doc.section(STANCE_SECTION)
    text = section.text if section else ""
    for phrase, stance in _STANCE_OF_PHRASE.items():
        if text.startswith(phrase):
            return TechniqueStance(name, stance)
    return TechniqueStance(
        name, None, f"technique {name!r} has no phrase in '{STANCE_SECTION}'"
    )


def knowledge_of(ctx: Any) -> KnowledgeBase | None:
    """The knowledge skill of the installed package, loaded once for the run
    and kept on `ctx.knowledge_base`. None when it cannot be read: then no
    build has a stance, and the gate needs a gain."""
    cached = getattr(ctx, "knowledge_base", None)
    if cached is not None:
        return cached or None
    try:
        base: KnowledgeBase | None = load()
    except (KnowledgeError, OSError):
        base = None
    ctx.knowledge_base = base if base is not None else False
    return base


# ---- the noise that the run measured ----


def noise_per_item(
    noise_rows: Iterable[Any], eval_id: str, score_key: str
) -> float | None:
    """The mean change of one item between two runs of the same config, in
    item-equivalents, from the run's `--noise-check` row for this (eval,
    score): the share of items whose pass/fail outcome changed, or, for a
    rating, the mean absolute change as a fraction of the scale. None when
    the run measured no noise on this score."""
    for row in noise_rows:
        if getattr(row, "eval_id", None) != eval_id:
            continue
        if getattr(row, "score_key", None) != score_key or not row.items:
            continue
        if row.score_type in BINARY_SCORE_TYPES:
            return row.redrive_changed / row.items
        domain = _SCORE_DOMAINS.get(row.score_type)
        if domain is None or row.redrive_mean_abs_change is None:
            return None
        return row.redrive_mean_abs_change / (domain[1] - domain[0])
    return None


def row_allowance(row: PerEvalResult, noise_rows: Iterable[Any]) -> int:
    """The net items that one row may lose and still count as "not worse":
    the measured noise per item times the row's items, rounded half up, and
    never above the speed gate's allowance (`analysis.row_loss_tolerance`).
    Zero when the run measured no noise on this score: the run gives no
    allowance for noise that it did not measure."""
    rate = noise_per_item(noise_rows, row.eval_id, row.score_key)
    if rate is None:
        return 0
    measured = math.floor(rate * row.n_items + 0.5)
    return min(row_loss_tolerance(row.n_items), measured)


# ---- "not worse" on quality ----


def pooled_net(
    rows: Sequence[PerEvalResult], weights: Mapping[str, float] | None = None
) -> float:
    weights = weights or {}
    return sum(
        weights.get(r.eval_id, UNPRIORITISED_WEIGHT) * (row_net_items(r) or 0.0)
        for r in rows
    )


def rows_reasons(
    rows: Sequence[PerEvalResult],
    weights: Mapping[str, float] | None = None,
    noise_rows: Iterable[Any] = (),
) -> list[str]:
    """Why these rows do NOT show "not worse" on quality. Empty when they
    do. Every row counts, a target or a guard:

    1. there is at least one row, and every row can be pooled
       (`analysis.row_net_items`);
    2. no row regressed significantly (`analysis.regressed_significantly`);
    3. no row loses more net items than its noise allowance
       (`row_allowance`);
    4. the net items pooled over every row, each eval weighted by its
       priority, are at least zero."""
    if not rows:
        return ["no quality row was compared"]
    noise = list(noise_rows)
    reasons: list[str] = []
    for r in rows:
        label = f"{r.eval_id}/{r.score_key}"
        if regressed_significantly(r):
            reasons.append(
                f"{label} regressed significantly (delta {r.delta:+.3f}, p={r.p_value})"
            )
        net = row_net_items(r)
        if net is None:
            reasons.append(f"{label} cannot be pooled (score type {r.score_type!r})")
            continue
        allowed = row_allowance(r, noise)
        if -net > allowed + 1e-9:
            reasons.append(
                f"{label} lost {-net:.1f} net item(s) of {r.n_items}, over its"
                f" noise allowance of {allowed}"
            )
    total = pooled_net(rows, weights)
    if total < -1e-9:
        reasons.append(
            f"quality is worse on balance: {total:+.1f} net item(s) pooled over"
            " every compared row (must be >= 0)"
        )
    return reasons


def quality_reasons(
    table: CandidateValTable,
    weights: Mapping[str, float] | None = None,
    noise_rows: Iterable[Any] = (),
) -> list[str]:
    """Why this val table does NOT show "not worse" on quality. On top of
    `rows_reasons`, the table itself must be whole and clean: no hole, no
    eval that the gate left out, and no integrity flag. Code cannot answer a
    flag, and a tie gives no reason to accept one."""
    reasons: list[str] = []
    if table.ineligible:
        reasons.append("the val evidence is incomplete or blocked")
    if table.skipped:
        reasons.append("the gate could not compare: " + "; ".join(table.skipped)[:300])
    if not table.integrity_checked:
        reasons.append("the integrity checks did not run")
    if table.integrity_flags:
        reasons.append(f"{len(table.integrity_flags)} integrity flag(s) need an answer")
    return reasons + rows_reasons(table.per_eval, weights, noise_rows)


# ---- "not worse" on cost and on latency ----


def clearly_worse(comparison: LatencyComparison) -> bool:
    """The mirror of `clearly_faster`: more items worse than better with an
    exact sign test below `WORSE_ALPHA` and a median ratio above 1, or a
    significant p90 regression."""
    if comparison.p90_regressed:
        return True
    return (
        comparison.slower_items > comparison.faster_items
        and comparison.sign_test_p is not None
        and comparison.sign_test_p < WORSE_ALPHA
        and (comparison.median_ratio or 0.0) > 1.0
    )


def metric_reasons(name: str, comparison: LatencyComparison | None) -> list[str]:
    """Why a paired comparison (`method: paired_drive`) does NOT show "not
    worse" on `name` ("cost" or "latency"). Empty when it does:

    1. the comparison was measured, and it is complete;
    2. it is not clearly worse (`clearly_worse`);
    3. its per-item median ratio is at most 1 + `TIE_RATIO_BAND`."""
    if comparison is None or comparison.method != "paired_drive":
        return [f"{name}: no paired comparison on val"]
    if comparison.incomplete or comparison.median_ratio is None:
        return [f"{name}: the paired comparison is incomplete ({comparison.note})"]
    reasons: list[str] = []
    if clearly_worse(comparison):
        reasons.append(
            f"{name}: clearly worse on val ({comparison.slower_items} items worse,"
            f" {comparison.faster_items} better, median ratio"
            f" {comparison.median_ratio:.2f})"
        )
    elif comparison.median_ratio > 1.0 + TIE_RATIO_BAND + 1e-9:
        reasons.append(
            f"{name}: median ratio {comparison.median_ratio:.2f} is above"
            f" {1.0 + TIE_RATIO_BAND:.2f}"
        )
    return reasons


# ---- the train side: how a tie reaches the gate ----


def keep_arm_rules(defaults: list[StopRule]) -> list[StopRule]:
    """Code's default stop rules for an arm whose technique says "Keep it.":
    the rules that demand a gain are left out. The rules that stop a worse
    arm stay, and so does a rule that the planner wrote for the arm."""
    return [r for r in defaults if r.kind not in GAIN_RULE_KINDS]


def train_verdict(
    item: ExperimentItem,
    rows: Sequence[PerEvalResult],
    coverage_floor: float,
    weights: Mapping[str, float] | None = None,
    noise_rows: Iterable[Any] = (),
) -> SuccessVerdict:
    """The train rule for an arm whose technique says "Keep it.", used when
    `analysis.success_by_rule` says no. The arm need not improve its primary
    target. It must be measured on it, at the coverage floor, and not worse
    on quality (`rows_reasons`, on the train rows)."""
    if not rows:
        return SuccessVerdict(False, ["no measured rows — nothing to judge"])
    reasons: list[str] = []
    primary = item.evaluation_target[0] if item.evaluation_target else None
    if primary is None:
        reasons.append("item names no evaluation target")
    else:
        wanted = (
            None
            if primary.scores is None
            else {score_json_key(s) for s in primary.scores}
        )
        if not any(
            r.eval_id == primary.eval_id and (wanted is None or r.score_key in wanted)
            for r in rows
        ):
            reasons.append(
                f"primary target {primary.eval_id} has no measured row (hole)"
            )
    for r in rows:
        if r.coverage is not None and r.coverage < coverage_floor:
            reasons.append(
                f"{r.eval_id}/{r.score_key} paired coverage {r.coverage:.0%} is"
                f" below the {coverage_floor:.0%} floor"
            )
    reasons += rows_reasons(rows, weights, noise_rows)
    return SuccessVerdict(not reasons, reasons)


def tie_rule_text(technique_id: str) -> str:
    """The train rule in words, for the analyzer of such an arm. Composed by
    code, with nothing from val in it."""
    return (
        f"This arm follows the technique `{technique_id}`, whose document says"
        " 'Keep it.' when the change does not move the evals. So the arm need"
        " not improve its target. `successful` is true when the arm is NOT"
        " WORSE on train: every targeted score was measured at the coverage"
        " floor; no score is down significantly; no score lost more net items"
        " than the noise that the run measured on it (zero when the run"
        " measured none); and the net items pooled over every score are at"
        " least zero. Code applies this rule too, and `successful` is your"
        " answer AND the rule. Do not veto the arm because it shows no gain."
        " Veto it for any other reason that you would veto an arm for, and"
        " give the `veto_reason`. A gain is still a gain: report it as usual."
    )


# ---- the choice ----


def _ratio(comparison: LatencyComparison | None) -> float:
    return comparison.median_ratio if comparison and comparison.median_ratio else 9.9


def pick_winner(
    tables: Sequence[CandidateValTable], weights: Mapping[str, float] | None = None
) -> CandidateValTable:
    """Among candidates that all passed the tie test: the highest pooled
    net, then the lowest cost ratio, then the lowest latency ratio, then the
    rc id."""
    return sorted(
        tables,
        key=lambda t: (
            -round(pooled_net(t.per_eval, weights), 6),
            round(_ratio(t.cost), 6),
            round(_ratio(t.latency), 6),
            t.rc_id,
        ),
    )[0]


def _ratio_text(name: str, comparison: LatencyComparison | None) -> str:
    if comparison is None or comparison.median_ratio is None:
        return f"{name} not compared"
    return (
        f"{name} x{comparison.median_ratio:.2f} (per-item median,"
        f" {comparison.n_items} paired items)"
    )


def rationale(
    table: CandidateValTable,
    technique_id: str,
    weights: Mapping[str, float] | None = None,
    evidence_rationale: str = "",
    passed: Sequence[str] = (),
) -> str:
    """The line for the `val` record, composed by code: summary numbers
    only, as the promoter's rationale. It starts with the grounds, so a
    reader of the record sees at once that no eval gain stands behind this
    promotion."""
    rows = "; ".join(
        f"{r.eval_id}/{r.score_key} {r.delta:+.3f}"
        f" ({row_net_items(r) or 0.0:+.1f} items of {r.n_items})"
        for r in table.per_eval
    )
    text = (
        f"design grounds (tie): technique `{technique_id}` says 'Keep it.';"
        " val shows no gain and no loss —"
        f" {pooled_net(table.per_eval, weights):+.1f} net items pooled, no eval"
        " over its noise allowance, none down significantly;"
        f" {_ratio_text('cost', table.cost)};"
        f" {_ratio_text('latency', table.latency)}. Rows: {rows}."
    )
    if len(passed) > 1:
        others = ", ".join(rc for rc in passed if rc != table.rc_id)
        text += f" Also not worse: {others}; the choice rule picked this one."
    if evidence_rationale:
        text += f" The evidence step chose no winner: {evidence_rationale[:400]}"
    return text
