"""The spending cap on every measured candidate (`kiln_harness_tuner.budget`):
the default, the clamp, the capped outcome (never a dead end), the reuse of a
capped arm's rows, the attribution of paired timing drives, and the prices the
planner is given."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import fix_item, make_plan, make_result, make_tracker
from fake_dispatcher import ok
from fake_kiln import make_run
from test_speed_objective import _speed_ctx
from test_viability import EVALS, GEMINI, _ctx, _judge, _request

from kiln_harness_tuner import measure as measure_mod
from kiln_harness_tuner.budget import (
    DEFAULT_HEADROOM,
    DEFAULT_MAX_USD,
    DEFAULT_REMAINING_SHARE,
    HARD_MAX_SHARE,
    HARD_MAX_USD,
    BudgetHolds,
    remaining_run_budget,
    resolve_budget,
)
from kiln_harness_tuner.memory import ExperimentStateError
from kiln_harness_tuner.memory_schema import CONTENT_MAX_CHARS, OVERVIEW_MAX_CHARS
from kiln_harness_tuner.orchestrator import SpendMeter, orchestrate
from kiln_harness_tuner.schemas import (
    BatchPlanInput,
    EvalTarget,
    ExperimentItem,
    FixWork,
    ScreenCandidate,
    ScreenRequest,
    StopRule,
)
from kiln_harness_tuner.viability import run_screening_round

# ------------------------------------------------------------ the numbers


class TestTheDefault:
    def test_headroom_over_the_expected_full_measurement(self):
        b = resolve_budget(None, "", 4.0, None)
        assert b.source == "default"
        assert b.usd == pytest.approx(DEFAULT_HEADROOM * 4.0)

    def test_never_above_the_fixed_default(self):
        assert resolve_budget(None, "", 1000.0, None).usd == DEFAULT_MAX_USD

    def test_never_above_its_share_of_the_remaining_run_budget(self):
        b = resolve_budget(None, "", 10.0, 50.0)
        assert b.usd == pytest.approx(DEFAULT_REMAINING_SHARE * 50.0)

    def test_an_unknown_price_takes_the_fixed_default(self):
        assert resolve_budget(None, "", None, None).usd == DEFAULT_MAX_USD
        assert "expected cost unknown" in resolve_budget(None, "", None, None).line


class TestTheClamp:
    def test_a_planner_value_over_the_share_is_clamped(self):
        b = resolve_budget(50.0, "a big lever", None, 100.0)
        assert b.source == "planner"
        assert b.usd == pytest.approx(HARD_MAX_SHARE * 100.0)
        assert "clamped from $50.00 to the hard maximum $25.00" in b.line

    def test_without_a_run_budget_the_absolute_maximum(self):
        assert resolve_budget(500.0, "r", None, None).usd == HARD_MAX_USD

    def test_a_value_under_the_clamp_stands(self):
        b = resolve_budget(3.0, "one eval", 100.0, 100.0)
        assert b.usd == 3.0 and not b.note

    def test_the_default_lies_inside_the_clamp(self):
        """By construction, so the default needs no clamp of its own."""
        assert DEFAULT_MAX_USD <= HARD_MAX_USD
        assert DEFAULT_REMAINING_SHARE <= HARD_MAX_SHARE


class TestTheSchema:
    def test_a_cap_needs_its_reason(self):
        with pytest.raises(ValueError, match="budget_reason"):
            ExperimentItem(
                id="w1-1",
                type="experiment",
                evaluation_target=[EvalTarget(eval_id="e1")],
                promotion_candidate=True,
                work=fix_item().work,
                max_usd=5.0,
            )
        with pytest.raises(ValueError, match="budget_reason"):
            ScreenCandidate(id="s1-1", max_usd=2.0)
        assert ScreenCandidate(id="s1-1", max_usd=2.0, budget_reason="r").max_usd

    def test_a_cap_is_not_a_stop_rule(self):
        rule = StopRule(kind="max_usd", threshold=3.0)
        with pytest.raises(ValueError, match="not a stop rule"):
            FixWork.model_validate(
                {**fix_item().work.model_dump(), "stop_rules": [rule.model_dump()]}
            )
        with pytest.raises(ValueError, match="not a stop rule"):
            ScreenRequest(candidates=[ScreenCandidate(id="s1-1")], stop_rules=[rule])


# ------------------------------------------------------------ in the loop


def _costly(ctx, n: int = 30, cost: float = 0.05, eval_cost: float = 0.05):
    """Train rows that cost money: the parent's stored, the candidate's
    pending, each with an id so a row is booked once."""
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        {
            **make_run(f"train{i}", cost, eval_cost, accuracy=0.0 if i == 0 else 1.0),
            "id": f"base_{i}",
        }
        for i in range(n)
    ]
    ctx.kiln.pending[("eval_1", "rc_cand", "train")] = [
        {**make_run(f"train{i}", cost, eval_cost, accuracy=1.0), "id": f"cand_{i}"}
        for i in range(n)
    ]


def _arm(max_usd: float | None = None, reason: str = "", existing: str | None = None):
    item = fix_item("w1-1", residual_gate=False)
    if existing:
        item.work.existing_rc_id = existing
    item.max_usd = max_usd
    item.budget_reason = reason
    return item


async def test_an_arm_at_its_cap_is_capped_not_a_dead_end(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx)
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(_arm(1.0, "one targeted eval, a small lever"))),
        ok(make_plan(loop_exit="done")),
    )
    await orchestrate(ctx, "better", timeout_mins=180)
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.status == "capped"
    m = outcome.measurement
    assert m.budget_usd == 1.0 and m.stop_kind == "max_usd"
    # The first chunk (1 failure + 4 guards, $0.10 each) is paid; the next
    # 12 items would pass $1.00, so the arm stops before them.
    assert m.judged == 5 and m.spend_usd == pytest.approx(0.5)
    assert m.stop_line.startswith(
        "stopped at n=5: capped at $1.00 after 5 items: inconclusive — net +1/−0 so far"
    )
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        " — CAPPED (capped at $1.00 after 5 items: inconclusive — net +1/−0 so far)"
    )
    assert "dead_end" not in record["tags"]
    assert not ctx.dispatch.calls_to("analyze_experiment_results")


async def test_a_larger_cap_later_goes_on_from_the_kept_rows(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx)
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(_arm(1.0, "small first look"))),
        ok(make_plan(_arm(10.0, "held 5 of 5; worth the split", existing="rc_cand"))),
        ok(make_plan(loop_exit="done")),
    )
    ctx.dispatch.queues["analyze_experiment_results"].clear()
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await orchestrate(ctx, "better", timeout_mins=180)
    first, second = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert first.status == "capped"
    assert second.status == "completed"
    # The 5 rows the capped arm paid for were not paid again.
    assert second.measurement.spend_usd == pytest.approx(0.1 * 25)


async def test_the_default_is_repriced_at_the_candidates_own_cost(tmp_path):
    """The parent costs $0.02 an item, the candidate $0.20: the first-chunk
    default (1.5 × 30 × $0.02 = $0.90) would cap it; re-priced after that
    chunk at its own cost it is 1.5 × 30 × $0.20 = $9, and it completes."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx, cost=0.01, eval_cost=0.01)
    ctx.kiln.pending[("eval_1", "rc_cand", "train")] = [
        {**make_run(f"train{i}", 0.1, 0.1, accuracy=1.0), "id": f"c{i}"}
        for i in range(30)
    ]
    ctx.dispatch.queues["analyze_experiment_results"].clear()
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await orchestrate(ctx, "better", timeout_mins=180)
    (budget,) = [f for k, f in ctx.log.events if k == "candidate_budget"]
    assert budget["usd"] == pytest.approx(1.5 * 30 * 0.02)
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.status == "completed"
    assert outcome.measurement.budget_usd == pytest.approx(1.5 * 30 * 0.2)


async def test_a_capped_screening_candidate_is_recorded_as_capped(tmp_path):
    ctx = _ctx(tmp_path, objective="quality")
    ctx.kiln.pending.update(
        {
            (e, "rc_probe_1", "train"): [
                {**make_run(f"{e}_{n}", 0.1, 0.1, accuracy=1.0), "id": f"{e}{n}"}
                for n in range(10)
            ]
            for e in EVALS
        }
    )
    cand = GEMINI.model_copy(update={"max_usd": 0.5, "budget_reason": "a cheap look"})
    (result,) = await run_screening_round(ctx, _request(cand), "rc_base", 1)
    assert result.verdict == "capped"
    assert result.budget_usd == 0.5
    assert result.reason.startswith("capped at $0.50 after ")
    assert "inconclusive" in result.line
    (record,) = [m for m in ctx.kiln.memories.values() if "screen" in m["tags"]]
    assert record["overview"].startswith("SCREEN CAPPED: ")
    assert "dead_end" not in record["tags"] and "lesson" in record["tags"]


async def test_paired_timing_is_attributed_per_candidate(tmp_path):
    """Two candidates timed beside the baseline in one window: each pays its
    own drives and half of the baseline's. The shares sum to the pass."""
    ctx = _ctx(tmp_path, objective="speed")
    inner = ctx.kiln.run_handler
    price = {"rc_base": 0.03, "rc_probe_1": 0.01, "rc_probe_2": 0.002}

    def handler(body):
        out = inner(body)
        out["usage"] = {
            **out.get("usage", {}),
            "cost": price[body["task_run_config_id"]],
        }
        return out

    ctx.kiln.run_handler = handler
    for rc in ("rc_probe_1", "rc_probe_2"):
        _judge(ctx, rc, {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
    other = ScreenCandidate(id="s1-2", overrides={"thinking_level": "low"})
    results = await run_screening_round(ctx, _request(GEMINI, other), "rc_base", 1)
    pairs = 20  # both evals' train items, all driveable
    by_id = {r.candidate_id: r for r in results}
    base_share = pairs * price["rc_base"] / 2
    assert by_id[GEMINI.id].spend_usd == pytest.approx(pairs * 0.01 + base_share)
    assert by_id["s1-2"].spend_usd == pytest.approx(pairs * 0.002 + base_share)
    total = sum(price[p["task_run_config_id"]] for p in ctx.kiln.run_posts)
    assert sum(r.spend_usd for r in results) == pytest.approx(total)


async def test_the_planner_is_given_prices_and_the_cap_rule(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx, cost=0.04, eval_cost=0.01)
    ctx.budget_usd = 100.0
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "better", timeout_mins=180)
    (call,) = ctx.dispatch.calls_to("batch_plan")
    assert isinstance(call, BatchPlanInput)
    prices = call.measurement_prices
    assert prices is not None
    assert prices.drive_usd_per_request == pytest.approx(0.04)
    (row,) = prices.per_eval
    assert row.judge_usd_per_item == pytest.approx(0.01)
    assert row.train_items == 30
    assert row.full_measurement_usd == pytest.approx(30 * 0.05)
    assert prices.remaining_run_budget_usd is not None
    assert prices.hard_max_usd == pytest.approx(
        HARD_MAX_SHARE * prices.remaining_run_budget_usd
    )
    assert "budget_reason" in prices.rule and "CAPPED" in prices.rule


async def test_a_forked_arm_pays_its_parents_timing_drives(tmp_path):
    """An arm forked from rc_fast is timed beside the baseline and its
    parent: alone in its window, it pays all three arms' drives."""
    ctx = _speed_ctx(tmp_path, cand_ms=260.0, parent_ms=200.0, train_n=8)
    inner = ctx.kiln.run_handler
    price = {"rc_base": 0.03, "rc_fast": 0.02, "rc_cand": 0.01}

    def handler(body):
        out = inner(body)
        out["usage"] = {**out["usage"], "cost": price[body["task_run_config_id"]]}
        return out

    ctx.kiln.run_handler = handler
    await orchestrate(ctx, "go fast", timeout_mins=180)
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.measurement.spend_usd == pytest.approx(8 * (0.03 + 0.02 + 0.01))


# ------------------------------------------------------------ holds


class TestHolds:
    """Candidates measured side by side hold the unspent part of their
    budgets, so they cannot each size a cap against the same dollars."""

    def _ctx(self, budget: float = 100.0, spent: float = 0.0):
        meter = SpendMeter()
        meter.event("spend", cost_usd=spent, lane="provider")
        return SimpleNamespace(
            budget_usd=budget, spend=meter, budget_holds=BudgetHolds()
        )

    def test_the_remaining_budget_is_net_of_the_holds(self):
        ctx = self._ctx(spent=10.0)
        ctx.budget_holds.hold("a", 25.0)
        ctx.budget_holds.hold("b", 5.0)
        assert remaining_run_budget(ctx) == pytest.approx(60.0)
        assert remaining_run_budget(ctx, excluding="a") == pytest.approx(85.0)
        ctx.budget_holds.release("a")
        assert remaining_run_budget(ctx) == pytest.approx(85.0)

    def test_a_second_candidate_is_clamped_against_what_the_first_holds(self):
        ctx = self._ctx()
        first = resolve_budget(60.0, "r", None, remaining_run_budget(ctx))
        ctx.budget_holds.hold("first", first.usd)
        second = resolve_budget(60.0, "r", None, remaining_run_budget(ctx))
        assert first.usd == pytest.approx(HARD_MAX_SHARE * 100.0)
        assert second.usd == pytest.approx(HARD_MAX_SHARE * 75.0)

    def test_a_negative_hold_holds_nothing(self):
        holds = BudgetHolds()
        holds.hold("over", -3.0)
        assert holds.total() == 0.0


async def test_an_arm_beside_another_measurement_sizes_its_cap_to_what_is_free(
    tmp_path,
):
    """Another measurement holds $90 of the $100 run budget: the arm's
    default is 10% of the $10 left, not of $100, and its own hold is gone
    when it ends while the other one's stays."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx)
    ctx.budget_usd = 100.0
    ctx.budget_holds.hold("beside", 90.0)
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan", ok(make_plan(_arm())), ok(make_plan(loop_exit="done"))
    )
    await orchestrate(ctx, "better", timeout_mins=180)
    (budget,) = [f for k, f in ctx.log.events if k == "candidate_budget"]
    assert budget["usd"] == pytest.approx(DEFAULT_REMAINING_SHARE * 10.0)
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.status == "capped"
    assert ctx.budget_holds.total() == pytest.approx(90.0)


async def test_a_chunk_that_would_pass_the_run_budget_is_not_paid(tmp_path):
    """The arm sized its cap with the whole budget free; then another
    measurement took nearly all of it. Its first chunk ($0.50) would pass
    the $0.20 left, so it ends capped by the run budget before paying."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx)
    ctx.budget_usd = 100.0
    inner = ctx.log.event

    def event(kind, **fields):
        if kind == "candidate_budget":
            ctx.budget_holds.hold("beside", 99.8)
        inner(kind, **fields)

    ctx.log.event = event
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan", ok(make_plan(_arm())), ok(make_plan(loop_exit="done"))
    )
    await orchestrate(ctx, "better", timeout_mins=180)
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.status == "capped"
    m = outcome.measurement
    assert m.judged == 0 and m.spend_usd == 0.0
    assert "capped by the run budget ($0.20 left) after 0 items" in m.stop_line
    assert ctx.budget_holds.total() == pytest.approx(99.8)


async def test_every_hold_is_released_when_a_measurement_raises(monkeypatch):
    ctx = SimpleNamespace(budget_holds=BudgetHolds())

    async def boom(ctx, candidates, spec, call):
        for i in range(len(candidates)):
            ctx.budget_holds.hold((call, i), 5.0)
        raise RuntimeError("provider down")

    monkeypatch.setattr(measure_mod, "_measure", boom)
    with pytest.raises(RuntimeError):
        await measure_mod.measure(ctx, [object(), object()], None)  # type: ignore[arg-type]
    assert ctx.budget_holds.total() == 0.0


async def test_a_candidate_that_stops_frees_its_hold_while_a_sibling_goes_on(
    tmp_path,
):
    """Two screening candidates in one call: the one capped at $0.50 lets go
    of its hold at the chunk it stops, not when the whole call returns, so
    its unspent budget is free for the measurements beside it."""
    ctx = _ctx(tmp_path, objective="quality")
    ctx.kiln.pending.update(
        {
            (e, "rc_probe_1", "train"): [
                {**make_run(f"{e}_{n}", 0.1, 0.1, accuracy=1.0), "id": f"{e}{n}"}
                for n in range(10)
            ]
            for e in EVALS
        }
    )
    _judge(ctx, "rc_probe_2", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
    seen: list[tuple[str, str, int]] = []
    inner = ctx.log.event

    def event(kind, **fields):
        if kind == "measure_chunk":
            n = len(ctx.budget_holds._held)
            seen.append((str(fields["rc_id"]), str(fields["line"]), n))
        inner(kind, **fields)

    ctx.log.event = event
    capped = GEMINI.model_copy(update={"max_usd": 0.5, "budget_reason": "a look"})
    other = ScreenCandidate(id="s1-2", overrides={"thinking_level": "low"})
    results = await run_screening_round(ctx, _request(capped, other), "rc_base", 1)
    assert {r.candidate_id: r.verdict for r in results}[GEMINI.id] == "capped"
    (at_cap,) = [n for rc, line, n in seen if rc == "rc_probe_1" and "capped" in line]
    assert at_cap == 1  # only the sibling still holds
    assert any(rc == "rc_probe_2" and n == 1 for rc, _, n in seen)
    assert ctx.budget_holds.total() == 0.0


async def test_a_candidate_stopped_after_a_chunk_frees_its_hold_there(tmp_path):
    """The first candidate stores no row in its first chunk and fails; its
    hold is gone at that chunk while its sibling is still measured."""
    ctx = _ctx(tmp_path, objective="quality")
    _judge(ctx, "rc_probe_2", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
    seen: list[tuple[str, str, int]] = []
    inner = ctx.log.event

    def event(kind, **fields):
        if kind == "measure_chunk":
            n = len(ctx.budget_holds._held)
            seen.append((str(fields["rc_id"]), str(fields["line"]), n))
        inner(kind, **fields)

    ctx.log.event = event
    other = ScreenCandidate(id="s1-2", overrides={"thinking_level": "low"})
    await run_screening_round(ctx, _request(GEMINI, other), "rc_base", 1)
    (first,) = [(line, n) for rc, line, n in seen if rc == "rc_probe_1"]
    assert first[0].endswith("failed") and first[1] == 1


# ------------------------------------------------------------ a refused write
# An arm's experiment record that reads back as a verdict makes its `capped`
# write refused. The refusal must not cancel the wave or end the run.

LONG_TECHNIQUE = (
    "rewrite every skill file and the prompt so that each tool call is checked "
    "against the schema before it is sent, then add new versions of every skill "
) * 40  # several thousand characters


async def test_a_refused_capped_write_is_logged_and_the_run_goes_on(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx)

    async def refuse(exp_mem_id: str, detail: str) -> None:
        raise ExperimentStateError(
            f"experiment {exp_mem_id} is verdict — refusing to mark it capped"
            " (allowed moves: none)"
        )

    ctx.memory.mark_capped = refuse
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(_arm(1.0, "one targeted eval, a small lever"))),
        ok(make_plan(loop_exit="done")),
    )
    await orchestrate(ctx, "better", timeout_mins=180)
    kinds = [k for k, _ in ctx.log.events]
    assert "fatal_cancel" not in kinds
    (exit_event,) = [f for k, f in ctx.log.events if k == "exit"]
    assert not str(exit_event["reason"]).startswith("fatal")
    (failed,) = [f for k, f in ctx.log.events if k == "memory_write_failed"]
    assert failed["write"] == "mark_capped" and failed["item_id"] == "w1-1"
    assert "ExperimentStateError" in failed["error"]
    # The item keeps its outcome, in the run status the report is written from.
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.status == "capped"
    tracker = make_tracker()
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)
    (status,) = [i for w in tracker.snapshot().waves for i in w.items]
    assert status.item_id == "w1-1" and status.outcome.status == "capped"


async def test_a_long_technique_arm_keeps_its_state_to_the_cap(tmp_path):
    """The live case, end to end: a technique of several thousand characters.
    The record opens RUNNING, takes its build and ends CAPPED — no write is
    refused."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _costly(ctx)
    arm = _arm(1.0, "one targeted eval, a small lever")
    arm.work.technique = LONG_TECHNIQUE
    assert len(LONG_TECHNIQUE) > 4000
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan", ok(make_plan(arm)), ok(make_plan(loop_exit="done"))
    )
    await orchestrate(ctx, "better", timeout_mins=180)
    assert not [f for k, f in ctx.log.events if k == "memory_write_failed"]
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.status == "capped"
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert len(record["overview"]) <= OVERVIEW_MAX_CHARS
    assert record["overview"].endswith(
        " — CAPPED (capped at $1.00 after 5 items: inconclusive — net +1/−0 so far)"
    )
    assert len(record["content"]) <= CONTENT_MAX_CHARS
    assert "rc: rc_cand" in record["content"].splitlines()
    assert "issue: issue_1" in record["content"].splitlines()
