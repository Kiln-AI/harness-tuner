"""Screening, in code.

Many candidate VARIANTS — a model, a provider, a thinking level, a sampling
parameter — are cheap to make (a run-config fork, no prompt authoring) and
expensive to measure in full. So candidates are SCREENED in code before any
of them becomes an arm: "screen many, commit few".

**A screening round** (`run_screening_round`) is N candidates through the
measurement engine (`kiln_harness_tuner.measure`) with small caps: every candidate
on the SAME queue (one seed for the run's screening, so the same items),
the queue ordered from the baseline's own stored results — its failures and
a few of its passes first, then a stratified random remainder — and cut at
`SCREEN_ROUND1_PER_EVAL` × 2^(round−1) items per eval (successive halving:
the planner re-screens the best few on a larger round). `target_evals`
restricts the round; a candidate screened on some evals is never read as
screened on the others. `mode: "hard_cases"` keeps only the discriminating
block. Speed and cost are paired with the baseline in the same window on the
queue's own items, up to the engine's timing cap; the engine's stop rules
(the objective's defaults merged with the round's `stop_rules`) run after
every chunk.

**The code verdict**: DEAD END when a rule stopped the candidate (invalid
output, clearly worse quality, not faster or cheaper once the timing is
done, a lever that did not reach the call). Otherwise SURVIVOR, with its
stratified net change and 95% interval — the planner chooses what to
promote, re-screen or drop, and survivors are ranked by speed band, then by
net. Every result is recorded in memory (`screen` tag) for the next run.

**Under speed and cost** a screen asks whether the candidate
KEEPS what the baseline passes. The queue is guard-first (`measure`), the
quality rule counts losses on the baseline's passes only
(`stop_rules.guard_losses`, against the run's noise rate), and a survivor
reads "lost k of n baseline passes (no loss rate above X% at 95%)", with
its fixes of baseline failures apart as noise-level evidence. A candidate
that changes the model or the provider (`MODEL_SWAP_KEYS`) moves every item
a little, so it is judged on at least `SWAP_MIN_GUARDS` baseline passes
(its own per-eval cap raised, up to `SWAP_MAX_ITEMS` items) before it can
be a survivor; a thinking-level, sampling or output-mode candidate keeps
the round's size.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.kiln_names import agent_provenance, kiln_name
from kiln_harness_tuner.measure import Candidate, Measured, MeasureSpec, measure
from kiln_harness_tuner.objective import decisive, is_efficiency
from kiln_harness_tuner.schemas import (
    EvalTarget,
    ScreenCandidate,
    ScreenRequest,
    ScreenResult,
    SettledSurvivor,
)
from kiln_harness_tuner.stop_rules import (
    default_stop_rules,
    merge_rules,
    noise_flip_rate,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

# Round 1 takes this many items per eval; each later round doubles it.
SCREEN_ROUND1_PER_EVAL = 3
SCREEN_RC_PREFIX = "SCREEN "
# Survivors whose objective ratios lie within this of each other are in one
# speed band; within a band the lower loss bound (speed, cost) or the higher
# net change ranks first.
SPEED_BAND = 0.03
# Speed and cost: the run-config keys of a model-level swap (a
# subset of `items.SWAP_KEYS`), and the baseline passes such a candidate is
# judged on before it is a survivor. 0 losses in 24 bound the loss rate at
# 12% (95%), about 1.4 times the noise rate one multi-turn task showed; a
# round-1 screen of 3 items per eval on 4 evals holds 12 and bounds it at
# only 22%. The raised cap stops at 40 items (0 in 40 bound it at 7%): past
# that each item buys little, and the arm and the val gate judge the whole
# split anyway.
MODEL_SWAP_KEYS = ("model_name", "model_provider_name")
SWAP_MIN_GUARDS = 24
SWAP_MAX_ITEMS = 40


def screen_seed(ctx: Ctx) -> str:
    """The queue's seed: the run id, so a run's screening can be redrawn."""
    return ctx.run_dir.name if ctx.run_dir is not None else "kiln-harness-tuner"


def rank_survivors(results: list[ScreenResult], objective: str) -> None:
    """Rank this run's survivors in place (`rank`, 1 = best): under speed or
    cost by the objective's median ratio in bands of `SPEED_BAND`, then by
    the lower loss bound, then by the net change; under quality by the net
    change. At equal speed code prefers the candidate that can have lost
    the least (not the higher net, which counts fixes)."""
    survivors = [r for r in results if r.verdict == "survivor"]

    def key(r: ScreenResult) -> tuple[float, float, float]:
        net = r.net if r.net is not None else -9.0
        if is_efficiency(objective):
            c = decisive(r, objective)
            ratio = c.median_ratio if c is not None and c.median_ratio else 9.0
            bound = r.loss_bound if r.loss_bound is not None else 1.0
            return (round(ratio / SPEED_BAND), bound, -net)
        return (0.0, 0.0, -net)

    for n, r in enumerate(sorted(survivors, key=key), start=1):
        r.rank = n
    for r in results:
        if r.verdict != "survivor":
            r.rank = None


def _screen_result(
    cand: ScreenCandidate,
    m: Measured,
    round_size: int,
    evals: list[str],
    evals_text: str = "all",
) -> ScreenResult:
    ev = m.evidence
    per_eval: dict[str, dict[str, int]] = {}
    for r in ev.judged:
        row = per_eval.setdefault(
            r.eval_id, {"n": 0, "candidate": 0, "baseline": 0, "fixed": 0, "broke": 0}
        )
        row["n"] += 1
        row["candidate"] += int(bool(r.cand_pass))
        row["baseline"] += int(bool(r.parent_pass))
        row["fixed"] += int(r.d == 1)
        row["broke"] += int(r.d == -1)
    block = m.residual
    fixed, broke = ev.fixed_broke()
    net_ci = m.estimate.ci() if m.estimate is not None else (None, None)
    # A spending cap is inconclusive: "capped", never a dead end. A round's
    # own size (or a `max_items` rule) is the planner's sizing: a survivor —
    # except a model-level swap judged on fewer baseline passes than its
    # minimum: inconclusive, "capped".
    short = (
        m.guards_required is not None
        and m.status != "stopped"
        and sum(1 for r in ev.judged if r.parent_pass is True) < m.guards_required
    )
    verdict = (
        "error"
        if m.status == "failed"
        else "dead_end"
        if m.status == "stopped"
        else "capped"
        if m.stop_kind == "max_usd" or short
        else "survivor"
    )
    detail = (
        f"{sum(1 for r in ev.judged if r.parent_pass is True)} of the"
        f" {m.guards_required} baseline passes a model or provider swap needs"
        f" judged: inconclusive"
        if short and m.stop_kind != "max_usd"
        else m.overview_detail
    )
    return ScreenResult(
        candidate_id=cand.id,
        label=m.label,
        rc_id=m.rc_id,
        round=round_size,
        evals=evals,
        items=ev.judged_n,
        latency=m.latency,
        cost=m.cost,
        valid=ev.outputs - ev.invalid,
        outputs=ev.outputs,
        per_eval=per_eval,
        passed=sum(v["candidate"] for v in per_eval.values()),
        baseline_passed=sum(v["baseline"] for v in per_eval.values()),
        fixed=fixed,
        broke=broke,
        hard_n=(block.failing + block.guards) if block else 0,
        hard_lost=block.broke if block else 0,
        hard_fixed=block.fixed if block else 0,
        verdict=verdict,  # type: ignore[arg-type]
        reason=(
            m.stop_line
            if m.status == "stopped"
            else detail
            if verdict == "capped"
            else (m.error.detail if m.error else "")
        ),
        line=(
            f"screen {cand.id} {m.body} · evals: {evals_text} → "
            + (
                "survivor"
                if verdict == "survivor"
                else f"capped: {detail}"
                if verdict == "capped"
                else f"dead end: {m.stop_line}"
                if verdict == "dead_end"
                else f"not screened (error): {m.error.detail if m.error else ''}"
            )
        ),
        spend_usd=m.spend_usd,
        stop=m.stop_line,
        stop_kind=m.stop_kind,
        net=m.estimate.net if m.estimate is not None else None,
        net_ci_low=net_ci[0],
        net_ci_high=net_ci[1],
        budget_usd=m.budget.usd if m.budget is not None else None,
        cost_per_item_usd=m.cost_per_item_usd,
        expected_full_usd=m.expected_full_usd,
        loss_bound=ev.loss_bound() if ev.efficiency else None,
        guards_required=m.guards_required,
    )


async def _changes_model(ctx: Ctx, parent_rc_id: str, rc_id: str) -> bool:
    """Does the run config serve the call with another model or provider
    than its parent (`MODEL_SWAP_KEYS`)? A config that cannot be read is
    treated as a swap: the larger sample is the safe side."""
    from kiln_harness_tuner.probes import rc_props

    try:
        parent = await rc_props(ctx, parent_rc_id)
        child = await rc_props(ctx, rc_id)
    except Exception:
        return True
    return any(parent.get(k) != child.get(k) for k in MODEL_SWAP_KEYS)


async def rc_label(ctx: Ctx, rc_id: str) -> str:
    """ "gemini_3_7_flash@gemini_api (thinking low)" for a saved run config."""
    from kiln_harness_tuner.probes import rc_props

    try:
        props = await rc_props(ctx, rc_id)
    except Exception:
        return rc_id
    label = f"{props.get('model_name')}@{props.get('model_provider_name')}"
    thinking = props.get("thinking_level")
    return label + (f" (thinking {thinking})" if thinking else "")


async def _make_candidate_rc(
    ctx: Ctx, candidate: ScreenCandidate, parent_rc_id: str
) -> tuple[str | None, str, bool]:
    """Fork the parent with the candidate's overrides — the model policy and
    the provider's thinking levels checked first. (rc id or None, reason,
    dead_end). `dead_end` is True only when the candidate itself is refused
    (policy, a thinking level the provider does not offer). A failure to make
    the run config — the parent unreadable, Kiln refusing the create — says
    nothing about the candidate: it is an error, and the candidate stays
    eligible (a refused name must not mark an untested model as a dead end)."""
    from kiln_harness_tuner.kiln import KilnError
    from kiln_harness_tuner.model_preflight import flagged_reason
    from kiln_harness_tuner.probes import ProbeSetupError, rc_props, thinking_levels

    if candidate.rc_id:
        if getattr(ctx, "flagged_models", None):
            try:
                saved = await rc_props(ctx, candidate.rc_id)
            except (ProbeSetupError, KilnError):
                saved = {}
            flagged = flagged_reason(
                ctx, saved.get("model_provider_name"), saved.get("model_name")
            )
            if flagged is not None:
                return None, f"the model preflight flagged its model: {flagged}", False
        return candidate.rc_id, "", False
    try:
        props = json.loads(json.dumps(await rc_props(ctx, parent_rc_id)))
    except (ProbeSetupError, KilnError) as exc:
        return None, f"parent {parent_rc_id} unreadable: {exc}", False
    props.update(candidate.overrides)
    if ctx.model_policy is not None:
        refusal = ctx.model_policy.refusal(
            props.get("model_provider_name"), props.get("model_name")
        )
        if refusal is not None:
            return None, refusal, True
    # A model the preflight flagged this run is not screened. It
    # is an error, not a dead end: the failure may not outlive this run.
    flagged = flagged_reason(
        ctx, props.get("model_provider_name"), props.get("model_name")
    )
    if flagged is not None:
        return None, f"the model preflight flagged its model: {flagged}", False
    # Thinking level. A model that offers no thinking levels must not be
    # recorded as a dead end when the planner sends `thinking_level: null`
    # for "leave it unset": that is not a level the provider fails to offer.
    levels = await thinking_levels(
        ctx, props.get("model_provider_name"), props.get("model_name")
    )
    asked = "thinking_level" in candidate.overrides
    wanted = candidate.overrides.get("thinking_level")
    if not levels:
        # A model with no thinking levels runs at its provider default; a
        # level inherited from the parent or asked for is dropped, not fatal.
        props.pop("thinking_level", None)
    elif not asked:
        # Not mentioned: keep the parent's level if this model offers it,
        # otherwise the provider default.
        if props.get("thinking_level") not in levels:
            props.pop("thinking_level", None)
    elif wanted is None:
        # "Leave it unset" on a model that has levels: the provider default.
        props.pop("thinking_level", None)
    elif wanted not in levels:
        return (
            None,
            (
                f"thinking_level={wanted!r} is not offered for"
                f" {props.get('model_provider_name')}/{props.get('model_name')}"
                f" (offered: {levels})"
            ),
            True,
        )
    name = kiln_name(
        f"{SCREEN_RC_PREFIX}{candidate.id} - "
        + (
            candidate.label
            or " ".join(f"{k} {v}" for k, v in candidate.overrides.items())
        )
    )
    description = (
        f"Kiln Harness Tuner screening candidate {candidate.id}: fork of"
        f" {parent_rc_id} with {candidate.overrides}."
    )
    try:
        created = await ctx.kiln.call(
            "POST",
            f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run_configs",
            body={
                "name": name,
                "description": description,
                "run_config_properties": props,
                "provenance": agent_provenance(description, [parent_rc_id]),
            },
        )
    except KilnError as exc:
        return None, f"run config refused: {exc}", False
    rc_id = created.get("id") if isinstance(created, dict) else None
    if rc_id:
        return str(rc_id), "", False
    return None, "run config create returned no id", False


async def run_screening_round(
    ctx: Ctx, request: ScreenRequest, baseline_rc_id: str, round_no: int
) -> list[ScreenResult]:
    """One screening round: make every candidate's run config, then N
    candidates through the engine on one capped queue; each gets a code
    verdict, the table is logged, and every result is recorded in memory.
    Survivors screened on every eval are noted in `ctx.screened_rcs`."""
    from kiln_harness_tuner.orchestrator import _active_evals

    per_eval = request.per_eval_items or SCREEN_ROUND1_PER_EVAL * 2 ** (
        request.round - 1
    )
    active = [str(e["id"]) for e, _c in await _active_evals(ctx)]
    wanted = [
        e for e in active if not request.target_evals or e in request.target_evals
    ]
    all_evals = not request.target_evals or set(active) <= set(request.target_evals)
    evals_text = "all" if all_evals else ", ".join(sorted(wanted)) or "none"

    made: list[tuple[ScreenCandidate, str | None, str, bool]] = []
    for cand in request.candidates:
        parent = cand.parent_rc_id or baseline_rc_id
        rc_id, reason, dead = await _make_candidate_rc(ctx, cand, parent)
        made.append((cand, rc_id, reason, dead))
    ready = [(c, rc) for c, rc, _r, _d in made if rc is not None]
    labels = {rc: c.label or await rc_label(ctx, rc) for c, rc in ready}
    swaps = {
        rc
        for c, rc in ready
        if is_efficiency(ctx.objective)
        and await _changes_model(ctx, c.parent_rc_id or baseline_rc_id, rc)
    }
    rules = merge_rules(
        default_stop_rules(
            ctx.objective,
            full=False,
            noise_rate=noise_flip_rate(getattr(ctx, "train_noise", []), wanted),
        ),
        request.stop_rules or [],
    )
    spec = MeasureSpec(
        targets=[EvalTarget(eval_id=e) for e in wanted],
        rules=rules,
        seed=f"{screen_seed(ctx)}:screen",
        queue_parent_rc_id=baseline_rc_id,
        reference_rc_id=baseline_rc_id,
        objective=ctx.objective,
        full=False,
        per_eval_cap=per_eval,
        hard_only=request.mode == "hard_cases",
        timing=True,  # every candidate is a variant: its lever is checked
        looks=request.looks,
        source="screening",
    )
    measured = (
        {
            m.rc_id: m
            for m in await measure(
                ctx,
                [
                    Candidate(
                        rc_id=rc,
                        label=labels[rc],
                        parent_rc_id=c.parent_rc_id or baseline_rc_id,
                        max_usd=c.max_usd,
                        budget_reason=c.budget_reason,
                        min_guards=SWAP_MIN_GUARDS if rc in swaps else 0,
                        min_guards_max_items=SWAP_MAX_ITEMS,
                    )
                    for c, rc in ready
                ],
                spec,
            )
        }
        if ready
        else {}
    )
    results: list[ScreenResult] = []
    for cand, rc_id, reason, dead in made:
        label = cand.label or (labels.get(rc_id) if rc_id else "") or cand.id
        m = measured.get(rc_id) if rc_id else None
        if m is None:
            results.append(
                ScreenResult(
                    candidate_id=cand.id,
                    label=label,
                    rc_id=rc_id,
                    round=request.round,
                    evals=sorted(wanted),
                    items=0,
                    verdict="dead_end" if dead else "error",
                    reason=reason,
                    line=(
                        f"screen {cand.id} {label}: dead end: {reason}"
                        if dead
                        else f"screen {cand.id} {label}: not screened (error, not"
                        f" a verdict on the candidate): {reason}"
                    ),
                )
            )
            continue
        result = _screen_result(
            cand, m, request.round, sorted(m.per_eval) or sorted(wanted), evals_text
        )
        results.append(result)
        if (
            result.verdict == "survivor"
            and all_evals
            and set(result.per_eval) >= set(m.per_eval)
            and result.per_eval
        ):
            ctx.screened_rcs[m.rc_id] = request.round
    rank_survivors(results, ctx.objective)

    spend = sum(r.spend_usd for r in results)
    header = (
        f"screening round {round_no} (size r{request.round}, up to {per_eval} item(s)"
        f" per eval, evals: {evals_text}, objective {ctx.objective}):"
        f" {len(results)} candidate(s),"
        f" {sum(r.verdict == 'survivor' for r in results)} survivor(s) —"
        f" ${spend:.2f}"
    )
    # Before the event: the run status folds the event's
    # results into the report's trade-off table, which names each
    # candidate's overrides and its round's baseline.
    cands = {c.id: c for c in request.candidates}
    for r in results:
        cand = cands.get(r.candidate_id)
        r.overrides = dict(cand.overrides) if cand is not None else {}
        r.parent_rc_id = cand.parent_rc_id if cand is not None else None
        r.round_no = round_no
        r.run_id = run_label(ctx)
        r.baseline_rc_id = baseline_rc_id
    ctx.log.event(
        "screening_round",
        round=round_no,
        line=header
        + "\n"
        + "\n".join(
            f"  {r.line}" + (f"\n    {r.stop}" if r.stop else "") for r in results
        ),
        results=[r.model_dump() for r in results],
    )
    for r in results:
        if r.verdict == "error":
            continue
        try:
            await ctx.memory.record_screen_result(r)
        except Exception as exc:  # the table is in the wave log either way
            ctx.log.event(
                "memory_write_failed",
                write="screen_result",
                what="a screening result",
                error=repr(exc),
            )
    return results


# ------------------------------------------ screening across runs


def run_label(ctx: Ctx) -> str:
    """This run's id (its directory name), as screening records carry it."""
    run_dir = getattr(ctx, "run_dir", None)
    return run_dir.name if run_dir is not None else "this-run"


async def load_prior_screening(ctx: Ctx) -> None:
    """At run start: every screening result earlier runs recorded, for the
    planner's table (`ctx.prior_screen_results`), and `screened_rcs` restored
    for each survivor that was screened on every eval the task has now and
    whose run config still exists — an arm that reuses it skips viability."""
    from kiln_harness_tuner.orchestrator import _active_evals
    from kiln_harness_tuner.probes import task_run_configs

    try:
        prior = await ctx.memory.prior_screen_results()
    except Exception as exc:  # orientation only: the run goes on without it
        ctx.log.event("prior_screening_unreadable", error=repr(exc)[:300])
        return
    ctx.prior_screen_results = prior
    if not prior:
        return
    try:
        active = {str(e["id"]) for e, _c in await _active_evals(ctx)}
        existing = {str(r.get("id")) for r in await task_run_configs(ctx)}
    except Exception:
        active, existing = set(), set()
    restored: list[str] = []
    for r in prior:  # newest first: the newest survivor of an rc wins
        if (
            r.verdict == "survivor"
            and r.rc_id
            and r.rc_id in existing
            and active
            and set(r.evals) >= active
            and r.rc_id not in ctx.screened_rcs
        ):
            ctx.screened_rcs[r.rc_id] = r.round
            restored.append(f"{r.label} ({r.rc_id}, run {r.run_id}, {r.date})")
    runs = sorted({r.run_id for r in prior if r.run_id}, reverse=True)
    ctx.log.event(
        "prior_screening",
        results=len(prior),
        restored=restored,
        line=(
            f"prior screening: {len(prior)} result(s) from {len(runs)} earlier"
            f" run(s) (newest {runs[0] if runs else 'unknown'}); survivors on"
            f" every eval, viability done: {', '.join(restored) or 'none'}"
        ),
    )


def _quality_neutral(r: ScreenResult) -> bool:
    judged = sum(v.get("n", 0) for v in r.per_eval.values())
    return r.verdict == "survivor" and judged > 0 and r.passed >= r.baseline_passed


def _signature(overrides: dict[str, Any], parent_rc_id: str | None) -> str:
    return json.dumps([parent_rc_id, overrides], sort_keys=True, default=str)


# Two rounds settle a candidate. A third or fourth screening round of the same
# candidate buys nothing.
SETTLED_ROUNDS = 2


def settled_survivors(results: Iterable[ScreenResult]) -> list[SettledSurvivor]:
    """Survivors that were quality-neutral or better in SETTLED_ROUNDS or
    more screening rounds (distinct run and round). One candidate is the
    same overrides on the same parent, whatever run config each round made
    for it; a re-screen by run config counts for the candidate that made it."""
    rows = list(results)
    sig_of_rc = {
        r.rc_id: _signature(r.overrides, r.parent_rc_id)
        for r in rows
        if r.rc_id and r.overrides
    }

    def key(r: ScreenResult) -> str:
        if r.overrides:
            return _signature(r.overrides, r.parent_rc_id)
        return sig_of_rc.get(r.rc_id or "", r.rc_id or r.candidate_id)

    rounds: dict[str, set[tuple[str, int]]] = {}
    rcs: dict[str, set[str]] = {}
    last: dict[str, ScreenResult] = {}
    shape: dict[str, ScreenResult] = {}
    for r in rows:
        if not _quality_neutral(r):
            continue
        k = key(r)
        rounds.setdefault(k, set()).add((r.run_id, r.round_no))
        if r.rc_id:
            rcs.setdefault(k, set()).add(r.rc_id)
        if r.overrides:
            shape.setdefault(k, r)
        if k not in last or (r.run_id, r.round_no) > (last[k].run_id, last[k].round_no):
            last[k] = r
    return [
        SettledSurvivor(
            rc_id=last[k].rc_id,
            rc_ids=sorted(rcs.get(k, set())),
            label=last[k].label,
            overrides=(shape.get(k) or last[k]).overrides,
            parent_rc_id=(shape.get(k) or last[k]).parent_rc_id,
            rounds=len(v),
            last_run=last[k].run_id,
            last_date=last[k].date,
        )
        for k, v in sorted(rounds.items())
        if len(v) >= SETTLED_ROUNDS
    ]


def drop_settled_rescreens(
    request: ScreenRequest, settled: list[SettledSurvivor]
) -> tuple[ScreenRequest | None, list[str]]:
    """The request without its re-screens of settled survivors that give no
    `rescreen_reason` — by run config, or by the same overrides on the same
    parent — and one line per dropped candidate. None when no candidate is
    left."""
    by_rc = {rc: s for s in settled for rc in s.rc_ids}
    by_sig = {
        _signature(s.overrides, s.parent_rc_id): s for s in settled if s.overrides
    }
    keep: list[ScreenCandidate] = []
    dropped: list[str] = []
    for cand in request.candidates:
        hit = by_rc.get(cand.rc_id) if cand.rc_id else None
        if hit is None and cand.overrides:
            hit = by_sig.get(_signature(cand.overrides, cand.parent_rc_id))
        if hit is None or cand.rescreen_reason.strip():
            keep.append(cand)
            continue
        dropped.append(
            f"screen {cand.id} {hit.label}: not screened again — quality-neutral"
            f" or better in {hit.rounds} screening rounds (last run"
            f" {hit.last_run or '?'}). Commit it as an arm"
            f" (work.existing_rc_id: {hit.rc_id}), or give a rescreen_reason."
        )
    if not keep:
        return None, dropped
    return request.model_copy(update={"candidates": keep}), dropped


def initial_screen_request(
    baseline_props: dict[str, Any], levels: list[str] | None
) -> ScreenRequest | None:
    """The round that opens every speed or cost run: the baseline's own model
    at every thinking level the provider offers BELOW the current one (off
    included) — the cheapest speed and cost variants there are. None when
    no lower level is offered or the levels are unknown."""
    current = baseline_props.get("thinking_level")
    offered = list(levels or [])
    if current not in offered:
        return None
    lower = offered[: offered.index(current)]
    if not lower:
        return None
    return ScreenRequest(
        candidates=[
            ScreenCandidate(
                id=f"s0-{n}",
                label=f"thinking {level}",
                overrides={"thinking_level": level},
            )
            for n, level in enumerate(reversed(lower), start=1)
        ],
        reason="opening round: the baseline's model at every lower thinking level",
    )
