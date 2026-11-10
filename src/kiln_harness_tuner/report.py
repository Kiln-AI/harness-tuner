"""Report-writer invocation (docs/architecture.md section 11) — the format-critical
write pattern: the agent returns the round report as MARKDOWN; code writes
`reports/kiln-harness-tuner-<run_id>.md`. The fallback shares the path — if the
report_writer hard-fails after the loop-level retry ladder, code renders the
report from `RunStatus` (hardcoded intro + the run history) and the guarantee
stays structural: a report always ships.

`ReportWriter` matches the `Ctx.write_report` seam: called with
(ctx, exit_reason) after the loop exits, returns the written path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from kiln_harness_tuner.items import LOOP_DISPATCH_BACKOFFS, call_with_retries
from kiln_harness_tuner.memory_schema import (
    BASELINE_AUDIT_TAG,
    BASELINE_JUDGE_COPY_TAG,
    ISSUE_TERMINAL_STATES,
)
from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    SUBSCRIPTION_LANE,
    IssueWithoutEval,
    ItemOutcome,
    ItemStatus,
    LatencyComparison,
    ReportInput,
    ReportPaths,
    RunStatus,
    ScreenResult,
    spend_lane_labels,
)
from kiln_harness_tuner.snapshots import snapshot_memory
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.ui import outcome_cell

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)

# The report_writer hard-fail row — wording refinable, guarantee structural.
FALLBACK_INTRO = (
    "Optimization completed, but the final report about the optimization "
    "failed to generate. The following experiments were run:"
)

# The spend appendix's fixed boilerplate — code-rendered
# so the report can never misstate what the lanes mean or what the books
# exclude; the agent narrates, never restates.
SPEND_APPENDIX_HEADING = "## Appendix: spend by source and lane (code-computed)"
# The lane column titles are `spend_lane_labels`, so the report
# and the terminal UI call the same money the same thing; the
# notes keep the wire lane name in parentheses, because that is what the
# `spend` events in `wave_log.jsonl` beside the report actually say.
SPEND_APPENDIX_NOTES = (
    "- **Kiln** $ (`provider`) = real money today (Kiln eval jobs: subject "
    "`task_run_usage` + judge `eval_usage`).",
    "- **Claude Code** $ (`subscription_equivalent`) = `claude -p` session "
    "cost (`total_cost_usd`) — quota burn now, real cost at deployment.",
    "- Excludes this report-writing session and any prior aborted runs.",
)


def spend_appendix_notes(agent_cli: str = "claude") -> tuple[str, ...]:
    """`SPEND_APPENDIX_NOTES`, with the agent lane's note for `agent_cli`."""
    if agent_cli == "claude":
        return SPEND_APPENDIX_NOTES
    label = spend_lane_labels(agent_cli)[SUBSCRIPTION_LANE]
    return (
        SPEND_APPENDIX_NOTES[0],
        f"- **{label}** $ (`subscription_equivalent`) = the cost each {label}"
        " session reports. A session that reports tokens only is counted in"
        " tokens below, not in dollars.",
        SPEND_APPENDIX_NOTES[2],
    )


def _fmt_usd(value: float) -> str:
    # Sub-cent money that would round to $0.00 renders as $<0.01 — displayed
    # cells must visibly reconcile with the totals they sum into.
    if 0 < value < 0.005:
        return "$<0.01"
    return f"${value:.2f}"


def _fmt_cell(value: float | None) -> str:
    return _fmt_usd(value) if value is not None else "—"


def _fmt_mins(secs: float) -> str:
    # Truncated, like `ui._fmt_duration`: a header that rounded up would read
    # `budget 120 min · elapsed 120 min` at 119m31s, claiming a budget was
    # spent that wasn't. Minutes elapsed is a floor everywhere else in the UI.
    return f"{int(max(secs, 0.0) // 60)} min"


def render_spend_header(status: RunStatus) -> str:
    """The appendix's first content line:
    `budget 120 min · elapsed 47 min · exit wrap_up`.

    A run review could not tell a 60-minute run from a 120-minute one —
    every time fact in the report was agent prose. All three come from
    `RunStatus`, and budget and elapsed share one unit so they can be read
    against each other (that comparison is the point; `ui._fmt_duration`'s
    `47m23s` would not serve it).

    Unknown facts are omitted rather than guessed, as the totals row omits an
    unknowable `left`: no `timeout_mins` means the run had no budget to
    state, and `exit_reason` is `None` only on a direct render (`ReportWriter`
    fills it before rendering).

    Known exclusion, the clock's twin of the appendix's spend one (`ui.totals_text`
    documents the same carve-out): `elapsed_secs` is
    frozen by the pre-report snapshot, so the report-writing session — a full
    agent session, minutes of it — is not in `elapsed`, exactly as its cost
    is not in the table. `budget 120 min · elapsed 118 min` is the wall clock
    up to the moment the report was commissioned, not to the moment it
    shipped. Counting it would mean rendering the header after the session
    that renders it."""
    segments = []
    if status.timeout_mins is not None:
        segments.append(f"budget {status.timeout_mins} min")
    if status.budget_usd is not None:  # --budget-usd
        segments.append(f"budget {_fmt_usd(status.budget_usd)}")
    segments.append(f"elapsed {_fmt_mins(status.elapsed_secs)}")
    if status.exit_reason is not None:
        segments.append(f"exit {status.exit_reason}")
    return " · ".join(segments)


def render_spend_appendix(status: RunStatus) -> str:
    """The code-computed per-source × per-lane spend table,
    under a code-computed budget/elapsed/exit header.
    Rows in fold (chronological) order; the two known lanes are always
    columns, any unexpected lane key gets its own column instead of vanishing
    (the fold never drops money)."""
    lanes = [SUBSCRIPTION_LANE, PROVIDER_LANE]
    lanes += sorted(
        {
            lane
            for fold in status.spend_by_source.values()
            for lane in fold
            if lane not in lanes
        }
    )
    lane_titles = spend_lane_labels(status.agent_cli)
    lines = [
        SPEND_APPENDIX_HEADING,
        "",
        # Under the heading, never above it: the appendix is concatenated
        # after a `---` rule, so a line before the heading floats between the
        # rule and the section it belongs to.
        render_spend_header(status),
        "",
        "| source | " + " | ".join(lane_titles.get(x, x) for x in lanes) + " | total |",
        "|---|" + "---:|" * (len(lanes) + 1),
    ]
    for source, fold in status.spend_by_source.items():
        cells = [_fmt_cell(fold.get(lane)) for lane in lanes]
        lines.append(
            f"| {source} | "
            + " | ".join(cells)
            + f" | {_fmt_usd(sum(fold.values()))} |"
        )
    totals = [
        sum(fold.get(lane, 0.0) for fold in status.spend_by_source.values())
        for lane in lanes
    ]
    lines.append(
        "| **total** | "
        + " | ".join(_fmt_usd(t) for t in totals)
        + f" | {_fmt_usd(sum(totals))} |"
    )
    # Budget honesty: the two numbers the table cannot carry.
    if status.evidence_spend_usd > status.spend_total_usd + 0.005:
        lines.append(
            f"- Evidence read this run: {_fmt_usd(status.evidence_spend_usd)} of "
            "stored EvalRuns stand behind the numbers above, cached rows paid "
            "for by earlier runs included; the table books only what this run "
            "spent."
        )
    if status.unmeasured_tokens:
        lines.append(
            f"- {status.unmeasured_tokens:,} tokens of agent sessions whose "
            "cost could not be read are NOT in the "
            f"{lane_titles[SUBSCRIPTION_LANE]} column: a "
            "session killed at its wall-clock cap never reports its cost, a "
            "Codex session reports tokens only, and so does an OpenCode "
            "session on a plan that prices nothing."
        )
    lines += ["", *spend_appendix_notes(status.agent_cli)]
    return "\n".join(lines) + "\n"


SPEED_APPENDIX_HEADING = "## Speed and cost (code-computed)"


def render_speed_appendix(status: RunStatus) -> str:
    """Speed beside quality. Under `--objective speed` (or when
    any gate measured speed) one row per val-side speed comparison: the
    paired drive pass, candidate vs the baseline of that gate. Empty string
    when there is nothing to show."""
    if status.objective not in ("speed", "cost") and not status.speed_results:
        return ""
    lines = [
        SPEED_APPENDIX_HEADING,
        "",
        f"Objective: **{status.objective}**. Latency is per-request wall-clock"
        " (`total_llm_latency_ms`) and cost is per-request provider cost of"
        " the task call, both paired on the same val items in the same"
        " window. A ratio below 1 is faster, or cheaper.",
        "",
    ]
    if not status.speed_results:
        lines.append("No promotion gate measured val speed in this run.")
        return "\n".join(lines) + "\n"
    lines += [
        "| wave | rc | n | median ms (base→cand) | ratio | p90 ms (base→cand)"
        " | p90 ratio [95%] | sign p | clearly faster | $/request (base→cand)"
        " | cost ratio | clearly cheaper |",
        "|---:|---|---:|---|---:|---|---|---:|---|---|---:|---|",
    ]
    for entry in status.speed_results:
        c = entry.get("latency") or {}
        if not c or c.get("median_ratio") is None:
            lines.append(
                f"| {entry.get('wave')} | {entry.get('rc_id')} | 0 | — | — | — | —"
                f" | — | no ({c.get('note') or 'unmeasured'}) |"
                + _cost_cells(entry.get("cost"))
            )
            continue
        ci = (
            f" [{c['p90_ratio_ci_low']:.2f}..{c['p90_ratio_ci_high']:.2f}]"
            if c.get("p90_ratio_ci_low") is not None
            and c.get("p90_ratio_ci_high") is not None
            else ""
        )
        lines.append(
            f"| {entry.get('wave')} | {entry.get('rc_id')} | {c['n_items']} |"
            f" {c['baseline_median_ms']:.0f}→{c['candidate_median_ms']:.0f} |"
            f" {c['median_ratio']:.2f} |"
            f" {c['baseline_p90_ms']:.0f}→{c['candidate_p90_ms']:.0f} |"
            f" {c['p90_ratio']:.2f}{ci} | {c['sign_test_p']:.3g} |"
            f" {'yes' if c.get('clearly_faster') else 'no'} |"
            + _cost_cells(entry.get("cost"))
        )
    return "\n".join(lines) + "\n"


DETECTABILITY_APPENDIX_HEADING = "## Detectability (code-computed)"


def _cell(value: object) -> str:
    return "—" if value is None else str(value)


def _share(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def render_detectability_appendix(status: RunStatus) -> str:
    """The pre-run detectability check and the `--noise-check`
    result. The val columns are VAL-GRADE: a split size on disk and an
    estimate from the train share — never a val result. Empty string when
    neither check ran."""
    detect = status.detectability
    noise = status.noise_check
    if detect is None and noise is None:
        return ""
    lines = [DETECTABILITY_APPENDIX_HEADING, ""]
    if detect is not None:
        lines += [
            f"Baseline `{detect.baseline_rc_id}`, objective **{detect.objective}**."
            f" A significant win (p < {detect.alpha:g}) needs at least"
            f" {detect.floor_binary} items fixed on a pass/fail score and"
            f" {detect.floor_rating} on a rating scale, all one way — the floor."
            " The best case is floor / n. The **val columns are val-grade**:"
            " `val n` is the split's size on disk, and `val room ≈` is an"
            " estimate at the train share with room (setup reads no val"
            " result). `interval` is the fewest wins at which the server's"
            " interval alone lies above zero; the check does not count it.",
            "",
            "| eval / score | type | train n | room | floor | best case"
            " | interval (train) | val n (val-grade) | val room ≈ (val-grade)"
            " | val best case (val-grade) | interval (val, val-grade) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in detect.rows:
            t = row.train
            lines.append(
                f"| {t.eval_id}/{t.score_key} | {t.score_type} | {t.n} | {t.room}"
                f" | {t.floor} | {_share(t.best_case_change)}"
                f" | {_cell(row.train_interval_wins)} | {_cell(row.val_n)}"
                f" | {_cell(row.val_room_estimate)}"
                f" | {_share(row.val_best_case_change)}"
                f" | {_cell(row.val_interval_best_case)} |"
            )
        if detect.min_effect is not None:
            lines += ["", f"`--min-effect`: {detect.min_effect:.0%}."]
        if detect.refusal:
            state = (
                "went on with `--allow-underpowered`"
                if detect.overridden
                else "the run stopped"
            )
            lines += ["", f"**Refused** ({state}):", "", "```"]
            lines += detect.refusal.splitlines()
            lines.append("```")
        if detect.warnings:
            lines += ["", "Warnings:", ""]
            lines += [f"- {w}" for w in detect.warnings]
        if detect.skipped:
            lines += ["", "Not assessed:", ""]
            lines += [f"- {s}" for s in detect.skipped]
    if noise is not None:
        lines += [
            "",
            f"**Run-to-run noise** (`--noise-check {noise.items_per_eval}`,"
            f" train items only): baseline `{noise.baseline_rc_id}`, clone"
            f" `{noise.clone_rc_id or '—'}`. Estimate ${noise.estimate_usd:.2f},"
            f" spent ${noise.spend_usd:.2f}.",
            "",
        ]
        if noise.skipped:
            lines.append(f"Skipped: {noise.skipped}")
        else:
            lines += [
                "| eval / score | items | re-drive changed | mean abs Δ"
                " | re-judge changed | mean abs Δ | needs about (train) |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
            for r in noise.rows:
                rejudge = (
                    f"{r.rejudge_changed}/{r.rejudge_items} ({_share(r.rejudge_rate)})"
                    if r.rejudge_changed is not None
                    else f"not measured: {r.rejudge_note}"
                )
                mean_r = (
                    f"{r.redrive_mean_abs_change:.2f}"
                    if r.redrive_mean_abs_change is not None
                    else "—"
                )
                mean_j = (
                    f"{r.rejudge_mean_abs_change:.2f}"
                    if r.rejudge_mean_abs_change is not None
                    else "—"
                )
                lines.append(
                    f"| {r.eval_id}/{r.score_key} | {r.items}"
                    f" | {r.redrive_changed}/{r.items} ({_share(r.redrive_rate)})"
                    f" | {mean_r} | {rejudge} | {mean_j}"
                    f" | {_cell(r.noise_adjusted_wins)} wins |"
                )
            if noise.warnings:
                lines += ["", "Warnings:", ""]
                lines += [f"- {w}" for w in noise.warnings]
    return "\n".join(lines) + "\n"


MODEL_PREFLIGHT_APPENDIX_HEADING = "## Model preflight (code-computed)"


def render_model_preflight_appendix(status: RunStatus) -> str:
    """Every model the model preflight checked, and the reason
    for each one it flagged (left out of screening and measurement) or left
    untested (nothing excluded). Empty string when the preflight did not
    run."""
    pre = status.model_preflight
    if pre is None:
        return ""
    tokens = (
        f"about {pre.estimated_prompt_tokens} tokens"
        if pre.estimated_prompt_tokens is not None
        else "not read"
    )
    lines = [
        MODEL_PREFLIGHT_APPENDIX_HEADING,
        "",
        f"Baseline `{pre.baseline_rc_id}`; its prompt and tools: {tokens}."
        f" Spent ${pre.spend_usd:.4f}.",
    ]
    if pre.skipped:
        lines += ["", f"Skipped: {pre.skipped}"]
    if pre.untested:
        lines += ["", f"{pre.untested} allowed model(s) not tested."]
    if pre.checks:
        lines += [
            "",
            "| model | calls | context window | result |",
            "|---|---:|---:|---|",
        ]
        for c in pre.checks:
            who = " (baseline)" if c.baseline else ""
            # A provider's text may carry a pipe, which would end the cell.
            reason = (c.reason or "").replace("|", "\\|")
            result = f"{c.outcome}: {reason}" if c.outcome != "ok" else "ok"
            lines.append(
                f"| {c.provider}/{c.model}{who} | {c.attempts}"
                f" | {_cell(c.context_window)} | {result} |"
            )
    return "\n".join(lines) + "\n"


STOP_APPENDIX_HEADING = "## Why each candidate stopped (code-computed)"

# A reader who sees "18 of 40 items" for an arm asks why it was
# not run in full. The engine stops an arm when a rule fires, and
# the report says which rule, in words a first-time reader follows.
# Key: the engine's stop kind (`schemas.StopKind`, plus `lever_not_applied`)
# or an outcome status. Value: how the arm ended, and why. The sentence is
# `<how>: <why> (<key>).`; a screening verdict uses `<why>` alone.
STOP_REASONS: dict[str, tuple[str, str]] = {
    "completed": ("Ran in full", "every planned train item was judged"),
    "futility": (
        "Stopped early",
        "it could not reach the bar with the items left, even if every one"
        " went its way",
    ),
    "eval_metric": (
        "Stopped early",
        "its quality was clearly below the bar (a significant loss)",
    ),
    "guard_losses": (
        "Stopped early",
        "it failed more of the baseline's passing items than noise explains",
    ),
    "residual": (
        "Stopped early",
        "on the hard items judged first, it fixed too few of its parent's"
        " failures or broke too many of its passes",
    ),
    "targets": ("Stopped early", "an item it was built to fix still failed"),
    "not_better": (
        "Stopped early",
        "it was not clearly cheaper or faster than the baseline",
    ),
    "latency_ratio": (
        "Stopped early",
        "it was clearly slower than the baseline, past the allowed ratio",
    ),
    "cost_ratio": (
        "Stopped early",
        "it cost clearly more per request than the baseline, past the allowed ratio",
    ),
    "invalid_output_rate": (
        "Stopped early",
        "too many of its outputs did not match the task's output format",
    ),
    "lever_not_applied": (
        "Stopped early",
        "its change did not reach the model call, so it cannot change a result",
    ),
    "max_usd": (
        "Stopped at its spending cap",
        "the result is inconclusive, not a loss",
    ),
    "run_budget": (
        "Stopped at the run's budget",
        "`--budget-usd` had no room for more items; the result is"
        " inconclusive, not a loss",
    ),
    "capped": (
        "Stopped at its spending cap",
        "the result is inconclusive, not a loss",
    ),
    "max_items": ("Stopped at its item limit", "the plan set the most items to judge"),
    "error": ("Stopped by an error", "the numbers say nothing about the change"),
    "cut_off": ("Cut off", "the run ended before this candidate finished"),
    "dead_end": (
        "Not measured",
        "the build, or a check before any train item was judged, ended it as"
        " a dead end",
    ),
    "not_landed": (
        "Not measured in full",
        "a trial on its target items left some failing",
    ),
    "skipped_not_reproduced": (
        "Not measured",
        "the issue did not reproduce on the baseline",
    ),
    "abandoned": ("Abandoned", "the wave stopped before this candidate finished"),
}


def _md(text: object) -> str:
    """A table cell: no pipe, no line break."""
    return " ".join(str(text).split()).replace("|", "\\|")


def stop_reason_text(code: str, detail: str = "", in_full: bool = False) -> str:
    """One plain sentence for a stop code. An unknown code prints raw.
    `in_full`: every planned item was judged, so a rule that fired did so on
    the last chunk — the arm was measured in full and then refused."""
    tail = f": {detail}" if detail else ""
    known = STOP_REASONS.get(code)
    if known is None:
        return f"Stopped by internal code `{code}`{tail} (no plain description)."
    how, why = known
    if in_full and how == "Stopped early":
        how = "Measured in full, then refused by a rule"
    return f"{how}: {why} ({code}{tail})."


def _judged_in_full(outcome: ItemOutcome | None) -> bool:
    m = outcome.measurement if outcome is not None else None
    if m is None or not m.planned_per_eval:
        return False
    return m.judged >= sum(m.planned_per_eval.values())


def stop_code(item: ItemStatus) -> tuple[str, str]:
    """(stop code, detail) for one arm: the engine's stop kind when a rule
    stopped it, else what its outcome says."""
    outcome = item.outcome
    if outcome is None:
        return "cut_off", f"still {item.live_state}"
    if outcome.status == "failed_infra":
        err = outcome.infra_error
        if err is None:
            return "error", ""
        if err.error_class == "timeout" and "deadline" in err.detail:
            return "cut_off", ""  # the orchestrator cancelled it in flight
        return "error", f"{err.error_class}, {err.detail[:160]}"
    m = outcome.measurement
    if m is not None:
        if m.status == "completed":
            return "completed", ""
        if m.stop_kind == "max_usd" and "run budget" in m.stop_line:
            return "run_budget", ""  # `measure.cap_run`, not the arm's own cap
        if m.stop_kind:
            return m.stop_kind, ""
        if m.status == "failed":
            return "error", ""
    return outcome.status, ""


def measured_extent(outcome: ItemOutcome | None) -> str:
    """In full or partly: the items judged of the items planned, per eval
    when there is more than one. An outcome written before the per-eval
    counts gives the total and the chunks."""
    m = outcome.measurement if outcome is not None else None
    if m is None:
        if outcome is not None and outcome.status == "completed":
            return "in full (no item counts recorded)"
        return "not measured"
    if m.planned_per_eval:
        planned = sum(m.planned_per_eval.values())
        word = (
            "not measured"
            if m.judged == 0
            else "in full"
            if m.judged >= planned
            else "partly"
        )
        text = f"{word}: {m.judged} of {planned} items"
        if len(m.planned_per_eval) > 1:
            text += (
                " ("
                + ", ".join(
                    f"`{e}` {m.judged_per_eval.get(e, 0)}/{n}"
                    for e, n in sorted(m.planned_per_eval.items())
                )
                + ")"
            )
        return text
    word = "in full" if m.status == "completed" else "partly"
    chunks = f", chunk {m.chunks} of {m.planned_chunks}" if m.planned_chunks else ""
    return f"{word}: {m.judged} items judged{chunks}"


def render_stop_reasons_appendix(status: RunStatus) -> str:
    """One row per experiment arm and merge item — why it
    stopped, in plain words, and how much of its train split was judged.
    Empty string when the run measured no arm."""
    rows = [
        (wave.wave, item)
        for wave in status.waves
        for item in wave.items
        if item.kind in ("fix", "merge")
    ]
    if not rows:
        return ""
    lines = [
        STOP_APPENDIX_HEADING,
        "",
        "Each arm is judged on its train split in chunks of items. After each"
        " chunk, code checks the stop rules. A rule that fires stops the arm."
        " Thus an arm that is measured only partly is normal, not a fault."
        " The rule's code is in parentheses, and the wave log has its numbers.",
        "",
        "| wave | item | rc | why it stopped | measured on train |",
        "|---:|---|---|---|---|",
    ]
    for wave, item in rows:
        code, detail = stop_code(item)
        rc = item.outcome.rc_id if item.outcome is not None else None
        lines.append(
            f"| {wave} | `{_md(item.item_id)}` {item.kind}"
            f" | {f'`{_md(rc)}`' if rc else '—'}"
            f" | {_md(stop_reason_text(code, detail, _judged_in_full(item.outcome)))}"
            f" | {_md(measured_extent(item.outcome))} |"
        )
    return "\n".join(lines) + "\n"


TRADEOFF_APPENDIX_HEADING = "## Screened candidates: trade-offs (code-computed)"


def _ratio(c: LatencyComparison | None) -> float | None:
    return c.median_ratio if c is not None else None


def _ratio_cell(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}×"


def screen_label(r: ScreenResult) -> str:
    """The candidate's label, with the model, provider and thinking level of
    its overrides when the label does not already name them."""
    base = r.label or r.candidate_id
    o = r.overrides
    parts = [
        "@".join(str(o[k]) for k in ("model_name", "model_provider_name") if o.get(k)),
        f"thinking {o['thinking_level']}" if o.get("thinking_level") else "",
    ]
    extra = [p for p in parts if p and p not in base]
    return base + (f" ({'; '.join(extra)})" if extra else "")


def _screen_verdict(r: ScreenResult) -> str:
    if r.verdict == "survivor":
        return f"survivor (rank {r.rank})" if r.rank else "survivor"
    if r.verdict == "capped":
        return "capped (inconclusive)"
    if r.verdict == "error":
        return "not screened (error)"
    known = STOP_REASONS.get(r.stop_kind or "")
    if known is not None:
        return f"dead end: {known[1]} ({r.stop_kind})"
    if r.stop_kind:
        return f"dead end (`{r.stop_kind}`)"
    return f"dead end: {r.reason[:160]}" if r.reason else "dead end"


def screen_sort_key(objective: str) -> Callable[[ScreenResult], tuple[float, ...]]:
    """Cheapest first under cost, fastest first under speed, the highest net
    change first under quality. A missing number sorts last."""
    inf = float("inf")

    def key(r: ScreenResult) -> tuple[float, ...]:
        cost = _ratio(r.cost)
        latency = _ratio(r.latency)
        cost_key = inf if cost is None else cost
        latency_key = inf if latency is None else latency
        if objective == "cost":
            return (cost_key, latency_key, r.broke)
        if objective == "speed":
            return (latency_key, cost_key, r.broke)
        return (inf if r.net is None else -r.net, r.broke, cost_key)

    return key


def _net_cell(r: ScreenResult) -> str:
    if r.net is None:
        return "—"
    if r.net_ci_low is None or r.net_ci_high is None:
        return f"{r.net:+.2f}"
    return f"{r.net:+.2f} [{r.net_ci_low:+.2f}..{r.net_ci_high:+.2f}]"


# The screened candidates the report agent sees: the rows the trade-off
# table lists first. The table that code appends has every row.
AGENT_SCREEN_ROWS = 12


def for_report_agent(status: RunStatus) -> RunStatus:
    """The run status the report agent reads. A long cost or speed run can
    screen hundreds of candidates, and the agent only names the first rows
    of a table that code renders, so it gets those rows without their
    per-eval counts."""
    if len(status.screen_results) <= AGENT_SCREEN_ROWS and not any(
        r.per_eval for r in status.screen_results
    ):
        return status
    first = sorted(status.screen_results, key=screen_sort_key(status.objective))
    slim = [r.model_copy(update={"per_eval": {}}) for r in first[:AGENT_SCREEN_ROWS]]
    return status.model_copy(update={"screen_results": slim})


def render_tradeoff_appendix(status: RunStatus) -> str:
    """One row per candidate this run screened — what it gave up
    on quality and what it saved in cost and latency, against its round's
    baseline on the same train items. A cost run that promoted nothing
    still found these prices. Reporting only: no promotion follows from it.
    Empty string on a quality run that screened nothing."""
    results = status.screen_results
    efficiency = status.objective in ("speed", "cost")
    if not results and not efficiency:
        return ""
    lines = [TRADEOFF_APPENDIX_HEADING, ""]
    if not results:
        lines.append("No screening round ran in this run.")
        return "\n".join(lines) + "\n"
    order = {"cost": "cheapest first", "speed": "fastest first"}.get(
        status.objective, "highest net change first"
    )
    lines += [
        f"Objective: **{status.objective}**, rows {order}. Each candidate is"
        " measured on train items against its round's baseline, on the same"
        " items. A ratio below 1 is cheaper, or faster. `lost` counts the"
        " baseline's passing items that the candidate failed. `fixed` counts"
        " the baseline's failing items that the candidate passed. The verdict"
        " applies the run's rules. An owner can accept a trade-off that the"
        " rules refused: the table shows each price. No promotion follows"
        " from this table.",
        "",
    ]
    net_col = not efficiency
    lines += [
        "| round | candidate | items | quality vs baseline | cost ratio | latency ratio"
        + (" | net [95%]" if net_col else "")
        + " | verdict | rc |",
        "|---:|---|---:|---|---:|---:|" + ("---|" if net_col else "") + "---|---|",
    ]
    for r in sorted(results, key=screen_sort_key(status.objective)):
        quality = (
            f"lost {r.broke} of {r.baseline_passed}, fixed {r.fixed}"
            if r.items
            else "not judged"
        )
        cells = [
            str(r.round_no or r.round),
            f"`{_md(r.candidate_id)}` {_md(screen_label(r))}",
            str(r.items),
            quality,
            _ratio_cell(_ratio(r.cost)),
            _ratio_cell(_ratio(r.latency)),
            *([_net_cell(r)] if net_col else []),
            _md(_screen_verdict(r)),
            f"`{r.rc_id}`" if r.rc_id else "—",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


MISSING_EVALS_APPENDIX_HEADING = "## Issues with no eval (code-computed)"
KILN_EVALS_DOCS_URL = "https://docs.kiln.tech/docs/evaluations"
# A bound on the report-time issue read on either path: a Kiln server that
# hangs must not hold the report back.
ISSUE_READ_TIMEOUT_SECS = 60.0


async def read_issues_without_eval(
    ctx: Ctx, *, retries: bool | None = None
) -> list[IssueWithoutEval] | None:
    """The open issues no eval binds, read from memory when the report is
    written, so the report also names the issues this run created. Closed
    issues are left out, and so are the baseline-audit issues: their own
    checks measure them (setup skips them for the same reason). None when
    the read fails; the section then uses what the run recorded."""
    try:
        issues = await asyncio.wait_for(
            ctx.memory.list_issues(retries=retries), ISSUE_READ_TIMEOUT_SECS
        )
    except Exception as exc:  # the report must ship without it
        logger.warning("report: the issue read failed: %r", exc)
        return None
    return [
        IssueWithoutEval(issue_memory_id=issue.id, overview=_md(issue.overview))
        for issue in issues
        if not issue.bound_eval_ids
        and issue.state not in ISSUE_TERMINAL_STATES
        and BASELINE_AUDIT_TAG not in issue.tags
        and BASELINE_JUDGE_COPY_TAG not in issue.tags
    ]


async def _add_issues_without_eval(
    ctx: Ctx, status: RunStatus, *, retries: bool | None = None
) -> None:
    """Both report paths: the report-time issue read, onto the snapshot the
    report renders from (and the report agent reads). Only while eval
    building is off — the section it feeds is empty otherwise."""
    if not status.eval_building:
        status.issues_without_eval = await read_issues_without_eval(
            ctx, retries=retries
        )


def render_missing_evals_appendix(status: RunStatus) -> str:
    """The open issues no eval can measure, while eval building is off:
    the loop cannot work on them, so the section tells the owner to create
    each eval in the Kiln app. The rows are the report-time read
    (`issues_without_eval`), each marked when code dropped a planned eval
    build for it. Without that read, the rows are the dropped items' issues
    and the ones setup found with no eval binding. Empty string when there
    are none, and with eval building on, where the loop builds the evals."""
    if status.eval_building:
        return ""
    dropped: dict[str, list[str]] = {}
    for entry in status.dropped_eval_items:
        dropped.setdefault(entry.issue_memory_id, []).append(
            f"`{entry.item_id}` in wave {entry.wave}"
        )
    if status.issues_without_eval is not None:
        rows = [(i.issue_memory_id, i.overview) for i in status.issues_without_eval]
    else:
        unbound = status.setup.unbound_unresolved_issue_ids if status.setup else []
        rows = [(issue_id, "") for issue_id in dict.fromkeys([*dropped, *unbound])]
    if not rows:
        return ""
    lines = [
        MISSING_EVALS_APPENDIX_HEADING,
        "",
        "No eval measures these issues, so the loop cannot work on them.",
        "",
    ]
    for issue_id, overview in rows:
        line = f"- `{issue_id}`"
        if overview:
            line += f": {overview}"
        if issue_id in dropped:
            line += (
                " — code dropped its planned eval build"
                f" ({', '.join(dropped[issue_id])})"
            )
        lines.append(line)
    lines += [
        "",
        "Eval building is experimental and off by default in this release, so"
        " the loop builds no eval itself. To measure an issue, create an eval"
        " for it in the Kiln app, then run the tuner again. The Kiln docs say"
        f" how: [Evals]({KILN_EVALS_DOCS_URL}).",
    ]
    return "\n".join(lines) + "\n"


def _cost_cells(c: object) -> str:
    if not isinstance(c, dict) or c.get("median_ratio") is None:
        return " — | — | — |"
    return (
        f" ${c['baseline_median_ms']:.4f}→${c['candidate_median_ms']:.4f} |"
        f" {c['median_ratio']:.2f} | {'yes' if c.get('clearly_faster') else 'no'} |"
    )


def _left_out_line(status: RunStatus) -> str:
    """The evals Kiln can't load, which the run left out, with Kiln's
    reason for each."""
    return "Left out: " + "; ".join(
        f'"{_md(e.name)}" (Kiln can\'t load it: {_md(e.reason)})'
        for e in status.unloadable_evals
    )


def _render_setup_section(status: RunStatus) -> list[str]:
    """The Setup section: what the pre-flight phase seeded,
    and — the half a human has to act on — the issues it refused to guess a
    binding for and the ones it flagged unmeasurable, and the evals Kiln
    can't load, which the run left out. Ids, not counts: those lists are a
    work queue.

    Dropped entirely on a warm run that did nothing, matching what the
    `kiln-harness-tuner-report` skill tells the agent to do on the other report path:
    "the setup phase changed nothing" is not news worth a heading."""
    setup = status.setup
    if setup is None:
        return []
    entries = [
        (label, ids)
        for label, ids in (
            ("issues seeded for evals", setup.seeded_eval_ids),
            ("issues re-synced for evals", setup.refreshed_eval_ids),
            ("issue bindings repaired", setup.repaired_issue_ids),
            ("issues flagged UNMEASURABLE", setup.unmeasurable_issue_ids),
            (
                "issues with no eval binding — needs a human",
                setup.unbound_unresolved_issue_ids,
            ),
        )
        if ids
    ]
    if not entries and not setup.baseline_rule and not status.unloadable_evals:
        return []
    lines = [
        "",
        "## Setup",
        "",
        f"- evals: {setup.evals_active} active of {setup.evals_total}",
    ]
    if status.unloadable_evals:
        lines.append(f"- {_left_out_line(status)}")
    if setup.baseline_rule:  # absent when a baseline record already existed
        lines.append(
            f"- baseline seeded: `{setup.baseline_rc_id}` (rule: {setup.baseline_rule})"
        )
    lines += [
        f"- {label}: {', '.join(f'`{i}`' for i in ids)}" for label, ids in entries
    ]
    return lines


def render_fallback_report(status: RunStatus) -> str:
    """Code-generated run history (waves, items, outcomes, promotions, spend)
    from `RunStatus` — the fallback body.

    The run's exit reason is NOT restated here: it is owned by the header
    line of the spend appendix (`render_spend_appendix`, appended to this
    body on the same path), alongside the budget and elapsed it is only
    readable against. The rule is: every run-level fact stated in
    exactly one place."""
    lines = [
        "# Kiln Harness Tuner run report (code-rendered fallback)",
        "",
        FALLBACK_INTRO,
        "",
        f"- project `{status.project_id}` · task `{status.task_id}`",
        f"- guidance: {status.guidance!r}",
        f"- total spend: ${status.spend_total_usd:.2f}",
        f"- baseline history: {' → '.join(status.baseline_history) or '(none)'}",
    ]
    if status.janitor_stamped:  # startup sweep
        lines.append(
            f"- janitor: stamped {len(status.janitor_stamped)} stale experiment "
            f"record(s) — ABANDONED ({', '.join(status.janitor_stamped)})"
        )
    if status.eval_job_error_items or status.val_fill_failures:
        # First-class incident accounting, so a reader need not hand-read
        # agent prose for it.
        classes = ", ".join(
            f"{key}: {count}"
            for key, count in sorted(status.eval_job_error_classes.items())
        )
        lines.append(
            f"- incidents: {status.eval_job_error_items} errored eval item(s)"
            f"{f' ({classes})' if classes else ''}, "
            f"{status.val_fill_failures} failed val fill(s) — see "
            "`eval_job_errors`/`val_fill_failed` events in wave_log.jsonl"
        )
    for block in status.gate_blocks:
        # A candidate the gate blocked for copying eval text.
        lines.append(f"- gate block: `{block.rc_id}` — {'; '.join(block.reasons)}")
    lines += _render_setup_section(status)
    for wave in status.waves:
        suffix = " (wrap-up)" if wave.should_wrap_up else ""
        lines += ["", f"## Wave {wave.wave}{suffix} — {wave.plan_headline}"]
        if wave.rejected_summary:
            lines.append(f"rejected: {wave.rejected_summary}")
        lines.append("")
        for item in wave.items:
            cell = (
                outcome_cell(item.outcome)
                if item.outcome is not None
                else f"unfinished ({item.live_state})"
            )
            lines.append(
                f"- `{item.item_id}` {item.kind} — {item.label}: {cell}"
                f" (${item.spend_usd:.2f})"
            )
            if item.outcome is not None and item.outcome.reference_findings:
                # What the measured run config names and does
                # not attach (`kiln_harness_tuner.reference_check`).
                lines.append(
                    "  - reference check: " + "; ".join(item.outcome.reference_findings)
                )
        if wave.promotion is not None:
            winner = wave.promotion.winner_rc_id
            lines.append(
                f"- promotion: {winner or 'no winner'} — {wave.promotion.rationale}"
            )
        if wave.promotion_skipped:  # Hard-fail noted in the report
            lines.append(f"- promotion skipped: {wave.promotion_skipped}")
        if wave.tripwire:
            lines.append(f"- tripwire: {wave.tripwire}")
    return "\n".join(lines) + "\n"


class ReportWriter:
    """The `Ctx.write_report` seam implementation: dispatch the report_writer
    agent on the loop-level ladder, then CODE writes the file."""

    def __init__(self, tracker: RunStatusTracker, run_id: str, reports_dir: Path):
        self._tracker = tracker
        self._run_id = run_id
        self._reports_dir = reports_dir

    @property
    def report_path(self) -> Path:
        return self._reports_dir / f"kiln-harness-tuner-{self._run_id}.md"

    def _write(self, markdown: str, status: RunStatus) -> str:
        markdown = markdown.rstrip("\n") + "\n\n---\n\n" + render_spend_appendix(status)
        speed = render_speed_appendix(status)
        if speed:
            markdown = markdown.rstrip("\n") + "\n\n" + speed
        detect = render_detectability_appendix(status)
        if detect:
            markdown = markdown.rstrip("\n") + "\n\n" + detect
        preflight = render_model_preflight_appendix(status)
        if preflight:
            markdown = markdown.rstrip("\n") + "\n\n" + preflight
        stops = render_stop_reasons_appendix(status)
        if stops:
            markdown = markdown.rstrip("\n") + "\n\n" + stops
        tradeoffs = render_tradeoff_appendix(status)
        if tradeoffs:
            markdown = markdown.rstrip("\n") + "\n\n" + tradeoffs
        missing_evals = render_missing_evals_appendix(status)
        if missing_evals:
            markdown = markdown.rstrip("\n") + "\n\n" + missing_evals
        path = self.report_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return str(path)

    async def write_fallback(self, ctx: Ctx, exit_reason: str) -> str:
        """The agent-free report path: render the
        code-only fallback from `RunStatus` and write it — no dispatch, no
        spend. Used on fatal and circuit-break exits, where the report agent
        may be unable to run (Kiln down) or running one is wrong (quota
        exhausted: the whole point is to stop spending). This path is why a run
        that got past preflight always ends with a report."""
        status = self._tracker.snapshot()
        if status.exit_reason is None:
            status.exit_reason = exit_reason
        # One attempt, no retry ladder: on a fatal exit Kiln may be what
        # failed, and the report must not wait on it.
        await _add_issues_without_eval(ctx, status, retries=False)
        path = self._write(render_fallback_report(status), status)
        ctx.log.event("report_written", path=path, fallback=True)
        return path

    async def __call__(self, ctx: Ctx, exit_reason: str) -> str:
        status = self._tracker.snapshot()
        if status.exit_reason is None:
            status.exit_reason = exit_reason
        # Report writing is an agent session after the exit banner — without
        # a phase it was the run's last silent stretch. The
        # phase starts AFTER the snapshot above, so the report never shows
        # its own phase.
        started = ctx.clock()
        ctx.log.event(
            "phase_start",
            name="reporting",
            label="Writing report… (a full agent session)",
        )
        # Past the run's deadline the report agent gets ONE
        # attempt — the five-rung loop ladder ran up to ~85 minutes after a
        # deadline that was supposed to be hard. The code fallback below
        # still ships a report either way.
        past_deadline = ctx.deadline is not None and ctx.clock() >= ctx.deadline
        await snapshot_memory(ctx, "report-before")  # the store at exit
        await _add_issues_without_eval(ctx, status)
        try:
            result = await call_with_retries(
                ctx.dispatch.report_writer,
                ReportInput(run_status=for_report_agent(status)),
                backoffs=() if past_deadline else LOOP_DISPATCH_BACKOFFS,
                sleep=ctx.sleep,
                log=ctx.log,
                source="report_writer",
            )
        finally:
            # try/finally: a dispatcher raise / Ctrl-C must not
            # leave a dangling phase_start in the audit trail.
            ctx.log.event(
                "phase_end", name="reporting", elapsed_secs=ctx.clock() - started
            )
        # Logged after the snapshot it reports on — the report's spend total
        # cannot include the report agent itself.
        ctx.log.event(
            "spend",
            source="report_writer",
            lane=SUBSCRIPTION_LANE,
            cost_usd=result.usage.cost_usd,
            unmeasured_tokens=result.usage.unmeasured_tokens,
        )
        # Conditioned on `result.data` directly (not a derived bool) so the
        # type checker narrows the Optional on the else branch.
        fallback = result.data is None
        markdown = (
            render_fallback_report(status)
            if result.data is None
            else result.data.markdown
        )
        # Code-computed spend appendix appended to BOTH
        # paths from the same pre-report snapshot the agent got — the agent
        # narrates spend, the table's numbers only ever come from code (and
        # the report session excludes itself by construction).
        paths = ReportPaths(report_path=self._write(markdown, status))
        ctx.log.event("report_written", path=paths.report_path, fallback=fallback)
        return paths.report_path
