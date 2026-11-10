"""Under a speed run: the baseline is measured and
timed before wave 1, probes carry speed, and probe run configs leave the
project at wave end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_ctx, make_plan
from fake_dispatcher import ok
from fake_kiln import make_run
from test_orchestrator import rc_ok, seed_active_eval, seed_baseline
from test_speed_objective import _project, _rc

from kiln_harness_tuner.orchestrator import (
    archive_probe_run_configs,
    baseline_is_covered,
    orchestrate,
)
from kiln_harness_tuner.schemas import ProbeItem, ProbeResult


def _timed(base_ms: float = 2000.0, other_ms: float = 500.0):
    def handler(body: dict) -> dict:
        ms = base_ms if body.get("task_run_config_id") == "rc_base" else other_ms
        text = body.get("plaintext_input") or ""
        return {
            "id": "r",
            "usage": {"total_llm_latency_ms": ms + len(text), "cost": 0.002},
        }

    return handler


def _setup_ctx(tmp_path, objective: str):
    ctx = make_ctx(gate_agent="auto")
    ctx.objective = objective
    ctx.measure_baseline_first = True
    ctx.target = ctx.target.model_copy(update={"project_path": str(_project(tmp_path))})
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.kiln.run_configs = [_rc("rc_base")]
    ctx.kiln.run_handler = _timed()
    # The baseline has NO stored train rows (the live run's case): its
    # measurement stores all eight train items.
    ctx.kiln.pending[("eval_1", "rc_base", "train")] = [
        make_run(f"train{n}", accuracy=1.0) for n in range(8)
    ]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    return ctx


class TestMeasureTheBaselineFirst:
    @pytest.mark.parametrize("objective", ["quality", "speed"])
    async def test_wave_1_plans_on_measured_eval_sizes(self, tmp_path, objective):
        ctx = _setup_ctx(tmp_path, objective)
        await orchestrate(ctx, "g", timeout_mins=180)
        assert ("eval_1", "rc_base", "train") in ctx.kiln.job_invocations
        (plan_input,) = ctx.dispatch.calls_to("batch_plan")
        assert plan_input.eval_sizes == {"eval_1": 8}
        (measured,) = [f for k, f in ctx.log.events if k == "baseline_measured"]
        assert measured["line"].startswith("baseline measured on train (rc_base):")
        assert "eval_1 8/8 measured" in measured["line"]

    async def test_under_speed_wave_1_plans_on_the_timed_baseline(self, tmp_path):
        ctx = _setup_ctx(tmp_path, "speed")
        await orchestrate(ctx, "g", timeout_mins=180)
        (plan_input,) = ctx.dispatch.calls_to("batch_plan")
        latency = plan_input.baseline_latency
        assert latency is not None and latency.method == "paired_drive"
        assert latency.n_items == 8 and latency.split == "train"
        assert latency.baseline_median_ms is not None
        assert latency.baseline_median_ms > 2000 and latency.baseline_iqr_ms is not None
        (speed,) = [f for k, f in ctx.log.events if k == "baseline_speed"]
        assert speed["line"].startswith("baseline speed: median 2.0 s, p90 2.0 s")
        assert speed["line"].endswith("over 8 train items")
        # Timed once: the plan read the cached timing, no second pass.
        base_drives = [
            p for p in ctx.kiln.run_posts if p["task_run_config_id"] == "rc_base"
        ]
        assert len(base_drives) == 8
        # Judge-free: the timing pass ran no eval job beyond the measurement.
        assert ctx.kiln.job_invocations.count(("eval_1", "rc_base", "train")) == 1

    async def test_the_baseline_is_timed_at_setup_before_any_planning(self, tmp_path):
        """Setup, not the first plan: the timing line lands with the setup
        lines, before the reconnaissance battery and the planner."""
        ctx = _setup_ctx(tmp_path, "speed")
        await orchestrate(ctx, "g", timeout_mins=180)
        kinds = [
            (k, f.get("name"))
            for k, f in ctx.log.events
            if k in ("baseline_speed", "phase_start")
        ]
        speed_at = kinds.index(("baseline_speed", None))
        planning_at = kinds.index(("phase_start", "planning"))
        assert speed_at < planning_at
        assert kinds[speed_at - 1] == ("phase_start", "baseline_measure")

    async def test_the_measurement_and_the_timing_run_concurrently(self, tmp_path):
        """The timing drives start while the baseline's eval job is still
        running — not after it. Every fake call yields once, the way a real
        request does, so the two coroutines can interleave."""
        import asyncio

        ctx = _setup_ctx(tmp_path, "speed")
        real = ctx.kiln.call

        async def yielding(*args, **kwargs):
            await asyncio.sleep(0)
            return await real(*args, **kwargs)

        ctx.kiln.call = yielding
        await orchestrate(ctx, "g", timeout_mins=180)
        order = [(m, u) for m, u, _q, _b in ctx.kiln.calls]
        drive = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run"
        first_drive = next(i for i, (_m, u) in enumerate(order) if u == drive)
        job_done = next(i for i, (_m, u) in enumerate(order) if u == "/api/jobs/wait")
        assert first_drive < job_done

    async def test_progress_lines_while_the_baseline_is_measured(self, tmp_path):
        """A line of items done per eval every `progress_interval_secs`
        while the measurement job runs; none once it is done."""
        import asyncio

        ctx = _setup_ctx(tmp_path, "quality")
        ctx.progress_interval_secs = 0.01
        ctx.kiln.job_progress["job_1"] = {"success": 3, "error": 1, "total": 8}
        real = ctx.kiln.call

        async def slow_wait(method, url_path, *args, **kwargs):
            if url_path == "/api/jobs/wait":
                await asyncio.sleep(0.05)
            return await real(method, url_path, *args, **kwargs)

        ctx.kiln.call = slow_wait
        # A reporter that outlives the measurement would hang the run.
        await asyncio.wait_for(orchestrate(ctx, "g", timeout_mins=180), timeout=10)
        lines = [f["line"] for k, f in ctx.log.events if k == "baseline_progress"]
        assert lines, "no progress line while the job ran"
        assert lines[0] == (
            "baseline measuring: Eval eval_1 4/8 (1 errored) — 1 job(s) running"
        )
        measured = [k for k, _f in ctx.log.events if k == "baseline_measured"]
        after = [
            k
            for k, _f in ctx.log.events[
                [k for k, _f in ctx.log.events].index("baseline_measured") :
            ]
            if k == "baseline_progress"
        ]
        assert measured and not after

    async def test_the_progress_line_prints(self):
        from kiln_harness_tuner.ui import SETUP_LINE_EVENTS

        assert "baseline_progress" in SETUP_LINE_EVENTS

    async def test_under_quality_the_baseline_is_not_timed(self, tmp_path):
        ctx = _setup_ctx(tmp_path, "quality")
        await orchestrate(ctx, "g", timeout_mins=180)
        assert not [f for k, f in ctx.log.events if k == "baseline_speed"]
        assert not ctx.kiln.run_posts

    async def test_a_covered_baseline_is_not_measured_again(self, tmp_path):
        ctx = _setup_ctx(tmp_path, "quality")
        ctx.kiln.results[("eval_1", "rc_base", "train")] = [
            make_run(f"train{n}", accuracy=1.0) for n in range(8)
        ]
        await orchestrate(ctx, "g", timeout_mins=180)
        assert ("eval_1", "rc_base", "train") not in ctx.kiln.job_invocations
        (measured,) = [f for k, f in ctx.log.events if k == "baseline_measured"]
        assert (
            "eval_1 8/8" in measured["line"]
            and "measured" not in measured["line"].split(":")[1].split("—")[0]
        )

    def test_coverage_rule(self):
        assert not baseline_is_covered(0, 8, 0.95)
        assert not baseline_is_covered(7, 8, 0.95)  # 7 < ceil(7.6)
        assert baseline_is_covered(8, 8, 0.95)
        assert not baseline_is_covered(0, None, 0.95)
        assert baseline_is_covered(1, None, 0.95)

    async def test_off_unless_asked(self, tmp_path):
        ctx = _setup_ctx(tmp_path, "speed")
        ctx.measure_baseline_first = False
        ctx.objective = "quality"
        await orchestrate(ctx, "g", timeout_mins=180)
        assert not [f for k, f in ctx.log.events if k == "baseline_measured"]

    def test_the_cli_turns_it_on(self, tmp_path):
        import kiln_harness_tuner.cli as cli
        from kiln_harness_tuner.kiln import KilnClient
        from kiln_harness_tuner.paths import StatePaths

        target = cli.target_from_args(
            cli.build_parser().parse_args(
                ["--project-path", "p", "--project-id", "1", "--task-id", "2"]
            )
        )
        state = StatePaths(tmp_path / "state")
        run_dir = state.runs / "r"
        ctx = cli.build_ctx(
            target,
            KilnClient("http://localhost:1"),
            state=state,
            agents_dir=state.agents / "claude",
            run_id="r",
            run_dir=run_dir,
            mcp_config_path=run_dir / "m.json",
            settings_path=run_dir / "s.json",
        )
        assert ctx.measure_baseline_first is True


def test_the_setup_lines_print_in_the_plain_display():
    from io import StringIO

    from rich.console import Console

    from kiln_harness_tuner.status import RunStatusTracker
    from kiln_harness_tuner.ui import PlainUi

    out = StringIO()
    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="")
    ui = PlainUi(tracker, console=Console(file=out, width=200))
    for kind in ("baseline_measured", "baseline_speed", "probe_rcs_archived"):
        ui.event(kind, line=f"{kind} line")
    text = out.getvalue()
    for kind in ("baseline_measured", "baseline_speed", "probe_rcs_archived"):
        assert f"{kind} line" in text, kind


# ---- probe run configs leave the project at wave end ----


def _write_rc(project: Path, rc_id: str, name: str) -> Path:
    folder = project / "tasks" / "t1 - T" / "run_configs" / f"{rc_id} - {name[:20]}"
    folder.mkdir(parents=True)
    (folder / "task_run_config.kiln").write_text(
        json.dumps({"id": rc_id, "name": name})
    )
    return folder


def _eval_run_for(project: Path, rc_id: str) -> None:
    run = (
        project
        / "tasks"
        / "t1 - T"
        / "evals"
        / "eval_1 - E"
        / "configs"
        / "c"
        / "runs"
        / f"er_{rc_id}"
    )
    run.mkdir(parents=True)
    (run / "eval_run.kiln").write_text(
        json.dumps({"id": f"er_{rc_id}", "task_run_config_id": rc_id})
    )


async def test_probe_rcs_are_archived_and_measured_ones_are_kept(tmp_path):
    project = _project(tmp_path)
    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(update={"project_path": str(project)})
    ctx.quarantine_root = tmp_path / "q"
    ctx.policy_exempt_rcs = {"rc_base"}
    folders = {
        "rc_p1": _write_rc(project, "rc_p1", "PROBE w1-1 - thinking none"),
        "rc_p2": _write_rc(project, "rc_p2", "PROBE r0-4: json"),
        "rc_measured": _write_rc(project, "rc_measured", "PROBE w1-2 - measured"),
        "rc_base_probe": _write_rc(project, "rc_base", "PROBE mislabelled baseline"),
        "rc_arm": _write_rc(project, "rc_arm", "arm w1-3"),
    }
    _eval_run_for(project, "rc_measured")
    ctx.kiln.run_configs = [
        {"id": "rc_p1", "name": "PROBE w1-1 - thinking none"},
        {"id": "rc_p2", "name": "PROBE r0-4: json"},
        {"id": "rc_measured", "name": "PROBE w1-2 - measured"},
        {"id": "rc_base", "name": "PROBE mislabelled baseline"},
        {"id": "rc_arm", "name": "arm w1-3"},
    ]
    assert await archive_probe_run_configs(ctx) == 2
    assert not folders["rc_p1"].exists() and not folders["rc_p2"].exists()
    for kept in ("rc_measured", "rc_base_probe", "rc_arm"):
        assert folders[kept].exists(), kept
    archived = list(
        (tmp_path / "q" / "archived_run_configs").rglob("task_run_config.kiln")
    )
    assert len(archived) == 2
    (event,) = [f for k, f in ctx.log.events if k == "probe_rcs_archived"]
    assert sorted(event["rc_ids"]) == ["rc_p1", "rc_p2"]
    # Idempotent.
    ctx.kiln.run_configs = [
        r for r in ctx.kiln.run_configs if r["id"] not in ("rc_p1", "rc_p2")
    ]
    assert await archive_probe_run_configs(ctx) == 0


async def test_the_task_default_rc_is_never_archived(tmp_path):
    project = _project(tmp_path)
    (project / "tasks" / "t1 - T" / "task.kiln").write_text(
        json.dumps({"id": "t1", "default_run_config_id": "rc_p1"})
    )
    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(update={"project_path": str(project)})
    ctx.quarantine_root = tmp_path / "q"
    folder = _write_rc(project, "rc_p1", "PROBE w1-1")
    ctx.kiln.run_configs = [{"id": "rc_p1", "name": "PROBE w1-1"}]
    assert await archive_probe_run_configs(ctx) == 0
    assert folder.exists()


async def test_the_loop_archives_at_wave_end(tmp_path):
    from conftest import fix_item

    project = _project(tmp_path)
    ctx = make_ctx(gate_agent="auto")
    ctx.target = ctx.target.model_copy(update={"project_path": str(project)})
    ctx.quarantine_root = tmp_path / "q"
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    _write_rc(project, "rc_p1", "PROBE r0-1")
    ctx.kiln.run_configs = [_rc("rc_base"), {"id": "rc_p1", "name": "PROBE r0-1"}]
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1", promotion_candidate=False))),
        ok(make_plan(loop_exit="done")),
    )
    ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_c"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert [f for k, f in ctx.log.events if k == "probe_rcs_archived"]


# ---- probes answer mechanism questions; nothing times them ----


async def test_an_agent_probe_is_never_timed_under_speed(tmp_path):
    """Speed is measured by the engine only. An agent probe's fork gets no
    paired drives and its answer carries no speed field."""
    from kiln_harness_tuner.items import execute_plan_item
    from kiln_harness_tuner.schemas import BaselineRef

    ctx = make_ctx()
    ctx.objective = "speed"
    ctx.target = ctx.target.model_copy(update={"project_path": str(_project(tmp_path))})
    ctx.kiln.run_configs = [_rc("rc_base"), _rc("rc_fork")]
    ctx.kiln.run_handler = _timed(base_ms=3000.0, other_ms=1000.0)
    item = ProbeItem(
        id="w1-1",
        type="probe",
        question="Does the model hold JSON with thinking off?",
        method="agent reads traces",
        reason="speed lever",
        max_drives=4,
    )
    ctx.dispatch.queue(
        "probe",
        ok(
            ProbeResult(
                answer="yes",
                evidence="4/4 valid",
                drives=4,
                ephemeral_rc_ids=["rc_fork"],
            )
        ),
    )
    outcome = await execute_plan_item(
        ctx, item, BaselineRef(rc_id="rc_base"), ["rc_base"], 1
    )
    assert outcome.status == "completed"
    assert not ctx.kiln.run_posts
    assert "latency" not in type(outcome.probe).model_fields
    assert "latency" not in type(ctx.probe_answers[-1]).model_fields


async def test_a_baseline_the_policy_denies_is_still_measured_and_timed(tmp_path):
    """A policy can deny every Claude model and all of OpenRouter as a
    candidate while the baseline is a Claude model through
    OpenRouter: the baseline must still be measured, timed and used as the
    parent side; the planner's model list must hold no Claude model."""
    from kiln_harness_tuner.model_policy import ModelPolicy

    ctx = _setup_ctx(tmp_path, "speed")
    ctx.model_policy = ModelPolicy.from_json(
        {
            "allow": [{"provider": "gemini_api", "model": "gemini_3_8_flash"}],
            "deny": [
                {"provider": "*", "model": "claude_*"},
                {"provider": "openrouter", "model": "*"},
            ],
            "exceptions": [{"provider": "cerebras", "model": "qwen_3p8_27b"}],
        }
    )
    ctx.kiln.run_configs = [_rc("rc_base", "openrouter", "claude_sonnet_4_6")]
    ctx.kiln.available_models = [
        {
            "provider_id": "openrouter",
            "models": [{"id": "claude_sonnet_4_6"}, {"id": "gemini_3_8_flash"}],
        },
        {"provider_id": "anthropic", "models": [{"id": "claude_4_5_haiku"}]},
        {"provider_id": "gemini_api", "models": [{"id": "gemini_3_8_flash"}]},
        {"provider_id": "cerebras", "models": [{"id": "qwen_3p8_27b"}]},
    ]
    await orchestrate(ctx, "g", timeout_mins=180)
    assert ("eval_1", "rc_base", "train") in ctx.kiln.job_invocations
    assert [f for k, f in ctx.log.events if k == "baseline_speed"][0]["latency"]
    assert not [f for k, f in ctx.log.events if k == "model_policy_refused"]
    assert [f for k, f in ctx.log.events if k == "baseline_outside_policy"]
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    models = {(m["provider"], m["model"]) for m in plan_input.allowed_models}
    assert models == {("gemini_api", "gemini_3_8_flash"), ("cerebras", "qwen_3p8_27b")}
