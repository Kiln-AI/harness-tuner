"""An invented run for the real app's screenshots: the preview's story
(`../ui_preview/sample_data.py`), told as loop events.

A freight-depot desk agent, tuned for quality with a $50 budget and a 3-hour
limit, in wave 4. The events have the real emitters' field shapes: the
builders of `tests/tui/model_events.py` make them. Each public function
moves a new `Run` to one moment of the story. The numbers are made up, but
the sections agree with each other. Every id is in the invented 424242 band.
"""

from __future__ import annotations

from model_events import (
    START_RC,
    Run,
    comparison,
    detect_row,
    fix,
    merge,
    probe,
    row,
)

from kiln_harness_tuner.schemas import (
    CandidateValTable,
    ModelCheck,
    ModelPreflightResult,
    NoiseCheckResult,
    ProbeResult,
    PromotionDecision,
    ScreenResult,
)
from kiln_harness_tuner.tui.model.names import RcNames

# One run config per item that builds one: 424242, then the wave and the item
# in three digits each (w2-1 is 424242002001).
RC = {f"w{w}-{n}": f"424242{w:03d}{n:03d}" for w in range(1, 8) for n in range(1, 8)}
NAMES = {
    START_RC: "rc 0003",
    RC["w2-1"]: "refusal rule in prompt",
    RC["w3-4"]: "get_container wrapper",
    RC["w4-1"]: "hold fields wrapper",
    RC["w4-2"]: "refusal rule, 2xx bodies",
    RC["w4-4"]: "wrapper + refusal rule",
    RC["w6-1"]: "escalation skill",
    RC["w7-2"]: "wrapper + refusal rule + escalation skill",
}
RATING = {"score_key": "rating", "score_type": "five_star"}
EVALS = (
    detect_row("e1", "Refusal honesty", room=31),
    detect_row("e2", "Hold placement", room=38),
    detect_row("e3", "Output format", room=7),
    detect_row("e4", "Tone and policy", room=60, **RATING),
    detect_row("e5", "Escalation", room=47),
)
# Each val profile: the five evals, then cost per request and latency (ms).
START_VAL = (0.702, 0.630, 0.933, 4.08, 0.548, 0.042, 8400)
PROFILES = {
    START_RC: START_VAL,
    RC["w2-1"]: (0.779, 0.630, 0.934, 4.10, 0.552, 0.041, 8100),
    RC["w3-4"]: (0.798, 0.701, 0.935, 4.12, 0.560, 0.039, 7600),
    RC["w4-2"]: (0.876, 0.704, 0.934, 4.11, 0.562, 0.040, 7900),
    RC["w4-4"]: (0.820, 0.700, 0.930, 4.10, 0.555, 0.040, 7800),
    RC["w6-1"]: (0.875, 0.706, 0.940, 4.15, 0.608, 0.038, 7400),
    RC["w7-2"]: (0.882, 0.760, 0.950, 4.20, 0.631, 0.033, 7000),
}
HOLD = (
    "Return only the 6 hold fields from get_container, so the model stops"
    " missing hold_status in long tool output"
)
TABLE_EVALS = ("e1", "e2", "e3", "e4", "e5")


def names() -> RcNames:
    return RcNames(dict(NAMES))


def val_table(rc: str, parent: str) -> CandidateValTable:
    """`rc` against `parent` on every val item, from their profiles."""
    base, cand = PROFILES[parent], PROFILES.get(rc, PROFILES[parent])
    rows = [
        row(
            eval_id,
            base[n],
            cand[n],
            parent=parent,
            split="val",
            **(RATING if eval_id == "e4" else {}),
        )
        for n, eval_id in enumerate(TABLE_EVALS)
    ]
    return CandidateValTable(
        rc_id=rc,
        experiment_memory_id=f"exp-{rc}",
        per_eval=rows,
        cost=comparison(base[5], cand[5], metric="cost_usd"),
        latency=comparison(base[6], cand[6]),
    )


def _spend(run: Run, kiln: float, agents: float, item: str | None = None) -> None:
    fields = {"item_id": item} if item else {}
    run.emit("spend", source="eval", lane="provider", cost_usd=kiln, **fields)
    lane = "subscription_equivalent"
    run.emit("spend", source="agent", lane=lane, cost_usd=agents, **fields)


def _spend_each(run: Run, spends: dict[str, tuple[float, float]]) -> None:
    """Each item's own spend, (Kiln, agents): its card shows the total."""
    for item, (kiln, agents) in spends.items():
        _spend(run, kiln, agents, item)


def _cells(fixed: int, held: int, failing: int, broke: int):
    """Judged rows as (parent passed, candidate passes), mixed as a real
    queue mixes them."""
    pools = [
        [(False, True)] * fixed,
        [(True, True)] * held,
        [(False, False)] * failing,
        [(True, False)] * broke,
    ]
    out = []
    while any(pools):
        for pool in pools:
            if pool:
                out.append(pool.pop())
    return out


def build(run: Run, item: str) -> None:
    run.state(item, "building_rc")


def measure(run: Run, item: str, counts: tuple[int, int, int, int], planned=36):
    """Measuring, with `counts` (fixed, held, still failing, broke) judged in
    chunks of 12."""
    run.state(item, "measuring")
    cells = _cells(*counts)
    for start in range(0, len(cells), 12):
        run.chunk(item, RC[item], cells[start : start + 12])
    run.model.items[item].planned = planned


def land(run: Run, item: str, *rows, parent: str, successful: bool = True, **kw):
    """Analyzed, with its train rows against `parent`, and its verdict."""
    run.emit("measurement", item_id=item, rc_id=RC[item], status="completed")
    run.state(item, "analyzing")
    run.arm_results(item, RC[item], *rows, parent=parent, **kw)
    run.outcome(item, rc_id=RC[item], successful=successful)


def stop(run: Run, item: str, stop_kind: str) -> None:
    run.outcome(item, "not_landed", rc_id=RC[item], stop_kind=stop_kind)


def answer(run: Run, item: str, yes: bool) -> None:
    result = ProbeResult(answer="yes" if yes else "no", evidence="…", drives=3)
    run.emit("item_probe", item_id=item, probe=result.model_dump())
    run.outcome(item)


def open_gate(run: Run, wave: int, parent: str, *rcs: str) -> None:
    run.emit(
        "phase_start",
        name="promotion_gate",
        wave=wave,
        label="Promotion gate: measuring candidates on val…",
    )
    run.emit(
        "gate_fills_planned",
        baseline_rc_id=parent,
        rc_ids=list(rcs),
        eval_ids=list(TABLE_EVALS),
        fills=len(TABLE_EVALS) * (len(rcs) + 1),
    )


def fill(run: Run, rc: str, evals: int) -> None:
    for n in range(evals):
        run.emit(
            "gate_fill", rc_id=rc, eval_id=TABLE_EVALS[n], stored=104, expected=104
        )


def close_gate(run: Run, wave: int, parent: str, *rcs: str, winner: str | None):
    run.emit(
        "gate_val_tables",
        baseline_rc_id=parent,
        objective="quality",
        tables=[val_table(rc, parent).model_dump(mode="json") for rc in rcs],
    )
    decision = PromotionDecision(
        winner_rc_id=winner, winner_experiment_memory_id=None, rationale="the numbers"
    )
    run.emit("promotion", winner_rc_id=winner, rationale="", decision=decision)
    for rc in rcs:
        if rc != winner:
            run.emit("rejection_recorded", rc_id=rc)
    run.gate_end(wave)


def gate(run: Run, wave: int, parent: str, *rcs: str, winner: str | None) -> None:
    open_gate(run, wave, parent, *rcs)
    for rc in (parent, *rcs):
        fill(run, rc, len(TABLE_EVALS))
    close_gate(run, wave, parent, *rcs, winner=winner)


def screened(round_no: int, candidates: int, survivors: int) -> list[dict]:
    """A screening round's results: the first `survivors` survive."""
    models = ("haiku-5-5", "flash-4-2", "mini-5", "sonnet-5", "nano-5", "air-4")
    return [
        ScreenResult(
            candidate_id=f"s{round_no}-{n}",
            label=f"model swap to {models[n % len(models)]}",
            rc_id=f"424242{round_no:03d}5{n:02d}",
            round=round_no,
            evals=["e1", "e2"],
            items=16,
            verdict="survivor" if n < survivors else "dead_end",
        ).model_dump(mode="json")
        for n in range(candidates)
    ]


# ---- the story ----


def starting() -> Run:
    """Nine minutes in: setup done, two recon probes answered, one asking."""
    run = Run(names=names())
    run.at(0).emit(
        "run_start",
        timeout_mins=180,
        deadline=run.t + 180 * 60,
        deadline_iso=run.wall.replace(hour=17).isoformat(),
        budget_usd=50.0,
        objective="quality",
        model_policy=None,
    )
    run.model.agent_cli_checked(run.t - 38)
    run.emit("train_set", items=312)
    run.emit("quarantine", heldout_items=208)
    run.emit("phase_start", name="preflight")
    run.at(0.5).emit("phase_end", name="preflight")
    run.emit("phase_start", name="setup")
    run.at(1).emit(
        "setup_sync", evals_active=5, evals_total=5, issues_created=8, issues_updated=0
    )
    run.emit("baseline_found", rc_id=START_RC)
    run.emit("baseline_measured", evals={e: {"how": "stored"} for e in TABLE_EVALS})
    checks = [ModelCheck(provider="openrouter", model=f"m{n}") for n in range(8)]
    run.emit(
        "model_preflight",
        result=ModelPreflightResult(baseline_rc_id=START_RC, checks=checks),
    )
    run.at(2).detectability(
        *EVALS, warnings=["Output format can only guard: 4 failures, a win needs 6"]
    )
    run.emit(
        "noise_check",
        result=NoiseCheckResult(
            baseline_rc_id=START_RC, items_per_eval=0, skipped="no noise check"
        ),
    )
    run.emit(
        "memory_written",
        record_kind="lesson",
        op="create",
        overview="The agent reads hold_status from the first page of results",
    )
    run.at(3).wave_start(
        0,
        probe("r0-1", "Does the agent call get_container before it places a hold?"),
        probe("r0-2", "Does the agent treat a 2xx with an error body as a success?"),
        probe("r0-3", "Does haiku-5-5 follow a long escalation table without a skill?"),
        probe_phase=True,
        headline="reconnaissance — 3 probes",
    )
    for item in ("r0-1", "r0-2", "r0-3"):
        run.state(item, "probing")
    _spend(run, 0.1, 0.1, "r0-1")
    run.at(6)
    answer(run, "r0-1", yes=True)
    _spend(run, 0.1, 0.1, "r0-2")
    run.at(8)
    answer(run, "r0-2", yes=True)
    _spend(run, 0.0, 0.2, "r0-3")
    return run.at(9)


def _waves_one_to_three(run: Run) -> None:
    answer(run.at(9.5), "r0-3", yes=False)
    run.at(10).emit("wave_end", wave=0)
    # Wave 1: a stop, a no win, and a success the gate rejects.
    run.plan(1).at(12).planned(1)
    run.wave_start(
        1,
        fix("w1-1", "e1", technique="thinking", hypothesis="Think harder on refusals"),
        fix("w1-2", "e1", hypothesis="Name the refusal cases in the prompt"),
        fix("w1-3", "e2", hypothesis="Tell the model where hold_status sits"),
    )
    for item in ("w1-1", "w1-2", "w1-3"):
        build(run, item)
    run.at(15)
    measure(run, "w1-1", (1, 8, 2, 4))
    measure(run, "w1-2", (2, 20, 13, 1))
    measure(run, "w1-3", (5, 27, 4, 0))
    _spend_each(run, {"w1-1": (0.7, 0.5), "w1-2": (0.8, 0.4), "w1-3": (0.6, 0.5)})
    run.at(19)
    stop(run, "w1-1", "eval_metric")
    run.at(22)
    land(
        run,
        "w1-2",
        row("e1", 0.69, 0.70, ci=(-0.03, 0.05)),
        parent=START_RC,
        successful=False,
    )
    run.at(23)
    land(run, "w1-3", row("e2", 0.62, 0.68), parent=START_RC)
    run.at(24)
    gate(run, 1, START_RC, RC["w1-3"], winner=None)
    # Wave 2: the first promotion.
    run.at(28).plan(2).at(30).planned(2)
    run.wave_start(
        2,
        fix("w2-1", "e1", hypothesis="State the refusal rule once, up front"),
        fix("w2-3", "e5", technique="few-shot", hypothesis="Show two escalations"),
    )
    build(run, "w2-1")
    build(run, "w2-3")
    run.at(33)
    measure(run, "w2-1", (8, 26, 2, 0))
    measure(run, "w2-3", (1, 10, 9, 0))
    _spend_each(run, {"w2-1": (1.6, 1.0), "w2-3": (1.0, 0.8)})
    run.at(36)
    stop(run, "w2-3", "residual")
    run.at(38)
    land(run, "w2-1", row("e1", 0.69, 0.77), parent=START_RC)
    run.at(39)
    gate(run, 2, START_RC, RC["w2-1"], winner=RC["w2-1"])
    # Wave 3: a screening round first, then the second promotion.
    parent = RC["w2-1"]
    run.at(43).plan(3).at(45).planned(3)
    run.emit("queue_drawn", item_id=None, size=16)
    run.at(48).emit("screening_round", round=1, results=screened(1, 8, 2))
    run.wave_start(
        3,
        fix("w3-2", "e2", hypothesis="List the hold fields in the prompt"),
        fix("w3-4", "e2", technique="tool wrapper", hypothesis="Wrap get_container"),
        parent=parent,
    )
    build(run, "w3-2")
    build(run, "w3-4")
    run.at(52)
    measure(run, "w3-2", (6, 26, 4, 0))
    measure(run, "w3-4", (7, 27, 2, 0))
    _spend_each(run, {"w3-2": (1.4, 1.0), "w3-4": (1.6, 1.2)})
    run.at(58)
    land(run, "w3-2", row("e2", 0.62, 0.68, parent=parent), parent=parent)
    land(run, "w3-4", row("e2", 0.62, 0.69, parent=parent), parent=parent)
    run.at(59)
    gate(run, 3, parent, RC["w3-2"], RC["w3-4"], winner=RC["w3-4"])


WAVE_4 = (
    fix("w4-1", "e2", technique="tool wrapper", hypothesis=HOLD),
    fix(
        "w4-2",
        "e1",
        hypothesis="Treat a 2xx response with a refusal in the body as a refusal",
    ),
    fix(
        "w4-3",
        "e5",
        technique="skill",
        hypothesis="Move the escalation table into a skill the prompt names by name",
    ),
    merge("w4-4", RC["w2-1"], RC["w3-4"]),
    probe("w4-5", "Does haiku-5-5 load an attached skill under JSON output mode?"),
    fix(
        "w4-6",
        "e3",
        technique="thinking",
        hypothesis="Lower thinking keeps quality and cuts cost",
    ),
    fix(
        "w4-7",
        "e4",
        technique="skill",
        hypothesis=(
            "Add a tone guide skill with three examples of a firm, polite refusal"
        ),
    ),
)


def running(*, wrap_up: bool = False) -> Run:
    """Wave 4, 84 minutes in: a card in each state."""
    run = starting()
    _waves_one_to_three(run)
    parent = RC["w3-4"]
    run.at(63).plan(4, wrap_up=wrap_up).at(66).planned(4)
    run.emit(
        "open_questions",
        questions=[
            {
                "constraint_id": "c1",
                "question": "Does JSON output mode stay on?",
                "default_assumed": "JSON output mode stays on",
            }
        ],
    )
    run.wave_start(4, *WAVE_4, parent=parent, wrap_up=wrap_up)
    for item in ("w4-1", "w4-2", "w4-3", "w4-4", "w4-6", "w4-7"):
        build(run, item)
    run.state("w4-5", "probing")
    run.at(69).emit(
        "item_change", item_id="w4-1", change={"summary": "adds get_container_for_hold"}
    )
    measure(run, "w4-1", (5, 31, 0, 0))
    measure(run, "w4-2", (9, 26, 1, 0))
    measure(run, "w4-6", (1, 8, 2, 1))
    measure(run, "w4-4", (6, 108, 5, 1), planned=120)
    _spend_each(
        run,
        {
            "w4-1": (1.4, 0.6),
            "w4-2": (1.4, 0.7),
            "w4-6": (0.4, 0.3),
            "w4-4": (1.6, 0.9),
        },
    )
    run.at(73)
    stop(run, "w4-6", "futility")
    run.at(74).emit("judge_copy", item_id="w4-7", rc_id=RC["w4-7"], status="copied")
    build(run, "w4-7")
    run.at(76)
    answer(run, "w4-5", yes=True)
    run.at(78)
    measure(run, "w4-3", (2, 8, 1, 1), planned=24)
    run.state("w4-4", "analyzing")
    run.emit(
        "eval_job_errors", errors=[{}] * 6, split="train", error_class="rate_limit"
    )
    run.at(79)
    land(
        run,
        "w4-2",
        row("e1", 0.80, 0.89, parent=parent),
        parent=parent,
        cost=comparison(0.039, 0.040, metric="cost_usd"),
        latency=comparison(7600, 7900),
    )
    run.at(80)
    land(
        run,
        "w4-1",
        row("e2", 0.70, 0.75, parent=parent, ci=(-0.01, 0.09)),
        parent=parent,
        cost=comparison(0.039, 0.033, metric="cost_usd"),
        latency=comparison(7600, 6500),
    )
    run.emit(
        "memory_written",
        record_kind="lesson",
        op="create",
        overview="hold_status is lost in long tool output",
    )
    _spend_each(
        run,
        {
            "w4-1": (0.2, 0.1),
            "w4-2": (0.2, 0.1),
            "w4-3": (0.6, 0.5),
            "w4-4": (0.4, 0.2),
            "w4-5": (0.2, 0.0),
            "w4-7": (0.2, 0.2),
        },
    )
    return run.at(84)


def gate_open() -> Run:
    """The wave 4 gate, three minutes in: two candidates on val."""
    run = running()
    parent = RC["w3-4"]
    run.at(85)
    land(run, "w4-4", row("e1", 0.80, 0.86, parent=parent), parent=parent)
    land(
        run,
        "w4-3",
        row("e5", 0.55, 0.56, ci=(-0.04, 0.06)),
        parent=parent,
        successful=False,
    )
    measure(run, "w4-7", (1, 6, 1, 4))
    stop(run, "w4-7", "guard_losses")
    run.at(87)
    open_gate(run, 4, parent, RC["w4-1"], RC["w4-2"], RC["w4-4"])
    fill(run, parent, 5)
    fill(run, RC["w4-1"], 4)
    fill(run, RC["w4-2"], 3)
    fill(run, RC["w4-4"], 2)
    _spend(run, 1.2, 0.3)
    return run.at(90)


def planning() -> Run:
    """Wave 5's planner, after the wave 4 gate promoted w4-2."""
    run = gate_open()
    parent = RC["w3-4"]
    run.at(92)
    for rc in (RC["w4-1"], RC["w4-2"], RC["w4-4"]):
        fill(run, rc, 5)
    close_gate(run, 4, parent, RC["w4-1"], RC["w4-2"], RC["w4-4"], winner=RC["w4-2"])
    run.at(93).plan(5)
    run.at(96).emit("phase_end", name="planning", wave=5, elapsed_secs=160)
    run.emit("queue_drawn", item_id=None, size=16)
    for n in range(6):
        run.emit("candidate_budget", item_id=None, rc_id=f"4242420005{n:02d}")
    return run.at(97)


def done() -> Run:
    """The end: three more waves, the report, and the time limit."""
    run = planning()
    run.at(99).emit("screening_round", round=2, results=screened(2, 6, 3))
    run.plan(5, replan=True).at(101).planned(5)
    parent = RC["w4-2"]
    run.wave_start(
        5,
        fix("w5-1", "e1", technique="model", hypothesis="Swap to a cheaper model"),
        parent=parent,
    )
    build(run, "w5-1")
    run.at(105)
    measure(run, "w5-1", (0, 10, 2, 5))
    _spend_each(run, {"w5-1": (0.8, 0.5)})
    run.at(110)
    stop(run, "w5-1", "eval_metric")
    run.at(112).emit("wave_end", wave=5)
    run.at(115).plan(6).at(117).planned(6)
    run.wave_start(
        6,
        fix("w6-1", "e5", technique="skill", hypothesis="Escalation table as a skill"),
        parent=parent,
    )
    build(run, "w6-1")
    run.at(122)
    measure(run, "w6-1", (8, 25, 3, 0))
    _spend_each(run, {"w6-1": (2.4, 1.1)})
    _spend(run, 3.6, 1.4)  # the screening round before the wave
    run.at(130)
    land(run, "w6-1", row("e5", 0.55, 0.63, parent=parent), parent=parent)
    run.at(131)
    gate(run, 6, parent, RC["w6-1"], winner=RC["w6-1"])
    run.at(137).plan(7, wrap_up=True).at(140).planned(7)
    parent = RC["w6-1"]
    run.wave_start(
        7, merge("w7-2", RC["w4-1"], RC["w6-1"]), parent=parent, wrap_up=True
    )
    build(run, "w7-2")
    run.at(145)
    measure(run, "w7-2", (9, 108, 3, 0), planned=120)
    _spend_each(run, {"w7-2": (3.0, 1.3)})
    _spend(run, 2.0, 0.8)  # the gate's val fills
    run.at(154)
    land(run, "w7-2", row("e2", 0.70, 0.76, parent=parent), parent=parent)
    run.at(156)
    gate(run, 7, parent, RC["w7-2"], winner=RC["w7-2"])
    run.at(161).emit("exit", reason="timeout")
    run.emit("phase_start", name="reporting")
    _spend(run, 0.4, 1.6)
    run.at(176).emit("phase_end", name="reporting", elapsed_secs=900)
    run.emit(
        "report_written",
        path="~/.kiln_ai/harness_tuner/reports/kiln-harness-tuner-261109-1412.md",
        fallback=False,
    )
    return run.at(177)
