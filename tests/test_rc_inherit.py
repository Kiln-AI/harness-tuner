"""A candidate never loses a run-config property of its parent only because
the tuner does not know it (`rc_inherit`, through the gateway's
`create_run_config`). Every id is invented."""

from __future__ import annotations

import pytest
from test_gateway import _gateway, _run

from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.gateway.ops import RC_PROP_KEYS
from kiln_harness_tuner.rc_inherit import (
    FROZEN_KEYS,
    InheritError,
    check_fork,
    inherit,
)
from kiln_harness_tuner.schemas import ProbeCheck

PARENT_ID = "424242000101"
NEW_ID = "424242000102"


def _props(provider: str = "fireworks_ai", **extra) -> dict:
    return {
        "type": "kiln_agent",
        "model_provider_name": provider,
        "model_name": "llama_x",
        "prompt_id": "id::424242000103",
        "structured_output_mode": "json_schema",
        **extra,
    }


ZDR = {"openrouter_routing": {"zero_data_retention": True}}


# ---- the rule ----


def test_an_unknown_property_of_the_parent_is_carried():
    parent = _props(future_knob={"a": 1})
    child, carried = inherit(parent, _props(), RC_PROP_KEYS)
    assert child["future_knob"] == {"a": 1}
    assert carried == ["future_knob"]
    child["future_knob"]["a"] = 2  # a copy, not the parent's own value
    assert parent["future_knob"] == {"a": 1}


def test_a_property_the_candidate_names_is_its_own():
    child, carried = inherit(_props(future_knob=1), _props(future_knob=2), RC_PROP_KEYS)
    assert child["future_knob"] == 2 and carried == []


def test_a_managed_property_left_out_stays_out():
    parent = _props(tools_config={"tools": ["kiln_tool::code::424242000104"]})
    child, carried = inherit(parent, _props(), RC_PROP_KEYS)
    assert "tools_config" not in child and carried == []


@pytest.mark.parametrize("key", [k for k in FROZEN_KEYS if k != "openrouter_routing"])
def test_a_frozen_property_is_carried_and_cannot_change(key):
    child, carried = inherit(_props(**{key: 7}), _props(), RC_PROP_KEYS)
    assert child[key] == 7 and carried == [key]
    with pytest.raises(InheritError, match="not a lever"):
        inherit(_props(**{key: 7}), _props(**{key: 8}), RC_PROP_KEYS)
    with pytest.raises(InheritError, match="not a lever"):
        inherit(_props(), _props(**{key: 8}), RC_PROP_KEYS)


def test_a_child_on_openrouter_keeps_zero_data_retention():
    parent = _props("openrouter", **ZDR)
    child, carried = inherit(parent, _props("openrouter"), RC_PROP_KEYS)
    assert child["openrouter_routing"] == {"zero_data_retention": True}
    assert carried == ["openrouter_routing"]
    for dropped in ({}, {"zero_data_retention": False}, None):
        with pytest.raises(InheritError, match="must keep it"):
            inherit(
                parent,
                _props("openrouter", openrouter_routing=dropped),
                RC_PROP_KEYS,
            )


def test_zero_data_retention_may_be_added_and_nothing_else_changes():
    parent = _props("openrouter", openrouter_routing={"sort": "price"})
    stricter = {"sort": "price", "zero_data_retention": True}
    child, _ = inherit(
        parent, _props("openrouter", openrouter_routing=stricter), RC_PROP_KEYS
    )
    assert child["openrouter_routing"] == stricter
    inherit(_props("openrouter"), _props("openrouter", **ZDR), RC_PROP_KEYS)
    with pytest.raises(InheritError, match="not a lever"):
        inherit(
            parent,
            _props("openrouter", openrouter_routing={"sort": "latency"}),
            RC_PROP_KEYS,
        )


def test_the_routing_does_not_follow_a_child_off_openrouter():
    child, carried = inherit(_props("openrouter", **ZDR), _props(), RC_PROP_KEYS)
    assert "openrouter_routing" not in child and carried == []
    # Named by a child that copied the view and changed the provider: removed.
    child, _ = inherit(_props("openrouter", **ZDR), _props(**ZDR), RC_PROP_KEYS)
    assert "openrouter_routing" not in child


def test_a_frozen_property_may_be_unset_on_another_model():
    parent = _props(max_output_tokens=32000)
    other = {**_props(), "model_name": "small_model", "max_output_tokens": None}
    child, carried = inherit(parent, other, RC_PROP_KEYS)
    assert child["max_output_tokens"] is None and carried == []
    with pytest.raises(InheritError, match="not a lever"):
        inherit(parent, _props(max_output_tokens=None), RC_PROP_KEYS)
    # Not set on either side: `null` and absent are the same.
    inherit(_props(), _props(seed=None), RC_PROP_KEYS)


def test_a_screen_fork_runs_the_same_checks():
    parent = _props("openrouter", **ZDR, seed=3)
    assert "not a lever" in (check_fork(parent, {**parent, "seed": 4}) or "")
    assert "must keep it" in (
        check_fork(parent, {**parent, "openrouter_routing": {}}) or ""
    )
    moved = {**parent, "model_provider_name": "fireworks_ai"}
    assert check_fork(parent, moved) is None
    assert "openrouter_routing" not in moved  # removed off OpenRouter
    assert check_fork(parent, dict(parent)) is None


def test_a_probe_may_not_override_a_frozen_property():
    with pytest.raises(ValueError, match="not levers"):
        ProbeCheck(count="tool_calls", overrides={"seed": 1})


def test_a_parent_without_the_properties_changes_nothing():
    # A server that does not return a property gives nothing to carry.
    request = _props(temperature=0.2)
    child, carried = inherit(_props(), request, RC_PROP_KEYS)
    assert child == request and carried == []


# ---- through the gateway ----


def _routes(gateway, kiln, parent_props: dict) -> None:
    kiln.routes[("GET", f"{gateway.s.base}/run_configs")] = [
        {"id": PARENT_ID, "name": "parent", "run_config_properties": parent_props}
    ]
    kiln.routes[("POST", f"{gateway.s.base}/run_configs")] = lambda _q, body: {
        "id": NEW_ID,
        "name": body["name"],
        "run_config_properties": body["run_config_properties"],
    }


def _create(gateway, props: dict, derived_from=(PARENT_ID,)):
    return _run(
        gateway,
        "create_run_config",
        name="candidate",
        run_config_properties=props,
        provenance={"derived_from_ids": list(derived_from), "origin": "agent"},
    )


def _posted(kiln) -> dict:
    (body,) = [call[3] for call in kiln.calls if call[0] == "POST"]
    return body["run_config_properties"]


def test_the_gateway_carries_an_unknown_property_and_says_so(tmp_path):
    gateway, kiln = _gateway(tmp_path, {})
    _routes(gateway, kiln, _props(seed=11, future_knob="on"))
    view = _create(gateway, _props())
    assert _posted(kiln)["future_knob"] == "on"
    assert _posted(kiln)["seed"] == 11
    assert sorted(view["carried_properties"]) == ["future_knob", "seed"]
    # The view shows the frozen keys, so an agent can see them.
    assert view["run_config_properties"]["seed"] == 11


def test_the_gateway_refuses_a_child_that_drops_zero_data_retention(tmp_path):
    gateway, kiln = _gateway(tmp_path, {})
    _routes(gateway, kiln, _props("openrouter", **ZDR))
    with pytest.raises(gw.GatewayError, match="zero data retention"):
        _create(gateway, _props("openrouter", openrouter_routing={}))
    assert not [call for call in kiln.calls if call[0] == "POST"]


@pytest.mark.parametrize("derived_from", [(), ("424242999999",)])
def test_a_request_with_no_parent_run_config_is_refused(tmp_path, derived_from):
    # Without the parent, a hidden setting would be dropped with no check.
    gateway, kiln = _gateway(tmp_path, {})
    _routes(gateway, kiln, _props("openrouter", **ZDR))
    with pytest.raises(gw.GatewayError, match="derived_from_ids"):
        _create(gateway, _props("openrouter"), derived_from=derived_from)
    assert not [call for call in kiln.calls if call[0] == "POST"]


def test_a_merge_takes_the_first_run_config_it_names(tmp_path):
    # A merge names the baseline first, then each ingredient; an id that is
    # not a run config of the task is skipped.
    gateway, kiln = _gateway(tmp_path, {})
    _routes(gateway, kiln, _props(future_knob="baseline"))
    kiln.routes[("GET", f"{gateway.s.base}/run_configs")].append(
        {"id": "424242000105", "run_config_properties": _props(future_knob="x")}
    )
    view = _create(
        gateway, _props(), derived_from=("424242999999", PARENT_ID, "424242000105")
    )
    assert _posted(kiln)["future_knob"] == "baseline"
    assert view["carried_properties"] == ["future_knob"]


def test_the_gateway_refuses_a_change_of_a_frozen_property(tmp_path):
    gateway, kiln = _gateway(tmp_path, {})
    _routes(gateway, kiln, _props(seed=11))
    with pytest.raises(gw.GatewayError, match="'seed' is not a lever"):
        _create(gateway, _props(seed=12))
    assert not [call for call in kiln.calls if call[0] == "POST"]


def test_a_failed_parent_read_fails_the_create(tmp_path):
    # Failing closed: a run config made without the parent's settings could
    # drop one with no message.
    gateway, kiln = _gateway(tmp_path, {})
    with pytest.raises(AssertionError, match="unrouted GET"):
        _create(gateway, _props())
    assert not [call for call in kiln.calls if call[0] == "POST"]


def test_reads_carry_no_carried_properties(tmp_path):
    gateway, kiln = _gateway(tmp_path, {})
    _routes(gateway, kiln, _props(future_knob="on"))
    (row,) = _run(gateway, "list_run_configs")
    assert "carried_properties" not in row
