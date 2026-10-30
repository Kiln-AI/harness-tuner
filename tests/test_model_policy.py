"""The model policy: which (provider, model) pairs the task's run
configs may use, enforced in the gateway and before any measurement or
promotion. Judges are not subject to it."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln_harness_tuner.model_policy import (
    ModelPolicy,
    ModelPolicyError,
    allowed_models,
    model_pairs,
)

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "model-policy.json"
TASK = "424242770014"
PROJECT = "424242748186"
BASE = f"/api/projects/{PROJECT}/tasks/{TASK}"


def _rc_body(provider: str, model: str) -> dict:
    return {
        "name": "arm",
        "run_config_properties": {
            "type": "kiln_agent",
            "model_name": model,
            "model_provider_name": provider,
            "prompt_id": "p",
            "structured_output_mode": "json_schema",
        },
    }


class TestTheExamplePolicyEncodesTheRule:
    """The example file IS the provider rule; these rows are the rule, one by
    one."""

    policy = ModelPolicy.load(EXAMPLE)

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            ("fireworks_ai", "gpt_oss_120b"),
            ("fireworks_ai", "kimi_k3"),
            ("together_ai", "qwen_3p8_2p4t_a95b"),
            ("openai", "gpt_5_4_mini"),
            ("anthropic", "claude_4_5_haiku"),
            ("gemini_api", "gemini_3_8_flash"),
            ("vertex", "gemini_3_8_flash"),
            ("openrouter", "gpt_5_4_mini"),
            ("openrouter", "claude_4_5_haiku"),
            ("openrouter", "claude_sonnet_4_6"),
            ("openrouter", "gemini_3_8_flash"),
        ],
    )
    def test_allowed(self, provider, model):
        assert self.policy.decide(provider, model).allowed, (provider, model)

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            ("openrouter", "gpt_oss_120b"),  # open-weight, matches gpt_*
            ("openrouter", "qwen_3p8_2p4t_a95b"),
            ("openrouter", "kimi_k3"),
            ("openrouter", "glm_5_3"),
            ("openrouter", "llama_4_maverick"),
            ("openrouter", "deepseek_v4"),
            ("groq", "gpt_oss_120b"),
            ("cerebras", "qwen_3p8_2p4t_a95b"),
            ("siliconflow_cn", "qwen_3p8_2p4t_a95b"),
            ("kiln_fine_tune", "anything"),
        ],
    )
    def test_refused(self, provider, model):
        assert not self.policy.decide(provider, model).allowed, (provider, model)


class TestOrder:
    def test_an_exception_allows_one_exact_pair_a_general_deny_refuses(self):
        policy = ModelPolicy.from_json(
            {
                "allow": [{"provider": "openai", "model": "*"}],
                "deny": [{"provider": "cerebras", "model": "*"}],
                "exceptions": [{"provider": "cerebras", "model": "qwen_3p8_2p4t_a95b"}],
            }
        )
        assert policy.decide("cerebras", "qwen_3p8_2p4t_a95b").allowed
        assert not policy.decide("cerebras", "gpt_oss_120b").allowed
        assert not policy.decide("groq", "qwen_3p8_2p4t_a95b").allowed

    def test_an_exception_also_allows_a_pair_no_allow_rule_names(self):
        policy = ModelPolicy.from_json(
            {
                "allow": [{"provider": "openai", "model": "*"}],
                "exceptions": [{"provider": "groq", "model": "kimi_k3"}],
            }
        )
        assert policy.decide("groq", "kimi_k3").allowed
        assert not policy.decide("groq", "gpt_oss_120b").allowed

    def test_deny_beats_allow(self):
        policy = ModelPolicy.from_json(
            {
                "allow": [{"provider": "openrouter", "model": "gpt_*"}],
                "deny": [{"provider": "openrouter", "model": "gpt_oss_*"}],
            }
        )
        assert policy.decide("openrouter", "gpt_5_4_mini").allowed
        assert not policy.decide("openrouter", "gpt_oss_120b").allowed

    def test_no_match_is_refused_and_a_missing_pair_is_refused(self):
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "openai", "model": "*"}]}
        )
        assert not policy.decide("anthropic", "claude_4_5_haiku").allowed
        assert not policy.decide(None, "claude_4_5_haiku").allowed
        assert not policy.decide("openai", None).allowed

    def test_globs_are_case_sensitive(self):
        policy = ModelPolicy.from_json(
            {"allow": [{"provider": "openai", "model": "gpt_*"}]}
        )
        assert not policy.decide("openai", "GPT_5").allowed


class TestLoading:
    @pytest.mark.parametrize(
        "data",
        [
            [],
            {"allow": []},
            {"alow": [{"provider": "a", "model": "b"}]},
            {"allow": [{"provider": "a"}]},
            {"allow": [{"provider": "a", "model": ""}]},
            {"allow": [{"provider": "a", "model": "b", "extra": 1}]},
            {
                "allow": [{"provider": "a", "model": "b"}],
                "exceptions": [{"provider": "c", "model": "d*"}],
            },
        ],
    )
    def test_a_malformed_policy_is_refused(self, data):
        with pytest.raises(ModelPolicyError):
            ModelPolicy.from_json(data)

    def test_an_unreadable_file_is_refused(self, tmp_path):
        with pytest.raises(ModelPolicyError):
            ModelPolicy.load(tmp_path / "missing.json")
        bad = tmp_path / "bad.json"
        bad.write_text("{")
        with pytest.raises(ModelPolicyError):
            ModelPolicy.load(bad)


def test_model_pairs_reads_nested_run_config_properties_and_not_judges():
    body = {
        "run_config_properties": {"model_name": "m1", "model_provider_name": "p1"},
        "judge": {"properties": {"model_name": "j", "model_provider": "openrouter"}},
        "list": [{"type": "kiln_agent", "model_name": "m2"}],
    }
    assert model_pairs(body) == [("p1", "m1"), (None, "m2")]


def test_allowed_models_filters_what_the_server_offers():
    policy = ModelPolicy.load(EXAMPLE)
    available = [
        {
            "provider_id": "openrouter",
            "models": [
                {"id": "gpt_oss_120b"},
                {"id": "claude_4_5_haiku", "name": "Haiku"},
            ],
        },
        {"provider_id": "groq", "models": [{"id": "kimi_k3"}]},
    ]
    assert allowed_models(policy, available) == [
        {"provider": "openrouter", "model": "claude_4_5_haiku", "name": "Haiku"}
    ]
    assert len(allowed_models(None, available)) == 3
