"""Measurement-lane tests: stats assembly on fixtures — sign
normalization per direction flag, significance flag, binary-only flip counts,
operation selection by score type — and the decision tree + eval-job
retry ladder against a fake KilnClient. All offline.
"""

import asyncio
import itertools

import pytest
from fake_kiln import FakeKiln, make_eval, make_job_error, make_run, make_score

from kiln_harness_tuner.analysis import (
    Analyzer,
    DirectionFlagMissingError,
    InconclusiveNegativeError,
    MeasurementFailed,
    MissingDefaultEvalConfigError,
    ScoreSpec,
    StatsResponseError,
    TargetExpansionError,
    UnsupportedScoreTypeError,
    _format_mean,
    expand_targets,
    flatten_result_row,
    normalize,
    pairable_item_count,
    reproduction_verdict,
    runs_cost,
    score_json_key,
)
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.schemas import (
    AnalysisBundle,
    BaselineRef,
    EvalTarget,
    ExperimentItem,
    FixWork,
    MergeWork,
    WinRef,
)

BASELINE = BaselineRef(rc_id="rc_base")


def make_item(targets=None, parent_rc_id=None) -> ExperimentItem:
    return ExperimentItem(
        id="w1-1",
        type="experiment",
        evaluation_target=targets or [EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=FixWork(
            issue_memory_id="mem_1",
            hypothesis="retry-skill for 429s",
            technique="prompt",
            predicted_effect="+10%",
            needs_eval=False,
            eval_id="eval_1",
            parent_rc_id=parent_rc_id,
        ),
    )


class RecordingSink:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def event(self, kind: str, **fields: object) -> None:
        self.events.append((kind, fields))


def make_analyzer(kiln, **kwargs):
    sleeps: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleeps.append(secs)

    analyzer = Analyzer(
        kiln,
        project_id="p1",
        task_id="t1",
        sleep=fake_sleep,
        log=RecordingSink(),
        **kwargs,
    )
    return analyzer, sleeps


# ---- target expansion ----


def test_score_json_key_normalizes():
    assert score_json_key("Tool Errors") == "tool_errors"
    assert score_json_key("Accuracy (v2)") == "accuracy_v2"


def test_expand_all_directional_skips_informational():
    eval_def = make_eval(
        scores=[
            make_score("Accuracy", "pass_fail", "higher_is_better"),
            make_score("Tool Calls", "five_star", "informational"),
            make_score("Tool Errors", "five_star", "lower_is_better"),
        ]
    )
    specs = expand_targets(eval_def, EvalTarget(eval_id="eval_1"))
    assert [(s.key, s.direction) for s in specs] == [
        ("accuracy", "higher_is_better"),
        ("tool_errors", "lower_is_better"),
    ]


def test_expand_explicit_subset():
    eval_def = make_eval(
        scores=[
            make_score("Accuracy", "pass_fail", "higher_is_better"),
            make_score("Tool Errors", "five_star", "lower_is_better"),
        ]
    )
    specs = expand_targets(
        eval_def, EvalTarget(eval_id="eval_1", scores=["tool_errors"])
    )
    assert [s.key for s in specs] == ["tool_errors"]


def test_expand_explicit_informational_rejected():
    eval_def = make_eval(scores=[make_score("Tool Calls", "custom", "informational")])
    with pytest.raises(TargetExpansionError, match="informational"):
        expand_targets(eval_def, EvalTarget(eval_id="eval_1", scores=["tool_calls"]))


def test_expand_unknown_key_rejected():
    with pytest.raises(TargetExpansionError, match="no score"):
        expand_targets(make_eval(), EvalTarget(eval_id="eval_1", scores=["nope"]))


def test_expand_missing_direction_flag_is_hard_error():
    eval_def = make_eval(scores=[make_score("Accuracy", "pass_fail", direction=None)])
    with pytest.raises(DirectionFlagMissingError, match="Accuracy"):
        expand_targets(eval_def, EvalTarget(eval_id="eval_1"))
    with pytest.raises(DirectionFlagMissingError, match="no direction field"):
        expand_targets(eval_def, EvalTarget(eval_id="eval_1", scores=["accuracy"]))


# ---- stats assembly ----


async def test_stored_parent_compare_happy_path():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # Parent stored (scrambled order to prove pairing by dataset_id).
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i3", cost=0.02, accuracy=0.0),
        make_run("i1", cost=0.02, accuracy=1.0),
        make_run("i4", cost=0.02, accuracy=1.0),
        make_run("i2", cost=0.02, accuracy=0.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", cost=0.03, accuracy=1.0),
        make_run("i2", cost=0.03, accuracy=1.0),
        make_run("i3", cost=0.03, accuracy=1.0),
        make_run("i4", cost=0.03, accuracy=0.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_base"]
    )

    assert isinstance(bundle, AnalysisBundle)
    assert bundle.errors == []
    [row] = bundle.per_eval
    assert row.eval_id == "eval_1"
    assert row.score_key == "accuracy"
    assert row.split == "train"
    assert row.compared_to == "parent"
    assert row.baseline_rc_id == "rc_base"
    assert row.n_items == 4
    assert row.delta == pytest.approx(0.25)  # cand 3/4 vs parent 2/4
    # p comes from the live mcnemar shape's "p_exact"; the fake's own
    # "significant" field says False — code computes significance.
    assert row.p_value == 0.03
    assert row.significant is True
    # mcnemar's live response has no stderr key — clean None, no error.
    assert row.stderr is None
    assert row.fail_to_pass == 2  # i2, i3
    assert row.pass_to_fail == 1  # i4
    # Headroom = 1 − the parent's pass rate (2/4): the largest delta the row
    # can show; the analyzer judges a prediction against it.
    assert row.headroom == pytest.approx(0.5)
    assert row.spend_usd == pytest.approx(0.02 * 4 + 0.03 * 4)
    assert bundle.spend_total_usd == pytest.approx(row.spend_usd)
    # Clean measurement → full coverage: n_expected falls back to
    # the stored row count when the job record carries no totals.
    assert row.n_expected == 4
    assert row.coverage == pytest.approx(1.0)
    assert row.missing_item_ids == []

    # Only the candidate was measured — the parent was a free cache read.
    assert kiln.job_invocations == [("eval_1", "rc_cand", "train")]
    # split rides the job body and every results query.
    job_body = next(b for _, p, _, b in kiln.calls if p == "/api/jobs/evals/run")
    assert job_body["split"] == "train"
    assert job_body["eval_config_id"] == "ec_1"
    assert all(q == {"split": "train"} for *_, q in kiln.results_queries)
    # mcnemar for pass_fail, arrays aligned by sorted item id.
    [stats_body] = kiln.stats_bodies
    assert stats_body["operation"] == "mcnemar_paired"
    assert stats_body["outcomes_a"] == [1, 0, 0, 1]
    assert stats_body["outcomes_b"] == [1, 1, 1, 0]


async def test_five_star_lower_is_better_sign_flip():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(
        scores=[make_score("Tool Errors", "five_star", "lower_is_better")]
    )
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", tool_errors=4.0),
        make_run("i2", tool_errors=3.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", tool_errors=2.0),
        make_run("i2", tool_errors=1.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    # Raw delta is −2.0 (fewer errors); good-direction units flip it positive.
    assert row.delta == pytest.approx(2.0)
    assert row.fail_to_pass is None
    assert row.pass_to_fail is None
    [stats_body] = kiln.stats_bodies
    assert stats_body["operation"] == "compare_paired"
    assert stats_body["values_a"] == [4.0, 3.0]
    assert stats_body["values_b"] == [2.0, 1.0]


async def test_custom_score_type_is_hard_error():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(
        scores=[make_score("Token Count", "custom", "lower_is_better")]
    )
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", token_count=10.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", token_count=9.0)]
    analyzer, _ = make_analyzer(kiln)

    with pytest.raises(UnsupportedScoreTypeError, match="custom"):
        await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])


async def test_p_value_explicitly_null_is_not_significant():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # A legitimately-null p: the key is present, the value is null.
    kiln.stats_response = {"p_exact": None}
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.p_value is None
    assert row.significant is False
    assert row.stderr is None


async def test_stats_response_without_accepted_p_key_is_hard_error():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # Contract mismatch (e.g. a renamed field): no accepted p key at all
    # must fail hard, not silently read as "not significant". (This guard
    # catches a live-shape mismatch such as p_value vs p_exact.)
    kiln.stats_response = {"statistic": 1.2, "significant": True}
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    with pytest.raises(StatsResponseError, match="p_exact"):
        await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])


async def test_mcnemar_ignores_p_value_key_reads_p_exact_only():
    # mcnemar_paired's real
    # response has NO "p_value" — a p_value-only response must fail hard,
    # proving the code reads "p_exact" for this operation.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()  # pass_fail → mcnemar_paired
    kiln.stats_response = {"p_value": 0.03, "standard_error": 0.1}
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    with pytest.raises(StatsResponseError, match="mcnemar_paired"):
        await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])


def _five_star_kiln() -> FakeKiln:
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(
        scores=[make_score("Tone", "five_star", "higher_is_better")]
    )
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", tone=3.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", tone=4.0)]
    return kiln


async def test_compare_paired_reads_wilcoxon_p_and_the_bootstrap_ci():
    # The live compare_paired shape (server source): the p is "wilcoxon_p"
    # and the CI is a paired bootstrap. The fake serves that shape by
    # default; this test pins that the code reads it, not "p_value"/"p_exact".
    kiln = _five_star_kiln()
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.p_value == pytest.approx(0.0391)
    assert row.significant is True
    assert (row.ci_low, row.ci_high) == (pytest.approx(0.125), pytest.approx(0.875))
    assert row.stderr is None


async def test_compare_paired_rejects_the_old_p_value_and_p_exact_keys():
    # Neither key exists on the live response; accepting them would let the
    # fake drift back to a shape the live server does not serve.
    for stats in ({"p_value": 0.6}, {"p_exact": 0.6}):
        kiln = _five_star_kiln()
        kiln.stats_response = stats
        analyzer, _ = make_analyzer(kiln)
        with pytest.raises(StatsResponseError, match="wilcoxon_p"):
            await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])


async def test_compare_paired_small_n_uses_the_ci_when_wilcoxon_is_omitted():
    # Below five non-zero paired differences the server omits wilcoxon_p and
    # judges significance from the bootstrap CI — the normal case at n≈25
    # where most items tie. Code follows the same rule instead of reading
    # every such row as insignificant.
    kiln = _five_star_kiln()
    kiln.stats_response = {
        "operation": "compare_paired",
        "n_pairs_used": 4,
        "mean_delta": 0.5,
        "ci_low": 0.25,
        "ci_high": 0.75,
        "wilcoxon_p": None,
        "significant": True,
    }
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.p_value is None
    assert row.significant is True
    kiln.stats_response["ci_low"] = -0.25  # straddles zero → not significant
    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )
    assert bundle.per_eval[0].significant is False


async def test_compare_paired_with_nothing_comparable_is_a_null_row():
    # n_pairs_used == 0: the server serves neither a p nor a CI — a null
    # row, not a contract mismatch.
    kiln = _five_star_kiln()
    kiln.stats_response = {
        "operation": "compare_paired",
        "n_pairs": 1,
        "n_pairs_used": 0,
        "mean_delta": None,
        "significant": None,
    }
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert (row.p_value, row.ci_low, row.ci_high, row.significant) == (
        None,
        None,
        None,
        False,
    )


async def test_ci_is_sign_normalized_with_the_delta_on_lower_is_better():
    # A lower_is_better rating score: the server's CI is on candidate −
    # baseline in raw units (negative = fewer errors = better); the row
    # flips it into good-direction units with the ends swapped, so
    # ci_low > 0 always means "better beyond doubt".
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(
        scores=[make_score("Tool Errors", "five_star", "lower_is_better")]
    )
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", tool_errors=4.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", tool_errors=2.0)]
    kiln.stats_response = {
        "operation": "compare_paired",
        "n_pairs_used": 1,
        "wilcoxon_p": None,
        "ci_low": -2.5,
        "ci_high": -1.5,
        "significant": True,
    }
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.delta == pytest.approx(2.0)
    assert (row.ci_low, row.ci_high) == (pytest.approx(1.5), pytest.approx(2.5))
    assert row.significant is True


async def test_mcnemar_carries_the_newcombe_ci():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    # The fake's mcnemar shape: ci_low −0.0757, ci_high 0.6503.
    assert (row.ci_low, row.ci_high) == (
        pytest.approx(-0.0757),
        pytest.approx(0.6503),
    )


async def test_insignificant_p_value():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.stats_response = {"p_exact": 0.6}
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert bundle.per_eval[0].significant is False


async def test_spend_split_across_rows_sums_to_total():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(
        scores=[
            make_score("Accuracy", "pass_fail", "higher_is_better"),
            make_score("Helpfulness", "five_star", "higher_is_better"),
        ]
    )
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", cost=0.10, accuracy=0.0, helpfulness=3.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", cost=0.30, accuracy=1.0, helpfulness=4.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert len(bundle.per_eval) == 2
    for row in bundle.per_eval:
        assert row.spend_usd == pytest.approx(0.20)
    assert bundle.spend_total_usd == pytest.approx(0.40)


async def test_missing_default_eval_config_is_hard_error():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(config_id=None)
    analyzer, _ = make_analyzer(kiln)

    with pytest.raises(MissingDefaultEvalConfigError):
        await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])


# ---- the decision tree ----


def seed_screen_fixture(kiln, candidate_accuracy):
    """Parent (rc_base) unmeasured; candidate measures via job; lineage rcs
    seeded by each test."""
    kiln.evals["eval_1"] = make_eval()
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run(f"i{i}", accuracy=v) for i, v in enumerate(candidate_accuracy)
    ]


def lineage_runs(accuracy_by_item):
    return [make_run(item_id, accuracy=v) for item_id, v in accuracy_by_item.items()]


async def test_screen_out_records_prior_baseline_rows():
    kiln = FakeKiln()
    seed_screen_fixture(kiln, [1.0, 0.0, 0.0, 0.0])  # cand 1/4
    kiln.results[("eval_1", "rc_b1", "train")] = lineage_runs(
        {"i0": 1.0, "i1": 1.0, "i2": 1.0, "i3": 0.0}  # ref 3/4 — cand ≤ ref
    )
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_base", "rc_b1"]
    )

    [row] = bundle.per_eval
    assert row.compared_to == "prior_baseline"
    assert row.baseline_rc_id == "rc_b1"
    assert row.delta == pytest.approx(-0.5)
    # The parent was never measured — the screen is the spend gate.
    assert kiln.job_invocations == [("eval_1", "rc_cand", "train")]


async def test_screen_beat_measures_parent_for_attribution():
    kiln = FakeKiln()
    seed_screen_fixture(kiln, [1.0, 1.0, 1.0, 1.0])  # cand 4/4
    kiln.results[("eval_1", "rc_b1", "train")] = lineage_runs(
        {"i0": 1.0, "i1": 0.0, "i2": 1.0, "i3": 0.0}  # ref 2/4 — cand beats it
    )
    kiln.pending[("eval_1", "rc_base", "train")] = lineage_runs(
        {"i0": 0.0, "i1": 1.0, "i2": 0.0, "i3": 1.0}  # parent 2/4, measured now
    )
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_base", "rc_b1"]
    )

    [row] = bundle.per_eval
    assert row.compared_to == "parent"
    assert row.baseline_rc_id == "rc_base"
    assert row.delta == pytest.approx(0.5)
    # Attribution paid for: candidate job + parent job.
    assert kiln.job_invocations == [
        ("eval_1", "rc_cand", "train"),
        ("eval_1", "rc_base", "train"),
    ]


async def test_a_screen_beat_keeps_the_candidates_spend_on_the_final_rows():
    """The screen books the candidate's records; its rows are dropped for
    the parent comparison, so their spend rides on the final rows — booked
    once and reported once, never lost."""
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        {**make_run(f"i{i}", cost=0.01, accuracy=1.0), "id": f"c{i}"} for i in range(4)
    ]
    kiln.results[("eval_1", "rc_b1", "train")] = lineage_runs(
        {"i0": 1.0, "i1": 0.0, "i2": 1.0, "i3": 0.0}
    )
    kiln.pending[("eval_1", "rc_base", "train")] = [
        {**make_run(f"i{i}", cost=0.02, accuracy=float(i % 2)), "id": f"p{i}"}
        for i in range(4)
    ]
    analyzer, _ = make_analyzer(kiln)
    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_base", "rc_b1"]
    )
    [row] = bundle.per_eval
    assert row.compared_to == "parent"
    assert row.spend_usd == pytest.approx(4 * 0.01 + 4 * 0.02)


async def test_no_screen_reference_measures_parent_directly():
    kiln = FakeKiln()
    seed_screen_fixture(kiln, [1.0, 0.0])
    kiln.pending[("eval_1", "rc_base", "train")] = lineage_runs({"i0": 0.0, "i1": 0.0})
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(),
        "rc_cand",
        BASELINE,
        [],  # no lineage coverage → no screen
    )

    [row] = bundle.per_eval
    assert row.compared_to == "parent"
    assert kiln.job_invocations == [
        ("eval_1", "rc_cand", "train"),
        ("eval_1", "rc_base", "train"),
    ]


async def test_lineage_walk_requires_full_coverage_newest_first():
    kiln = FakeKiln()
    seed_screen_fixture(kiln, [0.0, 0.0, 0.0, 0.0])
    # Newest lineage rc covers only 3 of 4 candidate items — skipped.
    kiln.results[("eval_1", "rc_new", "train")] = lineage_runs(
        {"i0": 1.0, "i1": 1.0, "i2": 1.0}
    )
    kiln.results[("eval_1", "rc_old", "train")] = lineage_runs(
        {"i0": 1.0, "i1": 1.0, "i2": 1.0, "i3": 1.0}
    )
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_new", "rc_old"]
    )

    [row] = bundle.per_eval
    assert row.compared_to == "prior_baseline"
    assert row.baseline_rc_id == "rc_old"


async def test_parent_rc_escape_hatch_overrides_baseline():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_other", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(parent_rc_id="rc_other"), "rc_cand", BASELINE, []
    )

    assert bundle.per_eval[0].baseline_rc_id == "rc_other"


async def test_merge_work_parent_is_the_batch_baseline():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)
    item = ExperimentItem(
        id="w2-1",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=MergeWork(wins=[WinRef(experiment_memory_id="mem_exp_1", rc_id="rc_w1")]),
    )

    bundle = await analyzer.run_experiment_analysis(item, "rc_cand", BASELINE, [])

    assert bundle.per_eval[0].baseline_rc_id == "rc_base"
    assert bundle.per_eval[0].compared_to == "parent"


async def test_no_paired_items_is_a_hole_not_a_row():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("j1", accuracy=1.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert bundle.per_eval == []
    [hole] = bundle.errors
    assert "no paired items" in hole.detail
    assert "accuracy" in hole.detail


# ---- eval-job driving ----


async def test_job_failure_retried_then_succeeds():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["fail", "ok"]
    analyzer, sleeps = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert len(bundle.per_eval) == 1
    assert bundle.errors == []
    assert sleeps == [60.0]  # one retry sleep
    assert kiln.job_invocations.count(("eval_1", "rc_cand", "train")) == 2


async def test_errored_items_count_as_failed_attempt():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["errored_items", "ok"]
    analyzer, sleeps = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert len(bundle.per_eval) == 1
    assert sleeps == [60.0]


async def test_exhausted_ladder_ships_named_holes_and_other_evals_land():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(eval_id="eval_1", config_id="ec_1")
    kiln.evals["eval_2"] = make_eval(eval_id="eval_2", config_id="ec_2")
    # Both attempts fail and NOTHING is stored — the zero-rows case is the
    # one that still raises MeasurementFailed → named holes.
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["fail", "fail"]
    kiln.results[("eval_2", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_2", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, sleeps = make_analyzer(kiln)
    item = make_item(
        targets=[EvalTarget(eval_id="eval_1"), EvalTarget(eval_id="eval_2")]
    )

    bundle = await analyzer.run_experiment_analysis(item, "rc_cand", BASELINE, [])

    # eval_1's pair is a named hole; eval_2 landed — the bundle ships anyway.
    [hole] = bundle.errors
    assert hole.error_class == "provider_error"
    assert "eval_1" in hole.detail
    assert "accuracy" in hole.detail
    assert "rc_cand" in hole.detail
    [row] = bundle.per_eval
    assert row.eval_id == "eval_2"
    assert sleeps == [60.0]  # 2 attempts (max 1 retry), 1 sleep


async def test_hung_wait_times_out_and_holes_are_timeout_class():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["hang", "hang"]
    analyzer, sleeps = make_analyzer(
        kiln, job_wait_timeout_secs=0.01, job_wait_margin_secs=0.005
    )

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [hole] = bundle.errors
    assert hole.error_class == "timeout"
    assert len(kiln.job_invocations) == 2
    assert sleeps == [60.0]


async def test_wait_504_classified_as_timeout():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["http504", "http504"]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [hole] = bundle.errors
    assert hole.error_class == "timeout"


async def test_422_missing_split_propagates_unchanged():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["http422"]
    analyzer, sleeps = make_analyzer(kiln)

    with pytest.raises(KilnAPIError) as excinfo:
        await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])

    assert excinfo.value.status_code == 422
    assert sleeps == []  # hard error, never retried into a hole


async def test_multi_eval_target_order_preserved():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(eval_id="eval_1", config_id="ec_1")
    kiln.evals["eval_2"] = make_eval(eval_id="eval_2", config_id="ec_2")
    for eval_id in ("eval_1", "eval_2"):
        kiln.results[(eval_id, "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
        kiln.pending[(eval_id, "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)
    item = make_item(
        targets=[EvalTarget(eval_id="eval_2"), EvalTarget(eval_id="eval_1")]
    )

    bundle = await analyzer.run_experiment_analysis(item, "rc_cand", BASELINE, [])

    assert [row.eval_id for row in bundle.per_eval] == ["eval_2", "eval_1"]


async def test_wait_long_poll_gets_layered_timeouts_and_no_client_retries():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(
        kiln, job_wait_timeout_secs=100.0, job_wait_margin_secs=7.0
    )

    await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])

    [wait] = kiln.wait_requests
    # Server long-poll bound < per-request client timeout (server answers
    # first — its 504 is the normal expiry path), and KilnClient's internal
    # retry ladder is off (the retry ladder owns retries).
    assert wait["body"]["timeout"] == 100.0
    assert wait["timeout_secs"] == 107.0
    assert wait["retries"] is False


async def test_screen_ref_without_target_score_key_is_not_vacuous():
    kiln = FakeKiln()
    seed_screen_fixture(kiln, [0.0, 0.0])
    # Lineage rc covers the item ids but was measured before "accuracy"
    # existed on the eval — no score key anywhere → disqualified as a screen
    # reference; the parent must be measured instead of a vacuous screen-out.
    kiln.results[("eval_1", "rc_b1", "train")] = [
        make_run("i0", other=1.0),
        make_run("i1", other=1.0),
    ]
    kiln.pending[("eval_1", "rc_base", "train")] = lineage_runs({"i0": 1.0, "i1": 0.0})
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_b1"]
    )

    [row] = bundle.per_eval
    assert row.compared_to == "parent"
    assert row.baseline_rc_id == "rc_base"
    assert kiln.job_invocations == [
        ("eval_1", "rc_cand", "train"),
        ("eval_1", "rc_base", "train"),
    ]


async def test_empty_screen_comparison_falls_through_to_parent():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # Candidate measured but its accuracy is null on every item: the ref
    # qualifies (non-null scores on all candidate items), yet pairing yields
    # zero rows — an empty comparison is not evidence of a failed screen, so
    # the parent is still measured (no permanent hole by vacuous screen-out).
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=None)]
    kiln.results[("eval_1", "rc_b1", "train")] = [make_run("i1", accuracy=1.0)]
    kiln.pending[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_b1"]
    )

    assert ("eval_1", "rc_base", "train") in kiln.job_invocations
    # The parent comparison still has no pairable value — a named hole, and
    # no crash on the null score.
    assert bundle.per_eval == []
    assert any("no paired items" in hole.detail for hole in bundle.errors)


async def test_null_score_values_are_skipped_in_pairing():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=None),
        make_run("i2", accuracy=0.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.n_items == 1  # i1's null pair skipped, no TypeError
    assert row.delta == pytest.approx(1.0)


async def test_flaky_results_read_after_success_rides_the_ladder():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    # Job succeeds, then the results GET 500s once — one failed attempt on
    # the retry ladder, not a raw crash out of the analysis.
    kiln.results_behaviors[("eval_1", "rc_cand", "train")] = ["http500"]
    analyzer, sleeps = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert len(bundle.per_eval) == 1
    assert bundle.errors == []
    assert sleeps == [60.0]


async def test_results_read_failures_exhaust_into_holes_not_crash():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(eval_id="eval_1", config_id="ec_1")
    kiln.evals["eval_2"] = make_eval(eval_id="eval_2", config_id="ec_2")
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    # 3 scripted 500s: both ladder attempts' post-success reads AND the
    # exhaustion path's final partial-results read — a dead results route is
    # the zero-rows MeasurementFailed case.
    kiln.results_behaviors[("eval_1", "rc_cand", "train")] = ["http500"] * 3
    kiln.results[("eval_2", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_2", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)
    item = make_item(
        targets=[EvalTarget(eval_id="eval_1"), EvalTarget(eval_id="eval_2")]
    )

    bundle = await analyzer.run_experiment_analysis(item, "rc_cand", BASELINE, [])

    # eval_1 holes; eval_2 still lands — "analysis proceeds on what landed".
    [hole] = bundle.errors
    assert hole.error_class == "provider_error"
    assert "eval_1" in hole.detail
    [row] = bundle.per_eval
    assert row.eval_id == "eval_2"


async def test_all_informational_eval_with_scores_none_is_rejected():
    eval_def = make_eval(
        scores=[
            make_score("Tool Calls", "custom", "informational"),
            make_score("Latency", "custom", "informational"),
        ]
    )
    with pytest.raises(TargetExpansionError, match="no directional scores"):
        expand_targets(eval_def, EvalTarget(eval_id="eval_1"))


# ---- partial results + job-error visibility ----


async def test_partial_results_returned_with_coverage_on_exhaustion():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
        make_run("i3", accuracy=1.0),
    ]
    # The candidate's job errors on i3 both attempts, but i1/i2 stored —
    # exhaustion returns the partial rows with health instead of raising.
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i3", "upstream provider exploded")]
    analyzer, sleeps = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.n_items == 2  # paired on the stored intersection
    assert row.n_expected == 3  # progress totals: 2 success + 1 error
    assert row.coverage == pytest.approx(2 / 3)
    assert row.missing_item_ids == ["i3"]
    # The bundle's error entry names the dark item WITH the fetched message.
    [err] = bundle.errors
    assert err.error_class == "provider_error"
    assert "partial: eval eval_1 rc rc_cand split train" in err.detail
    assert "i3" in err.detail
    assert "upstream provider exploded" in err.detail
    assert sleeps == [60.0]  # max 1 retry
    # One eval_job_errors wave-log event per failed attempt, verbatim rows.
    events = [f for k, f in analyzer.log.events if k == "eval_job_errors"]
    assert [e["attempt"] for e in events] == [1, 2]
    assert events[0]["eval_id"] == "eval_1"
    assert events[0]["rc_id"] == "rc_cand"
    assert events[0]["split"] == "train"
    assert events[0]["errors"] == [
        {"dataset_id": "i3", "error_message": "upstream provider exploded"}
    ]


async def test_subset_scoped_retry_totals_never_shrink_n_expected():
    # CR: a retry job re-runs only the failed set, so its progress totals may
    # be subset-scoped — n_expected accumulates by max, never last-write-wins
    # (coverage must not exceed 1.0 or overstate completeness).
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
        make_run("i3", accuracy=1.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i3", "still failing")]
    # Attempt 1 reports the full set (3); attempt 2 is scoped to the re-run
    # subset (1) — the larger total must win.
    kiln.job_progress[key] = [
        {"success": 2, "error": 1, "total": 3},
        {"success": 0, "error": 1, "total": 1},
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.n_expected == 3
    assert row.coverage == pytest.approx(2 / 3)


async def test_clean_success_n_expected_floored_by_stored_rows():
    # Floor isolated: attempt 1's record carries NO usable
    # totals, attempt 2's clean retry total is subset-scoped (1) and SMALLER
    # than the stored row count (2) — only the stored-row floor keeps
    # coverage from exceeding 1.0.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.pending[key] = [make_run("i1", accuracy=1.0), make_run("i2", accuracy=1.0)]
    kiln.job_behaviors[key] = ["errored_items", "ok"]
    kiln.job_errors[key] = [make_job_error("i2", "flake")]
    kiln.job_progress[key] = [
        {"error": 1},  # no totals — _progress_total yields None
        {"success": 1, "error": 0, "total": 1},  # subset-scoped clean retry
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert bundle.errors == []  # the retry recovered — clean measurement
    assert row.n_expected == 2  # floored by len(stored), not the total (1)
    assert row.coverage == pytest.approx(1.0)
    assert row.missing_item_ids == []


async def test_exhaustion_n_expected_floored_by_stored_rows():
    # Exhaustion floor isolated: the only total ever seen is a
    # subset-scoped 1 on the final failed attempt, while 2 rows are stored —
    # the floor keeps n_expected at len(stored) so coverage caps at 1.0.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
        make_run("i3", accuracy=1.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i3", "still failing")]
    kiln.job_progress[key] = [
        {"error": 1},  # no totals
        {"success": 0, "error": 1, "total": 1},  # subset-scoped retry
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.n_items == 2
    assert row.n_expected == 2  # floored by len(stored), not the total (1)
    assert row.coverage == pytest.approx(1.0)
    assert row.missing_item_ids == ["i3"]  # the dark item stays named


async def test_failed_last_errors_fetch_keeps_earlier_dark_set():
    # The dark set union-accumulates — attempt 2's fetch 500s
    # (→ []) but attempt 1 named i3; the health must keep it.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
        make_run("i3", accuracy=1.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i3", "upstream provider exploded")]
    kiln.job_error_behaviors[key] = ["serve", "http500"]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.missing_item_ids == ["i3"]  # attempt 1's dark set preserved
    [err] = bundle.errors
    assert "i3" in err.detail
    assert "upstream provider exploded" in err.detail
    # The failed fetch still produced its (empty) attempt-2 event.
    events = [f for k, f in analyzer.log.events if k == "eval_job_errors"]
    assert [e["attempt"] for e in events] == [1, 2]
    assert events[1]["errors"] == []


async def test_stale_missing_ids_filtered_against_stored_rows():
    # CR: attempt 1's errors fetch named i2, but i2 IS stored by exhaustion
    # (a later record-less attempt can land items) — the health reports only
    # items genuinely absent from the store.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.job_behaviors[key] = ["errored_items", "http504"]
    kiln.job_errors[key] = [make_job_error("i2", "timed out")]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.n_items == 2  # i2 paired — it IS stored
    assert row.missing_item_ids == []  # stale dark set filtered
    # The measurement still wasn't clean — the partial entry survives with
    # the error substance, but names no stored item as dark.
    [err] = bundle.errors
    assert "unknown items" in err.detail
    assert "timed out" in err.detail


async def test_partial_error_detail_caps_message_count():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run(f"i{i}", accuracy=0.0) for i in range(6)
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [make_run("i0", accuracy=1.0)]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [
        make_job_error(f"i{i}", f"message {i}") for i in range(1, 6)
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [err] = bundle.errors
    # First 3 messages + overflow marker; ids stay complete (the dark set).
    assert "message 1" in err.detail
    assert "message 3" in err.detail
    assert "message 4" not in err.detail
    assert "(and 2 more)" in err.detail
    for i in range(1, 6):
        assert f"i{i}" in err.detail


async def test_partial_parent_measurement_carries_health_and_error_entry():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # Candidate measures clean; the parent (unmeasured, no lineage screen)
    # errors on i2 both attempts with i1 stored — pair on the intersection.
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    parent_key = ("eval_1", "rc_base", "train")
    kiln.partial_results[parent_key] = [make_run("i1", accuracy=0.0)]
    kiln.job_behaviors[parent_key] = ["errored_items", "errored_items"]
    kiln.job_errors[parent_key] = [make_job_error("i2", "judge timeout")]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.compared_to == "parent"
    assert row.n_items == 1
    assert row.n_expected == 2  # pair-level: max(candidate 2, parent 1+1)
    assert row.coverage == pytest.approx(0.5)
    assert row.missing_item_ids == ["i2"]
    [err] = bundle.errors
    assert "partial: eval eval_1 rc rc_base split train" in err.detail
    assert "judge timeout" in err.detail


# ---- row position: raw absolutes + per-side job ids ----


async def test_binary_row_carries_pass_rate_absolutes():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=0.0),
        make_run("i3", accuracy=0.0),
        make_run("i4", accuracy=0.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
        make_run("i3", accuracy=1.0),
        make_run("i4", accuracy=0.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_base"]
    )

    [row] = bundle.per_eval
    assert row.baseline_score == pytest.approx(0.25)
    assert row.candidate_score == pytest.approx(0.75)
    # On a higher_is_better score the delta is exactly the gap between them.
    assert row.delta == pytest.approx(row.candidate_score - row.baseline_score)


async def test_lower_is_better_row_carries_raw_absolutes_beside_a_normalized_delta():
    # The point of keeping the absolutes raw: a lower_is_better improvement
    # reads as a POSITIVE delta (good-direction units) while the scores it
    # was computed from FALL. Normalizing them would invent numbers matching
    # no stored score.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(
        scores=[make_score("Tool Errors", "five_star", "lower_is_better")]
    )
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", tool_errors=4.0),
        make_run("i2", tool_errors=3.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", tool_errors=2.0),
        make_run("i2", tool_errors=1.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.delta == pytest.approx(2.0)  # improvement, sign-normalized
    assert row.baseline_score == pytest.approx(3.5)  # raw means, in scale units
    assert row.candidate_score == pytest.approx(1.5)
    assert row.candidate_score < row.baseline_score  # they fall, delta rises


async def test_measured_side_carries_its_job_id_and_a_cached_side_is_none():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # The baseline is already stored — a free cache read, no job, no id.
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_base"]
    )

    [row] = bundle.per_eval
    assert row.candidate_job_id == "job_1"
    assert row.baseline_job_id is None


async def test_both_measured_sides_carry_their_own_job_ids():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # Neither side is stored: the candidate's job runs first, then the
    # parent's — each id must land on its own side of the row.
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    kiln.pending[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert kiln.job_invocations == [
        ("eval_1", "rc_cand", "train"),
        ("eval_1", "rc_base", "train"),
    ]
    assert row.candidate_job_id == "job_1"
    assert row.baseline_job_id == "job_2"


async def test_partial_coverage_row_names_the_last_job_even_when_its_wait_raised():
    # CR: the job is created BEFORE the long-poll, so a wait that 504s (or
    # hangs, or drops) still left a real job running — and that job is the
    # one holding the errors behind the hole. Naming attempt 1 here would
    # route the promote agent to the wrong job's errors.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
        make_run("i3", accuracy=1.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.job_behaviors[key] = ["errored_items", "http504"]
    kiln.job_errors[key] = [make_job_error("i3", "upstream provider exploded")]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.coverage == pytest.approx(2 / 3)
    assert row.candidate_job_id == "job_2"


async def test_a_side_whose_every_wait_raised_still_names_its_last_job():
    # Both attempts 504 on the wait: two jobs ran, none reported. The rows
    # exist (results were stored), so the id must too — "a partial-coverage
    # row carries a non-null job id for whichever side(s) ran a job".
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    key = ("eval_1", "rc_cand", "train")
    kiln.results[key] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[key] = ["http504", "http504"]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.candidate_job_id == "job_2"


async def test_partial_coverage_row_carries_the_last_attempt_job_id():
    # The retry ladder ran two jobs on the candidate and still exhausted —
    # the row must name the LAST attempt, which is the one whose errors the
    # promote agent would fetch.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
        make_run("i3", accuracy=1.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i3", "upstream provider exploded")]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.coverage == pytest.approx(2 / 3)  # the case the id serves
    assert row.candidate_job_id == "job_2"
    assert row.baseline_job_id is None


async def test_job_errors_fetch_failure_is_never_fatal():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    key = ("eval_1", "rc_cand", "train")
    kiln.pending[key] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[key] = ["errored_items", "ok"]
    kiln.job_errors[key] = [make_job_error("i1", "flake")]
    # The visibility fetch itself 500s — measurement proceeds regardless.
    kiln.job_error_behaviors[key] = ["http500"]
    analyzer, sleeps = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert bundle.errors == []  # the retry recovered — clean measurement
    assert row.coverage == pytest.approx(1.0)
    assert sleeps == [60.0]
    [event] = [f for k, f in analyzer.log.events if k == "eval_job_errors"]
    assert event["errors"] == []  # fetch failed → event still fires, empty


async def test_job_error_messages_truncated_to_500_chars():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=1.0),
    ]
    key = ("eval_1", "rc_cand", "train")
    kiln.partial_results[key] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i2", "x" * 900)]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [event] = [
        f
        for k, f in analyzer.log.events
        if k == "eval_job_errors" and f["attempt"] == 1
    ]
    assert event["errors"][0]["error_message"] == "x" * 500
    [err] = bundle.errors
    assert "x" * 500 in err.detail
    assert "x" * 501 not in err.detail


async def test_zero_rows_after_exhaustion_is_still_measurement_failed():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    # errored_items with NO partial rows scripted — nothing ever stored.
    key = ("eval_1", "rc_cand", "train")
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i1", "boom"), make_job_error("i2", "boom")]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert bundle.per_eval == []
    [hole] = bundle.errors
    assert "unmeasured" in hole.detail


async def test_eval_usage_judge_cost_counted_in_spend():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("i1", cost=0.10, eval_cost=0.02, accuracy=0.0)
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", cost=0.30, eval_cost=0.08, accuracy=1.0)
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    # task_run_usage + eval_usage: the judge cost is spend too.
    assert bundle.spend_total_usd == pytest.approx(0.10 + 0.02 + 0.30 + 0.08)


# ---- concurrent target measurement ----


async def test_parallel_targets_match_sequential_results_and_emit_start_done():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(eval_id="eval_1", config_id="ec_1")
    kiln.evals["eval_2"] = make_eval(eval_id="eval_2", config_id="ec_2")
    for eval_id in ("eval_1", "eval_2"):
        kiln.results[(eval_id, "rc_base", "train")] = [
            make_run("i1", cost=0.01, accuracy=0.0),
            make_run("i2", cost=0.01, accuracy=1.0),
        ]
        kiln.pending[(eval_id, "rc_cand", "train")] = [
            make_run("i1", cost=0.02, accuracy=1.0),
            make_run("i2", cost=0.02, accuracy=1.0),
        ]
    # `FakeKiln.call` answers without ever suspending, so two legs that share
    # it run one after the other and the interleaving this test is about
    # cannot happen. A real client awaits a socket; this wrapper restores that
    # one property, for this test alone.
    answer = kiln.call

    async def yielding_call(*args, **kwargs):
        await asyncio.sleep(0)
        return await answer(*args, **kwargs)

    kiln.call = yielding_call
    analyzer, _ = make_analyzer(kiln)
    item = make_item(
        targets=[EvalTarget(eval_id="eval_2"), EvalTarget(eval_id="eval_1")]
    )
    lines: list[str] = []

    bundle = await analyzer.run_experiment_analysis(
        item, "rc_cand", BASELINE, [], progress=lines.append
    )

    # Rows assemble in target order regardless of completion order — the
    # sequential path's exact result.
    assert [row.eval_id for row in bundle.per_eval] == ["eval_2", "eval_1"]
    assert bundle.errors == []
    for row in bundle.per_eval:
        assert row.delta == pytest.approx(0.5)
        assert row.n_items == 2
        assert row.spend_usd == pytest.approx(0.06)
    assert bundle.spend_total_usd == pytest.approx(0.12)
    # Per-eval start/done detail lines replace i/N; both starts
    # fire before either done — the targets genuinely overlap.
    assert sorted(lines) == [
        "eval_1 done",
        "eval_2 done",
        "measuring eval_1…",
        "measuring eval_2…",
    ]
    assert lines[0].startswith("measuring") and lines[1].startswith("measuring")


async def test_parallel_hard_error_lets_siblings_finish_then_raises():
    kiln = FakeKiln()
    # eval_1 has no default config (hard error); eval_2 measures clean.
    kiln.evals["eval_1"] = make_eval(eval_id="eval_1", config_id=None)
    kiln.evals["eval_2"] = make_eval(eval_id="eval_2", config_id="ec_2")
    kiln.results[("eval_2", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_2", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln)
    item = make_item(
        targets=[EvalTarget(eval_id="eval_1"), EvalTarget(eval_id="eval_2")]
    )
    lines: list[str] = []

    with pytest.raises(MissingDefaultEvalConfigError):
        await analyzer.run_experiment_analysis(
            item, "rc_cand", BASELINE, [], progress=lines.append
        )

    # The sibling's measurement still landed before the raise (the
    # no-orphaned-awaits doctrine, same as the wave gather).
    assert ("eval_2", "rc_cand", "train") in kiln.job_invocations
    # The failing target reports "failed", never "done"; the sibling closes
    # normally.
    assert "eval_1 failed" in lines
    assert "eval_1 done" not in lines
    assert "eval_2 done" in lines


async def test_all_informational_target_short_circuits_before_measurement():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(
        scores=[make_score("Tool Calls", "custom", "informational")]
    )
    analyzer, _ = make_analyzer(kiln)

    with pytest.raises(TargetExpansionError):
        await analyzer.run_experiment_analysis(make_item(), "rc_cand", BASELINE, [])

    assert kiln.job_invocations == []  # no spend before the guard fired


def test_an_explicit_empty_score_list_is_refused_before_measurement():
    # Defence in depth behind the schema's own min_length=1 (test_schemas):
    # an empty expansion would otherwise vanish silently AFTER the job is
    # paid for. `model_construct` skips validation to reach this guard.
    target = EvalTarget.model_construct(eval_id="eval_1", scores=[])
    with pytest.raises(TargetExpansionError, match="empty score list"):
        expand_targets(make_eval(), target)


# ---- normalization + the any-headroom reproduction rule ----


def spec(
    key: str = "accuracy",
    type: str = "pass_fail",
    direction: str = "higher_is_better",
) -> ScoreSpec:
    return ScoreSpec(key, type, direction)


_scored_ids = itertools.count(1)


def scored(**scores: float) -> dict:
    """One stored EvalRun carrying score values, each on its own item (the
    rule reads values, it does not pair — but several rows
    on ONE item fold into a replicate mean, so distinct rows need distinct
    ids). Built directly rather than via `make_run`, whose `cost`/`eval_cost`
    kwargs would swallow score names."""
    return {"dataset_id": f"item_{next(_scored_ids)}", "scores": dict(scores)}


class TestNormalize:
    @pytest.mark.parametrize(
        ("score_type", "worst", "middle", "best"),
        [
            ("pass_fail", 0.0, 0.5, 1.0),
            # Kiln's -1/0/1 encoding: the midpoint is a plain fail, not half
            # a pass — which is why this type needs its own row.
            ("pass_fail_critical", -1.0, 0.0, 1.0),
            ("five_star", 1.0, 3.0, 5.0),
        ],
    )
    def test_maps_every_score_type_onto_zero_to_one(
        self, score_type, worst, middle, best
    ):
        s = spec(type=score_type)
        assert normalize(worst, s) == 0.0
        assert normalize(middle, s) == pytest.approx(0.5)
        assert normalize(best, s) == 1.0

    def test_inverts_lower_is_better(self):
        s = spec(type="five_star", direction="lower_is_better")
        assert normalize(1.0, s) == 1.0  # lowest rating = best
        assert normalize(5.0, s) == 0.0

    def test_clamps_out_of_domain_values(self):
        # The [0,1] range is the contract the headroom test reads; a drifted
        # value must not leave it.
        assert normalize(9.0, spec(type="five_star")) == 1.0
        assert normalize(-4.0, spec(type="pass_fail")) == 0.0

    def test_rejects_an_unmapped_score_type(self):
        with pytest.raises(UnsupportedScoreTypeError, match="normalization"):
            normalize(1.0, spec(type="custom"))


class TestReproductionVerdict:
    def test_any_headroom_on_any_score_reproduces(self):
        # The multi-score case: one score is perfect, the other is
        # not — the issue reproduces.
        runs = [scored(accuracy=1.0, tone=3.0), scored(accuracy=1.0, tone=5.0)]
        verdict = reproduction_verdict(
            runs, [spec("accuracy"), spec("tone", "five_star")]
        )
        assert verdict.reproduced is True

    def test_a_perfect_eval_does_not_reproduce(self):
        runs = [scored(accuracy=1.0), scored(accuracy=1.0)]
        assert reproduction_verdict(runs, [spec()]).reproduced is False

    def test_one_flaky_item_reproduces(self):
        # The consequence the rule accepts: the
        # conservative direction, since a false "not reproduced" silently
        # closes a genuine problem.
        runs = [scored(accuracy=1.0)] * 19 + [scored(accuracy=0.0)]
        assert reproduction_verdict(runs, [spec()]).reproduced is True

    def test_summary_carries_each_score_and_its_n(self):
        # String-asserted: this lands verbatim in the issue memory line
        # `record_baseline_measurement` writes.
        runs = [scored(accuracy=1.0, tone=5.0), scored(accuracy=0.0, tone=4.0)]
        verdict = reproduction_verdict(
            runs, [spec("accuracy"), spec("tone", "five_star")]
        )
        assert verdict.summary == (
            "accuracy 0.5, n=2/2; tone 0.875, n=2/2 — normalized, 1.00 = best"
        )

    def test_lower_is_better_perfect_run_does_not_reproduce(self):
        # The direction flag has to reach the RULE, not just normalize().
        runs = [scored(cost=1.0), scored(cost=1.0)]
        verdict = reproduction_verdict(
            runs, [spec("cost", "five_star", "lower_is_better")]
        )
        assert verdict.reproduced is False

    def test_a_negative_reached_with_a_score_missing_is_inconclusive(self):
        # A skipped score contributes nothing to an any(), which is the same
        # as contributing False — so the SKIP buys nothing on its own. The
        # rule is on the verdict: "nothing reproduced" needs every targeted
        # score measured, or WONT_FIX: lands on evidence never gathered.
        runs = [scored(accuracy=1.0), scored(accuracy=1.0)]
        with pytest.raises(InconclusiveNegativeError, match="cannot conclude"):
            reproduction_verdict(runs, [spec("accuracy"), spec("tone")])

    def test_headroom_elsewhere_stays_conclusive_despite_a_missing_score(self):
        # Asymmetric on purpose: more evidence could not UN-reproduce it, so
        # a positive stands — and the summary still names the gap.
        runs = [scored(accuracy=0.0), scored(accuracy=1.0)]
        verdict = reproduction_verdict(runs, [spec("accuracy"), spec("tone")])
        assert verdict.reproduced is True
        assert "tone unmeasured" in verdict.summary

    def test_a_null_score_value_is_not_counted_but_still_counts_against_coverage(
        self,
    ):
        # It is not averaged in, and it is not evidence either: 1-of-2 rows
        # cannot support a terminal "not reproduced".
        runs = [
            scored(accuracy=1.0),
            {"dataset_id": "i2", "scores": {"accuracy": None}},
        ]
        with pytest.raises(InconclusiveNegativeError, match=r"accuracy \(1/2\)"):
            reproduction_verdict(runs, [spec("accuracy")])

    def test_a_positive_survives_partial_per_score_coverage(self):
        runs = [
            scored(accuracy=0.0),
            {"dataset_id": "i2", "scores": {"accuracy": None}},
        ]
        verdict = reproduction_verdict(runs, [spec("accuracy")])
        assert verdict.reproduced is True
        assert verdict.summary.startswith("accuracy 0, n=1/2")

    def test_a_score_on_one_row_of_many_cannot_close_an_issue(self):
        # One level down from a partial split: a clean job whose judge emitted
        # the score on 1 of 24 rows must not settle the issue WONT_FIX: off
        # that single data point.
        runs = [scored(accuracy=1.0)] + [scored(other=1.0) for _ in range(23)]
        with pytest.raises(InconclusiveNegativeError, match=r"accuracy \(1/24\)"):
            reproduction_verdict(runs, [spec("accuracy")])

    def test_a_complete_perfect_measurement_still_concludes(self):
        # The guard must not make every negative unreachable.
        runs = [scored(accuracy=1.0) for _ in range(24)]
        verdict = reproduction_verdict(runs, [spec("accuracy")])
        assert verdict.reproduced is False
        assert "accuracy 1, n=24/24" in verdict.summary

    def test_the_summary_denominator_is_self_diagnosing(self):
        # n=1/24 tells the reader the coverage story whichever way the
        # verdict went — the memory line is the only artifact a planner sees.
        runs = [scored(accuracy=0.0)] + [scored(other=1.0) for _ in range(23)]
        assert (
            "accuracy 0, n=1/24"
            in reproduction_verdict(runs, [spec("accuracy")]).summary
        )

    def test_n_expected_is_the_denominator_when_it_outruns_the_stored_rows(self):
        # health.clean says nothing about len(runs) vs n_expected: a
        # "succeeded, 0 errors" job whose progress total outruns what the
        # results route returns would otherwise settle the issue off a
        # capped read — and print n=10/10 while the split held 24.
        runs = [scored(accuracy=1.0) for _ in range(10)]
        with pytest.raises(InconclusiveNegativeError, match=r"accuracy \(10/24\)"):
            reproduction_verdict(runs, [spec("accuracy")], n_expected=24)

    def test_n_expected_rides_the_summary_on_a_positive(self):
        # The positive still concludes, but the memory line must not claim
        # full coverage — this is the one case where it would actively lie.
        runs = [scored(accuracy=0.0)] + [scored(accuracy=1.0) for _ in range(9)]
        verdict = reproduction_verdict(runs, [spec("accuracy")], n_expected=24)
        assert verdict.reproduced is True
        assert "accuracy 0.9, n=10/24" in verdict.summary

    def test_a_matching_n_expected_still_concludes(self):
        runs = [scored(accuracy=1.0) for _ in range(10)]
        verdict = reproduction_verdict(runs, [spec("accuracy")], n_expected=10)
        assert verdict.reproduced is False
        assert "accuracy 1, n=10/10" in verdict.summary

    def test_a_smaller_n_expected_never_shrinks_the_denominator(self):
        runs = [scored(accuracy=1.0) for _ in range(10)]
        verdict = reproduction_verdict(runs, [spec("accuracy")], n_expected=4)
        assert "accuracy 1, n=10/10" in verdict.summary

    def test_a_near_perfect_continuous_mean_never_prints_as_perfect(self):
        # Continuous per-run values (live in this fixture:
        # knowledge_coverage 0.6667 &c) make this n-INDEPENDENT: a confident
        # judge answering 0.9999 three times formats to "1" under :.3g,
        # printing a perfect score beside a "reproduced" verdict.
        runs = [scored(accuracy=0.9999) for _ in range(3)]
        verdict = reproduction_verdict(runs, [spec("accuracy")])
        assert verdict.reproduced is True
        assert "accuracy 0.9999, n=3/3" in verdict.summary

    def test_no_mean_with_headroom_can_ever_print_as_perfect(self):
        # The property, swept rather than sampled: every value the headroom
        # test calls "reproduced" must render below the perfect value.
        for mean in (0.5, 0.9, 0.99, 0.999, 0.9999, 0.999999, 1.0 - 1e-8):
            assert float(_format_mean(mean, has_headroom=True)) < 1.0

    def test_the_bound_is_the_last_resort_for_an_unprintable_mean(self):
        # Unreachable through reproduction_verdict as long as the headroom
        # EPS (1e-9) is coarser than the widest precision tried (9 sig figs)
        # — kept so the two cannot drift apart silently.
        assert _format_mean(1.0 - 1e-12, has_headroom=True) == "<1"

    def test_a_genuinely_perfect_mean_still_prints_as_one(self):
        runs = [scored(accuracy=1.0)]
        verdict = reproduction_verdict(runs, [spec("accuracy")])
        assert verdict.reproduced is False
        assert "accuracy 1, n=1/1" in verdict.summary

    def test_a_flaky_item_at_scale_never_prints_a_perfect_mean(self):
        # :.3g, not :.2f — at n=300 one failure rounds to "1.00", which would
        # contradict the "reproduced" verdict written beside it.
        runs = [scored(accuracy=0.0)] + [scored(accuracy=1.0) for _ in range(299)]
        verdict = reproduction_verdict(runs, [spec("accuracy")])
        assert verdict.reproduced is True
        assert "accuracy 0.997, n=300/300" in verdict.summary

    def test_no_measured_score_at_all_raises(self):
        # Distinct from MeasurementFailed (nothing stored at all): rows exist,
        # they just carry nothing for the targeted score.
        with pytest.raises(InconclusiveNegativeError, match="cannot conclude"):
            reproduction_verdict([scored(other=1.0)], [spec("accuracy")])

    def test_zero_stored_runs_never_concludes(self):
        # A CLEAN job that stored nothing reaches the verdict — only the
        # EXHAUSTED ladder raises MeasurementFailed. The per-score coverage
        # test is `len(values) < total`, vacuously true at total=0, so this
        # needs its own guard or every score reads as fully measured.
        with pytest.raises(InconclusiveNegativeError, match="no stored runs"):
            reproduction_verdict([], [spec("accuracy")])

    def test_an_empty_spec_list_never_concludes(self):
        # Defence in depth behind expand_targets' own guard — reaching here
        # would close an issue WONT_FIX: having measured nothing at all.
        with pytest.raises(InconclusiveNegativeError, match="no targeted scores"):
            reproduction_verdict([scored(accuracy=1.0)], [])


def test_flatten_result_row_reads_the_wrapper_the_route_serves_now():
    # The score record moved under `eval_run` when the trace was split off it.
    # Everything the analysis reads — the item id, the scores, both usages —
    # has to survive that.
    row = {
        "eval_run": {
            "eval_input_id": "i1",
            "scores": {"quality": 1.0},
            "eval_usage": {"cost": 0.1},
            "task_run_usage": {"cost": 0.0},
            "task_run_trace": None,
        },
        "input": "in",
        "output": "out",
        "task_run_trace": [{"role": "user"}],
        "task_run_usage": {"cost": 0.25},
    }

    flat = flatten_result_row(row)

    assert flat["eval_input_id"] == "i1"
    assert flat["scores"] == {"quality": 1.0}
    assert flat["eval_usage"] == {"cost": 0.1}
    # The wrapper's resolved trace and usage win over the record's own: the
    # trace lives on the scored TaskRun, and only the wrapper resolves it.
    assert flat["task_run_trace"] == [{"role": "user"}]
    assert flat["task_run_usage"] == {"cost": 0.25}
    assert flat["input"] == "in"


def test_flatten_result_row_passes_a_bare_eval_run_through():
    # The older route served these directly, and kiln-harness-tuner runs against one.
    row = {"dataset_id": "i1", "scores": {"quality": 1.0}}

    assert flatten_result_row(row) == row


def test_flatten_result_row_leaves_a_non_dict_alone():
    assert flatten_result_row("nonsense") == "nonsense"
    assert flatten_result_row({"eval_run": "not a dict"}) == {"eval_run": "not a dict"}


async def test_stored_results_pairs_items_out_of_the_wrapped_rows():
    # The regression this guards is silent: unflattened, `_item_id` finds no id
    # on the wrapper, every row drops out of `_runs_by_item`, and a comparison
    # pairs nothing while reporting a clean read.
    kiln = FakeKiln()
    kiln.results[("eval_1", "rc_1", "train")] = [
        make_run("i1", cost=0.25, quality=1.0),
        make_run("i2", cost=0.15, quality=0.0),
    ]
    analyzer = Analyzer(kiln, project_id="p1", task_id="t1")

    runs = await analyzer.stored_results("eval_1", "ec_1", "rc_1", "train")

    assert pairable_item_count(runs) == 2
    assert runs_cost(runs) == pytest.approx(0.4)


def test_runs_cost_sums_subject_and_judge_cost():
    runs = [make_run("i1", cost=0.25, eval_cost=0.1), make_run("i2", cost=0.15)]
    assert runs_cost(runs) == pytest.approx(0.5)
    assert runs_cost([]) == 0.0


# ---- class-aware provider retry ladder ----

GROQ_RATE_LIMIT_MSG = (
    "litellm.RateLimitError: GroqException - Rate limit reached … "
    "Please try again in 500ms."
)
QUOTA_MSG = (
    "This request requires more credits, or fewer max_tokens. To increase, "
    "visit https://openrouter.ai/settings/keys"
)
JUDGE_JSON_MSG = "This task requires JSON output but the model didn't return valid JSON"


def _seed_flaky_candidate(kiln, behaviors, message):
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.pending[key] = [make_run("i1", accuracy=1.0), make_run("i2", accuracy=1.0)]
    kiln.job_behaviors[key] = behaviors
    kiln.job_errors[key] = [make_job_error("i1", message)]
    return key


async def test_rate_limited_job_gets_extra_attempts_with_exponential_backoff():
    kiln = FakeKiln()
    # Three throttled attempts, then clean — under the old flat 2-attempt
    # ladder this measurement was a permanent hole.
    _seed_flaky_candidate(
        kiln,
        ["errored_items", "errored_items", "errored_items", "ok"],
        GROQ_RATE_LIMIT_MSG,
    )
    analyzer, sleeps = make_analyzer(kiln)

    runs, health = await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert health.clean
    assert len(runs) == 2
    # 60 → 120 → 240 (the provider's 500ms hint is below every rung).
    assert sleeps == [60.0, 120.0, 240.0]
    events = [f for k, f in analyzer.log.events if k == "eval_job_errors"]
    assert [e["error_class"] for e in events] == ["rate_limit"] * 3


async def test_rate_limit_backoff_honors_the_hint_and_the_cap():
    kiln = FakeKiln()
    _seed_flaky_candidate(
        kiln,
        ["errored_items", "ok"],
        "rate limit exceeded — try again in 400s",
    )
    analyzer, sleeps = make_analyzer(kiln)

    await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    # The 400s provider hint beats the 60s base rung but is capped at 300s.
    assert sleeps == [300.0]


async def test_rate_limit_exhaustion_keeps_its_class_on_the_error():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.job_behaviors[key] = ["errored_items"] * 4
    kiln.job_errors[key] = [make_job_error("i1", GROQ_RATE_LIMIT_MSG)]
    analyzer, sleeps = make_analyzer(kiln)

    with pytest.raises(MeasurementFailed) as exc_info:
        await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert exc_info.value.error.error_class == "rate_limit"
    assert len(kiln.job_invocations) == 4  # rate_limit_attempts, not 2
    assert sleeps == [60.0, 120.0, 240.0]


async def test_quota_exhaustion_circuit_breaks_without_retrying():
    from kiln_harness_tuner.provider_errors import ProviderQuotaError

    kiln = FakeKiln()
    _seed_flaky_candidate(kiln, ["errored_items", "errored_items"], QUOTA_MSG)
    analyzer, sleeps = make_analyzer(kiln)

    with pytest.raises(ProviderQuotaError) as exc_info:
        await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert exc_info.value.error.error_class == "quota_exhausted"
    # The remedy hint reaches the operator verbatim.
    assert "openrouter.ai/settings" in exc_info.value.error.detail
    assert len(kiln.job_invocations) == 1  # never retried
    assert sleeps == []


async def test_malformed_output_gets_extra_attempts_without_a_cooldown():
    # Judge output flakes recover a slice per re-run, and eval jobs are
    # idempotent (only the errored subset re-runs) — so this class gets the
    # escalated attempt budget with zero sleep between rungs.
    kiln = FakeKiln()
    _seed_flaky_candidate(
        kiln,
        ["errored_items", "errored_items", "errored_items", "ok"],
        JUDGE_JSON_MSG,
    )
    analyzer, sleeps = make_analyzer(kiln)

    runs, health = await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert health.clean and len(runs) == 2
    assert sleeps == []  # the 60s cooldown bought nothing on a judge bug
    assert len(kiln.job_invocations) == 4  # escalated past the generic 2


async def test_concurrent_jobs_respect_the_semaphore_cap():
    kiln = FakeKiln()
    for eval_id in ("eval_1", "eval_2", "eval_3"):
        kiln.evals[eval_id] = make_eval(eval_id, "ec_1")
        kiln.pending[(eval_id, "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    analyzer, _ = make_analyzer(kiln, max_concurrent_jobs=1)

    import asyncio

    results = await asyncio.gather(
        *(
            analyzer.ensure_measured(eval_id, "ec_1", "rc_cand", "train")
            for eval_id in ("eval_1", "eval_2", "eval_3")
        )
    )

    assert all(health.clean for _runs, health in results)
    assert len(kiln.job_invocations) == 3


# ---- Jobs API: job-level error summary + orphan cancel ----


async def test_job_level_error_summary_reaches_the_classifier():
    # A worker-level crash stamps JobError on the record and leaves ZERO
    # per-item rows — the classifier must see it anyway. Here the summary is
    # a rate limit, so the ladder escalates instead of exhausting at 2.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.pending[key] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[key] = ["fail", "fail", "fail", "ok"]
    kiln.job_record_errors[key] = [
        {"error": "worker died: litellm.RateLimitError — try again in 1s"}
    ] * 3
    analyzer, sleeps = make_analyzer(kiln)

    runs, health = await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert health.clean and len(runs) == 1
    assert len(kiln.job_invocations) == 4  # escalated on the job-level class
    assert sleeps == [60.0, 120.0, 240.0]
    events = [f for k, f in analyzer.log.events if k == "eval_job_errors"]
    assert all(e["error_class"] == "rate_limit" for e in events)
    assert all("worker died" in e["job_error"] for e in events)


async def test_job_level_quota_error_circuit_breaks():
    from kiln_harness_tuner.provider_errors import ProviderQuotaError

    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.job_behaviors[key] = ["fail"]
    kiln.job_record_errors[key] = [
        {"error": "This request requires more credits, or fewer max_tokens."}
    ]
    analyzer, sleeps = make_analyzer(kiln)

    with pytest.raises(ProviderQuotaError):
        await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert len(kiln.job_invocations) == 1
    assert sleeps == []


async def test_job_level_error_lands_in_the_exhaustion_detail():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.job_behaviors[key] = ["fail", "fail"]
    kiln.job_record_errors[key] = [{"error": "worker exploded: OOM"}] * 2

    analyzer, _ = make_analyzer(kiln)
    with pytest.raises(MeasurementFailed) as exc_info:
        await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert "worker exploded: OOM" in exc_info.value.error.detail


async def test_wait_timeout_cancels_the_orphaned_job():
    # The wait is a pure observer — its timeout never stops the job, which
    # keeps running and spending server-side while attempt 2 races it.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.pending[key] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[key] = ["hang", "ok"]
    analyzer, _ = make_analyzer(
        kiln, job_wait_timeout_secs=0.01, job_wait_margin_secs=0.005
    )

    runs, health = await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert health.clean and len(runs) == 1
    assert kiln.job_cancels == ["job_1"]  # the hung attempt's own job only


async def test_wait_504_cancels_before_retrying():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.pending[key] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[key] = ["http504", "ok"]
    analyzer, _ = make_analyzer(kiln)

    await analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")

    assert kiln.job_cancels == ["job_1"]


async def test_caller_cancellation_cancels_the_server_job():
    # The run's hard deadline cancels the awaiting task mid-wait; the server
    # job survives observer teardown — the shielded best-effort cancel is
    # what stops its spend (the eval-lane twin of the dispatcher's
    # kill-and-reap).
    import asyncio

    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.job_behaviors[key] = ["hang"]
    analyzer, _ = make_analyzer(kiln)

    task = asyncio.ensure_future(
        analyzer.ensure_measured("eval_1", "ec_1", "rc_cand", "train")
    )
    await asyncio.sleep(0.05)  # let it start the job and enter the wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert kiln.job_cancels == ["job_1"]


def _run_at(item_id: str, created_at: str | None, cost: float, **scores):
    run = make_run(item_id, cost=cost, **scores)
    if created_at is not None:
        run["created_at"] = created_at
    return run


async def test_spend_books_only_rows_created_after_run_start():
    """Budget honesty: stored baseline rows paid for by an earlier run are
    EVIDENCE, not this run's spend, so they are not re-booked on every
    comparison."""
    from datetime import datetime

    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        _run_at("i1", "2026-07-01T10:00:00", 4.0, accuracy=0.0),  # earlier run
        _run_at("i2", "2026-07-01T10:00:00-04:00", 4.0, accuracy=1.0),  # aware
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        _run_at("i1", "2026-08-01T12:30:00", 1.0, accuracy=1.0),  # this run
        _run_at("i2", None, 1.0, accuracy=1.0),  # no timestamp → counted
    ]
    analyzer, _ = make_analyzer(kiln, run_started_at=datetime(2026, 8, 1, 12, 0))

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert bundle.spend_total_usd == pytest.approx(2.0)
    assert bundle.evidence_spend_usd == pytest.approx(10.0)
    [row] = bundle.per_eval
    assert row.spend_usd == pytest.approx(2.0)
    assert row.evidence_spend_usd == pytest.approx(10.0)


def test_run_is_fresh_edge_cases():
    from datetime import datetime

    from kiln_harness_tuner.analysis import run_is_fresh

    start = datetime(2026, 8, 1, 12, 0)
    assert run_is_fresh({"created_at": "2026-08-01T12:00:00"}, start)
    # Inside the clock-skew grace window still counts as this run's.
    assert run_is_fresh({"created_at": "2026-08-01T11:58:30"}, start)
    assert not run_is_fresh({"created_at": "2026-08-01T11:00:00"}, start)
    # Unknown never under-counts the budget.
    assert run_is_fresh({}, start)
    assert run_is_fresh({"created_at": "not a date"}, start)
    assert run_is_fresh({"created_at": 12345}, start)
    assert run_is_fresh({"created_at": "2026-01-01T00:00:00"}, None)


async def test_no_run_start_counts_every_row_as_before():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [
        _run_at("i1", "2026-07-01T10:00:00", 4.0, accuracy=0.0)
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        _run_at("i1", "2026-07-01T10:00:00", 1.0, accuracy=1.0)
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    assert bundle.spend_total_usd == pytest.approx(5.0)
    assert bundle.evidence_spend_usd == pytest.approx(5.0)


def _row(eval_id="eval_1", key="accuracy", delta=0.1, ci=(None, None), coverage=None):
    from kiln_harness_tuner.schemas import PerEvalResult

    return PerEvalResult(
        eval_id=eval_id,
        score_key=key,
        split="train",
        baseline_rc_id="rc_base",
        compared_to="parent",
        n_items=25,
        delta=delta,
        p_value=None,
        significant=False,
        ci_low=ci[0],
        ci_high=ci[1],
        fail_to_pass=None,
        pass_to_fail=None,
        spend_usd=0.0,
        coverage=coverage,
    )


def _bundle(*rows):
    from kiln_harness_tuner.schemas import AnalysisBundle

    return AnalysisBundle(per_eval=list(rows), spend_total_usd=0.0, errors=[])


class TestSuccessByRule:
    """The code floor under the analyzer's `successful`."""

    def test_primary_improved_and_no_proven_regression_is_ok(self):
        from kiln_harness_tuner.analysis import success_by_rule

        v = success_by_rule(
            make_item(),
            _bundle(
                _row(delta=0.2, ci=(-0.05, 0.4)),
                _row("eval_2", delta=-0.02, ci=(-0.2, 0.1)),
            ),
            0.95,
        )
        assert v.ok and v.reasons == []

    def test_no_rows_is_not_ok(self):
        from kiln_harness_tuner.analysis import success_by_rule

        v = success_by_rule(make_item(), _bundle(), 0.95)
        assert not v.ok and "no measured rows" in v.reasons[0]

    def test_primary_hole_is_not_ok(self):
        from kiln_harness_tuner.analysis import success_by_rule

        v = success_by_rule(make_item(), _bundle(_row("eval_2", delta=0.3)), 0.95)
        assert not v.ok and "primary target eval_1 has no measured row" in v.reasons[0]

    def test_primary_flat_or_worse_is_not_ok(self):
        from kiln_harness_tuner.analysis import success_by_rule

        v = success_by_rule(make_item(), _bundle(_row(delta=0.0)), 0.95)
        assert not v.ok and "did not improve" in v.reasons[0]

    def test_guard_regressed_beyond_doubt_vetoes(self):
        from kiln_harness_tuner.analysis import success_by_rule

        v = success_by_rule(
            make_item(),
            _bundle(_row(delta=0.2), _row("eval_2", delta=-0.2, ci=(-0.35, -0.05))),
            0.95,
        )
        assert not v.ok and "regressed beyond doubt" in v.reasons[0]

    def test_coverage_below_floor_vetoes(self):
        from kiln_harness_tuner.analysis import success_by_rule

        v = success_by_rule(make_item(), _bundle(_row(delta=0.2, coverage=0.8)), 0.95)
        assert not v.ok and "coverage 80%" in v.reasons[0]

    def test_scores_subset_on_the_primary_target(self):
        from kiln_harness_tuner.analysis import success_by_rule

        item = make_item(targets=[EvalTarget(eval_id="eval_1", scores=["Tone"])])
        v = success_by_rule(
            item,
            _bundle(_row(key="accuracy", delta=0.5), _row(key="tone", delta=0.0)),
            0.95,
        )
        assert not v.ok  # the targeted score is flat; the other one does not count


async def test_parallel_hard_error_cancels_the_siblings():
    """A hard error on one target cancels the others instead of
    letting them finish — the run exits on it either way."""
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval(eval_id="eval_1", config_id=None)
    kiln.evals["eval_2"] = make_eval(eval_id="eval_2", config_id="ec_2")
    kiln.results[("eval_2", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_2", "rc_cand", "train")] = [make_run("i1", accuracy=1.0)]
    # Hold eval_2's job open so eval_1's hard error arrives first.
    gate = asyncio.Event()
    cancelled: list[str] = []
    original_wait = kiln.call

    async def slow_call(method, url_path, query_params=None, body=None, **kw):
        if url_path == "/api/jobs/wait":
            try:
                await gate.wait()
            except asyncio.CancelledError:
                cancelled.append(url_path)
                raise
        return await original_wait(method, url_path, query_params, body, **kw)

    kiln.call = slow_call  # type: ignore[method-assign]
    analyzer, _ = make_analyzer(kiln)
    item = make_item(
        targets=[EvalTarget(eval_id="eval_1"), EvalTarget(eval_id="eval_2")]
    )

    with pytest.raises(MissingDefaultEvalConfigError):
        await asyncio.wait_for(
            analyzer.run_experiment_analysis(item, "rc_cand", BASELINE, []), 5
        )
    # The sibling never finished: its long-poll was CANCELLED (the gate never
    # opened, so finishing was impossible — the cancel is the evidence).
    assert cancelled == ["/api/jobs/wait"]


async def test_partially_stored_parent_is_topped_up_not_compared_on_the_intersection():
    """A parent stored at 23/25 must not compare on the intersection: the
    code rule's coverage clause would veto the win with nothing able to
    repair it. The item-grained cache runs only the missing items."""
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("i1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    kiln.pending[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, ["rc_base"]
    )

    [row] = bundle.per_eval
    assert row.compared_to == "parent" and row.n_items == 2
    assert row.coverage == 1.0
    assert kiln.job_invocations == [
        ("eval_1", "rc_cand", "train"),
        ("eval_1", "rc_base", "train"),  # the top-up
    ]


# ---- one measurement per key, replicates fold


async def test_concurrent_callers_share_one_measurement():
    # Concurrent arms that each find zero stored baseline rows must not each
    # drive the baseline. The first caller drives, the rest wait on the same
    # lock and read its result.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_base", "train")
    kiln.pending[key] = [make_run("i1", accuracy=1.0), make_run("i2", accuracy=0.0)]
    analyzer, _ = make_analyzer(kiln)

    results = await asyncio.gather(
        *(
            analyzer.ensure_measured("eval_1", "ec_1", "rc_base", "train")
            for _ in range(4)
        )
    )

    assert kiln.job_invocations.count(key) == 1
    assert all(len(runs) == 2 and health.clean for runs, health in results)
    shared = [f for k, f in analyzer.log.events if k == "measurement_shared"]
    assert len(shared) == 3 and shared[0]["rc_id"] == "rc_base"
    # A later caller reads the remembered clean result — no empty job spun.
    await analyzer.ensure_measured("eval_1", "ec_1", "rc_base", "train")
    assert kiln.job_invocations.count(key) == 1


async def test_a_partial_measurement_is_topped_up_by_the_next_caller():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_base", "train")
    kiln.partial_results[key] = [make_run("i1", accuracy=1.0)]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("i2", "provider exploded")]
    analyzer, _ = make_analyzer(kiln)

    _, health = await analyzer.ensure_measured("eval_1", "ec_1", "rc_base", "train")
    assert not health.clean
    before = kiln.job_invocations.count(key)
    kiln.job_behaviors[key] = []  # the next job comes back clean
    kiln.pending[key] = [make_run("i1", accuracy=1.0), make_run("i2", accuracy=0.0)]
    runs, health = await analyzer.ensure_measured("eval_1", "ec_1", "rc_base", "train")
    assert kiln.job_invocations.count(key) == before + 1  # a partial is not remembered
    assert health.clean and len(runs) == 2


async def test_replicate_baseline_rows_fold_per_item_and_coverage_counts_items():
    # The baseline holds three rows for i1 (1,0,1 → mean 0.667 → pass by
    # majority) and two for i2 (0,0 → fail); the candidate one row each.
    # Before: the last row per item won (a draw), and the 5 stored rows made
    # n_expected 5 against 2 paired items — "coverage 40%", which flipped a
    # real win to `successful: false` on the live run.
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.pending[("eval_1", "rc_base", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i1", accuracy=0.0),
        make_run("i2", accuracy=0.0),
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=0.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=1.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    bundle = await analyzer.run_experiment_analysis(
        make_item(), "rc_cand", BASELINE, []
    )

    [row] = bundle.per_eval
    assert row.n_items == 2
    assert row.n_expected == 2 and row.coverage == pytest.approx(1.0)
    assert row.baseline_score == pytest.approx(0.5)  # i1 pass (2 of 3), i2 fail
    assert row.candidate_score == pytest.approx(1.0)
    assert row.fail_to_pass == 1 and row.pass_to_fail == 0


def test_reproduction_verdict_folds_replicates_per_item():
    runs = [
        {"dataset_id": "a", "scores": {"accuracy": 1.0}},
        {"dataset_id": "a", "scores": {"accuracy": 0.0}},
        {"dataset_id": "a", "scores": {"accuracy": 1.0}},
        {"dataset_id": "b", "scores": {"accuracy": 1.0}},
    ]
    verdict = reproduction_verdict(runs, [spec("accuracy")], n_expected=2)
    assert verdict.reproduced is True  # a's replicate mean is 0.667
    assert "n=2/2" in verdict.summary


# ---- the residual trial


async def test_trial_drives_only_the_target_items_and_lands_when_all_pass():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    key = ("eval_1", "rc_cand", "train")
    kiln.pending[key] = [
        make_run("i1", accuracy=1.0, cost=0.01),
        make_run("i2", accuracy=1.0, cost=0.01),
        make_run("i3", accuracy=0.0, cost=0.01),
    ]
    analyzer, _ = make_analyzer(kiln)

    trial = await analyzer.trial(EvalTarget(eval_id="eval_1"), "rc_cand", ["i1", "i2"])

    assert trial.landed is True and trial.summary == "2/2 targets fixed"
    assert trial.scores == {"i1": 1.0, "i2": 1.0}
    # The job named its items; i3 stays pending for the later full run.
    (_, _, _, body) = [c for c in kiln.calls if c[1] == "/api/jobs/evals/run"][0]
    assert body["item_ids"] == ["i1", "i2"] and body["split"] == "train"
    assert [r["dataset_id"] for r in kiln.pending[key]] == ["i3"]
    assert [f for k, f in analyzer.log.events if k == "trial"][0]["landed"] is True


async def test_trial_not_landed_when_a_target_still_fails_or_stores_no_row():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=0.0),
    ]
    analyzer, _ = make_analyzer(kiln)

    trial = await analyzer.trial(
        EvalTarget(eval_id="eval_1"), "rc_cand", ["i1", "i2", "i9"]
    )

    assert trial.landed is False
    assert trial.scores == {"i1": 1.0, "i2": 0.0, "i9": None}
    assert trial.fixed == ["i1"] and trial.summary == "1/3 targets fixed"


async def test_trial_with_no_stored_row_at_all_is_infra_not_a_verdict():
    kiln = FakeKiln()
    kiln.evals["eval_1"] = make_eval()
    analyzer, _ = make_analyzer(kiln)
    with pytest.raises(MeasurementFailed):
        await analyzer.trial(EvalTarget(eval_id="eval_1"), "rc_cand", ["i1"])
