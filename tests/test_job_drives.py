"""Drives through eval jobs: a multi-turn task, a run config
with an OpenEnv tool, or an item with an `openenv_reset` cannot run on the plain
run endpoint, so its timing drives, probe drives and the gateway's
`run_task` go through one-item eval jobs. Judge-free where the server has
them, judged (with one log line) where it does not."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import make_ctx
from fake_kiln import FakeKiln, make_run
from test_gateway import BASE, TASK, _gateway, _run
from test_orchestrator import seed_active_eval
from test_speed_objective import _rc, _speed_ctx

from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.job_drives import (
    EvalJobDriver,
    doc_needs_eval_job,
    driver_for,
    lists_openenv_tool,
    openenv_tool_parts,
)
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.latency import paired_drive_pass, split_inputs, timed_drives
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.probes import drive_set, input_from_doc
from kiln_harness_tuner.provider_errors import ProviderQuotaError

OPENENV_TOOL = "kiln_tool::openenv::424242500001::append_note"


def _multi_turn_doc(item_id: str, tags: list[str], text: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "tags": tags,
        "data": {
            "type": "multi_turn_synthetic",
            "first_message": {"text": text},
            "synthetic_user_info": {"persona": "a technician", "goal": "log it"},
            "drive_config": {"model_name": "m", "model_provider": "p", "turns": 3},
        },
        "openenv_reset": {"openenv_server_id": "424242500001", "reset_kwargs": {}},
    }


def _project(tmp_path: Path, n: int = 6) -> Path:
    """eval_1's splits on disk, with `n` multi-turn world items per split."""
    task = tmp_path / "proj" / "tasks" / "t1 - T"
    evals = task / "evals" / "eval_1 - E"
    evals.mkdir(parents=True)
    splits = {
        name: {"source": "eval_input", "filter_id": f"tag::{name}"}
        for name in ("train", "val", "test")
    }
    (evals / "eval.kiln").write_text(json.dumps({"id": "eval_1", "splits": splits}))
    for split in ("train", "val", "test"):
        for i in range(n):
            item = task / "eval_inputs" / f"{split}{i}"
            item.mkdir(parents=True)
            doc = _multi_turn_doc(f"{split}{i}", [split], f"{split} opening {i}")
            (item / "eval_input.kiln").write_text(json.dumps(doc))
    return tmp_path / "proj"


def _run_of(ms: float, cost: float, su_cost: float = 0.0) -> dict[str, Any]:
    return {
        "usage": {"total_llm_latency_ms": ms, "cost": cost},
        "synthetic_user_usage": {"cost": su_cost},
        "output": {"output": "done"},
        "trace": [
            {"role": "user", "content": "opening"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "more"},
            {"role": "assistant", "content": "done"},
        ],
    }


def _ctx(tmp_path: Path, *, max_concurrent: int | None = None):
    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(update={"project_path": str(_project(tmp_path))})
    if max_concurrent is not None:
        ctx.analyzer._job_semaphore = asyncio.Semaphore(max_concurrent)
    seed_active_eval(ctx.kiln)
    ctx.kiln.task = {
        "id": "t1",
        "name": "Multi-turn world task",
        "turn_mode": "multiturn",
    }
    ctx.kiln.run_configs = [_rc("rc_base"), _rc("rc_cand")]

    def drive(rc_id: str, item_id: str, split: str) -> dict[str, Any]:
        return _run_of(1000.0 if rc_id == "rc_base" else 400.0, 0.002, su_cost=0.01)

    ctx.kiln.drive_handler = drive
    return ctx


# ------------------------------------------------------------ the inputs


class TestInputs:
    def test_a_multi_turn_input_is_drivable_through_a_job(self):
        inp = input_from_doc(_multi_turn_doc("a", ["train"], "hello there"))
        assert inp is not None
        assert inp.plaintext == "hello there" and inp.structured is None
        assert inp.multi_turn and inp.needs_eval_job

    def test_a_single_turn_input_with_an_openenv_reset_needs_a_job(self):
        doc = {
            "id": "b",
            "data": {"type": "single_turn", "user_message": {"text": "x"}},
            "openenv_reset": {"openenv_server_id": "w", "reset_kwargs": {}},
        }
        inp = input_from_doc(doc)
        assert inp is not None and inp.needs_eval_job and not inp.multi_turn
        del doc["openenv_reset"]
        assert not doc_needs_eval_job(doc)
        assert not input_from_doc(doc).needs_eval_job  # type: ignore[union-attr]

    def test_a_multi_turn_input_with_no_opening_is_not_drivable(self):
        doc = _multi_turn_doc("c", ["train"], "")
        assert input_from_doc(doc) is None

    def test_split_inputs_keeps_multi_turn_items_and_names_their_eval(self, tmp_path):
        inputs = split_inputs(str(_project(tmp_path, n=2)), "t1", "train")
        assert [i.id for i in inputs] == ["train0", "train1"]
        assert all(i.eval_id == "eval_1" and i.needs_eval_job for i in inputs)

    def test_an_openenv_tool_is_named(self):
        assert lists_openenv_tool({"tools_config": {"tools": [OPENENV_TOOL]}})
        assert not lists_openenv_tool({"tools_config": {"tools": ["kiln_tool::x"]}})
        assert not lists_openenv_tool(None)
        # The name Kiln used before v1.2.1 is not an OpenEnv tool.
        old = "kiln_tool::world::424242500001::append_note"
        assert not lists_openenv_tool({"tools_config": {"tools": [old]}})

    def test_an_openenv_tool_id_splits_into_server_and_tool(self):
        assert openenv_tool_parts(OPENENV_TOOL) == ("424242500001", "append_note")
        assert openenv_tool_parts("kiln_tool::openenv::424242500001") is None
        # Kiln refuses more than four parts; so does the parser.
        assert openenv_tool_parts("kiln_tool::openenv::s1::a::b") is None
        assert openenv_tool_parts("kiln_tool::code::1") is None


# ------------------------------------------------------------ the driver


class TestTheDriver:
    async def test_needed_for_a_multi_turn_task_a_world_tool_or_a_world_input(
        self,
    ):
        kiln = FakeKiln()
        single = EvalJobDriver(kiln.call, "p1", "t1")
        assert not await single.needed([{"model_name": "m"}], [])
        assert await single.needed([{"tools_config": {"tools": [OPENENV_TOOL]}}], [])
        world_input = input_from_doc(_multi_turn_doc("a", ["train"], "hi"))
        assert await single.needed([{}], [world_input])
        kiln.task = {"id": "t1", "turn_mode": "multiturn"}
        assert await EvalJobDriver(kiln.call, "p1", "t1").needed([{}], [])

    async def test_a_judge_free_drive_returns_the_driven_task_run(self):
        kiln = FakeKiln()
        seed_active_eval(kiln)
        kiln.drive_handler = lambda rc, item, split: _run_of(800.0, 0.003, 0.02)
        driver = EvalJobDriver(kiln.call, "p1", "t1")

        outcome = await driver.drive("rc_base", "train0", "eval_1", "train")

        assert outcome.error is None and not outcome.judged
        assert outcome.run is not None
        assert outcome.run["usage"]["total_llm_latency_ms"] == 800.0
        # The synthetic user's spend is provider spend, never the metric.
        assert outcome.overhead_cost == pytest.approx(0.02)
        (body,) = [b for _m, p, _q, b in kiln.calls if p == "/api/jobs/evals/run"]
        assert body["judge"] is False
        assert body["split"] == "train" and body["item_ids"] == ["train0"]
        assert body["eval_id"] == "eval_1" and body["eval_config_id"] == "ec_1"
        assert not kiln.run_posts

    async def test_a_failed_drive_carries_the_jobs_own_error_text(self):
        kiln = FakeKiln()
        seed_active_eval(kiln)
        kiln.drive_handler = lambda rc, item, split: "error:Rate limit exceeded"
        outcome = await EvalJobDriver(kiln.call, "p1", "t1").drive(
            "rc_base", "train0", "eval_1"
        )
        assert outcome.run is None
        assert "Rate limit exceeded" in (outcome.error or "")

    async def test_an_older_server_gets_judged_jobs_and_one_log_line(self):
        ctx = make_ctx()
        kiln = ctx.kiln
        seed_active_eval(kiln)
        kiln.judge_free_jobs = False
        kiln.pending[("eval_1", "rc_base", "train")] = [
            {
                **make_run("train0", cost=0.004, eval_cost=0.001, accuracy=1.0),
                "eval_input_id": "train0",
                "task_run_usage": {"cost": 0.004, "total_llm_latency_ms": 700.0},
                "output": "done",
            }
        ]
        driver = driver_for(ctx)

        first = await driver.drive("rc_base", "train0", "eval_1")
        await driver.drive("rc_base", "train0", "eval_1")

        assert first.judged and first.run is not None
        assert first.run["usage"]["total_llm_latency_ms"] == 700.0
        assert first.overhead_cost == pytest.approx(0.001)  # the judge
        bodies = [b for _m, p, _q, b in kiln.calls if p == "/api/jobs/evals/run"]
        assert all("judge" not in b for b in bodies)
        notes = [f for k, f in ctx.log.events if k == "eval_job_drives_judged"]
        assert len(notes) == 1

    async def test_a_legacy_default_judge_falls_back_to_judged_jobs(self):
        kiln = FakeKiln()
        seed_active_eval(kiln)
        kiln.pending[("eval_1", "rc_base", "train")] = [
            {**make_run("train0", accuracy=1.0), "eval_input_id": "train0"}
        ]
        inner = kiln.call

        async def call(method, path, query_params=None, body=None, **kw):
            if path == "/api/jobs/evals/run" and (body or {}).get("judge") is False:
                raise KilnAPIError(
                    method,
                    path,
                    400,
                    {"detail": "Eval config 'g' is not a V2 judge with an adapter"},
                )
            return await inner(method, path, query_params, body, **kw)

        outcome = await EvalJobDriver(call, "p1", "t1").drive(
            "rc_base", "train0", "eval_1"
        )
        assert outcome.judged and outcome.run is not None

    async def test_a_drive_never_names_the_test_split(self):
        driver = EvalJobDriver(FakeKiln().call, "p1", "t1")
        with pytest.raises(ValueError, match="test"):
            await driver.drive("rc_base", "test0", "eval_1", "test")


# ------------------------------------------------------------ timing drives


class TestTimingDrives:
    async def test_the_pairs_run_back_to_back_with_the_order_rotating(self, tmp_path):
        ctx = _ctx(tmp_path)
        inputs = split_inputs(ctx.target.project_path, "t1", "train")

        paired = await paired_drive_pass(
            ctx, "rc_base", ["rc_cand"], inputs, "train", "t"
        )

        assert not ctx.kiln.run_posts
        drives = ctx.kiln.job_drives
        assert len(drives) == 2 * len(inputs)
        assert all(split == "train" for _rc, _i, split in drives)
        # Per item, both arms; the first arm alternates from item to item.
        first_arm = {}
        for rc, item, _split in drives:
            first_arm.setdefault(item, rc)
        assert sorted(set(first_arm.values())) == ["rc_base", "rc_cand"]
        latency = paired.latency["rc_cand"]
        assert latency.n_items == len(inputs) and latency.median_ratio == 0.4
        # Spend counts the synthetic user; the per-request cost does not.
        assert paired.spend == pytest.approx(len(drives) * (0.002 + 0.01))
        assert paired.cost["rc_cand"].candidate_median_ms == pytest.approx(0.002)

    async def test_the_gate_times_val_items_through_val_jobs(self, tmp_path):
        ctx = _ctx(tmp_path)
        inputs = split_inputs(ctx.target.project_path, "t1", "val")
        await paired_drive_pass(ctx, "rc_base", ["rc_cand"], inputs, "val", "gate")
        assert {split for _rc, _i, split in ctx.kiln.job_drives} == {"val"}

    async def test_the_jobs_take_the_runs_eval_job_slots(self, tmp_path):
        ctx = _ctx(tmp_path, max_concurrent=1)
        inflight = peak = 0
        inner = ctx.kiln._wait_job

        async def counted(body):
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.001)
            try:
                return await inner(body)
            finally:
                inflight -= 1

        ctx.kiln._wait_job = counted
        inputs = split_inputs(ctx.target.project_path, "t1", "train")
        await timed_drives(ctx, ["rc_base", "rc_cand"], inputs, "t")
        assert peak == 1

    async def test_a_quota_error_in_a_job_stops_the_run(self, tmp_path):
        ctx = _ctx(tmp_path)
        ctx.kiln.drive_handler = lambda rc, item, split: (
            "error:insufficient_quota: You exceeded your current quota"
        )
        inputs = split_inputs(ctx.target.project_path, "t1", "train")
        with pytest.raises(ProviderQuotaError):
            await timed_drives(ctx, ["rc_base"], inputs, "t")

    async def test_a_single_turn_task_keeps_the_run_endpoint(self, tmp_path):
        ctx = _ctx(tmp_path)
        ctx.kiln.task = {"id": "t1"}
        drives = await timed_drives(ctx, ["rc_base"], [_plain("x1")], "t")
        assert ctx.kiln.run_posts and not ctx.kiln.job_drives
        assert drives.planned == 1


def _plain(item_id: str):
    return input_from_doc(
        {
            "id": item_id,
            "data": {"type": "single_turn", "user_message": {"text": "plain"}},
        }
    )


# ------------------------------------------------------------ probe drives


async def test_a_probe_drives_through_train_jobs(tmp_path):
    ctx = _ctx(tmp_path)
    inputs = split_inputs(ctx.target.project_path, "t1", "train")[:3]
    out = await drive_set(
        ctx, "rc_base", {}, "w1-1", inputs, "tool_calls", via_jobs=True
    )
    assert out.item_ids == [i.id for i in inputs]
    assert out.cost_usd == pytest.approx(3 * (0.002 + 0.01))
    assert {s for _rc, _i, s in ctx.kiln.job_drives} == {"train"}
    assert not ctx.kiln.run_posts


# ------------------------------------------------------------ the gateway


def _gateway_routes(world_item: str) -> dict:
    rc = {
        "id": "rc1",
        "run_config_properties": {"model_name": "m", "model_provider_name": "p"},
    }
    jobs: list[dict] = []

    def start(_q, body):
        jobs.append(body)
        return {"job_id": "j1", "status": "pending"}

    return {
        ("GET", f"{BASE}/run_configs"): [rc],
        ("GET", BASE): {"id": TASK, "turn_mode": "multiturn"},
        ("GET", "/openapi.json"): {
            "components": {"schemas": {"EvalJobParams": {"properties": {"judge": {}}}}}
        },
        ("GET", f"{BASE}/evals/e1"): {"id": "e1", "current_config_id": "ec1"},
        ("POST", "/api/jobs/evals/run"): start,
        ("POST", "/api/jobs/wait"): [
            {
                "status": "succeeded",
                "result": {
                    "driven_runs": [
                        {"item_id": world_item, "task_run_id": "424242600020"}
                    ]
                },
            }
        ],
        ("GET", f"{BASE}/runs/424242600020"): {
            "id": "424242600020",
            "output": {"output": "logged"},
            "usage": {"total_llm_latency_ms": 900.0, "cost": 0.01},
        },
        "jobs": jobs,
    }


def test_the_gateway_drives_a_multi_turn_train_item_through_a_train_job(tmp_path):
    routes = _gateway_routes("mt1")
    jobs = routes.pop("jobs")
    # On disk before the gateway computes its train set: a train item of e1.
    item_dir = tmp_path / "proj" / "tasks" / f"{TASK} - T" / "eval_inputs" / "mt1"
    item_dir.mkdir(parents=True)
    (item_dir / "eval_input.kiln").write_text(
        json.dumps(_multi_turn_doc("mt1", ["train_a"], "opening line"))
    )
    gateway, kiln = _gateway(tmp_path, routes)
    assert "mt1" in gateway.s.train.per_eval["e1"]

    out = _run(gateway, "run_task", run_config_id="rc1", item_id="mt1")

    (body,) = jobs
    assert body["judge"] is False and body["split"] == "train"
    assert body["item_ids"] == ["mt1"] and body["eval_id"] == "e1"
    assert out["output"] == "logged" and out["usage"]["total_llm_latency_ms"] == 900.0
    assert gateway.s.created["424242600020"] == ("task_run", "train", "mt1")
    assert not [c for c in kiln.calls if c[1] == f"{BASE}/run"]
    listed = {
        row["id"]: row
        for row in _run(gateway, "list_train_items", limit=50)
        if isinstance(row, dict)
    }
    assert listed["mt1"]["multi_turn"] is True
    assert listed["mt1"]["plaintext_input"] == "opening line"
    # A held-out item is still refused, on this route too.
    with pytest.raises(gw.GatewayError, match="not a train item"):
        _run(gateway, "run_task", run_config_id="rc1", item_id="va1")


# ------------------------------------------------------------ end to end


def _as_multi_turn(project_root: Path) -> None:
    """Rewrite every eval input of `_speed_ctx`'s project as a multi-turn
    world item with the same opening text."""
    for path in project_root.glob("tasks/*/eval_inputs/*/eval_input.kiln"):
        doc = json.loads(path.read_text())
        text = doc["data"]["user_message"]["text"]
        path.write_text(json.dumps(_multi_turn_doc(doc["id"], doc["tags"], text)))


async def test_a_multi_turn_cost_run_times_and_promotes_through_eval_jobs(
    tmp_path,
):
    """A multi-turn world task end to end: the baseline timing, the train timing
    chunks and the val gate all drive through judge-free eval jobs; the run
    endpoint is never called; the clearly cheaper arm is promoted."""
    ctx = _speed_ctx(tmp_path, cand_ms=1500.0, cand_cost=0.0002, objective="cost")
    _as_multi_turn(Path(ctx.target.project_path))
    kiln = ctx.kiln
    kiln.task = {"id": "t1", "name": "Fake task", "turn_mode": "multiturn"}
    handler = kiln.run_handler

    def drive(rc_id: str, item_id: str, split: str) -> dict[str, Any]:
        n = item_id.removeprefix(split)
        return handler(
            {"task_run_config_id": rc_id, "plaintext_input": f"{split} input {n}"}
        )

    kiln.drive_handler = drive
    kiln.run_handler = None

    assert await orchestrate(ctx, "cheaper", timeout_mins=180) == "done"

    assert not kiln.run_posts
    driven = {(rc, split) for rc, _item, split in kiln.job_drives}
    assert driven == {
        ("rc_base", "train"),
        ("rc_cand", "train"),
        ("rc_base", "val"),
        ("rc_cand", "val"),
    }
    assert "rc_rc_cand" in kiln.memories["mem_baseline"]["tags"]
