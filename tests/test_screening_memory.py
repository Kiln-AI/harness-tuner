"""Screening results survive a run. Every screened candidate
is a memory record; a run starts from them — the planner's table, marked as
prior-run results, and `screened_rcs` restored — and a survivor that held
quality over two screening rounds is committed, not screened again."""

from __future__ import annotations

from conftest import make_plan
from fake_dispatcher import ok
from test_viability import EVALS, GEMINI, _ctx, _judge, _rc, _request

from kiln_harness_tuner.memory import parse_screen_record, screen_record_pairs
from kiln_harness_tuner.memory_schema import SCREEN_TAG, MemoryRecord, kv_value
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.schemas import ScreenCandidate, ScreenResult
from kiln_harness_tuner.viability import (
    drop_settled_rescreens,
    load_prior_screening,
    run_screening_round,
    settled_survivors,
)

ALL = {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)}


def _screen_records(ctx) -> list[dict]:
    return [m for m in ctx.kiln.memories.values() if SCREEN_TAG in m["tags"]]


def _result(
    rc_id="rc_prior",
    *,
    run_id="260923-1705",
    round_no=1,
    verdict="survivor",
    passed=6,
    baseline=6,
    evals=EVALS,
    overrides=None,
):
    return ScreenResult(
        candidate_id="s1-1",
        label="gemini_3_8_flash@gemini_api (thinking low)",
        rc_id=rc_id,
        round=1,
        evals=list(evals),
        items=6,
        per_eval={e: {"n": 3} for e in evals},
        passed=passed,
        baseline_passed=baseline,
        verdict=verdict,
        reason="" if verdict == "survivor" else "stopped at n=3: not faster",
        line=f"screen s1-1 gemini: speed median 34.0 s→3.0 s → {verdict}",
        overrides=GEMINI.overrides if overrides is None else overrides,
        round_no=round_no,
        run_id=run_id,
        date="2026-09-23",
        baseline_rc_id="rc_base",
    )


class TestTheRecord:
    async def test_every_screened_result_is_recorded(self, tmp_path):
        ctx = _ctx(tmp_path, latency={"rc_probe_2": 40000.0})
        ctx.run_dir = tmp_path / "260924-2248"
        ctx.run_dir.mkdir()
        _judge(ctx, "rc_probe_1", ALL)
        slow = ScreenCandidate(id="s1-2", overrides={"thinking_level": "high"})
        await run_screening_round(ctx, _request(GEMINI, slow), "rc_base", 1)
        records = {m["overview"].split(":")[0]: m for m in _screen_records(ctx)}
        survivor = records["SCREEN SURVIVOR"]
        assert survivor["tags"] == ["lesson", "screen"]
        # Under speed: the losses on the baseline's passes and their bound.
        # The model swap is judged on all 12 baseline passes.
        assert survivor["overview"] == (
            "SCREEN SURVIVOR: gemini_3_8_flash@gemini_api (thinking low) — lost 0"
            " of 12 baseline passes, no loss rate above 22% at 95%"
        )
        content = survivor["content"]
        for key, value in (
            ("run config", "rc_probe_1"),
            ("round", "1"),
            ("round no", "1"),
            ("items", "18"),
            ("screened on evals", "e1, e2"),
            ("verdict", "survivor"),
            ("run", "260924-2248"),
            ("date", "2026-07-22"),
            ("baseline rc", "rc_base"),
        ):
            assert kv_value(content, key) == value, key
        assert '"model_name": "gemini_3_8_flash"' in kv_value(content, "overrides")
        assert kv_value(content, "measured").startswith("screen s1-1 gemini_3_8_flash")
        assert kv_value(content, "quality") == (
            "lost 0 of 12 baseline passes, no loss rate above 22% at 95%; fixed 6"
            " baseline failures (noise level, not set against the losses)"
        )
        dead = records["SCREEN DEAD END"]
        assert dead["tags"] == ["dead_end", "screen"]
        assert kv_value(dead["content"], "verdict") == "dead_end"

    async def test_an_error_row_is_not_a_result(self, tmp_path):
        ctx = _ctx(tmp_path)
        ctx.kiln.fail_run_config_create = True
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "error" and _screen_records(ctx) == []
        # The writer refuses one too, whoever calls it.
        assert await ctx.memory.record_screen_result(result) is None
        assert _screen_records(ctx) == []

    def test_a_record_reads_back_as_a_prior_result(self):
        result = _result()
        content = "\n".join(f"{k}: {v}" for k, v in screen_record_pairs(result))
        back = parse_screen_record(
            MemoryRecord("m1", "SCREEN SURVIVOR: x", content, ["lesson"], "t")
        )
        assert back.prior_run and back.line == result.line
        assert back.model_dump(exclude={"prior_run"}) == result.model_dump(
            exclude={"prior_run"}
        )

    def test_a_dead_end_from_before_the_change_still_reads(self):
        content = (
            "candidate: s1-1\nrun config: rc_old\nround: 2\n"
            "screened on evals: e1, e2\nreason: not faster\nmeasured: screen s1-1 …"
        )
        back = parse_screen_record(
            MemoryRecord(
                "m1",
                "SCREEN DEAD END: glm_5_3@together_ai — not faster",
                content,
                ["dead_end", "screen"],
                "t",
            )
        )
        assert back.verdict == "dead_end" and back.rc_id == "rc_old"
        assert back.label == "glm_5_3@together_ai" and back.round == 2
        assert back.evals == ["e1", "e2"] and back.prior_run


class TestTheNextRun:
    async def _seed(self, ctx, *results):
        for r in results:
            await ctx.memory.record_screen_result(r)

    async def test_a_survivor_on_every_eval_skips_viability_again(self, tmp_path):
        ctx = _ctx(tmp_path)
        ctx.kiln.run_configs.append(_rc("rc_prior"))
        await self._seed(ctx, _result())
        await load_prior_screening(ctx)
        assert ctx.screened_rcs == {"rc_prior": 1}
        assert ctx.prior_screen_results[0].prior_run
        (line,) = [f["line"] for k, f in ctx.log.events if k == "prior_screening"]
        assert line.startswith("prior screening: 1 result(s) from 1 earlier run(s)")
        assert "rc_prior, run 260923-1705, 2026-09-23" in line
        from kiln_harness_tuner.ui import SETUP_LINE_EVENTS

        assert {"prior_screening", "screen_candidate_dropped"} <= SETUP_LINE_EVENTS

    async def test_what_is_not_restored(self, tmp_path):
        ctx = _ctx(tmp_path)
        ctx.kiln.run_configs += [_rc("rc_one"), _rc("rc_dead")]
        await self._seed(
            ctx,
            _result("rc_gone"),  # the run config no longer exists
            _result("rc_one", evals=["e1"]),  # not screened on every eval
            _result("rc_dead", verdict="dead_end"),
        )
        await load_prior_screening(ctx)
        assert ctx.screened_rcs == {}
        assert len(ctx.prior_screen_results) == 3

    async def test_the_planner_sees_them_marked_newest_first(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality")
        ctx.gate_agent = "auto"
        await self._seed(
            ctx,
            _result("rc_a", run_id="260922-0900"),
            _result("rc_b", run_id="260923-1705"),
        )
        ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
        await orchestrate(ctx, "g", timeout_mins=180)
        (call,) = ctx.dispatch.calls_to("batch_plan")
        prior = call.prior_screening_results
        assert [r.rc_id for r in prior] == ["rc_b", "rc_a"]
        assert all(r.prior_run and r.date == "2026-09-23" for r in prior)
        assert call.screening_results == []  # this run's table is separate


class TestSettledSurvivors:
    def test_two_quality_neutral_rounds_settle_it(self):
        one = _result(run_id="260923-1705", round_no=1)
        two = _result(rc_id="rc_new", run_id="260924-0900", round_no=3)
        (settled,) = settled_survivors([one, two])
        assert settled.rounds == 2 and settled.rc_id == "rc_new"
        assert settled.rc_ids == ["rc_new", "rc_prior"]
        assert settled_survivors([one]) == []

    def test_a_rescreen_by_run_config_counts_for_its_candidate(self):
        first = _result(round_no=1)
        again = _result(round_no=2, overrides={})  # re-screened by rc_id
        assert settled_survivors([first, again])[0].rounds == 2

    def test_a_round_that_lost_quality_does_not_count(self):
        worse = _result(round_no=2, passed=4, baseline=6)
        assert settled_survivors([_result(round_no=1), worse]) == []
        dead = _result(round_no=2, verdict="dead_end")
        assert settled_survivors([_result(round_no=1), dead]) == []

    def test_a_rescreen_without_a_reason_is_dropped(self):
        settled = settled_survivors([_result(round_no=1), _result(round_no=2)])
        by_rc = ScreenCandidate(id="s4-1", rc_id="rc_prior")
        by_overrides = ScreenCandidate(id="s4-2", overrides=GEMINI.overrides)
        other = ScreenCandidate(id="s4-3", overrides={"thinking_level": "high"})
        request, dropped = drop_settled_rescreens(
            _request(by_rc, by_overrides, other), settled
        )
        assert [c.id for c in request.candidates] == ["s4-3"]
        assert len(dropped) == 2
        assert dropped[0].startswith("screen s4-1 gemini_3_8_flash@gemini_api")
        assert "work.existing_rc_id: rc_prior" in dropped[0]
        with_reason = by_rc.model_copy(update={"rescreen_reason": "a larger round"})
        request, dropped = drop_settled_rescreens(_request(with_reason), settled)
        assert request is not None and dropped == []
        assert drop_settled_rescreens(_request(by_rc), settled)[0] is None

    async def test_the_loop_does_not_rescreen_a_settled_survivor(self, tmp_path):
        """A survivor that held quality over two rounds is not screened again."""
        ctx = _ctx(tmp_path, objective="quality")
        ctx.gate_agent = "auto"
        ctx.kiln.run_configs.append(_rc("rc_prior"))
        for n in (1, 2):
            await ctx.memory.record_screen_result(_result(round_no=n))
        rescreen = make_plan().model_copy(
            update={
                "items": [],
                "screen": _request(ScreenCandidate(id="s1-1", rc_id="rc_prior")),
            }
        )
        ctx.dispatch.queue("batch_plan", ok(rescreen), ok(make_plan(loop_exit="done")))
        await orchestrate(ctx, "g", timeout_mins=180)
        assert not [k for k, _f in ctx.log.events if k == "screening_round"]
        calls = ctx.dispatch.calls_to("batch_plan")
        assert [c.phase for c in calls] == ["plan", "rescreen"]
        assert calls[0].settled_survivors[0].rc_id == "rc_prior"
        assert calls[1].screening_notes[0].startswith("screen s1-1 gemini_3_8_flash")
