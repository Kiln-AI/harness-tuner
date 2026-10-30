"""What the loop optimises (`--objective`), in one place.

- `quality` — the eval scores, the behaviour before this module.
- `speed` — per-request wall-clock latency of the task call.
- `cost` — per-request provider cost of the task call.

Speed and cost are the two EFFICIENCY objectives and share one machinery:
the viability / screening stage, the paired drive passes (which measure
latency and cost from the same runs), the time-first staging of arms, and
the val gate that requires the candidate to be clearly better on the
objective's metric with quality equal or better. The objective only picks
which paired comparison decides (`decisive`). Under quality, no efficiency
screen applies; a model, provider or thinking swap still passes the
viability stage's valid-output and hard-case checks before a full
measurement.
"""

from __future__ import annotations

from typing import Any, Literal

from kiln_harness_tuner.schemas import LatencyComparison

OBJECTIVES = ("quality", "speed", "cost")
EFFICIENCY = frozenset({"speed", "cost"})


def is_efficiency(objective: str) -> bool:
    return objective in EFFICIENCY


def metric_of(objective: str) -> Literal["latency_ms", "cost_usd"] | None:
    return {"speed": "latency_ms", "cost": "cost_usd"}.get(objective)  # type: ignore[return-value]


def better_word(objective: str) -> str:
    return {"speed": "faster", "cost": "cheaper"}.get(objective, "better")


def decisive(holder: Any, objective: str) -> LatencyComparison | None:
    """The comparison that decides under this objective, from anything that
    carries `latency` and `cost` (a bundle, a val table, a screen result)."""
    if objective == "speed":
        return getattr(holder, "latency", None)
    if objective == "cost":
        return getattr(holder, "cost", None)
    return None
