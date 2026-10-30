"""The speed objective and the model policy inside the loop:
the rules that decide are code, and each is tested where it decides — the
train success rule, the val gate's eligibility and choice, the analyzer's
policy guard and its held-out check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import ok
from fake_kiln import make_run
from test_orchestrator import (
    rc_ok,
    seed_active_eval,
    seed_baseline,
    seed_issue,
    seed_train,
)

from kiln_harness_tuner.analysis import (
    AnalysisError,
    HeldOutLeakError,
    ModelPolicyRefused,
    success_by_speed_rule,
)
from kiln_harness_tuner.latency import compare_latencies
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.orchestrator import (
    apply_speed_eligibility,
    decide_speed_gate,
    orchestrate,
    policy_refusal,
    pooled_net,
)
from kiln_harness_tuner.schemas import (
    AnalysisBundle,
    CandidateValTable,
    LatencyComparison,
    PerEvalResult,
)

POLICY = ModelPolicy.from_json(
    {
        "allow": [{"provider": "openai", "model": "*"}],
        "deny": [{"provider": "groq", "model": "*"}],
        "exceptions": [{"provider": "groq", "model": "fast_one"}],
    }
)


def _speed(ratio: float, n: int = 12) -> LatencyComparison:
    base = {f"i{k}": 1000.0 + k for k in range(n)}
    return compare_latencies(
        base,
        {k: v * ratio for k, v in base.items()},
        method="paired_drive",
        split="val",
    )


def _row(
    delta: float,
    significant: bool = False,
    eval_id: str = "e1",
    *,
    n: int = 20,
    f2p: int | None = None,
    p2f: int | None = None,
) -> PerEvalResult:
    """A pass/fail val row whose flips match its delta unless given."""
    if f2p is None and p2f is None:
        net = round(delta * n)
        f2p, p2f = max(net, 0), max(-net, 0)
    return PerEvalResult(
        eval_id=eval_id,
        score_key="s",
        score_type="pass_fail",
        direction="higher_is_better",
        split="val",
        baseline_rc_id="rc_base",
        compared_to="prior_baseline",
        n_items=n,
        delta=delta,
        p_value=0.01 if significant else 0.6,
        significant=significant,
        fail_to_pass=f2p,
        pass_to_fail=p2f,
        spend_usd=0.0,
    )


def _table(rc_id: str, ratio: float | None, *rows: PerEvalResult) -> CandidateValTable:
    return CandidateValTable(
        rc_id=rc_id,
        experiment_memory_id=f"mem_{rc_id}",
        per_eval=list(rows),
        latency=_speed(ratio) if ratio is not None else None,
    )


class TestEligibility:
    def test_clearly_faster_with_quality_held_is_eligible(self):
        table = _table("rc_a", 0.6, _row(-0.02), _row(+0.1, True))
        apply_speed_eligibility(table)
        assert not table.ineligible

    def test_not_faster_is_ineligible(self):
        table = _table("rc_a", 1.0, _row(+0.1, True))
        apply_speed_eligibility(table)
        assert table.ineligible
        assert any("not clearly faster" in e.detail for e in table.errors)

    def test_unmeasured_speed_is_ineligible(self):
        table = _table("rc_a", None, _row(+0.1))
        apply_speed_eligibility(table)
        assert table.ineligible

    def test_a_significant_regression_on_any_eval_is_ineligible(self):
        table = _table("rc_a", 0.5, _row(+0.2, True), _row(-0.1, True, "guard"))
        apply_speed_eligibility(table)
        assert table.ineligible
        assert any("guard/s regressed significantly" in e.detail for e in table.errors)

    def test_a_faster_median_with_a_worse_tail_is_ineligible(self):
        base = {f"i{k}": 1000.0 for k in range(30)}
        cand = {k: (5000.0 if n >= 22 else 800.0) for n, k in enumerate(base)}
        table = _table("rc_a", None, _row(0.0))
        table.latency = compare_latencies(
            base, cand, method="paired_drive", split="val"
        )
        apply_speed_eligibility(table)
        assert table.ineligible


class TestTheChoice:
    def test_the_fastest_eligible_candidate_wins(self):
        tables = [_table("rc_a", 0.8, _row(0.0)), _table("rc_b", 0.5, _row(0.0))]
        for t in tables:
            apply_speed_eligibility(t)
        decision = decide_speed_gate(tables)
        assert decision.winner_rc_id == "rc_b"
        assert "speed" in decision.rationale

    def test_quality_wins_do_not_beat_a_clearly_faster_candidate(self):
        # Outside the tie band speed decides, whatever the quality wins.
        tables = [
            _table("rc_a", 0.8, _row(+0.2, True)),
            _table("rc_b", 0.5, _row(0.0)),
        ]
        for t in tables:
            apply_speed_eligibility(t)
        assert decide_speed_gate(tables).winner_rc_id == "rc_b"

    def test_the_higher_net_breaks_a_speed_tie(self):
        tables = [
            _table("rc_a", 0.60, _row(0.0)),
            _table("rc_b", 0.62, _row(+0.2, True)),
        ]
        for t in tables:
            apply_speed_eligibility(t)
        decision = decide_speed_gate(tables)
        assert decision.winner_rc_id == "rc_b"
        assert "the higher pooled net broke the tie" in decision.rationale

    def test_a_larger_net_without_significance_beats_a_smaller_one(self):
        """The tie goes to the higher pooled net, not to the
        count of significant rows."""
        tables = [
            _table("rc_a", 0.60, _row(+0.05, True)),
            _table("rc_b", 0.61, _row(+0.15, False)),
        ]
        for t in tables:
            apply_speed_eligibility(t)
        assert pooled_net(tables[1]) > pooled_net(tables[0])
        assert decide_speed_gate(tables).winner_rc_id == "rc_b"

    def test_an_ineligible_candidate_never_wins_however_fast(self):
        tables = [
            _table("rc_a", 0.2, _row(-0.3, True)),
            _table("rc_b", 0.9, _row(0.0)),
        ]
        for t in tables:
            apply_speed_eligibility(t)
        assert decide_speed_gate(tables).winner_rc_id == "rc_b"

    def test_no_eligible_candidate_is_no_winner(self):
        tables = [_table("rc_a", 1.1, _row(0.0))]
        apply_speed_eligibility(tables[0])
        assert decide_speed_gate(tables).winner_rc_id is None


class TestTrainRule:
    def _bundle(self, latency, *rows):
        return AnalysisBundle(
            per_eval=list(rows), spend_total_usd=0.0, errors=[], latency=latency
        )

    def test_faster_and_held_is_successful(self):
        verdict = success_by_speed_rule(self._bundle(_speed(0.5), _row(0.0)), 0.95)
        assert verdict.ok, verdict.reasons

    def test_not_faster_fails(self):
        assert not success_by_speed_rule(self._bundle(_speed(1.0), _row(0.1)), 0.95).ok

    def test_unmeasured_speed_fails(self):
        assert not success_by_speed_rule(self._bundle(None, _row(0.1)), 0.95).ok

    def test_a_significant_regression_fails(self):
        bundle = self._bundle(_speed(0.5), _row(-0.1, True))
        assert not success_by_speed_rule(bundle, 0.95).ok


# ---- whole waves against the fakes ----


def _project(tmp_path: Path, train_n: int = 8) -> Path:
    """A project on disk with eval_1's splits and `train_n` train and eight
    val inputs — the drives read inputs from disk, as the orchestrator does."""
    task = tmp_path / "proj" / "tasks" / "t1 - T"
    evals = task / "evals" / "eval_1 - E"
    evals.mkdir(parents=True)
    splits = {
        n: {"source": "eval_input", "filter_id": f"tag::{n}"}
        for n in ("train", "val", "test")
    }
    (evals / "eval.kiln").write_text(json.dumps({"id": "eval_1", "splits": splits}))
    for split in ("train", "val", "test"):
        for n in range(train_n if split == "train" else 8):
            item = task / "eval_inputs" / f"{split}{n}"
            item.mkdir(parents=True)
            (item / "eval_input.kiln").write_text(
                json.dumps(
                    {
                        "id": f"{split}{n}",
                        "tags": [split],
                        "data": {
                            "type": "single_turn",
                            "user_message": {"text": f"{split} input {n}"},
                        },
                    }
                )
            )
    return tmp_path / "proj"


def _rc(rc_id: str, provider: str = "openai", model: str = "m") -> dict:
    return {
        "id": rc_id,
        "name": rc_id,
        "created_at": "2026-09-20T10:00:00-04:00",
        "run_config_properties": {
            "type": "kiln_agent",
            "model_provider_name": provider,
            "model_name": model,
        },
    }


def _seed_rows(kiln, rc_id: str, scores: list[float], stored: bool = False) -> None:
    """eval_1/train rows on the `_project` train inputs, `train{n}`."""
    rows = [make_run(f"train{n}", accuracy=v) for n, v in enumerate(scores)]
    if stored:
        kiln.results[("eval_1", rc_id, "train")] = rows
    else:
        kiln.pending[("eval_1", rc_id, "train")] = rows


def _speed_ctx(
    tmp_path,
    cand_ms: float,
    *,
    val_candidate: float = 1.0,
    early_ms: float | None = None,
    early_n: int = 6,
    output: str = '{"prompts": []}',
    train_n: int = 8,
    cand_cost: float = 0.001,
    objective: str = "speed",
    parent_ms: float | None = None,
):
    """`early_ms`: the candidate's latency on train inputs 0..`early_n`-1
    only (default: `cand_ms` everywhere); `output`: every drive's output;
    `parent_ms`: the arm is forked from `rc_fast`, this fast.
    The stored train rows name the project's own train inputs (the engine
    draws its queue from both)."""
    ctx = make_ctx(gate_agent="auto")
    ctx.objective = objective
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(_project(tmp_path, train_n))}
    )
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    _seed_rows(kiln, "rc_base", [0.0] + [1.0] * (train_n - 1), stored=True)
    _seed_rows(kiln, "rc_cand", [1.0] * train_n)
    if parent_ms is not None:  # the fork source's own stored train rows
        kiln.results[("eval_1", "rc_fast", "train")] = list(
            kiln.results[("eval_1", "rc_base", "train")]
        )
    kiln.pending[("eval_1", "rc_base", "val")] = [
        make_run("v1", accuracy=1.0),
        make_run("v2", accuracy=1.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "val")] = [
        make_run("v1", accuracy=val_candidate),
        make_run("v2", accuracy=val_candidate),
    ]
    kiln.run_configs = [_rc("rc_base"), _rc("rc_cand"), _rc("rc_fast")]

    early_texts = {f"train input {n}" for n in range(early_n)}

    def handler(body: dict) -> dict:
        text = body.get("plaintext_input") or ""
        if body.get("task_run_config_id") == "rc_base":
            ms = 1000.0
        elif body.get("task_run_config_id") == "rc_fast":
            ms = parent_ms or 1000.0
        elif early_ms is not None and text in early_texts:
            ms = early_ms
        else:
            ms = cand_ms
        base = body.get("task_run_config_id") == "rc_base"
        return {
            "id": "r",
            "usage": {
                "total_llm_latency_ms": ms + len(text),
                "cost": 0.001 if base else cand_cost,
            },
            "output": {"output": output},
        }

    kiln.run_handler = handler
    d = ctx.dispatch
    parent = "rc_fast" if parent_ms is not None else None
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1", parent_rc_id=parent))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    return ctx


async def test_a_clearly_faster_candidate_with_quality_held_is_promoted(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    assert await orchestrate(ctx, "go fast", timeout_mins=180) == "done"
    assert "rc_rc_cand" in ctx.kiln.memories["mem_baseline"]["tags"]
    # The planner knew the objective.
    assert ctx.dispatch.calls_to("batch_plan")[0].objective == "speed"
    # Drives: both arms on train inputs (the item) and on val inputs (the
    # gate) — never a test input.
    driven = {
        (p["task_run_config_id"], p["plaintext_input"].split()[0])
        for p in ctx.kiln.run_posts
    }
    assert driven == {
        ("rc_base", "train"),
        ("rc_cand", "train"),
        ("rc_base", "val"),
        ("rc_cand", "val"),
    }
    (speed,) = [f for k, f in ctx.log.events if k == "speed_result"]
    assert speed["latency"]["clearly_faster"] is True
    # The verdict record carries the code-composed latency line.
    exp = [m for m in ctx.kiln.memories.values() if "experiment" in m["tags"]]
    assert "latency (train, paired_drive" in exp[0]["content"]


async def test_a_faster_candidate_that_loses_quality_on_val_is_not_promoted(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0, val_candidate=0.0)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert "rc_rc_base" in ctx.kiln.memories["mem_baseline"]["tags"]
    (promotion,) = [f for k, f in ctx.log.events if k == "promotion"]
    assert promotion["winner_rc_id"] is None


def _details(ctx) -> list[str]:
    return [f["detail"] for k, f in ctx.log.events if k == "item_detail"]


def _outcome_of(ctx):
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    return outcome


async def test_a_candidate_not_faster_on_train_never_reaches_the_gate(tmp_path):
    """Through the engine: not faster once its timing pairs are
    done, the arm stops on `not_better` before its first chunk is judged:
    no judge call at all, no analyst, no val."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert not [j for j in ctx.kiln.job_invocations if j[1] == "rc_cand"]
    assert not ctx.dispatch.calls_to("analyze_experiment_results")
    assert not [j for j in ctx.kiln.job_invocations if j[2] == "val"]
    outcome = _outcome_of(ctx)
    assert outcome.status == "not_landed" and outcome.speed is not None
    assert outcome.measurement is not None
    assert outcome.measurement.stop_kind == "not_better"
    assert outcome.measurement.judged == 0
    (line,) = [f["line"] for k, f in ctx.log.events if k == "measurement"]
    assert line.startswith("w1-1: 0 judged")
    assert "latency 1.00× (0/8 faster)" in line
    assert "stopped — stopped at n=8: not faster" in line
    # The drives: the baseline's own timing, then the 8 pairs.
    assert len(ctx.kiln.run_posts) == 8 + 2 * 8
    exp = [m for m in ctx.kiln.memories.values() if "experiment" in m["tags"]]
    assert (
        "NOT LANDED (stopped at n=8 by not_better: not faster" in (exp[0]["overview"])
    )
    assert exp[0]["overview"].startswith("EXP ")  # the hypothesis is kept


async def test_a_candidate_fast_on_its_first_pairs_is_refuted_by_the_rest(
    tmp_path,
):
    """Fast on 6 of 24 items, flat on the rest: the first timing look (15
    pairs) cannot call it slower, and the completed timing says it is not
    faster — stopped before its second chunk is judged."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, early_ms=300.0, train_n=24)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    details = _details(ctx)
    assert any(
        d.startswith("w1-1: chunk 1/") and d.endswith("continue") for d in details
    )
    assert any(
        d.startswith("w1-1: chunk 2/") and "stopped: not_better" in d for d in details
    )
    assert not any(d.startswith("measuring eval_1") for d in details)
    assert not ctx.dispatch.calls_to("analyze_experiment_results")
    # Judged: the first chunk only.
    assert _outcome_of(ctx).measurement.chunks == 2
    assert 0 < _outcome_of(ctx).measurement.judged < 24
    # The report reads "partly: <judged> of 24 items".
    m = _outcome_of(ctx).measurement
    assert sum(m.planned_per_eval.values()) == 24
    assert sum(m.judged_per_eval.values()) == m.judged


async def test_a_fast_candidate_with_broken_output_is_stopped_unjudged(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=300.0, output="")
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert not [j for j in ctx.kiln.job_invocations if j[1] == "rc_cand"]
    (line,) = [f["line"] for k, f in ctx.log.events if k == "measurement"]
    assert "invalid output: 8 of 8 outputs fail the task's output schema" in line
    assert _outcome_of(ctx).status == "not_landed"


async def test_a_clearly_faster_arm_is_timed_before_its_quality_is_paid(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    details = _details(ctx)
    chunk = next(i for i, d in enumerate(details) if d.startswith("w1-1: chunk 1/"))
    assert "× latency" in details[chunk]
    measuring = next(
        i for i, d in enumerate(details) if d.startswith("measuring eval_1")
    )
    assert chunk < measuring


async def test_a_faster_arm_that_loses_train_quality_is_not_successful(tmp_path):
    """The analyst says successful; the code's train rule under speed
    (faster AND quality equal or better) is the floor and says no."""
    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    _seed_rows(ctx.kiln, "rc_base", [1.0] * 8, stored=True)
    _seed_rows(ctx.kiln, "rc_cand", [0.0, 0.0] + [1.0] * 6)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    outcome = _outcome_of(ctx)
    assert outcome.result is not None and outcome.result.successful is False
    assert "rc_rc_cand" not in ctx.kiln.memories["mem_baseline"]["tags"]


async def test_a_faster_arm_with_equal_quality_is_successful(tmp_path):
    """Under speed, flat quality is enough — the quality objective's rule
    (the targeted eval must improve) does not apply."""
    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    _seed_rows(ctx.kiln, "rc_base", [0.0] + [1.0] * 7, stored=True)
    _seed_rows(ctx.kiln, "rc_cand", [1.0, 0.0] + [1.0] * 6)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    outcome = _outcome_of(ctx)
    assert outcome.result is not None and outcome.result.successful is True


async def test_a_screened_survivor_goes_through_the_same_engine(tmp_path):
    """There is no viability stage left to skip. A survivor of a
    screening round is measured by the same engine; the rows the round
    stored are reused by the server-side skip."""
    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    ctx.screened_rcs = {"rc_cand": 1}
    await orchestrate(ctx, "go fast", timeout_mins=180)
    (m,) = [f for k, f in ctx.log.events if k == "measurement"]
    assert m["status"] == "completed"


# ---- the model policy in the loop ----


async def test_a_run_config_outside_the_policy_is_never_measured(tmp_path):
    ctx = make_ctx(gate_agent="auto")
    ctx.model_policy = POLICY
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    kiln.run_configs = [_rc("rc_base"), _rc("rc_cand", "groq", "kimi_k3")]
    d = ctx.dispatch
    d.queue(
        "batch_plan", ok(make_plan(fix_item("w1-1"))), ok(make_plan(loop_exit="done"))
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not [j for j in kiln.job_invocations if j[1] == "rc_cand"]
    refused = [f for k, f in ctx.log.events if k == "model_policy_refused"]
    assert refused and "outside the model policy" in refused[0]["reason"]
    (outcome,) = [f for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome["outcome"].status == "failed_infra"


async def test_under_speed_a_refused_run_config_is_never_driven(tmp_path):
    """Under speed the first spend is timing drives, not an eval job: the
    policy check comes before them."""
    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    ctx.model_policy = POLICY
    ctx.kiln.run_configs = [_rc("rc_base"), _rc("rc_cand", "groq", "kimi_k3")]
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert not [p for p in ctx.kiln.run_posts if p["task_run_config_id"] == "rc_cand"]
    assert not [j for j in ctx.kiln.job_invocations if j[1] == "rc_cand"]


async def test_the_baseline_is_exempt_and_named_when_outside_the_policy(tmp_path):
    ctx = make_ctx(gate_agent="auto")
    ctx.model_policy = POLICY
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    kiln.run_configs = [_rc("rc_base", "groq", "kimi_k3")]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert [f for k, f in ctx.log.events if k == "baseline_outside_policy"]
    assert await policy_refusal(ctx, "rc_base") is None


async def test_the_exception_pair_is_measured(tmp_path):
    ctx = make_ctx()
    ctx.model_policy = POLICY
    ctx.kiln.run_configs = [
        _rc("rc_x", "groq", "fast_one"),
        _rc("rc_y", "groq", "slow"),
    ]
    assert await policy_refusal(ctx, "rc_x") is None
    assert "outside the model policy" in (await policy_refusal(ctx, "rc_y") or "")


async def test_the_planner_gets_the_policy_and_the_allowed_models(tmp_path):
    ctx = make_ctx()
    ctx.model_policy = POLICY
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    kiln.available_models = [
        {"provider_id": "openai", "models": [{"id": "gpt_5_4_mini"}]},
        {"provider_id": "groq", "models": [{"id": "fast_one"}, {"id": "slow"}]},
    ]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert {(m["provider"], m["model"]) for m in plan_input.allowed_models} == {
        ("openai", "gpt_5_4_mini"),
        ("groq", "fast_one"),
    }
    assert plan_input.model_policy is not None
    assert plan_input.objective == "quality"


async def test_the_analyzer_guard_is_awaited_before_any_eval_job():
    ctx = make_ctx()

    async def refuse(rc_id: str) -> None:
        raise ModelPolicyRefused(f"rc {rc_id} refused")

    ctx.analyzer.rc_guard = refuse
    with pytest.raises(ModelPolicyRefused):
        await ctx.analyzer.ensure_measured("eval_1", "ec_1", "rc_x", "train")
    assert ctx.kiln.job_invocations == []


# ---- the analyzer's held-out check ----


async def test_a_train_read_that_returns_a_heldout_item_raises():
    ctx = make_ctx()
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("t1", accuracy=1.0),
        make_run("secret", accuracy=1.0),
    ]
    ctx.analyzer.heldout_ids = frozenset({"secret"})
    with pytest.raises(HeldOutLeakError) as err:
        await ctx.analyzer.stored_results("eval_1", "ec_1", "rc_base", "train")
    assert isinstance(err.value, AnalysisError) and "secret" in str(err.value)


async def test_the_val_read_is_the_orchestrators_and_is_not_checked():
    ctx = make_ctx()
    ctx.kiln.results[("eval_1", "rc_base", "val")] = [make_run("secret", accuracy=1.0)]
    ctx.analyzer.heldout_ids = frozenset({"secret"})
    rows = await ctx.analyzer.stored_results("eval_1", "ec_1", "rc_base", "val")
    assert len(rows) == 1


# ---- speed beside quality in the report ----


def test_the_report_appendix_shows_speed_beside_quality():
    from kiln_harness_tuner.report import render_speed_appendix
    from kiln_harness_tuner.status import RunStatusTracker

    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="g")
    tracker.event("run_start", objective="speed", timeout_mins=60)
    tracker.event("wave_start", wave=1, items=[])
    tracker.event("speed_result", rc_id="rc_a", latency=_speed(0.5).model_dump())
    status = tracker.snapshot()
    assert status.objective == "speed"
    text = render_speed_appendix(status)
    assert "## Speed and cost (code-computed)" in text
    assert "| 1 | rc_a | 12 |" in text and "| yes |" in text


def test_no_speed_appendix_for_a_quality_run_that_measured_no_speed():
    from kiln_harness_tuner.report import render_speed_appendix
    from kiln_harness_tuner.status import RunStatusTracker

    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="g")
    tracker.event("run_start", timeout_mins=60)
    assert render_speed_appendix(tracker.snapshot()) == ""


async def test_the_gate_drops_a_candidate_outside_the_policy_before_the_val_pass():
    """Defence in depth behind the analyzer guard: an rc that reached the gate
    by any route is refused there, before any val job runs."""
    from kiln_harness_tuner.orchestrator import maybe_promote
    from kiln_harness_tuner.schemas import ItemOutcome

    ctx = make_ctx(gate_agent="auto")
    ctx.model_policy = POLICY
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    kiln.run_configs = [_rc("rc_base"), _rc("rc_cand", "groq", "kimi_k3")]
    outcome = ItemOutcome(
        item=fix_item("w1-1"),
        status="completed",
        rc_id="rc_cand",
        experiment_memory_id="mem_x",
        result=make_result(),
        infra_error=None,
    )
    record = await ctx.memory.require_baseline_record()
    assert await maybe_promote(ctx, [outcome], record, wave=1) is None
    assert not [j for j in kiln.job_invocations if j[2] == "val"]
    (blocked,) = [f for k, f in ctx.log.events if k == "promotion_blocked"]
    assert "outside the model policy" in blocked["reasons"][0]


# ---- quality must be EQUAL OR BETTER under speed ----


def _rating(
    base: float, cand: float, n: int = 20, direction: str = "higher_is_better"
) -> PerEvalResult:
    delta = cand - base if direction == "higher_is_better" else base - cand
    return PerEvalResult(
        eval_id="stars",
        score_key="s",
        score_type="five_star",
        direction=direction,
        split="val",
        baseline_rc_id="rc_base",
        compared_to="prior_baseline",
        n_items=n,
        delta=delta,
        p_value=0.6,
        significant=False,
        fail_to_pass=None,
        pass_to_fail=None,
        spend_usd=0.0,
        baseline_score=base,
        candidate_score=cand,
    )


def _eligible(*rows: PerEvalResult) -> bool:
    table = _table("rc_a", 0.5, *rows)
    apply_speed_eligibility(table)
    return not table.ineligible


class TestEqualOrBetter:
    @pytest.mark.parametrize(
        ("n", "allowed"),
        [
            (1, 1),
            (4, 1),
            (5, 1),
            (10, 1),
            (14, 1),
            (15, 2),
            (16, 2),
            (20, 2),
            (24, 2),
            (25, 3),
            (30, 3),
        ],
    )
    def test_the_per_eval_noise_allowance(self, n, allowed):
        from kiln_harness_tuner.analysis import (
            SPEED_ROW_LOSS_FRACTION,
            row_loss_tolerance,
        )

        assert SPEED_ROW_LOSS_FRACTION == 0.10
        assert row_loss_tolerance(n) == allowed

    def test_a_real_15pp_drop_that_is_not_significant_is_refused(self):
        # 3 of 20 lost, p=0.25: the old rule let this through.
        row = _row(-0.15, eval_id="e1", f2p=0, p2f=3)
        assert not row.significant
        assert not _eligible(row, _row(0.0, eval_id="e2"))

    def test_pooled_small_losses_are_refused(self):
        # Two evals each down one item, nothing up: within each allowance,
        # worse on balance.
        rows = (_row(-0.05, eval_id="e1"), _row(-0.05, eval_id="e2"))
        assert not _eligible(*rows)

    def test_small_losses_balanced_by_gains_pass(self):
        rows = (
            _row(-0.05, eval_id="e1"),
            _row(-0.05, eval_id="e2"),
            _row(+0.10, eval_id="e3"),
        )
        assert _eligible(*rows)

    def test_one_eval_over_its_allowance_is_refused_even_when_net_positive(self):
        rows = (_row(-0.15, eval_id="e1"), _row(+0.25, eval_id="e2"))
        table = _table("rc_a", 0.5, *rows)
        apply_speed_eligibility(table)
        assert table.ineligible
        assert any("noise allowance" in e.detail for e in table.errors)

    def test_a_loss_exactly_at_the_allowance_passes_when_balanced(self):
        rows = (_row(-0.10, eval_id="e1"), _row(+0.10, eval_id="e2"))
        assert _eligible(*rows)

    def test_flips_not_the_delta_are_the_binary_measure(self):
        # Churn: 3 fixed, 3 broken — net zero, allowed.
        assert _eligible(_row(0.0, f2p=3, p2f=3))

    def test_a_lower_is_better_binary_score_reads_its_flips_reversed(self):
        row = _row(-0.15, f2p=3, p2f=0)
        row.direction = "lower_is_better"
        assert not _eligible(row)

    def test_a_rating_row_pools_as_normalized_items(self):
        from kiln_harness_tuner.analysis import row_net_items

        # five_star 4.0 → 3.2 on 20 items: (3.2 − 4.0) / 4 × 20 = −4 items.
        assert row_net_items(_rating(4.0, 3.2)) == pytest.approx(-4.0)
        assert not _eligible(_rating(4.0, 3.2))
        # 4.0 → 3.8: −1 item, balanced by one pass/fail gain.
        assert _eligible(_rating(4.0, 3.8), _row(+0.05, eval_id="e2"))
        # lower_is_better: a falling mean is a gain.
        assert row_net_items(_rating(3.0, 2.2, direction="lower_is_better")) > 0

    @pytest.mark.parametrize("score_type", ["custom", None])
    def test_a_row_that_cannot_be_pooled_makes_the_candidate_ineligible(
        self, score_type
    ):
        row = _rating(4.0, 4.0)
        row.score_type = score_type
        table = _table("rc_a", 0.5, row)
        apply_speed_eligibility(table)
        assert table.ineligible
        assert any("cannot be pooled" in e.detail for e in table.errors)

    def test_no_quality_row_is_not_equal_quality(self):
        assert not _eligible()

    def test_the_train_rule_applies_the_same_rule(self):
        bundle = AnalysisBundle(
            per_eval=[_row(-0.15, f2p=0, p2f=3)],
            spend_total_usd=0.0,
            errors=[],
            latency=_speed(0.5),
        )
        verdict = success_by_speed_rule(bundle, 0.95)
        assert not verdict.ok
        assert any("noise allowance" in r for r in verdict.reasons)
        empty = AnalysisBundle(
            per_eval=[], spend_total_usd=0.0, errors=[], latency=_speed(0.5)
        )
        assert not success_by_speed_rule(empty, 0.95).ok

    def test_the_rationale_states_the_quality_rule(self):
        tables = [_table("rc_a", 0.5, _row(+0.05))]
        apply_speed_eligibility(tables[0])
        rationale = decide_speed_gate(tables).rationale
        assert "equal or better" in rationale and "+1.0 net items" in rationale


def test_real_rows_carry_their_score_type_and_direction():
    """The rule reads `score_type`/`direction` off the rows the analyzer
    builds; a row missing them would be unpoolable and block every speed
    candidate."""
    import asyncio

    from kiln_harness_tuner.analysis import ScoreSpec

    ctx = make_ctx()
    spec = ScoreSpec(key="accuracy", type="pass_fail", direction="higher_is_better")
    rows, _holes = asyncio.run(
        ctx.analyzer.compare_rows(
            eval_id="eval_1",
            specs=[spec],
            candidate_runs=[make_run("a", accuracy=1.0), make_run("b", accuracy=1.0)],
            baseline_runs=[make_run("a", accuracy=0.0), make_run("b", accuracy=1.0)],
            baseline_rc_id="rc_base",
            compared_to="prior_baseline",
            split="val",
        )
    )
    (row,) = rows
    assert (row.score_type, row.direction) == ("pass_fail", "higher_is_better")
    assert (row.fail_to_pass, row.pass_to_fail) == (1, 0)


async def test_the_planner_and_the_analyst_are_given_the_exact_rule(tmp_path):
    from kiln_harness_tuner.analysis import speed_rule_text

    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    rule = speed_rule_text()
    assert "equal or better" in rule and "max(1, round(0.10" in rule
    assert ctx.dispatch.calls_to("batch_plan")[0].speed_rule == rule
    assert ctx.dispatch.calls_to("analyze_experiment_results")[0].speed_rule == rule


async def test_no_speed_rule_under_the_quality_objective():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert ctx.dispatch.calls_to("batch_plan")[0].speed_rule is None


async def test_under_cost_a_slower_but_clearly_cheaper_arm_is_promoted(tmp_path):
    """--objective cost: the same machinery, the cost per request
    of the task call decides; latency is reported, not required."""
    ctx = _speed_ctx(tmp_path, cand_ms=1500.0, cand_cost=0.0002, objective="cost")
    assert await orchestrate(ctx, "cheaper", timeout_mins=180) == "done"
    assert "rc_rc_cand" in ctx.kiln.memories["mem_baseline"]["tags"]
    (promotion,) = [f for k, f in ctx.log.events if k == "promotion"]
    assert promotion["rationale"].startswith(
        "code rule (cost): promote — clearly cheaper"
    )
    assert ctx.dispatch.calls_to("batch_plan")[0].objective == "cost"
    assert "Objective cost" in ctx.dispatch.calls_to("batch_plan")[0].speed_rule


async def test_under_cost_a_faster_but_not_cheaper_arm_stops(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=300.0, cand_cost=0.001, objective="cost")
    await orchestrate(ctx, "cheaper", timeout_mins=180)
    (m,) = [f for k, f in ctx.log.events if k == "measurement"]
    assert m["stop_kind"] == "not_better" and "not cheaper" in m["line"]
    assert "rc_rc_base" in ctx.kiln.memories["mem_baseline"]["tags"]


class TestPriorityWeights:
    """The pooled net is weighted by the eval's priority (p0 = 4 … p3 = 1,
    no priority = 1); the per-eval noise cap is not."""

    def test_the_weights(self):
        from kiln_harness_tuner.analysis import UNPRIORITISED_WEIGHT, priority_weight

        assert [priority_weight(p) for p in (0, 1, 2, 3)] == [4.0, 3.0, 2.0, 1.0]
        assert priority_weight(None) == UNPRIORITISED_WEIGHT == 1.0
        assert priority_weight(True) == 1.0 and priority_weight(7) == 1.0

    def test_a_loss_on_a_p0_eval_outweighs_a_gain_on_a_p3_eval(self):
        from kiln_harness_tuner.analysis import speed_quality_reasons

        rows = (_row(-0.05, eval_id="e1"), _row(+0.05, eval_id="e2"))
        assert speed_quality_reasons(rows) == []  # unweighted: net 0
        reasons = speed_quality_reasons(rows, {"e1": 4.0, "e2": 1.0})
        assert reasons == [
            "speed: quality is worse on balance — -3.0 priority-weighted net"
            " item(s) pooled over every compared row (must be >= 0)"
        ]
        # And the other way round it passes.
        assert speed_quality_reasons(rows, {"e1": 1.0, "e2": 4.0}) == []

    def test_the_noise_cap_is_not_weighted(self):
        from kiln_harness_tuner.analysis import speed_quality_reasons

        # Two items down on a p0 eval of 20: within its 2-item cap.
        rows = (_row(-0.10, eval_id="e1"), _row(+0.40, eval_id="e2"))
        assert speed_quality_reasons(rows, {"e1": 4.0, "e2": 1.0}) == []

    async def test_the_gate_reads_the_spec_priorities(self):
        from kiln_harness_tuner.orchestrator import eval_weights

        ctx = make_ctx()
        ctx.kiln.specs = [
            {"id": "s1", "eval_id": "e1", "status": "active", "priority": 0},
            {"id": "s2", "eval_id": "e2", "status": "active"},
        ]
        assert await eval_weights(ctx) == {"e1": 4.0}

    async def test_the_train_rule_and_the_gate_get_the_weights(
        self, tmp_path, monkeypatch
    ):
        from kiln_harness_tuner import items as items_mod
        from kiln_harness_tuner import orchestrator as orch_mod

        seen: dict[str, object] = {}
        real_rule = items_mod.success_by_speed_rule
        real_gate = orch_mod.apply_speed_eligibility

        def rule(bundle, floor, objective, weights=None):
            seen["train"] = weights
            return real_rule(bundle, floor, objective, weights)

        def gate(table, objective, weights=None):
            seen["val"] = weights
            return real_gate(table, objective, weights)

        monkeypatch.setattr(items_mod, "success_by_speed_rule", rule)
        monkeypatch.setattr(orch_mod, "apply_speed_eligibility", gate)
        ctx = _speed_ctx(tmp_path, cand_ms=400.0)
        ctx.kiln.specs = [
            {"id": "s1", "eval_id": "eval_1", "status": "active", "priority": 0}
        ]
        await orchestrate(ctx, "go fast", timeout_mins=180)
        assert seen == {"train": {"eval_1": 4.0}, "val": {"eval_1": 4.0}}


# ---- an arm forked from a fast, non-baseline parent ----


async def test_a_prompt_fix_on_a_fast_parent_is_judged_against_the_baseline(
    tmp_path,
):
    """Run 260924-2248's w4-4: 1.3× slower than its fast parent, 4× faster
    than the baseline — clearly faster than the baseline on its timing
    pairs, within the noise of its parent, completed and promoted."""
    ctx = _speed_ctx(tmp_path, cand_ms=260.0, parent_ms=200.0, train_n=24)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    (m,) = [f for k, f in ctx.log.events if k == "measurement"]
    assert m["status"] == "completed"
    assert "latency vs its parent 1.2" in m["line"]
    # The parent was driven beside the baseline and the candidate.
    driven = {p["task_run_config_id"] for p in ctx.kiln.run_posts}
    assert {"rc_base", "rc_fast", "rc_cand"} <= driven
    assert "rc_rc_cand" in ctx.kiln.memories["mem_baseline"]["tags"]


async def test_clearly_slower_than_its_parent_on_the_full_pass_does_not_land(
    tmp_path,
):
    """Near its parent on 6 items, 3× slower on the rest: faster than the
    baseline, but clearly slower than its parent once the 24 pairs are in."""
    ctx = _speed_ctx(
        tmp_path, cand_ms=600.0, early_ms=250.0, parent_ms=200.0, train_n=24
    )
    await orchestrate(ctx, "go fast", timeout_mins=180)
    (outcome,) = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcome.status == "not_landed"
    assert outcome.measurement.stop_kind == "not_better"
    assert "clearly slower than its parent:" in outcome.speed_verdict
    assert not ctx.dispatch.calls_to("analyze_experiment_results")
