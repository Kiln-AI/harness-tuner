"""The agents' skills and instructions, where one agent CLI reads them.

The package keeps one copy of each, in `kiln_harness_tuner/agent_files/`
(`src/kiln_harness_tuner/agent_files/` in a checkout): the skills in
`agent_files/skills/` and the loop agents' instructions in
`agent_files/AGENTS.md`. They are package data, read through
`importlib.resources`, so an installed wheel carries them. Each agent CLI
reads them from its own place:

| Agent CLI | Skills | Instructions |
|---|---|---|
| `claude` (Claude Code) | `.claude/skills/` | `CLAUDE.md` |
| `codex` (Codex) | `.agents/skills/` | `AGENTS.md` |
| `opencode` (OpenCode) | `.agents/skills/` | `AGENTS.md` |

**A run** installs the skills and the instructions for its own agent CLI into
the state folder, at `agents/<cli>/<version>/` (`install_for_run`), before its
first session. The folder is checked at every run start and replaced when its
files differ from the package's.

**`harness-tuner-install-agents --dest DIR`** (`install_into`) puts the
skills into someone's own project, for interactive sessions. For Claude Code
it also adds the `kiln_api_mcp` server (the `call_kiln_api` tool) from
`agent_files/claude/`: the server into `.mcp.json` and its switch into
`.claude/settings.json`, both merged into what is there. It writes no
instructions file: the loop agents' instructions describe the loop, and are
no use in another project. Loop sessions never read either MCP file: each
gets its own MCP config per run.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import filecmp
import importlib.metadata
import importlib.resources
import json
import os
import secrets
import shlex
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from kiln_harness_tuner import __version__

DIST_NAME = "harness-tuner"
SOURCE_INSTRUCTIONS = "AGENTS.md"
# Names that are never agent files, wherever they sit. Byte code: the suite
# imports the wrapper templates under `agent_files/skills/` from the source
# tree, and an install may compile them. `.DS_Store`: macOS's Finder leaves
# one in any folder it opens, which would make a run's agents folder differ
# from the package and be swapped out under a live run. Every walk over
# agent files leaves them out.
NOT_AGENT_FILES = frozenset({"__pycache__", ".DS_Store"})
# What a rename onto a folder gives when another run has just placed it.
_TAKEN = frozenset({errno.ENOTEMPTY, errno.EEXIST})


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
    missing, a config to merge into is not JSON, or the agents folder cannot
    be built."""


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


def package_version() -> str:
    """The installed distribution's version, which names a run's agents
    folder. `__version__` when no distribution metadata is found."""
    try:
        return importlib.metadata.version(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return __version__


def _files_under(folder: Path) -> Iterator[Path]:
    """Every file under `folder`, `NOT_AGENT_FILES` left out."""
    for path in sorted(folder.rglob("*")):
        parts = path.relative_to(folder).parts
        if NOT_AGENT_FILES.isdisjoint(parts) and path.is_file():
            yield path


# ---- the run's install: <state>/agents/<cli>/<version>/ ----


def expected_files(agent_cli: str) -> dict[str, Path]:
    """What a run's agents folder holds: relative path → source file. Every
    skill file under the CLI's skills folder, and the instructions under the
    CLI's instructions name. Nothing else."""
    source = source_dir()
    layout = AGENT_CLIS[agent_cli]
    skills = source / "skills"
    expected = {
        f"{layout.skills}/{path.relative_to(skills).as_posix()}": path
        for path in _files_under(skills)
    }
    expected[layout.instructions] = source / SOURCE_INSTRUCTIONS
    return expected


def matches(target: Path, expected: dict[str, Path]) -> bool:
    """True when `target` holds exactly the expected files (none missing,
    none extra; folders and `NOT_AGENT_FILES` not counted) and each one's
    bytes equal its source's."""
    if not target.is_dir():
        return False
    found = {path.relative_to(target).as_posix() for path in _files_under(target)}
    if found != set(expected):
        return False
    try:
        return all(
            filecmp.cmp(target / rel, source, shallow=False)
            for rel, source in expected.items()
        )
    except FileNotFoundError:  # swapped out by another run mid-walk
        return False


def build(tmp: Path, expected: dict[str, Path]) -> None:
    """Copy every expected file into `tmp`. File by file, not `copytree`: the
    layout renames the paths."""
    for rel, source in expected.items():
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)


def _rename_unless_taken(src: Path, dst: Path) -> None:
    """`os.rename`, except that a `dst` another run has just placed is left
    as it is."""
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno not in _TAKEN:
            raise


def _place(tmp: Path, target: Path, expected: dict[str, Path], tag: str) -> None:
    """Put the built `tmp` folder at `target`. `tmp` is gone afterwards.

    Concurrent runs on the same version can place or swap the folder at any
    step. A `target` that already matches is kept, so the sessions of a run
    that placed it keep their working folder. A rename that fails because
    another run got there first leaves that run's copy, which the caller
    checks. Any other failure is raised, with a swapped-out copy put back."""
    try:
        if matches(target, expected):
            return
        if not target.exists():
            _rename_unless_taken(tmp, target)
            return
        old = target.parent / f".old-{tag}"
        try:
            os.rename(target, old)
        except FileNotFoundError:
            # Another run moved it away to swap in its own copy.
            _rename_unless_taken(tmp, target)
            return
        try:
            os.rename(tmp, target)
        except OSError as exc:
            if exc.errno not in _TAKEN:
                with contextlib.suppress(OSError):
                    os.rename(old, target)  # the last copy, back in place
                raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_for_run(agent_cli: str, agents_root: Path, version: str) -> Path:
    """Make `agents_root/<agent_cli>/<version>/` hold the package's skills and
    instructions in the CLI's layout, and return it. A folder that already
    matches is used as is. Otherwise a fresh copy is built in a temporary
    folder beside it and renamed into place, so a failed build leaves nothing
    half-built where sessions read."""
    expected = expected_files(agent_cli)
    target = agents_root / agent_cli / version
    try:
        if matches(target, expected):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        tag = f"{version}-{os.getpid()}-{secrets.token_hex(4)}"
        tmp = target.parent / f".tmp-{tag}"
        try:
            build(tmp, expected)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        _place(tmp, target, expected, tag)
        placed = matches(target, expected)
    except OSError as exc:
        raise InstallError(
            f"cannot install the agent files into {target} ({exc}).\n"
            "fix: make the folder writable and free some disk space, then"
            " start the run again."
        ) from exc
    if not placed:
        raise InstallError(
            f"the agents folder {target} does not hold the package's files"
            " after install.\n"
            "fix: start the run again; if this repeats, delete the folder."
        )
    return target


# ---- `harness-tuner-install-agents --dest DIR` ----


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


def install_into(agent_cli: str, dest: Path) -> list[Path]:
    """Install the skills for `agent_cli` under `dest`, and for `claude` the
    `kiln_api_mcp` server. Returns what was written. A skill of the same name
    is replaced; other skills under `dest` are left alone."""
    source = source_dir()
    layout = AGENT_CLIS[agent_cli]
    written: list[Path] = []
    for skill in sorted(p for p in (source / "skills").iterdir() if p.is_dir()):
        target = dest / layout.skills / skill.name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill, target, ignore=shutil.ignore_patterns(*NOT_AGENT_FILES))
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
            "Copy the agents' skills from the package into a folder of your"
            " own, in the layout one agent CLI reads, for interactive"
            " sessions. For claude, also add the kiln_api_mcp server. A run"
            " does not need this: it installs its own."
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
        required=True,
        help="the folder to install into, for example your own project",
    )
    args = parser.parse_args(argv)
    agent_clis = list(AGENT_CLIS) if args.agent_cli == "all" else [args.agent_cli]
    dest = args.dest.expanduser().resolve()
    try:
        for agent_cli in agent_clis:
            install_into(agent_cli, dest)
            line = f"{agent_cli}: skills in {dest / AGENT_CLIS[agent_cli].skills}"
            if agent_cli == "claude":
                line += f", kiln_api_mcp in {dest / '.mcp.json'}"
            print(line)
    except InstallError as exc:
        print(f"harness-tuner-install-agents: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
