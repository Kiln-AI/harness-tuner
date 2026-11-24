"""The output-mode rules that code applies: the mode of a model swap, the
mode that Kiln refuses, and the signal that a mode reached the call."""

from __future__ import annotations

import pytest

from kiln_harness_tuner.output_modes import (
    FORMAT_BLOCK,
    MODES,
    TOOLS_FALLBACK,
    mode_evidence,
    mode_signal,
    refusal,
    server_default,
    swap_mode,
)

TOOLS = {"tools_config": {"tools": ["kiln_tool::code::t1"]}}
SKILL = {"tools_config": {"tools": ["kiln_tool::skill::s1"]}}


class TestTheModeOfASwap:
    def test_the_new_pair_takes_its_default(self):
        choice = swap_mode("json_schema", "json_instructions", provider="p", tools=0)
        assert choice.mode == "json_instructions"
        assert "default for the pair" in choice.why

    @pytest.mark.parametrize("default", [None, "", "default", "unknown"])
    def test_no_usable_default_keeps_the_parents_mode(self, default):
        choice = swap_mode("json_schema", default, provider="p", tools=False)
        assert choice.mode == "json_schema" and "parent's mode" in choice.why

    @pytest.mark.parametrize("default", ["function_calling", "function_calling_weak"])
    def test_a_tool_mode_default_with_tools_takes_the_fallback(self, default):
        choice = swap_mode("json_schema", default, provider="openai", tools=True)
        assert choice.mode == TOOLS_FALLBACK and default in choice.why
        # With no tools on the run config, the default stands.
        assert swap_mode("json_schema", default, provider="x", tools=False).mode == (
            default
        )

    def test_a_kept_legacy_default_that_is_a_tool_on_the_new_provider(self):
        """`default` is json_schema on Ollama and a tool elsewhere."""
        moved = swap_mode("default", None, provider="openai", tools=True)
        assert moved.mode == TOOLS_FALLBACK
        stays = swap_mode("default", None, provider="ollama", tools=True)
        assert stays.mode == "default"

    def test_no_parent_mode_and_no_default_is_no_mode(self):
        assert swap_mode(None, None, provider="p", tools=False).mode is None


class TestTheRefusal:
    def test_a_value_that_is_not_a_mode(self):
        why = refusal({"structured_output_mode": "json"}, structured=True)
        assert why is not None and "not a mode of Kiln" in why
        assert all(mode in why for mode in MODES)

    @pytest.mark.parametrize("mode", ["function_calling", "function_calling_weak"])
    @pytest.mark.parametrize("attached", [TOOLS, SKILL])
    def test_a_tool_mode_with_a_tool_or_a_skill(self, mode, attached):
        props = {"structured_output_mode": mode, **attached}
        why = refusal(props, structured=True)
        assert why is not None and "sends the output schema as a tool" in why
        # No schema, or a task that could not be read: nothing is refused.
        assert refusal(props, structured=False) is None
        assert refusal(props, structured=None) is None
        # No tools: Kiln accepts the mode.
        assert refusal({"structured_output_mode": mode}, structured=True) is None

    def test_default_is_a_tool_outside_ollama_and_docker(self):
        props = {"structured_output_mode": "default", **TOOLS}
        assert refusal({**props, "model_provider_name": "openai"}, structured=True)
        for provider in ("ollama", "docker_model_runner"):
            assert (
                refusal({**props, "model_provider_name": provider}, structured=True)
                is None
            )

    def test_unknown_is_read_as_the_default_of_the_pair(self):
        props = {"structured_output_mode": "unknown", **TOOLS}
        why = refusal(props, structured=True, default="function_calling")
        assert why is not None and "unknown (here function_calling)" in why
        assert refusal(props, structured=True, default="json_schema") is None

    @pytest.mark.parametrize(
        "mode",
        [
            "json_schema",
            "json_mode",
            "json_instructions",
            "json_instruction_and_object",
            "json_custom_instructions",
        ],
    )
    def test_every_other_mode_runs_with_tools(self, mode):
        assert refusal({"structured_output_mode": mode, **TOOLS}, structured=True) is (
            None
        )


def test_the_default_mode_of_a_pair_in_the_servers_list():
    offered = [
        {
            "provider_id": "p",
            "models": [
                {"id": "m", "structured_output_mode": "json_schema"},
                {"id": "bare"},
            ],
        },
        "not an entry",
    ]
    assert server_default(offered, "p", "m") == "json_schema"
    assert server_default(offered, "p", "bare") is None
    assert server_default(offered, "other", "m") is None
    assert server_default(None, "p", "m") is None


def _run(mode=None, *, system="You answer.", tool=None) -> dict:
    """A task run as Kiln saves it: the mode on `output.source.properties`,
    the system message, and the final assistant message."""
    final: dict = {"role": "assistant", "content": '{"a": 1}'}
    if tool:
        final = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"type": "function", "function": {"name": tool, "arguments": "{}"}}
            ],
        }
    props = {"structured_output_mode": mode} if mode else {}
    return {
        "trace": [
            {"role": "system", "content": system},
            {"role": "user", "content": "u"},
            final,
        ],
        "output": {"output": '{"a": 1}', "source": {"properties": props}},
    }


def _drives(*runs: dict) -> dict:
    return {f"i{n}": mode_evidence(run) for n, run in enumerate(runs)}


class TestTheEvidence:
    def test_what_one_run_records(self):
        e = mode_evidence(_run("function_calling", tool="task_response"))
        assert e["mode_recorded"] == "function_calling" and e["response_tool"]
        assert e["format_block"] is False
        block = f"You answer.\n\n{FORMAT_BLOCK}\n```\n{{}}\n```"
        assert mode_evidence(_run("json_instructions", system=block))["format_block"]
        # Another tool is not the response tool.
        assert not mode_evidence(_run("json_schema", tool="search"))["response_tool"]

    def test_a_run_with_no_trace_and_no_record(self):
        e = mode_evidence({})
        assert e == {
            "mode_recorded": None,
            "response_tool": False,
            "format_block": None,
        }

    def test_the_mode_on_the_runs_own_run_config(self):
        run = {
            "output": {
                "source": {
                    "run_config": {
                        "run_config_properties": {"structured_output_mode": "json_mode"}
                    }
                }
            }
        }
        assert mode_evidence(run)["mode_recorded"] == "json_mode"


class TestTheSignal:
    @pytest.mark.parametrize("mode", ["function_calling", "function_calling_weak"])
    def test_a_tool_mode_shows_in_the_trace(self, mode):
        signal = mode_signal(
            mode, _drives(_run(mode, tool="task_response"), _run(mode))
        )
        assert (signal.status, signal.proof) == ("applied", "trace")
        assert "1 of 2 drives call the task_response tool" in signal.reason
        # No drive calls the tool: the drives cannot tell.
        signal = mode_signal(mode, _drives(_run(mode), _run(mode)))
        assert (signal.status, signal.proof) == ("unverified", "none")

    @pytest.mark.parametrize(
        "mode", ["json_instructions", "json_instruction_and_object"]
    )
    def test_an_instruction_mode_shows_in_the_system_message(self, mode):
        with_block = _run(mode, system=f"Prompt.\n\n{FORMAT_BLOCK}\n```\n{{}}\n```")
        signal = mode_signal(mode, _drives(with_block))
        assert (signal.status, signal.proof) == ("applied", "trace")
        signal = mode_signal(mode, _drives(_run(mode)))
        assert signal.status == "unverified" and "format block" in signal.reason

    @pytest.mark.parametrize(
        "mode", ["json_schema", "json_mode", "json_custom_instructions"]
    )
    def test_a_mode_with_no_mark_stands_on_kilns_record(self, mode):
        signal = mode_signal(mode, _drives(_run(mode), _run(mode)))
        assert (signal.status, signal.proof) == ("applied", "record")
        assert "A trace does not hold the request parameter" in signal.reason
        # No record either.
        signal = mode_signal(mode, _drives(_run(), _run()))
        assert (signal.status, signal.proof) == ("unverified", "none")

    def test_a_run_that_records_another_mode_is_not_applied(self):
        signal = mode_signal(
            "json_schema", _drives(_run("json_schema"), _run("function_calling"))
        )
        assert (signal.status, signal.proof) == ("not_applied", "record")
        assert "1 of 2 drives record function_calling" in signal.reason

    def test_a_response_tool_call_in_a_mode_without_the_tool_is_not_applied(self):
        signal = mode_signal(
            "json_schema", _drives(_run("json_schema", tool="task_response"))
        )
        assert (signal.status, signal.proof) == ("not_applied", "trace")

    @pytest.mark.parametrize("mode", ["default", "unknown", None])
    def test_a_legacy_mode_is_never_verified(self, mode):
        signal = mode_signal(mode, _drives(_run("json_schema")))
        assert signal.status == "unverified"
