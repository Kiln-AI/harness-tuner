"""`prepare_start` end to end on a fake Kiln: the Kiln check, the project and
task pickers, the rerun line, the readiness check and the API-key check, in
that order."""

import json

import pytest
from onboarding_fakes import (
    FOLDER,
    PROG,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    ScriptedTerminal,
    available_models,
    eval_config,
    kiln_routes,
    memories_path,
    project,
    responses,
    run_config,
    runner_for,
    task,
    task_base,
)

from kiln_harness_tuner.cli import ConfigRenderError, build_parser, load_model_policy
from kiln_harness_tuner.model_policy import ModelPolicy, Rule
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.flow import prepare_start

SUPPORT = project("118230554411", "Support Triage", "/kiln-projects/Support Triage")
SUMMARISE = task("424242100001", "Summarise threads")
RERUN_HEADING = "To run this again without the questions:"
READINESS_PROMPT = "[r]etry  [o]pen the first link  [t]ask: pick another  e[x]it: "
READINESS_READS = ("evals", "run_configs", "specs")
BASE = task_base(PROJECT_ID, TASK_ID)
KEY_PROMPT = "[r]etry  [o]pen AI Providers  e[x]it: "
EVERY_FLAG = [
    "--project-id",
    PROJECT_ID,
    "--task-id",
    TASK_ID,
    "--project-path",
    FOLDER,
]


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
):
    parser = build_parser()
    return prepare_start(
        parser.parse_args(argv),
        argv,
        prog=PROG,
        parser_long_options=parser.long_options,
        policy_loader=policy_loader,
        terminal=terminal,
        runner=runner_for(kiln),
    )


def rerun_lines(terminal: ScriptedTerminal) -> list[str]:
    return [line for line in terminal.out if line.startswith(RERUN_HEADING)]


def test_no_flags_walks_every_check_and_returns_the_filled_flags():
    kiln = two_projects()
    # The Fireworks key is added while the key prompt waits.
    kiln.routes[("GET", "/api/available_models")] = responses(
        available_models(), available_models("fireworks_ai")
    )
    terminal = ScriptedTerminal(["1", "1", "r"])
    args = start(kiln, terminal, ["--guidance", "Keep the output format."])
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert args.guidance == "Keep the output format."
    assert terminal.prompts == [
        "Project [1-2, x to exit]: ",
        "Task [1-2, x to exit]: ",
        KEY_PROMPT,
    ]
    rerun, missing_key = terminal.out[-2:]
    assert rerun == (
        f"{RERUN_HEADING}\n"
        "  kiln-harness-tuner --project-path '/kiln-projects/Forum Moderation'"
        " --project-id 424242975950 --task-id 424242783061"
        " --guidance 'Keep the output format.'"
    )
    assert len(rerun_lines(terminal)) == 1
    assert missing_key == (
        "Kiln has no API key for 1 provider this run needs:\n"
        '  • Fireworks AI (fireworks_ai), used by the task\'s run config "Baseline"\n'
        "Add it in Kiln under AI Providers: http://localhost:8757/settings/providers"
    )
    # The Kiln check, then the lists, then readiness, then the key check,
    # which the retry runs again in full.
    paths = kiln.paths()
    assert paths[:4] == [
        "/ping",
        "/openapi.json",
        "/api/projects",
        f"/api/projects/{PROJECT_ID}/tasks",
    ]
    assert sorted(paths[4:7]) == [f"{BASE}/{read}" for read in READINESS_READS]
    key_check = sorted(
        [
            BASE,
            *(f"{BASE}/{read}" for read in READINESS_READS),
            f"{BASE}/evals/eval_1/eval_config/ec_1",
            memories_path(PROJECT_ID),
            "/api/available_models",
        ]
    )
    first, retry = paths[7 : 7 + len(key_check)], paths[7 + len(key_check) :]
    assert sorted(first) == sorted(retry) == key_check
    assert first[-1] == retry[-1] == "/api/available_models"


def test_every_flag_given_prints_no_rerun_line_and_asks_nothing():
    terminal = ScriptedTerminal(interactive=False)
    args = start(two_projects(), terminal, EVERY_FLAG)
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert terminal.out == [] and terminal.prompts == []


def test_a_single_project_and_task_are_used_and_the_rerun_line_is_printed():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(interactive=False)
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == (PROJECT_ID, TASK_ID)
    assert terminal.out == [
        "Using project Forum Moderation (424242975950), the only project in Kiln.",
        "Using task Moderate posts (424242783061), the only task in Forum Moderation.",
        f"{RERUN_HEADING}\n"
        "  kiln-harness-tuner --project-path '/kiln-projects/Forum Moderation'"
        " --project-id 424242975950 --task-id 424242783061",
    ]


def test_a_task_that_isnt_ready_retries_until_it_is():
    kiln = two_projects(run_configs=responses([], [{"id": "rc_1"}]))
    terminal = ScriptedTerminal(["r"])
    argv = ["--project-id", PROJECT_ID, "--task-id", TASK_ID]
    args = start(kiln, terminal, argv)
    assert args.task_id == TASK_ID
    (message,) = [line for line in terminal.out if "isn't ready" in line]
    assert message.startswith(
        "Task Moderate posts (424242783061) isn't ready to tune:\n"
        "  • It has no run configs."
    )
    # The task came from a flag, so there is no other task to pick.
    assert terminal.prompts == ["[r]etry  [o]pen the first link  e[x]it: "]
    # The rerun line (the path was filled in) comes before the check.
    assert terminal.out.index(rerun_lines(terminal)[0]) < terminal.out.index(message)


def test_t_goes_back_to_the_task_picker():
    kiln = two_projects(evals=[])
    terminal = ScriptedTerminal(["1", "1", "t", "2"])
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == (PROJECT_ID, "424242100001")
    assert terminal.prompts == [
        "Project [1-2, x to exit]: ",
        "Task [1-2, x to exit]: ",
        READINESS_PROMPT,
        "Task [1-2, x to exit]: ",
    ]
    first, second = rerun_lines(terminal)
    assert "--task-id 424242783061" in first
    assert "--task-id 424242100001" in second
    assert "--project-id 424242975950" in second


def test_t_is_not_offered_for_a_task_that_wasnt_picked():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    kiln.routes[("GET", f"{task_base(PROJECT_ID, TASK_ID)}/evals")] = []
    terminal = ScriptedTerminal(["t", "x"])
    with pytest.raises(OnboardingStopped) as stopped:
        start(kiln, terminal, [])
    assert stopped.value.exit_code == 1
    assert terminal.prompts == ["[r]etry  [o]pen the first link  e[x]it: "] * 2


def test_open_opens_the_first_link():
    kiln = two_projects(run_configs=responses([], [{"id": "rc_1"}]))
    terminal = ScriptedTerminal(["o", "r"])
    start(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
    assert terminal.opened == [f"http://localhost:8757/optimize/{PROJECT_ID}/{TASK_ID}"]


def test_not_ready_and_not_interactive_fails_with_the_message():
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(two_projects(evals=[]), terminal, EVERY_FLAG)
    assert str(error.value) == (
        "Task Moderate posts (424242783061) isn't ready to tune:\n"
        "  • It has no eval the loop can measure. Add one in Kiln:"
        f" http://localhost:8757/specs/{PROJECT_ID}/{TASK_ID}"
    )
    assert terminal.prompts == []


def test_the_readiness_warning_is_printed_each_time_the_check_runs():
    kiln = two_projects(
        evals=responses(
            {"evals": [], "load_error_count": 1},
            {"evals": [{"id": "e", "current_config_id": "ec"}], "load_error_count": 1},
        )
    )
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
    assert (
        terminal.err
        == [
            "Kiln couldn't load 1 eval of task Moderate posts, so the run can't"
            " measure it."
        ]
        * 2
    )


def test_ctrl_c_at_the_picker_stops_with_130():
    with pytest.raises(OnboardingStopped) as stopped:
        start(two_projects(), ScriptedTerminal([KeyboardInterrupt()]), [])
    assert stopped.value.exit_code == 130


def test_a_missing_key_when_not_interactive_fails_with_the_message():
    kiln = two_projects()
    kiln.routes[("GET", "/api/available_models")] = available_models("openai")
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(kiln, terminal, EVERY_FLAG)
    assert str(error.value) == (
        "Kiln has no API key for 1 provider this run needs:\n"
        '  • Fireworks AI (fireworks_ai), used by the task\'s run config "Baseline"\n'
        "Add it in Kiln under AI Providers: http://localhost:8757/settings/providers"
    )
    assert terminal.prompts == []


def test_open_opens_the_ai_providers_page():
    kiln = two_projects()
    kiln.routes[("GET", "/api/available_models")] = responses(
        [], available_models("fireworks_ai")
    )
    terminal = ScriptedTerminal(["o", "r"])
    start(kiln, terminal, EVERY_FLAG)
    assert terminal.opened == ["http://localhost:8757/settings/providers"]
    assert terminal.prompts == [KEY_PROMPT] * 2


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
    assert str(error.value).splitlines()[1:-1] == [
        "  • OpenAI (openai), used by --model-policy, or remove it from --model-policy"
    ]


def test_a_bad_policy_file_stops_before_the_key_check(tmp_path):
    bad = tmp_path / "policy.json"
    bad.write_text("{not json")
    kiln = two_projects()
    with pytest.raises(ConfigRenderError, match="--model-policy"):
        start(kiln, ScriptedTerminal(), [*EVERY_FLAG, "--model-policy", str(bad)])
    assert memories_path(PROJECT_ID) not in kiln.paths()
    assert "/api/available_models" not in kiln.paths()


def missing_key_messages(terminal: ScriptedTerminal) -> list[str]:
    return [line for line in terminal.out if line.startswith("Kiln has no API key")]


def test_a_retry_sees_a_judge_changed_in_kiln():
    kiln = two_projects()
    # The user moves Tone to a code eval while the key prompt waits.
    kiln.routes[("GET", f"{BASE}/evals/eval_1/eval_config/ec_1")] = responses(
        eval_config({"eval_steps": ["Is it polite?"]}, model_provider="openai"),
        eval_config(),
    )
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal, EVERY_FLAG)
    assert terminal.prompts == [KEY_PROMPT]
    (message,) = missing_key_messages(terminal)
    assert 'OpenAI (openai), used by eval "Tone"\'s judge' in message


def test_a_retry_sees_a_new_default_run_config():
    kiln = two_projects(
        run_configs=[
            run_config("rc_1", "Old", "openai"),
            run_config("rc_2", "New", "fireworks_ai"),
        ]
    )
    # The user makes the Fireworks run config the default while the key
    # prompt waits.
    kiln.routes[("GET", BASE)] = responses(
        {"id": TASK_ID, "default_run_config_id": "rc_1"},
        {"id": TASK_ID, "default_run_config_id": "rc_2"},
    )
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal, EVERY_FLAG)
    assert terminal.prompts == [KEY_PROMPT]
    (message,) = missing_key_messages(terminal)
    assert 'OpenAI (openai), used by the task\'s run config "Old"' in message


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
    start(two_projects(), terminal, [*EVERY_FLAG, "--model-policy", str(policy)])
    assert terminal.prompts == [KEY_PROMPT]
    (message,) = missing_key_messages(terminal)
    assert "OpenAI (openai), used by --model-policy, or remove it" in message


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
