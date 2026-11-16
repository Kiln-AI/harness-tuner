"""The OpenEnv check: which servers the task uses (`used_servers`), and that
each one answers through Kiln (`check_openenv`). Then the check in
`prepare_start`, guided and not guided. Kiln is a fake: no OpenEnv server
runs, and nothing connects to one."""

import json
from pathlib import Path

import pytest
from onboarding_fakes import (
    ANSWERED,
    FOLDER,
    PROG,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    ScriptedTerminal,
    kiln_routes,
    project,
    responses,
    run_config,
    runner_for,
    task,
    task_base,
)

from kiln_harness_tuner.cli import build_parser, load_model_policy
from kiln_harness_tuner.job_drives import OPENENV_LIST_TIMEOUT_SECS
from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError
from kiln_harness_tuner.onboarding.errors import OnboardingError
from kiln_harness_tuner.onboarding.flow import prepare_start
from kiln_harness_tuner.onboarding.openenv_check import (
    RETRY_LABEL,
    OpenEnvCheck,
    ServerUse,
    check_openenv,
    used_servers,
)

SERVERS = f"/api/projects/{PROJECT_ID}/openenv_servers"
SHOP = "424242000501"
MAZE = "424242000502"
SHOP_URL = "http://127.0.0.1:8000"
MAZE_URL = "http://127.0.0.1:8001"
SHOP_TOOLS = [f"kiln_tool::openenv::{SHOP}::search", f"kiln_tool::openenv::{SHOP}::buy"]
BAD_GATEWAY = KilnAPIError(
    "GET", f"{SERVERS}/{SHOP}/tools", 502, {"detail": "connection refused"}
)


def tools_path(server_id: str) -> str:
    return f"{SERVERS}/{server_id}/tools"


def server(server_id: str, name: str, url: str | None) -> dict:
    """One `GET …/openenv_servers` row."""
    return {"id": server_id, "name": name, "description": None, "url": url}


def tools(server_id: str, *names: str) -> list[dict]:
    """A `GET …/openenv_servers/{id}/tools` body."""
    return [
        {
            "tool_id": f"kiln_tool::openenv::{server_id}::{name}",
            "name": name,
            "description": "",
            "input_schema": {},
        }
        for name in names
    ]


def openenv_kiln(**overrides: object) -> RouteKiln:
    """A Kiln whose project has the shop and the maze servers, both up."""
    routes = {
        ("GET", SERVERS): [
            server(SHOP, "Shop", SHOP_URL),
            server(MAZE, "Maze", MAZE_URL),
        ],
        ("GET", tools_path(SHOP)): tools(SHOP, "search", "buy", "refund"),
        ("GET", tools_path(MAZE)): tools(MAZE, "move"),
    }
    for path, answer in overrides.items():
        routes[("GET", path)] = answer
    return RouteKiln(routes)


async def check(kiln: RouteKiln, uses: dict[str, ServerUse]) -> OpenEnvCheck:
    return await check_openenv(kiln, project_id=PROJECT_ID, uses=uses)


def shop_tools(*names: str) -> dict[str, ServerUse]:
    return {SHOP: ServerUse(tools=set(names))}


# ---- which servers the task uses ----


def write(path: Path, content: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content))


def eval_input(item_id: str, tag: str, server_id: str | None) -> dict:
    doc: dict = {"id": item_id, "tags": [tag], "data": {"text": "hi"}}
    if server_id is not None:
        doc["openenv_reset"] = {"openenv_server_id": server_id, "kwargs": {}}
    return doc


@pytest.fixture
def project_with_splits(tmp_path: Path) -> Path:
    """A task with one eval whose train, val and test splits each hold an
    input that resets into a different server, and a sibling task."""
    task_dir = tmp_path / "tasks" / f"{TASK_ID} - Shop"
    write(
        task_dir / "evals" / "e1" / "eval.kiln",
        {
            "id": "424242000601",
            "splits": {
                name: {"source": "eval_input", "filter_id": f"tag::{name}"}
                for name in ("train", "val", "test")
            },
        },
    )
    inputs = task_dir / "eval_inputs"
    write(inputs / "a" / "eval_input.kiln", eval_input("a", "train", SHOP))
    write(inputs / "b" / "eval_input.kiln", eval_input("b", "train", SHOP))
    write(inputs / "c" / "eval_input.kiln", eval_input("c", "train", None))
    write(inputs / "d" / "eval_input.kiln", eval_input("d", "val", MAZE))
    write(inputs / "e" / "eval_input.kiln", eval_input("e", "test", "424242000503"))
    sibling = tmp_path / "tasks" / "424242000999 - Other" / "eval_inputs"
    write(sibling / "a" / "eval_input.kiln", eval_input("a", "train", MAZE))
    return tmp_path


def test_the_baseline_tools_name_their_servers_and_other_tools_are_left_out():
    tool_ids = [
        *SHOP_TOOLS,
        f"kiln_tool::openenv::{MAZE}::move",
        "kiln_tool::add_numbers",
        "mcp::local::server::tool",
        f"kiln_tool::openenv::{MAZE}",  # not four parts
    ]
    uses = used_servers(tool_ids, Path("/no/such/project"), TASK_ID)
    assert {server_id: use.tools for server_id, use in uses.items()} == {
        SHOP: {"search", "buy"},
        MAZE: {"move"},
    }


def test_only_train_inputs_name_servers(project_with_splits):
    uses = used_servers([], project_with_splits, TASK_ID)
    assert uses == {SHOP: ServerUse(train_inputs=2)}


def test_both_sources_are_joined(project_with_splits):
    uses = used_servers(SHOP_TOOLS[:1], project_with_splits, TASK_ID)
    assert uses == {SHOP: ServerUse(tools={"search"}, train_inputs=2)}


def test_a_project_whose_splits_cant_be_read_names_no_server(project_with_splits):
    bad = project_with_splits / "tasks" / f"{TASK_ID} - Shop" / "eval_inputs" / "z"
    bad.mkdir()
    (bad / "eval_input.kiln").write_text("{not json")
    assert used_servers([], project_with_splits, TASK_ID) == {}


# ---- check_openenv ----


async def test_a_task_that_uses_no_openenv_server_makes_no_call():
    kiln = openenv_kiln()
    assert await check(kiln, {}) == OpenEnvCheck()
    assert kiln.calls == []


@pytest.mark.parametrize("status", [404, 405])
async def test_a_kiln_with_no_openenv_endpoint_passes_silently(status):
    kiln = openenv_kiln()
    kiln.routes[("GET", SERVERS)] = KilnAPIError("GET", SERVERS, status, {})
    assert await check(kiln, shop_tools("search")) == OpenEnvCheck()
    assert kiln.paths() == [SERVERS]


async def test_any_other_error_on_the_server_list_is_raised():
    kiln = openenv_kiln()
    kiln.routes[("GET", SERVERS)] = KilnAPIError("GET", SERVERS, 500, {})
    with pytest.raises(KilnAPIError):
        await check(kiln, shop_tools("search"))


async def test_every_server_up_passes_and_each_is_asked_once_through_kiln():
    kiln = openenv_kiln()
    uses = {SHOP: ServerUse(tools={"search", "buy"}), MAZE: ServerUse(train_inputs=3)}
    result = await check(kiln, uses)
    assert result == OpenEnvCheck(servers=2)
    assert result.step_text == "2 servers answer through Kiln"
    asked = [call for call in kiln.calls if call.path.endswith("/tools")]
    assert sorted(call.path for call in asked) == [tools_path(SHOP), tools_path(MAZE)]
    # The OpenEnv server is reached only through Kiln, with no retry, and
    # with a wait a little longer than Kiln's own 30 s connect timeout.
    assert all(call.retries is False for call in asked)
    assert all(call.timeout_secs == OPENENV_LIST_TIMEOUT_SECS for call in asked)
    assert OPENENV_LIST_TIMEOUT_SECS > 30


async def test_one_server_up_is_singular():
    result = await check(openenv_kiln(), shop_tools("search"))
    assert result.step_text == "1 server answers through Kiln"


def lines(result: OpenEnvCheck) -> tuple[str, ...]:
    assert result.problem is not None
    return result.problem.body


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (BAD_GATEWAY, "Kiln answered HTTP 502: connection refused."),
        (
            KilnTransportError("timed out"),
            f"Kiln gave no answer in {OPENENV_LIST_TIMEOUT_SECS:g} s,"
            " or the connection to Kiln failed.",
        ),
    ],
)
async def test_a_server_that_doesnt_answer_is_named_with_its_url(answer, why):
    kiln = openenv_kiln(**{tools_path(SHOP): answer})
    uses = {SHOP: ServerUse(tools={"search"}, train_inputs=2), MAZE: ServerUse()}
    result = await check(kiln, uses)
    assert result.problem is not None
    assert result.problem.title == "An OpenEnv server the task uses isn't ready"
    assert result.problem.retry_label == RETRY_LABEL
    assert lines(result)[2:] == (
        f'  • "Shop" at {SHOP_URL}, used by the baseline run config\'s tools and'
        " 2 train eval inputs, doesn't answer.",
        f"    {why}",
        f"    Start the environment at {SHOP_URL}, or correct the server's URL"
        " in Kiln.",
    )


async def test_a_server_with_no_url_is_not_asked():
    kiln = openenv_kiln()
    kiln.routes[("GET", SERVERS)] = [server(SHOP, "Shop", None)]
    result = await check(kiln, {SHOP: ServerUse(train_inputs=1)})
    assert lines(result)[2:] == (
        '  • "Shop", used by 1 train eval input: it has no URL in Kiln.',
        "    Start the environment, then set the server's URL in Kiln.",
    )
    assert tools_path(SHOP) not in kiln.paths()


async def test_a_server_kiln_doesnt_have_is_named_by_its_id():
    result = await check(openenv_kiln(), {"424242000599": ServerUse(train_inputs=1)})
    assert lines(result)[2:] == (
        "  • OpenEnv server 424242000599, used by 1 train eval input: Kiln has no"
        " OpenEnv server with this ID in this project.",
        "    Add the server in Kiln, or change what uses it.",
    )


async def test_a_tool_the_server_doesnt_serve_names_the_tools_it_serves():
    result = await check(openenv_kiln(), shop_tools("search", "teleport"))
    assert lines(result)[2:] == (
        f'  • "Shop" at {SHOP_URL} doesn\'t serve a tool the baseline run config'
        ' lists: "teleport".',
        '    It serves "buy", "refund" and "search".',
        "    Start the right environment at this URL, or change the run config's"
        " tools.",
    )


async def test_a_server_that_serves_no_tools_says_so():
    kiln = openenv_kiln(**{tools_path(SHOP): []})
    result = await check(kiln, shop_tools("search", "buy"))
    assert lines(result)[2:4] == (
        f'  • "Shop" at {SHOP_URL} doesn\'t serve tools the baseline run config'
        ' lists: "buy" and "search".',
        "    It serves no tools.",
    )


async def test_every_server_that_isnt_ready_is_in_one_problem():
    kiln = openenv_kiln(**{tools_path(SHOP): BAD_GATEWAY})
    result = await check(kiln, {SHOP: ServerUse(), MAZE: ServerUse(tools={"jump"})})
    assert result.problem is not None
    assert result.problem.title == "2 OpenEnv servers the task uses aren't ready"
    bullets = [line for line in lines(result) if line.startswith("  •")]
    assert [b.split('"')[1] for b in bullets] == ["Shop", "Maze"]


# ---- in prepare_start ----

BASE = task_base(PROJECT_ID, TASK_ID)
EVERY_FLAG = [
    "--project-id",
    PROJECT_ID,
    "--task-id",
    TASK_ID,
    "--project-path",
    FOLDER,
]
OPENENV_STEP = "step OpenEnv: 1 server answers through Kiln"


def openenv_start_kiln(shop_tools_answer: object) -> RouteKiln:
    """The demo's Kiln, whose baseline run config lists two shop tools."""
    baseline = run_config()
    baseline["run_config_properties"]["tools_config"] = {"tools": SHOP_TOOLS}
    routes = kiln_routes((project(), [task()]))
    routes[("GET", f"{BASE}/run_configs")] = [baseline]
    routes[("GET", SERVERS)] = [server(SHOP, "Shop", SHOP_URL)]
    routes[("GET", tools_path(SHOP))] = shop_tools_answer
    return RouteKiln(routes)


def start(kiln: RouteKiln, terminal: ScriptedTerminal):
    parser = build_parser()
    argv = [*EVERY_FLAG, *ANSWERED]
    if terminal.interactive:
        terminal.answers += ["enter", "s"]
    return prepare_start(
        parser.parse_args(argv),
        argv,
        prog=PROG,
        option_dests=parser.option_dests,
        policy_loader=load_model_policy,
        terminal=terminal,
        runner=runner_for(kiln),
    )


def test_a_guided_start_checks_the_servers_after_the_keys():
    terminal = ScriptedTerminal()
    start(openenv_start_kiln(tools(SHOP, "search", "buy")), terminal)
    keys = terminal.transcript.index("step API keys: Fireworks AI")
    assert terminal.transcript[keys + 1 : keys + 3] == [
        "busy Checking the OpenEnv servers…",
        OPENENV_STEP,
    ]
    assert "busy Checking the OpenEnv servers…" in terminal.transcript


def test_a_guided_start_with_a_server_down_checks_again_until_it_answers():
    kiln = openenv_start_kiln(responses(BAD_GATEWAY, tools(SHOP, "search", "buy")))
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal)
    assert [title for title, _body in terminal.blocks] == [
        "An OpenEnv server the task uses isn't ready"
    ]
    assert terminal.problem_menus == [("r", "x")]
    assert OPENENV_STEP in terminal.transcript
    assert kiln.paths().count(tools_path(SHOP)) == 2


def test_not_guided_a_server_down_stops_the_start_before_the_summary():
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(openenv_start_kiln(BAD_GATEWAY), terminal)
    message = str(error.value)
    assert message.startswith("An OpenEnv server the task uses isn't ready\n")
    assert f'"Shop" at {SHOP_URL}' in message
    assert f"Start the environment at {SHOP_URL}" in message
    assert terminal.out == []  # no summary: the run never starts


def test_not_guided_a_missing_tool_stops_the_start():
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(openenv_start_kiln(tools(SHOP, "search")), terminal)
    assert 'lists: "buy".' in str(error.value)
    assert 'It serves "search".' in str(error.value)


def test_a_task_with_no_openenv_server_shows_no_step_and_asks_kiln_nothing():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(interactive=False)
    start(kiln, terminal)
    assert not any("OpenEnv" in line for line in terminal.transcript)
    assert not any("openenv" in path for path in kiln.paths())
