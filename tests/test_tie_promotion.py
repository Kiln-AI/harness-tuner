"""Promotion on a tie (`kiln_harness_tuner.tie`, specs/projects/tie-promotion).

Knowledge settles a tie, never a loss: when the gate's evidence step
chooses no winner, a candidate whose technique document says "Keep it." is
promoted when code shows that it is not worse than the baseline on any eval,
on cost and on latency. Code decides "not worse"."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import ok
from fake_kiln import make_run
from test_orchestrator import decision, seed_active_eval, seed_baseline, seed_issue
from test_speed_objective import _project, _rc

from kiln_harness_tuner import tie
from kiln_harness_tuner.knowledge import load
from kiln_harness_tuner.orchestrator import (
    GateCandidate,
    _mark_tie_candidates,
    _settle_tie,
    orchestrate,
)
from kiln_harness_tuner.report import render_fallback_report
from kiln_harness_tuner.schemas import (
    CandidateValTable,
    ErrorInfo,
    EvalTarget,
    ExperimentItem,
    ItemOutcome,
    LatencyComparison,
    MergeWork,
    NoiseRow,
    PerEvalResult,
    PromotionDecision,
    RcBuildResult,
    StopRule,
    TieCheck,
    WinRef,
)
from kiln_harness_tuner.stop_rules import default_stop_rules
from kiln_harness_tuner.tie import TechniqueStance
from kiln_harness_tuner.tui.model import log as tui_log

# ---- a small knowledge tree: one technique for each case ----

_TECHNIQUE = """---
title: {title}
description: A technique of the test tree, which changes the harness of the agent.
reviewed: 2026-01-01
status: {status}
levers:
  - {lever}
applies_to: all
cost: low
---

# {title}

## When to use

When a test needs it.

## If it does not move the evals

{stance} With a condition.

## Confidence

High, in a test.
"""


def _knowledge(tmp_path: Path):
    techniques = tmp_path / "knowledge" / "references" / "techniques"
    techniques.mkdir(parents=True)
    docs = {
        "t-keep": ("Keep it.", "active", "prompt"),
        "t-move": ("It must move an eval.", "active", "prompt"),
        "t-depends": ("It depends.", "active", "prompt"),
        "t-dormant": ("Keep it.", "dormant", "prompt"),
        "t-tools": ("Keep it.", "active", "tool_config"),
        "t-no-phrase": ("Perhaps keep it.", "active", "prompt"),
    }
    for name, (stance, status, lever) in docs.items():
        (techniques / f"{name}.md").write_text(
            _TECHNIQUE.format(
                title=f"Technique {name}", stance=stance, status=status, lever=lever
            )
        )
    return load(tmp_path / "knowledge")


# ---- the technique that a build names ----


class TestTheStance:
    @pytest.mark.parametrize(
        ("technique_id", "stance"),
        [("t-keep", "keep"), ("t-move", "must_move"), ("t-depends", "depends")],
    )
    def test_each_phrase_is_read_from_the_document(
        self, tmp_path, technique_id, stance
    ):
        got = tie.resolve_stance(_knowledge(tmp_path), technique_id, "prompt")
        assert (got.technique_id, got.stance) == (technique_id, stance)
        assert got.keep is (stance == "keep")

    @pytest.mark.parametrize(
        ("technique_id", "why"),
        [
            (None, "named no technique"),
            ("", "named no technique"),
            ("not-a-document", "no technique document"),
            ("t-dormant", "not active"),
            ("t-tools", "is for the levers"),
            ("t-no-phrase", "has no phrase"),
        ],
    )
    def test_anything_else_is_no_stance(self, tmp_path, technique_id, why):
        got = tie.resolve_stance(_knowledge(tmp_path), technique_id, "prompt")
        assert got.stance is None and not got.keep
        assert why in got.note

    def test_no_knowledge_is_no_stance(self):
        assert tie.resolve_stance(None, "t-keep", "prompt").stance is None

    def test_the_real_knowledge_skill_gives_a_stance_to_each_technique(self):
        """The gate and the knowledge check read the phrase the same way."""
        base = load()
        for doc in base.by_kind("techniques"):
            if doc.meta is None or doc.meta.status != "active":  # type: ignore[attr-defined]
                continue
            lever = doc.meta.levers[0]  # type: ignore[attr-defined]
            assert tie.resolve_stance(base, doc.id, lever).stance is not None, doc.id


# ---- "not worse" on quality ----


def _row(
    eval_id: str = "eval_1",
    *,
    fixed: int = 0,
    broke: int = 0,
    n: int = 20,
    significant: bool = False,
    score_key: str = "accuracy",
) -> PerEvalResult:
    return PerEvalResult(
        eval_id=eval_id,
        score_key=score_key,
        split="val",
        baseline_rc_id="rc_base",
        compared_to="prior_baseline",
        n_items=n,
        delta=(fixed - broke) / n,
        p_value=0.01 if significant else 0.5,
        significant=significant,
        fail_to_pass=fixed,
        pass_to_fail=broke,
        spend_usd=0.0,
        score_type="pass_fail",
        direction="higher_is_better",
    )


def _noise(eval_id: str = "eval_1", *, items: int = 20, changed: int = 2) -> NoiseRow:
    return NoiseRow(
        eval_id=eval_id,
        score_key="accuracy",
        score_type="pass_fail",
        baseline_rc_id="rc_base",
        items=items,
        redrive_changed=changed,
    )


def _table(*rows: PerEvalResult, **kwargs) -> CandidateValTable:
    kwargs.setdefault("integrity_checked", True)
    return CandidateValTable(
        rc_id="rc_cand", experiment_memory_id="mem_x", per_eval=list(rows), **kwargs
    )


class TestTheNoiseAllowance:
    def test_a_run_that_measured_no_noise_gives_none(self):
        assert tie.row_allowance(_row(), []) == 0
        # A noise row of another eval, or of another score, is not this one's.
        assert tie.row_allowance(_row(), [_noise("eval_2")]) == 0

    def test_it_is_the_measured_rate_times_the_items(self):
        assert tie.row_allowance(_row(n=20), [_noise(items=20, changed=2)]) == 2
        assert tie.row_allowance(_row(n=20), [_noise(items=20, changed=1)]) == 1
        assert tie.row_allowance(_row(n=20), [_noise(items=20, changed=0)]) == 0

    def test_it_is_never_above_the_speed_gate_allowance(self):
        # 50% measured noise on 20 items would be 10; the speed gate allows 2.
        assert tie.row_allowance(_row(n=20), [_noise(items=20, changed=10)]) == 2

    def test_a_rating_reads_the_mean_change_as_a_share_of_the_scale(self):
        row = _row(n=20).model_copy(update={"score_type": "five_star"})
        noise = NoiseRow(
            eval_id="eval_1",
            score_key="accuracy",
            score_type="five_star",
            baseline_rc_id="rc_base",
            items=10,
            redrive_changed=4,
            redrive_mean_abs_change=0.4,  # of a scale 4 wide: 0.10 per item
        )
        assert tie.noise_per_item([noise], "eval_1", "accuracy") == pytest.approx(0.1)
        assert tie.row_allowance(row, [noise]) == 2


class TestNotWorseOnQuality:
    def test_a_tie_passes(self):
        assert tie.quality_reasons(_table(_row())) == []
        assert tie.quality_reasons(_table(_row(fixed=2, broke=2))) == []

    def test_a_small_loss_fails_when_no_noise_was_measured(self):
        (reason,) = tie.quality_reasons(_table(_row(fixed=1, broke=2)))[:1]
        assert "lost 1.0 net item(s) of 20, over its noise allowance of 0" in reason

    def test_a_loss_inside_the_noise_still_needs_a_pooled_net_of_zero(self):
        noise = [_noise("eval_1"), _noise("eval_2")]
        one = _table(_row(fixed=1, broke=2))
        (reason,) = tie.quality_reasons(one, None, noise)
        assert "worse on balance" in reason
        two = _table(_row(fixed=1, broke=2), _row("eval_2", fixed=1))
        assert tie.quality_reasons(two, None, noise) == []

    def test_a_loss_over_the_allowance_fails_whatever_the_other_evals_gain(self):
        noise = [_noise("eval_1"), _noise("eval_2")]
        table = _table(_row(broke=3), _row("eval_2", fixed=3))
        (reason,) = tie.quality_reasons(table, None, noise)
        assert "eval_1/accuracy lost 3.0" in reason

    def test_priorities_weigh_the_pooled_net(self):
        noise = [_noise("eval_1"), _noise("eval_2")]
        table = _table(_row(broke=1), _row("eval_2", fixed=1))
        assert tie.quality_reasons(table, None, noise) == []
        # A loss on a p0 eval is not paid by the same gain on a p3 eval.
        reasons = tie.quality_reasons(table, {"eval_1": 4.0, "eval_2": 1.0}, noise)
        assert "worse on balance" in reasons[0]

    def test_a_significant_drop_fails(self):
        noise = [_noise(changed=10)]
        table = _table(_row(broke=2, significant=True), _row("eval_2", fixed=5))
        assert any(
            "regressed significantly" in r
            for r in tie.quality_reasons(table, None, noise)
        )

    def test_a_table_with_a_hole_fails(self):
        table = _table(
            _row(),
            ineligible=True,
            errors=[ErrorInfo(error_class="provider_error", detail="fill failed")],
        )
        assert "incomplete" in tie.quality_reasons(table)[0]

    def test_an_eval_the_gate_left_out_fails(self):
        table = _table(_row(), skipped=["eval_9/tone: no statistics operation"])
        assert "could not compare" in tie.quality_reasons(table)[0]

    def test_an_integrity_flag_fails(self):
        table = _table(_row(), integrity_flags=["review: paraphrases the judge"])
        assert "integrity flag" in tie.quality_reasons(table)[0]

    def test_a_table_that_was_not_checked_fails(self):
        assert (
            "did not run"
            in (tie.quality_reasons(_table(_row(), integrity_checked=False))[0])
        )

    def test_no_row_fails(self):
        assert tie.quality_reasons(_table()) == ["no quality row was compared"]

    def test_a_row_that_cannot_be_pooled_fails(self):
        row = _row().model_copy(
            update={"fail_to_pass": None, "pass_to_fail": None, "score_type": "custom"}
        )
        assert "cannot be pooled" in tie.quality_reasons(_table(row))[0]


# ---- "not worse" on cost and on latency ----


def _paired(
    ratio: float = 1.0, *, faster: int = 4, slower: int = 4, p: float = 1.0, **kwargs
) -> LatencyComparison:
    return LatencyComparison(
        method="paired_drive",
        split="val",
        n_items=faster + slower,
        median_ratio=ratio,
        faster_items=faster,
        slower_items=slower,
        sign_test_p=p,
        **kwargs,
    )


class TestNotWorseOnAMetric:
    def test_equal_and_better_pass(self):
        assert tie.metric_reasons("cost", _paired(1.0)) == []
        assert (
            tie.metric_reasons("cost", _paired(0.6, faster=8, slower=0, p=0.01)) == []
        )
        assert tie.metric_reasons("latency", _paired(1.03)) == []

    def test_not_measured_fails(self):
        assert "no paired comparison" in tie.metric_reasons("cost", None)[0]
        stored = _paired().model_copy(update={"method": "eval_runs"})
        # The stored eval runs are information, never this verdict.
        assert "no paired comparison" in tie.metric_reasons("cost", stored)[0]

    def test_an_incomplete_pass_fails(self):
        assert (
            "incomplete"
            in (tie.metric_reasons("latency", _paired(0.9, incomplete=True))[0])
        )

    def test_clearly_worse_fails(self):
        worse = _paired(1.02, faster=0, slower=8, p=0.008)
        assert tie.clearly_worse(worse)
        assert "clearly worse" in tie.metric_reasons("latency", worse)[0]
        assert tie.clearly_worse(_paired(1.0, p90_regressed=True))

    def test_a_ratio_over_the_band_fails(self):
        (reason,) = tie.metric_reasons("cost", _paired(1.10))
        assert "above 1.03" in reason


# ---- the train side ----


class TestTheTrainSide:
    def test_a_keep_arm_is_not_held_to_the_gain_rules(self):
        defaults = default_stop_rules("quality", full=True, residual=True)
        assert {"futility", "residual"} <= {r.kind for r in defaults}
        kept = tie.keep_arm_rules(defaults)
        assert {r.kind for r in kept} == {"invalid_output_rate", "eval_metric"}
        # A rule that stops a worse arm is not a gain rule.
        assert tie.keep_arm_rules([StopRule(kind="targets")])

    def test_no_gain_on_train_passes_the_tie_rule(self):
        verdict = tie.train_verdict(fix_item(), [_row()], 0.95)
        assert verdict.ok and verdict.reasons == []

    def test_a_loss_on_train_fails_it(self):
        verdict = tie.train_verdict(fix_item(), [_row(broke=1)], 0.95)
        assert not verdict.ok

    def test_an_unmeasured_primary_target_fails_it(self):
        verdict = tie.train_verdict(fix_item(), [_row("eval_2")], 0.95)
        assert "has no measured row" in verdict.reasons[0]

    def test_low_coverage_fails_it(self):
        row = _row().model_copy(update={"coverage": 0.5})
        assert "coverage" in tie.train_verdict(fix_item(), [row], 0.95).reasons[0]

    def test_the_rule_text_names_the_technique_and_no_number_of_the_run(self):
        text = tie.tie_rule_text("t-keep")
        assert "`t-keep`" in text and "NOT" in text
        assert " val " not in text and not any(c.isdigit() for c in text)


# ---- the choice ----


def test_among_ties_the_higher_net_then_the_cheaper_wins():
    low = _table(_row()).model_copy(update={"rc_id": "rc_a", "cost": _paired(0.5)})
    high = _table(_row(fixed=1)).model_copy(update={"rc_id": "rc_b"})
    assert tie.pick_winner([low, high]).rc_id == "rc_b"
    dear = _table(_row()).model_copy(update={"rc_id": "rc_c", "cost": _paired(0.9)})
    assert tie.pick_winner([dear, low]).rc_id == "rc_a"


# ---- whole waves against the fakes ----

BASE_VAL = [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0]
TIE_VAL = list(BASE_VAL)
SWAP_VAL = [0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0]  # one fixed, one broke
LOSS_VAL = [0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0]  # one broke
GAIN_VAL = [1.0] * 8


def _tie_ctx(
    tmp_path: Path,
    *,
    technique_id: str | None = "t-keep",
    cand_val: list[float] | None = TIE_VAL,
    train_gain: bool = True,
    cand_ms: float = 1000.0,
    cand_cost: float = 0.001,
    gate_agent: str = "auto",
    project: Path | None = None,
):
    """One wave under the quality objective: a fix arm whose build names
    `technique_id`, measured on train, then the val gate. `cand_val`: the
    candidate's val scores against `BASE_VAL` (None: its val fill fails).
    `train_gain`: the arm fixes the baseline's one train failure, or changes
    nothing on train. The drives of the paired pass take `cand_ms` and cost
    `cand_cost`, against 1000 ms and $0.001 for the baseline."""
    ctx = make_ctx(gate_agent=gate_agent)
    ctx.knowledge_base = _knowledge(tmp_path)
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(project or _project(tmp_path))}
    )
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    base_train = [0.0] + [1.0] * 7
    cand_train = [1.0] * 8 if train_gain else base_train
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run(f"train{n}", accuracy=v) for n, v in enumerate(base_train)
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run(f"train{n}", accuracy=v) for n, v in enumerate(cand_train)
    ]
    kiln.pending[("eval_1", "rc_base", "val")] = [
        make_run(f"v{n}", accuracy=v) for n, v in enumerate(BASE_VAL)
    ]
    if cand_val is not None:
        kiln.pending[("eval_1", "rc_cand", "val")] = [
            make_run(f"v{n}", accuracy=v) for n, v in enumerate(cand_val)
        ]
    kiln.run_configs = [_rc("rc_base"), _rc("rc_cand")]

    def handler(body: dict) -> dict:
        base = body.get("task_run_config_id") == "rc_base"
        text = body.get("plaintext_input") or ""
        return {
            "id": "r",
            "usage": {
                "total_llm_latency_ms": (1000.0 if base else cand_ms) + len(text),
                "cost": 0.001 if base else cand_cost,
            },
            "output": {"output": "ok"},
        }

    kiln.run_handler = handler
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue(
        "create_experiment_rc",
        ok(
            RcBuildResult(
                rc_id="rc_cand", build_notes="built", technique_id=technique_id
            )
        ),
    )
    d.queue("analyze_experiment_results", ok(make_result()))
    return ctx


def _not_significant(ctx) -> None:
    """The fake statistics answer p=0.03 for every pair. A loss of one item
    in eight is not significant on a real McNemar test."""
    ctx.kiln.mcnemar_response["p_exact"] = 0.5


def _events(ctx, kind: str) -> list[dict]:
    return [f for k, f in ctx.log.events if k == kind]


def _promoted(ctx) -> bool:
    return "rc_rc_cand" in ctx.kiln.memories["mem_baseline"]["tags"]


def _experiment_overview(ctx) -> str:
    (record,) = [
        m for m in ctx.kiln.memories.values() if "experiment" in (m.get("tags") or [])
    ]
    return record["overview"]


def _final_decision(ctx) -> PromotionDecision:
    return _events(ctx, "promotion")[-1]["decision"]


async def test_a_tie_with_keep_it_is_promoted_on_design_grounds(tmp_path):
    ctx = _tie_ctx(tmp_path)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _promoted(ctx)
    decided = _final_decision(ctx)
    assert (decided.grounds, decided.technique_id) == ("design", "t-keep")
    assert decided.rationale.startswith("design grounds (tie): technique `t-keep`")
    assert ctx.tie_promotions == 1
    # One `promotion` event for the gate, as for any other gate.
    assert len(_events(ctx, "promotion")) == 1
    (check,) = _events(ctx, "tie_check")
    assert check["not_worse"] is True and check["reasons"] == []
    (named,) = _events(ctx, "technique_named")
    assert (named["technique_id"], named["stance"]) == ("t-keep", "keep")


async def test_the_evidence_step_ran_first_and_chose_no_winner(tmp_path):
    ctx = _tie_ctx(tmp_path)
    await orchestrate(ctx, "g", timeout_mins=180)
    (rule,) = _events(ctx, "promotion_rule")
    assert rule["decision"].winner_rc_id is None
    assert "no targeted row improved" in rule["decision"].rationale
    (promo,) = _events(ctx, "tie_promotion")
    assert "no targeted row improved" in promo["evidence_rationale"]


async def test_a_swap_with_a_net_of_zero_is_a_tie(tmp_path):
    ctx = _tie_ctx(tmp_path, cand_val=SWAP_VAL)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _promoted(ctx) and _final_decision(ctx).grounds == "design"


@pytest.mark.parametrize(
    "technique_id",
    ["t-move", "t-depends", "t-dormant", "t-tools", "not-a-document", None],
)
async def test_a_tie_with_any_other_phrase_is_not_promoted(tmp_path, technique_id):
    ctx = _tie_ctx(tmp_path, technique_id=technique_id)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    assert _final_decision(ctx).winner_rc_id is None
    assert not _events(ctx, "tie_check")  # the test is for "Keep it." only
    assert "REJECTED AT VAL" in _experiment_overview(ctx)
    # No paired pass was paid for.
    assert not _events(ctx, "speed_result")


async def test_a_small_loss_is_not_a_tie(tmp_path):
    ctx = _tie_ctx(tmp_path, cand_val=LOSS_VAL)
    _not_significant(ctx)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    (check,) = _events(ctx, "tie_check")
    assert check["not_worse"] is False
    assert (
        "lost 1.0 net item(s) of 8, over its noise allowance of 0"
        in (check["reasons"][0])
    )
    assert "REJECTED AT VAL" in _experiment_overview(ctx)
    # Quality failed, so the paired pass was not paid for.
    assert not _events(ctx, "speed_result")
    assert ctx.tie_promotions == 0


async def test_a_loss_inside_the_measured_noise_is_still_a_net_loss(tmp_path):
    ctx = _tie_ctx(tmp_path, cand_val=LOSS_VAL)
    _not_significant(ctx)
    ctx.train_noise = [_noise(items=8, changed=1)]
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    # The row is inside its allowance of 1; the pooled net of -1 is not.
    (check,) = _events(ctx, "tie_check")
    (reason,) = check["reasons"]
    assert reason.startswith("quality is worse on balance: -1.0")


async def test_a_candidate_with_a_val_hole_is_not_promoted_and_not_judged(tmp_path):
    ctx = _tie_ctx(tmp_path, cand_val=None)
    ctx.kiln.job_behaviors[("eval_1", "rc_cand", "val")] = ["fail", "fail"]
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    assert _events(ctx, "val_fill_failed")
    assert not _events(ctx, "tie_promotion")
    # A hole is not a verdict: the tie step stamps nothing.
    assert "REJECTED AT VAL" not in _experiment_overview(ctx)
    assert "PROMOTED" not in _experiment_overview(ctx)


async def test_a_dearer_candidate_is_not_a_tie(tmp_path):
    ctx = _tie_ctx(tmp_path, cand_cost=0.002)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    (check,) = _events(ctx, "tie_check")
    assert check["reasons"][0].startswith("cost: clearly worse on val")


async def test_a_slower_candidate_is_not_a_tie(tmp_path):
    ctx = _tie_ctx(tmp_path, cand_ms=1500.0)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    (check,) = _events(ctx, "tie_check")
    assert check["reasons"][0].startswith("latency: clearly worse on val")


async def test_a_cheaper_and_faster_candidate_is_a_tie(tmp_path):
    ctx = _tie_ctx(tmp_path, cand_ms=600.0, cand_cost=0.0005)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _promoted(ctx)
    rationale = _final_decision(ctx).rationale
    assert "cost x0.50" in rationale and "latency x0." in rationale
    # The paired pass is real spend, and it is booked.
    assert [f for f in _events(ctx, "spend") if f.get("source") == "latency_drives"]


async def test_no_drivable_val_input_is_no_tie(tmp_path):
    empty = tmp_path / "empty"
    (empty / "tasks" / "t1 - T").mkdir(parents=True)
    ctx = _tie_ctx(tmp_path, project=empty)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    (check,) = _events(ctx, "tie_check")
    assert "cost: no paired comparison on val" in check["reasons"]
    assert "latency: no paired comparison on val" in check["reasons"]


async def test_a_run_makes_one_promotion_on_a_tie(tmp_path):
    assert tie.MAX_TIE_PROMOTIONS == 1
    ctx = _tie_ctx(tmp_path)
    ctx.tie_promotions = 1  # the run already made one
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    (check,) = _events(ctx, "tie_check")
    assert check["not_worse"] is False
    assert "already made 1 promotion(s) on a tie" in check["reasons"][0]
    assert not _events(ctx, "speed_result")
    assert ctx.tie_promotions == 1


async def test_a_real_gain_is_promoted_on_evidence_as_before(tmp_path):
    ctx = _tie_ctx(tmp_path, gate_agent="always")
    ctx.kiln.pending[("eval_1", "rc_cand", "val")] = [
        make_run(f"v{n}", accuracy=v) for n, v in enumerate(GAIN_VAL)
    ]
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+25% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _promoted(ctx)
    assert _final_decision(ctx).grounds == "evidence"
    assert not _events(ctx, "tie_check") and ctx.tie_promotions == 0
    # A promotion on evidence closes the issue of the fix.
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("FIXED:")


async def test_a_promotion_on_a_tie_closes_no_issue(tmp_path):
    ctx = _tie_ctx(tmp_path)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _promoted(ctx)
    assert not ctx.kiln.memories["issue_1"]["overview"].startswith("FIXED:")
    # The train-visible stamp is the same as for any promotion.
    overview = _experiment_overview(ctx)
    assert "— PROMOTED" in overview and "design" not in overview.lower()
    # The val record carries the mark; the baseline record carries no number.
    (val,) = [m for m in ctx.kiln.memories.values() if "val" in (m.get("tags") or [])]
    assert "design grounds (tie)" in val["content"]
    assert "design" not in ctx.kiln.memories["mem_baseline"]["overview"]


async def test_the_agent_says_no_winner_and_code_settles_the_tie(tmp_path):
    ctx = _tie_ctx(tmp_path, gate_agent="always")
    ctx.dispatch.queue("promote_best", decision(None, "nothing moved on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _promoted(ctx)
    assert _final_decision(ctx).grounds == "design"
    # The agent saw the stance of the candidate, as information.
    (table,) = ctx.dispatch.calls_to("promote_best")[0].candidates
    assert table.tie is not None
    assert (table.tie.technique_id, table.tie.stance) == ("t-keep", "keep")
    (promo,) = _events(ctx, "tie_promotion")
    assert promo["evidence_rationale"] == "nothing moved on val"


async def test_no_agent_promotes_on_a_tie(tmp_path):
    """An agent that sets `grounds: design` and names a winner gets a
    promotion on evidence, checked as any other. It cannot use the tie."""
    ctx = _tie_ctx(tmp_path, gate_agent="always", technique_id="t-move")
    ctx.dispatch.queue(
        "promote_best",
        decision("rc_cand", "a tie", grounds="design", technique_id="t-keep"),
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    decided = _final_decision(ctx)
    assert (decided.grounds, decided.technique_id) == ("evidence", None)
    assert ctx.tie_promotions == 0


async def test_a_gate_held_on_a_constraint_is_not_settled_by_a_tie(tmp_path):
    ctx = _tie_ctx(tmp_path, gate_agent="always")
    ctx.dispatch.queue(
        "promote_best", decision(None, "blocked on c1", blocked_on="constraint_1")
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    assert not _events(ctx, "tie_check")


# ---- how a tie reaches the gate ----


async def test_a_keep_arm_with_no_train_gain_reaches_the_gate(tmp_path):
    ctx = _tie_ctx(tmp_path, train_gain=False)
    await orchestrate(ctx, "g", timeout_mins=180)
    (train,) = _events(ctx, "tie_train_rule")
    assert train["rule_ok"] is True
    assert "did not improve" in train["gain_rule_reasons"][0]
    (rule,) = _events(ctx, "success_rule")
    assert rule["rule_ok"] is True and rule["on_tie"] is True
    # The analyzer was told the rule of this arm.
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert "`t-keep`" in analysis.tie_rule
    assert _promoted(ctx) and _final_decision(ctx).grounds == "design"


@pytest.mark.parametrize("technique_id", ["t-move", "t-depends", None])
async def test_any_other_arm_with_no_train_gain_never_reaches_the_gate(
    tmp_path, technique_id
):
    ctx = _tie_ctx(tmp_path, technique_id=technique_id, train_gain=False)
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    assert not _events(ctx, "gate_fills_planned")
    assert not _events(ctx, "tie_train_rule")
    assert not [j for j in ctx.kiln.job_invocations if j[2] == "val"]


async def test_an_arm_with_a_gain_is_not_given_the_tie_rule(tmp_path):
    ctx = _tie_ctx(tmp_path, technique_id="t-move")
    await orchestrate(ctx, "g", timeout_mins=180)
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.tie_rule is None


async def test_after_the_limit_a_keep_arm_is_held_to_a_gain_again(tmp_path):
    ctx = _tie_ctx(tmp_path, train_gain=False)
    ctx.tie_promotions = 1
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _events(ctx, "gate_fills_planned")
    assert not _events(ctx, "tie_train_rule")


async def test_the_analyzer_can_still_veto_a_keep_arm(tmp_path):
    ctx = _tie_ctx(tmp_path, train_gain=False)
    ctx.dispatch.queues["analyze_experiment_results"].clear()
    ctx.dispatch.queue(
        "analyze_experiment_results",
        ok(make_result(successful=False, veto_reason="other")),
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _events(ctx, "gate_fills_planned")


async def test_a_missed_prediction_does_not_hold_back_a_keep_arm(tmp_path):
    ctx = _tie_ctx(tmp_path, train_gain=False)
    ctx.dispatch.queues["analyze_experiment_results"].clear()
    ctx.dispatch.queue(
        "analyze_experiment_results",
        ok(make_result(successful=False, veto_reason="prediction_miss")),
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _events(ctx, "veto_overridden")
    assert _events(ctx, "gate_fills_planned")
    assert _promoted(ctx)


# ---- what the tie step leaves alone ----


def _outcome(item) -> ItemOutcome:
    return ItemOutcome(
        item=item,
        status="completed",
        rc_id="rc_cand",
        experiment_memory_id="mem_x",
        result=make_result(),
        infra_error=None,
    )


def test_a_merge_has_no_stance():
    ctx = make_ctx()
    ctx.tie_stances["rc_cand"] = TechniqueStance("t-keep", "keep")
    merge = ExperimentItem(
        id="w2-1",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=MergeWork(
            wins=[WinRef(experiment_memory_id="mem_a", rc_id="rc_a", summary="a")]
        ),
    )
    table = _table(_row())
    candidate = GateCandidate("rc_cand", "mem_x", _outcome(merge))
    _mark_tie_candidates(ctx, [candidate], [table])
    assert table.tie is None
    # The same table for a fix item gets the stance.
    _mark_tie_candidates(
        ctx, [GateCandidate("rc_cand", "mem_x", _outcome(fix_item()))], [table]
    )
    assert table.tie == TieCheck(technique_id="t-keep", stance="keep")


@pytest.mark.parametrize("objective", ["speed", "cost"])
async def test_the_speed_and_cost_objectives_have_no_tie_step(objective):
    ctx = make_ctx()
    ctx.objective = objective
    ctx.tie_stances["rc_cand"] = TechniqueStance("t-keep", "keep")
    table = _table(_row())
    candidate = GateCandidate("rc_cand", "mem_x", _outcome(fix_item()))
    _mark_tie_candidates(ctx, [candidate], [table])
    assert table.tie is None
    table.tie = TieCheck(technique_id="t-keep", stance="keep")
    no_winner = PromotionDecision(
        winner_rc_id=None, winner_experiment_memory_id=None, rationale="no winner"
    )
    assert await _settle_tie(ctx, [candidate], [table], "rc_base", no_winner) is (
        no_winner
    )
    assert not _events(ctx, "tie_check")


async def test_a_decision_with_a_winner_is_left_alone():
    ctx = make_ctx()
    table = _table(_row())
    table.tie = TieCheck(technique_id="t-keep", stance="keep")
    winner = PromotionDecision(
        winner_rc_id="rc_other", winner_experiment_memory_id="mem_o", rationale="+9%"
    )
    assert await _settle_tie(ctx, [], [table], "rc_base", winner) is winner
    assert not _events(ctx, "tie_check")


# ---- what a finished run shows ----


async def test_the_displays_and_the_report_mark_the_promotion(tmp_path):
    from kiln_harness_tuner.ui import SETUP_LINE_EVENTS, own_line

    ctx = _tie_ctx(tmp_path)
    await orchestrate(ctx, "g", timeout_mins=180)
    (check,) = _events(ctx, "tie_check")
    (promo,) = _events(ctx, "tie_promotion")
    assert {"tie_check", "tie_promotion"} <= SETUP_LINE_EVENTS
    assert own_line("tie_check", check) == "tie check rc_cand (t-keep): not worse"
    assert own_line("tie_promotion", promo).startswith("promotion on a tie: rc_cand")


def test_the_fallback_report_marks_a_promotion_on_a_tie():
    from conftest import make_tracker

    tracker = make_tracker()
    tracker.event("wave_start", wave=1, baseline_rc_id="rc_base", items=[])
    design = PromotionDecision(
        winner_rc_id="rc_cand",
        winner_experiment_memory_id="mem_x",
        rationale="design grounds (tie): technique `t-keep` says 'Keep it.'",
        grounds="design",
        technique_id="t-keep",
    )
    tracker.event(
        "promotion", winner_rc_id="rc_cand", rationale=design.rationale, decision=design
    )
    report = render_fallback_report(tracker.snapshot())
    assert (
        "- promotion (on design grounds: a tie on val, technique `t-keep`): rc_cand"
        in report
    )
    evidence = design.model_copy(update={"grounds": "evidence", "technique_id": None})
    tracker.event(
        "promotion", winner_rc_id="rc_cand", rationale="+9%", decision=evidence
    )
    assert "- promotion: rc_cand" in render_fallback_report(tracker.snapshot())


def test_the_terminal_app_words_a_promotion_on_a_tie():
    line = tui_log.promoted_on_tie("w1-1", "t-keep")
    assert line == (
        "w1-1 promoted on a tie · no gain and no loss on val · its technique"
        " (t-keep) says to keep it"
    )
