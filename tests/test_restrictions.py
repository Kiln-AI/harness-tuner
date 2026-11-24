"""The restrictions the owner states at the start: each answer becomes a
check in code. The model restrictions are rules in the model policy, and the
tool lock is checked by the gateway and by the loop.

Every model, provider and tool id here is invented."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_gateway import _gateway, _run
from test_model_preflight import BASE, LARGE, SMALL, _ctx

from kiln_harness_tuner import cli
from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.gateway.server import load_policy
from kiln_harness_tuner.model_catalog import Catalog, CatalogModel, Source
from kiln_harness_tuner.model_policy import (
    ModelPolicy,
    ModelPolicyError,
    Restriction,
    Rule,
    with_restrictions,
)
from kiln_harness_tuner.orchestrator import (
    _allowed_models,
    lock_baseline_tools,
    pin_baseline_model,
    policy_refusal,
    tool_lock_refusal,
)
from kiln_harness_tuner.restrictions import (
    NO_ZERO_RETENTION,
    RESTRICTIONS_FILE,
    RestrictionError,
    Restrictions,
    ToolLock,
    catalog_families,
    load_tool_lock,
    parse_id_list,
    write_restrictions,
)
from kiln_harness_tuner.setup import SetupPreconditionError

OK = Source(state="ok")
CATALOG = Catalog(
    kiln=OK,
    models_dev=OK,
    models=[
        CatalogModel(
            model="closed_one", display_name="C1", family="alpha", open_weights=False
        ),
        CatalogModel(
            model="open_one", display_name="O1", family="beta", open_weights=True
        ),
        CatalogModel(model="no_fact", display_name="N1", family="Beta"),
        CatalogModel(model="no_family", display_name="N2", open_weights=True),
    ],
)
LOCKED = "mcp::remote::424242000001::search"
WRAPPER = "kiln_tool::code::424242000002"
OLD_WRAPPER = "kiln_tool::code::424242000003"


def policy_of(restrictions: Restrictions, catalog: Catalog = CATALOG) -> ModelPolicy:
    """`--models any` with the restrictions: every pair is allowed, and the
    restrictions decide."""
    policy, _text, _rules = cli.restricted_policy(
        restrictions, catalog, None, None, keep=False
    )
    assert policy is not None
    return policy


# ---- the flags ----


def test_an_id_list_keeps_the_order_and_each_id_once():
    assert parse_id_list("openai, anthropic,openai") == ["openai", "anthropic"]
    with pytest.raises(ValueError, match="separated by commas"):
        parse_id_list(" , ")


def test_the_flags_become_the_restrictions():
    args = cli.build_parser().parse_args(
        [
            "--providers",
            "openai,anthropic",
            "--model-weights",
            "closed",
            "--exclude-model-families",
            "Beta",
            "--zero-data-retention",
            "--lock-tool",
            LOCKED,
            "--lock-tool",
            WRAPPER,
        ]
    )
    restrictions = Restrictions.from_args(args)
    assert restrictions == Restrictions(
        providers=("openai", "anthropic"),
        model_weights="closed",
        exclude_families=("beta",),
        zero_data_retention=True,
        locked_tools=(LOCKED, WRAPPER),
    )
    assert restrictions.statements() == [
        "only these providers: openai, anthropic",
        "closed-weight models only",
        "no models of these families: beta",
        "zero data retention only",
        f"these tools must not change: {LOCKED}, {WRAPPER}",
    ]


def test_no_flag_states_no_restriction():
    restrictions = Restrictions.from_args(cli.build_parser().parse_args([]))
    assert not restrictions
    assert restrictions.statements() == []
    assert restrictions.limits() == []
    assert restrictions.model_restrictions(Catalog.unavailable("off")) == ()


def test_each_restriction_says_what_code_cannot_check():
    limits = " ".join(
        Restrictions(
            providers=("openai",),
            model_weights="open",
            zero_data_retention=True,
            locked_tools=(LOCKED,),
        ).limits()
    )
    assert "no country or company of origin" in limits
    assert "cannot check the retention terms" in limits
    assert "agent CLI's account" in limits
    assert "does not check the text" in limits


# ---- the model restrictions, as rules of the model policy ----


def test_providers_refuse_every_other_provider():
    policy = policy_of(Restrictions(providers=("openai", "anthropic")))
    assert policy.refusal("openai", "any_model") is None
    refusal = policy.refusal("groq", "any_model")
    assert refusal is not None
    assert "only these providers: openai, anthropic" in refusal


def test_closed_weights_refuse_an_open_model_and_one_with_no_fact():
    policy = policy_of(Restrictions(model_weights="closed"))
    assert policy.refusal("openai", "closed_one") is None
    assert "open-weight model" in (policy.refusal("openai", "open_one") or "")
    assert "does not say if its weights are open" in (
        policy.refusal("openai", "no_fact") or ""
    )
    assert policy.refusal("openai", "not_in_the_catalogue") is not None


def test_open_weights_refuse_a_closed_model():
    policy = policy_of(Restrictions(model_weights="open"))
    assert policy.refusal("fireworks_ai", "open_one") is None
    assert "closed-weight model" in (policy.refusal("openai", "closed_one") or "")


def test_excluded_families_are_refused_whatever_their_case_in_the_catalogue():
    policy = policy_of(Restrictions(exclude_families=("beta",)))
    assert policy.refusal("openai", "closed_one") is None
    assert "family beta" in (policy.refusal("openai", "open_one") or "")
    assert "family beta" in (policy.refusal("openai", "no_fact") or "")
    assert "does not give its family" in (policy.refusal("openai", "no_family") or "")


def test_a_family_the_catalogue_does_not_have_is_an_error_that_lists_them():
    assert catalog_families(CATALOG) == {"alpha": 1, "beta": 2}
    with pytest.raises(RestrictionError, match="no model family gamma.*alpha, beta"):
        Restrictions(exclude_families=("gamma",)).model_restrictions(CATALOG)


def test_an_origin_restriction_needs_the_catalogue():
    off = Catalog.unavailable("turned off")
    with pytest.raises(RestrictionError, match="needs the model catalogue"):
        Restrictions(model_weights="open").model_restrictions(off)
    with pytest.raises(cli.ConfigRenderError, match="fix: "):
        cli.restricted_policy(
            Restrictions(exclude_families=("beta",)), off, None, None, keep=False
        )
    # A restriction on providers or on retention needs no catalogue.
    rules = Restrictions(
        providers=("openai",), zero_data_retention=True
    ).model_restrictions(off)
    assert [rule.name for rule in rules] == ["providers", "data_retention"]


def test_zero_retention_refuses_the_routes_that_cannot_give_it():
    assert "openrouter" in NO_ZERO_RETENTION
    policy = policy_of(Restrictions(zero_data_retention=True))
    refusal = policy.refusal("openrouter", "closed_one")
    assert refusal is not None
    assert "never sends `zdr`" in refusal
    assert policy.refusal("openai", "closed_one") is None


def test_a_model_id_is_a_name_and_not_a_pattern():
    catalog = Catalog(
        kiln=OK,
        models_dev=OK,
        models=[CatalogModel(model="odd[1]*", display_name="Odd", open_weights=True)],
    )
    policy = policy_of(Restrictions(model_weights="open"), catalog)
    assert policy.refusal("openai", "odd[1]*") is None
    assert policy.refusal("openai", "odd1x") is not None


def test_an_exception_does_not_pass_a_restriction():
    data = {
        "allow": [{"provider": "*", "model": "*"}],
        "exceptions": [{"provider": "openrouter", "model": "closed_one"}],
    }
    assert ModelPolicy.from_json(data).refusal("openrouter", "closed_one") is None
    rules = Restrictions(zero_data_retention=True).model_restrictions(CATALOG)
    policy = ModelPolicy.from_json(with_restrictions(data, rules))
    assert policy.refusal("openrouter", "closed_one") is not None


def test_the_summary_gives_the_statements_to_the_planner():
    policy = policy_of(Restrictions(providers=("openai",), zero_data_retention=True))
    summary = policy.summary()
    assert summary["restrictions"] == [
        "only these providers: openai",
        "zero data retention only",
    ]
    assert summary["order"].startswith("restrictions first")
    # A policy with no restriction reads as it did before.
    plain = ModelPolicy(allow=(Rule("*", "*"),)).summary()
    assert "restrictions" not in plain


def test_a_restriction_round_trips_through_the_policy_file():
    restriction = Restriction(
        name="providers",
        statement="only these providers: openai",
        allow=(Rule("openai", "*"),),
        deny=(Rule("openai", "bad", "it is bad"),),
        otherwise="its provider is not one of them",
    )
    data = with_restrictions(
        {"allow": [{"provider": "*", "model": "*"}]}, [restriction]
    )
    assert ModelPolicy.from_json(json.loads(json.dumps(data))).restrictions == (
        restriction,
    )


@pytest.mark.parametrize(
    "restrictions",
    [
        {"name": "x"},
        [{"name": "x"}],
        [{"name": "x", "statement": "y", "alow": []}],
        [{"name": "x", "statement": "y", "allow": [{"provider": "p"}]}],
    ],
)
def test_a_bad_restriction_in_a_policy_file_is_refused(restrictions):
    data = {"allow": [{"provider": "*", "model": "*"}], "restrictions": restrictions}
    with pytest.raises(ModelPolicyError):
        ModelPolicy.from_json(data)


# ---- the run's policy: a file, `--models any`, `--models keep` ----


def test_a_policy_file_gets_the_restrictions_added(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"allow": [{"provider": "*", "model": "closed_*"}]}))
    loaded = cli.load_model_policy(str(path))
    policy, text, rules = cli.restricted_policy(
        Restrictions(providers=("openai",)), CATALOG, loaded, str(path), keep=False
    )
    assert policy is not None and text is not None
    assert policy.refusal("openai", "closed_one") is None
    assert policy.refusal("groq", "closed_one") is not None  # the restriction
    assert policy.refusal("openai", "open_one") is not None  # the file's rule
    assert [rule.name for rule in rules] == ["providers"]
    # The copy in the run directory is what the gateway loads.
    (tmp_path / "model_policy.json").write_text(text)
    gateway = load_policy(tmp_path)
    assert gateway is not None
    assert gateway.refusal("groq", "closed_one") is not None


def test_a_policy_file_with_no_restriction_is_copied_as_it_is(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"allow":  [{"provider": "*", "model": "*"}]}')
    loaded = cli.load_model_policy(str(path))
    policy, text, rules = cli.restricted_policy(
        Restrictions(locked_tools=(LOCKED,)), CATALOG, loaded, str(path), keep=False
    )
    assert policy is loaded
    assert text == path.read_text()
    assert rules == ()


def test_any_with_no_restriction_on_models_has_no_policy():
    assert cli.restricted_policy(Restrictions(), CATALOG, None, None, keep=False) == (
        None,
        None,
        (),
    )


def test_keep_leaves_the_rules_for_the_pin():
    policy, text, rules = cli.restricted_policy(
        Restrictions(providers=("openai",)), CATALOG, None, None, keep=True
    )
    assert policy is None
    assert [rule.name for rule in rules] == ["providers"]
    # Until the pin, the gateway's copy holds the restrictions alone.
    assert text is not None
    copy = ModelPolicy.from_json(json.loads(text))
    assert copy.refusal("openai", "any_model") is None
    assert copy.refusal("groq", "any_model") is not None


async def test_keep_pins_the_baseline_inside_the_restrictions(tmp_path):
    ctx = _ctx(tmp_path, policy=None)
    ctx.keep_model, ctx.run_dir = True, tmp_path
    ctx.model_restrictions = Restrictions(providers=(BASE[0],)).model_restrictions(
        CATALOG
    )
    await pin_baseline_model(ctx, "rc_base")
    assert ctx.model_policy is not None
    assert ctx.model_policy.refusal(*BASE) is None
    assert ctx.model_policy.refusal(*SMALL) is not None
    gateway = load_policy(tmp_path)
    assert gateway is not None and gateway.restrictions


async def test_keep_stops_when_a_restriction_refuses_the_tasks_model(tmp_path):
    ctx = _ctx(tmp_path, policy=None)
    ctx.keep_model, ctx.run_dir = True, tmp_path
    ctx.model_restrictions = Restrictions(providers=("not_it",)).model_restrictions(
        CATALOG
    )
    with pytest.raises(SetupPreconditionError, match="fix: "):
        await pin_baseline_model(ctx, "rc_base")


async def test_keep_with_an_unreadable_baseline_still_enforces_the_restrictions(
    tmp_path,
):
    ctx = _ctx(tmp_path, policy=None)
    ctx.keep_model, ctx.run_dir = True, tmp_path
    ctx.model_restrictions = Restrictions(providers=(SMALL[0],)).model_restrictions(
        CATALOG
    )
    await pin_baseline_model(ctx, "rc_missing")
    assert ctx.model_policy is not None
    assert ctx.model_policy.refusal(*SMALL) is None
    assert ctx.model_policy.refusal(*LARGE) is not None
    assert load_policy(tmp_path) is not None


async def test_the_planners_models_leave_out_what_a_restriction_refuses(tmp_path):
    ctx = _ctx(tmp_path, policy=None)
    ctx.model_policy = policy_of(Restrictions(providers=(BASE[0], LARGE[0])))
    rows = await _allowed_models(ctx, "rc_base")
    assert [(r["provider"], r["model"]) for r in rows] == [BASE, LARGE]


# ---- the tool lock ----


def test_a_candidate_that_drops_or_replaces_a_locked_tool_breaks_the_lock():
    lock = ToolLock((LOCKED,))
    assert lock.dropped([LOCKED, WRAPPER]) is None
    refusal = lock.dropped([WRAPPER])  # replaced by another tool
    assert refusal is not None
    assert LOCKED in refusal and "drop or replace" in refusal
    assert lock.dropped([]) is not None
    assert not ToolLock()
    assert ToolLock().dropped([]) is None


def test_a_code_tool_that_may_call_a_locked_tool_is_a_wrapper():
    lock = ToolLock((LOCKED,), frozenset({OLD_WRAPPER}))
    assert "may not wrap" in (lock.wraps([LOCKED, "other"]) or "")
    assert lock.wraps(["other"]) is None
    assert lock.wraps(None) is None
    # A code tool that the baseline attached is not a new wrapper.
    assert lock.new_code_tools([LOCKED, WRAPPER, OLD_WRAPPER]) == ["424242000002"]


def test_the_lock_file_round_trips_and_a_bad_one_refuses_everything(tmp_path):
    assert not load_tool_lock(tmp_path)
    (tmp_path / RESTRICTIONS_FILE).write_text("")
    assert not load_tool_lock(tmp_path)
    written = write_restrictions(
        tmp_path, Restrictions(locked_tools=(LOCKED,)), [LOCKED, OLD_WRAPPER]
    )
    assert load_tool_lock(tmp_path) == written
    assert written.baseline_tools == {LOCKED, OLD_WRAPPER}
    for text in ("{bad", "[]", '{"locked_tools": "x"}'):
        (tmp_path / RESTRICTIONS_FILE).write_text(text)
        broken = load_tool_lock(tmp_path)
        assert broken
        assert "unreadable" in (broken.dropped([LOCKED]) or "")


def _props(*tools: str) -> dict:
    return {
        "type": "kiln_agent",
        "model_provider_name": "openai",
        "model_name": "gpt",
        "tools_config": {"tools": list(tools)},
    }


def _locked_gateway(tmp_path, routes=None, lock: ToolLock | None = None):
    gateway, kiln = _gateway(tmp_path, routes or {})
    gateway.s.tool_lock = lock or ToolLock((LOCKED,), frozenset({OLD_WRAPPER}))
    return gateway, kiln


class TestTheGatewayEnforcesTheToolLock:
    def test_a_run_config_that_drops_a_locked_tool_is_refused(self, tmp_path):
        gateway, kiln = _locked_gateway(tmp_path)
        with pytest.raises(gw.GatewayError, match="drop or replace"):
            _run(gateway, "create_run_config", name="n", run_config_properties=_props())
        with pytest.raises(gw.GatewayError, match="drop or replace"):
            _run(
                gateway,
                "create_run_config",
                name="n",
                run_config_properties=_props("kiln_tool::code::other"),
            )
        assert kiln.calls == []

    def test_a_run_config_that_wraps_a_locked_tool_is_refused(self, tmp_path):
        tool = f"/api/projects/{gateway_project()}/code_tools/424242000002"
        gateway, kiln = _locked_gateway(
            tmp_path,
            {("GET", tool): {"id": "424242000002", "tool_allowlist": [LOCKED]}},
        )
        with pytest.raises(gw.GatewayError, match="may not wrap"):
            _run(
                gateway,
                "create_run_config",
                name="n",
                run_config_properties=_props(LOCKED, WRAPPER),
            )
        assert [call[0] for call in kiln.calls] == ["GET"]

    def test_a_run_config_that_keeps_the_tool_is_created(self, tmp_path):
        project = gateway_project()
        base = f"/api/projects/{project}/tasks"
        gateway, kiln = _locked_gateway(tmp_path)
        tool = f"/api/projects/{project}/code_tools/424242000002"
        kiln.routes[("GET", tool)] = {"id": "424242000002", "tool_allowlist": []}
        post = next(iter({f"{gateway.s.base}/run_configs"}))
        assert post.startswith(base)
        kiln.routes[("POST", post)] = {"id": "424242000009", "name": "n"}
        # The baseline's own wrapper stays attached: it is not read again.
        view = _run(
            gateway,
            "create_run_config",
            name="n",
            run_config_properties=_props(LOCKED, WRAPPER, OLD_WRAPPER),
        )
        assert view["id"] == "424242000009"
        assert [(c[0], c[1]) for c in kiln.calls] == [("GET", tool), ("POST", post)]

    def test_a_code_tool_that_calls_a_locked_tool_is_refused(self, tmp_path):
        gateway, kiln = _locked_gateway(tmp_path)
        with pytest.raises(gw.GatewayError, match="may not wrap"):
            _run(
                gateway,
                "create_code_tool",
                name="wrapper",
                tool_function_name="search_short",
                tool_description="search, shorter",
                parameters_schema={"type": "object", "properties": {}},
                code="def search_short():\n    return ''\n",
                tool_allowlist=[LOCKED],
            )
        assert kiln.calls == []

    def test_a_locked_code_tool_or_skill_is_not_archived(self, tmp_path):
        lock = ToolLock(
            ("kiln_tool::code::424242000004", "kiln_tool::skill::424242000005")
        )
        gateway, kiln = _locked_gateway(tmp_path, lock=lock)
        with pytest.raises(gw.GatewayError, match="may not be archived"):
            _run(gateway, "archive_code_tool", id="424242000004")
        with pytest.raises(gw.GatewayError, match="may not be archived"):
            _run(gateway, "archive_skill", id="424242000005")
        assert kiln.calls == []

    def test_an_unreadable_lock_refuses_every_run_config(self, tmp_path):
        (tmp_path / RESTRICTIONS_FILE).write_text("{bad")
        gateway, kiln = _locked_gateway(tmp_path, lock=load_tool_lock(tmp_path))
        with pytest.raises(gw.GatewayError, match="unreadable"):
            _run(
                gateway,
                "create_run_config",
                name="n",
                run_config_properties=_props(LOCKED),
            )
        assert kiln.calls == []

    def test_no_lock_checks_nothing(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, {})
        kiln.routes[("POST", f"{gateway.s.base}/run_configs")] = {"id": "424242000009"}
        _run(gateway, "create_run_config", name="n", run_config_properties=_props())
        assert [call[0] for call in kiln.calls] == ["POST"]


def gateway_project() -> str:
    from test_gateway import PROJECT

    return PROJECT


# ---- the loop: before a run config is measured or promoted ----

BASE_TOOL = "kiln_tool::code::tool_1"  # the tool of `_ctx`'s rc_base


def _add_rc(ctx, rc_id: str, *tools: str) -> None:
    props = dict(ctx.kiln.run_configs[0]["run_config_properties"])
    props["tools_config"] = {"tools": list(tools)}
    ctx.kiln.run_configs.append(
        {"id": rc_id, "name": rc_id, "run_config_properties": props}
    )


def _serve_code_tools(ctx, tools: dict[str, list[str]]) -> None:
    """Answer `GET …/code_tools/<id>` with the tool's allowlist."""
    inner = ctx.kiln.call

    async def call(method, url_path, *args, **kwargs):
        if method == "GET" and "/code_tools/" in url_path:
            return {"tool_allowlist": tools[url_path.rsplit("/", 1)[1]]}
        return await inner(method, url_path, *args, **kwargs)

    ctx.kiln.call = call


async def test_the_start_records_the_baselines_tools_for_the_gateway(tmp_path):
    ctx = _ctx(tmp_path, policy=None)
    ctx.run_dir = tmp_path
    ctx.restrictions = Restrictions(locked_tools=(BASE_TOOL,))
    await lock_baseline_tools(ctx, "rc_base")
    assert ctx.tool_lock == ToolLock((BASE_TOOL,), frozenset({BASE_TOOL}))
    assert load_tool_lock(tmp_path) == ctx.tool_lock
    data = json.loads((tmp_path / RESTRICTIONS_FILE).read_text())
    assert data["statements"] == [f"these tools must not change: {BASE_TOOL}"]


async def test_a_locked_tool_that_the_baseline_lacks_stops_the_start(tmp_path):
    ctx = _ctx(tmp_path, policy=None)
    ctx.restrictions = Restrictions(locked_tools=("kiln_tool::code::not_there",))
    with pytest.raises(SetupPreconditionError, match="does not attach"):
        await lock_baseline_tools(ctx, "rc_base")


async def test_a_run_config_that_breaks_the_lock_is_never_measured(tmp_path):
    ctx = _ctx(tmp_path, policy=None)
    ctx.restrictions = Restrictions(locked_tools=(BASE_TOOL,))
    await lock_baseline_tools(ctx, "rc_base")
    _add_rc(ctx, "rc_dropped")
    _add_rc(ctx, "rc_wrapped", BASE_TOOL, "kiln_tool::code::w1")
    _add_rc(ctx, "rc_fine", BASE_TOOL, "kiln_tool::code::w2")
    _serve_code_tools(ctx, {"w1": [BASE_TOOL], "w2": []})
    assert "drop or replace" in (await policy_refusal(ctx, "rc_dropped") or "")
    assert "may not wrap" in (await policy_refusal(ctx, "rc_wrapped") or "")
    assert await policy_refusal(ctx, "rc_fine") is None
    assert await policy_refusal(ctx, "rc_base") is None
    assert "not found" in (await tool_lock_refusal(ctx, "rc_nowhere") or "")


def test_the_planner_input_carries_the_restrictions():
    from kiln_harness_tuner.schemas import BatchPlanInput

    fields = BatchPlanInput.model_fields
    assert {"restrictions", "restriction_limits", "locked_tools"} <= set(fields)


def test_asyncio_is_not_needed_for_the_pure_checks():
    # The lock's own checks are plain functions: the gateway and the loop
    # call the same ones.
    assert asyncio.iscoroutinefunction(tool_lock_refusal)
    assert not asyncio.iscoroutinefunction(ToolLock.dropped)
