"""The experiment details panel and the experiment picker, as views
(functional spec 7.9, ui_design 4.13)."""

from model_events import (
    HOST,
    INFO,
    START_NAME,
    START_RC,
    Run,
    comparison,
    detect_row,
    eval_only,
    fix,
    measure,
    merge,
    probe,
    remeasure,
    row,
    table,
    val_row,
)

from kiln_harness_tuner.schemas import EvalTarget, ProbeCheck, ProbeResult
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.state import DetailLine, DetailsView, Span, plain

GRAPH = f"{HOST}/optimize/{INFO.project_id}/{INFO.task_id}/experiment_lineage"


def wave_one(*items) -> Run:
    run = Run().standard_start()
    run.at(10).plan(1).at(12).planned(1)
    return run.wave_start(1, *items)


def panel(run: Run, item_id: str, index: int | None = None) -> DetailsView:
    """The panel of `item_id` in the last `wave_start` that has it, or in
    `model.waves[index]`."""
    model = run.model
    if index is None:
        index = max(
            n
            for n, wave in enumerate(model.waves)
            if any(item.item_id == item_id for item in wave.items)
        )
    view = model.details((index, item_id), run.t)
    assert view is not None
    return view


def fields(view: DetailsView) -> dict[str, str]:
    return {f.label: " / ".join(plain(line) for line in f.lines) for f in view.fields}


def sections(view: DetailsView) -> dict[str, list[str]]:
    return {s.title: [plain(line.line) for line in s.lines] for s in view.sections}


# ---- what the item is ----


def test_a_queued_fix_shows_what_it_is_and_no_links_yet():
    item = fix("w1-1", "e1", technique="tool_config", hypothesis="Name the fields")
    view = panel(wave_one(item), "w1-1")
    assert (view.title, view.qualifier) == ("w1-1", "target: Hold placement")
    assert view.status == (Span("· Queued: waiting for an agent slot", "muted"),)
    assert fields(view) == {
        "Kind": "Fix",
        "Wave": "1",
        "Parent": f"Start · {START_NAME}",
        "Targets": "Eval: Hold placement",
        "Lever": "tool config",
    }
    # The planner's text in full, the predicted effect from the wave start.
    assert sections(view) == {
        "Hypothesis": ["Name the fields"],
        "Predicted effect": ["+5 points"],
    }
    # No run config is built yet: no link has a target.
    assert view.links == ()


def test_a_wildcard_fix_and_one_that_builds_its_eval_say_so():
    item = fix("w1-1", needs_eval=True)
    item.work.wildcard = True
    view = panel(wave_one(item), "w1-1")
    assert fields(view)["Kind"] == "Fix · wildcard"
    assert fields(view)["Targets"] == "New eval"


def test_targets_are_named_as_their_results_cards():
    item = fix("w1-1", "e1")
    item.evaluation_target = [
        EvalTarget(eval_id="e1", scores=["tone"]),
        EvalTarget(eval_id="e2", scores=["pass"]),
        EvalTarget(eval_id="e3"),
        EvalTarget(eval_id="e9"),
    ]
    run = Run().start()
    run.detectability(
        detect_row("e1", "Hold placement"),
        detect_row("e1", "Hold placement", score_key="tone"),
        detect_row("e2", "Refusal honesty"),
        detect_row("e3", "Tone", score_key="rating", score_type="five_star"),
        detect_row("e3", "Tone", score_key="pass"),
    )
    run.plan(1).planned(1).wave_start(1, item)
    targets = next(f for f in panel(run, "w1-1").fields if f.label == "Targets")
    assert targets.lines == (
        # The score key only when the eval has two or more cards.
        (Span("Eval: Hold placement"), Span(" tone", "muted")),
        (Span("Eval: Refusal honesty"),),
        # No scores named: every card of the eval, the rating's scale too.
        (Span("Eval: Tone"), Span(" rating 1–5", "muted")),
        (Span("Eval: Tone"), Span(" pass", "muted")),
        # An eval with no card yet, by its id.
        (Span("Eval: e9"),),
    )


def test_the_spend_is_the_item_s_whole_and_the_cap_its_measurement_s():
    item = fix("w1-1")
    item.max_usd, item.budget_reason = 6.0, "a long queue"
    run = wave_one(item)
    # Before measurement: the planner's cap.
    assert fields(panel(run, "w1-1"))["Spend"] == (
        "$0.00 so far · measurement cap $6.00"
    )
    run.at(13).state("w1-1", "building_rc")
    run.emit("spend", item_id="w1-1", source="create_experiment_rc", cost_usd=1.1)
    run.at(15).state("w1-1", "measuring")
    run.emit("candidate_budget", item_id="w1-1", rc_id="rc_a", usd=5.0)
    assert fields(panel(run, "w1-1"))["Spend"] == (
        "$1.10 so far · measurement cap $5.00"
    )
    # The cap the loop measures with, and what the measurement spent of it.
    run.chunk("w1-1", "rc_a", [(False, True)])
    run.emit("spend", item_id="w1-1", source="measurement", cost_usd=0.5)
    assert fields(panel(run, "w1-1"))["Spend"] == (
        "$1.60 so far · measurement cap $4.00 ($0.50 used)"
    )
    assert fields(panel(run, "w1-1"))["Time"] == "2m00s"
    run.at(20).state("w1-1", "analyzing")
    run.arm_results("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.outcome("w1-1", rc_id="rc_a", successful=True)
    run.at(40)
    view = panel(run, "w1-1")
    # Landed: no "so far", and the time stops at the verdict.
    assert fields(view)["Spend"] == "$1.60 · measurement cap $4.00 ($0.50 used)"
    assert fields(view)["Time"] == "7m00s"


def test_an_item_with_no_spend_and_no_cap_has_no_spend_field():
    view = panel(wave_one(fix("w1-1")), "w1-1")
    assert "Spend" not in fields(view) and "Time" not in fields(view)


# ---- the status line ----


def test_the_status_line_follows_the_item_live():
    run = wave_one(fix("w1-1"))
    run.at(13).state("w1-1", "building_rc")
    run.at(14)
    status = panel(run, "w1-1").status
    # A spinner, then the state and what it does now.
    assert status[0] == Span("", "spinner")
    assert plain(status) == " Building · an agent is writing the change · 1m00s"
    run.state("w1-1", "measuring")
    run.chunk("w1-1", "rc_a", [(False, True), (True, False)])
    run.at(16)
    assert plain(panel(run, "w1-1").status) == (
        " Measuring · 1 fixed · 1 broke so far · 2m00s"
    )
    run.state("w1-1", "analyzing")
    assert plain(panel(run, "w1-1").status) == " Analyzing · 1 fixed · 1 broke · 0s"


def test_a_landed_item_shows_its_verdict_in_full_words():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "measuring")
    run.outcome("w1-1", "capped", rc_id="rc_a")
    # The card's short form is "Stopped: spending cap, inconclusive".
    assert panel(run, "w1-1").status == (
        Span("✗ Stopped: reached its spending cap (inconclusive)", "error"),
    )


def test_at_the_gate_the_status_adds_the_val_progress():
    run = wave_one(fix("w1-1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a", end=False)
    assert plain(panel(run, "w1-1").status) == (
        "● Train success · checking validation set · 0 of 100 val items"
    )


def test_a_cut_off_item_stops_its_clock():
    run = wave_one(fix("w1-1"))
    run.at(13).state("w1-1", "building_rc")
    run.at(17).emit("fatal_cancel", item_ids=["w1-1"], error="boom")
    run.at(30)
    view = panel(run, "w1-1")
    assert view.status == (Span("✗ Cut off: tuning ended", "error"),)
    assert fields(view)["Time"] == "4m00s"


# ---- what it did and how it went ----


def test_the_build_s_change_lists_what_changed_and_what_stayed():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "building_rc")
    change = {
        "summary": "adds a hold wrapper",
        "changes": ["tool X replaced by Y", "prompt v3 → v4"],
        "unchanged": ["model", "temperature"],
    }
    run.emit("item_change", item_id="w1-1", change=change)
    lines = next(
        s for s in panel(run, "w1-1").sections if s.title == words.CHANGE_SECTION
    )
    # The list of changes as list lines.
    assert lines.lines == (
        DetailLine((Span("adds a hold wrapper"),)),
        DetailLine((Span("tool X replaced by Y"),), bullet=True),
        DetailLine((Span("prompt v3 → v4"),), bullet=True),
        DetailLine((Span("Unchanged: ", "muted"), Span("model, temperature", "muted"))),
    )
    # A change the build agent wrote wrong leaves the last good one.
    run.emit("item_change", item_id="w1-1", change={"summary": "no list"})
    assert (
        sections(panel(run, "w1-1"))[words.CHANGE_SECTION][0] == "adds a hold wrapper"
    )


def test_train_results_are_estimated_changes_and_say_when_they_could_be_noise():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "measuring")
    run.chunk("w1-1", "rc_a", [(False, True), (True, True), (True, False)])
    run.arm_results(
        "w1-1",
        "rc_a",
        row("e1", 0.6, 0.7),
        row("e2", 0.8, 0.79, ci=(-0.05, 0.03)),
        cost=comparison(0.04, 0.03, metric="cost_usd", clear=True),
    )
    run.outcome("w1-1", rc_id="rc_a", successful=True, findings="It worked.")
    view = panel(run, "w1-1")
    train = next(s for s in view.sections if s.title == words.TRAIN_SECTION)
    assert [plain(line.line) for line in train.lines] == [
        "3 of 36 judged · 1 fixed · 1 broke",
        "Eval: Hold placement   +10.0 pp",
        "Eval: Refusal honesty  −1.0 pp · could be noise",
        "Cost / request         −25%",
    ]
    tones = [line.line[1].tone for line in train.lines[1:]]
    assert tones == ["success", "muted", "success"]
    assert sections(view)[words.FINDINGS_SECTION] == ["It worked."]
    # Not before the gate: no val result.
    assert words.VAL_SECTION not in sections(view)


def test_val_results_stay_with_the_item_after_later_gates():
    run = wave_one(fix("w1-1"), fix("w1-2", "e2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.success("w1-2", "rc_b", row("e2", 0.8, 0.85))
    run.at(30).gate(
        1,
        table("rc_a", val_row("e1", 0.6, 0.7), val_row("e2", 0.8, 0.8)),
        table("rc_b", val_row("e1", 0.6, 0.6), val_row("e2", 0.8, 0.82)),
        winner="rc_a",
    )
    run.at(40).plan(2).planned(2).wave_start(2, fix("w2-1"), parent="rc_a")
    run.success("w2-1", "rc_c", row("e1", 0.7, 0.75, parent="rc_a"), parent="rc_a")
    run.gate(2, table("rc_c", val_row("e1", 0.7, 0.72, parent="rc_a")), winner=None)
    view = panel(run, "w1-2")
    assert plain(view.status) == "○ Not selected · w1-1 promoted"
    val = next(s for s in view.sections if s.title == words.VAL_SECTION)
    assert [plain(line.line) for line in val.lines] == [
        "Eval: Hold placement   60.0% → 60.0%  +0.0 pp",
        "Eval: Refusal honesty  80.0% → 82.0%  +2.0 pp",
    ]
    assert [line.line[-1].tone for line in val.lines] == ["muted", "success"]
    assert plain(panel(run, "w1-1").status) == "◆ Promoted · now Best Confirmed"


def test_the_links_select_the_item_s_config_and_compare_it_with_its_parent():
    run = wave_one(fix("w1-1"))
    run.state("w1-1", "building_rc").state("w1-1", "measuring")
    run.emit("candidate_budget", item_id="w1-1", rc_id="rc_a", usd=4.0)
    graph, compare = panel(run, "w1-1").links
    assert (graph.text, graph.url) == (
        "Show in Experiment Graph",
        f"{GRAPH}?selected=rc_a",
    )
    assert compare.text == "Compare to parent in Kiln"
    assert f"models={START_RC},rc_a&columns=2" in compare.url


def test_the_parent_is_the_one_the_train_results_compare_to():
    item = fix("w2-1")
    item.work.parent_rc_id = "rc_fork"
    run = Run().standard_start()
    run.plan(2).planned(2).wave_start(2, item, parent="rc_base")
    assert fields(panel(run, "w2-1"))["Parent"] == "rc_fork"
    run.success(
        "w2-1", "rc_c", row("e1", 0.6, 0.7, parent="rc_other"), parent="rc_other"
    )
    view = panel(run, "w2-1")
    assert fields(view)["Parent"] == "rc_other"
    assert "models=rc_other,rc_c" in view.links[1].url


# ---- the other kinds ----


def test_a_merge_names_its_ingredients_by_item_id():
    run = wave_one(fix("w1-1"), fix("w1-2", "e2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.success("w1-2", "rc_b", row("e2", 0.8, 0.85))
    run.at(40).plan(2).planned(2)
    run.wave_start(2, merge("w2-1", "rc_a", "rc_b", "rc_old"))
    view = panel(run, "w2-1")
    assert (view.title, view.qualifier) == ("w2-1", "merge")
    # Built in this run: the item id; from an earlier run: the config's id.
    assert fields(view)["Kind"] == "Merge of w1-1 + w1-2 + rc_old"
    assert fields(view)["Targets"] == "Eval: Hold placement / Eval: Refusal honesty"
    assert "Lever" not in fields(view)
    assert sections(view)["Plan"] == ["Combine w1-1, w1-2 and rc_old on top of start"]


def test_a_probe_shows_its_question_what_it_unblocks_and_its_evidence():
    run = wave_one(probe("w1-1", "Does it read the skill?", for_idea="a skill arm"))
    run.state("w1-1", "probing")
    view = panel(run, "w1-1")
    assert (view.title, view.qualifier) == ("w1-1", "probe")
    assert fields(view)["Kind"] == "Probe"
    assert fields(view)["Run config"].startswith("Start · ")
    assert sections(view) == {
        "Question": ["Does it read the skill?"],
        "What it unblocks": ["a skill arm"],
    }
    answer = ProbeResult(answer="yes", evidence="3 of 3 read it.", drives=3, notes="n")
    run.emit("item_probe", item_id="w1-1", probe=answer.model_dump())
    run.outcome("w1-1")
    view = panel(run, "w1-1")
    assert view.status == (Span("● YES · 3 drives", "success"),)
    assert sections(view)["Evidence"] == ["3 of 3 read it.", "n"]
    assert view.links == ()


def test_a_code_probe_says_code_answers_it():
    item = probe("w1-1")
    item.check = ProbeCheck(count="tool_calls")
    assert fields(panel(wave_one(item), "w1-1"))["Kind"] == "Probe · answered by code"


def test_eval_builds_measures_and_re_measures_show_their_own_fields():
    run = wave_one(eval_only("w1-1"), measure("w1-2"), remeasure("w1-3", "rc_x"))
    build = panel(run, "w1-1")
    assert fields(build) == {
        "Kind": "New eval",
        "Wave": "1",
        "Issue": "424242500009",
    }
    # The planner's reason, in its words.
    assert sections(build) == {
        "Plan": ["Build an eval for 424242500009"],
        "Reason": ["no eval"],
    }
    assert build.links == ()
    issue = panel(run, "w1-2")
    assert fields(issue)["Kind"] == "Measure"
    assert fields(issue)["Issue"] == "424242500010"
    assert fields(issue)["Targets"] == "Eval: Refusal honesty"
    assert fields(issue)["Run config"].startswith("Start · ")
    assert sections(issue)["Reason"] == ["settle it"]
    again = panel(run, "w1-3")
    assert fields(again)["Kind"] == "Re-measure"
    assert fields(again)["Run config"] == "rc_x"
    assert fields(again)["Targets"] == "Eval: Hold placement"
    assert sections(again)["Reason"] == ["holes"]
    # The run config it completes is in the graph, with no compare.
    assert [link.url for link in again.links] == [f"{GRAPH}?selected=rc_x"]


def test_recon_is_no_wave_and_a_probe_phase_says_so():
    run = Run().standard_start()
    run.wave_start(0, probe("r0-1"), probe_phase=True)
    run.plan(1).planned(1).wave_start(1, probe("w1-1"), probe_phase=True)
    assert fields(panel(run, "r0-1"))["Wave"] == "Recon"
    assert fields(panel(run, "w1-1"))["Wave"] == "1 · probe phase"


# ---- the keys and the picker ----


def probe_phase_and_replan() -> Run:
    """Wave 1's probe phase, then its re-plan, which uses the same id."""
    run = Run().standard_start()
    run.wave_start(0, probe("r0-1"), probe_phase=True)
    run.outcome("r0-1")
    run.plan(1).planned(1)
    run.wave_start(1, probe("w1-1", "Does it read the skill?"), probe_phase=True)
    run.state("w1-1", "probing")
    run.emit(
        "item_probe",
        item_id="w1-1",
        probe=ProbeResult(answer="no", evidence="none did", drives=2).model_dump(),
    )
    run.outcome("w1-1")
    run.plan(1, replan=True).planned(1)
    run.wave_start(1, fix("w1-1", hypothesis="Name the fields"), fix("w1-2"))
    run.state("w1-1", "building_rc")
    return run


def test_an_id_the_re_plan_uses_again_never_mixes_two_items():
    run = probe_phase_and_replan()
    model = run.model
    assert model.item_at((1, "w1-1")).kind == "probe"
    assert model.item_at((2, "w1-1")).kind == "fix"
    assert model.item_at((5, "w1-1")) is None
    assert model.item_at((2, "w9-9")) is None
    old, new = panel(run, "w1-1", 1), panel(run, "w1-1", 2)
    assert plain(old.status) == "○ NO · 2 drives"
    assert sections(old)["Question"] == ["Does it read the skill?"]
    assert plain(new.status).startswith(" Building")
    assert sections(new)["Hypothesis"] == ["Name the fields"]
    # The cards say which item they are.
    assert [card.key for card in model.experiments(run.t).cards] == [
        (2, "w1-1"),
        (2, "w1-2"),
    ]


def test_the_picker_lists_every_item_newest_wave_first_and_recon_last():
    groups = probe_phase_and_replan().model.picker()
    assert [group.header for group in groups] == [
        "WAVE 1",
        "WAVE 1 · probes",
        "RECON",
    ]
    rows = [[(r.key, r.title, r.qualifier) for r in g.rows] for g in groups]
    assert rows == [
        [
            ((2, "w1-1"), "w1-1", "target: Hold placement"),
            ((2, "w1-2"), "w1-2", "target: Hold placement"),
        ],
        [((1, "w1-1"), "w1-1", "probe")],
        [((0, "r0-1"), "r0-1", "probe")],
    ]
    statuses = [[r.status for r in g.rows] for g in groups]
    assert statuses[0] == [
        (Span("", "spinner"), Span(" building", "primary")),
        (Span("· queued", "muted"),),
    ]
    assert statuses[1] == [(Span("○ NO · 2 drives", "muted"),)]


def test_the_picker_is_empty_before_the_first_items():
    assert Run().standard_start().model.picker() == ()


def test_columns_line_up_in_cells_with_wide_names():
    # Eval names come from the person's project: a wide character takes two
    # cells.
    run = Run().start()
    run.detectability(detect_row("e1", "正确"), detect_row("e2", "Refusal"))
    run.plan(1).planned(1).wave_start(1, fix("w1-1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7), row("e2", 0.8, 0.8))
    train = next(
        s for s in panel(run, "w1-1").sections if s.title == words.TRAIN_SECTION
    )
    names = [line.line[0].text for line in train.lines]
    assert names == ["Eval: 正确     ", "Eval: Refusal  "]
