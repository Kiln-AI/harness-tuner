"""The packaging smoke test: the built package works with no checkout.

The offline suite runs from the checkout, so it cannot catch a package that
works only from one. This script checks what the sdist and the wheel hold,
installs the wheel as a uv tool into temporary folders, and makes a real run
from an empty folder. A fake `claude` (`fake_claude.py`) stands in for Claude
Code, and a fake Kiln (`fake_kiln_server.py`) answers the reads of the
start-up checks from the run's copy of the demo project. The run gets through
the start-up checks, the agent-file install, the agent CLI check, the lock,
the run folder, the rendered configs, the split check and the pre-flight,
where the installed gateway pings the fake Kiln. It stops with exit code 1 at
its first write to Kiln, which the fake refuses. The checks then look at where
every file went. The run is made twice: with `KILN_HARNESS_TUNER_HOME` set,
and without it.

    uv build
    python tests/packaging/smoke_installed_wheel.py --dist dist

`--dist` is the folder `uv build` wrote, with one sdist and one wheel in it.
The script and its fakes are stdlib only and never import `kiln_harness_tuner`:
the installed copy is what they test. The tool is installed on the interpreter that
runs the script. It needs `uv`, `uvx` and `git` on `PATH`. Each passed check
prints one `ok:` line. The first failed check stops the script with a message
that names it.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from fake_kiln_server import FakeKiln

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")
# The demo project ships in the package, for `--demo-project`.
EXAMPLE_PROJECT = (
    REPO_ROOT / "src" / "kiln_harness_tuner" / "demo_project" / "Forum Moderation"
)
PROJECT_ID = "424242975950"
TASK_ID = "424242783061"
RUN_TIMEOUT_SECS = 600
# An install with a cold uv cache downloads every dependency.
COMMAND_TIMEOUT_SECS = 600

TOOL_NAME = "harness-tuner"
COMMANDS = (
    "harness-tuner",
    "harness-tuner-install-agents",
    "harness-tuner-gateway-mcp",
    "harness-tuner-skill-installer-mcp",
    "kiln_api_mcp",
)
# The sdist is the package source and its metadata. Hatchling adds
# `.gitignore` itself, so that a wheel built from the sdist keeps the same
# exclusions. A licence file joins them when the repository gets one.
SDIST_ENTRIES = frozenset(
    {"src", "pyproject.toml", "README.md", "PKG-INFO", ".gitignore"}
)
INSTRUCTIONS = "kiln_harness_tuner/agent_files/AGENTS.md"
WHEEL_FILES = (
    INSTRUCTIONS,
    "kiln_harness_tuner/agent_files/skills/kiln-api/SKILL.md",
    "kiln_harness_tuner/agent_files/claude/.mcp.json",
)
# Package data: the wheel holds every file git tracks under these folders.
PACKAGE_DATA = (
    "src/kiln_harness_tuner/agent_files",
    "src/kiln_harness_tuner/agents",
    "src/kiln_harness_tuner/demo_project",
)
WHEEL_PACKAGES = frozenset({"kiln_harness_tuner", "kiln_api_mcp"})
GATEWAY_SERVER = "kiln_harness_tuner_gateway"
GATEWAY_ARGS = ["-P", "-m", "kiln_harness_tuner.gateway.server"]
CLI_CHECK_WORD = "KILN-HARNESS-TUNER-READY"
TAIL_LINES = 40


def fail(check: str, detail: str) -> NoReturn:
    raise SystemExit(f"FAILED: {check}: {detail}")


def ok(check: str) -> None:
    print(f"ok: {check}", flush=True)


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:]) or "(nothing)"


def output_of(proc: subprocess.CompletedProcess[str]) -> str:
    return (
        f"--- stdout (tail)\n{tail(proc.stdout)}\n"
        f"--- stderr (tail)\n{tail(proc.stderr)}"
    )


def run(
    check: str,
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    expect: int = 0,
    timeout: int = COMMAND_TIMEOUT_SECS,
) -> subprocess.CompletedProcess[str]:
    """Run argv, and fail `check` when it does not exit `expect`."""
    try:
        proc = subprocess.run(
            argv,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(check, f"`{shlex.join(argv)}` ran for more than {timeout} s")
    if proc.returncode != expect:
        fail(
            check,
            f"`{shlex.join(argv)}` exited {proc.returncode}, not {expect}\n"
            + output_of(proc),
        )
    return proc


def git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        fail("git", "git is not on PATH; the script reads the checkout with it")
    if proc.returncode != 0:
        fail("git", f"`git {shlex.join(args)}` exited {proc.returncode}: {proc.stderr}")
    return proc.stdout


def checkout_status() -> str:
    """The checkout's status, with ignored files. `--ignored`, because the
    old run state (`runs/`, `traces/`, `reports/`) is git-ignored: a plain
    status would not show a run that wrote it."""
    return git("status", "--porcelain", "--ignored")


def built_files(dist: Path) -> tuple[Path, Path, str]:
    """The sdist, the wheel, and the version in the wheel's file name."""
    sdists = sorted(dist.glob("*.tar.gz"))
    wheels = sorted(dist.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        fail(
            "dist",
            f"{dist} holds {len(sdists)} sdist(s) and {len(wheels)} wheel(s),"
            " not one of each; build into an empty folder",
        )
    # <name>-<version>-<python tag>-<abi tag>-<platform tag>.whl
    version = wheels[0].name.split("-")[1]
    return sdists[0], wheels[0], version


def check_sdist(sdist: Path) -> None:
    with tarfile.open(sdist) as tar:
        names = [PurePosixPath(name) for name in tar.getnames()]
    root = sdist.name.removesuffix(".tar.gz")
    outside = sorted(str(name) for name in names if name.parts[0] != root)
    if outside:
        fail("sdist", f"{sdist.name} has entries outside {root}/: {outside[:5]}")
    entries = {name.parts[1] for name in names if len(name.parts) > 1}
    extra = sorted(
        entry
        for entry in entries
        if entry not in SDIST_ENTRIES and not entry.startswith("LICENSE")
    )
    if extra:
        fail(
            "sdist",
            f"{sdist.name} ships {extra}; only {sorted(SDIST_ENTRIES)} and"
            " LICENSE* belong in it",
        )
    agent_files = PurePosixPath(root, "src", "kiln_harness_tuner", "agent_files")
    if agent_files / "AGENTS.md" not in names:
        fail("sdist", f"{sdist.name} has no {agent_files / 'AGENTS.md'}")
    ok(f"sdist holds only {', '.join(sorted(entries))}, with the agent files")


def check_wheel(wheel: Path, version: str) -> bytes:
    """Check the wheel's contents. Returns the loop-agent instructions it
    ships, which a run installs as the agents folder's CLAUDE.md."""
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        instructions = archive.read(INSTRUCTIONS) if INSTRUCTIONS in names else b""
    tracked = [
        path.removeprefix("src/")
        for path in git("ls-files", "-z", "--", *PACKAGE_DATA).split("\0")
        if path
    ]
    missing = sorted({*WHEEL_FILES, *tracked} - names)
    if missing:
        fail("wheel", f"{wheel.name} lacks {len(missing)} file(s): {missing[:10]}")
    tops = {name.split("/")[0] for name in names}
    allowed = {*WHEEL_PACKAGES, f"harness_tuner-{version}.dist-info"}
    extra = sorted(tops - allowed)
    if extra:
        fail("wheel", f"{wheel.name} ships {extra} (no tests/, no examples/)")
    ok(
        "wheel holds the agent files, role templates and demo project"
        f" ({len(tracked)} tracked files) and only {', '.join(sorted(tops))}"
    )
    return instructions


def which(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        fail("tools", f"`{name}` is not on PATH")
    return path


def base_env(uv: str) -> dict[str, str]:
    """The environment every command starts from.

    - `UV_CACHE_DIR` stays the outer one, so the dependencies come from the
      runner's cache while `HOME` is a temporary folder.
    - `UV_PYTHON` is this interpreter: the tool runs on the Python version
      under test, and uv downloads none.
    - `KILN_HARNESS_TUNER_HOME` is set per run, and `PYTHONPATH` and
      `PYTHONHOME` are dropped: either could put the checkout's code in
      front of the installed copy."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"KILN_HARNESS_TUNER_HOME", "PYTHONPATH", "PYTHONHOME"}
    }
    if not env.get("UV_CACHE_DIR"):
        cache = subprocess.run([uv, "cache", "dir"], capture_output=True, text=True)
        if cache.returncode != 0 or not cache.stdout.strip():
            fail("uv", f"`uv cache dir` failed: {cache.stderr}")
        env["UV_CACHE_DIR"] = cache.stdout.strip()
    env["UV_PYTHON"] = sys.executable
    return env


@dataclass(frozen=True)
class Tool:
    bin: Path  # the console scripts
    python: Path  # the tool's own interpreter

    def command(self, name: str) -> str:
        return str(self.bin / name)


def install_tool(uv: str, wheel: Path, tmp: Path, env: dict[str, str]) -> Tool:
    tool = Tool(bin=tmp / "bin", python=tmp / "tools" / TOOL_NAME / "bin" / "python")
    install_env = {
        **env,
        "HOME": str(tmp / "home"),
        "UV_TOOL_DIR": str(tmp / "tools"),
        "UV_TOOL_BIN_DIR": str(tool.bin),
    }
    run("install", [uv, "tool", "install", str(wheel)], env=install_env, cwd=tmp)
    absent = [
        str(path)
        for path in (tool.python, *(tool.bin / name for name in COMMANDS))
        if not path.exists()
    ]
    if absent:
        fail("install", f"`uv tool install` did not make {absent}")
    ok(f"uv tool install {wheel.name}: {len(COMMANDS)} commands in {tool.bin}")
    return tool


def check_commands(
    tool: Tool, uvx: str, wheel: Path, tmp: Path, env: dict[str, str]
) -> None:
    cwd = tmp / "cwd"
    env = {**env, "HOME": str(tmp / "home")}
    for argv in (
        [tool.command("harness-tuner"), "--help"],
        [tool.command("harness-tuner-install-agents"), "--help"],
        [uvx, "--from", str(wheel), "harness-tuner", "--help"],
    ):
        proc = run("--help", argv, env=env, cwd=cwd)
        if "usage:" not in proc.stdout:
            fail("--help", f"`{shlex.join(argv)}` printed no usage\n{output_of(proc)}")
    ok("harness-tuner --help, harness-tuner-install-agents --help and uvx --from")

    dest = tmp / "dest"
    check = "install-agents --dest"
    run(
        check,
        [
            tool.command("harness-tuner-install-agents"),
            "--agent-cli",
            "claude",
            "--dest",
            str(dest),
        ],
        env=env,
        cwd=cwd,
    )
    if not (dest / ".claude" / "skills" / "kiln-api" / "SKILL.md").is_file():
        fail(check, f"no .claude/skills/kiln-api/SKILL.md in {dest}")
    servers = read_json(check, dest / ".mcp.json").get("mcpServers", {})
    if "kiln_api_mcp" not in servers:
        fail(check, f"{dest / '.mcp.json'} has no kiln_api_mcp server")
    written = [name for name in ("CLAUDE.md", "AGENTS.md") if (dest / name).exists()]
    if written:
        fail(check, f"it wrote {written}; it writes no instructions file")
    ok("harness-tuner-install-agents --agent-cli claude --dest: skills and MCP config")


def read_json(check: str, path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(check, f"cannot read {path} as JSON: {exc}")


def tree(root: Path) -> set[Path]:
    """Every file and folder under root, relative to it."""
    return {path.relative_to(root) for path in root.rglob("*")}


def write_fake_claude(fakebin: Path, python: Path) -> None:
    fakebin.mkdir(exist_ok=True)
    claude = fakebin / "claude"
    claude.write_text(
        f"#!{python}\n" + FAKE_CLAUDE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    claude.chmod(0o755)


@dataclass(frozen=True)
class DryRun:
    label: str  # names the run in every check
    home: Path  # HOME for the run
    state: Path  # where the state folder must land
    override: bool  # whether KILN_HARNESS_TUNER_HOME is set to `state`


def dry_preflight(
    dry: DryRun,
    tool: Tool,
    version: str,
    instructions: bytes,
    tmp: Path,
    env: dict[str, str],
    checkout_before: str,
) -> None:
    """One run that stops at its first write to Kiln, after the pre-flight,
    and the checks on where its files went."""

    def check(name: str) -> str:
        return f"[{dry.label}] {name}"

    work = tmp / f"run-{dry.label}"
    project = work / "project" / EXAMPLE_PROJECT.name
    shutil.copytree(EXAMPLE_PROJECT, project)
    fake_log = work / "fake.jsonl"
    write_fake_claude(tmp / "fakebin", tool.python)
    dry.home.mkdir(exist_ok=True)
    run_env = {
        **env,
        "HOME": str(dry.home),
        "PATH": os.pathsep.join(
            [str(tmp / "fakebin"), str(tool.bin), env.get("PATH", "")]
        ),
        "FAKE_CLAUDE_LOG": str(fake_log),
    }
    if dry.override:
        run_env["KILN_HARNESS_TUNER_HOME"] = str(dry.state)
    home_before = tree(dry.home)
    argv = [
        tool.command("harness-tuner"),
        "--project-path",
        str(project),
        "--project-id",
        PROJECT_ID,
        "--task-id",
        TASK_ID,
        "--kiln-host",
        "<fake Kiln>",
        "--no-progress",
    ]
    with FakeKiln(project) as kiln:
        argv[argv.index("<fake Kiln>")] = kiln.host
        proc = run(
            check("run"),
            argv,
            env=run_env,
            cwd=tmp / "cwd",
            expect=1,
            timeout=RUN_TIMEOUT_SECS,
        )
    writes = [request for request in kiln.requests if not request.startswith("GET ")]
    runs = dry.state / "runs"
    if f"files in {runs}/" not in proc.stdout:
        fail(check("start line"), f"no run folder under {runs}/\n{output_of(proc)}")
    # The first write is the last request: the fake refuses it, and the run
    # stops there.
    if len(writes) != 1 or kiln.requests[-1] != writes[0]:
        fail(
            check("exit"),
            f"the run made {len(writes)} write(s) to Kiln, not one last one:"
            f" {kiln.requests[-5:]}\n{output_of(proc)}",
        )
    for expected in ("Kiln returned an error", writes[0], "  Run folder: "):
        if expected not in proc.stderr:
            fail(check("exit"), f"stderr lacks {expected!r}\n{output_of(proc)}")
    ok(check(f"exit code 1 at the first write to Kiln ({writes[0]}), after the start"))

    agents_dir = dry.state / "agents" / "claude" / version
    if not (agents_dir / ".claude" / "skills" / "kiln-api" / "SKILL.md").is_file():
        fail(check("agents folder"), f"no .claude/skills/kiln-api in {agents_dir}")
    claude_md = agents_dir / "CLAUDE.md"
    if not claude_md.is_file() or claude_md.read_bytes() != instructions:
        fail(check("agents folder"), f"{claude_md} is not the wheel's {INSTRUCTIONS}")
    if (agents_dir / ".mcp.json").exists():
        fail(check("agents folder"), f"a run wrote {agents_dir / '.mcp.json'}")
    ok(check(f"agents folder {agents_dir}: skills and CLAUDE.md, no .mcp.json"))

    calls = (
        [json.loads(line) for line in fake_log.read_text(encoding="utf-8").splitlines()]
        if fake_log.exists()
        else []
    )
    cli_checks = [call for call in calls if CLI_CHECK_WORD in call["prompt"]]
    preflights = [call for call in calls if "--mcp-config" in call["argv"]]
    if not cli_checks or not preflights:
        fail(
            check("fake claude"),
            f"{len(calls)} call(s): {len(cli_checks)} CLI check(s) and"
            f" {len(preflights)} pre-flight(s); one of each was expected",
        )
    elsewhere = sorted(
        {call["cwd"] for call in calls if Path(call["cwd"]).resolve() != agents_dir}
    )
    if elsewhere:
        fail(check("session cwd"), f"sessions ran in {elsewhere}, not {agents_dir}")
    ok(check(f"{len(calls)} claude sessions, every one in the agents folder"))

    run_dirs = [p for p in runs.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(run_dirs) != 1:
        fail(check("run folder"), f"{runs} holds {len(run_dirs)} run folders, not 1")
    run_dir = run_dirs[0]
    if f"  Run folder: {run_dir}\n" not in proc.stderr:
        fail(check("run folder"), f"stderr does not name {run_dir}\n{output_of(proc)}")
    absent = [
        name
        for name in ("settings.json", "settings.readonly.json", "mcp_config.json")
        if not (run_dir / name).is_file()
    ]
    train_set = run_dir / "train_set.json"
    if not train_set.is_file() or train_set.stat().st_size == 0:
        absent.append("a non-empty train_set.json")
    if absent:
        fail(check("run folder"), f"{run_dir} lacks {absent}")
    lock = runs / f".lock-{PROJECT_ID}-{TASK_ID}"
    if not lock.is_file():
        fail(check("run lock"), f"no {lock}")
    ok(check(f"run folder {run_dir}, its configs, train set and lock"))

    config = read_json(check("gateway"), run_dir / "mcp_config.json")
    gateway = config.get("mcpServers", {}).get(GATEWAY_SERVER, {})
    if gateway.get("command") != str(tool.python):
        fail(
            check("gateway"),
            f"the gateway runs {gateway.get('command')!r}, not {tool.python}",
        )
    if gateway.get("args", [])[: len(GATEWAY_ARGS)] != GATEWAY_ARGS:
        fail(
            check("gateway"),
            f"its args start {gateway.get('args', [])[:3]}, not {GATEWAY_ARGS}",
        )
    settings = read_json(check("sandbox"), run_dir / "settings.json")
    deny_write = settings.get("sandbox", {}).get("filesystem", {}).get("denyWrite")
    if deny_write != [str(dry.state)]:
        fail(check("sandbox"), f"denyWrite is {deny_write!r}, not [{dry.state}]")
    ok(check("the gateway runs the tool interpreter with -P -m; denyWrite the state"))

    preflight = preflights[-1]
    if "ping" not in preflight.get("tools", []):
        fail(
            check("gateway tools"),
            f"the gateway listed {preflight.get('tools')!r}"
            f" (error: {preflight.get('error')!r})\n" + output_of(proc),
        )
    if '"ok": true' not in str(preflight.get("ping")):
        fail(
            check("gateway ping"),
            f"the gateway's ping answered {preflight.get('ping')!r}"
            f" (error: {preflight.get('error')!r})\n" + output_of(proc),
        )
    ok(check(f"the gateway listed {preflight['tools']} and its ping reached Kiln"))

    if any((tmp / "cwd").iterdir()):
        fail(check("cwd"), f"the run wrote {sorted((tmp / 'cwd').iterdir())}")
    tuner_home = Path(".kiln_ai", "harness_tuner")
    # Kiln may add its own files under ~/.kiln_ai. The default state folder
    # is allowed only when it is this run's state folder.
    stray = sorted(
        str(path)
        for path in tree(dry.home) - home_before
        if path.parts[0] != ".kiln_ai"
        or (dry.override and path.parts[:2] == tuner_home.parts)
    )
    if stray:
        fail(check("HOME"), f"the run wrote under {dry.home}: {stray[:10]}")
    if dry.override and (dry.home / tuner_home).exists():
        fail(check("HOME"), f"{dry.home / tuner_home} exists despite the override")
    after = checkout_status()
    if after != checkout_before:
        fail(
            check("checkout"),
            f"git status changed in {REPO_ROOT}:\n"
            f"--- before\n{checkout_before}--- after\n{after}",
        )
    ok(check("nothing written to the cwd, HOME or the checkout"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dist", type=Path, required=True, help="the folder `uv build` wrote"
    )
    args = parser.parse_args(argv)
    print(f"smoke: Python {sys.version.split()[0]} at {sys.executable}", flush=True)
    uv, uvx = which("uv"), which("uvx")
    if not EXAMPLE_PROJECT.is_dir():
        fail("example", f"no Kiln project at {EXAMPLE_PROJECT}")
    checkout_before = checkout_status()
    sdist, wheel, version = built_files(args.dist.resolve())
    check_sdist(sdist)
    instructions = check_wheel(wheel, version)
    with tempfile.TemporaryDirectory(
        prefix="harness-tuner-smoke-", ignore_cleanup_errors=True
    ) as name:
        tmp = Path(name).resolve()
        (tmp / "cwd").mkdir()
        (tmp / "home").mkdir()
        env = base_env(uv)
        tool = install_tool(uv, wheel, tmp, env)
        check_commands(tool, uvx, wheel, tmp, env)
        for dry in (
            DryRun(
                label="override",
                home=tmp / "home",
                state=tmp / "state",
                override=True,
            ),
            DryRun(
                label="default",
                home=tmp / "home2",
                state=tmp / "home2" / ".kiln_ai" / "harness_tuner",
                override=False,
            ),
        ):
            dry_preflight(dry, tool, version, instructions, tmp, env, checkout_before)
    print("smoke: passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
