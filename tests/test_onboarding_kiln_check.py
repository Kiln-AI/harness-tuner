"""The Kiln check: Kiln answers `/ping`, and its API list has what the tuner
needs. Through `check_kiln` directly, and through `prepare_start` for the
prompt and the exit paths."""

import httpx
import pytest
import respx
from onboarding_fakes import (
    FOLDER,
    HOST,
    PROG,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    ScriptedTerminal,
    flags,
    one_project_routes,
    openapi,
    responses,
    runner_for,
)

from kiln_harness_tuner.cli import load_model_policy
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
    "Kiln isn't running at http://localhost:8757.\n"
    "Start the Kiln app. If you don't have it, download it from https://kiln.tech.\n"
    "If Kiln runs on another port, pass --kiln-host."
)
KILN_PROMPT = "[r]etry  [o]pen kiln.tech  e[x]it: "
KILN_CHECK_PATHS = ("/ping", "/openapi.json")


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
    """`prepare_start` with every project flag given, so a start that gets
    past the Kiln check asks nothing more and prints nothing more."""
    args = flags(PROJECT_ID, TASK_ID, FOLDER, kiln_host=host)
    returned = prepare_start(
        args,
        [],
        prog=PROG,
        parser_long_options=[],
        policy_loader=load_model_policy,
        terminal=terminal,
        runner=runner_for(kiln),
    )
    assert returned is args
    return returned


def refused() -> KilnTransportError:
    return KilnTransportError("GET /ping failed after 1 attempts: ConnectError")


def test_a_running_new_kiln_passes_without_a_word():
    kiln = make_kiln()
    terminal = ScriptedTerminal()
    start(kiln, terminal)
    assert terminal.out == [] and terminal.err == [] and terminal.prompts == []
    assert check_paths(kiln) == ["/ping", "/openapi.json"]


def test_kiln_not_running_interactive_retry_succeeds():
    kiln = make_kiln(ping=responses(refused(), "pong"))
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal)
    assert terminal.out == [NOT_RUNNING]
    assert terminal.prompts == [KILN_PROMPT]
    assert check_paths(kiln) == ["/ping", "/ping", "/openapi.json"]


def test_kiln_not_running_interactive_exit_stops_with_1():
    kiln = make_kiln(ping=refused())
    with pytest.raises(OnboardingStopped) as stopped:
        start(kiln, ScriptedTerminal(["x"]))
    assert stopped.value.exit_code == 1
    assert kiln.paths() == ["/ping"]


def test_kiln_not_running_interactive_open_offers_kiln_tech():
    kiln = make_kiln(ping=responses(refused(), "pong"))
    terminal = ScriptedTerminal(["o", "r"])
    start(kiln, terminal)
    assert terminal.opened == ["https://kiln.tech"]


def test_kiln_not_running_not_interactive_fails_without_reading_input():
    kiln = make_kiln(ping=refused())
    terminal = ScriptedTerminal(["r"], interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(kiln, terminal)
    assert str(error.value) == NOT_RUNNING
    assert terminal.prompts == [] and terminal.out == []
    assert kiln.paths() == ["/ping"]


async def test_ping_and_the_api_list_are_one_try_each():
    kiln = make_kiln()
    await check_kiln(kiln, HOST, prog=PROG)
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
    result = await check_kiln(kiln, HOST, prog=PROG)
    assert result.problem is not None
    assert result.problem.message == (
        "Something answered at http://localhost:8757, but it isn't Kiln"
        f" (GET /ping returned {answer}).\n"
        "Check --kiln-host, or start the Kiln app."
    )
    assert result.problem.open_url == "https://kiln.tech"
    assert result.problem.open_label == "kiln.tech"
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
    result = await check_kiln(kiln, HOST, prog=PROG)
    assert result.problem is not None
    assert result.problem.message == (
        "Kiln at http://localhost:8757 (version 1.0.3) is too old for"
        " kiln-harness-tuner.\n"
        "It's missing: the memory API, run-config provenance.\n"
        "Update Kiln: in the app, open Settings → Check for update, or download"
        " the latest from https://kiln.tech.\n"
        "Then restart it."
    )
    assert result.problem.open_url == "https://kiln.tech"
    assert result.warning is None


@pytest.mark.parametrize("version", ["unknown", " Unknown ", "", None, 3])
async def test_too_old_kiln_leaves_out_a_version_that_says_nothing(version):
    kiln = make_kiln(api=openapi(version=version, eval_jobs=False))
    result = await check_kiln(kiln, HOST, prog=PROG)
    assert result.problem is not None
    assert result.problem.message.startswith(
        "Kiln at http://localhost:8757 is too old for kiln-harness-tuner.\n"
    )


async def test_the_too_old_message_names_the_command_it_is_given():
    kiln = make_kiln(api=openapi(eval_jobs=False))
    result = await check_kiln(kiln, HOST, prog="harness-tuner")
    assert result.problem is not None
    assert "is too old for harness-tuner." in result.problem.message


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
    result = await check_kiln(make_kiln(api=api_list), HOST, prog=PROG)
    assert result.problem is None
    assert result.warning == UNREADABLE_API_LIST_WARNING


def test_the_unreadable_api_list_warning_is_printed_once_and_the_start_goes_on():
    kiln = make_kiln(api="not json at all")
    terminal = ScriptedTerminal()
    start(kiln, terminal)
    assert terminal.err == [
        "couldn't read Kiln's API list, so its version wasn't checked."
    ]
    assert terminal.out == [] and terminal.prompts == []


def test_the_default_terminal_is_the_real_one(capsys):
    prepare_start(
        flags(PROJECT_ID, TASK_ID, FOLDER),
        [],
        prog=PROG,
        parser_long_options=[],
        policy_loader=load_model_policy,
        runner=runner_for(make_kiln(api="not json")),
    )
    assert capsys.readouterr().err == (
        "kiln-harness-tuner: couldn't read Kiln's API list,"
        " so its version wasn't checked.\n"
    )


def test_a_trailing_slash_on_the_host_is_dropped():
    kiln = make_kiln(ping=refused())
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(kiln, terminal, host="http://kiln.local:9000/")
    assert str(error.value).startswith("Kiln isn't running at http://kiln.local:9000.")


def test_ctrl_c_during_a_kiln_read_stops_with_130():
    def interrupted(_fn):
        raise KeyboardInterrupt

    with pytest.raises(OnboardingStopped) as stopped:
        prepare_start(
            flags(),
            [],
            prog=PROG,
            parser_long_options=[],
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
