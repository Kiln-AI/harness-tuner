"""The pre-run detectability check: can this task's evals show
the improvement the loop is looking for at all?

It runs in setup, after the baseline's train measurement (`prepare_baseline`),
and before any wave spend. It reads the baseline's STORED train rows and the
split sizes on disk. It starts no eval job and no agent, so it costs nothing.

**The floor.** A paired test reaches p < `SIGNIFICANCE_ALPHA` only with a
minimum number of changed items, even when every change is a win and every
other item is unchanged:

- pass/fail (exact two-sided McNemar): k discordant pairs all one way give
  p = 2 · 0.5^k — 5 gives 0.0625, 6 gives 0.03125;
- a rating scale (the server's Wilcoxon signed-rank): no p below 5 non-zero
  differences, and with k all positive its normal approximation gives 0.059
  at 5 and 0.036 at 6.

Both floors are computed from the alpha (`binary_floor`, `rating_floor`),
never written down. So a score can show a significant win only when at
least `floor` items have room to improve, and the smallest change it can
show is floor / n of the items — a best case.

**The interval route.** The one-candidate val rule
(`orchestrator.decide_single_candidate`) also promotes when a row's
interval lies above zero. The server's intervals can do that with fewer
wins than the floor: its Newcombe paired interval for pass/fail says so for
3 of 5 failures fixed on 10 items, and for 1 of 1 on a single item; its
paired bootstrap for ratings says so for 4 wins at any n from 10 up. That
is not evidence of an improvement, so the check does NOT count it. It
reports it beside the floor (`interval_wins`: the server's arithmetic,
reproduced in `paired_ci_low` and `bootstrap_ci_excludes_zero`).

**Train and val.** The train side is exact: n and the room come from the
stored rows. The val side is not: setup reads no val result (the gate is
the only reader of val results).
So `val_n` is the val split's size on disk — a certain fact: fewer val
items than the floor can never show a significant win — and
`val_room_estimate` is `val_n` times the train share with room: an
estimate, labelled as one.

**The refusal.** Under `--objective quality`, the check refuses the run
(`UnderpoweredEvalsError`, a `SetupError`) when, as a certain fact, no
active eval score that could drive a promotion can show a significant val
win: every such score's val split holds fewer items than the floor, or
there is no such score at all. With `--min-effect`, also when no such
score's val split can show that change even in the best case (floor /
val_n above it). An estimate never refuses: it warns. One eval at its
ceiling never refuses either: a guard eval at 100% exists to catch
regressions. `--allow-underpowered` goes on anyway; the refusal stays in
the warnings and in the report. Under speed and cost, quality only has to
hold, so the check reports and never refuses.

**Where the numbers go.** The train rows (`TrainDetectability`) go to the
planner every wave. The full rows, with the val side, go only to the
terminal, the event log (read-denied to every agent) and the report, whose
appendix labels them val-grade. Nothing here is written to memory.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from statistics import NormalDist
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import (
    BINARY_SCORE_TYPES,
    RATING_SCORE_TYPES,
    SIGNIFICANCE_ALPHA,
    DirectionFlagMissingError,
    ScoreSpec,
    TargetExpansionError,
    UnsupportedScoreTypeError,
    _runs_by_item,
    expand_targets,
    normalize,
)
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.schemas import (
    DetectabilityReport,
    DetectabilityRow,
    EvalTarget,
    TrainDetectability,
)
from kiln_harness_tuner.setup import SetupError

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)

# The server omits the Wilcoxon p below this many non-zero differences
# (`statistics_lib.wilcoxon_signed_rank_p`).
WILCOXON_MIN_NONZERO = 5
# A normalized score this close to 1.0 is at the best value (the same slack
# `analysis.reproduction_verdict` uses).
_BEST_EPS = 1e-9
# Upper bound on any floor search, so a nonsense alpha cannot loop forever.
_MAX_FLOOR = 10_000


class UnderpoweredEvalsError(SetupError):
    """No active eval score that could drive a promotion can show a
    significant val win (module doc, **The refusal**). Raised before any
    wave spend, listing every offending score at once;
    `--allow-underpowered` goes on anyway."""


# ---- the floors ----


def binary_floor(alpha: float = SIGNIFICANCE_ALPHA) -> int:
    """The fewest discordant pairs, all one way, whose exact two-sided
    McNemar p is below `alpha`: p = 2 · 0.5^k."""
    k = 1
    while 2.0 * 0.5**k >= alpha and k < _MAX_FLOOR:
        k += 1
    return k


def wilcoxon_all_positive_p(k: int) -> float | None:
    """The server's two-sided Wilcoxon signed-rank p when all `k` non-zero
    differences are positive: the normal approximation with a continuity
    correction and no tie correction. None below `WILCOXON_MIN_NONZERO`,
    as the server answers. With every difference one way the rank sum is
    k(k + 1)/2 whatever the magnitudes and ties."""
    if k < WILCOXON_MIN_NONZERO:
        return None
    mu = k * (k + 1) / 4.0
    sigma = math.sqrt(k * (k + 1) * (2 * k + 1) / 24.0)
    w_plus = k * (k + 1) / 2.0
    z = max(0.0, abs(w_plus - mu) - 0.5) / sigma
    return min(1.0, max(0.0, math.erfc(z / math.sqrt(2.0))))


def rating_floor(alpha: float = SIGNIFICANCE_ALPHA) -> int:
    """The fewest non-zero differences, all positive, whose server Wilcoxon
    p is below `alpha`."""
    k = WILCOXON_MIN_NONZERO
    while k < _MAX_FLOOR:
        p = wilcoxon_all_positive_p(k)
        if p is not None and p < alpha:
            return k
        k += 1
    return k


def significance_floor(score_type: str, alpha: float = SIGNIFICANCE_ALPHA) -> int:
    if score_type in BINARY_SCORE_TYPES:
        return binary_floor(alpha)
    return rating_floor(alpha)


def fewest_wins(
    score_type: str, n: int, room: int, alpha: float = SIGNIFICANCE_ALPHA
) -> int | None:
    """The fewest wins that can make a significant win on `n` paired items
    of which `room` have room to improve: the floor, when the room reaches
    it. None when no outcome can be significant."""
    floor = significance_floor(score_type, alpha)
    return floor if min(room, n) >= floor else None


# ---- the interval route (reported, never counted) ----


def _z(alpha: float) -> float:
    return NormalDist().inv_cdf(1.0 - alpha / 2.0)


def _wilson(successes: int, total: int, z: float) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))
    return (max(0.0, center - margin), min(1.0, center + margin))


def paired_ci_low(n11: int, n10: int, n01: int, n00: int, z: float) -> float | None:
    """The lower end of the SERVER's Newcombe paired interval on p_b − p_a
    (A = the baseline, B = the candidate; n01 = the items B fixed), rounded
    to 4 places as the server serves it. The arithmetic is the server's,
    copied as it is (`statistics_lib.paired_proportion_diff_ci`), so this
    answers what the gate reads — including the lower end being built from
    p_a's lower margin and p_b's upper margin."""
    n = n11 + n10 + n01 + n00
    if n == 0:
        return None
    succ_a = n11 + n10
    succ_b = n11 + n01
    p_a = succ_a / n
    p_b = succ_b / n
    delta = p_b - p_a
    l_a, _u_a = _wilson(succ_a, n, z)
    _l_b, u_b = _wilson(succ_b, n, z)
    a_term = n11 * n00 - n10 * n01
    denom = math.sqrt(
        max(0.0, float(succ_a) * (n01 + n00) * float(succ_b) * (n10 + n00))
    )
    if denom == 0:
        phi = 0.0
    elif a_term - n / 2.0 > 0:
        phi = (a_term - n / 2.0) / denom
    elif a_term >= 0:
        phi = 0.0
    else:
        phi = a_term / denom
    phi = max(-1.0, min(1.0, phi))
    lower_var = (
        (p_a - l_a) ** 2 - 2 * phi * (p_a - l_a) * (u_b - p_b) + (u_b - p_b) ** 2
    )
    return round(max(-1.0, delta - math.sqrt(max(0.0, lower_var))), 4)


def bootstrap_ci_excludes_zero(n: int, k: int, alpha: float) -> bool:
    """Does the server's paired bootstrap interval lie above zero when `k`
    of `n` differences are positive and the rest zero? Every resample mean
    is then at least zero, and it is zero only when the draw holds no
    positive difference, with probability ((n − k)/n)^n. (The server's draw
    is random, so at that edge it can answer either way.)"""
    if n <= 0 or k <= 0:
        return False
    return ((n - k) / n) ** n < alpha / 2.0


def interval_wins(
    score_type: str, n: int, room: int, alpha: float = SIGNIFICANCE_ALPHA
) -> int | None:
    """The fewest wins, all one way, at which the server's interval lies
    above zero on `n` paired items of which `room` have room. None when no
    outcome does."""
    if n <= 0 or room <= 0:
        return None
    z = _z(alpha)
    for k in range(1, min(room, n) + 1):
        if score_type in BINARY_SCORE_TYPES:
            low = paired_ci_low(n - room, 0, k, room - k, z)
            if low is not None and low > 0:
                return k
        elif bootstrap_ci_excludes_zero(n, k, alpha):
            return k
    return None


def interval_best_case(
    score_type: str, n: int, alpha: float = SIGNIFICANCE_ALPHA
) -> int | None:
    """`interval_wins` at the best room up to `n`."""
    best: int | None = None
    for room in range(1, n + 1):
        k = interval_wins(score_type, n, room, alpha)
        if k is not None and (best is None or k < best):
            best = k
        if best == 1:
            break
    return best


# ---- the rows ----


def score_room(values: Iterable[float], spec: ScoreSpec) -> int:
    """How many of these baseline values are not at the best value. A
    pass/fail score reads the pass line the statistics read (>= 0.5 is a
    pass); a rating reads the normalized value."""
    room = 0
    for value in values:
        if spec.type in BINARY_SCORE_TYPES:
            passed = value >= 0.5
            best = not passed if spec.direction == "lower_is_better" else passed
            room += 0 if best else 1
        elif normalize(value, spec) < 1.0 - _BEST_EPS:
            room += 1
    return room


def train_row(
    eval_def: dict[str, Any],
    spec: ScoreSpec,
    runs: Sequence[dict[str, Any]],
    train_size: int | None,
    alpha: float = SIGNIFICANCE_ALPHA,
) -> TrainDetectability:
    """One score's train side, from the baseline's stored train rows."""
    by_item = _runs_by_item(runs)
    values = [
        float(v)
        for run in by_item.values()
        if (v := (run.get("scores") or {}).get(spec.key)) is not None
    ]
    n = len(values)
    room = score_room(values, spec)
    floor = significance_floor(spec.type, alpha)
    return TrainDetectability(
        eval_id=str(eval_def["id"]),
        eval_name=str(eval_def.get("name") or eval_def["id"]),
        score_key=spec.key,
        score_type=spec.type,
        direction=spec.direction,
        n=n,
        room=room,
        unscored=max(train_size - n, 0) if train_size is not None else None,
        floor=floor,
        fewest_wins=fewest_wins(spec.type, n, room, alpha),
        best_case_change=(floor / n) if n >= floor else None,
    )


def comparable_specs(
    eval_def: dict[str, Any],
) -> tuple[list[ScoreSpec], list[str]]:
    """The eval's scores the val gate can compare, and why the rest cannot.
    Informational scores are left out without a note: they never count."""
    eval_id = str(eval_def.get("id"))
    try:
        specs = expand_targets(eval_def, EvalTarget(eval_id=eval_id))
    except TargetExpansionError:
        return [], []
    except DirectionFlagMissingError as exc:
        return [], [f"{eval_id}: {exc}"]
    keep = [s for s in specs if s.type in BINARY_SCORE_TYPES | RATING_SCORE_TYPES]
    notes = [
        f"{eval_id}/{s.key}: score type {s.type!r} has no statistics operation"
        for s in specs
        if s not in keep
    ]
    return keep, notes


async def train_detectability(
    ctx: Ctx,
    rc_id: str,
    active: Sequence[tuple[dict[str, Any], str]],
    train_sizes: dict[str, int] | None = None,
    *,
    runs: Sequence[dict[str, Any]] | None = None,
) -> list[TrainDetectability]:
    """The planner's rows for `rc_id`: every comparable score of every
    active eval, from its stored train rows (`runs`, when the caller has
    just read them for a single eval). Advisory: a failed read drops that
    eval, never the wave."""
    rows: list[TrainDetectability] = []
    for eval_def, config_id in active:
        specs, _notes = comparable_specs(eval_def)
        if not specs:
            continue
        eval_runs: Sequence[dict[str, Any]]
        if runs is None:
            try:
                eval_runs = await ctx.analyzer.stored_results(
                    str(eval_def["id"]), config_id, rc_id, "train"
                )
            except KilnError:
                continue
        else:
            eval_runs = runs
        size = (train_sizes or {}).get(str(eval_def["id"]))
        for spec in specs:
            try:
                rows.append(train_row(eval_def, spec, eval_runs, size))
            except UnsupportedScoreTypeError:
                continue
    return rows


def build_rows(
    train_rows: Sequence[TrainDetectability],
    val_sizes: dict[str, int],
    alpha: float = SIGNIFICANCE_ALPHA,
) -> list[DetectabilityRow]:
    """Each train row with its val side: the val split size on disk, the
    estimate at the train share, and the interval route on both sides."""
    out: list[DetectabilityRow] = []
    for train in train_rows:
        val_n = val_sizes.get(train.eval_id)
        estimate = (
            round(val_n * train.room / train.n)
            if val_n is not None and train.n > 0
            else None
        )
        out.append(
            DetectabilityRow(
                train=train,
                train_interval_wins=interval_wins(
                    train.score_type, train.n, train.room, alpha
                ),
                val_n=val_n,
                val_room_estimate=estimate,
                val_best_case_change=(
                    train.floor / val_n
                    if val_n is not None and val_n >= train.floor
                    else None
                ),
                val_interval_best_case=(
                    interval_best_case(train.score_type, val_n, alpha)
                    if val_n is not None
                    else None
                ),
                promotion_driving=True,
            )
        )
    return out


# ---- the assessment ----


def _label(row: DetectabilityRow) -> str:
    t = row.train
    return f'{t.eval_id} "{t.eval_name}" {t.score_key} ({t.score_type})'


def _pct(value: float) -> str:
    return f"{value:.0%}"


def val_certainly_undetectable(row: DetectabilityRow) -> bool:
    """A certain fact: the val split holds fewer items than the floor."""
    return row.val_n is not None and row.val_n < row.train.floor


def val_estimate_undetectable(row: DetectabilityRow) -> bool:
    """An estimate: if val fails like train, fewer items than the floor
    have room."""
    return (
        row.val_room_estimate is not None
        and not val_certainly_undetectable(row)
        and row.val_room_estimate < row.train.floor
    )


def row_line(row: DetectabilityRow) -> str:
    """One plain line per score, for the terminal. The val half says which
    part is a split size and which is an estimate (`≈`)."""
    t = row.train
    if t.n == 0:
        train_part = "train: no stored baseline row"
    elif t.fewest_wins is not None and t.best_case_change is not None:
        train_part = (
            f"train {t.room} of {t.n} with room, a significant win needs"
            f" {t.fewest_wins} fixed ({_pct(t.best_case_change)} best case)"
        )
    else:
        train_part = (
            f"train {t.room} of {t.n} with room, fewer than the {t.floor} a"
            " significant win needs"
        )
    if row.val_n is None:
        val_part = "val size unknown"
    elif val_certainly_undetectable(row):
        val_part = f"val {row.val_n} items, fewer than {t.floor}: no significant win"
    elif row.val_room_estimate is None:
        val_part = f"val {row.val_n} items"
    else:
        val_part = f"val {row.val_n} items, ≈{row.val_room_estimate} with room" + (
            " (estimate: too few)" if val_estimate_undetectable(row) else ""
        )
    return f"detectability: {t.eval_id}/{t.score_key} — {train_part}; {val_part}"


def _row_warnings(row: DetectabilityRow, min_effect: float | None) -> list[str]:
    t = row.train
    label = f"{t.eval_id}/{t.score_key}"
    if t.n == 0:
        return [f"{label}: no stored baseline train row — not assessed"]
    out: list[str] = []
    if t.room == 0 and not t.unscored:
        out.append(
            f"{label}: at its ceiling on train ({t.n}/{t.n} at the best value)"
            " — a guard: no arm can improve it on train, it can only catch a"
            " regression"
        )
    elif t.fewest_wins is None:
        out.append(
            f"{label}: {t.room} train item(s) have room, fewer than the"
            f" {t.floor} a significant win needs"
        )
    if val_certainly_undetectable(row):
        out.append(
            f"{label}: val holds {row.val_n} item(s), fewer than the"
            f" {t.floor} a significant win needs"
        )
    elif val_estimate_undetectable(row) and t.room > 0:
        out.append(
            f"{label}: val (estimate) ≈{row.val_room_estimate} of {row.val_n}"
            " items have room if val fails like train — fewer than the"
            f" {t.floor} a significant win needs"
        )
    if min_effect is not None:
        if t.best_case_change is None or t.best_case_change > min_effect:
            out.append(
                f"{label}: train cannot show a change of {_pct(min_effect)}"
                + (
                    f" (best case {_pct(t.best_case_change)})"
                    if t.best_case_change is not None
                    else ""
                )
            )
        if (
            row.val_best_case_change is not None
            and row.val_best_case_change <= min_effect
            and val_estimate_undetectable(row)
        ):
            out.append(
                f"{label}: val (estimate) cannot show a change of"
                f" {_pct(min_effect)}: too few items with room"
            )
    return out


def assess(
    rows: Sequence[DetectabilityRow],
    *,
    objective: str,
    min_effect: float | None,
    alpha: float = SIGNIFICANCE_ALPHA,
) -> tuple[list[str], str | None]:
    """(warnings, refusal text or None). Pure: the rule in the module doc."""
    warnings = [w for row in rows for w in _row_warnings(row, min_effect)]
    if objective != "quality":
        return warnings, None
    driving = [r for r in rows if r.promotion_driving]
    tail = (
        "The one-candidate val gate promotes on a significant val win"
        f" (p < {alpha:g}). Without one, every wave is paid for and no"
        " candidate can be promoted on that evidence.\n"
    )
    override = (
        " Or pass --allow-underpowered to run anyway: the promotion agent then"
        " weighs nominal val wins."
    )
    floors = f"{binary_floor(alpha)} for pass/fail, {rating_floor(alpha)} for ratings"
    if not driving:
        return warnings, (
            "No active eval has a score the val gate can compare: every score"
            " is informational, has a type with no statistics operation, or"
            " could not be read.\n"
            + tail
            + "fix: give an active eval a pass/fail or five-star score with a"
            " direction." + override
        )
    if all(val_certainly_undetectable(r) for r in driving):
        listing = "\n".join(
            f"  • {_label(r)} — {r.val_n} val item(s), fewer than {r.train.floor}"
            for r in driving
        )
        plural = "s" if len(driving) != 1 else ""
        return warnings, (
            "No active eval score can show a significant win on val"
            f" ({len(driving)} score{plural} checked):\n{listing}\n"
            + tail
            + "fix: add val items to these evals. A significant win needs at"
            f" least this many val items with room to improve: {floors}"
            f" (p < {alpha:g})." + override
        )
    if min_effect is not None and all(
        r.val_n is not None
        and (r.val_best_case_change is None or r.val_best_case_change > min_effect)
        for r in driving
    ):
        listing = "\n".join(
            f"  • {_label(r)} — {r.val_n} val item(s): the smallest change that"
            " can show is "
            + (
                _pct(r.val_best_case_change)
                if r.val_best_case_change is not None
                else "none"
            )
            for r in driving
        )
        return warnings, (
            f"No active eval score can show a change of {_pct(min_effect)} on"
            f" val (--min-effect), even in the best case:\n{listing}\n"
            + tail
            + "fix: add val items to these evals, or lower --min-effect."
            + override
        )
    return warnings, None


# ---- the check ----


def split_sizes(ctx: Ctx, split: str) -> dict[str, int]:
    """eval id -> the split's size, from the project on disk. Empty when the
    project cannot be read. A count of items, never a result."""
    from kiln_harness_tuner.quarantine import QuarantineError, membership, task_dirs

    try:
        sizes: dict[str, int] = {}
        for task_dir in task_dirs(Path(ctx.target.project_path), ctx.target.task_id):
            member, _loaded = membership(task_dir)
            for eval_id, splits in member.by_eval.items():
                sizes[eval_id] = len(splits.get(split, ()))
        return sizes
    except (QuarantineError, OSError):
        return {}


async def run_detectability_check(ctx: Ctx, rc_id: str) -> DetectabilityReport:
    """The whole check: rows, lines, the event, and the refusal. Raises
    `UnderpoweredEvalsError` unless `ctx.allow_underpowered`."""
    from kiln_harness_tuner.orchestrator import _active_evals

    alpha = SIGNIFICANCE_ALPHA
    active = await _active_evals(ctx)
    train_sizes = split_sizes(ctx, "train")
    ctx.train_split_sizes = train_sizes
    skipped: list[str] = []
    for eval_def, _config_id in active:
        skipped += comparable_specs(eval_def)[1]
    train = await train_detectability(ctx, rc_id, active, train_sizes)
    rows = build_rows(train, split_sizes(ctx, "val"), alpha)
    warnings, refusal = assess(
        rows, objective=ctx.objective, min_effect=ctx.min_effect, alpha=alpha
    )
    report = DetectabilityReport(
        baseline_rc_id=rc_id,
        objective=ctx.objective,
        alpha=alpha,
        floor_binary=binary_floor(alpha),
        floor_rating=rating_floor(alpha),
        min_effect=ctx.min_effect,
        rows=rows,
        skipped=skipped,
        warnings=warnings,
        refusal=refusal,
        overridden=refusal is not None and ctx.allow_underpowered,
    )
    for row in rows:
        ctx.log.event("detectability_row", line=row_line(row))
    for warning in warnings:
        ctx.log.event("detectability_warning", line=f"⚠ detectability: {warning}")
    summary = (
        f"detectability: {len(rows)} score(s) on {len(active)} active eval(s);"
        f" a significant win needs at least {report.floor_binary} items fixed"
        f" (p < {alpha:g})"
    )
    if refusal is not None:
        summary += (
            " — REFUSED, going on (--allow-underpowered)"
            if report.overridden
            else " — REFUSED"
        )
    ctx.log.event("detectability", line=summary, report=report)
    logger.info("%s", summary)
    if refusal is not None:
        if not ctx.allow_underpowered:
            raise UnderpoweredEvalsError(refusal)
        ctx.log.event(
            "detectability_warning",
            line="⚠ detectability: refused, and --allow-underpowered goes on — "
            + refusal.splitlines()[0],
        )
    return report
