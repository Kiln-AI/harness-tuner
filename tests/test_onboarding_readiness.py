"""The readiness check: a run config to start from, and an eval the loop can
measure. Plus the two setup helpers that hold the loop's measurement rule."""

import pytest
from onboarding_fakes import (
    HOST,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    ready_task_routes,
    task_base,
)

from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.onboarding.readiness import Readiness, readiness_problem
from kiln_harness_tuner.onboarding.terminal import Action, ActionKind
from kiln_harness_tuner.setup import (
    SetupPreconditionError,
    active_eval_rows,
    measurable_evals,
)

BASE = task_base(PROJECT_ID, TASK_ID)
RUN_CONFIGS_LINK = f"{HOST}/optimize/{PROJECT_ID}/{TASK_ID}"
EVALS_LINK = f"{HOST}/specs/{PROJECT_ID}/{TASK_ID}"
TITLE = "Moderate posts isn't ready to tune yet"
WHY = (
    "The tuner starts from one of the task's run configs and measures every change",
    "with its evals. This task is missing:",
)
NO_RUN_CONFIGS = "  • A run config: the model and prompt the tuner starts from."
NO_EVAL = "  • An eval it can measure: an active eval with a default judge set."
RUN_CONFIGS_ACTION = Action(
    "c", "Open Run configs in Kiln", ActionKind.OPEN, url=RUN_CONFIGS_LINK
)
EVALS_ACTION = Action("e", "Open Evals in Kiln", ActionKind.OPEN, url=EVALS_LINK)


def an_eval(eval_id: str, name: str | None = None, config_id: str | None = "ec_1"):
    row = {"id": eval_id, "name": name or eval_id.title()}
    if config_id is not None:
        row["current_config_id"] = config_id
    return row


def a_spec(eval_id: str, status: object = "active") -> dict:
    spec = {"id": f"spec_{eval_id}", "eval_id": eval_id}
    if status is not None:
        spec["status"] = status
    return spec


# ---- active_eval_rows and measurable_evals ----


def test_active_eval_rows_keeps_evals_with_no_spec_or_an_active_one():
    evals = [an_eval("a"), an_eval("b"), an_eval("c")]
    assert active_eval_rows(evals, [a_spec("b"), a_spec("zz", "archived")]) == evals


@pytest.mark.parametrize("status", ["archived", "draft", "Active", None])
def test_active_eval_rows_drops_an_eval_whose_spec_isnt_active(status):
    evals = [an_eval("a"), an_eval("b")]
    assert active_eval_rows(evals, [a_spec("a", status)]) == [an_eval("b")]


def test_active_eval_rows_skips_rows_that_arent_dicts_or_have_no_id():
    evals = ["oops", None, {"name": "no id"}, {"id": ""}, an_eval("a")]
    specs = ["not a spec", {"status": "archived"}, a_spec("a")]
    assert active_eval_rows(evals, specs) == [an_eval("a")]


def test_active_eval_rows_compares_ids_as_text():
    evals = [{"id": 7, "current_config_id": "ec_1"}]
    assert active_eval_rows(evals, [{"eval_id": "7", "status": "archived"}]) == []


def test_measurable_evals_pairs_each_active_eval_with_its_default_config():
    with_config = an_eval("a", config_id="ec_a")
    no_config = an_eval("b", config_id=None)
    inactive = an_eval("c", config_id="ec_c")
    evals = [with_config, no_config, inactive, an_eval("d", config_id="")]
    assert measurable_evals(evals, [a_spec("c", "archived")]) == [(with_config, "ec_a")]


# ---- readiness_problem ----


def kiln_with(**reads: object) -> RouteKiln:
    """A Kiln whose task is ready, except for the reads given:
    `run_configs`, `evals` or `specs`."""
    routes = ready_task_routes(PROJECT_ID, TASK_ID)
    for name, body in reads.items():
        routes[("GET", f"{BASE}/{name}")] = body
    return RouteKiln(routes)


async def check(kiln: RouteKiln):
    return await readiness_problem(
        kiln,
        host=HOST,
        project_id=PROJECT_ID,
        task_id=TASK_ID,
        task_name="Moderate posts",
    )


async def test_a_ready_task_has_no_problem_and_counts_what_it_has():
    kiln = kiln_with(
        run_configs=[{"id": "rc_1"}, {"id": "rc_2"}],
        evals=[an_eval("a"), an_eval("b"), an_eval("c", config_id=None)],
    )
    readiness = await check(kiln)
    assert readiness == Readiness(None, run_configs=2, measurable=2, warnings=[])
    assert readiness.step_text == "2 run configs · 2 evals the tuner can measure"
    # Each measurable eval's default config is read, to see that Kiln can
    # load it. A 404 is a config that is gone, not one Kiln can't load.
    assert sorted(kiln.paths()) == [
        f"{BASE}/evals",
        f"{BASE}/evals/a/eval_config/ec_1",
        f"{BASE}/evals/b/eval_config/ec_1",
        f"{BASE}/run_configs",
        f"{BASE}/specs",
    ]


def test_the_step_text_is_singular_for_one():
    assert Readiness(None, 1, 1).step_text == (
        "1 run config · 1 eval the tuner can measure"
    )


@pytest.mark.parametrize(
    "run_configs", [None, [], [{"name": "no id"}, {"id": ""}, "oops"]]
)
async def test_no_run_configs(run_configs):
    readiness = await check(kiln_with(run_configs=run_configs))
    problem = readiness.problem
    assert problem is not None
    assert (problem.title, problem.body) == (TITLE, (*WHY, NO_RUN_CONFIGS))
    assert problem.actions == (RUN_CONFIGS_ACTION,)
    assert problem.retry_label == "Check again (I've fixed it in Kiln)"
    assert readiness.warnings == []


@pytest.mark.parametrize(
    "evals,specs",
    [
        ([], []),
        (None, None),
        ([an_eval("a")], [a_spec("a", "archived")]),
        ([an_eval("a", config_id=None)], [a_spec("a", "archived")]),
    ],
    ids=["no evals", "empty bodies", "inactive spec", "inactive, no config"],
)
async def test_no_eval_the_loop_can_measure(evals, specs):
    problem = (await check(kiln_with(evals=evals, specs=specs))).problem
    assert problem is not None
    assert problem.body == (*WHY, NO_EVAL)
    assert problem.actions == (EVALS_ACTION,)


async def test_active_evals_without_a_default_config_are_named():
    evals = [
        an_eval("a", "Tone", config_id=None),
        an_eval("b", "Accuracy", config_id=None),
        an_eval("c", "Old", config_id=None),
        {"id": "d", "current_config_id": None},
    ]
    problem = (
        await check(kiln_with(evals=evals, specs=[a_spec("c", "archived")]))
    ).problem
    assert problem is not None
    assert problem.body[-1] == (
        '  • A default judge on its active evals: "Tone", "Accuracy" and "d"'
        " have none set."
    )
    assert problem.actions == (EVALS_ACTION,)


async def test_one_active_eval_without_a_default_config_has_none_set():
    evals = [an_eval("a", "Tone", config_id=None)]
    problem = (await check(kiln_with(evals=evals))).problem
    assert problem is not None
    assert problem.body[-1] == (
        '  • A default judge on its active evals: "Tone" has none set.'
    )


async def test_both_problems_are_listed_with_one_action_for_each_link():
    problem = (await check(kiln_with(run_configs=[], evals=[]))).problem
    assert problem is not None
    assert problem.body == (*WHY, NO_RUN_CONFIGS, NO_EVAL)
    assert [a.key for a in problem.menu()] == ["r", "c", "e", "x"]


async def test_not_guided_the_message_names_each_link():
    problem = (await check(kiln_with(run_configs=[], evals=[]))).problem
    assert problem is not None
    assert problem.error().body[-2:] == (
        f"Open Run configs in Kiln: {RUN_CONFIGS_LINK}",
        f"Open Evals in Kiln: {EVALS_LINK}",
    )


@pytest.mark.parametrize(
    "count,text,them",
    [(1, "1 eval", "it"), (2, "2 evals", "them")],
    ids=["one", "several"],
)
async def test_the_eval_envelope_is_read_and_load_errors_warn(count, text, them):
    envelope = {"evals": [an_eval("a")], "load_error_count": count}
    readiness = await check(kiln_with(evals=envelope))
    assert readiness.problem is None
    assert readiness.warnings == [
        f"Kiln couldn't load {text} of this task, so the run can't measure {them}."
    ]


async def test_load_errors_warn_alongside_a_problem():
    envelope = {"evals": [], "load_error_count": 3}
    readiness = await check(kiln_with(evals=envelope))
    assert readiness.problem is not None and len(readiness.warnings) == 1
    # The problem carries them too, to print after its block.
    assert readiness.problem.warnings == tuple(readiness.warnings)


@pytest.mark.parametrize(
    "read,body",
    [
        ("run_configs", {"detail": "drift"}),
        ("evals", "drift"),
        ("specs", {"specs": []}),
    ],
)
async def test_a_drifted_listing_raises_setup_precondition_error(read, body):
    with pytest.raises(SetupPreconditionError, match=f"GET {BASE}/{read} did not"):
        await check(kiln_with(**{read: body}))


# ---- evals Kiln can't load ----

JINJA = "Invalid Jinja2 template: No filter named 'trace_overview'"


def cant_load(status: int = 422, message: str = JINJA) -> KilnAPIError:
    """Kiln's answer for an eval config it can't load."""
    return KilnAPIError(
        "GET", "config", status, {"error_messages": [f": Value error, {message}"]}
    )


def kiln_with_configs(evals: list, specs: list, configs: dict) -> RouteKiln:
    """A ready task with `evals` and `specs`, whose eval `id`'s config
    read answers `configs[id]`."""
    kiln = kiln_with(evals=evals, specs=specs)
    for row in evals:
        if row["id"] in configs:
            path = f"{BASE}/evals/{row['id']}/eval_config/{row['current_config_id']}"
            kiln.routes[("GET", path)] = configs[row["id"]]
    return kiln


def warning(name: str, link: str, reason: str = JINJA) -> str:
    return (
        f'Kiln can\'t load eval "{name}": {reason}.\n'
        "  The run leaves it out. To use it, fix its judge in Kiln, or update"
        " Kiln if\n"
        f"  the eval was made with a newer version: {link}"
    )


@pytest.mark.parametrize("status", [400, 422])
async def test_an_eval_kiln_cant_load_is_left_out_with_a_warning(status):
    evals = [an_eval("a", "Tone"), an_eval("b", "Accuracy"), an_eval("c", "Format")]
    kiln = kiln_with_configs(
        evals, [a_spec("a")], {"a": cant_load(status), "b": {"id": "ec_1"}}
    )
    readiness = await check(kiln)
    assert readiness.problem is None
    assert (readiness.measurable, readiness.left_out) == (2, 1)
    assert readiness.step_text == (
        "1 run config · 2 evals the tuner can measure (1 left out)"
    )
    # Its link is its own page, through its spec.
    assert readiness.warnings == [
        warning("Tone", f"{EVALS_LINK}/spec_a/a"),
    ]


async def test_an_eval_with_no_spec_links_to_the_evals_list():
    evals = [an_eval("a", "Tone"), an_eval("b", "Accuracy")]
    kiln = kiln_with_configs(
        evals, [a_spec("zz")], {"a": cant_load(400, "Bad config.")}
    )
    readiness = await check(kiln)
    # Kiln's own full stop is not doubled.
    assert readiness.warnings == [warning("Tone", EVALS_LINK, "Bad config")]


async def test_each_eval_kiln_cant_load_gets_its_warning_after_the_load_errors():
    envelope = {
        "evals": [an_eval("a", "Tone"), an_eval("b", "Accuracy"), an_eval("c")],
        "load_error_count": 1,
    }
    kiln = kiln_with(evals=envelope)
    for eval_id in ("a", "b"):
        path = f"{BASE}/evals/{eval_id}/eval_config/ec_1"
        kiln.routes[("GET", path)] = cant_load()
    readiness = await check(kiln)
    assert readiness.problem is None
    assert readiness.step_text == (
        "1 run config · 1 eval the tuner can measure (2 left out)"
    )
    assert readiness.warnings == [
        "Kiln couldn't load 1 eval of this task, so the run can't measure it.",
        warning("Tone", EVALS_LINK),
        warning("Accuracy", EVALS_LINK),
    ]


async def test_when_kiln_can_load_none_of_the_evals_the_task_isnt_ready():
    evals = [an_eval("a", "Tone"), an_eval("b", "Accuracy")]
    kiln = kiln_with_configs(evals, [], {"a": cant_load(), "b": cant_load(400)})
    readiness = await check(kiln)
    problem = readiness.problem
    assert problem is not None
    assert problem.body == (
        *WHY,
        "  • An eval it can measure: Kiln can't load any of this task's evals"
        " (see the warnings).",
    )
    assert problem.actions == (EVALS_ACTION,)
    assert problem.warnings == (
        warning("Tone", EVALS_LINK),
        warning("Accuracy", EVALS_LINK),
    )
    assert (readiness.measurable, readiness.left_out) == (0, 2)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_any_other_error_reading_an_eval_config_still_stops(status):
    broken = KilnAPIError("GET", "config", status, {"detail": "boom"})
    kiln = kiln_with_configs([an_eval("a", "Tone")], [], {"a": broken})
    with pytest.raises(KilnAPIError) as raised:
        await check(kiln)
    assert raised.value is broken
