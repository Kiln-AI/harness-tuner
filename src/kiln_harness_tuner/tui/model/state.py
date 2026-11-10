"""The run model's dataclasses: the fold's own state, and the views the
run screen's sections read (functional spec section 7, ui_design section 7).

The views are frozen and final: every word and number is already written.
Where color carries a meaning, a view holds `Span`s with a `Tone`, and the
widgets map each tone to a theme color. A tone never stands alone: the
words or a glyph say the same (functional spec section 8).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from kiln_harness_tuner.schemas import (
    CandidateValTable,
    ItemOutcome,
    LatencyComparison,
    PerEvalResult,
    ProbeResult,
    PromotionDecision,
)

Tone = Literal[
    "fg",
    "bold",
    "muted",
    "faint",
    "primary",
    "success",
    "error",
    "warning",
    "promoted",
    # The spinner's frame: the widget draws the current frame in its place.
    "spinner",
]


@dataclass(frozen=True)
class Span:
    text: str
    tone: Tone = "fg"


Line = tuple[Span, ...]
SPIN = Span("", "spinner")


def plain(line: Sequence[Span]) -> str:
    return "".join(span.text for span in line)


def joined(parts: Sequence[Line], separator: str = " · ") -> Line:
    """The parts, with a muted separator between them."""
    out: list[Span] = []
    for index, part in enumerate(parts):
        if index:
            out.append(Span(separator, "muted"))
        out.extend(part)
    return tuple(out)


# ---- the CLI's facts ----


@dataclass(frozen=True)
class RunInfo:
    """What the CLI knows about the run, not the loop (functional spec 12,
    "From the CLI"). The run id is known only once `_run` makes it."""

    project_id: str
    task_id: str
    kiln_host: str
    objective: str = "quality"
    agent_cli: str = "claude"  # the kind: names the agent's spend lane
    agent_cli_text: str = "claude -p"
    agent_slots: int = 4
    eval_slots: int = 4
    project_name: str = ""
    task_name: str = ""
    run_id: str | None = None


# ---- the fold's state ----

ConcernKey = tuple[str, str]  # (eval_id, score_key)
ConcernKind = Literal["pass", "rating", "cost", "latency"]
COST_KEY: ConcernKey = ("", "cost")
LATENCY_KEY: ConcernKey = ("", "latency")
LOWER_IS_BETTER = "lower_is_better"


@dataclass(frozen=True)
class Concern:
    """One Results card: an eval score that can decide a promotion, or cost
    per request, or latency. Values are raw: a pass rate as a fraction, a
    rating's mean, USD per request, milliseconds."""

    key: ConcernKey
    name: str
    kind: ConcernKind
    direction: str = "higher_is_better"

    @property
    def eval_id(self) -> str | None:
        return self.key[0] or None

    def better(self, value: float, than: float) -> bool:
        if self.direction == LOWER_IS_BETTER:
            return value < than
        return value > than


COST = Concern(COST_KEY, "Cost / request", "cost", LOWER_IS_BETTER)
LATENCY = Concern(LATENCY_KEY, "Latency", "latency", LOWER_IS_BETTER)

Cell = Literal["fixed", "held", "failing", "broke", "unknown"]
# The loop's live item states, and the card states they map to.
BUILD_STATES = frozenset({"building_eval", "building_rc", "reviewing"})


@dataclass
class ItemModel:
    """One planned item, from its `wave_start` entry to its verdict.
    `state` is the last live state: an outcome does not replace it, so a
    stopped card knows the step it stopped at."""

    item_id: str
    wave: int
    kind: str  # fix, merge, eval, measure, remeasure, probe (`status.item_kind`)
    details: Mapping[str, Any]
    state: str = "planned"
    state_since: float = 0.0
    builds: int = 0
    finding: str | None = None  # the last check finding that forces a rebuild
    rebuild_reason: str | None = None
    last_detail: str | None = None
    rc_id: str | None = None
    planned: int | None = None
    judged: int = 0
    fixed: int = 0
    broke: int = 0
    cells: list[Cell] = field(default_factory=list)
    spend_usd: float = 0.0
    outcome: ItemOutcome | None = None
    probe: ProbeResult | None = None
    train_rows: list[PerEvalResult] = field(default_factory=list)
    train_latency: LatencyComparison | None = None
    train_cost: LatencyComparison | None = None
    cut_off: bool = False
    # Set by the gate: at_gate, promoted, rejected, held, copied (a
    # `gate_block`), blocked (by the integrity checks), refused (by the
    # model policy, before the gate) or pooled (not judged).
    gate: str | None = None

    @property
    def landed(self) -> bool:
        return self.outcome is not None or self.cut_off

    @property
    def live(self) -> bool:
        if self.gate == "at_gate":
            return True
        return not self.landed and self.state != "planned"

    @property
    def targets(self) -> list[str]:
        """The eval ids the item targets, from its wave-start details."""
        targets = self.details.get("targets") or []
        return [str(t.get("eval_id")) for t in targets if isinstance(t, Mapping)]


@dataclass
class GateModel:
    wave: int
    started: float
    ended: float | None = None
    rc_ids: list[str] = field(default_factory=list)
    eval_ids: list[str] = field(default_factory=list)
    # (rc_id, eval_id) → (stored, expected); a failed fill stores 0.
    fills: dict[tuple[str, str], tuple[int, int | None]] = field(default_factory=dict)
    tables: list[CandidateValTable] = field(default_factory=list)
    decision: PromotionDecision | None = None
    blocked: dict[str, list[str]] = field(default_factory=dict)
    copied: set[str] = field(default_factory=set)
    skipped: str | None = None
    promoted: str | None = None  # the confirmed winner, at the gate's end


@dataclass
class WaveModel:
    """One `wave_start`: a main wave, a probe phase, or wave 0's recon."""

    wave: int
    probe_phase: bool
    wrap_up: bool
    parent_rc_id: str | None
    headline: str
    items: list[ItemModel]


StepStatus = Literal["ok", "fail", "warn", "busy", "skip"]
PlanKind = Literal["plan", "replan", "rescreen", "screen", "probes"]


@dataclass
class PlanStep:
    """One step of a wave's planning: a planner session (`plan`, `replan`
    after probes, `rescreen` after a screening round), a screening round,
    or a probe phase."""

    kind: PlanKind
    started: float
    ended: float | None = None
    then: str | None = None  # what the plan asked for next: screen or probes
    round: int = 0  # a screening round's run-wide number; 0 until known
    candidates: int = 0
    items_each: int | None = None
    survivors: int | None = None


@dataclass
class StartStep:
    """One line of the Starting checklist, kept in first-seen order."""

    status: StepStatus
    label: str
    text: str
    since: float | None = None  # a busy line's start: its text adds the elapsed


@dataclass
class Unconfirmed:
    """One train result for one concern: an arm's change against its parent,
    or a screen survivor's cost or latency. `factor` results multiply the
    parent's value (cost and latency); the others add to it."""

    key: ConcernKey
    rc_id: str | None
    item_id: str | None
    label: str | None  # a screen candidate's own label
    change: float
    factor: bool
    clear: bool
    generation: int


@dataclass(frozen=True)
class Segment:
    """One stretch of the run timeline, in seconds from the run's start."""

    kind: str  # setup, recon, plan, screen, items, gate, backoff, report
    wave: int
    start: float
    end: float | None = None
    mark: str | None = None  # a gate's: promotion or no_winner


# ---- the views ----

Mode = Literal["starting", "planning", "running", "gate", "done"]
StepState = Literal["done", "current", "later", "stopped"]


@dataclass(frozen=True)
class TitleView:
    task: str
    run: str  # "run 261109-1412", or empty before the run id is known
    meta: str  # the agent CLI and the slot counts


@dataclass(frozen=True)
class GoalRow:
    role: Literal["optimize", "hold", "watch"]
    what: str
    note: str


@dataclass(frozen=True)
class GoalView:
    rows: tuple[GoalRow, ...]
    data: Line


@dataclass(frozen=True)
class GaugeView:
    label: str
    value: str
    done: float
    total: float | None  # None: no end to the gauge (no budget)
    limit: str


@dataclass(frozen=True)
class ClockView:
    time: GaugeView
    spend: GaugeView
    lanes: Line
    times: str
    wave: Line
    last_wave: bool


@dataclass(frozen=True)
class TimelineView:
    limit: float
    segments: tuple[Segment, ...]  # an open segment ends at `now`
    projected: tuple[tuple[float, int], ...]  # (start, wave) of the waves to come
    now: float
    now_label: str
    ended: bool
    ticks: tuple[tuple[float, str], ...]


@dataclass(frozen=True)
class TierView:
    value: str
    change: str
    change_tone: Tone
    fraction: float  # the bar's length, 0 to 1


@dataclass(frozen=True)
class ConcernView:
    title: str
    qualifier: str
    start: TierView | None
    # Beside the Start row: "not measured yet" with no value, or "train"
    # when this concern's value is on train while the key says val.
    start_note: str
    confirmed: TierView | None
    unconfirmed: TierView | None
    in_flight: int | None  # None for cost and latency (16.12)
    subtitle: Line


@dataclass(frozen=True)
class ResultsView:
    key: tuple[Line, Line, Line]
    cards: tuple[ConcernView, ...]
    compare_rc_ids: tuple[str, ...]


@dataclass(frozen=True)
class StepLine:
    status: StepStatus
    label: str
    text: str


@dataclass(frozen=True)
class StripView:
    """Line 4 while an arm is judged: one cell per judged item, in judging
    order, then `total − len(cells)` queued items."""

    cells: tuple[Cell, ...]
    total: int
    count: str


@dataclass(frozen=True)
class GaugeLine:
    done: int
    total: int
    label: str


@dataclass(frozen=True)
class NoteLine:
    text: str
    tone: Tone = "muted"


@dataclass(frozen=True)
class CardView:
    item_id: str
    title: str
    qualifier: str
    text: str
    steps: tuple[tuple[str, StepState], ...]
    line4: StripView | GaugeLine | NoteLine | None
    line5: Line
    subtitle: str
    live: bool


@dataclass(frozen=True)
class DoneView:
    ended: str
    summary: Line
    totals: tuple[tuple[str, str], ...]  # (count, word)
    best: Line
    report: str
    report_path: str | None
    next_step: str
    subtitle: str


@dataclass(frozen=True)
class ExperimentsView:
    mode: Mode
    header: Line
    elapsed: str
    steps: tuple[StepLine, ...]
    subhead: str
    cards: tuple[CardView, ...]
    note: str
    done: DoneView | None


@dataclass(frozen=True)
class LogLine:
    time: str
    glyph: str
    tone: Tone
    text: str
    bold: bool = False
    divider: bool = False  # a wave divider: `text` names the wave
