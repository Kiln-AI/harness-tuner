"""memory_schema.py: the memory store's record schema, in both directions.

`test_memory.py` string-asserts what the writers put on the wire; these tests
assert the TYPED side — that every view reads back what a writer wrote, that
every lifecycle state parses from its prefix/suffix, that legacy and
agent-authored records load leniently, and that a writer handed a record of
the wrong kind refuses before writing."""

from __future__ import annotations

from datetime import datetime

import pytest
from fake_kiln import FakeKiln

from kiln_harness_tuner.memory import MemoryWriter
from kiln_harness_tuner.memory_schema import (
    CONTENT_MAX_CHARS,
    EXPERIMENT_TRANSITIONS,
    SETUP_DELIMITER,
    SETUP_HEAD_LINE_MAX_CHARS,
    SETUP_HEAD_MAX_CHARS,
    TECHNIQUE_MAX_CHARS,
    BaselineView,
    ExperimentState,
    ExperimentView,
    FixOpening,
    IssueNote,
    IssueState,
    IssueView,
    MemoryRecord,
    MergeOpening,
    OpenUnknownContent,
    RecordKind,
    RecordKindError,
    SetupHead,
    baseline_lineage,
    canonical_question,
    experiment_state,
    experiment_suffix,
    has_rejection_note,
    is_promoted,
    is_rejected_at_val,
    issue_state,
    join_owned,
    kv_line,
    kv_lines,
    kv_value,
    priority_from_tags,
    promoted_stamp,
    question_key,
    record_kind,
    rejected_stamp,
    rejection_note,
    render_kv,
    set_state_prefix,
    split_owned,
    state_prefix_run,
    strip_state_suffix,
    tags_with_priority,
    verdict_overview,
)
from kiln_harness_tuner.schemas import ExperimentResult

FIXED_NOW = datetime(2026, 7, 22, 10, 0, 0)


def record(
    overview: str,
    tags: list[str],
    content: str | None = None,
    *,
    memory_id: str = "mem_1",
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id, overview=overview, content=content, tags=tags, scope="task::t1"
    )


def make_writer(kiln: FakeKiln) -> MemoryWriter:
    return MemoryWriter(kiln, project_id="p1", task_id="t1", now=lambda: FIXED_NOW)


# ---- record kinds ----


def test_record_kind_reads_the_tag_with_issue_winning_over_annotations():
    assert record_kind(["issue", "lesson"]) is RecordKind.ISSUE
    assert record_kind(["lesson"]) is RecordKind.LESSON
    assert record_kind(["baseline", "rc_1"]) is RecordKind.BASELINE
    assert record_kind(["val"]) is RecordKind.VAL
    assert record_kind([]) is RecordKind.OTHER


def test_record_kind_treats_an_untagged_open_question_as_a_constraint():
    assert record_kind([], "OPEN: is there a docs page?") is RecordKind.CONSTRAINT
    assert record_kind([], "something else") is RecordKind.OTHER


@pytest.mark.parametrize(
    "view_cls,tags",
    [
        (IssueView, ["experiment"]),
        (ExperimentView, ["issue"]),
        (BaselineView, ["experiment"]),
    ],
)
def test_every_view_refuses_a_record_of_another_kind(view_cls, tags):
    with pytest.raises(RecordKindError):
        view_cls.from_record(record("x", tags))


# ---- the key: value codec ----


def test_kv_line_parses_spaced_keys_and_trims_values():
    assert kv_line("predicted effect:  +10% pass rate  ") == (
        "predicted effect",
        "+10% pass rate",
    )
    assert kv_line("re-ask when: the fix ships") == ("re-ask when", "the fix ships")
    assert kv_line("technique:") == ("technique", "")
    assert kv_line("just prose, no colon") is None
    assert kv_line("") is None


def test_kv_lines_skips_prose_and_keeps_order():
    content = "hypothesis: a\nSome analyst prose.\n\nissue: mem_9\nissue: mem_10"
    assert kv_lines(content) == [
        ("hypothesis", "a"),
        ("issue", "mem_9"),
        ("issue", "mem_10"),
    ]
    assert kv_lines(None) == []


def test_kv_value_is_first_match_exact_key_and_ignores_empty():
    content = "Issue: prose\nissue:\nissue: mem_9\nissue: mem_10"
    assert kv_value(content, "issue") == "mem_9"
    assert kv_value(content, "Issue") == "prose"
    assert kv_value(content, "missing") is None


def test_render_kv_round_trips_through_kv_lines():
    pairs = [("hypothesis", "retry"), ("parent rc", "rc_1"), ("eval", "e1")]
    assert kv_lines(render_kv(pairs)) == pairs


# ---- issue lifecycle: prefixes ----


@pytest.mark.parametrize(
    "overview,state",
    [
        ("issue: x", IssueState.OPEN),
        ("FIXED: issue: x", IssueState.FIXED),
        ("WONT_FIX: issue: x", IssueState.WONT_FIX),
        ("UNMEASURED: issue: x", IssueState.UNMEASURED),
        ("UNMEASURABLE: issue: x", IssueState.UNMEASURABLE),
    ],
)
def test_issue_state_parses_every_prefix_and_renders_it_back(overview, state):
    assert issue_state(overview) is state
    assert overview.startswith(state.prefix)
    assert set_state_prefix("issue: x", state) == overview


def test_issue_state_values_are_the_plain_words_setup_and_the_ui_compare():
    # StrEnum: `issue.state == "fixed"` keeps working for every reader.
    assert IssueState.FIXED == "fixed"
    assert IssueState.OPEN.prefix == ""


def test_state_prefix_run_keeps_a_stacked_legacy_run_verbatim():
    assert state_prefix_run("FIXED: UNMEASURED: issue: x") == "FIXED: UNMEASURED: "
    assert state_prefix_run("issue: x") == ""
    assert set_state_prefix("FIXED: UNMEASURED: issue: x") == "issue: x"


def test_issue_view_reads_state_priority_bindings_head_and_tail():
    head = SetupHead(
        description="Replies drop the block.\nSecond line.",
        eval_id="424242966189",
        eval_name="Handoff Block",
        config_kind="code_eval",
        priority=1,
        priority_source="spec 424242331708",
        state_at_sync="unmeasured",
        fingerprint="a1b2c3d4",
        synced="2026-07-31",
    )
    tail = IssueNote.fixed("07-20", "rc_9") + "\n" + IssueNote.repros("rc_10")
    view = IssueView.from_record(
        record(
            "UNMEASURED: issue: replies drop the block",
            ["issue", "p1", "eval_424242966189"],
            join_owned(head.render(), tail),
        )
    )
    assert view.state is IssueState.UNMEASURED
    assert view.priority == 1
    assert view.bound_eval_ids == frozenset({"424242966189"})
    assert view.is_setup_owned
    assert view.setup_head == head
    assert view.fingerprint == "a1b2c3d4"
    assert view.loop_tail == tail


def test_issue_view_on_a_loop_discovered_record_has_no_head():
    view = IssueView.from_record(
        record("issue: found by the analyst", ["issue"], "- eval: 1 (built 07-22)")
    )
    assert not view.is_setup_owned
    assert view.setup_head is None
    assert view.fingerprint is None
    assert view.priority is None
    assert view.loop_tail == "- eval: 1 (built 07-22)"
    assert view.bound_eval_ids == frozenset()


def test_setup_head_parse_reads_metadata_past_a_trailing_line():
    # A stray line after `status:` (a trailing blank, an off-contract note)
    # must not hide the fingerprint — that read schedules a PAID describe.
    rendered = SetupHead(
        description="d",
        eval_id="1",
        eval_name="n",
        config_kind="k",
        priority=1,
        priority_source="default",
        state_at_sync="open",
        fingerprint="a1b2c3d4",
        synced="2026-07-31",
    ).render()
    for tail in ("\n- agent note", "\n \n", "\n[… truncated …]"):
        parsed = SetupHead.parse(rendered + tail)
        assert parsed.fingerprint == "a1b2c3d4", tail
        assert parsed.eval_id == "1"
    # The LAST line per key wins, so a description quoting `status:` never
    # answers for setup's.
    quoted = "status: garbage · fp: ffffffff\n" + rendered
    assert SetupHead.parse(quoted).fingerprint == "a1b2c3d4"
    assert SetupHead.parse(quoted).description.startswith("status: garbage")


def test_setup_head_parse_is_lenient_on_a_hand_edited_head():
    head = SetupHead.parse("just a description\nstatus: garbage")
    assert head.description == "just a description"
    assert head.eval_id is None and head.priority is None
    assert head.state_at_sync is None and head.fingerprint is None
    assert SetupHead.parse("").description == ""


def test_setup_head_render_parse_round_trip_and_budget():
    head = SetupHead(
        description="d" * 5000,
        eval_id="1",
        eval_name="n" * 500,
        config_kind="code_eval",
        priority=2,
        priority_source="default",
        state_at_sync="open",
        fingerprint="deadbeef",
        synced="2026-07-31",
    )
    rendered = head.render()
    assert len(rendered) <= SETUP_HEAD_MAX_CHARS
    assert all(
        len(line) <= SETUP_HEAD_LINE_MAX_CHARS for line in rendered.splitlines()[1:]
    )
    parsed = SetupHead.parse(rendered)
    # The over-long eval line was clamped, so its id survives and its tail
    # does not; the status line is short and reads back whole.
    assert parsed.eval_id == "1"
    assert parsed.fingerprint == "deadbeef"
    assert parsed.synced == "2026-07-31"
    assert parsed.state_at_sync == "open"
    assert parsed.priority == 2 and parsed.priority_source == "default"
    assert SETUP_HEAD_MAX_CHARS + 2000 <= CONTENT_MAX_CHARS


def test_a_delimiter_with_one_word_before_setup_still_splits():
    """Earlier versions wrote one word between the dash and "setup". Their
    records must still split, so a refresh does not stack a second head."""
    old = SETUP_DELIMITER.replace("— setup", "— old setup")
    head, tail = split_owned(f"the head\n{old}\n- a loop note")
    assert head == "the head"
    assert tail == "- a loop note"
    assert split_owned("a quote of it mid-line: " + old) == (
        None,
        "a quote of it mid-line: " + old,
    )


def test_setup_head_render_drops_a_delimiter_line_inside_the_description():
    head = SetupHead(
        description=f"before\n{SETUP_DELIMITER}\nafter",
        eval_id="1",
        eval_name="n",
        config_kind="k",
        priority=1,
        priority_source="default",
        state_at_sync="open",
        fingerprint="abcd",
        synced="2026-07-31",
    )
    assert SETUP_DELIMITER not in head.render()
    view = IssueView.from_record(
        record("issue: x", ["issue"], join_owned(head.render(), "- fp: ffff"))
    )
    assert view.fingerprint == "abcd"


def test_priority_tags():
    assert priority_from_tags(["issue", "p2"]) == 2
    assert priority_from_tags(["issue"]) is None
    assert tags_with_priority(["issue", "p2", "eval_1"], 0) == ["issue", "p0", "eval_1"]
    assert tags_with_priority(["issue"], 1) == ["issue", "p1"]


# ---- experiment lifecycle: suffixes ----


@pytest.mark.parametrize(
    "overview,state",
    [
        ("EXP 0722-w1-2 (prompt): retry — RUNNING", ExperimentState.RUNNING),
        (
            "EXP 0722-w1-2: retry — +8pp, base=62, cand=70, n=20, p=.04",
            ExperimentState.VERDICT,
        ),
        ("EXP 0722-w1-2: retry — +1pp, n=20, p=.6 — REFUTED", ExperimentState.REFUTED),
        (
            "EXP 0722-w1-2 (prompt): retry — FAILED (infra: rate_limit)",
            ExperimentState.FAILED,
        ),
        (
            "EXP 0722-w1-2 (prompt): retry — ABANDONED (orchestrator restart 07-22)",
            ExperimentState.ABANDONED,
        ),
        (
            "EXP 0722-w1-2 (prompt): retry — DEAD END (inert knob)",
            ExperimentState.DEAD_END,
        ),
    ],
)
def test_experiment_state_parses_every_suffix(overview, state):
    assert experiment_state(overview) is state


def test_experiment_state_is_read_past_the_gate_stamps():
    verdict = "EXP 0722-w1-2: retry — +8pp, n=20, p=.04"
    promoted = verdict + promoted_stamp("07-23")
    rejected = verdict + " — REFUTED" + rejected_stamp("07-23")
    assert experiment_state(promoted) is ExperimentState.VERDICT
    assert is_promoted(promoted) and not is_rejected_at_val(promoted)
    assert experiment_state(rejected) is ExperimentState.REFUTED
    assert is_rejected_at_val(rejected) and not is_promoted(rejected)


def test_every_suffix_renders_to_what_its_state_parses():
    # UNKNOWN is read only: no writer renders it.
    for state in set(ExperimentState) - {ExperimentState.UNKNOWN}:
        overview = "EXP 0722-w1-2: retry" + experiment_suffix(state, "why_not")
        assert experiment_state(overview) is state, state


def test_strip_state_suffix_removes_exactly_the_named_state():
    assert strip_state_suffix("x — RUNNING", ExperimentState.RUNNING) == "x"
    assert (
        strip_state_suffix("x — FAILED (infra: timeout)", ExperimentState.FAILED) == "x"
    )
    assert strip_state_suffix("x — REFUTED", ExperimentState.VERDICT) == "x — REFUTED"
    # Two stacked no-result suffixes (a hand edit): only the state named is
    # stripped, whatever the process hash seed.
    stacked = "x — FAILED (infra: timeout) — RUNNING"
    assert strip_state_suffix(stacked, ExperimentState.RUNNING) == (
        "x — FAILED (infra: timeout)"
    )


def test_experiment_transitions_replace_no_result_states_and_keep_results():
    assert EXPERIMENT_TRANSITIONS[ExperimentState.RUNNING] >= {
        ExperimentState.VERDICT,
        ExperimentState.FAILED,
        ExperimentState.ABANDONED,
        ExperimentState.DEAD_END,
    }
    assert EXPERIMENT_TRANSITIONS[ExperimentState.FAILED] == {ExperimentState.ABANDONED}
    for result in (
        ExperimentState.VERDICT,
        ExperimentState.REFUTED,
        ExperimentState.ABANDONED,
        ExperimentState.DEAD_END,
    ):
        assert EXPERIMENT_TRANSITIONS[result] == frozenset(), result


@pytest.mark.parametrize(
    "overview",
    [
        # The live case: the opening cut by the overview cap mid-technique.
        "EXP 1027-w2-1 (rewrite the prompt and add new versions of every skil…",
        # The opening with its suffix removed by hand.
        "EXP 0722-w1-2 (prompt): retry",
    ],
)
def test_an_opening_with_no_suffix_is_unknown_not_a_verdict(overview):
    """No suffix is a verdict only in the verdict's shape."""
    assert experiment_state(overview) is ExperimentState.UNKNOWN
    # The forward moves of RUNNING, so the arm that owns it can go on.
    assert (
        EXPERIMENT_TRANSITIONS[ExperimentState.UNKNOWN]
        == EXPERIMENT_TRANSITIONS[ExperimentState.RUNNING]
    )
    assert strip_state_suffix(overview, ExperimentState.UNKNOWN) == overview
    # A verdict row, with or without the overview cap's ellipsis, is not.
    assert experiment_state("EXP 0722-w1-2: retry — +8pp, n=20…") is (
        ExperimentState.VERDICT
    )


def test_verdict_overview_drops_only_a_technique_after_the_label():
    assert verdict_overview("EXP 0722-w1-2 (prompt): retry — +8pp") == (
        "EXP 0722-w1-2: retry — +8pp"
    )
    assert verdict_overview("EXP 0722-w1-2: retry (prompt): +8pp") == (
        "EXP 0722-w1-2: retry (prompt): +8pp"
    )


def test_a_fix_opening_bounds_every_planner_value():
    """Each value is one line and bounded on render, so the
    opening always fits whole and parses back."""
    huge = "word " * 2000 + "\nissue: not_this_one"
    opening = FixOpening(
        hypothesis=huge,
        predicted_effect=huge,
        parent_rc_id="rc_base",
        eval_id="eval_1",
        issue_id="issue_1",
        technique=huge,
        head_to_head_group=huge,
    )
    rendered = opening.render()
    assert len(rendered) < CONTENT_MAX_CHARS // 2
    assert len(rendered.splitlines()) == 7
    assert kv_value(rendered, "issue") == "issue_1"
    technique = kv_value(rendered, "technique")
    assert technique is not None and len(technique) <= TECHNIQUE_MAX_CHARS


def test_suffix_regexes_are_derived_from_the_templates():
    # A detail with characters outside `[a-z_]` still parses back: the reader
    # is built from the writer's template, not a second hand-written regex.
    overview = "x" + experiment_suffix(ExperimentState.FAILED, "HTTP-503")
    assert experiment_state(overview) is ExperimentState.FAILED
    long_reason = "r" * 300
    for state in (ExperimentState.ABANDONED, ExperimentState.DEAD_END):
        rendered = experiment_suffix(state, long_reason)
        assert len(rendered) < 300
        assert experiment_state("x" + rendered) is state


def test_experiment_view_reads_a_fix_opening_back():
    opening = FixOpening(
        hypothesis="retry-skill for 429s",
        predicted_effect="+10% pass rate",
        parent_rc_id="rc_base",
        eval_id="eval_1",
        issue_id="issue_1",
        technique="prompt",
        head_to_head_group="tone",
    )
    view = ExperimentView.from_record(
        record(
            "EXP 0722-w1-2 (prompt): retry-skill for 429s — RUNNING",
            ["experiment"],
            opening.render(),
        )
    )
    assert view.state is ExperimentState.RUNNING
    assert view.label == "EXP 0722-w1-2"
    assert view.technique == "prompt"
    assert view.overview_technique == "prompt"
    assert view.lever == "prompt"
    assert view.issue_id == "issue_1"
    assert view.hypothesis == "retry-skill for 429s"
    assert view.predicted_effect == "+10% pass rate"
    assert view.parent_rc_id == "rc_base"
    assert view.eval_id == "eval_1"
    assert view.head_to_head_group == "tone"
    assert view.ingredients == ()
    assert not view.is_merge
    assert not view.rejection_noted


def test_experiment_view_reads_a_merge_opening_back():
    opening = MergeOpening(
        ingredients=(("mem_1", "rc_1"), ("mem_2", "rc_2")),
        conflict_flags=("both edit the prompt",),
        parent_rc_id="rc_base",
    )
    assert opening.label == "mem_1 + mem_2"
    view = ExperimentView.from_record(
        record(
            "EXP 0722-w3-1 (merge): mem_1 + mem_2 — RUNNING",
            ["experiment"],
            opening.render(),
        )
    )
    assert view.is_merge
    assert view.ingredients == (("mem_1", "rc_1"), ("mem_2", "rc_2"))
    assert view.parent_rc_id == "rc_base"
    assert view.issue_id is None and view.technique is None
    assert view.overview_technique == "merge"


def test_experiment_view_lever_falls_back_to_the_overview_only_while_running():
    # An older arm: no `technique:` line. RUNNING → the label serves;
    # after the verdict rewrote the overview there is nothing to read.
    running = ExperimentView.from_record(
        record("EXP 0722-w1-2 (prompt): retry — RUNNING", ["experiment"], "issue: i1")
    )
    assert running.technique is None and running.lever == "prompt"
    verdict = ExperimentView.from_record(
        record("EXP 0722-w1-2: retry — +8pp, n=20, p=.04", ["experiment"], "issue: i1")
    )
    assert verdict.lever is None


def test_experiment_view_on_an_agent_authored_body_is_lenient():
    view = ExperimentView.from_record(
        record(
            "EXP 0722-w1-2: retry — +8pp, n=20, p=.04",
            ["experiment"],
            "Free prose.\nnext: try X",
        )
    )
    assert view.state is ExperimentState.VERDICT
    assert view.issue_id is None and view.head_to_head_group is None
    assert view.content == "Free prose.\nnext: try X"


def test_rejection_note_is_detected_by_its_prefix_whatever_the_date():
    assert has_rejection_note(rejection_note("07-22", "rc_base"))
    assert has_rejection_note("prose\n" + rejection_note("12-31", "rc_x"))
    assert not has_rejection_note("- rejected by the planner")
    assert not has_rejection_note(None)


# ---- open unknowns ----


def test_open_unknown_content_round_trips_and_keys_on_the_question():
    body = OpenUnknownContent(
        question="Is there an API doc?",
        default="assume no",
        gates="the docs lever",
        source="assumption",
        reask_when="the fix ships",
    )
    parsed = OpenUnknownContent.parse(body.render())
    assert parsed == body
    assert parsed.key == question_key("is there an api doc?")


def test_open_unknown_content_parse_is_case_insensitive_and_first_line_wins():
    parsed = OpenUnknownContent.parse("Question: a\nquestion: b\nDefault: d")
    assert parsed is not None
    assert parsed.question == "a" and parsed.default == "d"
    assert parsed.gates == "" and parsed.reask_when == ""


def test_open_unknown_content_parse_needs_a_question_line():
    assert OpenUnknownContent.parse("free prose") is None
    assert OpenUnknownContent.parse(None) is None


def test_canonical_question_is_one_line_bounded_and_idempotent():
    messy = "  is\nthere   an API\tdoc? "
    canonical = canonical_question(messy)
    assert canonical == "is there an API doc?"
    assert canonical_question(canonical) == canonical
    assert len(canonical_question("q" * 5000)) == 2000


# ---- baseline ----


def test_baseline_view_reads_the_pointer_from_tags_and_the_chain_from_content():
    view = BaselineView.from_record(
        record(
            "Baseline: run config rc_3 — promoted 07-22 by EXP 0722-w1-2",
            ["baseline", "rc_3"],
            "rc_3 (promoted 07-22)\nrc_2\n[… earlier history truncated …]\nrc_1",
        )
    )
    assert view.rc_ids == ("3",)  # the tag is `rc_<id>`; the id is what is read
    assert view.lineage == ("rc_3", "rc_2", "rc_1")
    assert baseline_lineage(None) == []


# ---- the writers refuse the wrong kind before writing ----


async def test_issue_writers_refuse_an_experiment_id_without_writing():
    kiln = FakeKiln()
    exp = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — RUNNING", "task::t1", ["experiment"]
    )
    before = dict(kiln.memories[exp])
    writer = make_writer(kiln)
    with pytest.raises(RecordKindError):
        await writer.mark_fixed(exp, "rc_9")
    with pytest.raises(RecordKindError):
        await writer.flag_unmeasurable(exp, "eval gone")
    assert kiln.memories[exp] == before


async def test_experiment_writers_refuse_an_issue_id_without_writing():
    kiln = FakeKiln()
    issue = kiln.add_memory("issue: x", "task::t1", ["issue", "p1"])
    before = dict(kiln.memories[issue])
    writer = make_writer(kiln)
    with pytest.raises(RecordKindError):
        await writer.stamp_promoted(issue)
    with pytest.raises(RecordKindError):
        await writer.record_verdict(
            issue,
            ExperimentResult(
                successful=True,
                findings="f",
                memory_overview="EXP 0722-w1-2: x — +1pp, n=2, p=.5",
                memory_content="c",
            ),
        )
    assert kiln.memories[issue] == before


async def test_list_issues_returns_typed_views():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: issue: x",
        "task::t1",
        ["issue", "p0", "eval_424242966189"],
        content="- fixed",
    )
    (view,) = await make_writer(kiln).list_issues()
    assert isinstance(view, IssueView)
    assert view.id == issue
    assert view.state is IssueState.FIXED
    assert view.priority == 0
    assert view.bound_eval_ids == {"424242966189"}


# ---- the experiment writers refuse moves the table does not allow ----


async def test_verdict_over_a_result_is_refused():
    from kiln_harness_tuner.memory import ExperimentStateError

    kiln = FakeKiln()
    abandoned = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — ABANDONED (wave tripwire: timeout)",
        "task::t1",
        ["experiment"],
    )
    before = dict(kiln.memories[abandoned])
    with pytest.raises(ExperimentStateError):
        await make_writer(kiln).record_verdict(
            abandoned,
            ExperimentResult(
                successful=True,
                findings="f",
                memory_overview="EXP 0722-w1-2: x — +1pp, n=2, p=.5",
                memory_content="c",
            ),
        )
    assert kiln.memories[abandoned] == before


async def test_restate_over_a_result_is_refused():
    from kiln_harness_tuner.memory import ExperimentStateError

    kiln = FakeKiln()
    refuted = kiln.add_memory(
        "EXP 0722-w1-2: x — +1pp, n=2, p=.5 — REFUTED", "task::t1", ["experiment"]
    )
    with pytest.raises(ExperimentStateError):
        await make_writer(kiln).mark_abandoned(refuted, "tripwire")
    assert kiln.memories[refuted]["overview"].endswith("— REFUTED")


async def test_stamp_promoted_is_idempotent_and_needs_a_verdict():
    from kiln_harness_tuner.memory import ExperimentStateError

    kiln = FakeKiln()
    writer = make_writer(kiln)
    verdict = kiln.add_memory(
        "EXP 0722-w1-2: x — +8pp, n=20, p=.04", "task::t1", ["experiment"]
    )
    await writer.stamp_promoted(verdict)
    await writer.stamp_promoted(verdict)  # the retry ladder re-running it
    assert kiln.memories[verdict]["overview"].count("— PROMOTED") == 1
    running = kiln.add_memory(
        "EXP 0722-w1-3 (prompt): y — RUNNING", "task::t1", ["experiment"]
    )
    with pytest.raises(ExperimentStateError):
        await writer.stamp_promoted(running)


async def test_janitor_leaves_a_stamped_record_alone():
    # A `— RUNNING` behind a gate stamp is a hand edit or an analyzer echo;
    # it is a result now, and the janitor must not append ABANDONED to it.
    kiln = FakeKiln()
    stamped = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — RUNNING — PROMOTED 07-22",
        "task::t1",
        ["experiment"],
    )
    assert await make_writer(kiln).janitor_abandoned_experiments() == []
    assert kiln.memories[stamped]["overview"].endswith("— PROMOTED 07-22")


def test_held_stamp_parses_names_its_constraint_and_counts_as_stamped():
    from kiln_harness_tuner.memory_schema import held_constraint, held_stamp, is_stamped

    verdict = "EXP 0722-w1-2: x — +8pp, n=20, p=.04"
    held = verdict + held_stamp("09-15", "424242093572")
    assert held.endswith(" — HELD (constraint 424242093572) 09-15")
    assert experiment_state(held) is ExperimentState.VERDICT
    assert held_constraint(held) == "424242093572"
    assert held_constraint(verdict) is None
    assert is_stamped(held) and not is_stamped(verdict)
    view = ExperimentView.from_record(record(held, ["experiment"], None))
    assert view.held_on == "424242093572"
    assert view.stamped and not view.promoted and not view.rejected_at_val
