"""RAG as a lever (`kiln_harness_tuner.rag_lever`): the gateway operations
that create a RAG config from parts and index it, the rules around them
(the run's limit, the model policy, the extraction, the index budget and
the document check), the check on a built arm, the check that the search
tool was called, and the run-end step that archives what no run config
attaches. Every id is invented."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import fix_item, make_ctx, make_tracker
from fake_kiln import FakeKiln as LoopKiln
from test_gateway import PROJECT, _gateway, _run
from test_quarantine import TASK

from kiln_harness_tuner import rag_lever
from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.items import RagChange, _check_rag
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.report import RAG_APPENDIX_HEADING, render_rag_appendix
from kiln_harness_tuner.schemas import BaselineRef, RagConfigRecord, TargetBlock

P = f"/api/projects/{PROJECT}"
EXTRACTOR = "424242600001"
CHUNKER = "424242600002"
EMBEDDING = "424242600003"
STORE = "424242600004"
NEW_CHUNKER = "424242600012"
NEW_STORE = "424242600014"
NEW_RAG = "424242600020"
OLD_RAG = "424242600030"

# Eight words or more, so the document check looks for them.
HELD_OUT_INPUT = "my tomato leaves turn yellow from the bottom up after rain"
HELD_OUT_ANSWER = "remove the lower leaves and water the soil, not the leaves"


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def add_document(
    root: Path,
    doc_id: str,
    text: str | None,
    *,
    tags: tuple[str, ...] = ("handbook",),
    extractor: str = EXTRACTOR,
    chunker: str | None = None,
    embedding: str | None = None,
) -> None:
    """One document of the library on disk, as Kiln writes it: its record,
    and (when `text` is given) an extraction with its output file, a
    chunked document for `chunker` and embeddings for `embedding`."""
    folder = root / "documents" / f"{doc_id} - doc {doc_id}"
    _write(
        folder / "document.kiln",
        {"id": doc_id, "name": f"doc {doc_id}", "kind": "document", "tags": tags},
    )
    if text is None:
        return
    extraction = folder / "extractions" / f"x{doc_id}"
    _write(
        extraction / "extraction.kiln",
        {
            "id": f"x{doc_id}",
            "extractor_config_id": extractor,
            "output": {"path": "o.md"},
        },
    )
    (extraction / "o.md").write_text(text)
    if chunker is not None:
        chunked = extraction / "chunked_documents" / f"c{doc_id}"
        _write(chunked / "chunked_document.kiln", {"chunker_config_id": chunker})
        if embedding is not None:
            _write(
                chunked / "chunk_embeddings" / "e1" / "chunk_embeddings.kiln",
                {"embedding_config_id": embedding},
            )


def add_eval_input(
    root: Path, task_id: str, item_id: str, tags: list[str], text: str, reference=None
) -> None:
    doc: dict[str, Any] = {
        "id": item_id,
        "tags": tags,
        "data": {"type": "single_turn", "user_message": {"text": text}},
    }
    if reference is not None:
        doc["reference"] = reference
    task = next(iter(sorted(root.glob(f"tasks/{task_id} - *"))), None)
    task = task or root / "tasks" / f"{task_id} - T"
    _write(task / "eval_inputs" / item_id / "eval_input.kiln", doc)


# ------------------------------------------------------------------ tool ids


def test_added_tools_are_the_rag_tools_the_parent_lacks():
    parent = {"tools_config": {"tools": ["kiln_tool::rag::a", "kiln_tool::code::c"]}}
    child = {
        "tools_config": {
            "tools": ["kiln_tool::rag::a", "kiln_tool::rag::b", "kiln_task::t"]
        }
    }
    assert rag_lever.rag_tool_ids(child) == ("kiln_tool::rag::a", "kiln_tool::rag::b")
    assert rag_lever.added_tools(parent, child) == ("kiln_tool::rag::b",)
    assert rag_lever.added_tools(child, parent) == ()
    assert rag_lever.rag_tool_ids(None) == ()


# ------------------------------------------------------------------ the library


class TestTheLibrary:
    def test_the_scope_follows_kilns_tag_rule(self, tmp_path):
        add_document(tmp_path, "d1", "one", tags=("handbook",))
        add_document(tmp_path, "d2", "two", tags=("faq",))
        add_document(tmp_path, "d3", None, tags=("handbook",))
        every = rag_lever.read_scope(tmp_path, EXTRACTOR, None)
        assert [d.id for d in every.documents] == ["d1", "d2", "d3"]
        tagged = rag_lever.read_scope(tmp_path, EXTRACTOR, ["handbook"])
        assert [d.id for d in tagged.documents] == ["d1", "d3"]
        assert tagged.not_extracted == ["d3"]
        assert tagged.documents[0].text() == "one"
        assert rag_lever.library_tags(tmp_path) == {"": 3, "handbook": 2, "faq": 1}

    def test_an_extraction_of_another_extractor_does_not_count(self, tmp_path):
        add_document(tmp_path, "d1", "text", extractor="424242609999")
        assert rag_lever.read_scope(tmp_path, EXTRACTOR, None).not_extracted == ["d1"]


# ------------------------------------------------------------------ the index budget


class TestTheIndexBudget:
    def test_embeddings_that_exist_cost_nothing(self, tmp_path):
        add_document(tmp_path, "d1", "a" * 100, chunker=CHUNKER, embedding=EMBEDDING)
        add_document(tmp_path, "d2", "b" * 50, chunker=CHUNKER)
        scope = rag_lever.read_scope(tmp_path, EXTRACTOR, None)
        plan = rag_lever.index_plan(
            scope, chunker_id=CHUNKER, embedding_id=EMBEDDING, semantic=False
        )
        assert (plan.paid_documents, plan.paid_bytes) == (1, 50)
        new = rag_lever.index_plan(
            scope, chunker_id=None, embedding_id=EMBEDDING, semantic=False
        )
        assert (new.paid_documents, new.paid_bytes) == (2, 150)

    def test_a_semantic_chunker_pays_the_embedding_model_twice(self, tmp_path):
        add_document(tmp_path, "d1", "a" * 100)
        scope = rag_lever.read_scope(tmp_path, EXTRACTOR, None)
        plan = rag_lever.index_plan(
            scope, chunker_id=None, embedding_id=None, semantic=True
        )
        assert plan.paid_bytes == 200

    def test_over_the_cap_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_lever, "INDEX_MAX_BYTES", 120)
        add_document(tmp_path, "d1", "a" * 100)
        add_document(tmp_path, "d2", "b" * 100)
        scope = rag_lever.read_scope(tmp_path, EXTRACTOR, None)
        plan = rag_lever.index_plan(
            scope, chunker_id=None, embedding_id=None, semantic=False
        )
        assert "200 bytes" in (plan.refusal() or "")
        one = rag_lever.index_plan(
            rag_lever.Scope(scope.documents[:1]),
            chunker_id=None,
            embedding_id=None,
            semantic=False,
        )
        assert one.refusal() is None


# ------------------------------------------------------------------ the model rule


class TestTheModelRule:
    POLICY = ModelPolicy.from_json(
        {
            "allow": [{"provider": "openai", "model": "text-embedding-*"}],
            "deny": [{"provider": "groq", "model": "*", "reason": "no groq"}],
        }
    )
    IN_USE = {"embedding": {("gemini_api", "gemini-embedding-001")}, "reranker": set()}

    def test_the_policy_decides(self):
        rule = rag_lever.model_refusal
        assert (
            rule(self.POLICY, {}, "embedding", "openai", "text-embedding-3-small")
            is None
        )
        refusal = rule(self.POLICY, {}, "embedding", "together_ai", "m2-bert")
        assert refusal and "documents and every search query" in refusal
        assert rule(None, {}, "reranker", "x", "y") is None

    def test_a_model_the_project_already_uses_in_the_role_is_allowed(self):
        rule = rag_lever.model_refusal
        pair = ("gemini_api", "gemini-embedding-001")
        assert rule(self.POLICY, self.IN_USE, "embedding", *pair) is None
        # Another role is another use: the reranker never saw the documents.
        assert rule(self.POLICY, self.IN_USE, "reranker", *pair) is not None

    def test_a_deny_rule_wins_over_the_project_use(self):
        in_use = {"embedding": {("groq", "e")}}
        assert rag_lever.model_refusal(self.POLICY, in_use, "embedding", "groq", "e")

    def test_a_config_this_run_created_gives_no_exception(self):
        rows = [
            {
                "id": "own",
                "embedding_config": {"model_provider_name": "a", "model_name": "b"},
            },
            {
                "id": "old",
                "reranker_config": {"model_provider_name": "c", "model_name": "d"},
            },
        ]
        used = rag_lever.in_use_pairs(rows, ["own"])
        assert used["embedding"] == set() and used["reranker"] == {("c", "d")}


# ------------------------------------------------------------------ the document check


class TestTheDocumentCheck:
    def _items(self, tmp_path: Path) -> rag_lever.ItemTexts:
        add_eval_input(
            tmp_path, "t1", "va9", ["val_a"], HELD_OUT_INPUT, HELD_OUT_ANSWER
        )
        add_eval_input(tmp_path, "t1", "tr9", ["train_a"], "short input")
        return rag_lever.eval_item_texts(tmp_path, "t1")

    def test_every_split_is_read_and_short_texts_are_left_out(self, tmp_path):
        items = self._items(tmp_path)
        assert items.items == 2
        assert set(items.inputs) == {"va9"} and set(items.references) == {"va9"}

    def test_a_document_that_holds_an_eval_input_blocks(self, tmp_path):
        items = self._items(tmp_path)
        # A JSONL file of labelled cases, with the input in other case.
        text = json.dumps({"q": HELD_OUT_INPUT.upper() + "!", "label": "x"}) + "\n"
        add_document(tmp_path, "d1", text)
        add_document(tmp_path, "d2", "how to grow tomatoes in a cold spring")
        found = rag_lever.scan_documents(
            rag_lever.read_scope(tmp_path, EXTRACTOR, None).documents, items
        )
        assert found.item_documents == {"d1": 1}
        assert "document d1" in (found.block or "")
        # A finding names documents, never an item or its text.
        assert "va9" not in found.line and "tomato leaves" not in found.line
        assert found.scanned == 2

    def test_a_document_that_holds_an_expected_output_is_a_flag(self, tmp_path):
        items = self._items(tmp_path)
        add_document(tmp_path, "d1", f"Advice: {HELD_OUT_ANSWER}. Then wait.")
        found = rag_lever.scan_documents(
            rag_lever.read_scope(tmp_path, EXTRACTOR, None).documents, items
        )
        assert found.block is None
        assert found.reference_documents == {"d1": 1}
        assert "expected output" in (found.flag or "")
        # The flag comes from held-out items too: the line that loop agents
        # read never carries it.
        assert "expected output" not in found.line

    def test_part_of_an_input_is_not_the_input(self, tmp_path):
        items = self._items(tmp_path)
        add_document(tmp_path, "d1", "my tomato leaves turn yellow from the bottom")
        found = rag_lever.scan_documents(
            rag_lever.read_scope(tmp_path, EXTRACTOR, None).documents, items
        )
        assert found.block is None and found.item_documents == {}

    def test_a_structured_task_run_is_looked_for_by_its_values(self, tmp_path):
        # A structured task stores its input as JSON text. The document holds
        # the post, not the JSON with its keys.
        task = tmp_path / "tasks" / "t1 - T"
        _write(
            task / "runs" / "r1" / "task_run.kiln",
            {
                "id": "r1",
                "tags": ["test_b"],
                "input": json.dumps({"post": HELD_OUT_INPUT, "author": "x"}),
                "output": {"output": json.dumps({"advice": HELD_OUT_ANSWER})},
            },
        )
        add_document(tmp_path, "d1", f"Case 4: {HELD_OUT_INPUT}")
        add_document(tmp_path, "d2", f"Our advice: {HELD_OUT_ANSWER}")
        found = rag_lever.scan_documents(
            rag_lever.read_scope(tmp_path, EXTRACTOR, None).documents,
            rag_lever.eval_item_texts(tmp_path, "t1"),
        )
        assert found.item_documents == {"d1": 1}
        assert found.reference_documents == {"d2": 1}

    def test_a_task_run_with_a_tag_is_an_eval_item_too(self, tmp_path):
        task = tmp_path / "tasks" / "t1 - T"
        _write(
            task / "runs" / "r1" / "task_run.kiln",
            {"id": "r1", "tags": ["test_b"], "input": HELD_OUT_INPUT},
        )
        _write(
            task / "runs" / "r2" / "task_run.kiln",
            {
                "id": "r2",
                "tags": ["kiln_harness_tuner_gateway"],
                "input": "a b c d e f g h i",
            },
        )
        items = rag_lever.eval_item_texts(tmp_path, "t1")
        assert set(items.inputs) == {"r1"}

    def test_the_scan_stops_at_its_cap_and_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_lever, "SCAN_MAX_BYTES", 10)
        add_document(tmp_path, "d1", "x" * 20)
        found = rag_lever.scan_documents(
            rag_lever.read_scope(tmp_path, EXTRACTOR, None).documents,
            rag_lever.ItemTexts(),
        )
        assert not found.complete
        # A check that could not read every document blocks.
        assert "could not read the rest" in (found.block or "")
        assert found.block in found.line


# ------------------------------------------------------------------ the index run


def test_the_index_stream_and_its_state():
    stream = (
        'data: {"total_document_count": 2, "logs": []}\n\n'
        'data: {"total_document_count": 2, "total_document_completed_count": 2,'
        ' "total_chunk_count": 9, "total_chunk_completed_count": 9,'
        ' "total_document_embedded_error_count": 1,'
        ' "logs": [{"level": "error", "message": "rate limit"}]}\n\n'
    )
    last = rag_lever.stream_progress(stream)
    assert rag_lever.index_complete(last)
    assert rag_lever.progress_errors(last) == [
        "rate limit",
        "total_document_embedded_error_count: 1",
    ]
    assert not rag_lever.index_complete({"total_document_count": 2})
    # An index with no chunk is empty: a search returns nothing.
    assert not rag_lever.index_complete(
        {"total_document_count": 1, "total_document_completed_count": 1}
    )


# ------------------------------------------------------------------ the lever check


def _row(item: str, calls: list[str] | None) -> dict[str, Any]:
    if calls is None:
        return {"eval_input_id": item}
    trace = [{"role": "user", "content": "q"}] + [
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": name, "arguments": "{}"}}],
        }
        for name in calls
    ]
    return {"eval_input_id": item, "task_run_trace": json.dumps(trace)}


class TestTheLeverCheck:
    def test_one_call_is_applied(self):
        verdict = rag_lever.applied_verdict(
            {"a": _row("a", ["search_handbook"]), "b": _row("b", [])},
            {"search_handbook"},
        )
        assert verdict.status == "applied" and "1 of 2" in verdict.reason

    def test_no_call_on_enough_traces_is_not_applied(self):
        verdict = rag_lever.applied_verdict(
            {"a": _row("a", ["other"]), "b": _row("b", [])}, {"search_handbook"}
        )
        assert verdict.status == "not_applied"
        assert verdict.reason.startswith("lever not applied:")

    def test_too_few_traces_is_unverified(self):
        verdict = rag_lever.applied_verdict(
            {"a": _row("a", []), "b": _row("b", None)}, {"search_handbook"}
        )
        assert verdict.status == "unverified"


# ------------------------------------------------------------------ the gateway


def _rag_row(rag_id: str, **over: Any) -> dict[str, Any]:
    row = {
        "id": rag_id,
        "name": "Handbook",
        "tool_name": "search_handbook",
        "tool_description": "Search the gardening handbook.",
        "is_archived": False,
        "tags": ["handbook"],
        "extractor_config": {
            "id": EXTRACTOR,
            "model_provider_name": "gemini_api",
            "model_name": "gemini-2.5-flash",
        },
        "chunker_config": {
            "id": CHUNKER,
            "chunker_type": "fixed_window",
            "properties": {"chunk_size": 512, "chunk_overlap": 64},
        },
        "embedding_config": {
            "id": EMBEDDING,
            "model_provider_name": "openai",
            "model_name": "text-embedding-3-small",
        },
        "vector_store_config": {
            "id": STORE,
            "store_type": "lancedb_hybrid",
            "properties": {"similarity_top_k": 5},
        },
        "reranker_config": None,
    }
    row.update(over)
    return row


DONE = {
    "total_document_count": 1,
    "total_document_completed_count": 1,
    "total_chunk_count": 3,
    "total_chunk_completed_count": 3,
}


class Library:
    """The routes of a project with one extractor, one chunker, one
    embedding config, one vector store and one RAG config that existed
    before the run, and the writes of a create."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, Any]] = []
        self.rags: dict[str, dict[str, Any]] = {OLD_RAG: _rag_row(OLD_RAG)}
        self.progress: dict[str, Any] = dict(DONE)
        self.index_runs: list[str] = []

    def post(self, route: str, new_id: str):
        def handler(_query, body):
            self.posts.append((route, body))
            if route == "create_rag_config":
                self.rags[new_id] = _rag_row(new_id, **{"tool_name": body["tool_name"]})
            return {**(body or {}), "id": new_id}

        return handler

    def index(self, rag_id: str):
        def handler(_query, _body):
            self.index_runs.append(rag_id)
            return f"data: {json.dumps(self.progress)}\n\n"

        return handler

    def routes(self) -> dict[tuple[str, str], Any]:
        rags = self.rags
        return {
            ("GET", f"{P}/extractor_configs"): [
                {
                    "id": EXTRACTOR,
                    "extractor_type": "litellm",
                    "model_provider_name": "gemini_api",
                    "model_name": "gemini-2.5-flash",
                }
            ],
            ("GET", f"{P}/chunker_configs"): [
                {"id": CHUNKER, "chunker_type": "fixed_window", "properties": {}}
            ],
            ("GET", f"{P}/embedding_configs"): [
                {
                    "id": EMBEDDING,
                    "model_provider_name": "openai",
                    "model_name": "text-embedding-3-small",
                }
            ],
            ("GET", f"{P}/vector_store_configs"): [
                {"id": STORE, "store_type": "lancedb_hybrid"}
            ],
            ("GET", f"{P}/reranker_configs"): [],
            ("GET", f"{P}/rag_configs"): lambda _q, _b: list(rags.values()),
            ("GET", f"{P}/rag_configs/{OLD_RAG}"): lambda _q, _b: rags[OLD_RAG],
            ("GET", f"{P}/rag_configs/{NEW_RAG}"): lambda _q, _b: rags[NEW_RAG],
            ("GET", "/api/available_embedding_models"): [
                {
                    "provider_id": "openai",
                    "models": [{"id": "text-embedding-3-small", "name": "small"}],
                },
                {"provider_id": "together_ai", "models": [{"id": "m2-bert"}]},
            ],
            ("GET", "/api/available_reranker_models"): [
                {"provider_id": "together_ai", "models": [{"id": "rerank-x"}]}
            ],
            ("POST", f"{P}/create_chunker_config"): self.post("chunker", NEW_CHUNKER),
            ("POST", f"{P}/create_vector_store_config"): self.post("search", NEW_STORE),
            ("POST", f"{P}/rag_configs/create_rag_config"): self.post(
                "create_rag_config", NEW_RAG
            ),
            ("GET", f"{P}/rag_configs/{NEW_RAG}/run"): self.index(NEW_RAG),
            ("POST", f"{P}/rag_configs/progress"): lambda _q, body: {
                rid: self.progress for rid in body["rag_config_ids"]
            },
            ("PATCH", f"{P}/rag_configs/{NEW_RAG}"): lambda _q, body: (
                rags[NEW_RAG].update(body) or rags[NEW_RAG]
            ),
        }


CREATE = {
    "name": "Handbook: fixed 256",
    "tool_name": "search_handbook",
    "tool_description": "Search the gardening handbook.",
    "extractor_config_id": EXTRACTOR,
    "new_chunker": {
        "chunker_type": "fixed_window",
        "chunk_size": 256,
        "chunk_overlap": 32,
    },
    "embedding_config_id": EMBEDDING,
    "new_search": {"store_type": "lancedb_fts", "similarity_top_k": 4},
    "tags": ["handbook"],
}


def _library_gateway(tmp_path, lib: Library, policy=None, role="create_experiment_rc"):
    gateway, kiln = _gateway(tmp_path, lib.routes(), role=role, policy=policy)
    root = gateway.s.project_path
    assert root is not None
    add_document(root, "d1", "Water tomatoes at the base in the morning.")
    return gateway, kiln, root


class TestCreateRagConfig:
    def test_a_create_writes_the_parts_indexes_and_registers(self, tmp_path):
        lib = Library()
        gateway, _kiln, _root = _library_gateway(tmp_path, lib)
        made = _run(gateway, "create_rag_config", **CREATE)
        assert made["tool_id"] == f"kiln_tool::rag::{NEW_RAG}"
        assert made["created_parts"] == {"chunker": NEW_CHUNKER, "search": NEW_STORE}
        assert made["index"]["complete"] is True and lib.index_runs == [NEW_RAG]
        # Every new byte goes to the embedding model: the chunker is new.
        assert made["paid_documents"] == 1 and made["paid_bytes"] > 0
        assert "no eval item's input is in a document" in made["document_check"]
        routes = [r for r, _b in lib.posts]
        assert routes == ["chunker", "search", "create_rag_config"]
        rag_body = lib.posts[-1][1]
        assert rag_body["chunker_config_id"] == NEW_CHUNKER
        assert rag_body["vector_store_config_id"] == NEW_STORE
        assert rag_body["embedding_config_id"] == EMBEDDING
        assert rag_body["reranker_config_id"] is None
        assert rag_body["tags"] == ["handbook"]
        assert lib.posts[0][1]["properties"] == {
            "chunker_type": "fixed_window",
            "chunk_size": 256,
            "chunk_overlap": 32,
        }
        # The registry names every record, for the run-end step.
        registry = (gateway.s.run_dir / gw.CREATED_FILE).read_text()
        for key in (f"rag_config:{NEW_RAG}", f"rag_chunker_config:{NEW_CHUNKER}"):
            assert key in registry
        assert rag_lever.created_rag_configs(gateway.s.run_dir) == [NEW_RAG]

    def test_only_the_build_agent_creates(self, tmp_path):
        lib = Library()
        for role in ("probe", "batch_plan", "analyze_experiment_results"):
            gateway, _k, _r = _library_gateway(tmp_path / role, lib, role=role)
            with pytest.raises(gw.GatewayError, match="not available"):
                _run(gateway, "create_rag_config", **CREATE)

    def test_a_document_with_no_extraction_is_refused(self, tmp_path):
        lib = Library()
        gateway, _k, root = _library_gateway(tmp_path, lib)
        add_document(root, "d2", None)
        with pytest.raises(gw.GatewayError, match="starts no extraction"):
            _run(gateway, "create_rag_config", **CREATE)
        assert lib.posts == []

    def test_a_document_that_holds_an_eval_input_is_refused(self, tmp_path):
        lib = Library()
        gateway, _k, root = _library_gateway(tmp_path, lib)
        add_eval_input(root, TASK, "te9", ["test_a"], HELD_OUT_INPUT)
        add_document(root, "d2", f"Case 12. {HELD_OUT_INPUT}. Answer: wait.")
        with pytest.raises(gw.GatewayError, match="document d2") as refused:
            _run(gateway, "create_rag_config", **CREATE)
        assert "te9" not in str(refused.value)
        assert lib.posts == []

    def test_the_index_budget_is_checked_before_any_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_lever, "INDEX_MAX_BYTES", 5)
        lib = Library()
        gateway, _k, _r = _library_gateway(tmp_path, lib)
        with pytest.raises(gw.GatewayError, match="embedding model"):
            _run(gateway, "create_rag_config", **CREATE)
        assert lib.posts == []

    def test_a_model_outside_the_policy_is_refused(self, tmp_path):
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "anthropic", "model": "*"}]}
        )
        lib = Library()
        gateway, _k, _r = _library_gateway(tmp_path, lib, policy=policy)
        body = dict(CREATE, embedding_config_id=None)
        body["new_embedding"] = {
            "model_provider_name": "together_ai",
            "model_name": "m2-bert",
        }
        with pytest.raises(
            gw.GatewayError, match="embedding model together_ai/m2-bert"
        ):
            _run(gateway, "create_rag_config", **body)
        assert lib.posts == []

    def test_the_models_the_project_already_uses_are_allowed(self, tmp_path):
        # The policy allows no RAG model, but the RAG config that existed
        # before the run already sends the documents to these models.
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "anthropic", "model": "*"}]}
        )
        lib = Library()
        gateway, _k, _r = _library_gateway(tmp_path, lib, policy=policy)
        made = _run(gateway, "create_rag_config", **CREATE)
        assert made["tool_id"].endswith(NEW_RAG)
        parts = _run(gateway, "list_rag_parts")
        by_model = {o["model_name"]: o for o in parts["embedding_models"]}
        assert by_model["text-embedding-3-small"]["allowed"] is True
        assert by_model["m2-bert"]["allowed"] is False

    def test_a_model_kiln_does_not_offer_is_refused(self, tmp_path):
        lib = Library()
        gateway, _k, _r = _library_gateway(tmp_path, lib)
        body = dict(CREATE, embedding_config_id=None)
        body["new_embedding"] = {"model_provider_name": "openai", "model_name": "nope"}
        with pytest.raises(gw.GatewayError, match="offers no embedding model"):
            _run(gateway, "create_rag_config", **body)

    def test_one_run_creates_at_most_the_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_lever, "MAX_CREATED_PER_RUN", 1)
        lib = Library()
        gateway, _k, _r = _library_gateway(tmp_path, lib)
        _run(gateway, "create_rag_config", **CREATE)
        with pytest.raises(gw.GatewayError, match="already created 1"):
            _run(gateway, "create_rag_config", **CREATE)

    def test_a_part_needs_exactly_one_source(self, tmp_path):
        lib = Library()
        gateway, _k, _r = _library_gateway(tmp_path, lib)
        with pytest.raises(ValueError, match="exactly one"):
            _run(
                gateway, "create_rag_config", **dict(CREATE, chunker_config_id=CHUNKER)
            )

    def test_a_refused_index_run_still_names_the_config(self, tmp_path):
        lib = Library()
        routes = lib.routes()

        def refused(_q, _b):
            raise gw.KilnAPIError("GET", "run", 422, {"detail": "archived"})

        routes[("GET", f"{P}/rag_configs/{NEW_RAG}/run")] = refused
        gateway, _kiln = _gateway(tmp_path, routes)
        add_document(gateway.s.project_path, "d1", "Water at the base.")
        lib.progress = {}
        made = _run(gateway, "create_rag_config", **CREATE)
        assert made["tool_id"].endswith(NEW_RAG)
        assert made["index"]["complete"] is False
        assert "refused the index run" in made["index"]["note"]

    def test_an_index_that_did_not_end_is_run_again(self, tmp_path):
        lib = Library()
        lib.progress = {
            "total_document_count": 1,
            "logs": [{"level": "error", "message": "429"}],
        }
        gateway, _k, _r = _library_gateway(tmp_path, lib)
        made = _run(gateway, "create_rag_config", **CREATE)
        assert made["index"]["complete"] is False
        assert made["index"]["errors"] == ["429"]
        props = {"tools_config": {"tools": [made["tool_id"]]}}
        with pytest.raises(gw.GatewayError, match="not complete"):
            _run(gateway, "create_run_config", name="rc", run_config_properties=props)
        lib.progress = dict(DONE)
        assert _run(gateway, "index_rag_config", id=made["tool_id"])["complete"]
        assert lib.index_runs == [NEW_RAG, NEW_RAG]


class TestTheRunConfigRules:
    def _create(self, gateway, tool: str):
        props = {
            "type": "kiln_agent",
            "model_name": "m",
            "model_provider_name": "p",
            "tools_config": {"tools": [tool]},
        }
        return _run(
            gateway, "create_run_config", name="rc", run_config_properties=props
        )

    def test_an_unknown_rag_tool_is_refused_and_an_archived_one_is_not(self, tmp_path):
        lib = Library()
        lib.rags[OLD_RAG]["is_archived"] = True
        routes = lib.routes()

        def missing(_q, _b):
            raise gw.KilnAPIError("GET", "x", 404, {"detail": "not found"})

        routes[("GET", f"{P}/rag_configs/424242699999")] = missing
        routes[("POST", f"{P}/tasks/{TASK}/run_configs")] = lambda _q, body: {
            **body,
            "id": "424242600099",
        }
        gateway, _kiln = _gateway(tmp_path, routes)
        add_document(gateway.s.project_path, "d1", "Water at the base.")
        # Kiln still runs the search tool of an archived config, and a
        # baseline can attach one.
        assert self._create(gateway, f"kiln_tool::rag::{OLD_RAG}")["id"]
        with pytest.raises(gw.GatewayError, match="not a RAG config"):
            self._create(gateway, "kiln_tool::rag::424242699999")

    def test_a_config_whose_documents_hold_an_eval_item_is_never_reached(
        self, tmp_path
    ):
        """A RAG config of the user, under `tool_config` or a search: the
        gateway refuses to attach it, to drive it, and to search it."""
        lib = Library()
        gateway, _kiln, root = _library_gateway(tmp_path, lib)
        add_eval_input(root, TASK, "va9", ["val_a"], HELD_OUT_INPUT)
        add_document(root, "d2", f"Q: {HELD_OUT_INPUT}")
        tool = f"kiln_tool::rag::{OLD_RAG}"
        with pytest.raises(gw.GatewayError, match="document d2"):
            self._create(gateway, tool)
        with pytest.raises(gw.GatewayError, match="document d2") as refused:
            _run(gateway, "rag_search", id=tool, query="yellow leaves")
        assert "va9" not in str(refused.value)
        assert not any(path.endswith("/search") for _m, path, *_ in _kiln.calls)

    def test_only_a_config_this_run_created_is_indexed_or_archived(self, tmp_path):
        lib = Library()
        gateway, _k, _r = _library_gateway(tmp_path, lib)
        for op, args in (("index_rag_config", {}), ("archive_rag_config", {})):
            with pytest.raises(gw.GatewayError, match="not created through"):
                _run(gateway, op, id=OLD_RAG, **args)
        _run(gateway, "create_rag_config", **CREATE)
        rcs: list[dict[str, Any]] = [
            {"id": "rc_a", "run_config_properties": {"tools_config": {"tools": []}}}
        ]
        gateway.call.routes[("GET", f"{gateway.s.base}/run_configs")] = lambda _q, _b: (
            rcs
        )
        view = _run(gateway, "archive_rag_config", id=NEW_RAG)
        assert view["is_archived"] is True
        # A config that a run config attaches is never archived.
        rcs[0]["run_config_properties"]["tools_config"]["tools"] = [
            f"kiln_tool::rag::{NEW_RAG}"
        ]
        with pytest.raises(gw.GatewayError, match="attached by run config"):
            _run(gateway, "archive_rag_config", id=NEW_RAG)


def test_list_rag_parts_names_the_parts_and_the_limits(tmp_path):
    lib = Library()
    gateway, _k, root = _library_gateway(tmp_path, lib, role="batch_plan")
    add_document(root, "d2", None, tags=("faq",))
    parts = _run(gateway, "list_rag_parts")
    assert parts["documents"] == 2
    assert parts["document_tags"] == {"faq": 1, "handbook": 1}
    (extractor,) = parts["extractors"]
    assert extractor["id"] == EXTRACTOR and extractor["extracted_documents"] == 1
    assert extractor["used_by"] == [OLD_RAG]
    assert parts["max_created_per_run"] == rag_lever.MAX_CREATED_PER_RUN
    assert parts["reranker_models"][0]["model_name"] == "rerank-x"


# ------------------------------------------------------------------ the arm check


def _loop_ctx(tmp_path: Path, rags: dict[str, dict[str, Any]], parent_tools, tools):
    kiln = LoopKiln()
    kiln.rag_configs = rags
    kiln.run_configs = [
        {
            "id": "rc_base",
            "run_config_properties": {"tools_config": {"tools": parent_tools}},
        },
        {"id": "rc_new", "run_config_properties": {"tools_config": {"tools": tools}}},
    ]
    ctx = make_ctx(kiln)
    ctx.target = TargetBlock(
        project_path=str(tmp_path), project_id="p1", task_id="t1", kiln_host="http://h"
    )
    return ctx, kiln


def _rag_item():
    item = fix_item()
    item.work.technique = "rag"
    return item


def _check(ctx, item):
    async def go():
        exp = await ctx.memory.open_experiment(item, 1, BaselineRef(rc_id="rc_base"))
        return await _check_rag(ctx, item, exp, "rc_base", "rc_new")

    return asyncio.run(go())


class TestTheArmCheck:
    TOOL = f"kiln_tool::rag::{NEW_RAG}"

    def test_a_new_rag_tool_is_described_with_its_document_check(self, tmp_path):
        add_document(tmp_path, "d1", "Water tomatoes at the base.")
        ctx, _ = _loop_ctx(tmp_path, {NEW_RAG: _rag_row(NEW_RAG)}, [], [self.TOOL])
        change = _check(ctx, _rag_item())
        assert isinstance(change, RagChange)
        assert change.tool_names == frozenset({"search_handbook"})
        assert "search lancedb_hybrid top 5" in change.line
        assert "document check: 1 document(s) read" in change.line
        events = [f for k, f in ctx.log.events if k == "rag"]
        assert events[-1]["status"] == "changed"

    def test_the_lever_named_and_no_new_tool_is_not_applied(self, tmp_path):
        ctx, _ = _loop_ctx(tmp_path, {}, [self.TOOL], [self.TOOL])
        outcome = _check(ctx, _rag_item())
        assert outcome.status == "dead_end"
        (event,) = [f for k, f in ctx.log.events if k == "rag"]
        assert event["status"] == "not_applied"

    def test_another_lever_with_no_new_tool_is_left_alone(self, tmp_path):
        ctx, _ = _loop_ctx(tmp_path, {}, [], [])
        assert _check(ctx, fix_item()) is None

    def test_an_existing_rag_tool_whose_documents_hold_an_eval_item_ends_the_arm(
        self, tmp_path
    ):
        # Under `tool_config`, an arm that attaches a RAG config the run did
        # not create is held to the same rule.
        add_eval_input(tmp_path, "t1", "va9", ["val_a"], HELD_OUT_INPUT)
        add_document(tmp_path, "d1", f"Q: {HELD_OUT_INPUT}")
        ctx, _ = _loop_ctx(tmp_path, {NEW_RAG: _rag_row(NEW_RAG)}, [], [self.TOOL])
        item = fix_item()
        item.work.technique = "tool_config"
        outcome = _check(ctx, item)
        assert outcome.status == "dead_end"
        (event,) = [f for k, f in ctx.log.events if k == "rag"]
        assert event["status"] == "blocked" and "va9" not in event["line"]


# ------------------------------------------------------------------ the run end


def test_the_run_end_archives_what_no_run_config_attaches(tmp_path):
    ctx, kiln = _loop_ctx(tmp_path, {}, [], [f"kiln_tool::rag::{NEW_RAG}"])
    kiln.rag_configs = {
        NEW_RAG: _rag_row(NEW_RAG),
        "424242600021": _rag_row("424242600021"),
    }
    ctx.run_dir = tmp_path / "run"
    ctx.run_dir.mkdir()
    lines = [
        {"id": f"rag_config:{i}", "kind": "rag_config", "split": "meta", "eval_id": ""}
        for i in (NEW_RAG, "424242600021")
    ]
    (ctx.run_dir / gw.CREATED_FILE).write_text(
        "".join(json.dumps(line) + "\n" for line in lines)
    )
    archived = asyncio.run(rag_lever.close_rag_configs(ctx))
    assert archived == 1
    assert kiln.rag_config_patches == [("424242600021", {"is_archived": True})]
    (event,) = [f for k, f in ctx.log.events if k == "rag_configs"]
    records = {r["id"]: r for r in event["rag_configs"]}
    assert records[NEW_RAG]["attached_by"] == ["rc_new"]
    assert records[NEW_RAG]["archived"] is False
    assert records["424242600021"]["archived"] is True

    tracker = make_tracker()
    tracker.event("rag_configs", **event)
    status = tracker.snapshot()
    status.baseline_history = ["rc_base", "rc_new"]
    section = render_rag_appendix(status)
    assert section.startswith(RAG_APPENDIX_HEADING)
    assert "in the final baseline `rc_new`" in section
    assert "archived: no run config attaches it" in section
    assert "Kiln reports no cost for an embedding call" in section


def test_no_rag_config_no_section():
    tracker = make_tracker()
    assert render_rag_appendix(tracker.snapshot()) == ""
    assert RagConfigRecord(id="x", tool_id="kiln_tool::rag::x").summary == ""


def test_a_screen_or_a_probe_cannot_attach_a_rag_tool():
    from kiln_harness_tuner.schemas import ProbeCheck, ScreenCandidate

    tools = {"tools_config": {"tools": ["kiln_tool::rag::424242600020"]}}
    with pytest.raises(ValueError, match="technique: rag"):
        ScreenCandidate(id="s1-1", overrides=tools)
    with pytest.raises(ValueError, match="technique: rag"):
        ProbeCheck(overrides=tools, count="tool_calls")
    ScreenCandidate(
        id="s1-2", overrides={"tools_config": {"tools": ["kiln_tool::code::c"]}}
    )


class TestTheFixesOfTheReview:
    def test_every_new_part_is_sent_in_kilns_shape(self, tmp_path):
        lib = Library()
        routes = lib.routes()
        routes[("POST", f"{P}/create_embedding_config")] = lib.post(
            "embedding", "424242600013"
        )
        routes[("POST", f"{P}/create_reranker_config")] = lib.post(
            "reranker", "424242600015"
        )
        routes[("GET", "/api/available_reranker_models")] = [
            {"provider_id": "together_ai", "models": [{"id": "rerank-x"}]}
        ]
        gateway, _kiln = _gateway(tmp_path, routes)
        add_document(gateway.s.project_path, "d1", "Water at the base.")
        body = dict(CREATE, embedding_config_id=None)
        body["new_embedding"] = {
            "model_provider_name": "openai",
            "model_name": "text-embedding-3-small",
            "dimensions": 256,
        }
        body["new_chunker"] = {
            "chunker_type": "semantic",
            "buffer_size": 1,
            "breakpoint_percentile_threshold": 95,
        }
        body["new_reranker"] = {
            "model_provider_name": "together_ai",
            "model_name": "rerank-x",
            "top_n": 3,
        }
        made = _run(gateway, "create_rag_config", **body)
        sent = dict(lib.posts)
        assert sent["embedding"]["properties"] == {"dimensions": 256}
        # The semantic chunker finds its breaks with the new embedding.
        assert sent["chunker"]["properties"] == {
            "chunker_type": "semantic",
            "embedding_config_id": "424242600013",
            "buffer_size": 1,
            "breakpoint_percentile_threshold": 95,
        }
        assert sent["reranker"]["properties"] == {"type": "cohere_compatible"}
        assert sent["reranker"]["top_n"] == 3
        assert sent["create_rag_config"]["reranker_config_id"] == "424242600015"
        # A semantic chunker pays the embedding model twice.
        assert made["paid_bytes"] == 2 * len("Water at the base.")

    def test_the_index_wait_has_a_hard_bound(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_lever, "INDEX_WAIT_TIMEOUT_SECS", 0.05)
        lib = Library()
        lib.progress = {"total_document_count": 1}
        gateway, kiln, _root = _library_gateway(tmp_path, lib)

        async def slow(method, path, query_params=None, body=None, **options):
            if path.endswith("/run"):
                await asyncio.sleep(5)
            return await kiln(method, path, query_params, body, **options)

        gateway.call = slow
        made = _run(gateway, "create_rag_config", **CREATE)
        assert made["index"]["complete"] is False
        assert "did not end" in made["index"]["note"]

    def test_the_extractor_shows_whether_the_policy_allows_it(self, tmp_path):
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "anthropic", "model": "*"}]}
        )
        lib = Library()
        lib.rags.clear()  # no RAG config uses the extractor's model yet
        gateway, _k, _r = _library_gateway(tmp_path, lib, policy=policy)
        (extractor,) = _run(gateway, "list_rag_parts")["extractors"]
        assert extractor["allowed"] is False
        assert "extractor model gemini_api/gemini-2.5-flash" in extractor["note"]

    def test_an_unreadable_listing_fails_the_arm_as_infra(self, tmp_path, monkeypatch):
        ctx, _kiln = _loop_ctx(tmp_path, {}, [], [])

        async def failing(_ctx):
            raise gw.KilnError("down")

        import kiln_harness_tuner.probes as probes

        monkeypatch.setattr(probes, "task_run_configs", failing)
        outcome = _check(ctx, _rag_item())
        assert outcome.status == "failed_infra"
        assert outcome.infra_error.error_class == "provider_error"

    def test_the_run_end_archives_nothing_when_it_cannot_list(self, tmp_path):
        ctx, kiln = _loop_ctx(tmp_path, {NEW_RAG: _rag_row(NEW_RAG)}, [], [])
        kiln.run_configs = None  # a drifted listing: no rows
        ctx.run_dir = tmp_path / "run"
        ctx.run_dir.mkdir()
        line = {"id": f"rag_config:{NEW_RAG}", "kind": "rag_config", "split": "meta"}
        (ctx.run_dir / gw.CREATED_FILE).write_text(json.dumps(line) + "\n")

        async def failing():
            raise gw.KilnError("down")

        import kiln_harness_tuner.probes as probes

        original = probes.task_run_configs
        probes.task_run_configs = lambda _ctx: failing()
        try:
            assert asyncio.run(rag_lever.close_rag_configs(ctx)) == 0
        finally:
            probes.task_run_configs = original
        assert kiln.rag_config_patches == []
