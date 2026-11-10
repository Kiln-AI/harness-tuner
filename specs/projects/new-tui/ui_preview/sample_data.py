"""The invented sample run that the preview draws.

A freight-depot desk agent, tuned for quality with a $50 budget and a 3-hour
limit. The numbers are made up, but they agree across the sections: the
Results cards, the experiment cards, the timeline and the log tell one story.
Every id is in the invented 424242 band.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The run and its task
# ---------------------------------------------------------------------------

TASK = "Depot Ops Desk"
TASK_ID = "424242783017"
PROJECT = "Depot Ops"
PROJECT_ID = "424242110001"
RUN_ID = "261109-1412"
KILN_HOST = "http://localhost:8757"
AGENT_CLI = "claude"
AGENT_SLOTS = 4
EVAL_SLOTS = 4
VERSION = "v0.1.0"
TIME_LIMIT_MIN = 180
BUDGET = 50.0
STARTED = "14:12"
ENDS = "17:12"
REPORT_PATH = "~/.kiln_ai/harness_tuner/reports/261109-1412-depot-ops-desk.md"

COMPARE_URL = (
    f"{KILN_HOST}/specs/{PROJECT_ID}/{TASK_ID}/compare"
    "?models=rc-0003,w3-4,w4-2,w4-1&columns=4&from=optimize"
)
LINEAGE_URL = f"{KILN_HOST}/optimize/{PROJECT_ID}/{TASK_ID}/experiment_lineage"


@dataclass(frozen=True)
class Concern:
    """One Results card: an eval score, cost or latency."""

    name: str
    qualifier: str  # muted text after the name in the card title
    kind: str  # "pass", "rating", "cost" or "latency"


CONCERNS = (
    Concern("Refusal honesty", "p0", "pass"),
    Concern("Hold placement", "p1", "pass"),
    Concern("Output format", "p1", "pass"),
    Concern("Tone and policy", "p2 · 1–5", "rating"),
    Concern("Escalation", "p2", "pass"),
    Concern("Cost / request", "", "cost"),
    Concern("Latency", "median", "latency"),
)
EVAL_CONCERNS = tuple(c.name for c in CONCERNS if c.kind in ("pass", "rating"))

START_VAL = (70.2, 63.0, 93.3, 4.08, 54.8, 0.042, 8.4)
START_TRAIN = (68.9, 61.5, 93.0, 4.05, 53.2, 0.042, 8.5)

# ---------------------------------------------------------------------------
# Experiment items
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Strip:
    """Line 4 as a judged-item strip. One code per judged item: F fixed,
    H held, S still failing, B broke. Items past the codes are queued."""

    codes: str
    total: int


@dataclass(frozen=True)
class Progress:
    """Line 4 as a gauge with a label."""

    done: int
    total: int
    label: str


@dataclass(frozen=True)
class Note:
    """Line 4 as a line of text."""

    text: str
    tone: str = "muted"  # or "warning"


@dataclass(frozen=True)
class SoFar:
    """Line 5: fixed and broke, so far or final."""

    fixed: int
    broke: int
    so_far: bool = True


@dataclass(frozen=True)
class Verdict:
    """Line 5 once the item has landed."""

    tone: str  # success, nowin, stopped, promoted, rejected or done
    text: str


Stage = tuple[str, str]  # (name, state): done, current, later or stopped


def stages(
    *names: str,
    current: str | None = None,
    stopped: str | None = None,
    queued: bool = False,
) -> tuple[Stage, ...]:
    """The stages before `current` (or `stopped`) are done, that stage is
    current (or stopped), and later stages wait. With neither, every stage is
    done, or every stage waits when `queued`."""
    mark = current or stopped
    state = "later" if queued else "done"
    result: list[Stage] = []
    for name in names:
        if name == mark:
            result.append((name, "current" if current else "stopped"))
            state = "later"
        else:
            result.append((name, state))
    return tuple(result)


FIX = ("build", "measure", "analyze")
GATED = (*FIX, "gate")
PROBE = ("asked", "answered")
ASKING = ("asked", "answering")


@dataclass(frozen=True)
class Item:
    """One experiment card."""

    id: str
    target: str  # a concern name, "All evals" or "Question"
    kind: str  # the kind of change, or the item type
    text: str  # the hypothesis, the question or the item's job
    steps: tuple[Stage, ...]
    line4: Strip | Progress | Note | None = None
    line5: SoFar | Verdict | None = None
    spend: str = ""
    live: bool = False

    @property
    def targets(self) -> tuple[str, ...]:
        if self.target == "All evals":
            return EVAL_CONCERNS
        return (self.target,)


# The judged-item strips, in judging order.
STRIP_W4_1 = "FFSFSFFSHHHHHHHHHHHHHHHHHHHHHHHHHHHH"
STRIP_W4_2 = "FFFFFFSSHHHBHHFHHHHSHHHHHHHHHHHHHHHH"
STRIP_W4_3 = "FSSFSSSSHHBHHHHHHHHHHHHH"
STRIP_W4_6 = "FSSSSSSSHHBH"
STRIP_W4_7 = "FSBSBBSHBHHH"
STRIP_W4_4 = "FFFFFFB" + "S" * 10 + "H" * 103

HYP = {
    "w4-1": "Return only the 6 hold fields from get_container, so the model "
    "stops missing hold_status",
    "w4-2": "Treat a 2xx response with a refusal in the body as a refusal",
    "w4-3": "Move the escalation table into a skill the prompt names by name",
    "w4-4": "Combine w3-1 and w3-3 on top of w3-4",
    "w4-5": "Does haiku-5-5 load an attached skill under JSON output mode?",
    "w4-6": "Lower thinking keeps quality and cuts cost",
    "w4-7": "Add a tone guide skill with three examples of a firm, polite refusal",
}


def _w41_measuring() -> Item:
    return Item(
        "w4-1",
        "Hold placement",
        "wrapper",
        HYP["w4-1"],
        stages(*FIX, current="measure"),
        Strip(STRIP_W4_1[:14], 36),
        SoFar(5, 0),
        "$1.20 of $4",
        live=True,
    )


W41_SUCCESS = Item(
    "w4-1",
    "Hold placement",
    "wrapper",
    HYP["w4-1"],
    stages(*FIX),
    Strip(STRIP_W4_1, 36),
    Verdict("success", "Success"),
    "$2.30 of $4",
)
W42_ANALYZING = Item(
    "w4-2",
    "Refusal honesty",
    "prompt",
    HYP["w4-2"],
    stages(*FIX, current="analyze"),
    Strip(STRIP_W4_2, 36),
    SoFar(7, 1, so_far=False),
    "$2.10 of $4",
    live=True,
)
W42_TO_GATE = Item(
    "w4-2",
    "Refusal honesty",
    "prompt",
    HYP["w4-2"],
    stages(*FIX),
    Strip(STRIP_W4_2, 36),
    Verdict("success", "Success · goes to the gate"),
    "$2.40 of $4",
)
W42_AT_GATE = Item(
    "w4-2",
    "Refusal honesty",
    "prompt",
    HYP["w4-2"],
    stages(*GATED, current="gate"),
    Progress(312, 520, "312 of 520 val items"),
    Verdict("success", "Success · goes to the gate"),
    "$3.80 of $4",
    live=True,
)
W42_PROMOTED = Item(
    "w4-2",
    "Refusal honesty",
    "prompt",
    HYP["w4-2"],
    stages(*GATED),
    Strip(STRIP_W4_2, 36),
    Verdict("promoted", "Promoted · now Best Confirmed"),
    "$3.80 of $4",
)
W43_MEASURING = Item(
    "w4-3",
    "Escalation",
    "skill",
    HYP["w4-3"],
    stages(*FIX, current="measure"),
    Strip(STRIP_W4_3[:12], 24),
    SoFar(2, 1),
    "$1.10 of $4",
    live=True,
)
W43_REBUILDING = Item(
    "w4-3",
    "Escalation",
    "skill",
    HYP["w4-3"],
    stages(*FIX, current="build"),
    Note("! rebuilding once · copied text found · 5m02s", "warning"),
    None,
    "$0.90 of $4",
    live=True,
)
W43_NO_WIN = Item(
    "w4-3",
    "Escalation",
    "skill",
    HYP["w4-3"],
    stages(*FIX),
    Strip(STRIP_W4_3, 24),
    Verdict("nowin", "No win"),
    "$1.60 of $4",
)
W44_MEASURING = Item(
    "w4-4",
    "All evals",
    "merge",
    HYP["w4-4"],
    stages(*FIX, current="measure"),
    Strip(STRIP_W4_4[:52], 120),
    SoFar(6, 1),
    "$2.60 of $6",
    live=True,
)
W44_ANALYZING = Item(
    "w4-4",
    "All evals",
    "merge",
    HYP["w4-4"],
    stages(*FIX, current="analyze"),
    Strip(STRIP_W4_4, 120),
    SoFar(6, 1, so_far=False),
    "$3.10 of $6",
    live=True,
)
W44_AT_GATE = Item(
    "w4-4",
    "All evals",
    "merge",
    HYP["w4-4"],
    stages(*GATED, current="gate"),
    Progress(288, 520, "288 of 520 val items"),
    Verdict("success", "Success · goes to the gate"),
    "$4.40 of $6",
    live=True,
)
W44_REJECTED = Item(
    "w4-4",
    "All evals",
    "merge",
    HYP["w4-4"],
    stages(*GATED, stopped="gate"),
    Strip(STRIP_W4_4, 120),
    Verdict("rejected", "Rejected at val"),
    "$4.40 of $6",
)
W45_ASKING = Item(
    "w4-5",
    "Question",
    "probe",
    HYP["w4-5"],
    stages(*ASKING, current="answering"),
    Note("3 drives"),
    None,
    "$0.10",
    live=True,
)
W45_YES = Item(
    "w4-5",
    "Question",
    "probe",
    HYP["w4-5"],
    stages(*PROBE),
    Note("3 drives"),
    Verdict("success", "YES, 3 of 3 drives"),
    "$0.20",
)
W46_STOPPED = Item(
    "w4-6",
    "Cost / request",
    "thinking",
    HYP["w4-6"],
    stages(*FIX, stopped="measure"),
    Strip(STRIP_W4_6, 36),
    Verdict("stopped", "Stopped: can't beat its parent"),
    "$0.70 of $4",
)
W46_QUEUED = Item(
    "w4-6",
    "Cost / request",
    "thinking",
    HYP["w4-6"],
    stages(*FIX, queued=True),
    Note("waiting for an agent slot"),
)
W47_BUILDING = Item(
    "w4-7",
    "Tone and policy",
    "skill",
    HYP["w4-7"],
    stages(*FIX, current="build"),
    Note("an agent is writing the change · 3m10s"),
    None,
    "$0.40 of $4",
    live=True,
)
W47_STOPPED = Item(
    "w4-7",
    "Tone and policy",
    "skill",
    HYP["w4-7"],
    stages(*FIX, stopped="measure"),
    Strip(STRIP_W4_7, 36),
    Verdict("stopped", "Stopped: broke too many passing items"),
    "$1.20 of $4",
)

RECON = (
    Item(
        "w0-1",
        "Question",
        "probe",
        "Does the agent call get_container before it places a hold?",
        stages(*PROBE),
        Note("3 drives"),
        Verdict("success", "YES, 3 of 3 drives"),
        "$0.10",
    ),
    Item(
        "w0-2",
        "Question",
        "probe",
        "Does the agent treat a 2xx response with an error body as a success?",
        stages(*PROBE),
        Note("3 drives"),
        Verdict("success", "YES, 2 of 3 drives"),
        "$0.10",
    ),
    Item(
        "w0-3",
        "Question",
        "probe",
        "Does haiku-5-5 follow a long escalation table without a skill?",
        stages(*ASKING, current="answering"),
        Note("3 drives"),
        None,
        "$0.05",
        live=True,
    ),
)

# The card gallery: every variant of ui_design.md section 7.5.
GALLERY: tuple[tuple[str, Item], ...] = (
    ("Fix · building", W47_BUILDING),
    ("Fix · rebuilding once", W43_REBUILDING),
    ("Fix · measuring", _w41_measuring()),
    ("Fix · analyzing", W42_ANALYZING),
    ("Fix · landed, success", W42_TO_GATE),
    ("Fix · landed, no win", W43_NO_WIN),
    ("Fix · stopped", W46_STOPPED),
    ("Fix · queued", W46_QUEUED),
    ("At the gate", W42_AT_GATE),
    ("At the gate · promoted", W42_PROMOTED),
    ("At the gate · rejected", W44_REJECTED),
    ("Merge · measuring (strip too long: gauge)", W44_MEASURING),
    ("Probe · asking", W45_ASKING),
    ("Probe · answered", W45_YES),
    (
        "Eval build · building",
        Item(
            "w5-3",
            "Escalation",
            "eval build",
            "Build an eval for late escalations on hazmat holds",
            stages("build", current="build"),
            None,
            None,
            "$0.60 of $3",
            live=True,
        ),
    ),
    (
        "Eval build · landed",
        Item(
            "w5-3",
            "Escalation",
            "eval build",
            "Build an eval for late escalations on hazmat holds",
            stages("build"),
            None,
            Verdict("done", "Eval built"),
            "$1.40 of $3",
        ),
    ),
    (
        "Issue baseline measurement · measuring",
        Item(
            "w5-4",
            "Hold placement",
            "measure",
            "Measure holds placed on the wrong bay on the baseline",
            stages("measure", current="measure"),
            Progress(24, 40, "24 of 40 items"),
            None,
            "$0.30 of $2",
            live=True,
        ),
    ),
    (
        "Issue baseline measurement · landed",
        Item(
            "w5-4",
            "Hold placement",
            "measure",
            "Measure holds placed on the wrong bay on the baseline",
            stages("measure"),
            Progress(40, 40, "40 of 40 items"),
            Verdict("nowin", "Not reproduced"),
            "$0.50 of $2",
        ),
    ),
    (
        "Re-measure · measuring",
        Item(
            "w5-5",
            "Refusal honesty",
            "re-measure",
            "Complete the measurement of w3-2",
            stages("measure", current="measure"),
            Progress(48, 72, "48 of 72 items"),
            None,
            "$0.70 of $2",
            live=True,
        ),
    ),
    (
        "Re-measure · landed",
        Item(
            "w5-5",
            "Refusal honesty",
            "re-measure",
            "Complete the measurement of w3-2",
            stages("measure"),
            Progress(72, 72, "72 of 72 items"),
            Verdict("done", "Measured"),
            "$1.10 of $2",
        ),
    ),
)

# ---------------------------------------------------------------------------
# The timeline: (start, end, kind, wave) in minutes since the start
# ---------------------------------------------------------------------------

TIMELINE = (
    (0, 5, "setup", 0),
    (5, 10, "recon", 0),
    (10, 12, "plan", 1),
    (12, 29, "items", 1),
    (29, 31, "gate", 1),
    (31, 33, "plan", 2),
    (33, 42, "items", 2),
    (42, 44, "promo", 2),
    (44, 47, "plan", 3),
    (47, 53, "screen", 3),
    (53, 62, "items", 3),
    (62, 64, "promo", 3),
    (64, 67, "plan", 4),
    (67, 85, "items", 4),
    (85, 89, "promo", 4),
    (89, 92, "plan", 5),
    (92, 94, "screen", 5),
    (94, 100, "plan", 5),
    (100, 120, "items", 5),
    (120, 123, "gate", 5),
    (123, 126, "plan", 6),
    (126, 144, "items", 6),
    (144, 147, "promo", 6),
    (147, 150, "plan", 7),
    (150, 170, "items", 7),
    (170, 172, "promo", 7),
    (172, 178, "report", 8),
)

# ---------------------------------------------------------------------------
# The log: (time, wave, kind, text)
# ---------------------------------------------------------------------------

LOG = (
    ("14:12", 0, "run", "Run started · quality · $50 · 3h"),
    ("14:13", 0, "run", "Building on 41 records from 2 earlier runs"),
    ("14:17", 0, "warn", "Output format can only guard: 4 failures, a win needs 6"),
    ("14:21", 0, "yes", "w0-1 get_container before a hold? YES, 3 of 3"),
    ("14:22", 1, "wave", "Wave 1 from start · 3 experiments"),
    (
        "14:31",
        1,
        "stop",
        "w1-1 thinking high: stopped, quality clearly worse than its parent",
    ),
    ("14:35", 1, "nowin", "w1-2 Refusal honesty: no win, 2 fixed, 1 broke"),
    ("14:38", 1, "success", "w1-3 Hold placement: success, goes to the gate"),
    ("14:41", 1, "reject", "w1-3 rejected at val: train +5.1, val +0.4"),
    ("14:43", 2, "wave", "Wave 2 from start · 3 experiments"),
    ("14:50", 2, "success", "w2-1 Refusal honesty: success, goes to the gate"),
    ("14:52", 2, "stop", "w2-3 few-shot: stopped, fixed too few of its targets"),
    ("14:56", 2, "promo", "w2-1 promoted · Refusal honesty +7.7 on val"),
    ("14:59", 3, "wave", "Wave 3 from w2-1 · screening first"),
    ("15:05", 3, "run", "Screen: 2 of 8 models survive"),
    ("15:12", 3, "success", "w3-4 Hold placement: success, goes to the gate"),
    ("15:13", 3, "lesson", "Dead end: few-shot examples for Hold placement"),
    ("15:16", 3, "promo", "w3-4 promoted · Hold placement +6.1 on val"),
    ("15:16", 3, "reject", "w3-2 rejected at val: train +6.0, val +1.0"),
    ("15:19", 4, "wave", "Wave 4 from w3-4 · 6 experiments, 1 probe"),
    ("15:20", 4, "assumed", "Assumed: JSON output mode stays on"),
    ("15:24", 4, "built", "w4-1 adds get_container_for_hold"),
    ("15:25", 4, "warn", "w4-4: copied judge text, rebuilding once"),
    ("15:27", 4, "yes", "w4-5 haiku-5-5 loads skills in JSON mode: YES, 3 of 3"),
    ("15:28", 4, "built", "w4-3 moves the escalation table into a skill"),
    ("15:29", 4, "stop", "w4-6 thinking low: stopped, can't beat its parent"),
    ("15:30", 4, "warn", "openai rate limit · 6 eval items retried"),
    ("15:31", 4, "success", "w4-2 Refusal honesty: success, goes to the gate"),
    ("15:33", 4, "success", "w4-1 Hold placement: success, 5 fixed, 0 broke"),
    ("15:34", 4, "lesson", "Lesson: hold_status is lost in long tool output"),
    ("15:37", 4, "nowin", "w4-3 Escalation: no win, 2 fixed, 1 broke"),
    ("15:37", 4, "success", "w4-4 merge: success, goes to the gate"),
    (
        "15:38",
        4,
        "stop",
        "w4-7 Tone and policy: stopped, broke too many items that passed",
    ),
    ("15:38", 4, "gate", "Promotion gate: 2 candidates on val"),
    ("15:41", 4, "promo", "w4-2 promoted · Refusal honesty +7.8 on val"),
    ("15:41", 4, "reject", "w4-4 rejected at val: w4-2 won the gate"),
    ("15:46", 5, "run", "Screen round 1: 3 of 6 candidates survive"),
    ("15:52", 5, "wave", "Wave 5 from w4-2 · 5 experiments"),
    (
        "16:04",
        5,
        "stop",
        "w5-1 model swap: stopped, quality clearly worse than its parent",
    ),
    ("16:12", 5, "gate", "Promotion gate: no winner"),
    ("16:15", 6, "wave", "Wave 6 from w4-2 · 4 experiments"),
    ("16:31", 6, "success", "w6-1 Escalation: success, goes to the gate"),
    ("16:39", 6, "promo", "w6-1 promoted · Escalation +6.0 on val"),
    ("16:39", 7, "wave", "Wave 7 from w6-1 · 4 experiments · last wave"),
    ("16:58", 7, "success", "w7-2 merge: success, goes to the gate"),
    ("17:04", 7, "promo", "w7-2 promoted · Hold placement +5.6 on val"),
    ("17:05", 7, "run", "No time for another wave: writing the report"),
    ("17:10", 7, "run", "Report written"),
)

# ---------------------------------------------------------------------------
# The run screen's modes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepLine:
    """A step line: a glyph, a 9-character label and the text."""

    status: str  # ok, fail, warn or busy
    label: str
    text: str


@dataclass(frozen=True)
class RunState:
    mode: str  # starting, planning, running, gate or done
    clock: str
    now_min: int
    elapsed: str
    spend: float
    kiln: float
    agent: float
    burn: float | None
    wave: int | None
    start_split: str
    confirmed: tuple[float, ...] | None = None
    confirmed_rc: str = ""
    confirmed_name: str = ""
    unconfirmed: dict[int, tuple[float, str]] = field(default_factory=dict)
    items: tuple[Item, ...] = ()
    steps: tuple[StepLine, ...] = ()
    projected: tuple[tuple[int, int], ...] = ()  # (start minute, wave)
    mode_elapsed: str = ""

    @property
    def start(self) -> tuple[float, ...]:
        return START_TRAIN if self.start_split == "train" else START_VAL


W3_4 = (79.8, 70.1, 93.5, 4.12, 56.0, 0.039, 7.6)
W4_2 = (87.6, 70.4, 93.4, 4.11, 56.2, 0.040, 7.9)
W7_2 = (88.2, 76.0, 95.0, 4.20, 63.1, 0.033, 7.0)
PROJECTED_W4 = ((89, 5), (116, 6), (143, 7))

RUN_STATES = {
    "starting": RunState(
        mode="starting",
        clock="14:21",
        now_min=9,
        elapsed="9m",
        spend=0.60,
        kiln=0.20,
        agent=0.40,
        burn=None,
        wave=None,
        start_split="train",
        items=RECON,
        mode_elapsed="9m",
        steps=(
            StepLine("ok", "Agent CLI", "claude -p answered the test prompt · 38s"),
            StepLine("ok", "Preflight", "the agents can reach Kiln"),
            StepLine("ok", "Setup", "5 evals and 8 issues synced"),
            StepLine("ok", "Baseline", "rc 0003 · train scores read from Kiln"),
            StepLine("ok", "Models", "8 models the policy allows answered"),
            StepLine(
                "warn",
                "Detection",
                "Output format can only guard: 4 failures, a win needs 6",
            ),
            StepLine("ok", "Noise", "skipped: no noise check"),
            StepLine("busy", "Recon", "2 of 3 probes answered · 4m10s"),
        ),
    ),
    "running": RunState(
        mode="running",
        clock="15:36",
        now_min=84,
        elapsed="1h24m",
        spend=21.40,
        kiln=14.40,
        agent=7.00,
        burn=15.30,
        wave=4,
        start_split="val",
        confirmed=W3_4,
        confirmed_rc="w3-4",
        confirmed_name="get_container wrapper",
        unconfirmed={
            0: (89.0, "w4-2"),
            1: (75.3, "w4-1"),
            5: (0.033, "w4-1"),
            6: (6.5, "w4-1"),
        },
        items=(
            W41_SUCCESS,
            W42_TO_GATE,
            W43_MEASURING,
            W44_ANALYZING,
            W45_YES,
            W46_STOPPED,
            W47_BUILDING,
        ),
        projected=PROJECTED_W4,
        mode_elapsed="17m",
    ),
    "gate": RunState(
        mode="gate",
        clock="15:39",
        now_min=87,
        elapsed="1h27m",
        spend=24.10,
        kiln=16.20,
        agent=7.90,
        burn=16.60,
        wave=4,
        start_split="val",
        confirmed=W3_4,
        confirmed_rc="w3-4",
        confirmed_name="get_container wrapper",
        unconfirmed={
            0: (89.0, "w4-2"),
            1: (77.9, "w4-4"),
            4: (58.1, "w4-4"),
            5: (0.033, "w4-1"),
            6: (6.5, "w4-1"),
        },
        items=(
            W41_SUCCESS,
            W42_AT_GATE,
            W43_NO_WIN,
            W44_AT_GATE,
            W45_YES,
            W46_STOPPED,
            W47_STOPPED,
        ),
        projected=PROJECTED_W4,
        mode_elapsed="2m",
    ),
    "planning": RunState(
        mode="planning",
        clock="15:47",
        now_min=95,
        elapsed="1h35m",
        spend=26.30,
        kiln=17.90,
        agent=8.40,
        burn=16.60,
        wave=5,
        start_split="val",
        confirmed=W4_2,
        confirmed_rc="w4-2",
        confirmed_name="refusal rule in prompt",
        items=(
            W41_SUCCESS,
            W42_PROMOTED,
            W43_NO_WIN,
            W44_REJECTED,
            W45_YES,
            W46_STOPPED,
            W47_STOPPED,
        ),
        steps=(
            StepLine("ok", "Planner", "2m40s · asked for a screening round first"),
            StepLine("ok", "Screen 1", "6 candidates · 16 items each · 3 survive"),
            StepLine("busy", "Planner", "again, with the screen results · 1m12s"),
        ),
        projected=((116, 6), (143, 7)),
        mode_elapsed="6m",
    ),
    "done": RunState(
        mode="done",
        clock="17:10",
        now_min=178,
        elapsed="2h58m",
        spend=46.20,
        kiln=31.80,
        agent=14.40,
        burn=None,
        wave=7,
        start_split="val",
        confirmed=W7_2,
        confirmed_rc="w7-2",
        confirmed_name="wrapper + refusal rule + escalation skill",
        mode_elapsed="2h58m",
    ),
}

RUN_COMPLETE = {
    "ended": "Time limit reached",
    "totals": (
        ("7", "waves"),
        ("38", "experiments"),
        ("5", "promotions"),
        ("12", "stopped early"),
    ),
}

# ---------------------------------------------------------------------------
# The start-up flow (strings from src/kiln_harness_tuner/onboarding/)
# ---------------------------------------------------------------------------

WELCOME_TITLE = "Kiln Harness Tuner"
WELCOME_DESCRIPTION = (
    "Automatically improves an AI agent by tuning its harness: the prompt, "
    "model, tools, skills and settings around it."
)
PICK_HINT = "↑↓ move · type to filter · Enter pick"
GUIDANCE = "Improve refusals and hold placement. Keep the output format."
EVAL_LINK = f"{KILN_HOST}/specs/{PROJECT_ID}/{TASK_ID}/424242550012/424242550013"
UNLOADABLE_EVAL = (  # readiness.py and setup.py: an eval Kiln can't load
    'Kiln can\'t load eval "Tone (old judge)": its judge model is retired.\n'
    "  The run leaves it out. To use it, fix its judge in Kiln, or update Kiln if\n"
    f"  the eval was made with a newer version: {EVAL_LINK}"
)

HISTORY = (
    StepLine("ok", "Kiln", f"0.24.1 at {KILN_HOST}"),
    StepLine("ok", "Project", f"{PROJECT} ({PROJECT_ID})"),
    StepLine("ok", "Task", f"{TASK} ({TASK_ID}) · the only task in this project"),
    StepLine(
        "ok", "Ready", "3 run configs · 5 evals the tuner can measure (1 left out)"
    ),
    StepLine("warn", "", UNLOADABLE_EVAL),
    StepLine("ok", "API keys", "Anthropic, OpenAI"),
    StepLine("ok", "Improve", "Quality"),
    StepLine("ok", "Guidance", f'"{GUIDANCE}"'),
    StepLine(
        "ok", "Agents", "Claude Code (claude -p) · opus · the only agent CLI installed"
    ),
    StepLine("ok", "Budget", "$50.00"),
    StepLine("ok", "Time", "3 hours"),
    StepLine("ok", "Advanced", "defaults"),
)


@dataclass(frozen=True)
class PickRow:
    label: str
    columns: tuple[str, ...] = ()
    description: str | None = None
    disabled: str | None = None


PROJECT_PICK = (
    PickRow(
        PROJECT,
        (PROJECT_ID, "~/Kiln Projects/Depot Ops"),
        "The freight depot's desk agent: holds, refusals and escalations.",
    ),
    PickRow(
        "Forum Moderation",
        ("424242975961", "~/Kiln Projects/Forum Moderation"),
        "The demo: moderates posts on an invented gardening forum.",
    ),
    PickRow(
        "Invoice Reader",
        ("424242110014", "~/Kiln Projects/Invoice Reader"),
        "Reads supplier invoices into the ledger's JSON format.",
    ),
    PickRow("Returns Desk", ("424242110031", ""), disabled="Kiln has no folder"),
    PickRow(
        "Support Triage",
        ("424242110027", "~/Work/support-triage"),
        "Sorts support tickets by product and urgency.",
    ),
)


@dataclass(frozen=True)
class Problem:
    """A failed check: the ✗ title, the body lines, the warnings and the
    actions of its `What next?` menu."""

    title: str
    body: tuple[str, ...]
    warnings: tuple[str, ...]
    actions: tuple[tuple[str, str], ...]


PROBLEM = Problem(
    title=f"{TASK} isn't ready to tune yet",
    body=(
        "The tuner starts from one of the task's run configs and measures every change",
        "with its evals. This task is missing:",
        "  • A run config: the model and prompt the tuner starts from.",
        '  • A default judge on its active evals: "Escalation" and "Hold'
        ' placement" have none set.',
    ),
    warnings=(UNLOADABLE_EVAL,),
    actions=(
        ("r", "Check again (I've fixed it in Kiln)"),
        ("c", "Open Run configs in Kiln"),
        ("e", "Open Evals in Kiln"),
        ("x", "Exit"),
    ),
)


@dataclass(frozen=True)
class TextQuestion:
    question: str
    typed: str  # what the person typed over the default
    hint: str
    error: str  # the validator's message for `typed`


MODEL_QUESTION = TextQuestion(
    question="Model for the agents",
    typed="claude opus",
    hint="Enter keeps opus. sonnet or haiku makes the run cheaper, with weaker plans.",
    error="Enter one model name, with no spaces",
)

SUMMARY_LINES = (
    ("Task", f"{TASK} ({TASK_ID}) in project {PROJECT} ({PROJECT_ID})"),
    ("Starts from", 'run config "rc 0003": claude-haiku-5-5 on Anthropic'),
    ("Improve", "Quality"),
    ("Guidance", f'"{GUIDANCE}"'),
    ("Agents", "Claude Code (claude -p) · opus"),
    ("Limits", "$50.00 · 3 hours"),
    ("Advanced", "defaults"),
)
RERUN_HEADING = "To run this again with the same answers and no questions:"
RERUN_COMMAND = (
    "  harness-tuner \\",
    f"    --project-id {PROJECT_ID} --task-id {TASK_ID} \\",
    "    --project-path '/home/sam/Kiln Projects/Depot Ops' \\",
    "    --objective quality \\",
    f"    --guidance '{GUIDANCE}' \\",
    "    --agent-cli 'claude -p' \\",
    "    --budget-usd 50 --timeout-mins 180 \\",
    "    --no-interaction",
)
START_ACTIONS = (("s", "Start the run"), ("e", "Change an answer"), ("x", "Exit"))
