"""`prepare_start` end to end on a fake Kiln: the welcome, the Kiln check,
the demo copy, the project and task pickers, the readiness check and the
API-key check, each with its step line; then, in a guided start, the
questions, the key check again for a new policy, the summary with the rerun
command, and the start prompt. A start that isn't guided prints the summary
and asks nothing."""

import json
import shlex

import pytest
from onboarding_fakes import (
    ADVANCED_PROMPT,
    ANSWERED,
    CORE_ANSWERED,
    FOLDER,
    KEEP_AND_START,
    PROG,
    PROJECT_ID,
    START_PROMPT,
    SWAP_ADVANCED_PROMPT,
    TASK_ID,
    RouteKiln,
    ScriptedTerminal,
    available_models,
    eval_config,
    import_project_route,
    kiln_routes,
    memories_path,
    project,
    responses,
    run_config,
    runner_for,
    task,
    task_base,
)

from kiln_harness_tuner import __version__
from kiln_harness_tuner.cli import ConfigRenderError, build_parser, load_model_policy
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.model_policy import ModelPolicy, Rule
from kiln_harness_tuner.onboarding.demo import DEMO_GUIDANCE, IMPORT_PATH
from kiln_harness_tuner.onboarding.errors import (
    GoBack,
    OnboardingError,
    OnboardingStopped,
)
from kiln_harness_tuner.onboarding.flow import (
    CHANGE_QUESTION,
    StartReport,
    prepare_start,
)
from kiln_harness_tuner.onboarding.questions import TIME_HINT
from kiln_harness_tuner.onboarding.summary import RERUN_HEADING

SUPPORT = project("118230554411", "Support Triage", "/kiln-projects/Support Triage")
SUMMARISE = task("424242100001", "Summarise threads")
READINESS_READS = ("evals", "run_configs", "specs")
BASE = task_base(PROJECT_ID, TASK_ID)
HOST = "http://localhost:8757"
KEY_MISSING = "Kiln is missing an API key this run needs"
NOT_READY = "Moderate posts isn't ready to tune yet"
EVERY_FLAG = [
    "--project-id",
    PROJECT_ID,
    "--task-id",
    TASK_ID,
    "--project-path",
    FOLDER,
]
PASSED = [
    "Kiln: 1.0.3 at http://localhost:8757",
    "Project: Forum Moderation (424242975950)",
    "Task: Moderate posts (424242783061)",
    "Ready: 1 run config · 1 eval the tuner can measure",
    "API keys: Fireworks AI",
]
DEFAULT_SUMMARY = (
    "\n"
    "Ready to start\n"
    "  Task         Moderate posts (424242783061) in project Forum Moderation"
    " (424242975950)\n"
    '  Starts from  run config "Baseline": llama-v3p1-8b-instruct on Fireworks AI\n'
    "  Improve      Quality\n"
    "  Guidance     none\n"
    "  Agents       Claude Code (claude -p) · opus\n"
    "  Limits       $50.00 on the task's model calls · about 12 hours\n"
    "  Advanced     defaults"
)


def every_cli(name: str) -> str:
    """`shutil.which` on a machine with every agent CLI installed."""
    return f"/usr/local/bin/{name}"


def two_projects(**task_reads) -> RouteKiln:
    """Two projects; the first has two tasks. `task_reads` replaces the
    readiness reads of the demo's task, keyed by route suffix."""
    routes = kiln_routes(
        (project(), [task(), SUMMARISE]),
        (SUPPORT, [task("118230000001", "Triage tickets")]),
    )
    for suffix, body in task_reads.items():
        routes[("GET", f"{task_base(PROJECT_ID, TASK_ID)}/{suffix}")] = body
    return RouteKiln(routes)


def start(
    kiln: RouteKiln,
    terminal: ScriptedTerminal,
    argv: list[str],
    policy_loader=load_model_policy,
    home=None,
    which=every_cli,
    report=None,
):
    parser = build_parser()
    return prepare_start(
        parser.parse_args(argv),
        argv,
        prog=PROG,
        option_dests=parser.option_dests,
        policy_loader=policy_loader,
        terminal=terminal,
        runner=runner_for(kiln),
        home=home,
        which=which,
        report=report,
    )


def start_answered(kiln: RouteKiln, terminal: ScriptedTerminal, argv: list[str], **kw):
    """A start whose core questions are answered by flags, for tests about
    the checks: a guided one keeps the advanced options and starts, after
    the test's own answers."""
    if terminal.interactive:
        terminal.answers += KEEP_AND_START
    return start(kiln, terminal, [*argv, *ANSWERED], **kw)


def summaries(terminal: ScriptedTerminal) -> list[str]:
    return [text for text in terminal.out if text.startswith("\nReady to start")]


def rerun_texts(terminal: ScriptedTerminal) -> list[str]:
    """Each rerun command said, with its heading, as the terminal got it."""
    return [text for text in terminal.out if text.startswith(f"\n{RERUN_HEADING}\n")]


def rerun_commands(terminal: ScriptedTerminal) -> list[str]:
    """Each rerun command printed, as one line, without its heading."""
    heading = f"\n{RERUN_HEADING}\n"
    return [
        " ".join(
            line.strip().removesuffix("\\").strip()
            for line in text.removeprefix(heading).strip().splitlines()
        )
        for text in terminal.out
        if text.startswith(heading)
    ]


# ---- the whole start ----


def test_no_flags_walks_every_check_and_question_and_returns_the_answers():
    kiln = two_projects()
    # The Fireworks key is added while the key menu waits.
    kiln.routes[("GET", "/api/available_models")] = responses(
        available_models(), available_models("fireworks_ai")
    )
    terminal = ScriptedTerminal(
        [
            PROJECT_ID,
            TASK_ID,
            "r",
            "speed",
            "Keep the output format.",
            "codex",
            "gpt-6.1-sol",
            "high",
            "$20",
            "90m",
            "enter",
            "s",
        ]
    )
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert (args.objective, args.guidance, args.agent_cli) == (
        "speed",
        "Keep the output format.",
        "codex exec -m gpt-6.1-sol",
    )
    assert (args.budget_usd, args.timeout_mins) == (20.0, 90)
    assert (args.model_policy, args.min_effect, args.noise_check) == (None, None, 0)
    assert (args.max_concurrent_evals, args.max_concurrent_agents) == (4, 4)
    assert terminal.transcript == [
        f"welcome Kiln Harness Tuner v{__version__}",
        "busy Checking Kiln at http://localhost:8757…",
        "step Kiln: 1.0.3 at http://localhost:8757",
        "busy Reading projects…",
        "pick Which project do you want to tune?",
        "step Project: Forum Moderation (424242975950)",
        "busy Reading Forum Moderation's tasks…",
        "pick Which task in Forum Moderation?",
        "step Task: Moderate posts (424242783061)",
        "busy Checking the task is ready…",
        "step Ready: 1 run config · 1 eval the tuner can measure",
        "busy Checking API keys…",
        f"block {KEY_MISSING}",
        "act What next?",
        "busy Checking API keys…",
        "step API keys: Fireworks AI",
        "pick What should the tuner improve?",
        "step Improve: Speed",
        "text Guidance for the tuner's planner (optional)",
        'step Guidance: "Keep the output format."',
        "busy Looking for agent CLIs…",
        "pick Which agent CLI should run the tuner's agents?",
        "pick Model for the agents",
        "pick Thinking level for the agents",
        "step Agents: Codex (codex exec) · gpt-6.1-sol",
        "text Budget for this run",
        "step Budget: $20.00",
        "text Time limit (1h, 24h, 45m)",
        "step Time: 90 minutes",
        f"act {SWAP_ADVANCED_PROMPT}",
        "step Advanced: defaults",
        "say Ready to start",
        f"say {RERUN_HEADING}",
        f"act {START_PROMPT}",
    ]
    assert terminal.out == [
        "\n"
        "Ready to start\n"
        "  Task         Moderate posts (424242783061) in project Forum Moderation"
        " (424242975950)\n"
        '  Starts from  run config "Baseline": llama-v3p1-8b-instruct on'
        " Fireworks AI\n"
        "  Improve      Speed\n"
        '  Guidance     "Keep the output format."\n'
        "  Agents       Codex (codex exec) · gpt-6.1-sol\n"
        "  Limits       $20.00 on the task's model calls · about 90 minutes\n"
        "  Advanced     defaults",
        f"\n{RERUN_HEADING}\n"
        "  kiln-harness-tuner \\\n"
        "    --project-id 424242975950 --task-id 424242783061 \\\n"
        "    --project-path '/kiln-projects/Forum Moderation' \\\n"
        "    --objective speed \\\n"
        "    --guidance 'Keep the output format.' \\\n"
        "    --agent-cli 'codex exec -m gpt-6.1-sol' \\\n"
        "    --budget-usd 20 --timeout-mins 90 \\\n"
        "    --no-interaction\n",
    ]
    assert terminal.menus == [("r", "p", "x"), ("enter", "c"), ("s", "e", "x")]


def test_a_rerun_with_the_printed_command_asks_nothing_and_gets_the_same_flags():
    terminal = ScriptedTerminal(
        [
            PROJECT_ID,
            TASK_ID,
            "cost",
            "",
            "claude",
            "sonnet",
            "high",
            "none",
            "2h",
            "enter",
        ]
        + ["s"]
    )
    first = start(two_projects(), terminal, ["--kiln-host", HOST])
    (command,) = rerun_commands(terminal)
    argv = shlex.split(command)[1:]
    rerun = ScriptedTerminal(interactive=False)
    again = start(two_projects(), rerun, argv)
    assert vars(again) == {**vars(first), "no_interaction": True}
    assert rerun.asked == []


def test_not_guided_with_no_flags_prints_the_summary_and_takes_the_defaults():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(interactive=False)
    args = start(kiln, terminal, [])
    assert terminal.asked == []
    assert terminal.out == [DEFAULT_SUMMARY]
    assert terminal.step_lines()[1:3] == [
        "Project: Forum Moderation (424242975950) · the only project in Kiln",
        "Task: Moderate posts (424242783061) · the only task in this project",
    ]
    defaults = build_parser().parse_args([])
    for dest in ("objective", "guidance", "agent_cli", "budget_usd", "timeout_mins"):
        assert getattr(args, dest) == getattr(defaults, dest)
    assert (args.budget_usd, args.timeout_mins) == (50.0, 720)


def test_every_flag_given_and_not_guided_prints_the_steps_and_the_summary():
    terminal = ScriptedTerminal(interactive=False)
    args = start(two_projects(), terminal, [*EVERY_FLAG, "--budget-usd", "none"])
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert args.budget_usd is None
    assert terminal.step_lines() == PASSED
    assert terminal.out == [
        DEFAULT_SUMMARY.replace("$50.00 on the task's model calls", "no cap")
    ]
    assert terminal.asked == []


def test_every_question_answered_by_a_flag_is_skipped():
    terminal = ScriptedTerminal(["s"])
    argv = [
        *EVERY_FLAG,
        *ANSWERED,
        "--model-policy",
        "policy.json",
        "--min-effect",
        "0.1",
        "--noise-check",
        "3",
    ]
    args = start(two_projects(), terminal, argv, policy_loader=lambda _path: None)
    # Every question was answered, so there is nothing to change.
    assert terminal.questions == [START_PROMPT]
    assert terminal.menus == [("s", "x")]
    assert (args.model_policy, args.min_effect, args.noise_check) == (
        "policy.json",
        0.1,
        3,
    )
    assert summaries(terminal)[0].endswith(
        "  Advanced     model policy policy.json · minimum effect 10%"
        " · noise check on 3 items per eval"
    )


def test_exit_at_the_start_prompt_stops_with_1():
    terminal = ScriptedTerminal(["enter", "x"])
    with pytest.raises(OnboardingStopped) as stopped:
        start(two_projects(), terminal, [*EVERY_FLAG, *ANSWERED])
    assert stopped.value.exit_code == 1
    assert terminal.questions == [ADVANCED_PROMPT, START_PROMPT]


def test_change_an_answer_asks_it_again_and_shows_the_summary_again():
    terminal = ScriptedTerminal(
        ["", "claude", "opus", "high", "50", "12h", "enter"]
        + ["e", "budget", "$5", "e", "time", "3h", "s"]
    )
    args = start(two_projects(), terminal, [*EVERY_FLAG, "--objective", "speed"])
    assert (args.budget_usd, args.timeout_mins, args.objective) == (5.0, 180, "speed")
    # Only the questions no flag answered can be changed.
    picks = [a for a in terminal.asked if a.kind == "pick"]
    change = picks.index(terminal.asked_about(CHANGE_QUESTION))
    assert picks[change].offered == (
        "guidance",
        "agents",
        "budget",
        "time",
        "model_policy",
        "min_effect",
        "max_concurrent_evals",
        "max_concurrent_agents",
        "noise_check",
    )
    budget = terminal.options[change][2]
    assert (budget.label, budget.columns) == ("Budget", ("$50.00",))
    assert terminal.asked_about("Budget for this run").default == "50"
    assert terminal.asked_about("Time limit (1h, 24h, 45m)").default == "12h"
    limits = [s.splitlines()[-2] for s in summaries(terminal)]
    assert limits == [
        "  Limits       $50.00 on the task's model calls · about 12 hours",
        "  Limits       $5.00 on the task's model calls · about 12 hours",
        "  Limits       $5.00 on the task's model calls · about 3 hours",
    ]
    assert rerun_commands(terminal)[-1].endswith(
        "--budget-usd 5 --timeout-mins 180 --no-interaction"
    )
    # The key check ran once: no answer touched the policy.
    assert terminal.busy_texts.count("Checking API keys…") == 1


# ---- the time limit is approximate ----


def test_the_time_question_says_the_limit_is_approximate_with_no_warning():
    # Every core flag but the time limit: the time is the one question.
    no_time = [a for a in CORE_ANSWERED if a not in ("--timeout-mins", "720")]
    terminal = ScriptedTerminal(["45m", *KEEP_AND_START])
    start(two_projects(), terminal, [*EVERY_FLAG, *no_time])
    assert terminal.asked_about("Time limit (1h, 24h, 45m)").hint == TIME_HINT
    assert "approximate" in TIME_HINT
    # No warning line follows the answer: nothing went wrong.
    time_step = terminal.transcript.index("step Time: 45 minutes")
    assert not terminal.transcript[time_step + 1].startswith("warn ")
    assert terminal.err == []
    assert summaries(terminal)[-1].splitlines()[-2] == (
        "  Limits       $50.00 on the task's model calls · about 45 minutes"
    )
    # The rerun command stays exact.
    assert "--timeout-mins 45" in rerun_commands(terminal)[-1]


def test_a_start_that_is_not_guided_has_no_time_warning():
    terminal = ScriptedTerminal(interactive=False)
    start(two_projects(), terminal, [*EVERY_FLAG, "--timeout-mins", "45"])
    assert terminal.err == []
    (summary,) = summaries(terminal)
    assert "· about 45 minutes" in summary


# ---- Esc at the start prompt and while changing an answer ----

ESC = GoBack()  # an answer: Esc at a prompt that offers it
TIME_QUESTION = "Time limit (1h, 24h, 45m)"


def test_esc_at_start_asks_the_last_question_again():
    terminal = ScriptedTerminal(["enter", ESC, "c", "", "", "4", "4", "2", "s"])
    args = start(two_projects(), terminal, [*EVERY_FLAG, *CORE_ANSWERED])
    assert terminal.questions[:3] == [ADVANCED_PROMPT, START_PROMPT, ADVANCED_PROMPT]
    assert terminal.asked[1].back is True
    assert args.noise_check == 2
    assert len(summaries(terminal)) == 2
    # The first Advanced line went with the Esc.
    assert [line for line in terminal.step_lines() if line.startswith("Adv")] == [
        "Advanced: noise check on 2 items per eval"
    ]


def test_esc_at_start_after_the_last_advanced_question_asks_it_again():
    terminal = ScriptedTerminal(["c", "", "", "4", "4", "0", ESC, "5", "s"])
    args = start(two_projects(), terminal, [*EVERY_FLAG, *CORE_ANSWERED])
    noise = "Noise check: items per eval re-run before wave 1"
    assert terminal.questions[-3:] == [START_PROMPT, noise, START_PROMPT]
    assert terminal.asked_about(noise).default == "0"
    assert args.noise_check == 5


def test_esc_at_start_walks_back_on_from_the_last_question():
    terminal = ScriptedTerminal(
        ["", "claude", "opus", "high", "50", "12h", "enter", ESC, ESC, "3h"]
        + ["enter", "s"]
    )
    argv = [*EVERY_FLAG, "--objective", "speed"]
    args = start(two_projects(), terminal, argv)
    assert terminal.questions[-5:] == [
        START_PROMPT,
        SWAP_ADVANCED_PROMPT,
        TIME_QUESTION,
        SWAP_ADVANCED_PROMPT,
        START_PROMPT,
    ]
    assert terminal.asked[-3].default == "12h"
    assert args.timeout_mins == 180
    assert [line for line in terminal.step_lines() if line.startswith("Time")] == [
        "Time: 3 hours"
    ]


def test_with_nothing_asked_esc_is_not_offered_at_start():
    terminal = ScriptedTerminal(["s"])
    argv = [*EVERY_FLAG, *ANSWERED, "--model-policy", "p.json", "--min-effect", "0.1"]
    start(two_projects(), terminal, [*argv, "--noise-check", "3"], lambda _p: None)
    assert terminal.asked_about(START_PROMPT).back is False


def test_esc_at_the_change_picker_shows_the_summary_again():
    terminal = ScriptedTerminal(["enter", "e", ESC, "s"])
    args = start(two_projects(), terminal, [*EVERY_FLAG, *ANSWERED])
    assert terminal.questions == [
        ADVANCED_PROMPT,
        START_PROMPT,
        CHANGE_QUESTION,
        START_PROMPT,
    ]
    assert terminal.asked_about(CHANGE_QUESTION).back is True
    assert len(summaries(terminal)) == 2
    assert args.noise_check == 0


def test_esc_at_the_changed_question_leaves_the_answer_as_it_was():
    terminal = ScriptedTerminal(["enter", "e", "min_effect", ESC, "s"])
    args = start(two_projects(), terminal, [*EVERY_FLAG, *ANSWERED])
    assert terminal.questions[-2:] == [
        "Smallest improvement worth acting on",
        START_PROMPT,
    ]
    assert terminal.asked_about("Smallest improvement worth acting on").back is True
    assert args.min_effect is None
    assert summaries(terminal)[0] == summaries(terminal)[1]


def test_esc_while_changing_the_agents_steps_back_then_cancels():
    terminal = ScriptedTerminal(
        ["", "claude", "opus", "high", "50", "12h", "enter"]
        + ["e", "agents", "codex", "", ESC, ESC, ESC, "s"]
    )
    args = start(two_projects(), terminal, [*EVERY_FLAG, "--objective", "speed"])
    assert terminal.questions[-6:] == [
        "Which agent CLI should run the tuner's agents?",
        "Model for the agents",
        "Thinking level for the agents",
        "Model for the agents",
        "Which agent CLI should run the tuner's agents?",
        START_PROMPT,
    ]
    assert args.agent_cli == "claude -p"
    assert [line for line in terminal.step_lines() if line.startswith("Agents")] == [
        "Agents: Claude Code (claude -p) · opus"
    ]


# ---- the key check again, for a policy the questions set ----


def policy_naming(*providers: str):
    """A policy loader that loads, for any path, a policy allowing every
    model of `providers`, and records the paths it was given."""
    loaded: list = []

    def load(path):
        loaded.append(path)
        if path is None:
            return None
        return ModelPolicy(allow=tuple(Rule(p, "*") for p in providers))

    return load, loaded


def openai_kiln() -> RouteKiln:
    kiln = two_projects()
    kiln.routes[("GET", "/api/available_models")] = available_models(
        "fireworks_ai", "openai"
    )
    return kiln


CUSTOMIZE_WITH_A_POLICY = ["c", "policy.json", "", "4", "4", "0"]


def test_a_policy_set_in_advanced_runs_the_key_check_again():
    load, loaded = policy_naming("openai")
    terminal = ScriptedTerminal([*CUSTOMIZE_WITH_A_POLICY, "s"])
    args = start(openai_kiln(), terminal, [*EVERY_FLAG, *CORE_ANSWERED], load)
    assert args.model_policy == "policy.json"
    # The first check loads no policy; the field's check and the key check
    # each load the one typed.
    assert loaded == [None, "policy.json", "policy.json"]
    assert [line for line in terminal.step_lines() if "API keys" in line] == [
        "API keys: Fireworks AI",
        "API keys: Fireworks AI, OpenAI",
    ]
    transcript = terminal.transcript
    assert transcript.index("step Advanced: model policy policy.json") < (
        transcript.index("step API keys: Fireworks AI, OpenAI")
    )
    assert "--model-policy policy.json" in rerun_commands(terminal)[0]


def test_a_policy_with_a_missing_key_is_fixed_before_the_summary():
    load, _loaded = policy_naming("openai")
    kiln = two_projects()
    kiln.routes[("GET", "/api/available_models")] = responses(
        available_models("fireworks_ai"),
        available_models("fireworks_ai"),
        available_models("fireworks_ai", "openai"),
    )
    terminal = ScriptedTerminal([*CUSTOMIZE_WITH_A_POLICY, "r", "s"])
    start(kiln, terminal, [*EVERY_FLAG, *CORE_ANSWERED], load)
    (body,) = [body for title, body in terminal.blocks if title == KEY_MISSING]
    assert body[1] == (
        "  • OpenAI: used by --model-policy, or remove it from the model policy"
    )
    assert terminal.transcript.index(f"block {KEY_MISSING}") < (
        terminal.transcript.index("say Ready to start")
    )


def test_a_policy_changed_by_change_an_answer_runs_the_key_check_again():
    load, loaded = policy_naming("openai")
    terminal = ScriptedTerminal(["enter", "e", "model_policy", "policy.json", "s"])
    start(openai_kiln(), terminal, [*EVERY_FLAG, *CORE_ANSWERED], load)
    assert loaded == [None, "policy.json", "policy.json"]
    assert terminal.step_lines()[-2:] == [
        "Advanced: model policy policy.json",
        "API keys: Fireworks AI, OpenAI",
    ]
    advanced = [s.splitlines()[-1] for s in summaries(terminal)]
    assert advanced == [
        "  Advanced     defaults",
        "  Advanced     model policy policy.json",
    ]


def test_a_policy_given_by_flag_is_checked_once():
    load, loaded = policy_naming("openai")
    terminal = ScriptedTerminal(KEEP_AND_START)
    argv = [*EVERY_FLAG, *ANSWERED, "--model-policy", "policy.json"]
    start(openai_kiln(), terminal, argv, load)
    assert loaded == ["policy.json"]
    assert terminal.busy_texts.count("Checking API keys…") == 1


# ---- the checks, in a guided start ----


def test_a_task_that_isnt_ready_checks_again_until_it_is():
    kiln = two_projects(run_configs=responses([], [run_config()]))
    terminal = ScriptedTerminal(["r"])
    argv = ["--project-id", PROJECT_ID, "--task-id", TASK_ID]
    args = start_answered(kiln, terminal, argv)
    assert args.task_id == TASK_ID
    assert [title for title, _body in terminal.blocks] == [NOT_READY]
    # The task came from a flag, so there is no other task to pick.
    assert terminal.problem_menus == [("r", "c", "x")]
    assert "step Ready: 1 run config · 1 eval the tuner can measure" in (
        terminal.transcript
    )


def test_t_goes_back_to_the_task_picker():
    kiln = two_projects(evals=[])
    terminal = ScriptedTerminal([PROJECT_ID, TASK_ID, "t", "424242100001"])
    args = start_answered(kiln, terminal, [])
    assert (args.project_id, args.task_id) == (PROJECT_ID, "424242100001")
    assert terminal.questions[:4] == [
        "Which project do you want to tune?",
        "Which task in Forum Moderation?",
        "What next?",
        "Which task in Forum Moderation?",
    ]
    assert terminal.problem_menus == [("r", "e", "t", "x")]
    # The project stays chosen: it is announced once, and only the task again.
    assert terminal.step_lines()[1:4] == [
        "Project: Forum Moderation (424242975950)",
        "Task: Moderate posts (424242783061)",
        "Task: Summarise threads (424242100001)",
    ]
    assert kiln.paths().count("/api/projects") == 1
    (command,) = rerun_commands(terminal)
    assert "--project-id 424242975950 --task-id 424242100001" in command


def test_t_then_no_tasks_still_offers_a_different_project():
    kiln = two_projects(evals=[])
    # The project's tasks are gone when the task picker reads them again.
    kiln.routes[("GET", f"/api/projects/{PROJECT_ID}/tasks")] = responses(
        [task(), SUMMARISE], []
    )
    terminal = ScriptedTerminal([PROJECT_ID, TASK_ID, "t", "p", "118230554411"])
    args = start_answered(kiln, terminal, [])
    assert (args.project_id, args.task_id) == ("118230554411", "118230000001")
    assert terminal.problem_menus == [("r", "e", "t", "x"), ("o", "r", "p", "x")]


def test_t_is_not_offered_for_a_task_that_wasnt_picked():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    kiln.routes[("GET", f"{BASE}/evals")] = []
    terminal = ScriptedTerminal(["x"])
    with pytest.raises(OnboardingStopped) as stopped:
        start_answered(kiln, terminal, [])
    assert stopped.value.exit_code == 1
    assert terminal.problem_menus == [("r", "e", "x")]


def test_e_opens_evals_in_kiln():
    kiln = two_projects(evals=responses([], [{"id": "e", "current_config_id": "c"}]))
    terminal = ScriptedTerminal(["e", "r"])
    start_answered(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
    assert terminal.opened == [f"{HOST}/specs/{PROJECT_ID}/{TASK_ID}"]
    assert terminal.problem_menus == [("r", "e", "x")] * 2


def test_not_ready_and_not_guided_fails_with_the_plain_message():
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(two_projects(evals=[]), terminal, EVERY_FLAG)
    assert str(error.value) == (
        f"{NOT_READY}\n"
        "  The tuner starts from one of the task's run configs and measures every"
        " change\n"
        "  with its evals. This task is missing:\n"
        "    • An eval it can measure: an active eval with a default judge set.\n"
        f"  Open Evals in Kiln: {HOST}/specs/{PROJECT_ID}/{TASK_ID}"
    )
    assert terminal.out == []


LOAD_WARNING = "Kiln couldn't load 1 eval of this task, so the run can't measure it."


def test_the_readiness_warning_follows_the_block_then_the_step_line():
    kiln = two_projects(
        evals=responses(
            {"evals": [], "load_error_count": 1},
            {"evals": [{"id": "e", "current_config_id": "ec"}], "load_error_count": 1},
        )
    )
    terminal = ScriptedTerminal(["r"])
    start_answered(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
    readiness = terminal.transcript[terminal.transcript.index(f"block {NOT_READY}") :]
    assert readiness[:6] == [
        f"block {NOT_READY}",
        f"warn {LOAD_WARNING}",
        "act What next?",
        "busy Checking the task is ready…",
        "step Ready: 1 run config · 1 eval the tuner can measure",
        f"warn {LOAD_WARNING}",
    ]


def test_the_readiness_warning_not_guided_comes_before_the_error():
    kiln = two_projects(evals={"evals": [], "load_error_count": 1})
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError):
        start(kiln, terminal, EVERY_FLAG)
    assert terminal.err == [LOAD_WARNING]


TWO_EVALS = [
    {"id": "eval_1", "name": "Tone", "current_config_id": "ec_1"},
    {"id": "eval_2", "name": "Accuracy", "current_config_id": "ec_2"},
]
TONE_CONFIG = f"{BASE}/evals/eval_1/eval_config/ec_1"


def tone_answers(status: int) -> KilnAPIError:
    return KilnAPIError(
        "GET",
        TONE_CONFIG,
        status,
        {"error_messages": [": Value error, Invalid Jinja2 template"]},
    )


def with_tone_answering(answer: object) -> RouteKiln:
    """The demo's task with two evals, whose "Tone" config read gives
    `answer`."""
    kiln = two_projects(evals=TWO_EVALS)
    kiln.routes[("GET", f"{BASE}/evals/eval_2/eval_config/ec_2")] = eval_config()
    kiln.routes[("GET", TONE_CONFIG)] = answer
    return kiln


TONE_WARNING = (
    'Kiln can\'t load eval "Tone": Invalid Jinja2 template.\n'
    "  The run leaves it out. To use it, fix its judge in Kiln, or update Kiln if\n"
    f"  the eval was made with a newer version: {HOST}/specs/{PROJECT_ID}/{TASK_ID}"
)


def test_an_eval_kiln_cant_load_is_left_out_of_a_guided_start_with_a_warning():
    terminal = ScriptedTerminal()
    start_answered(with_tone_answering(tone_answers(422)), terminal, EVERY_FLAG)
    ready = terminal.transcript.index(
        "step Ready: 1 run config · 1 eval the tuner can measure (1 left out)"
    )
    assert terminal.transcript[ready + 1] == f"warn {TONE_WARNING}"
    assert terminal.blocks == []
    # The key check reads no judge for it.
    assert "step API keys: Fireworks AI" in terminal.transcript


def test_not_guided_an_eval_kiln_cant_load_warns_on_stderr_and_the_start_goes_on():
    terminal = ScriptedTerminal(interactive=False)
    args = start(with_tone_answering(tone_answers(400)), terminal, EVERY_FLAG)
    assert args.task_id == TASK_ID
    assert terminal.err == [TONE_WARNING]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_any_other_error_on_an_eval_config_stops_the_start(status):
    error = tone_answers(status)
    terminal = ScriptedTerminal()
    with pytest.raises(KilnAPIError) as raised:
        start_answered(with_tone_answering(error), terminal, EVERY_FLAG)
    assert raised.value is error
    assert terminal.blocks == [] and terminal.questions == []


def test_ctrl_c_at_the_picker_stops_with_130():
    with pytest.raises(OnboardingStopped) as stopped:
        start(two_projects(), ScriptedTerminal([KeyboardInterrupt()]), [])
    assert stopped.value.exit_code == 130


def test_ctrl_c_at_a_question_stops_with_130():
    terminal = ScriptedTerminal([KeyboardInterrupt()])
    with pytest.raises(OnboardingStopped) as stopped:
        start(two_projects(), terminal, EVERY_FLAG)
    assert stopped.value.exit_code == 130
    assert terminal.questions == ["What should the tuner improve?"]


def test_a_missing_key_when_not_guided_fails_with_the_message():
    kiln = two_projects()
    kiln.routes[("GET", "/api/available_models")] = available_models("openai")
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(kiln, terminal, EVERY_FLAG)
    assert str(error.value) == (
        f"{KEY_MISSING}\n"
        "  The tuner calls this provider through Kiln, which needs a key for it:\n"
        '    • Fireworks AI: used by the task\'s run config "Baseline"\n'
        "  Add the key in Kiln under Settings → AI Providers.\n"
        f"  Open AI Providers in Kiln: {HOST}/settings/providers"
    )


def test_p_opens_the_ai_providers_page():
    kiln = two_projects()
    kiln.routes[("GET", "/api/available_models")] = responses(
        [], available_models("fireworks_ai")
    )
    terminal = ScriptedTerminal(["p", "r"])
    start_answered(kiln, terminal, EVERY_FLAG)
    assert terminal.opened == [f"{HOST}/settings/providers"]
    assert terminal.problem_menus == [("r", "p", "x")] * 2


def test_the_policy_is_loaded_before_the_key_check_and_its_providers_join_it():
    loaded = []

    def policy_loader(path):
        loaded.append(path)
        return ModelPolicy(allow=(Rule("fireworks_ai", "*"), Rule("openai", "gpt-*")))

    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(
            two_projects(),
            terminal,
            [*EVERY_FLAG, "--model-policy", "policy.json"],
            policy_loader=policy_loader,
        )
    assert loaded == ["policy.json"]
    assert error.value.body[1] == (
        "  • OpenAI: used by --model-policy, or remove it from the model policy"
    )


def test_a_bad_policy_file_stops_before_the_key_check(tmp_path):
    bad = tmp_path / "policy.json"
    bad.write_text("{not json")
    kiln = two_projects()
    with pytest.raises(ConfigRenderError, match="--model-policy"):
        start(kiln, ScriptedTerminal(), [*EVERY_FLAG, "--model-policy", str(bad)])
    assert memories_path(PROJECT_ID) not in kiln.paths()
    assert "/api/available_models" not in kiln.paths()


def missing_key_bodies(terminal: ScriptedTerminal) -> list[tuple[str, ...]]:
    return [body for title, body in terminal.blocks if title == KEY_MISSING]


def test_a_retry_sees_a_judge_changed_in_kiln():
    kiln = two_projects()
    legacy_judge = eval_config(
        {"eval_steps": ["Is it polite?"]}, model_provider="openai"
    )
    # The readiness check reads the config, then the key check does; the user
    # moves Tone to a code eval while the key menu waits.
    kiln.routes[("GET", f"{BASE}/evals/eval_1/eval_config/ec_1")] = responses(
        legacy_judge, legacy_judge, eval_config()
    )
    terminal = ScriptedTerminal(["r"])
    start_answered(kiln, terminal, EVERY_FLAG)
    assert terminal.problem_menus == [("r", "p", "x")]
    (body,) = missing_key_bodies(terminal)
    assert '  • OpenAI: used by eval "Tone"\'s judge' in body
    assert "API keys: Fireworks AI" in terminal.step_lines()


def test_a_retry_sees_a_new_default_run_config():
    kiln = two_projects(
        run_configs=[
            run_config("rc_1", "Old", "openai"),
            run_config("rc_2", "New", "fireworks_ai"),
        ]
    )
    # The user makes the Fireworks run config the default while the key
    # menu waits.
    kiln.routes[("GET", BASE)] = responses(
        {"id": TASK_ID, "default_run_config_id": "rc_1"},
        {"id": TASK_ID, "default_run_config_id": "rc_2"},
    )
    terminal = ScriptedTerminal(["r"])
    start_answered(kiln, terminal, EVERY_FLAG)
    (body,) = missing_key_bodies(terminal)
    assert '  • OpenAI: used by the task\'s run config "Old"' in body
    # The summary names the baseline the retry found.
    assert 'Starts from  run config "New":' in summaries(terminal)[0]


def with_no_baseline_run_config() -> RouteKiln:
    """`two_projects`, with a baseline record that names no run config."""
    record = {
        "id": "424242000001",
        "tags": ["baseline"],  # names no run config
        "scope": f"task::{TASK_ID}",
        "content": None,
    }
    kiln = two_projects()
    kiln.routes[("GET", memories_path(PROJECT_ID))] = {
        "listings": [record],
        "matched": 1,
        "remaining": 0,
    }
    kiln.routes[("GET", f"{memories_path(PROJECT_ID)}/by_ids")] = [record]
    return kiln


def test_the_summary_leaves_out_starts_from_with_no_baseline_run_config():
    terminal = ScriptedTerminal(interactive=False)
    start(with_no_baseline_run_config(), terminal, EVERY_FLAG)
    (summary,) = summaries(terminal)
    assert "Starts from" not in summary
    assert summary.splitlines()[2].startswith("  Task ")
    assert summary.splitlines()[3] == "  Improve      Quality"


def write_policy(path, *providers: str) -> None:
    rules = [{"provider": provider, "model": "*"} for provider in providers]
    path.write_text(json.dumps({"allow": rules}))


def test_a_retry_sees_an_edit_to_the_policy_file(tmp_path):
    policy = tmp_path / "policy.json"
    write_policy(policy, "fireworks_ai", "openai")

    def drop_openai_then_retry():
        write_policy(policy, "fireworks_ai")
        return "r"

    terminal = ScriptedTerminal([drop_openai_then_retry])
    start_answered(
        two_projects(), terminal, [*EVERY_FLAG, "--model-policy", str(policy)]
    )
    (body,) = missing_key_bodies(terminal)
    assert "  • OpenAI: used by --model-policy, or remove it from the model policy" in (
        body
    )


def test_a_policy_file_broken_before_a_retry_stops_the_start(tmp_path):
    policy = tmp_path / "policy.json"
    write_policy(policy, "openai")

    def break_the_file_then_retry():
        policy.write_text("{not json")
        return "r"

    with pytest.raises(ConfigRenderError, match="--model-policy"):
        start(
            two_projects(),
            ScriptedTerminal([break_the_file_then_retry]),
            [*EVERY_FLAG, "--model-policy", str(policy)],
        )


@pytest.mark.parametrize("interactive", [True, False])
def test_an_error_is_shown_as_a_block_only_in_a_guided_start(interactive):
    terminal = ScriptedTerminal(interactive=interactive)
    with pytest.raises(OnboardingError) as error:
        start(two_projects(), terminal, ["--project-id", "999"])
    assert error.value.title == "Kiln has no project 999"
    expected = [(error.value.title, error.value.body)] if interactive else []
    assert terminal.blocks == expected


# ---- the start report ----


def test_the_start_report_names_the_choice_and_repeats_the_last_texts():
    reports: list[StartReport] = []
    terminal = ScriptedTerminal(
        ["", "claude", "opus", "high", "50", "12h", "enter", "e", "budget", "$5", "s"]
    )
    argv = [*EVERY_FLAG, "--objective", "speed"]
    start(two_projects(), terminal, argv, report=reports.append)
    first, last = summaries(terminal)
    assert first != last  # the change of budget is in the last one only
    assert reports == [
        StartReport(
            project_name="Forum Moderation",
            task_name="Moderate posts",
            baseline_rc_id="rc_1",
            baseline_name="Baseline",
            summary=last,
            rerun=rerun_texts(terminal)[-1],
        )
    ]
    assert "--budget-usd 5" in reports[0].rerun


def test_the_start_report_of_a_start_that_isnt_guided_has_no_rerun():
    reports: list[StartReport] = []
    terminal = ScriptedTerminal(interactive=False)
    start(two_projects(), terminal, EVERY_FLAG, report=reports.append)
    assert terminal.out == [DEFAULT_SUMMARY]
    assert reports == [
        StartReport(
            project_name="Forum Moderation",
            task_name="Moderate posts",
            baseline_rc_id="rc_1",
            baseline_name="Baseline",
            summary=DEFAULT_SUMMARY,
            rerun=None,
        )
    ]


def test_the_start_report_with_no_baseline_run_config_names_none():
    reports: list[StartReport] = []
    terminal = ScriptedTerminal(interactive=False)
    start(with_no_baseline_run_config(), terminal, EVERY_FLAG, report=reports.append)
    (report,) = reports
    assert (report.baseline_rc_id, report.baseline_name) == (None, None)


def test_no_start_report_when_the_start_stops():
    reports: list[StartReport] = []
    with pytest.raises(OnboardingStopped):
        start(
            two_projects(),
            ScriptedTerminal(["enter", "x"]),
            [*EVERY_FLAG, *ANSWERED],
            report=reports.append,
        )
    with pytest.raises(OnboardingError):
        start(
            two_projects(),
            ScriptedTerminal(interactive=False),
            ["--project-id", "999"],
            report=reports.append,
        )
    assert reports == []


# ---- the demo ----


def demo_kiln() -> RouteKiln:
    """A Kiln with one project of its own, that imports like Kiln does."""
    kiln = RouteKiln(kiln_routes((SUPPORT, [task("118230000001", "Triage tickets")])))
    kiln.routes[("POST", IMPORT_PATH)] = import_project_route(kiln)
    return kiln


def test_demo_project_copies_imports_and_runs_on_the_copy(tmp_path):
    kiln = demo_kiln()
    terminal = ScriptedTerminal()
    args = start_answered(kiln, terminal, ["--demo-project"], home=tmp_path)
    folder = tmp_path / "Kiln Projects" / "Forum Moderation"
    copied = json.loads((folder / "project.kiln").read_text(encoding="utf-8"))
    assert (args.project_id, args.task_id, args.project_path) == (
        copied["id"],
        TASK_ID,
        str(folder),
    )
    assert args.project_id not in (PROJECT_ID, SUPPORT["id"])
    assert terminal.questions == [ADVANCED_PROMPT, START_PROMPT]
    # The rerun command runs on the copy, so a rerun makes no second copy.
    (command,) = rerun_commands(terminal)
    assert command.startswith(
        f"{PROG} --project-id {args.project_id} --task-id {TASK_ID}"
        f" --project-path {shlex.quote(str(folder))} "
    )
    assert "--demo-project" not in command
    assert terminal.step_lines()[1:4] == [
        "Demo: ~/Kiln Projects/Forum Moderation, imported as project"
        f" {args.project_id}",
        f"Project: Forum Moderation ({args.project_id})",
        "Task: Forum Moderation (424242783061)",
    ]
    # The Kiln check, then the copy and its import, then the choice, which
    # finds the copy in Kiln, then readiness and the key check.
    base = task_base(args.project_id, TASK_ID)
    paths = kiln.paths()
    assert paths[:6] == [
        "/ping",
        "/openapi.json",
        "/api/projects",
        IMPORT_PATH,
        "/api/projects",
        f"/api/projects/{args.project_id}/tasks",
    ]
    assert sorted(paths[6:9]) == [f"{base}/{read}" for read in READINESS_READS]
    assert paths[-1] == "/api/available_models"


def test_the_demo_pre_fills_its_guidance(tmp_path):
    terminal = ScriptedTerminal(["quality", DEMO_GUIDANCE])
    with pytest.raises(OnboardingStopped):  # the answers run out at the agents
        start(demo_kiln(), terminal, ["--demo-project"], home=tmp_path)
    asked = terminal.asked_about("Guidance for the tuner's planner (optional)")
    assert asked.default == DEMO_GUIDANCE
    assert f'Guidance: "{DEMO_GUIDANCE}"' in terminal.step_lines()


def test_a_second_demo_project_makes_a_dash_1_copy(tmp_path):
    kiln = demo_kiln()
    first = start_answered(kiln, ScriptedTerminal(), ["--demo-project"], home=tmp_path)
    terminal = ScriptedTerminal()
    second = start_answered(kiln, terminal, ["--demo-project"], home=tmp_path)
    folder = tmp_path / "Kiln Projects" / "Forum Moderation-1"
    assert second.project_path == str(folder)
    assert second.project_id != first.project_id
    assert f"--project-id {second.project_id}" in rerun_commands(terminal)[0]
    listed = kiln.routes[("GET", "/api/projects")]
    assert [row["name"] for row in listed] == [
        "Support Triage",
        "Forum Moderation",
        "Forum Moderation-1",
    ]


def test_demo_project_when_not_guided_asks_nothing_and_takes_the_default_guidance(
    tmp_path,
):
    terminal = ScriptedTerminal(interactive=False)
    args = start(demo_kiln(), terminal, ["--demo-project"], home=tmp_path)
    assert args.task_id == TASK_ID
    assert args.guidance == ""
    assert terminal.asked == [] and terminal.err == []


def test_no_projects_m_makes_the_demo_and_runs_on_it(tmp_path):
    kiln = RouteKiln(kiln_routes())
    kiln.routes[("POST", IMPORT_PATH)] = import_project_route(kiln)
    terminal = ScriptedTerminal(["m"])
    args = start_answered(kiln, terminal, [], home=tmp_path)
    folder = tmp_path / "Kiln Projects" / "Forum Moderation"
    assert (args.task_id, args.project_path, args.demo_project) == (
        TASK_ID,
        str(folder),
        True,
    )
    assert terminal.problem_menus == [("m", "o", "r", "x")]
    assert [title for title, _body in terminal.blocks] == ["Kiln has no projects yet"]
    assert terminal.step_lines()[1].startswith(
        "Demo: ~/Kiln Projects/Forum Moderation, imported as project"
    )
    (command,) = rerun_commands(terminal)
    assert f"--project-id {args.project_id}" in command
    assert "--demo-project" not in command


def test_no_projects_m_pre_fills_the_demo_guidance(tmp_path):
    kiln = RouteKiln(kiln_routes())
    kiln.routes[("POST", IMPORT_PATH)] = import_project_route(kiln)
    terminal = ScriptedTerminal(["m", "quality", DEMO_GUIDANCE])
    with pytest.raises(OnboardingStopped):
        start(kiln, terminal, [], home=tmp_path)
    asked = terminal.asked_about("Guidance for the tuner's planner (optional)")
    assert asked.default == DEMO_GUIDANCE


def test_no_tasks_p_picks_a_different_project():
    kiln = RouteKiln(kiln_routes((project(), []), (SUPPORT, [task("118230000001")])))
    terminal = ScriptedTerminal([PROJECT_ID, "p", "118230554411"])
    args = start_answered(kiln, terminal, [])
    assert (args.project_id, args.task_id) == ("118230554411", "118230000001")
    assert terminal.problem_menus == [("o", "r", "p", "x")]
