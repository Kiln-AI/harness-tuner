"""The Experiments section's cards and the verdict words (functional spec
7.6 with 16.7 to 16.12; ui_design 7.5 and 14).

A card keeps its size and its place from wave start to verdict. It answers
what is tried, on which concern, how far along it is, and whether it works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln_harness_tuner.schemas import ItemOutcome
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.state import (
    BUILD_STATES,
    SPIN,
    CardView,
    DoneView,
    ExperimentsView,
    GaugeLine,
    ItemModel,
    Line,
    NoteLine,
    PlanStep,
    Span,
    StepLine,
    StepState,
    StripView,
    WaveModel,
    joined,
)
from kiln_harness_tuner.tui.model.words import Verdict

if TYPE_CHECKING:
    from kiln_harness_tuner.tui.model.fold import RunModel

DEAD_END_PREFIX = "dead end: "
# The stage each live state is in, per stage list.
_STAGE_OF_STATE = {
    "building_eval": "build",
    "building_rc": "build",
    "reviewing": "build",
    "measuring": "measure",
    "analyzing": "analyze",
    "probing": "asking",
}


def goes_to_gate(outcome: ItemOutcome) -> bool:
    """The gate's own first filter (`orchestrator._maybe_promote`): a
    successful promotion candidate with an rc and an experiment record."""
    item = outcome.item
    return bool(
        getattr(item, "promotion_candidate", False)
        and outcome.result is not None
        and outcome.result.successful
        and outcome.rc_id
        and outcome.experiment_memory_id
    )


def landed_verdict(model: RunModel, item: ItemModel) -> Verdict | None:
    """The verdict from the item's own outcome, before any gate."""
    outcome = item.outcome
    if outcome is None:
        return words.CUT_OFF if item.cut_off else None
    probe = item.probe or outcome.probe
    if probe is not None:
        return words.probe_answer(probe.answer, probe.drives)
    status = outcome.status
    if status == "completed":
        if item.kind in ("fix", "merge"):
            if outcome.result is not None and outcome.result.successful:
                return words.SUCCESS_GATE if goes_to_gate(outcome) else words.SUCCESS
            return words.NO_WIN
        if item.kind == "eval":
            return words.EVAL_BUILT
        if item.kind == "measure":
            # Both verdicts end `completed`; the words are in the detail
            # line the item emits just before (`items._measure_issue_baseline`).
            detail = item.last_detail or ""
            if detail.startswith("not reproduced"):
                return words.NOT_REPRODUCED
            if detail.startswith("reproduced"):
                return words.REPRODUCED
        return words.MEASURED
    if status == "skipped_not_reproduced":
        return words.NOT_REPRODUCED
    if status == "failed_infra":
        error = outcome.infra_error
        return words.failed(error.error_class if error else None)
    if status == "capped":
        return words.CAPPED
    if status == "abandoned":
        return words.ABANDONED
    stop_kind = outcome.measurement.stop_kind if outcome.measurement else None
    if status == "dead_end":
        if stop_kind == "lever_not_applied":
            return words.LEVER_NOT_APPLIED
        return words.dead_end(dead_end_reason(item))
    if stop_kind is None and outcome.residual is not None:
        stop_kind = "residual"
    return words.stop_verdict(stop_kind, model.objective)


def dead_end_reason(item: ItemModel) -> str | None:
    """A build or check dead end has no reason on its outcome; the item's
    last detail line before it carries one (audit 1.7)."""
    detail = item.last_detail or ""
    return (
        detail[len(DEAD_END_PREFIX) :] if detail.startswith(DEAD_END_PREFIX) else None
    )


GATE_VERDICTS = {
    "promoted": words.PROMOTED,
    "rejected": words.REJECTED,
    "held": words.HELD,
    "copied": words.COPIED,
    "blocked": words.BLOCKED,
    "refused": words.REFUSED,
    "pooled": words.POOLED,
}


def verdict(model: RunModel, item: ItemModel) -> Verdict | None:
    """The card's verdict: the gate's, once the gate decided; else the
    item's own (also while it is at the gate)."""
    if item.gate in GATE_VERDICTS:
        return GATE_VERDICTS[item.gate]
    return landed_verdict(model, item)


def card_state(item: ItemModel) -> str:
    """The card state, as the Experiments header counts it."""
    if item.gate == "at_gate":
        return "at the gate"
    if item.landed:
        return "landed"
    if item.state == "planned":
        return "queued"
    if item.state in BUILD_STATES:
        return "building"
    if item.state == "probing":
        return "asking"
    return item.state  # measuring, analyzing


def eval_name(model: RunModel, eval_id: str | None) -> str:
    if not eval_id:
        return words.NEW_EVAL
    for concern in model.concerns:
        if concern.eval_id == eval_id:
            return concern.name
    return model.eval_names.get(eval_id, eval_id)


def _title(model: RunModel, item: ItemModel) -> tuple[str, str, str]:
    """(title, qualifier, text) by item type (functional spec 7.6)."""
    details = item.details
    if item.kind == "fix":
        targets = item.targets
        technique = details.get("technique")
        return (
            eval_name(model, targets[0] if targets else None),
            words.humane(str(technique)) if technique else "",
            str(details.get("hypothesis") or ""),
        )
    if item.kind == "merge":
        wins = details.get("merge_wins") or []
        ingredients = [model.rc_label(str(w.get("rc_id"))) for w in wins]
        return (
            words.ALL_EVALS,
            words.KIND_WORDS["merge"],
            words.merge_text(ingredients, model.parent_label(item.wave)),
        )
    if item.kind == "probe":
        probe = details.get("probe") or {}
        return words.QUESTION, words.KIND_WORDS["probe"], str(probe.get("question", ""))
    issue = str(details.get("issue_memory_id") or "")
    # The preview titles an eval build with a concern; the eval it builds is
    # not one yet, so the card says "New eval".
    if item.kind == "eval":
        return words.NEW_EVAL, words.KIND_WORDS["eval"], words.eval_build_text(issue)
    if item.kind == "measure":
        measure = details.get("measure") or {}
        return (
            eval_name(model, measure.get("eval_id")),
            words.KIND_WORDS["measure"],
            words.measure_text(issue),
        )
    remeasure = details.get("remeasure") or {}
    eval_ids = remeasure.get("eval_ids") or [None]
    return (
        eval_name(model, eval_ids[0]),
        words.KIND_WORDS["remeasure"],
        words.remeasure_text(model.rc_label(str(remeasure.get("rc_id") or ""))),
    )


def _stages(item: ItemModel, verdict_: Verdict | None) -> tuple[str, ...]:
    if item.kind == "probe":
        return words.PROBE_STAGES
    if item.kind == "eval":
        return ("build",)
    if item.kind in ("measure", "remeasure"):
        return ("measure",)
    if item.gate is not None or verdict_ is words.SUCCESS_GATE:
        return (*words.FIX_STAGES, words.GATE_STAGE)
    return words.FIX_STAGES


_GATE_STAGE_STATE: dict[str | None, StepState] = {
    "at_gate": "current",
    "promoted": "done",
    "rejected": "stopped",
    "copied": "stopped",
    "blocked": "stopped",
}


def _work_stage(item: ItemModel, work: list[str]) -> str:
    stage = _STAGE_OF_STATE.get(item.state, work[0])
    return stage if stage in work else work[0]


def steps(
    item: ItemModel, verdict_: Verdict | None
) -> tuple[tuple[str, StepState], ...]:
    """Done stages, the current one, later ones; a stop marks the stage it
    stopped at. A landed item that did not stop has every stage done."""
    names = _stages(item, verdict_)
    work = [name for name in names if name != words.GATE_STAGE]
    current: str | None = None
    mark: StepState = "done"
    if not item.landed:
        if item.state == "planned":
            mark = "later"
        else:
            current, mark = _work_stage(item, work), "current"
    elif verdict_ is not None and verdict_.tone == "error" and item.gate is None:
        current, mark = _work_stage(item, work), "stopped"
    out: list[tuple[str, StepState]] = []
    before: StepState = "later" if mark == "later" else "done"
    for name in work:
        if name == current:
            out.append((name, mark))
            before = "later"
        else:
            out.append((name, before))
    if words.GATE_STAGE in names:
        out.append((words.GATE_STAGE, _GATE_STAGE_STATE.get(item.gate, "later")))
    return tuple(out)


def _strip(item: ItemModel) -> StripView:
    total = max(item.planned or 0, len(item.cells))
    return StripView(tuple(item.cells), total, f"{item.judged}/{total}")


def line4(
    model: RunModel, item: ItemModel, now: float
) -> StripView | GaugeLine | NoteLine | None:
    in_state = words.duration(now - item.state_since)
    if item.gate == "at_gate":
        done, total = model.val_progress(item.rc_id)
        return GaugeLine(done, total, words.val_progress(done, total))
    if not item.landed and item.state == "planned":
        return NoteLine(words.QUEUED)
    if item.kind == "probe":
        probe = item.probe or (item.outcome.probe if item.outcome else None)
        count = (
            probe.drives
            if probe
            else (item.details.get("probe") or {}).get("max_drives", 0)
        )
        return NoteLine(words.drives(int(count or 0)))
    if item.kind in ("measure", "remeasure"):
        if item.landed:
            return None
        return NoteLine(f"{words.MEASURING} · {in_state}")
    if item.kind == "eval":
        return None
    if not item.landed and item.state in BUILD_STATES:
        if item.rebuild_reason is not None:
            return NoteLine(
                f"{words.REBUILDING} · {item.rebuild_reason} · {in_state}", "warning"
            )
        doing = {
            "reviewing": words.REVIEWING,
            "building_eval": words.BUILDING_EVAL,
        }.get(item.state, words.WRITING)
        return NoteLine(f"{doing} · {in_state}")
    if item.planned is None and not item.cells:
        if item.landed:
            return None
        return NoteLine(f"{words.MEASURING} · {in_state}")
    return _strip(item)


def _verdict_line(verdict_: Verdict) -> Line:
    return (Span(f"{verdict_.glyph} {verdict_.card}", verdict_.tone),)


def line5(model: RunModel, item: ItemModel) -> Line:
    if item.landed or item.gate is not None:
        found = verdict(model, item)
        return _verdict_line(found) if found is not None else ()
    if item.kind in ("fix", "merge") and item.state in ("measuring", "analyzing"):
        if item.planned is None and not item.cells:
            return ()
        fixed, broke = words.fixed_broke(item.fixed, item.broke)
        line: Line = (
            Span(fixed, "success"),
            Span(" · ", "muted"),
            Span(broke, "error" if item.broke else "muted"),
        )
        if item.state == "measuring":
            line += (Span(f" {words.SO_FAR}", "muted"),)
        return line
    return ()


def card_view(model: RunModel, item: ItemModel, now: float) -> CardView:
    title, qualifier, text = _title(model, item)
    found = verdict(model, item)
    subtitle = item.item_id
    if item.spend_usd > 0:
        subtitle += f" · {words.money(item.spend_usd)}"
    return CardView(
        item_id=item.item_id,
        title=title,
        qualifier=qualifier,
        text=text,
        steps=steps(item, found),
        line4=line4(model, item, now),
        line5=line5(model, item),
        subtitle=subtitle,
        live=item.live,
    )


def item_subject(model: RunModel, item: ItemModel) -> str:
    """How a log line names what an item is about."""
    if item.kind == "fix":
        targets = item.targets
        return eval_name(model, targets[0] if targets else None)
    return words.KIND_WORDS.get(item.kind, item.kind)


# ---- the Experiments section ----


def _with_elapsed(text: str, since: float | None, now: float) -> str:
    if since is None:
        return text
    elapsed = words.duration(now - since)
    return f"{text} · {elapsed}" if text else elapsed


def start_steps(model: RunModel, now: float) -> tuple[StepLine, ...]:
    """The Starting checklist: the agent CLI test (which no event carries),
    then the run's own start steps in the order they began."""
    cmd = model.info.agent_cli_text
    if model.cli_checked is None:
        first = StepLine(
            "busy",
            words.AGENT_CLI_STEP,
            _with_elapsed(words.agent_cli_testing(cmd), model.started, now),
        )
    else:
        first = StepLine(
            "ok",
            words.AGENT_CLI_STEP,
            words.agent_cli_answered(cmd, model.cli_checked - model.started),
        )
    out = [first]
    for step in model.start_steps:
        text = step.text
        if step.status == "busy" and step.label == words.RECON_STEP:
            recon = model.wave(0)
            items = recon.items if recon else []
            text = words.probes_answered(answered(items), len(items))
        elif step.status == "busy" and step.label == words.SCREEN_STEP:
            screen = model.opening_screen
            text = words.screen_text(
                screen.candidates if screen else 0,
                screen.items_each if screen else None,
                None,
            )
        if step.status == "busy":
            text = _with_elapsed(text, step.since, now)
        label = step.label
        screen = model.opening_screen
        if label == words.SCREEN_STEP and step.status != "skip" and screen:
            label = words.screen_label(screen.round)
        out.append(StepLine(step.status, label, text))
    return tuple(out)


def _plan_step_line(step: PlanStep, wave: int, wrap_up: bool, now: float) -> StepLine:
    busy = step.ended is None
    status = "busy" if busy else "ok"
    took = words.duration((step.ended or now) - step.started)
    if step.kind == "screen":
        text = words.screen_text(step.candidates, step.items_each, step.survivors)
        return StepLine(
            status,
            words.screen_label(step.round),
            f"{text} · {took}" if busy else text,
        )
    if step.kind in words.PLAN_AGAIN:
        return StepLine(
            status, words.PLANNER_STEP, f"{words.PLAN_AGAIN[step.kind]} · {took}"
        )
    if busy:
        return StepLine(
            status, words.PLANNER_STEP, f"{words.planning_wave(wave, wrap_up)} · {took}"
        )
    then = words.PLAN_THEN.get(step.then or "")
    return StepLine(status, words.PLANNER_STEP, f"{took} · {then}" if then else took)


def answered(items: list[ItemModel]) -> int:
    """The probes with an answer; a failed or cut-off probe has none."""
    return sum(
        1
        for item in items
        if item.probe is not None
        or (item.outcome is not None and item.outcome.probe is not None)
    )


def probes_line(items: list[ItemModel], since: float | None, now: float) -> StepLine:
    """A probe phase, or wave 0's recon: answered of planned while they
    run; a warning when some ended with no answer."""
    count, total = answered(items), len(items)
    if since is not None:
        text = _with_elapsed(words.probes_answered(count, total), since, now)
        return StepLine("busy", words.PROBES_STEP, text)
    if count == total:
        return StepLine("ok", words.PROBES_STEP, words.all_probes_answered(total))
    return StepLine("warn", words.PROBES_STEP, words.probes_answered(count, total))


def plan_steps(model: RunModel, wave: int, now: float) -> tuple[StepLine, ...]:
    phase = next(
        (w for w in reversed(model.waves) if w.wave == wave and w.probe_phase), None
    )
    out = []
    for step in model.plans.get(wave, []):
        if step.kind == "probes":
            since = step.started if step.ended is None else None
            out.append(probes_line(phase.items if phase else [], since, now))
        else:
            out.append(_plan_step_line(step, wave, model.wrap_up, now))
    return tuple(out)


def _counts(items: list[ItemModel]) -> Line:
    states = [card_state(item) for item in items]
    parts: list[Line] = []
    for word, live in words.CARD_STATES:
        count = states.count(word)
        if count:
            parts.append((Span(f"{count} {word}", "primary" if live else "fg"),))
    return joined(parts)


def _header(model: RunModel, now: float) -> Line:
    mode = model.mode
    head = Span(words.MODE_WORDS[mode], "bold")
    wave = model.waves[-1] if model.waves else None
    if mode == "starting":
        busy = [s for s in start_steps(model, now) if s.status == "busy"]
        line: Line = (SPIN, Span(" "), head)
        if busy:
            activity = words.STEP_ACTIVITY.get(busy[-1].label, busy[-1].label.lower())
            line += (Span(f" · {activity}", "muted"),)
        return line
    if mode == "planning":
        number = model.planning_wave or 0
        return (
            SPIN,
            Span(" "),
            head,
            Span(f" · wave {number} from ", "muted"),
            Span(model.parent_label()),
        )
    if mode == "done":
        waves = len({w.wave for w in model.waves if w.wave >= 1})
        return (head, Span(f" · {words.plural(waves, 'wave')}", "muted"))
    items = wave.items if wave else []
    if mode == "gate":
        number = model.gate.wave if model.gate else (wave.wave if wave else 0)
        return (
            head,
            Span(f" · wave {number} · {words.EVERY_EVAL_ON_VAL} · ", "muted"),
            *_counts(items),
        )
    label = words.wave_label(wave.wave, wave.probe_phase) if wave else "wave"
    return (
        head,
        Span(f" · {label} from ", "muted"),
        Span(model.parent_label(wave.wave if wave else None)),
        Span(" · ", "muted"),
        *_counts(items),
    )


def done_view(model: RunModel) -> DoneView:
    status = model.tracker.status
    elapsed = (
        model.exit_at - model.zero
        if model.exit_at is not None and model.zero is not None
        else 0.0
    )
    summary: Line = (
        Span(words.duration(elapsed), "bold"),
        Span(" · ", "muted"),
        Span(words.money(status.spend_total_usd)),
    )
    if model.budget is not None:
        summary += (Span(f" of {words.money(model.budget)}", "muted"),)
    experiments = [i for i in model.items.values() if i.kind in ("fix", "merge")]
    stopped = sum(
        1
        for i in experiments
        if i.outcome is not None
        and i.outcome.measurement is not None
        and i.outcome.measurement.status in ("stopped", "capped")
    )
    counts = (
        (len({w.wave for w in model.waves if w.wave >= 1}), "wave", "waves"),
        (len(experiments), "experiment", "experiments"),
        (len(model.promotions), "promotion", "promotions"),
        (stopped, "stopped early", "stopped early"),
    )
    totals = tuple((str(n), one if n == 1 else many) for n, one, many in counts)
    label = Span(f"{words.DONE_BEST}  ", "muted")
    if model.confirmed_rc:
        best: Line = (label, Span(model.rc_label(model.confirmed_rc), "bold"))
        description = model.names.description(model.confirmed_rc)
        if description:
            best += (Span(" · ", "muted"), Span(description))
        best += (Span(f" · {words.MEASURED_ON_VAL}", "muted"),)
    else:
        best = (label, Span(words.KEPT_START))
    if model.report_path:
        report = model.report_path
    elif model.exit_reason == "interrupted" or model.run_ended:
        report = words.NO_REPORT
    else:
        report = words.WRITING_REPORT
    shipped = model.confirmed_rc or model.start_rc
    return DoneView(
        ended=words.how_it_ended(model.exit_reason),
        summary=summary,
        totals=totals,
        best=best,
        report=report,
        report_path=model.report_path,
        next_step=words.next_step(model.rc_label(shipped)) if shipped else "",
        subtitle=words.run_label(model.info.run_id),
    )


def experiments_view(model: RunModel, now: float) -> ExperimentsView:
    mode = model.mode
    header = _header(model, now)
    if mode == "done":
        until = model.exit_at if model.exit_at is not None else now
        elapsed = words.duration(until - model.zero) if model.zero is not None else ""
        return ExperimentsView(mode, header, elapsed, (), "", (), "", done_view(model))
    elapsed = words.duration(now - model.mode_since)
    steps: tuple[StepLine, ...] = ()
    subhead = note = ""
    shown: WaveModel | None = model.waves[-1] if model.waves else None
    if mode == "starting":
        steps = start_steps(model, now)
        shown = model.wave(0)
        if shown is not None and shown.items:
            subhead = words.subhead(0, False)
            if not all(item.landed for item in shown.items):
                note = words.RECON_NOTE
    elif mode == "planning":
        steps = plan_steps(model, model.planning_wave or 0, now)
        if shown is not None:
            subhead = words.subhead(shown.wave, shown.probe_phase)
    cards = tuple(card_view(model, i, now) for i in (shown.items if shown else []))
    return ExperimentsView(mode, header, elapsed, steps, subhead, cards, note, None)
