"""The guided start's Restrictions question, its summary rows and rerun
flags, and the start's check of the routes that the run calls whatever the
model policy says. Every id is invented."""

from __future__ import annotations

import dataclasses
import shlex

import pytest
from onboarding_fakes import (
    ANSWERED,
    KEEP_AND_START,
    RouteKiln,
    ScriptedTerminal,
    kiln_routes,
    project,
    task,
)
from test_onboarding_flow import EVERY_FLAG, start
from test_onboarding_summary import BASELINE, SELECTION

from kiln_harness_tuner.cli import build_parser, load_model_policy, validate_args
from kiln_harness_tuner.model_catalog import Catalog, CatalogModel, Source
from kiln_harness_tuner.onboarding.errors import GoBack, OnboardingError
from kiln_harness_tuner.onboarding.keys import Need
from kiln_harness_tuner.onboarding.questions import (
    QUESTIONS,
    Answers,
    QuestionContext,
    ask_all,
)
from kiln_harness_tuner.onboarding.restrictions import (
    FAMILIES,
    FAMILIES_QUESTION,
    KINDS_QUESTION,
    PROVIDERS,
    PROVIDERS_QUESTION,
    RESTRICTIONS_QUESTION,
    RETENTION,
    ROUTE_TITLE,
    SET,
    TOOLS,
    TOOLS_QUESTION,
    WEIGHTS,
    WEIGHTS_QUESTION,
    route_problem,
)
from kiln_harness_tuner.onboarding.summary import (
    parser_defaults,
    rerun_command,
    summary_lines,
)
from kiln_harness_tuner.restrictions import Restrictions

OPTIONS = build_parser().option_dests
DEFAULTS = parser_defaults(OPTIONS)
EVERY_DEST = frozenset().union(*(q.dests for q in QUESTIONS))
(RESTRICTIONS,) = [q for q in QUESTIONS if q.name == "restrictions"]
ONLY_RESTRICTIONS = EVERY_DEST - RESTRICTIONS.dests
OK = Source(state="ok")
CATALOG = Catalog(
    kiln=OK,
    models_dev=OK,
    models=[
        CatalogModel(model="m1", display_name="M1", family="alpha"),
        CatalogModel(model="m2", display_name="M2", family="beta"),
    ],
)
OFFERED = [
    {"provider_id": "openai", "provider_name": "OpenAI", "models": [{"id": "a"}]},
    {"provider_id": "groq", "provider_name": "Groq", "models": [{"id": "b"}]},
]
TOOL = "mcp::remote::424242000001::search"
SKILL = "kiln_tool::skill::424242000002"
ESC = GoBack()


def context(
    terminal: ScriptedTerminal,
    *,
    catalog: Catalog = CATALOG,
    tools: tuple[str, ...] = (TOOL, SKILL),
) -> QuestionContext:
    return QuestionContext(
        terminal,
        DEFAULTS,
        load_model_policy,
        baseline_pair=("openai", "a"),
        offered=lambda: OFFERED,
        baseline_tools=tools,
        catalog=lambda: catalog,
    )


def ask(terminal: ScriptedTerminal, current: Answers = DEFAULTS, **kw) -> Answers:
    answers = dataclasses.replace(current)
    return ask_all(answers, context(terminal, **kw), given=ONLY_RESTRICTIONS)


# ---- the question ----


def test_no_restrictions_is_one_answer():
    terminal = ScriptedTerminal(["none"])
    got = ask(terminal)
    assert terminal.questions == [RESTRICTIONS_QUESTION]
    assert got.restrictions == Restrictions()
    assert got.no_restrictions is True
    assert terminal.step_lines() == ["Restrictions: none"]


def test_each_marked_kind_has_its_question():
    terminal = ScriptedTerminal(
        [
            SET,
            [PROVIDERS, WEIGHTS, FAMILIES, RETENTION, TOOLS],
            ["openai"],
            "closed",
            ["beta"],
            [TOOL],
        ]
    )
    got = ask(terminal)
    assert terminal.questions == [
        RESTRICTIONS_QUESTION,
        KINDS_QUESTION,
        PROVIDERS_QUESTION,
        WEIGHTS_QUESTION,
        FAMILIES_QUESTION,
        TOOLS_QUESTION,
    ]
    assert got.restrictions == Restrictions(
        providers=("openai",),
        model_weights="closed",
        exclude_families=("beta",),
        zero_data_retention=True,
        locked_tools=(TOOL,),
    )
    assert got.no_restrictions is False
    # The providers are Kiln's, and the task's own provider starts marked.
    providers = terminal.asked_about(PROVIDERS_QUESTION)
    assert providers.offered == ("openai", "groq")
    assert providers.default == ("openai",)
    assert terminal.asked_about(TOOLS_QUESTION).offered == (TOOL, SKILL)
    assert terminal.asked_about(FAMILIES_QUESTION).offered == ("alpha", "beta")
    assert terminal.step_lines() == [
        "Restrictions: only these providers: openai · closed-weight models only"
        " · no models of these families: beta · zero data retention only"
        f" · these tools must not change: {TOOL}"
    ]


def test_zero_retention_alone_needs_no_other_question():
    terminal = ScriptedTerminal([SET, [RETENTION]])
    got = ask(terminal)
    assert terminal.questions == [RESTRICTIONS_QUESTION, KINDS_QUESTION]
    assert got.restrictions == Restrictions(zero_data_retention=True)


def test_without_a_catalogue_or_tools_their_kinds_are_not_offered():
    terminal = ScriptedTerminal([SET, [RETENTION]])
    ask(terminal, catalog=Catalog.unavailable("off"), tools=())
    assert terminal.asked_about(KINDS_QUESTION).offered == (PROVIDERS, RETENTION)
    assert any("origin of the models" in line for line in terminal.err)
    assert any("has no tools" in line for line in terminal.err)


def test_without_models_dev_the_weights_are_not_offered():
    catalog = CATALOG.model_copy(update={"models_dev": Source(state="unavailable")})
    terminal = ScriptedTerminal([SET, [FAMILIES], ["alpha"]])
    ask(terminal, catalog=catalog)
    offered = terminal.asked_about(KINDS_QUESTION).offered
    assert WEIGHTS not in offered and FAMILIES in offered
    assert any("weights of the models" in line for line in terminal.err)


def test_esc_steps_back_and_a_kind_unmarked_is_dropped():
    terminal = ScriptedTerminal(
        [SET, [PROVIDERS, WEIGHTS], ["groq"], ESC, ESC, [WEIGHTS], "open"]
    )
    got = ask(terminal)
    assert terminal.questions == [
        RESTRICTIONS_QUESTION,
        KINDS_QUESTION,
        PROVIDERS_QUESTION,
        WEIGHTS_QUESTION,
        PROVIDERS_QUESTION,
        KINDS_QUESTION,
        WEIGHTS_QUESTION,
    ]
    assert got.restrictions == Restrictions(model_weights="open")


def test_asked_again_it_starts_from_the_answer():
    current = dataclasses.replace(
        DEFAULTS, providers=["groq"], zero_data_retention=True
    )
    terminal = ScriptedTerminal([SET, [PROVIDERS, RETENTION], ["groq"]])
    ask(terminal, current)
    assert terminal.asked_about(RESTRICTIONS_QUESTION).default == SET
    assert terminal.asked_about(KINDS_QUESTION).default == (PROVIDERS, RETENTION)
    assert terminal.asked_about(PROVIDERS_QUESTION).default == ("groq",)


def test_a_flag_answers_the_question():
    for argv in (["--no-restrictions"], ["--providers", "openai"]):
        given = {OPTIONS[argv[0]].dest}
        assert given <= RESTRICTIONS.dests


def test_no_restrictions_is_refused_with_a_restriction_flag():
    parser = build_parser()
    args = parser.parse_args(["--no-restrictions", "--zero-data-retention"])
    with pytest.raises(SystemExit):
        validate_args(parser, args)


# ---- the summary and the rerun command ----


def stated() -> Answers:
    return dataclasses.replace(
        DEFAULTS,
        providers=["openai", "anthropic"],
        model_weights="open",
        exclude_model_families=["beta"],
        zero_data_retention=True,
        lock_tool=[TOOL, SKILL],
    )


def test_the_summary_shows_the_restrictions_and_what_code_cannot_check():
    lines = dict(summary_lines(SELECTION, stated(), BASELINE, DEFAULTS))
    assert lines["Restrictions"].startswith("only these providers: openai, anthropic")
    assert "cannot check the retention terms" in lines["Not checked"]
    # None stated: no row.
    plain = dict(summary_lines(SELECTION, DEFAULTS, BASELINE, DEFAULTS))
    assert "Restrictions" not in plain and "Not checked" not in plain


def test_the_rerun_command_repeats_each_restriction():
    command = rerun_command(
        "kiln-harness-tuner",
        [],
        option_dests=OPTIONS,
        selection=SELECTION,
        answers=stated(),
    )
    assert (
        "--providers openai,anthropic --model-weights open"
        " --exclude-model-families beta --zero-data-retention"
        f" --lock-tool {TOOL} --lock-tool {SKILL}"
    ) in " ".join(shlex.split(command.replace("\\\n", " ")))
    args = build_parser().parse_args(shlex.split(command.replace("\\\n", " "))[1:])
    assert Answers.from_args(args).restrictions == stated().restrictions


# ---- the start's check of the routes ----


def test_a_provider_the_run_calls_anyway_is_a_problem():
    needs = [
        Need("fireworks_ai", "the baseline run config"),
        Need("openrouter", "the judge of eval Tone"),
        Need("groq", "--model-policy", from_policy=True),
    ]
    problem = route_problem(needs, Restrictions(providers=("openrouter", "groq")))
    assert problem is not None and problem.title == ROUTE_TITLE
    body = "\n".join(problem.body)
    assert "the baseline run config" in body
    assert "eval Tone" not in body  # openrouter is allowed
    assert route_problem(needs, Restrictions()) is None
    retention = route_problem(needs, Restrictions(zero_data_retention=True))
    assert retention is not None
    assert "the judge of eval Tone" in "\n".join(retention.body)
    # A provider that only the model policy names is not called yet.
    assert route_problem(needs[2:], Restrictions(providers=("x",))) is None


def test_a_start_whose_baseline_breaks_a_restriction_stops():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError, match="restrictions refuse"):
        start(kiln, terminal, [*EVERY_FLAG, *ANSWERED, "--providers", "openai"])


def test_a_start_inside_the_restrictions_goes_on():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(KEEP_AND_START)
    argv = [a for a in ANSWERED if a != "--no-restrictions"]
    args = start(kiln, terminal, [*EVERY_FLAG, *argv, "--providers", "fireworks_ai"])
    assert args.providers == ["fireworks_ai"]
    assert "--providers fireworks_ai" in " ".join(terminal.out)


def test_a_locked_tool_that_the_baseline_lacks_stops_the_start():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(interactive=False)
    argv = [a for a in ANSWERED if a != "--no-restrictions"]
    with pytest.raises(OnboardingError, match="locked tool"):
        start(kiln, terminal, [*EVERY_FLAG, *argv, "--lock-tool", TOOL])
