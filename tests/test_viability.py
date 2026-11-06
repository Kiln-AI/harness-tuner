"""Screening in code:
screening rounds (targeted or not) as N candidates through
`kiln_harness_tuner.measure`, the code verdicts, the opening thinking-level round and
the planner loop. The engine's own tests are `test_measure.py`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_ctx, make_plan
from fake_dispatcher import ok
from fake_kiln import make_eval, make_run
from test_orchestrator import seed_baseline

from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.orchestrator import orchestrate, run_opening_screen
from kiln_harness_tuner.schemas import (
    BaselineRef,
    ScreenCandidate,
    ScreenRequest,
    StopRule,
)
from kiln_harness_tuner.viability import (
    SCREEN_ROUND1_PER_EVAL,
    initial_screen_request,
    run_screening_round,
)

EVALS = ("e1", "e2")


def _write_input(task: Path, item_id: str, tag: str, text: str) -> None:
    d = task / "eval_inputs" / item_id
    d.mkdir(parents=True)
    (d / "eval_input.kiln").write_text(
        json.dumps(
            {
                "id": item_id,
                "tags": [tag],
                "data": {"type": "single_turn", "user_message": {"text": text}},
            }
        ),
        encoding="utf-8",
    )


def _project(tmp_path: Path, evals: dict[str, list[int]]) -> Path:
    """evals: eval id -> the prompt length of each of its train items."""
    task = tmp_path / "proj" / "tasks" / "t1 - T"
    for eval_id, lengths in evals.items():
        folder = task / "evals" / f"{eval_id} - E"
        folder.mkdir(parents=True)
        splits = {
            n: {"source": "eval_input", "filter_id": f"tag::{n}_{eval_id}"}
            for n in ("train", "val", "test")
        }
        (folder / "eval.kiln").write_text(
            json.dumps({"id": eval_id, "splits": splits}), encoding="utf-8"
        )
        for n, length in enumerate(lengths):
            _write_input(task, f"{eval_id}_{n}", f"train_{eval_id}", "x" * length)
    return tmp_path / "proj"


def _seed_evals(ctx, eval_ids=EVALS):
    for e in eval_ids:
        ctx.kiln.evals[e] = make_eval(e, f"ec_{e}")
    ctx.kiln.specs = [
        {"id": f"s_{e}", "eval_id": e, "status": "active"} for e in eval_ids
    ]


def _rc(rc_id, provider="openrouter", model="claude_sonnet_4_6", thinking="medium"):
    return {
        "id": rc_id,
        "name": rc_id,
        "created_at": "2026-09-20T10:00:00-04:00",
        "run_config_properties": {
            "type": "kiln_agent",
            "model_provider_name": provider,
            "model_name": model,
            "thinking_level": thinking,
        },
    }


def _ctx(
    tmp_path,
    *,
    objective="speed",
    latency=None,
    output='{"a": 1}',
    no_reasoning=False,
    served_model=None,
    output_tokens=None,
):
    """Two evals, 10 train items each; the baseline passes 9 of e1 (strong)
    and 3 of e2 (weak). `latency`: rc -> ms (the baseline 34 s)."""
    project = _project(tmp_path, {e: [10 * (n + 1) for n in range(10)] for e in EVALS})
    ctx = make_ctx()
    ctx.objective = objective
    ctx.target = ctx.target.model_copy(update={"project_path": str(project)})
    _seed_evals(ctx)
    seed_baseline(ctx.kiln)
    ctx.kiln.run_configs = [_rc("rc_base")]
    ctx.kiln.results[("e1", "rc_base", "train")] = [
        make_run(f"e1_{n}", accuracy=0.0 if n == 9 else 1.0) for n in range(10)
    ]
    ctx.kiln.results[("e2", "rc_base", "train")] = [
        make_run(f"e2_{n}", accuracy=1.0 if n < 3 else 0.0) for n in range(10)
    ]
    ctx.kiln.available_models = [
        {
            "provider_id": "openrouter",
            "models": [
                {
                    "id": "claude_sonnet_4_6",
                    "available_thinking_levels": {
                        "Off": "none",
                        "Low": "low",
                        "Medium": "medium",
                        "High": "high",
                    },
                }
            ],
        },
        {
            "provider_id": "gemini_api",
            "models": [
                {
                    "id": "gemini_3_8_flash",
                    "available_thinking_levels": {"Low": "low", "High": "high"},
                }
            ],
        },
        # A model Kiln offers no thinking levels for (GLM 5.3 on Together).
        {"provider_id": "together_ai", "models": [{"id": "glm_5_3"}]},
    ]
    speeds = {"rc_base": 34000.0, **(latency or {})}

    def handler(body):
        rc = body["task_run_config_id"]
        ms = speeds.get(rc, 3000.0)
        props = body.get("run_config_properties") or {}
        thinks = props.get("thinking_level") not in (None, "none")
        return {
            "id": "r",
            "usage": {
                "total_llm_latency_ms": ms,
                "cost": 0.075 if rc == "rc_base" else 0.004,
                # The declared thinking level shows in the run.
                "reasoning_tokens": (0 if no_reasoning else 120) if thinks else 0,
                # `output_tokens`: rc -> the output-token count of its drives
                # (absent = the provider records none).
                **(
                    {"output_tokens": output_tokens.get(rc, output_tokens.get("*"))}
                    if output_tokens
                    else {}
                ),
            },
            "output": {
                "output": output,
                "source": {
                    "properties": {
                        "model_name": served_model or props.get("model_name"),
                        "model_provider": props.get("model_provider_name"),
                    }
                },
            },
        }

    ctx.kiln.run_handler = handler
    return ctx


def _judge(ctx, rc_id: str, scores: dict[str, float]):
    """What the candidate's judged rows will be, per item id."""
    for eval_id in EVALS:
        ctx.kiln.pending[(eval_id, rc_id, "train")] = [
            make_run(i, accuracy=v) for i, v in scores.items() if i.startswith(eval_id)
        ]


def _request(*candidates: ScreenCandidate, **kw) -> ScreenRequest:
    return ScreenRequest(candidates=list(candidates), **kw)


GEMINI = ScreenCandidate(
    id="s1-1",
    overrides={
        "model_name": "gemini_3_8_flash",
        "model_provider_name": "gemini_api",
        "thinking_level": "low",
    },
)


class TestAScreeningRound:
    async def test_every_candidate_on_the_same_items_with_a_table(self, tmp_path):
        ctx = _ctx(tmp_path)
        for rc in ("rc_probe_1", "rc_probe_2"):
            _judge(ctx, rc, {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        good = GEMINI
        slow = ScreenCandidate(id="s1-2", overrides={"thinking_level": "high"})
        results = await run_screening_round(ctx, _request(good, slow), "rc_base", 1)
        by_id = {r.candidate_id: r for r in results}
        # rc_probe ids are made in order: rc_probe_1 = good, rc_probe_2 = slow.
        assert by_id["s1-1"].rc_id == "rc_probe_1"
        # A thinking-level candidate keeps the round's size. The model swap
        # is judged on every baseline pass (12 < SWAP_MIN_GUARDS): e1 holds 9,
        # so its own cap is 9 items per eval.
        assert by_id["s1-2"].items == 2 * SCREEN_ROUND1_PER_EVAL
        assert by_id["s1-1"].items == 18 and by_id["s1-1"].guards_required == 12
        assert by_id["s1-1"].baseline_passed == 12
        (table,) = [f for k, f in ctx.log.events if k == "screening_round"]
        assert table["line"].startswith(
            "screening round 1 (size r1, up to 3 item(s) per eval"
        )
        assert "screen s1-1 gemini_3_8_flash@gemini_api (thinking low)" in table["line"]
        # The event's results carry each candidate's overrides
        # and its round's baseline (the report's trade-off appendix).
        logged = {r["candidate_id"]: r for r in table["results"]}
        assert logged["s1-2"]["overrides"] == {"thinking_level": "high"}
        assert logged["s1-2"]["baseline_rc_id"] == "rc_base"
        assert logged["s1-2"]["round_no"] == 1
        # One queue: the thinking candidate's items are the first of the
        # swap's.
        jobs = [
            (c[3]["run_config_id"], c[3]["eval_id"], tuple(c[3].get("item_ids", ())))
            for c in ctx.kiln.calls
            if c[1] == "/api/jobs/evals/run" and c[3].get("item_ids")
        ]
        items = {
            rc: {(e, i) for r, e, ids in jobs if r == rc for i in ids}
            for rc, _e, _i in jobs
        }
        assert items["rc_probe_2"] < items["rc_probe_1"]

    async def test_clearly_slower_is_a_dead_end_and_never_judged(self, tmp_path):
        ctx = _ctx(tmp_path, latency={"rc_probe_1": 40000.0})
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "dead_end"
        assert result.stop_kind == "latency_ratio"
        # Stopped by the paired speed test on the first timing chunk (15
        # pairs, taken from the whole split), before any item is judged.
        assert result.reason.startswith(
            "stopped at n=15: latency ratio median 1.18 > 1.00, 15 of 15 above"
        )
        assert "rule: latency_ratio clearly above 1.00 (paired sign test)" in (
            result.stop
        )
        drives = [p for p in ctx.kiln.run_posts if p["task_run_config_id"] != "rc_base"]
        assert len(drives) == 15
        assert not [j for j in ctx.kiln.job_invocations if j[1] == "rc_probe_1"]
        dead = [m for m in ctx.kiln.memories.values() if "dead_end" in m["tags"]]
        assert dead and dead[0]["overview"].startswith("SCREEN DEAD END: ")

    async def test_under_cost_the_screen_is_cheaper_not_faster(self, tmp_path):
        ctx = _ctx(tmp_path, objective="cost", latency={"rc_probe_1": 40000.0})
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        # Slower, but 0.004 vs 0.075 per request: a cost survivor.
        assert result.verdict == "survivor", result.reason
        assert result.cost is not None and result.cost.median_ratio == pytest.approx(
            0.004 / 0.075
        )
        assert "cost 0.05× (20/20 cheaper)" in result.line

    async def test_under_quality_only_the_hard_cases_and_validity_decide(
        self, tmp_path
    ):
        ctx = _ctx(tmp_path, objective="quality", latency={"rc_probe_1": 40000.0})
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "survivor"

    @pytest.mark.parametrize("objective", ["speed", "quality"])
    async def test_invalid_output_is_a_dead_end(self, tmp_path, objective):
        ctx = _ctx(tmp_path, objective=objective, output="")
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "dead_end" and "invalid output" in result.reason
        # never judged
        assert not [j for j in ctx.kiln.job_invocations if j[1] == "rc_probe_1"]

    async def test_clearly_worse_quality_is_a_dead_end(self, tmp_path):
        ctx = _ctx(tmp_path)
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 0.0 for e in EVALS for n in range(10)})
        (result,) = await run_screening_round(
            ctx, _request(GEMINI, per_eval_items=6), "rc_base", 1
        )
        assert result.verdict == "dead_end"
        # Under speed the quality rule counts losses on baseline passes.
        assert "rule: guard_losses" in result.reason
        assert result.net is not None and result.net < 0

    async def test_a_policy_refusal_and_an_unoffered_level_never_run(self, tmp_path):
        ctx = _ctx(tmp_path)
        ctx.model_policy = ModelPolicy.from_json(
            {"allow": [{"provider": "gemini_api", "model": "*"}]}
        )
        bad_model = ScreenCandidate(
            id="s1-3",
            overrides={"model_name": "kimi_k3", "model_provider_name": "groq"},
        )
        bad_level = ScreenCandidate(
            id="s1-4",
            overrides={
                "model_name": "gemini_3_8_flash",
                "model_provider_name": "gemini_api",
                "thinking_level": "minimal",
            },
        )
        results = await run_screening_round(
            ctx, _request(bad_model, bad_level), "rc_base", 1
        )
        assert [r.verdict for r in results] == ["dead_end", "dead_end"]
        assert "outside the model policy" in results[0].reason
        assert "not offered" in results[1].reason
        assert not ctx.kiln.created_run_configs and not ctx.kiln.run_posts

    async def test_a_label_kiln_would_refuse_still_makes_a_run_config(self, tmp_path):
        # Run 260924-2010: every label looked like this one, and Kiln refused
        # all nine names. The candidate must be made and measured.
        ctx = _ctx(tmp_path)
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        labelled = GEMINI.model_copy(
            update={"label": "gemini 3.8 flash @ gemini_api, thinking low"}
        )
        (result,) = await run_screening_round(ctx, _request(labelled), "rc_base", 1)
        assert result.verdict != "error", result.reason
        (made,) = ctx.kiln.created_run_configs
        assert made["name"].startswith("SCREEN s1-1 - gemini 3")
        assert "." not in made["name"] and "," not in made["name"]
        # Stamped as the loop's own object, in the lineage under its parent.
        assert made["provenance"] == {
            "notes": made["description"],
            "derived_from_ids": ["rc_base"],
            "origin": "agent",
        }

    async def test_a_refused_create_is_an_error_not_a_dead_end(self, tmp_path):
        # A run config Kiln would not make says nothing about the model: it is
        # not a dead end, and no dead-end record may keep the planner from it.
        ctx = _ctx(tmp_path)
        ctx.kiln.fail_run_config_create = True
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "error"
        assert "not screened" in result.line and "run config refused" in result.reason
        assert not any("SCREEN DEAD END" in str(post) for post in ctx.kiln.memory_posts)
        assert not ctx.kiln.run_posts

    async def test_a_model_without_thinking_levels_is_screened_not_dead(self, tmp_path):
        # Run 260924-2032: GLM, DeepSeek and Qwen offer no levels; the planner
        # sent thinking_level None ("leave it unset") or a level, and all five
        # were recorded as dead ends without a drive.
        for override in ({"thinking_level": None}, {"thinking_level": "low"}, {}):
            ctx = _ctx(tmp_path / str(len(override) and override["thinking_level"]))
            _judge(
                ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)}
            )
            glm = ScreenCandidate(
                id="s1-7",
                overrides={
                    "model_name": "glm_5_3",
                    "model_provider_name": "together_ai",
                    **override,
                },
            )
            (result,) = await run_screening_round(ctx, _request(glm), "rc_base", 1)
            assert result.verdict != "error", (override, result.reason)
            assert "not offered" not in (result.reason or ""), override
            (made,) = ctx.kiln.created_run_configs
            assert "thinking_level" not in made["run_config_properties"], override

    async def test_an_unmentioned_level_keeps_the_parents_when_offered(self, tmp_path):
        ctx = _ctx(tmp_path)
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        swap = ScreenCandidate(
            id="s1-2",
            overrides={
                "model_name": "gemini_3_8_flash",
                "model_provider_name": "gemini_api",
            },
        )
        # The parent (Sonnet) is at "medium", which Gemini does not offer.
        (result,) = await run_screening_round(ctx, _request(swap), "rc_base", 1)
        assert result.verdict != "error", result.reason
        assert "not offered" not in (result.reason or "")
        (made,) = ctx.kiln.created_run_configs
        assert "thinking_level" not in made["run_config_properties"]
        # A parent level the new model does offer is kept.
        ctx2 = _ctx(tmp_path / "kept")
        ctx2.kiln.run_configs = [_rc("rc_base", thinking="low")]
        await run_screening_round(ctx2, _request(swap), "rc_base", 1)
        (made2,) = ctx2.kiln.created_run_configs
        assert made2["run_config_properties"]["thinking_level"] == "low"

    async def test_a_targeted_round_never_drives_or_judges_other_evals(self, tmp_path):
        ctx = _ctx(tmp_path)
        _judge(ctx, "rc_probe_1", {f"e2_{n}": 1.0 for n in range(10)})
        (result,) = await run_screening_round(
            ctx, _request(GEMINI, target_evals=["e2"]), "rc_base", 1
        )
        assert {e for e, _rc, _s in ctx.kiln.job_invocations} == {"e2"}
        assert result.evals == ["e2"] and set(result.per_eval) == {"e2"}
        assert "evals: e2" in result.line
        # Screened on e2 only: never read as screened for an arm.
        assert "rc_probe_1" not in ctx.screened_rcs

    async def test_a_survivor_screened_on_every_eval_is_noted(self, tmp_path):
        ctx = _ctx(tmp_path)
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "survivor"
        assert ctx.screened_rcs == {"rc_probe_1": 1}
        # Ranked, with its net change and interval.
        assert result.rank == 1
        assert result.net_ci_low <= result.net <= result.net_ci_high


ALL_PASS = {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)}
THINKING = ScreenCandidate(id="s1-2", overrides={"thinking_level": "low"})


class TestLossesUnderSpeedAndCost:
    """Under speed and cost a screen counts losses on the
    baseline's passes, reports fixes apart, and judges a model or provider
    swap on more baseline passes."""

    async def test_the_survivor_line_reads_losses_and_a_bound(self, tmp_path):
        ctx = _ctx(tmp_path)
        _judge(ctx, "rc_probe_1", ALL_PASS)
        (result,) = await run_screening_round(ctx, _request(THINKING), "rc_base", 1)
        assert result.verdict == "survivor"
        # A thinking-level screen keeps the round's size, all guards.
        assert result.items == 6 and result.baseline_passed == 6
        assert result.guards_required is None
        assert result.loss_bound == pytest.approx(0.393, abs=0.001)
        assert (
            "6 judged, lost 0 of 6 baseline passes (no loss rate above 39% at 95%)"
            " · fixed 0 of 0 baseline failures (noise level)"
        ) in result.line
        assert " net " not in result.line
        (drawn,) = [f for k, f in ctx.log.events if k == "queue_drawn"]
        assert "fragility" in drawn and "per_eval_caps" not in drawn

    async def test_a_model_swap_judged_short_of_its_minimum_is_capped(self, tmp_path):
        ctx = _ctx(tmp_path)
        _judge(ctx, "rc_probe_1", ALL_PASS)
        request = _request(GEMINI, stop_rules=[StopRule(kind="max_items", threshold=6)])
        (result,) = await run_screening_round(ctx, request, "rc_base", 1)
        # A thinking screen at its `max_items` is a survivor; a model swap
        # judged on 6 of the 12 baseline passes it needs is not.
        assert result.verdict == "capped"
        assert result.guards_required == 12
        assert "6 of the 12 baseline passes a model or provider swap needs" in (
            result.reason
        )

    async def test_the_swap_minimum_is_priced_in_its_budget(self, tmp_path):
        ctx = _ctx(tmp_path)
        for e in EVALS:
            ctx.kiln.results[(e, "rc_base", "train")] = [
                {**r, "task_run_usage": {"cost": 0.05}, "eval_usage": {"cost": 0.05}}
                for r in ctx.kiln.results[(e, "rc_base", "train")]
            ]
        for rc in ("rc_probe_1", "rc_probe_2"):
            _judge(ctx, rc, ALL_PASS)
        results = await run_screening_round(
            ctx, _request(GEMINI, THINKING), "rc_base", 1
        )
        swap, thinking = (
            next(
                e
                for k, e in ctx.log.events
                if k == "candidate_budget" and e["rc_id"] == rc
            )
            for rc in ("rc_probe_1", "rc_probe_2")
        )
        # Priced from the baseline's rows before the first chunk: 18 items
        # against 6, the same timing pairs.
        assert swap["usd"] > thinking["usd"]
        assert {r.candidate_id: r.items for r in results} == {"s1-1": 18, "s1-2": 6}
        (drawn,) = [f for k, f in ctx.log.events if k == "queue_drawn"]
        assert drawn["per_eval_caps"] == {"rc_probe_1": 9, "rc_probe_2": 3}
        jobs = [
            (c[3]["run_config_id"], len(c[3].get("item_ids", ())))
            for c in ctx.kiln.calls
            if c[1] == "/api/jobs/evals/run" and c[3].get("item_ids")
        ]
        judged = {rc: sum(n for r, n in jobs if r == rc) for rc, _n in jobs}
        assert judged == {"rc_probe_1": 18, "rc_probe_2": 6}

    async def test_under_quality_nothing_changes(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        for rc in ("rc_probe_1", "rc_probe_2"):
            _judge(ctx, rc, ALL_PASS)
        results = await run_screening_round(
            ctx, _request(GEMINI, THINKING), "rc_base", 1
        )
        for r in results:
            # The same items for a model swap and a thinking level, the net
            # line, no loss bound.
            assert r.items == 2 * SCREEN_ROUND1_PER_EVAL
            assert r.loss_bound is None and r.guards_required is None
            assert " vs baseline · net " in r.line
        (drawn,) = [f for k, f in ctx.log.events if k == "queue_drawn"]
        assert "fragility" not in drawn and "per_eval_caps" not in drawn
        # The baseline's failures lead the quality queue.
        assert drawn["queue"][0][2] == "fail"


class TestTheOpeningRound:
    def test_every_lower_offered_level(self):
        request = initial_screen_request(
            {"thinking_level": "medium"}, ["none", "low", "medium", "high"]
        )
        assert [c.overrides["thinking_level"] for c in request.candidates] == [
            "low",
            "none",
        ]
        assert (
            initial_screen_request({"thinking_level": "low"}, ["low", "high"]) is None
        )
        assert initial_screen_request({"thinking_level": "medium"}, None) is None

    async def test_speed_and_cost_open_with_it_quality_does_not(self, tmp_path):
        for objective, expected in (("speed", 2), ("cost", 2), ("quality", 0)):
            ctx = _ctx(tmp_path / objective, objective=objective)
            await run_opening_screen(ctx, BaselineRef(rc_id="rc_base"))
            made = [rc["run_config_properties"] for rc in ctx.kiln.created_run_configs]
            assert len(made) == expected, objective
            assert all(p["model_name"] == "claude_sonnet_4_6" for p in made)

    async def test_no_opening_round_when_the_policy_denies_the_baseline_model(
        self, tmp_path
    ):
        ctx = _ctx(tmp_path)
        ctx.model_policy = ModelPolicy.from_json(
            {"allow": [{"provider": "gemini_api", "model": "*"}]}
        )
        await run_opening_screen(ctx, BaselineRef(rc_id="rc_base"))
        assert not ctx.kiln.created_run_configs and not ctx.screen_results
        assert not any("SCREEN DEAD END" in str(post) for post in ctx.kiln.memory_posts)


async def test_the_planner_screens_then_commits(tmp_path):
    """A screening plan runs a round in code, then the planner is asked
    again with the table (`rescreen`), and only then commits arms."""
    ctx = _ctx(tmp_path, objective="quality")
    ctx.gate_agent = "auto"
    _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
    screen_plan = make_plan().model_copy(
        update={"items": [], "screen": _request(GEMINI)}
    )
    ctx.dispatch.queue("batch_plan", ok(screen_plan), ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    calls = ctx.dispatch.calls_to("batch_plan")
    assert [c.phase for c in calls] == ["plan", "rescreen"]
    assert calls[1].screening_results[0].candidate_id == "s1-1"
    assert calls[1].screen_rounds_left == 3


def test_a_screening_plan_carries_no_items():
    from kiln_harness_tuner.schemas import BatchPlan

    with pytest.raises(ValueError, match="screening plan"):
        BatchPlan.model_validate(
            {
                "items": [
                    {
                        "id": "w1-1",
                        "type": "measure_issue_baseline",
                        "issue_memory_id": "i",
                        "eval_id": "e",
                        "reason": "r",
                    }
                ],
                "rejected_summary": "",
                "open_questions": [],
                "screen": {"candidates": [{"id": "s1-1"}]},
            }
        )
