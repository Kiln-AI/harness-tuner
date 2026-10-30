"""A spending cap on every measured candidate.

Every arm and every screening candidate the measurement engine measures
carries a dollar budget (`Budget`). When its attributed spend would pass
the budget, the engine ends it `capped`: an inconclusive result, never a
dead end or a failure. Its judged rows stay stored, and a later arm on the
same run config reuses them (`work.existing_rc_id` with a larger
`max_usd`).

**Where the number comes from.**

- The planner sets `max_usd` on an experiment item or a screening
  candidate, with a one-line `budget_reason` (what finding is worth that
  much).
- Without one, code sets the DEFAULT:

      min(DEFAULT_HEADROOM × expected full measurement,
          DEFAULT_MAX_USD,
          DEFAULT_REMAINING_SHARE × remaining run budget)

  The expected full measurement is the candidate's queue priced per judged
  item, plus its timing pairs. Before the first chunk the price is the
  parent's own stored rows (median task cost + median judge cost per item,
  per eval). After each chunk it is the candidate's own measured median per
  judged item, so the default scales with what the candidate really costs.
  The remaining-share term applies only under `--budget-usd`.
- The planner's value is then CLAMPED to the hard maximum:

      min(HARD_MAX_USD, HARD_MAX_SHARE × remaining run budget)

  so no single idea can take a large slice of the run. Without
  `--budget-usd` only HARD_MAX_USD applies. The default lies inside the
  clamp by construction (DEFAULT_MAX_USD ≤ HARD_MAX_USD and
  DEFAULT_REMAINING_SHARE ≤ HARD_MAX_SHARE; a test holds the order).
  The remaining run budget is `--budget-usd` minus the spend booked and
  minus the budget HELD by the other candidates being measured, read when
  the candidate's measurement starts.

**Held.** A candidate being measured holds the unspent part of its budget
(`BudgetHolds`, on `Ctx`): its budget minus its attributed spend, updated
after each chunk, released when it stops. So candidates measured in
parallel — the arms of one round, a screening round beside them — cannot
each size a cap against the same dollars: the second one sees the run
budget less what the first one may still spend. Before each chunk the
engine also checks the run as a whole: the spend booked, plus what the
other candidates hold, plus this chunk's projection, must stay inside
`--budget-usd`. A candidate that would pass it is capped (inconclusive),
like one at its own budget. Spend that nothing holds — agent sessions, a
drive outside the engine — is booked as it happens, so this check sees it
at the next chunk.

**Enforced.** Before each chunk the engine projects the chunk's cost (its
items at the current price per item, and its timing pairs at the current
price per pair). A candidate whose spend plus that projection passes its
budget is capped before the chunk is paid; a candidate already past it
(a chunk cost more than projected) is capped at the next chunk the same
way. The queue's last chunk, once paid, completes the measurement: a
complete measurement is never discarded for its cost.

**Attribution.** A candidate's spend is its own judged rows that this run
paid for (a row booked earlier costs $0), plus its own timing drives, plus
an equal share of the baseline's timing drives in the same window among the
candidates live in it, plus an equal share of its parent's timing drives
among the candidates with that parent, plus an equal share of any drive cost
the pass could not attribute to one arm. The shares sum to the pass's cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx
    from kiln_harness_tuner.schemas import MeasurementPrices

# The default cap: this much headroom over the expected full measurement ...
DEFAULT_HEADROOM = 1.5
# ... never more than this ...
DEFAULT_MAX_USD = 20.0
# ... nor more than this share of the remaining run budget.
DEFAULT_REMAINING_SHARE = 0.10
# The hard maximum, for any value, the planner's included.
HARD_MAX_USD = 60.0
HARD_MAX_SHARE = 0.25


@dataclass(frozen=True)
class Budget:
    usd: float
    source: Literal["planner", "default"]
    reason: str
    note: str = ""

    @property
    def line(self) -> str:
        head = f"budget ${self.usd:.2f} ({self.source}"
        head += f": {self.reason})" if self.reason else ")"
        return head + (f" — {self.note}" if self.note else "")


class BudgetHolds:
    """The budget held by the candidates being measured: for each, its
    budget minus its attributed spend (module doc, **Held**). Keys are
    opaque and unique per measured candidate."""

    def __init__(self) -> None:
        self._held: dict[object, float] = {}

    def hold(self, key: object, usd: float) -> None:
        self._held[key] = max(0.0, usd)

    def release(self, key: object) -> None:
        self._held.pop(key, None)

    def total(self, excluding: object = None) -> float:
        return sum(v for k, v in self._held.items() if k != excluding)


def booked_spend(ctx: Ctx) -> float:
    spend = getattr(ctx, "spend", None)
    return spend.total if spend is not None else 0.0


def held_budget(ctx: Ctx, excluding: object = None) -> float:
    holds = getattr(ctx, "budget_holds", None)
    return holds.total(excluding) if holds is not None else 0.0


def remaining_run_budget(ctx: Ctx, excluding: object = None) -> float | None:
    """`--budget-usd` minus the spend booked so far and minus the budget the
    candidates being measured hold (all but `excluding`'s); None without a
    run budget."""
    budget = getattr(ctx, "budget_usd", None)
    if budget is None:
        return None
    return max(0.0, float(budget) - booked_spend(ctx) - held_budget(ctx, excluding))


def hard_max_usd(remaining: float | None) -> float:
    cap = HARD_MAX_USD
    if remaining is not None:
        cap = min(cap, HARD_MAX_SHARE * remaining)
    return max(0.0, cap)


def default_budget_usd(expected_full: float | None, remaining: float | None) -> float:
    terms = [DEFAULT_MAX_USD]
    if expected_full is not None and expected_full > 0:
        terms.append(DEFAULT_HEADROOM * expected_full)
    if remaining is not None:
        terms.append(DEFAULT_REMAINING_SHARE * remaining)
    return max(0.0, min(terms))


def resolve_budget(
    planner_usd: float | None,
    reason: str,
    expected_full: float | None,
    remaining: float | None,
) -> Budget:
    """The candidate's budget: the planner's value or code's default, then
    clamped to the hard maximum."""
    cap = hard_max_usd(remaining)
    left = f"${remaining:.2f} left" if remaining is not None else "no run budget"
    if planner_usd is not None:
        usd = min(planner_usd, cap)
        note = (
            f"clamped from ${planner_usd:.2f} to the hard maximum ${cap:.2f} ({left})"
            if usd < planner_usd
            else ""
        )
        return Budget(usd, "planner", reason, note)
    usd = default_budget_usd(expected_full, remaining)
    expected = (
        f"expected full measurement ${expected_full:.2f}"
        if expected_full is not None
        else "expected cost unknown"
    )
    return Budget(usd, "default", f"{expected}, {left}")


def expected_full_usd(
    per_item: dict[str, float | None],
    items_per_eval: dict[str, int],
    timing_pairs: int,
    per_pair: float | None,
) -> float | None:
    """The cost of measuring the whole queue: each eval's items at its price
    per judged item, plus the timing pairs at their price per pair. None
    when no eval has a price."""
    total = 0.0
    priced = False
    for eval_id, n in items_per_eval.items():
        price = per_item.get(eval_id)
        if price is not None:
            total += price * n
            priced = True
    if timing_pairs and per_pair is not None:
        total += timing_pairs * per_pair
        priced = True
    return total if priced else None


def median_or_none(values: list[float]) -> float | None:
    return median(values) if values else None


BUDGET_RULE_TEXT = (
    "Every measured candidate has a dollar budget. Set `max_usd` on an"
    " experiment item or a screening candidate with a one-line"
    " `budget_reason` (what finding is worth that much). Without one, code"
    f" sets min({DEFAULT_HEADROOM:g} × the expected full measurement,"
    f" ${DEFAULT_MAX_USD:.0f}, {DEFAULT_REMAINING_SHARE:.0%} of the remaining"
    " run budget), re-priced after each chunk at the candidate's own measured"
    " cost per item. Any value is clamped to"
    f" min(${HARD_MAX_USD:.0f}, {HARD_MAX_SHARE:.0%} of the remaining run"
    " budget). The remaining run budget is net of what the candidates"
    " already being measured hold (their budgets less their spend), so the"
    " arms and screening candidates measured at the same time share it: a"
    " later one gets less than `hard_max_usd` when earlier ones hold a lot."
    " Before each chunk the run as a whole is also checked: a chunk that"
    " would take the run past `--budget-usd` is not paid, and its candidate"
    " ends CAPPED. A candidate that reaches its budget ends CAPPED: inconclusive,"
    " not a dead end; its rows are kept, and a later arm on the same run"
    " config (`existing_rc_id`) with a larger `max_usd` and a reason goes on"
    " from them."
)


async def measurement_prices(
    ctx: Ctx, baseline_rc_id: str, remaining: float | None
) -> MeasurementPrices | None:
    """Measured prices for the planner, from the baseline's stored train
    rows on each active eval: the model's median cost per request, each
    judge's median cost per item, and what a full measurement of each eval
    (and of all of them, with the timing pairs when they apply) is expected
    to cost. None when nothing can be read."""
    from kiln_harness_tuner.analysis import _runs_by_item
    from kiln_harness_tuner.kiln import KilnError
    from kiln_harness_tuner.latency import SPEED_SAMPLE_ITEMS, task_cost_of_eval_run
    from kiln_harness_tuner.objective import is_efficiency
    from kiln_harness_tuner.orchestrator import _active_evals
    from kiln_harness_tuner.schemas import EvalPrice, MeasurementPrices

    try:
        active = await _active_evals(ctx)
    except KilnError:
        return None
    drives: list[float] = []
    rows_out: list[EvalPrice] = []
    for eval_def, config_id in active:
        eval_id = str(eval_def["id"])
        try:
            runs = await ctx.analyzer.stored_results(
                eval_id, config_id, baseline_rc_id, "train"
            )
        except KilnError:
            continue
        by_item = _runs_by_item(runs)
        task = [
            c for r in by_item.values() if (c := task_cost_of_eval_run(r)) is not None
        ]
        judge = [
            float((r.get("eval_usage") or {}).get("cost") or 0.0)
            for r in by_item.values()
            if (r.get("eval_usage") or {}).get("cost") is not None
        ]
        drives += task
        rows_out.append(
            EvalPrice(
                eval_id=eval_id,
                name=str(eval_def.get("name") or eval_id),
                train_items=len(by_item),
                judge_usd_per_item=median_or_none(judge),
            )
        )
    drive = median_or_none(drives)
    for row in rows_out:
        if drive is not None or row.judge_usd_per_item is not None:
            row.full_measurement_usd = row.train_items * (
                (drive or 0.0) + (row.judge_usd_per_item or 0.0)
            )
    timing = (
        SPEED_SAMPLE_ITEMS * 2 * drive
        if drive is not None and is_efficiency(ctx.objective)
        else None
    )
    fulls = [r.full_measurement_usd for r in rows_out if r.full_measurement_usd]
    full = (sum(fulls) + (timing or 0.0)) if fulls else None
    return MeasurementPrices(
        drive_usd_per_request=drive,
        per_eval=rows_out,
        timing_usd=timing,
        full_measurement_usd=full,
        remaining_run_budget_usd=remaining,
        default_budget_usd=resolve_budget(None, "", full, remaining).usd,
        hard_max_usd=hard_max_usd(remaining),
        rule=BUDGET_RULE_TEXT,
    )
