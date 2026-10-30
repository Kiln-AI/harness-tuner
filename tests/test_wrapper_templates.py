"""The wrapper templates in
`.agents/skills/kiln-harness-tuner-code-tool-authoring/references/wrappers/` are real
code the build agent copies into `POST /code_tools`. Each is run here against a stub of
`kiln.tools` (the sandbox's nested-call surface), so the plumbing — envelope parsing,
error shaping, pagination, batching — is proven before an agent adapts the routes to a
subject API."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

# tests/ sits at the repo root, so parents[1] is the root.
WRAPPERS = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "kiln-harness-tuner-code-tool-authoring"
    / "references"
    / "wrappers"
)


class StubTools:
    """`kiln.tools` stand-in: `call_subject_api(**kw)` answers from a script
    keyed by (method, url_path) or by a callable; records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[dict] = []

    def call_subject_api(self, **kw):
        self.calls.append(kw)
        key = (kw.get("method", "GET").upper(), kw.get("url_path"))
        answer = self.answers.get(key, self.answers.get("*"))
        if callable(answer):
            answer = answer(kw)
        if answer is None:
            raise AssertionError(f"unscripted call {key}")
        # Half the answers as JSON strings: the sandbox does both.
        return json.dumps(answer) if len(self.calls) % 2 else answer


@pytest.fixture
def load(monkeypatch):
    def _load(name, answers):
        tools = StubTools(answers)
        kiln = types.ModuleType("kiln")
        kiln.tools = tools
        monkeypatch.setitem(sys.modules, "kiln", kiln)
        spec = importlib.util.spec_from_file_location(
            f"wrapper_{name}", WRAPPERS / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, tools

    return _load


def ok(body):
    return {"status_code": 200, "body": body}


def test_every_template_is_listed_in_the_readme():
    readme = (WRAPPERS / "README.md").read_text()
    for path in WRAPPERS.glob("*.py"):
        assert f"`{path.name}`" in readme, path.name


class TestNormalizeInputs:
    def test_case_and_separator_variants_map_to_the_enum(self, load):
        mod, tools = load(
            "normalize_inputs", {("GET", "/api/v1/shipments"): ok({"results": []})}
        )
        out = mod.run(status=" On-Hold ", priority="HIGH", limit="7")
        assert out["ok"] and out["params_used"] == {
            "status": "on_hold",
            "priority": "high",
            "limit": 7,
        }

    def test_unknown_enum_returns_the_valid_values_not_a_422(self, load):
        mod, tools = load("normalize_inputs", {})
        out = mod.run(status="done")
        assert out["ok"] is False and out["valid_values"] == list(mod.STATUS_VALUES)
        assert tools.calls == []  # never reached the API

    def test_display_number_resolves_to_the_id(self, load):
        mod, tools = load(
            "normalize_inputs",
            {
                ("GET", "/api/v1/shipments"): lambda kw: ok(
                    {"results": [{"id": "shp_abc", "title": "pallets"}]}
                    if kw.get("query_params", {}).get("number") == 123
                    else {"results": [{"id": "shp_abc"}]}
                ),
            },
        )
        out = mod.run(shipment="SHP-123")
        assert out["ok"] and out["params_used"]["id"] == "shp_abc"
        assert tools.calls[0]["query_params"] == {"number": 123}

    def test_unresolvable_number_names_the_problem(self, load):
        mod, _ = load(
            "normalize_inputs", {("GET", "/api/v1/shipments"): ok({"results": []})}
        )
        out = mod.run(shipment="shp 999")
        assert out["ok"] is False and "no shipment with number 999" in out["error"]

    def test_limit_is_clamped_and_bad_limit_is_explained(self, load):
        mod, _ = load(
            "normalize_inputs", {("GET", "/api/v1/shipments"): ok({"results": []})}
        )
        assert mod.run(limit="500")["params_used"]["limit"] == mod.MAX_LIMIT
        assert "whole number" in mod.run(limit="many")["error"]


class TestPaginateAll:
    def test_follows_cursors_to_the_end(self, load):
        pages = {None: (["a", "b"], "c2"), "c2": (["c"], "c3"), "c3": (["d"], None)}

        def answer(kw):
            items, nxt = pages[kw["query_params"].get("cursor")]
            return ok({"items": items, "next_cursor": nxt})

        mod, tools = load("paginate_all", {("GET", "/api/v1/things"): answer})
        out = mod.run("/api/v1/things")
        assert out == {
            "ok": True,
            "total": 4,
            "items": ["a", "b", "c", "d"],
            "truncated": False,
            "next_cursor": None,
            "pages_read": 3,
        }

    def test_stops_at_the_cap_and_hands_back_the_cursor(self, load):
        mod, _ = load(
            "paginate_all",
            {
                ("GET", "/api/v1/things"): lambda kw: ok(
                    {"items": ["x"], "next_cursor": "more"}
                )
            },
        )
        out = mod.run("/api/v1/things", max_pages=2)
        assert (
            out["truncated"] is True
            and out["next_cursor"] == "more"
            and out["total"] == 2
        )


class TestBatchWithErrors:
    def test_partial_success_returns_both_lists(self, load):
        def answer(kw):
            if kw["url_path"].endswith("/2"):
                return {"status_code": 404, "body": {"detail": "gone"}}
            return ok({"id": kw["url_path"].rsplit("/", 1)[-1]})

        mod, _ = load("batch_with_errors", {"*": answer})
        out = mod.run(
            [
                {
                    "method": "PATCH",
                    "url_path": "/api/v1/shipments/1",
                    "body": {"status": "cleared"},
                },
                {
                    "method": "PATCH",
                    "url_path": "/api/v1/shipments/2",
                    "body": {"status": "cleared"},
                },
                {
                    "method": "PATCH",
                    "url_path": "/api/v1/shipments/3",
                    "body": {"status": "cleared"},
                },
            ]
        )
        assert out["ok"] is False
        assert [d["index"] for d in out["done"]] == [0, 2]
        assert out["errors"][0]["index"] == 1 and out["errors"][0]["status_code"] == 404
        assert out["summary"] == "2 succeeded, 1 failed of 3"

    def test_oversized_batch_is_refused_before_any_call(self, load):
        mod, tools = load("batch_with_errors", {})
        out = mod.run([{"method": "GET", "url_path": "/x"}] * (mod.MAX_OPERATIONS + 1))
        assert (
            out["ok"] is False
            and "split the list" in out["error"]
            and tools.calls == []
        )


class TestPreviewAndCap:
    def test_returns_total_and_a_projected_preview(self, load):
        rows = [{"id": i, "title": f"t{i}", "noise": "x" * 100} for i in range(50)]
        mod, _ = load(
            "preview_and_cap",
            {("GET", "/api/v1/shipments"): ok({"results": rows, "total": 682})},
        )
        out = mod.run("/api/v1/shipments", preview=3, fields=["id", "title"])
        assert out["total"] == 682 and out["shown"] == 3
        assert out["preview"] == [
            {"id": 0, "title": "t0"},
            {"id": 1, "title": "t1"},
            {"id": 2, "title": "t2"},
        ]
        assert "noise" not in json.dumps(out)


class TestCompositeResolveFetch:
    def test_found_ambiguous_and_missing(self, load):
        def answer(kw):
            if kw["url_path"] == "/api/v1/records":
                q = kw["query_params"]["q"]
                hits = {
                    "one": [{"id": "r1", "display_number": "1", "name": "One"}],
                    "amb": [{"id": "r1"}, {"id": "r2"}],
                }.get(q, [])
                return ok({"results": hits})
            return ok({"id": "r1", "name": "One", "detail": "full"})

        mod, _ = load("composite_resolve_fetch", {"*": answer})
        assert mod.run("one") == {
            "found": True,
            "record": {"id": "r1", "name": "One", "detail": "full"},
        }
        assert mod.run("amb")["ambiguous"] == [
            {"id": "r1", "display": None, "name": None},
            {"id": "r2", "display": None, "name": None},
        ]
        assert "no record matches" in mod.run("nothing")["hint"]


class TestActionableErrors:
    def test_success_passes_through(self, load):
        mod, _ = load(
            "actionable_errors", {("GET", "/api/v1/shipments/1"): ok({"id": 1})}
        )
        assert mod.run("GET", "/api/v1/shipments/1") == ok({"id": 1})

    def test_404_names_the_nearest_matches(self, load):
        def answer(kw):
            if kw["url_path"] == "/api/v1/shipments/SHP-77":
                return {"status_code": 404, "body": {"detail": "not found"}}
            return ok({"results": [{"id": "shp_77", "name": "Pallets 77"}]})

        mod, _ = load("actionable_errors", {"*": answer})
        out = mod.run("GET", "/api/v1/shipments/SHP-77")
        assert (
            out["status_code"] == 404
            and "nearest matches" in out["error"]
            and "shp_77" in out["error"]
        )

    def test_422_lists_field_errors_with_valid_values(self, load):
        body = {
            "detail": [
                {
                    "loc": ["body", "status"],
                    "msg": "not a valid enumeration member",
                    "ctx": {"expected": "'booked', 'cleared'"},
                }
            ]
        }
        mod, _ = load("actionable_errors", {"*": {"status_code": 422, "body": body}})
        out = mod.run("POST", "/api/v1/shipments", body={"status": "done"})
        assert (
            "status: not a valid enumeration member (valid: 'booked', 'cleared')"
            in out["error"]
        )

    def test_get_with_body_is_explained(self, load):
        mod, _ = load(
            "actionable_errors",
            {"*": {"status_code": 405, "body": "Method Not Allowed"}},
        )
        out = mod.run("GET", "/api/v1/shipments", body={"q": "x"})
        assert "GET takes no body" in out["error"]

    def test_429_says_how_long_to_wait(self, load):
        mod, _ = load(
            "actionable_errors",
            {"*": {"status_code": 429, "body": {"retry_after": 12}}},
        )
        assert "wait 12 s" in mod.run("GET", "/api/v1/shipments")["error"]


class TestContextOnToolSurface:
    def test_response_carries_the_directive_for_whatever_context_returns(
        self, load, monkeypatch
    ):
        mod, tools = load("context_on_tool_surface", {})
        # The shipped `_context()` is a placeholder the builder must replace.
        assert "<REPLACE" in mod.run()["user"]["display_name"]
        monkeypatch.setattr(
            mod, "_context", lambda: {"id": "u1", "display_name": "Det. Quen"}
        )
        out = mod.run()
        assert out["directive"].startswith("Address the user as Det. Quen")
        assert tools.calls == []  # no nested calls; allowlist stays empty


class TestReadonlyGuard:
    def test_get_forwards_and_writes_are_refused(self, load):
        mod, tools = load(
            "readonly_guard", {("GET", "/api/v1/shipments"): ok({"results": []})}
        )
        assert mod.run("GET", "/api/v1/shipments") == ok({"results": []})
        out = mod.run("post", "/api/v1/shipments")
        assert out["refused"] is True and mod.WRITE_TOOL_NAME in out["error"]
        assert len(tools.calls) == 1


# ---- raising nested tools, bad shapes, coercion, caps


def raising(_kw):
    raise RuntimeError("inner tool crashed")


class TestNestedToolRaises:
    """A crashed inner tool raises out of the bridge; every template turns
    that into an error envelope instead of a traceback."""

    def test_paginate_all(self, load):
        mod, _ = load("paginate_all", {"*": raising})
        out = mod.run("/api/v1/shipments")
        assert out["ok"] is False and "RuntimeError" in str(out["raw"])

    def test_preview_and_cap(self, load):
        mod, _ = load("preview_and_cap", {"*": raising})
        assert mod.run("/api/v1/shipments")["ok"] is False

    def test_composite_resolve_fetch(self, load):
        mod, _ = load("composite_resolve_fetch", {"*": raising})
        assert mod.run("SHP-1")["found"] is False

    def test_actionable_errors(self, load):
        mod, _ = load("actionable_errors", {"*": raising})
        out = mod.run("GET", "/api/v1/shipments/1")
        assert out["ok"] is False and out["status_code"] == -1

    def test_readonly_guard(self, load):
        mod, _ = load("readonly_guard", {"*": raising})
        assert mod.run("GET", "/api/v1/shipments")["status_code"] == -1

    def test_normalize_inputs(self, load):
        mod, _ = load("normalize_inputs", {"*": raising})
        assert mod.run(status="booked")["ok"] is False

    def test_batch_with_errors_keeps_going(self, load):
        mod, _ = load(
            "batch_with_errors", {("GET", "/a"): raising, ("GET", "/b"): ok({})}
        )
        out = mod.run(
            [{"method": "GET", "url_path": "/a"}, {"method": "GET", "url_path": "/b"}]
        )
        assert [d["index"] for d in out["done"]] == [1]
        assert [e["index"] for e in out["errors"]] == [0]


class TestBadShapesAndCoercion:
    def test_batch_reports_a_malformed_operation_and_continues(self, load):
        mod, _ = load("batch_with_errors", {("GET", "/b"): ok({})})
        out = mod.run(["oops", {"method": "GET"}, {"method": "GET", "url_path": "/b"}])
        assert len(out["errors"]) == 2 and len(out["done"]) == 1
        assert all("needs method and url_path" in e["error"] for e in out["errors"])

    def test_preview_handles_a_bare_json_array_body(self, load):
        mod, _ = load("preview_and_cap", {"*": ok([{"id": 1}, {"id": 2}])})
        out = mod.run("/api/v1/shipments", preview="1")
        assert out["total"] == 2 and out["preview"] == [{"id": 1}]

    def test_preview_is_capped_whatever_the_model_asks(self, load):
        rows = [{"id": i} for i in range(500)]
        mod, _ = load("preview_and_cap", {"*": ok({"results": rows})})
        assert mod.run("/api/v1/shipments", preview=1000)["shown"] == mod.MAX_PREVIEW
        assert mod.run("/api/v1/shipments", preview="five")["ok"] is False

    def test_paginate_coerces_max_pages(self, load):
        pages = {
            ("GET", "/api/v1/shipments"): lambda kw: ok(
                {"items": [kw["query_params"].get("cursor", "p0")], "next_cursor": "c"}
            )
        }
        mod, _ = load("paginate_all", pages)
        out = mod.run("/api/v1/shipments", max_pages="2")
        assert out["truncated"] is True and out["pages_read"] == 2
        assert mod.run("/api/v1/shipments", max_pages="two")["ok"] is False

    def test_paginate_handles_a_bare_array_page(self, load):
        mod, _ = load("paginate_all", {"*": ok([{"id": 1}])})
        out = mod.run("/api/v1/shipments")
        assert out["items"] == [{"id": 1}] and out["truncated"] is False

    def test_readonly_guard_refuses_a_non_string_method(self, load):
        mod, _ = load("readonly_guard", {})
        assert mod.run(None, "/x")["refused"] is True

    def test_404_derives_the_collection_from_any_route_shape(self, load):
        mod, tools = load(
            "actionable_errors",
            {
                ("GET", "/v2/consignees/CNS-9"): {"status_code": 404, "body": {}},
                ("GET", "/v2/consignees"): ok(
                    {"results": [{"id": "CNS-1", "name": "x"}]}
                ),
            },
        )
        out = mod.run("GET", "/v2/consignees/CNS-9")
        assert "CNS-1" in out["error"]
        assert tools.calls[1]["url_path"] == "/v2/consignees"

    def test_429_without_a_server_number_invents_none(self, load):
        mod, _ = load("actionable_errors", {"*": {"status_code": 429, "body": {}}})
        error = mod.run("GET", "/api/v1/shipments")["error"]
        assert "wait briefly" in error and "30" not in error

    def test_every_template_declares_its_parameters_schema(self):
        for path in WRAPPERS.glob("*.py"):
            assert "parameters_schema" in path.read_text(), path.name
