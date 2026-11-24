"""Graded predictions and the fix check.

A fix arm's plan names items: the train items the change should fix
(`FixWork.target_items`) and the train items the parent passes that the
change could break (`FixWork.at_risk_items`). Those two lists are the plan's
item-level prediction. After the arm's train measurement, code grades them
on the rows that the measurement judged (`measure.Measured.evidence`). No
agent grades its own prediction.

**The grade**, one result for each named item (`schemas.GradedItem`):

- a target item the parent fails: `fixed` or `still_failing`;
- an at-risk item the parent passes: `held` or `broke`;
- `wrong_side`: the parent's stored result is not what the role needs (a
  target item the parent does not fail, an at-risk item it does not pass);
- `not_measured`: the item is not in the arm's train queue (it is not a
  train item of a targeted eval), the arm stopped before the item, or the
  candidate has no row on it.

The parent's result and the candidate's result are the pass or fail of the
first targeted score of the item's eval, as the measurement queue reads
them (`measure.draw_queue`).

**The fix check** reads the same rows. A fix passes the items that led to
it, and breaks none that passed:

1. every target item with a result passes (`PredictionGrade.targets_pass`);
2. the items the parent passes that now fail are not clearly above noise:
   `broke` of `passing_judged`, a one-sided exact binomial test against the
   run's noise flip rate at alpha 0.05, one look. It is the test of the
   guard half of the `residual` stop rule (`stop_rules.guard_breaks`), on
   every judged pass and not on four guards.

**A miss is a flag, not a verdict.** A prediction sets the caution, never
the verdict (`items._execute_plan_item`). The grade and the check change no
stop rule and no part of `successful`. The stop rules that end an arm are
in `stop_rules`: `targets` ends an arm whose named item still fails after
the first chunk, and `residual` ends an arm whose discriminating block
fails. This module adds the record of what the items did. The lines go on
the experiment record, the grade goes to the analyzer
(`AnalysisInput.prediction`), to the next planner
(`BatchPlanInput.graded_predictions`) and to the report.

All ids here are train ids. The measurement queue is drawn from the train
split only, so an id from another split is never judged: its grade is
`not_measured`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from kiln_harness_tuner.estimate import Judged
from kiln_harness_tuner.schemas import GradedItem, PredictionGrade, StopRule
from kiln_harness_tuner.stop_rules import RESIDUAL_MIN_FIXED_SHARE, guard_breaks

# The most item ids one record line names. The grade keeps every id.
LINE_MAX_IDS = 8

PREDICTED_FIXES_KEY = "predicted fixes"
PREDICTED_RISKS_KEY = "predicted risks"
OTHER_BREAKS_KEY = "breaks not predicted"
FIX_CHECK_KEY = "fix check"
PREDICTION_LINE_KEYS = (
    PREDICTED_FIXES_KEY,
    PREDICTED_RISKS_KEY,
    OTHER_BREAKS_KEY,
    FIX_CHECK_KEY,
)


def _unique(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


def _graded(role: str, item_id: str, rows: Sequence[Judged]) -> list[GradedItem]:
    """One named item's results: one for each eval that judged it."""
    out: list[GradedItem] = []
    for row in rows:
        if row.cand_pass is None:
            result = "not_measured"
        elif role == "fix":
            if row.parent_pass is False:
                result = "fixed" if row.cand_pass else "still_failing"
            else:
                result = "wrong_side"
        elif row.parent_pass is True:
            result = "held" if row.cand_pass else "broke"
        else:
            result = "wrong_side"
        out.append(
            GradedItem(
                item_id=item_id,
                role=role,  # type: ignore[arg-type]
                eval_id=row.eval_id,
                result=result,  # type: ignore[arg-type]
            )
        )
    if not out:
        out.append(
            GradedItem(
                item_id=item_id,
                role=role,  # type: ignore[arg-type]
                result="not_measured",
            )
        )
    return out


def grade_predictions(
    judged: Sequence[Judged],
    *,
    item_id: str,
    rc_id: str,
    target_items: Sequence[str] = (),
    at_risk_items: Sequence[str] = (),
    complete: bool,
    noise_rate: float | None = None,
) -> PredictionGrade | None:
    """The grade of one arm (module doc). `judged` is every row of the arm's
    measurement, `noise_rate` the run's measured noise flip rate on the
    arm's evals (None: the default of `stop_rules.guard_noise_rate`). None
    when the candidate has no row at all: there is nothing to grade."""
    measured = [r for r in judged if r.cand_pass is not None]
    if not measured:
        return None
    by_item: dict[str, list[Judged]] = {}
    for row in judged:
        by_item.setdefault(row.item_id, []).append(row)
    at_risk = _unique(at_risk_items)
    items: list[GradedItem] = []
    for named in _unique(target_items):
        items += _graded("fix", named, by_item.get(named, []))
    for named in at_risk:
        items += _graded("at_risk", named, by_item.get(named, []))
    failing = [r for r in measured if r.parent_pass is False]
    passing = [r for r in measured if r.parent_pass is True]
    other: dict[str, list[str]] = {}
    for row in passing:
        if not row.cand_pass and row.item_id not in at_risk:
            other.setdefault(row.eval_id, []).append(row.item_id)
    # The rule carries the noise rate and the alpha only: the fix half of
    # `residual` (its threshold) is not read here.
    rule = StopRule(
        kind="residual", threshold=RESIDUAL_MIN_FIXED_SHARE, noise_rate=noise_rate
    )
    test = guard_breaks(rule, passing)
    grade = PredictionGrade(
        item_id=item_id,
        rc_id=rc_id,
        complete=complete,
        items=items,
        failing_judged=len(failing),
        fixed=sum(1 for r in failing if r.cand_pass),
        passing_judged=test.n,
        broke=test.broke,
        other_breaks={e: sorted(v) for e, v in sorted(other.items())},
        noise_rate=test.p0 if test.n else None,
        alpha=test.alpha,
        break_p=test.p if test.n else None,
        breaks_clear=test.clear,
    )
    grade.lines = grade_lines(grade)
    return grade


def _ids(by_eval: dict[str, list[str]]) -> str:
    """`eval: id, id; eval: id`, at most `LINE_MAX_IDS` ids, then the count
    of the rest."""
    parts: list[str] = []
    left = LINE_MAX_IDS
    hidden = 0
    for eval_id, ids in by_eval.items():
        shown = ids[: max(left, 0)]
        hidden += len(ids) - len(shown)
        left -= len(shown)
        if shown:
            parts.append(f"{eval_id}: {', '.join(shown)}")
    text = "; ".join(parts)
    if hidden:
        text += f" (+{hidden} more)"
    return text


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _named_line(grade: PredictionGrade, role: str) -> str | None:
    """`predicted fixes: …` or `predicted risks: …`, or None when the plan
    named no item of the role."""
    fix = role == "fix"
    good, bad = ("fixed", "still_failing") if fix else ("held", "broke")
    n_good, n_bad = grade.count(role, good), grade.count(role, bad)
    wrong = grade.named(role, "wrong_side")
    missing = grade.named(role, "not_measured")
    if not (n_good or n_bad or wrong or missing):
        return None
    noun = "target item" if fix else "at-risk item"
    parts: list[str] = []
    if n_good or n_bad:
        if fix:
            head = f"{n_good} of {_plural(n_good + n_bad, noun)} now pass"
        else:
            head = f"{n_bad} of {_plural(n_good + n_bad, noun)} broke"
        if n_bad:
            word = "still failing" if fix else "broke"
            head += f" ({word} {_ids(grade.named(role, bad))})"
        parts.append(head)
    else:
        parts.append(f"no {noun} has a result")
    if wrong:
        side = "the parent does not fail" if fix else "the parent does not pass"
        parts.append(f"{side} {_ids(wrong)}")
    if missing:
        parts.append(f"not measured {_ids(missing)}")
    key = PREDICTED_FIXES_KEY if fix else PREDICTED_RISKS_KEY
    return f"{key}: " + "; ".join(parts)


def break_text(grade: PredictionGrade) -> str:
    """ "2 of 41 passing items broke, within noise (p=0.78, noise rate
    0.10)"."""
    if not grade.passing_judged or grade.break_p is None:
        return "no passing item judged"
    verdict = "clearly above noise" if grade.breaks_clear else "within noise"
    rate = f", noise rate {grade.noise_rate:.2f}" if grade.noise_rate else ""
    return (
        f"{grade.broke} of {_plural(grade.passing_judged, 'passing item')} broke,"
        f" {verdict} (p={grade.break_p:.2g}{rate})"
    )


def fix_check_line(grade: PredictionGrade) -> str:
    """The fix check in one line. It starts `fix check: ok` or `fix check:
    FLAG`."""
    n_fixed = grade.count("fix", "fixed")
    n_still = grade.count("fix", "still_failing")
    if grade.targets_pass is None:
        targets = (
            "no target item has a result"
            if grade.count("fix", "wrong_side", "not_measured")
            else "no target item named"
        )
        targets += (
            f", {grade.fixed} of {_plural(grade.failing_judged, 'failing item')} fixed"
        )
    else:
        targets = f"{n_fixed} of {_plural(n_fixed + n_still, 'target item')} pass"
    word = "FLAG" if grade.flagged else "ok"
    scope = "" if grade.complete else " (on the items judged before the arm stopped)"
    return f"{FIX_CHECK_KEY}: {word} — {targets}; {break_text(grade)}{scope}"


def grade_lines(grade: PredictionGrade) -> list[str]:
    """The lines code writes on the experiment record, in order. Each is a
    `key: value` line of `PREDICTION_LINE_KEYS`."""
    lines = [
        line
        for line in (_named_line(grade, "fix"), _named_line(grade, "at_risk"))
        if line is not None
    ]
    if grade.other_breaks:
        n = sum(len(v) for v in grade.other_breaks.values())
        lines.append(
            f"{OTHER_BREAKS_KEY}: {_plural(n, 'passing item')} broke that the plan"
            f" did not name at risk ({_ids(grade.other_breaks)})"
        )
    lines.append(fix_check_line(grade))
    return lines
