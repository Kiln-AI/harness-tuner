"""The loop gateway: loop agents reach Kiln only through typed
operations that answer from TRAIN records, field by field.

Kiln responses here follow the vendored API docs' schemas (EvalRun and its
results wrapper, TaskRun, memory listings, run configs, the task) and carry
held-out records at the top level, nested, and in lists, plus fields no
gateway model names. Every id is invented."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from test_quarantine import TASK, Project

from kiln_harness_tuner.gateway import hook
from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.gateway.server import (
    POLICY_FILE,
    create_server,
    load_policy,
    load_state,
)
from kiln_harness_tuner.gateway.trainset import (
    FILE_NAME,
    TrainSet,
    TrainSetError,
    compute,
)
from kiln_harness_tuner.kiln import JOB_WAIT_MARGIN_SECS, KilnAPIError, KilnError
from kiln_harness_tuner.model_catalog import (
    Catalog,
    CatalogModel,
    Source,
    write_catalog,
)
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.quarantine import QuarantineError

PROJECT = "424242748186"
BASE = f"/api/projects/{PROJECT}/tasks/{TASK}"


# ------------------------------------------------------------ the train set


def _project(tmp_path: Path) -> Project:
    p = Project(tmp_path / "proj")
    p.eval("e1", "a")  # train_a / val_a / test_a
    p.eval(
        "e2",
        "b",
        {
            "train": {"source": "eval_input", "filter_id": "tag::train_b"},
            "golden_check": {"source": "eval_input", "filter_id": "tag::kept_b"},
        },
    )
    p.eval_input("tr1", ["train_a"], text="train question one")
    p.eval_input("tr2", ["train_a", "train_b"], text="train question two")
    p.eval_input("va1", ["val_a"], text="SECRET val question")
    p.eval_input("both", ["train_a", "val_a"])  # train and val of one eval
    p.eval_input("cross", ["train_b", "test_a"])  # train for e2, test for e1
    p.eval_input("orphan", ["train_a", "holdout_2023"])  # no eval uses the tag
    p.eval_input("kept", ["train_a", "kept_b"])  # a non-train split of any name
    return p


class TestTheTrainSet:
    def test_only_provable_train_items(self, tmp_path):
        p = _project(tmp_path)
        train = compute(p.root, TASK)
        assert train.eval_inputs == {"tr1", "tr2"}
        assert train.per_eval == {"e1": {"tr1", "tr2"}, "e2": {"tr2"}}
        assert {"val_a", "test_a", "kept_b", "holdout_2023"} <= train.heldout_tags

    @pytest.mark.parametrize("filter_id", ["high_rating", "multi_filter::a&b", "tag::"])
    def test_a_filter_it_cannot_evaluate_refuses_the_start(self, tmp_path, filter_id):
        p = _project(tmp_path)
        p.eval("e3", "c", {"test": {"source": "eval_input", "filter_id": filter_id}})
        with pytest.raises((TrainSetError, QuarantineError), match="fix: "):
            compute(p.root, TASK)

    def test_a_tag_that_is_train_for_one_eval_and_val_for_another_is_out(
        self, tmp_path
    ):
        # The tag is a train tag, so it is not a held-out TAG; the items are
        # still in a non-train split, and that alone takes them out.
        p = _project(tmp_path)
        p.eval(
            "e3", "c", {"val": {"source": "eval_input", "filter_id": "tag::train_b"}}
        )
        train = compute(p.root, TASK)
        assert "train_b" not in train.heldout_tags
        assert train.eval_inputs == {"tr1"}

    def test_the_train_set_refuses_a_filter_on_its_own(self):
        # Not only through the quarantine, which reads the same filters first.
        from kiln_harness_tuner.gateway.trainset import _select

        with pytest.raises(TrainSetError, match="fix: "):
            _select("high_rating", {"x": {"tags": ["high_rating"]}}, "eval e9")

    def test_a_train_split_of_all_items_refuses_the_start(self, tmp_path):
        p = _project(tmp_path)
        p.eval("e3", "c", {"train": {"source": "eval_input", "filter_id": "all"}})
        with pytest.raises(TrainSetError, match="'all'"):
            compute(p.root, TASK)

    def test_a_tag_a_train_split_uses_is_not_excluded(self, tmp_path):
        p = _project(tmp_path)
        p.eval(
            "e4", "d", {"train": {"source": "eval_input", "filter_id": "tag::val_x"}}
        )
        p.eval_input("vx", ["val_x"])
        assert "vx" in compute(p.root, TASK).eval_inputs

    def test_round_trip_and_a_bad_file(self, tmp_path):
        train = compute(_project(tmp_path).root, TASK)
        path = tmp_path / "train_set.json"
        train.write(path)
        assert TrainSet.load(path) == train
        path.write_text('{"version": 2}')
        with pytest.raises(TrainSetError, match="fix: "):
            TrainSet.load(path)


# ------------------------------------------------------------ a fake Kiln


class FakeKiln:
    """Routes (method, path) to canned responses; records every call."""

    def __init__(self, routes: dict[tuple[str, str], Any]):
        self.routes = routes
        self.calls: list[tuple[str, str, Any, Any]] = []
        # The keyword options of each call (timeout, retries), by path.
        self.options: dict[str, dict[str, Any]] = {}

    async def __call__(self, method, path, query_params=None, body=None, **options):
        self.calls.append((method, path, query_params, body))
        self.options[path] = options
        value = self.routes.get((method, path))
        if callable(value):
            return value(query_params, body)
        if value is None:
            raise AssertionError(f"unrouted {method} {path}")
        return value


def _gateway(tmp_path, routes, role="create_experiment_rc", policy=None):
    p = _project(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    state = gw.State(
        project_id=PROJECT,
        task_id=TASK,
        role=role,
        train=compute(p.root, TASK),
        run_dir=run_dir,
        project_path=p.root,
        policy=policy,
    )
    kiln = FakeKiln(routes)
    return gw.Gateway(kiln, state), kiln


def _eval_run(item: str, run_id: str, output: str) -> dict:
    return {
        "v": 1,
        "id": run_id,
        "eval_input_id": item,
        "task_run_config_id": "424242100003",
        "input": f"input of {item}",
        "output": output,
        "scores": {"accuracy": 1.0},
        "task_run_usage": {"cost": 0.01, "total_llm_latency_ms": 900.0},
        "a_field_kiln_adds_tomorrow": "SECRET",
    }


def _run(gateway, op, **arguments):
    return asyncio.run(gw.dispatch(gateway, gateway.s.role, op, arguments))


def _clean(value: Any) -> None:
    text = json.dumps(value)
    for secret in ("SECRET", "va1", "cross", "orphan", "kept", "both"):
        assert secret not in text, secret


RESULTS = f"{BASE}/evals/e1/eval_config/ec1/run_config/424242100003/results"


class TestReads:
    ROUTES = {
        ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
        ("GET", RESULTS): {
            "results": [
                {
                    "eval_run": _eval_run("tr1", "er1", "fine"),
                    "input": "train question one",
                    "output": "fine",
                    "task_run_trace": "[]",
                },
                {
                    "eval_run": _eval_run("va1", "er2", "SECRET"),
                    "input": "SECRET val question",
                    "output": "SECRET",
                },
                _eval_run("cross", "er3", "SECRET"),  # an unwrapped row
                {"eval_run": {"id": "er4", "input": "SECRET"}, "output": "SECRET"},
            ]
        },
    }

    def test_train_results_carry_train_rows_only(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, self.ROUTES)
        out = _run(
            gateway, "get_train_results", eval_id="e1", run_config_id="424242100003"
        )
        _clean(out)
        assert [r["item_id"] for r in out["rows"]] == ["tr1"]
        assert out["dropped"] == 3
        assert "a_field_kiln_adds_tomorrow" not in json.dumps(out)
        assert out["rows"][0]["latency_ms"] == 900.0
        # The gateway, not the agent, names the split.
        assert kiln.calls[-1][2] == {"split": "train"}

    def test_a_task_run_must_be_provably_train(self, tmp_path):
        run = {
            "id": "424242600001",
            "input": "SECRET val question",
            "output": {
                "output": "SECRET",
                "source": {"properties": {"model_name": "m"}},
            },
            "eval_source": {"source_type": "eval_input", "source_id": "va1"},
        }
        train_run = {
            "id": "424242600002",
            "input": "train question one",
            "output": {"output": "ok"},
            "eval_source": {"source_type": "eval_input", "source_id": "tr1"},
            "trace": [{"role": "assistant", "content": "ok", "unknown": "SECRET"}],
            "usage": {"output_tokens": 12, "cost": 0.01, "provider_secret": "SECRET"},
        }
        unlinked = {"id": "424242600003", "input": "SECRET", "output": {"output": "x"}}
        routes = {
            ("GET", f"{BASE}/runs/424242600001"): run,
            ("GET", f"{BASE}/runs/424242600002"): train_run,
            ("GET", f"{BASE}/runs/424242600003"): unlinked,
        }
        gateway, _ = _gateway(tmp_path, routes)
        for refused in ("424242600001", "424242600003"):
            with pytest.raises(gw.GatewayError, match="not provably"):
                _run(gateway, "get_task_run", id=refused)
        out = _run(gateway, "get_task_run", id="424242600002")
        assert out["item_id"] == "tr1" and out["output"] == "ok"
        assert "SECRET" not in json.dumps(out)

    def test_train_items_come_from_the_train_set_only(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {})
        out = _run(gateway, "list_train_items")
        assert [i["id"] for i in out] == ["tr1", "tr2"]
        assert out[0]["plaintext_input"] == "train question one"
        _clean(out)

    def test_expected_outputs_come_from_the_train_set_only(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {})
        p = Project(tmp_path / "proj")
        p.eval_input(
            "tr1", ["train_a", "rule_r4"], reference={"expected": {"total": 1080}}
        )
        p.eval_input("va1", ["val_a"], reference={"expected": "SECRET"})
        out = _run(gateway, "get_train_expected")["items"]
        assert [i["id"] for i in out] == ["tr1", "tr2"]
        assert out[0]["reference"] == {"expected": {"total": 1080}}
        assert out[0]["eval_ids"] == ["e1"]
        assert out[0]["tags"] == ["train_a", "rule_r4"]
        assert out[1]["reference"] is None  # an input with no reference
        _clean(out)

    def test_expected_outputs_by_id_refuse_a_held_out_item(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {})
        p = Project(tmp_path / "proj")
        p.eval_input("tr1", ["train_a"], reference={"expected": "a"})
        out = _run(gateway, "get_train_expected", item_ids=["tr1"])["items"]
        assert [i["reference"] for i in out] == [{"expected": "a"}]
        for refused in ("va1", "both", "cross", "nonexistent"):
            with pytest.raises(gw.GatewayError, match="not provably"):
                _run(gateway, "get_train_expected", item_ids=["tr1", refused])

    def test_expected_outputs_per_eval(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {})
        out = _run(gateway, "get_train_expected", eval_id="e2")["items"]
        assert [i["id"] for i in out] == ["tr2"]

    def test_a_large_expected_output_is_clipped(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {})
        p = Project(tmp_path / "proj")
        p.eval_input("tr1", ["train_a"], reference={"x": "y" * 30_000})
        (first, _second) = _run(gateway, "get_train_expected")["items"]
        assert first["clipped"] is True
        assert len(first["reference"]) == gw.EXPECTED_MAX_CHARS
        # The whole reference is in a file under the trace folder.
        whole = json.loads(Path(first["reference_path"]).read_text())
        assert whole == {"x": "y" * 30_000}
        assert Path(first["reference_path"]).is_relative_to(gateway.s.trace_root)

    def test_projection_drops_what_the_models_do_not_name(self, tmp_path):
        routes = {
            ("GET", BASE): {
                "id": TASK,
                "name": "T",
                "instruction": "Do it.",
                "requirements": [{"name": "r", "instruction": "i", "secret": "SECRET"}],
                "path": "/SECRET/on/disk",
            },
            ("GET", f"{BASE}/run_configs"): [
                {
                    "id": "rc1",
                    "name": "base",
                    "run_config_properties": {
                        "model_name": "m",
                        "model_provider_name": "p",
                        "new_kiln_knob": "SECRET",
                    },
                    "path": "/SECRET",
                }
            ],
        }
        gateway, _ = _gateway(tmp_path, routes)
        task = _run(gateway, "get_task")
        rcs = _run(gateway, "list_run_configs")
        assert "SECRET" not in json.dumps([task, rcs])
        assert rcs[0]["model_name"] == "m"

    def test_the_models_are_the_policys(self, tmp_path):
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "openai", "model": "*"}]}
        )
        routes = {
            ("GET", "/api/available_models"): [
                {"provider_id": "openai", "models": [{"id": "gpt", "name": "GPT"}]},
                {"provider_id": "groq", "models": [{"id": "kimi"}]},
            ]
        }
        gateway, _ = _gateway(tmp_path, routes, policy=policy)
        out = _run(gateway, "list_models")
        assert [
            (p["provider"], r["model"]) for r in out["models"] for p in r["providers"]
        ] == [("openai", "gpt")]
        out = _run(gateway, "list_models", include_not_available=True)
        statuses = {r["model"]: r["providers"][0]["status"] for r in out["models"]}
        assert statuses == {"gpt": "ready", "kimi": "blocked_by_policy"}


class TestThePolicyCopy:
    """The gateway reads `runs/<run_id>/model_policy.json`. An unreadable copy
    refuses every model and names the file. It does not stop the gateway."""

    @pytest.mark.parametrize(
        "text",
        ["{bad", '{"allow": []}', '{"allow": [{"provider": "*"}]}', "[]"],
    )
    def test_an_invalid_copy_refuses_every_model(self, tmp_path, text):
        path = tmp_path / POLICY_FILE
        path.write_text(text)
        self._refuses_every_model(load_policy(tmp_path), path)

    def test_a_copy_that_is_not_utf8_refuses_every_model(self, tmp_path):
        path = tmp_path / POLICY_FILE
        path.write_bytes(b"\xff\xfe{")
        self._refuses_every_model(load_policy(tmp_path), path)

    def test_a_copy_that_is_a_folder_refuses_every_model(self, tmp_path):
        path = tmp_path / POLICY_FILE
        path.mkdir()
        self._refuses_every_model(load_policy(tmp_path), path)

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="root reads a file with no read permission",
    )
    def test_a_copy_with_no_read_permission_refuses_every_model(self, tmp_path):
        path = tmp_path / POLICY_FILE
        path.write_text(json.dumps({"allow": [{"provider": "*", "model": "*"}]}))
        path.chmod(0)
        try:
            policy = load_policy(tmp_path)
        finally:
            path.chmod(0o600)
        self._refuses_every_model(policy, path)

    @staticmethod
    def _refuses_every_model(policy: ModelPolicy | None, path: Path) -> None:
        assert policy is not None
        for provider, model in [("openai", "gpt"), ("groq", "kimi"), ("x", "y")]:
            reason = policy.refusal(provider, model)
            assert reason is not None
            assert str(path) in reason
        assert policy.refusal(None, None) is not None
        assert str(path) in policy.summary()["description"]

    def test_the_gateway_refuses_a_run_config_under_an_unreadable_copy(self, tmp_path):
        (tmp_path / POLICY_FILE).write_text("{bad")
        gateway, kiln = _gateway(tmp_path, {}, policy=load_policy(tmp_path))
        props = {
            "type": "kiln_agent",
            "model_provider_name": "openai",
            "model_name": "gpt",
        }
        with pytest.raises(gw.GatewayError, match="unreadable"):
            _run(gateway, "create_run_config", name="n", run_config_properties=props)
        assert kiln.calls == []

    def test_no_copy_or_an_empty_copy_is_no_policy(self, tmp_path):
        assert load_policy(tmp_path) is None
        (tmp_path / POLICY_FILE).write_text("  \n")
        assert load_policy(tmp_path) is None

    def test_a_readable_copy_is_the_policy(self, tmp_path):
        (tmp_path / POLICY_FILE).write_text(
            json.dumps({"allow": [{"provider": "openai", "model": "*"}]})
        )
        policy = load_policy(tmp_path)
        assert policy is not None
        assert policy.refusal("openai", "gpt") is None
        assert policy.refusal("groq", "kimi") is not None


class TestTheCatalogueSnapshot:
    """The gateway process reads `runs/<run_id>/model_catalog.json` at start,
    as it reads the policy copy."""

    @staticmethod
    def _load_state(tmp_path: Path, snapshot: Catalog | None) -> gw.State:
        p = _project(tmp_path)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        compute(p.root, TASK).write(run_dir / FILE_NAME)
        if snapshot is not None:
            write_catalog(run_dir, snapshot)
        args = argparse.Namespace(
            run_dir=str(run_dir),
            project_id=PROJECT,
            task_id=TASK,
            role="batch_plan",
            project_path=None,
        )
        return load_state(args)

    def test_load_state_reads_the_catalogue_snapshot(self, tmp_path):
        snapshot = Catalog(
            fetched_at="2026-11-13T12:00:00+00:00",
            kiln=Source(state="ok"),
            models=[CatalogModel(model="m1", display_name="M1")],
        )
        state = self._load_state(tmp_path, snapshot)
        assert state.catalog == snapshot

    def test_no_snapshot_loads_as_unavailable(self, tmp_path):
        state = self._load_state(tmp_path, None)
        assert state.catalog == Catalog.unavailable("no snapshot")


class TestEvalListShapes:
    """The live eval list is wrapped (`{"evals": [...], "load_error_count": N}`);
    older servers return the bare list. A reader that only knows the bare list
    iterates the wrapper's keys and silently lists no evals."""

    EVAL = {"id": "e1", "name": "Accuracy", "current_config_id": "ec1"}

    def _listed(self, tmp_path, response):
        gateway, _ = _gateway(
            tmp_path, {("GET", f"{BASE}/evals"): response}, role="batch_plan"
        )
        return _run(gateway, "list_evals")

    def test_wrapped_list_is_read(self, tmp_path):
        rows = self._listed(tmp_path, {"evals": [self.EVAL], "load_error_count": 0})
        assert [r["id"] for r in rows] == ["e1"]

    def test_bare_list_is_still_read(self, tmp_path):
        rows = self._listed(tmp_path, [self.EVAL])
        assert [r["id"] for r in rows] == ["e1"]


class TestMemory:
    LISTING = {
        "listings": [
            {
                "id": "m1",
                "overview": "an issue",
                "tags": ["issue"],
                "scope": f"task::{TASK}",
            },
            {
                "id": "m2",
                "overview": "Validation metrics",
                "tags": ["val"],
                "scope": f"task::{TASK}",
            },
        ],
        "matched": 2,
    }

    def _routes(self):
        return {
            ("GET", f"/api/projects/{PROJECT}/memories"): self.LISTING,
            ("GET", f"/api/projects/{PROJECT}/memories/by_ids"): self.LISTING[
                "listings"
            ]
            + [
                {"id": "m3", "overview": "x", "scope": "task::424242999999", "tags": []}
            ],
        }

    def test_the_val_record_is_hidden_from_agents(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._routes(), role="batch_plan")
        out = _run(gateway, "memory_list", scope=f"task::{TASK}")
        assert [r["id"] for r in out["listings"]] == ["m1"]
        with pytest.raises(gw.GatewayError, match="`val`"):
            _run(gateway, "memory_list", scope=f"task::{TASK}", tags=["val"])
        got = _run(gateway, "memory_get", ids=["m1", "m2", "m3"])
        assert [r["id"] for r in got] == ["m1"]  # val hidden, other task dropped

    def test_the_val_readers_see_it(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._routes(), role="promote_best")
        out = _run(gateway, "memory_list", scope=f"task::{TASK}", tags=["val"])
        assert len(out["listings"]) == 2

    def test_scopes_and_the_val_tag_on_writes(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {}, role="batch_plan")
        with pytest.raises(gw.GatewayError, match="scope must be"):
            _run(gateway, "memory_list", scope="task::424242999999")
        with pytest.raises(gw.GatewayError, match="loop's code only"):
            _run(gateway, "memory_create", overview="o", scope="project", tags=["val"])
        # A refused write leaves no memory line.
        assert not (tmp_path / "run" / gw.MEMORY_FILE).exists()

    LESSON = {
        "id": "m9",
        "overview": "LESSON: a skill file is read only when named",
        "content": "the whole lesson, which the line leaves out",
        "tags": ["lesson", "skill"],
        "scope": f"task::{TASK}",
    }

    def _write_routes(self):
        updated = {**self.LESSON, "tags": ["dead_end"], "overview": "DEAD END: x"}
        return {
            ("POST", f"/api/projects/{PROJECT}/memories"): self.LESSON,
            ("GET", f"/api/projects/{PROJECT}/memories/by_ids"): [self.LESSON],
            ("PATCH", f"/api/projects/{PROJECT}/memories/m9"): updated,
        }

    def _lines(self, tmp_path) -> list[dict]:
        text = (tmp_path / "run" / gw.MEMORY_FILE).read_text()
        return [json.loads(line) for line in text.splitlines()]

    def test_each_agent_memory_write_leaves_one_line(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._write_routes(), role="batch_plan")
        _run(
            gateway,
            "memory_create",
            overview=self.LESSON["overview"],
            scope=f"task::{TASK}",
            content=self.LESSON["content"],
            tags=["lesson", "skill"],
        )
        _run(gateway, "memory_update", id="m9", tags=["dead_end"])
        create, update = self._lines(tmp_path)
        assert {k: v for k, v in create.items() if k != "ts"} == {
            "op": "create",
            "id": "m9",
            "role": "batch_plan",
            "scope": f"task::{TASK}",
            "tags": ["lesson", "skill"],
            "overview": "LESSON: a skill file is read only when named",
        }
        datetime.fromisoformat(create["ts"])
        # The update's line is the record as the write answered it.
        assert update["op"] == "update"
        assert (update["tags"], update["overview"]) == (["dead_end"], "DEAD END: x")
        assert "content" not in create and "content" not in update

    def test_a_failed_append_does_not_fail_the_write(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._write_routes(), role="batch_plan")
        (tmp_path / "run" / gw.MEMORY_FILE).mkdir()  # os.open fails on it
        out = _run(
            gateway, "memory_create", overview="o", scope="project", tags=["lesson"]
        )
        assert out["id"] == "m9"


class TestWrites:
    def test_a_run_config_outside_the_policy_is_refused(self, tmp_path):
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "openai", "model": "*"}]}
        )
        gateway, kiln = _gateway(tmp_path, {}, policy=policy)
        props = {
            "type": "kiln_agent",
            "model_provider_name": "groq",
            "model_name": "kimi",
        }
        with pytest.raises(gw.GatewayError, match="outside the model policy"):
            _run(gateway, "create_run_config", name="n", run_config_properties=props)
        with pytest.raises(gw.GatewayError, match="kiln_agent"):
            _run(
                gateway,
                "create_run_config",
                name="n",
                run_config_properties={"type": "mcp", "tool_id": "x"},
            )
        assert kiln.calls == []

    def test_an_agent_name_kiln_would_refuse_is_made_valid(self, tmp_path):
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "gemini_api", "model": "*"}]}
        )
        sent = {}

        def create(_query, body):
            sent.update(body)
            return {"id": "424242600020", **body}

        gateway, _ = _gateway(
            tmp_path, {("POST", f"{BASE}/run_configs"): create}, policy=policy
        )
        props = {
            "type": "kiln_agent",
            "model_provider_name": "gemini_api",
            "model_name": "gemini_3_8_flash",
        }
        _run(
            gateway,
            "create_run_config",
            name="gemini 3.8 flash, thinking: low",
            run_config_properties=props,
        )
        assert sent["name"] == "gemini 3 8 flash thinking low"

    def test_a_drive_runs_on_a_train_item_with_its_own_input(self, tmp_path):
        rc = {
            "id": "rc1",
            "run_config_properties": {"model_name": "m", "model_provider_name": "p"},
        }
        routes = {
            ("GET", f"{BASE}/run_configs"): [rc],
            ("GET", BASE): {"id": TASK, "turn_mode": "single_turn"},
            ("POST", f"{BASE}/run"): {"id": "424242600010", "output": {"output": "ok"}},
        }
        gateway, kiln = _gateway(tmp_path, routes)
        out = _run(gateway, "run_task", run_config_id="rc1", item_id="tr1")
        (_m, _p, _q, body) = kiln.calls[-1]
        assert body["plaintext_input"] == "train question one"
        assert out["item_id"] == "tr1"
        # The drive is now a record this run created on a train item.
        assert gateway.s.created["424242600010"] == ("task_run", "train", "tr1")
        registry = (tmp_path / "run" / gw.CREATED_FILE).read_text()
        assert "424242600010" in registry
        for refused in ("va1", "cross", "orphan", "nope"):
            with pytest.raises(gw.GatewayError, match="not a train item"):
                _run(gateway, "run_task", run_config_id="rc1", item_id=refused)
        with pytest.raises(gw.GatewayError, match="held-out split"):
            _run(
                gateway, "run_task", run_config_id="rc1", item_id="tr1", tags=["val_x"]
            )

    def test_invalid_requests_never_reach_kiln(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, {})
        for op, args in [
            ("run_task", {"run_config_id": "rc1", "item_id": "a b/../c"}),
            ("run_task", {"run_config_id": "rc1", "item_id": "tr1", "input": "x"}),
            ("memory_create", {"overview": "x" * 300, "scope": "project"}),
            ("get_train_results", {"eval_id": "e1"}),
        ]:
            with pytest.raises(ValueError):
                _run(gateway, op, **args)
        assert kiln.calls == []

    def test_an_eval_job_runs_on_train_items_only(self, tmp_path):
        routes = {
            ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
            ("POST", "/api/jobs/evals/run"): {"job_id": "j1"},
            ("POST", "/api/jobs/wait"): [
                {"status": "succeeded", "progress": {"success": 2, "total": 2}}
            ],
        }
        gateway, kiln = _gateway(tmp_path, routes, role="eval_build")
        with pytest.raises(gw.GatewayError, match="not train items"):
            _run(
                gateway,
                "run_train_eval",
                eval_id="e1",
                run_config_id="rc1",
                item_ids=["va1"],
            )
        out = _run(gateway, "run_train_eval", eval_id="e1", run_config_id="rc1")
        job_body = next(b for m, p, q, b in kiln.calls if p == "/api/jobs/evals/run")
        assert job_body["split"] == "train"
        assert job_body["item_ids"] == ["tr1", "tr2"]
        assert out["success"] == 2

    def test_an_eval_job_wait_outlasts_the_default_read_timeout(self, tmp_path):
        """Kiln holds `POST /api/jobs/wait` for up to the `timeout` in the
        body. The read timeout of the call must be longer, or the client's
        default 60 s ends the wait while the job still runs."""
        routes = {
            ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
            ("POST", "/api/jobs/evals/run"): {"job_id": "j1"},
            ("POST", "/api/jobs/wait"): [
                {"status": "succeeded", "progress": {"success": 2, "total": 2}}
            ],
        }
        gateway, kiln = _gateway(tmp_path, routes, role="eval_build")
        _run(gateway, "run_train_eval", eval_id="e1", run_config_id="rc1")
        wait_body = next(b for m, p, q, b in kiln.calls if p == "/api/jobs/wait")
        options = kiln.options["/api/jobs/wait"]
        assert wait_body == {"ids": ["j1"], "timeout": 600.0}
        assert options["timeout_secs"] == wait_body["timeout"] + JOB_WAIT_MARGIN_SECS
        assert options["timeout_secs"] > 60.0
        assert options["retries"] is False

    def test_a_code_tool_dry_run_keeps_its_documented_fields(self, tmp_path):
        entry = {
            "tool_name": "call_subject_api",
            "arguments": {"id": "x"},
            "output_preview": "404",
            "is_error": True,
            "duration_ms": 3,
            "a_field_kiln_adds_tomorrow": "SECRET",
        }
        routes = {
            ("POST", f"/api/projects/{PROJECT}/test_code_tool"): {
                "result": "done",
                "error": None,
                "traceback": None,
                "not_trusted": False,
                "stdout": "",
                "stderr": "",
                "tool_call_log": [{"value": [entry]}],
                "duration_ms": 12,
                "a_field_kiln_adds_tomorrow": "SECRET",
            }
        }
        gateway, _kiln = _gateway(tmp_path, routes)
        out = _run(
            gateway,
            "test_code_tool",
            tool_function_name="f",
            parameters_schema={"type": "object"},
            code="def run():\n    return 'done'\n",
        )
        assert out["result"] == "done" and out["not_trusted"] is False
        assert out["tool_call_log"][0]["output_preview"] == "404"
        _clean(out)

    def test_the_evals_the_loop_measures_with_are_frozen(self, tmp_path):
        routes = {
            ("POST", f"{BASE}/create_evaluator"): {"id": "e9", "name": "new"},
            ("PATCH", f"{BASE}/evals/e9"): {"id": "e9"},
            ("POST", f"{BASE}/evals/e9/create_eval_config"): {"id": "ec9"},
            ("POST", f"{BASE}/evals/e9/set_current_eval_config/ec9"): {},
        }
        gateway, kiln = _gateway(tmp_path, routes, role="eval_build")
        config = {"name": "judge", "type": "llm_as_judge", "properties": {}}
        for op, args in (
            ("create_eval_config", config),
            ("set_current_eval_config", {"eval_config_id": "ec1"}),
            ("add_eval_items", {"split": "train", "items": [{"input": "x"}]}),
        ):
            with pytest.raises(gw.GatewayError, match="not created through"):
                _run(gateway, op, eval_id="e1", **args)
        assert kiln.calls == []
        _run(
            gateway,
            "create_eval",
            name="new",
            output_scores=[{"name": "ok", "type": "pass_fail"}],
            evaluation_data_type="final_answer",
        )
        assert (
            _run(gateway, "create_eval_config", eval_id="e9", **config)["id"] == "ec9"
        )
        _run(gateway, "set_current_eval_config", eval_id="e9", eval_config_id="ec9")

    def test_a_new_evals_val_and_test_items_are_never_readable(self, tmp_path):
        counter = iter(range(100))
        routes = {
            ("POST", f"{BASE}/create_evaluator"): {"id": "e9", "name": "new"},
            ("PATCH", f"{BASE}/evals/e9"): {"id": "e9"},
            ("POST", f"{BASE}/runs"): lambda q, b: {
                "id": f"424242700{next(counter):03d}"
            },
            ("GET", f"{BASE}/evals/e9"): {"id": "e9", "current_config_id": "ec9"},
        }
        gateway, kiln = _gateway(tmp_path, routes, role="eval_build")
        _run(
            gateway,
            "create_eval",
            name="new",
            output_scores=[{"name": "ok", "type": "pass_fail"}],
            evaluation_data_type="final_answer",
        )
        create = next(b for m, p, q, b in kiln.calls if p.endswith("/create_evaluator"))
        token = (
            create["eval_set_filter_id"].removeprefix("tag::kl_").removesuffix("_test")
        )
        patch = next(b for m, p, q, b in kiln.calls if m == "PATCH")
        assert patch == {"train_set_filter_id": f"tag::kl_{token}_train"}
        train = _run(
            gateway,
            "add_eval_items",
            eval_id="e9",
            split="train",
            items=[{"input": "t", "output": "o"}],
        )
        held = _run(
            gateway,
            "add_eval_items",
            eval_id="e9",
            split="val",
            items=[{"input": "SECRET", "output": "SECRET"}],
        )
        results = f"{BASE}/evals/e9/eval_config/ec9/run_config/rc1/results"
        routes[("GET", results)] = {
            "results": [
                _eval_run(train[0]["id"], "er9", "ok")
                | {"eval_input_id": None, "dataset_id": train[0]["id"]},
                _eval_run(held[0]["id"], "er10", "SECRET")
                | {"eval_input_id": None, "dataset_id": held[0]["id"]},
            ]
        }
        out = _run(gateway, "get_train_results", eval_id="e9", run_config_id="rc1")
        assert [r["item_id"] for r in out["rows"]] == [train[0]["id"]]
        assert "SECRET" not in json.dumps(out)
        with pytest.raises(gw.GatewayError, match="not created through this gateway"):
            _run(
                gateway,
                "add_eval_items",
                eval_id="e1",
                split="train",
                items=[{"input": "x"}],
            )


class TestDocumentedShapes:
    """Each answer keeps the documented fields its role needs (the vendored
    API docs), and each request sends only documented fields."""

    def test_a_run_config_carries_its_prompt_and_input_transform(self, tmp_path):
        routes = {
            ("GET", f"{BASE}/run_configs"): [
                {
                    "id": "rc1",
                    "prompt": {"name": "p", "prompt": "Be brief.", "id": "SECRET"},
                    "run_config_properties": {
                        "model_name": "m",
                        "input_transform": {"type": "t"},
                    },
                }
            ]
        }
        gateway, _ = _gateway(tmp_path, routes)
        (rc,) = _run(gateway, "list_run_configs")
        assert rc["prompt"]["prompt"] == "Be brief."
        assert rc["run_config_properties"]["input_transform"] == {"type": "t"}
        _clean(rc)

    def test_a_rename_sends_the_name_only(self, tmp_path):
        routes = {("PATCH", f"{BASE}/run_configs/rc1"): {"id": "rc1", "name": "n"}}
        gateway, kiln = _gateway(tmp_path, routes)
        with pytest.raises(ValueError):
            _run(gateway, "rename_run_config", id="rc1", description="d")
        _run(gateway, "rename_run_config", id="rc1", name="n")
        assert kiln.calls[-1][3] == {"name": "n"}

    def test_a_spec_needs_its_properties_and_a_short_name(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, {}, role="eval_build")
        with pytest.raises(ValueError):
            _run(gateway, "create_spec", name="s", definition="d")
        assert kiln.calls == []
        # A name over Kiln's 32-character limit for specs is cut to a valid
        # name rather than refused (kiln_name), so no create is lost to it.
        from kiln_harness_tuner.gateway import models as gm

        spec = gm.SpecCreate.model_validate(
            {"name": "x" * 33, "definition": "d", "properties": {}}
        )
        assert spec.name == "x" * 32

    def test_skips_and_failures_say_why(self, tmp_path):
        routes = {
            ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
            ("GET", RESULTS): {
                "results": [
                    _eval_run("tr1", "er1", None)
                    | {"skipped_reason": "missing_key", "scores": {}}
                ]
            },
            ("POST", f"{BASE}/evals/e1/test_v2_eval"): {
                "scores": {},
                "skipped_reason": "no_output",
                "score_range_errors": ["ok out of range"],
            },
            ("POST", "/api/jobs/evals/run"): {"job_id": "j1"},
            ("POST", "/api/jobs/wait"): [
                {"status": "failed", "progress": {}, "error": {"error": "no key"}}
            ],
        }
        gateway, _ = _gateway(tmp_path, routes, role="eval_build")
        res = _run(
            gateway, "get_train_results", eval_id="e1", run_config_id="424242100003"
        )
        assert res["rows"][0]["skipped_reason"] == "missing_key"
        judge = _run(
            gateway, "test_judge", eval_id="e1", properties={}, final_message="x"
        )
        assert judge["skipped_reason"] == "no_output"
        assert judge["score_range_errors"] == ["ok out of range"]
        job = _run(gateway, "run_train_eval", eval_id="e1", run_config_id="rc1")
        assert job["error_message"] == "no key"

    def test_models_and_tools_keep_the_names_a_run_config_needs(self, tmp_path):
        routes = {
            ("GET", "/api/available_models"): [
                {
                    "provider_id": "openai",
                    "models": [{"id": "gpt", "structured_output_mode": "json_schema"}],
                }
            ],
            ("GET", f"/api/projects/{PROJECT}/available_tools"): [
                {
                    "type": "code",
                    "set_name": "Code tools",
                    "tools": [{"id": "t1", "name": "T", "function_name": "kestrel"}],
                }
            ],
            ("GET", f"/api/projects/{PROJECT}/openenv_servers"): [],
        }
        gateway, _ = _gateway(tmp_path, routes)
        model = _run(gateway, "get_model_details", model="gpt")
        assert model["providers"][0]["default_structured_output_mode"] == "json_schema"
        (tool_set,) = _run(gateway, "list_available_tools")
        assert tool_set["type"] == "code"
        assert tool_set["tools"][0]["function_name"] == "kestrel"

    def test_openenv_tools_are_listed_from_their_servers(self, tmp_path):
        """Kiln's `available_tools` leaves out the tools an OpenEnv server
        serves: it knows them only during an episode. The gateway asks each
        server, and a server that does not answer gets a note."""
        servers = f"/api/projects/{PROJECT}/openenv_servers"

        def down(_query, _body):
            raise KilnAPIError("GET", f"{servers}/s2/tools", 502, "connection refused")

        def hangs(_query, _body):  # the client's timeout: not an HTTP answer
            raise KilnError("GET s3/tools timed out after 45s")

        routes = {
            ("GET", f"/api/projects/{PROJECT}/available_tools"): [],
            ("GET", servers): [
                {"id": "s1", "name": "Depot", "url": "http://localhost:8000"},
                {"id": "s2", "name": "Down", "url": "http://localhost:8001"},
                {"id": "s3", "name": "Hangs", "url": "http://localhost:8002"},
            ],
            ("GET", f"{servers}/s1/tools"): [
                {
                    "tool_id": "kiln_tool::openenv::s1::append_note",
                    "name": "append_note",
                    "description": "Add a note to a ticket.",
                    "input_schema": {"type": "object"},
                }
            ],
            ("GET", f"{servers}/s2/tools"): down,
            ("GET", f"{servers}/s3/tools"): hangs,
        }
        gateway, kiln = _gateway(tmp_path, routes)
        depot, down_set, hung_set = _run(gateway, "list_available_tools")
        # A server that is down costs one short wait, not the retry ladder.
        for server in ("s1", "s2", "s3"):
            assert kiln.options[f"{servers}/{server}/tools"] == {
                "timeout_secs": gw.OPENENV_LIST_TIMEOUT_SECS,
                "retries": False,
            }
        assert depot["type"] == "openenv" and depot["set_name"] == "Depot"
        (tool,) = depot["tools"]
        assert tool["id"] == "kiln_tool::openenv::s1::append_note"
        assert tool["function_name"] == "append_note"
        assert tool["description"] == "Add a note to a ticket."
        assert down_set["tools"] == [] and "connection refused" in down_set["note"]
        assert hung_set["tools"] == [] and "timed out" in hung_set["note"]

    def test_a_kiln_without_openenv_servers_lists_its_tools_as_before(self, tmp_path):
        def missing(_query, _body):
            raise KilnAPIError("GET", "openenv_servers", 404, "Not Found")

        routes = {
            ("GET", f"/api/projects/{PROJECT}/available_tools"): [
                {"type": "code", "set_name": "Code tools", "tools": []}
            ],
            ("GET", f"/api/projects/{PROJECT}/openenv_servers"): missing,
        }
        gateway, _ = _gateway(tmp_path, routes)
        assert [s["type"] for s in _run(gateway, "list_available_tools")] == ["code"]

    def test_an_openenv_tool_definition_comes_from_its_server(self, tmp_path):
        """Kiln's definition endpoint answers 404 for an OpenEnv tool outside
        an episode, so the gateway reads it from the server's tool list."""
        tools = f"/api/projects/{PROJECT}/openenv_servers/s1/tools"
        routes = {
            ("GET", tools): [
                {
                    "tool_id": "kiln_tool::openenv::s1::append_note",
                    "name": "append_note",
                    "description": "Add a note to a ticket.",
                    "input_schema": {"properties": {"text": {"type": "string"}}},
                }
            ],
            ("GET", f"{BASE}/tools/kiln_tool::code::c1/definition"): {
                "function_name": "kestrel"
            },
        }
        gateway, kiln = _gateway(tmp_path, routes)
        out = _run(
            gateway, "get_tool_definition", id="kiln_tool::openenv::s1::append_note"
        )
        assert out["definition"] == {
            "tool_id": "kiln_tool::openenv::s1::append_note",
            "function_name": "append_note",
            "description": "Add a note to a ticket.",
            "parameters": {"properties": {"text": {"type": "string"}}},
        }
        assert all("/definition" not in path for _m, path, *_ in kiln.calls)
        with pytest.raises(gw.GatewayError, match="it serves: append_note"):
            _run(gateway, "get_tool_definition", id="kiln_tool::openenv::s1::nope")
        # One list per server per process: a list is a fresh read of the server.
        assert sum(path == tools for _m, path, *_ in kiln.calls) == 1
        # Any other tool: Kiln's own definition endpoint, as before.
        code = _run(gateway, "get_tool_definition", id="kiln_tool::code::c1")
        assert code["definition"]["function_name"] == "kestrel"

    def test_rag_configs_documents_and_search(self, tmp_path):
        """A RAG search tool's config and what it retrieves, and the library
        it indexes: the document's facts, not its file path."""
        project = f"/api/projects/{PROJECT}"
        rag = {
            "id": "r1",
            "name": "Handbook",
            "tool_name": "search_handbook",
            "tool_description": "Search the handbook.",
            "is_archived": False,
            "tags": ["handbook"],
            "chunker_config": {
                "name": "fixed",
                "chunker_type": "fixed_window",
                "properties": {
                    "chunk_size": 512,
                    "chunk_overlap": 64,
                },
                "path": "/secret/path/chunker.kiln",
            },
            "embedding_config": {
                "model_provider_name": "openai",
                "model_name": "text-embedding-3-small",
                "properties": {},
            },
            "vector_store_config": {
                "store_type": "lancedb_hybrid",
                "properties": {"text_key": "text", "doc_id_key": "doc_id"},
            },
            "reranker_config": None,
        }
        searches = []

        def search(_query, body):
            searches.append(body)
            return {
                "results": [
                    {
                        "document_id": "d1",
                        "chunk_idx": 3,
                        "chunk_text": "Returns take 30 days.",
                        "similarity": 0.82,
                    }
                ]
            }

        routes = {
            ("GET", f"{project}/rag_configs"): [rag],
            ("GET", f"{project}/rag_configs/r1"): rag,
            ("POST", f"{project}/rag_configs/r1/search"): search,
            ("GET", f"{project}/documents"): [
                {
                    "id": "d1",
                    "name": "handbook.pdf",
                    "friendly_name": "Handbook",
                    "description": "Policies",
                    "kind": "document",
                    "tags": ["handbook"],
                    "path": "/Users/x/Kiln Projects/p/documents/d1/document.kiln",
                    "original_file": {
                        "filename": "handbook.pdf",
                        "size": 2048,
                        "mime_type": "application/pdf",
                        "attachment": {"path": "/Users/x/secret.pdf"},
                    },
                }
            ],
        }
        gateway, _ = _gateway(tmp_path, routes, role="analyze_experiment_results")
        (listed,) = _run(gateway, "list_rag_configs")
        assert listed["tool_name"] == "search_handbook"
        assert listed["chunker"]["properties"]["chunk_size"] == 512
        assert listed["embedding"]["model_name"] == "text-embedding-3-small"
        assert listed["reranker"] is None
        # Field names that look like keys are settings, not secrets.
        assert listed["vector_store"]["properties"] == {
            "text_key": "text",
            "doc_id_key": "doc_id",
        }
        assert "/secret/path" not in json.dumps(listed)
        assert "SECRET" not in json.dumps(listed)
        one = _run(gateway, "get_rag_config", id="kiln_tool::rag::r1")
        assert one["id"] == "r1"
        (hit,) = _run(gateway, "rag_search", id="kiln_tool::rag::r1", query="returns")
        assert hit["chunk_text"] == "Returns take 30 days." and searches == [
            {"query": "returns"}
        ]
        (doc,) = _run(gateway, "list_documents")
        assert doc == {
            "id": "d1",
            "name": "Handbook",
            "description": "Policies",
            "kind": "document",
            "tags": ["handbook"],
            "filename": "handbook.pdf",
            "mime_type": "application/pdf",
            "size": 2048,
        }

    def test_a_kiln_task_tool_shows_its_task_and_run_config_only(self, tmp_path):
        """The task behind a `kiln_task::` tool and the run config it is
        called with. Its runs and evals are never read: they can hold this
        task's held-out items."""
        project = f"/api/projects/{PROJECT}"
        sub = f"{project}/tasks/t2"
        routes = {
            ("GET", f"{project}/tool_servers/s9/config"): {
                "id": "s9",
                "type": "kiln_task",
                "name": "triage",
                "properties": {
                    "task_id": "t2",
                    "run_config_id": "rc7",
                    "name": "triage",
                    "description": "Triage a ticket.",
                },
            },
            ("GET", sub): {
                "id": "t2",
                "name": "Triage",
                "instruction": "Sort the ticket.",
                "requirements": [],
            },
            ("GET", f"{sub}/run_configs"): [
                {
                    "id": "rc7",
                    "name": "fast",
                    "run_config_properties": {
                        "model_name": "m",
                        "model_provider_name": "p",
                        "prompt_id": "id::p1",
                    },
                }
            ],
            ("GET", f"{sub}/gen_prompt/id::p1"): {
                "prompt_id": "id::p1",
                "prompt": "You triage tickets.",
            },
            ("GET", f"{project}/tool_servers/m1/config"): {
                "id": "m1",
                "type": "remote_mcp",
                "properties": {"server_url": "https://x", "headers": {"k": "SECRET"}},
            },
        }
        gateway, kiln = _gateway(tmp_path, routes, role="batch_plan")
        out = _run(gateway, "get_kiln_task_tool", id="kiln_task::s9")
        assert out["tool_id"] == "kiln_task::s9"
        assert out["tool_description"] == "Triage a ticket."
        assert out["task"]["instruction"] == "Sort the ticket."
        assert out["run_config"]["id"] == "rc7"
        assert out["prompt"]["prompt"] == "You triage tickets."
        paths = [path for _m, path, *_ in kiln.calls]
        assert not any("/runs" in p or "/evals" in p for p in paths)
        # An MCP server is not a Kiln task tool, and its secrets stay out.
        with pytest.raises(gw.GatewayError, match="not a Kiln task tool") as err:
            _run(gateway, "get_kiln_task_tool", id="m1")
        assert "SECRET" not in str(err.value)
        # Only the saved record is read: the plain route would connect to an
        # MCP server, or start a local one, to list its tools.
        servers = [
            p for p in (path for _m, path, *_ in kiln.calls) if "tool_servers" in p
        ]
        assert servers and all(p.endswith("/config") for p in servers)

    @pytest.mark.parametrize(
        "prompt_id, frozen",
        [
            ("few_shot_prompt_builder", None),
            ("repairs_prompt_builder", None),
            ("id::p1", {"base_prompt": "Frozen."}),
        ],
    )
    def test_a_kiln_task_tool_renders_no_prompt_built_from_runs(
        self, tmp_path, prompt_id, frozen
    ):
        """A generator such as few-shot builds its text from the called
        task's stored runs, which can come from held-out drives: it is named,
        not rendered. A frozen prompt is already on the run config: nothing
        is rendered."""
        project = f"/api/projects/{PROJECT}"
        routes = {
            ("GET", f"{project}/tool_servers/s9/config"): {
                "type": "kiln_task",
                "properties": {"task_id": "t2", "run_config_id": "rc7"},
            },
            ("GET", f"{project}/tasks/t2"): {"id": "t2", "instruction": "x"},
            ("GET", f"{project}/tasks/t2/run_configs"): [
                {
                    "id": "rc7",
                    "run_config_properties": {"prompt_id": prompt_id},
                    "prompt": frozen,
                }
            ],
        }
        gateway, kiln = _gateway(tmp_path, routes, role="batch_plan")
        out = _run(gateway, "get_kiln_task_tool", id="s9")
        assert out["prompt"] is None
        assert not any("gen_prompt" in path for _m, path, *_ in kiln.calls)
        if frozen is None:
            assert prompt_id in out["note"]
        else:
            assert out["run_config"]["prompt"] is not None and out["note"] is None

    @pytest.mark.parametrize(
        "prompt_id",
        [
            "simple_prompt_builder",
            "simple_chain_of_thought_prompt_builder",
            "fine_tune_prompt::p::t2::ft1",
            "task_run_config::p::t2::rc1",
        ],
    )
    def test_a_kiln_task_tool_renders_a_prompt_that_reads_no_runs(
        self, tmp_path, prompt_id
    ):
        """These prompts use the task's own fields, a fine-tune's prompt or a
        run config's frozen prompt, never the task's stored runs."""
        project = f"/api/projects/{PROJECT}"
        routes = {
            ("GET", f"{project}/tool_servers/s9/config"): {
                "type": "kiln_task",
                "properties": {"task_id": "t2", "run_config_id": "rc7"},
            },
            ("GET", f"{project}/tasks/t2"): {"id": "t2", "instruction": "x"},
            ("GET", f"{project}/tasks/t2/run_configs"): [
                {"id": "rc7", "run_config_properties": {"prompt_id": prompt_id}}
            ],
            ("GET", f"{project}/tasks/t2/gen_prompt/{prompt_id}"): {
                "prompt_id": prompt_id,
                "prompt": "Sort the ticket.",
            },
        }
        gateway, _ = _gateway(tmp_path, routes, role="batch_plan")
        out = _run(gateway, "get_kiln_task_tool", id="s9")
        assert out["prompt"]["prompt"] == "Sort the ticket." and out["note"] is None

    def test_a_kiln_task_tool_with_a_missing_run_config_says_so(self, tmp_path):
        project = f"/api/projects/{PROJECT}"
        routes = {
            ("GET", f"{project}/tool_servers/s9/config"): {
                "type": "kiln_task",
                "properties": {"task_id": "t2", "run_config_id": "gone"},
            },
            ("GET", f"{project}/tasks/t2"): {"id": "t2", "instruction": "x"},
            ("GET", f"{project}/tasks/t2/run_configs"): [],
        }
        gateway, _ = _gateway(tmp_path, routes, role="probe")
        out = _run(gateway, "get_kiln_task_tool", id="s9")
        assert out["run_config"] is None and "gone" in out["note"]

    def test_statistics_accept_the_documented_nulls(self, tmp_path):
        routes = {("POST", "/api/statistics"): {"n": 1}}
        gateway, kiln = _gateway(tmp_path, routes, role="promote_best")
        _run(
            gateway,
            "compute_statistics",
            operation="paired",
            values_a=[1.0, None],
            values_b=[0.5, 0.2],
        )
        assert kiln.calls[-1][3]["values_a"] == [1.0, None]


class TestRoles:
    def test_each_role_is_served_its_operations_only(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {})
        for role, ops in gw.ROLE_OPS.items():
            names = {
                t.name for t in asyncio.run(create_server(gateway, role).list_tools())
            }
            assert names == set(ops), role
        assert gw.ROLE_OPS["preflight"] == ("ping",)
        writes = {"create_run_config", "run_task", "memory_create", "create_eval"}
        for reader in ("promote_best", "report_writer", "describe_eval"):
            assert not writes & set(gw.ROLE_OPS[reader]), reader
        for role, ops in gw.ROLE_OPS.items():
            if role not in gw.VAL_READERS:
                assert "get_val_summary" not in ops, role
        # Every role that plans, builds, probes or analyses an arm can read
        # every tool kind a run config attaches.
        for role in ("batch_plan", "create_experiment_rc", "probe"):
            assert set(gw._ARTIFACTS) <= set(gw.ROLE_OPS[role]), role
        assert set(gw._ARTIFACTS) <= set(gw.ROLE_OPS["analyze_experiment_results"])
        for reader in ("promote_best", "report_writer"):
            assert not set(gw._TOOL_READS) & set(gw.ROLE_OPS[reader]), reader

    def test_the_model_roles_are_served_the_model_operations(self):
        models = {"list_models", "get_model_details", "list_providers"}
        for role, ops in gw.ROLE_OPS.items():
            assert "list_available_models" not in ops, role
            has = models & set(ops)
            if role in ("batch_plan", "create_experiment_rc", "probe"):
                assert has == models, role
            else:
                assert not has, role

    def test_the_baseline_reviewer_reads_and_never_writes(self):
        ops = set(gw.ROLE_OPS["baseline_review"])
        assert {
            "get_run_config",
            "get_prompt_text",
            "get_train_results",
            "get_skill_content",
            "get_tool_definition",
            "memory_list",
        } <= ops
        writes = {
            "memory_create",
            "memory_update",
            "run_task",
            "create_run_config",
            "rename_run_config",
            "create_prompt",
            "create_code_tool",
            "create_eval",
        }
        assert not writes & ops
        assert not any(op.startswith(("create_", "archive_")) for op in ops)
        # The reviewer does not judge whether an answer is right.
        assert "get_train_expected" not in ops
        assert "get_task_run" not in ops
        assert "baseline_review" not in gw.VAL_READERS
        assert "baseline_review" in gw.TRACE_READER_ROLES

    def test_an_operation_outside_the_role_is_refused(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, {}, role="batch_plan")
        with pytest.raises(
            gw.GatewayError, match="not available to the batch_plan role"
        ):
            asyncio.run(gw.dispatch(gateway, "batch_plan", "run_task", {}))
        assert kiln.calls == []

    def test_there_is_no_operation_for_held_out_or_operator_actions(self):
        forbidden = {
            "settings",
            "setting",
            "provider",
            "providers",
            "delete",
            "split",
            "splits",
            "heldout",
            "test_items",
            "val_items",
        }
        # `list_providers` reads the catalogue's notes on each provider. No
        # operation changes a provider.
        for op in set(gw.OPS) - {"list_providers"}:
            assert not forbidden & set(op.split("_")), op


class TestTheHook:
    @pytest.mark.parametrize(
        "tool",
        [
            "mcp__kiln_api_mcp__call_kiln_api",
            "mcp__kiln_harness_tuner_gateway__run_task",  # not a planner operation
            "mcp__skill_installer__install_skill_bundle",
            "mcp__some_other_server__x",
        ],
    )
    def test_the_planner_may_call_none_of_these(self, tool):
        assert hook.decide(tool, "batch_plan") is not None

    def test_its_own_operations_and_file_tools_pass(self):
        assert (
            hook.decide("mcp__kiln_harness_tuner_gateway__get_task", "batch_plan")
            is None
        )
        assert hook.decide("Read", "batch_plan") is None
        builder = "create_experiment_rc"
        assert (
            hook.decide("mcp__skill_installer__install_skill_bundle", builder) is None
        )

    def test_a_session_without_a_role_calls_no_mcp_tool(self):
        assert hook.decide("mcp__kiln_harness_tuner_gateway__ping", "none") is not None

    def test_the_process_blocks_with_exit_2(self, monkeypatch, capsys):
        event = {"tool_name": "mcp__kiln_api_mcp__call_kiln_api", "tool_input": {}}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
        assert hook.main(["--role", "probe"]) == 2
        assert "gateway operations" in capsys.readouterr().err
        monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
        assert hook.main(["--role", "probe"]) == 2


def test_the_val_readers_get_the_code_assembled_tables(tmp_path):
    from kiln_harness_tuner.schemas import CandidateValTable

    gateway, _ = _gateway(tmp_path, {}, role="promote_best")
    assert _run(gateway, "get_val_summary")["candidates"] == []
    # The run folder is prepared with an empty file: still no gate, no error.
    (tmp_path / "run" / gw.VAL_SUMMARY_FILE).write_text("")
    empty = _run(gateway, "get_val_summary")
    assert empty["candidates"] == []
    assert empty["note"] == "no promotion gate has run yet"
    table = CandidateValTable(rc_id="rc1", experiment_memory_id="m1", per_eval=[])
    (tmp_path / "run" / gw.VAL_SUMMARY_FILE).write_text(
        json.dumps({"candidates": [table.model_dump(mode="json")], "note": "n"})
    )
    out = _run(gateway, "get_val_summary")
    assert out["candidates"][0]["rc_id"] == "rc1"


# ------------------------------------------------------------ trace files

SYSTEM_PROMPT = "You are the depot agent. " + "Rule. " * 2_000
TOOL_DEFS = [
    {"type": "function", "function": {"name": f"tool_{n}", "parameters": {}}}
    for n in range(90)
]
BIG_RESULT = '{"rows": [' + ", ".join(f'"row {n}"' for n in range(8_000)) + "]}"


def _agent_trace(item: str, *, usage: bool = True, error: bool = True) -> dict:
    """A multi-turn agent trace stored as an object: the system prompt, the
    tool definitions, two tool calls (one an error) and a final reply."""

    def turn(n: int) -> dict:
        return (
            {"usage": {"input_tokens": 900 + n, "output_tokens": 40,
                       "cached_tokens": 800, "cost": 0.001},
             "latency_ms": 700}
            if usage
            else {}
        )  # fmt: skip

    return {
        "tools": TOOL_DEFS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"question of {item}"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "kestrel_api", "arguments": '{"q": 1}'},
                    }
                ],
                **turn(1),
            },
            {"role": "tool", "tool_call_id": "c1", "content": BIG_RESULT},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "kestrel_api", "arguments": '{"q": 2}'},
                    }
                ],
                **turn(2),
            },
            {
                "role": "tool",
                "tool_call_id": "c2",
                "content": "404 not found",
                "is_error": error,
                "a_field_kiln_adds_tomorrow": "SECRET",
            },
            {"role": "assistant", "content": f"final answer for {item}", **turn(3)},
        ],
    }


def _result_row(item: str, run_id: str, trace: Any, output: str = "fine") -> dict:
    return {
        "eval_run": _eval_run(item, run_id, output)
        | {
            "task_run_usage": {
                "cost": 0.02,
                "total_llm_latency_ms": 2100.0,
                "input_tokens": 2700,
                "output_tokens": 120,
                "cached_tokens": 2400,
            },
            "intermediate_outputs": {"reasoning": f"judge reason for {item}"},
        },
        "input": f"train question of {item}",
        "output": output,
        "task_run_trace": json.dumps(trace),
    }


def _held_out_row(item: str, run_id: str) -> dict:
    trace = {"messages": [{"role": "user", "content": "SECRET val question"}]}
    return {
        "eval_run": _eval_run(item, run_id, "SECRET"),
        "output": "SECRET",
        "task_run_trace": json.dumps(trace),
    }


def _all_files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class TestTraceFiles:
    """A train trace goes to a file the agent may read; the
    answer carries its path and a one-line summary, never the trace."""

    RC = "424242100003"

    def _routes(self, rows: list[dict]) -> dict:
        return {
            ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
            ("GET", RESULTS): {"results": rows},
        }

    def _rows(self) -> list[dict]:
        return [
            _result_row("tr1", "er1", _agent_trace("tr1")),
            _result_row("tr2", "er2", _agent_trace("tr2", usage=False, error=False)),
            _held_out_row("va1", "er3"),
            _held_out_row("both", "er4"),
            _held_out_row("cross", "er5"),
        ]

    def test_only_train_rows_are_written(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._routes(self._rows()))
        out = _run(gateway, "get_train_results", eval_id="e1", run_config_id=self.RC)
        assert [r["item_id"] for r in out["rows"]] == ["tr1", "tr2"]
        assert out["dropped"] == 3
        files = _all_files(gateway.s.trace_root)
        assert files, "no trace file was written"
        for name, text in files.items():
            for secret in ("SECRET", "va1", "both", "cross"):
                assert secret not in name and secret not in text, (name, secret)
        # A held-out id asked for by name is dropped as before, and writes
        # nothing.
        out = _run(
            gateway,
            "get_train_results",
            eval_id="e1",
            run_config_id=self.RC,
            item_ids=["va1", "cross"],
        )
        assert out["rows"] == []
        assert _all_files(gateway.s.trace_root) == files

    def test_the_answer_has_paths_and_no_trace(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._routes(self._rows()))
        out = _run(gateway, "get_train_results", eval_id="e1", run_config_id=self.RC)
        text = json.dumps(out)
        assert "task_run_trace" not in text
        assert SYSTEM_PROMPT[:200] not in text and "row 7999" not in text
        assert len(text) < 5_000
        (first, second) = out["rows"]
        assert first["trace_summary"] == (
            "3 assistant turns, 1 user turns, 2 tool calls (1 tool errors),"
            f" final reply {len('final answer for tr1')} chars"
        )
        assert "(0 tool errors)" in second["trace_summary"]
        assert first["scores"] == {"accuracy": 1.0}
        assert first["task_cost_usd"] == 0.02 and first["latency_ms"] == 2100.0
        root = gateway.s.trace_root
        assert root == tmp_path / "run" / "scratch" / "traces"
        assert Path(first["trace_path"]) == root / self.RC / "e1" / "tr1.md"
        assert out["traces_dir"] == str(root)
        assert out["stats_path"] == str(root / self.RC / "_stats.md")

    def test_a_trace_file_moves_the_system_prompt_and_tools_out(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._routes(self._rows()))
        out = _run(gateway, "get_train_results", eval_id="e1", run_config_id=self.RC)
        trace = Path(out["rows"][0]["trace_path"]).read_text()
        # The header.
        for line in (
            "item_id: tr1",
            "eval_id: e1",
            f"run_config_id: {self.RC}",
            "eval_run_id: er1",
            "scores: accuracy=1",
            "task_cost_usd: 0.02",
            "latency_ms: 2100",
            "input_tokens: 2700",
            "output_tokens: 120",
            "cached_tokens: 2400",
            "assistant_turns: 3",
            "tool_calls: 2",
            "tool_errors: 1",
        ):
            assert line in trace.splitlines(), line
        assert "judge reason for tr1" in trace
        # The conversation in order, one block per message, the tool
        # result in full, the error marked.
        heads = [line for line in trace.splitlines() if line.startswith("## [")]
        assert [h.split("]")[0] for h in heads] == [f"## [{n}" for n in range(7)]
        assert heads[0].startswith("## [0] system (moved out:")
        assert "kestrel_api" in heads[3] and "TOOL_ERROR" not in heads[3]
        assert heads[5].endswith("TOOL_ERROR")
        assert "### tool call kestrel_api (id c1)" in trace
        assert BIG_RESULT in trace
        assert "SECRET" not in trace  # a field no renderer names
        # The system prompt and the tool definitions are not in the trace;
        # they are in one shared file, which the header points to.
        assert "Rule. Rule." not in trace and "tool_89" not in trace
        (system,) = (gateway.s.trace_root / self.RC / "_system").glob("*.md")
        assert f"system: {system} (" in trace
        shared = system.read_text()
        assert SYSTEM_PROMPT in shared and '"tool_89"' in shared
        # Both items share the one system file.
        second = Path(out["rows"][1]["trace_path"]).read_text()
        assert f"system: {system} (" in second

    def test_the_stats_file(self, tmp_path):
        gateway, _ = _gateway(tmp_path, self._routes(self._rows()))
        _run(gateway, "get_train_results", eval_id="e1", run_config_id=self.RC)
        folder = gateway.s.trace_root / self.RC
        data = json.loads((folder / "_stats.json").read_text())
        by_item = {i["item_id"]: i for i in data["items"]}
        assert set(by_item) == {"tr1", "tr2"}
        tr1, tr2 = by_item["tr1"], by_item["tr2"]
        assert tr1["tools"]["kestrel_api"] == {
            "calls": 2,
            "errors": 1,
            "result_chars": len(BIG_RESULT) + len("404 not found"),
            "max_result_chars": len(BIG_RESULT),
        }
        assert tr1["usage_source"] == "per_turn"
        assert [t["input_tokens"] for t in tr1["turn_usage"]] == [901, 902, 903]
        assert tr1["turn_usage"][0]["cached_tokens"] == 800
        assert tr1["run_usage"]["cost"] == 0.02
        # No per-turn usage stored: the run's usage, and it says so.
        assert tr2["usage_source"] == "per_run_only" and tr2["turn_usage"] == []
        assert data["tools"]["kestrel_api"]["calls"] == 4
        md = (folder / "_stats.md").read_text()
        assert "| e1 | tr1 | 3 | 2 | 1 | 2700 | 120 | 2400 |" in md
        assert "per_run_only" in md and "| kestrel_api | 4 | 1 |" in md

    def test_a_changed_row_overwrites_its_file(self, tmp_path):
        rows = self._rows()
        routes = self._routes(rows)
        gateway, _ = _gateway(tmp_path, routes)
        args = {"eval_id": "e1", "run_config_id": self.RC}
        path = Path(_run(gateway, "get_train_results", **args)["rows"][0]["trace_path"])
        first = path.read_text()
        mtime = path.stat().st_mtime_ns
        _run(gateway, "get_train_results", **args)
        assert path.stat().st_mtime_ns == mtime  # unchanged: not written again
        rows[0] = _result_row("tr1", "er9", _agent_trace("tr1"), output="better")
        routes[("GET", RESULTS)] = {"results": rows}
        _run(gateway, "get_train_results", **args)
        assert path.read_text() != first
        assert "eval_run_id: er9" in path.read_text()
        assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]

    def test_an_id_cannot_climb_out_of_the_folder(self, tmp_path):
        results = f"{BASE}/evals/e1/eval_config/ec1/run_config/../results"
        routes = self._routes(self._rows()) | {
            ("GET", results): {"results": self._rows()}
        }
        gateway, _ = _gateway(tmp_path, routes)
        out = _run(gateway, "get_train_results", eval_id="e1", run_config_id="..")
        root = gateway.s.trace_root.resolve()
        for row in out["rows"]:
            assert root in Path(row["trace_path"]).resolve().parents

    def test_a_task_run_trace_goes_to_a_file(self, tmp_path):
        run = {
            "id": "424242600002",
            "input": "train question one",
            "output": {"output": "ok"},
            "eval_source": {"source_type": "eval_input", "source_id": "tr1"},
            "trace": _agent_trace("tr1")["messages"],
            "usage": {"input_tokens": 2700, "cost": 0.02},
        }
        held = {
            "id": "424242600001",
            "input": "SECRET val question",
            "output": {"output": "SECRET"},
            "eval_source": {"source_type": "eval_input", "source_id": "va1"},
            "trace": [{"role": "user", "content": "SECRET"}],
        }
        routes = {
            ("GET", f"{BASE}/runs/424242600002"): run,
            ("GET", f"{BASE}/runs/424242600001"): held,
        }
        gateway, _ = _gateway(tmp_path, routes, role="probe")
        with pytest.raises(gw.GatewayError, match="not provably"):
            _run(gateway, "get_task_run", id="424242600001")
        out = _run(gateway, "get_task_run", id="424242600002")
        assert "trace" not in out and "row 7999" not in json.dumps(out)
        assert out["trace_summary"].startswith("3 assistant turns")
        path = Path(out["trace_path"])
        assert path == gateway.s.trace_root / "task_runs" / "424242600002.md"
        assert BIG_RESULT in path.read_text()
        assert SYSTEM_PROMPT not in path.read_text()
        for name, text in _all_files(gateway.s.trace_root).items():
            assert "SECRET" not in text and "424242600001" not in name


class TestTheAnswerCap:
    """No answer is over ANSWER_MAX_CHARS. A page ends at the
    cap with `next_offset`; any other answer over it comes back in pieces
    through `get_answer_part`."""

    def test_results_come_in_pages(self, tmp_path, monkeypatch):
        rows = [
            _result_row("tr1", "er1", _agent_trace("tr1"), output="x" * 2_000),
            _result_row("tr2", "er2", _agent_trace("tr2"), output="y" * 2_000),
        ]
        routes = {
            ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
            ("GET", RESULTS): {"results": rows},
        }
        gateway, _ = _gateway(tmp_path, routes)
        args = {"eval_id": "e1", "run_config_id": "424242100003"}
        out = _run(gateway, "get_train_results", **args, limit=1)
        assert [r["item_id"] for r in out["rows"]] == ["tr1"]
        assert (out["total"], out["next_offset"]) == (2, 1)
        out = _run(gateway, "get_train_results", **args, offset=1)
        assert [r["item_id"] for r in out["rows"]] == ["tr2"]
        assert out["next_offset"] is None
        # A page ends at the cap too.
        monkeypatch.setattr(gw, "ANSWER_MAX_CHARS", 1_800)
        out = _run(gateway, "get_train_results", **args)
        assert [r["item_id"] for r in out["rows"]] == ["tr1"]
        assert out["next_offset"] == 1
        assert len(json.dumps(out)) <= 1_800
        assert len(out["rows"][0]["output_preview"]) == gw.OUTPUT_PREVIEW_CHARS

    def test_expected_outputs_come_in_pages_under_the_cap(self, tmp_path):
        gateway, _ = _gateway(tmp_path, {})
        p = Project(tmp_path / "proj")
        # Quote-dense references: their clipped JSON text doubles when it is
        # escaped into the answer.
        p.eval_input("tr1", ["train_a"], reference={"x": ['"q"'] * 5_000})
        p.eval_input("tr2", ["train_a"], reference={"y": ['"q"'] * 5_000})
        seen: list[str] = []
        offset: int | None = 0
        while offset is not None:
            out = _run(gateway, "get_train_expected", offset=offset)
            assert len(json.dumps(out)) <= gw.ANSWER_MAX_CHARS
            assert out["items"], "a page holds at least one item"
            seen += [i["id"] for i in out["items"]]
            offset = out["next_offset"]
        assert seen == ["tr1", "tr2"]
        # By id, the same pages.
        out = _run(gateway, "get_train_expected", item_ids=["tr2", "tr1"], limit=1)
        assert [i["id"] for i in out["items"]] == ["tr2"]
        assert (out["total"], out["next_offset"]) == (2, 1)

    def test_any_answer_over_the_cap_comes_in_pieces(self, tmp_path):
        code = 'print("a \\\\ quote")\n' * 9_000  # escapes grow it in JSON
        routes = {
            ("GET", f"/api/projects/{PROJECT}/code_tools/t1"): {
                "id": "t1",
                "code": code,
            }
        }
        gateway, kiln = _gateway(tmp_path, routes)
        first = _run(gateway, "get_code_tool", id="t1")
        assert first["truncated"] is True and first["offset"] == 0
        calls = len(kiln.calls)
        pieces = [first["part"]]
        piece = first
        while piece["next_offset"] is not None:
            assert len(json.dumps(piece)) <= gw.ANSWER_MAX_CHARS
            piece = _run(
                gateway,
                "get_answer_part",
                continuation=first["continuation"],
                offset=piece["next_offset"],
            )
            pieces.append(piece["part"])
        assert len(json.dumps(piece)) <= gw.ANSWER_MAX_CHARS
        assert json.loads("".join(pieces))["code"] == code
        assert len(kiln.calls) == calls  # a piece never runs the operation again
        with pytest.raises(gw.GatewayError, match="no kept answer"):
            _run(gateway, "get_answer_part", continuation="0" * 12, offset=0)

    def test_every_role_with_operations_can_read_a_piece(self):
        for role, ops in gw.ROLE_OPS.items():
            assert ("get_answer_part" in ops) == (role != "preflight"), role
        assert gw.ANSWER_MAX_CHARS <= 50_000  # well under ~25,000 tokens


def test_a_tool_answer_is_text_only(tmp_path):
    # A `str` tool is structured by default in FastMCP, which also sends
    # `{"result": <the JSON as a string>}`. A preflight session relayed that
    # wrapper in place of the answer.
    gateway, _ = _gateway(tmp_path, {("GET", "/ping"): {}}, role="preflight")
    server = create_server(gateway, "preflight")
    (tool,) = asyncio.run(server.list_tools())
    assert tool.outputSchema is None
    result = asyncio.run(server.call_tool("ping", {}))
    assert [type(part).__name__ for part in result] == ["TextContent"]
    assert json.loads(result[0].text) == {"ok": True}


# ------------------------------------------------------------ sub-agents

SUB_TASK = "424242700001"
SUB_RC = "424242700002"
SUB_SERVER = "424242700003"
SUB_TOOL = f"kiln_task::{SUB_SERVER}"
P = f"/api/projects/{PROJECT}"
SUB_PROPS = {
    "model_name": "gpt-test",
    "model_provider_name": "openai",
    "structured_output_mode": "json_schema",
}


def _sub_agent_routes(sent: dict[str, Any]) -> dict:
    """The three creates of `create_sub_agent`; `sent` gets each body."""

    def create(kind: str, record_id: str):
        def route(_query, body):
            sent[kind] = body
            return {"id": record_id, **body}

        return route

    return {
        ("POST", f"{P}/tasks"): create("task", SUB_TASK),
        ("POST", f"{P}/tasks/{SUB_TASK}/run_configs"): create("run_config", SUB_RC),
        ("POST", f"{P}/kiln_task_tool"): create("tool", SUB_SERVER),
    }


def _sub_agent_args(**overrides: Any) -> dict:
    return {
        "name": "Manifest reader",
        "tool_name": "read_manifest",
        "tool_description": "Read one manifest and return its counts.",
        "instruction": "You read a freight manifest and return its counts.",
        "run_config_properties": dict(SUB_PROPS),
        **overrides,
    }


def _attached_sub_agent(provider: str = "openai", task_id: str = SUB_TASK) -> dict:
    """The reads behind a `kiln_task::` tool a run config attaches."""
    return {
        ("GET", f"{P}/tool_servers/{SUB_SERVER}/config"): {
            "id": SUB_SERVER,
            "type": "kiln_task",
            "properties": {
                "name": "read_manifest",
                "description": "Read one manifest.",
                "task_id": task_id,
                "run_config_id": SUB_RC,
            },
        },
        ("GET", f"{P}/tasks/{task_id}"): {"id": task_id, "instruction": "x"},
        ("GET", f"{P}/tasks/{task_id}/run_configs"): [
            {
                "id": SUB_RC,
                "run_config_properties": {
                    "model_name": "m",
                    "model_provider_name": provider,
                },
            }
        ],
    }


OPENAI_ONLY = {"allow": [{"provider": "openai", "model": "*"}]}


class TestSubAgents:
    def test_a_sub_agent_is_a_task_a_run_config_and_a_tool(self, tmp_path):
        sent: dict[str, Any] = {}
        gateway, kiln = _gateway(tmp_path, _sub_agent_routes(sent))
        out = _run(
            gateway,
            "create_sub_agent",
            **_sub_agent_args(
                description="Reads manifests.",
                output_json_schema={
                    "type": "object",
                    "properties": {"pallets": {"type": "integer"}},
                },
                provenance={"origin": "agent", "notes": "why"},
            ),
        )

        assert out["tool_id"] == SUB_TOOL
        assert (out["task_id"], out["run_config_id"]) == (SUB_TASK, SUB_RC)
        assert (out["model_provider_name"], out["model_name"]) == ("openai", "gpt-test")
        # The task: single-turn, the instruction as its prompt, the schemas
        # as JSON text, and a description that says where it comes from.
        task = sent["task"]
        assert task["instruction"].startswith("You read a freight manifest")
        assert task["turn_mode"] == "single_turn"
        assert json.loads(task["output_json_schema"])["properties"] == {
            "pallets": {"type": "integer"}
        }
        assert "input_json_schema" not in task
        assert task["description"].startswith("Reads manifests.")
        assert f"Sub-agent of task {TASK}" in task["description"]
        # The run config: on the NEW task, with a prompt that sends the
        # instruction, and the provenance.
        rc = sent["run_config"]
        assert rc["run_config_properties"]["prompt_id"] == "simple_prompt_builder"
        assert rc["run_config_properties"]["type"] == "kiln_agent"
        assert rc["provenance"] == {"origin": "agent", "notes": "why"}
        # The tool pins that task and that run config.
        assert sent["tool"] == {
            "name": "read_manifest",
            "description": "Read one manifest and return its counts.",
            "task_id": SUB_TASK,
            "run_config_id": SUB_RC,
            "is_archived": False,
        }
        # Nothing of the subject task was written.
        assert not [p for m, p, *_ in kiln.calls if m == "POST" and BASE in p]

    def test_what_it_created_is_in_the_runs_registry(self, tmp_path):
        from kiln_harness_tuner.sub_agents import CreatedSubAgent, created_sub_agents

        gateway, _ = _gateway(tmp_path, _sub_agent_routes({}))
        _run(gateway, "create_sub_agent", **_sub_agent_args())
        assert created_sub_agents(tmp_path / "run") == [
            CreatedSubAgent(SUB_TASK, SUB_RC, SUB_TOOL)
        ]
        # No registry key can be taken for a train record's id.
        assert gateway.s.created_train() == set()
        assert all(":" in key for key in gateway.s.created)

    def test_a_model_outside_the_policy_is_refused_before_any_write(self, tmp_path):
        policy = ModelPolicy.from_json(OPENAI_ONLY)
        gateway, kiln = _gateway(tmp_path, _sub_agent_routes({}), policy=policy)
        props = {**SUB_PROPS, "model_provider_name": "groq", "model_name": "kimi"}
        with pytest.raises(gw.GatewayError, match="outside the model policy"):
            _run(
                gateway,
                "create_sub_agent",
                **_sub_agent_args(run_config_properties=props),
            )
        assert kiln.calls == []
        _run(gateway, "create_sub_agent", **_sub_agent_args())  # inside: allowed

    @pytest.mark.parametrize(
        "tool",
        [
            "kiln_task::424242700009",
            "kiln_tool::call_kiln_api",
            "kiln_tool::memory::search",
        ],
    )
    def test_a_sub_agent_gets_no_sub_agent_and_no_way_into_kiln(self, tmp_path, tool):
        gateway, kiln = _gateway(tmp_path, _sub_agent_routes({}))
        props = {**SUB_PROPS, "tools_config": {"tools": ["kiln_tool::code::c1", tool]}}
        with pytest.raises(gw.GatewayError, match="cannot attach"):
            _run(
                gateway,
                "create_sub_agent",
                **_sub_agent_args(run_config_properties=props),
            )
        assert kiln.calls == []

    def test_a_sub_agent_can_have_tools_of_its_own(self, tmp_path):
        sent: dict[str, Any] = {}
        gateway, _ = _gateway(tmp_path, _sub_agent_routes(sent))
        # An OpenEnv tool too: the route of a drive reads the sub-agent's
        # tools, so such a run config is driven as an eval job.
        tools = {
            "tools": [
                "kiln_tool::code::c1",
                "mcp::remote::m1::search",
                "kiln_tool::openenv::424242700008::read_scenario",
            ]
        }
        props = {**SUB_PROPS, "tools_config": tools}
        _run(
            gateway, "create_sub_agent", **_sub_agent_args(run_config_properties=props)
        )
        assert sent["run_config"]["run_config_properties"]["tools_config"] == tools

    @pytest.mark.parametrize(
        "bad",
        [
            {"tool_name": "ReadManifest"},
            {"tool_name": "read_manifest_"},
            {"tool_name": "read__manifest"},
            {"tool_name": "9lives"},
            {"tool_description": "x" * 129},
            {"instruction": ""},
            {"input_json_schema": {"type": "string"}},
            {"output_json_schema": {"type": "object"}},
            {"task_id": TASK},
        ],
    )
    def test_what_kiln_would_refuse_never_reaches_kiln(self, tmp_path, bad):
        gateway, kiln = _gateway(tmp_path, _sub_agent_routes({}))
        with pytest.raises(ValueError):
            _run(gateway, "create_sub_agent", **_sub_agent_args(**bad))
        assert kiln.calls == []

    def test_a_create_that_fails_half_way_names_what_it_left(self, tmp_path):
        from kiln_harness_tuner.sub_agents import CreatedSubAgent, created_sub_agents

        routes = _sub_agent_routes({})

        def refuse(_query, _body):
            raise KilnAPIError("POST", "x", 422, {"detail": "bad output mode"})

        routes[("POST", f"{P}/tasks/{SUB_TASK}/run_configs")] = refuse
        gateway, _ = _gateway(tmp_path, routes)
        with pytest.raises(gw.GatewayError) as err:
            _run(gateway, "create_sub_agent", **_sub_agent_args())
        assert f"the sub-agent's task {SUB_TASK} exists" in str(err.value)
        assert "bad output mode" in str(err.value)
        assert created_sub_agents(tmp_path / "run") == [CreatedSubAgent(SUB_TASK)]

    def test_only_the_build_agent_creates_a_sub_agent(self):
        roles = [r for r, ops in gw.ROLE_OPS.items() if "create_sub_agent" in ops]
        assert roles == ["create_experiment_rc"]

    # ---- attaching one: `create_run_config` and `run_task`

    def _attach(self, gateway, tool: str = SUB_TOOL):
        props = {
            "type": "kiln_agent",
            "model_provider_name": "openai",
            "model_name": "gpt-test",
            "tools_config": {"tools": ["kiln_tool::code::c1", tool]},
        }
        return _run(gateway, "create_run_config", name="n", run_config_properties=props)

    def test_a_run_config_attaches_a_sub_agent_inside_the_policy(self, tmp_path):
        routes = {
            **_attached_sub_agent("openai"),
            ("POST", f"{BASE}/run_configs"): lambda _q, body: {"id": "rc9", **body},
        }
        policy = ModelPolicy.from_json(OPENAI_ONLY)
        gateway, kiln = _gateway(tmp_path, routes, policy=policy)
        out = self._attach(gateway)
        assert SUB_TOOL in out["tools"]
        # Only the task's own record and its run configs were read.
        assert not any("/runs" in p or "/evals" in p for _m, p, *_ in kiln.calls)

    def test_a_run_config_is_refused_for_the_model_of_its_sub_agent(self, tmp_path):
        """The model policy is about where the task's data is sent. A
        sub-agent's input is the task's data."""
        policy = ModelPolicy.from_json(OPENAI_ONLY)
        gateway, kiln = _gateway(tmp_path, _attached_sub_agent("groq"), policy=policy)
        with pytest.raises(gw.GatewayError) as err:
            self._attach(gateway)
        assert f"sub-agent tool {SUB_TOOL}" in str(err.value)
        assert "outside the model policy" in str(err.value)
        assert not [m for m, *_ in kiln.calls if m == "POST"]

    def test_a_tool_that_calls_the_subject_task_is_refused(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, _attached_sub_agent(task_id=TASK))
        with pytest.raises(gw.GatewayError, match="the subject task itself"):
            self._attach(gateway)
        assert not [m for m, *_ in kiln.calls if m == "POST"]

    def test_a_tool_id_that_names_no_kiln_task_tool_is_refused(self, tmp_path):
        def gone(_query, _body):
            raise KilnAPIError("GET", "x", 404, {"detail": "not found"})

        routes = {
            ("GET", f"{P}/tool_servers/{SUB_SERVER}/config"): gone,
            ("GET", f"{P}/tool_servers/m1/config"): {"id": "m1", "type": "remote_mcp"},
        }
        gateway, _ = _gateway(tmp_path, routes)
        for tool in (SUB_TOOL, "kiln_task::m1"):
            with pytest.raises(gw.GatewayError, match="not a Kiln task tool"):
                self._attach(gateway, tool)

    def test_a_tool_whose_run_config_is_gone_is_refused(self, tmp_path):
        routes = _attached_sub_agent()
        routes[("GET", f"{P}/tasks/{SUB_TASK}/run_configs")] = []
        gateway, _ = _gateway(tmp_path, routes)
        with pytest.raises(gw.GatewayError, match="every call of the tool would fail"):
            self._attach(gateway)

    def test_a_drive_is_refused_for_the_model_of_its_sub_agent(self, tmp_path):
        rc = {
            "id": "rc1",
            "run_config_properties": {
                "model_name": "gpt-test",
                "model_provider_name": "openai",
                "tools_config": {"tools": [SUB_TOOL]},
            },
        }
        routes = {**_attached_sub_agent("groq"), ("GET", f"{BASE}/run_configs"): [rc]}
        policy = ModelPolicy.from_json(OPENAI_ONLY)
        gateway, kiln = _gateway(tmp_path, routes, policy=policy)
        with pytest.raises(gw.GatewayError, match="outside the model policy"):
            _run(gateway, "run_task", run_config_id="rc1", item_id="tr1")
        assert not [m for m, *_ in kiln.calls if m == "POST"]

    def test_a_drive_shows_the_sub_agent_calls_and_their_usage(self, tmp_path):
        """Kiln's `usage` holds the run's own model calls. The answer counts
        the sub-agent calls and gives the usage of the sub-agent's own run
        beside it. It never adds the two: the fix for the sum is in Kiln."""
        rc = {
            "id": "rc1",
            "run_config_properties": {
                "model_name": "m",
                "model_provider_name": "p",
                "tools_config": {"tools": [SUB_TOOL]},
            },
        }
        data = f"{PROJECT}:::{SUB_TOOL}:::{SUB_TASK}"
        run = {
            "id": "424242600030",
            "output": {"output": "ok"},
            "usage": {"cost": 0.01, "total_llm_latency_ms": 300.0},
            "trace": [
                {"role": "user", "content": "q"},
                {"role": "tool", "kiln_task_tool_data": f"{data}:::424242600031"},
                {"role": "tool", "kiln_task_tool_data": f"{data}:::None"},
            ],
        }
        routes = {
            **_attached_sub_agent(),
            ("GET", f"{BASE}/run_configs"): [rc],
            ("GET", BASE): {"id": TASK, "turn_mode": "single_turn"},
            ("POST", f"{BASE}/run"): run,
            ("GET", f"{P}/tasks/{SUB_TASK}/runs/424242600031"): {
                "id": "424242600031",
                "input": "SECRET",
                "usage": {"cost": 0.02, "total_llm_latency_ms": 700.0},
            },
        }
        gateway, _ = _gateway(tmp_path, routes)
        out = _run(gateway, "run_task", run_config_id="rc1", item_id="tr1")

        # `usage` is what Kiln gave. The sub-agent's own run is beside it.
        assert out["usage"]["cost"] == 0.01
        assert out["usage"]["total_llm_latency_ms"] == 300.0
        assert out["sub_agent_usage"]["cost"] == 0.02
        assert out["sub_agent_usage"]["total_llm_latency_ms"] == 700.0
        assert out["sub_agent_calls"] == 2
        assert "1 of them have no usage record" in out["sub_agent_note"]
        assert "it is not in `usage`" in out["sub_agent_note"]
        _clean(out)  # only the usage of the sub-agent's run is read

    def test_a_drive_with_no_sub_agent_call_says_so(self, tmp_path):
        rc = {
            "id": "rc1",
            "run_config_properties": {"model_name": "m", "model_provider_name": "p"},
        }
        routes = {
            ("GET", f"{BASE}/run_configs"): [rc],
            ("GET", BASE): {"id": TASK, "turn_mode": "single_turn"},
            ("POST", f"{BASE}/run"): {
                "id": "424242600040",
                "output": {"output": "ok"},
                "trace": [{"role": "user", "content": "q"}],
            },
        }
        gateway, _ = _gateway(tmp_path, routes)
        out = _run(gateway, "run_task", run_config_id="rc1", item_id="tr1")
        assert out["sub_agent_calls"] == 0 and out["sub_agent_note"] is None
        assert out["sub_agent_usage"] is None

    def test_a_train_result_that_calls_a_sub_agent_shows_no_cost(self, tmp_path):
        """The row's usage is the run's own model calls, not the request's.
        An agent that reads it would take a delegating arm for a cheap one."""
        results = f"{BASE}/evals/e1/eval_config/ec1/run_config/rc1/results"
        data = f"{PROJECT}:::{SUB_TOOL}:::{SUB_TASK}:::None"
        calls = _eval_run("tr1", "er1", "fine")
        calls["task_run_trace"] = json.dumps(
            [{"role": "tool", "kiln_task_tool_data": data}]
        )
        plain = _eval_run("tr2", "er2", "fine")
        routes = {
            ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
            ("GET", results): {"results": [calls, plain]},
        }
        gateway, _ = _gateway(tmp_path, routes)
        out = _run(gateway, "get_train_results", eval_id="e1", run_config_id="rc1")
        by_item = {r["item_id"]: r for r in out["rows"]}

        assert by_item["tr1"]["task_cost_usd"] is None
        assert by_item["tr1"]["latency_ms"] is None
        assert "calls a sub-agent" in by_item["tr1"]["usage_note"]
        assert by_item["tr2"]["task_cost_usd"] == 0.01
        assert by_item["tr2"]["usage_note"] is None

    def test_a_train_result_that_holds_the_sub_agents_usage_shows_its_cost(
        self, tmp_path
    ):
        """A Kiln with Kiln-AI/Kiln#2077 writes the sub-agent's usage on the
        tool message and in the run's usage: the cost is the request's. The
        latency never is."""
        results = f"{BASE}/evals/e1/eval_config/ec1/run_config/rc1/results"
        row = _eval_run("tr1", "er1", "fine")
        data = f"{PROJECT}:::{SUB_TOOL}:::{SUB_TASK}:::None"
        message = {"role": "tool", "kiln_task_tool_data": data}
        row["task_run_trace"] = json.dumps([{**message, "usage": {"cost": 0.004}}])
        routes = {
            ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
            ("GET", results): {"results": [row]},
        }
        gateway, _ = _gateway(tmp_path, routes)
        out = _run(gateway, "get_train_results", eval_id="e1", run_config_id="rc1")
        (shown,) = out["rows"]
        assert shown["task_cost_usd"] == 0.01 and shown["latency_ms"] is None
        assert shown["usage_note"].startswith("latency not measured")

    def test_two_allowed_models_let_the_two_agents_differ(self, tmp_path):
        """A policy with two models: the main agent on one, the sub-agent on
        the other. A third model is refused for either."""
        policy = ModelPolicy.from_json(
            {
                "allow": [
                    {"provider": "openai", "model": "gpt-test"},
                    {"provider": "groq", "model": "kimi"},
                ]
            }
        )
        routes = {
            **_sub_agent_routes({}),
            **_attached_sub_agent("groq"),
            ("POST", f"{BASE}/run_configs"): lambda _q, body: {"id": "rc9", **body},
        }
        routes[("GET", f"{P}/tasks/{SUB_TASK}/run_configs")] = [
            {
                "id": SUB_RC,
                "run_config_properties": {
                    "model_name": "kimi",
                    "model_provider_name": "groq",
                },
            }
        ]
        gateway, _ = _gateway(tmp_path, routes, policy=policy)
        cheap = {**SUB_PROPS, "model_provider_name": "groq", "model_name": "kimi"}
        made = _run(
            gateway, "create_sub_agent", **_sub_agent_args(run_config_properties=cheap)
        )
        assert (made["model_provider_name"], made["model_name"]) == ("groq", "kimi")
        assert SUB_TOOL in self._attach(gateway)["tools"]  # the main agent: openai
        other = {**SUB_PROPS, "model_name": "gpt-other"}
        with pytest.raises(gw.GatewayError, match="outside the model policy"):
            _run(
                gateway,
                "create_sub_agent",
                **_sub_agent_args(run_config_properties=other),
            )
