"""The questions a guided start asks: the value parsers, how each answer
reads, the agent CLIs, and each question asked through a scripted
terminal."""

import argparse
import dataclasses

import pytest
from onboarding_fakes import ScriptedTerminal

from kiln_harness_tuner.cli import DISPATCHERS, build_parser, load_model_policy
from kiln_harness_tuner.cli import OBJECTIVES as CLI_OBJECTIVES
from kiln_harness_tuner.dispatch.agent_cli import parse_agent_cli
from kiln_harness_tuner.dispatch.claude_cli import AGENT_MODEL
from kiln_harness_tuner.onboarding.agent_models import CLAUDE_MODELS, CODEX_MODELS
from kiln_harness_tuner.onboarding.demo import DEMO_GUIDANCE
from kiln_harness_tuner.onboarding.errors import GoBack, OnboardingStopped
from kiln_harness_tuner.onboarding.questions import (
    AGENT_KINDS,
    BUDGET_ERROR,
    COUNT_ERROR,
    DURATION_ERROR,
    EFFORT_ERROR,
    FRACTION_ERROR,
    MIN_TIME_LIMIT_MINS,
    MODEL_ERROR,
    OBJECTIVES,
    QUESTIONS,
    TIME_LIMIT_ERROR,
    Answers,
    QuestionContext,
    advanced_changes,
    advanced_values,
    agent_command,
    agents_text,
    ask_all,
    ask_one,
    budget_default,
    budget_text,
    changeable,
    check_time_limit,
    duration_default,
    duration_text,
    guidance_text,
    installed_agent_clis,
    number_text,
    parse_budget,
    parse_count,
    parse_duration,
    parse_fraction,
)
from kiln_harness_tuner.onboarding.summary import parser_defaults

DEFAULTS = parser_defaults(build_parser().option_dests)
EVERY_DEST = frozenset().union(*(question.dests for question in QUESTIONS))
ADVANCED_DESTS = frozenset().union(*(q.dests for q in QUESTIONS if q.advanced))
CORE_QUESTIONS = [
    "What should the tuner improve?",
    "Guidance for the tuner's planner (optional)",
    "Which agent CLI should run the tuner's agents?",
    "Model for the agents",
    "Thinking level for the agents",
    "Budget for this run",
    "Time limit (1h, 24h, 45m)",
]
ADVANCED_PROMPT = (
    "Advanced options: same model · 4 evals and 4 agents at once"
    " · no minimum effect · no noise check"
)
ADVANCED_QUESTIONS = [
    "Model policy file",
    "Smallest improvement worth acting on",
    "Eval jobs at once",
    "Agent sessions at once",
    "Noise check: items per eval re-run before wave 1",
]


def answers(**changes) -> Answers:
    return dataclasses.replace(DEFAULTS, **changes)


def installed(*kinds: str):
    """`shutil.which` on a machine with only `kinds` on PATH."""
    return lambda name: f"/usr/local/bin/{name}" if name in kinds else None


EVERY_CLI = installed("claude", "codex", "opencode")


def context(
    terminal: ScriptedTerminal,
    *,
    which=EVERY_CLI,
    policy_loader=load_model_policy,
    demo: bool = False,
) -> QuestionContext:
    return QuestionContext(terminal, DEFAULTS, policy_loader, demo=demo, which=which)


class Typed:
    """A text answer typed several times: each attempt but the last must be
    refused, and the last accepted. `refusals` holds what the field said."""

    def __init__(self, *attempts: str):
        self.attempts = attempts
        self.refusals: list[str | None] = []

    def __call__(self, validate):
        *refused, accepted = self.attempts
        self.refusals = [validate(attempt) for attempt in refused]
        assert validate(accepted) is None
        return accepted


# ---- the value parsers ----


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("50", 50.0),
        ("$12.50", 12.5),
        (" 7 ", 7.0),
        ("0.01", 0.01),
        ("none", None),
        ("NONE", None),
        (" None ", None),
    ],
)
def test_parse_budget_accepts(text, value):
    assert parse_budget(text) == value


@pytest.mark.parametrize("text", ["0", "-1", "lots", "$", "", "inf", "nan", "5 USD"])
def test_parse_budget_refuses(text):
    with pytest.raises(ValueError, match=f"^{BUDGET_ERROR}$"):
        parse_budget(text)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("90", 90),
        ("90m", 90),
        ("12h", 720),
        ("1.5h", 90),
        (" 3H ", 180),
        ("10", 10),
        ("9.5", 10),  # whole minutes, to the nearest
        ("10.4", 10),
        ("0.5h", 30),
        ("2 h", 120),
    ],
)
def test_parse_duration_accepts(text, minutes):
    assert parse_duration(text) == minutes


@pytest.mark.parametrize(
    "text", ["0", "0.5", "0m", "", "h", "12 hours", "-5", "1d", "soon", "9" * 400]
)
def test_parse_duration_refuses(text):
    with pytest.raises(ValueError, match=f"^{DURATION_ERROR}$"):
        parse_duration(text)


@pytest.mark.parametrize("text", ["1", "5m", "9", "9.4", "0.1h", ".15h"])
def test_parse_duration_refuses_under_10_minutes(text):
    with pytest.raises(ValueError) as refused:
        parse_duration(text)
    assert str(refused.value) == TIME_LIMIT_ERROR


def test_the_time_limit_minimum_says_why():
    assert MIN_TIME_LIMIT_MINS == 10
    assert check_time_limit(10) == 10
    with pytest.raises(ValueError):
        check_time_limit(9)
    assert TIME_LIMIT_ERROR == "Enter at least 10 minutes: setup alone takes a few"


@pytest.mark.parametrize(("text", "value"), [("0.1", 0.1), ("1", 1.0), (" .5 ", 0.5)])
def test_parse_fraction_accepts(text, value):
    assert parse_fraction(text) == value


@pytest.mark.parametrize("text", ["0", "1.5", "-0.1", "ten", "", "nan"])
def test_parse_fraction_refuses(text):
    with pytest.raises(ValueError, match=f"^{FRACTION_ERROR}$"):
        parse_fraction(text)


@pytest.mark.parametrize(("text", "value"), [("0", 0), ("4", 4), (" 12 ", 12)])
def test_parse_count_accepts(text, value):
    assert parse_count(text) == value


@pytest.mark.parametrize("text", ["-1", "1.5", "four", "", "+3"])
def test_parse_count_refuses(text):
    with pytest.raises(ValueError, match=f"^{COUNT_ERROR}$"):
        parse_count(text)


# ---- how answers read ----


def test_numbers_read_as_typed():
    assert [number_text(v) for v in (50.0, 12.5, 0.1)] == ["50", "12.5", "0.1"]
    assert [budget_default(v) for v in (50.0, 12.5, None)] == ["50", "12.5", "none"]
    assert [budget_text(v) for v in (50.0, 1234.5, None)] == [
        "$50.00",
        "$1,234.50",
        "no cap",
    ]


def test_durations_read_in_hours_when_whole():
    assert [duration_text(m) for m in (720, 60, 90, 1)] == [
        "12 hours",
        "1 hour",
        "90 minutes",
        "1 minute",
    ]
    assert [duration_default(m) for m in (720, 90)] == ["12h", "90m"]


def test_guidance_reads_quoted_or_none():
    assert guidance_text("Keep the format.") == '"Keep the format."'
    assert guidance_text("") == "none"


def test_answers_come_from_the_flags_and_go_back_into_them():
    args = build_parser().parse_args(["--objective", "cost", "--budget-usd", "none"])
    read = Answers.from_args(args)
    assert (read.objective, read.budget_usd, read.timeout_mins) == ("cost", None, 720)
    changed = dataclasses.replace(read, guidance="x", noise_check=3)
    target = argparse.Namespace(**vars(args))
    changed.apply(target)
    assert Answers.from_args(target) == changed
    assert target.kiln_host == args.kiln_host  # flags that aren't answers stay


# ---- the agent CLIs ----


def test_the_agent_clis_match_the_dispatchers():
    assert {agent.kind for agent in AGENT_KINDS} == set(DISPATCHERS)
    for agent in AGENT_KINDS:
        dispatcher, _bin = DISPATCHERS[agent.kind]
        assert agent.command == dispatcher.CLI_NAME
        assert agent.default_effort == dispatcher.DEFAULT_EFFORT, agent.kind
    claude = next(agent for agent in AGENT_KINDS if agent.kind == "claude")
    assert claude.default_model == AGENT_MODEL


def test_a_hint_says_what_a_blank_model_does_only_when_it_is_the_cli_s_own():
    # A blank model leaves the model option out. The dispatcher then passes
    # its pinned default, or nothing, so the CLI's own default applies.
    for agent in AGENT_KINDS:
        dispatcher, _bin = DISPATCHERS[agent.kind]
        own = dispatcher.DEFAULT_MODEL is None and not dispatcher.MODEL_DEFAULTS
        said = f"Blank uses {agent.name}'s default model." in agent.model_hint
        assert said is own, agent.kind
        assert "Enter" not in agent.model_hint


def test_the_objectives_match_the_flags_choices():
    assert tuple(OBJECTIVES) == CLI_OBJECTIVES


def test_installed_agent_clis_are_those_on_path_in_order():
    assert installed_agent_clis(installed("opencode", "claude")) == (
        "claude",
        "opencode",
    )
    assert installed_agent_clis(installed()) == ()


@pytest.mark.parametrize(
    ("kind", "model", "command"),
    [
        ("claude", "opus", "claude -p"),
        ("claude", "", "claude -p"),
        ("claude", "sonnet", "claude -p --model sonnet"),
        ("codex", "", "codex exec"),
        ("codex", "gpt-6.1-sol", "codex exec -m gpt-6.1-sol"),
        (
            "opencode",
            "zai-coding-plan/glm-5.3",
            "opencode run -m zai-coding-plan/glm-5.3",
        ),
    ],
)
def test_agent_command_names_the_model_unless_it_is_the_default(kind, model, command):
    assert agent_command(kind, model) == command
    parsed = parse_agent_cli(command)
    assert (parsed.kind, parsed.model) == (
        kind,
        None if model in ("", "opus") else model,
    )


@pytest.mark.parametrize(
    ("command", "text"),
    [
        ("claude -p", "Claude Code (claude -p) · opus"),
        (
            "claude -p --model sonnet --effort high",
            "Claude Code (claude -p) · sonnet · effort high",
        ),
        ("codex exec", "Codex (codex exec) · default model"),
        ("/opt/bin/codex exec -m x --oss", "Codex (/opt/bin/codex exec --oss) · x"),
        ("opencode run -m a/b", "OpenCode (opencode run) · a/b"),
        ("nonsense", "nonsense"),
    ],
)
def test_agents_text_names_the_cli_its_command_and_its_model(command, text):
    assert agents_text(command) == text


# ---- every question ----


def test_ask_all_with_nothing_given_asks_every_question_in_order():
    terminal = ScriptedTerminal(
        [
            "speed",
            "Keep it short.",
            "opencode",
            "zai/glm",
            "max",
            "none",
            "1.5h",
            "enter",
        ]
    )
    got = ask_all(answers(), context(terminal), given=frozenset())
    # Speed may swap models, so with no policy the Advanced line reads so.
    swapping = ADVANCED_PROMPT.replace("same model", "any model")
    assert terminal.questions == [*CORE_QUESTIONS, swapping]
    assert terminal.step_lines() == [
        "Improve: Speed",
        'Guidance: "Keep it short."',
        "Agents: OpenCode (opencode run) · zai/glm · effort max",
        "Budget: no cap",
        "Time: 90 minutes",
        "Advanced: defaults",
    ]
    assert got == answers(
        objective="speed",
        guidance="Keep it short.",
        agent_cli="opencode run -m zai/glm --variant max",
        budget_usd=None,
        timeout_mins=90,
    )
    # Each question pre-filled with the default the flag has.
    assert [asked.default for asked in terminal.asked[:-1]] == [
        "quality",
        "",
        "claude",
        "",  # OpenCode's own default model
        "high",  # the tuner's own thinking level
        "50",
        "12h",
    ]
    assert terminal.menus == [("enter", "c")]


def test_the_objective_rows_and_the_hints():
    terminal = ScriptedTerminal(
        ["quality", "", "claude", "opus", "high", "50", "12h", "enter"]
    )
    ask_all(answers(), context(terminal), given=frozenset())
    assert [option.label for option in terminal.options[0]] == [
        "Quality: higher eval scores",
        "Speed: lower latency per request, without losing quality",
        "Cost: lower cost per request, without losing quality",
    ]
    hints = [asked.hint for asked in terminal.asked[:-1]]
    assert hints == [
        "One objective per run. Speed and cost only promote a change that keeps"
        " quality equal or better.",
        "These instructions guide the tuner when it plans new experiments. Some"
        " examples:\n"
        " · Where to focus: \"Start with the Correctness eval. It's our biggest"
        ' quality gap."\n'
        ' · Trade-offs: "Between options of equal quality, prefer the cheaper'
        ' one."\n'
        ' · Ideas to try: "Try the newest DeepSeek release on OpenRouter."\n'
        ' · What not to touch: "Don\'t change the model or temperature."\n'
        ' · Directives: "Only use models from OpenAI."\n'
        "Optional. Blank means no guidance.",
        "The tuner's own agents (planner, builders, reviewers) run through this CLI"
        " and are billed to its account, not to your Kiln provider keys.",
        "Which model will be used to plan and implement tuning experiments. We"
        " suggest frontier quality (Opus, Fable, etc). Other to type a model"
        " name: an alias or a full name.",
        "How much each agent reasons before it answers.",
        "The most this run may spend on the task's model calls, in USD. The"
        " tuner's agents run on your agent CLI's account and are not counted."
        " No wave starts past it. Type none for no cap.",
        "At least 10m. The limit is approximate: as the run nears it, the"
        " tuner starts to wrap up, so the run can end a little later.",
    ]
    assert [option.label for option in terminal.options[1]] == [
        "Claude Code: claude -p",
        "Codex: codex exec",
        "OpenCode: opencode run",
    ]
    assert terminal.step_lines()[2] == "Agents: Claude Code (claude -p) · opus"


@pytest.mark.parametrize(
    ("name", "typed", "asked"),
    [
        ("objective", ["cost"], CORE_QUESTIONS[:1]),
        ("guidance", ["x"], CORE_QUESTIONS[1:2]),
        ("agents", ["codex", "", "high"], CORE_QUESTIONS[2:5]),
        ("budget", ["9"], CORE_QUESTIONS[5:6]),
        ("time", ["45"], CORE_QUESTIONS[6:7]),
    ],
)
def test_only_the_question_no_flag_answered_is_asked(name, typed, asked):
    """Every other flag given, the advanced ones too, so the Advanced menu
    is skipped."""
    (question,) = [q for q in QUESTIONS if q.name == name]
    terminal = ScriptedTerminal(typed)
    ask_all(answers(), context(terminal), given=EVERY_DEST - question.dests)
    assert terminal.questions == asked


def test_with_every_flag_given_nothing_is_asked():
    terminal = ScriptedTerminal()
    assert ask_all(answers(), context(terminal), given=EVERY_DEST) == answers()
    assert terminal.asked == [] and terminal.steps == []


def test_guidance_of_spaces_is_none():
    terminal = ScriptedTerminal(["   "])
    got = ask_all(answers(), context(terminal), given=EVERY_DEST - {"guidance"})
    assert got.guidance == ""
    assert terminal.step_lines() == ["Guidance: none"]


def test_the_demo_pre_fills_its_own_guidance():
    terminal = ScriptedTerminal([DEMO_GUIDANCE])
    given = EVERY_DEST - {"guidance"}
    got = ask_all(answers(), context(terminal, demo=True), given=given)
    assert terminal.asked[0].default == DEMO_GUIDANCE
    assert got.guidance == DEMO_GUIDANCE


def test_the_demo_keeps_a_guidance_given_by_flag():
    terminal = ScriptedTerminal()
    got = ask_all(
        answers(guidance="mine"), context(terminal, demo=True), given=EVERY_DEST
    )
    assert got.guidance == "mine"


# ---- the agent CLI and its model ----

AGENTS_ONLY = EVERY_DEST - {"agent_cli"}


def test_with_one_agent_cli_installed_it_is_used_without_a_question():
    terminal = ScriptedTerminal(["sonnet", "high"])
    got = ask_all(
        answers(), context(terminal, which=installed("claude")), given=AGENTS_ONLY
    )
    assert terminal.questions == [
        "Model for the agents",
        "Thinking level for the agents",
    ]
    assert got.agent_cli == "claude -p --model sonnet"
    assert terminal.step_lines() == [
        "Agents: Claude Code (claude -p) · sonnet · the only agent CLI installed"
    ]


def test_an_agent_cli_not_installed_is_shown_and_cant_be_picked():
    terminal = ScriptedTerminal(["codex", "", "high"])
    which = installed("codex", "opencode")
    ask_all(answers(), context(terminal, which=which), given=AGENTS_ONLY)
    assert [option.disabled for option in terminal.options[0]] == [
        "not installed",
        None,
        None,
    ]
    # The default, Claude Code, can't be picked, so the picker starts on the
    # first that can.
    assert terminal.asked[0].default is None


def test_with_no_agent_cli_installed_check_again_finds_one():
    on_path: set[str] = set()

    def install_claude_then_retry():
        on_path.add("claude")
        return "r"

    terminal = ScriptedTerminal([install_claude_then_retry, "opus", "high"])
    context_ = context(terminal, which=lambda name: name if name in on_path else None)
    got = ask_all(answers(), context_, given=AGENTS_ONLY)
    assert got.agent_cli == "claude -p"
    (title, body) = terminal.blocks[0]
    assert title == "No agent CLI is installed"
    assert body == (
        "The tuner's agents run through an agent CLI, signed in with its own account:",
        "Claude Code (claude), Codex (codex) or OpenCode (opencode).",
        "docs/install.md section 4 has the install and sign-in steps for each.",
    )
    assert terminal.menus == [("r", "x")]
    assert terminal.busy_texts == ["Looking for agent CLIs…"] * 2
    assert terminal.step_lines()[-1].endswith("· the only agent CLI installed")


def test_with_no_agent_cli_installed_exit_stops_with_1():
    terminal = ScriptedTerminal(["x"])
    with pytest.raises(OnboardingStopped) as stopped:
        ask_all(answers(), context(terminal, which=installed()), given=AGENTS_ONLY)
    assert stopped.value.exit_code == 1


def test_opencode_has_no_list_so_its_model_is_typed():
    terminal = ScriptedTerminal(["opencode", "", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    model = terminal.asked_about("Model for the agents")
    assert (model.kind, model.default, model.hint) == (
        "text",
        "",
        "Which model will be used to plan and implement tuning experiments. We"
        " suggest frontier quality (Fable, Opus, Astra, etc). Models are"
        " PROVIDER/MODEL, for example zai-coding-plan/glm-5.3-flash."
        " `opencode models` lists them. Blank uses OpenCode's default model.",
    )
    # Enter on the default leaves the model out of the command.
    assert got.agent_cli == "opencode run"


def test_claude_code_models_are_picked_from_its_list():
    terminal = ScriptedTerminal(["claude", "fable", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    model = terminal.asked_about("Model for the agents")
    assert model.kind == "pick"
    assert model.offered == (*(m.name for m in CLAUDE_MODELS), " other")
    assert "fable" in model.offered
    assert model.default == "opus"
    assert model.hint == (
        "Which model will be used to plan and implement tuning experiments. We"
        " suggest frontier quality (Opus, Fable, etc). Other to type a model"
        " name: an alias or a full name."
    )
    assert [o.label for o in terminal.options[-2]][-1] == "Other…"
    assert got.agent_cli == "claude -p --model fable"


def test_codex_models_are_picked_from_its_list():
    terminal = ScriptedTerminal(["codex", "gpt-6.1-terra", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    model = terminal.asked_about("Model for the agents")
    assert model.offered == ("", *(m.name for m in CODEX_MODELS), " other")
    assert model.default == ""
    assert model.hint == (
        "Which model will be used to plan and implement tuning experiments. We"
        " suggest frontier quality (Astra, Terra, etc). Other to type a model"
        " name. All of Codex's models: https://developers.openai.com/codex/models"
    )
    rows = terminal.options[-2]
    assert (rows[0].label, rows[0].description) == (
        "Default",
        "Codex's own default model. Its config.toml is not read.",
    )
    assert all(row.description for row in rows)
    assert got.agent_cli == "codex exec -m gpt-6.1-terra"


def test_the_default_row_leaves_the_model_out_of_the_command():
    terminal = ScriptedTerminal(["codex", "", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert got.agent_cli == "codex exec"


def test_other_asks_for_a_typed_name():
    typed = Typed("gpt 6", "gpt-7-preview")
    terminal = ScriptedTerminal(["codex", " other", typed, "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert terminal.questions[-3:] == [
        "Model for the agents",
        "Model name for the agents",
        "Thinking level for the agents",
    ]
    assert terminal.asked_about("Model name for the agents").hint == (
        "Blank uses Codex's default model. For example gpt-6.1-sol. The"
        " models: https://developers.openai.com/codex/models"
    )
    assert typed.refusals == [MODEL_ERROR]
    assert got.agent_cli == "codex exec -m gpt-7-preview"


def test_claude_code_s_typed_name_does_not_say_what_a_blank_does():
    # A blank Claude Code model is the dispatcher's pinned opus, not Claude
    # Code's own default. The hint also does not name the field's value.
    terminal = ScriptedTerminal(["claude", " other", "claude-opus-5-5", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert terminal.asked_about("Model name for the agents").hint == (
        "An alias, such as sonnet, or a full model name, such as claude-opus-5-5."
    )
    assert got.agent_cli == "claude -p --model claude-opus-5-5"


def test_asked_again_a_model_not_in_the_list_starts_on_other():
    terminal = ScriptedTerminal(["codex", " other", "my-model", "high"])
    ask_all(
        answers(agent_cli="codex exec -m my-model"),
        context(terminal),
        given=AGENTS_ONLY,
    )
    assert terminal.asked_about("Model for the agents").default == " other"
    assert terminal.asked_about("Model name for the agents").default == "my-model"


def test_a_model_name_with_a_space_is_refused():
    typed = Typed("zai/glm 5", "zai/glm-5")
    terminal = ScriptedTerminal(["opencode", typed, "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert typed.refusals == [MODEL_ERROR]
    assert got.agent_cli == "opencode run -m zai/glm-5"


def test_the_thinking_level_is_picked_and_starts_on_high():
    terminal = ScriptedTerminal(["codex", "gpt-6.1-sol", "xhigh"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    level = terminal.asked_about("Thinking level for the agents")
    assert level.offered == ("low", "medium", "high", "xhigh", "max", " other")
    assert level.default == "high"
    assert level.hint == (
        "How much each agent reasons before it answers. Some models also take"
        " ultra: Other to type it."
    )
    assert got.agent_cli == "codex exec -m gpt-6.1-sol -c model_reasoning_effort=xhigh"
    assert parse_agent_cli(got.agent_cli).effort == "xhigh"
    assert terminal.step_lines()[-1] == (
        "Agents: Codex (codex exec) · gpt-6.1-sol · effort xhigh"
    )


def test_claude_code_levels_have_no_other():
    # Claude Code ignores a level it doesn't know, so only its own are offered.
    terminal = ScriptedTerminal(["claude", "opus", "max"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    level = terminal.asked_about("Thinking level for the agents")
    assert level.offered == ("low", "medium", "high", "xhigh", "max")
    assert level.hint == "How much each agent reasons before it answers."
    assert got.agent_cli == "claude -p --effort max"


def test_high_leaves_the_level_out_of_the_command():
    terminal = ScriptedTerminal(["claude", "opus", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert got.agent_cli == "claude -p"


def test_other_types_a_level():
    typed = Typed("very high", "", "ultra")
    terminal = ScriptedTerminal(["codex", "", " other", typed])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    level = terminal.asked_about("Thinking level name for the agents")
    # A blank level is refused, so the hint says nothing about a blank.
    assert typed.refusals == [EFFORT_ERROR] * 2
    assert level.hint == "A level name the model takes."
    assert got.agent_cli == "codex exec -c model_reasoning_effort=ultra"


def test_opencode_types_its_variant():
    typed = Typed("", "max")
    terminal = ScriptedTerminal(["opencode", "zai/glm", typed])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    level = terminal.asked_about("Thinking level for the agents")
    assert (level.kind, level.default) == ("text", "high")
    # The hint does not name the field's value, which Esc back can change.
    assert typed.refusals == [EFFORT_ERROR]
    assert level.hint == (
        "OpenCode's level is its --variant, whose names depend on the model,"
        " such as high or max. A model ignores a variant it doesn't have."
    )
    assert got.agent_cli == "opencode run -m zai/glm --variant max"


# ---- budget and time ----

BUDGET_ONLY = EVERY_DEST - {"budget_usd"}


def test_a_budget_is_typed_with_or_without_a_dollar_sign():
    typed = Typed("lots", "0", "$20")
    terminal = ScriptedTerminal([typed])
    got = ask_all(answers(), context(terminal), given=BUDGET_ONLY)
    assert typed.refusals == [BUDGET_ERROR] * 2
    assert got.budget_usd == 20.0
    assert terminal.step_lines() == ["Budget: $20.00"]


@pytest.mark.parametrize("agent_cli", ["claude -p", "codex exec", "opencode run"])
def test_the_budget_never_counts_the_agents(agent_cli):
    terminal = ScriptedTerminal(["50"])
    ask_all(answers(agent_cli=agent_cli), context(terminal), given=BUDGET_ONLY)
    assert terminal.asked[0].hint == (
        "The most this run may spend on the task's model calls, in USD. The"
        " tuner's agents run on your agent CLI's account and are not counted."
        " No wave starts past it. Type none for no cap."
    )


def test_a_time_limit_is_typed_in_minutes_or_hours():
    typed = Typed("soon", "0m", "2h")
    terminal = ScriptedTerminal([typed])
    got = ask_all(answers(), context(terminal), given=EVERY_DEST - {"timeout_mins"})
    assert typed.refusals == [DURATION_ERROR] * 2
    assert got.timeout_mins == 120
    assert terminal.step_lines() == ["Time: 2 hours"]


def test_a_time_limit_under_10_minutes_is_refused():
    typed = Typed("5m", "10m")
    terminal = ScriptedTerminal([typed])
    got = ask_all(answers(), context(terminal), given=EVERY_DEST - {"timeout_mins"})
    assert typed.refusals == [TIME_LIMIT_ERROR]
    assert got.timeout_mins == 10
    assert terminal.asked[0].hint == (
        "At least 10m. The limit is approximate: as the run nears it, the"
        " tuner starts to wrap up, so the run can end a little later."
    )


# ---- the advanced options ----

CORE_DESTS = EVERY_DEST - ADVANCED_DESTS


def test_keep_leaves_the_advanced_options_as_they_are():
    terminal = ScriptedTerminal(["enter"])
    got = ask_all(answers(noise_check=2), context(terminal), given=CORE_DESTS)
    assert got == answers(noise_check=2)
    assert terminal.questions == [
        "Advanced options: same model · 4 evals and 4 agents at once"
        " · no minimum effect · noise check on 2 items per eval"
    ]
    assert terminal.step_lines() == ["Advanced: noise check on 2 items per eval"]


def test_customize_asks_each_advanced_question_with_its_value(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"allow": [{"provider": "openai", "model": "*"}]}')
    terminal = ScriptedTerminal(["c", str(policy), "0.25", "2", "0", "10"])
    got = ask_all(answers(), context(terminal), given=CORE_DESTS)
    assert terminal.questions == [ADVANCED_PROMPT, *ADVANCED_QUESTIONS]
    assert [asked.default for asked in terminal.asked[1:]] == ["", "", "4", "4", "0"]
    assert (got.model_policy, got.min_effect, got.noise_check) == (
        str(policy),
        0.25,
        10,
    )
    assert (got.max_concurrent_evals, got.max_concurrent_agents) == (2, 0)
    assert terminal.step_lines() == [
        f"Advanced: model policy {policy} · 2 evals at once"
        " · any number of agents at once · minimum effect 25%"
        " · noise check on 10 items per eval"
    ]


def test_the_policy_and_minimum_hints_say_what_a_blank_answer_does():
    # Prefilled or not, a blank answer sets neither: the hint says so.
    terminal = ScriptedTerminal(["c", "", "", "4", "4", "0"])
    got = ask_all(answers(min_effect=0.2), context(terminal), given=CORE_DESTS)
    effect = terminal.asked[2]
    assert effect.default == "0.2"
    assert effect.hint.endswith(
        " Optional. Blank means no minimum, and the run does not stop for it."
    )
    assert (got.model_policy, got.min_effect) == (None, None)


def test_customize_skips_the_values_flags_gave():
    terminal = ScriptedTerminal(["c", "", "3", "3"])
    given = CORE_DESTS | {"model_policy", "noise_check"}
    got = ask_all(answers(noise_check=5), context(terminal), given=given)
    assert terminal.questions[1:] == ADVANCED_QUESTIONS[1:4]
    assert got.noise_check == 5
    assert got.min_effect is None


def test_a_bad_policy_file_is_refused_with_the_loaders_message(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    typed = Typed(str(bad), str(tmp_path / "missing.json"), "")
    terminal = ScriptedTerminal(["c", typed, "", "4", "4", "0"])
    got = ask_all(answers(), context(terminal), given=CORE_DESTS)
    bad_json, missing = typed.refusals
    assert bad_json.startswith(f"--model-policy: {bad} is not valid JSON")
    assert missing.startswith("--model-policy: cannot read")
    assert got.model_policy is None


def test_invalid_advanced_values_are_refused():
    effect, evals = Typed("2", "x", ""), Typed("-1", "many", "3")
    terminal = ScriptedTerminal(["c", "", effect, evals, "4", "0"])
    got = ask_all(answers(), context(terminal), given=CORE_DESTS)
    assert effect.refusals == [FRACTION_ERROR] * 2
    assert evals.refusals == [COUNT_ERROR] * 2
    assert (got.min_effect, got.max_concurrent_evals) == (None, 3)


def test_the_advanced_lines():
    assert advanced_values(answers()) == ADVANCED_PROMPT.removeprefix(
        "Advanced options: "
    )
    assert advanced_changes(answers(), DEFAULTS) == "defaults"
    changed = answers(model_policy="./policy.json", max_concurrent_evals=2)
    assert advanced_changes(changed, DEFAULTS) == (
        "model policy ./policy.json · 2 evals at once"
    )
    one = answers(max_concurrent_evals=1, max_concurrent_agents=0, min_effect=0.1)
    assert advanced_values(one) == (
        "same model · 1 eval and any number of agents at once"
        " · minimum effect 10% · no noise check"
    )


def test_without_a_policy_the_model_text_follows_the_objective():
    # Quality keeps the task's model; speed and cost may swap it. A policy
    # limits swaps and reads as its path under every objective.
    show = next(q for q in QUESTIONS if q.name == "model_policy").show
    assert show(answers()) == "the task keeps its model"
    assert advanced_values(answers()).startswith("same model · ")
    for objective in ("speed", "cost"):
        swapping = answers(objective=objective)
        assert show(swapping) == "any model Kiln offers"
        assert advanced_values(swapping).startswith("any model · ")
    # Quality guidance may allow other models, so the text says so.
    guided = answers(guidance="Try the newest DeepSeek release.")
    assert show(guided) == (
        "the task keeps its model, unless the guidance allows other models"
    )
    assert advanced_values(guided).startswith("same model unless guidance allows · ")
    policy = answers(objective="quality", model_policy="./policy.json")
    assert show(policy) == "./policy.json"
    assert advanced_values(policy).startswith("model policy ./policy.json · ")


def test_the_policy_hint_follows_the_objective():
    terminal = ScriptedTerminal(["c", "", "", "4", "4", "0"])
    ask_all(answers(), context(terminal), given=CORE_DESTS)
    assert terminal.asked[1].hint == (
        "Limits the models the tuner may move the task to. Under Quality the"
        " task keeps its model, unless your guidance allows other models."
        " Blank means no limit. examples/model-policy.json shows the format."
    )
    terminal = ScriptedTerminal(["c", "", "", "4", "4", "0"])
    ask_all(answers(objective="cost"), context(terminal), given=CORE_DESTS)
    assert terminal.asked[1].hint == (
        "Limits the models the tuner may move the task to. Blank means any"
        " model Kiln offers. examples/model-policy.json shows the format."
    )


# ---- asking one again ----


def test_ask_one_pre_fills_the_current_answer():
    current = answers(objective="cost", budget_usd=12.5, timeout_mins=90)
    terminal = ScriptedTerminal(["speed", "12.5", "90m"])
    for name in ("objective", "budget", "time"):
        ask_one(name, current, context(terminal))
    assert [asked.default for asked in terminal.asked] == ["cost", "12.5", "90m"]
    assert current.objective == "speed"


def test_ask_one_agents_starts_on_the_current_cli_and_model():
    current = answers(agent_cli="claude -p --model sonnet")
    terminal = ScriptedTerminal(["claude", "sonnet", "xhigh"])
    ask_one("agents", current, context(terminal))
    assert [asked.default for asked in terminal.asked] == ["claude", "sonnet", "high"]
    assert current.agent_cli == "claude -p --model sonnet --effort xhigh"


def test_ask_one_agents_starts_on_the_current_level():
    current = answers(agent_cli="codex exec -c model_reasoning_effort=max")
    terminal = ScriptedTerminal(["codex", "", "max"])
    ask_one("agents", current, context(terminal))
    assert terminal.asked_about("Thinking level for the agents").default == "max"
    assert current.agent_cli == "codex exec -c model_reasoning_effort=max"


def test_ask_one_of_an_advanced_question_prints_the_advanced_line():
    current = answers()
    terminal = ScriptedTerminal(["2"])
    ask_one("max_concurrent_agents", current, context(terminal))
    assert terminal.step_lines() == ["Advanced: 2 agents at once"]


def test_changeable_lists_the_questions_no_flag_answered_in_order():
    names = [q.name for q in changeable({"objective", "noise_check", "kiln_host"})]
    assert names == [
        "guidance",
        "agents",
        "budget",
        "time",
        "model_policy",
        "min_effect",
        "max_concurrent_evals",
        "max_concurrent_agents",
    ]


# ---- Esc goes back ----

ESC = GoBack()  # an answer: Esc at a prompt that offers it
CORE_ONLY = ADVANCED_DESTS  # every advanced question answered by a flag


def test_esc_asks_the_question_before_again_pre_filled_and_takes_its_line():
    terminal = ScriptedTerminal(
        ["speed", "Keep it short.", ESC, "Shorter.", "claude", "opus", "high"]
        + ["50", "12h"]
    )
    got = ask_all(answers(), context(terminal), given=CORE_ONLY)
    assert terminal.questions[:4] == [
        CORE_QUESTIONS[0],
        CORE_QUESTIONS[1],
        CORE_QUESTIONS[2],
        CORE_QUESTIONS[1],
    ]
    assert terminal.asked[3].default == "Keep it short."
    assert got.guidance == "Shorter."
    # The first Guidance line went with the Esc.
    assert terminal.step_lines()[:2] == ["Improve: Speed", 'Guidance: "Shorter."']
    assert len(terminal.step_lines()) == 5


def test_the_first_question_does_not_offer_esc():
    terminal = ScriptedTerminal(["speed", ESC, "cost", "", "claude", "opus", "high"])
    terminal.answers += ["50", "12h"]
    got = ask_all(answers(), context(terminal), given=CORE_ONLY)
    assert [asked.back for asked in terminal.asked[:4]] == [False, True, False, True]
    # Asked again at the first question, pre-filled with its answer.
    assert terminal.asked[2].default == "speed"
    assert got.objective == "cost"
    assert terminal.step_lines()[0] == "Improve: Cost"
    assert [line.split(":")[0] for line in terminal.step_lines()].count("Improve") == 1


def test_every_question_after_the_first_offers_esc():
    terminal = ScriptedTerminal(
        ["speed", "", "claude", "opus", "high", "50", "12h", "c", "", "", "4", "4", "0"]
    )
    ask_all(answers(), context(terminal), given=frozenset())
    assert [asked.back for asked in terminal.asked] == [False] + [True] * 12


def test_esc_at_the_advanced_menu_asks_the_last_core_question_again():
    terminal = ScriptedTerminal(["3h", ESC, "4h", "enter"])
    given = CORE_DESTS - {"timeout_mins"}
    got = ask_all(answers(), context(terminal), given=given)
    assert terminal.questions == [
        "Time limit (1h, 24h, 45m)",
        ADVANCED_PROMPT,
        "Time limit (1h, 24h, 45m)",
        ADVANCED_PROMPT,
    ]
    assert terminal.asked[2].default == "3h"
    assert got.timeout_mins == 240
    assert terminal.step_lines() == ["Time: 4 hours", "Advanced: defaults"]


def test_the_advanced_menu_alone_does_not_offer_esc():
    terminal = ScriptedTerminal(["enter"])
    ask_all(answers(), context(terminal), given=CORE_DESTS)
    assert terminal.asked[0].back is False


def test_esc_at_the_first_advanced_question_asks_the_menu_again():
    terminal = ScriptedTerminal(["c", ESC, "enter"])
    got = ask_all(answers(), context(terminal), given=CORE_DESTS)
    assert terminal.questions == [
        ADVANCED_PROMPT,
        ADVANCED_QUESTIONS[0],
        ADVANCED_PROMPT,
    ]
    assert got == answers()
    assert terminal.step_lines() == ["Advanced: defaults"]


def test_esc_at_an_advanced_question_asks_the_one_before_it_again():
    terminal = ScriptedTerminal(["c", "", "0.25", ESC, "0.5", "4", "4", "0"])
    got = ask_all(answers(), context(terminal), given=CORE_DESTS)
    assert terminal.questions == [
        ADVANCED_PROMPT,
        *ADVANCED_QUESTIONS[:3],
        *ADVANCED_QUESTIONS[1:],
    ]
    assert terminal.asked_about(ADVANCED_QUESTIONS[1]).default == "0.25"
    assert got.min_effect == 0.5
    assert terminal.step_lines() == ["Advanced: minimum effect 50%"]


def test_esc_at_the_first_core_question_after_customize_steps_back_over_all():
    # Esc from the last advanced question back to the time limit, one step
    # at a time: no Advanced line is left from the first pass.
    terminal = ScriptedTerminal(
        ["3h", "c", "", "", "4", "4", ESC, ESC, ESC, ESC, ESC, ESC, "5h", "enter"]
    )
    got = ask_all(answers(), context(terminal), given=CORE_DESTS - {"timeout_mins"})
    assert terminal.questions[-2:] == ["Time limit (1h, 24h, 45m)", ADVANCED_PROMPT]
    assert got.timeout_mins == 300
    assert terminal.step_lines() == ["Time: 5 hours", "Advanced: defaults"]


# ---- Esc inside the agents question ----


def test_esc_at_the_level_asks_the_model_and_then_the_cli_again():
    terminal = ScriptedTerminal(
        ["codex", "gpt-6.1-sol", ESC, ESC, "claude", "sonnet", "max"]
    )
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert terminal.questions == [
        "Which agent CLI should run the tuner's agents?",
        "Model for the agents",
        "Thinking level for the agents",
        "Model for the agents",
        "Which agent CLI should run the tuner's agents?",
        "Model for the agents",
        "Thinking level for the agents",
    ]
    # The agents question is the only one: its CLI has no step before it.
    assert [asked.back for asked in terminal.asked] == [
        *(False, True, True, True),
        *(False, True, True),
    ]
    assert terminal.asked[3].default == "gpt-6.1-sol"
    assert terminal.asked[4].default == "codex"
    # Another CLI starts on its own default model.
    assert terminal.asked[5].default == "opus"
    assert got.agent_cli == "claude -p --model sonnet --effort max"
    assert terminal.step_lines() == [
        "Agents: Claude Code (claude -p) · sonnet · effort max"
    ]


def test_the_same_cli_picked_again_keeps_its_model_and_level():
    current = answers(agent_cli="codex exec -c model_reasoning_effort=max")
    terminal = ScriptedTerminal(
        ["codex", "gpt-6.1-sol", ESC, ESC, "codex", "gpt-6.1-sol", "max"]
    )
    got = ask_all(current, context(terminal), given=AGENTS_ONLY)
    assert terminal.asked[5].default == "gpt-6.1-sol"
    assert terminal.asked[6].default == "max"
    assert got.agent_cli == "codex exec -m gpt-6.1-sol -c model_reasoning_effort=max"


def test_with_one_cli_esc_at_the_model_asks_the_question_before_again():
    terminal = ScriptedTerminal(["Keep it short.", ESC, "Shorter.", "opus", "high"])
    given = EVERY_DEST - {"agent_cli", "guidance"}
    got = ask_all(answers(), context(terminal, which=installed("claude")), given=given)
    assert terminal.questions == [
        CORE_QUESTIONS[1],
        "Model for the agents",
        CORE_QUESTIONS[1],
        "Model for the agents",
        "Thinking level for the agents",
    ]
    assert terminal.asked[1].back is True
    assert got.guidance == "Shorter."
    assert terminal.step_lines() == [
        'Guidance: "Shorter."',
        "Agents: Claude Code (claude -p) · opus · the only agent CLI installed",
    ]


def test_with_one_cli_and_no_question_before_the_model_does_not_offer_esc():
    terminal = ScriptedTerminal(["opus", ESC, "sonnet", "high"])
    got = ask_all(
        answers(), context(terminal, which=installed("claude")), given=AGENTS_ONLY
    )
    assert [asked.back for asked in terminal.asked] == [False, True, False, True]
    assert got.agent_cli == "claude -p --model sonnet"


def test_esc_at_a_typed_name_after_other_goes_back_to_the_list():
    terminal = ScriptedTerminal(["codex", " other", ESC, "gpt-6.1-sol", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert terminal.questions[1:4] == [
        "Model for the agents",
        "Model name for the agents",
        "Model for the agents",
    ]
    assert terminal.asked_about("Model name for the agents").back is True
    assert got.agent_cli == "codex exec -m gpt-6.1-sol"


def test_esc_at_a_typed_level_after_other_goes_back_to_the_levels():
    terminal = ScriptedTerminal(["codex", "", " other", ESC, "max"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert terminal.questions[2:] == [
        "Thinking level for the agents",
        "Thinking level name for the agents",
        "Thinking level for the agents",
    ]
    assert got.agent_cli == "codex exec -c model_reasoning_effort=max"


def test_opencode_esc_at_the_typed_level_and_model_asks_each_again():
    terminal = ScriptedTerminal(
        ["opencode", "zai/glm", ESC, "zai/glm-5", ESC, ESC, "opencode", "", "max"]
    )
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert [(a.kind, a.question) for a in terminal.asked[1:4]] == [
        ("text", "Model for the agents"),
        ("text", "Thinking level for the agents"),
        ("text", "Model for the agents"),
    ]
    assert terminal.asked[3].default == "zai/glm"
    assert terminal.asked[4].default == "high"
    assert terminal.asked[5].default == "zai/glm-5"
    assert terminal.asked[6].default == "opencode"
    # The model typed before Esc is kept for the same CLI.
    assert terminal.asked[7].default == "zai/glm-5"
    assert got.agent_cli == "opencode run --variant max"


def test_ask_one_with_esc_leaves_the_answer_as_it_was():
    current = answers(budget_usd=12.5)
    terminal = ScriptedTerminal([ESC])
    with pytest.raises(GoBack):
        ask_one("budget", current, dataclasses.replace(context(terminal), back=True))
    assert current.budget_usd == 12.5
    assert terminal.step_lines() == []
