"""What a start shows before the run: the `Ready to start` summary, and the
command that runs the same start again with no questions.

Both read the command line as argparse does, through the parser's own
table of long options (`OptionInfo`, recorded by cli's parser), so the
start knows which flags were given and can drop the ones it sets itself.
"""

from __future__ import annotations

import argparse
import itertools
import shlex
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kiln_harness_tuner.onboarding.choose import Selection
from kiln_harness_tuner.onboarding.keys import Baseline
from kiln_harness_tuner.onboarding.questions import (
    Answers,
    advanced_changes,
    agents_text,
    budget_default,
    duration_text,
    guidance_text,
    limits_budget_text,
    number_text,
    objective_text,
)


@dataclass(frozen=True)
class OptionInfo:
    """What the parser knows of one long option."""

    dest: str
    takes_value: bool
    default: Any = None


# The dests the start fills in, which the rerun command passes itself.
SET_BY_START = frozenset(
    {
        "project_id",
        "task_id",
        "project_path",
        "demo_project",
        "objective",
        "guidance",
        "agent_cli",
        "budget_usd",
        "timeout_mins",
        "model_policy",
        "min_effect",
        "max_concurrent_evals",
        "max_concurrent_agents",
        "noise_check",
        "no_interaction",
    }
)

SUMMARY_HEADING = "Ready to start"
RERUN_HEADING = "To run this again with the same answers and no questions:"
SUMMARY_LABEL_WIDTH = 12

# The advanced flags the rerun command passes when they aren't the
# defaults, each with how its value is written.
ADVANCED_FLAGS: tuple[tuple[str, str, Callable[[Any], str]], ...] = (
    ("model_policy", "--model-policy", str),
    ("min_effect", "--min-effect", number_text),
    ("max_concurrent_evals", "--max-concurrent-evals", str),
    ("max_concurrent_agents", "--max-concurrent-agents", str),
    ("noise_check", "--noise-check", str),
)


def _long_option(token: str, long_options: Collection[str]) -> str | None:
    """The long option a `--x` or `--x=v` token names, the way argparse's
    `allow_abbrev` reads it: the option it equals, else the one option it
    is a prefix of. `None` when it names none, or several."""
    name = token.split("=", 1)[0]
    if name in long_options:
        return name
    matches = {option for option in long_options if option.startswith(name)}
    return matches.pop() if len(matches) == 1 else None


def _read(
    argv: Sequence[str], option_dests: Mapping[str, OptionInfo]
) -> Iterator[tuple[list[str], OptionInfo | None]]:
    """`argv` in groups: each option with its value, and each other token on
    its own, with the option's `OptionInfo` (`None` for a token that names
    none). An option that takes a value takes the next token, unless it is
    written `--x=v`, as argparse reads it. The scan stops at `--`: the
    parser takes no positional arguments, so nothing can follow it."""
    tokens = iter(argv)
    for token in tokens:
        if token == "--":
            return
        option = _long_option(token, option_dests) if token.startswith("--") else None
        info = option_dests.get(option) if option is not None else None
        group = [token]
        if info is not None and info.takes_value and "=" not in token:
            group += itertools.islice(tokens, 1)
        yield group, info


def given_dests(
    argv: Sequence[str], option_dests: Mapping[str, OptionInfo]
) -> frozenset[str]:
    """The dests of the options on the command line, each given even when
    its value is the default."""
    return frozenset(info.dest for _group, info in _read(argv, option_dests) if info)


def parser_defaults(option_dests: Mapping[str, OptionInfo]) -> Answers:
    """The answers the flags' own defaults give."""
    defaults = {info.dest: info.default for info in option_dests.values()}
    return Answers.from_args(argparse.Namespace(**defaults))


def _baseline_text(baseline: Baseline) -> str:
    text = f'run config "{baseline.name}"'
    if baseline.model:
        text += f": {baseline.model}"
    if baseline.provider:
        text += f" on {baseline.provider}"
    return text


def summary_lines(
    selection: Selection,
    answers: Answers,
    baseline: Baseline | None,
    defaults: Answers,
) -> list[tuple[str, str]]:
    """The summary's rows, as `(label, text)`. "Starts from" is left out
    when the key check found no baseline run config, and "Advanced" names
    only what differs from the `defaults`."""
    project, task = selection.project, selection.task
    lines = [
        ("Task", f"{task.name} ({task.id}) in project {project.name} ({project.id})")
    ]
    if baseline is not None:
        lines.append(("Starts from", _baseline_text(baseline)))
    budget = limits_budget_text(answers.budget_usd, answers.agent_cli)
    limits = f"{budget} · {duration_text(answers.timeout_mins)}"
    return [
        *lines,
        ("Improve", objective_text(answers.objective)),
        ("Guidance", guidance_text(answers.guidance)),
        ("Agents", agents_text(answers.agent_cli)),
        ("Limits", limits),
        ("Advanced", advanced_changes(answers, defaults)),
    ]


def summary_text(lines: Sequence[tuple[str, str]]) -> str:
    rows = (f"  {label:<{SUMMARY_LABEL_WIDTH}} {text}" for label, text in lines)
    return "\n".join([SUMMARY_HEADING, *rows])


def rerun_command(
    prog: str,
    argv: Sequence[str],
    *,
    option_dests: Mapping[str, OptionInfo],
    selection: Selection,
    answers: Answers,
    indent: str = "",
) -> str:
    """The command that runs this start again with the same answers and no
    questions: every answer as its flag, the flags the start never sets
    kept as typed, and `--no-interaction`. One group of flags per line,
    joined with `\\` and quoted for a POSIX shell. Each line starts with
    `indent`; a value that holds a newline is left as it is, so it still
    pastes as typed."""
    defaults = parser_defaults(option_dests)
    advanced: list[str] = []
    for dest, flag, written in ADVANCED_FLAGS:
        value = getattr(answers, dest)
        if value is not None and value != getattr(defaults, dest):
            advanced += [flag, written(value)]
    kept = [
        token
        for group, info in _read(argv, option_dests)
        if info is None or info.dest not in SET_BY_START
        for token in group
    ]
    lines = [
        ["--project-id", selection.project.id, "--task-id", selection.task.id],
        ["--project-path", str(selection.project_path)],
        ["--objective", answers.objective],
        ["--guidance", answers.guidance],
        ["--agent-cli", answers.agent_cli],
        [
            "--budget-usd",
            budget_default(answers.budget_usd),
            "--timeout-mins",
            str(answers.timeout_mins),
        ],
        advanced,
        kept,
        ["--no-interaction"],
    ]
    quoted = [" ".join(shlex.quote(token) for token in line) for line in lines if line]
    return " \\\n".join(
        [f"{indent}{shlex.quote(prog)}", *(f"{indent}  {line}" for line in quoted)]
    )
