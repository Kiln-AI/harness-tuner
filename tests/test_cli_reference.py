"""`docs/cli.md`, held to the argparse definitions of the public commands.

The CLI reference is the one place that lists flags. A flag it leaves out is
a flag nobody uses, and a default it gets wrong is one a reader acts on. So
the parser of each public command is read here and compared with the page:

- **Every console script is named.** Each script in `[project.scripts]`
  appears on the page in backticks. The internal ones (the MCP servers) get
  a few lines and no flag table, because a person does not run them.
- **Every public command has one section.** It owns the part of
  `docs/cli.md` under a level-2 heading that is only its name in backticks
  (`## `harness-tuner``), up to the next level-2 heading.
- **Every flag has one row in its command's section, with its default.** A
  flag row is a table row whose FIRST cell is exactly one backticked long
  flag. Its second cell starts with the default, as `documented_default`
  writes it. Prose and example command lines are not rows, so the page can
  name a flag in a sentence.
- **A flag row lives nowhere else.** Not between the command sections of
  `docs/cli.md`, and not in another document under `docs/` or in
  `README.md`: a second table is a second description, which will disagree.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from kiln_harness_tuner import cli, install_agents
from kiln_harness_tuner.cli import REMOVED_FLAGS
from kiln_harness_tuner.onboarding.questions import number_text

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_DOC = REPO_ROOT / "docs" / "cli.md"
EXAMPLE_POLICY = REPO_ROOT / "examples" / "model-policy.json"
MAIN_COMMAND = "harness-tuner"

# The public commands, each with the parser its `main()` parses with.
PUBLIC_COMMANDS = {
    MAIN_COMMAND: cli.build_parser,
    "harness-tuner-install-agents": install_agents.build_parser,
}

# The first cell of a table row, when that cell is nothing but one backticked
# long flag. Anchored at the leading pipe, so a flag in a LATER cell is not a
# row. The character class is every character argparse allows in a long
# option; `FLAG_SPELLING_RE` is the same set, and turns a flag this pattern
# cannot express into a message instead of an unsatisfiable failure.
FLAG_ROW_RE = re.compile(r"^\|\s*`(--[A-Za-z0-9][A-Za-z0-9_.-]*)`\s*\|")
FLAG_SPELLING_RE = re.compile(r"^--[A-Za-z0-9][A-Za-z0-9_.-]*$")
COMMAND_HEADING_RE = re.compile(r"^## `([^`]+)`\s*$")
CELL_BORDER_RE = re.compile(r"(?<!\\)\|")
FENCE = "```"


@dataclass(frozen=True)
class FlagRow:
    section: str | None  # the command heading above it; None between commands
    flag: str
    default: str
    line: int


def console_scripts() -> dict[str, str]:
    """`[project.scripts]`: command name → `module:function`."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    return pyproject["project"]["scripts"]


def long_options(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    """Every long option the parser takes, argparse's own `--help` left out."""
    return {
        option: action
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }


def documented_default(action: argparse.Action) -> str:
    """The text a flag's Default cell starts with. `docs/cli.md` explains
    each form above its first table."""
    default = action.default
    if action.required:
        return "required"
    if action.nargs == 0:
        assert default is False, (
            f"{action.option_strings}: a switch whose default is {default!r}."
            " Give documented_default a form for it, and docs/cli.md a line."
        )
        return "off"
    if default is None or default == []:
        return "not set"
    if default == "":
        return "empty"
    if isinstance(default, float):
        return f"`{number_text(default)}`"
    if isinstance(default, int | str):
        return f"`{default}`"
    raise AssertionError(
        f"{action.option_strings}: no documented form for the default"
        f" {default!r}. Add one to documented_default, and to docs/cli.md."
    )


def starts_with_default(cell: str, expected: str) -> bool:
    """`cell` is `expected`, or `expected` and then more text that does not
    run on from it: `720` passes `` `720` (12 hours) `` and fails `` `7200` ``,
    and `off` fails `offline`."""
    return re.match(re.escape(expected) + r"(?![\w`])", cell) is not None


def flag_rows(path: Path) -> list[FlagRow]:
    """Every flag row in `path`, with the command section it sits in. Lines in
    code fences are neither headings nor rows."""
    rows: list[FlagRow] = []
    section: str | None = None
    in_fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith(FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## "):
            heading = COMMAND_HEADING_RE.match(line)
            section = heading.group(1) if heading else None
            continue
        row = FLAG_ROW_RE.match(line)
        if row:
            cells = CELL_BORDER_RE.split(line)
            default = cells[2].strip() if len(cells) > 3 else ""
            rows.append(FlagRow(section, row.group(1), default, lineno))
    return rows


def command_headings() -> list[str]:
    return [
        heading.group(1)
        for line in CLI_DOC.read_text(encoding="utf-8").splitlines()
        if (heading := COMMAND_HEADING_RE.match(line))
    ]


def section_text(command: str) -> str:
    """The lines of `docs/cli.md` under `command`'s heading."""
    lines = CLI_DOC.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"## `{command}`") + 1
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_every_console_script_is_named():
    scripts = sorted(console_scripts())
    assert scripts, "pyproject.toml lists no console scripts"
    text = CLI_DOC.read_text(encoding="utf-8")
    unnamed = [name for name in scripts if f"`{name}`" not in text]
    assert not unnamed, (
        f"console scripts docs/cli.md does not name: {unnamed}. Give a public"
        " command a section, and an internal one a few lines under"
        " Internal commands."
    )


def test_every_public_command_has_one_section():
    not_installed = sorted(set(PUBLIC_COMMANDS) - set(console_scripts()))
    assert not not_installed, (
        f"public commands pyproject.toml does not install: {not_installed}."
        " A renamed command needs its new name in PUBLIC_COMMANDS."
    )
    headings = command_headings()
    missing = [name for name in PUBLIC_COMMANDS if name not in headings]
    unknown = sorted(set(headings) - set(PUBLIC_COMMANDS))
    duplicated = sorted({name for name in headings if headings.count(name) > 1})
    assert not missing, (
        f"public commands with no section in docs/cli.md: {missing}. Add a"
        " `## `<command>`` heading and a flag table under it."
    )
    assert not unknown, (
        f"docs/cli.md has command sections for {unknown}, which are not public"
        " commands. A renamed command leaves its section, and an internal"
        " command goes under Internal commands."
    )
    assert not duplicated, f"commands with two sections in docs/cli.md: {duplicated}"


@pytest.mark.parametrize("command", sorted(PUBLIC_COMMANDS))
def test_every_flag_is_documented_with_its_default(command: str):
    actions = long_options(PUBLIC_COMMANDS[command]())
    rows = [row for row in flag_rows(CLI_DOC) if row.section == command]
    documented = [row.flag for row in rows]

    inexpressible = sorted(f for f in actions if not FLAG_SPELLING_RE.match(f))
    assert not inexpressible, (
        f"{command} flags this test cannot read in a table row: {inexpressible}."
        " Widen FLAG_ROW_RE and FLAG_SPELLING_RE to cover them."
    )
    missing = sorted(set(actions) - set(documented))
    unknown = sorted(set(documented) - set(actions))
    duplicated = sorted({flag for flag in documented if documented.count(flag) > 1})
    assert not missing, (
        f"{command} flags missing from docs/cli.md: {missing}. Add one row per"
        f" flag under `## `{command}``: the flag alone in the first cell, its"
        " default in the second."
    )
    assert not unknown, (
        f"docs/cli.md documents {command} flags its parser does not define:"
        f" {unknown}. A renamed or removed flag leaves its row behind."
    )
    assert not duplicated, f"{command} flags with two rows in docs/cli.md: {duplicated}"

    wrong = [
        f"line {row.line}: {row.flag} has default {row.default!r}, expected it"
        f" to start with {documented_default(actions[row.flag])!r}"
        for row in rows
        if not starts_with_default(row.default, documented_default(actions[row.flag]))
    ]
    assert not wrong, "docs/cli.md has wrong defaults:\n" + "\n".join(wrong)


def test_no_flag_row_outside_a_command_section():
    stray = [
        f"docs/cli.md:{row.line}: {row.flag}"
        for row in flag_rows(CLI_DOC)
        if row.section is None
    ]
    others = sorted(set((REPO_ROOT / "docs").rglob("*.md")) - {CLI_DOC})
    stray += [
        f"{path.relative_to(REPO_ROOT)}:{row.line}: {row.flag}"
        for path in [REPO_ROOT / "README.md", *others]
        for row in flag_rows(path)
    ]
    assert not stray, (
        "flag rows outside the command sections of docs/cli.md:\n"
        + "\n".join(stray)
        + "\nMove each into its command's section of docs/cli.md, or link to it."
    )


def test_the_removed_flags_are_named():
    """An old command line stops with a message. The reference names each
    removed flag, so a reader who searches for one finds what replaced it."""
    text = section_text(MAIN_COMMAND)
    unnamed = sorted(flag for flag in REMOVED_FLAGS if f"`{flag}`" not in text)
    assert not unnamed, f"removed flags docs/cli.md does not name: {unnamed}"


def test_the_example_policy_is_the_example_file():
    """The page shows `examples/model-policy.json` whole. Both are JSON, so
    they are compared as JSON: a reflowed line is not a difference."""
    example = json.loads(EXAMPLE_POLICY.read_text(encoding="utf-8"))
    blocks = re.findall(
        r"^```\n(.*?)^```$", CLI_DOC.read_text(encoding="utf-8"), re.M | re.S
    )
    shown = []
    for block in blocks:
        try:
            shown.append(json.loads(block))
        except ValueError:
            continue
    assert example in shown, (
        "docs/cli.md does not show examples/model-policy.json as it is."
        " Copy the file into the page's example policy block."
    )


# ---- the helpers, on cases the page does not have today ----


@pytest.mark.parametrize(
    ("cell", "expected", "matches"),
    [
        ("`720` (12 hours)", "`720`", True),
        ("`720`", "`720`", True),
        ("`7200`", "`720`", False),
        ("`72`", "`720`", False),
        ("off", "off", True),
        ("offline", "off", False),
        ("not set: the only project in Kiln", "not set", True),
        ("`0` (off)", "`0`", True),
        ("required", "not set", False),
    ],
)
def test_starts_with_default(cell, expected, matches):
    assert starts_with_default(cell, expected) is matches


def test_flag_rows_reads_sections_and_skips_fences(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "| `--before` | `1` | between commands |\n"
        "## `tool`\n"
        "### A group\n"
        "| Flag | Default | What it does |\n"
        "|---|---|---|\n"
        "| `--size` | `4` | a size, with a \\| pipe |\n"
        "| see `--size` | not a row | |\n"
        "```\n"
        "## `not-a-heading`\n"
        "| `--in-fence` | `0` | |\n"
        "```\n"
        "## Something else\n"
        "| `--after` | off | |\n",
        encoding="utf-8",
    )
    assert flag_rows(page) == [
        FlagRow(None, "--before", "`1`", 1),
        FlagRow("tool", "--size", "`4`", 6),
        FlagRow(None, "--after", "off", 13),
    ]
