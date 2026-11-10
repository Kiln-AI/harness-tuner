"""Every user-facing string of the run screen, and the formats of its
numbers. The wording tables are functional spec sections 7.2, 7.6 and 16,
with the card short forms of ui_design section 7.5. No stop-rule names and
no internal ids where a name exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from kiln_harness_tuner.tui.model.state import (
    SPIN,
    ConcernKind,
    GoalRow,
    Line,
    Span,
    Tone,
)

MINUS = "−"

# ---- numbers ----


def duration(secs: float) -> str:
    """ "38s", "3m10s", "1h24m"."""
    secs = max(int(secs), 0)
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs}s"


def span_of_minutes(minutes: int) -> str:
    """A time limit or a tick on the axis: "0", "30m", "1h", "1h30"."""
    if minutes <= 0:
        return "0"
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest}m"
    return f"{hours}h" if not rest else f"{hours}h{rest:02d}"


def money(usd: float) -> str:
    return f"${usd:.2f}"


def clock(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def value_text(kind: ConcernKind, value: float) -> str:
    """ "70.2%", "4.08", "$0.042", "8.4s"."""
    if kind == "pass":
        return f"{value * 100:.1f}%"
    if kind == "rating":
        return f"{value:.2f}"
    if kind == "cost":
        return f"${value:.3f}" if value >= 0.01 else f"${value:.2g}"
    return f"{value / 1000:.1f}s" if value >= 1000 else f"{value:.0f}ms"


def signed(number: float, digits: int) -> str:
    text = f"{abs(number):.{digits}f}"
    return ("+" if number >= 0 or float(text) == 0 else MINUS) + text


def change_text(kind: ConcernKind, value: float, reference: float) -> str:
    """The change from `reference` to `value`: percentage points for a pass
    rate, the raw change for a rating, the relative change for cost and
    latency."""
    if kind == "pass":
        return signed((value - reference) * 100, 1)
    if kind == "rating":
        return signed(value - reference, 2)
    if reference == 0:
        return ""
    return signed((value / reference - 1) * 100, 0) + "%"


def plural(count: int, word: str, words: str | None = None) -> str:
    return f"{count} {word if count == 1 else (words or word + 's')}"


def listed(names: Sequence[str]) -> str:
    """ "a", "a and b", "a, b and c"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def humane(code: str) -> str:
    """An error class or a technique, as words: "rate_limit" → "rate limit"."""
    return code.replace("_", " ")


# ---- title, goal and clock ----


def title_meta(agent_cli_text: str, agents: int, evals: int) -> str:
    return (
        f"{agent_cli_text} · {plural(agents, 'agent slot')}"
        f" · {plural(evals, 'eval slot')}"
    )


def goal_rows(objective: str, evals: int | None) -> tuple[GoalRow, ...]:
    """What the run optimizes, holds and watches (functional spec 7.2, with
    16.2: the evals are not weighted under quality)."""
    if objective == "speed":
        return (
            GoalRow("optimize", "Latency", "per request"),
            GoalRow("hold", "Quality", "equal or better on val"),
            GoalRow("watch", "Cost", "shown, never decides"),
        )
    if objective == "cost":
        return (
            GoalRow("optimize", "Cost", "per request"),
            GoalRow("hold", "Quality", "equal or better on val"),
            GoalRow("watch", "Latency", "shown, never decides"),
        )
    return (
        GoalRow("optimize", "Quality", plural(evals, "eval") if evals else "the evals"),
        GoalRow("hold", "Every eval", "none may drop on val"),
        GoalRow("watch", "Cost, latency", "shown, never decide"),
    )


TEST_NEVER_READ = "test never read"
UNKNOWN = "–"
TIME_LABEL = "Time"
SPEND_LABEL = "Spend"
NO_BUDGET = "no budget"
BEFORE_WAVE_1 = "before wave 1"


def times_text(started: datetime | None, ends: datetime | None) -> str:
    parts = []
    if started is not None:
        parts.append(f"started {clock(started)}")
    if ends is not None:
        parts.append(f"ends {clock(ends)}")
    return " · ".join(parts)


def tokens(count: int) -> str:
    """`~1.2M tokens`: a rough count."""
    if count >= 1_000_000:
        text = f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        text = f"{count / 1_000:.0f}k"
    else:
        text = str(count)
    return f"~{text} tokens"


def burn(usd_per_hour: float) -> str:
    return f"{money(usd_per_hour)}/h"


# ---- results ----

RESULTS_START = "Start"
RESULTS_CONFIRMED = "Best Confirmed"
RESULTS_UNCONFIRMED = "Best Unconfirmed"
STARTING_POINT = "starting point"
NO_PROMOTION_YET = "no promotion yet"
TRAIN_ESTIMATE = "train estimate"
NOT_MEASURED_YET = "not measured yet"
SWATCH = "■"
RATING_SCALE = "1–5"
MEDIAN = "median"


def in_flight(count: int) -> str:
    return f"{count} in flight"


# ---- experiments ----

MODE_WORDS = {
    "starting": "Starting",
    "planning": "Planning",
    "running": "Running",
    "gate": "Promotion gate",
    "done": "Done",
}
EVERY_EVAL_ON_VAL = "every eval on val"
FROM_START = "start"
# The Experiments header's counts, in this order; the live ones are blue.
CARD_STATES = (
    ("queued", False),
    ("building", True),
    ("measuring", True),
    ("asking", True),
    ("analyzing", True),
    ("at the gate", True),
    ("landed", False),
)
QUEUED = "waiting for an agent slot"
WRITING = "an agent is writing the change"
REVIEWING = "an agent is reviewing the change"
BUILDING_EVAL = "an agent is building an eval"
MEASURING = "measuring"
REBUILDING = "rebuilding once"
SO_FAR = "so far"
NEW_EVAL = "New eval"
ALL_EVALS = "All evals"
QUESTION = "Question"
RECON_NOTE = "Wave 1 is planned when the recon probes finish."
FIX_STAGES = ("build", "measure", "analyze")
GATE_STAGE = "gate"
PROBE_STAGES = ("asking", "answered")
KIND_WORDS = {
    "merge": "merge",
    "probe": "probe",
    "eval": "eval build",
    "measure": "measure",
    "remeasure": "re-measure",
}
# Why a build is made again (the check whose finding forced it).
FINDINGS = {
    "judge_copy": "copied text found",
    "generality": "train item text found",
    "reference_check": "names what it does not attach",
}
REBUILD_UNKNOWN = "a check found a problem"
FINDING_LOG = {
    "judge_copy": "copied judge text",
    "generality": "train item text leaked into the change",
    "reference_check": "names a skill or tool it does not attach",
}


def wave_label(wave: int, probe_phase: bool) -> str:
    return f"wave {wave} probes" if probe_phase else f"wave {wave}"


def subhead(wave: int, probe_phase: bool) -> str:
    if wave == 0:
        return "WAVE 0 · recon probes"
    return f"WAVE {wave} · {'probes' if probe_phase else 'landed'}"


def merge_text(ingredients: Sequence[str], parent: str) -> str:
    return f"Combine {listed(ingredients)} on top of {parent}"


def eval_build_text(issue: str) -> str:
    return f"Build an eval for {issue}"


def measure_text(issue: str) -> str:
    return f"Measure {issue} on the baseline"


def remeasure_text(rc: str) -> str:
    return f"Complete the measurement of {rc}"


def val_progress(done: int, total: int) -> str:
    return f"{done} of {total} val items"


def drives(count: int) -> str:
    return plural(count, "drive")


def fixed_broke(fixed: int, broke: int) -> tuple[str, str]:
    return f"{fixed} fixed", f"{broke} broke"


def stopping_detail(live: int) -> str:
    return f"cancelling {plural(live, 'experiment')}"


# The stop dialog (functional spec section 9, ui_design section 9).
STOP_TITLE = "Stop the run?"
KEEP_RUNNING = "Keep running"
STOP_THE_RUN = "Stop the run"


def stop_question(live: int, planning_wave: int | None) -> str:
    """The stop dialog's message (functional spec section 9)."""
    if not live and planning_wave is not None:
        return (
            f"The planner is at work on wave {planning_wave}. Its work so far is"
            " lost, and no report is written."
        )
    if not live:
        # The run's own start, or a gate: no experiment to name.
        return "The run's work so far is lost, and no report is written."
    are = "is" if live == 1 else "are"
    return (
        f"{plural(live, 'experiment')} {are} in flight. Their work so far is"
        " lost, and no report is written."
    )


# ---- the Starting checklist and the planning steps ----

AGENT_CLI_STEP = "Agent CLI"
PREFLIGHT_STEP = "Preflight"
SETUP_STEP = "Setup"
BASELINE_STEP = "Baseline"
MODELS_STEP = "Models"
DETECTION_STEP = "Detection"
NOISE_STEP = "Noise"
RECON_STEP = "Recon"
SCREEN_STEP = "Screen"
PLANNER_STEP = "Planner"
PROBES_STEP = "Probes"
# What the Starting header says while a step runs.
STEP_ACTIVITY = {
    AGENT_CLI_STEP: "testing the agent CLI",
    PREFLIGHT_STEP: "preflight",
    SETUP_STEP: "setup",
    BASELINE_STEP: "the baseline",
    MODELS_STEP: "the model preflight",
    DETECTION_STEP: "the detectability check",
    NOISE_STEP: "the noise check",
    RECON_STEP: "wave 0, recon",
    SCREEN_STEP: "the opening screen",
}


def agent_cli_testing(cmd: str) -> str:
    return f"testing {cmd}"


def agent_cli_answered(cmd: str, secs: float) -> str:
    return f"{cmd} answered the test prompt · {duration(secs)}"


PREFLIGHT_BUSY = "checking that the agents can reach Kiln"
PREFLIGHT_OK = "the agents can reach Kiln"
SETUP_BUSY = "syncing the evals and issues"


def setup_text(active: object, total: object, writes: Sequence[tuple[str, int]]) -> str:
    """ "5 of 6 evals active · 2 evals seeded, 1 issue repaired"; `writes`
    are `SETUP_WRITES` keys with their counts."""
    done = []
    for field, count in writes:
        if count:
            noun, verb = SETUP_WRITES[field]
            done.append(f"{plural(count, noun)} {verb}")
    return f"{active} of {total} evals active · " + (
        ", ".join(done) if done else "in sync"
    )


# The `setup_sync` counts: (what is counted, what was done to it).
SETUP_WRITES = {
    "seeded": ("eval", "seeded"),
    "refreshed": ("eval", "refreshed"),
    "repaired": ("issue", "repaired"),
    "unmeasurable": ("issue", "unmeasurable"),
}
BASELINE_MEASURING = "measuring on train"
BASELINE_STORED = "train scores read from Kiln"
BASELINE_MEASURED = "measured on train"


def baseline_failed(failed: int, total: int) -> str:
    return f"{failed} of {plural(total, 'eval')} could not be measured"


def models_text(checked: int, flagged: int, untested: int) -> str:
    if flagged:
        return f"{plural(checked, 'model')} checked · {flagged} flagged"
    text = f"{plural(checked, 'model')} the policy allows answered"
    return text + (f" · {untested} untested" if untested else "")


def skipped(why: str) -> str:
    return f"skipped: {why}"


def detection_ok(floor: int, scores: int) -> str:
    return f"a win needs {floor} items fixed · {plural(scores, 'score')} checked"


def noise_busy(estimate_usd: object) -> str:
    if isinstance(estimate_usd, (int, float)):
        return f"measuring run-to-run noise · about {money(float(estimate_usd))}"
    return "measuring run-to-run noise"


def noise_ok(scores: int, items: int) -> str:
    return f"{plural(scores, 'score')} judged again on {items} items each"


def probes_answered(answered: int, total: int) -> str:
    return f"{answered} of {total} probes answered"


def all_probes_answered(total: int) -> str:
    return f"{plural(total, 'probe')} answered"


def screen_label(round_no: int) -> str:
    """A screening round by the loop's run-wide number; no number until the
    round's event gives it."""
    return f"{SCREEN_STEP} {round_no}" if round_no else SCREEN_STEP


def screen_text(candidates: int, items_each: int | None, survivors: int | None) -> str:
    parts = [plural(candidates, "candidate")]
    if items_each:
        parts.append(f"{items_each} items each")
    if survivors is not None:
        parts.append(f"{survivors} survive")
    return " · ".join(parts)


def planning_wave(wave: int, wrap_up: bool) -> str:
    return f"planning the wrap-up wave {wave}" if wrap_up else f"planning wave {wave}"


PLAN_THEN = {
    "screen": "asked for a screening round first",
    "probes": "asked for probes first",
}
PLAN_AGAIN = {
    "replan": "again, with the probe answers",
    "rescreen": "again, with the screen results",
}


# ---- verdicts ----


@dataclass(frozen=True)
class Verdict:
    """An item's verdict: the card's words, the log's words, and how both
    look. `log` is the functional spec's wording; `card` is the same, or its
    short form where a card line is too narrow (ui_design 7.5)."""

    card: str
    log: str
    tone: Tone
    glyph: str


def _same(text: str, tone: Tone, glyph: str) -> Verdict:
    return Verdict(text, text, tone, glyph)


SUCCESS_GATE = _same("Success · goes to the gate", "success", "●")
SUCCESS = _same("Success", "success", "●")
NO_WIN = _same("No win", "muted", "○")
NOT_REPRODUCED = _same("Not reproduced", "muted", "○")
REPRODUCED = _same("Reproduced", "success", "✓")
EVAL_BUILT = _same("Eval built", "success", "✓")
MEASURED = _same("Measured", "success", "✓")
LEVER_NOT_APPLIED = _same("Stopped: the change did not take effect", "error", "✗")
CAPPED = Verdict(
    "Stopped: spending cap, inconclusive",
    "Stopped: reached its spending cap (inconclusive)",
    "error",
    "✗",
)
ABANDONED = _same("Stopped: abandoned", "error", "✗")
CUT_OFF = _same("Cut off: the run ended", "error", "✗")
PROMOTED = _same("Promoted · now Best Confirmed", "promoted", "◆")
REJECTED = _same("Rejected at val", "error", "✗")
HELD = _same("Held: waiting on an open question", "warning", "?")
COPIED = _same("Blocked: copies eval text", "error", "✗")
# Gate results the spec's table has no row for (audit 1.7): a block by
# code's integrity checks that is not a copy of eval text, a model-policy
# refusal before the gate, and a candidate the gate did not judge (no
# decision, val evidence with holes, or its winner refused at apply).
BLOCKED = _same("Blocked at the gate", "error", "✗")
REFUSED = _same("Blocked before the gate: model policy", "error", "✗")
POOLED = _same("Not judged: stays pooled", "muted", "·")

_STOPS = {
    "futility": _same("Stopped: can't beat its parent", "error", "✗"),
    "eval_metric": Verdict(
        "Stopped: quality clearly worse",
        "Stopped: quality clearly worse than its parent",
        "error",
        "✗",
    ),
    "residual": _same("Stopped: fixed too few of its targets", "error", "✗"),
    "targets": _same("Stopped: a named target still fails", "error", "✗"),
    "latency_ratio": Verdict(
        "Stopped: clearly slower",
        "Stopped: clearly slower than its parent",
        "error",
        "✗",
    ),
    "cost_ratio": Verdict(
        "Stopped: clearly costlier",
        "Stopped: clearly costlier than its parent",
        "error",
        "✗",
    ),
    "guard_losses": Verdict(
        "Stopped: broke too many passing items",
        "Stopped: broke too many items that passed",
        "error",
        "✗",
    ),
    # Stop kinds the spec's table has no row for (audit 1.7).
    "invalid_output_rate": _same("Stopped: too many invalid outputs", "error", "✗"),
    "max_items": _same("Stopped: reached its item limit", "error", "✗"),
}
STOPPED_EARLY = _same("Stopped early", "error", "✗")


def stop_verdict(stop_kind: str | None, objective: str) -> Verdict:
    if stop_kind == "not_better":
        better = "cheaper" if objective == "cost" else "faster"
        return _same(f"Stopped: not clearly {better}", "error", "✗")
    return _STOPS.get(stop_kind or "", STOPPED_EARLY)


def dead_end(reason: str | None) -> Verdict:
    return _same(f"Dead end: {reason}" if reason else "Dead end", "error", "✗")


def failed(error_class: str | None) -> Verdict:
    return _same(f"Failed: {humane(error_class or 'unknown error')}", "error", "✗")


def probe_answer(answer: str, drive_count: int) -> Verdict:
    text = f"{answer.upper()} · {drives(drive_count)}"
    if answer == "yes":
        return _same(text, "success", "●")
    return _same(text, "muted", "○")


def log_verdict(text: str) -> str:
    """A verdict in the log's sentence form: "Stopped: can't beat its
    parent" → "stopped, can't beat its parent"."""
    text = text.replace(": ", ", ", 1).replace(" · ", ", ")
    return text[:1].lower() + text[1:]


# ---- how the run ended ----

_ENDINGS = {
    "timeout": "Time limit reached",
    "budget_exhausted": "Budget spent",
    "wrap_up": "Ended early: not enough time or budget for another wave",
    "empty_plan": "Nothing left to try",
    "planner_failed": "The planner failed",
    "tripwire": "Stopped by the tripwire: too many items failed",
    "quota_exhausted": "Stopped on a provider error: quota or authentication",
    "interrupted": "Stopped by you",
}


def how_it_ended(reason: str | None) -> str:
    if reason is None:
        return "The run ended"
    if reason in _ENDINGS:
        return _ENDINGS[reason]
    if reason.startswith("fatal:"):
        return f"Failed: {reason.removeprefix('fatal:')}"
    # Any other reason is the planner's own `loop_exit` text.
    return f"Nothing left to try: {reason}"


DONE_BEST = "Best Confirmed"
NO_REPORT = "no report"
WRITING_REPORT = "writing the report…"
MEASURED_ON_VAL = "measured on val"
KEPT_START = "no promotion: the run kept its starting point"


def next_step(rc: str) -> str:
    return f"Next: run your test split on {rc}. The tuner never read it."


def run_label(run_id: str | None) -> str:
    return f"run {run_id}" if run_id else ""


# ---- the log ----

# (glyph, tone) per kind of log line (ui_design section 5).
LOG_GLYPHS: dict[str, tuple[str, Tone]] = {
    "run": ("·", "muted"),
    "wave": ("▸", "fg"),
    "gate": ("·", "muted"),
    "success": ("●", "success"),
    "nowin": ("○", "muted"),
    "stop": ("✗", "error"),
    "promo": ("◆", "success"),
    "warn": ("!", "warning"),
    "assumed": ("?", "warning"),
    "lesson": ("≈", "muted"),
    "built": ("+", "muted"),
    # A link the app could not open: its address, to open by hand.
    "link": ("↗", "primary"),
}
START_DIVIDER = "start"
UI_ERROR = "The display skipped an event it could not read; details in the run log"
MEMORY_KINDS = {"lesson": "Lesson", "dead_end": "Dead end", "issue": "New issue"}


# ---- the labels the widgets draw around the views ----

TITLE_MARK = "KILN HARNESS TUNER"
GOAL_ROLES = {"optimize": "▲ optimize", "hold": "═ hold", "watch": "○ watch"}
GOAL_DATA_LABEL = "data"
LAST_WAVE_TAG = "LAST WAVE"
STOPPING = "Stopping…"
RUN_COMPLETE = "RUN COMPLETE"
DONE_REPORT = "Report"

# The section headers (ui_design 4.2).
GOAL = "GOAL"
TIME_AND_SPEND = "TIME & SPEND"
RUN_TIMELINE = "RUN TIMELINE"
RESULTS = "RESULTS"
EXPERIMENTS = "EXPERIMENTS"
LOG = "LOG"

# The links and the keys (functional spec 7.8, ui_design 10).
COMPARE_LINK = "Compare in Kiln UI"
LINEAGE_LINK = "Lineage in Kiln UI"
REPORT_LINK = "Report"
STOP_KEY = "Stop"
CLOSE_KEY = "Close"

# The run timeline's legend, in its order (ui_design 7.3).
LEGEND_PLAN = "plan"
LEGEND_SCREEN = "screen"
LEGEND_EXPERIMENTS = "experiments"
LEGEND_PROMOTION = "promotion"
LEGEND_NO_WINNER = "gate, no winner"
LEGEND_PROJECTED = "projected"
SETUP_LABEL = "setup"
REPORT_LABEL = "report"


def wave_mark(wave: int) -> str:
    """A wave's label on the timeline: "w4"."""
    return f"w{wave}"


def stopping_header(live: int) -> Line:
    """The Experiments header while the run stops (ui_design section 9)."""
    line: Line = (SPIN, Span(" "), Span(STOPPING, "error"))
    if live:
        line += (Span(f" · {stopping_detail(live)}", "muted"),)
    return line
