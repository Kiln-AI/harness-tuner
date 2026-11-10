"""The Results section: the concerns and their three tiers (functional spec
7.5 with 16.3, 16.6, 16.12 and 16.13; architecture 7.2).

Values are raw: a pass rate as a fraction, a rating's mean, cost in USD per
request, latency in milliseconds. "Better" follows each concern's
direction. Train changes are `PerEvalResult.delta`, which is in good-
direction units, turned back to the raw scale.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Literal

from kiln_harness_tuner.analysis import BINARY_SCORE_TYPES
from kiln_harness_tuner.schemas import (
    CandidateValTable,
    DetectabilityReport,
    LatencyComparison,
    PerEvalResult,
    ScreenResult,
)
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.state import (
    COST,
    COST_KEY,
    LATENCY,
    LATENCY_KEY,
    LOWER_IS_BETTER,
    SPIN,
    Concern,
    ConcernKey,
    ConcernKind,
    ConcernView,
    Line,
    ResultsView,
    Span,
    TierView,
    Tone,
    Unconfirmed,
    joined,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.tui.model.fold import RunModel

Metric = Literal["latency", "cost"]
# A rating's scale (`words.RATING_SCALE`).
RATING_LOW, RATING_HIGH = 1.0, 5.0
# Cost and latency bars are drawn against Start, which sits at this share
# of the bar, so a cut shows as a shorter bar.
START_SHARE = 0.8


def concern_kind(score_type: str | None) -> ConcernKind:
    return "pass" if score_type in BINARY_SCORE_TYPES else "rating"


def concerns_from(report: DetectabilityReport) -> list[Concern]:
    """One concern per (eval, score) that can decide a promotion, in the
    task's order."""
    out: list[Concern] = []
    for row in report.rows:
        train = row.train
        key = (train.eval_id, train.score_key)
        if row.promotion_driving and all(c.key != key for c in out):
            out.append(
                Concern(
                    key,
                    train.eval_name or train.eval_id,
                    concern_kind(train.score_type),
                    train.direction,
                )
            )
    return out


def concern_from_row(row: PerEvalResult, name: str) -> Concern | None:
    """A concern for an eval first named mid-run, by a C1b or C2 row. A
    score with no direction cannot be compared, so it is no concern."""
    if row.direction == "informational":
        return None
    return Concern(
        (row.eval_id, row.score_key),
        name,
        concern_kind(row.score_type),
        row.direction or "higher_is_better",
    )


def start_pass_rates(report: DetectabilityReport) -> dict[ConcernKey, float]:
    """Start's train pass rates from setup: `room` is the items not at the
    best value, so the raw pass rate is (n − room) ÷ n, or room ÷ n where
    lower is better."""
    rates: dict[ConcernKey, float] = {}
    for row in report.rows:
        train = row.train
        if train.score_type not in BINARY_SCORE_TYPES or train.n <= 0:
            continue
        at_best = (train.n - train.room) / train.n
        rates[(train.eval_id, train.score_key)] = (
            1 - at_best if train.direction == LOWER_IS_BETTER else at_best
        )
    return rates


def comparison(
    overall: LatencyComparison | None, rows: Sequence[PerEvalResult], metric: Metric
) -> LatencyComparison | None:
    """One cost or latency comparison per config: the paired one when the
    loop timed the pair, else the per-eval one read off the most items."""
    if overall is not None and overall.baseline_median_ms is not None:
        return overall
    found = [
        c
        for c in (row.latency if metric == "latency" else row.cost for row in rows)
        if c is not None and c.baseline_median_ms is not None
    ]
    return max(found, key=lambda c: c.n_items, default=None)


def _per_request(
    rows: Sequence[PerEvalResult],
    latency: LatencyComparison | None,
    cost: LatencyComparison | None,
) -> tuple[tuple[ConcernKey, LatencyComparison | None], ...]:
    """The cost and the latency comparison of a set of rows."""
    return (
        (COST_KEY, comparison(cost, rows, "cost")),
        (LATENCY_KEY, comparison(latency, rows, "latency")),
    )


def side_values(
    rows: Sequence[PerEvalResult],
    latency: LatencyComparison | None,
    cost: LatencyComparison | None,
    side: Literal["baseline", "candidate"],
) -> dict[ConcernKey, float]:
    """One side's raw values of a set of rows: the scores per (eval, score),
    and the median cost and latency."""
    values: dict[ConcernKey, float] = {}
    for row in rows:
        score = row.baseline_score if side == "baseline" else row.candidate_score
        if score is not None:
            values.setdefault((row.eval_id, row.score_key), score)
    for key, picked in _per_request(rows, latency, cost):
        if picked is not None:
            median = (
                picked.baseline_median_ms
                if side == "baseline"
                else picked.candidate_median_ms
            )
            if median is not None:
                values[key] = median
    return values


def table_values(
    table: CandidateValTable, side: Literal["baseline", "candidate"]
) -> dict[ConcernKey, float]:
    return side_values(table.per_eval, table.latency, table.cost, side)


def _factor(c: LatencyComparison | None) -> float | None:
    if c is None or not c.baseline_median_ms or c.candidate_median_ms is None:
        return None
    return c.candidate_median_ms / c.baseline_median_ms


def _crosses_zero(row: PerEvalResult) -> bool:
    if row.ci_low is None or row.ci_high is None:
        return not row.significant
    return row.ci_low <= 0 <= row.ci_high


def arm_unconfirmed(
    rows: Sequence[PerEvalResult],
    latency: LatencyComparison | None,
    cost: LatencyComparison | None,
    *,
    rc_id: str | None,
    item_id: str,
    generation: int,
) -> list[Unconfirmed]:
    """An arm's train results against its parent, one per concern."""
    out = []
    for row in rows:
        change = -row.delta if row.direction == LOWER_IS_BETTER else row.delta
        out.append(
            Unconfirmed(
                (row.eval_id, row.score_key),
                rc_id,
                item_id,
                None,
                change,
                factor=False,
                clear=not _crosses_zero(row),
                generation=generation,
            )
        )
    for key, picked in _per_request(rows, latency, cost):
        factor = _factor(picked)
        if picked is not None and factor is not None:
            out.append(
                Unconfirmed(
                    key,
                    rc_id,
                    item_id,
                    None,
                    factor,
                    factor=True,
                    clear=picked.clearly_faster,
                    generation=generation,
                )
            )
    return out


def survivor_unconfirmed(result: ScreenResult, generation: int) -> list[Unconfirmed]:
    """A screen survivor counts for cost and latency only: its quality was
    screened, not measured."""
    out = []
    for key, picked in ((COST_KEY, result.cost), (LATENCY_KEY, result.latency)):
        factor = _factor(picked)
        if picked is not None and factor is not None:
            out.append(
                Unconfirmed(
                    key,
                    result.rc_id,
                    None,
                    result.label or result.candidate_id,
                    factor,
                    factor=True,
                    clear=picked.clearly_faster,
                    generation=generation,
                )
            )
    return out


def on_scale(kind: ConcernKind, value: float) -> float:
    """`value` inside its concern's scale: a pass rate in 0–100%, a rating
    in 1–5, cost and latency at 0 or more. A train change added to its
    parent's value can pass the scale's end."""
    if kind == "pass":
        return max(0.0, min(1.0, value))
    if kind == "rating":
        return max(RATING_LOW, min(RATING_HIGH, value))
    return max(0.0, value)


def fraction(kind: ConcernKind, value: float, start: float | None) -> float:
    """The bar's length: a pass rate on 0–100%, a rating on 1–5, cost and
    latency against Start."""
    if kind == "pass":
        share = value
    elif kind == "rating":
        share = (value - RATING_LOW) / (RATING_HIGH - RATING_LOW)
    else:
        share = value / start * START_SHARE if start else START_SHARE
    return max(0.0, min(1.0, share))


def _tier(
    concern: Concern,
    value: float,
    start: float | None,
    reference: float | None,
    tone: Tone,
) -> TierView:
    change = (
        "" if reference is None else words.change_text(concern.kind, value, reference)
    )
    return TierView(
        words.value_text(concern.kind, value),
        change,
        tone,
        fraction(concern.kind, value, start),
    )


def _direction_tone(concern: Concern, value: float, reference: float) -> Tone:
    """Best Confirmed against Start: green or red by direction only (16.13)."""
    if concern.better(value, reference):
        return "success"
    if concern.better(reference, value):
        return "error"
    return "muted"


def best_unconfirmed(
    model: RunModel, concern: Concern, parent: float | None
) -> tuple[float, Unconfirmed] | None:
    """The best result of this generation for the concern that beats its
    parent's value. Only successful arms count, and screen survivors (cost
    and latency); a config that stopped, failed or was judged and not
    promoted never does (`RunModel.counts`)."""
    if parent is None:
        return None
    best: tuple[float, Unconfirmed] | None = None
    for result in model.unconfirmed:
        if result.key != concern.key or result.generation != model.generation:
            continue
        if not model.counts(result):
            continue
        value = parent * result.change if result.factor else parent + result.change
        value = on_scale(concern.kind, value)
        if not concern.better(value, parent):
            continue
        if best is None or concern.better(value, best[0]):
            best = (value, result)
    return best


def all_concerns(model: RunModel) -> list[Concern]:
    return [*model.concerns, COST, LATENCY]


def start_split(model: RunModel) -> str:
    """The split the key names for Start: val from the first gate on."""
    return "val" if model.start_val is not None else "train"


def start_value(model: RunModel, key: ConcernKey) -> tuple[float | None, str]:
    """Start's value for one concern, and its split. From the first gate on,
    the incumbent's val value where the gate measured it; else the train
    value, which keeps its train label."""
    if model.start_val is not None and key in model.start_val:
        return model.start_val[key], "val"
    return model.start_train.get(key), "train"


def parent_value(model: RunModel, key: ConcernKey) -> float | None:
    """What a wave's arms branch from: Best Confirmed, or Start before the
    first promotion."""
    confirmed = model.confirmed.get(key) if model.confirmed_rc else None
    return confirmed if confirmed is not None else start_value(model, key)[0]


def _title(concern: Concern, multi: Counter[str | None]) -> tuple[str, str]:
    """An eval's card says it is an eval, and names its score only when
    the eval has more than one card."""
    qualifiers = []
    if concern.eval_id is not None and multi[concern.eval_id] > 1:
        qualifiers.append(concern.key[1])
    if concern.kind == "rating":
        qualifiers.append(words.RATING_SCALE)
    if concern.kind == "latency":
        qualifiers.append(words.MEDIAN)
    name = words.eval_title(concern.name) if concern.eval_id else concern.name
    return name, " ".join(qualifiers)


def _subtitle(in_flight: int | None, holder: str | None) -> Line:
    parts: list[Line] = []
    if in_flight:
        parts.append((SPIN, Span(f" {words.in_flight(in_flight)}", "muted")))
    if holder:
        parts.append((Span(holder, "warning"),))
    return joined(parts)


def _start_note(model: RunModel, start: float | None, split: str) -> str:
    if start is None:
        return words.NOT_MEASURED_YET
    return split if split != start_split(model) else ""


def concern_view(
    model: RunModel, concern: Concern, multi: Counter[str | None]
) -> ConcernView:
    start, split = start_value(model, concern.key)
    confirmed = model.confirmed.get(concern.key) if model.confirmed_rc else None
    parent = parent_value(model, concern.key)
    best = best_unconfirmed(model, concern, parent)
    in_flight = (
        None
        if concern.eval_id is None
        else sum(
            1
            for item in model.items.values()
            if item.live and concern.eval_id in item.targets
        )
    )
    holder = None
    if best is not None:
        holder = model.holder_label(best[1])
    title, qualifier = _title(concern, multi)
    return ConcernView(
        title=title,
        qualifier=qualifier,
        start=None if start is None else _tier(concern, start, start, None, "muted"),
        start_note=_start_note(model, start, split),
        confirmed=None
        if confirmed is None
        else _tier(
            concern,
            confirmed,
            start,
            start,
            "muted" if start is None else _direction_tone(concern, confirmed, start),
        ),
        unconfirmed=None
        if best is None
        else _tier(
            concern, best[0], start, parent, "success" if best[1].clear else "muted"
        ),
        in_flight=in_flight,
        subtitle=_subtitle(in_flight, holder),
    )


def key_lines(model: RunModel) -> tuple[Line, Line, Line]:
    start = (
        Span(words.SWATCH, "muted"),
        Span(f" {words.RESULTS_START}"),
        Span(f" · {start_split(model)} · {words.STARTING_POINT}", "muted"),
    )
    if model.confirmed_rc:
        confirmed: Line = (
            Span(words.SWATCH),
            Span(f" {words.RESULTS_CONFIRMED}"),
            Span(" · val · ", "muted"),
            Span(model.rc_label(model.confirmed_rc)),
        )
    else:
        confirmed = (
            Span(words.SWATCH),
            Span(f" {words.RESULTS_CONFIRMED}"),
            Span(f" · {words.NO_PROMOTION_YET}", "muted"),
        )
    unconfirmed = (
        Span(words.SWATCH, "warning"),
        Span(f" {words.RESULTS_UNCONFIRMED}"),
        Span(f" · {words.TRAIN_ESTIMATE}", "muted"),
    )
    return start, confirmed, unconfirmed


def compare_rc_ids(model: RunModel) -> tuple[str, ...]:
    """Start, Best Confirmed, then each distinct Best Unconfirmed config, in
    that order, without repeats (functional spec 7.8)."""
    ids: list[str | None] = [model.start_rc, model.confirmed_rc or model.start_rc]
    for concern in all_concerns(model):
        best = best_unconfirmed(model, concern, parent_value(model, concern.key))
        if best is not None:
            ids.append(best[1].rc_id)
    return tuple(dict.fromkeys(rc for rc in ids if rc))


def results_view(model: RunModel) -> ResultsView:
    multi = Counter(c.eval_id for c in model.concerns)
    return ResultsView(
        key=key_lines(model),
        cards=tuple(concern_view(model, c, multi) for c in all_concerns(model)),
        compare_rc_ids=compare_rc_ids(model),
    )


def parsed_rows(raw: Iterable[object]) -> list[PerEvalResult]:
    """The C1b or C2 rows that validate; a row that does not is left out."""
    rows = []
    for row in raw:
        try:
            rows.append(PerEvalResult.model_validate(row))
        except ValueError:
            continue
    return rows
