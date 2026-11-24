"""predictions.py: the plan's named items graded on an arm's train rows,
the fix check on the same rows, and where the grade goes — the experiment
record, the analysis input, the next plan's input and the report. A grade
is caution: no test here may show it change `successful` or a stop rule.
"""

from conftest import fix_item, make_ctx, make_result, make_tracker
from fake_dispatcher import ok
from fake_kiln import make_eval, make_run
from test_status import WAVE1_ITEMS, start_wave

from kiln_harness_tuner.estimate import FAIL, NAMED, PASS, Judged
from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.memory import _fit_experiment_content
from kiln_harness_tuner.memory_schema import (
    CONTENT_MAX_CHARS,
    EXPERIMENT_HEAD_KEYS,
    FixOpening,
    kv_line,
)
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.predictions import (
    PREDICTION_LINE_KEYS,
    fix_check_line,
    grade_predictions,
)
from kiln_harness_tuner.report import (
    PREDICTIONS_APPENDIX_HEADING,
    render_fallback_report,
    render_predictions_appendix,
)
from kiln_harness_tuner.schemas import (
    BaselineRef,
    BatchPlanInput,
    FixWork,
    ItemOutcome,
    PredictionGrade,
    RcBuildResult,
)

BASELINE = BaselineRef(rc_id="rc_base")


def row(item_id, parent, cand, *, eval_id="eval_1", block="remainder", fragility=0.0):
    stratum = NAMED if block == "named" else PASS if parent else FAIL
    return Judged(eval_id, item_id, stratum, block, parent, cand, fragility)


def passes(n, broke=0, prefix="p"):
    """`n` items the parent passes, the first `broke` of them now failing."""
    return [row(f"{prefix}{i}", True, i >= broke) for i in range(n)]


def grade(judged, **kw) -> PredictionGrade:
    kw.setdefault("complete", True)
    out = grade_predictions(judged, item_id="w1-1", rc_id="rc_new", **kw)
    assert out is not None
    return out


# ---- the grade of each named item ----


def test_each_named_item_gets_its_result():
    judged = [
        row("f1", False, True, block="named"),
        row("f2", False, False, block="named"),
        row("f3", True, True, block="named"),  # the parent already passes it
        row("r1", True, True),
        row("r2", True, False),
        row("r3", False, False),  # the parent already fails it
        row("r4", True, None),  # no candidate row
    ]
    g = grade(
        judged,
        target_items=["f1", "f2", "f3", "f_other_split"],
        at_risk_items=["r1", "r2", "r3", "r4"],
    )
    results = {(i.role, i.item_id): (i.result, i.eval_id) for i in g.items}
    assert results == {
        ("fix", "f1"): ("fixed", "eval_1"),
        ("fix", "f2"): ("still_failing", "eval_1"),
        ("fix", "f3"): ("wrong_side", "eval_1"),
        # Not in the train queue (another split, or another eval): never judged.
        ("fix", "f_other_split"): ("not_measured", None),
        ("at_risk", "r1"): ("held", "eval_1"),
        ("at_risk", "r2"): ("broke", "eval_1"),
        ("at_risk", "r3"): ("wrong_side", "eval_1"),
        ("at_risk", "r4"): ("not_measured", "eval_1"),
    }
    assert g.targets_pass is False
    assert (g.failing_judged, g.fixed) == (3, 1)
    # r4 has no candidate row: neither a break nor a judged pass.
    assert (g.passing_judged, g.broke) == (3, 1)
    assert g.other_breaks == {}  # the one break was named at risk


def test_an_item_judged_on_two_evals_is_graded_on_each():
    judged = [
        row("f1", False, True, eval_id="eval_1"),
        row("f1", False, False, eval_id="eval_2"),
    ]
    g = grade(judged, target_items=["f1", "f1"])
    assert [(i.eval_id, i.result) for i in g.items] == [
        ("eval_1", "fixed"),
        ("eval_2", "still_failing"),
    ]
    assert g.named("fix", "still_failing") == {"eval_2": ["f1"]}


def test_no_candidate_row_means_no_grade():
    judged = [row("f1", False, None), row("p1", True, None)]
    assert (
        grade_predictions(
            judged, item_id="w1-1", rc_id="rc_new", target_items=["f1"], complete=False
        )
        is None
    )


# ---- the fix check ----


def test_breaks_within_noise_do_not_flag():
    # 2 of 40 at the default noise rate of 0.10: fewer than noise gives.
    g = grade([row("f1", False, True), *passes(40, broke=2)], target_items=["f1"])
    assert g.targets_pass is True
    assert g.breaks_clear is False and g.flagged is False
    assert g.noise_rate == 0.10 and g.alpha == 0.05
    assert g.other_breaks == {"eval_1": ["p0", "p1"]}
    assert g.lines == [
        "predicted fixes: 1 of 1 target item now pass",
        "breaks not predicted: 2 passing items broke that the plan did not name"
        " at risk (eval_1: p0, p1)",
        "fix check: ok — 1 of 1 target item pass; 2 of 40 passing items broke,"
        " within noise (p=0.92, noise rate 0.10)",
    ]


def test_breaks_clearly_above_noise_flag_an_arm_whose_targets_pass():
    # The failure the check is for: the targets are fixed, and 10 of 40
    # passing items now fail (p0 0.10: p is about 0.005).
    g = grade([row("f1", False, True), *passes(40, broke=10)], target_items=["f1"])
    assert g.targets_pass is True
    assert g.breaks_clear is True and g.flagged is True
    assert g.break_p is not None and g.break_p < 0.05
    assert g.lines[-1].startswith(
        "fix check: FLAG — 1 of 1 target item pass; 10 of 40 passing items broke,"
        " clearly above noise (p="
    )


def test_the_break_test_reads_the_runs_noise_rate():
    judged = passes(40, broke=10)
    assert grade(judged).breaks_clear is True
    # A run that measured a flip rate of 0.30 expects 12 breaks in 40.
    noisy = grade(judged, noise_rate=0.30)
    assert noisy.breaks_clear is False and noisy.noise_rate == 0.30


def test_a_fragile_item_raises_the_null_of_the_break_test():
    # Items that other configs often fail break by chance more often.
    fragile = [row(f"p{i}", True, i >= 10, fragility=0.4) for i in range(40)]
    g = grade(fragile)
    assert g.noise_rate == 0.4 and g.breaks_clear is False


def test_a_target_that_still_fails_flags():
    g = grade(
        [row("f1", False, True), row("f2", False, False), *passes(10)],
        target_items=["f1", "f2"],
    )
    assert g.targets_pass is False and g.flagged is True
    assert g.lines == [
        "predicted fixes: 1 of 2 target items now pass (still failing eval_1: f2)",
        "fix check: FLAG — 1 of 2 target items pass; 0 of 10 passing items broke,"
        " within noise (p=1, noise rate 0.10)",
    ]


def test_with_no_named_target_the_check_reports_the_parents_failures():
    g = grade([row("f1", False, True), row("f2", False, False), *passes(10, broke=1)])
    assert g.targets_pass is None and g.flagged is False
    assert g.lines == [
        "breaks not predicted: 1 passing item broke that the plan did not name"
        " at risk (eval_1: p0)",
        "fix check: ok — no target item named, 1 of 2 failing items fixed; 1 of"
        " 10 passing items broke, within noise (p=0.65, noise rate 0.10)",
    ]


def test_the_lines_of_a_stopped_arm_say_so_and_name_the_risks():
    g = grade(
        [row("f1", False, False, block="named"), row("r1", True, False)],
        target_items=["f1", "f9"],
        at_risk_items=["r1", "r2"],
        complete=False,
    )
    assert g.lines == [
        "predicted fixes: 0 of 1 target item now pass (still failing eval_1: f1);"
        " not measured f9",
        "predicted risks: 1 of 1 at-risk item broke (broke eval_1: r1); not"
        " measured r2",
        "fix check: FLAG — 0 of 1 target item pass; 1 of 1 passing item broke,"
        " within noise (p=0.1, noise rate 0.10) (on the items judged before the"
        " arm stopped)",
    ]
    assert fix_check_line(g) == g.lines[-1]


def test_a_line_names_at_most_eight_ids_and_the_grade_keeps_all():
    g = grade(passes(30, broke=12))
    assert sum(len(v) for v in g.other_breaks.values()) == 12
    (line,) = [x for x in g.lines if x.startswith("breaks not predicted")]
    assert line.endswith("(eval_1: p0, p1, p10, p11, p2, p3, p4, p5 (+4 more))")


# ---- the record: the opening names the items, the clamp keeps the grade ----


def test_the_opening_names_the_predicted_items():
    opening = FixOpening(
        hypothesis="h",
        predicted_effect="+10%",
        parent_rc_id="rc_base",
        eval_id="eval_1",
        issue_id="issue_1",
        technique="prompt",
        target_items=("f1", "f2"),
        at_risk_items=("r1",),
    )
    text = opening.render()
    assert "target items: f1, f2\n" in text
    assert "at-risk items: r1" in text
    bare = FixOpening("h", "+10%", "rc_base", "eval_1", "issue_1", "prompt")
    assert "items:" not in bare.render()


def test_every_grade_line_is_a_line_the_record_keeps():
    g = grade(
        [row("f1", False, False), row("r1", True, False), *passes(5, broke=1)],
        target_items=["f1"],
        at_risk_items=["r1"],
    )
    keys = [kv_line(line)[0] for line in g.lines]
    assert keys == list(PREDICTION_LINE_KEYS)
    assert set(PREDICTION_LINE_KEYS) <= EXPERIMENT_HEAD_KEYS
    # An over-cap record drops the analyzer's prose, never the grade.
    prose = "\n".join(f"prose line {i} " + "x" * 80 for i in range(400))
    fitted = _fit_experiment_content(prose + "\n" + "\n".join(g.lines))
    assert len(fitted) <= CONTENT_MAX_CHARS
    for line in g.lines:
        assert line in fitted.splitlines()


# ---- through the item path ----


def _seed(kiln, base, cand):
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run(i, accuracy=float(v)) for i, v in base.items()
    ]
    kiln.pending[("eval_1", "rc_new", "train")] = [
        make_run(i, accuracy=float(v)) for i, v in cand.items()
    ]


def _predicting_fix(targets, at_risk=()):
    item = fix_item()
    item.work.target_items = list(targets)
    item.work.at_risk_items = list(at_risk)
    return item


async def test_a_completed_arm_carries_its_grade_everywhere():
    ctx = make_ctx()
    # t1 fails on the parent and is fixed; g1 passes and breaks; g2 holds.
    _seed(ctx.kiln, {"t1": 0, "g1": 1, "g2": 1}, {"t1": 1, "g1": 0, "g2": 1})
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="b"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))

    item = _predicting_fix(["t1"], at_risk=["g2"])
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    g = outcome.prediction
    assert g is not None and g.complete is True
    assert g.named("fix", "fixed") == {"eval_1": ["t1"]}
    assert g.named("at_risk", "held") == {"eval_1": ["g2"]}
    assert g.other_breaks == {"eval_1": ["g1"]}
    # The analyzer reads the grade; the next planner gets it; the log has it.
    (req,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert req.prediction == g
    assert ctx.graded_predictions == [g]
    (event,) = [f for k, f in ctx.log.events if k == "prediction_grade"]
    assert event["item_id"] == "w1-1" and event["rc_id"] == "rc_new"
    assert event["grade"]["other_breaks"] == {"eval_1": ["g1"]}
    # The lines are on the verdict record, after the analyzer's content.
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    for line in g.lines:
        assert line in content.splitlines()
    assert "predicted fixes: 1 of 1 target item now pass" in content
    assert "predicted risks: 0 of 1 at-risk item broke" in content


async def test_a_plan_that_names_the_wrong_items_is_graded_and_not_refused():
    ctx = make_ctx()
    _seed(ctx.kiln, {"t1": 0, "t2": 0, "g1": 1}, {"t1": 1, "t2": 1, "g1": 1})
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="b"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result(successful=True)))

    # The plan names a target the parent already passes, and one that is
    # not a train item: neither has a result, and nothing is refused.
    item = _predicting_fix(["t1", "g1", "v_not_train"])
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.result is not None and outcome.result.successful is True
    g = outcome.prediction
    assert g is not None
    assert g.count("fix", "wrong_side") == 1
    assert g.named("fix", "not_measured") == {"-": ["v_not_train"]}
    (rule,) = [f for k, f in ctx.log.events if k == "success_rule"]
    assert rule["rule_ok"] is True


async def test_a_flagged_grade_leaves_a_successful_arm_successful(monkeypatch):
    ctx = make_ctx()
    _seed(ctx.kiln, {"t1": 0, "g1": 1}, {"t1": 1, "g1": 1})
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="b"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result(successful=True)))
    flagged = PredictionGrade(
        item_id="w1-1",
        rc_id="rc_new",
        complete=True,
        breaks_clear=True,
        lines=["fix check: FLAG — forced by the test"],
    )
    monkeypatch.setattr(
        "kiln_harness_tuner.items.grade_predictions", lambda *a, **k: flagged
    )

    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert flagged.flagged is True
    assert outcome.status == "completed"
    assert outcome.result is not None and outcome.result.successful is True
    assert outcome.prediction == flagged
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert "fix check: FLAG — forced by the test" in content.splitlines()


async def test_a_stopped_arm_keeps_its_grade_on_the_running_record():
    ctx = make_ctx()
    # The candidate still fails its named item: the `targets` rule stops it.
    _seed(ctx.kiln, {"t1": 0, "g1": 1}, {"t1": 0, "g1": 1})
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="b"))
    )

    item = _predicting_fix(["t1"], at_risk=["g1"])
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "not_landed"
    assert outcome.measurement is not None
    assert outcome.measurement.stop_kind == "targets"
    g = outcome.prediction
    assert g is not None and g.complete is False and g.flagged is True
    assert g.named("fix", "still_failing") == {"eval_1": ["t1"]}
    exp = ctx.kiln.memories[outcome.experiment_memory_id]
    assert exp["overview"].endswith(" — NOT LANDED (0/1 targets fixed)")
    lines = exp["content"].splitlines()
    # The opening is still there, with the plan's named items.
    assert "target items: t1" in lines and "at-risk items: g1" in lines
    assert (
        "predicted fixes: 0 of 1 target item now pass (still failing eval_1: t1)"
        in lines
    )
    assert lines[-1].startswith("fix check: FLAG — 0 of 1 target item pass; ")
    assert lines[-1].endswith("(on the items judged before the arm stopped)")
    assert ctx.graded_predictions == [g]


# ---- the next planner, and the report ----


def test_the_plan_input_and_the_fix_item_default_to_no_prediction():
    assert "graded_predictions" in BatchPlanInput.model_fields
    assert BatchPlanInput.model_fields["graded_predictions"].default == []
    work = FixWork(
        issue_memory_id="i",
        hypothesis="h",
        technique="prompt",
        predicted_effect="e",
        needs_eval=False,
        eval_id="eval_1",
    )
    assert work.target_items == [] and work.at_risk_items == []


async def test_the_next_planner_receives_the_graded_predictions():
    from test_orchestrator import _promotion_scenario, decision

    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision(None, rationale="not yet"))

    await orchestrate(ctx, "", timeout_mins=180)

    first, second = ctx.dispatch.calls_to("batch_plan")
    assert first.graded_predictions == []
    assert [g.item_id for g in second.graded_predictions] == ["w1-1"]
    assert second.graded_predictions[0].lines[-1].startswith("fix check: ")


def _outcome_with(prediction, status="completed"):
    out = ItemOutcome(
        item=fix_item(),
        status=status,
        rc_id="rc_new",
        experiment_memory_id="mem_exp",
        result=None,
        infra_error=None,
    )
    out.prediction = prediction
    return out


def test_the_report_has_one_row_for_each_graded_arm():
    tracker = make_tracker()
    start_wave(tracker, items=WAVE1_ITEMS)
    assert render_predictions_appendix(tracker.snapshot()) == ""
    g = grade(
        [row("f1", False, True), row("r1", True, False), *passes(40, broke=10)],
        target_items=["f1", "f9"],
        at_risk_items=["r1"],
    )
    tracker.event("item_outcome", item_id="w1-1", outcome=_outcome_with(g))
    status = tracker.snapshot()

    text = render_predictions_appendix(status)

    assert text.startswith(PREDICTIONS_APPENDIX_HEADING)
    assert "It is not a verdict." in text
    assert (
        "| 1 | `w1-1` fix | `rc_new` | 1 of 1 fixed, 1 with no result | 1 of 1 broke"
        " | 11 of 41 passing items broke, clearly above noise (p=0.0018, noise rate"
        " 0.10) | 10 | FLAG |"
    ) in text
    # The fallback report carries the same lines under the item.
    fallback = render_fallback_report(status)
    for line in g.lines:
        assert f"  - {line}" in fallback


def test_the_report_marks_an_arm_that_stopped_early():
    tracker = make_tracker()
    start_wave(tracker, items=WAVE1_ITEMS)
    g = grade([row("f1", False, False)], target_items=["f1"], complete=False)
    tracker.event(
        "item_outcome", item_id="w1-1", outcome=_outcome_with(g, "not_landed")
    )
    text = render_predictions_appendix(tracker.snapshot())
    assert "| 0 of 1 fixed | — | no passing item judged | 0 | FLAG (arm stopped" in text
