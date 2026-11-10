"""Progress display (docs/architecture.md section 11) — every view renders
`RunStatus`.

Two EventLog-shaped views, chosen in cli.py:

- `LiveUi` (`rich.live`): completed waves are flushed ONCE as static
  scrollback — at `wave_end`, when the block (items + banners) is final, so
  post-wave lines land below it in chronological order; the
  next `wave_start` / `exit` flushes are crash-path nets — never erased or
  replaced;
  the live renderable is the active wave's block plus, whenever a loop phase
  (planning / promotion gate / tripwire backoff / reporting) is running, a
  phase line with its own elapsed ticker, and a pinned TOTAL row
  (spend · elapsed · time left) — all recomputed each refresh
  from a tracker snapshot (elapsed / time-left tick between events; the
  run-level totals are stated ONLY there, never also in the wave header).
  The totals row lives in the live region until `exit`, and is never flushed
  with a wave; at exit the totals stop moving and the exit block carries them
  into scrollback — so exactly one totals row is on screen at any moment.
  Pre-flight results, phase done-lines, dispatch retries, and failed memory
  writes print as scrollback, so a long silent phase is visible (the
  memory-write line is the compensating control for the loop's one
  log-and-continue write).
- `PlainUi` (`--no-progress` / non-TTY): one timestamped log line per
  significant event — phases get a start line and a done line with elapsed,
  and the wave_end / exit lines carry the same totals (read at event time, no
  ticking).

Render functions are pure (`RunStatus`/`WaveStatus`/`ItemStatus` → str) so the
display is unit-testable without a terminal. Item cells carry no verdict mark
at all — the numbers are the signal; see `outcome_cell`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime
from types import TracebackType

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from kiln_harness_tuner.latency import not_faster_verdict
from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    SUBSCRIPTION_LANE,
    EvalOnlyItem,
    ItemOutcome,
    ItemStatus,
    MeasureIssueBaselineItem,
    RunStatus,
    WaveStatus,
    spend_lane_labels,
)
from kiln_harness_tuner.status import RunStatusTracker, spend_split

RULE = "─" * 78
_LABEL_WIDTH = 42
_FINDING_WIDTH = 60
# Widest kind noun (`status.item_kind`): "measure" — the column exists to
# keep the label column aligned, so it tracks the vocabulary.
_KIND_WIDTH = 7
# Error text is the only unbounded field any line renders (exception strings
# carry whole HTTP response bodies) — clipped, never wrapped off screen.
_ERROR_WIDTH = 80
_SPINNER = "⠸"
# The frames `LiveUi` cycles through. `_SPINNER` is one of them, and it is the
# frame every pure renderer draws unless it is handed another.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_FPS = 10
# Cells in an eval's progress bar.
_BAR_WIDTH = 12
# A finished row's finding starts where an in-flight row's state text does —
# `_SPINNER` plus its separating space, which a done row
# fills with a blank.
_FINDING_GUTTER = " " * len(f"{_SPINNER} ")

LIVE_STATE_TEXT = {
    "planned": "planned",
    "building_eval": "building eval…",
    "building_rc": "building rc…",
    "reviewing": "checking generality…",
    "measuring": "eval running",
    "analyzing": "analyzing…",
}

# phase_end done-line nouns (events carry the machine `name`; the display
# label on phase_start is free prose and may be long).
_PHASE_NOUNS = {
    "preflight": "MCP-lane preflight",
    "setup": "setup",
    "planning": "planning",
    "promotion_gate": "promotion gate",
    "tripwire_backoff": "tripwire backoff",
    "reporting": "report writing",
}
# The item row's left gutter: 2 spaces + item_id(6) + kind — the detail line
# indents to the label column.
_DETAIL_INDENT = " " * (2 + 6 + _KIND_WIDTH)


def _fmt_duration(secs: float) -> str:
    secs = max(int(secs), 0)
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs}s"


def _fmt_usd(amount: float) -> str:
    return f"${amount:.2f}"


def _clip(text: str, limit: int, full: bool = False) -> str:
    """`text` cut to `limit` characters. With `full`, the whole text: lines
    that go to the scrollback wrap rather than lose their end."""
    if full or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def outcome_cell(outcome: ItemOutcome, full: bool = False) -> str:
    """The finding that replaces the running status: numbers from the
    analyzer's front-loaded memory_overview tail, or the terminal state.

    **No ✓/✗ mark.** A mark inferred from the tail's leading signed number
    would make the analyzer's overview format un-evolvable, because any
    format not leading with a signed delta would silently invert it (a
    regression opening with `pass 3/34` would render ✓). The numbers in the
    cell already say which way it went, and a mark that can lie is worse
    than no mark.

    (The promotion banner in `_banners` keeps its ✓: it states an event that
    happened, not a judgment inferred from an agent-authored string, so it
    carries none of the inversion hazard this docstring is about.)"""
    if outcome.status == "completed":
        if outcome.probe is not None:
            # A probe item: the answer is the whole finding.
            return _clip(
                f"probe → {outcome.probe.answer.upper()}: {outcome.probe.evidence}",
                _FINDING_WIDTH,
                full,
            )
        if isinstance(outcome.item, MeasureIssueBaselineItem):
            # Both verdicts end `completed` with result=None, so the frozen
            # row can only report that the measurement happened;
            # reproduced-or-not rides the wave log's `item_detail` event and
            # the issue memory the item just PATCHed.
            return "baseline measured"
        if isinstance(outcome.item, EvalOnlyItem) or outcome.result is None:
            return "eval built"
        # Clipped: a separator-less overview falls back to the whole ≤280-char
        # string, which must not blow the line.
        return _clip(
            outcome.result.memory_overview.split(" — ", 1)[-1].strip(),
            _FINDING_WIDTH,
            full,
        )
    if outcome.status == "skipped_not_reproduced":
        return "not reproduced → WONT_FIX"
    if outcome.status == "failed_infra":
        error_class = outcome.infra_error.error_class if outcome.infra_error else "?"
        return f"infra: {error_class}"
    if outcome.status == "not_landed" and outcome.speed_verdict:
        # A stop rule of the measurement engine: its line.
        return _clip(outcome.speed_verdict, _FINDING_WIDTH, full)
    if outcome.status == "not_landed" and outcome.speed is not None:
        return _clip(not_faster_verdict(outcome.speed), _FINDING_WIDTH, full)
    if outcome.status == "not_landed" and outcome.residual is not None:
        return f"not landed ({outcome.residual.summary}) — rest of split unjudged"
    if outcome.status == "not_landed":
        # An older wave log: the residual trial's summary.
        summary = outcome.trial.summary if outcome.trial else "targets still failing"
        return f"not landed ({summary}) — no train run"
    if outcome.status == "capped":
        # The spending cap: inconclusive, not a dead end.
        line = outcome.measurement.stop_line if outcome.measurement else ""
        return _clip(f"capped (inconclusive): {line}", _FINDING_WIDTH, full)
    if outcome.status == "dead_end" and outcome.speed_verdict:
        return _clip(outcome.speed_verdict, _FINDING_WIDTH, full)
    if outcome.status == "dead_end":
        # A proven product dead-end (failed_expected build) — terminal by
        # decision, never retryable infra.
        return "dead end (proven, not retryable)"
    return outcome.status


def spinner_frame(now: float) -> str:
    """The spinner frame for a moment in seconds."""
    return _SPINNER_FRAMES[int(now * _SPINNER_FPS) % len(_SPINNER_FRAMES)]


def progress_bar(done: int, total: int) -> str:
    """A fixed-width bar: `━` for the done share, `─` for the rest."""
    filled = round(_BAR_WIDTH * done / total) if total > 0 else 0
    filled = min(max(filled, 0), _BAR_WIDTH)
    return "━" * filled + "─" * (_BAR_WIDTH - filled)


def item_line(item: ItemStatus, spinner: str = _SPINNER, full: bool = False) -> str:
    """The item's row. With `full`, a finished row keeps its whole label and
    finding: a label too long for its column puts the finding on a second
    line, under the label."""
    left = (
        f"  {item.item_id:<6}{item.kind:<{_KIND_WIDTH}}"
        f"{_clip(item.label, _LABEL_WIDTH):<{_LABEL_WIDTH + 2}}"
    )
    if item.outcome is not None:
        cell = outcome_cell(item.outcome, full)
        if item.spend_usd > 0:
            cell += f"   {_fmt_usd(item.spend_usd)}"
        if full and len(item.label) > _LABEL_WIDTH:
            head = f"  {item.item_id:<6}{item.kind:<{_KIND_WIDTH}}{item.label}"
            return f"{head}\n{_DETAIL_INDENT}→ {cell}"
        # The gutter of a done row: an in-flight row spends
        # the same two columns on `_SPINNER + " "`, and a live block mixes
        # both kinds of row. Without it the findings sit two columns left of
        # the running states.
        return f"{left}{_FINDING_GUTTER}{cell}"
    state_text = LIVE_STATE_TEXT.get(item.live_state, item.live_state)
    if item.live_state == "measuring" and item.eval_progress is not None:
        done, total = item.eval_progress
        state_text += f" {progress_bar(done, total)} ({done}/{total})"
    line = f"{left}{spinner} {state_text}   {_fmt_duration(item.elapsed_secs)}"
    if item.spend_usd > 0:  # spend-so-far as it accumulates
        line += f"   {_fmt_usd(item.spend_usd)}"
    return line


def item_lines(
    item: ItemStatus, spinner: str = _SPINNER, full: bool = False
) -> list[str]:
    """The item's row plus, in flight, an indented sub-state detail line
    (which eval started/finished measuring; per-eval start/done lines). Done items
    never get one — the frozen finding line stays exactly as before."""
    lines = item_line(item, spinner, full).split("\n")
    if item.outcome is None and item.detail:
        lines.append(f"{_DETAIL_INDENT}↳ {item.detail}")
    return lines


def render_header(status: RunStatus, started_label: str) -> str:
    line = (
        f"Kiln Harness Tuner · {status.project_id} / {status.task_id}"
        f" · started {started_label}"
    )
    if status.objective != "quality":
        line += f" · objective: {status.objective}"
    # The guidance is free text of any length, so it gets its own line.
    if status.guidance:
        line += f'\n guidance: "{status.guidance}"'
    return f"{line}\n{RULE}"


def change_lines(fields: dict) -> list[str]:
    """The build agent's "what changed" block, printed once
    when the build lands: the one-line summary, then each concrete change
    and what stayed as the parent had it."""
    change = fields.get("change")
    if not isinstance(change, dict):
        return []
    item_id = str(fields.get("item_id", ""))
    lines = [f"  {item_id:<6}Δ {change.get('summary', '')}"]
    for entry in change.get("changes") or []:
        lines.append(f"{_DETAIL_INDENT}· {entry}")
    unchanged = change.get("unchanged") or []
    if unchanged:
        lines.append(f"{_DETAIL_INDENT}= unchanged: {'; '.join(unchanged)}")
    check = change.get("consistency_check")
    if check:
        lines.append(f"{_DETAIL_INDENT}✓ checked: {check}")
    return lines


def _banners(wave: WaveStatus, full: bool = False) -> list[str]:
    lines = []
    if wave.promotion is not None:
        if wave.promotion.winner_rc_id:
            lines.append(
                f"  ✓ promoted {wave.promotion.winner_rc_id}"
                f" — {_clip(wave.promotion.rationale, 60, full)}"
            )
        else:
            lines.append("  · promotion: no winner")
    if wave.promotion_skipped:  # a hard-fail must be visible
        lines.append(
            f"  ⚠ promotion skipped: {_clip(wave.promotion_skipped, 60, full)}"
        )
    if wave.tripwire:
        lines.append(f"  ⚠ tripwire: {wave.tripwire}")
    return lines


def render_wave_plan(wave: WaveStatus, full: bool = False) -> str:
    """The batch-plan headline — printed at wave START (LiveUi emits it
    as scrollback the moment the wave begins, not when it finishes)."""
    header = f" Wave {wave.wave} plan: {wave.plan_headline}"
    if wave.probe_phase:
        header += "  [probes first — arms are planned after the answers]"
    if wave.should_wrap_up:
        header += "  [wrap-up]"
    if wave.rejected_summary:
        header += f'  (rejected: "{_clip(wave.rejected_summary, 50, full)}")'
    return header


def render_wave_items(wave: WaveStatus, full: bool = False) -> str:
    """A finished wave's item lines + banners (its plan line already sits in
    scrollback from wave start)."""
    return "\n".join(
        [
            *(line for i in wave.items for line in item_lines(i, full=full)),
            *_banners(wave, full),
        ]
    )


def render_wave(wave: WaveStatus) -> str:
    """A whole wave block — plan line + items. Convenience composition with
    no production caller (report.py builds its own markdown): kept for tests
    asserting the full block in one shot."""
    return f"{render_wave_plan(wave)}\n{render_wave_items(wave)}"


def render_active_wave(status: RunStatus, spinner: str = _SPINNER) -> str:
    """The live block: active-wave header (which wave, against which
    baseline) plus one line per item.

    Spend and time-left left this header when the pinned totals row took
    them over: they are run-level facts, and stating them here
    too put the same two numbers on screen twice for the whole run."""
    wave = status.waves[-1]
    baseline = status.baseline_history[-1] if status.baseline_history else "?"
    header = f" Wave {wave.wave} · baseline {baseline}"
    if wave.should_wrap_up:
        header += " · wrap-up"
    return "\n".join(
        [
            header,
            *(line for i in wave.items for line in item_lines(i, spinner)),
            *_banners(wave),
        ]
    )


def render_phase_line(status: RunStatus, spinner: str = _SPINNER) -> str:
    """The loop-phase live line (planning / promotion gate / tripwire backoff
    / reporting): what the loop is doing when no item row is moving, with its
    own elapsed ticker. Empty when no phase is running."""
    if not status.phase:
        return ""
    return (
        f" {spinner} {status.phase_detail or status.phase}"
        f"   {_fmt_duration(status.phase_elapsed_secs)}"
    )


def totals_text(status: RunStatus) -> str:
    """The run's running totals: spend across BOTH lanes (the
    `spend_total_usd` fold already sums them), elapsed, and time left.

    The `left` segment is omitted when no `run_start` deadline was ever seen
    rather than guessing — `time_remaining_secs` reads 0.0 in that case,
    which would render as a run that just ran out. Past the deadline the
    tracker clamps it to 0, so a long-overrun run says `0s left`: truthful
    enough for a wave-boundary budget.

    Known exclusion (so nobody re-derives it against the JSONL): the
    report_writer agent's own cost is emitted as a `spend` event AFTER
    `exit`, so no rendered total carries it — the last one is snapshotted in
    the exit block, and the report's spend appendix is written from the same
    pre-report state. `wave_log.jsonl` has the event. Surfacing it would cost
    a second post-exit totals line, which is the duplicate-row problem the
    `_exited` gate exists to prevent."""
    text = f"{_fmt_usd(status.spend_total_usd)}{spend_split_text(status)}"
    text += f" · {_fmt_duration(status.elapsed_secs)} elapsed"
    if status.time_remaining_known:
        text += f" · {_fmt_duration(status.time_remaining_secs)} left"
    return text


def _fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def spend_split_text(status: RunStatus) -> str:
    """` (Claude Code $x · Kiln $y)` — the two things the run's money is:
    the agent sessions, named for the run's agent CLI, vs the provider tokens
    Kiln burns on the drives and eval jobs this run triggers. Agent tokens
    with no cost to read (a Codex session, a plan that prices nothing) are
    shown beside the agent lane's dollars, never folded in as $0.

    Parenthesised after the total rather than replacing it: the total is the
    number the budget is spent against, and the row is one line wide.
    Empty while nothing has been spent, so a run's first frames stay quiet;
    money on an unrecognised lane is appended rather than hidden, matching the
    report appendix's "the fold never drops money" rule."""
    claude_code, kiln, other = spend_split(status.spend_by_lane)
    unpriced = status.unmeasured_tokens
    if not (claude_code or kiln or other or unpriced):
        return ""
    labels = spend_lane_labels(status.agent_cli)
    agent = f"{labels[SUBSCRIPTION_LANE]} {_fmt_usd(claude_code)}"
    if unpriced:
        agent += f" + {_fmt_tokens(unpriced)} tokens unpriced"
    parts = [agent, f"{labels[PROVIDER_LANE]} {_fmt_usd(kiln)}"]
    if other:
        parts.append(f"other {_fmt_usd(other)}")
    return " (" + " · ".join(parts) + ")"


def render_totals_row(status: RunStatus) -> str:
    """The pinned bottom row of the live block — ruled off from the
    active wave/phase lines above it."""
    return f"{RULE}\n TOTAL  {totals_text(status)}"


def baseline_found_line(fields: dict) -> str:
    """The rc id alone. The `baseline` record's overview became
    free prose with no contract, so there is no bounded provenance string to
    append — cold-start provenance is `baseline_seeded_line`'s job."""
    return f"baseline: {fields.get('rc_id')}"


def baseline_seeded_line(fields: dict) -> str:
    """The task had no `baseline` record and code picked one
    — say which rc and by which rule, and name a default that could not be
    honored (a user who DID set one deserves to know why another rc is being
    optimized)."""
    line = f"baseline seeded: {fields.get('rc_id')} ({fields.get('rule')})"
    unresolvable = fields.get("unresolvable_default")
    if unresolvable:
        line += f" — the task's default {unresolvable} is not a live run config"
    return line


def setup_line(fields: dict) -> str:
    """The one-line setup summary, rendered from the event's
    own counts. `unbound_unresolved` is a permanent report-only condition on
    some tasks, so it is a roll-up count here and a list in the report only."""
    counts = [
        (name, fields.get(name))
        for name in ("seeded", "refreshed", "repaired", "unmeasurable")
    ]
    written = ", ".join(f"{value} {name}" for name, value in counts if value)
    total = fields.get("evals_total")
    active = fields.get("evals_active")
    line = f"setup: {active} of {total} evals active"
    line += f" — {written}" if written else " — in sync"
    unbound = fields.get("unbound_unresolved")
    if unbound:
        line += f"; {unbound} issue(s) need a human (no eval binding)"
    cost = fields.get("cost_usd")
    if isinstance(cost, (int, float)) and cost > 0:
        line += f"   {_fmt_usd(float(cost))}"
    return line


# Events that carry their own one plain line: printed as-is by
# both displays (`own_line`).
SETUP_LINE_EVENTS = frozenset(
    {
        "quarantine",
        "baseline_measured",
        "baseline_speed",
        "probe_rcs_archived",
        "provider_quota",
        "measure_chunk",
        "candidate_budget",
        "measurement",
        "gate_progress",
        "heldout_tag",
        "train_set",
        "screening_round",
        "baseline_progress",
        "prior_screening",
        "spend_reconciled",
        "screen_candidate_dropped",
        # The detectability check and the noise check.
        "detectability",
        "detectability_row",
        "detectability_warning",
        "noise_estimate",
        "noise_check",
        "noise_row",
        "noise_warning",
        # A candidate the gate blocked for copying eval text,
        # with the reason, and the baseline audit's eval-text half (a line
        # only when it found a copy).
        "gate_block",
        "baseline_judge_copy_audit",
        # The model preflight's summary and each flagged model.
        "model_preflight",
        "model_preflight_flag",
        "model_preflight_untested",
        # An eval Kiln can't load, which the run leaves out.
        "eval_unloadable",
        # A planned eval build dropped because eval building is off.
        "eval_item_dropped",
    }
)


def own_line(kind: str, fields: dict) -> str:
    """The event's own line, or "" when another event shows it: an arm's
    `measure_chunk` line is also its `item_detail`, so only a
    screening round's chunk lines print on their own."""
    if kind in ("measure_chunk", "candidate_budget") and fields.get("item_id"):
        return ""
    return str(fields.get("line") or "")


def janitor_line(count: int) -> str:
    """The sweep result — or the 'pre-flight checks passed' line that beats
    silence when there was no debris."""
    if count:
        plural = "s" if count != 1 else ""
        return f"janitor: {count} stale experiment record{plural} stamped — ABANDONED"
    return "pre-flight checks passed"


def phase_done_line(fields: dict) -> str:
    name = str(fields.get("name") or "phase")
    noun = _PHASE_NOUNS.get(name, name)
    wave = fields.get("wave")
    wave_part = f" wave {wave}" if wave is not None else ""
    elapsed = fields.get("elapsed_secs")
    duration = (
        _fmt_duration(float(elapsed)) if isinstance(elapsed, (int, float)) else "?"
    )
    return f"{noun}{wave_part} done ({duration})"


def _count_of(fields: dict) -> int:
    count = fields.get("count")
    return count if isinstance(count, int) else 0


def retry_line(fields: dict) -> str:
    item_id = fields.get("item_id")
    prefix = f"{item_id}: " if item_id else ""
    backoff = fields.get("backoff_secs")
    duration = (
        _fmt_duration(float(backoff)) if isinstance(backoff, (int, float)) else "?"
    )
    return (
        f"{prefix}{fields.get('source')} attempt {fields.get('attempt')} failed"
        f" ({fields.get('error_class')}) — retrying in {duration}"
    )


def memory_write_failed_line(fields: dict) -> str:
    """Two swallowed-write paths emit this: the gate's rejection
    writeback and the promotion's `val` stats write. Both are
    log-and-continue, so without a printed line the operator's only signal is
    the JSONL wave log — and in the rejection case the record left unwritten
    is exactly the reads-like-a-win record that write exists to fix.

    The event kind is generic, so the subject comes from the event's `what`
    field rather than being hardcoded — each emitter names its own subject and this
    line stays true. `write` is the machine-readable discriminator.

    "incomplete", never "missing": a write pair fails independently, so a
    failed `note` usually sits on a record whose `stamp` DID land — that
    record is half-written, not unstamped, and the line must not say
    otherwise. `_ERROR_WIDTH` bounds the error: KilnAPIError stringifies the
    whole response body, and one 422 validation payload would otherwise shove
    the active-wave block off screen. The full repr stays in the JSONL wave
    log."""
    what = fields.get("what") or "memory record"
    # Each emitter names the ids it actually has — the val-stats
    # writeback has an rc but no experiment record — so absent ids are
    # dropped rather than rendered as a literal "None", and the whole
    # "for <subject>" clause goes when there are none. No filler noun: the
    # two emitters have different subjects (a rejected candidate, a
    # promotion), so any stand-in would be wrong for one of them.
    subject = " / ".join(
        str(value) for value in (fields.get("rc_id"), fields.get("exp_mem_id")) if value
    )
    target = f" for {subject}" if subject else ""
    return (
        f"memory write FAILED ({fields.get('write')}){target} — its"
        f" {what} is incomplete: {_clip(str(fields.get('error')), _ERROR_WIDTH)}"
    )


# Styling for `LiveUi`. The renderers above return plain text, and the tests
# read that text. This layer only adds colour and stops a line from wrapping;
# it never changes a character. It styles by what a line starts with or
# contains, so it reads only the shapes the renderers above produce.
_RULE_MAX = 100
_ITEM_ID = re.compile(r"\bw\d+-\d+\b")
_DURATION = re.compile(r"\b\d+(?:h\d{2}m|m\d{2}s|s)\b(?: elapsed| left)?")
_SPINNER_GLYPH = re.compile(f"[{_SPINNER_FRAMES}]")
_BAR = re.compile(r"(━*)(─+)(?= \()|(━+)(?= \()")
_WARNING = re.compile(r"⚠.*")
_FAILURE = re.compile(r"\b(?:infra: \S+|dead end \(proven, not retryable\))")
_SOFT_FAILURE = re.compile(r"\b(?:not landed|capped \(inconclusive\)|not reproduced)")


def style_line(line: str, width: int) -> Text:
    """One rendered line as styled text that does not wrap."""
    if line == RULE:
        return Text("─" * min(width, _RULE_MAX), style="dim")
    text = Text(line, no_wrap=True, overflow="ellipsis")
    stripped = line.strip()
    if line.startswith("Kiln Harness Tuner"):
        text.stylize("dim")
        text.stylize("bold", 0, len("Kiln Harness Tuner"))
        return text
    if line.startswith(" guidance: "):
        text.stylize("dim italic")
        return text
    if re.match(r" Wave \d+ plan", line):
        text.stylize("bold cyan")
        return text
    if line.startswith(" Wave "):
        text.stylize("bold")
        return text
    if line.startswith(" TOTAL"):
        text.stylize("bold", 0, len(" TOTAL"))
        text.highlight_regex(_DURATION, "dim")
        return text
    if stripped.startswith("✓ promoted"):
        text.stylize("bold green")
        return text
    if line.startswith(" · ") or line.startswith(_DETAIL_INDENT):
        text.stylize("dim")
        if stripped.startswith("✓ checked"):
            text.stylize("green", line.index("✓"), line.index("✓") + 1)
        return text
    if stripped.startswith("exit:") or stripped.startswith("report:"):
        text.stylize("bold")
        return text
    text.highlight_regex(_ITEM_ID, "bold")
    text.highlight_regex(_DURATION, "dim")
    text.highlight_regex(_SPINNER_GLYPH, "bold cyan")
    text.highlight_regex(_FAILURE, "red")
    text.highlight_regex(_SOFT_FAILURE, "yellow")
    text.highlight_regex(_WARNING, "yellow")
    if " Δ " in line:
        delta = line.index(" Δ ") + 1
        text.stylize("magenta", delta, delta + 1)
    match = _BAR.search(line)
    if match:
        done = match.group(1) if match.group(1) is not None else match.group(3)
        start = match.start()
        text.stylize("cyan", start, start + len(done))
        if match.group(2):
            text.stylize("dim", start + len(done), match.end())
    return text


def style_block(block: str, width: int) -> Group:
    """Every line of a rendered block, styled by `style_line`."""
    return Group(*(style_line(line, width) for line in block.split("\n")))


_ITEM_ROW = re.compile(r"  w\d+-\d+ ")


# Where an item row's finding starts: id, kind, label column, gutter.
_FINDING_COLUMN = 2 + 6 + _KIND_WIDTH + _LABEL_WIDTH + 2 + len(_FINDING_GUTTER)


def _hanging_indent(line: str, width: int) -> int:
    """Where a wrapped line's next lines start: under the text they continue."""
    if line.startswith(_DETAIL_INDENT):
        return len(_DETAIL_INDENT) + 2  # after its `· `, `↳ ` or `→ `
    if _ITEM_ROW.match(line):
        if line[8:10] == "Δ ":
            return 2 + 6 + 2
        if width - _FINDING_COLUMN >= 30 and line[_FINDING_COLUMN - 2 :][:2] == "  ":
            return _FINDING_COLUMN  # a one-line row: continue its finding
        return 2 + 6 + _KIND_WIDTH
    return len(line) - len(line.lstrip()) + 2


def _break_offsets(line: str, width: int, indent: int) -> list[int]:
    """Where to cut `line` so each piece fits `width`, the pieces after the
    first starting `indent` columns in. Cuts at a space when one is close."""
    offsets: list[int] = []
    start, room = 0, width
    while len(line) - start > room:
        cut = line.rfind(" ", start + 1, start + room + 1)
        if cut <= start + room // 2:
            cut = start + room  # no space close enough: cut the word
        offsets.append(cut)
        start = cut
        room = width - indent
    return offsets


def wrap_line(line: str, width: int) -> list[Text]:
    """One rendered line, styled by `style_line` and wrapped to `width` with a
    hanging indent. Scrollback is permanent, so it keeps every character."""
    styled = style_line(line, width)
    if line == RULE or len(line) <= width:
        return [styled]
    indent = max(2, min(_hanging_indent(line, width), width - 20))
    pieces = styled.divide(_break_offsets(line, width, indent))
    out = [pieces[0]]
    for piece in pieces[1:]:
        piece = piece[len(piece.plain) - len(piece.plain.lstrip(" ")) :]
        out.append(Text(" " * indent) + piece)
    for text in out:
        text.no_wrap = True
    return out


def wrap_block(block: str, width: int) -> Group:
    """Every line of a rendered block, wrapped by `wrap_line`."""
    return Group(*(t for line in block.split("\n") for t in wrap_line(line, width)))


class _ActiveView:
    """Live renderable: each rich refresh re-renders from a fresh tracker
    snapshot, so elapsed/time-left tick and the spinner turns without new
    events."""

    def __init__(self, ui: LiveUi):
        self._ui = ui

    def __rich__(self) -> Group:
        return style_block(self._ui.active_text(), self._ui.width)


class LiveUi:
    """EventLog view over the tracker: static scrollback for finished waves
    (flushed at `wave_end`), a `rich.live.Live` block for the active one."""

    def __init__(
        self,
        tracker: RunStatusTracker,
        *,
        console: Console | None = None,
        now: Callable[[], datetime] = datetime.now,
        spinner: Callable[[], str] | None = None,
    ):
        self._tracker = tracker
        self._console = console or Console()
        self._started_label = now().strftime("%H:%M")
        # The spinner turns with the wall clock. Tests pass a fixed frame.
        self._spinner = spinner or (lambda: spinner_frame(time.monotonic()))
        # Read by the rich refresh thread (active_text) without a lock —
        # accepted race: both are monotonic flags whose worst-case cost is one
        # stale render frame (CPython attribute reads are atomic). Do NOT
        # lock the render path.
        self._flushed_waves = 0
        self._exited = False
        self._live = Live(
            _ActiveView(self),
            console=self._console,
            refresh_per_second=_SPINNER_FPS,
            transient=True,
        )

    def __enter__(self) -> LiveUi:
        self._live.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._live.stop()

    @property
    def width(self) -> int:
        return self._console.width

    def active_text(self) -> str:
        status = self._tracker.snapshot()
        frame = self._spinner()
        parts = []
        if not self._exited and len(status.waves) > self._flushed_waves:
            parts.append(render_active_wave(status, frame))
        # The phase line renders even with no active wave (pre-wave planning,
        # post-exit report writing) — the live region is never silent while
        # the loop is doing something.
        phase_line = render_phase_line(status, frame)
        if phase_line:
            parts.append(phase_line)
        # The UI always shows running totals — before wave 1 the live
        # region is this row alone. It lives only here, never in a
        # _flush_waves block, so exactly one totals row is on screen.
        #
        # Gated on `_exited` like the wave block above: at exit the totals
        # stop running and the exit block states them in scrollback, but the
        # live region keeps refreshing through the report-writing phase that
        # follows. An ungated row would tick on beside the frozen scrollback
        # one for minutes — two rows, same label, DIFFERENT numbers, exactly
        # when the operator is reading the run's final state.
        if not self._exited:
            parts.append(render_totals_row(status))
        return "\n".join(parts)

    def _print(self, text: str) -> None:
        # Live hooks Console.print: static lines land above the live region.
        self._console.print(wrap_block(text, self.width), highlight=False)

    def _flush_waves(self, status: RunStatus, upto: int) -> None:
        # Items + banners only: each wave's plan line already went to
        # scrollback at its wave_start (no duplication).
        for index in range(self._flushed_waves, upto):
            self._print(render_wave_items(status.waves[index], full=True))
        self._flushed_waves = max(self._flushed_waves, upto)

    def event(self, kind: str, **fields: object) -> None:
        if kind == "run_start":
            self._print(render_header(self._tracker.snapshot(), self._started_label))
        elif kind == "item_change":
            for line in change_lines(fields):
                self._print(line)
        elif kind == "baseline_found":
            self._print(f" {baseline_found_line(fields)}")
        elif kind == "baseline_seeded":
            self._print(f" {baseline_seeded_line(fields)}")
        elif kind in ("setup_sync", "setup_ok"):
            self._print(f" {setup_line(fields)}")
        elif kind == "janitor":
            self._print(f" {janitor_line(_count_of(fields))}")
        elif kind in SETUP_LINE_EVENTS and own_line(kind, fields):
            self._print(f" {own_line(kind, fields)}")
        elif kind == "phase_end":
            # The live phase line vanishes with the phase — its done-line
            # (with elapsed) freezes into scrollback instead.
            self._print(f" · {phase_done_line(fields)}")
        elif kind == "dispatch_retry":
            self._print(f" ⚠ {retry_line(fields)}")
        elif kind == "memory_write_failed":
            self._print(f" ⚠ {memory_write_failed_line(fields)}")
        elif kind == "wave_end":
            # The wave's block is final (items + banners; the orchestrator
            # emits this before the gate's phase_end) — freeze it into
            # scrollback NOW, so post-wave lines (gate done, next planning
            # done) land BELOW it in chronological order.
            status = self._tracker.snapshot()
            self._flush_waves(status, len(status.waves))
        elif kind == "wave_start":
            # The new wave is already appended (tracker runs first in the
            # fanout) — everything before it is finished scrollback (already
            # flushed at its wave_end; this flush is the crash-path net),
            # then the new wave's plan headline prints immediately, at
            # wave start.
            status = self._tracker.snapshot()
            self._flush_waves(status, len(status.waves) - 1)
            # A blank line sets each wave apart in the scrollback.
            self._console.print()
            self._print(render_wave_plan(status.waves[-1], full=True))
        elif kind == "exit":
            status = self._tracker.snapshot()
            self._flush_waves(status, len(status.waves))
            self._exited = True
            # The live region is transient — this is where the run's totals
            # enter the terminal record, and (with `_exited` set above) the
            # only totals row from here on. The exit line does not restate
            # spend: the row beneath it carries that number plus elapsed.
            # Written as one block rather than `render_totals_row`, whose
            # leading RULE would put a second rule between the exit line and
            # its own totals.
            self._print(
                f"{RULE}\n exit: {fields.get('reason')}\n TOTAL  {totals_text(status)}"
            )
        elif kind == "report_written":
            self._print(f" report: {fields.get('path')}")


class PlainUi:
    """`--no-progress` / non-TTY fallback: timestamped log lines, one per
    significant event — no live region. The timestamps are the point for CI
    logs: a 6-minute planner dispatch reads as a visible gap between its start
    and done lines.

    It reads the tracker for one thing only: the wave-boundary totals
    suffix. The tracker is the loop's single spend/clock fold, and it is
    required rather than optional because those totals must always show — an
    optional one would let a miswired call site silently drop them."""

    def __init__(
        self,
        tracker: RunStatusTracker,
        *,
        console: Console | None = None,
        now: Callable[[], datetime] = datetime.now,
    ):
        self._tracker = tracker
        self._console = console or Console()
        self._now = now

    def __enter__(self) -> PlainUi:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def _print(self, text: str) -> None:
        stamp = self._now().strftime("%H:%M:%S")
        self._console.print(f"[{stamp}] {text}", markup=False, highlight=False)

    def _totals_suffix(self) -> str:
        """The plain-mode totals: the same numbers LiveUi pins, read at event
        time (no ticking without a live region), on the two lines a reader
        scans for run state — each wave's end and the exit."""
        return f" — total {totals_text(self._tracker.snapshot())}"

    def event(self, kind: str, **fields: object) -> None:
        if kind == "run_start":
            self._print(
                f"harness-tuner: run started (timeout {fields.get('timeout_mins')} min)"
            )
        elif kind == "item_change":
            for line in change_lines(fields):
                self._print(line.strip())
        elif kind == "baseline_found":
            self._print(baseline_found_line(fields))
        elif kind == "baseline_seeded":
            self._print(baseline_seeded_line(fields))
        elif kind in ("setup_sync", "setup_ok"):
            self._print(setup_line(fields))
        elif kind == "janitor":
            self._print(janitor_line(_count_of(fields)))
        elif kind in SETUP_LINE_EVENTS and own_line(kind, fields):
            self._print(own_line(kind, fields))
        elif kind == "phase_start":
            self._print(str(fields.get("label") or fields.get("name")))
        elif kind == "phase_end":
            self._print(phase_done_line(fields))
        elif kind == "item_detail":
            self._print(f"  {fields.get('item_id')}: {fields.get('detail')}")
        elif kind == "dispatch_retry":
            self._print(retry_line(fields))
        elif kind == "memory_write_failed":
            self._print(memory_write_failed_line(fields))
        elif kind == "wave_end":
            what = "probes done" if fields.get("probe_phase") else "done"
            self._print(f"wave {fields.get('wave')} {what}{self._totals_suffix()}")
        elif kind == "wave_start":
            self._print(
                f"wave {fields.get('wave')}: {fields.get('plan_headline')}"
                f" · baseline {fields.get('baseline_rc_id')}"
                + (" · wrap-up" if fields.get("should_wrap_up") else "")
            )
            for item in fields.get("items") or []:  # type: ignore[union-attr]
                self._print(
                    f"  {item['item_id']} {item['kind']}: {item['label']} — planned"
                )
        elif kind == "item_state":
            state = LIVE_STATE_TEXT.get(str(fields.get("state")), fields.get("state"))
            self._print(f"  {fields.get('item_id')}: {state}")
        elif kind == "item_outcome":
            outcome = fields.get("outcome")
            cell = outcome_cell(outcome) if isinstance(outcome, ItemOutcome) else "?"
            self._print(f"  {fields.get('item_id')}: {cell}")
        elif kind == "promotion":
            winner = fields.get("winner_rc_id")
            self._print(
                f"promotion: {winner or 'no winner'}"
                + (f" — {fields.get('rationale')}" if winner else "")
            )
        elif kind == "promotion_skipped":
            self._print(f"promotion skipped: {fields.get('reason')}")
        elif kind == "promotion_blocked":
            # The reason a gate refused its winner, printed.
            raw = fields.get("reasons")
            reasons = "; ".join(str(r) for r in raw) if isinstance(raw, list) else ""
            self._print(
                f"promotion blocked: {fields.get('winner_rc_id')}"
                + (f" — {reasons}" if reasons else "")
            )
        elif kind == "tripwire":
            self._print(
                f"tripwire on wave {fields.get('wave')}:"
                f" {fields.get('failed')}× {fields.get('error_class')}"
            )
        elif kind == "exit":
            self._print(f"exit: {fields.get('reason')}{self._totals_suffix()}")
        elif kind == "report_written":
            self._print(f"report: {fields.get('path')}")
