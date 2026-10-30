"""memory.py format tests: the write formats are contracts —
string-asserted here to freeze them. All against FakeKiln's memory routes.
"""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from conftest import baseline_record
from fake_kiln import FakeKiln

from kiln_harness_tuner import memory as memory_module
from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError
from kiln_harness_tuner.memory import (
    BASELINE_RULE_MOST_RECENT,
    BASELINE_RULE_TASK_DEFAULT,
    BaselineRecordMissingError,
    ExperimentStateError,
    IssueBindingMismatchError,
    IssueTerminalStateError,
    MemoryListShapeError,
    MemoryWriter,
    _fit_content,
    _fit_stateful,
)
from kiln_harness_tuner.memory_schema import (
    CONTENT_MAX_CHARS,
    HISTORY_TRUNCATION_MARKER,
    OVERVIEW_MAX_CHARS,
    SETUP_DELIMITER,
    SETUP_HEAD_LINE_MAX_CHARS,
    SETUP_HEAD_MAX_CHARS,
    SETUP_TAIL_RESERVE_CHARS,
    TECHNIQUE_MAX_CHARS,
    TRUNCATION_MARKER,
    VAL_STATS_OVERVIEW,
    VAL_STATS_TAG,
    ExperimentState,
    ExperimentView,
    IssueState,
    MemoryRecord,
    MemorySummary,
    SetupHead,
    baseline_rc_tag,
    clamp,
    eval_ids_from_tags,
    eval_tag,
    experiment_state,
    issue_state,
    join_owned,
    rc_ids_from_tags,
    set_state_prefix,
    split_owned,
    state_prefix_run,
)
from kiln_harness_tuner.schemas import (
    BaselineRef,
    ErrorInfo,
    EvalBuildReport,
    EvalDescription,
    EvalTarget,
    ExperimentItem,
    ExperimentResult,
    FixWork,
    MergeWork,
    OpenUnknown,
    PromotionDecision,
    RcChange,
    ResidualGateResult,
    ResidualItem,
    WinRef,
)
from kiln_harness_tuner.setup import EvalView, SpecView

FIXED_NOW = datetime(2026, 7, 22, 14, 0)
BASELINE = BaselineRef(rc_id="rc_base")


def stored_fingerprint(content: str | None) -> str | None:
    """What setup reads back: the head's fingerprint via `SetupHead.parse`,
    None on a record with no setup head (`IssueView.fingerprint`)."""
    head, _tail = split_owned(content)
    return None if head is None else SetupHead.parse(head).fingerprint


def make_writer(kiln: FakeKiln) -> MemoryWriter:
    return MemoryWriter(kiln, project_id="p1", task_id="t1", now=lambda: FIXED_NOW)


def fix_item(**work_kwargs) -> ExperimentItem:
    defaults = dict(
        issue_memory_id="issue_1",
        hypothesis="retry-skill for 429s",
        technique="prompt",
        predicted_effect="+10% pass rate",
        needs_eval=False,
        eval_id="eval_1",
    )
    defaults.update(work_kwargs)
    return ExperimentItem(
        id="w1-2",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=FixWork(**defaults),
    )


# ---- live API shapes ----


async def test_list_serves_summaries_without_content():
    # Regression for the live-shape fix: a listing-only read can NEVER see
    # content — rows are MemorySummary (no content attribute at all).
    kiln = FakeKiln()
    kiln.add_memory(
        "the client drops 429 retries", "task::t1", ["issue"], content="trace 42"
    )
    writer = make_writer(kiln)
    (row,) = await writer._list("task::t1", ["issue"])
    assert isinstance(row, MemorySummary)
    assert not hasattr(row, "content")
    assert row.content_length == len("trace 42")
    # The fake's route serves the real envelope exactly: summaries under
    # "listings" (no content key) + matched/remaining paging.
    envelope = await kiln.call(
        "GET", "/api/projects/p1/memories", query_params={"scope": "task::t1"}
    )
    assert set(envelope) == {"listings", "matched", "remaining", "remaining_tag_counts"}
    assert "content" not in envelope["listings"][0]
    assert envelope["listings"][0]["content_length"] == len("trace 42")
    assert envelope["matched"] == 1
    assert envelope["remaining"] == 0


async def test_list_pages_on_remaining():
    kiln = FakeKiln()
    for i in range(120):
        kiln.add_memory(f"mem {i}", "task::t1", ["experiment"])
    writer = make_writer(kiln)
    rows = await writer._list("task::t1", ["experiment"])
    assert len(rows) == 120
    list_calls = [c for c in kiln.calls if c[0] == "GET" and c[1].endswith("/memories")]
    assert [c[2]["offset"] for c in list_calls] == [0, 50, 100]


async def test_list_defensive_stop_on_empty_page():
    # A malformed response (empty page but remaining > 0) must terminate,
    # not loop forever.
    class StuckKiln:
        def __init__(self):
            self.calls = 0

        async def call(self, method, url_path, query_params=None, body=None, **kw):
            self.calls += 1
            return {
                "listings": [],
                "matched": 5,
                "remaining": 5,
                "remaining_tag_counts": {},
            }

    kiln = StuckKiln()
    writer = MemoryWriter(kiln, project_id="p1", task_id="t1", now=lambda: FIXED_NOW)
    assert await writer._list("task::t1", ["experiment"]) == []
    assert kiln.calls == 1


class CannedKiln:
    """Stub serving a fixed list-response per call (shape tests only)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.offsets = []

    async def call(self, method, url_path, query_params=None, body=None, **kw):
        self.offsets.append((query_params or {}).get("offset"))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def canned_writer(responses) -> tuple[MemoryWriter, CannedKiln]:
    kiln = CannedKiln(responses)
    return (
        MemoryWriter(kiln, project_id="p1", task_id="t1", now=lambda: FIXED_NOW),
        kiln,
    )


async def test_list_shape_drift_fails_hard_never_an_empty_listing():
    # Stats-guard doctrine: a response without the verified envelope must
    # raise (naming what came back), never read as "no records" — that
    # would fake a missing baseline, skip janitor debris, and duplicate
    # open-unknown POSTs.
    row = {"id": "m1", "overview": "x", "tags": [], "scope": "task::t1"}
    drifted = [
        {"memories": [row]},  # the old assumed key
        {"listings": [row], "matched": 1},  # missing remaining
        {"remaining": 0, "matched": 0},  # missing listings
        None,  # empty 2xx body (KilnClient returns None)
        "gateway text",  # non-JSON body (KilnClient returns str)
    ]
    for response in drifted:
        writer, _ = canned_writer([response])
        with pytest.raises(MemoryListShapeError, match="listings"):
            await writer._list("task::t1", ["experiment"])
    # The old-key case names the received keys for the fixer.
    writer, _ = canned_writer([{"memories": [row]}])
    with pytest.raises(MemoryListShapeError, match="memories"):
        await writer._list("task::t1", ["experiment"])


def summary_row(i: int) -> dict:
    return {
        "id": f"m{i}",
        "overview": f"mem {i}",
        "tags": ["experiment"],
        "scope": "task::t1",
        "content_length": 0,
    }


async def test_list_keeps_paging_on_short_nonempty_pages():
    # Pins remaining-based paging: a server capping pages BELOW the
    # requested 50 still gets fully drained — the retired
    # `len(page) < limit` rule would have stopped after page one.
    def page(rows: list[dict], remaining: int) -> dict:
        return {"listings": rows, "matched": 5, "remaining": remaining}

    writer, kiln = canned_writer(
        [
            page([summary_row(0), summary_row(1)], remaining=3),
            page([summary_row(2), summary_row(3)], remaining=1),
            page([summary_row(4)], remaining=0),
        ]
    )
    rows = await writer._list("task::t1", ["experiment"])
    assert [r.id for r in rows] == ["m0", "m1", "m2", "m3", "m4"]
    assert kiln.offsets == [0, 2, 4]


async def test_list_matched_bounds_an_offset_ignoring_server():
    # An offset-ignoring server repeating the same nonempty page with
    # remaining > 0 must terminate via the unconditional `matched` bound.
    page = {
        "listings": [summary_row(0), summary_row(1)],
        "matched": 2,
        "remaining": 1,  # lying — would loop forever without the bound
    }
    writer, kiln = canned_writer([page])
    rows = await writer._list("task::t1", ["experiment"])
    assert [r.id for r in rows] == ["m0", "m1"]
    assert kiln.offsets == [0]


async def test_get_parses_bare_array_with_content():
    kiln = FakeKiln()
    mem = kiln.add_memory("issue x", "task::t1", ["issue"], content="line 1")
    writer = make_writer(kiln)
    record = await writer._get(mem)
    assert isinstance(record, MemoryRecord)
    assert record.content == "line 1"
    # The fake's by_ids route serves the verified shape: a BARE ARRAY of
    # full records, content present (null when empty).
    rows = await kiln.call(
        "GET", "/api/projects/p1/memories/by_ids", query_params={"ids": [mem]}
    )
    assert isinstance(rows, list)
    assert rows[0]["content"] == "line 1"
    assert rows[0]["model_type"] == "memory"


async def test_get_missing_id_raises():
    writer = make_writer(FakeKiln())
    with pytest.raises(KeyError, match="nope"):
        await writer._get("nope")


async def test_require_baseline_record_hydrates_content_via_by_ids():
    # Listings never carry content — the baseline read must hydrate it
    # (baseline_lineage and promote both parse the history content).
    kiln = FakeKiln()
    baseline_record(
        kiln,
        "Baseline: run config rc_213 — promoted 07-16 by EXP 0716-w4-2",
        content="rc_213 (07-16, EXP 0716-w4-2) ← rc_207",
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    assert record.content == "rc_213 (07-16, EXP 0716-w4-2) ← rc_207"
    assert any("/by_ids" in c[1] for c in kiln.calls)


async def test_janitor_scan_stays_on_summaries():
    # The — RUNNING scan reads only summary overviews: NO hydration
    # round-trip for the scan itself; only the stamped record's restate
    # hydrates (one by_ids call, for the stamped id).
    kiln = FakeKiln()
    running = kiln.add_memory(
        "EXP 0721-w9-9 (prompt): stale — RUNNING", "task::t1", ["experiment"]
    )
    kiln.add_memory("EXP 0721-w1-1: won — +8%, n=20, p=.03", "task::t1", ["experiment"])
    writer = make_writer(kiln)
    assert await writer.janitor_abandoned_experiments() == [running]
    by_ids_calls = [c for c in kiln.calls if "/by_ids" in c[1]]
    assert [c[2]["ids"] for c in by_ids_calls] == [[running]]


async def test_write_open_unknowns_hydrates_once_per_batch():
    # The dedupe matches on the hydrated content's
    # question line — the batch pays exactly ONE by_ids round-trip.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    q = OpenUnknown(
        question="do API docs exist",
        default_assumed="no",
        gates="tooling",
        reask_when="ticket answered",
    )
    first_id = await writer.record_open_unknown(q)
    kiln.calls.clear()
    assert await writer.record_open_unknown(q) == first_id
    assert len(kiln.memory_posts) == 1  # PATCHed, not duplicated
    assert len([c for c in kiln.calls if "/by_ids" in c[1]]) == 1


# ---- write-side caps (enforced server-side: the API 422s overview past
# 280 and content past 4000 on POST/PATCH) ----


def make_verdict_result(content: str) -> ExperimentResult:
    return ExperimentResult(
        successful=True,
        findings="worked",
        memory_overview="EXP 0722-w1-2: retry-skill for 429s — +18%, n=34, p=.04",
        memory_content=content,
    )


def open_running(kiln: FakeKiln) -> str:
    return kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — RUNNING", "task::t1", ["experiment"]
    )


async def test_content_exactly_at_cap_stored_verbatim():
    # Boundary: exactly 4000 chars (the raised cap) passes untouched.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    content = "a" * CONTENT_MAX_CHARS
    await writer.record_verdict(open_running(kiln), make_verdict_result(content))
    assert kiln.memories["mem_1"]["content"] == content


def test_fit_content_over_cap_single_line_falls_back_to_tail_cut():
    over = _fit_content("a" * (CONTENT_MAX_CHARS + 1))
    assert over == "a" * (CONTENT_MAX_CHARS - 1) + "…"
    assert _fit_content(None) is None


VERDICT_HEAD = [
    "hypothesis: retry-skill for 429s",
    "lever: prompt",
    "candidate rc: rc_213",
    "issue memory id: issue_1",
    "eval: eval_9",
    "parent rc: rc_207 (parent compare)",
]
VERDICT_TAIL = ["caution: low power (n=12)", "next: rerun with n=40 against rc_213"]


def verdict_content(prose_lines: int) -> str:
    prose = [
        f"interpretation: verbose line {i} " + "x" * 80 for i in range(prose_lines)
    ]
    return "\n".join(VERDICT_HEAD + prose + VERDICT_TAIL)


async def test_record_verdict_2300_chars_now_stored_unclamped():
    # A ~2300-char verdict content fits the 4000-char cap — stored VERBATIM,
    # no clamp, no truncation marker.
    content = verdict_content(20)
    assert 2300 < len(content) <= CONTENT_MAX_CHARS

    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.record_verdict(open_running(kiln), make_verdict_result(content))
    stored = kiln.memories["mem_1"]["content"]
    assert stored == content
    assert "[… truncated …]" not in stored


async def test_record_verdict_over_cap_succeeds_keeping_ids_and_next():
    # Past the raised 4000-char cap the safety clamp still applies: the
    # PATCH must succeed (the fake 422s unclamped writes), and the clamp
    # must keep the id-bearing head lines AND the `next:` tail — never a
    # blind tail cut.
    content = verdict_content(40)
    assert len(content) > CONTENT_MAX_CHARS

    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.record_verdict(open_running(kiln), make_verdict_result(content))
    stored = kiln.memories["mem_1"]["content"]
    assert len(stored) <= CONTENT_MAX_CHARS
    for line in VERDICT_HEAD:
        assert line in stored  # the MUST-cite id lines survive whole
    assert "[… truncated …]" in stored
    assert stored.endswith("next: rerun with n=40 against rc_213")


async def test_append_over_cap_keeps_new_line_drops_oldest():
    # link_eval on a near-cap issue record: the appended line MUST survive;
    # the OLDEST lines drop first behind the history marker.
    old_lines = [f"- note {i:03d}: " + "y" * 30 for i in range(93)]
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "the client drops 429 retries",
        "task::t1",
        ["issue"],
        content="\n".join(old_lines),
    )
    assert len("\n".join(old_lines)) <= CONTENT_MAX_CHARS
    writer = make_writer(kiln)
    rep = EvalBuildReport(
        eval_id="eval_9",
        eval_config_id="ec_9",
        eval_type="llm_judge",
        case_count=20,
        reproduced=True,
        baseline_headline="62% pass",
        priority="p1",
    )
    await writer.link_eval(issue, rep)
    stored = kiln.memories[issue]["content"]
    assert len(stored) <= CONTENT_MAX_CHARS
    assert stored.startswith("[… earlier history truncated …]")
    assert stored.endswith("- eval: eval_9 (built 07-22, reproduced)")
    assert "- note 000" not in stored  # oldest dropped first
    assert "- note 092" in stored  # newest history kept


async def test_promote_chain_over_cap_keeps_newest_lineage():
    # The chain is written NEWEST FIRST and clamped by
    # `_fit_content_keep_head`, which drops the OLDEST lines — so what
    # survives is the recent lineage the screen actually walks. (`_fit_content`
    # is wrong here and is why that clamp exists: its head+tail regions would
    # keep the deepest ancestors and punch a hole through the middle.) Losing
    # the oldest ids costs nothing but the deepest cache screens.
    chain = [f"rc_{i}" for i in range(100, 700)]
    assert len("\n".join(chain)) > CONTENT_MAX_CHARS
    kiln = FakeKiln()
    mem_id = baseline_record(
        kiln, "Baseline: run config rc_100", content="\n".join(chain), rc_id="rc_100"
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    await writer.promote(
        PromotionDecision(
            winner_rc_id="rc_213", winner_experiment_memory_id="mem_9", rationale="+9%"
        ),
        "EXP 0722-w4-2",
        record,
    )
    stored = kiln.memories[mem_id]["content"]
    assert len(stored) <= CONTENT_MAX_CHARS
    fresh = MemoryRecord.from_api(kiln.memories[mem_id])
    lineage = writer.baseline_lineage(fresh)
    assert lineage[:3] == ["rc_213", "rc_100", "rc_101"]  # newest survive
    assert "rc_699" not in lineage  # oldest gone, and no marker read as an id


async def test_fake_rejects_over_cap_writes_with_real_422():
    # The fake serves the REAL write-side caps — BOTH fields —
    # so any future unclamped write path fails the suite
    # instead of green-lighting.
    kiln = FakeKiln()
    mem = kiln.add_memory("x", "task::t1", ["issue"])
    over_content = {"content": "z" * (CONTENT_MAX_CHARS + 1)}
    over_overview = {"overview": "o" * (OVERVIEW_MAX_CHARS + 1)}
    for field, cap, patch_body in [
        ("content", "4000", over_content),
        ("overview", "280", over_overview),
    ]:
        post_body = {"overview": "o", "scope": "task::t1", "tags": [], **patch_body}
        for method, path, body in [
            ("PATCH", f"/api/projects/p1/memories/{mem}", patch_body),
            ("POST", "/api/projects/p1/memories", post_body),
        ]:
            with pytest.raises(KilnAPIError) as exc:
                await kiln.call(method, path, body=body)
            assert exc.value.status_code == 422
            [detail] = exc.value.body["detail"]
            assert detail["type"] == "string_too_long"
            assert detail["loc"] == ["body", field]
            assert detail["ctx"] == {"max_length": cap}


async def test_overview_282_chars_clamped_before_fake_422():
    # A 282-char overview: `_fit_overview` clamps to 280 BEFORE the write
    # reaches the API, so the fake's (new, write-enforced) overview cap
    # never trips — the client-side clamp is what keeps writes 422-free.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    res = ExperimentResult(
        successful=True,
        findings="x",
        memory_overview="E" * 282,
        memory_content="short",
    )
    await writer.record_verdict(open_running(kiln), res)
    stored = kiln.memories["mem_1"]["overview"]
    assert len(stored) == OVERVIEW_MAX_CHARS == 280
    assert stored == "E" * 279 + "…"


# ---- experiment lifecycle ----


async def test_open_experiment_fix_format():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)
    record = kiln.memories[mem_id]
    assert (
        record["overview"] == "EXP 0722-w1-2 (prompt): retry-skill for 429s — RUNNING"
    )
    assert record["scope"] == "task::t1"
    assert record["tags"] == ["experiment"]
    assert record["content"] == (
        "hypothesis: retry-skill for 429s\n"
        "predicted effect: +10% pass rate\n"
        "parent rc: rc_base\n"
        "eval: eval_1\n"
        "issue: issue_1\n"
        "technique: prompt"
    )


async def test_open_experiment_records_escape_hatch_parent_and_group():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    item = fix_item(parent_rc_id="rc_fork", head_to_head_group="tone")
    mem_id = await writer.open_experiment(item, wave=1, baseline=BASELINE)
    content = kiln.memories[mem_id]["content"]
    assert "parent rc: rc_fork" in content
    assert content.endswith("head_to_head_group: tone")


async def test_open_experiment_merge_format():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    item = ExperimentItem(
        id="w4-2",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=MergeWork(
            wins=[
                WinRef(experiment_memory_id="mem_a", rc_id="rc_1"),
                WinRef(experiment_memory_id="mem_b", rc_id="rc_2"),
            ],
            conflict_flags=["mem_a/mem_b both touch prompt tone"],
        ),
    )
    mem_id = await writer.open_experiment(item, wave=4, baseline=BASELINE)
    record = kiln.memories[mem_id]
    # Ingredient ids are the label — no hypothesis field to quote.
    assert record["overview"] == "EXP 0722-w4-2 (merge): mem_a + mem_b — RUNNING"
    assert "- ingredient: mem_a (rc rc_1)" in record["content"]
    assert "conflict flags: mem_a/mem_b both touch prompt tone" in record["content"]
    assert "parent rc: rc_base" in record["content"]


async def test_record_verdict_preserves_head_to_head_group_line():
    # The verdict content REPLACES the open record's, but the
    # group marker becomes load-bearing exactly post-verdict (later
    # planners/builders read the post-verdict content) — code re-appends the
    # frozen line when the analyzer drops it. String-asserted like the other
    # frozen formats.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(
        fix_item(head_to_head_group="tone"), wave=1, baseline=BASELINE
    )
    await writer.record_verdict(
        mem_id, make_verdict_result("hypothesis: x\nnext: rerun")
    )
    stored = kiln.memories[mem_id]["content"]
    # The issue/technique lines ride along too (lever ladder),
    # ahead of the group line, which stays last.
    assert stored == (
        "hypothesis: x\nnext: rerun\nissue: issue_1\ntechnique: prompt\n"
        "head_to_head_group: tone"
    )


async def test_record_verdict_group_line_not_doubled_when_recited():
    # An analyzer-cited copy is stripped and the stored frozen line is
    # appended LAST (position-deterministic tail placement) —
    # never doubled, and an analyzer variant of the value never wins.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(
        fix_item(head_to_head_group="tone"), wave=1, baseline=BASELINE
    )
    content = "hypothesis: x\nhead_to_head_group: tone-VARIANT\nnext: rerun"
    await writer.record_verdict(mem_id, make_verdict_result(content))
    stored = kiln.memories[mem_id]["content"]
    assert stored == (
        "hypothesis: x\nnext: rerun\nissue: issue_1\ntechnique: prompt\n"
        "head_to_head_group: tone"
    )
    assert stored.count("head_to_head_group") == 1


async def test_record_verdict_recited_group_line_survives_over_cap_clamp():
    # The compliant-analyzer branch: the analyzer re-cites the
    # group mid-content AND the content is over-cap — the mid-content copy
    # sits in the clamp's dropped-middle region, so "already present" must
    # not skip the tail append. The line is re-homed to the tail and
    # survives.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(
        fix_item(head_to_head_group="tone"), wave=1, baseline=BASELINE
    )
    prose = [f"interpretation: verbose line {i} " + "x" * 80 for i in range(40)]
    content = "\n".join(
        VERDICT_HEAD
        + prose[:25]
        + ["head_to_head_group: tone"]
        + prose[25:]
        + VERDICT_TAIL
    )
    assert len(content) > CONTENT_MAX_CHARS
    await writer.record_verdict(mem_id, make_verdict_result(content))
    stored = kiln.memories[mem_id]["content"]
    assert len(stored) <= CONTENT_MAX_CHARS
    assert stored.endswith("head_to_head_group: tone")
    assert stored.count("head_to_head_group") == 1
    assert "next: rerun with n=40 against rc_213" in stored


async def test_record_verdict_group_line_survives_over_cap_clamp():
    # Appended last, inside _fit_content's protected tail region — the group
    # must survive the safety clamp whole, alongside the `next:` tail.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(
        fix_item(head_to_head_group="tone"), wave=1, baseline=BASELINE
    )
    content = verdict_content(40)
    assert len(content) > CONTENT_MAX_CHARS
    await writer.record_verdict(mem_id, make_verdict_result(content))
    stored = kiln.memories[mem_id]["content"]
    assert len(stored) <= CONTENT_MAX_CHARS
    assert stored.endswith("head_to_head_group: tone")
    assert "next: rerun with n=40 against rc_213" in stored


async def test_open_experiment_merge_records_head_to_head_group():
    # Both work types carry the field, and the group must survive the
    # wave for merge arms too — merging.md
    # reads it off the experiment memory's content.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    item = ExperimentItem(
        id="w3-1",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=MergeWork(
            wins=[WinRef(experiment_memory_id="mem_a", rc_id="rc_1")],
            head_to_head_group="scope-surface",
        ),
    )
    mem_id = await writer.open_experiment(item, wave=3, baseline=BASELINE)
    content = kiln.memories[mem_id]["content"]
    assert content.endswith("head_to_head_group: scope-surface")
    assert "parent rc: rc_base" in content


async def test_open_experiment_clamps_middle_but_keeps_label_and_suffix():
    # The lifecycle state suffix is the vocabulary the whole loop reads — it
    # must survive truncation: the janitor's endswith and
    # mark_failed's suffix swap both depend on it.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    item = fix_item(hypothesis="H" * 300)
    mem_id = await writer.open_experiment(item, wave=1, baseline=BASELINE)
    overview = kiln.memories[mem_id]["overview"]
    assert len(overview) == OVERVIEW_MAX_CHARS
    assert overview.startswith("EXP 0722-w1-2 (prompt): HHH")
    assert "…" in overview
    assert overview.endswith(" — RUNNING")
    # ...and the stamps still work on the clamped overview:
    await writer.mark_failed(mem_id, ErrorInfo(error_class="timeout", detail="t"))
    failed = kiln.memories[mem_id]["overview"]
    assert failed.endswith("— FAILED (infra: timeout)")
    assert len(failed) <= OVERVIEW_MAX_CHARS
    await writer.mark_abandoned(mem_id, "wave tripwire: timeout")
    abandoned = kiln.memories[mem_id]["overview"]
    assert abandoned.endswith("— ABANDONED (wave tripwire: timeout)")
    assert len(abandoned) <= OVERVIEW_MAX_CHARS


async def test_stamp_promoted_preserves_suffix_on_long_overview():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory("EXP 0722-w1-2: " + "x" * 260, "task::t1", ["experiment"])
    await writer.stamp_promoted(mem_id)
    overview = kiln.memories[mem_id]["overview"]
    assert overview.endswith(" — PROMOTED 07-22")
    assert len(overview) == OVERVIEW_MAX_CHARS


def test_clamp_non_positive_budget_yields_empty():
    # A negative slice must not keep nearly the whole text.
    assert clamp("hello", 0) == ""
    assert clamp("hello", -3) == ""
    assert clamp("hello", 3) == "he…"
    assert clamp("hello", 5) == "hello"


def test_exp_label_uses_run_start_date_not_call_time():
    # A run crossing midnight must keep one label — the
    # promotion-time recomputation has to match what open_experiment wrote.
    clock = {"now": datetime(2026, 7, 22, 23, 59)}
    writer = MemoryWriter(
        FakeKiln(), project_id="p1", task_id="t1", now=lambda: clock["now"]
    )
    assert writer.exp_label("w1-1") == "EXP 0722-w1-1"
    clock["now"] = datetime(2026, 7, 23, 0, 5)  # midnight passes mid-run
    assert writer.exp_label("w1-1") == "EXP 0722-w1-1"


async def test_record_verdict_patches_overview_and_content_verbatim():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — RUNNING", "task::t1", ["experiment"]
    )
    res = ExperimentResult(
        successful=True,
        findings="worked",
        memory_overview="EXP 0722-w1-2: retry-skill for 429s — +18%, n=34, p=.04",
        memory_content="hypothesis: ...\nresult: +18%",
    )
    await writer.record_verdict(mem_id, res)
    record = kiln.memories[mem_id]
    assert record["overview"] == res.memory_overview
    assert "REFUTED" not in record["overview"]  # successful → no suffix
    assert record["content"] == res.memory_content


async def test_record_verdict_refuted_appends_suffix():
    # String-asserted for parser safety: the suffix
    # lands AFTER the front-loaded label + numbers tail, so the stored
    # overview keeps its `EXP <MMDD>-<id>: … — <numbers>` shape in front.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — RUNNING", "task::t1", ["experiment"]
    )
    res = ExperimentResult(
        successful=False,
        findings="no effect",
        memory_overview="EXP 0722-w1-2: retry-skill for 429s — +2%, n=12, p=.61",
        memory_content="result: +2% (noise)",
    )
    await writer.record_verdict(mem_id, res)
    record = kiln.memories[mem_id]
    assert record["overview"] == (
        "EXP 0722-w1-2: retry-skill for 429s — +2%, n=12, p=.61 — REFUTED"
    )
    assert record["content"] == "result: +2% (noise)"


async def test_record_verdict_refuted_suffix_survives_cap():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory("EXP: x — RUNNING", "task::t1", ["experiment"])
    res = ExperimentResult(
        successful=False,
        findings="x",
        memory_overview="E" * OVERVIEW_MAX_CHARS,
        memory_content="short",
    )
    await writer.record_verdict(mem_id, res)
    stored = kiln.memories[mem_id]["overview"]
    assert len(stored) == OVERVIEW_MAX_CHARS
    assert stored.endswith("… — REFUTED")  # suffix whole; middle clamped


async def test_record_verdict_refuted_suffix_idempotent():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory("EXP: x — RUNNING", "task::t1", ["experiment"])
    res = ExperimentResult(
        successful=False,
        findings="x",
        memory_overview="EXP 0722-w1-2: retry-skill — +2%, n=12, p=.61 — REFUTED",
        memory_content="c",
    )
    await writer.record_verdict(mem_id, res)
    stored = kiln.memories[mem_id]["overview"]
    assert stored.count("— REFUTED") == 1
    assert stored.endswith(" — REFUTED")


async def test_record_verdict_refuted_never_resembles_running_debris():
    # A refuted record must not re-acquire a tail the janitor stamps.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — RUNNING", "task::t1", ["experiment"]
    )
    res = ExperimentResult(
        successful=False,
        findings="x",
        memory_overview="EXP 0722-w1-2: retry-skill — -1%, n=12, p=.8",
        memory_content="c",
    )
    await writer.record_verdict(mem_id, res)
    assert await writer.janitor_abandoned_experiments() == []
    assert kiln.memories[mem_id]["overview"].endswith(" — REFUTED")


async def test_mark_failed_swaps_running_suffix():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): retry-skill — RUNNING", "task::t1", ["experiment"]
    )
    await writer.mark_failed(mem_id, ErrorInfo(error_class="rate_limit", detail="429"))
    assert kiln.memories[mem_id]["overview"] == (
        "EXP 0722-w1-2 (prompt): retry-skill — FAILED (infra: rate_limit)"
    )


async def test_mark_abandoned_replaces_failed_suffix_too():
    # The tripwire re-stamps an already-FAILED memory as ABANDONED.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory(
        "EXP 0722-w1-2 (prompt): x — FAILED (infra: rate_limit)",
        "task::t1",
        ["experiment"],
    )
    await writer.mark_abandoned(mem_id, "wave tripwire: rate_limit")
    assert kiln.memories[mem_id]["overview"] == (
        "EXP 0722-w1-2 (prompt): x — ABANDONED (wave tripwire: rate_limit)"
    )


async def test_janitor_stamps_only_running_debris():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    running = kiln.add_memory(
        "EXP 0721-w9-9 (prompt): stale — RUNNING", "task::t1", ["experiment"]
    )
    done = kiln.add_memory(
        "EXP 0721-w1-1: won — +8%, n=20, p=.03", "task::t1", ["experiment"]
    )
    other_scope = kiln.add_memory(
        "EXP 0720-w1-1 (prompt): other task — RUNNING", "task::t2", ["experiment"]
    )
    stamped = await writer.janitor_abandoned_experiments()
    assert stamped == [running]
    assert kiln.memories[running]["overview"] == (
        "EXP 0721-w9-9 (prompt): stale — ABANDONED (orchestrator restart 07-22)"
    )
    assert kiln.memories[done]["overview"].endswith("p=.03")
    assert kiln.memories[other_scope]["overview"].endswith("— RUNNING")


async def test_stamp_promoted_appends():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory(
        "EXP 0722-w1-2: retry-skill — +18%, n=34, p=.04", "task::t1", ["experiment"]
    )
    await writer.stamp_promoted(mem_id)
    assert kiln.memories[mem_id]["overview"] == (
        "EXP 0722-w1-2: retry-skill — +18%, n=34, p=.04 — PROMOTED 07-22"
    )


# ---- val-gate rejection ----


def experiment_record(kiln: FakeKiln, content: str | None = None) -> str:
    """A post-verdict experiment record — success-shaped numbers tail, no
    lifecycle stamp yet: exactly what the gate hands the rejection writers."""
    return kiln.add_memory(
        "EXP 0722-w1-2: retry-skill — +8.5pp, p=.006",
        "task::t1",
        ["experiment"],
        content=content,
    )


async def test_stamp_rejected_appends():
    # The loser's twin of `— PROMOTED`: without it the numbers tail alone
    # reads success-shaped in a memory listing.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = experiment_record(kiln)
    await writer.stamp_rejected(mem_id)
    assert kiln.memories[mem_id]["overview"] == (
        "EXP 0722-w1-2: retry-skill — +8.5pp, p=.006 — REJECTED AT VAL 07-22"
    )


async def test_stamp_rejected_preserves_suffix_on_long_overview():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory("EXP 0722-w1-2: " + "x" * 260, "task::t1", ["experiment"])
    await writer.stamp_rejected(mem_id)
    overview = kiln.memories[mem_id]["overview"]
    assert overview.endswith(" — REJECTED AT VAL 07-22")
    assert len(overview) == OVERVIEW_MAX_CHARS


async def test_stamp_rejected_is_idempotent():
    # Re-entrant gate (crash-retry) must not double-stamp.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = experiment_record(kiln)
    await writer.stamp_rejected(mem_id)
    await writer.stamp_rejected(mem_id)
    overview = kiln.memories[mem_id]["overview"]
    assert overview.count("— REJECTED AT VAL") == 1
    assert len([mid for mid, _ in kiln.memory_patches if mid == mem_id]) == 1


async def test_note_rejection_frozen_format():
    # Metric-free by construction (F1): no val numbers leave the baseline
    # record — the line is status intent plus a pointer to the run report.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = experiment_record(kiln, content="hypothesis: retry-skill for 429s")
    await writer.note_rejection(mem_id, "rc_base")
    record = kiln.memories[mem_id]
    assert record["content"] == (
        "hypothesis: retry-skill for 429s\n"
        "- rejected at the val gate 07-22 vs baseline rc_base — did not beat it; "
        "details in the run report."
    )
    assert record["overview"] == "EXP 0722-w1-2: retry-skill — +8.5pp, p=.006"


async def test_note_rejection_is_idempotent():
    # Prefix-keyed guard, so a retry that crosses midnight (a different
    # `<MM-DD>`) still cannot append a second copy.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = experiment_record(kiln)
    await writer.note_rejection(mem_id, "rc_base")
    writer_next_day = MemoryWriter(
        kiln, project_id="p1", task_id="t1", now=lambda: datetime(2026, 7, 23, 1, 0)
    )
    await writer_next_day.note_rejection(mem_id, "rc_base")
    assert kiln.memories[mem_id]["content"].count("- rejected at the val gate ") == 1
    assert len([mid for mid, _ in kiln.memory_patches if mid == mem_id]) == 1


# ---- baseline record ----


async def test_require_baseline_record_missing_is_instructive():
    writer = make_writer(FakeKiln())
    with pytest.raises(BaselineRecordMissingError) as exc:
        await writer.require_baseline_record()
    message = str(exc.value)
    assert "baseline setup" in message  # written for the calling agent
    assert "re-invoke" in message
    assert "never invents a baseline" in message
    # It must name the TAG, not an overview format — that is what a seeder
    # now has to produce.
    assert "rc_<rc_id>" in message


async def test_require_baseline_record_accepts_any_overview_prose():
    # The deleted dangling-record fatal: the overview carries no contract, so
    # prose nobody can parse is not an error as long as the rc tag is there.
    kiln = FakeKiln()
    baseline_record(kiln, "whatever a human felt like typing", rc_id="rc_207")
    record = await make_writer(kiln).require_baseline_record()
    assert record.overview == "whatever a human felt like typing"


async def test_baseline_rc_id_comes_from_the_tag_not_the_overview():
    kiln = FakeKiln()
    # An overview naming a DIFFERENT rc — the tag is what the loop reads.
    baseline_record(kiln, "Baseline: run config rc_999 (stale prose)", rc_id="rc_207")
    writer = make_writer(kiln)
    assert writer.baseline_rc_id(await writer.require_baseline_record()) == "rc_207"


async def test_baseline_rc_id_without_an_rc_tag_is_fatal():
    kiln = FakeKiln()
    baseline_record(kiln, "Baseline: run config rc_207 — seeded by hand", rc_id=None)
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    with pytest.raises(BaselineRecordMissingError, match="carries no `rc_<rc_id>` tag"):
        writer.baseline_rc_id(record)


async def test_baseline_rc_id_with_two_rc_tags_is_fatal():
    # An ambiguous pointer is worse than none: every build and measurement
    # in the wave parents from this id.
    kiln = FakeKiln()
    kiln.add_memory(
        "Baseline: hand-edited",
        "task::t1",
        ["baseline", baseline_rc_tag("rc_207"), baseline_rc_tag("rc_213")],
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    with pytest.raises(BaselineRecordMissingError, match="names 2 run configs"):
        writer.baseline_rc_id(record)


async def test_baseline_lineage_reads_the_chain_newest_first():
    kiln = FakeKiln()
    baseline_record(
        kiln,
        "Baseline: run config rc_213 — promoted 07-16 by EXP 0716-w4-2",
        content="rc_213\nrc_207\nrc_101\nrc_9",
        rc_id="rc_213",
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    assert writer.baseline_lineage(record) == ["rc_213", "rc_207", "rc_101", "rc_9"]


async def test_baseline_lineage_dedups_and_ignores_the_truncation_marker():
    # Tolerant on purpose: a human annotation after the id survives, blank
    # lines and repeats collapse, and the module's OWN clamp marker is never
    # read back as an rc id.
    kiln = FakeKiln()
    baseline_record(
        kiln,
        "Baseline: run config rc_213",
        content=(
            "rc_213 (07-16, EXP 0716-w4-2)\n\nrc_207\nrc_213\n[… truncated …]\nrc_9"
        ),
        rc_id="rc_213",
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    assert writer.baseline_lineage(record) == ["rc_213", "rc_207", "rc_9"]


async def test_baseline_lineage_on_unparseable_content_does_not_raise():
    # The degrade-to-no-screen contract: a bad parse costs a cache screen
    # (`_latest_baseline_results` finds no covered ancestor and the run
    # re-measures) — never a raise, and nothing leaks, since the record
    # holds no val data.
    kiln = FakeKiln()
    baseline_record(kiln, "Baseline: run config rc_9", content=None, rc_id="rc_9")
    writer = make_writer(kiln)
    assert writer.baseline_lineage(await writer.require_baseline_record()) == []


async def test_promote_writes_no_val_metrics_to_the_baseline_record():
    # The whole point of B1: the agent's val summary reaches this record
    # through no field at all.
    kiln = FakeKiln()
    mem_id = baseline_record(
        kiln,
        "Baseline: run config rc_207 — promoted 07-15 by EXP 0715-w3-1",
        content="rc_207\nrc_184",
        rc_id="rc_207",
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    await writer.promote(
        PromotionDecision(
            winner_rc_id="rc_213",
            winner_experiment_memory_id="mem_9",
            rationale="+9% mean win rate vs rc_207",
        ),
        "EXP 0722-w4-2",
        record,
    )
    patched = kiln.memories[mem_id]
    assert patched["overview"] == (
        "Baseline: run config rc_213 — promoted 07-22 by EXP 0722-w4-2"
    )
    written = f"{patched['overview']}\n{patched['content']}\n{patched['tags']}"
    assert "+9%" not in written
    assert "win rate" not in written


async def test_promote_repoints_the_rc_tag():
    kiln = FakeKiln()
    mem_id = kiln.add_memory(
        "Baseline: run config rc_207",
        "task::t1",
        ["baseline", baseline_rc_tag("rc_207"), "hand_added"],
        content="rc_207",
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    await writer.promote(
        PromotionDecision(
            winner_rc_id="rc_213", winner_experiment_memory_id=None, rationale="+9%"
        ),
        "EXP 0722-w4-2",
        record,
    )
    tags = kiln.memories[mem_id]["tags"]
    # The pointer MOVES (not additive — two rc tags is an ambiguous
    # pointer); every other tag survives.
    assert tags == ["baseline", "rc_rc_213", "hand_added"]
    assert writer.baseline_rc_id(MemoryRecord.from_api(kiln.memories[mem_id])) == (
        "rc_213"
    )


async def test_promote_prepends_the_winner_to_the_chain():
    kiln = FakeKiln()
    mem_id = baseline_record(
        kiln, "Baseline: run config rc_207", content="rc_207\nrc_184", rc_id="rc_207"
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    await writer.promote(
        PromotionDecision(
            winner_rc_id="rc_213", winner_experiment_memory_id=None, rationale="+9%"
        ),
        "EXP 0722-w4-2",
        record,
    )
    fresh = MemoryRecord.from_api(kiln.memories[mem_id])
    assert fresh.content == "rc_213\nrc_207\nrc_184"
    assert writer.baseline_lineage(fresh) == ["rc_213", "rc_207", "rc_184"]


async def test_promote_clamps_an_unbounded_winner_label_to_the_overview_cap():
    # `winner_label` is normally the bounded `EXP <MMDD>-<id>`, but on the
    # unmatched-winner path the orchestrator composes it from the agent's
    # UNVERIFIED `winner_experiment_memory_id`, which has no length bound.
    # `_patch`'s `_fit_overview` is the net; the server 422s over-cap writes,
    # so an unclamped overview would fail the promotion outright.
    kiln = FakeKiln()
    mem_id = baseline_record(
        kiln, "Baseline: run config rc_207", content="rc_207", rc_id="rc_207"
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    await writer.promote(
        PromotionDecision(
            winner_rc_id="rc_213", winner_experiment_memory_id=None, rationale="+9%"
        ),
        f"unmatched (claimed {'m' * 400})",
        record,
    )
    overview = kiln.memories[mem_id]["overview"]
    assert len(overview) == OVERVIEW_MAX_CHARS == 280
    assert overview.endswith("…")
    # The pointer still resolves — the clamp is cosmetic, the tag is not.
    assert writer.baseline_rc_id(MemoryRecord.from_api(kiln.memories[mem_id])) == (
        "rc_213"
    )


async def test_promote_never_duplicates_an_rc_already_at_the_chain_head():
    kiln = FakeKiln()
    mem_id = baseline_record(
        kiln, "Baseline: run config rc_207", content="rc_207\nrc_184", rc_id="rc_207"
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    await writer.promote(
        PromotionDecision(
            winner_rc_id="rc_207", winner_experiment_memory_id=None, rationale="+0%"
        ),
        "EXP 0722-w4-2",
        record,
    )
    assert kiln.memories[mem_id]["content"] == "rc_207\nrc_184"


# ---- the `val` stats record ----


def promotion(rationale: str = "+9% mean win rate vs rc_207") -> PromotionDecision:
    return PromotionDecision(
        winner_rc_id="rc_213", winner_experiment_memory_id="mem_9", rationale=rationale
    )


def val_stats_record(kiln: FakeKiln) -> dict:
    (record,) = [m for m in kiln.memories.values() if VAL_STATS_TAG in m["tags"]]
    return record


async def test_val_stats_record_is_created_on_first_promotion():
    # Onboarding seeds `baseline` alone — the stats record appears here.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.record_val_stats(promotion(), "EXP 0722-w4-2", "rc_207")
    record = val_stats_record(kiln)
    assert record["tags"] == [VAL_STATS_TAG]
    assert record["scope"] == "task::t1"
    assert record["overview"] == VAL_STATS_OVERVIEW
    assert record["content"] == (
        "rc_213 (07-22, EXP 0722-w4-2) ← rc_207 — +9% mean win rate vs rc_207"
    )


async def test_val_stats_record_is_patched_newest_first_on_later_promotions():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.record_val_stats(promotion("+5% vs rc_184"), "EXP 0722-w1-1", "rc_184")
    await writer.record_val_stats(promotion("+9% vs rc_207"), "EXP 0722-w4-2", "rc_207")
    record = val_stats_record(kiln)  # singleton — one record, not two
    assert record["content"].splitlines() == [
        "rc_213 (07-22, EXP 0722-w4-2) ← rc_207 — +9% vs rc_207",
        "rc_213 (07-22, EXP 0722-w1-1) ← rc_184 — +5% vs rc_184",
    ]


async def test_val_stats_line_survives_a_multiline_rationale():
    # The record's contract is one line per promotion. `rationale` and
    # `winner_label` are agent-authored with no shape constraint, and the API
    # rejects newlines only in `overview` — so nothing downstream would catch
    # a breach, and the clamp would start cutting mid-rationale.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.record_val_stats(
        promotion("+9% mean win rate\nand a second line\n\nand a third"),
        "unmatched (claimed\nmem_2)",
        "rc_207",
    )
    content = (val_stats_record(kiln))["content"]
    assert content.count("\n") == 0
    assert content == (
        "rc_213 (07-22, unmatched (claimed mem_2)) ← rc_207 "
        "— +9% mean win rate and a second line and a third"
    )


async def test_a_multiline_winner_rc_id_breaks_neither_record():
    # `winner_rc_id` is agent-supplied and only MATCHED against the candidate
    # set, so a foreign value reaches both writes. Unsanitized it forks the
    # baseline chain into bogus lineage entries and breaks the `val` record's
    # one-line-per-promotion contract.
    kiln = FakeKiln()
    mem_id = baseline_record(
        kiln, "Baseline: run config rc_207", content="rc_207", rc_id="rc_207"
    )
    writer = make_writer(kiln)
    record = await writer.require_baseline_record()
    decision = PromotionDecision(
        winner_rc_id="rc_213\nrc_evil",
        winner_experiment_memory_id=None,
        rationale="+9%",
    )
    await writer.promote(decision, "EXP 0722-w4-2", record)
    await writer.record_val_stats(decision, "EXP 0722-w4-2", "rc_207")

    fresh = MemoryRecord.from_api(kiln.memories[mem_id])
    # One chain line, not two: the injected id cannot forge a lineage entry,
    # and `baseline_lineage` reads the line's first token as before.
    assert fresh.content == "rc_213 rc_evil\nrc_207"
    assert writer.baseline_lineage(fresh) == ["rc_213", "rc_207"]
    assert kiln.memories[mem_id]["tags"] == ["baseline", "rc_rc_213 rc_evil"]
    # And one line per promotion in the `val` record.
    assert val_stats_record(kiln)["content"].count("\n") == 0


async def test_val_stats_overview_carries_no_numbers():
    # A `tags=val` LISTING returns overviews only, so the record can be
    # located without the listing itself carrying val metrics.
    kiln = FakeKiln()
    await make_writer(kiln).record_val_stats(promotion(), "EXP 0722-w4-2", "rc_207")
    overview = (val_stats_record(kiln))["overview"]
    assert not any(char.isdigit() for char in overview)


async def test_val_stats_content_survives_an_unbounded_rationale():
    # `rationale` is agent-authored and unbounded; it rides in content,
    # where the cap and `_fit_content` handle it (the server 422s over-cap
    # writes, so an unclamped write would fail the promotion).
    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.record_val_stats(promotion("x" * 6000), "EXP 0722-w4-2", "rc_207")
    content = (val_stats_record(kiln))["content"]
    assert len(content) <= CONTENT_MAX_CHARS
    assert content.startswith("rc_213 (07-22, EXP 0722-w4-2) ← rc_207 — xxx")


# ---- baseline seeding ----


async def test_find_baseline_record_returns_none_when_absent():
    kiln = FakeKiln()
    assert await make_writer(kiln).find_baseline_record() is None


async def test_find_baseline_record_returns_a_summary_without_hydrating():
    kiln = FakeKiln()
    mem_id = baseline_record(kiln, "Baseline: seeded at onboarding", "rc_9", "rc_9")
    found = await make_writer(kiln).find_baseline_record()
    assert isinstance(found, MemorySummary)
    assert found.id == mem_id
    # An existence check hydrates nothing (the warm-run read budget) —
    # require_baseline_record does that a moment later.
    assert not [c for c in kiln.calls if "/by_ids" in c[1]]


async def test_seed_baseline_task_default_format():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.seed_baseline(
        "424242035642", rule=BASELINE_RULE_TASK_DEFAULT, rc_count=12
    )
    record = kiln.memories[mem_id]
    assert record["overview"] == (
        "Baseline: run config 424242035642 — seeded 07-22 by kiln-harness-tuner setup "
        "(task default run config)"
    )
    # The chain starts here — one rc id per line, newest first.
    assert record["content"] == "424242035642"
    assert record["tags"] == ["baseline", "rc_424242035642"]
    assert record["scope"] == "task::t1"


async def test_seed_baseline_most_recent_provenance_names_the_count():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.seed_baseline(
        "424242035642", rule=BASELINE_RULE_MOST_RECENT, rc_count=12
    )
    assert kiln.memories[mem_id]["overview"] == (
        "Baseline: run config 424242035642 — seeded 07-22 by kiln-harness-tuner setup "
        "(most recent of 12 run configs; no default set)"
    )


@pytest.mark.parametrize(
    "rule", [BASELINE_RULE_TASK_DEFAULT, BASELINE_RULE_MOST_RECENT]
)
async def test_seed_baseline_round_trips_through_the_loops_own_read(rule):
    # The contract between setup.py and the loop: what setup writes, the
    # loop's own read resolves to the same rc id — and to a lineage of one,
    # so wave 1 screens against nothing.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.seed_baseline("424242035642", rule=rule, rc_count=12)
    record = await writer.require_baseline_record()
    assert writer.baseline_rc_id(record) == "424242035642"
    assert writer.baseline_lineage(record) == ["424242035642"]


async def test_seed_baseline_rejects_an_unknown_rule():
    writer = make_writer(FakeKiln())
    with pytest.raises(ValueError, match="unknown baseline seeding rule"):
        await writer.seed_baseline("rc_1", rule="survey", rc_count=1)


# ---- issue lifecycle ----


async def test_mark_fixed_prefix_and_citation_line():
    kiln = FakeKiln()
    issue = kiln.add_memory("the client drops 429 retries", "task::t1", ["issue", "p1"])
    writer = make_writer(kiln)
    await writer.mark_fixed(issue, "rc_213, EXP 0722-w4-2")
    record = kiln.memories[issue]
    assert record["overview"] == "FIXED: the client drops 429 retries"
    assert record["content"] == "- fixed 07-22 by rc_213, EXP 0722-w4-2"
    assert record["tags"] == ["issue", "p1"]  # tags untouched


async def test_flag_regressed_reopens_closed_issue():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: the client drops 429 retries",
        "task::t1",
        ["issue"],
        content="- fixed 07-15 by rc_207, EXP 0715-w3-1",
    )
    writer = make_writer(kiln)
    await writer.flag_regressed(issue, "rc_213")
    record = kiln.memories[issue]
    assert record["overview"] == "the client drops 429 retries"  # prefix stripped
    assert record["content"] == (
        "- fixed 07-15 by rc_207, EXP 0715-w3-1\n"
        "- reopened when promoting baseline rc_213. Issue repros on rc_213."
    )


async def test_flag_regressed_open_issue_gets_repro_line_only():
    kiln = FakeKiln()
    issue = kiln.add_memory("the client drops 429 retries", "task::t1", ["issue"])
    writer = make_writer(kiln)
    await writer.flag_regressed(issue, "rc_213")
    record = kiln.memories[issue]
    assert record["overview"] == "the client drops 429 retries"
    # Metric-free AND prose-free by construction: only the code-composed
    # line ever lands on the train-side record.
    assert record["content"] == "- repros on baseline rc_213 (noted at promotion)."


async def test_mark_partial_frozen_format():
    # Landed ∧ regressed → ONE merged metric-free line;
    # the issue stays open (overview + tags untouched).
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "the client drops 429 retries",
        "task::t1",
        ["issue", "p1"],
        content="seen in trace 42",
    )
    writer = make_writer(kiln)
    await writer.mark_partial(issue, "rc_213")
    record = kiln.memories[issue]
    assert record["overview"] == "the client drops 429 retries"
    assert record["content"] == (
        "seen in trace 42\n"
        "- landed in merge rc_213 — reduced, not closed; still repros."
    )
    assert record["tags"] == ["issue", "p1"]


async def test_mark_partial_strips_stale_fixed_prefix():
    # An issue FIXED by an earlier promotion, re-targeted this run: "still
    # repros" and a FIXED: overview cannot coexist — same reopen semantics
    # as flag_regressed.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: the client drops 429 retries",
        "task::t1",
        ["issue"],
        content="- fixed 07-15 by rc_207, EXP 0715-w3-1",
    )
    writer = make_writer(kiln)
    await writer.mark_partial(issue, "rc_213")
    record = kiln.memories[issue]
    assert record["overview"] == "the client drops 429 retries"
    assert record["content"] == (
        "- fixed 07-15 by rc_207, EXP 0715-w3-1\n"
        "- landed in merge rc_213 — reduced, not closed; still repros."
    )


async def test_mark_wont_fix():
    kiln = FakeKiln()
    issue = kiln.add_memory("phantom timeout issue", "task::t1", ["issue"])
    writer = make_writer(kiln)
    await writer.mark_wont_fix(issue, "not reproduced on rc_base (n=12)")
    record = kiln.memories[issue]
    assert record["overview"] == "WONT_FIX: phantom timeout issue"
    assert record["content"] == "- WONT_FIX (07-22): not reproduced on rc_base (n=12)"


async def test_mark_wont_fix_refuses_a_closed_issue():
    # The loop's OTHER WONT_FIX: writer, held to the same
    # ISSUE_TERMINAL_STATES doctrine as record_baseline_measurement:
    # _set_state_prefix strips the whole prefix run, so an eval_only item
    # whose eval-build agent cannot reproduce a FIXED: issue would relabel a
    # landed fix as a decision and lose the citing rc.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: phantom timeout issue",
        "task::t1",
        ["issue"],
        content="- fixed 07-20 by rc_207, EXP 0720-w3-1",
    )
    with pytest.raises(IssueTerminalStateError, match="already fixed"):
        await make_writer(kiln).mark_wont_fix(issue, "not reproduced on rc_base")
    assert kiln.memory_patches == []
    record = kiln.memories[issue]
    assert record["overview"] == "FIXED: phantom timeout issue"
    assert record["content"] == "- fixed 07-20 by rc_207, EXP 0720-w3-1"


async def test_mark_wont_fix_is_idempotent_on_an_already_wont_fix_issue():
    kiln = FakeKiln()
    issue = kiln.add_memory("WONT_FIX: phantom timeout issue", "task::t1", ["issue"])
    with pytest.raises(IssueTerminalStateError, match="already wont_fix"):
        await make_writer(kiln).mark_wont_fix(issue, "not reproduced again")
    assert kiln.memory_patches == []


async def test_mark_wont_fix_still_closes_an_unmeasured_issue():
    # The seeded state is NOT terminal — the common path must keep working.
    kiln = FakeKiln()
    issue = kiln.add_memory("UNMEASURED: issue: drops retries", "task::t1", ["issue"])
    await make_writer(kiln).mark_wont_fix(issue, "not reproduced on rc_base (n=12)")
    assert kiln.memories[issue]["overview"] == "WONT_FIX: issue: drops retries"


async def test_link_eval_appends_line():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "the client drops 429 retries",
        "task::t1",
        ["issue"],
        content="seen in trace 42",
    )
    writer = make_writer(kiln)
    rep = EvalBuildReport(
        eval_id="eval_9",
        eval_config_id="ec_9",
        eval_type="llm_judge",
        case_count=20,
        reproduced=True,
        baseline_headline="62% pass",
        priority="p1",
    )
    await writer.link_eval(issue, rep)
    assert kiln.memories[issue]["content"] == (
        "seen in trace 42\n- eval: eval_9 (built 07-22, reproduced)"
    )


# ---- open unknowns ----


async def test_record_open_unknown_posts_then_patches_on_exact_question():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    q = OpenUnknown(
        question="do API docs exist for the graphql endpoint",
        default_assumed="no docs; introspect instead",
        gates="tool-building experiments",
        reask_when="re-ask when the vendor answers the docs ticket",
    )
    first_id = await writer.record_open_unknown(q)
    record = kiln.memories[first_id]
    assert record["overview"] == "OPEN: do API docs exist for the graphql endpoint"
    assert record["scope"] == "project"
    assert record["tags"] == ["constraint"]
    assert record["content"] == (
        "question: do API docs exist for the graphql endpoint\n"
        "default: no docs; introspect instead\n"
        "gates: tool-building experiments\n"
        "source: assumption\n"
        "re-ask when: re-ask when the vendor answers the docs ticket"
    )
    # Same question again → PATCH the existing record, never a duplicate.
    q2 = q.model_copy(update={"default_assumed": "docs exist but stale"})
    second_id = await writer.record_open_unknown(q2)
    assert second_id == first_id
    assert len(kiln.memory_posts) == 1
    assert "default: docs exist but stale" in kiln.memories[first_id]["content"]


def open_unknown(question: str, **updates) -> OpenUnknown:
    fields = dict(
        question=question,
        default_assumed="assume no",
        gates="tooling experiments",
        reask_when="when the vendor answers",
    )
    fields.update(updates)
    return OpenUnknown(**fields)


async def test_open_unknown_dedup_normalizes_whitespace_and_case():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    first_id = await writer.record_open_unknown(
        open_unknown("do API docs exist for the graphql endpoint")
    )
    jittered = open_unknown(
        "  Do  API docs EXIST \t for the graphql endpoint ",
        default_assumed="probably not",
    )
    assert await writer.record_open_unknown(jittered) == first_id
    assert len(kiln.memory_posts) == 1
    assert "default: probably not" in kiln.memories[first_id]["content"]


async def test_open_unknown_dedup_survives_truncated_overview():
    # A >280-char question clamps the
    # overview with `…`, so overview-equality matching forked a duplicate
    # record on every re-ask. The content question line is never clamped
    # at these sizes — the normalized-content key matches.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    long_question = "does the scheduler honor per-site quiet hours " + "x" * 300
    first_id = await writer.record_open_unknown(open_unknown(long_question))
    overview = kiln.memories[first_id]["overview"]
    assert len(overview) == OVERVIEW_MAX_CHARS
    assert overview.endswith("…")  # clamped — overview equality would fail
    second = open_unknown(long_question, default_assumed="site-level only")
    assert await writer.record_open_unknown(second) == first_id
    assert len(kiln.memory_posts) == 1
    assert "default: site-level only" in kiln.memories[first_id]["content"]


async def test_open_unknown_batch_hydrates_once_and_skips_shapeless_records():
    # Hand-authored constraint records without the frozen `question:`
    # content shape never match (a fresh POST beats a wrong PATCH) and
    # never crash the batch; the whole batch pays one listing + one
    # by_ids hydration.
    kiln = FakeKiln()
    kiln.add_memory("OPEN: legacy constraint", "project", ["constraint"])
    kiln.add_memory(
        "OPEN: another legacy", "project", ["constraint"], content="free prose"
    )
    writer = make_writer(kiln)
    kiln.calls.clear()
    ids = await writer.write_open_unknowns(
        [open_unknown("legacy constraint"), open_unknown("q two")]
    )
    assert len(kiln.memory_posts) == 2  # neither matched the shapeless rows
    assert len(set(ids)) == 2
    assert len([c for c in kiln.calls if "/by_ids" in c[1]]) == 1
    list_calls = [c for c in kiln.calls if c[0] == "GET" and c[1].endswith("/memories")]
    assert len(list_calls) == 1


async def test_open_unknown_within_batch_dedup():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    ids = await writer.write_open_unknowns(
        [
            open_unknown("do API docs exist"),
            open_unknown("DO api DOCS  exist", default_assumed="yes, stale"),
        ]
    )
    assert ids[0] == ids[1]
    assert len(kiln.memory_posts) == 1
    assert "default: yes, stale" in kiln.memories[ids[0]]["content"]


async def test_open_unknown_multiline_question_stored_single_line_and_dedups():
    # Questions are agent-authored, so embedded newlines are a
    # realistic shape. The write boundary canonicalizes to ONE line — the
    # frozen 5-line content contract holds, and the stored `question:` line
    # keys identically to the incoming multiline form on re-ask.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    multiline = "does the scheduler honor\nper-site quiet hours"
    first_id = await writer.record_open_unknown(open_unknown(multiline))
    content = kiln.memories[first_id]["content"]
    assert content.splitlines()[0] == (
        "question: does the scheduler honor per-site quiet hours"
    )
    assert len(content.splitlines()) == 5  # contract intact
    assert kiln.memories[first_id]["overview"] == (
        "OPEN: does the scheduler honor per-site quiet hours"
    )
    second = open_unknown(multiline, default_assumed="site-level only")
    assert await writer.record_open_unknown(second) == first_id
    # ...and the single-line form matches the multiline original too.
    third = open_unknown(
        "does the scheduler honor per-site quiet hours", default_assumed="v3"
    )
    assert await writer.record_open_unknown(third) == first_id
    assert len(kiln.memory_posts) == 1
    assert "default: v3" in kiln.memories[first_id]["content"]


async def test_open_unknown_oversize_question_keeps_its_dedup_key():
    # A question past ~2,400 chars must not lose its `question:` line
    # to the content clamp's head region, or dedup is gone for that record.
    # The canonical bound (2,000) keeps the line whole even when the OTHER
    # content lines push the record over the 4,000 cap.
    kiln = FakeKiln()
    writer = make_writer(kiln)
    q = open_unknown("does the scheduler " + "x" * 4200, default_assumed="y" * 3000)
    first_id = await writer.record_open_unknown(q)
    content = kiln.memories[first_id]["content"]
    assert len(content) <= CONTENT_MAX_CHARS  # other lines forced a clamp
    question_line = content.splitlines()[0]
    assert question_line.startswith("question: does the scheduler xxx")
    assert question_line.endswith("…")  # bounded at the write boundary
    assert len(question_line) == len("question: ") + 2000
    second = q.model_copy(update={"default_assumed": "short now"})
    assert await writer.record_open_unknown(second) == first_id
    assert len(kiln.memory_posts) == 1
    assert "default: short now" in kiln.memories[first_id]["content"]


async def test_get_many_chunks_by_ids_requests():
    # /by_ids ids ride the query string — hydration chunks at
    # 50 so a large constraint store never composes an over-long URL.
    kiln = FakeKiln()
    ids = [kiln.add_memory(f"mem {i}", "project", ["constraint"]) for i in range(120)]
    writer = make_writer(kiln)
    records = await writer._get_many(ids)
    assert [r.id for r in records] == ids  # order preserved across chunks
    by_ids_calls = [c for c in kiln.calls if "/by_ids" in c[1]]
    assert [len(c[2]["ids"]) for c in by_ids_calls] == [50, 50, 20]


# ---- the setup/loop content splice + the issue writers ----


HANDOFF = EvalView(
    id="424242966189",
    name="Handoff Block",
    current_config_id="424242617422",
    train_filter="tag::train_handoff",
    val_filter="tag::val_handoff",
    config_properties={"type": "code_eval", "code": "def check(): ..."},
    spec=SpecView(
        id="424242331708",
        status="active",
        priority=1,
        definition="## Desired Behaviour\nend with <handoff>",
        issue_description=None,
    ),
)
DESCRIPTION = EvalDescription(
    overview="replies do not end with the required <handoff> XML block",
    description="Every reply must end with a <handoff> block using <item> tags.",
)


def seeded_issue(kiln: FakeKiln, tail: str = "", *, head: str = "described") -> str:
    """An issue record already carrying a setup head + the boundary."""
    return kiln.add_memory(
        "UNMEASURED: issue: replies drop the block",
        "task::t1",
        ["issue", "p1", "eval_424242966189"],
        content=join_owned(f"{head}\nstatus: unmeasured · fp: deadbeef", tail),
    )


def test_split_owned_without_delimiter_is_all_loop_owned():
    assert split_owned("- eval: 1 (built)\n- fixed") == (
        None,
        "- eval: 1 (built)\n- fixed",
    )
    assert split_owned(None) == (None, "")
    assert split_owned("") == (None, "")


def test_split_owned_preserves_the_tail_byte_for_byte():
    tail = "- eval: 424242005456 (built 07-30)\n\n  indented finding  \n- fixed 08-01"
    head, split_tail = split_owned(join_owned("head line", tail))
    assert head == "head line"
    assert split_tail == tail  # equality, not containment


def test_join_owned_round_trips():
    joined = join_owned("head", "tail")
    assert joined.splitlines() == ["head", SETUP_DELIMITER, "tail"]
    assert join_owned(*(p or "" for p in split_owned(joined))) == joined
    # An empty loop region still carries the boundary — it exists from birth.
    assert join_owned("head", "") == f"head\n{SETUP_DELIMITER}"


def test_split_owned_ignores_a_mid_line_delimiter():
    # Agent prose quoting the sentence must not be read as the boundary.
    quoted = f'the record said "{SETUP_DELIMITER}" which is not a boundary'
    assert split_owned(quoted) == (None, quoted)


def test_setup_budgets_fit_the_content_cap():
    assert (
        SETUP_HEAD_MAX_CHARS + SETUP_TAIL_RESERVE_CHARS + len(SETUP_DELIMITER) + 2
        <= CONTENT_MAX_CHARS
    )


def test_fit_content_protects_the_delimiter():
    head = "described\nstatus: unmeasured · fp: deadbeef"
    tail = "\n".join(f"- loop line {i:02d} " + "x" * 60 for i in range(60))
    content = join_owned(head, tail)
    assert len(content) > CONTENT_MAX_CHARS

    fitted = _fit_content(content, SETUP_DELIMITER)

    assert len(fitted) <= CONTENT_MAX_CHARS
    fitted_head, fitted_tail = split_owned(fitted)
    assert fitted_head == head  # the whole setup head survives
    assert SETUP_DELIMITER in fitted.splitlines()
    lines = fitted_tail.splitlines()
    assert lines[0] == "[… earlier history truncated …]"
    assert lines[-1] == f"- loop line 59 {'x' * 60}"  # newest kept
    assert "- loop line 00 " not in fitted_tail  # oldest dropped
    # The loop keeps its reserve even at the cap.
    assert len(fitted_tail) >= SETUP_TAIL_RESERVE_CHARS - len(SETUP_DELIMITER)


def test_fit_content_ignores_an_absent_protected_line():
    # A non-issue record clamps exactly as before — head + tail regions
    # around the plain truncation marker, no history marker.
    content = "\n".join(f"line {i} " + "y" * 60 for i in range(100))
    fitted = _fit_content(content, SETUP_DELIMITER)
    assert "[… truncated …]" in fitted
    assert "earlier history truncated" not in fitted


async def test_append_content_keeps_the_setup_head_alive():
    # The regression this guards: _fit_content_keep_tail drops OLDEST-first,
    # which on an issue record means the head and the delimiter.
    kiln = FakeKiln()
    tail = "\n".join(f"- loop line {i} " + "z" * 60 for i in range(50))
    issue = seeded_issue(kiln, tail)
    writer = make_writer(kiln)

    await writer.mark_fixed(issue, "rc_213, EXP 0722-w4-2")

    content = kiln.memories[issue]["content"]
    assert len(content) <= CONTENT_MAX_CHARS
    head, new_tail = split_owned(content)
    assert head is not None and "status: unmeasured · fp: deadbeef" in head
    assert new_tail.endswith("- fixed 07-22 by rc_213, EXP 0722-w4-2")
    assert kiln.memories[issue]["overview"] == "FIXED: issue: replies drop the block"


# ---- the state-prefix vocabulary ----


def test_issue_state_reads_every_prefix():
    assert issue_state("UNMEASURED: issue: x") == "unmeasured"
    assert issue_state("UNMEASURABLE: issue: x") == "unmeasurable"
    assert issue_state("FIXED: issue: x") == "fixed"
    assert issue_state("WONT_FIX: issue: x") == "wont_fix"
    assert issue_state("issue: x") == "open"


def test_set_state_prefix_collapses_stacked_prefixes():
    assert set_state_prefix("FIXED: UNMEASURED: issue: x", IssueState.WONT_FIX) == (
        "WONT_FIX: issue: x"
    )
    assert set_state_prefix("UNMEASURED: issue: x") == "issue: x"


async def test_mark_fixed_replaces_an_unmeasured_prefix():
    kiln = FakeKiln()
    issue = kiln.add_memory("UNMEASURED: issue: drops retries", "task::t1", ["issue"])
    await make_writer(kiln).mark_fixed(issue, "rc_213, EXP 0722-w4-2")
    assert kiln.memories[issue]["overview"] == "FIXED: issue: drops retries"


async def test_flag_regressed_keeps_an_unmeasured_prefix():
    # A promotion-time repro line is not a train-side measurement — only
    # record_baseline_measurement clears UNMEASURED:.
    kiln = FakeKiln()
    issue = kiln.add_memory("UNMEASURED: issue: drops retries", "task::t1", ["issue"])
    await make_writer(kiln).flag_regressed(issue, "rc_213")
    assert kiln.memories[issue]["overview"] == "UNMEASURED: issue: drops retries"


# ---- the binding tag ----


def test_eval_ids_from_tags_reads_only_id_shaped_tags():
    assert eval_ids_from_tags(
        ["issue", "p1", "eval_424242966189", "eval_424242005456", "eval_lane", "evals"]
    ) == frozenset({"424242966189", "424242005456"})
    assert eval_tag("424242966189") == "eval_424242966189"


def test_stored_fingerprint_reads_the_head_only():
    content = join_owned("d\nstatus: unmeasured · fp: a1b2c3d4 · synced 2026-07-31", "")
    assert stored_fingerprint(content) == "a1b2c3d4"
    # A loop line quoting an fp never answers for setup's.
    assert stored_fingerprint(join_owned("d\nstatus: x", "- fp: ffffffff")) is None
    assert stored_fingerprint("- fp: ffffffff") is None
    assert stored_fingerprint(None) is None


# ---- seed / refresh / bind / flag / measure ----


async def test_seed_issue_format():
    kiln = FakeKiln()
    writer = make_writer(kiln)

    memory_id = await writer.seed_issue(HANDOFF, DESCRIPTION, "a1b2c3d4")

    (post,) = kiln.memory_posts
    assert post["overview"] == (
        "UNMEASURED: issue: replies do not end with the required <handoff> XML block"
    )
    assert post["tags"] == ["issue", "p1", "eval_424242966189"]
    assert post["scope"] == "task::t1"
    assert post["content"].splitlines() == [
        "Every reply must end with a <handoff> block using <item> tags.",
        "eval: 424242966189 (Handoff Block, code_eval)",
        "priority: p1 (source: spec 424242331708)",
        "status: unmeasured at sync · fp: a1b2c3d4 · synced 2026-07-22",
        SETUP_DELIMITER,
    ]
    assert kiln.memories[memory_id]["content"] == post["content"]


async def test_seed_issue_without_a_spec_uses_the_default_priority():
    kiln = FakeKiln()
    view = replace(HANDOFF, spec=None, config_properties=None)
    await make_writer(kiln).seed_issue(view, DESCRIPTION, "a1b2c3d4")
    (post,) = kiln.memory_posts
    assert "priority: p1 (source: default)" in post["content"]
    assert "eval: 424242966189 (Handoff Block, unknown)" in post["content"]
    assert post["tags"] == ["issue", "p1", "eval_424242966189"]


async def test_seed_issue_clamps_a_long_description_to_the_head_budget():
    kiln = FakeKiln()
    long_description = DESCRIPTION.model_copy(update={"description": "d" * 4000})
    await make_writer(kiln).seed_issue(HANDOFF, long_description, "a1b2c3d4")
    head, tail = split_owned(kiln.memory_posts[0]["content"])
    assert len(head) <= SETUP_HEAD_MAX_CHARS
    assert head.splitlines()[0].endswith("…")
    assert head.endswith("· synced 2026-07-22")  # metadata never clamped away
    assert tail == ""


async def test_refresh_issue_preserves_the_loop_tail_byte_for_byte():
    kiln = FakeKiln()
    tail = "- eval: 424242966189 (built 07-30, reproduced)\n- repros on rc_171."
    issue = seeded_issue(kiln, tail, head="stale description")
    writer = make_writer(kiln)

    await writer.refresh_issue(issue, HANDOFF, DESCRIPTION, "ffffffff")

    head, new_tail = split_owned(kiln.memories[issue]["content"])
    assert new_tail == (f"{tail}\n- eval rubric changed; description resynced 07-22")
    assert head.splitlines() == [
        "Every reply must end with a <handoff> block using <item> tags.",
        "eval: 424242966189 (Handoff Block, code_eval)",
        "priority: p1 (source: spec 424242331708)",
        "status: unmeasured at sync · fp: ffffffff · synced 2026-07-22",
    ]
    assert kiln.memories[issue]["overview"] == (
        "UNMEASURED: issue: replies do not end with the required <handoff> XML block"
    )


async def test_refresh_issue_replaces_the_priority_tag_in_place():
    kiln = FakeKiln()
    issue = seeded_issue(kiln)
    view = replace(HANDOFF, spec=replace(HANDOFF.spec, priority=0))
    await make_writer(kiln).refresh_issue(issue, view, DESCRIPTION, "ffffffff")
    assert kiln.memories[issue]["tags"] == ["issue", "p0", "eval_424242966189"]


async def test_refresh_issue_keeps_the_state_prefix():
    # A rewritten rubric must not reopen a closed issue.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: issue: replies drop the block",
        "task::t1",
        ["issue", "p1", "eval_424242966189"],
        content=join_owned("stale\nstatus: fixed · fp: deadbeef", "- fixed 07-20"),
    )
    await make_writer(kiln).refresh_issue(issue, HANDOFF, DESCRIPTION, "ffffffff")
    record = kiln.memories[issue]
    assert record["overview"].startswith("FIXED: issue: replies do not end")
    assert "status: fixed at sync · fp: ffffffff" in record["content"]


async def test_refresh_issue_on_a_record_with_no_head_prepends_one():
    kiln = FakeKiln()
    legacy = "found while analyzing EXP 0731-w1-1\n- eval: 424242966189 (built 07-30)"
    issue = kiln.add_memory(
        "issue: replies drop the block",
        "task::t1",
        ["issue", "p2", "eval_424242966189"],
        content=legacy,
    )
    await make_writer(kiln).refresh_issue(issue, HANDOFF, DESCRIPTION, "ffffffff")
    head, tail = split_owned(kiln.memories[issue]["content"])
    assert head is not None
    assert tail.startswith(legacy)  # loop prose untouched


async def test_bind_eval_adds_the_tag_only():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "issue: quantities do not match the manifest",
        "task::t1",
        ["issue", "p1"],
        content="- eval: 424242005456 (built 07-30, reproduced)",
    )
    await make_writer(kiln).bind_eval(issue, "424242005456", add_line=False)
    record = kiln.memories[issue]
    assert record["tags"] == ["issue", "p1", "eval_424242005456"]
    assert record["content"] == "- eval: 424242005456 (built 07-30, reproduced)"


async def test_bind_eval_also_appends_the_missing_line():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "issue: bare & breaks XML parsing",
        "task::t1",
        ["issue", "p2"],
        content="found while analyzing EXP 0731-w1-1 (eval 424242966189 Handoff Block)",
    )
    await make_writer(kiln).bind_eval(issue, "424242966189", add_line=True)
    record = kiln.memories[issue]
    assert record["tags"] == ["issue", "p2", "eval_424242966189"]
    assert record["content"].endswith(
        "\n- eval: 424242966189 (linked by kiln-harness-tuner setup 07-22)"
    )


async def test_bind_eval_is_idempotent_on_the_tag():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "issue: x", "task::t1", ["issue", "eval_424242966189", "p2"]
    )
    await make_writer(kiln).bind_eval(issue, "424242966189", add_line=False)
    assert kiln.memories[issue]["tags"] == ["issue", "eval_424242966189", "p2"]


async def test_bind_eval_backfills_a_missing_priority_tag():
    # A repaired issue is never setup-owned, so refresh_issue never touches
    # it — this is its only chance at making sure a priority tag is
    # always present. Live records have been found tagged just ["issue"].
    kiln = FakeKiln()
    issue = kiln.add_memory("issue: x", "task::t1", ["issue"])
    await make_writer(kiln).bind_eval(issue, "424242966189", add_line=False)
    assert kiln.memories[issue]["tags"] == ["issue", "p1", "eval_424242966189"]


async def test_flag_unmeasurable_prefixes_and_appends_the_cause():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "issue: quantities do not match the manifest",
        "task::t1",
        ["issue", "p1", "eval_424242005456"],
        content="seen in trace 42",
    )
    await make_writer(kiln).flag_unmeasurable(issue, "eval 424242005456 deleted")
    record = kiln.memories[issue]
    assert (
        record["overview"]
        == "UNMEASURABLE: issue: quantities do not match the manifest"
    )
    assert record["content"] == (
        "seen in trace 42\n- eval 424242005456 deleted (noted 07-22)"
    )


async def test_record_baseline_measurement_reproduced_clears_unmeasured():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "UNMEASURED: issue: drops retries", "task::t1", ["issue", "eval_424242005456"]
    )
    await make_writer(kiln).record_baseline_measurement(
        issue,
        reproduced=True,
        rc_id="rc_base",
        summary="0.35, n=23",
        expected_eval_id="424242005456",
    )
    record = kiln.memories[issue]
    assert record["overview"] == "issue: drops retries"
    assert record["content"] == ("- reproduced on baseline rc_base 07-22 (0.35, n=23)")


async def test_record_baseline_measurement_not_reproduced_is_wont_fix():
    kiln = FakeKiln()
    issue = kiln.add_memory("UNMEASURED: issue: drops retries", "task::t1", ["issue"])
    await make_writer(kiln).record_baseline_measurement(
        issue,
        reproduced=False,
        rc_id="rc_base",
        summary="1.0, n=23",
        expected_eval_id="424242005456",
    )
    record = kiln.memories[issue]
    assert record["overview"] == "WONT_FIX: issue: drops retries"
    assert record["content"] == (
        "- not reproduced on baseline rc_base 07-22 (1.0, n=23)"
    )


async def test_record_baseline_measurement_refuses_a_foreign_eval():
    # The planner supplies the issue id and the eval id
    # independently, and the binding tag makes the pair machine-checkable —
    # so a mismatch never stamps terminal state from another eval's numbers.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "UNMEASURED: issue: drops retries", "task::t1", ["issue", "eval_424242005456"]
    )
    with pytest.raises(IssueBindingMismatchError, match="424242005456"):
        await make_writer(kiln).record_baseline_measurement(
            issue,
            reproduced=False,
            rc_id="rc_base",
            summary="1.0, n=23",
            expected_eval_id="424242966189",
        )
    assert kiln.memory_patches == []
    assert kiln.memories[issue]["overview"].startswith("UNMEASURED: ")


async def test_record_baseline_measurement_refuses_a_closed_issue():
    # ISSUE_TERMINAL_STATES' doctrine ("never un-closes one"), applied to the
    # one lifecycle writer that would otherwise bypass it: _set_state_prefix
    # strips whatever is there, so measuring a FIXED: issue would rewrite it
    # to WONT_FIX: — mislabelling a landed fix as a decision and losing which
    # rc fixed it.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: issue: drops retries",
        "task::t1",
        ["issue"],
        content="- fixed 07-20 by rc_207, EXP 0720-w3-1",
    )
    with pytest.raises(IssueTerminalStateError, match="already fixed"):
        await make_writer(kiln).record_baseline_measurement(
            issue,
            reproduced=False,
            rc_id="rc_base",
            summary="1, n=23/23",
            expected_eval_id="424242005456",
        )
    assert kiln.memory_patches == []
    record = kiln.memories[issue]
    assert record["overview"] == "FIXED: issue: drops retries"
    assert record["content"] == "- fixed 07-20 by rc_207, EXP 0720-w3-1"


async def test_record_baseline_measurement_refuses_a_wont_fix_issue():
    kiln = FakeKiln()
    issue = kiln.add_memory("WONT_FIX: issue: drops retries", "task::t1", ["issue"])
    with pytest.raises(IssueTerminalStateError, match="already wont_fix"):
        await make_writer(kiln).record_baseline_measurement(
            issue,
            reproduced=True,
            rc_id="rc_base",
            summary="0.35, n=23/23",
            expected_eval_id="424242005456",
        )
    assert kiln.memory_patches == []


async def test_record_baseline_measurement_allows_an_unmeasurable_issue():
    # UNMEASURABLE: is a status, not a decision — remeasuring one is fine.
    kiln = FakeKiln()
    issue = kiln.add_memory("UNMEASURABLE: issue: drops retries", "task::t1", ["issue"])
    await make_writer(kiln).record_baseline_measurement(
        issue,
        reproduced=True,
        rc_id="rc_base",
        summary="0.35, n=23/23",
        expected_eval_id="424242005456",
    )
    assert kiln.memories[issue]["overview"] == "issue: drops retries"


async def test_record_baseline_measurement_allows_a_second_binding():
    # An issue MAY bind several evals — any of them may
    # measure it.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "UNMEASURED: issue: drops retries",
        "task::t1",
        ["issue", "eval_424242005456", "eval_424242966189"],
    )
    await make_writer(kiln).record_baseline_measurement(
        issue,
        reproduced=True,
        rc_id="rc_base",
        summary="0.35, n=23",
        expected_eval_id="424242966189",
    )
    assert kiln.memories[issue]["overview"] == "issue: drops retries"


async def test_link_eval_writes_the_binding_tag():
    # A loop-built eval binds exactly like a seeded one, so
    # the next startup's coverage pass never seeds a duplicate issue.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "issue: drops retries", "task::t1", ["issue", "p1"], content="seen in trace 42"
    )
    rep = EvalBuildReport(
        eval_id="424242966189",
        eval_config_id="424242617422",
        eval_type="code_eval",
        case_count=20,
        reproduced=True,
        baseline_headline="62% pass",
        priority="p1",
    )
    await make_writer(kiln).link_eval(issue, rep)
    record = kiln.memories[issue]
    assert record["tags"] == ["issue", "p1", "eval_424242966189"]
    assert record["content"].endswith("- eval: 424242966189 (built 07-22, reproduced)")


async def test_list_issues_hydrates_content():
    kiln = FakeKiln()
    issue = seeded_issue(kiln, "- fixed 07-20")
    kiln.add_memory("EXP 0722-w1-1 — RUNNING", "task::t1", ["experiment"])
    records = await make_writer(kiln).list_issues()
    assert [r.id for r in records] == [issue]
    assert records[0].content is not None


async def test_list_issues_on_a_cold_project_never_hydrates():
    kiln = FakeKiln()
    assert await make_writer(kiln).list_issues() == []
    assert not [c for c in kiln.calls if "/by_ids" in c[1]]


# ---- state prefixes, multiline overviews, description delimiters ----


async def test_flag_regressed_keeps_an_unmeasured_prefix_stacked_behind_fixed():
    # The OLD mark_fixed wrote `FIXED: UNMEASURED: …`, so that shape exists
    # in live stores. Exactly ONE prefix is cleared here — clearing both
    # would report an unmeasured issue to the planner as measured.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: UNMEASURED: issue: drops retries", "task::t1", ["issue"]
    )
    await make_writer(kiln).flag_regressed(issue, "rc_213")
    assert kiln.memories[issue]["overview"] == "UNMEASURED: issue: drops retries"


async def test_mark_partial_keeps_an_unmeasured_prefix_stacked_behind_fixed():
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: UNMEASURED: issue: drops retries", "task::t1", ["issue"]
    )
    await make_writer(kiln).mark_partial(issue, "rc_213")
    assert kiln.memories[issue]["overview"] == "UNMEASURED: issue: drops retries"


async def test_seed_issue_collapses_a_multiline_agent_overview():
    # The API's overview contract is "no newlines" — a stray one is a fatal
    # 422 on the cold-start path, AFTER the describe fan-out was paid for.
    # FakeKiln enforces it, so this fails loudly if the collapse is lost.
    kiln = FakeKiln()
    multiline = DESCRIPTION.model_copy(
        update={"overview": "replies drop the block\n\n  and trail emojis  "}
    )
    await make_writer(kiln).seed_issue(HANDOFF, multiline, "a1b2c3d4")
    assert kiln.memory_posts[0]["overview"] == (
        "UNMEASURED: issue: replies drop the block and trail emojis"
    )


async def test_refresh_issue_collapses_a_multiline_agent_overview():
    kiln = FakeKiln()
    issue = seeded_issue(kiln)
    multiline = DESCRIPTION.model_copy(update={"overview": "line one\nline two"})
    await make_writer(kiln).refresh_issue(issue, HANDOFF, multiline, "ffffffff")
    assert kiln.memories[issue]["overview"] == ("UNMEASURED: issue: line one line two")


async def test_record_verdict_collapses_a_multiline_agent_overview():
    # _fit_overview is the single funnel, so EVERY agent-authored overview is
    # covered — not just setup's.
    kiln = FakeKiln()
    exp = open_running(kiln)
    await make_writer(kiln).record_verdict(
        exp,
        make_verdict_result("result: +18%").model_copy(
            update={"memory_overview": "EXP 0722-w1-1: retry-skill\n— +18%, n=2"}
        ),
    )
    assert "\n" not in kiln.memories[exp]["overview"]


async def test_seed_issue_drops_a_delimiter_line_from_the_description():
    # A description carrying the boundary sentence on its own line would hand
    # the rest of setup's OWN head to the loop region on the next read.
    kiln = FakeKiln()
    spoof = DESCRIPTION.model_copy(
        update={"description": f"real prose\n{SETUP_DELIMITER}\nmore prose"}
    )
    await make_writer(kiln).seed_issue(HANDOFF, spoof, "a1b2c3d4")
    head, tail = split_owned(kiln.memory_posts[0]["content"])
    assert head.splitlines()[:2] == ["real prose", "more prose"]
    assert tail == ""


async def test_stored_fingerprint_ignores_a_status_line_in_the_description():
    # The LAST status line wins, and _compose_setup_head always writes it
    # last — so an agent description quoting one cannot spoof the drift check.
    kiln = FakeKiln()
    spoof = DESCRIPTION.model_copy(
        update={"description": "status: unmeasured · fp: deadbeef · synced 1999-01-01"}
    )
    await make_writer(kiln).seed_issue(HANDOFF, spoof, "a1b2c3d4")
    assert stored_fingerprint(kiln.memory_posts[0]["content"]) == "a1b2c3d4"


def test_fit_content_marks_a_truncated_head():
    # Unreachable with a composed head, but a legacy record with a huge
    # pre-delimiter region must not lose its `status: … fp: …` line silently:
    # that reads back as "never synced" and buys a PAID describe every run.
    head = "\n".join(f"legacy head line {i:02d} " + "h" * 60 for i in range(60))
    fitted = _fit_content(join_owned(head, "- loop line"), SETUP_DELIMITER)
    assert len(fitted) <= CONTENT_MAX_CHARS
    fitted_head, fitted_tail = split_owned(fitted)
    assert fitted_head.endswith("[… truncated …]")
    assert fitted_tail == "- loop line"  # the loop's reserve is untouched


def test_a_max_size_composed_head_survives_the_clamp_whole():
    # The budget guarantee, asserted by construction rather than by
    # re-deriving the arithmetic: a head at exactly SETUP_HEAD_MAX_CHARS is
    # never a drop candidate, however long the loop region gets.
    head = ("h" * 79 + "\n") * (SETUP_HEAD_MAX_CHARS // 80)
    head = head.rstrip("\n")
    assert len(head) <= SETUP_HEAD_MAX_CHARS
    tail = "\n".join(f"- loop line {i:03d} " + "t" * 60 for i in range(60))
    fitted_head, _tail = split_owned(
        _fit_content(join_owned(head, tail), SETUP_DELIMITER)
    )
    assert fitted_head == head


# ---- setup head clamping ----


def test_setup_head_line_budget_leaves_room_for_a_description():
    # The per-line caps are what make SETUP_HEAD_MAX_CHARS a real guarantee
    # rather than a description-only one.
    assert 3 * SETUP_HEAD_LINE_MAX_CHARS + 2 < SETUP_HEAD_MAX_CHARS


async def test_seed_issue_clamps_an_over_long_eval_name():
    # `name` comes straight off the API. Unclamped, it pushed the head past
    # _fit_around_protected's budget, which dropped the `status: … fp: …`
    # line — read back as "never synced", so the eval refreshed and paid for
    # a describe dispatch on EVERY invocation, forever.
    kiln = FakeKiln()
    view = replace(HANDOFF, name="N" * 4200)

    await make_writer(kiln).seed_issue(view, DESCRIPTION, "a1b2c3d4")

    content = kiln.memory_posts[0]["content"]
    head, _tail = split_owned(content)
    assert len(head) <= SETUP_HEAD_MAX_CHARS
    assert head.endswith("· synced 2026-07-22")
    # The whole point: the record survives a re-read, so no refresh loop.
    assert stored_fingerprint(content) == "a1b2c3d4"
    assert _fit_content(content, SETUP_DELIMITER) == content  # already fits


async def test_seed_issue_clamps_an_over_long_config_kind_and_spec_id():
    kiln = FakeKiln()
    view = replace(
        HANDOFF,
        config_properties={"type": "T" * 4200, "code": "x"},
        spec=replace(HANDOFF.spec, id="S" * 4200),
    )

    await make_writer(kiln).seed_issue(view, DESCRIPTION, "a1b2c3d4")

    head, _tail = split_owned(kiln.memory_posts[0]["content"])
    assert len(head) <= SETUP_HEAD_MAX_CHARS
    assert all(len(line) <= SETUP_HEAD_LINE_MAX_CHARS for line in head.splitlines()[1:])
    assert stored_fingerprint(kiln.memory_posts[0]["content"]) == "a1b2c3d4"


async def test_refresh_issue_keeps_a_stacked_legacy_prefix_whole():
    # The same over-strip fixed in flag_regressed/mark_partial: re-deriving
    # the prefix from issue_state's single answer would drop UNMEASURED:.
    kiln = FakeKiln()
    issue = kiln.add_memory(
        "FIXED: UNMEASURED: issue: replies drop the block",
        "task::t1",
        ["issue", "p1", "eval_424242966189"],
        content=join_owned("stale\nstatus: fixed at sync · fp: deadbeef", "- fixed"),
    )
    await make_writer(kiln).refresh_issue(issue, HANDOFF, DESCRIPTION, "ffffffff")
    assert kiln.memories[issue]["overview"].startswith(
        "FIXED: UNMEASURED: issue: replies do not end"
    )


def test_state_prefix_run_reads_the_whole_run():
    assert state_prefix_run("FIXED: UNMEASURED: issue: x") == "FIXED: UNMEASURED: "
    assert state_prefix_run("issue: x") == ""


# ---- the committed Kiln fixtures ----
#
# `orchestrate` resolves the baseline through `baseline_rc_id` before wave 1,
# so a committed `baseline` record without an `rc_<id>` tag is not a stale
# fixture — it is a run that dies at startup. A committed record was exactly
# that after the split landed, and a reset script restored the failing shape,
# so the guard belongs in the suite rather than in a validation run.

# memory.py = <repo>/src/kiln_harness_tuner/memory.py → parents[2] is the repo root (the
# same derivation `test_agent_templates` uses for `.agents/skills`).
_KILN_PROJECTS = Path(memory_module.__file__).resolve().parents[2]

# A Kiln project checked into this repository sits under `examples/<name>/`;
# the bare root pattern covers one placed beside it.
_PROJECT_GLOBS = ("*/project.kiln", "examples/*/*/project.kiln")
_MEMORY_GLOBS = (
    "*/assistant_memory/*/memory.kiln",
    "examples/*/*/assistant_memory/*/memory.kiln",
)


def _committed_kiln_projects() -> list[Path]:
    return sorted(
        {p.parent for glob in _PROJECT_GLOBS for p in _KILN_PROJECTS.glob(glob)}
    )


def _committed_memory_stores() -> list[Path]:
    """The `assistant_memory/` directories that exist under a committed project.

    A project the loop has never run against has none: the store is created by
    the first run. So this, and not the presence of records, is what tells a
    mis-aimed glob apart from a project that has simply not been driven yet.
    """
    return sorted(
        {
            path
            for project in _committed_kiln_projects()
            for path in [project / "assistant_memory"]
            if path.is_dir()
        }
    )


def _committed_memory_records() -> list[tuple[Path, dict]]:
    paths = sorted({p for glob in _MEMORY_GLOBS for p in _KILN_PROJECTS.glob(glob)})
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]


def _committed_baseline_records() -> list[tuple[Path, dict]]:
    return [
        (path, record)
        for path, record in _committed_memory_records()
        if "baseline" in (record.get("tags") or [])
    ]


def test_every_committed_baseline_record_resolves_to_one_rc():
    # ZERO baseline records is a legitimate state, not a broken glob: a
    # reset deletes a task's memory outright, and the next run
    # seeds its own — already split-shaped — baseline record. So the glob is
    # sanity-checked against records in general, and the shape is asserted
    # over whatever baseline records happen to exist.
    # The skip is gated on the absence of a memory STORE, not on the absence
    # of records or of a PROJECT: a project can exist with no store at all.
    # "found nothing" is also what a mis-aimed glob returns, and a glob that
    # stops matching once records exist must fail rather than skip for ever.
    # The store's own directory is the thing that appears exactly when there
    # is something to find.
    if not _committed_memory_stores():
        pytest.skip(
            "no committed Kiln project carries an assistant_memory store yet,"
            " so there is nothing to check. The store is created by the first"
            " run, not by the build"
        )
    assert _committed_memory_records(), (
        "a committed Kiln project carries an assistant_memory directory but no"
        f" memory record was found under it — check the glob {_MEMORY_GLOBS}"
    )
    for path, record in _committed_baseline_records():
        rc_ids = rc_ids_from_tags(record.get("tags") or [])
        assert len(rc_ids) == 1, (
            f"{path.relative_to(_KILN_PROJECTS)} has rc tags {rc_ids} (tags:"
            f" {record.get('tags')}) — `baseline_rc_id` fatals on anything but"
            " exactly one, so the loop would die before wave 1"
        )


def test_no_committed_baseline_record_carries_val_metrics():
    # The planner never reads this record, but it is the record the split
    # exists to keep clean — and a live one still holding a val summary means
    # the leak is open on the only real fixture.
    #
    # Gated like its neighbour: iterating an empty list passes for ever, and
    # "empty" is what both a never-run project and a mis-aimed glob look like.
    if not _committed_memory_stores():
        pytest.skip(
            "no committed Kiln project carries an assistant_memory store yet,"
            " so there is nothing to check. The store is created by the first"
            " run, not by the build"
        )
    assert _committed_memory_records(), (
        "a committed Kiln project carries an assistant_memory directory but no"
        f" memory record was found under it — check the glob {_MEMORY_GLOBS}"
    )
    for path, record in _committed_baseline_records():
        overview = record.get("overview") or ""
        assert "BASELINE:" not in overview, (
            f"{path.relative_to(_KILN_PROJECTS)} still uses the deleted"
            f" `BASELINE: <rc> — …` overview format: {overview!r}"
        )
        assert "%" not in overview, (
            f"{path.relative_to(_KILN_PROJECTS)} carries val metrics in its"
            f" overview: {overview!r} — those belong in the `val` record"
        )


async def test_post_adopts_the_record_when_only_the_reply_was_lost():
    """`KilnClient` does not replay writes; a lost reply is
    recovered by re-listing the record's own (scope, tags) for the exact
    overview just written, so a singleton is never forked."""
    kiln = FakeKiln()
    kiln.memory_post_behaviors["experiment"] = ["lost_reply"]
    writer = make_writer(kiln)

    mem_id = await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)

    assert mem_id in kiln.memories
    assert len(kiln.memory_posts) == 1  # no replay
    assert [m for m in kiln.memories.values() if "experiment" in m["tags"]] == [
        kiln.memories[mem_id]
    ]


async def test_post_refused_by_the_server_still_raises():
    kiln = FakeKiln()
    kiln.memory_post_behaviors["experiment"] = ["http500"]
    writer = make_writer(kiln)
    with pytest.raises(KilnAPIError):
        await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)


async def test_issues_by_eval_maps_the_binding_tags():
    kiln = FakeKiln()
    kiln.add_memory(
        "issue A", "task::t1", ["issue", "p1", "eval_111111"], memory_id="i_a"
    )
    kiln.add_memory(
        "issue B",
        "task::t1",
        ["issue", "p2", "eval_111111", "eval_222222"],
        memory_id="i_b",
    )
    kiln.add_memory("a lesson", "task::t1", ["lesson", "eval_999999"], memory_id="l_1")
    writer = make_writer(kiln)
    mapping = await writer.issues_by_eval()
    assert mapping == {"111111": ["i_a", "i_b"], "222222": ["i_b"]}


async def test_lever_attempts_counts_arms_per_issue_and_technique():
    """The lever ladder's input: arms ever opened per
    (issue, technique), any outcome; merges and pre-`issue:` records skipped."""
    kiln = FakeKiln()
    writer = make_writer(kiln)
    await writer.open_experiment(
        fix_item(technique="prompt"), wave=1, baseline=BASELINE
    )
    await writer.open_experiment(
        fix_item(technique="prompt"), wave=2, baseline=BASELINE
    )
    await writer.open_experiment(fix_item(technique="skill"), wave=3, baseline=BASELINE)
    await writer.open_experiment(
        fix_item(issue_memory_id="issue_2", technique="new_tool"),
        wave=3,
        baseline=BASELINE,
    )
    # A record from before the `issue:` line existed, and a merge: neither counts.
    kiln.add_memory(
        "EXP 0701-w1-1 (prompt): old arm — REFUTED",
        "task::t1",
        ["experiment"],
        content="hypothesis: old\neval: eval_1",
    )
    kiln.add_memory(
        "EXP 0702-w2-1 (merge): w1-1 + w1-2 — PROMOTED 07-02",
        "task::t1",
        ["experiment"],
        content="merge of: w1-1 + w1-2\nissue: issue_1",
    )

    assert await writer.lever_attempts() == {
        "issue_1": {"prompt": 2, "skill": 1},
        "issue_2": {"new_tool": 1},
    }


async def test_lever_attempts_is_empty_with_no_experiments():
    kiln = FakeKiln()
    assert await make_writer(kiln).lever_attempts() == {}


# ---- lost-reply adoption is exact and unique; multi-word
# ---- technique values survive; the group line stays last; PATCH replays


async def test_post_does_not_adopt_when_two_records_match_exactly():
    """Two identical records (a re-opened arm, same wave) and a lost reply:
    "the one I just created" is unknowable, so adopting is refused."""
    kiln = FakeKiln()
    writer = make_writer(kiln)
    first = await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)
    kiln.memory_post_behaviors["experiment"] = ["lost_reply"]
    with pytest.raises(KilnTransportError):
        await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)
    assert first in kiln.memories


async def test_post_adoption_matches_on_content_not_overview_alone():
    """An older record with the same overview but other content is not the
    one the lost POST created; the exact match is."""
    kiln = FakeKiln()
    writer = make_writer(kiln)
    item = fix_item()
    label = writer.exp_label(item.id)
    older = kiln.add_memory(
        f"{label} (prompt): {item.work.hypothesis} — RUNNING",
        "task::t1",
        ["experiment"],
        content="a different record with the same overview",
    )
    kiln.memory_post_behaviors["experiment"] = ["lost_reply"]
    adopted = await writer.open_experiment(item, wave=1, baseline=BASELINE)
    assert adopted != older
    assert kiln.memories[adopted]["content"] != kiln.memories[older]["content"]


async def test_multi_word_technique_survives_the_verdict_and_counts():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(
        fix_item(technique="tool config"), wave=1, baseline=BASELINE
    )
    await writer.record_verdict(mem_id, make_verdict_result("hypothesis: x"))
    assert kiln.memories[mem_id]["content"].endswith("technique: tool config")
    assert await writer.lever_attempts() == {"issue_1": {"tool config": 1}}


async def test_record_verdict_moves_an_analyzer_authored_group_line_last():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)
    await writer.record_verdict(
        mem_id, make_verdict_result("head_to_head_group: tone\nnext: rerun")
    )
    assert kiln.memories[mem_id]["content"] == (
        "next: rerun\nissue: issue_1\ntechnique: prompt\nhead_to_head_group: tone"
    )


async def test_patch_walks_the_transport_retry_ladder():
    """PATCH bodies are absolute values, so a replay is idempotent — and the
    promotion writes re-run the whole read-modify-write on a transport error,
    which WOULD duplicate a line that had landed."""
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)
    await writer.record_verdict(mem_id, make_verdict_result("hypothesis: x"))
    assert kiln.memory_patch_retries and all(kiln.memory_patch_retries)


# ---- val-gate HELD: escalated on a constraint, not judged ----


async def test_stamp_held_names_the_constraint_and_is_idempotent():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = experiment_record(kiln)
    await writer.stamp_held(mem_id, "424242093572")
    await writer.stamp_held(mem_id, "424242093572")
    overview = kiln.memories[mem_id]["overview"]
    assert overview.endswith(" — HELD (constraint 424242093572) 07-22")
    assert overview.count("— HELD") == 1
    assert "REJECTED AT VAL" not in overview


async def test_note_held_frozen_format_and_idempotent():
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = experiment_record(kiln, content="hypothesis: x")
    await writer.note_held(mem_id, "424242093572")
    await writer.note_held(mem_id, "424242093572")
    content = kiln.memories[mem_id]["content"]
    assert (
        content.count("- held at the val gate 07-22 on OPEN constraint 424242093572")
        == 1
    )
    assert "resubmit this rc unchanged (existing_rc_id) once answered" in content


async def test_a_held_record_is_a_result_no_restate_moves_it():
    from kiln_harness_tuner.memory import ExperimentStateError

    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = experiment_record(kiln)
    await writer.stamp_held(mem_id, "424242093572")
    with pytest.raises(ExperimentStateError):
        await writer.mark_abandoned(mem_id, "restart")


# ---- long planner text must not cost a record its state ----
#
# A planner can give one arm a `technique` of several hundred characters.
# The label prefix `EXP <id> (<technique>): ` then fills the overview cap,
# the cap cuts the ` — RUNNING` suffix off, `experiment_state` reads the
# record as a verdict, and the arm's next writes are refused.

LONG_TECHNIQUE = (
    "rewrite every skill file and the prompt so that each tool call is checked "
    "against the schema before it is sent, then add new versions of every skill "
) * 40


def _view(record: dict) -> ExperimentView:
    return ExperimentView.from_record(MemoryRecord.from_api(record))


@pytest.mark.parametrize("length", [300, 600, len(LONG_TECHNIQUE)])
async def test_a_long_technique_keeps_the_running_suffix(length):
    """A technique long enough to push the suffix past the overview cap —
    from just past it to several thousand characters."""
    technique = LONG_TECHNIQUE[:length]
    # Without the bound, the label alone leaves no room for the suffix.
    label = f"EXP 0722-w1-2 ({technique}): "
    assert len(label) + len(" — RUNNING") > OVERVIEW_MAX_CHARS
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(
        fix_item(technique=technique, hypothesis=LONG_TECHNIQUE),
        wave=1,
        baseline=BASELINE,
    )
    record = kiln.memories[mem_id]
    assert len(record["overview"]) <= OVERVIEW_MAX_CHARS
    assert record["overview"].endswith(" — RUNNING")
    assert experiment_state(record["overview"]) is ExperimentState.RUNNING
    assert len(record["content"]) <= CONTENT_MAX_CHARS
    assert TRUNCATION_MARKER not in record["content"]
    view = _view(record)
    assert view.state is ExperimentState.RUNNING
    assert view.technique is not None
    assert len(view.technique) <= TECHNIQUE_MAX_CHARS
    assert view.overview_technique == view.technique  # the lever sources agree
    assert (view.parent_rc_id, view.eval_id, view.issue_id) == (
        "rc_base",
        "eval_1",
        "issue_1",
    )


@pytest.mark.parametrize("end", ["capped", "verdict"])
async def test_a_long_technique_record_takes_every_later_move(end):
    """rc built → measuring (no memory write) → capped, or → verdict and the
    promotion stamp."""
    long_item = fix_item(technique=LONG_TECHNIQUE, hypothesis=LONG_TECHNIQUE)
    change = RcChange(summary=LONG_TECHNIQUE, changes=["x"])
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(long_item, wave=1, baseline=BASELINE)
    await writer.record_rc_built(mem_id, "rc_cand", change)
    await writer.record_generality(mem_id, LONG_TECHNIQUE)
    record = kiln.memories[mem_id]
    assert len(record["content"]) <= CONTENT_MAX_CHARS
    view = _view(record)
    assert view.state is ExperimentState.RUNNING
    assert view.rc_id == "rc_cand" and view.issue_id == "issue_1"
    if end == "capped":
        await writer.mark_capped(mem_id, "capped at $1.00 after 5 items")
        overview = kiln.memories[mem_id]["overview"]
        assert overview.endswith(" — CAPPED (capped at $1.00 after 5 items)")
        assert experiment_state(overview) is ExperimentState.CAPPED
    else:
        await writer.record_verdict(mem_id, make_verdict_result("body"))
        view = _view(kiln.memories[mem_id])
        assert view.state is ExperimentState.VERDICT
        assert view.rc_id == "rc_cand"
        assert view.technique is not None
        assert len(view.technique) <= TECHNIQUE_MAX_CHARS
        await writer.stamp_promoted(mem_id)
        assert kiln.memories[mem_id]["overview"].endswith(" — PROMOTED 07-22")


def test_fit_stateful_keeps_the_suffix_when_the_prefix_fills_the_cap():
    prefix = "EXP 0722-w1-2 (" + "x" * 400 + "): "
    overview = _fit_stateful(prefix, "h", " — RUNNING")
    assert len(overview) == OVERVIEW_MAX_CHARS
    assert overview.endswith("… — RUNNING")
    assert experiment_state(overview) is ExperimentState.RUNNING


@pytest.mark.parametrize("end", ["capped", "verdict"])
async def test_a_record_cut_before_its_suffix_reads_unknown_and_moves_on(end):
    """A record whose opening was cut with an ellipsis and has no
    suffix. It is not read as a verdict, and the arm's writes land."""
    cut = clamp("EXP 0722-w1-2 (" + LONG_TECHNIQUE, OVERVIEW_MAX_CHARS)
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = kiln.add_memory(cut, "task::t1", ["experiment"], content="issue: i_1")
    assert _view(kiln.memories[mem_id]).state is ExperimentState.UNKNOWN
    # Not a result: a gate stamp is refused.
    with pytest.raises(ExperimentStateError):
        await writer.stamp_promoted(mem_id)
    await writer.record_rc_built(mem_id, "rc_cand")
    if end == "capped":
        await writer.mark_capped(mem_id, "capped at $1.00 after 5 items")
        overview = kiln.memories[mem_id]["overview"]
        assert len(overview) <= OVERVIEW_MAX_CHARS
        assert experiment_state(overview) is ExperimentState.CAPPED
    else:
        await writer.record_verdict(mem_id, make_verdict_result("body"))
        assert _view(kiln.memories[mem_id]).state is ExperimentState.VERDICT


async def test_a_verdict_overview_drops_a_technique_after_the_label():
    """Code keeps the verdict and the opening shapes apart: an analyzer row
    that kept `(technique)` would read UNKNOWN when it has no suffix."""
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = open_running(kiln)
    res = make_verdict_result("body").model_copy(
        update={"memory_overview": "EXP 0722-w1-2 (prompt): x — +18%, n=34, p=.04"}
    )
    await writer.record_verdict(mem_id, res)
    overview = kiln.memories[mem_id]["overview"]
    assert overview == "EXP 0722-w1-2: x — +18%, n=34, p=.04"
    assert experiment_state(overview) is ExperimentState.VERDICT


async def test_an_over_cap_append_keeps_the_lines_code_reads():
    """The append clamp drops the oldest OTHER lines, never the opening or
    the `rc:` line — and the residual lines land as lines."""
    kiln = FakeKiln()
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)
    await writer.record_rc_built(mem_id, "rc_cand")
    prose = "\n".join(f"note {i}: " + "y" * 150 for i in range(30))
    kiln.memories[mem_id]["content"] += "\n" + prose  # a hand-written body
    gate = ResidualGateResult(
        seed="s",
        items=[
            ResidualItem(eval_id="eval_1", item_id=f"item_{i}", role="failing")
            for i in range(3)
        ],
        fixed=0,
        failing=3,
        broke=0,
        guards=0,
        required=1,
        passed=False,
    )
    await writer.record_residual(mem_id, gate)
    content = kiln.memories[mem_id]["content"]
    assert len(content) <= CONTENT_MAX_CHARS
    lines = content.splitlines()
    assert lines[:7] == [
        "hypothesis: retry-skill for 429s",
        "predicted effect: +10% pass rate",
        "parent rc: rc_base",
        "eval: eval_1",
        "issue: issue_1",
        "technique: prompt",
        "rc: rc_cand",
    ]
    assert lines[7] == HISTORY_TRUNCATION_MARKER
    assert lines[-3].startswith("residual gate: ")
    assert lines[-2].startswith("residual still failing: ")
    assert lines[-1].startswith("residual guards broke: ")
    assert "note 29: " + "y" * 150 in lines  # the newest prose stays
