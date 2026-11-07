"""`prepare_start` end to end on a fake Kiln: the header, the Kiln check,
the demo copy, the project and task pickers, the rerun line, the readiness
check and the API-key check, in that order, each with its step line."""

import json
import shlex

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
from kiln_harness_tuner.model_policy import ModelPolicy, Rule
from kiln_harness_tuner.onboarding.demo import IMPORT_PATH
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.flow import prepare_start

SUPPORT = project("118230554411", "Support Triage", "/kiln-projects/Support Triage")
SUMMARISE = task("424242100001", "Summarise threads")
RERUN_HEADING = "To run this again without the questions:"
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
        home=home,
    )


def rerun_lines(terminal: ScriptedTerminal) -> list[str]:
    return [line for line in terminal.out if line.startswith(RERUN_HEADING)]


def test_no_flags_walks_every_check_and_returns_the_filled_flags():
    kiln = two_projects()
    # The Fireworks key is added while the key menu waits.
    kiln.routes[("GET", "/api/available_models")] = responses(
        available_models(), available_models("fireworks_ai")
    )
    terminal = ScriptedTerminal([PROJECT_ID, TASK_ID, "r"])
    args = start(kiln, terminal, ["--guidance", "Keep the output format."])
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert args.guidance == "Keep the output format."
    assert terminal.transcript == [
        f"header Kiln Harness Tuner {__version__}",
        "busy Checking Kiln at http://localhost:8757…",
        "step Kiln: 1.0.3 at http://localhost:8757",
        "busy Reading projects…",
        "pick Which project do you want to tune?",
        "step Project: Forum Moderation (424242975950)",
        "busy Reading Forum Moderation's tasks…",
        "pick Which task in Forum Moderation?",
        "step Task: Moderate posts (424242783061)",
        f"say {RERUN_HEADING}",
        "busy Checking the task is ready…",
        "step Ready: 1 run config · 1 eval the tuner can measure",
        "busy Checking API keys…",
        f"block {KEY_MISSING}",
        "act What next?",
        "busy Checking API keys…",
        "step API keys: Fireworks AI",
    ]
    assert terminal.out == [
        f"{RERUN_HEADING}\n"
        "  kiln-harness-tuner --project-path '/kiln-projects/Forum Moderation'"
        " --project-id 424242975950 --task-id 424242783061"
        " --guidance 'Keep the output format.'"
    ]
    assert terminal.menus == [("r", "p", "x")]
    assert terminal.blocks[0][1][1] == (
        '  • Fireworks AI: used by the task\'s run config "Baseline"'
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


def test_every_flag_given_and_not_guided_prints_the_steps_and_asks_nothing():
    terminal = ScriptedTerminal(interactive=False)
    args = start(two_projects(), terminal, EVERY_FLAG)
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert terminal.step_lines() == PASSED
    assert terminal.out == [] and terminal.asked == []


def test_a_single_project_and_task_are_used_and_the_rerun_line_is_printed():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(interactive=False)
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == (PROJECT_ID, TASK_ID)
    assert terminal.step_lines()[1:3] == [
        "Project: Forum Moderation (424242975950) · the only project in Kiln",
        "Task: Moderate posts (424242783061) · the only task in this project",
    ]
    assert terminal.out == [
        f"{RERUN_HEADING}\n"
        "  kiln-harness-tuner --project-path '/kiln-projects/Forum Moderation'"
        " --project-id 424242975950 --task-id 424242783061",
    ]


def test_a_task_that_isnt_ready_checks_again_until_it_is():
    kiln = two_projects(run_configs=responses([], [run_config()]))
    terminal = ScriptedTerminal(["r"])
    argv = ["--project-id", PROJECT_ID, "--task-id", TASK_ID]
    args = start(kiln, terminal, argv)
    assert args.task_id == TASK_ID
    assert [title for title, _body in terminal.blocks] == [NOT_READY]
    # The task came from a flag, so there is no other task to pick.
    assert terminal.menus == [("r", "c", "x")]
    # The rerun line (the path was filled in) comes before the check.
    transcript = terminal.transcript
    assert transcript.index(f"say {RERUN_HEADING}") < transcript.index(
        f"block {NOT_READY}"
    )
    assert "step Ready: 1 run config · 1 eval the tuner can measure" in transcript


def test_t_goes_back_to_the_task_picker():
    kiln = two_projects(evals=[])
    terminal = ScriptedTerminal([PROJECT_ID, TASK_ID, "t", "424242100001"])
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == (PROJECT_ID, "424242100001")
    assert terminal.questions == [
        "Which project do you want to tune?",
        "Which task in Forum Moderation?",
        "What next?",
        "Which task in Forum Moderation?",
    ]
    assert terminal.menus == [("r", "e", "t", "x")]
    # The project stays chosen: it is announced once, and only the task again.
    assert terminal.step_lines()[1:4] == [
        "Project: Forum Moderation (424242975950)",
        "Task: Moderate posts (424242783061)",
        "Task: Summarise threads (424242100001)",
    ]
    assert kiln.paths().count("/api/projects") == 1
    first, second = rerun_lines(terminal)
    assert "--task-id 424242783061" in first
    assert "--task-id 424242100001" in second
    assert "--project-id 424242975950" in second


def test_t_then_no_tasks_still_offers_a_different_project():
    kiln = two_projects(evals=[])
    # The project's tasks are gone when the task picker reads them again.
    kiln.routes[("GET", f"/api/projects/{PROJECT_ID}/tasks")] = responses(
        [task(), SUMMARISE], []
    )
    terminal = ScriptedTerminal([PROJECT_ID, TASK_ID, "t", "p", "118230554411"])
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == ("118230554411", "118230000001")
    assert terminal.menus == [("r", "e", "t", "x"), ("o", "r", "p", "x")]


def test_t_is_not_offered_for_a_task_that_wasnt_picked():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    kiln.routes[("GET", f"{BASE}/evals")] = []
    terminal = ScriptedTerminal(["x"])
    with pytest.raises(OnboardingStopped) as stopped:
        start(kiln, terminal, [])
    assert stopped.value.exit_code == 1
    assert terminal.menus == [("r", "e", "x")]


def test_e_opens_evals_in_kiln():
    kiln = two_projects(evals=responses([], [{"id": "e", "current_config_id": "c"}]))
    terminal = ScriptedTerminal(["e", "r"])
    start(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
    assert terminal.opened == [f"{HOST}/specs/{PROJECT_ID}/{TASK_ID}"]
    assert terminal.menus == [("r", "e", "x")] * 2


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
    assert error.value.shown is False


LOAD_WARNING = "Kiln couldn't load 1 eval of this task, so the run can't measure it."


def test_the_readiness_warning_follows_the_block_then_the_step_line():
    kiln = two_projects(
        evals=responses(
            {"evals": [], "load_error_count": 1},
            {"evals": [{"id": "e", "current_config_id": "ec"}], "load_error_count": 1},
        )
    )
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
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


def test_ctrl_c_at_the_picker_stops_with_130():
    with pytest.raises(OnboardingStopped) as stopped:
        start(two_projects(), ScriptedTerminal([KeyboardInterrupt()]), [])
    assert stopped.value.exit_code == 130


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
    start(kiln, terminal, EVERY_FLAG)
    assert terminal.opened == [f"{HOST}/settings/providers"]
    assert terminal.menus == [("r", "p", "x")] * 2


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
    # The user moves Tone to a code eval while the key menu waits.
    kiln.routes[("GET", f"{BASE}/evals/eval_1/eval_config/ec_1")] = responses(
        eval_config({"eval_steps": ["Is it polite?"]}, model_provider="openai"),
        eval_config(),
    )
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal, EVERY_FLAG)
    assert terminal.menus == [("r", "p", "x")]
    (body,) = missing_key_bodies(terminal)
    assert '  • OpenAI: used by eval "Tone"\'s judge' in body
    assert terminal.step_lines()[-1] == "API keys: Fireworks AI"


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
    start(kiln, terminal, EVERY_FLAG)
    (body,) = missing_key_bodies(terminal)
    assert '  • OpenAI: used by the task\'s run config "Old"' in body


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
    assert error.value.shown is interactive
    expected = [(error.value.title, error.value.body)] if interactive else []
    assert terminal.blocks == expected


def demo_kiln() -> RouteKiln:
    """A Kiln with one project of its own, that imports like Kiln does."""
    kiln = RouteKiln(kiln_routes((SUPPORT, [task("118230000001", "Triage tickets")])))
    kiln.routes[("POST", IMPORT_PATH)] = import_project_route(kiln)
    return kiln


DEMO_ARGV = ["--demo-project", "--guidance", "Keep the output format."]


def test_demo_project_copies_imports_and_runs_on_the_copy(tmp_path):
    kiln = demo_kiln()
    terminal = ScriptedTerminal()
    args = start(kiln, terminal, DEMO_ARGV, home=tmp_path)
    folder = tmp_path / "Kiln Projects" / "Forum Moderation"
    copied = json.loads((folder / "project.kiln").read_text(encoding="utf-8"))
    assert (args.project_id, args.task_id, args.project_path) == (
        copied["id"],
        TASK_ID,
        str(folder),
    )
    assert args.project_id not in (PROJECT_ID, SUPPORT["id"])
    assert args.guidance == "Keep the output format."
    assert terminal.asked == []
    command = shlex.join(
        [
            PROG,
            "--project-path",
            str(folder),
            "--project-id",
            args.project_id,
            "--task-id",
            TASK_ID,
            "--guidance",
            "Keep the output format.",
        ]
    )
    assert terminal.out == [f"{RERUN_HEADING}\n  {command}"]
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


def test_a_second_demo_project_makes_a_dash_1_copy(tmp_path):
    kiln = demo_kiln()
    first = start(kiln, ScriptedTerminal(), DEMO_ARGV, home=tmp_path)
    terminal = ScriptedTerminal()
    second = start(kiln, terminal, DEMO_ARGV, home=tmp_path)
    folder = tmp_path / "Kiln Projects" / "Forum Moderation-1"
    assert second.project_path == str(folder)
    assert second.project_id != first.project_id
    assert f"--project-id {second.project_id}" in rerun_lines(terminal)[0]
    listed = kiln.routes[("GET", "/api/projects")]
    assert [row["name"] for row in listed] == [
        "Support Triage",
        "Forum Moderation",
        "Forum Moderation-1",
    ]


def test_demo_project_when_not_guided_asks_nothing(tmp_path):
    terminal = ScriptedTerminal(interactive=False)
    args = start(demo_kiln(), terminal, ["--demo-project"], home=tmp_path)
    assert args.task_id == TASK_ID
    assert terminal.asked == [] and terminal.err == []


def test_no_projects_m_makes_the_demo_and_runs_on_it(tmp_path):
    kiln = RouteKiln(kiln_routes())
    kiln.routes[("POST", IMPORT_PATH)] = import_project_route(kiln)
    terminal = ScriptedTerminal(["m"])
    args = start(kiln, terminal, [], home=tmp_path)
    folder = tmp_path / "Kiln Projects" / "Forum Moderation"
    assert (args.task_id, args.project_path, args.demo_project) == (
        TASK_ID,
        str(folder),
        True,
    )
    assert terminal.menus == [("m", "o", "r", "x")]
    assert [title for title, _body in terminal.blocks] == ["Kiln has no projects yet"]
    assert terminal.step_lines()[1].startswith(
        "Demo: ~/Kiln Projects/Forum Moderation, imported as project"
    )
    # The rerun line runs on the copy, so a rerun makes no second copy.
    (rerun,) = rerun_lines(terminal)
    assert f"--project-id {args.project_id}" in rerun
    assert "--demo-project" not in rerun


def test_no_tasks_p_picks_a_different_project():
    kiln = RouteKiln(kiln_routes((project(), []), (SUPPORT, [task("118230000001")])))
    terminal = ScriptedTerminal([PROJECT_ID, "p", "118230554411"])
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == ("118230554411", "118230000001")
    assert terminal.menus == [("o", "r", "p", "x")]
