"""`kiln-harness-tuner-install-agents` — put the agents' skills and
instructions where one agent CLI looks for them.

The repository keeps one copy of each, in `.agents/`: the skills in
`.agents/skills/` and the instructions in `.agents/AGENTS.md`. Each agent
CLI reads them from its own place:

| Harness | Skills | Instructions |
|---|---|---|
| `claude` (Claude Code) | `.claude/skills/` | `CLAUDE.md` |
| `codex` (Codex) | `.agents/skills/` | `AGENTS.md` |
| `opencode` (OpenCode) | `.agents/skills/` | `AGENTS.md` |

Codex and OpenCode read `.agents/skills/` themselves, so at the repository
root only their instructions file is written.

For Claude Code it also adds the `kiln_api_mcp` server (the `call_kiln_api`
tool, for interactive sessions) from `.agents/claude/`: the server into
`.mcp.json` and its switch into `.claude/settings.json`. Both are merged into
what is there. Loop sessions never use them: each gets its own MCP config. No
MCP config is written for OpenCode, because its loop sessions run inside the
repository and would load a project config, and the gateway must stay their
only way to reach Kiln. A run installs for its own
harness before the first session; this command does the same by hand, or
into another directory with `--dest`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# install_agents.py sits at <repo>/src/kiln_harness_tuner/, as cli.py does.
REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / ".agents"
SOURCE_INSTRUCTIONS = "AGENTS.md"

# The first line of every installed instructions file. A file that starts with
# it was written here and is replaced freely; any other file is the reader's
# own, and is replaced only with `--force`.
MARKER = (
    "<!-- Installed by kiln-harness-tuner-install-agents from"
    " .agents/AGENTS.md. Edit that file, not this one. -->"
)


@dataclass(frozen=True)
class Layout:
    skills: str
    instructions: str


HARNESSES: dict[str, Layout] = {
    "claude": Layout(skills=".claude/skills", instructions="CLAUDE.md"),
    "codex": Layout(skills=".agents/skills", instructions="AGENTS.md"),
    "opencode": Layout(skills=".agents/skills", instructions="AGENTS.md"),
}


class InstallError(Exception):
    """An install that would overwrite a file it did not write."""


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
    harness: str,
    dest: Path = REPO_ROOT,
    *,
    source: Path = SOURCE,
    force: bool = False,
) -> list[Path]:
    """Install the skills and the instructions for `harness` under `dest`.
    Returns what was written. A skill directory of the same name is replaced;
    other skills under `dest` are left alone."""
    layout = HARNESSES[harness]
    written: list[Path] = []
    skills_source = (source / "skills").resolve()
    skills_dest = dest / layout.skills
    if skills_dest.resolve() != skills_source:
        for skill in sorted(p for p in skills_source.iterdir() if p.is_dir()):
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
        if not current.startswith(MARKER):
            raise InstallError(
                f"{target} exists and was not written by this command.\n"
                "fix: move it away, or pass --force to replace it."
            )
    target.write_text(f"{MARKER}\n\n{text}", encoding="utf-8")
    written.append(target)
    if harness == "claude":
        written += _install_claude_mcp(source, dest)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kiln-harness-tuner-install-agents",
        description=(
            "Copy the agents' skills and instructions from .agents/ into the"
            " layout one agent CLI reads."
        ),
    )
    parser.add_argument(
        "--harness",
        choices=[*HARNESSES, "all"],
        required=True,
        help="the agent CLI to install for: claude, codex, opencode, or all",
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
    harnesses = list(HARNESSES) if args.harness == "all" else [args.harness]
    dest = args.dest.expanduser().resolve()
    try:
        for harness in harnesses:
            install(harness, dest, force=args.force)
            layout = HARNESSES[harness]
            print(
                f"{harness}: skills in {dest / layout.skills},"
                f" instructions in {dest / layout.instructions}"
            )
    except InstallError as exc:
        print(f"kiln-harness-tuner-install-agents: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
