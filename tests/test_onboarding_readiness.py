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

from kiln_harness_tuner.onboarding.readiness import readiness_problem
from kiln_harness_tuner.setup import (
    SetupPreconditionError,
    active_eval_rows,
    measurable_evals,
)

BASE = task_base(PROJECT_ID, TASK_ID)
RUN_CONFIGS_LINK = f"{HOST}/optimize/{PROJECT_ID}/{TASK_ID}"
EVALS_LINK = f"{HOST}/specs/{PROJECT_ID}/{TASK_ID}"
HEADLINE = "Task Moderate posts (424242783061) isn't ready to tune:"
NO_RUN_CONFIGS = f"  • It has no run configs. Create one in Kiln: {RUN_CONFIGS_LINK}"
NO_EVAL = f"  • It has no eval the loop can measure. Add one in Kiln: {EVALS_LINK}"


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


async def test_a_ready_task_has_no_problem():
    kiln = kiln_with()
    assert await check(kiln) == (None, [])
    assert sorted(kiln.paths()) == [
        f"{BASE}/evals",
        f"{BASE}/run_configs",
        f"{BASE}/specs",
    ]


@pytest.mark.parametrize(
    "run_configs", [None, [], [{"name": "no id"}, {"id": ""}, "oops"]]
)
async def test_no_run_configs(run_configs):
    problem, warnings = await check(kiln_with(run_configs=run_configs))
    assert problem is not None
    assert problem.message == f"{HEADLINE}\n{NO_RUN_CONFIGS}"
    assert problem.open_url == RUN_CONFIGS_LINK
    assert problem.open_label == "the first link"
    assert warnings == []


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
    problem, _warnings = await check(kiln_with(evals=evals, specs=specs))
    assert problem is not None
    assert problem.message == f"{HEADLINE}\n{NO_EVAL}"
    assert problem.open_url == EVALS_LINK


async def test_active_evals_without_a_default_config_are_named():
    evals = [
        an_eval("a", "Tone", config_id=None),
        an_eval("b", "Accuracy", config_id=None),
        an_eval("c", "Old", config_id=None),
        {"id": "d", "current_config_id": None},
    ]
    problem, _warnings = await check(
        kiln_with(evals=evals, specs=[a_spec("c", "archived")])
    )
    assert problem is not None
    assert problem.message == (
        f"{HEADLINE}\n"
        '  • Its active evals have no default eval config: "Tone", "Accuracy", "d".'
        f" Set one on each eval's page in Kiln: {EVALS_LINK}"
    )


async def test_both_problems_are_listed_at_once_and_the_first_link_opens():
    problem, _warnings = await check(kiln_with(run_configs=[], evals=[]))
    assert problem is not None
    assert problem.message == f"{HEADLINE}\n{NO_RUN_CONFIGS}\n{NO_EVAL}"
    assert problem.open_url == RUN_CONFIGS_LINK


@pytest.mark.parametrize(
    "count,text,them",
    [(1, "1 eval", "it"), (2, "2 evals", "them")],
    ids=["one", "several"],
)
async def test_the_eval_envelope_is_read_and_load_errors_warn(count, text, them):
    envelope = {"evals": [an_eval("a")], "load_error_count": count}
    problem, warnings = await check(kiln_with(evals=envelope))
    assert problem is None
    assert warnings == [
        f"Kiln couldn't load {text} of task Moderate posts, so the run can't"
        f" measure {them}."
    ]


async def test_load_errors_warn_alongside_a_problem():
    envelope = {"evals": [], "load_error_count": 3}
    problem, warnings = await check(kiln_with(evals=envelope))
    assert problem is not None and len(warnings) == 1


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
