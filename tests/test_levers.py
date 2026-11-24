"""The declared lever must take effect before a candidate is paid for
further: thinking set above none shows reasoning; the model and
provider that served the drives are the ones the run config names."""

from __future__ import annotations

from test_viability import EVALS, GEMINI, _ctx, _judge, _request

from kiln_harness_tuner.levers import lever_evidence, lever_not_applied, lever_verdict
from kiln_harness_tuner.measure import TIMING_CHUNK
from kiln_harness_tuner.schemas import ScreenCandidate
from kiln_harness_tuner.viability import run_screening_round


class TestTheEvidence:
    def test_reasoning_from_the_trace_the_store_or_the_usage(self):
        assert lever_evidence(
            {"trace": [{"role": "assistant", "reasoning_content": "hmm"}]}
        )["reasoning"]
        assert lever_evidence({"intermediate_outputs": {"reasoning": "x"}})["reasoning"]
        assert lever_evidence({"usage": {"reasoning_tokens": 5}})["reasoning"]
        assert lever_evidence(
            {"usage": {"completion_tokens_details": {"reasoning_tokens": 5}}}
        )["reasoning"]
        assert not lever_evidence({"usage": {"output_tokens": 50}})["reasoning"]

    def test_the_served_model_from_the_source(self):
        e = lever_evidence(
            {
                "output": {
                    "source": {"properties": {"model_name": "m", "model_provider": "p"}}
                }
            }
        )
        assert (e["model"], e["provider"]) == ("m", "p")
        assert lever_evidence({})["model"] is None


def _ev(n: int, **fields) -> dict:
    return {f"i{k}": dict(fields) for k in range(n)}


class TestTheRule:
    HIGH = {"thinking_level": "high"}
    LOW = {"thinking_level": "low"}

    def test_reasoning_shown_is_applied(self):
        v = lever_verdict(self.HIGH, _ev(3, reasoning=True), self.LOW)
        assert v.status == "applied"

    def test_hidden_reasoning_with_tokens_that_rose_is_applied(self):
        v = lever_verdict(
            self.HIGH,
            _ev(3, reasoning=False, output_tokens=900.0),
            self.LOW,
            _ev(3, output_tokens=300.0),
        )
        assert v.status == "applied"

    def test_no_reasoning_and_flat_tokens_is_not_applied(self):
        v = lever_verdict(
            self.HIGH,
            _ev(3, reasoning=False, output_tokens=310.0),
            self.LOW,
            _ev(3, output_tokens=300.0),
        )
        assert v.status == "not_applied"
        assert v.reason == (
            "lever not applied: thinking_level=high is set, but 0 of 3 drives"
            " show reasoning and output tokens are 1.03x the parent's on the"
            " same 3 items"
        )
        assert lever_not_applied(
            self.HIGH,
            _ev(3, output_tokens=310.0),
            self.LOW,
            _ev(3, output_tokens=300.0),
        )

    def test_neither_signal_is_unverified_not_stopped(self):
        v = lever_verdict(self.HIGH, _ev(3, reasoning=False), self.LOW, _ev(3))
        assert v.status == "unverified"
        assert v.reason.startswith("lever unverified: thinking_level=high")
        assert lever_not_applied(self.HIGH, _ev(3), self.LOW, _ev(3)) is None

    def test_tokens_on_one_item_only_are_not_a_signal(self):
        cand = _ev(3, output_tokens=None) | {"i0": {"output_tokens": 300.0}}
        parent = _ev(3, output_tokens=300.0)
        assert lever_verdict(self.HIGH, cand, self.LOW, parent).status == "unverified"

    def test_an_undeclared_or_zero_level_needs_nothing(self):
        assert lever_verdict(self.HIGH, _ev(3), self.HIGH, _ev(3)).status == "applied"
        none = {"thinking_level": "none"}
        assert lever_verdict(none, _ev(3), self.LOW, _ev(3)).status == "applied"

    def test_another_served_model_is_not_applied(self):
        props = {"model_name": "flash", "model_provider_name": "gemini_api"}
        served = _ev(2, model="pro", provider="gemini_api")
        v = lever_verdict(props, served, {"model_name": "x"})
        assert v.status == "not_applied" and "served by pro@gemini_api" in v.reason

    def test_an_unrecorded_model_switch_is_unverified(self):
        props = {"model_name": "flash", "model_provider_name": "gemini_api"}
        parent = {"model_name": "x", "model_provider_name": "openai"}
        v = lever_verdict(props, _ev(2), parent)
        assert v.status == "unverified" and "no drive records" in v.reason

    def test_the_model_is_read_from_the_usage_or_the_trace(self):
        assert lever_evidence({"usage": {"model_name": "m"}})["model"] == "m"
        run = {"trace": [{"role": "assistant", "model": "m2", "provider": "p"}]}
        e = lever_evidence(run)
        assert (e["model"], e["provider"]) == ("m2", "p")
        assert lever_evidence({"usage": {"output_tokens": 12}})["output_tokens"] == 12


class TestTheOutputMode:
    """The mode is checked when it differs from the parent's
    (`output_modes.mode_signal` gives the signal)."""

    PARENT = {"structured_output_mode": "json_schema"}

    def test_the_same_mode_as_the_parent_is_not_checked(self):
        v = lever_verdict(self.PARENT, _ev(2, mode_recorded="json_mode"), self.PARENT)
        assert v.status == "applied" and v.mode is None
        # No parent: nothing was declared.
        assert lever_verdict(self.PARENT, _ev(2)).mode is None

    def test_a_mark_in_the_trace_is_applied(self):
        props = {"structured_output_mode": "function_calling"}
        v = lever_verdict(props, _ev(3, response_tool=True), self.PARENT)
        assert v.status == "applied"
        assert v.mode is not None and v.mode.proof == "trace"

    def test_kilns_record_alone_is_applied_and_says_so(self):
        props = {"structured_output_mode": "json_mode"}
        v = lever_verdict(props, _ev(3, mode_recorded="json_mode"), self.PARENT)
        assert v.status == "applied"
        assert v.mode is not None and v.mode.proof == "record"

    def test_another_recorded_mode_is_not_applied(self):
        props = {"structured_output_mode": "json_mode"}
        v = lever_verdict(props, _ev(3, mode_recorded="json_schema"), self.PARENT)
        assert v.status == "not_applied"
        assert v.reason.startswith("lever not applied: the run config names")
        assert lever_not_applied(
            props, _ev(3, mode_recorded="json_schema"), self.PARENT
        )

    def test_no_mark_and_no_record_is_unverified(self):
        props = {"structured_output_mode": "json_instructions"}
        v = lever_verdict(props, _ev(3), self.PARENT)
        assert v.status == "unverified"
        assert "lever unverified: structured_output_mode=json_instructions" in v.reason

    def test_a_task_with_no_output_schema_is_unverified(self):
        """Kiln does not use the mode there, so a record of it proves nothing."""
        props = {"structured_output_mode": "json_mode"}
        v = lever_verdict(
            props, _ev(3, mode_recorded="json_mode"), self.PARENT, structured=False
        )
        assert v.status == "unverified" and "no output schema" in v.reason

    def test_the_run_gives_the_evidence(self):
        e = lever_evidence(
            {
                "trace": [
                    {
                        "role": "assistant",
                        "tool_calls": [{"function": {"name": "task_response"}}],
                    }
                ],
                "output": {
                    "source": {
                        "properties": {"structured_output_mode": "function_calling"}
                    }
                },
            }
        )
        assert e["response_tool"] and e["mode_recorded"] == "function_calling"


class TestInARound:
    async def test_a_swap_takes_the_new_pairs_mode_and_the_signal_is_logged(
        self, tmp_path
    ):
        """The candidate names no mode: code sets the default of the new
        pair, and the lever check logs what the drives show about it."""
        ctx = _ctx(tmp_path, objective="quality")
        ctx.kiln.task = {"id": "t1", "output_json_schema": '{"type": "object"}'}
        ctx.kiln.run_configs[0]["run_config_properties"]["structured_output_mode"] = (
            "json_schema"
        )
        ctx.kiln.available_models[1]["models"][0]["structured_output_mode"] = (
            "json_instructions"
        )
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "survivor", result.reason
        (made,) = ctx.kiln.created_run_configs
        assert made["run_config_properties"]["structured_output_mode"] == (
            "json_instructions"
        )
        events = [f for k, f in ctx.log.events if k == "output_mode"]
        assert [e["status"] for e in events] == ["set_on_swap", "unverified"]
        assert events[0]["parent_mode"] == "json_schema"
        assert "Kiln's default for the pair" in events[0]["line"]
        # The fake's drives record no mode and no format block.
        assert events[1]["proof"] == "none" and events[1]["rc_id"] == "rc_probe_1"

    async def test_no_reasoning_and_flat_tokens_stop_before_judging(self, tmp_path):
        ctx = _ctx(
            tmp_path, objective="quality", no_reasoning=True, output_tokens={"*": 300}
        )
        high = ScreenCandidate(id="s1-7", overrides={"thinking_level": "high"})
        (result,) = await run_screening_round(ctx, _request(high), "rc_base", 1)
        assert result.verdict == "dead_end"
        assert result.reason.startswith("lever not applied: thinking_level=high")
        # Never judged (the only jobs are the queue parent's stored rows).
        assert not [j for j in ctx.kiln.job_invocations if j[1] != "rc_base"]
        drives = [p for p in ctx.kiln.run_posts if p["task_run_config_id"] != "rc_base"]
        # The first timing chunk only.
        assert 0 < len(drives) <= TIMING_CHUNK
        (event,) = [f for k, f in ctx.log.events if k == "lever_not_applied"]
        assert event["line"].endswith("— stopped")

    async def test_hidden_reasoning_with_more_tokens_goes_on(self, tmp_path):
        ctx = _ctx(
            tmp_path,
            objective="quality",
            no_reasoning=True,
            output_tokens={"rc_base": 300, "*": 900},
        )
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        high = ScreenCandidate(id="s1-7", overrides={"thinking_level": "high"})
        (result,) = await run_screening_round(ctx, _request(high), "rc_base", 1)
        assert result.verdict == "survivor", result.reason

    async def test_no_signal_at_all_is_logged_and_goes_on(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality", no_reasoning=True)
        _judge(ctx, "rc_probe_1", {f"{e}_{n}": 1.0 for e in EVALS for n in range(10)})
        high = ScreenCandidate(id="s1-7", overrides={"thinking_level": "high"})
        (result,) = await run_screening_round(ctx, _request(high), "rc_base", 1)
        assert result.verdict == "survivor"
        (event,) = [f for k, f in ctx.log.events if k == "lever_unverified"]
        assert "lever unverified: thinking_level=high" in event["line"]
        assert "lever unverified" in result.line

    async def test_a_model_served_by_another_is_stopped(self, tmp_path):
        ctx = _ctx(tmp_path, objective="quality", served_model="gemini_3_8_pro")
        (result,) = await run_screening_round(ctx, _request(GEMINI), "rc_base", 1)
        assert result.verdict == "dead_end"
        assert "served by gemini_3_8_pro@gemini_api" in result.reason
