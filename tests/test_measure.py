"""The measurement engine and what is built on it: the queue,
the estimator, the sequential validity of its stop rules (simulated), the
paired timing path, a full loop through the fake Kiln, the retirement of the
paths it replaced, and one test for each guard that has no other.

The simulations are seeded, so each rate below is a fixed number, not a
flaky draw. `sim_*` are also what the report's tables were made with."""

from __future__ import annotations

import importlib
import json
import math
import random
import re
from pathlib import Path

import pytest
from conftest import make_plan, make_result
from fake_dispatcher import ok
from fake_kiln import make_run
from test_orchestrator import rc_ok
from test_speed_objective import _seed_rows, _speed_ctx
from test_viability import EVALS, _ctx

from kiln_harness_tuner.estimate import FAIL, PASS, Judged, estimate
from kiln_harness_tuner.latency import compare_latencies
from kiln_harness_tuner.measure import (
    BLOCK_DISC,
    BLOCK_NAMED,
    BLOCK_REST,
    DISC_FAILS,
    DISC_GUARDS,
    DISC_GUARDS_EFFICIENCY,
    QItem,
    _bounds,
    _interleave,
    cap_items,
    draw_queue,
    guard_cap,
    progress_line,
)
from kiln_harness_tuner.orchestrator import Ctx, orchestrate
from kiln_harness_tuner.schemas import (
    EvalTarget,
    StopRule,
)
from kiln_harness_tuner.stop_rules import (
    DEFAULT_ALPHA,
    QUALITY_MIN_N,
    TIMING_MIN_PAIRS,
    Evidence,
    check,
    default_stop_rules,
    quality_looks,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "kiln_harness_tuner"

# ------------------------------------------------------------------ queue


class TestTheQueue:
    async def test_hard_first_then_the_rest_every_item_once(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        targets = [EvalTarget(eval_id=e) for e in EVALS]
        q = await draw_queue(ctx, "rc_base", targets, "seed-a", named=["e2_5"])
        blocks = [x.block for x in q.items]
        # named, then the discriminating block, then the remainder
        assert blocks[0] == BLOCK_NAMED
        first_rest = blocks.index(BLOCK_REST)
        assert set(blocks[1:first_rest]) == {BLOCK_DISC}
        assert BLOCK_DISC not in blocks[first_rest:]
        disc = [x for x in q.items if x.block == BLOCK_DISC]
        fails = [x for x in disc if x.stratum == FAIL]
        guards = [x for x in disc if x.stratum == PASS]
        # e1 has one failure (e1_9) and e2 seven (e2_3..e2_9, less the
        # named e2_5): 7 fit under the cap of 8.
        assert len(fails) == min(DISC_FAILS, 1 + 6)
        assert all(not x.parent_pass for x in fails)
        assert len(guards) == DISC_GUARDS and all(x.parent_pass for x in guards)
        # Every train item of both evals, exactly once.
        keys = [(x.eval_id, x.item_id) for x in q.items]
        assert len(keys) == len(set(keys)) == 20
        # The population is the whole split, by the parent's stored result.
        assert q.population["e1"] == {FAIL: 1, PASS: 9, "unscored": 0, "named": 0}
        assert q.population["e2"] == {FAIL: 6, PASS: 3, "unscored": 0, "named": 1}

    async def test_seeded_and_recorded(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        targets = [EvalTarget(eval_id=e) for e in EVALS]
        a = await draw_queue(ctx, "rc_base", targets, "seed-a")
        b = await draw_queue(ctx, "rc_base", targets, "seed-a")
        c = await draw_queue(ctx, "rc_base", targets, "seed-b")
        assert a.items == b.items
        assert [x.item_id for x in a.items] != [x.item_id for x in c.items]
        # The blocks do not depend on the seed; only the order within them.
        assert [x.block for x in a.items] == [x.block for x in c.items]
        assert a.to_json()["seed"] == "seed-a"

    async def test_a_cap_keeps_the_timing_order_whole(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        targets = [EvalTarget(eval_id=e) for e in EVALS]
        q = await draw_queue(ctx, "rc_base", targets, "s", per_eval_cap=3)
        assert max(sum(1 for x in q.items if x.eval_id == e) for e in EVALS) == 3
        assert len(q.timing_order) == 20

    def test_the_remainder_keeps_each_strata_share_in_every_prefix(self):
        order = _interleave({FAIL: [f"f{n}" for n in range(3)], PASS: list("abcdef")})
        for k in range(1, len(order) + 1):
            fails = sum(1 for s, _i in order[:k] if s == FAIL)
            assert abs(fails - k / 3) < 1

    def test_the_chunk_bounds(self):
        assert _bounds(40, 12, None) == [12, 24, 36, 40]
        assert _bounds(40, 5, None) == [5, 17, 29, 40]
        assert _bounds(10, 5, 3) == [4, 8, 10]
        assert _bounds(0, 5, None) == []


def _stored(ctx, eval_id: str, rc_id: str, scores: dict[str, float]) -> None:
    """Rows of another run config on the eval's default config, on disk, the
    way Kiln stores them."""
    task = Path(ctx.target.project_path) / "tasks" / "t1 - T"
    (eval_dir,) = (task / "evals").glob(f"{eval_id} - *")
    config = eval_dir / "configs" / f"ec_{eval_id} - judge"
    config.mkdir(parents=True, exist_ok=True)
    (config / "eval_config.kiln").write_text(
        json.dumps({"id": f"ec_{eval_id}"}), encoding="utf-8"
    )
    for item_id, value in scores.items():
        run = config / "runs" / f"{rc_id}-{item_id}"
        run.mkdir(parents=True)
        (run / "eval_run.kiln").write_text(
            json.dumps(
                {
                    "id": f"{rc_id}-{item_id}",
                    "eval_input_id": item_id,
                    "task_run_config_id": rc_id,
                    "scores": {"accuracy": value},
                }
            ),
            encoding="utf-8",
        )


def _seeded_passes(seed: str, eval_id: str, passes: list[str], fails: list[str]):
    """The pass stratum in the queue's seeded order (`draw_queue`)."""
    rng = random.Random(f"{seed}:{eval_id}")
    f, p = sorted(fails), sorted(passes)
    rng.shuffle(f)
    rng.shuffle(p)
    return p


class TestTheGuardFirstQueue:
    """Under speed and cost the discriminating block is the
    baseline's passes, most fragile first, then its failures."""

    E1_PASS = [f"e1_{n}" for n in range(9)]
    E2_PASS = [f"e2_{n}" for n in range(3)]

    async def test_passes_first_by_fragility_then_the_failures(self, tmp_path):
        ctx = _ctx(tmp_path, objective="speed")
        _stored(ctx, "e1", "rc_a", {"e1_5": 0.0, "e1_7": 0.0, "e1_0": 1.0})
        _stored(ctx, "e1", "rc_b", {"e1_5": 0.0, "e1_7": 1.0, "e1_0": 1.0})
        _stored(ctx, "e2", "rc_a", {"e2_1": 0.0})
        # Not counted: a candidate of the round, the parent itself, and an
        # item outside the train split.
        _stored(ctx, "e1", "rc_cand", {"e1_2": 0.0})
        _stored(ctx, "e1", "rc_base", {"e1_3": 0.0})
        _stored(ctx, "e1", "rc_c", {"e1_heldout": 0.0})
        targets = [EvalTarget(eval_id=e) for e in EVALS]
        q = await draw_queue(
            ctx, "rc_base", targets, "s", objective="speed", exclude_rcs=["rc_cand"]
        )
        disc = [x for x in q.items if x.block == BLOCK_DISC]
        # Every pass (12 < DISC_GUARDS_EFFICIENCY), then up to DISC_FAILS fails.
        assert [x.stratum for x in disc] == [PASS] * 12 + [FAIL] * DISC_FAILS
        assert DISC_GUARDS_EFFICIENCY >= 12
        # Round-robin across the evals, the most fragile pass of each first:
        # e1_5 (2 of 2 configs fail it), e2_1 (1 of 1), e1_7 (1 of 2).
        assert [x.item_id for x in disc[:3]] == ["e1_5", "e2_1", "e1_7"]
        assert [round(x.fragility, 3) for x in disc[:3]] == [0.667, 0.5, 0.333]
        # Equal fragility keeps the seeded order.
        e1 = [x.item_id for x in disc if x.eval_id == "e1" and x.stratum == PASS]
        seeded = _seeded_passes("s", "e1", self.E1_PASS, ["e1_9"])
        assert e1[2:] == [i for i in seeded if i not in ("e1_5", "e1_7")]
        # The log shows why: the counts of the block's items with a row.
        assert q.fragility == {
            "e1": {"e1_5": [2, 2], "e1_7": [1, 2], "e1_0": [0, 2]},
            "e2": {"e2_1": [1, 1]},
        }

    async def test_no_stored_row_of_another_config_keeps_the_seeded_order(
        self, tmp_path
    ):
        ctx = _ctx(tmp_path, objective="cost")
        targets = [EvalTarget(eval_id=e) for e in EVALS]
        q = await draw_queue(ctx, "rc_base", targets, "s", objective="cost")
        e1 = [x.item_id for x in q.items if x.eval_id == "e1" and x.stratum == PASS]
        assert e1 == _seeded_passes("s", "e1", self.E1_PASS, ["e1_9"])
        assert q.items[0].stratum == PASS
        assert all(x.fragility == 0.0 for x in q.items) and q.fragility == {}

    async def test_the_quality_queue_is_unchanged(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        _stored(ctx, "e1", "rc_a", {"e1_5": 0.0})
        targets = [EvalTarget(eval_id=e) for e in EVALS]
        q = await draw_queue(ctx, "rc_base", targets, "s")
        disc = [x for x in q.items if x.block == BLOCK_DISC]
        # Failures first, then DISC_GUARDS passes, by the seed alone.
        assert [x.stratum for x in disc] == [FAIL] * DISC_FAILS + [PASS] * DISC_GUARDS
        assert all(x.fragility == 0.0 for x in q.items) and q.fragility == {}

    def test_the_guard_cap_of_a_model_swap(self):
        def queue(passes_per_eval: dict[str, int], fails: int = 4) -> list[QItem]:
            out = []
            for e, n in passes_per_eval.items():
                out += [QItem(e, f"{e}p{i}", PASS, BLOCK_DISC, True) for i in range(n)]
                out += [
                    QItem(e, f"{e}f{i}", FAIL, BLOCK_DISC, False) for i in range(fails)
                ]
            return out

        four = queue({"a": 10, "b": 10, "c": 10, "d": 10})
        # 3 per eval holds 12 passes; 6 per eval holds 24.
        assert guard_cap(four, 3, 24, 40) == 6
        assert guard_cap(four, 8, 24, 40) == 8  # never below the round's size
        # Scarce passes: every pass the queue holds is enough.
        assert guard_cap(queue({"a": 2, "b": 3}), 3, 24, 40) == 3
        # One eval short of passes: the others grow, up to 40 items.
        skewed = queue({"a": 1, "b": 30, "c": 30}, fails=10)
        assert guard_cap(skewed, 3, 24, 40) == 12  # 25 passes in 35 items
        # A minimum the item ceiling cannot reach stops below 40 items.
        assert guard_cap(skewed, 3, 30, 40) == 14
        assert len(cap_items(skewed, 14)) == 39


# ------------------------------------------------------------ estimator


def _population(n_fail: int, n_pass: int) -> dict[str, dict[str, int]]:
    return {"e": {FAIL: n_fail, PASS: n_pass, "unscored": 0, "named": 0}}


def _engine_order(
    fails: list[int], passes: list[int], rng: random.Random
) -> list[tuple[str, int]]:
    """The engine's order for one eval: each stratum shuffled, 8 failures
    and 4 passes first, then the rest interleaved (`measure._interleave`)."""
    f, p = list(fails), list(passes)
    rng.shuffle(f)
    rng.shuffle(p)
    head = [(FAIL, i) for i in f[:DISC_FAILS]] + [(PASS, i) for i in p[:DISC_GUARDS]]
    return head + _interleave({FAIL: f[DISC_FAILS:], PASS: p[DISC_GUARDS:]})


def _judged(order, cand, parent, upto: int) -> list[Judged]:
    return [
        Judged("e", str(i), s, BLOCK_REST, parent[i], cand[i]) for s, i in order[:upto]
    ]


def sim_estimator(draws: int = 4000, n_judged: int = 24, seed: int = 0) -> dict:
    """60 items, the parent fails 12. The candidate fixes 6 of them and
    breaks 5 of its 48 passes: true net +1/60. Stopped after `n_judged`
    items of the engine's hard-first order: the stratified estimate against
    a plain mean over the judged items."""
    rng = random.Random(seed)
    parent = [i >= 12 for i in range(60)]
    cand = [(i < 6) or (i >= 12 and i >= 17) for i in range(60)]
    truth = (sum(cand) - sum(parent)) / 60
    fails = list(range(12))
    passes = list(range(12, 60))
    strat, plain, covered = [], [], 0
    for _ in range(draws):
        order = _engine_order(fails, passes, rng)
        rows = _judged(order, cand, parent, n_judged)
        est = estimate(rows, _population(12, 48))
        assert est is not None
        strat.append(est.net)
        plain.append(sum(r.d for r in rows) / len(rows))  # type: ignore[misc]
        lo, hi = est.ci()
        covered += lo <= truth <= hi
    return {
        "truth": truth,
        "stratified_mean": sum(strat) / draws,
        "plain_mean": sum(plain) / draws,
        "ci_coverage": covered / draws,
    }


class TestTheEstimator:
    def test_a_census_is_exact(self):
        parent = [i >= 4 for i in range(10)]
        cand = [i >= 2 for i in range(10)]
        order = _engine_order(list(range(4)), list(range(4, 10)), random.Random(1))
        est = estimate(_judged(order, cand, parent, 10), _population(4, 6))
        assert est is not None and est.net == pytest.approx(2 / 10)
        assert est.se == 0 and est.complete

    def test_an_unsampled_stratum_is_incomplete_and_wide(self):
        rows = [Judged("e", "f", FAIL, BLOCK_DISC, False, True)]
        est = estimate(rows, _population(10, 90))
        assert est is not None and not est.complete and est.se > 0.4

    def test_a_missing_candidate_row_counts_as_a_fail(self):
        rows = [Judged("e", "p", PASS, BLOCK_DISC, True, None)]
        assert rows[0].d == -1

    def test_unbiased_at_a_stop_where_the_plain_mean_is_not(self):
        r = sim_estimator()
        assert r["stratified_mean"] == pytest.approx(r["truth"], abs=0.01)
        # The hard-first order over-weights the failures, where a candidate
        # can only fix: the plain mean reads a large gain that is not there.
        assert r["plain_mean"] - r["truth"] > 0.1
        assert r["ci_coverage"] >= 0.95


# ------------------------------------------------ sequential validity


WORSE = StopRule(
    kind="eval_metric",
    threshold=0.0,
    vs_baseline=True,
    alpha=DEFAULT_ALPHA,
    min_n=QUALITY_MIN_N,
)


def sim_quality(
    fix: float,
    brk: float,
    draws: int = 2000,
    n_fail: int = 24,
    n_pass: int = 72,
    seed: int = 0,
) -> dict:
    """One eval of n_fail + n_pass items; the candidate fixes each parent
    failure with probability `fix` and breaks each pass with probability
    `brk`, drawn afresh per run. The engine's order, chunks and geometric
    looks; the default quality rule. Returns the stop rate and the mean
    items judged."""
    rng = random.Random(seed)
    n = n_fail + n_pass
    parent = [i >= n_fail for i in range(n)]
    looks = quality_looks(n)
    stops = judged_total = 0
    for _ in range(draws):
        cand = [
            (rng.random() < fix) if not parent[i] else (rng.random() >= brk)
            for i in range(n)
        ]
        order = _engine_order(list(range(n_fail)), list(range(n_fail, n)), rng)
        bounds = _bounds(n, DISC_FAILS + DISC_GUARDS, None)
        mark = 0
        stopped = False
        for end in bounds:
            look = False
            while mark < len(looks) and end >= looks[mark]:
                look, mark = True, mark + 1
            ev = Evidence(
                judged=_judged(order, cand, parent, end),
                population=_population(n_fail, n_pass),
                quality_look=look,
                quality_looks=len(looks),
                full=True,
            )
            if check(WORSE, ev) is not None:
                stopped = True
                judged_total += end
                break
        if not stopped:
            judged_total += n
        stops += stopped
    return {"stop_rate": stops / draws, "mean_judged": judged_total / draws}


def _log_noise(rng: random.Random) -> float:
    """The repo's per-item latency noise model (`latency.py`), fitted to
    two identical configs in run 260924-0335: log-ratio 85% N(0, 0.30²),
    15% N(0, 0.90²)."""
    return rng.gauss(0, 0.30) if rng.random() < 0.85 else rng.gauss(0, 0.90)


RATIO = StopRule(
    kind="latency_ratio", threshold=1.0, min_n=TIMING_MIN_PAIRS, alpha=DEFAULT_ALPHA
)


def sim_latency(
    ratio: float,
    pairs: int = 30,
    draws: int = 4000,
    seed: int = 0,
    not_better: bool = True,
) -> dict:
    """The candidate's true median latency is `ratio` × the baseline's.
    Pairs arrive 15 at a time (the engine's timing chunks): the latency
    ratio rule at each look, then `not_better` once all are in. Returns how
    often each stops the candidate."""
    rng = random.Random(seed)
    looks = math.ceil(pairs / 15)
    slow_stops = not_better_stops = 0
    for _ in range(draws):
        ref = {f"i{n}": 3700.0 for n in range(pairs)}
        cand = {k: v * ratio * math.exp(_log_noise(rng)) for k, v in ref.items()}
        stopped = False
        for k in range(1, looks + 1):
            got = list(ref)[: 15 * k]
            ev = Evidence(
                objective="speed",
                latency={i: cand[i] for i in got},
                ref_latency={i: ref[i] for i in got},
                timing_look=True,
                timing_looks=looks,
                timing_planned=pairs,
                timing_done=k == looks,
            )
            if check(RATIO, ev) is not None:
                slow_stops += 1
                stopped = True
                break
        if (
            not stopped
            and not_better
            and check(StopRule(kind="not_better"), ev) is not None
        ):
            not_better_stops += 1
    return {
        "slower_stop": slow_stops / draws,
        "not_better_stop": not_better_stops / draws,
    }


def sim_latency_on_three(ratio: float, draws: int = 2000, seed: int = 0) -> float:
    """The old failure: a verdict on 3 pairs. The share of draws the rule
    stops on 3 pairs (it must be 0 whatever the noise)."""
    rng = random.Random(seed)
    stops = 0
    for _ in range(draws):
        ref = {f"i{n}": 3700.0 for n in range(3)}
        cand = {k: v * ratio * math.exp(_log_noise(rng)) for k, v in ref.items()}
        ev = Evidence(
            objective="speed",
            latency=cand,
            ref_latency=ref,
            timing_look=True,
            timing_planned=3,
            timing_done=True,
        )
        stops += any(
            check(r, ev) is not None for r in (RATIO, StopRule(kind="not_better"))
        )
    return stops / draws


class TestSequentialValidity:
    """The false-stop and false-continue rates, simulated with fixed seeds.
    The numbers asserted are the bounds the design promises; the report
    has the measured rates."""

    def test_quality_a_candidate_that_is_not_worse_is_rarely_stopped(self):
        # Net 0 with churn: fixes 30% of 24 failures, breaks 10% of 72 passes.
        assert sim_quality(0.30, 0.10)["stop_rate"] <= DEFAULT_ALPHA
        # A candidate with no change at all is never stopped.
        assert sim_quality(0.0, 0.0)["stop_rate"] == 0.0

    def test_quality_a_clearly_worse_candidate_is_stopped_early(self):
        # Breaks 25% of its passes and fixes 10% of the failures: net −0.16.
        r = sim_quality(0.10, 0.25)
        assert r["stop_rate"] >= 0.8
        assert r["mean_judged"] < 72

    def test_latency_a_flat_candidate_is_rarely_cut_as_slower(self):
        assert sim_latency(1.0, not_better=False)["slower_stop"] <= DEFAULT_ALPHA
        # ... and it is not claimed as faster: `not_better` stops it.
        assert sim_latency(1.0, draws=300)["not_better_stop"] >= 0.9

    def test_latency_a_real_ten_percent_gain_is_never_cut_as_slower(self):
        assert sim_latency(0.9, not_better=False)["slower_stop"] <= 0.01

    def test_latency_no_verdict_of_any_kind_on_three_pairs(self):
        for ratio in (0.9, 1.0, 1.5, 3.0):
            assert sim_latency_on_three(ratio) == 0.0

    def test_latency_a_clearly_slower_candidate_is_stopped(self):
        assert sim_latency(1.5, not_better=False)["slower_stop"] >= 0.8
        assert sim_latency(2.0, not_better=False)["slower_stop"] >= 0.95


# ------------------------------------------------------- the engine


async def test_progress_lines_one_per_chunk_per_candidate(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0, train_n=24)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    lines = [f["line"] for k, f in ctx.log.events if k == "measure_chunk"]
    assert lines, "no progress line"
    # Under speed: the losses on the baseline's passes, the fixes apart.
    pat = re.compile(
        r"^w1-1: chunk (\d+)/(\d+) · \d+ judged · lost \d+/\d+ passes, fixed"
        r" \d+/\d+( · [0-9.]+× latency)? · (continue|done|stopped: \w+)$"
    )
    for line in lines:
        assert pat.match(line), line
    assert lines[-1].endswith("done")
    # The same lines are the item's details in the live view.
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert all(line in details for line in lines)


def test_the_progress_line_shape():
    ev = Evidence(
        judged=[
            Judged("e", "a", FAIL, BLOCK_DISC, False, True),
            Judged("e", "b", PASS, BLOCK_DISC, True, True),
        ],
        latency={"a": 880.0},
        ref_latency={"a": 1000.0},
    )
    assert (
        progress_line("w7-1", 3, 9, ev, "continue")
        == "w7-1: chunk 3/9 · 2 judged · +1/−0 vs baseline · 0.88× latency · continue"
    )


async def test_a_quality_arm_completes_and_its_rows_are_the_train_measurement(
    tmp_path,
):
    """A full loop through the engine with the fake Kiln under --objective
    quality: the queue is drawn and recorded, the arm is judged chunk by
    chunk, the analysis re-drives nothing, and the arm reaches the gate."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    ctx.dispatch.queues["analyze_experiment_results"].clear()
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await orchestrate(ctx, "better", timeout_mins=180)
    (drawn,) = [f for k, f in ctx.log.events if k == "queue_drawn"]
    assert drawn["size"] == 30 and drawn["seed"].endswith(":w1-1")
    (m,) = [f for k, f in ctx.log.events if k == "measurement"]
    assert m["status"] == "completed"
    # Judged in chunks: one narrowed job per chunk, none after them.
    jobs = [
        c[3]
        for c in ctx.kiln.calls
        if c[1] == "/api/jobs/evals/run"
        and c[3]["run_config_id"] == "rc_cand"
        and c[3]["split"] == "train"
    ]
    chunk_jobs = [j for j in jobs if j.get("item_ids")]
    assert len(chunk_jobs) == len(_bounds(30, 1 + DISC_GUARDS, None))
    # The analysis reads the stored rows through one full job, which the
    # server skip leaves with nothing to drive.
    assert len(jobs) - len(chunk_jobs) <= 1
    assert not ctx.kiln.pending.get(("eval_1", "rc_cand", "train"))
    # Quality mode: nothing is timed.
    assert not [p for p in ctx.kiln.run_posts if "train" in p["plaintext_input"]]
    # Completed and successful: it reached the val gate.
    assert ("eval_1", "rc_cand", "val") in ctx.kiln.job_invocations


async def test_futility_stops_an_arm_that_can_no_longer_improve(tmp_path):
    """The parent fails 1 of 30; the candidate breaks a guard in the first
    chunk and does not fix the failure: +1 is out of reach."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _seed_rows(ctx.kiln, "rc_cand", [0.0] + [1.0] * 29)
    ctx.dispatch.queues["create_experiment_rc"].clear()
    ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_cand"))
    from conftest import fix_item

    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1", residual_gate=False))),
        ok(make_plan(loop_exit="done")),
    )
    await orchestrate(ctx, "better", timeout_mins=180)
    (m,) = [f for k, f in ctx.log.events if k == "measurement"]
    assert m["stop_kind"] == "futility"
    assert "it must improve" in m["line"]
    assert not ctx.dispatch.calls_to("analyze_experiment_results")


async def test_two_candidates_are_timed_on_the_same_items_in_one_window(tmp_path):
    ctx = _ctx(tmp_path, objective="speed", latency={"rc_probe_1": 900.0})
    from test_viability import GEMINI, _judge, _request

    from kiln_harness_tuner.schemas import ScreenCandidate
    from kiln_harness_tuner.viability import run_screening_round

    _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
    _judge(ctx, "rc_probe_2", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
    other = ScreenCandidate(id="s1-2", overrides={"thinking_level": "low"})
    await run_screening_round(ctx, _request(GEMINI, other), "rc_base", 1)
    by_rc: dict[str, list[str]] = {}
    for post in ctx.kiln.run_posts:
        by_rc.setdefault(post["task_run_config_id"], []).append(post["plaintext_input"])
    arms = sorted(by_rc)
    assert len(arms) == 3  # the baseline and the two candidates
    assert len({tuple(sorted(v)) for v in by_rc.values()}) == 1


# ------------------------------------------- no per-request ceiling


class TestNoPerRequestCeiling:
    """`--max-request-cost` and `--max-request-latency` are
    removed. The objective's own ratio rules stay."""

    @pytest.mark.parametrize("kind", ["cost_per_request", "mean_latency"])
    def test_the_absolute_ceiling_kinds_are_gone(self, kind):
        with pytest.raises(ValueError):
            StopRule.model_validate({"kind": kind, "threshold": 4.0})

    @pytest.mark.parametrize(
        ("objective", "kind"), [("speed", "latency_ratio"), ("cost", "cost_ratio")]
    )
    def test_the_objective_ratio_rules_stay(self, objective, kind):
        (rule,) = [r for r in default_stop_rules(objective) if r.kind == kind]
        assert rule.threshold == 1.0
        assert rule.alpha and rule.min_n == TIMING_MIN_PAIRS

    def test_quality_has_no_timing_rule(self):
        kinds = {r.kind for r in default_stop_rules("quality", full=True)}
        assert not kinds & {"latency_ratio", "cost_ratio", "not_better"}
        assert "ceilings" not in Ctx.__dataclass_fields__

    async def test_under_quality_an_arm_is_not_timed(self, tmp_path):
        """Under quality the arm, the reference and the parent (another run
        config here) are never driven judge-free on train, and the gate
        times nothing on val. The arm's latency and cost are still on its
        rows, its record and the val tables, from the stored eval runs."""
        ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", parent_ms=900)
        ctx.run_dir = tmp_path
        kiln = ctx.kiln

        def priced(rows: list[dict], usd: float, ms: float) -> list[dict]:
            usage = {"cost": usd, "total_llm_latency_ms": ms}
            return [{**r, "task_run_usage": usage} for r in rows]

        fast = ("eval_1", "rc_fast", "train")
        kiln.results[fast] = priced(kiln.results[fast], 0.002, 1000.0)
        for key, usd, ms in (
            (("eval_1", "rc_cand", "train"), 0.001, 2000.0),
            (("eval_1", "rc_base", "val"), 0.002, 1000.0),
            (("eval_1", "rc_cand", "val"), 0.001, 2000.0),
        ):
            kiln.pending[key] = priced(kiln.pending[key], usd, ms)
        await orchestrate(ctx, "better", timeout_mins=180)
        (m,) = [f for k, f in ctx.log.events if k == "measurement"]
        assert m["status"] == "completed"
        assert ctx.kiln.run_posts == []  # no timing drive, train or val
        assert not [f for k, f in ctx.log.events if k == "speed_result"]
        # The latency and the cost stay visible: the record and the val
        # tables.
        record = ctx.kiln.memories[
            ctx.dispatch.calls_to("analyze_experiment_results")[0].experiment_memory_id
        ]["content"]
        line = r"cost eval_1 \(train, eval_runs, n=\d+\).*ratio 0\.50"
        assert re.search(line, record)
        line = r"latency eval_1 \(train, eval_runs, n=\d+\).*ratio 2\.00"
        assert re.search(line, record)
        summary = json.loads(
            (tmp_path / "val_summary.json").read_text(encoding="utf-8")
        )
        (row,) = summary["candidates"][0]["per_eval"]
        assert row["cost"]["metric"] == "cost_usd"
        assert row["cost"]["median_ratio"] == pytest.approx(0.5)
        assert row["latency"]["method"] == "eval_runs"
        assert row["latency"]["median_ratio"] == pytest.approx(2.0)

    async def test_under_quality_a_slower_arm_is_judged_not_timed(self, tmp_path):
        """An arm 2.5x slower than the baseline: under quality no timing
        rule stops it, nothing is driven judge-free, and it is judged."""
        ctx = _speed_ctx(tmp_path, cand_ms=2500.0, objective="quality")
        await orchestrate(ctx, "better", timeout_mins=180)
        (m,) = [f for k, f in ctx.log.events if k == "measurement"]
        assert m["status"] == "completed"
        assert ctx.kiln.run_posts == []
        assert [j for j in ctx.kiln.job_invocations if j[1] == "rc_cand"]
        (plan,) = ctx.dispatch.calls_to("batch_plan")[:1]
        assert not {r.kind for r in plan.default_stop_rules} & {
            "latency_ratio",
            "cost_ratio",
        }


# ------------------------------------------------------ probes, retirement


async def test_a_data_question_is_answered_from_stored_rows(tmp_path):
    from test_probe_phase import code_probe, probe_ctx

    from kiln_harness_tuner.items import execute_plan_item
    from kiln_harness_tuner.schemas import BaselineRef, ProbeCheck

    ctx = probe_ctx(tmp_path)
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        {**make_run(f"t{n}", accuracy=1.0), "output": '{"a": 1}' if n else "oops"}
        for n in range(4)
    ]
    check_ = ProbeCheck(count="json_valid", source="stored", rule="all")
    outcome = await execute_plan_item(
        ctx, code_probe(check_), BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.probe is not None and outcome.probe.answer == "no"
    assert "over 4 stored train row(s) of rc_base (no drive)" in (
        outcome.probe.evidence
    )
    assert not ctx.kiln.run_posts
    assert not ctx.dispatch.calls_to("probe")


def test_a_stored_probe_takes_no_fork_settings():
    from kiln_harness_tuner.schemas import ProbeCheck

    with pytest.raises(ValueError, match="stored rows"):
        ProbeCheck(count="json_valid", source="stored", compare_to_parent=True)


class TestTheOldPathsAreGone:
    """No code path still runs the viability stage, the residual gate, the
    30-item timing pass, the quick rule or a probe's speed line."""

    def test_the_modules_and_names(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("kiln_harness_tuner.residual")
        viability = importlib.import_module("kiln_harness_tuner.viability")
        items = importlib.import_module("kiln_harness_tuner.items")
        latency = importlib.import_module("kiln_harness_tuner.latency")
        for mod, names in (
            (
                viability,
                [
                    "run_viability",
                    "measure_candidates",
                    "ensure_pool",
                    "ensure_quick_sample",
                ],
            ),
            (
                items,
                [
                    "_residual_gate",
                    "_train_speed_pass",
                    "_agent_probe_speed",
                    "probe_speed_text",
                    "SpeedPass",
                ],
            ),
            (latency, ["quick_sample", "quick_probe_verdict", "QUICK_PROBE_ITEMS"]),
        ):
            for name in names:
                assert not hasattr(mod, name), f"{mod.__name__}.{name}"

    def test_only_the_engine_judges_an_item_subset(self):
        callers = sorted(
            p.name
            for p in SRC.rglob("*.py")
            if re.search(r"analyzer\.trial\(", p.read_text(encoding="utf-8"))
        )
        assert callers == ["measure.py"]

    def test_no_probe_carries_a_speed_field(self):
        from kiln_harness_tuner.schemas import ProbeAnswer, ProbeResult

        assert "latency" not in ProbeResult.model_fields
        assert "latency" not in ProbeAnswer.model_fields


# ------------------------------------------------ guards, one test each


def test_a_timing_test_never_fires_under_its_floor_even_at_one_look():
    """5 of 5 pairs slower is p = 0.031 < 0.05 at a single look: only the
    six-pair floor stops a verdict on it."""
    ref = {f"i{n}": 1000.0 for n in range(5)}
    ev = Evidence(
        latency={k: 1300.0 for k in ref},
        ref_latency=ref,
        timing_look=True,
        timing_looks=1,
    )
    assert check(RATIO, ev) is None
    ref6 = {f"i{n}": 1000.0 for n in range(6)}
    ev6 = Evidence(
        latency={k: 1300.0 for k in ref6},
        ref_latency=ref6,
        timing_look=True,
        timing_looks=1,
    )
    assert check(RATIO, ev6) is not None


def test_the_quality_test_waits_for_its_floor():
    """Five judged passes all broken in a large stratum: the z-test alone
    would stop it; `min_n` (6) holds the verdict."""
    rows = [Judged("e", f"p{n}", PASS, BLOCK_REST, True, False) for n in range(5)]
    ev = Evidence(judged=rows, population=_population(0, 200), quality_look=True)
    assert check(WORSE, ev) is None
    rows.append(Judged("e", "p5", PASS, BLOCK_REST, True, False))
    ev.judged = rows
    assert check(WORSE, ev) is not None


def test_survivors_are_ranked_by_speed_band_then_net():
    from kiln_harness_tuner.schemas import ScreenResult
    from kiln_harness_tuner.viability import rank_survivors

    def result(cid: str, ratio: float, net: float, verdict: str = "survivor"):
        base = {f"i{n}": 1000.0 for n in range(10)}
        return ScreenResult(
            candidate_id=cid,
            label=cid,
            rc_id=f"rc_{cid}",
            round=1,
            evals=["e1"],
            items=6,
            verdict=verdict,  # type: ignore[arg-type]
            latency=compare_latencies(
                base,
                {k: v * ratio for k, v in base.items()},
                method="paired_drive",
                split="train",
            ),
            net=net,
        )

    results = [
        result("a", 0.50, -0.05),
        result("b", 0.51, 0.10),  # same band as a, higher net
        result("c", 0.30, -0.20),  # faster band
        result("d", 0.10, 0.50, "dead_end"),
    ]
    rank_survivors(results, "speed")
    assert [r.rank for r in results] == [3, 2, 1, None]
    rank_survivors(results, "quality")
    assert [r.rank for r in results] == [2, 1, 3, None]


async def test_a_stored_train_row_the_disk_does_not_list_is_in_the_queue(tmp_path):
    ctx = _ctx(tmp_path, objective="quality")
    ctx.kiln.results[("e1", "rc_base", "train")].append(
        make_run("e1_extra", accuracy=0.0)
    )
    q = await draw_queue(ctx, "rc_base", [EvalTarget(eval_id="e1")], "s")
    assert ("e1", "e1_extra") in {(x.eval_id, x.item_id) for x in q.items}


async def test_the_val_gate_reports_one_line_per_candidate_per_eval(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality")
    await orchestrate(ctx, "better", timeout_mins=180)
    lines = [f["line"] for k, f in ctx.log.events if k == "gate_progress"]
    assert lines == ["gate rc_cand: eval 1/1 · 2 judged · +0/−0 vs baseline · done"]


def test_an_arms_chunk_line_prints_once():
    from kiln_harness_tuner.ui import own_line

    assert own_line("measure_chunk", {"item_id": "w1-1", "line": "x"}) == ""
    assert own_line("measure_chunk", {"line": "s1-1: chunk 1/3"}) == "s1-1: chunk 1/3"
    assert own_line("measurement", {"item_id": "w1-1", "line": "m"}) == "m"


def test_no_per_request_ceiling_flag_is_left():
    """Both per-request ceilings are removed, and so are their
    old names. Nothing on the command line can time a quality arm."""
    import importlib.util

    from kiln_harness_tuner.cli import build_parser

    flags = {a for act in build_parser()._actions for a in act.option_strings}
    gone = {
        "--latency-ceiling",
        "--cost-ceiling",
        "--max-request-cost",
        "--max-request-latency",
    }
    assert not gone & flags
    assert not importlib.util.find_spec("kiln_harness_tuner.ceilings")


@pytest.mark.parametrize(
    ("given", "flag", "reason"),
    [
        (["--max-request-cost", "$0.02"], "--max-request-cost", "--model-policy"),
        (["--max-request-cost=1.2x"], "--max-request-cost", "--model-policy"),
        (["--max-request-latency", "4s"], "--max-request-latency", "--objective"),
        (["--max-request-latency=1.1x"], "--max-request-latency", "--objective"),
    ],
)
def test_a_removed_ceiling_flag_fails_with_its_reason(given, flag, reason, capsys):
    """An old command line with a removed ceiling flag, in either spelling,
    stops in argparse with one line that names the removal and the reason,
    before anything runs."""
    from kiln_harness_tuner.cli import build_parser

    argv = ["--project-path", "p", "--task-id", "t", "--project-id", "q", *given]
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"{flag} was removed:" in err
    assert reason in err and "unrecognized" not in err
    (line,) = [x for x in err.splitlines() if "was removed" in x]
    assert "stays" not in line


async def test_a_one_chunk_round_is_still_timed_in_full(tmp_path):
    """`looks: 1` gives one judge chunk; the timing pairs must not stop at
    the first 15 (the rest would read as an incomplete pass)."""
    from test_viability import GEMINI, _judge, _request

    from kiln_harness_tuner.viability import run_screening_round

    ctx = _ctx(tmp_path, objective="speed", latency={"rc_probe_1": 900.0})
    _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
    (result,) = await run_screening_round(ctx, _request(GEMINI, looks=1), "rc_base", 1)
    assert result.verdict == "survivor", result.reason
    assert result.latency is not None and result.latency.n_items == 20
    drives = [p for p in ctx.kiln.run_posts if p["task_run_config_id"] != "rc_base"]
    assert len(drives) == 20
