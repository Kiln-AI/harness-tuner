"""The Results section from the run's events: the concerns, and the Start,
Best Confirmed and Best Unconfirmed tiers (functional spec 7.5 and 16)."""

from dataclasses import replace

import pytest
from model_events import (
    INFO,
    START_RC,
    Run,
    comparison,
    detect_row,
    fix,
    row,
    table,
    val_row,
)

from kiln_harness_tuner.schemas import LatencyComparison, ScreenResult
from kiln_harness_tuner.tui.model import results
from kiln_harness_tuner.tui.model.state import plain


def titles(run: Run) -> list[tuple[str, str]]:
    return [(c.title, c.qualifier) for c in run.model.results().cards]


def test_concerns_follow_the_detectability_rows_then_cost_and_latency():
    run = Run().start()
    run.detectability(
        detect_row("e1", "Hold placement"),
        detect_row("e3", "Informational", driving=False),
        detect_row("e4", "Tone", score_key="accuracy"),
        detect_row("e4", "Tone", score_key="tone", score_type="five_star"),
    )
    # "Eval: " says what the card is. The score key shows only for an eval
    # with two or more cards, and no " · " parts the muted words.
    assert titles(run) == [
        ("Eval: Hold placement", ""),
        ("Eval: Tone", "accuracy"),
        ("Eval: Tone", "tone 1–5"),
        ("Cost / request", ""),
        ("Latency", "median"),
    ]


def test_a_rating_card_with_one_score_shows_only_the_scale():
    run = Run().start()
    run.detectability(
        detect_row("e4", "Tone", score_key="tone", score_type="five_star"),
        detect_row("e5", "Escalation", score_key="a"),
        detect_row("e5", "Escalation", score_key="b", driving=False),
    )
    # Escalation has two scores, but only one can decide: one card, no key.
    assert titles(run)[:2] == [("Eval: Tone", "1–5"), ("Eval: Escalation", "")]


def test_an_eval_first_named_mid_run_is_added_before_cost():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e9"))
    run.emit(
        "arm_estimates",
        item_id="w1-1",
        rc_id="rc_a",
        per_eval=[{"eval_id": "e9", "eval_name": "Late escalations"}],
    )
    run.success("w1-1", "rc_a", row("e9", 0.5, 0.6))
    assert [t for t, _ in titles(run)] == [
        "Eval: Hold placement",
        "Eval: Refusal honesty",
        "Eval: Late escalations",
        "Cost / request",
        "Latency",
    ]
    # Its card names it too.
    assert run.card("w1-1").qualifier == "target: Late escalations"


def test_start_is_train_until_the_first_gate_then_val():
    run = Run().standard_start()
    run.detectability(
        detect_row("e1", "Hold placement", room=40),
        detect_row("e2", "Refusal honesty", room=20),
        detect_row("e5", "Tone", score_type="five_star", room=60),
    )
    key = run.model.results().key
    assert plain(key[0]) == "■ Start · train · starting point"
    assert plain(key[1]) == "■ Best Confirmed · no promotion yet"
    assert run.concern("Hold placement").start.value == "60.0%"
    assert run.concern("Refusal honesty").start.value == "80.0%"
    # Rating, cost and latency come from the first arm measured against
    # Start (16.6).
    for title in ("Tone", "Cost / request", "Latency"):
        assert run.concern(title).start is None
        assert run.concern(title).start_note == "not measured yet"

    run.wave_start(1, fix("w1-1", "e5"))
    run.success(
        "w1-1",
        "rc_a",
        row("e5", 3.9, 4.1, score_type="five_star"),
        latency=comparison(8400.0, 7900.0),
        cost=comparison(0.042, 0.040, metric="cost_usd"),
    )
    assert run.concern("Tone").start.value == "3.90"
    assert run.concern("Latency").start.value == "8.4s"
    assert run.concern("Cost / request").start.value == "$0.042"

    run.gate(1, table("rc_a", val_row("e1", 0.62, 0.62)), winner=None)
    assert plain(run.model.results().key[0]) == "■ Start · val · starting point"
    assert run.concern("Hold placement").start.value == "62.0%"


def test_a_rating_from_an_arm_on_another_parent_is_not_start():
    run = Run().standard_start()
    run.detectability(detect_row("e5", "Tone", score_type="five_star"))
    run.wave_start(1, fix("w1-1", "e5"))
    run.success(
        "w1-1",
        "rc_a",
        row("e5", 4.4, 4.5, score_type="five_star", parent="rc_x"),
        parent="rc_x",
    )
    assert run.concern("Tone").start is None


def promoted_run(*, blocked: bool = False) -> Run:
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"), fix("w1-2", "e2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.success("w1-2", "rc_b", row("e2", 0.8, 0.85))
    run.gate(
        1,
        table("rc_a", val_row("e1", 0.62, 0.70), val_row("e2", 0.80, 0.78)),
        table("rc_b", val_row("e1", 0.62, 0.60), val_row("e2", 0.80, 0.84)),
        winner="rc_a",
        blocked={"rc_a": ["integrity checks did not run on the winner"]}
        if blocked
        else None,
        end=False,
    )
    return run


def test_a_promotion_is_confirmed_only_when_the_gate_ends_unblocked():
    run = promoted_run()
    # `promotion` arrives before `_apply_checked` can still refuse it.
    assert run.model.confirmed_rc is None
    assert run.card("w1-1").steps[-1] == ("gate", "current")
    run.gate_end(1)
    assert run.model.confirmed_rc == "rc_a"
    assert plain(run.model.results().key[1]) == "■ Best Confirmed · val · w1-1"
    assert run.line5("w1-1") == "◆ Promoted · now Best Confirmed"
    assert run.card("w1-1").steps[-1] == ("gate", "done")
    # The gate picked w1-1: w1-2 is not a failure.
    assert run.line5("w1-2") == "○ Not selected · w1-1 promoted"
    assert run.card("w1-2").line5[0].tone == "warning"
    assert run.card("w1-2").steps[-1] == ("gate", "warned")


def test_a_blocked_winner_is_not_best_confirmed():
    run = promoted_run(blocked=True)
    run.gate_end(1)
    assert run.model.confirmed_rc is None
    assert plain(run.model.results().key[1]) == "■ Best Confirmed · no promotion yet"
    assert run.line5("w1-1") == "✗ Blocked at the gate"
    # No rejection is written when the winner is refused: the rest stay
    # pooled.
    assert run.line5("w1-2") == "· Not judged: stays pooled"
    assert "rc_b" not in run.model.rejected


def test_best_confirmed_change_is_colored_by_direction_only():
    run = promoted_run()
    run.gate_end(1)
    hold = run.concern("Hold placement").confirmed
    assert (hold.value, hold.change, hold.change_tone) == ("70.0%", "+8.0", "success")
    refusal = run.concern("Refusal honesty").confirmed
    assert (refusal.value, refusal.change, refusal.change_tone) == (
        "78.0%",
        "−2.0",
        "error",
    )


def test_best_unconfirmed_counts_only_successful_arms_that_beat_the_parent():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"), fix("w1-2", "e1"), fix("w1-3", "e2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    # A bigger change on an arm that did not win does not count.
    run.state("w1-2", "analyzing")
    run.arm_results("w1-2", "rc_b", row("e1", 0.6, 0.9))
    run.outcome("w1-2", rc_id="rc_b", successful=False)
    # A change that does not beat the parent is not shown.
    run.success("w1-3", "rc_c", row("e2", 0.8, 0.75, ci=(-0.1, 0.0)))

    hold = run.concern("Hold placement")
    assert (hold.unconfirmed.value, hold.unconfirmed.change) == ("70.0%", "+10.0")
    assert hold.unconfirmed.change_tone == "success"
    assert plain(hold.subtitle) == "w1-1"
    assert run.concern("Refusal honesty").unconfirmed is None


def test_best_unconfirmed_stays_inside_its_scale():
    # A train change against a parent on another split can pass the scale's
    # end when it is added to Start: the estimate stops at the end.
    run = Run().standard_start()
    run.detectability(detect_row("e5", "Tone", score_type="five_star"))
    run.wave_start(1, fix("w1-1", "e1"), fix("w1-2", "e5"), fix("w1-3", "e5"))
    run.success("w1-1", "rc_a", row("e1", 0.3, 0.8, parent="rc_x"))
    hold = run.concern("Hold placement")
    assert (hold.start.value, hold.unconfirmed.value) == ("60.0%", "100.0%")
    assert (hold.unconfirmed.change, hold.unconfirmed.fraction) == ("+40.0", 1.0)
    rating = {"score_type": "five_star"}
    run.success("w1-2", "rc_b", row("e5", 4.5, 4.6, **rating))
    run.success("w1-3", "rc_c", row("e5", 2.0, 4.0, parent="rc_x", **rating))
    assert run.concern("Tone").unconfirmed.value == "5.00"


def test_each_scale_has_its_own_ends():
    assert results.on_scale("pass", -0.1) == 0.0
    assert results.on_scale("pass", 1.07) == 1.0
    assert results.on_scale("rating", 0.4) == 1.0
    assert results.on_scale("rating", 5.3) == 5.0
    assert results.on_scale("cost", -0.002) == 0.0
    assert results.on_scale("latency", 9000.0) == 9000.0


def test_best_unconfirmed_is_dim_when_its_interval_crosses_zero():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.65, ci=(-0.02, 0.12)))
    assert run.concern("Hold placement").unconfirmed.change_tone == "muted"


def test_screen_survivors_count_for_cost_and_latency_only():
    run = Run().standard_start()
    run.emit("phase_start", name="planning", wave=1, label="Planning wave 1…")
    run.emit("phase_end", name="planning", wave=1, elapsed_secs=60.0)
    result = ScreenResult(
        candidate_id="s1-1",
        label="haiku, thinking low",
        rc_id="rc_s",
        round=1,
        evals=["e1"],
        items=16,
        verdict="survivor",
        cost=comparison(0.040, 0.030, metric="cost_usd", clear=True),
        latency=comparison(8000.0, 9000.0),
    )
    run.emit("screening_round", round=1, line="", results=[result.model_dump()])
    run.wave_start(1, fix("w1-1", "e1"))
    run.success(
        "w1-1",
        "rc_a",
        row("e1", 0.6, 0.65),
        cost=comparison(0.040, 0.044, metric="cost_usd"),
        latency=comparison(8000.0, 7000.0),
    )
    cost = run.concern("Cost / request")
    assert (cost.unconfirmed.value, cost.unconfirmed.change) == ("$0.030", "−25%")
    assert cost.unconfirmed.change_tone == "success"
    assert plain(cost.subtitle) == "rc_s"
    # The survivor was slower: the arm holds latency, dim (not clearly faster).
    latency = run.concern("Latency")
    assert (latency.unconfirmed.value, latency.unconfirmed.change) == ("7.0s", "−12%")
    assert latency.unconfirmed.change_tone == "muted"
    assert plain(latency.subtitle) == "w1-1"


def test_best_unconfirmed_clears_at_a_confirmed_promotion():
    run = promoted_run()
    assert run.concern("Refusal honesty").unconfirmed is not None
    run.gate_end(1)
    for card in run.model.results().cards:
        assert card.unconfirmed is None
    # The next wave's arms branch from Best Confirmed: its value is the parent.
    run.plan(2).planned(2)
    run.wave_start(2, fix("w2-1", "e1"), parent="rc_a")
    run.success("w2-1", "rc_d", row("e1", 0.65, 0.75, parent="rc_a"))
    assert run.concern("Hold placement").unconfirmed.value == "80.0%"


def test_an_rc_rejected_at_val_drops_out():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    assert run.concern("Hold placement").unconfirmed is not None
    run.gate(1, table("rc_a", val_row("e1", 0.62, 0.62)), winner=None)
    assert run.concern("Hold placement").unconfirmed is None
    assert "Promotion gate: no winner" in run.log()


def test_lower_is_better_concerns_compare_the_right_way():
    run = Run().start()
    run.detectability(
        detect_row("e6", "False alarms", direction="lower_is_better", room=30)
    )
    # 30 of 100 items are not at the best value (0): the raw rate is 30%.
    assert run.concern("False alarms").start.value == "30.0%"
    run.wave_start(1, fix("w1-1", "e6"))
    run.success(
        "w1-1", "rc_a", row("e6", 0.3, 0.2, direction="lower_is_better", ci=(0.02, 0.2))
    )
    unconfirmed = run.concern("False alarms").unconfirmed
    assert (unconfirmed.value, unconfirmed.change) == ("20.0%", "−10.0")
    assert unconfirmed.change_tone == "success"


def test_in_flight_counts_live_items_on_the_eval_and_none_for_cost_and_latency():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"), fix("w1-2", "e1"), fix("w1-3", "e2"))
    run.state("w1-1", "measuring").state("w1-2", "building_rc")
    hold = run.concern("Hold placement")
    assert hold.in_flight == 2
    assert plain(hold.subtitle) == " 2 in flight"
    assert hold.subtitle[0].tone == "spinner"
    # A queued item is not in flight.
    assert run.concern("Refusal honesty").in_flight == 0
    assert run.concern("Cost / request").in_flight is None
    assert run.concern("Latency").in_flight is None


def test_compare_rc_ids_are_start_confirmed_then_unconfirmed_without_repeats():
    run = Run().standard_start()
    assert run.model.results().compare_rc_ids == (START_RC,)
    run.wave_start(1, fix("w1-1", "e1"), fix("w1-2", "e2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7), cost=comparison(0.04, 0.03))
    run.success("w1-2", "rc_b", row("e2", 0.8, 0.9))
    assert run.model.results().compare_rc_ids == (START_RC, "rc_a", "rc_b")
    run.gate(
        1,
        table("rc_a", val_row("e1", 0.6, 0.7), val_row("e2", 0.8, 0.8)),
        winner="rc_a",
    )
    assert run.model.results().compare_rc_ids == (START_RC, "rc_a")
    url = run.model.compare_url()
    assert url.endswith(f"?models={START_RC},rc_a&columns=2&from=optimize")


# ---- what keeps counting for Best Unconfirmed ----


def survivor_run() -> Run:
    """Under cost: a screen survivor `rc_s` at 75% of Start's cost, then
    arm w1-2, committed on `rc_s`, measuring it in full."""
    run = Run(replace(INFO, objective="cost")).standard_start(objective="cost")
    run.at(10).plan(1).planned(1)
    survivor = ScreenResult(
        candidate_id="s1-1",
        label="haiku, thinking low",
        rc_id="rc_s",
        round=1,
        evals=["e1"],
        items=16,
        verdict="survivor",
        cost=comparison(0.040, 0.030, metric="cost_usd", clear=True),
    )
    run.emit("screening_round", round=1, line="", results=[survivor.model_dump()])
    run.wave_start(1, fix("w1-1"), fix("w1-2"))
    # Start's cost comes from an arm against Start that does not beat it.
    run.success("w1-1", "rc_a", row(), cost=comparison(0.040, 0.041, metric="cost_usd"))
    cost = run.concern("Cost / request")
    assert (cost.unconfirmed.value, plain(cost.subtitle)) == ("$0.030", "rc_s")
    run.state("w1-2", "measuring")
    run.emit("candidate_budget", item_id="w1-2", rc_id="rc_s", usd=4.0)
    return run


def test_a_survivor_estimate_stops_counting_while_an_arm_measures_its_config():
    run = survivor_run()
    assert run.concern("Cost / request").unconfirmed is None


def test_a_survivor_whose_arm_stops_no_longer_counts():
    run = survivor_run()
    run.outcome("w1-2", "not_landed", rc_id="rc_s", stop_kind="eval_metric")
    assert run.line5("w1-2") == "✗ Stopped: quality clearly worse"
    assert run.concern("Cost / request").unconfirmed is None


def test_a_survivor_whose_arm_succeeds_counts_where_the_arm_did_not_measure():
    run = survivor_run()
    run.state("w1-2", "analyzing")
    run.arm_results("w1-2", "rc_s", row("e1", 0.6, 0.62))
    run.outcome("w1-2", rc_id="rc_s", successful=True)
    cost = run.concern("Cost / request")
    assert (cost.unconfirmed.value, plain(cost.subtitle)) == ("$0.030", "w1-2")


def test_the_arm_s_full_measurement_replaces_the_screen_estimate():
    run = survivor_run()
    run.state("w1-2", "analyzing")
    # On the full train split the config saves 5%, not the screen's 25%.
    run.arm_results(
        "w1-2",
        "rc_s",
        row("e1", 0.6, 0.62),
        cost=comparison(0.040, 0.038, metric="cost_usd", clear=True),
    )
    run.outcome("w1-2", rc_id="rc_s", successful=True)
    cost = run.concern("Cost / request")
    assert (cost.unconfirmed.value, cost.unconfirmed.change) == ("$0.038", "−5%")
    assert plain(cost.subtitle) == "w1-2"


def gate_with_block(reasons: list[str]) -> Run:
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.gate(
        1,
        table("rc_a", val_row("e1", 0.62, 0.70)),
        winner="rc_a",
        blocked={"rc_a": reasons},
    )
    return run


def test_a_config_the_gate_blocked_drops_out():
    run = gate_with_block(["integrity flags not addressed in the rationale"])
    assert run.line5("w1-1") == "✗ Blocked at the gate"
    assert run.concern("Hold placement").unconfirmed is None


def test_a_config_whose_val_evidence_has_holes_stays_pooled_and_counts():
    run = gate_with_block(["val evidence incomplete — stays pooled"])
    assert run.line5("w1-1") == "· Not judged: stays pooled"
    assert run.card("w1-1").steps[-1] == ("gate", "later")
    assert run.log()[-1] == (
        "w1-1 not judged at the gate: val evidence incomplete — stays pooled"
    )
    # It still counts, now on Start's val value (62%) plus its train +10.
    assert run.concern("Hold placement").unconfirmed.value == "72.0%"


def test_a_config_that_copies_eval_text_drops_out():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.gate(1, table("rc_a", val_row("e1", 0.62, 0.70)), winner=None, end=False)
    run.emit("gate_block", rc_id="rc_a", exp_mem_id="exp", reasons=["copies"])
    run.gate_end(1)
    assert run.line5("w1-1") == "✗ Blocked: copies eval text"
    assert run.concern("Hold placement").unconfirmed is None


def test_a_config_the_model_policy_refused_drops_out():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.emit("promotion_blocked", winner_rc_id="rc_a", reasons=["outside the policy"])
    assert run.concern("Hold placement").unconfirmed is None


def test_start_keeps_its_train_value_where_the_first_gate_has_none():
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success(
        "w1-1",
        "rc_a",
        row("e1", 0.6, 0.7),
        cost=comparison(0.042, 0.040, metric="cost_usd"),
    )
    # The val rows carry no cost comparison, and no row for e2.
    run.gate(1, table("rc_a", val_row("e1", 0.62, 0.62)), winner=None)
    assert plain(run.model.results().key[0]) == "■ Start · val · starting point"
    hold = run.concern("Hold placement")
    assert (hold.start.value, hold.start_note) == ("62.0%", "")
    cost = run.concern("Cost / request")
    assert (cost.start.value, cost.start_note) == ("$0.042", "train")
    refusal = run.concern("Refusal honesty")
    assert (refusal.start.value, refusal.start_note) == ("80.0%", "train")


# ---- the cost and latency bars ----


def bars(card) -> list[float | None]:
    tiers = (card.start, card.confirmed, card.unconfirmed)
    return [None if tier is None else tier.fraction for tier in tiers]


def confirmed_run(**speed: LatencyComparison) -> Run:
    """rc_a is Best Confirmed from the first gate, whose table carries
    `speed`: its cost or latency against Start on val."""
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.gate(1, table("rc_a", val_row("e1", 0.62, 0.70), **speed), winner="rc_a")
    return run


def arm_on(run: Run, wave: int, parent: str, **speed: LatencyComparison) -> Run:
    """A successful arm of `wave` on Best Confirmed `parent`, which measures
    `speed` on train."""
    item_id = f"w{wave}-1"
    run.plan(wave).planned(wave)
    run.wave_start(wave, fix(item_id, "e1"), parent=parent)
    return run.success(
        item_id,
        f"rc_w{wave}",
        row("e1", 0.70, 0.75, parent=parent),
        parent=parent,
        **speed,
    )


def usd(base: float, cand: float) -> LatencyComparison:
    return comparison(base, cand, metric="cost_usd")


def test_a_card_with_only_cuts_draws_start_at_four_fifths_and_each_cut_shorter():
    run = arm_on(
        confirmed_run(cost=usd(0.040, 0.030)), 2, "rc_a", cost=usd(0.030, 0.015)
    )
    assert bars(run.concern("Cost / request")) == pytest.approx([0.8, 0.6, 0.3])


def test_a_cost_rise_draws_its_largest_value_full_and_start_from_zero():
    run = confirmed_run(cost=usd(0.00014, 0.00038))
    card = run.concern("Cost / request")
    assert bars(card) == pytest.approx([0.00014 / 0.00038, 1.0, None])
    # Only the bars change: the values and the red change stay.
    assert (card.start.value, card.confirmed.value) == ("$0.00014", "$0.00038")
    assert (card.confirmed.change, card.confirmed.change_tone) == ("+171%", "error")


def test_a_cut_below_a_risen_best_confirmed_draws_a_shorter_bar():
    run = arm_on(
        confirmed_run(cost=usd(0.00014, 0.00038)),
        2,
        "rc_a",
        cost=usd(0.00038, 0.00019),
    )
    assert bars(run.concern("Cost / request")) == pytest.approx(
        [0.00014 / 0.00038, 1.0, 0.5]
    )


@pytest.mark.parametrize(
    ("confirmed", "expected"),
    [
        # A rise to less than Start ÷ 0.8 keeps Start at 0.8.
        (9000.0, [0.8, 0.9, None]),
        (16000.0, [0.5, 1.0, None]),
    ],
)
def test_a_latency_rise_draws_linearly_from_zero(confirmed, expected):
    run = confirmed_run(latency=comparison(8000.0, confirmed))
    assert bars(run.concern("Latency")) == pytest.approx(expected)


def test_a_card_with_no_start_value_draws_its_largest_value_full():
    # The first gate measured no cost, so Start has none. The second gate
    # promotes rc_w2 at $0.030, and a wave-3 arm halves it.
    run = arm_on(confirmed_run(), 2, "rc_a")
    gate_table = table(
        "rc_w2", val_row("e1", 0.70, 0.75, parent="rc_a"), cost=usd(0.040, 0.030)
    )
    run.gate(2, gate_table, winner="rc_w2")
    run = arm_on(run, 3, "rc_w2", cost=usd(0.030, 0.015))
    card = run.concern("Cost / request")
    assert card.start_note == "not measured yet"
    assert bars(card) == pytest.approx([None, 1.0, 0.5])
