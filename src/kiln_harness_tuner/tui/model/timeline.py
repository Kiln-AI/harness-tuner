"""The run timeline and the wave estimate (functional spec 7.3 and 7.4,
with 16.15; ui_design 7.2 and 7.3).

No event carries a start time: segments run between the stamps of the
events that open and close them (audit 1.4). Times are seconds from the
run's start, which is `run_start.deadline − timeout_mins × 60` on the
loop's own clock (audit 5.2).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from statistics import fmean
from typing import TYPE_CHECKING

from kiln_harness_tuner.orchestrator import MIN_WAVE_START_SECS, WRAP_UP_WINDOW_SECS
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.state import TimelineView

if TYPE_CHECKING:
    from kiln_harness_tuner.tui.model.fold import RunModel

TICK_SECS = 30 * 60


def axis_ticks(limit_secs: float) -> tuple[tuple[float, str], ...]:
    """A tick every 30 minutes, and one at the time limit."""
    ticks = []
    at = 0.0
    while at < limit_secs:
        ticks.append((at, words.span_of_minutes(round(at / 60))))
        at += TICK_SECS
    ticks.append((limit_secs, words.span_of_minutes(round(limit_secs / 60))))
    return tuple(ticks)


def projected_waves(
    *,
    wave: int,
    cycle_start: float,
    cycle_spend: float,
    now: float,
    spend_now: float,
    deadline: float,
    budget: float | None,
    cycles: list[tuple[float, float]],
    wrap_up: bool,
) -> list[tuple[float, int]]:
    """The waves after `wave` that the loop can still start, by its own
    rules (`orchestrator._run_loop`): none starts with less than
    `MIN_WAVE_START_SECS` left; one that starts inside the wrap-up window
    (`should_wrap`: the larger of `WRAP_UP_WINDOW_SECS` and the mean cycle)
    is the last; at the budget the run exits, and inside one mean cycle cost
    of it the wave is the last. Each wave starts one mean cycle after the
    one before, and the next no earlier than now. `cycles` are the finished
    main waves' (seconds, cost)."""
    # Architecture 7.2 gives M as one closed form over the wrap-up window.
    # This walks the loop's own rules instead, so that both constants count
    # (functional spec 16.15) and "wave N of ~M" names the same waves the
    # timeline draws as projected.
    if wrap_up or not cycles:
        return []
    mean_secs = fmean(secs for secs, _cost in cycles)
    mean_cost = fmean(cost for _secs, cost in cycles)
    if mean_secs <= 0:
        return []
    window = max(WRAP_UP_WINDOW_SECS, mean_secs)
    start = max(cycle_start + mean_secs, now)
    spent = max(cycle_spend + mean_cost, spend_now)
    out: list[tuple[float, int]] = []
    while True:
        remaining = deadline - start
        if remaining < MIN_WAVE_START_SECS:
            break
        last = remaining < window
        if budget is not None:
            if spent >= budget:
                break
            if budget - spent < mean_cost:
                last = True
        wave += 1
        out.append((start, wave))
        if last:
            break
        start += mean_secs
        spent += mean_cost
    return out


def projection(model: RunModel, now: float) -> list[tuple[float, int]]:
    """The waves to come, from the model's current cycle (priority 3)."""
    if (
        model.exit_reason is not None
        or model.deadline is None
        or model.cycle_wave is None
        or model.cycle_start is None
    ):
        return []
    return projected_waves(
        wave=model.cycle_wave,
        cycle_start=model.cycle_start,
        cycle_spend=model.cycle_spend,
        now=now,
        spend_now=model.tracker.status.budget_spend_usd,
        deadline=model.deadline,
        budget=model.budget,
        cycles=model.cycles,
        wrap_up=model.wrap_up,
    )


def waves_estimate(model: RunModel, now: float) -> int | None:
    """M in "wave N of ~M": N plus the waves to come. Blank (None) until the
    first main wave's cycle has ended; N itself in the wrap-up wave."""
    if model.cycle_wave is None:
        return None
    if model.wrap_up:
        return model.cycle_wave
    if not model.cycles:
        return None
    return model.cycle_wave + len(projection(model, now))


def timeline_view(model: RunModel, now: float) -> TimelineView | None:
    if model.zero is None or model.limit is None or model.zero_wall is None:
        return None
    ended = model.exit_reason is not None and not model.open_segments
    if ended:
        ends = [seg.end for seg in model.segments if seg.end is not None]
        at = max(ends, default=0.0)
    else:
        at = max(now - model.zero, 0.0)
    segments = tuple(
        seg if seg.end is not None else replace(seg, end=max(at, seg.start))
        for seg in model.segments
    )
    projected = tuple(
        (start - model.zero, wave) for start, wave in projection(model, now)
    )
    moment = model.zero_wall + timedelta(seconds=at)
    label = f"{'ended' if ended else 'now'} {words.clock(moment)}"
    return TimelineView(
        limit=model.limit,
        segments=segments,
        projected=projected,
        now=at,
        now_label=label,
        ended=ended,
        ticks=axis_ticks(model.limit),
    )
