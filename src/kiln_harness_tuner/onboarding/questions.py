"""The questions a guided start asks once the checks pass: what to improve,
the guidance, the agent CLI and its model, the budget, the time limit, and
the advanced options. A question whose flag is on the command line is never
asked: the flag is the answer.

Each answer is held in `Answers`, under the dest of the flag it stands for,
so the run reads it from the parsed flags as if it had been typed. The value
parsers are shared with `cli.py`, so a typed answer and a flag accept the
same values.
"""

from __future__ import annotations

import argparse
import math
import re
import shlex
import shutil
from collections.abc import Callable, Collection
from dataclasses import dataclass, fields
from typing import TypeVar

from kiln_harness_tuner.dispatch.agent_cli import AgentCli, parse_agent_cli
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.onboarding.demo import DEMO_GUIDANCE
from kiln_harness_tuner.onboarding.terminal import (
    ENTER,
    Action,
    ActionKind,
    Option,
    Problem,
    Terminal,
    plural,
    resolve_problem,
)

T = TypeVar("T")

# ---- the value parsers ----

NO_CAP = "none"
BUDGET_ERROR = "Enter an amount in USD, like 50, or none"
DURATION_ERROR = "Enter a time like 90m or 12h"
FRACTION_ERROR = "Enter a fraction above 0 and at most 1, like 0.1 for 10%"
COUNT_ERROR = "Enter a whole number, 0 or more"
MODEL_ERROR = "Enter one model name, with no spaces"

_DURATION = re.compile(r"(\d+(?:\.\d*)?|\.\d+)\s*([mh]?)")
_COUNT = re.compile(r"[0-9]+")


def _finite(text: str, error: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ValueError(error) from None
    if not math.isfinite(value):
        raise ValueError(error)
    return value


def parse_budget(text: str) -> float | None:
    """An amount of USD above 0, with an optional leading `$`, or `none`
    (any case) for no cap."""
    answer = text.strip()
    if answer.lower() == NO_CAP:
        return None
    value = _finite(answer.removeprefix("$"), BUDGET_ERROR)
    if value <= 0:
        raise ValueError(BUDGET_ERROR)
    return value


def parse_duration(text: str) -> int:
    """Minutes (`90`), or a number with `m` or `h` (`90m`, `12h`, `1.5h`), as
    whole minutes, rounded to the nearest: the flag takes whole minutes. At
    least 1 minute."""
    match = _DURATION.fullmatch(text.strip().lower())
    if match is None:
        raise ValueError(DURATION_ERROR)
    number, unit = match.groups()
    minutes = float(number) * (60 if unit == "h" else 1)
    if not math.isfinite(minutes) or minutes < 1:
        raise ValueError(DURATION_ERROR)
    return math.floor(minutes + 0.5)


def parse_fraction(text: str) -> float:
    """A share above 0 and at most 1 (`0.1` is 10%)."""
    value = _finite(text.strip(), FRACTION_ERROR)
    if not 0.0 < value <= 1.0:
        raise ValueError(FRACTION_ERROR)
    return value


def parse_count(text: str) -> int:
    """A whole number, 0 or more."""
    answer = text.strip()
    if _COUNT.fullmatch(answer) is None:
        raise ValueError(COUNT_ERROR)
    return int(answer)


def _refusal(parse: Callable[[str], object]) -> Callable[[str], str | None]:
    """A text question's validate callback: `parse`'s message, or `None`."""

    def validate(text: str) -> str | None:
        try:
            parse(text)
        except ValueError as exc:
            return str(exc)
        return None

    return validate


def _optional(parse: Callable[[str], T]) -> Callable[[str], T | None]:
    """`parse`, where an empty answer means not set."""
    return lambda text: parse(text) if text.strip() else None


# ---- how answers read ----


def number_text(value: float) -> str:
    """A number as it would be typed: `50` or `12.5`, never `50.0`."""
    return str(int(value)) if value.is_integer() else repr(value)


def budget_text(budget: float | None) -> str:
    return "no cap" if budget is None else f"${budget:,.2f}"


def budget_default(budget: float | None) -> str:
    """The budget as the question pre-fills it, and as the flag takes it."""
    return NO_CAP if budget is None else number_text(budget)


def duration_text(minutes: int) -> str:
    if minutes % 60 == 0:
        return plural(minutes // 60, "hour")
    return plural(minutes, "minute")


def duration_default(minutes: int) -> str:
    """The time limit as the question pre-fills it: `12h`, or `90m`."""
    return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"


def percent_text(fraction: float) -> str:
    return f"{fraction * 100:g}%"


def guidance_text(guidance: str) -> str:
    return f'"{guidance}"' if guidance else "none"


# ---- the answers ----


@dataclass
class Answers:
    """Every option the start can ask about, each under its flag's dest."""

    objective: str
    guidance: str
    agent_cli: str  # the whole command, e.g. "claude -p --model sonnet"
    budget_usd: float | None
    timeout_mins: int
    model_policy: str | None
    min_effect: float | None
    max_concurrent_evals: int
    max_concurrent_agents: int
    noise_check: int

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Answers:
        return cls(**{field.name: getattr(args, field.name) for field in fields(cls)})

    def apply(self, args: argparse.Namespace) -> None:
        """Write every answer into `args`, where the run reads its flags."""
        for field in fields(self):
            setattr(args, field.name, getattr(self, field.name))


OBJECTIVES = {
    "quality": ("Quality", "higher eval scores"),
    "speed": ("Speed", "lower latency per request, without losing quality"),
    "cost": ("Cost", "lower cost per request, without losing quality"),
}


def objective_text(objective: str) -> str:
    return OBJECTIVES.get(objective, (objective, ""))[0]


# ---- the agent CLIs ----


@dataclass(frozen=True)
class AgentKind:
    """An agent CLI the tuner can run, as the questions offer it."""

    kind: str  # its executable, and `AgentCli.kind`
    name: str
    mode: str  # what puts it in its one-shot mode
    model_option: str
    default_model: str  # "" = the CLI's own default
    model_hint: str
    reports_cost: bool

    @property
    def command(self) -> str:
        return f"{self.kind} {self.mode}"


# In the order the picker lists them. Only cli.py may name the dispatchers
# (tests/test_dispatch_boundary.py), so their facts are restated here, and a
# test holds the two equal: Claude Code's default model is the dispatcher's
# `AGENT_MODEL`, and `reports_cost` its `REPORTS_COST` (a CLI that reports
# tokens and no cost can't count toward the budget).
#
# The hints say what a blank answer does only where it is the CLI's own
# default. A blank Claude Code model is the dispatcher's pinned `AGENT_MODEL`,
# not Claude Code's own default, so its hint says nothing about a blank.
_MODEL_QUESTION = (
    "Which model will be used to plan and implement tuning experiments."
    " We suggest frontier quality"
)
AGENT_KINDS = (
    AgentKind(
        "claude",
        "Claude Code",
        "-p",
        "--model",
        "opus",
        f"{_MODEL_QUESTION} (Opus, Fable, etc).",
        reports_cost=True,
    ),
    AgentKind(
        "codex",
        "Codex",
        "exec",
        "-m",
        "",
        f"{_MODEL_QUESTION} (Astra, Terra, etc). Blank uses Codex's default model.",
        reports_cost=False,
    ),
    AgentKind(
        "opencode",
        "OpenCode",
        "run",
        "-m",
        "",
        f"{_MODEL_QUESTION} (Fable, Opus, Astra, etc). Models are PROVIDER/MODEL."
        " Blank uses OpenCode's default model.",
        reports_cost=True,
    ),
)
_KINDS = {agent.kind: agent for agent in AGENT_KINDS}


def installed_agent_clis(
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, ...]:
    """The agent CLIs on PATH, in the picker's order."""
    return tuple(agent.kind for agent in AGENT_KINDS if which(agent.kind) is not None)


def agent_command(kind: str, model: str) -> str:
    """The `--agent-cli` command for `kind` with `model`. The model is left
    out when it is the CLI's default, so the command reads as it would be
    typed."""
    agent = _KINDS[kind]
    tokens = [agent.kind, agent.mode]
    if model and model != agent.default_model:
        tokens += [agent.model_option, model]
    return shlex.join(tokens)


def _parsed(command: str) -> AgentCli | None:
    try:
        return parse_agent_cli(command)
    except ValueError:
        return None  # the run's own start-up refuses it, with its message


def agents_text(command: str) -> str:
    """`Claude Code (claude -p) · opus`: the CLI, the command without its
    model, and the model."""
    parsed = _parsed(command)
    if parsed is None:
        return command
    agent = _KINDS[parsed.kind]
    base = shlex.join([parsed.bin, agent.mode, *parsed.options])
    model = parsed.model or agent.default_model or "default model"
    effort = f" · effort {parsed.effort}" if parsed.effort else ""
    return f"{agent.name} ({base}) · {model}{effort}"


def _model_refusal(text: str) -> str | None:
    """A model name is one word. `agent_command` quotes it, so a space would
    reach the CLI inside the name rather than split it."""
    return MODEL_ERROR if any(c.isspace() for c in text.strip()) else None


def _no_agent_cli() -> Problem:
    names = [f"{agent.name} ({agent.kind})" for agent in AGENT_KINDS]
    return Problem(
        "No agent CLI is installed",
        (
            "The tuner's agents run through an agent CLI, signed in with its own"
            " account:",
            f"{', '.join(names[:-1])} or {names[-1]}.",
            "docs/install.md section 4 has the install and sign-in steps for each.",
        ),
        retry_label="Check again (I've installed one)",
    )


# ---- the questions ----


@dataclass(frozen=True)
class QuestionContext:
    terminal: Terminal
    defaults: Answers  # the parser's defaults, which Advanced compares with
    # cli's `load_model_policy`. It raises an error whose message says what
    # is wrong with the file (cli's ConfigRenderError, a RuntimeError).
    policy_loader: Callable[[str | None], ModelPolicy | None]
    demo: bool = False
    which: Callable[[str], str | None] = shutil.which


@dataclass(frozen=True)
class Question:
    name: str
    title: str  # how Change an answer lists it
    dests: frozenset[str]  # the flags that answer it
    advanced: bool
    # Asks, and sets the answer. A core question also prints its step line;
    # the advanced ones share one, the Advanced line.
    ask: Callable[[Answers, QuestionContext], None]
    show: Callable[[Answers], str]  # the answer as it reads


def _ask_objective(answers: Answers, ctx: QuestionContext) -> None:
    options = [
        Option(objective, f"{name}: {what}")
        for objective, (name, what) in OBJECTIVES.items()
    ]
    answers.objective = ctx.terminal.pick(
        "What should the tuner improve?",
        options,
        hint="One objective per run. Speed and cost only promote a change that"
        " keeps quality equal or better.",
        default=answers.objective,
    )
    ctx.terminal.step("ok", "Improve", objective_text(answers.objective))


# One example on each line (the start-up screen keeps the hint's lines).
GUIDANCE_HINT = "\n".join(
    (
        "These instructions guide the tuner when it plans new experiments."
        " Some examples:",
        " · Where to focus: \"Start with the Correctness eval. It's our biggest"
        ' quality gap."',
        ' · Trade-offs: "Between options of equal quality, prefer the cheaper one."',
        ' · Ideas to try: "Try the newest DeepSeek release on OpenRouter."',
        ' · What not to touch: "Don\'t change the model or temperature."',
        ' · Directives: "Only use models from OpenAI."',
        "Enter to skip.",
    )
)


def _ask_guidance(answers: Answers, ctx: QuestionContext) -> None:
    answer = ctx.terminal.text(
        "Guidance for the tuner's planner (optional)",
        default=answers.guidance,
        hint=GUIDANCE_HINT,
    )
    answers.guidance = answer.strip()
    ctx.terminal.step("ok", "Guidance", guidance_text(answers.guidance))


def _ask_agents(answers: Answers, ctx: QuestionContext) -> None:
    """The agent CLI, then its model: together they make `--agent-cli`."""
    current = _parsed(answers.agent_cli)
    kind, note = _choose_agent_cli(ctx, current.kind if current else None)
    agent = _KINDS[kind]
    model = agent.default_model
    if current is not None and current.kind == kind and current.model:
        model = current.model  # asked again: the model it has
    answer = ctx.terminal.text(
        "Model for the agents",
        default=model,
        hint=agent.model_hint,
        validate=_model_refusal,
    )
    answers.agent_cli = agent_command(kind, answer.strip())
    # One Agents line, after the model: the spec's only-one note (FS §8)
    # follows the model, which FS §10.1 adds to the same line.
    text = agents_text(answers.agent_cli)
    ctx.terminal.step("ok", "Agents", f"{text} · {note}" if note else text)


def _choose_agent_cli(ctx: QuestionContext, current: str | None) -> tuple[str, str]:
    """The agent CLI, and a note for its step line. One installed CLI is
    used without a question; none is a problem to fix by installing one."""
    installed: tuple[str, ...] = ()

    def check() -> Problem | None:
        nonlocal installed
        installed = installed_agent_clis(ctx.which)
        return None if installed else _no_agent_cli()

    resolve_problem(check, ctx.terminal, busy="Looking for agent CLIs…")
    if len(installed) == 1:
        return installed[0], "the only agent CLI installed"
    options = [
        Option(
            agent.kind,
            f"{agent.name}: {agent.command}",
            disabled=None if agent.kind in installed else "not installed",
        )
        for agent in AGENT_KINDS
    ]
    kind = ctx.terminal.pick(
        "Which agent CLI should run the tuner's agents?",
        options,
        hint="The tuner's own agents (planner, builders, reviewers) run through"
        " this CLI and are billed to its account, not to your Kiln provider keys.",
        default=current if current in installed else None,
    )
    return kind, ""


def _ask_budget(answers: Answers, ctx: QuestionContext) -> None:
    hint = (
        "The most this run may spend, in USD: the tuner's agents and the task's"
        " model calls together. No wave starts past it. Type none for no cap."
    )
    parsed = _parsed(answers.agent_cli)
    agent = _KINDS[parsed.kind] if parsed is not None else None
    # The note follows each dispatcher's `REPORTS_COST`, as the run's own
    # budget warning does: Codex reports no cost, OpenCode reports each
    # session's.
    if agent is not None and not agent.reports_cost:
        hint += (
            f" {agent.name} doesn't report its cost, so this caps the task's model"
            " calls only."
        )
    answer = ctx.terminal.text(
        "Budget for this run",
        default=budget_default(answers.budget_usd),
        hint=hint,
        validate=_refusal(parse_budget),
    )
    answers.budget_usd = parse_budget(answer)
    ctx.terminal.step("ok", "Budget", budget_text(answers.budget_usd))


def _ask_time(answers: Answers, ctx: QuestionContext) -> None:
    answer = ctx.terminal.text(
        "Time limit (1h, 24h, 45m)",
        default=duration_default(answers.timeout_mins),
        hint="No wave starts after this, and work still running at the limit is"
        " cancelled, so leave room for a whole wave.",
        validate=_refusal(parse_duration),
    )
    answers.timeout_mins = parse_duration(answer)
    ctx.terminal.step("ok", "Time", duration_text(answers.timeout_mins))


def _ask_model_policy(answers: Answers, ctx: QuestionContext) -> None:
    def refusal(text: str) -> str | None:
        path = text.strip()
        if not path:
            return None
        try:
            ctx.policy_loader(path)
        except (RuntimeError, ValueError) as exc:
            return str(exc)
        return None

    answer = ctx.terminal.text(
        "Model policy file",
        default=answers.model_policy or "",
        hint="Which models the tuner may move the task to."
        " examples/model-policy.json shows the format.",
        validate=refusal,
    )
    answers.model_policy = answer.strip() or None


def _ask_min_effect(answers: Answers, ctx: QuestionContext) -> None:
    parse = _optional(parse_fraction)
    answer = ctx.terminal.text(
        "Smallest improvement worth acting on",
        default="" if answers.min_effect is None else number_text(answers.min_effect),
        hint="The run stops before spending when no eval can show this change on val.",
        validate=_refusal(parse),
    )
    answers.min_effect = parse(answer)


CONCURRENCY_HINT = "Lower these if your provider rate-limits you."


def _ask_count(
    dest: str, question: str, hint: str
) -> Callable[[Answers, QuestionContext], None]:
    def ask(answers: Answers, ctx: QuestionContext) -> None:
        answer = ctx.terminal.text(
            question,
            default=str(getattr(answers, dest)),
            hint=hint,
            validate=_refusal(parse_count),
        )
        setattr(answers, dest, parse_count(answer))

    return ask


def _limit_text(count: int) -> str:
    return "no limit" if count == 0 else str(count)


def _noise_text(items: int) -> str:
    return "off" if items == 0 else f"{plural(items, 'item')} per eval"


QUESTIONS: tuple[Question, ...] = (
    Question(
        "objective",
        "What to improve",
        frozenset({"objective"}),
        False,
        _ask_objective,
        lambda a: objective_text(a.objective),
    ),
    Question(
        "guidance",
        "Guidance",
        frozenset({"guidance"}),
        False,
        _ask_guidance,
        lambda a: guidance_text(a.guidance),
    ),
    Question(
        "agents",
        "Agent CLI and model",
        frozenset({"agent_cli"}),
        False,
        _ask_agents,
        lambda a: agents_text(a.agent_cli),
    ),
    Question(
        "budget",
        "Budget",
        frozenset({"budget_usd"}),
        False,
        _ask_budget,
        lambda a: budget_text(a.budget_usd),
    ),
    Question(
        "time",
        "Time limit",
        frozenset({"timeout_mins"}),
        False,
        _ask_time,
        lambda a: duration_text(a.timeout_mins),
    ),
    Question(
        "model_policy",
        "Model policy",
        frozenset({"model_policy"}),
        True,
        _ask_model_policy,
        lambda a: a.model_policy or "any model Kiln offers",
    ),
    Question(
        "min_effect",
        "Minimum effect",
        frozenset({"min_effect"}),
        True,
        _ask_min_effect,
        lambda a: "not set" if a.min_effect is None else percent_text(a.min_effect),
    ),
    Question(
        "max_concurrent_evals",
        "Eval jobs at once",
        frozenset({"max_concurrent_evals"}),
        True,
        _ask_count("max_concurrent_evals", "Eval jobs at once", CONCURRENCY_HINT),
        lambda a: _limit_text(a.max_concurrent_evals),
    ),
    Question(
        "max_concurrent_agents",
        "Agent sessions at once",
        frozenset({"max_concurrent_agents"}),
        True,
        _ask_count("max_concurrent_agents", "Agent sessions at once", CONCURRENCY_HINT),
        lambda a: _limit_text(a.max_concurrent_agents),
    ),
    Question(
        "noise_check",
        "Noise check",
        frozenset({"noise_check"}),
        True,
        _ask_count(
            "noise_check",
            "Noise check: items per eval re-run before wave 1",
            "Measures how often the same config scores differently. Costs extra;"
            " it is estimated first.",
        ),
        lambda a: _noise_text(a.noise_check),
    ),
)
_BY_NAME = {question.name: question for question in QUESTIONS}

# `Enter) Keep these` leads: the values on the menu's own line are kept.
ADVANCED_MENU = (
    Action(ENTER, "Keep these", ActionKind.KEEP),
    Action("c", "Customize", ActionKind.CUSTOMIZE),
)


def _at_once(count: int, noun: str) -> str:
    return f"any number of {noun}s" if count == 0 else plural(count, noun)


def _advanced_parts(answers: Answers) -> dict[str, str]:
    """Each advanced answer as the Advanced lines read it, by dest, in their
    order."""
    policy = answers.model_policy
    effect = answers.min_effect
    noise = answers.noise_check
    return {
        "model_policy": f"model policy {policy}" if policy else "any model",
        "max_concurrent_evals": _at_once(answers.max_concurrent_evals, "eval"),
        "max_concurrent_agents": _at_once(answers.max_concurrent_agents, "agent"),
        "min_effect": (
            "no minimum effect"
            if effect is None
            else f"minimum effect {percent_text(effect)}"
        ),
        "noise_check": f"noise check on {_noise_text(noise)}"
        if noise
        else "no noise check",
    }


def advanced_values(answers: Answers) -> str:
    """Every advanced value, for the Advanced menu's question:
    `any model · 4 evals and 4 agents at once · no minimum effect · no noise
    check`."""
    parts = _advanced_parts(answers)
    concurrency = (
        f"{parts['max_concurrent_evals']} and {parts['max_concurrent_agents']} at once"
    )
    return " · ".join(
        [parts["model_policy"], concurrency, parts["min_effect"], parts["noise_check"]]
    )


def advanced_changes(answers: Answers, defaults: Answers) -> str:
    """The advanced values that differ from the defaults, for the Advanced
    step and summary line: `model policy ./policy.json · 2 evals at once`,
    or `defaults`."""
    concurrency = {"max_concurrent_evals", "max_concurrent_agents"}
    changes = [
        f"{text} at once" if dest in concurrency else text
        for dest, text in _advanced_parts(answers).items()
        if getattr(answers, dest) != getattr(defaults, dest)
    ]
    return " · ".join(changes) or "defaults"


def _advanced_step(answers: Answers, ctx: QuestionContext) -> None:
    ctx.terminal.step("ok", "Advanced", advanced_changes(answers, ctx.defaults))


def changeable(given: Collection[str]) -> tuple[Question, ...]:
    """The questions no flag answered, in order: those a guided start asks,
    and Change an answer offers."""
    return tuple(q for q in QUESTIONS if not q.dests & set(given))


def ask_all(
    answers: Answers, ctx: QuestionContext, *, given: Collection[str]
) -> Answers:
    """Ask every question whose flag isn't among the `given` dests, in
    order, then the Advanced menu, setting `answers` as they come. The demo
    pre-fills its own guidance."""
    unanswered = changeable(given)
    if ctx.demo and _BY_NAME["guidance"] in unanswered:
        answers.guidance = DEMO_GUIDANCE
    for question in unanswered:
        if not question.advanced:
            question.ask(answers, ctx)
    advanced = [question for question in unanswered if question.advanced]
    if advanced:
        action = ctx.terminal.act(
            f"Advanced options: {advanced_values(answers)}", ADVANCED_MENU
        )
        if action.kind is ActionKind.CUSTOMIZE:
            for question in advanced:
                question.ask(answers, ctx)
        _advanced_step(answers, ctx)
    return answers


def ask_one(name: str, answers: Answers, ctx: QuestionContext) -> Answers:
    """Ask the question `name` again, pre-filled with its current answer."""
    question = _BY_NAME[name]
    question.ask(answers, ctx)
    if question.advanced:
        _advanced_step(answers, ctx)
    return answers
