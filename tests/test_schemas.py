"""Schema contract tests: validators, discriminated unions, and the
generic AgentResult envelope."""

import pytest
from pydantic import ValidationError

from kiln_harness_tuner.schemas import (
    AgentResult,
    BatchPlan,
    DescribeEvalInput,
    ErrorInfo,
    EvalOnlyItem,
    EvalTarget,
    ExperimentItem,
    FixWork,
    ItemOutcome,
    ItemStatus,
    MeasureIssueBaselineItem,
    MergeWork,
    PromotionDecision,
    ReportInput,
    RunStatus,
    SetupReport,
    Usage,
    WaveStatus,
    WinRef,
)


def make_fix_work(**overrides):
    fields = dict(
        issue_memory_id="mem_61",
        hypothesis="tone fix helps",
        technique="prompt",
        predicted_effect="+5% pass rate",
        needs_eval=True,
    )
    fields.update(overrides)
    return FixWork(**fields)


def make_experiment_item(**overrides):
    fields = dict(
        id="w1-1",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=make_fix_work(),
    )
    fields.update(overrides)
    return ExperimentItem(**fields)


class TestFixWorkEvalIdValidator:
    def test_needs_eval_false_requires_eval_id(self):
        with pytest.raises(ValidationError, match="eval_id is required"):
            make_fix_work(needs_eval=False)

    def test_needs_eval_false_with_eval_id_ok(self):
        work = make_fix_work(needs_eval=False, eval_id="eval_9")
        assert work.eval_id == "eval_9"

    def test_needs_eval_true_without_eval_id_ok(self):
        assert make_fix_work(needs_eval=True).eval_id is None


class TestMergeImpliesPromotionCandidate:
    merge = MergeWork(wins=[WinRef(experiment_memory_id="mem_1", rc_id="rc_1")])

    def test_merge_with_promotion_candidate_false_rejected(self):
        with pytest.raises(ValidationError, match="promotion_candidate"):
            make_experiment_item(work=self.merge, promotion_candidate=False)

    def test_merge_with_promotion_candidate_true_ok(self):
        item = make_experiment_item(work=self.merge, promotion_candidate=True)
        assert isinstance(item.work, MergeWork)

    def test_fix_work_promotion_candidate_free_choice(self):
        item = make_experiment_item(promotion_candidate=False)
        assert item.promotion_candidate is False


class TestHeadToHeadGroups:
    """`head_to_head_group` is shared by both
    work types, and a BatchPlan-level group must actually name mutually
    exclusive arms — a one-sided group string is meaningless."""

    def test_merge_work_head_to_head_group_default_none(self):
        merge = MergeWork(wins=[WinRef(experiment_memory_id="m", rc_id="r")])
        assert merge.head_to_head_group is None

    @staticmethod
    def plan_with(items):
        return BatchPlan(items=items, rejected_summary="", open_questions=[])

    @staticmethod
    def grouped_fix(item_id, group):
        return make_experiment_item(
            id=item_id, work=make_fix_work(head_to_head_group=group)
        )

    @staticmethod
    def grouped_merge(item_id, group):
        return make_experiment_item(
            id=item_id,
            work=MergeWork(
                wins=[WinRef(experiment_memory_id="mem_a", rc_id="rc_a")],
                head_to_head_group=group,
            ),
        )

    def test_group_spanning_fix_and_merge_accepted(self):
        # A MergeWork re-base head-to-head with a FixWork alternative on the same
        # surface.
        plan = self.plan_with(
            [self.grouped_merge("w3-1", "scope"), self.grouped_fix("w3-3", "scope")]
        )
        assert {item.work.head_to_head_group for item in plan.items} == {"scope"}

    def test_one_sided_head_to_head_group_rejected(self):
        with pytest.raises(ValidationError, match="scope"):
            self.plan_with([self.grouped_fix("w3-3", "scope")])

    def test_two_groups_one_lonely_rejected_by_name(self):
        with pytest.raises(ValidationError, match=r"\['lonely'\]"):
            self.plan_with(
                [
                    self.grouped_fix("w1-1", "paired"),
                    self.grouped_fix("w1-2", "paired"),
                    self.grouped_merge("w1-3", "lonely"),
                ]
            )

    def test_ungrouped_items_unaffected(self):
        plan = self.plan_with(
            [
                make_experiment_item(),
                EvalOnlyItem(
                    id="w1-2", type="eval_only", issue_memory_id="mem_1", reason="r"
                ),
            ]
        )
        assert len(plan.items) == 2


class TestLoopExitExcludesItems:
    """A full wave plus `loop_exit` is a confusion: the orchestrator would
    honor exit and drop the items silently. The schema makes it
    un-returnable: exit means nothing-to-do-NOW, so items + loop_exit is
    invalid (the ValidationError feeds the dispatcher's one-shot repair
    retry)."""

    def test_items_plus_loop_exit_rejected_with_instructive_message(self):
        with pytest.raises(ValidationError, match="nothing-to-do-NOW") as exc_info:
            BatchPlan(
                items=[make_experiment_item()],
                rejected_summary="",
                open_questions=[],
                loop_exit="recommend ending after these two items land",
            )
        message = str(exc_info.value)
        assert "return them" in message
        assert "future round" in message

    def test_loop_exit_only_valid(self):
        plan = BatchPlan(
            items=[],
            rejected_summary="",
            open_questions=[],
            loop_exit="out of worthwhile experiments",
        )
        assert plan.loop_exit == "out of worthwhile experiments"

    def test_items_only_valid(self):
        plan = BatchPlan(
            items=[make_experiment_item()], rejected_summary="", open_questions=[]
        )
        assert plan.loop_exit is None
        assert len(plan.items) == 1

    def test_rejected_on_model_validate(self):
        # The planner's dict/JSON path — the boundary the defect arrives on.
        with pytest.raises(ValidationError, match="loop_exit"):
            BatchPlan.model_validate(
                {
                    "items": [
                        {
                            "id": "w1-2",
                            "type": "eval_only",
                            "issue_memory_id": "mem_102",
                            "reason": "anti-starvation valve",
                        }
                    ],
                    "rejected_summary": "",
                    "open_questions": [],
                    "loop_exit": "plateau",
                }
            )


class TestEvaluationTargetDedup:
    """Duplicate eval_ids in evaluation_target
    fire duplicate concurrent measurement jobs (duplicate spend + duplicate
    bundle rows) — the schema normalizes them away at the planner boundary."""

    def test_duplicate_eval_targets_collapse(self):
        item = make_experiment_item(
            evaluation_target=[
                EvalTarget(eval_id="eval_1"),
                EvalTarget(eval_id="eval_1"),
            ]
        )
        assert [t.eval_id for t in item.evaluation_target] == ["eval_1"]

    def test_first_seen_order_preserved(self):
        item = make_experiment_item(
            evaluation_target=[
                EvalTarget(eval_id="eval_2"),
                EvalTarget(eval_id="eval_1"),
                EvalTarget(eval_id="eval_2"),
            ]
        )
        assert [t.eval_id for t in item.evaluation_target] == ["eval_2", "eval_1"]

    def test_duplicate_eval_target_scores_union(self):
        item = make_experiment_item(
            evaluation_target=[
                EvalTarget(eval_id="eval_1", scores=["acc", "cost"]),
                EvalTarget(eval_id="eval_1", scores=["cost", "tone"]),
            ]
        )
        (target,) = item.evaluation_target
        assert target.scores == ["acc", "cost", "tone"]

    def test_scores_none_subsumes_subsets_either_order(self):
        for targets in (
            [EvalTarget(eval_id="e"), EvalTarget(eval_id="e", scores=["acc"])],
            [EvalTarget(eval_id="e", scores=["acc"]), EvalTarget(eval_id="e")],
        ):
            item = make_experiment_item(evaluation_target=targets)
            (target,) = item.evaluation_target
            assert target.scores is None

    def test_within_target_duplicate_scores_dedup(self):
        item = make_experiment_item(
            evaluation_target=[EvalTarget(eval_id="e", scores=["acc", "acc"])]
        )
        assert item.evaluation_target[0].scores == ["acc"]

    def test_spelling_variants_of_one_score_dedup(self):
        # Measurement expansion keys scores via score_json_key
        # ("Out Of Scope" → "out_of_scope"), so spelling variants are ONE score —
        # exact-string dedup would have yielded two identical ScoreSpecs
        # (duplicate rows + stats calls). First-seen spelling wins.
        item = make_experiment_item(
            evaluation_target=[
                EvalTarget(eval_id="e", scores=["Out Of Scope", "out_of_scope"]),
                EvalTarget(eval_id="e", scores=["OUT of scope", "Accuracy (v2)"]),
            ]
        )
        (target,) = item.evaluation_target
        assert target.scores == ["Out Of Scope", "Accuracy (v2)"]

    def test_distinct_eval_targets_untouched(self):
        targets = [
            EvalTarget(eval_id="eval_1", scores=["acc"]),
            EvalTarget(eval_id="eval_2"),
        ]
        item = make_experiment_item(evaluation_target=targets)
        assert [t.eval_id for t in item.evaluation_target] == ["eval_1", "eval_2"]
        assert item.evaluation_target[0].scores == ["acc"]
        assert item.evaluation_target[1].scores is None

    def test_dedup_applies_on_model_validate(self):
        # The planner's dict/JSON path — the boundary the slip arrives on.
        item = ExperimentItem.model_validate(
            {
                "id": "w1-1",
                "type": "experiment",
                "evaluation_target": [{"eval_id": "eval_1"}, {"eval_id": "eval_1"}],
                "promotion_candidate": False,
                "work": {
                    "issue_memory_id": "mem_61",
                    "hypothesis": "h",
                    "technique": "prompt",
                    "predicted_effect": "e",
                    "needs_eval": True,
                },
            }
        )
        assert len(item.evaluation_target) == 1


class TestBatchPlanDiscriminatedUnion:
    def test_mixed_items_parse_to_right_classes(self):
        plan = BatchPlan.model_validate(
            {
                "items": [
                    {
                        "id": "w1-1",
                        "type": "experiment",
                        "evaluation_target": [{"eval_id": "eval_1"}],
                        "promotion_candidate": False,
                        "work": {
                            "issue_memory_id": "mem_61",
                            "hypothesis": "h",
                            "technique": "prompt",
                            "predicted_effect": "e",
                            "needs_eval": True,
                        },
                    },
                    {
                        "id": "w1-2",
                        "type": "eval_only",
                        "issue_memory_id": "mem_102",
                        "reason": "anti-starvation valve",
                    },
                ],
                "rejected_summary": "…and 9 others, lower value",
                "open_questions": [],
            }
        )
        assert isinstance(plan.items[0], ExperimentItem)
        assert isinstance(plan.items[1], EvalOnlyItem)
        assert plan.loop_exit is None

    def test_unknown_item_type_rejected(self):
        with pytest.raises(ValidationError):
            BatchPlan.model_validate(
                {
                    "items": [{"id": "w1-1", "type": "merge"}],
                    "rejected_summary": "",
                    "open_questions": [],
                }
            )


def test_eval_target_scores_default_none():
    target = EvalTarget(eval_id="eval_1")
    assert target.scores is None


def test_an_empty_score_list_is_rejected_by_both_item_types():
    # "Every directional score" is spelled None, never []. Caught at the
    # schema so the dispatcher's in-session repair fixes a planner typo —
    # reaching the measurement lane with [] is a hard TargetExpansionError,
    # and on the experiment path that kills the run.
    with pytest.raises(ValidationError, match="at least 1 item"):
        EvalTarget(eval_id="eval_1", scores=[])
    with pytest.raises(ValidationError, match="at least 1 item"):
        MeasureIssueBaselineItem(
            id="w2-3",
            type="measure_issue_baseline",
            issue_memory_id="mem_84",
            eval_id="eval_9",
            scores=[],
            reason="r",
        )


def test_agent_result_generic_round_trip():
    plan = BatchPlan(items=[], rejected_summary="none", open_questions=[])
    result = AgentResult[BatchPlan].model_validate(
        {"status": "ok", "data": plan.model_dump(), "usage": {"cost_usd": 1.5}}
    )
    assert isinstance(result.data, BatchPlan)
    assert result.usage.cost_usd == 1.5
    assert result.usage.tokens == 0  # defaults


def test_agent_result_defaults():
    result = AgentResult[BatchPlan](status="failed_infra")
    assert result.data is None
    assert result.error is None
    assert result.usage == Usage()


class TestItemOutcome:
    def test_completed_eval_only_outcome_with_none_result(self):
        # A successful eval_only item ends "completed" with result=None
        outcome = ItemOutcome(
            item=EvalOnlyItem(
                id="w1-5", type="eval_only", issue_memory_id="mem_102", reason="cheap"
            ),
            status="completed",
            rc_id=None,
            experiment_memory_id=None,
            result=None,
            infra_error=None,
        )
        assert outcome.result is None
        assert isinstance(outcome.item, EvalOnlyItem)

    def test_failed_infra_outcome_for_experiment(self):
        outcome = ItemOutcome(
            item=make_experiment_item(),
            status="failed_infra",
            rc_id=None,
            experiment_memory_id="mem_200",
            result=None,
            infra_error=ErrorInfo(error_class="timeout", detail="hung"),
        )
        assert outcome.infra_error.error_class == "timeout"

    def test_item_discriminated_from_dict(self):
        outcome = ItemOutcome.model_validate(
            {
                "item": {
                    "id": "w2-1",
                    "type": "eval_only",
                    "issue_memory_id": "mem_1",
                    "reason": "r",
                },
                "status": "completed",
                "rc_id": None,
                "experiment_memory_id": None,
                "result": None,
                "infra_error": None,
            }
        )
        assert isinstance(outcome.item, EvalOnlyItem)

    def test_item_outcome_embeds_a_measure_item(self):
        # Reproduced or not, the item ends `completed` with
        # result=None — so the embedded union has to carry it.
        outcome = ItemOutcome.model_validate(
            {
                "item": {
                    "id": "w2-3",
                    "type": "measure_issue_baseline",
                    "issue_memory_id": "mem_1",
                    "eval_id": "424242966189",
                    "reason": "settle it before spending a fix wave on it",
                },
                "status": "completed",
                "rc_id": None,
                "experiment_memory_id": None,
                "result": None,
                "infra_error": None,
            }
        )
        assert isinstance(outcome.item, MeasureIssueBaselineItem)
        assert outcome.result is None


class TestMeasureIssueBaselineItem:
    def test_round_trips_with_scores_defaulting_to_none(self):
        item = MeasureIssueBaselineItem(
            id="w2-3",
            type="measure_issue_baseline",
            issue_memory_id="mem_84",
            eval_id="424242966189",
            reason="p1 issue, eval already exists",
        )
        assert item.scores is None
        assert MeasureIssueBaselineItem.model_validate(item.model_dump()) == item

    def test_scores_target_a_subset(self):
        item = MeasureIssueBaselineItem(
            id="w2-3",
            type="measure_issue_baseline",
            issue_memory_id="mem_84",
            eval_id="424242966189",
            scores=["Handoff Block"],
            reason="only the score the issue is about",
        )
        assert item.scores == ["Handoff Block"]

    def test_batch_plan_discriminates_all_three_item_types(self):
        plan = BatchPlan.model_validate(
            {
                "items": [
                    {
                        "id": "w2-1",
                        "type": "experiment",
                        "evaluation_target": [{"eval_id": "eval_1"}],
                        "promotion_candidate": True,
                        "work": {
                            "issue_memory_id": "mem_61",
                            "hypothesis": "h",
                            "technique": "prompt",
                            "predicted_effect": "e",
                            "needs_eval": False,
                            "eval_id": "eval_1",
                        },
                    },
                    {
                        "id": "w2-2",
                        "type": "eval_only",
                        "issue_memory_id": "mem_102",
                        "reason": "anti-starvation valve",
                    },
                    {
                        "id": "w2-3",
                        "type": "measure_issue_baseline",
                        "issue_memory_id": "mem_84",
                        "eval_id": "eval_9",
                        "reason": "cheap check before a fix wave",
                    },
                ],
                "rejected_summary": "w2: …",
                "open_questions": [],
            }
        )
        assert [type(item) for item in plan.items] == [
            ExperimentItem,
            EvalOnlyItem,
            MeasureIssueBaselineItem,
        ]


def test_promotion_decision_regressed_issues_default_empty():
    decision = PromotionDecision(
        winner_rc_id=None, winner_experiment_memory_id=None, rationale="no winner"
    )
    assert decision.regressed_issues == []


def test_run_status_report_input_construct():
    status = RunStatus(
        project_id="424242748186",
        task_id="424242613304",
        guidance="one wave, cheapest issues",
        baseline_history=["rc_184", "rc_207"],
        waves=[
            WaveStatus(
                wave=1,
                should_wrap_up=False,
                plan_headline="4 experiments",
                rejected_summary="…",
                items=[
                    ItemStatus(
                        item_id="w1-1",
                        kind="fix",
                        label="system-prompt tone (mem_61)",
                        live_state="measuring",
                        eval_progress=(12, 20),
                        outcome=None,
                        elapsed_secs=130.0,
                        spend_usd=1.10,
                    )
                ],
                promotion=None,
                tripwire=None,
            )
        ],
        spend_total_usd=14.20,
        time_remaining_secs=6120.0,
        exit_reason=None,
    )
    report_input = ReportInput(run_status=status)
    assert report_input.run_status.waves[0].items[0].eval_progress == (12, 20)

    # tuple survives a JSON round-trip (serialized as a 2-list)
    rehydrated = ReportInput.model_validate_json(report_input.model_dump_json())
    assert rehydrated.run_status.waves[0].items[0].eval_progress == (12, 20)


def test_describe_eval_input_spec_is_optional():
    # Not every eval has a Spec, and one without is still active (the eval
    # is the parent, the Spec is enrichment).
    req = DescribeEvalInput(eval_id="424242966189", eval_name="Handoff Block")
    assert req.spec_id is None
    assert (
        DescribeEvalInput(eval_id="1", eval_name="n", spec_id="424242331708").spec_id
        == "424242331708"
    )


def test_run_status_setup_defaults_to_none_and_round_trips():
    # Additive on RunStatus: every pre-setup RunStatus still validates, and
    # the report writer gets the whole object through ReportInput.
    status = RunStatus(
        project_id="p1",
        task_id="t1",
        guidance="",
        baseline_history=[],
        waves=[],
        spend_total_usd=0.0,
        time_remaining_secs=0.0,
        exit_reason=None,
    )
    assert status.setup is None

    status.setup = SetupReport(evals_total=3, evals_active=2, seeded_eval_ids=["e1"])
    rehydrated = ReportInput.model_validate_json(
        ReportInput(run_status=status).model_dump_json()
    )
    assert rehydrated.run_status.setup.seeded_eval_ids == ["e1"]
