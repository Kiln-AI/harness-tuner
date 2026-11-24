"""The thin-evidence rule (`kiln_harness_tuner.thin_evidence`): no win and
no loss on fewer than `MIN_DIFFERING_ITEMS` items that differ. The helpers,
the train rule and the val gate, each at and around the limit."""

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import ok
from fake_kiln import make_run
from test_orchestrator import (
    _experiment_memories,
    decision,
    rc_ok,
    seed_active_eval,
    seed_baseline,
    seed_issue,
)

from kiln_harness_tuner.analysis import (
    ScoreSpec,
    row_loss_tolerance,
    speed_quality_reasons,
    speed_quality_unverified,
    success_by_rule,
    success_by_speed_rule,
)
from kiln_harness_tuner.latency import compare_latencies
from kiln_harness_tuner.memory_schema import (
    ExperimentState,
    ValRejection,
    ValRejectionKind,
    experiment_state,
    rejection_note,
)
from kiln_harness_tuner.orchestrator import (
    apply_speed_eligibility,
    decide_single_candidate,
    gate_unverified,
    orchestrate,
    unverified_decision,
    val_rejection_reason,
)
from kiln_harness_tuner.schemas import (
    AnalysisBundle,
    CandidateValTable,
    PerEvalResult,
)
from kiln_harness_tuner.stop_rules import GUARD_NOISE_DEFAULT
from kiln_harness_tuner.thin_evidence import (
    MIN_DIFFERING_ITEMS,
    TargetEvidence,
    differing_items,
    is_thin,
    pooled_unverified_reason,
    room_items,
    target_evidence,
    took_all_room,
    unverified_reason,
)

LIMIT = MIN_DIFFERING_ITEMS
_UP = (0.05, 0.4)  # an interval entirely above zero
_DOWN = (-0.4, -0.05)  # an interval entirely below zero


def row(
    fixed: int,
    broke: int = 0,
    *,
    room: int = 10,
    n: int = 40,
    eval_id: str = "eval_1",
    ci: tuple[float | None, float | None] = (None, None),
    significant: bool = False,
    split: str = "train",
    coverage: float | None = None,
) -> PerEvalResult:
    """A pass/fail row as `Analyzer._stats_row` builds it: `fixed` of the
    `room` items that the baseline side fails now pass, and `broke` of the
    others now fail."""
    return PerEvalResult(
        eval_id=eval_id,
        score_key="accuracy",
        score_type="pass_fail",
        direction="higher_is_better",
        split=split,
        baseline_rc_id="rc_base",
        compared_to="parent",
        n_items=n,
        delta=(fixed - broke) / n,
        p_value=0.03 if significant else 0.5,
        significant=significant,
        ci_low=ci[0],
        ci_high=ci[1],
        headroom=room / n,
        fail_to_pass=fixed,
        pass_to_fail=broke,
        n_differing=fixed + broke,
        n_room=room,
        spend_usd=0.0,
        coverage=coverage,
        baseline_score=(n - room) / n,
        candidate_score=(n - room + fixed - broke) / n,
    )


def rating_row(delta: float, differing: int, *, room: int = 10) -> PerEvalResult:
    return PerEvalResult(
        eval_id="eval_1",
        score_key="quality",
        score_type="five_star",
        direction="higher_is_better",
        split="train",
        baseline_rc_id="rc_base",
        compared_to="parent",
        n_items=40,
        delta=delta,
        p_value=None,
        significant=False,
        fail_to_pass=None,
        pass_to_fail=None,
        n_differing=differing,
        n_room=room,
        spend_usd=0.0,
    )


def legacy_row(delta: float, ci=(None, None), significant=False) -> PerEvalResult:
    """A row with no counts: built before the count fields existed."""
    return PerEvalResult(
        eval_id="eval_1",
        score_key="accuracy",
        split="val",
        baseline_rc_id="rc_base",
        compared_to="prior_baseline",
        n_items=20,
        delta=delta,
        p_value=None,
        significant=significant,
        ci_low=ci[0],
        ci_high=ci[1],
        fail_to_pass=None,
        pass_to_fail=None,
        spend_usd=0.0,
    )


# ---- the constant and the helpers ----


def test_the_minimum_is_one_item_above_the_noise_of_a_small_eval():
    # What the constant rests on: at the default flip rate, a config that is
    # measured twice changes one or two items of a 16-to-24-item eval.
    assert MIN_DIFFERING_ITEMS == 3
    assert round(GUARD_NOISE_DEFAULT * 16) == 2
    assert round(GUARD_NOISE_DEFAULT * 24) == 2
    assert row_loss_tolerance(16) + 1 == MIN_DIFFERING_ITEMS
    assert row_loss_tolerance(24) + 1 == MIN_DIFFERING_ITEMS


@pytest.mark.parametrize(
    ("differing", "thin"),
    [(0, True), (LIMIT - 1, True), (LIMIT, False), (LIMIT + 1, False)],
)
def test_a_row_is_thin_below_the_minimum_and_not_at_it(differing, thin):
    assert is_thin(row(differing)) is thin
    assert is_thin(row(0, differing)) is thin
    assert is_thin(rating_row(0.1, differing)) is thin


def test_the_counts_come_from_the_new_fields_then_from_the_flips():
    assert differing_items(row(2, 1)) == 3
    flips_only = row(2, 1).model_copy(update={"n_differing": None, "n_room": None})
    assert differing_items(flips_only) == 3
    assert room_items(flips_only) == 10  # headroom x items
    assert differing_items(rating_row(0.1, 2)) == 2


def test_a_row_with_no_counts_is_never_thin():
    unknown = legacy_row(0.05)
    assert differing_items(unknown) is None
    assert room_items(unknown) is None
    assert not is_thin(unknown)


def test_took_all_room_reads_a_gain_equal_to_the_headroom():
    assert took_all_room(row(2, room=2))
    assert not took_all_room(row(1, room=2))
    assert not took_all_room(row(3, 1, room=3))  # it broke one
    assert not took_all_room(row(0, room=0))  # no room: nothing to take
    assert not took_all_room(rating_row(0.5, 2))  # a rating states no headroom


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        # A gain needs the minimum of items that differ.
        ([row(LIMIT)], TargetEvidence.GAIN),
        ([row(LIMIT - 1)], TargetEvidence.UNVERIFIED),
        ([row(LIMIT - 1, 1)], TargetEvidence.GAIN),  # 3 differ, net +1
        ([row(1)], TargetEvidence.UNVERIFIED),
        # No row improved: "no gain" when a row had the chances to.
        ([row(0, room=LIMIT)], TargetEvidence.NO_GAIN),
        ([row(0, room=LIMIT - 1)], TargetEvidence.UNVERIFIED),
        ([row(0, 2, room=10)], TargetEvidence.NO_GAIN),
        ([row(0, 2, room=1)], TargetEvidence.UNVERIFIED),
        ([row(1, 2, room=1, n=40)], TargetEvidence.NO_GAIN),  # 3 differ, net -1
        # Several rows: one row that is not thin decides.
        ([row(1), row(LIMIT, eval_id="e2")], TargetEvidence.GAIN),
        ([row(1), row(0, room=10, eval_id="e2")], TargetEvidence.UNVERIFIED),
        ([row(0, room=1), row(0, room=10, eval_id="e2")], TargetEvidence.NO_GAIN),
        # Ratings read the same counts.
        ([rating_row(0.2, LIMIT)], TargetEvidence.GAIN),
        ([rating_row(0.2, LIMIT - 1)], TargetEvidence.UNVERIFIED),
        # A thin gain beside a drop on enough items is no gain; beside a
        # thin drop it stays unverified.
        ([row(2), row(0, LIMIT, eval_id="e2")], TargetEvidence.NO_GAIN),
        ([row(2), row(0, LIMIT - 1, eval_id="e2")], TargetEvidence.UNVERIFIED),
        # No counts: the rule does not guess.
        ([legacy_row(0.05)], TargetEvidence.GAIN),
        ([legacy_row(0.0)], TargetEvidence.NO_GAIN),
    ],
)
def test_target_evidence(rows, expected):
    assert target_evidence(rows) is expected


def test_a_full_room_gain_counts_only_when_asked():
    perfect = [row(2, room=2)]
    assert target_evidence(perfect) is TargetEvidence.UNVERIFIED
    assert target_evidence(perfect, full_room_counts=True) is TargetEvidence.GAIN
    partial = [row(1, room=2)]
    assert target_evidence(partial, full_room_counts=True) is TargetEvidence.UNVERIFIED


def test_the_reason_names_the_counts_and_the_minimum():
    reason = unverified_reason([row(2), row(5, eval_id="e2")], "primary target eval_1")
    assert reason.startswith("unverified: primary target eval_1 shows no win and no")
    assert "eval_1/accuracy: 2 of 40 paired items differ, 10 had room" in reason
    assert "e2" not in reason  # only the thin rows
    assert f"at least {LIMIT} items that differ" in reason


def test_the_pooled_reason_at_the_limit():
    below = [row(0, 1), row(0, LIMIT - 2, eval_id="e2")]
    assert "2 item(s) differ over the 2 compared row(s)" in (
        pooled_unverified_reason(below, "quality") or ""
    )
    at = [row(0, 1), row(0, LIMIT - 1, eval_id="e2")]
    assert pooled_unverified_reason(at, "quality") is None
    assert pooled_unverified_reason([legacy_row(-0.1)], "quality") is None


# ---- the rows carry the counts ----


async def test_stats_rows_carry_the_differing_and_room_counts():
    ctx = make_ctx()
    kiln = ctx.kiln
    base = [make_run(f"t{i}", accuracy=float(i >= 4), stars=3.0) for i in range(10)]
    cand = [
        make_run(f"t{i}", accuracy=float(i >= 2 and i != 9), stars=3.0 + (i < 2))
        for i in range(10)
    ]
    cand[5]["scores"]["stars"] = 5.0
    base[6]["scores"]["stars"] = 5.0
    cand[6]["scores"]["stars"] = 5.0
    rows, holes = await ctx.analyzer.compare_rows(
        eval_id="eval_1",
        specs=[
            ScoreSpec(key="accuracy", type="pass_fail", direction="higher_is_better"),
            ScoreSpec(key="stars", type="five_star", direction="higher_is_better"),
        ],
        candidate_runs=cand,
        baseline_runs=base,
        baseline_rc_id="rc_base",
        compared_to="parent",
        split="train",
    )
    assert not holes
    binary, rating = rows
    # t2 and t3 fixed, t9 broken; the baseline fails t0..t3.
    assert (binary.fail_to_pass, binary.pass_to_fail) == (2, 1)
    assert (binary.n_differing, binary.n_room) == (3, 4)
    # t0, t1 and t5 moved; t6 is at the best value on the baseline side.
    assert (rating.n_differing, rating.n_room) == (3, 9)
    assert kiln.stats_bodies  # the statistics still come from the server


async def test_room_follows_the_direction_of_a_lower_is_better_score():
    ctx = make_ctx()
    base = [make_run(f"t{i}", errors=float(i < 3)) for i in range(8)]
    cand = [make_run(f"t{i}", errors=0.0) for i in range(8)]
    rows, _holes = await ctx.analyzer.compare_rows(
        eval_id="eval_1",
        specs=[ScoreSpec(key="errors", type="pass_fail", direction="lower_is_better")],
        candidate_runs=cand,
        baseline_runs=base,
        baseline_rc_id="rc_base",
        compared_to="parent",
        split="train",
    )
    (only,) = rows
    assert (only.n_differing, only.n_room) == (3, 3)
    assert took_all_room(only)


# ---- the train rule ----


def _bundle(*rows: PerEvalResult, latency=None) -> AnalysisBundle:
    return AnalysisBundle(
        per_eval=list(rows), spend_total_usd=0.0, errors=[], latency=latency
    )


def _verdict(*rows: PerEvalResult):
    return success_by_rule(fix_item(), _bundle(*rows), 0.95)


def test_train_a_gain_at_the_minimum_passes_the_rule():
    verdict = _verdict(row(LIMIT))
    assert verdict.ok, verdict.reasons
    assert verdict.unverified is None


def test_train_a_gain_below_the_minimum_is_unverified():
    verdict = _verdict(row(LIMIT - 1))
    assert not verdict.ok
    assert verdict.unverified is not None
    assert verdict.reasons == [verdict.unverified]
    assert "2 of 40 paired items differ" in verdict.unverified
    # One item is the same case.
    assert _verdict(row(1)).unverified is not None


def test_train_no_gain_with_room_stays_a_loss():
    verdict = _verdict(row(0, room=10))
    assert not verdict.ok
    assert verdict.unverified is None
    assert "did not improve" in verdict.reasons[0]
    # One or two broken items do not make it unverified either: the arm had
    # the chances, and it fixed nothing.
    assert _verdict(row(0, LIMIT - 1, room=10)).unverified is None


def test_train_no_gain_without_room_is_unverified():
    verdict = _verdict(row(0, room=LIMIT - 1))
    assert not verdict.ok
    assert verdict.unverified is not None
    assert _verdict(row(0, room=LIMIT)).unverified is None


def test_train_an_arm_that_takes_all_the_room_keeps_its_ticket():
    # The analysis skill's rule for an eval at its ceiling: a perfect arm
    # goes to the val gate, which has no such exception.
    assert _verdict(row(2, room=2)).ok
    assert _verdict(row(1, room=1)).ok
    assert _verdict(row(1, room=2)).unverified is not None


def test_train_a_thin_guard_drop_is_not_a_regression_beyond_doubt():
    target = row(5)
    thin_guard = row(0, LIMIT - 1, eval_id="guard", ci=_DOWN)
    assert _verdict(target, thin_guard).ok
    guard = row(0, LIMIT, eval_id="guard", ci=_DOWN)
    verdict = _verdict(target, guard)
    assert not verdict.ok
    assert "guard/accuracy regressed beyond doubt" in verdict.reasons[0]
    assert verdict.unverified is None


def test_train_unverified_with_another_reason_is_a_loss():
    guard = row(0, LIMIT, eval_id="guard", ci=_DOWN)
    verdict = _verdict(row(LIMIT - 1), guard)
    assert not verdict.ok
    assert len(verdict.reasons) == 2
    assert verdict.unverified is None
    low_coverage = _verdict(row(LIMIT - 1, coverage=0.5))
    assert low_coverage.unverified is None
    assert len(low_coverage.reasons) == 2


def test_train_a_rating_row_reads_the_same_minimum():
    assert _verdict(rating_row(0.2, LIMIT)).ok
    assert _verdict(rating_row(0.2, LIMIT - 1)).unverified is not None


# ---- the train rule under speed and cost ----


def _speed(ratio: float, n: int = 12):
    base = {f"i{k}": 1000.0 + k for k in range(n)}
    return compare_latencies(
        base,
        {k: v * ratio for k, v in base.items()},
        method="paired_drive",
        split="train",
    )


def test_speed_a_quality_refusal_on_too_few_items_is_unverified():
    bundle = _bundle(row(0, LIMIT - 1), latency=_speed(0.5))
    assert speed_quality_reasons(bundle.per_eval)  # the rule refuses it
    verdict = success_by_speed_rule(bundle, 0.95)
    assert not verdict.ok
    assert verdict.unverified is not None
    assert verdict.reasons == [verdict.unverified]
    assert "2 item(s) differ over the 1 compared row(s)" in verdict.unverified
    assert "quality is worse on balance" in verdict.unverified  # the refusal


def test_speed_a_quality_refusal_at_the_minimum_stands():
    bundle = _bundle(row(0, LIMIT), latency=_speed(0.5))
    verdict = success_by_speed_rule(bundle, 0.95)
    assert not verdict.ok
    assert verdict.unverified is None
    assert any("quality is worse on balance" in r for r in verdict.reasons)


def test_speed_an_arm_that_is_not_faster_is_a_loss_whatever_the_quality():
    bundle = _bundle(row(0, 1), latency=_speed(1.0))
    verdict = success_by_speed_rule(bundle, 0.95)
    assert not verdict.ok
    assert verdict.unverified is None


def test_speed_equal_quality_is_still_a_win():
    # No item differs and nothing refuses: the minimum applies to a refusal,
    # never to a candidate whose quality is equal.
    verdict = success_by_speed_rule(_bundle(row(0), latency=_speed(0.5)), 0.95)
    assert verdict.ok, verdict.reasons


def test_speed_a_row_that_cannot_be_pooled_is_not_unverified():
    unknown = legacy_row(-0.1)
    assert speed_quality_unverified([unknown], ["speed: cannot be pooled"]) is None
    assert speed_quality_unverified([row(0, 1)], []) is None


# ---- the val gate: the rule functions ----


def _table(*rows: PerEvalResult, ineligible: bool = False) -> CandidateValTable:
    return CandidateValTable(
        rc_id="rc_c",
        experiment_memory_id="mem_c",
        per_eval=list(rows),
        ineligible=ineligible,
    )


def val(fixed: int, broke: int = 0, **kwargs) -> PerEvalResult:
    return row(fixed, broke, split="val", n=20, **kwargs)


def test_gate_a_win_beyond_doubt_at_the_minimum_promotes():
    table = _table(val(LIMIT, ci=_UP, significant=True))
    decided = decide_single_candidate(table, {}, ["eval_1"])
    assert decided is not None
    assert decided.winner_rc_id == "rc_c"


def test_gate_a_win_beyond_doubt_below_the_minimum_is_unverified():
    # The server's interval can lie above zero on one or two fixed items.
    table = _table(val(LIMIT - 1, ci=_UP, significant=True))
    decided = decide_single_candidate(table, {}, ["eval_1"])
    assert decided is not None
    assert decided.winner_rc_id is None
    assert decided.rationale.startswith("code rule: no winner — unverified: val")
    assert "2 of 20 paired items differ" in decided.rationale
    assert val_rejection_reason(table, ["eval_1"], judge_gain=True) == ValRejection(
        ValRejectionKind.UNVERIFIED
    )


def test_gate_the_full_room_exception_does_not_apply_at_val():
    table = _table(val(2, room=2, ci=_UP, significant=True))
    decided = decide_single_candidate(table, {}, ["eval_1"])
    assert decided is not None and decided.winner_rc_id is None
    assert gate_unverified(table, ["eval_1"]) is not None


def test_gate_a_nominal_gain_at_the_minimum_still_goes_to_the_agent():
    table = _table(val(LIMIT, ci=(-0.1, 0.3)))
    assert decide_single_candidate(table, {}, ["eval_1"]) is None
    assert gate_unverified(table, ["eval_1"]) is None
    assert val_rejection_reason(table, ["eval_1"], judge_gain=True) == ValRejection(
        ValRejectionKind.GAIN_NOT_CLEAR
    )


def test_gate_a_drop_beyond_doubt_needs_the_minimum_too():
    at = _table(val(0, LIMIT, ci=_DOWN))
    decided = decide_single_candidate(at, {}, ["eval_1"])
    assert decided is not None and "regressed beyond doubt" in decided.rationale
    assert val_rejection_reason(at, ["eval_1"], judge_gain=True) == ValRejection(
        ValRejectionKind.EVAL_DROPPED, dropped_eval_ids=("eval_1",)
    )
    # Below it, the row is not a drop. With room it is "no gain".
    below = _table(val(0, LIMIT - 1, ci=_DOWN))
    decided = decide_single_candidate(below, {}, ["eval_1"])
    assert decided is not None and "no targeted row improved" in decided.rationale
    assert val_rejection_reason(below, ["eval_1"], judge_gain=True) == ValRejection(
        ValRejectionKind.NO_GAIN
    )
    # Below it and with no room, val shows nothing.
    no_room = _table(val(0, LIMIT - 1, room=1, ci=_DOWN))
    assert val_rejection_reason(no_room, ["eval_1"], judge_gain=True) == ValRejection(
        ValRejectionKind.UNVERIFIED
    )


def test_gate_a_thin_guard_drop_does_not_reject_a_real_win():
    table = _table(
        val(LIMIT, ci=_UP, significant=True),
        val(0, LIMIT - 1, eval_id="guard", ci=_DOWN),
    )
    decided = decide_single_candidate(table, {}, ["eval_1"])
    assert decided is not None and decided.winner_rc_id == "rc_c"


def test_gate_a_flat_candidate_with_room_is_no_gain_and_without_is_unverified():
    with_room = _table(val(0, room=LIMIT))
    assert val_rejection_reason(with_room, ["eval_1"], judge_gain=True) == (
        ValRejection(ValRejectionKind.NO_GAIN)
    )
    without = _table(val(0, room=LIMIT - 1))
    assert val_rejection_reason(without, ["eval_1"], judge_gain=True) == (
        ValRejection(ValRejectionKind.UNVERIFIED)
    )
    decided = decide_single_candidate(without, {}, ["eval_1"])
    assert decided is not None and decided.winner_rc_id is None
    assert "unverified" in decided.rationale


def test_gate_only_the_targeted_rows_decide_unverified():
    # A guard that moved on many items does not verify a thin target.
    table = _table(val(1, ci=_UP), val(8, eval_id="guard", ci=_UP, significant=True))
    assert gate_unverified(table, ["eval_1"]) is not None
    assert gate_unverified(table, ["guard"]) is None


def test_gate_unverified_is_not_set_on_a_table_with_no_verdict():
    assert gate_unverified(_table(val(1), ineligible=True), ["eval_1"]) is None
    assert gate_unverified(_table(), ["eval_1"]) is None
    assert gate_unverified(_table(val(1)), ["other_eval"]) is None
    dropped = _table(val(1), val(0, LIMIT, eval_id="guard", ci=_DOWN))
    assert gate_unverified(dropped, ["eval_1"]) is None  # a loss, not unverified


def test_gate_rows_with_no_counts_keep_the_rule_as_it_was():
    table = _table(legacy_row(0.2, ci=_UP, significant=True))
    decided = decide_single_candidate(table, {}, ["eval_1"])
    assert decided is not None and decided.winner_rc_id == "rc_c"


def test_the_unverified_decision_names_each_candidate():
    one = _table(val(1))
    one.unverified = gate_unverified(one, ["eval_1"])
    decided = unverified_decision([one])
    assert decided.winner_rc_id is None
    assert decided.rationale.startswith("code rule: no winner — rc_c: unverified: val")


def test_the_rejection_line_for_unverified_has_no_number():
    line = rejection_note("07-22", "rc_base", ValRejection(ValRejectionKind.UNVERIFIED))
    assert line == (
        "- rejected at the val gate 07-22 vs baseline rc_base — unverified at"
        " val: too few val items differ from the baseline, so val shows no win"
        " and no loss."
    )
    assert not any(ch.isdigit() for ch in line.split("—", 1)[1])


# ---- the val gate under speed and cost ----


def _speed_table(*rows: PerEvalResult, ratio: float = 0.5) -> CandidateValTable:
    table = _table(*rows)
    table.latency = _speed(ratio)
    return table


def test_speed_gate_a_quality_refusal_on_too_few_items_is_unverified():
    table = _speed_table(val(0, LIMIT - 1))
    apply_speed_eligibility(table)
    assert table.ineligible  # still not promoted
    assert table.unverified is not None
    assert val_rejection_reason(table, ["eval_1"], judge_gain=False) == ValRejection(
        ValRejectionKind.UNVERIFIED
    )


def test_speed_gate_a_quality_refusal_at_the_minimum_is_a_loss():
    table = _speed_table(val(0, LIMIT))
    apply_speed_eligibility(table)
    assert table.ineligible
    assert table.unverified is None
    assert val_rejection_reason(table, ["eval_1"], judge_gain=False) == ValRejection(
        ValRejectionKind.NOT_PROMOTED
    )


def test_speed_gate_a_candidate_that_is_not_faster_is_never_unverified():
    table = _speed_table(val(0, 1), ratio=1.0)
    apply_speed_eligibility(table)
    assert table.ineligible
    assert table.unverified is None


def test_speed_gate_equal_quality_stays_eligible():
    table = _speed_table(val(0))
    apply_speed_eligibility(table)
    assert not table.ineligible
    assert table.unverified is None


# ---- the record ----


def test_the_unverified_suffix_is_a_state_of_its_own():
    overview = "EXP 0722-w1-1: retry-skill — +5pp, base=75, cand=80, n=40, p=.5"
    assert experiment_state(overview + " — UNVERIFIED") is ExperimentState.UNVERIFIED
    assert experiment_state(overview + " — REFUTED") is ExperimentState.REFUTED


# ---- whole waves against the fakes ----


def _seed(kiln, split: str, rc_id: str, passes: set[int], n: int = 8) -> None:
    key = ("eval_1", rc_id, split)
    rows = [make_run(f"{split[0]}{i}", accuracy=float(i in passes)) for i in range(n)]
    if rc_id == "rc_base" and split == "train":
        kiln.results[key] = rows
    else:
        kiln.pending[key] = rows


def _scenario(ctx, *, train_fixed: int, val_fixed: int, candidates=("rc_cand",)):
    """One wave. The baseline fails items 0..3 of 8 on train, and items
    0..4 of 8 on val. Each candidate fixes the first `train_fixed` of them
    on train and the first `val_fixed` on val. (An arm that fixes fewer than
    half of the parent's train failures does not reach the analysis: the
    `residual` stop rule ends it.)"""
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    train_base, base = {4, 5, 6, 7}, {5, 6, 7}
    _seed(kiln, "train", "rc_base", train_base)
    _seed(kiln, "val", "rc_base", base)
    items = []
    for index, rc_id in enumerate(candidates, start=1):
        seed_issue(kiln, f"issue_{index}")
        _seed(kiln, "train", rc_id, train_base | set(range(train_fixed)))
        _seed(kiln, "val", rc_id, base | set(range(val_fixed)))
        items.append(fix_item(f"w1-{index}", issue_memory_id=f"issue_{index}"))
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(*items)), ok(make_plan(loop_exit="done")))
    d.queue(
        "create_experiment_rc",
        *[rc_ok(rc_id) for rc_id in candidates],
    )
    d.queue("analyze_experiment_results", *([ok(make_result())] * len(candidates)))


def _events(ctx, kind: str) -> list[dict]:
    return [f for k, f in ctx.log.events if k == kind]


async def test_wave_a_train_gain_on_two_items_is_unverified_and_skips_the_gate():
    ctx = make_ctx(gate_agent="auto")
    _scenario(ctx, train_fixed=LIMIT - 1, val_fixed=5)

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    # The analyzer said "successful". Code says unverified: no gate.
    (rule,) = _events(ctx, "success_rule")
    assert rule["analyzer_successful"] is True
    assert rule["rule_ok"] is False
    assert rule["unverified"].startswith("unverified: primary target eval_1")
    assert not _events(ctx, "promotion")
    assert not [job for job in ctx.kiln.job_invocations if "val" in job]
    (record,) = _experiment_memories(ctx.kiln)
    assert record["overview"].endswith(" — UNVERIFIED")
    assert "— REFUTED" not in record["overview"]
    assert "\nverdict: unverified: primary target eval_1" in record["content"]
    assert "2 of 8 paired items differ, 4 had room" in record["content"]
    (done,) = _events(ctx, "item_outcome")
    assert done["outcome"].result.successful is False
    assert done["outcome"].result.unverified_reason == rule["unverified"]
    assert done["outcome"].result.findings.endswith(rule["unverified"])


async def test_wave_a_train_gain_on_three_items_goes_to_the_gate():
    ctx = make_ctx(gate_agent="auto")
    _scenario(ctx, train_fixed=LIMIT, val_fixed=5)
    ctx.kiln.mcnemar_response = {**ctx.kiln.mcnemar_response, "ci_low": 0.1}

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    (rule,) = _events(ctx, "success_rule")
    assert rule["rule_ok"] is True
    assert rule["unverified"] is None
    (promo,) = _events(ctx, "promotion")
    assert promo["winner_rc_id"] == "rc_cand"


async def test_wave_a_val_gain_on_two_items_is_not_promoted_and_not_a_loss():
    ctx = make_ctx(gate_agent="auto")
    _scenario(ctx, train_fixed=4, val_fixed=LIMIT - 1)
    # The interval lies above zero: before the rule, code promoted on it.
    ctx.kiln.mcnemar_response = {**ctx.kiln.mcnemar_response, "ci_low": 0.1}

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert ctx.dispatch.calls_to("promote_best") == []
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]
    (promo,) = _events(ctx, "promotion")
    assert promo["winner_rc_id"] is None
    assert promo["rationale"].startswith("code rule: no winner — unverified: val")
    (record,) = _experiment_memories(ctx.kiln)
    assert record["overview"].endswith(" — REJECTED AT VAL 07-22")
    assert record["content"].endswith(
        "— unverified at val: too few val items differ from the baseline, so val"
        " shows no win and no loss."
    )
    (recorded,) = _events(ctx, "rejection_recorded")
    assert recorded["reason"] == "unverified"
    # The val readers see the mark: the gate's tables and its event.
    (emitted,) = _events(ctx, "gate_val_tables")
    assert emitted["tables"][0]["unverified"].startswith("unverified: val")
    (flagged,) = _events(ctx, "gate_unverified")
    assert list(flagged["unverified"]) == ["rc_cand"]


async def test_wave_a_val_gain_on_three_items_is_promoted():
    ctx = make_ctx(gate_agent="auto")
    _scenario(ctx, train_fixed=4, val_fixed=LIMIT)
    ctx.kiln.mcnemar_response = {**ctx.kiln.mcnemar_response, "ci_low": 0.1}

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    (promo,) = _events(ctx, "promotion")
    assert promo["winner_rc_id"] == "rc_cand"
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]


async def test_wave_the_agent_is_not_asked_when_every_candidate_is_unverified():
    ctx = make_ctx()  # gate_agent="always": the agent would decide
    _scenario(ctx, train_fixed=4, val_fixed=1, candidates=("rc_a", "rc_b"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert ctx.dispatch.calls_to("promote_best") == []
    (rule,) = _events(ctx, "promotion_rule")
    assert rule["unverified"] is True
    (flagged,) = _events(ctx, "gate_unverified")
    assert sorted(flagged["unverified"]) == ["rc_a", "rc_b"]
    records = _experiment_memories(ctx.kiln)
    assert len(records) == 2
    for record in records:
        assert "— unverified at val:" in record["content"]
    assert {f["reason"] for f in _events(ctx, "rejection_recorded")} == {"unverified"}


async def test_wave_the_agent_ranks_only_the_candidates_with_a_verdict():
    ctx = make_ctx()
    _scenario(ctx, train_fixed=4, val_fixed=LIMIT, candidates=("rc_a", "rc_b"))
    # rc_b fixes one val item only.
    _seed(ctx.kiln, "val", "rc_b", {5, 6, 7, 0})
    ctx.dispatch.queue("promote_best", decision(None, rationale="not enough"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    (request,) = ctx.dispatch.calls_to("promote_best")
    assert [t.rc_id for t in request.candidates] == ["rc_a"]
    by_rc = {f["rc_id"]: f["reason"] for f in _events(ctx, "rejection_recorded")}
    # The fake serves p=0.03 for every comparison, so rc_a's gain on three
    # items is beyond doubt, and code cannot tell why the agent passed it
    # over. rc_b, on one item, is unverified.
    assert by_rc == {"rc_a": "not promoted", "rc_b": "unverified"}


async def test_wave_the_agent_cannot_promote_a_candidate_it_was_not_given():
    # The agent can still name an unverified rc (from the val summary or
    # memory). Code refuses it like a holed winner: no promotion, no stamps.
    ctx = make_ctx()
    _scenario(ctx, train_fixed=4, val_fixed=LIMIT, candidates=("rc_a", "rc_b"))
    _seed(ctx.kiln, "val", "rc_b", {5, 6, 7, 0})
    ctx.dispatch.queue(
        "promote_best",
        decision("rc_b", winner_experiment_memory_id="whatever"),
    )

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]
    (blocked,) = _events(ctx, "promotion_blocked")
    assert blocked["winner_rc_id"] == "rc_b"
    assert blocked["reasons"][0].startswith("unverified: val")
    assert not _events(ctx, "rejection_recorded")


async def test_wave_an_unverified_candidate_is_not_passed_over_for_the_winner():
    ctx = make_ctx()
    _scenario(ctx, train_fixed=4, val_fixed=LIMIT, candidates=("rc_a", "rc_b"))
    _seed(ctx.kiln, "val", "rc_b", {5, 6, 7, 0})
    ctx.dispatch.queue("promote_best", decision("rc_a"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_a"]
    (recorded,) = _events(ctx, "rejection_recorded")
    assert (recorded["rc_id"], recorded["reason"]) == ("rc_b", "unverified")


@pytest.mark.parametrize(
    ("veto_reason", "stamp"),
    [
        (None, " — UNVERIFIED"),
        ("prediction_miss", " — UNVERIFIED"),
        ("secondary_regression", " — REFUTED"),
        ("dominated_by_ingredient", " — REFUTED"),
    ],
)
async def test_wave_an_analyzer_veto_of_its_own_stays_refuted(veto_reason, stamp):
    ctx = make_ctx(gate_agent="auto")
    _scenario(ctx, train_fixed=LIMIT - 1, val_fixed=5)
    ctx.dispatch.queues["analyze_experiment_results"] = [
        ok(make_result(successful=False, veto_reason=veto_reason))
    ]

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    (record,) = _experiment_memories(ctx.kiln)
    assert record["overview"].endswith(stamp)
    assert ("\nverdict: unverified:" in record["content"]) is (stamp == " — UNVERIFIED")
    (done,) = _events(ctx, "item_outcome")
    assert (done["outcome"].result.unverified_reason is None) is (stamp == " — REFUTED")
