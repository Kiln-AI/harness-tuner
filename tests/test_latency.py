"""Latency measurement: per-item wall-clock time, compared paired
on the same items — the median by a sign test, the tail by a p90 bootstrap."""

from __future__ import annotations

import math

import pytest

from kiln_harness_tuner.latency import (
    MIN_PAIRS,
    clearly_slower,
    compare_eval_runs,
    compare_latencies,
    item_latencies,
    latency_line,
    latency_of_eval_run,
    latency_of_task_run,
    percentile,
    sign_test_p,
)


def _items(values: list[float]) -> dict[str, float]:
    return {f"i{n:02d}": v for n, v in enumerate(values)}


def test_sign_test_matches_the_binomial():
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(5, 0) == pytest.approx(2 / 32)  # 0.0625, not significant
    assert sign_test_p(6, 0) == pytest.approx(2 / 64)
    # 21 of 30 one way: 2 * P(X <= 9) for Binomial(30, 1/2). 20 of 30 is
    # NOT enough (p = 0.099).
    tail = sum(math.comb(30, k) for k in range(10)) / 2**30
    assert sign_test_p(21, 9) == pytest.approx(2 * tail)
    assert sign_test_p(21, 9) < 0.05 < sign_test_p(20, 10)
    assert sign_test_p(3, 3) == 1.0


def test_percentile_is_linear_and_inclusive():
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile([0, 10], 0.9) == pytest.approx(9.0)
    assert percentile([7], 0.9) == 7
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_a_task_run_is_timed_by_its_own_usage():
    assert latency_of_task_run({"usage": {"total_llm_latency_ms": 1200}}) == 1200.0
    assert latency_of_task_run({"usage": {"total_llm_latency_ms": None}}) is None
    assert latency_of_task_run({"usage": {"total_llm_latency_ms": True}}) is None
    assert latency_of_task_run({}) is None


def test_an_eval_run_is_timed_by_the_task_never_the_judge():
    """The rule: speed is the task call. An eval run's judge usage — and
    any `usage` key a record might carry — is never read."""
    judged = {
        "task_run_usage": {"total_llm_latency_ms": 900},
        "eval_usage": {"total_llm_latency_ms": 5000},
        "usage": {"total_llm_latency_ms": 7000},
    }
    assert latency_of_eval_run(judged) == 900.0
    only_judge = {
        "eval_usage": {"total_llm_latency_ms": 5000},
        "usage": {"total_llm_latency_ms": 7000},
    }
    assert latency_of_eval_run(only_judge) is None
    assert (
        latency_of_task_run({"task_run_usage": {"total_llm_latency_ms": 900}}) is None
    )


def test_replicates_give_their_median():
    runs = [
        {"eval_input_id": "a", "task_run_usage": {"total_llm_latency_ms": v}}
        for v in (100, 300, 200)
    ]
    assert item_latencies(runs) == {"a": 200.0}


class TestClearlyFaster:
    def test_consistently_faster_is_clearly_faster(self):
        base = _items([1000 + 10 * n for n in range(30)])
        cand = {k: v * 0.6 for k, v in base.items()}
        c = compare_latencies(base, cand, method="paired_drive", split="val")
        assert c.clearly_faster and not c.p90_regressed
        assert c.median_ratio == pytest.approx(0.6)
        assert (c.faster_items, c.slower_items) == (30, 0)
        assert c.p90_ratio_ci_high is not None and c.p90_ratio_ci_high < 1

    def test_a_faster_median_with_a_worse_tail_is_not_clearly_faster(self):
        """The rule: stable wall-clock time. Faster on most items but
        with a quarter of them much slower must NOT count."""
        base = _items([1000.0] * 30)
        cand = {k: 800.0 for k in base}
        for k in list(cand)[-8:]:
            cand[k] = 5000.0  # a quarter of the items blow up
        c = compare_latencies(base, cand, method="paired_drive", split="val")
        assert c.faster_items == 22 and c.sign_test_p < 0.05
        assert c.median_ratio < 1
        assert c.p90_regressed
        assert not c.clearly_faster
        assert "p90" in c.note

    def test_noise_is_not_clearly_faster(self):
        base = _items([1000.0] * 20)
        cand = {k: (990.0 if n % 2 else 1010.0) for n, k in enumerate(base)}
        c = compare_latencies(base, cand, method="paired_drive", split="val")
        assert not c.clearly_faster

    def test_faster_on_more_items_but_not_significantly_is_not_clearly_faster(
        self,
    ):
        # 7 of 12 faster (by a lot), 5 slower (by a little): the median ratio
        # is below 1 and the tail is fine, but p = 0.77 — noise.
        base = _items([1000.0] * 12)
        cand = {k: (500.0 if n < 7 else 1010.0) for n, k in enumerate(base)}
        c = compare_latencies(base, cand, method="paired_drive", split="val")
        assert c.faster_items == 7 and c.median_ratio < 1
        assert not c.p90_regressed and c.sign_test_p > 0.05
        assert not c.clearly_faster

    def test_too_few_pairs_give_no_verdict(self):
        base = _items([1000.0] * (MIN_PAIRS - 1))
        cand = {k: 100.0 for k in base}
        c = compare_latencies(base, cand, method="paired_drive", split="train")
        assert not c.clearly_faster and c.p90_ratio_ci_low is None

    def test_only_items_on_both_sides_are_paired(self):
        base = _items([1000.0] * 10)
        cand = {"i00": 1.0, "other": 1.0}
        c = compare_latencies(base, cand, method="eval_runs", split="val")
        assert c.n_items == 1

    def test_no_pairs(self):
        c = compare_latencies({}, {"a": 1.0}, method="eval_runs", split="val")
        assert c.n_items == 0 and not c.clearly_faster

    def test_the_bootstrap_is_deterministic(self):
        base = _items([1000 + 37 * (n % 7) for n in range(25)])
        cand = {k: v * (0.7 + 0.02 * (n % 5)) for n, (k, v) in enumerate(base.items())}
        a = compare_latencies(base, cand, method="paired_drive", split="val")
        b = compare_latencies(base, cand, method="paired_drive", split="val")
        assert a == b


def test_the_passive_comparison_reads_eval_runs():
    def run(item, ms):
        return {"eval_input_id": item, "task_run_usage": {"total_llm_latency_ms": ms}}

    base = [run(f"i{n}", 1000) for n in range(8)]
    cand = [run(f"i{n}", 500) for n in range(8)]
    c = compare_eval_runs(base, cand, "train")
    assert c is not None and c.method == "eval_runs" and c.n_items == 8
    assert compare_eval_runs([{"eval_input_id": "a"}], [], "train") is None


def test_the_line_names_both_arms_and_the_tail():
    base = _items([1000.0] * 10)
    c = compare_latencies(
        base, {k: 500.0 for k in base}, method="paired_drive", split="val"
    )
    line = latency_line("speed", c)
    assert "1000→500 ms" in line and "p90" in line and "sign p=" in line
    assert "no latency" in latency_line("speed", None)


class TestClearlySlowerThanTheParent:
    """The parent check of an arm forked from a non-baseline
    parent. It needs six pairs, significance, and a median above 1.5×."""

    def _c(self, ratios):
        parent = _items([1000.0] * len(ratios))
        arm = _items([1000.0 * r for r in ratios])
        return compare_latencies(parent, arm, method="paired_drive", split="train")

    def test_every_condition(self):
        assert clearly_slower(self._c([3.0] * 8))
        assert not clearly_slower(self._c([1.3] * 8))  # under the 1.5× floor
        assert not clearly_slower(self._c([3.0] * 5))  # five pairs: never
        # Most items 3× slower, but the sign test is not convinced.
        assert not clearly_slower(self._c([3.0] * 5 + [0.5] * 3))
        assert not clearly_slower(None)

    def test_an_incomplete_pass_is_never_clearly_slower(self):
        parent = _items([1000.0] * 8)
        arm = _items([3000.0] * 8)
        c = compare_latencies(
            parent, arm, method="paired_drive", split="train", planned=30
        )
        assert c.incomplete and not clearly_slower(c)
