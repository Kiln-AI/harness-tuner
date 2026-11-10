"""The Log section's lines (functional spec 7.7; ui_design 4.10 and 7.6).

The fold decides when an event makes a line; this module words it. One line
per notable event, with its time, a glyph by meaning and plain words. Item
states, chunks, phases and spend never make a line: other sections show
them. A line is never cut short.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from kiln_harness_tuner.schemas import ScreenResult
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.state import LogLine
from kiln_harness_tuner.tui.model.words import Verdict

DETECTABILITY_PREFIX = "⚠ detectability: "
PROBE_PHASE_PREFIX = "probe phase — "
RECON_PREFIX = "reconnaissance — "
# A settled survivor's drop line (`viability.drop_settled_rescreens`):
# "screen <id> <label>: not screened again — …", then an instruction to the
# planner that the log leaves out.
SETTLED_HEAD = "screen "
SETTLED_TAIL = ": not screened again"


def line(wall: datetime, kind: str, text: str, *, bold: bool = False) -> LogLine:
    glyph, tone = words.LOG_GLYPHS[kind]
    return LogLine(words.clock(wall), glyph, tone, text, bold=bold)


def verdict_line(wall: datetime, verdict: Verdict, text: str) -> LogLine:
    return LogLine(words.clock(wall), verdict.glyph, verdict.tone, text)


def divider(wall: datetime, name: str) -> LogLine:
    return LogLine(words.clock(wall), "", "faint", name, divider=True)


def run_started(objective: str, budget: float | None, limit_mins: int | None) -> str:
    parts = ["Run started", objective]
    parts.append(words.money(budget) if budget is not None else words.NO_BUDGET)
    if limit_mins is not None:
        parts.append(words.span_of_minutes(limit_mins))
    return " · ".join(parts)


def starting_from(rc: str) -> str:
    return f"Starting from {rc}"


def detectability_warning(text: str) -> str:
    return text.removeprefix(DETECTABILITY_PREFIX)


def wave_started(
    wave: int, probe_phase: bool, parent: str, headline: str, wrap_up: bool
) -> str:
    if wave == 0:
        return f"Wave 0, recon · {headline.removeprefix(RECON_PREFIX)}"
    head = f"Wave {wave} probes" if probe_phase else f"Wave {wave}"
    text = f"{head} from {parent} · {headline.removeprefix(PROBE_PHASE_PREFIX)}"
    return text + (" · last wave" if wrap_up and not probe_phase else "")


def built(item_id: str, summary: str) -> str:
    return f"{item_id} built: {summary}"


def rebuilding(item_id: str, check: str | None) -> str:
    finding = words.FINDING_LOG.get(check or "")
    return (
        f"{item_id}: {finding}, rebuilding once"
        if finding
        else (f"{item_id}: {words.REBUILDING}")
    )


def item_verdict(item_id: str, subject: str, verdict: Verdict) -> str:
    return f"{item_id} {subject}: {words.log_verdict(verdict.log)}"


def probe_answered(item_id: str, question: str, verdict: Verdict) -> str:
    """The question, then its answer: after a question mark, with no colon."""
    joint = " " if question.endswith("?") else ": "
    return f"{item_id} {question}{joint}{verdict.log}"


def cut_off(item_ids: Sequence[str], fatal: bool) -> str:
    why = "after a fatal error" if fatal else "at the deadline"
    return f"Cut off {why}: {', '.join(item_ids)}"


def grace(item_ids: Sequence[str], secs: object) -> str:
    time = words.duration(float(secs)) if isinstance(secs, (int, float)) else "a while"
    return f"Deadline: {', '.join(item_ids)} get {time} to finish their analysis"


def screening_round(round_no: int | None, results: Sequence[ScreenResult]) -> str:
    survivors = sum(1 for r in results if r.verdict == "survivor")
    head = f"Screen round {round_no}" if round_no else "Screen round"
    return f"{head}: {survivors} of {words.plural(len(results), 'candidate')} survive"


def settled_survivor(line: str) -> str:
    """A candidate the round left out: it was quality-neutral or better in
    earlier rounds."""
    head = line.split(" — ", 1)[0]
    name = head.removeprefix(SETTLED_HEAD).removesuffix(SETTLED_TAIL)
    if head.startswith(SETTLED_HEAD) and head.endswith(SETTLED_TAIL) and name:
        return (
            f"Screen candidate {name}: not screened again,"
            " quality-neutral or better in earlier rounds"
        )
    return (
        "A screen candidate was not screened again:"
        " quality-neutral or better in earlier rounds"
    )


_DROPS = {"dead_end": "dropped", "error": "failed", "capped": "capped"}


def screen_drop(result: ScreenResult) -> str:
    what = _DROPS.get(result.verdict, result.verdict)
    name = f"{result.candidate_id} {result.label}".strip()
    return f"{name}: {what}" + (f", {result.reason}" if result.reason else "")


def assumed(default: str, question: str) -> str:
    return f"Assumed: {default} (open question: {question})"


def gate_started(candidates: int) -> str:
    return f"Promotion gate: {words.plural(candidates, 'candidate')} on val"


NO_WINNER = "Promotion gate: no winner"


def promoted(who: str, concern: str | None, change: str | None) -> str:
    if concern and change:
        return f"{who} promoted · {concern} {change} on val"
    return f"{who} promoted"


def rejected(who: str, concern: str | None, train: str | None, val: str | None) -> str:
    if concern and train and val:
        return f"{who} rejected at val · {concern} train {train}, val {val}"
    return f"{who} rejected at val"


def held(who: str, question: str | None) -> str:
    return f"{who} held: waiting on " + (
        f"the open question “{question}”" if question else "an open question"
    )


def copied(who: str) -> str:
    return f"{who} blocked: copies eval text"


def blocked(who: str, reasons: Sequence[str], *, before_gate: bool) -> str:
    where = "before the gate" if before_gate else "at the gate"
    text = f"{who} blocked {where}"
    return text + (f": {'; '.join(reasons)}" if reasons else "")


def pooled(who: str, reasons: Sequence[str]) -> str:
    if reasons:
        return f"{who} not judged at the gate: {'; '.join(reasons)}"
    return f"{who} not judged at the gate, stays pooled"


def gate_skipped(reason: str) -> str:
    return f"Promotion gate skipped: {reason}" if reason else "Promotion gate skipped"


def tripwire(wave: object, failed: object, error_class: object) -> str:
    return (
        f"Tripwire on wave {wave}: {failed} items failed with"
        f" {words.humane(str(error_class))}"
    )


BACKOFF = "Tripwire backoff: a pause before the next wave"


def job_errors(count: int, split: object, error_class: object) -> str:
    what = words.humane(str(error_class)) if error_class else "an error"
    return f"{words.plural(count, 'eval item')} errored on {split or 'a split'}: {what}"


def retry(item_id: object, source: object, error_class: object, backoff: object) -> str:
    prefix = f"{item_id}: " if item_id else ""
    wait = (
        words.duration(float(backoff)) if isinstance(backoff, (int, float)) else "a bit"
    )
    return (
        f"{prefix}{source} failed ({words.humane(str(error_class))}),"
        f" retrying in {wait}"
    )


def memory_write_failed(what: object) -> str:
    return f"A memory write failed: {what or 'a memory record'}"


def memory_record(record_kind: object, op: object, overview: str) -> str | None:
    """A lesson, a dead end or a new issue an agent wrote, with its
    overview. Other kinds, and issue updates, make no line."""
    name = words.MEMORY_KINDS.get(str(record_kind))
    if name is None or (record_kind == "issue" and op != "create"):
        return None
    if op == "update":
        name += " updated"
    return f"{name}: {_without_label(overview)}"


def _without_label(overview: str) -> str:
    """An overview's own leading "LESSON: " style label, which the line's
    words already say."""
    head, sep, rest = overview.partition(": ")
    if sep and head.replace(" ", "").replace("_", "").isupper():
        return rest
    return overview


def ended(reason: str | None) -> str:
    return f"Run ended: {words.how_it_ended(reason)}"


def report_written(fallback: bool) -> str:
    return (
        "Report written (the short fallback report)" if fallback else "Report written"
    )
