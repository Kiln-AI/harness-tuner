"""`harness-tuner-install-agents` — put the agents' skills and
instructions where one agent CLI looks for them.

The package keeps one copy of each, in `kiln_harness_tuner/agent_files/`
(`src/kiln_harness_tuner/agent_files/` in a checkout): the skills in
`agent_files/skills/` and the instructions in `agent_files/AGENTS.md`. They
are package data, read through `importlib.resources`, so an installed wheel
carries them. Each agent CLI reads them from its own place:

| Agent CLI | Skills | Instructions |
|---|---|---|
| `claude` (Claude Code) | `.claude/skills/` | `CLAUDE.md` |
| `codex` (Codex) | `.agents/skills/` | `AGENTS.md` |
| `opencode` (OpenCode) | `.agents/skills/` | `AGENTS.md` |

For Claude Code it also adds the `kiln_api_mcp` server (the `call_kiln_api`
tool, for interactive sessions) from `agent_files/claude/`: the server into
`.mcp.json` and its switch into `.claude/settings.json`. Both are merged into
what is there. Loop sessions never use them: each gets its own MCP config. No
MCP config is written for OpenCode, because its loop sessions run inside the
repository and would load a project config, and the gateway must stay their
only way to reach Kiln. A run installs for its own
agent CLI before the first session; this command does the same by hand, or
into another directory with `--dest`.
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# install_agents.py sits at <repo>/src/kiln_harness_tuner/, as cli.py does.
# The default install target only: runs still install into the checkout.
REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_INSTRUCTIONS = "AGENTS.md"

# The first line of every installed instructions file. A file that starts with
# it was written here and is replaced freely; any other file is the reader's
# own, and is replaced only with `--force`.
MARKER = (
    "<!-- Installed by harness-tuner-install-agents from"
    " src/kiln_harness_tuner/agent_files/AGENTS.md. Edit that file, not this"
    " one. -->"
)
# The marker this command wrote before the files moved into the package. A
# checkout's installed `CLAUDE.md` or `AGENTS.md` still starts with it, and is
# this command's own file all the same.
PREVIOUS_MARKERS = (
    "<!-- Installed by kiln-harness-tuner-install-agents from"
    " .agents/AGENTS.md. Edit that file, not this one. -->",
)


@dataclass(frozen=True)
class Layout:
    skills: str
    instructions: str


AGENT_CLIS: dict[str, Layout] = {
    "claude": Layout(skills=".claude/skills", instructions="CLAUDE.md"),
    "codex": Layout(skills=".agents/skills", instructions="AGENTS.md"),
    "opencode": Layout(skills=".agents/skills", instructions="AGENTS.md"),
}


class InstallError(Exception):
    """An install that cannot go ahead: the package's agent files are
    missing, or it would overwrite a file it did not write."""


def source_dir() -> Path:
    """The packaged agent files on disk.

    `files(...)` is a `pathlib.Path` for a package installed on disk, a wheel
    or an editable install, which is every install uv makes. Anything else (a
    zip import) is refused: the agent CLIs read skills from real folders."""
    root = importlib.resources.files("kiln_harness_tuner") / "agent_files"
    if not isinstance(root, Path) or not (root / SOURCE_INSTRUCTIONS).is_file():
        raise InstallError(
            f"the agent files are missing from the installed package ({root}).\n"
            "fix: reinstall harness-tuner."
        )
    return root


def _merge_json(target: Path, merge) -> Path:
    """Apply `merge` to the JSON object in `target` (empty when the file is
    absent) and write it back. A file that is not a JSON object is refused."""
    data: dict = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise InstallError(
                f"{target} is not valid JSON ({exc}).\nfix: correct or move it."
            ) from exc
        if not isinstance(data, dict):
            raise InstallError(f"{target} is not a JSON object.\nfix: move it.")
    merge(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target


def _install_claude_mcp(source: Path, dest: Path) -> list[Path]:
    """The `kiln_api_mcp` server into `.mcp.json`, and its switch into
    `.claude/settings.json`. Other servers and settings are kept."""
    ours = json.loads((source / "claude" / ".mcp.json").read_text(encoding="utf-8"))
    enabled = json.loads(
        (source / "claude" / "settings.json").read_text(encoding="utf-8")
    )["enabledMcpjsonServers"]

    def add_servers(data: dict) -> None:
        data.setdefault("mcpServers", {}).update(ours["mcpServers"])

    def enable(data: dict) -> None:
        names = data.setdefault("enabledMcpjsonServers", [])
        names.extend(n for n in enabled if n not in names)

    return [
        _merge_json(dest / ".mcp.json", add_servers),
        _merge_json(dest / ".claude" / "settings.json", enable),
    ]


def install(
    agent_cli: str,
    dest: Path = REPO_ROOT,
    *,
    source: Path | None = None,
    force: bool = False,
) -> list[Path]:
    """Install the skills and the instructions for `agent_cli` under `dest`,
    from `source` (default: the package's agent files). Returns what was
    written. A skill directory of the same name is replaced; other skills
    under `dest` are left alone."""
    if source is None:
        source = source_dir()
    layout = AGENT_CLIS[agent_cli]
    written: list[Path] = []
    skills_dest = dest / layout.skills
    for skill in sorted(p for p in (source / "skills").iterdir() if p.is_dir()):
        target = skills_dest / skill.name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill, target)
        written.append(target)

    text = (source / SOURCE_INSTRUCTIONS).read_text(encoding="utf-8")
    target = dest / layout.instructions
    if target.exists() and not force:
        current = target.read_text(encoding="utf-8")
        if not current.startswith((MARKER, *PREVIOUS_MARKERS)):
            raise InstallError(
                f"{target} exists and was not written by this command.\n"
                "fix: move it away, or pass --force to replace it."
            )
    target.write_text(f"{MARKER}\n\n{text}", encoding="utf-8")
    written.append(target)
    if agent_cli == "claude":
        written += _install_claude_mcp(source, dest)
    return written


def agent_cli_name(text: str) -> str:
    """An `--agent-cli` value as the name of the agent CLI it installs for.

    A name (`codex`, `all`) is itself. A whole command, as the run command's
    `--agent-cli` takes it (`'codex exec -m gpt-6.1-sol'`), is named by its
    first word, a path's last part. argparse's `choices` then accepts or
    refuses the name. The first word is taken here rather than through
    `dispatch.agent_cli`, whose package import loads every dispatcher."""
    try:
        words = shlex.split(text)
    except ValueError:  # unbalanced quotes: `choices` refuses it as written
        return text
    return Path(words[0]).name if words else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness-tuner-install-agents",
        description=(
            "Copy the agents' skills and instructions from the package into"
            " the layout one agent CLI reads."
        ),
    )
    parser.add_argument(
        "--agent-cli",
        type=agent_cli_name,
        choices=[*AGENT_CLIS, "all"],
        required=True,
        help="the agent CLI to install for, by name: claude, codex, opencode,"
        " or all. The whole command a run takes ('codex exec -m MODEL') also"
        " works: its first word names the CLI",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=REPO_ROOT,
        help="the directory to install into (default: this repository)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an instructions file this command did not write",
    )
    args = parser.parse_args(argv)
    agent_clis = list(AGENT_CLIS) if args.agent_cli == "all" else [args.agent_cli]
    dest = args.dest.expanduser().resolve()
    try:
        for agent_cli in agent_clis:
            install(agent_cli, dest, force=args.force)
            layout = AGENT_CLIS[agent_cli]
            print(
                f"{agent_cli}: skills in {dest / layout.skills},"
                f" instructions in {dest / layout.instructions}"
            )
    except InstallError as exc:
        print(f"harness-tuner-install-agents: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
