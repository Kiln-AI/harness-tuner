"""What a new run config takes from its parent, so that no candidate loses a
property only because the tuner does not know it.

An agent reads a run config through the gateway, whose view shows only the
properties the tuner knows (`gateway.ops.RC_PROP_KEYS` and `FROZEN_KEYS`).
Kiln can have more: a newer server adds properties, and the view does not
show them. An agent that copies the view into a new run config then drops
them with no message. So `create_run_config` runs `inherit` on the request,
with the parent that its provenance names (`derived_from_ids`, the first id
that is a run config of the task):

- A property that the parent has, that the candidate does not name, and
  that the tuner does not manage, is copied from the parent. The answer
  names each one (`RunConfigView.carried_properties`).
- A property in `FROZEN_KEYS` is not a lever yet: the candidate keeps the
  parent's value. A candidate that changes one is refused.
- `openrouter_routing` applies only on OpenRouter. It is carried, and
  frozen, while the candidate stays on OpenRouter. A candidate on OpenRouter
  may not drop `zero_data_retention` from it when the parent has it. It may
  add `zero_data_retention: true`: that only makes the route stricter.

A server that does not return a property gives nothing to carry, so the
request goes to Kiln as it came.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping
from typing import Any

OPENROUTER = "openrouter"
ROUTING = "openrouter_routing"
ZERO_RETENTION = "zero_data_retention"
# Run-config properties that a newer Kiln adds and that are not levers of
# the tuner yet: a candidate keeps the parent's value.
FROZEN_KEYS = (
    "max_output_tokens",
    "seed",
    "frequency_penalty",
    "presence_penalty",
    ROUTING,
)
_ABSENT = object()


class InheritError(ValueError):
    """The candidate changes a property that it must keep."""


def _asks_zero_retention(routing: Any) -> bool:
    return isinstance(routing, Mapping) and routing.get(ZERO_RETENTION) is True


def inherit(
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
    managed: Collection[str],
) -> tuple[dict[str, Any], list[str]]:
    """The child's properties with what it takes from `parent`, and the
    names of the properties carried. `managed` are the properties that the
    tuner sets itself: a candidate that leaves one out leaves it out on
    purpose. Raises `InheritError` for a change that is refused."""
    props = dict(child)
    on_openrouter = props.get("model_provider_name") == OPENROUTER
    carried: list[str] = []
    for key, value in parent.items():
        if key in managed or key in props:
            continue
        if key == ROUTING and not on_openrouter:
            continue
        props[key] = copy.deepcopy(value)
        carried.append(key)
    for key in FROZEN_KEYS:
        if key == ROUTING:
            if on_openrouter:
                _check_routing(parent.get(ROUTING), props.get(ROUTING))
            continue
        before, after = parent.get(key, _ABSENT), props.get(key, _ABSENT)
        if before != after:
            raise InheritError(
                f"the run config property {key!r} is not a lever of the tuner"
                f" yet: keep the parent's value ({_text(before)}), not"
                f" {_text(after)}"
            )
    return props, carried


def _check_routing(before: Any, after: Any) -> None:
    if _asks_zero_retention(before) and not _asks_zero_retention(after):
        raise InheritError(
            "the parent asks OpenRouter for zero data retention"
            f" ({ROUTING}.{ZERO_RETENTION}): a candidate on OpenRouter must keep"
            " it"
        )
    if after == before:
        return
    base = before if isinstance(before, Mapping) else {}
    if after != {**base, ZERO_RETENTION: True}:
        raise InheritError(
            f"the run config property {ROUTING!r} is not a lever of the tuner"
            f" yet: keep the parent's value ({_text(before)}). The one change"
            f" allowed is to add {ZERO_RETENTION}: true"
        )


def _text(value: Any) -> str:
    return "not set" if value is _ABSENT or value is None else repr(value)
