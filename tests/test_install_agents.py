"""The agent files the package ships in `agent_files/`: a run's agents folder
(`install_for_run`) and `harness-tuner-install-agents --dest`
(`install_into`)."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

import kiln_harness_tuner
from kiln_harness_tuner import install_agents
from kiln_harness_tuner.install_agents import (
    AGENT_CLIS,
    InstallError,
    expected_files,
    install_for_run,
    install_into,
    main,
    package_version,
    source_dir,
)

VERSION = "9.9.9"


def _package_files_at(monkeypatch, root: Path) -> None:
    """`importlib.resources.files` answers `root` for the package."""
    monkeypatch.setattr(importlib.resources, "files", lambda package: root)


@pytest.fixture
def source(tmp_path, monkeypatch) -> Path:
    """A small package source, served as the package's agent files."""
    src = tmp_path / "package" / "agent_files"
    (src / "skills" / "alpha" / "references").mkdir(parents=True)
    (src / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n")
    (src / "skills" / "alpha" / "references" / "notes.md").write_text("notes\n")
    (src / "skills" / "beta").mkdir()
    (src / "skills" / "beta" / "SKILL.md").write_text("---\nname: beta\n---\n")
    (src / "AGENTS.md").write_text("# The loop\n")
    (src / "claude").mkdir()
    server = {"type": "stdio", "command": "kiln_api_mcp", "args": []}
    (src / "claude" / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kiln_api_mcp": server}})
    )
    (src / "claude" / "settings.json").write_text(
        json.dumps({"enabledMcpjsonServers": ["kiln_api_mcp"]})
    )
    _package_files_at(monkeypatch, src.parent)
    return src


def _bytecode(folder: Path) -> Path:
    """A byte-code cache under `folder`, as an import of a template leaves."""
    cache = folder / "__pycache__"
    cache.mkdir(parents=True)
    pyc = cache / "wrapper.cpython-313.pyc"
    pyc.write_bytes(b"\x00compiled")
    return pyc


def _files(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


# ---- the package's agent files ----


def test_the_package_ships_the_agent_files():
    root = source_dir()
    package = Path(kiln_harness_tuner.__file__).resolve().parent
    assert root.resolve() == package / "agent_files"
    assert (root / "AGENTS.md").is_file()
    assert (root / "claude" / ".mcp.json").is_file()
    assert (root / "claude" / "settings.json").is_file()
    assert any((root / "skills").glob("*/SKILL.md"))


def test_the_loop_instructions_are_the_loop_alone():
    # The contributor guide moved to the repository root; the loop agents
    # read only what the loop is (FS 5.2).
    text = (source_dir() / "AGENTS.md").read_text()
    assert text.startswith("# Kiln Harness Tuner\n")
    assert "##" not in text
    assert "ASD-STE100" not in text


def test_missing_agent_files_are_refused(tmp_path, monkeypatch):
    (tmp_path / "agent_files" / "skills").mkdir(parents=True)  # no AGENTS.md
    _package_files_at(monkeypatch, tmp_path)
    with pytest.raises(InstallError) as exc:
        install_for_run("claude", tmp_path / "agents", VERSION)
    assert str(tmp_path / "agent_files") in str(exc.value)
    assert "fix: reinstall harness-tuner." in str(exc.value)
    assert not (tmp_path / "agents").exists()


def test_agent_files_not_on_disk_are_refused(tmp_path, monkeypatch):
    # A zip import: `files()` is a Traversable that is no real folder, and the
    # agent CLIs read skills from real folders.
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("agent_files/AGENTS.md", "# Rules\n")
    _package_files_at(monkeypatch, zipfile.Path(archive))
    with pytest.raises(InstallError, match="reinstall harness-tuner"):
        source_dir()


def test_the_version_is_the_installed_distributions():
    assert package_version() == importlib.metadata.version("harness-tuner")


def test_the_version_falls_back_to_the_packages_own(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert package_version() == kiln_harness_tuner.__version__


# ---- a run's agents folder ----


@pytest.mark.parametrize(
    ("agent_cli", "skills", "instructions"),
    [
        ("claude", ".claude/skills", "CLAUDE.md"),
        ("codex", ".agents/skills", "AGENTS.md"),
        ("opencode", ".agents/skills", "AGENTS.md"),
    ],
)
def test_a_run_installs_the_skills_and_instructions_per_version(
    tmp_path, source, agent_cli, skills, instructions
):
    folder = install_for_run(agent_cli, tmp_path / "agents", VERSION)
    assert folder == tmp_path / "agents" / agent_cli / VERSION
    assert _files(folder) == {
        f"{skills}/alpha/SKILL.md",
        f"{skills}/alpha/references/notes.md",
        f"{skills}/beta/SKILL.md",
        instructions,
    }
    # The instructions are the package's, word for word: no marker line.
    assert (folder / instructions).read_text() == "# The loop\n"
    # Nothing beside the folder: the temporary build was renamed into place.
    assert [p.name for p in folder.parent.iterdir()] == [VERSION]


def test_the_real_package_installs_without_the_mcp_config(tmp_path):
    folder = install_for_run("claude", tmp_path / "agents", VERSION)
    assert (folder / ".claude" / "skills" / "kiln-api" / "SKILL.md").is_file()
    assert (folder / "CLAUDE.md").read_bytes() == (
        source_dir() / "AGENTS.md"
    ).read_bytes()
    assert not (folder / ".mcp.json").exists()
    assert not (folder / ".claude" / "settings.json").exists()
    assert not any("__pycache__" in p.parts for p in folder.rglob("*"))


def test_a_matching_folder_is_used_as_it_is(tmp_path, source):
    folder = install_for_run("claude", tmp_path / "agents", VERSION)
    skill = folder / ".claude" / "skills" / "alpha" / "SKILL.md"
    os.utime(skill, (1_000_000, 1_000_000))
    assert install_for_run("claude", tmp_path / "agents", VERSION) == folder
    assert skill.stat().st_mtime == 1_000_000


def test_each_version_gets_its_own_folder(tmp_path, source):
    old = install_for_run("codex", tmp_path / "agents", "0.1.0")
    new = install_for_run("codex", tmp_path / "agents", "0.2.0")
    assert old != new and old.is_dir() and new.is_dir()


@pytest.mark.parametrize("change", ["extra", "changed", "missing"])
def test_a_folder_that_differs_is_replaced(tmp_path, source, change):
    folder = install_for_run("claude", tmp_path / "agents", VERSION)
    skill = folder / ".claude" / "skills" / "alpha" / "SKILL.md"
    if change == "extra":
        (folder / ".claude" / "skills" / "alpha" / "stale.md").write_text("old")
    elif change == "changed":
        # A contributor's edit lands in the source, not the installed copy.
        (source / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    else:
        skill.unlink()
    assert install_for_run("claude", tmp_path / "agents", VERSION) == folder
    assert _files(folder) == set(expected_files("claude"))
    assert skill.read_bytes() == (source / "skills" / "alpha" / "SKILL.md").read_bytes()
    assert [p.name for p in folder.parent.iterdir()] == [VERSION]


def test_byte_code_caches_are_neither_copied_nor_counted(tmp_path, source):
    _bytecode(source / "skills" / "alpha")
    folder = install_for_run("claude", tmp_path / "agents", VERSION)
    assert not any("__pycache__" in p.parts for p in folder.rglob("*"))
    # A cache that appears in the installed folder is not a difference.
    pyc = _bytecode(folder / ".claude" / "skills" / "alpha")
    install_for_run("claude", tmp_path / "agents", VERSION)
    assert pyc.exists()


def test_finder_files_are_neither_copied_nor_counted(tmp_path, source):
    # macOS's Finder leaves a `.DS_Store` in any folder it opens. One in the
    # agents folder must not swap it out from under a live run.
    (source / "skills" / ".DS_Store").write_bytes(b"\x00finder")
    folder = install_for_run("claude", tmp_path / "agents", VERSION)
    assert not any(p.name == ".DS_Store" for p in folder.rglob("*"))
    (folder / ".claude" / ".DS_Store").write_bytes(b"\x00finder")
    skill = folder / ".claude" / "skills" / "alpha" / "SKILL.md"
    os.utime(skill, (1_000_000, 1_000_000))
    install_for_run("claude", tmp_path / "agents", VERSION)
    assert skill.stat().st_mtime == 1_000_000


def test_dest_leaves_finder_files_behind(tmp_path, source):
    (source / "skills" / "alpha" / ".DS_Store").write_bytes(b"\x00finder")
    install_into("claude", tmp_path / "dest")
    skill = tmp_path / "dest" / ".claude" / "skills" / "alpha"
    assert (skill / "SKILL.md").is_file()
    assert not (skill / ".DS_Store").exists()


@pytest.mark.parametrize("found", ["missing", "different"])
def test_a_concurrent_placement_is_accepted(tmp_path, source, monkeypatch, found):
    """Two runs start at once, and the other one places its copy just before
    this run's own rename into place. This run's rename fails, its copy is
    dropped, and the other's is used: it holds the same files."""
    folder = tmp_path / "agents" / "claude" / VERSION
    if found == "different":
        install_for_run("claude", tmp_path / "agents", VERSION)
        (folder / "stale.md").write_text("old")
    real_rename = os.rename

    def the_other_run_places_first(src, dst):
        if Path(dst) == folder:
            monkeypatch.setattr(os, "rename", real_rename)
            install_for_run("claude", tmp_path / "agents", VERSION)
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", the_other_run_places_first)
    assert install_for_run("claude", tmp_path / "agents", VERSION) == folder
    assert _files(folder) == set(expected_files("claude"))
    # Neither run's temporary or swapped-out copy is left beside it.
    assert [p.name for p in folder.parent.iterdir()] == [VERSION]


def test_a_matching_copy_placed_meanwhile_is_kept(tmp_path, source, monkeypatch):
    """Two runs start while the folder is missing, and the other one places
    its copy while this run builds. This run keeps that copy rather than
    swapping it out: the other run's sessions already work in it."""
    folder = tmp_path / "agents" / "claude" / VERSION
    real_build = install_agents.build
    placed = {}

    def build_while_the_other_run_places(tmp, expected):
        real_build(tmp, expected)
        monkeypatch.setattr(install_agents, "build", real_build)
        install_for_run("claude", tmp_path / "agents", VERSION)  # the other run
        placed["inode"] = folder.stat().st_ino

    monkeypatch.setattr(install_agents, "build", build_while_the_other_run_places)
    assert install_for_run("claude", tmp_path / "agents", VERSION) == folder
    assert folder.stat().st_ino == placed["inode"]
    assert [p.name for p in folder.parent.iterdir()] == [VERSION]


def test_a_folder_swapped_away_meanwhile_is_replaced(tmp_path, source, monkeypatch):
    """The other run moved the differing folder away to swap in its own
    copy, and has not put it there yet: this run places its own copy in the
    free slot."""
    folder = install_for_run("claude", tmp_path / "agents", VERSION)
    (folder / "stale.md").write_text("old")
    real_rename = os.rename

    def the_other_run_moves_it_first(src, dst):
        if Path(src) == folder:
            monkeypatch.setattr(os, "rename", real_rename)
            shutil.rmtree(folder)  # the other run's swap, halfway done
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", the_other_run_moves_it_first)
    assert install_for_run("claude", tmp_path / "agents", VERSION) == folder
    assert _files(folder) == set(expected_files("claude"))
    assert [p.name for p in folder.parent.iterdir()] == [VERSION]


def test_a_failed_swap_puts_the_last_copy_back(tmp_path, source, monkeypatch):
    """A rename that fails for any reason but a concurrent run is an error,
    with the OS's message, and the folder sessions read is not lost."""
    folder = install_for_run("claude", tmp_path / "agents", VERSION)
    (folder / "stale.md").write_text("old")
    real_rename = os.rename

    def refused(src, dst):
        if Path(dst) == folder and Path(src).name.startswith(".tmp-"):
            raise PermissionError(13, "Permission denied", str(dst))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", refused)
    with pytest.raises(InstallError) as exc:
        install_for_run("claude", tmp_path / "agents", VERSION)
    assert "Permission denied" in str(exc.value)
    assert (folder / "stale.md").read_text() == "old"
    assert [p.name for p in folder.parent.iterdir()] == [VERSION]


def test_a_failed_build_leaves_nothing_half_built(tmp_path, source, monkeypatch):
    def disk_full(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(install_agents.shutil, "copyfile", disk_full)
    with pytest.raises(InstallError) as exc:
        install_for_run("claude", tmp_path / "agents", VERSION)
    assert "No space left on device" in str(exc.value)
    assert "\nfix: " in str(exc.value)
    assert list((tmp_path / "agents" / "claude").iterdir()) == []


def test_a_folder_still_wrong_after_install_is_refused(tmp_path, source, monkeypatch):
    monkeypatch.setattr(install_agents, "_place", lambda *_args: None)
    with pytest.raises(InstallError, match="does not hold the package's files"):
        install_for_run("claude", tmp_path / "agents", VERSION)


# ---- `--dest` ----


@pytest.mark.parametrize(
    ("agent_cli", "skills"),
    [("claude", ".claude/skills"), ("codex", ".agents/skills")],
)
def test_dest_gets_the_skills_and_no_instructions(tmp_path, source, agent_cli, skills):
    dest = tmp_path / "dest"
    dest.mkdir()
    install_into(agent_cli, dest)
    assert (dest / skills / "alpha" / "references" / "notes.md").is_file()
    assert (dest / skills / "beta" / "SKILL.md").is_file()
    assert not (dest / "CLAUDE.md").exists()
    assert not (dest / "AGENTS.md").exists()


def test_dest_leaves_byte_code_behind(tmp_path, source):
    _bytecode(source / "skills" / "alpha")
    install_into("codex", tmp_path / "dest")
    installed = tmp_path / "dest" / ".agents" / "skills" / "alpha"
    assert (installed / "SKILL.md").is_file()
    assert not (installed / "__pycache__").exists()


def test_a_reinstall_replaces_the_skill_and_keeps_other_skills(tmp_path, source):
    dest = tmp_path / "dest"
    other = dest / ".claude" / "skills" / "mine"
    other.mkdir(parents=True)
    install_into("claude", dest)
    stale = dest / ".claude" / "skills" / "alpha" / "old.md"
    stale.write_text("stale")
    (dest / ".claude" / "skills" / "beta").rename(dest / "beta-moved")
    (dest / ".claude" / "skills" / "beta").write_text("a file in the way")
    install_into("claude", dest)
    assert not stale.exists()
    assert (dest / ".claude" / "skills" / "beta" / "SKILL.md").is_file()
    assert other.is_dir()


def test_claude_gets_the_mcp_server_merged_into_its_config(tmp_path, source):
    dest = tmp_path / "dest"
    (dest / ".claude").mkdir(parents=True)
    (dest / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {}}}))
    (dest / ".claude" / "settings.json").write_text(
        json.dumps({"model": "x", "enabledMcpjsonServers": ["mine"]})
    )
    install_into("claude", dest)
    install_into("claude", dest)  # a second install changes nothing
    servers = json.loads((dest / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"mine", "kiln_api_mcp"}
    settings = json.loads((dest / ".claude" / "settings.json").read_text())
    assert settings == {"model": "x", "enabledMcpjsonServers": ["mine", "kiln_api_mcp"]}


def test_codex_and_opencode_get_no_mcp_config(tmp_path, source):
    dest = tmp_path / "dest"
    dest.mkdir()
    install_into("codex", dest)
    install_into("opencode", dest)
    assert not (dest / ".mcp.json").exists()
    assert not (dest / ".claude").exists()


@pytest.mark.parametrize("text", ["not json", "[1, 2]"])
def test_a_config_that_is_not_a_json_object_is_refused(tmp_path, source, text):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / ".mcp.json").write_text(text)
    with pytest.raises(InstallError, match="JSON"):
        install_into("claude", dest)
    assert (dest / ".mcp.json").read_text() == text
    # Refused before anything is written: no skills, no settings.
    assert not (dest / ".claude").exists()


def test_a_refused_settings_file_leaves_mcp_json_as_it_was(tmp_path, source):
    dest = tmp_path / "dest"
    (dest / ".claude").mkdir(parents=True)
    (dest / ".claude" / "settings.json").write_text("[]")
    (dest / ".mcp.json").write_text('{"mcpServers": {}}')
    with pytest.raises(InstallError, match="settings.json is not a JSON object"):
        install_into("claude", dest)
    assert (dest / ".mcp.json").read_text() == '{"mcpServers": {}}'
    assert not (dest / ".claude" / "skills").exists()


def test_a_dest_that_is_a_file_is_an_error_not_a_traceback(tmp_path, capsys):
    dest = tmp_path / "a-file"
    dest.write_text("")
    assert main(["--agent-cli", "codex", "--dest", str(dest)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"harness-tuner-install-agents: cannot install into {dest}")
    assert "fix: give a folder that you can write to as --dest." in err


# ---- the command ----


def test_the_command_installs_every_agent_cli(tmp_path, capsys):
    dest = tmp_path / "dest"
    dest.mkdir()
    assert main(["--agent-cli", "all", "--dest", str(dest)]) == 0
    for layout in AGENT_CLIS.values():
        assert (dest / layout.skills / "memory" / "SKILL.md").is_file()
    assert not (dest / "CLAUDE.md").exists() and not (dest / "AGENTS.md").exists()
    assert capsys.readouterr().out.splitlines() == [
        f"claude: skills in {dest / '.claude/skills'},"
        f" kiln_api_mcp in {dest / '.mcp.json'}",
        f"codex: skills in {dest / '.agents/skills'}",
        f"opencode: skills in {dest / '.agents/skills'}",
    ]


def test_dest_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--agent-cli", "claude"])
    assert exc.value.code == 2
    assert "--dest" in capsys.readouterr().err


def test_force_is_gone(tmp_path, capsys):
    # It only replaced an instructions file, which is no longer written.
    with pytest.raises(SystemExit) as exc:
        main(["--agent-cli", "claude", "--dest", str(tmp_path), "--force"])
    assert exc.value.code == 2
    assert "unrecognized arguments: --force" in capsys.readouterr().err


def test_the_command_names_itself_in_a_refusal(tmp_path, monkeypatch, capsys):
    _package_files_at(monkeypatch, tmp_path)
    assert main(["--agent-cli", "codex", "--dest", str(tmp_path / "dest")]) == 1
    assert capsys.readouterr().err.startswith("harness-tuner-install-agents: ")


@pytest.mark.parametrize(
    "value",
    ["codex", "codex exec -m gpt-6.1-sol", "/usr/local/bin/codex exec"],
)
def test_the_command_takes_a_name_or_a_whole_agent_cli_command(
    tmp_path, source, capsys, value
):
    dest = tmp_path / "dest"
    dest.mkdir()
    assert main(["--agent-cli", value, "--dest", str(dest)]) == 0
    assert capsys.readouterr().out.startswith("codex: skills in")
    assert (dest / ".agents" / "skills" / "alpha").is_dir()
    assert not (dest / ".claude").exists()


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
