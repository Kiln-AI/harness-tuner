"""The experiment details panel and the experiment picker (functional spec
7.9, ui_design 4.13 and 7.7).

Details that do not fit the overview open in a modal panel. The panel shows
one item in full: what it is, what it tries, what it costs, and how it went.
Only the fields and the sections that have data show. The picker lists
every item of the run under its wave, newest wave first and recon last.

Both find an item by its key (`ItemKey`), not by its id: a wave's probe
phase and its re-plan can use the same ids.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from rich.cells import cell_len

from kiln_harness_tuner.schemas import RcChange
from kiln_harness_tuner.tui.model import cards, links, results, words
from kiln_harness_tuner.tui.model.state import (
    SPIN,
    Concern,
    ConcernKey,
    DetailField,
    DetailLine,
    DetailLink,
    DetailSection,
    DetailsView,
    ItemKey,
    ItemModel,
    Line,
    NoteLine,
    PickerGroup,
    PickerRow,
    Span,
    Tone,
    WaveModel,
)
from kiln_harness_tuner.tui.model.words import Verdict

if TYPE_CHECKING:
    from kiln_harness_tuner.tui.model.fold import RunModel

# The kinds that build a run config of their own.
BUILDS = ("fix", "merge")
SEPARATOR = Span(" · ", "muted")


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _said(value: object, tone: Tone = "fg") -> Line:
    """Words from the planner or an agent, as they wrote them; nothing when
    they wrote none."""
    text = _text(value).strip()
    return (Span(text, tone),) if text else ()


def _pad(text: str, width: int) -> str:
    """`text` padded to `width` terminal cells: names come from the
    person's Kiln project, wide characters included."""
    return text + " " * max(width - cell_len(text), 0)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _wave(model: RunModel, item: ItemModel) -> WaveModel | None:
    index = item.wave_index
    return model.waves[index] if 0 <= index < len(model.waves) else None


def config_label(model: RunModel, rc_id: str) -> str:
    """A run config by its name. The run's starting config says so."""
    label = model.rc_label(rc_id)
    return f"{words.RESULTS_START} · {label}" if rc_id == model.start_rc else label


def parent_rc(model: RunModel, item: ItemModel) -> str | None:
    """The run config the item works on: the parent its train results
    compare to, else its plan's own parent, else its wave's baseline."""
    wave = _wave(model, item)
    return (
        item.parent_rc_id
        or _text(item.details.get("parent_rc_id"))
        or (wave.parent_rc_id if wave is not None else None)
        or None
    )


def _measured_rc(model: RunModel, item: ItemModel) -> str | None:
    """The existing run config a probe drives, a measure measures or a
    re-measure completes."""
    if item.kind == "remeasure":
        rc = _text(_mapping(item.details.get("remeasure")).get("rc_id"))
        return rc or item.rc_id
    return parent_rc(model, item)


# ---- the status line ----


def _verdict_line(found: Verdict) -> Line:
    """A verdict in its full words: the panel has room for them."""
    return (Span(f"{found.glyph} {found.log}", found.tone),)


def status_line(model: RunModel, item: ItemModel, now: float) -> Line:
    """The card's verdict, or its live state with what it does now."""
    found = cards.verdict(model, item)
    if item.gate == "at_gate" and found is not None:
        done, total = model.val_progress(item.rc_id)
        return _verdict_line(found) + (
            Span(f" · {words.val_progress(done, total)}", "muted"),
        )
    if item.landed or item.gate is not None:
        return _verdict_line(found) if found is not None else ()
    if item.state == "planned":
        return (Span(f"· {words.QUEUED_STATUS}", "muted"),)
    state = cards.card_state(item)
    head: Line = (SPIN, Span(" "), Span(words.LIVE_STATES.get(state, state), "primary"))
    note = cards.line4(model, item, now)
    if state == "building" and isinstance(note, NoteLine):
        return head + (SEPARATOR, Span(note.text, note.tone))
    in_state = Span(f" · {words.duration(now - item.state_since)}", "muted")
    so_far = cards.line5(model, item)
    return head + ((SEPARATOR, *so_far) if so_far else ()) + (in_state,)


# ---- the fields ----


def _kind(model: RunModel, item: ItemModel) -> str:
    details = item.details
    if item.kind == "merge":
        wins = [_mapping(w) for w in details.get("merge_wins") or []]
        rcs = [_text(w.get("rc_id")) for w in wins if w.get("rc_id")]
        # An ingredient built in this run by its item id.
        return words.merge_kind([model.who(rc) for rc in rcs])
    kind = words.DETAIL_KINDS.get(item.kind, item.kind)
    if item.kind == "fix" and details.get("wildcard"):
        return f"{kind} · {words.WILDCARD}"
    if item.kind == "probe" and _mapping(details.get("probe")).get("code"):
        return f"{kind} · {words.CODE_PROBE}"
    return kind


def _target_lines(model: RunModel, eval_id: str, scores: object) -> list[Line]:
    """One target as its Results cards name it, one line per card: its
    scores, or all of them. An eval with no card yet by its name."""
    keys = {str(key) for key in scores} if isinstance(scores, (list, tuple)) else None
    found = [
        concern
        for concern in model.concerns
        if concern.eval_id == eval_id and (keys is None or concern.key[1] in keys)
    ]
    if not found:
        return [(Span(words.eval_title(cards.eval_name(model, eval_id))),)]
    out: list[Line] = []
    for concern in found:
        title, qualifier = results.concern_title(model, concern)
        muted = (Span(f" {qualifier}", "muted"),) if qualifier else ()
        out.append((Span(title), *muted))
    return out


def _targets(model: RunModel, item: ItemModel) -> list[Line]:
    details = item.details
    pairs: list[tuple[str, object]]
    if item.kind in BUILDS:
        targets = [_mapping(t) for t in details.get("targets") or []]
        pairs = [(_text(t.get("eval_id")), t.get("scores")) for t in targets]
        if not pairs and item.kind == "fix":
            return [(Span(words.NEW_EVAL),)]
    elif item.kind == "measure":
        measure = _mapping(details.get("measure"))
        pairs = [(_text(measure.get("eval_id")), measure.get("scores"))]
    elif item.kind == "remeasure":
        remeasure = _mapping(details.get("remeasure"))
        pairs = [(_text(e), None) for e in remeasure.get("eval_ids") or []]
    else:
        return []
    return [
        line
        for eval_id, scores in pairs
        if eval_id
        for line in _target_lines(model, eval_id, scores)
    ]


def _spend(item: ItemModel) -> Line:
    """The item's whole spend, and the cap on its measurement: the one the
    loop measures with, else the planner's before measurement starts."""
    cap = item.cap_usd
    planned = item.details.get("max_usd")
    if cap is None and isinstance(planned, (int, float)):
        cap = float(planned)
    if item.spend_usd <= 0 and cap is None:
        return ()
    text = words.item_spend(
        item.spend_usd, not item.landed, cap, item.measure_spend_usd
    )
    return (Span(text),)


def _time(item: ItemModel, now: float) -> Line:
    """From its first live state to its verdict."""
    if item.started_at is None:
        return ()
    end = item.landed_at if item.landed_at is not None else now
    return (Span(words.duration(end - item.started_at)),)


def _field(label: str, *lines: Line) -> DetailField:
    return DetailField(label, tuple(line for line in lines if line))


def _fields(model: RunModel, item: ItemModel, now: float) -> Iterator[DetailField]:
    wave = _wave(model, item)
    yield _field(words.KIND_FIELD, (Span(_kind(model, item)),))
    if wave is not None:
        text = words.detail_wave(wave.wave, wave.probe_phase)
        yield _field(words.WAVE_FIELD, (Span(text),))
    if item.kind in BUILDS:
        rc = parent_rc(model, item)
        yield _field(words.PARENT_FIELD, (Span(config_label(model, rc)),) if rc else ())
    elif item.kind in ("probe", "measure", "remeasure"):
        rc = _measured_rc(model, item)
        label = (Span(config_label(model, rc)),) if rc else ()
        yield _field(words.RUN_CONFIG_FIELD, label)
    if item.kind in ("eval", "measure"):
        yield _field(words.ISSUE_FIELD, _said(item.details.get("issue_memory_id")))
    yield _field(words.TARGETS_FIELD, *_targets(model, item))
    technique = _text(item.details.get("technique"))
    yield _field(
        words.LEVER_FIELD, (Span(words.humane(technique)),) if technique else ()
    )
    yield _field(words.SPEND_FIELD, _spend(item))
    yield _field(words.TIME_FIELD, _time(item, now))


# ---- the sections ----


def _change_lines(change: RcChange | None) -> list[Line | DetailLine]:
    """The build agent's summary, its list of changes, and what it kept."""
    if change is None:
        return []
    out: list[Line | DetailLine] = [_said(change.summary)]
    out += [
        DetailLine((Span(line),), bullet=True)
        for line in change.changes
        if line.strip()
    ]
    if change.unchanged:
        kept = ", ".join(change.unchanged)
        out.append((Span(words.UNCHANGED_LABEL, "muted"), Span(kept, "muted")))
    return out


def _named(model: RunModel, rows: Sequence[tuple[Concern, Line]]) -> list[Line]:
    """Per-concern lines, the concern's name in a column as wide as the
    longest."""
    names = [" ".join(filter(None, results.concern_title(model, c))) for c, _ in rows]
    width = max((cell_len(name) for name in names), default=0)
    return [
        (Span(_pad(name, width + 2)), *line)
        for name, (_concern, line) in zip(names, rows, strict=True)
    ]


def _counts(item: ItemModel) -> Line:
    if item.kind not in BUILDS or not item.judged:
        return ()
    total = max(item.planned or 0, item.judged)
    fixed, broke = words.fixed_broke(item.fixed, item.broke)
    return (
        Span(words.judged_counts(item.judged, total)),
        SEPARATOR,
        Span(fixed, "success"),
        SEPARATOR,
        Span(broke, "error" if item.broke else "muted"),
    )


def _train_lines(model: RunModel, item: ItemModel) -> list[Line]:
    """The judged items, then each concern's estimated change against the
    parent. A raw train score is skewed (the parent's failures are judged
    first), so the change is the measure, as for Best Unconfirmed."""
    estimates = results.arm_unconfirmed(
        item.train_rows,
        item.train_latency,
        item.train_cost,
        rc_id=item.rc_id,
        item_id=item.item_id,
        generation=0,
    )
    by_key: dict[ConcernKey, Any] = {}
    for estimate in estimates:
        by_key.setdefault(estimate.key, estimate)
    rows: list[tuple[Concern, Line]] = []
    for concern in results.all_concerns(model):
        estimate = by_key.get(concern.key)
        if estimate is None:
            continue
        reference = 1.0 if estimate.factor else 0.0
        value = estimate.change
        change = words.change_unit(
            concern.kind, words.change_text(concern.kind, value, reference)
        )
        better = concern.better(value, reference)
        worse = concern.better(reference, value)
        tone: Tone = "muted"
        if estimate.clear and (better or worse):
            tone = "success" if better else "error"
        line: Line = (Span(change, tone),)
        if not estimate.clear:
            line += (Span(f" · {words.NOT_CLEAR}", "muted"),)
        rows.append((concern, line))
    return [line for line in (_counts(item), *_named(model, rows)) if line]


def _val_lines(model: RunModel, item: ItemModel) -> list[Line]:
    """Its gate's val values against the incumbent, measured on every val
    item: the raw values, then the change, each in a column."""
    table = item.val_table
    if table is None:
        return []
    base = results.table_values(table, "baseline")
    cand = results.table_values(table, "candidate")
    found: list[tuple[Concern, str, str, Tone]] = []
    for concern in results.all_concerns(model):
        if concern.key not in base or concern.key not in cand:
            continue
        before, after = base[concern.key], cand[concern.key]
        change = words.change_unit(
            concern.kind, words.change_text(concern.kind, after, before)
        )
        tone: Tone = "muted"
        if concern.better(after, before):
            tone = "success"
        elif concern.better(before, after):
            tone = "error"
        values = (
            f"{words.value_text(concern.kind, before)} {words.ARROW}"
            f" {words.value_text(concern.kind, after)}"
        )
        found.append((concern, values, change, tone))
    width = max((cell_len(values) for _c, values, _ch, _t in found), default=0)
    rows: list[tuple[Concern, Line]] = [
        (concern, (Span(_pad(values, width + 2)), Span(change, tone)))
        for concern, values, change, tone in found
    ]
    return _named(model, rows)


def _findings(item: ItemModel) -> Line:
    outcome = item.outcome
    result = outcome.result if outcome is not None else None
    return _said(result.findings) if result is not None else ()


def _section(title: str, *lines: Line | DetailLine) -> DetailSection:
    """A section of the lines with words; a plain `Line` is no list line."""
    shown = (
        line if isinstance(line, DetailLine) else DetailLine(line) for line in lines
    )
    return DetailSection(title, tuple(line for line in shown if line.line))


def _sections(model: RunModel, item: ItemModel) -> Iterator[DetailSection]:
    details = item.details
    if item.kind == "fix":
        yield _section(words.HYPOTHESIS_SECTION, _said(details.get("hypothesis")))
        yield _section(words.PREDICTED_SECTION, _said(details.get("predicted_effect")))
    elif item.kind == "probe":
        probe = _mapping(details.get("probe"))
        yield _section(words.QUESTION_SECTION, _said(probe.get("question")))
        yield _section(words.UNBLOCKS_SECTION, _said(probe.get("for_idea")))
        answer = item.probe or (item.outcome.probe if item.outcome else None)
        if answer is not None:
            yield _section(
                words.EVIDENCE_SECTION,
                _said(answer.evidence),
                _said(answer.notes, "muted"),
            )
    else:
        yield _section(words.PLAN_SECTION, cards.about(model, item)[1])
        # Why the planner added an eval build or a measurement.
        yield _section(words.REASON_SECTION, _said(details.get("reason")))
    yield _section(words.CHANGE_SECTION, *_change_lines(item.change))
    yield _section(words.TRAIN_SECTION, *_train_lines(model, item))
    yield _section(words.VAL_SECTION, *_val_lines(model, item))
    yield _section(words.FINDINGS_SECTION, _findings(item))


# ---- the links ----


def _links(model: RunModel, item: ItemModel) -> tuple[DetailLink, ...]:
    """The graph with the item's run config selected, and its compare with
    its parent. None before the item has a run config."""
    if item.kind in BUILDS:
        rc = item.rc_id
    elif item.kind == "remeasure":
        rc = _measured_rc(model, item)
    else:
        rc = None
    if not rc:
        return ()
    out = [DetailLink(words.GRAPH_DETAIL_LINK, model.lineage_url(rc))]
    parent = parent_rc(model, item) if item.kind in BUILDS else None
    if parent and parent != rc:
        info = model.info
        url = links.compare_url(
            info.kiln_host, info.project_id, info.task_id, [parent, rc]
        )
        out.append(DetailLink(words.COMPARE_PARENT_LINK, url))
    return tuple(out)


# ---- the views ----


def details_view(model: RunModel, key: ItemKey, now: float) -> DetailsView | None:
    item = model.item_at(key)
    if item is None:
        return None
    return DetailsView(
        title=item.item_id,
        qualifier=cards.about(model, item)[0],
        status=status_line(model, item, now),
        fields=tuple(f for f in _fields(model, item, now) if f.lines),
        sections=tuple(s for s in _sections(model, item) if s.lines),
        links=_links(model, item),
    )


def picker_status(model: RunModel, item: ItemModel) -> Line:
    """The card state while live, the card's verdict once landed."""
    if item.live:
        return (SPIN, Span(f" {cards.card_state(item)}", "primary"))
    found = cards.verdict(model, item)
    if found is not None:
        return (Span(f"{found.glyph} {found.card}", found.tone),)
    return (Span(f"· {cards.card_state(item)}", "muted"),)


def picker_view(model: RunModel) -> tuple[PickerGroup, ...]:
    """Every `wave_start` with items, newest first, then recon."""
    shown = [wave for wave in model.waves if wave.items]
    ordered = [w for w in reversed(shown) if w.wave != 0] + [
        w for w in shown if w.wave == 0
    ]
    return tuple(
        PickerGroup(
            words.picker_group(wave.wave, wave.probe_phase),
            tuple(
                PickerRow(
                    key=item.key,
                    title=item.item_id,
                    qualifier=cards.about(model, item)[0],
                    status=picker_status(model, item),
                )
                for item in wave.items
            ),
        )
        for wave in ordered
    )
