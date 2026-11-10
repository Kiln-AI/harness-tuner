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
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.questions import (
    AGENT_KINDS,
    BUDGET_ERROR,
    COUNT_ERROR,
    DURATION_ERROR,
    FRACTION_ERROR,
    MODEL_ERROR,
    OBJECTIVES,
    QUESTIONS,
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
    "Advanced options: any model · 4 evals and 4 agents at once"
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
        ("1", 1),
        ("1.5", 2),  # whole minutes, to the nearest
        ("2.4", 2),
        ("0.1h", 6),
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
    assert terminal.questions == [*CORE_QUESTIONS, ADVANCED_PROMPT]
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
        "What to try first and what not to touch, in your own words. For example:"
        ' "Keep the output format. Don\'t switch models." Enter to skip.',
        "The tuner's own agents (planner, builders, reviewers) run through this CLI"
        " and are billed to its account, not to your Kiln provider keys.",
        "Other to type a model name: an alias or a full name.",
        "How much each agent reasons before it answers.",
        "The most this run may spend on the task's model calls, in USD. The"
        " tuner's agents run on your agent CLI's account and are not counted."
        " No wave starts past it. Type none for no cap.",
        "No wave starts after this, and work still running at the limit is"
        " cancelled, so leave room for a whole wave.",
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
        "Enter uses OpenCode's own default model. A model is PROVIDER/MODEL, for"
        " example zai-coding-plan/glm-5.3-flash. `opencode models` lists them.",
    )
    # Enter on the default leaves the model out of the command.
    assert got.agent_cli == "opencode run"


def test_claude_code_models_are_picked_from_its_list():
    terminal = ScriptedTerminal(["claude", "claude-fable-5-2", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    model = terminal.asked_about("Model for the agents")
    assert model.kind == "pick"
    assert model.offered == (*(m.name for m in CLAUDE_MODELS), " other")
    assert "claude-fable-5-2" in model.offered
    assert model.default == "opus"
    assert model.hint == "Other to type a model name: an alias or a full name."
    assert [o.label for o in terminal.options[-2]][-1] == "Other…"
    assert got.agent_cli == "claude -p --model claude-fable-5-2"


def test_codex_models_are_picked_from_its_list():
    terminal = ScriptedTerminal(["codex", "gpt-6.1-terra", "high"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    model = terminal.asked_about("Model for the agents")
    assert model.offered == ("", *(m.name for m in CODEX_MODELS), " other")
    assert model.default == ""
    assert model.hint == (
        "Other to type a model name. All of Codex's models:"
        " https://developers.openai.com/codex/models"
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
        "Enter uses Codex's own default model. For example gpt-6.1-sol. The"
        " models: https://developers.openai.com/codex/models"
    )
    assert typed.refusals == [MODEL_ERROR]
    assert got.agent_cli == "codex exec -m gpt-7-preview"


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
    terminal = ScriptedTerminal(["codex", "", " other", Typed("very high", "ultra")])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    assert terminal.questions[-1] == "Thinking level name for the agents"
    assert got.agent_cli == "codex exec -c model_reasoning_effort=ultra"


def test_opencode_types_its_variant():
    terminal = ScriptedTerminal(["opencode", "zai/glm", "max"])
    got = ask_all(answers(), context(terminal), given=AGENTS_ONLY)
    level = terminal.asked_about("Thinking level for the agents")
    assert (level.kind, level.default) == ("text", "high")
    assert "--variant" in level.hint
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


# ---- the advanced options ----

CORE_DESTS = EVERY_DEST - ADVANCED_DESTS


def test_keep_leaves_the_advanced_options_as_they_are():
    terminal = ScriptedTerminal(["enter"])
    got = ask_all(answers(noise_check=2), context(terminal), given=CORE_DESTS)
    assert got == answers(noise_check=2)
    assert terminal.questions == [
        "Advanced options: any model · 4 evals and 4 agents at once"
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
        "any model · 1 eval and any number of agents at once"
        " · minimum effect 10% · no noise check"
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
