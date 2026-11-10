"""The models each agent CLI offers: Codex's cache and config, OpenCode's
`opencode models`, and Claude Code's fixed list."""

import json
import subprocess

import pytest

from kiln_harness_tuner.onboarding.agent_models import (
    CLAUDE_MODELS,
    AgentModel,
    codex_default_model,
    codex_home,
    codex_models,
    opencode_models,
)


def write_cache(home, models) -> None:
    (home / "models_cache.json").write_text(
        json.dumps({"fetched_at": "2026-11-11", "models": models})
    )


def test_codex_models_are_the_listed_ones_in_codex_order(tmp_path):
    write_cache(
        tmp_path,
        [
            {"slug": "gpt-6.1-sol", "visibility": "list", "description": "Workhorse."},
            {"slug": "codex-auto-review", "visibility": "hide"},
            {"slug": "gpt-6.1-terra", "description": 7},
            {"slug": "has space", "visibility": "list"},
            {"visibility": "list"},
            "not a model",
        ],
    )
    assert codex_models(tmp_path) == (
        AgentModel("gpt-6.1-sol", "Workhorse."),
        AgentModel("gpt-6.1-terra", None),
    )


@pytest.mark.parametrize(
    "content",
    [None, "not json", json.dumps([1, 2]), json.dumps({"models": "x"}), "{}"],
)
def test_a_missing_or_unexpected_codex_cache_gives_no_list(tmp_path, content):
    if content is not None:
        (tmp_path / "models_cache.json").write_text(content)
    assert codex_models(tmp_path) is None


def test_a_codex_cache_with_no_listed_model_gives_no_list(tmp_path):
    write_cache(tmp_path, [{"slug": "codex-auto-review", "visibility": "hide"}])
    assert codex_models(tmp_path) is None


def test_codex_default_model_is_read_from_its_config(tmp_path):
    (tmp_path / "config.toml").write_text(
        'model = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n'
    )
    assert codex_default_model(tmp_path) == "gpt-6-astra"


@pytest.mark.parametrize("content", [None, "model = [", "other = 1\n", 'model = ""'])
def test_no_codex_default_model_when_config_doesnt_set_one(tmp_path, content):
    if content is not None:
        (tmp_path / "config.toml").write_text(content)
    assert codex_default_model(tmp_path) is None


def test_codex_home_follows_codex_home(tmp_path):
    assert codex_home({"CODEX_HOME": str(tmp_path)}) == tmp_path
    assert codex_home({}).name == ".codex"


def runs(returncode=0, stdout="", error: BaseException | None = None):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if error is not None:
            raise error
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    run.calls = calls
    return run


def test_opencode_models_are_its_provider_model_lines():
    run = runs(stdout="opencode/big-pickle\n\nzai-coding-plan/glm-5.3\nWarning: x\n")
    assert opencode_models(run) == (
        AgentModel("opencode/big-pickle"),
        AgentModel("zai-coding-plan/glm-5.3"),
    )
    ((argv, kwargs),) = run.calls
    assert argv == ["opencode", "models"]
    assert kwargs["timeout"] > 0
    assert kwargs["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize(
    "run",
    [
        runs(returncode=1, stdout="opencode/big-pickle\n"),
        runs(stdout="no models here\n"),
        runs(error=FileNotFoundError("opencode")),
        runs(error=subprocess.TimeoutExpired(["opencode", "models"], 20)),
    ],
)
def test_opencode_gives_no_list_when_the_command_fails(run):
    assert opencode_models(run) is None


def test_claude_code_lists_its_aliases_and_fable_5_2():
    assert [model.name for model in CLAUDE_MODELS] == [
        "opus",
        "sonnet",
        "haiku",
        "claude-fable-5-2",
    ]
