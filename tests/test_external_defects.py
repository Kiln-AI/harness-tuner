"""A defect outside the harness (a broken tool, stale data, a quota): the
record an agent writes through the gateway, what the planner gets and what
the defect guard does to its plan, and the report's "Fix elsewhere" section.

Every id is invented."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import ok
from test_gateway import PROJECT, _gateway, _run
from test_orchestrator import rc_ok, seed_active_eval, seed_baseline, seed_train
from test_quarantine import TASK
from test_status import make_tracker
from test_ui import capture_console

from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.items import apply_defect_guard
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.memory_schema import (
    CONTENT_MAX_CHARS,
    DEFECT_MAX_ITEMS,
    DEFECT_MAX_NOTES,
    OVERVIEW_MAX_CHARS,
    DefectRecord,
    DefectView,
    MemoryRecord,
    RecordKind,
    RecordKindError,
    defect_is_resolved,
    defect_key,
    record_kind,
)
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.report import (
    FIX_ELSEWHERE_APPENDIX_HEADING,
    ReportWriter,
    read_external_defects,
    render_fix_elsewhere_appendix,
)
from kiln_harness_tuner.schemas import (
    DefectGuardAction,
    ExternalDefect,
    ReportMarkdown,
)
from kiln_harness_tuner.tui.model import log as tui_log
from kiln_harness_tuner.ui import PlainUi

SEEN = "lookup_order answers HTTP 500 for each order id. The answer needs the status."
CHECKED = "The arguments fit the schema. The call fails on the baseline too."


def a_record(**changes: Any) -> DefectRecord:
    fields: dict[str, Any] = {
        "source": "lookup_order",
        "kind": "broken_tool",
        "seen": SEEN,
        "checked": CHECKED,
        "item_ids": ("t1", "t2", "t3"),
        "blocked_item_ids": ("t1", "t2"),
        "eval_ids": ("eval_1",),
        "rc_ids": ("rc_base",),
        "issue_ids": ("issue_1",),
        "harness": "A wrapper can say that the lookup failed.",
        "role": "analyze_experiment_results",
        "run_id": "261125-0900",
        "date": "2026-11-25",
    }
    return DefectRecord(**{**fields, **changes})


def add_defect(kiln, record: DefectRecord, memory_id: str = "defect_1", **kw) -> str:
    return kiln.add_memory(
        kw.get("overview", record.overview()),
        "task::t1",
        ["external_defect"],
        content=record.render(),
        memory_id=memory_id,
    )


def a_defect(**changes: Any) -> ExternalDefect:
    fields: dict[str, Any] = {
        "memory_id": "defect_1",
        "overview": a_record().overview(),
        "source": "lookup_order",
        "kind": "broken_tool",
        "seen": SEEN,
        "checked": CHECKED,
        "item_ids": ["t1", "t2", "t3"],
        "blocked_item_ids": ["t1", "t2"],
        "eval_ids": ["eval_1"],
        "rc_ids": ["rc_base"],
        "issue_ids": ["issue_1"],
        "harness": "A wrapper can say that the lookup failed.",
        "recorded_by": "analyze_experiment_results",
        "run_id": "261125-0900",
        "date": "2026-11-25",
    }
    return ExternalDefect(**{**fields, **changes})


def targeted(item_id: str, targets: list[str], issue: str = "issue_1"):
    item = fix_item(item_id, promotion_candidate=False, issue_memory_id=issue)
    return item.model_copy(
        update={"work": item.work.model_copy(update={"target_items": targets})}
    )


# ---- the record's format ----


def test_the_record_renders_one_line_per_field_and_reads_back():
    record = a_record()
    assert record.overview() == f"FIX ELSEWHERE: lookup_order (broken tool): {SEEN}"
    assert record.render().splitlines() == [
        "source: lookup_order",
        "defect: broken_tool",
        f"seen: {SEEN}",
        f"checked: {CHECKED}",
        "items: t1, t2, t3",
        "blocked items: t1, t2",
        "evals: eval_1",
        "run configs: rc_base",
        "issues: issue_1",
        "harness: A wrapper can say that the lookup failed.",
        "recorded: analyze_experiment_results · run 261125-0900 · 2026-11-25",
    ]
    assert DefectRecord.parse(record.render()) == record


def test_empty_id_lists_read_back_empty_and_no_harness_line_is_written():
    record = a_record(blocked_item_ids=(), rc_ids=(), issue_ids=(), harness="")
    text = record.render()
    assert "blocked items: none" in text and "issues: none" in text
    assert "harness:" not in text
    assert DefectRecord.parse(text) == record


def test_the_record_is_bounded_to_the_memory_caps():
    # An agent's text cannot break a line or pass a cap.
    record = a_record(
        source="lookup\norder " + "x" * 300,
        seen="seen: with a colon\nand a line break " + "y" * 2000,
        checked="c" * 2000,
        item_ids=tuple(f"item_{n:036d}" for n in range(60)),
        blocked_item_ids=tuple(f"item_{n:036d}" for n in range(60)),
        harness="h" * 2000,
        notes=tuple(f"- note {n} " + "n" * 400 for n in range(9)),
    )
    assert len(record.overview()) <= OVERVIEW_MAX_CHARS
    assert "\n" not in record.overview()
    text = record.render()
    assert len(text) <= CONTENT_MAX_CHARS
    back = DefectRecord.parse(text)
    assert back is not None
    assert len(back.item_ids) == DEFECT_MAX_ITEMS
    assert back.seen.startswith("seen: with a colon and a line break")
    assert len(back.notes) <= DEFECT_MAX_NOTES


def test_a_blocked_item_that_is_not_an_item_is_left_out():
    record = a_record(item_ids=("t1",), blocked_item_ids=("t1", "t9")).bounded()
    assert record.blocked_item_ids == ("t1",)


def test_an_unknown_kind_reads_as_other():
    text = a_record().render().replace("defect: broken_tool", "defect: gremlin")
    assert DefectRecord.parse(text).kind == "other"


def test_a_later_report_adds_its_ids_and_one_note_and_keeps_the_first_text():
    later = a_record(
        seen="lookup_order still answers HTTP 500 on the candidate.",
        item_ids=("t3", "t4"),
        blocked_item_ids=("t4",),
        rc_ids=("rc_cand",),
        issue_ids=("issue_2",),
        harness="another text",
        role="batch_plan",
        run_id="261126-1000",
        date="2026-11-26",
    )
    merged = a_record().merged(later)
    assert merged.seen == SEEN
    assert merged.harness == "A wrapper can say that the lookup failed."
    assert merged.item_ids == ("t1", "t2", "t3", "t4")
    assert merged.blocked_item_ids == ("t1", "t2", "t4")
    assert merged.rc_ids == ("rc_base", "rc_cand")
    assert merged.issue_ids == ("issue_1", "issue_2")
    assert merged.notes == (
        "- 2026-11-26 batch_plan, run 261126-1000: lookup_order still answers"
        " HTTP 500 on the candidate.",
    )
    # The note line is in the content, and the record reads back whole.
    assert DefectRecord.parse(merged.render()) == merged


def test_a_full_record_keeps_the_blocked_items_first():
    # A later report's blocked items must reach the guard, even when the
    # record already holds as many items as it keeps.
    first = a_record(
        item_ids=tuple(f"a{n}" for n in range(DEFECT_MAX_ITEMS)), blocked_item_ids=()
    )
    merged = first.merged(a_record(item_ids=("b1", "b2"), blocked_item_ids=("b2",)))
    assert len(merged.item_ids) == DEFECT_MAX_ITEMS
    assert merged.blocked_item_ids == ("b2",)
    assert "b2" in merged.item_ids and "b1" not in merged.item_ids
    assert DefectRecord.parse(merged.render()).blocked_item_ids == ("b2",)


def test_only_the_newest_notes_are_kept():
    record = a_record()
    for n in range(DEFECT_MAX_NOTES + 2):
        record = record.merged(a_record(seen=f"report number {n} of the same defect"))
    assert len(record.notes) == DEFECT_MAX_NOTES
    assert "report number 4" in record.notes[-1]


def test_the_key_is_the_source_without_case_and_the_kind():
    assert defect_key(" Lookup_Order ", "quota") == defect_key("lookup_order", "quota")
    assert defect_key("lookup_order", "quota") != a_record().key


def test_a_record_with_no_source_line_is_not_read():
    assert DefectRecord.parse("a note that an agent wrote by hand") is None
    assert DefectRecord.parse(None) is None


def test_the_tag_names_the_kind_and_the_view_refuses_another_kind():
    assert record_kind(["external_defect"]) is RecordKind.EXTERNAL_DEFECT
    record = a_record()
    view = DefectView.from_record(
        MemoryRecord("d1", record.overview(), record.render(), ["external_defect"], "s")
    )
    assert (view.resolved, view.defect) == (False, record)
    resolved = DefectView.from_record(
        MemoryRecord(
            "d1", "RESOLVED: " + record.overview(), None, ["external_defect"], "s"
        )
    )
    assert resolved.resolved and resolved.defect is None
    assert defect_is_resolved("RESOLVED: FIX ELSEWHERE: x") is True
    # A person types the prefix by hand.
    assert defect_is_resolved("  resolved:FIX ELSEWHERE: x") is True
    assert defect_is_resolved("FIX ELSEWHERE: RESOLVED: x") is False
    with pytest.raises(RecordKindError):
        DefectView.from_record(MemoryRecord("i1", "issue: x", None, ["issue"], "s"))


# ---- the gateway operation ----

MEMORIES = f"/api/projects/{PROJECT}/memories"
SCOPE = f"task::{TASK}"


class Store:
    """A memory store behind the gateway's routes."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def add(self, memory_id: str, overview: str, tags: list[str], content=None) -> None:
        self.rows[memory_id] = {
            "id": memory_id,
            "overview": overview,
            "tags": tags,
            "scope": SCOPE,
            "content": content,
        }

    def routes(self) -> dict[tuple[str, str], Any]:
        def listing(query, _body):
            tags = set(query.get("tags") or [])
            rows = [r for r in self.rows.values() if tags <= set(r["tags"])]
            return {"listings": rows, "matched": len(rows), "remaining": 0}

        def by_ids(query, _body):
            return [self.rows[i] for i in query["ids"] if i in self.rows]

        def post(_query, body):
            memory_id = f"m{len(self.rows) + 1}"
            self.rows[memory_id] = {"id": memory_id, **body}
            return self.rows[memory_id]

        routes: dict[tuple[str, str], Any] = {
            ("GET", MEMORIES): listing,
            ("GET", f"{MEMORIES}/by_ids"): by_ids,
            ("POST", MEMORIES): post,
        }
        for memory_id in ("m1", "m2", "old"):
            routes[("PATCH", f"{MEMORIES}/{memory_id}")] = self._patch(memory_id)
        return routes

    def _patch(self, memory_id: str):
        def patch(_query, body):
            self.rows[memory_id].update(body)
            return self.rows[memory_id]

        return patch


def report(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source": "lookup_order",
        "kind": "broken_tool",
        "seen": SEEN,
        "checked": CHECKED,
        "item_ids": ["tr1", "tr2"],
        "blocked_item_ids": ["tr1"],
    }
    return {**fields, **changes}


def defect_gateway(tmp_path, store: Store, role: str = "analyze_experiment_results"):
    return _gateway(tmp_path, store.routes(), role=role)


class TestTheGatewayOperation:
    def test_it_writes_the_record_in_codes_format(self, tmp_path):
        store = Store()
        store.add("i1", "issue: orders are wrong", ["issue", "p1"])
        gateway, kiln = defect_gateway(tmp_path, store)

        out = _run(
            gateway,
            "record_external_defect",
            **report(rc_ids=["424242100003"], issue_ids=["i1"]),
        )

        (post,) = [c for c in kiln.calls if c[0] == "POST"]
        body = post[3]
        assert body["scope"] == SCOPE
        assert body["tags"] == ["external_defect"]
        assert body["overview"] == f"FIX ELSEWHERE: lookup_order (broken tool): {SEEN}"
        written = DefectRecord.parse(body["content"])
        assert written.item_ids == ("tr1", "tr2")
        assert written.blocked_item_ids == ("tr1",)
        # The evals come from the train set, not from the agent: tr1 is a
        # train item of e1, tr2 of e1 and e2.
        assert written.eval_ids == ("e1", "e2")
        assert (written.rc_ids, written.issue_ids) == (("424242100003",), ("i1",))
        assert (written.role, written.run_id) == ("analyze_experiment_results", "run")
        assert out["id"] == "m2" and out["merged"] is False
        assert out["blocked_item_ids"] == ["tr1"] and out["eval_ids"] == ["e1", "e2"]

    def test_the_write_leaves_a_memory_line_for_the_run_log(self, tmp_path):
        gateway, _ = defect_gateway(tmp_path, Store())
        _run(gateway, "record_external_defect", **report())
        (line,) = (tmp_path / "run" / gw.MEMORY_FILE).read_text().splitlines()
        assert '"tags": ["external_defect"]' in line and '"op": "create"' in line

    @pytest.mark.parametrize("held_out", ["va1", "cross", "nope"])
    def test_an_item_that_is_not_train_is_refused(self, tmp_path, held_out):
        gateway, kiln = defect_gateway(tmp_path, Store())
        with pytest.raises(gw.GatewayError, match="not train items"):
            _run(
                gateway,
                "record_external_defect",
                **report(item_ids=["tr1", held_out], blocked_item_ids=[]),
            )
        assert kiln.calls == []  # refused before any Kiln call

    def test_a_blocked_item_must_be_one_of_the_items(self, tmp_path):
        gateway, _ = defect_gateway(tmp_path, Store())
        with pytest.raises(gw.GatewayError, match="blocked_item_ids names"):
            _run(
                gateway,
                "record_external_defect",
                **report(item_ids=["tr1"], blocked_item_ids=["tr2"]),
            )

    def test_blocked_item_ids_has_no_default(self, tmp_path):
        # The reporter decides each time if a harness change can still help.
        gateway, _ = defect_gateway(tmp_path, Store())
        fields = report()
        del fields["blocked_item_ids"]
        with pytest.raises(Exception, match="blocked_item_ids"):
            _run(gateway, "record_external_defect", **fields)

    def test_an_issue_id_that_is_not_an_issue_is_refused(self, tmp_path):
        store = Store()
        store.add("l1", "LESSON: x", ["lesson"])
        gateway, kiln = defect_gateway(tmp_path, store)
        with pytest.raises(gw.GatewayError, match="not issue records"):
            _run(gateway, "record_external_defect", **report(issue_ids=["l1"]))
        assert not [c for c in kiln.calls if c[0] == "POST"]

    def test_a_second_report_of_the_same_defect_is_added_to_the_record(self, tmp_path):
        store = Store()
        gateway, kiln = defect_gateway(tmp_path, store)
        _run(gateway, "record_external_defect", **report(item_ids=["tr1"]))

        out = _run(
            gateway,
            "record_external_defect",
            **report(
                source="Lookup_Order",
                seen="The same HTTP 500 on the candidate run config.",
                item_ids=["tr2"],
                blocked_item_ids=["tr2"],
            ),
        )

        assert out["merged"] is True and out["id"] == "m1"
        assert out["item_ids"] == ["tr1", "tr2"]
        assert out["blocked_item_ids"] == ["tr1", "tr2"]
        assert len([c for c in kiln.calls if c[0] == "POST"]) == 1
        (patch,) = [c for c in kiln.calls if c[0] == "PATCH"]
        assert set(patch[3]) == {"content"}  # the overview stays the first one
        assert "The same HTTP 500 on the candidate" in store.rows["m1"]["content"]
        assert len(store.rows) == 1

    def test_the_record_is_found_past_the_first_page(self, tmp_path):
        # Many records, the open one of this defect listed last: the listing
        # is paged and the content is read in parts of at most 50 ids.
        store = Store()
        other = a_record(item_ids=("tr1",), blocked_item_ids=())
        for n in range(70):
            store.add(f"x{n}", "FIX ELSEWHERE: x", ["external_defect"], None)
        store.add("m1", other.overview(), ["external_defect"], other.render())
        routes = store.routes()
        everything = routes[("GET", MEMORIES)]

        def paged(query, body):
            rows = everything(query, body)["listings"]
            start, size = query["offset"], query["limit"]
            page = rows[start : start + size]
            left = max(len(rows) - start - len(page), 0)
            return {"listings": page, "matched": len(rows), "remaining": left}

        routes[("GET", MEMORIES)] = paged
        gateway, kiln = _gateway(tmp_path, routes, role="batch_plan")

        out = _run(gateway, "record_external_defect", **report())

        assert out["merged"] is True and out["id"] == "m1"
        listings = [c for c in kiln.calls if c[1] == MEMORIES and c[0] == "GET"]
        assert len(listings) == 2
        reads = [c[2]["ids"] for c in kiln.calls if c[1] == f"{MEMORIES}/by_ids"]
        assert sorted(len(ids) for ids in reads) == [21, 50]

    def test_an_item_of_an_eval_this_run_created_names_that_eval(self, tmp_path):
        gateway, _ = defect_gateway(tmp_path, Store())
        gateway.s.record("new1", "eval_item", "train", "e9")
        out = _run(
            gateway,
            "record_external_defect",
            **report(item_ids=["new1"], blocked_item_ids=[]),
        )
        assert out["eval_ids"] == ["e9"]

    def test_the_answer_names_the_items_a_full_record_could_not_keep(self, tmp_path):
        store = Store()
        full = a_record(
            item_ids=tuple(f"a{n}" for n in range(DEFECT_MAX_ITEMS)),
            blocked_item_ids=(),
        )
        store.add("m1", full.overview(), ["external_defect"], full.render())
        gateway, _ = defect_gateway(tmp_path, store)
        out = _run(gateway, "record_external_defect", **report())
        assert out["merged"] is True
        assert out["not_kept"] == ["tr2"]  # tr1 is blocked, so it is kept
        assert "tr1" in out["blocked_item_ids"]

    def test_another_kind_or_a_resolved_record_gets_a_new_record(self, tmp_path):
        store = Store()
        old = a_record(item_ids=("tr1",), blocked_item_ids=())
        store.add(
            "old", "RESOLVED: " + old.overview(), ["external_defect"], old.render()
        )
        gateway, _ = defect_gateway(tmp_path, store)
        first = _run(gateway, "record_external_defect", **report())
        second = _run(gateway, "record_external_defect", **report(kind="quota"))
        assert first["merged"] is False and second["merged"] is False
        assert len(store.rows) == 3

    def test_no_agent_writes_or_changes_a_defect_record_by_hand(self, tmp_path):
        store = Store()
        record = a_record(item_ids=("tr1",), blocked_item_ids=("tr1",))
        store.add("old", record.overview(), ["external_defect"], record.render())
        gateway, kiln = defect_gateway(tmp_path, store, role="batch_plan")
        with pytest.raises(gw.GatewayError, match="record_external_defect only"):
            _run(
                gateway,
                "memory_create",
                overview="FIX ELSEWHERE: x",
                scope=SCOPE,
                tags=["external_defect"],
            )
        # An agent cannot resolve a record, or unblock its items.
        with pytest.raises(gw.GatewayError, match="its owner resolves it"):
            _run(gateway, "memory_update", id="old", overview="RESOLVED: done")
        assert not [c for c in kiln.calls if c[0] in ("POST", "PATCH")]

    def test_only_the_planner_and_the_analyzer_have_the_operation(self):
        have = {r for r, ops in gw.ROLE_OPS.items() if "record_external_defect" in ops}
        assert have == {"batch_plan", "analyze_experiment_results"}


# ---- the read ----


async def test_the_read_gives_the_open_records_and_skips_the_resolved():
    ctx = make_ctx()
    add_defect(ctx.kiln, a_record())
    add_defect(ctx.kiln, a_record(source="rates"), "defect_2", overview="RESOLVED: x")
    ctx.kiln.add_memory(
        "FIX ELSEWHERE: by hand", "task::t1", ["external_defect"], memory_id="defect_3"
    )
    ctx.kiln.add_memory("issue: x", "task::t1", ["issue"], memory_id="issue_1")

    found = await ctx.memory.open_external_defects()

    assert [d.memory_id for d in found] == ["defect_1", "defect_3"]
    assert found[0] == a_defect()
    # A record with the tag and no content lines is shown, and blocks nothing.
    assert found[1] == ExternalDefect(
        memory_id="defect_3", overview="FIX ELSEWHERE: by hand"
    )


async def test_a_task_with_no_record_costs_one_listing():
    ctx = make_ctx()
    assert await ctx.memory.open_external_defects() == []
    assert [c[:2] for c in ctx.kiln.calls] == [("GET", "/api/projects/p1/memories")]


# ---- the defect guard ----


def guard_events(ctx) -> list[dict]:
    return [f for k, f in ctx.log.events if k == "defect_guard"]


def test_the_guard_removes_blocked_items_from_an_arms_targets():
    ctx = make_ctx()
    item = targeted("w2-1", ["t1", "t5", "t2"])

    (kept,) = apply_defect_guard(ctx, [item], 2, [a_defect()])

    assert kept.work.target_items == ["t5"]
    assert item.work.target_items == ["t1", "t5", "t2"]  # the plan's own item
    (event,) = guard_events(ctx)
    assert event == {
        "wave": 2,
        "item_id": "w2-1",
        "action": "targets_removed",
        "issue_memory_id": "issue_1",
        "item_ids": ["t1", "t2"],
        "defect_ids": ["defect_1"],
        "line": (
            "w2-1: removed the target item(s) t1, t2: the defect record(s)"
            " defect_1 say that no harness change can make them pass"
        ),
    }
    assert ctx.defect_guard_lines == [event["line"]]


def test_the_guard_drops_an_arm_whose_targets_are_all_blocked():
    ctx = make_ctx()
    other = targeted("w2-2", ["t7"], issue="issue_9")

    kept = apply_defect_guard(
        ctx, [targeted("w2-1", ["t2", "t1"]), other], 2, [a_defect()]
    )

    assert kept == [other]
    (event,) = guard_events(ctx)
    assert (event["action"], event["item_ids"]) == ("dropped", ["t1", "t2"])
    assert event["line"] == (
        "dropped w2-1: the defect record(s) defect_1 block every target item"
        " (t1, t2), so no harness change can make them pass"
    )


def test_the_guard_drops_an_arm_with_no_target_on_an_issue_a_record_names():
    ctx = make_ctx()
    free = fix_item("w2-2", promotion_candidate=False, issue_memory_id="issue_9")

    kept = apply_defect_guard(
        ctx, [fix_item("w2-1", promotion_candidate=False), free], 2, [a_defect()]
    )

    assert kept == [free]
    (event,) = guard_events(ctx)
    assert (event["action"], event["item_ids"]) == ("dropped", [])
    assert event["line"] == (
        "dropped w2-1: the defect record(s) defect_1 name issue issue_1, and the"
        " item names no target item. Plan a fix on this issue only with target"
        " items that no defect record blocks"
    )


def test_an_arm_on_that_issue_with_targets_that_are_not_blocked_runs():
    ctx = make_ctx()
    item = targeted("w2-1", ["t3", "t8"])  # t3 shows the defect; it is not blocked
    assert apply_defect_guard(ctx, [item], 2, [a_defect()]) == [item]
    assert guard_events(ctx) == []


def test_a_record_that_blocks_no_item_changes_no_plan():
    # The reporter said that a harness change can still make each item pass.
    ctx = make_ctx()
    items = [fix_item("w2-1"), targeted("w2-2", ["t1"])]
    defect = a_defect(blocked_item_ids=[])
    assert apply_defect_guard(ctx, items, 2, [defect]) == items
    assert apply_defect_guard(ctx, items, 2, []) == items
    assert guard_events(ctx) == []


def test_two_records_that_block_one_item_are_both_named():
    ctx = make_ctx()
    second = a_defect(memory_id="defect_2", source="rates", issue_ids=[])
    apply_defect_guard(ctx, [targeted("w1-1", ["t1"])], 1, [a_defect(), second])
    (event,) = guard_events(ctx)
    assert event["defect_ids"] == ["defect_1", "defect_2"]


# ---- the planner's input, and the plan that runs ----


async def test_the_planner_gets_the_records_and_the_wave_runs_what_is_left():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    add_defect(ctx.kiln, a_record(item_ids=("t1", "t9"), blocked_item_ids=("t9",)))
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(
            make_plan(
                targeted("w1-1", ["t1", "t9"]),
                targeted("w1-2", ["t9"], issue="issue_2"),
            )
        ),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    first, second = d.calls_to("batch_plan")
    assert [x.memory_id for x in first.external_defects] == ["defect_1"]
    assert first.external_defects[0].blocked_item_ids == ["t9"]
    assert first.defect_guard_lines == []
    # The next planner reads what code did, so it knows w1-2 never ran.
    lines = [f["line"] for f in guard_events(ctx)]
    assert second.defect_guard_lines == lines
    assert [f["action"] for f in guard_events(ctx)] == ["targets_removed", "dropped"]
    # The wave shows and runs one arm, without the blocked target.
    (wave_start,) = [f for k, f in ctx.log.events if k == "wave_start"]
    assert [i["item_id"] for i in wave_start["items"]] == ["w1-1"]
    (build,) = d.calls_to("create_experiment_rc")
    assert build.item.work.target_items == ["t1"]


async def test_a_plan_of_blocked_arms_only_is_planned_again():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    add_defect(ctx.kiln, a_record())
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(targeted("w1-1", ["t1"]))),
        ok(make_plan(targeted("w1-2", ["t5"]))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    (dropped,) = guard_events(ctx)
    assert dropped["item_id"] == "w1-1"
    assert [p.defect_guard_lines for p in d.calls_to("batch_plan")] == [
        [],
        [dropped["line"]],
        [dropped["line"]],
    ]
    (all_dropped,) = [f for k, f in ctx.log.events if k == "plan_all_items_dropped"]
    assert all_dropped == {"wave": 1, "phase": "plan", "dropped": 1}
    (wave_start,) = [f for k, f in ctx.log.events if k == "wave_start"]
    assert [i["item_id"] for i in wave_start["items"]] == ["w1-2"]


async def test_a_record_written_while_planning_guards_that_same_plan():
    # The planner can record a defect in the session that returns the plan.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    d = ctx.dispatch

    def plan_and_record(_request):
        add_defect(ctx.kiln, a_record())
        return ok(make_plan(targeted("w1-1", ["t1", "t5"])))

    d.queue("batch_plan", plan_and_record, ok(make_plan(loop_exit="done")))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    (event,) = guard_events(ctx)
    assert (event["action"], event["item_ids"]) == ("targets_removed", ["t1"])
    (build,) = d.calls_to("create_experiment_rc")
    assert build.item.work.target_items == ["t5"]


async def test_the_planner_gets_no_item_that_is_held_out_now():
    # A record of an earlier run; a later split change made t9 a val item.
    ctx = make_ctx()
    ctx.analyzer.heldout_ids = frozenset({"t9"})
    add_defect(ctx.kiln, a_record(item_ids=("t1", "t9"), blocked_item_ids=("t9",)))
    add_defect(ctx.kiln, a_record(source="rates", item_ids=("t9",)), "defect_2")

    from kiln_harness_tuner.orchestrator import _external_defects

    (defect,) = await _external_defects(ctx)

    assert defect.memory_id == "defect_1"
    assert (defect.item_ids, defect.blocked_item_ids) == (["t1"], [])


async def test_a_failed_read_keeps_the_last_records_and_the_wave():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    ctx.external_defects = [a_defect()]

    async def kiln_is_down(**_):
        raise KilnAPIError("GET", "/memories", 503, "down")

    ctx.memory.open_external_defects = kiln_is_down
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(targeted("w1-1", ["t1", "t5"]))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert [k for k, _ in ctx.log.events].count("external_defects_unavailable") == 3
    assert d.calls_to("batch_plan")[0].external_defects == [a_defect()]
    (build,) = d.calls_to("create_experiment_rc")
    assert build.item.work.target_items == ["t5"]


# ---- the run status and the displays ----


def guard_event(tracker, item_id: str, action: str, wave: int = 2, **fields) -> None:
    tracker.event(
        "defect_guard",
        wave=wave,
        item_id=item_id,
        action=action,
        issue_memory_id="issue_1",
        item_ids=fields.get("item_ids", ["t1"]),
        defect_ids=fields.get("defect_ids", ["defect_1"]),
        line=f"{item_id}: …",
    )


def test_the_tracker_keeps_guard_actions_and_the_defects_agents_wrote():
    tracker = make_tracker()
    guard_event(tracker, "w2-1", "targets_removed")
    tracker.event("defect_guard", wave="two", item_id="w2-9", action="dropped")
    tracker.event(
        "memory_written",
        id="defect_1",
        op="create",
        role="batch_plan",
        record_kind="external_defect",
        overview="FIX ELSEWHERE: lookup_order (broken tool): HTTP 500",
    )
    tracker.event("memory_written", id="l1", record_kind="lesson", overview="LESSON: x")

    status = tracker.snapshot()

    assert status.defect_guard_actions == [
        DefectGuardAction(
            item_id="w2-1",
            wave=2,
            action="targets_removed",
            issue_memory_id="issue_1",
            item_ids=["t1"],
            defect_ids=["defect_1"],
            line="w2-1: …",
        )
    ]
    assert status.recorded_defects == {
        "defect_1": "FIX ELSEWHERE: lookup_order (broken tool): HTTP 500"
    }


def test_the_plain_display_prints_each_guard_line():
    console, out = capture_console()
    with PlainUi(make_tracker(), console=console) as ui:
        ui.event("defect_guard", wave=1, item_id="w1-1", line="dropped w1-1: blocked")
    assert "dropped w1-1: blocked" in out.getvalue()


def test_the_app_log_names_a_new_defect_record():
    assert tui_log.memory_record(
        "external_defect", "create", "FIX ELSEWHERE: lookup_order (quota): HTTP 429"
    ) == ("Defect outside the harness: lookup_order (quota): HTTP 429")


# ---- the report's section ----


def test_the_section_gives_each_defect_with_its_evidence():
    tracker = make_tracker()
    guard_event(tracker, "w2-1", "targets_removed", item_ids=["t1", "t2"])
    guard_event(tracker, "w3-2", "dropped", wave=3)
    guard_event(tracker, "w3-3", "dropped", wave=3, defect_ids=["defect_7"])
    status = tracker.snapshot()
    status.external_defects = [a_defect(later_reports=["2026-11-26 batch_plan: again"])]

    assert render_fix_elsewhere_appendix(status).splitlines() == [
        FIX_ELSEWHERE_APPENDIX_HEADING,
        "",
        "The agents found these defects in a tool or a data source of the task."
        " No change to the harness (the prompt, the model, the tools, the skills"
        " or the settings) can fix them, so the tuner spends no experiment on"
        " them. Fix each one outside the tuner. The train items and the run"
        " configs name the traces that show it: a trace is a file in the folder"
        " of the run that recorded the defect,"
        " `runs/<run id>/scratch/traces/<run config>/<eval>/<train item>.md`.",
        "",
        "### `lookup_order`: broken tool",
        "",
        f"- **What was seen:** {SEEN}",
        f"- **How it was checked:** {CHECKED}",
        "- **Train items that show it:** `t1`, `t2`, `t3` (evals `eval_1`)",
        "- **Train items that cannot pass until it is fixed:** `t1`, `t2`",
        "- **Run configs that show it:** `rc_base`",
        "- **Issues that it explains:** `issue_1`",
        "- **What a harness change can still do:** A wrapper can say that the"
        " lookup failed.",
        "- **Seen again:** 2026-11-26 batch_plan: again",
        "- **Recorded:** by the analyzer, run `261125-0900`, 2026-11-25."
        " Memory record `defect_1`.",
        "- **In this run:** code removed `t1`, `t2` from the targets of `w2-1`"
        " (wave 2); code dropped the fix arm `w3-2` (wave 3).",
        "",
        "After you fix a defect, put `RESOLVED:` at the start of the overview of"
        " its memory record, or delete the record. The tuner then plans on its"
        " train items again. Until then, each run keeps the items that cannot"
        " pass out of the targets of its experiments. The items stay in every"
        " measurement.",
    ]


def test_the_section_says_when_no_item_is_blocked_and_keeps_a_cell_on_one_line():
    status = make_tracker().snapshot()
    status.external_defects = [
        a_defect(
            seen="a | pipe\nand a break",
            blocked_item_ids=[],
            rc_ids=[],
            issue_ids=[],
            harness="",
            recorded_by="batch_plan",
        )
    ]
    text = render_fix_elsewhere_appendix(status)
    assert "- **What was seen:** a \\| pipe and a break" in text
    assert (
        "- **Train items that cannot pass until it is fixed:** none named."
        " A harness change can still make each item pass." in text
    )
    assert "**Run configs" not in text and "**Issues" not in text
    assert "**In this run:**" not in text and "**What a harness change" not in text
    assert "- **Recorded:** by the planner, run" in text


def test_a_record_code_cannot_read_is_shown_with_its_overview():
    status = make_tracker().snapshot()
    status.external_defects = [
        ExternalDefect(memory_id="defect_3", overview="FIX ELSEWHERE: by hand")
    ]
    assert render_fix_elsewhere_appendix(status).splitlines()[4:8] == [
        "### FIX ELSEWHERE: by hand",
        "",
        "- **Memory record:** `defect_3`. Code could not read its fields, so it"
        " blocks no train item.",
        "",
    ]


def test_no_defect_gives_no_section():
    status = make_tracker().snapshot()
    assert render_fix_elsewhere_appendix(status) == ""  # not read, none written
    status.external_defects = []
    assert render_fix_elsewhere_appendix(status) == ""


def test_without_the_read_the_section_lists_what_agents_wrote_in_this_run():
    tracker = make_tracker()
    tracker.event(
        "memory_written",
        id="defect_1",
        record_kind="external_defect",
        overview="FIX ELSEWHERE: lookup_order (broken tool): HTTP 500",
    )
    status = tracker.snapshot()
    assert status.external_defects is None

    lines = render_fix_elsewhere_appendix(status).splitlines()

    assert lines[0] == FIX_ELSEWHERE_APPENDIX_HEADING
    assert "The read of the records failed" in lines[2]
    assert lines[4:] == [
        "- `defect_1`: FIX ELSEWHERE: lookup_order (broken tool): HTTP 500"
    ]


def test_without_the_read_a_resolved_record_gives_no_section():
    tracker = make_tracker()
    tracker.event(
        "memory_written",
        id="defect_1",
        record_kind="external_defect",
        overview="RESOLVED: FIX ELSEWHERE: lookup_order (broken tool): HTTP 500",
    )
    assert render_fix_elsewhere_appendix(tracker.snapshot()) == ""


async def test_a_failed_read_is_none_and_logged(caplog):
    ctx = make_ctx()

    async def kiln_is_down(*, retries=None):
        raise KilnAPIError("GET", "/memories", 503, "down")

    ctx.memory.open_external_defects = kiln_is_down
    with caplog.at_level("WARNING", logger="kiln_harness_tuner.report"):
        assert await read_external_defects(ctx) is None
    assert "the defect record read failed" in caplog.text


async def test_the_report_has_the_section_after_the_agents_markdown(tmp_path):
    # A record of an earlier run is in the report too: it is still open.
    ctx = make_ctx()
    add_defect(ctx.kiln, a_record())
    writer = ReportWriter(make_tracker(), "261125-0900", tmp_path / "reports")
    ctx.dispatch.queue("report_writer", ok(ReportMarkdown(markdown="# Round report\n")))

    await writer(ctx, "wrap_up")

    text = writer.report_path.read_text()
    assert text.startswith("# Round report\n")
    assert FIX_ELSEWHERE_APPENDIX_HEADING in text
    assert "### `lookup_order`: broken tool" in text
    assert "`t1`, `t2`, `t3` (evals `eval_1`)" in text
    # The report agent gets the same records, to name them in its summary.
    (request,) = ctx.dispatch.calls_to("report_writer")
    assert request.run_status.external_defects == [a_defect()]


async def test_the_fallback_report_has_the_section_with_no_agent(tmp_path):
    ctx = make_ctx()
    add_defect(ctx.kiln, a_record())
    writer = ReportWriter(make_tracker(), "261125-0900", tmp_path / "reports")

    await writer.write_fallback(ctx, "quota_exhausted")

    text = writer.report_path.read_text()
    assert "code-rendered fallback" in text
    assert "### `lookup_order`: broken tool" in text
    assert ctx.dispatch.calls == []
