"""`kiln-harness-tuner-install-agents`: the skills and the instructions in
`.agents/` land where each agent CLI reads them, and an instructions file the
command did not write is never replaced without `--force`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kiln_harness_tuner.install_agents import (
    AGENT_CLIS,
    MARKER,
    REPO_ROOT,
    InstallError,
    agent_cli_name,
    install,
    main,
)


def source(tmp_path: Path) -> Path:
    src = tmp_path / "src" / ".agents"
    (src / "skills" / "alpha").mkdir(parents=True)
    (src / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\n---\n", encoding="utf-8"
    )
    (src / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    (src / "claude").mkdir()
    server = {"type": "stdio", "command": "kiln_api_mcp", "args": []}
    (src / "claude" / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kiln_api_mcp": server}}), encoding="utf-8"
    )
    (src / "claude" / "settings.json").write_text(
        json.dumps({"enabledMcpjsonServers": ["kiln_api_mcp"]}), encoding="utf-8"
    )
    return src


def test_the_repository_keeps_one_copy_in_dot_agents():
    assert (REPO_ROOT / ".agents" / "AGENTS.md").is_file()
    skills = REPO_ROOT / ".agents" / "skills"
    assert any(skills.glob("*/SKILL.md"))


@pytest.mark.parametrize(
    ("agent_cli", "skills", "instructions"),
    [
        ("claude", ".claude/skills", "CLAUDE.md"),
        ("codex", ".agents/skills", "AGENTS.md"),
        ("opencode", ".agents/skills", "AGENTS.md"),
    ],
)
def test_each_agent_cli_gets_its_own_layout(tmp_path, agent_cli, skills, instructions):
    dest = tmp_path / "dest"
    dest.mkdir()
    install(agent_cli, dest, source=source(tmp_path))
    assert (dest / skills / "alpha" / "SKILL.md").is_file()
    text = (dest / instructions).read_text(encoding="utf-8")
    assert text.startswith(MARKER) and text.endswith("# Rules\n")


def test_skills_read_in_place_are_not_copied_onto_themselves(tmp_path):
    src = source(tmp_path)
    written = install("codex", src.parent, source=src)
    assert written == [src.parent / "AGENTS.md"]
    assert (src / "skills" / "alpha" / "SKILL.md").is_file()


def test_a_reinstall_replaces_the_skill_and_keeps_other_skills(tmp_path):
    dest = tmp_path / "dest"
    other = dest / ".claude" / "skills" / "mine"
    other.mkdir(parents=True)
    src = source(tmp_path)
    install("claude", dest, source=src)
    stale = dest / ".claude" / "skills" / "alpha" / "old.md"
    stale.write_text("stale", encoding="utf-8")
    install("claude", dest, source=src)
    assert not stale.exists()
    assert other.is_dir()


def test_an_instructions_file_it_did_not_write_needs_force(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "CLAUDE.md").write_text("my own project rules\n", encoding="utf-8")
    src = source(tmp_path)
    with pytest.raises(InstallError, match="--force"):
        install("claude", dest, source=src)
    assert (dest / "CLAUDE.md").read_text(encoding="utf-8") == "my own project rules\n"
    install("claude", dest, source=src, force=True)
    assert (dest / "CLAUDE.md").read_text(encoding="utf-8").startswith(MARKER)


def test_the_command_installs_every_agent_cli(tmp_path, capsys):
    dest = tmp_path / "dest"
    dest.mkdir()
    assert main(["--agent-cli", "all", "--dest", str(dest)]) == 0
    for layout in AGENT_CLIS.values():
        assert (dest / layout.instructions).is_file()
        assert (dest / layout.skills / "memory" / "SKILL.md").is_file()
    assert "claude: skills in" in capsys.readouterr().out


@pytest.mark.parametrize(
    "value",
    ["codex", "codex exec -m gpt-6.1-sol", "/usr/local/bin/codex exec"],
)
def test_the_command_takes_a_name_or_a_whole_agent_cli_command(tmp_path, capsys, value):
    dest = tmp_path / "dest"
    dest.mkdir()
    assert main(["--agent-cli", value, "--dest", str(dest)]) == 0
    assert capsys.readouterr().out.startswith("codex: skills in")
    assert (dest / "AGENTS.md").is_file()
    assert not (dest / "CLAUDE.md").exists()


@pytest.mark.parametrize("value", ["cursor", "cursor-agent -p", "'codex exec", ""])
def test_the_command_refuses_an_agent_cli_it_has_no_layout_for(tmp_path, capsys, value):
    with pytest.raises(SystemExit) as exc:
        main(["--agent-cli", value, "--dest", str(tmp_path)])
    assert exc.value.code == 2
    assert not any(tmp_path.iterdir())
    # The error is the last line; the usage line above it lists the choices
    # whatever the error says. How argparse quotes them differs between
    # Python versions.
    error = capsys.readouterr().err.strip().splitlines()[-1]
    assert "invalid choice" in error
    assert all(name in error for name in [*AGENT_CLIS, "all"])


def test_claude_gets_the_mcp_server_merged_into_its_config(tmp_path):
    dest = tmp_path / "dest"
    (dest / ".claude").mkdir(parents=True)
    (dest / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"mine": {}}}), encoding="utf-8"
    )
    (dest / ".claude" / "settings.json").write_text(
        json.dumps({"model": "x", "enabledMcpjsonServers": ["mine"]}), encoding="utf-8"
    )
    src = source(tmp_path)
    install("claude", dest, source=src)
    install("claude", dest, source=src)  # a second install changes nothing
    servers = json.loads((dest / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"mine", "kiln_api_mcp"}
    settings = json.loads(
        (dest / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    assert settings == {"model": "x", "enabledMcpjsonServers": ["mine", "kiln_api_mcp"]}


def test_codex_and_opencode_get_no_mcp_config(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    src = source(tmp_path)
    install("codex", dest, source=src)
    install("opencode", dest, source=src)
    assert not (dest / ".mcp.json").exists()
    assert not (dest / ".claude").exists()


def test_a_config_that_is_not_json_is_refused(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / ".mcp.json").write_text("not json", encoding="utf-8")
    with pytest.raises(InstallError, match="not valid JSON"):
        install("claude", dest, source=source(tmp_path))


@pytest.mark.parametrize(
    ("value", "name"),
    [
        (r"C:\Users\steve\AppData\Roaming\npm\codex.cmd exec", "codex"),
        (r'"C:\Program Files\OpenCode\opencode.exe" run', "opencode"),
        ("claude.EXE -p", "claude"),
    ],
)
def test_a_windows_command_names_its_agent_cli(monkeypatch, value, name):
    monkeypatch.setattr(sys, "platform", "win32")
    assert agent_cli_name(value) == name
