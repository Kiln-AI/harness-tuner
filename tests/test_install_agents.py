"""`harness-tuner-install-agents`: the skills and the instructions the
package ships in `agent_files/` land where each agent CLI reads them, and an
instructions file the command did not write is never replaced without
`--force`."""

from __future__ import annotations

import importlib.resources
import json
import zipfile
from pathlib import Path

import pytest

import kiln_harness_tuner
from kiln_harness_tuner.install_agents import (
    AGENT_CLIS,
    MARKER,
    PREVIOUS_MARKERS,
    InstallError,
    install,
    main,
    source_dir,
)


def source(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "agent_files"
    (src / "skills" / "alpha").mkdir(parents=True)
    (src / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n")
    (src / "AGENTS.md").write_text("# Rules\n")
    (src / "claude").mkdir()
    server = {"type": "stdio", "command": "kiln_api_mcp", "args": []}
    (src / "claude" / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kiln_api_mcp": server}})
    )
    (src / "claude" / "settings.json").write_text(
        json.dumps({"enabledMcpjsonServers": ["kiln_api_mcp"]})
    )
    return src


def test_the_package_ships_the_agent_files():
    root = source_dir()
    package = Path(kiln_harness_tuner.__file__).resolve().parent
    assert root.resolve() == package / "agent_files"
    assert (root / "AGENTS.md").is_file()
    assert (root / "claude" / ".mcp.json").is_file()
    assert (root / "claude" / "settings.json").is_file()
    assert any((root / "skills").glob("*/SKILL.md"))


def _package_files_at(monkeypatch, root) -> None:
    """`importlib.resources.files` answers `root` for the package."""
    monkeypatch.setattr(importlib.resources, "files", lambda package: root)


def test_missing_agent_files_are_refused(tmp_path, monkeypatch):
    (tmp_path / "agent_files" / "skills").mkdir(parents=True)  # no AGENTS.md
    _package_files_at(monkeypatch, tmp_path)
    with pytest.raises(InstallError) as exc:
        install("claude", tmp_path / "dest")
    assert str(tmp_path / "agent_files") in str(exc.value)
    assert "fix: reinstall harness-tuner." in str(exc.value)
    assert not (tmp_path / "dest").exists()


def test_agent_files_not_on_disk_are_refused(tmp_path, monkeypatch):
    # A zip import: `files()` is a Traversable that is no real folder, and the
    # agent CLIs read skills from real folders.
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("agent_files/AGENTS.md", "# Rules\n")
    _package_files_at(monkeypatch, zipfile.Path(archive))
    with pytest.raises(InstallError, match="reinstall harness-tuner"):
        source_dir()


def test_the_command_names_itself_in_a_refusal(tmp_path, monkeypatch, capsys):
    _package_files_at(monkeypatch, tmp_path)
    assert main(["--agent-cli", "codex", "--dest", str(tmp_path / "dest")]) == 1
    assert capsys.readouterr().err.startswith("harness-tuner-install-agents: ")


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
    text = (dest / instructions).read_text()
    assert text.startswith(MARKER) and text.endswith("# Rules\n")


def test_a_reinstall_replaces_the_skill_and_keeps_other_skills(tmp_path):
    dest = tmp_path / "dest"
    other = dest / ".claude" / "skills" / "mine"
    other.mkdir(parents=True)
    src = source(tmp_path)
    install("claude", dest, source=src)
    stale = dest / ".claude" / "skills" / "alpha" / "old.md"
    stale.write_text("stale")
    install("claude", dest, source=src)
    assert not stale.exists()
    assert other.is_dir()


def test_an_instructions_file_it_did_not_write_needs_force(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "CLAUDE.md").write_text("my own project rules\n")
    src = source(tmp_path)
    with pytest.raises(InstallError, match="--force"):
        install("claude", dest, source=src)
    assert (dest / "CLAUDE.md").read_text() == "my own project rules\n"
    install("claude", dest, source=src, force=True)
    assert (dest / "CLAUDE.md").read_text().startswith(MARKER)


@pytest.mark.parametrize("marker", PREVIOUS_MARKERS)
def test_an_install_from_before_the_rename_is_replaced(tmp_path, marker):
    # A checkout's CLAUDE.md, installed by `kiln-harness-tuner-install-agents`
    # from `.agents/`, is this command's own file: no `--force` needed.
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "CLAUDE.md").write_text(f"{marker}\n\n# Old rules\n")
    install("claude", dest, source=source(tmp_path))
    text = (dest / "CLAUDE.md").read_text()
    assert text.startswith(MARKER) and text.endswith("# Rules\n")


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
    (dest / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {}}}))
    (dest / ".claude" / "settings.json").write_text(
        json.dumps({"model": "x", "enabledMcpjsonServers": ["mine"]})
    )
    src = source(tmp_path)
    install("claude", dest, source=src)
    install("claude", dest, source=src)  # a second install changes nothing
    servers = json.loads((dest / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"mine", "kiln_api_mcp"}
    settings = json.loads((dest / ".claude" / "settings.json").read_text())
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
    (dest / ".mcp.json").write_text("not json")
    with pytest.raises(InstallError, match="not valid JSON"):
        install("claude", dest, source=source(tmp_path))
