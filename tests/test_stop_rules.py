"""The measurement engine's stop rules:
each rule on hand-built evidence, the defaults per objective, the planner's
overrides, and rules inside a screening round."""

from __future__ import annotations

import pytest
from test_viability import EVALS, GEMINI, _ctx, _judge, _request

from kiln_harness_tuner.estimate import FAIL, NAMED, PASS, Judged
from kiln_harness_tuner.measure import _residual_result
from kiln_harness_tuner.schemas import BatchPlanInput, NoiseRow, StopRule
from kiln_harness_tuner.stop_rules import (
    GUARD_NOISE_DEFAULT,
    GUARD_NOISE_FLOOR,
    Evidence,
    below_rate_p,
    check,
    default_stop_rules,
    first_stop,
    guard_noise_rate,
    merge_rules,
    noise_flip_rate,
    quality_looks,
    rate_upper_bound,
    rule_text,
    worse_sign_p,
)
from kiln_harness_tuner.viability import run_screening_round

# ------------------------------------------------------------------ schema


class TestTheSchema:
    def test_there_is_no_promote_rule(self):
        with pytest.raises(ValueError):
            StopRule.model_validate({"kind": "promote_early", "threshold": 0.9})

    def test_well_formed(self):
        with pytest.raises(ValueError, match="alpha"):
            StopRule(kind="eval_metric", threshold=0.5)
        with pytest.raises(ValueError, match="0 or negative"):
            StopRule(kind="eval_metric", threshold=0.1, vs_baseline=True, alpha=0.05)
        with pytest.raises(ValueError, match="eval_metric rules only"):
            StopRule(kind="latency_ratio", threshold=1.1, vs_baseline=True)
        with pytest.raises(ValueError, match="takes no `alpha`"):
            StopRule(kind="futility", alpha=0.05)
        with pytest.raises(ValueError, match="share"):
            StopRule(kind="residual", threshold=0)
        # Timing kinds may carry alpha: then they are tests.
        assert StopRule(kind="latency_ratio", threshold=1.1, alpha=0.05).alpha

    def test_a_round_carries_rules_and_looks(self):
        request = _request(
            GEMINI,
            stop_rules=[{"kind": "cost_ratio", "threshold": 1.2, "min_n": 4}],
            looks=4,
        )
        assert request.stop_rules[0].kind == "cost_ratio"
        assert _request(GEMINI).stop_rules is None  # = code's defaults

    async def test_the_planner_is_told_the_defaults(self, tmp_path):
        from conftest import make_plan
        from fake_dispatcher import ok

        from kiln_harness_tuner.orchestrator import orchestrate

        ctx = _ctx(tmp_path, objective="cost")
        ctx.opening_screen = False
        ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
        await orchestrate(ctx, "g", timeout_mins=180)
        (call,) = ctx.dispatch.calls_to("batch_plan")
        assert isinstance(call, BatchPlanInput)
        assert [r.kind for r in call.default_stop_rules] == [
            "invalid_output_rate",
            "guard_losses",
            "cost_ratio",
            "not_better",
        ]


class TestTheDefaults:
    @pytest.mark.parametrize(
        ("objective", "extra"),
        [
            ("quality", ["eval_metric"]),
            ("speed", ["guard_losses", "latency_ratio", "not_better"]),
            ("cost", ["guard_losses", "cost_ratio", "not_better"]),
        ],
    )
    def test_per_objective(self, objective, extra):
        kinds = [r.kind for r in default_stop_rules(objective)]
        assert kinds == ["invalid_output_rate", *extra]

    def test_an_arm_adds_its_gates_and_the_futility_bound(self):
        kinds = [
            r.kind for r in default_stop_rules("quality", full=True, residual=True)
        ]
        assert kinds == ["invalid_output_rate", "eval_metric", "residual", "futility"]
        named = [
            r.kind for r in default_stop_rules("quality", targets=True, residual=True)
        ]
        assert "targets" in named and "residual" not in named

    def test_the_planners_rules_replace_by_kind_and_add_the_rest(self):
        defaults = default_stop_rules("speed")
        mine = [
            StopRule(kind="latency_ratio", threshold=0.8, min_n=10, alpha=0.1),
            StopRule(kind="max_items", threshold=24),
        ]
        merged = merge_rules(defaults, mine)
        ratio = [r for r in merged if r.kind == "latency_ratio"]
        assert len(ratio) == 1 and ratio[0].threshold == 0.8
        assert merged[-1].kind == "max_items"


def test_the_exact_tests():
    assert worse_sign_p(6, 0) == pytest.approx(1 / 64)
    assert worse_sign_p(0, 0) == 1.0
    assert below_rate_p(0, 6, 0.5) == pytest.approx(1 / 64)


def test_the_quality_looks_are_geometric():
    assert quality_looks(240) == [12, 24, 48, 96, 192]
    assert quality_looks(20) == [12]
    assert quality_looks(5) == [12]


# ------------------------------------------------------------ evidence


def _j(eval_id, item, stratum, parent, cand, block="remainder"):
    return Judged(eval_id, item, stratum, block, parent, cand)


def _ev(rows, population, **kw):
    ev = Evidence(judged=list(rows), population=population, **kw)
    ev.quality_look = kw.get("quality_look", True)
    return ev


WORSE = StopRule(
    kind="eval_metric", threshold=0.0, vs_baseline=True, alpha=0.05, min_n=6
)


class TestQualityClearlyWorse:
    def _broke(self, n_broke, n_same=0):
        rows = [_j("e1", f"p{i}", PASS, True, False) for i in range(n_broke)]
        rows += [_j("e1", f"q{i}", PASS, True, True) for i in range(n_same)]
        rows += [_j("e1", f"f{i}", FAIL, False, False) for i in range(4)]
        return rows

    def test_stops_on_clear_breaks_and_names_the_numbers(self):
        ev = _ev(self._broke(8), {"e1": {FAIL: 20, PASS: 60}})
        hit = check(WORSE, ev)
        assert hit is not None and "broke 8" in hit.numbers
        assert "0.05 per look over 1 looks" in rule_text(WORSE, ev)

    def test_the_alpha_is_split_over_the_looks(self):
        ev = _ev(self._broke(3, 3), {"e1": {FAIL: 20, PASS: 60}}, quality_looks=1)
        split = _ev(self._broke(3, 3), {"e1": {FAIL: 20, PASS: 60}}, quality_looks=50)
        assert (check(WORSE, ev) is None) or (check(WORSE, split) is None)
        assert check(WORSE, split) is None

    def test_only_at_a_look_and_after_min_n(self):
        ev = _ev(self._broke(8), {"e1": {FAIL: 20, PASS: 60}}, quality_look=False)
        assert check(WORSE, ev) is None
        few = _ev(self._broke(1)[:3], {"e1": {FAIL: 20, PASS: 60}})
        assert check(WORSE, few) is None

    def test_fixes_on_the_parents_failures_are_not_a_regression(self):
        rows = [_j("e1", f"f{i}", FAIL, False, True) for i in range(8)]
        rows += [_j("e1", f"p{i}", PASS, True, True) for i in range(4)]
        assert check(WORSE, _ev(rows, {"e1": {FAIL: 20, PASS: 60}})) is None

    def test_an_absolute_pass_rate(self):
        rule = StopRule(kind="eval_metric", threshold=0.9, alpha=0.05, min_n=6)
        rows = [_j("e1", f"p{i}", PASS, True, False) for i in range(10)]
        assert check(rule, _ev(rows, {"e1": {PASS: 10}})) is not None


class TestFutility:
    def test_quality_needs_the_primary_eval_to_improve(self):
        rows = [_j("e1", "f1", FAIL, False, False), _j("e1", "p1", PASS, True, False)]
        rule = StopRule(kind="futility")
        # One parent failure left: the best end is 0 net — it cannot improve.
        ev = _ev(
            rows,
            {"e1": {FAIL: 2, PASS: 1}},
            remaining={"e1": {FAIL: 1}},
            primary_eval="e1",
            full=True,
        )
        hit = check(rule, ev)
        assert hit is not None and "at most +0 net" in hit.numbers
        # Two failures left: +1 is still possible.
        ev.remaining = {"e1": {FAIL: 2}}
        assert check(rule, ev) is None
        # Never on a capped round (the queue is not the whole split).
        ev.remaining, ev.full = {"e1": {FAIL: 1}}, False
        assert check(rule, ev) is None

    def test_speed_needs_every_eval_within_its_allowance(self):
        rows = [_j("e1", f"p{i}", PASS, True, False) for i in range(4)]
        ev = _ev(
            rows,
            {"e1": {PASS: 10}},
            remaining={"e1": {PASS: 6}},
            full=True,
            objective="speed",
        )
        hit = check(StopRule(kind="futility"), ev)
        assert hit is not None and "allowance 1 of 10" in hit.numbers


class TestTheResidualBlock:
    def _block(self, fixed, broke, fails=8, guards=4):
        rows = [
            _j("e1", f"f{i}", FAIL, False, i < fixed, "discriminating")
            for i in range(fails)
        ]
        rows += [
            _j("e1", f"p{i}", PASS, True, i >= broke, "discriminating")
            for i in range(guards)
        ]
        return rows

    def _rule(self, noise_rate=None):
        (rule,) = [
            r
            for r in default_stop_rules(
                "quality", full=True, residual=True, noise_rate=noise_rate
            )
            if r.kind == "residual"
        ]
        return rule

    def _fires(self, rule, broke, guards=4, fixed=8):
        ev = _ev(self._block(fixed, broke, guards=guards), {}, disc_done=True)
        return check(rule, ev) is not None

    def test_half_the_failures(self):
        # The fix half is unchanged: fewer than half fixed stops the arm,
        # with no guard broken.
        rule = self._rule()
        ok = _ev(self._block(4, 0), {}, disc_done=True)
        assert check(rule, ok) is None
        hit = check(rule, _ev(self._block(3, 0), {}, disc_done=True))
        assert hit is not None
        assert hit.numbers == (
            "residual: 3/8 fixed (needed 4), 0 of 4 guards broke, within noise"
            " (p=1) against a noise rate of 0.10 at alpha 0.05"
        )
        # And with a guard broken within noise: the fix half still fires.
        assert self._fires(rule, 1, fixed=3)
        # Only once the block is judged.
        assert check(rule, _ev(self._block(0, 0), {}, disc_done=False)) is None

    def test_one_broken_guard_of_four_is_within_noise(self):
        # 8 of 8 fixed and 1 of 4 guards broke is no stop at the
        # default noise rate (p = 1 - 0.9^4 = 0.34).
        rule = self._rule()
        assert rule.alpha == 0.05 and rule.noise_rate == GUARD_NOISE_DEFAULT
        assert check(rule, _ev(self._block(8, 1), {}, disc_done=True)) is None

    def test_breaks_clearly_above_noise_fire(self):
        rule = self._rule()
        hit = check(rule, _ev(self._block(8, 3), {}, disc_done=True))
        assert hit is not None
        assert hit.numbers == (
            "residual: 8/8 fixed (needed 4), 3 of 4 guards broke, clearly above"
            " noise (p=0.0037) against a noise rate of 0.10 at alpha 0.05"
        )
        # The fewest breaks that fire at the defaults, per number of guards:
        # 2 of 2 (p=0.01), 2 of 3 (p=0.028), 3 of 4 (p=0.0037); 2 of 4 gives
        # p=0.052 and does not.
        fewest = {
            n: min(k for k in range(n + 1) if self._fires(rule, k, guards=n))
            for n in (2, 3, 4)
        }
        assert fewest == {2: 2, 3: 2, 4: 3}

    def test_the_measured_noise_rate_is_used_and_floored(self):
        # A quiet task (1 change in 50): 2 of 4 breaks are clearly above it,
        # while the default rate lets them pass; 1 of 4 is still noise.
        quiet = self._rule(noise_flip_rate(_noise_rows(1, 50)))
        assert quiet.noise_rate == pytest.approx(0.02)
        assert self._fires(quiet, 2) and not self._fires(self._rule(), 2)
        assert not self._fires(quiet, 1)
        # A noisy task (17 in 100): 2 of 4 are still noise, 3 of 4 are not.
        noisy = self._rule(noise_flip_rate(_noise_rows(17, 100)))
        assert not self._fires(noisy, 2) and self._fires(noisy, 3)
        # A check that saw no change is floored.
        assert self._rule(0.0).noise_rate == GUARD_NOISE_FLOOR
        assert self._fires(self._rule(0.0), 2) and not self._fires(self._rule(0.0), 1)

    def test_a_guard_with_no_row_is_not_counted(self):
        rows = self._block(3, 1, guards=3)
        rows.append(_j("e1", "p9", PASS, True, None, "discriminating"))
        hit = check(self._rule(), _ev(rows, {}, disc_done=True))
        assert hit is not None  # the fix half: 3 of 8 fixed
        assert "1 of 3 guards broke, within noise (p=0.27)" in hit.numbers

    def test_the_planner_rule_reads_the_run_noise(self):
        defaults = default_stop_rules(
            "quality", full=True, residual=True, noise_rate=0.17
        )
        (merged,) = [
            r
            for r in merge_rules(defaults, [StopRule(kind="residual", threshold=0.75)])
            if r.kind == "residual"
        ]
        assert merged.threshold == 0.75
        assert merged.alpha == 0.05 and merged.noise_rate == 0.17
        # A rule with no default to read is tested at the default rate.
        bare = StopRule(kind="residual", threshold=0.5)
        assert not self._fires(bare, 1) and self._fires(bare, 3)

    def test_the_rule_and_record_text(self):
        rule = self._rule()
        ev = _ev(self._block(8, 1), {}, disc_done=True)
        assert rule_text(rule, ev) == (
            "residual: at least 50% of the parent's failures in the"
            " discriminating block fixed, and the guard breaks not clearly"
            " above the noise rate 0.10 (one-sided exact binomial test at alpha"
            " 0.05, one look)"
        )
        gate = _residual_result(ev, "seed", [rule])
        assert gate is not None and gate.passed
        assert gate.summary == ("8/8 fixed, 1 of 4 guards broke, within noise (p=0.34)")
        gate = _residual_result(
            _ev(self._block(8, 3), {}, disc_done=True), "seed", [rule]
        )
        assert gate is not None and not gate.passed
        assert gate.summary == (
            "8/8 fixed, 3 of 4 guards broke, clearly above noise (p=0.0037)"
        )

    def test_the_schema(self):
        assert StopRule(kind="residual", threshold=0.5, alpha=0.05, noise_rate=0.1)
        with pytest.raises(ValueError, match="residual rules only"):
            StopRule(kind="futility", noise_rate=0.1)
        with pytest.raises(ValueError):
            StopRule(kind="residual", threshold=0.5, noise_rate=1.0)

    def test_under_speed_no_net_loss(self):
        rule = StopRule(kind="residual", threshold=0.5)
        ev = _ev(self._block(1, 1), {}, disc_done=True, objective="speed")
        assert check(rule, ev) is None
        ev = _ev(self._block(0, 1), {}, disc_done=True, objective="speed")
        assert check(rule, ev) is not None


def test_named_targets_must_all_pass():
    rows = [
        _j("e1", "a", NAMED, False, True, "named"),
        _j("e1", "b", NAMED, False, False, "named"),
    ]
    hit = check(StopRule(kind="targets"), _ev(rows, {}, named_done=True))
    assert hit is not None and "1/2 named items pass; still failing b" in hit.numbers


class TestTheTimingRules:
    def _pairs(self, ratios):
        ref = {f"i{n}": 1000.0 for n in range(len(ratios))}
        cand = {f"i{n}": 1000.0 * r for n, r in enumerate(ratios)}
        return ref, cand

    def test_a_paired_test_with_an_n_floor(self):
        rule = StopRule(kind="latency_ratio", threshold=1.0, min_n=6, alpha=0.05)
        ref, cand = self._pairs([1.3] * 3)
        # Three items 1.3× slower: never a stop (the floor).
        ev = _ev([], {}, latency=cand, ref_latency=ref, timing_look=True)
        assert check(rule, ev) is None
        ref, cand = self._pairs([1.3] * 8)
        ev = _ev(
            [], {}, latency=cand, ref_latency=ref, timing_look=True, timing_looks=2
        )
        hit = check(rule, ev)
        assert hit is not None and "8 of 8 above" in hit.numbers

    def test_a_ratio_inside_the_threshold_does_not_fire(self):
        rule = StopRule(kind="latency_ratio", threshold=1.1, min_n=6, alpha=0.05)
        ref, cand = self._pairs([1.05] * 10)  # slower, inside the threshold
        ev = _ev([], {}, latency=cand, ref_latency=ref, timing_look=True)
        assert check(rule, ev) is None

    def test_without_alpha_a_kill_line(self):
        rule = StopRule(kind="cost_ratio", threshold=1.0, min_n=3)
        ref, cand = self._pairs([1.2] * 3)
        ev = _ev([], {}, cost=cand, ref_cost=ref)
        assert check(rule, ev) is not None

    def test_not_better_once_the_timing_is_done(self):
        rule = StopRule(kind="not_better")
        ref, cand = self._pairs([1.0] * 30)
        ev = _ev(
            [], {}, latency=cand, ref_latency=ref, objective="speed", timing_planned=30
        )
        assert check(rule, ev) is None  # not done yet
        ev.timing_done = True
        assert check(rule, ev) is not None
        ref, cand = self._pairs([0.8] * 30)
        ev = _ev(
            [],
            {},
            latency=cand,
            ref_latency=ref,
            objective="speed",
            timing_planned=30,
            timing_done=True,
        )
        assert check(rule, ev) is None

    def test_no_speed_verdict_on_too_few_pairs(self):
        rule = StopRule(kind="not_better")
        ref, cand = self._pairs([1.0] * 3)
        ev = _ev(
            [],
            {},
            latency=cand,
            ref_latency=ref,
            objective="speed",
            timing_planned=3,
            timing_done=True,
        )
        assert check(rule, ev) is None

    def test_invalid_output_and_budgets(self):
        assert check(
            StopRule(kind="invalid_output_rate", threshold=0.0),
            _ev([], {}, outputs=4, invalid=1),
        )
        rows = [_j("e1", f"p{i}", PASS, True, True) for i in range(5)]
        budget = check(StopRule(kind="max_items", threshold=5), _ev(rows, {}))
        assert budget is not None and budget.budget
        dollars = check(
            StopRule(kind="max_usd", threshold=1.0), _ev([], {}, spend_usd=1.5)
        )
        assert dollars is not None and dollars.budget

    def test_a_verdict_wins_over_a_budget(self):
        rules = [
            StopRule(kind="max_items", threshold=1),
            StopRule(kind="invalid_output_rate", threshold=0.0),
        ]
        rows = [_j("e1", "p", PASS, True, True)]
        hit = first_stop(rules, _ev(rows, {}, outputs=1, invalid=1))
        assert hit is not None and hit.rule.kind == "invalid_output_rate"


# ------------------------------------------------------------ in a round


class TestInARound:
    async def test_a_budget_caps_without_a_dead_end(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        request = _request(
            GEMINI,
            per_eval_items=6,
            stop_rules=[StopRule(kind="max_items", threshold=4)],
            looks=3,
        )
        (result,) = await run_screening_round(ctx, request, "rc_base", 1)
        assert result.verdict == "survivor"
        assert result.items == 4

    async def test_a_latency_rule_stops_a_slow_candidate(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        request = _request(
            GEMINI,
            stop_rules=[StopRule(kind="latency_ratio", threshold=0.01, min_n=3)],
        )
        (result,) = await run_screening_round(ctx, request, "rc_base", 1)
        assert result.verdict == "dead_end"
        assert "latency_ratio" in result.reason


# ------------------------------------------- losses under speed and cost


def _guards(lost, n, *, fixes=0, fails=0, fragility=0.0, **kw):
    """`n` judged baseline passes, the first `lost` of them lost, and
    `fails` baseline failures, the first `fixes` of them fixed."""
    rows = [
        Judged("e1", f"p{i}", PASS, "discriminating", True, i >= lost, fragility)
        for i in range(n)
    ]
    rows += [
        Judged("e1", f"f{i}", FAIL, "discriminating", False, i < fixes)
        for i in range(fails)
    ]
    return _ev(rows, {}, objective="speed", quality_looks=3, **kw)


def _loss_rule(noise_rate=None):
    (rule,) = [
        r
        for r in default_stop_rules("speed", noise_rate=noise_rate)
        if r.kind == "guard_losses"
    ]
    return rule


def _noise_rows(changed, items, score_type="pass_fail", eval_id="e1"):
    return [
        NoiseRow(
            eval_id=eval_id,
            score_key="accuracy",
            score_type=score_type,
            baseline_rc_id="rc_base",
            items=items,
            redrive_changed=changed,
        )
    ]


class TestGuardLosses:
    """Under speed and cost the quality verdict counts losses on
    the baseline's passes against the noise flip rate."""

    def test_against_the_default_noise_rate(self):
        rule = _loss_rule()
        assert rule.threshold == GUARD_NOISE_DEFAULT == 0.10
        # At 0.10 and alpha 0.05 over 3 looks, 24 guards need 7 losses.
        assert check(rule, _guards(6, 24)) is None
        hit = check(rule, _guards(7, 24))
        assert hit is not None
        assert hit.numbers.startswith(
            "lost 7 of 24 baseline passes (29%) against a noise rate of 0.10"
        )
        assert "losses on the baseline's passes clearly above the noise rate" in (
            rule_text(rule, _guards(7, 24))
        )

    def test_against_the_measured_noise_rate(self):
        measured = noise_flip_rate(_noise_rows(1, 50))
        assert measured == pytest.approx(0.02)
        rule = _loss_rule(measured)
        assert rule.threshold == pytest.approx(0.02)
        # A quiet task: 3 losses of 24 are clearly above its noise, while the
        # default rate lets them pass.
        assert check(rule, _guards(3, 24)) is not None
        assert check(_loss_rule(), _guards(3, 24)) is None

    def test_the_noise_rate_comes_from_the_targeted_pass_fail_rows(self):
        rows = [
            *_noise_rows(6, 12, eval_id="e1"),
            *_noise_rows(0, 12, eval_id="e2"),
            *_noise_rows(9, 12, score_type="five_star", eval_id="e1"),
        ]
        assert noise_flip_rate(rows) == pytest.approx(6 / 24)
        assert noise_flip_rate(rows, ["e2"]) == 0.0
        assert noise_flip_rate(rows, ["e3"]) is None
        assert noise_flip_rate([]) is None
        # A check that saw no change does not show a zero rate.
        assert guard_noise_rate(0.0) == GUARD_NOISE_FLOOR
        assert guard_noise_rate(None) == GUARD_NOISE_DEFAULT

    def test_fixes_never_offset_losses(self):
        rule = _loss_rule()
        plain = check(rule, _guards(7, 24))
        fixed = check(rule, _guards(7, 24, fixes=8, fails=8))
        assert plain is not None and fixed is not None
        assert plain.n == fixed.n == 24
        assert "fixes not counted: 8 of 8" in fixed.numbers
        # The same evidence read on the net (the quality rule) is no stop:
        # the eight fixes outweigh the seven losses.
        ev = _guards(7, 24, fixes=8, fails=8)
        ev.population = {"e1": {FAIL: 8, PASS: 24}}
        assert check(WORSE, ev) is None
        # And six losses stay below the bar whatever the fixes.
        assert check(rule, _guards(6, 24, fixes=0, fails=8)) is None

    def test_a_fragile_guard_raises_the_null(self):
        rule = _loss_rule()
        # Guards that half the other configs fail flip often by noise too.
        assert check(rule, _guards(7, 24, fragility=0.5)) is None
        assert check(rule, _guards(20, 24, fragility=0.5)) is not None

    def test_only_at_a_look_and_after_min_n(self):
        rule = _loss_rule()
        assert check(rule, _guards(5, 5)) is None  # QUALITY_MIN_N = 6
        assert check(rule, _guards(12, 12, quality_look=False)) is None

    def test_the_loss_bound(self):
        assert rate_upper_bound(0, 24) == pytest.approx(0.117, abs=0.001)
        assert rate_upper_bound(0, 12) == pytest.approx(0.221, abs=0.001)
        assert rate_upper_bound(0, 40) == pytest.approx(0.072, abs=0.001)
        assert rate_upper_bound(3, 3) == 1.0
        ev = _guards(1, 24, fixes=1, fails=2)
        assert ev.loss_text() == (
            "lost 1 of 24 baseline passes (no loss rate above 18% at 95%) · fixed"
            " 1 of 2 baseline failures (noise level)"
        )

    def test_the_schema(self):
        with pytest.raises(ValueError, match="alpha"):
            StopRule(kind="guard_losses", threshold=0.1)
        with pytest.raises(ValueError, match="pooled"):
            StopRule(kind="guard_losses", threshold=0.1, alpha=0.05, eval_id="e1")
        with pytest.raises(ValueError, match="noise flip rate"):
            StopRule(kind="guard_losses", threshold=1.0, alpha=0.05)

    def test_no_net_rule_under_speed_and_cost(self):
        for objective in ("speed", "cost"):
            kinds = [
                r.kind for r in default_stop_rules(objective, full=True, residual=True)
            ]
            assert "residual" not in kinds and "eval_metric" not in kinds
            assert "guard_losses" in kinds
        quality = [
            r.kind for r in default_stop_rules("quality", full=True, residual=True)
        ]
        assert quality == ["invalid_output_rate", "eval_metric", "residual", "futility"]
