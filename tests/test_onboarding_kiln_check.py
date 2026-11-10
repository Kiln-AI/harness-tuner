"""The Kiln check: Kiln answers `/ping`, and its API list has what the tuner
needs. Through `check_kiln` directly, and through `prepare_start` for the
prompt and the exit paths."""

import asyncio

import httpx
import pytest
import respx
from onboarding_fakes import (
    ADVANCED_PROMPT,
    ANSWERED,
    FOLDER,
    HOST,
    KEEP_AND_START,
    PROG,
    PROJECT_ID,
    START_PROMPT,
    TASK_ID,
    RouteKiln,
    ScriptedTerminal,
    flags,
    one_project_routes,
    openapi,
    responses,
    runner_for,
)

from kiln_harness_tuner import __version__
from kiln_harness_tuner.cli import build_parser, load_model_policy
from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.flow import kiln_runner, prepare_start
from kiln_harness_tuner.onboarding.kiln_check import (
    OPENAPI_TIMEOUT_SECS,
    PING_TIMEOUT_SECS,
    UNREADABLE_API_LIST_WARNING,
    check_kiln,
    missing_apis,
)

NOT_RUNNING = (
    "Kiln isn't running at http://localhost:8757",
    (
        "The tuner works through the Kiln app, so Kiln has to be open on this"
        " computer.",
        "  1. Install Kiln if you don't have it: https://kiln.tech/download",
        "  2. Open the Kiln app and wait for its window to appear.",
        "Kiln on another port? Exit and pass --kiln-host http://localhost:<port>.",
    ),
)
KILN_CHECK_PATHS = ("/ping", "/openapi.json")
DOWNLOAD = "https://kiln.tech/download"


def make_kiln(ping: object = "pong", api: object = None) -> RouteKiln:
    """A Kiln with one ready task, whose `/ping` and `/openapi.json` answer
    as given."""
    return RouteKiln(
        {
            **one_project_routes(),
            ("GET", "/ping"): ping,
            ("GET", "/openapi.json"): openapi() if api is None else api,
        }
    )


def check_paths(kiln: RouteKiln) -> list[str]:
    """The Kiln check's own calls, without the later checks' reads."""
    return [path for path in kiln.paths() if path in KILN_CHECK_PATHS]


def start(kiln: RouteKiln, terminal: ScriptedTerminal, host: str = HOST):
    """`prepare_start` with every project flag and core question given, so a
    guided start that gets past the Kiln check asks only the Advanced menu,
    kept, and whether to start, and starts."""
    args = flags(PROJECT_ID, TASK_ID, FOLDER, kiln_host=host)
    if terminal.interactive:
        terminal.answers += KEEP_AND_START
    returned = prepare_start(
        args,
        ANSWERED,
        prog=PROG,
        option_dests=build_parser().option_dests,
        policy_loader=load_model_policy,
        terminal=terminal,
        runner=runner_for(kiln),
    )
    assert returned is args
    return returned


def refused() -> KilnTransportError:
    return KilnTransportError("GET /ping failed after 1 attempts: ConnectError")


def test_a_running_new_kiln_passes_with_its_step_line():
    kiln = make_kiln()
    terminal = ScriptedTerminal()
    start(kiln, terminal)
    assert terminal.welcomes == [
        (
            "Kiln Harness Tuner",
            __version__,
            "Automatically improves an AI agent by tuning its harness: the"
            " prompt, model, tools, skills and settings around it.",
        )
    ]
    assert terminal.steps[0] == ("ok", "Kiln", "1.0.3 at http://localhost:8757")
    assert terminal.busy_texts[0] == "Checking Kiln at http://localhost:8757…"
    assert terminal.blocks == [] and terminal.err == []
    assert terminal.questions == [ADVANCED_PROMPT, START_PROMPT]
    assert check_paths(kiln) == ["/ping", "/openapi.json"]


@pytest.mark.parametrize("version", ["unknown", " Unknown ", "", None, 3])
def test_the_step_line_leaves_out_a_version_that_says_nothing(version):
    terminal = ScriptedTerminal()
    start(make_kiln(api=openapi(version=version)), terminal)
    assert terminal.steps[0] == ("ok", "Kiln", "running at http://localhost:8757")


def test_kiln_not_running_check_again_succeeds():
    kiln = make_kiln(ping=responses(refused(), "pong"))
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal)
    assert terminal.blocks == [NOT_RUNNING]
    assert terminal.problem_menus == [("r", "d", "x")]
    assert terminal.busy_texts[:2] == ["Checking Kiln at http://localhost:8757…"] * 2
    assert terminal.steps[0][:2] == ("ok", "Kiln")
    assert check_paths(kiln) == ["/ping", "/ping", "/openapi.json"]


def test_a_check_again_that_still_fails_shows_the_block_again():
    kiln = make_kiln(ping=responses(refused(), refused(), "pong"))
    terminal = ScriptedTerminal(["r", "r"])
    start(kiln, terminal)
    assert terminal.blocks == [NOT_RUNNING] * 2


def test_kiln_not_running_menu_labels():
    problem = asyncio.run(check_kiln(make_kiln(ping=refused()), HOST)).problem
    assert [(a.key, a.label) for a in problem.menu()] == [
        ("r", "Check again (Kiln is open now)"),
        ("d", "Open the download page"),
        ("x", "Exit"),
    ]


def test_kiln_not_running_exit_stops_with_1():
    kiln = make_kiln(ping=refused())
    with pytest.raises(OnboardingStopped) as stopped:
        start(kiln, ScriptedTerminal(["x"]))
    assert stopped.value.exit_code == 1
    assert kiln.paths() == ["/ping"]


def test_kiln_not_running_d_opens_the_download_page():
    kiln = make_kiln(ping=responses(refused(), "pong"))
    terminal = ScriptedTerminal(["d", "r"])
    start(kiln, terminal)
    assert terminal.opened == [DOWNLOAD]
    assert terminal.problem_menus == [("r", "d", "x")] * 2


def test_kiln_not_running_not_guided_fails_with_the_plain_message():
    kiln = make_kiln(ping=refused())
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(kiln, terminal)
    title, body = NOT_RUNNING
    assert str(error.value) == "\n".join([title, *(f"  {line}" for line in body)])
    assert error.value.shown is False
    assert kiln.paths() == ["/ping"]


async def test_ping_and_the_api_list_are_one_try_each():
    kiln = make_kiln()
    await check_kiln(kiln, HOST)
    ping, api_list = kiln.calls
    assert (ping.timeout_secs, ping.retries) == (PING_TIMEOUT_SECS, False)
    assert PING_TIMEOUT_SECS == 5.0
    assert (api_list.timeout_secs, api_list.retries) == (OPENAPI_TIMEOUT_SECS, False)


@pytest.mark.parametrize(
    "ping,answer",
    [
        (KilnAPIError("GET", "/ping", 404, {"detail": "Not Found"}), "HTTP 404"),
        (KilnAPIError("GET", "/ping", 502, "Bad Gateway"), "HTTP 502"),
        ("<html>hello</html>", "an unexpected answer"),
        ({"status": "ok"}, "an unexpected answer"),
    ],
)
async def test_something_else_on_the_port(ping, answer):
    kiln = make_kiln(ping=ping)
    result = await check_kiln(kiln, HOST)
    assert result.problem is not None
    assert result.problem.title == (
        "Something other than Kiln is answering at http://localhost:8757"
    )
    assert result.problem.body == (
        f"GET /ping returned {answer}, so this isn't the Kiln app.",
        "  1. If Kiln is running on another port, exit and pass --kiln-host with"
        " its address.",
        "  2. Otherwise close the program using this port, then open the Kiln app.",
    )
    assert [(a.key, a.label, a.url) for a in result.problem.menu()] == [
        ("r", "Check again", None),
        ("d", "Open the download page", DOWNLOAD),
        ("x", "Exit", None),
    ]
    assert result.warning is None
    assert kiln.paths() == ["/ping"]


@pytest.mark.parametrize(
    "turned_off,label",
    [
        ("memories", "the memory API"),
        ("eval_jobs", "the eval-job API"),
        ("provenance", "run-config provenance"),
    ],
)
def test_missing_apis_names_each_absent_api(turned_off, label):
    assert missing_apis(openapi(**{turned_off: False})) == [label]


def test_missing_apis_all_present():
    assert missing_apis(openapi()) == []


def test_missing_apis_keeps_the_required_order():
    document = openapi(memories=False, eval_jobs=False, provenance=False)
    assert missing_apis(document) == [
        "the memory API",
        "the eval-job API",
        "run-config provenance",
    ]


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"paths": [], "components": "nope"},
        {"components": {"schemas": {"CreateTaskRunConfigRequest": None}}},
        {"components": {"schemas": {"CreateTaskRunConfigRequest": {"type": "x"}}}},
    ],
)
def test_missing_apis_counts_a_missing_or_odd_level_as_absent(document):
    assert missing_apis(document) == [
        "the memory API",
        "the eval-job API",
        "run-config provenance",
    ]


async def test_too_old_kiln_names_each_missing_api_and_the_version():
    kiln = make_kiln(api=openapi(memories=False, provenance=False))
    result = await check_kiln(kiln, HOST)
    assert result.problem is not None
    assert result.problem.title == "Kiln 1.0.3 is too old for the tuner"
    assert result.problem.body == (
        "The tuner needs the memory API and run-config provenance, which this"
        " Kiln doesn't have.",
        "  1. Update Kiln: in the app, open Settings → Check for update,",
        "     or download the latest version from https://kiln.tech/download.",
        "  2. Quit and reopen Kiln so the update takes effect.",
    )
    assert [(a.key, a.label, a.url) for a in result.problem.menu()] == [
        ("r", "Check again (I've updated and reopened Kiln)", None),
        ("u", "Open Kiln's update page", f"{HOST}/settings/check_for_update"),
        ("d", "Open the download page", DOWNLOAD),
        ("x", "Exit", None),
    ]
    assert (result.version, result.warning) == ("1.0.3", None)


async def test_too_old_kiln_names_only_the_api_it_lacks():
    result = await check_kiln(make_kiln(api=openapi(eval_jobs=False)), HOST)
    assert result.problem is not None
    assert result.problem.body[0] == (
        "The tuner needs the eval-job API, which this Kiln doesn't have."
    )


@pytest.mark.parametrize("version", ["unknown", " Unknown ", "", None, 3])
async def test_too_old_kiln_leaves_out_a_version_that_says_nothing(version):
    kiln = make_kiln(api=openapi(version=version, eval_jobs=False))
    result = await check_kiln(kiln, HOST)
    assert result.problem is not None
    assert result.problem.title == "This Kiln is too old for the tuner"
    assert result.version is None


def test_too_old_u_opens_kilns_update_page():
    kiln = make_kiln(api=responses(openapi(eval_jobs=False), openapi()))
    terminal = ScriptedTerminal(["u", "r"])
    start(kiln, terminal)
    assert terminal.opened == [f"{HOST}/settings/check_for_update"]
    assert terminal.problem_menus == [("r", "u", "d", "x")] * 2


def test_too_old_not_guided_fails_with_the_title_and_the_body():
    kiln = make_kiln(api=openapi(eval_jobs=False))
    with pytest.raises(OnboardingError) as error:
        start(kiln, ScriptedTerminal(interactive=False))
    assert str(error.value) == (
        "Kiln 1.0.3 is too old for the tuner\n"
        "  The tuner needs the eval-job API, which this Kiln doesn't have.\n"
        "    1. Update Kiln: in the app, open Settings → Check for update,\n"
        "       or download the latest version from https://kiln.tech/download.\n"
        "    2. Quit and reopen Kiln so the update takes effect.\n"
        "  Open Kiln's update page: http://localhost:8757/settings/check_for_update"
    )


@pytest.mark.parametrize(
    "api_list",
    [
        KilnAPIError("GET", "/openapi.json", 500, "boom"),
        KilnAPIError("GET", "/openapi.json", 404, {"detail": "Not Found"}),
        KilnTransportError("GET /openapi.json failed after 1 attempts"),
        "not json at all",
        ["a", "list"],
    ],
    ids=["500", "404", "transport", "text", "list"],
)
async def test_an_unreadable_api_list_is_a_warning_only(api_list):
    result = await check_kiln(make_kiln(api=api_list), HOST)
    assert result.problem is None
    assert result.warning == UNREADABLE_API_LIST_WARNING


def test_the_unreadable_api_list_warning_follows_the_step_and_the_start_goes_on():
    kiln = make_kiln(api="not json at all")
    terminal = ScriptedTerminal()
    start(kiln, terminal)
    assert terminal.steps[0] == ("ok", "Kiln", "running at http://localhost:8757")
    assert terminal.err == [
        "Couldn't read Kiln's API list, so its version wasn't checked."
    ]
    assert terminal.blocks == []
    assert terminal.questions == [ADVANCED_PROMPT, START_PROMPT]


def test_with_no_terminal_the_start_is_plain(capsys):
    prepare_start(
        flags(PROJECT_ID, TASK_ID, FOLDER),
        [],
        prog=PROG,
        option_dests=build_parser().option_dests,
        policy_loader=load_model_policy,
        runner=runner_for(make_kiln(api="not json")),
    )
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == "Kiln      running at http://localhost:8757"
    assert captured.err == (
        "kiln-harness-tuner: Couldn't read Kiln's API list,"
        " so its version wasn't checked.\n"
    )


def test_a_trailing_slash_on_the_host_is_dropped():
    kiln = make_kiln(ping=refused())
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(kiln, terminal, host="http://kiln.local:9000/")
    assert error.value.title == "Kiln isn't running at http://kiln.local:9000"


def test_ctrl_c_during_a_kiln_read_stops_with_130():
    def interrupted(_fn):
        raise KeyboardInterrupt

    with pytest.raises(OnboardingStopped) as stopped:
        prepare_start(
            flags(),
            [],
            prog=PROG,
            option_dests=build_parser().option_dests,
            policy_loader=load_model_policy,
            terminal=ScriptedTerminal(),
            runner=interrupted,
        )
    assert stopped.value.exit_code == 130


def test_ctrl_c_at_the_prompt_stops_with_130():
    kiln = make_kiln(ping=refused())
    with pytest.raises(OnboardingStopped) as stopped:
        start(kiln, ScriptedTerminal([KeyboardInterrupt()]))
    assert stopped.value.exit_code == 130


def test_kiln_runner_runs_on_a_fresh_client_for_the_host():
    async def ping(kiln):
        return await kiln.call("GET", "/ping")

    with respx.mock:
        route = respx.get("http://kiln.test:8757/ping").mock(
            return_value=httpx.Response(200, json="pong")
        )
        run = kiln_runner("http://kiln.test:8757")
        assert run(ping) == "pong"
        assert run(ping) == "pong"
    assert route.call_count == 2
