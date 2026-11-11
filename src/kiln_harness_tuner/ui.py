"""Progress display (docs/architecture.md section 11): `PlainUi`, the plain
lines of a run with `--no-progress` or with no terminal. A terminal run
shows the app's run screen instead (`tui/`).

`PlainUi` prints one timestamped log line per significant event: phases
get a start line and a done line with elapsed, and the wave_end / exit
lines carry the run's totals (read at event time, no ticking).

Render functions are pure (`RunStatus` and event fields → str) so the
display is unit-testable without a terminal. Item cells carry no verdict
mark at all — the numbers are the signal; see `outcome_cell`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from types import TracebackType

from rich.console import Console

from kiln_harness_tuner.latency import not_faster_verdict
from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    SUBSCRIPTION_LANE,
    EvalOnlyItem,
    ItemOutcome,
    MeasureIssueBaselineItem,
    RunStatus,
    spend_lane_labels,
)
from kiln_harness_tuner.status import RunStatusTracker, exit_detail, spend_split

_FINDING_WIDTH = 60
# Widest kind noun (`status.item_kind`): "measure" — the column exists to
# keep the label column aligned, so it tracks the vocabulary.
_KIND_WIDTH = 7
# Error text is the only unbounded field any line renders (exception strings
# carry whole HTTP response bodies) — clipped, never wrapped off screen.
_ERROR_WIDTH = 80

ITEM_STATE_TEXT = {
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


def _clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def outcome_cell(outcome: ItemOutcome) -> str:
    """The finding that replaces the running status: numbers from the
    analyzer's front-loaded memory_overview tail, or the terminal state.

    **No ✓/✗ mark.** A mark inferred from the tail's leading signed number
    would make the analyzer's overview format un-evolvable, because any
    format not leading with a signed delta would silently invert it (a
    regression opening with `pass 3/34` would render ✓). The numbers in the
    cell already say which way it went, and a mark that can lie is worse
    than no mark."""
    if outcome.status == "completed":
        if outcome.probe is not None:
            # A probe item: the answer is the whole finding.
            return _clip(
                f"probe → {outcome.probe.answer.upper()}: {outcome.probe.evidence}",
                _FINDING_WIDTH,
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
        )
    if outcome.status == "skipped_not_reproduced":
        return "not reproduced → WONT_FIX"
    if outcome.status == "failed_infra":
        error_class = outcome.infra_error.error_class if outcome.infra_error else "?"
        return f"infra: {error_class}"
    if outcome.status == "not_landed" and outcome.speed_verdict:
        # A stop rule of the measurement engine: its line.
        return _clip(outcome.speed_verdict, _FINDING_WIDTH)
    if outcome.status == "not_landed" and outcome.speed is not None:
        return _clip(not_faster_verdict(outcome.speed), _FINDING_WIDTH)
    if outcome.status == "not_landed" and outcome.residual is not None:
        return f"not landed ({outcome.residual.summary}) — rest of split unjudged"
    if outcome.status == "not_landed":
        # An older wave log: the residual trial's summary.
        summary = outcome.trial.summary if outcome.trial else "targets still failing"
        return f"not landed ({summary}) — no train run"
    if outcome.status == "capped":
        # The spending cap: inconclusive, not a dead end.
        line = outcome.measurement.stop_line if outcome.measurement else ""
        return _clip(f"capped (inconclusive): {line}", _FINDING_WIDTH)
    if outcome.status == "dead_end" and outcome.speed_verdict:
        return _clip(outcome.speed_verdict, _FINDING_WIDTH)
    if outcome.status == "dead_end":
        # A proven product dead-end (failed_expected build) — terminal by
        # decision, never retryable infra.
        return "dead end (proven, not retryable)"
    return outcome.status


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


def totals_text(status: RunStatus) -> str:
    """The run's running totals: the spend the budget counts
    (`spend_total_usd`: the task's model calls, never the agents), the agent
    tokens beside it, elapsed, and time left.

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
    a second post-exit totals line beside the exit line's."""
    text = f"{_fmt_usd(status.spend_total_usd)}{spend_split_text(status)}"
    text += f" · {_fmt_duration(status.elapsed_secs)} elapsed"
    if status.time_remaining_known:
        text += f" · {_fmt_duration(status.time_remaining_secs)} left"
    return text


def totals_suffix(status: RunStatus) -> str:
    """The plain-mode totals, read at event time (no ticking), on the two
    lines a reader scans for run state — each wave's end and the exit."""
    return f" — total {totals_text(status)}"


def plain_exit_line(fields: Mapping[str, object], status: RunStatus) -> str:
    """`PlainUi`'s line for the `exit` event, without its timestamp. The
    totals keep moving after `exit` (the clock, and the report writer's
    spend), so a caller builds the line from a snapshot taken while it
    handles that event, as `PlainUi` does."""
    reason = fields.get("reason")
    detail = exit_detail(reason, fields.get("detail"))
    why = f" ({detail})" if detail else ""
    return f"exit: {reason}{why}{totals_suffix(status)}"


def _fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def spend_split_text(status: RunStatus) -> str:
    """` (API keys $y · Claude Code ~1.2M tokens)` — the provider money Kiln
    burns on the drives and eval jobs this run triggers, and the agent
    sessions' tokens, named for the run's agent CLI. The agents run on the
    agent CLI's own account: their tokens are a rough count for information,
    never dollars and never in the budget.

    Parenthesised after the total rather than replacing it: the total is the
    number the budget is spent against, and the row is one line wide.
    Empty while nothing has been spent, so a run's first frames stay quiet;
    money on an unrecognised lane is appended rather than hidden, matching the
    report appendix's "the fold never drops money" rule."""
    kiln, other = spend_split(status.spend_by_lane)
    tokens = status.agent_tokens
    if not (kiln or other or tokens):
        return ""
    labels = spend_lane_labels(status.agent_cli)
    parts = [f"{labels[PROVIDER_LANE]} {_fmt_usd(kiln)}"]
    if other:
        parts.append(f"other {_fmt_usd(other)}")
    if tokens:
        parts.append(f"{labels[SUBSCRIPTION_LANE]} ~{_fmt_tokens(tokens)} tokens")
    return " (" + " · ".join(parts) + ")"


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
    tokens = fields.get("tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
        line += f"   ~{_fmt_tokens(tokens)} tokens"
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


def gate_fill_line(fields: Mapping[str, object]) -> str:
    """One val fill of the promotion gate finished. The gate's own lines come
    only when every fill is in, and a fill can take many minutes, so each
    fill prints as it lands."""
    missing = fields.get("missing") or 0
    return (
        f"  gate: val fill {fields.get('done')}/{fields.get('total')} done ·"
        f" {fields.get('role')} {fields.get('rc_id')} ·"
        f" {fields.get('stored')}/{fields.get('expected')} item(s) stored"
        + (f" · {missing} missing" if missing else "")
    )


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
    whole response body, and one 422 validation payload would otherwise make
    the line a screen long. The full repr stays in the JSONL wave log."""
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


class PlainUi:
    """`--no-progress` / non-TTY fallback: timestamped log lines, one per
    significant event. The timestamps are the point for CI
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
            totals = totals_suffix(self._tracker.snapshot())
            self._print(f"wave {fields.get('wave')} {what}{totals}")
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
            state = ITEM_STATE_TEXT.get(str(fields.get("state")), fields.get("state"))
            self._print(f"  {fields.get('item_id')}: {state}")
        elif kind == "gate_fill":
            self._print(gate_fill_line(fields))
        elif kind == "val_fill_failed":
            self._print(
                f"  gate: val fill failed · {fields.get('rc_id')} ·"
                f" {fields.get('detail')}"
            )
        elif kind == "item_outcome":
            outcome = fields.get("outcome")
            if (
                isinstance(outcome, ItemOutcome)
                and outcome.status == "completed"
                and outcome.probe is not None
            ):
                # Its answer printed already, as the item's detail line.
                return
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
            self._print(plain_exit_line(fields, self._tracker.snapshot()))
        elif kind == "report_written":
            self._print(f"report: {fields.get('path')}")
