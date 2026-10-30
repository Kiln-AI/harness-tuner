"""Reward-hacking checks at the promotion gate: quoting the
evals blocks in code; an output shape shift or a new kind of write action on
val is a flag the promotion rationale must address."""

from __future__ import annotations

import json

import pytest
from conftest import make_ctx
from fake_kiln import make_eval_config, make_run
from test_orchestrator import (
    decision,
    fix_item,
    make_plan,
    make_result,
    ok,
    rc_ok,
    seed_active_eval,
    seed_baseline,
    seed_issue,
    seed_train,
)

from kiln_harness_tuner.integrity import (
    output_shape_findings,
    quote_findings,
    rationale_addresses,
    write_action_findings,
    write_actions,
)
from kiln_harness_tuner.orchestrator import orchestrate


@pytest.fixture(autouse=True)
def _gate_only(monkeypatch):
    """These tests pin the GATE's check (a). The same matcher also runs right
    after the build (`kiln_harness_tuner.judge_copy`) and ends a quoting arm
    before the gate. Here the build-time check is off, as for a candidate
    whose evals changed after its build: the gate check is the last line of
    defense, and it must still block."""
    from kiln_harness_tuner.judge_copy import JudgeCopyCheck

    async def clean(*_a, **_k):
        return JudgeCopyCheck("clean")

    monkeypatch.setattr("kiln_harness_tuner.items.check_judge_copies", clean)


JUDGE = (
    "Score the reply PASS only when every count in the answer matches the"
    " manifest exactly and the reply names the depot it counted in."
)
BASE_PROMPT = "You are a freight depot assistant. Answer the operator's question."


class TestQuoting:
    def test_a_run_of_judge_words_is_a_quote(self):
        cand = (
            BASE_PROMPT
            + " Make sure every count in the answer matches the manifest exactly."
        )
        (finding,) = quote_findings(
            cand, {"the judge of eval X": JUDGE}, [], BASE_PROMPT
        )
        assert finding.startswith("quotes the judge of eval X verbatim")

    def test_words_the_baseline_or_the_task_already_has_are_not(self):
        allowed = (
            BASE_PROMPT + " every count in the answer matches the manifest exactly"
        )
        cand = allowed + " Be brief."
        assert quote_findings(cand, {"judge": JUDGE}, [], allowed) == []

    def test_naming_an_eval_verbatim(self):
        cand = BASE_PROMPT + " You will be graded on Strict Count Adherence."
        (finding,) = quote_findings(
            cand, {}, ["Strict Count Adherence", "Tone"], BASE_PROMPT
        )
        assert finding == 'names the eval "Strict Count Adherence" verbatim'


def _val(item: str, output: str, trace: list | None = None, **scores):
    run = {**make_run(item, **scores), "output": output}
    if trace is not None:
        run["task_run_trace"] = json.dumps(trace)
    return run


def _call(name: str, **args):
    return {
        "role": "assistant",
        "tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}],
    }


class TestShapeAndActions:
    def test_a_length_shift(self):
        base = [_val(f"v{i}", "x" * 100) for i in range(4)]
        cand = [_val(f"v{i}", "x" * 250) for i in range(4)]
        (finding,) = output_shape_findings(base, cand, "E")
        assert "median output length 2.50×" in finding
        assert output_shape_findings(base, base, "E") == []

    def test_a_json_key_change(self):
        base = [_val("v1", json.dumps({"answer": 1}))]
        cand = [_val("v1", json.dumps({"answer": 1, "confidence": "high"}))]
        findings = output_shape_findings(base, cand, "E")
        assert any(
            "top-level keys changed" in f and "confidence" in f for f in findings
        )

    def test_write_actions(self):
        run = _val(
            "v1",
            "ok",
            [
                _call("kestrel_api", method="GET", path="/orders"),
                _call("kestrel_api", method="post", path="/holds"),
                _call("cancel_order", order="1"),
            ],
        )
        assert write_actions(run) == {"kestrel_api POST", "cancel_order"}

    def test_a_new_kind_of_write(self):
        base = [_val("v1", "ok", [_call("kestrel_api", method="GET")])]
        cand = [_val("v1", "ok", [_call("kestrel_api", method="DELETE")])]
        (finding,) = write_action_findings(base, cand, "E")
        assert "['kestrel_api DELETE']" in finding
        assert write_action_findings(cand, cand, "E") == []

    def test_the_rationale_must_say_integrity(self):
        assert rationale_addresses("… Integrity: the longer replies are the fix.")
        assert not rationale_addresses("+9% on val")


# ---- at the gate ----


def _gate_ctx(
    cand_prompt: str, *, cand_output: str = "ok", gate_agent="always", item=None
):
    ctx = make_ctx(gate_agent=gate_agent)
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    kiln.eval_configs["ec_1"] = make_eval_config("ec_1", prompt_template=JUDGE)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    kiln.run_configs = [
        {"id": "rc_base", "name": "b", "prompt": {"prompt": BASE_PROMPT}},
        {"id": "rc_cand", "name": "c", "prompt": {"prompt": cand_prompt}},
    ]
    trace = [_call("kestrel_api", method="GET")]
    kiln.pending[("eval_1", "rc_base", "val")] = [
        _val("v1", "ok", trace, accuracy=0.0),
        _val("v2", "ok", trace, accuracy=1.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "val")] = [
        _val("v1", cand_output, trace, accuracy=1.0),
        _val("v2", cand_output, trace, accuracy=1.0),
    ]
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(item or fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_cand"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    return ctx


def _promoted(ctx) -> bool:
    return "rc_rc_cand" in ctx.kiln.memories["mem_baseline"]["tags"]


async def test_a_prompt_that_quotes_the_judge_is_never_promoted():
    ctx = _gate_ctx(
        BASE_PROMPT + " Every count in the answer matches the manifest exactly."
    )
    ctx.dispatch.queue("promote_best", decision("rc_cand", "Integrity: none."))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    (table,) = ctx.dispatch.calls_to("promote_best")[0].candidates
    assert table.ineligible
    assert table.integrity_flags[0].startswith("BLOCKED: quotes the judge of eval")
    assert [f for k, f in ctx.log.events if k == "promotion_blocked"]


async def test_a_flag_the_rationale_ignores_blocks_the_promotion():
    ctx = _gate_ctx(BASE_PROMPT, cand_output="x" * 50)
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+9% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)
    (blocked,) = [f for k, f in ctx.log.events if k == "promotion_blocked"]
    assert "not addressed" in blocked["reasons"][0]
    assert "median output length" in blocked["reasons"][1]


async def test_a_flag_the_rationale_addresses_is_promoted():
    ctx = _gate_ctx(BASE_PROMPT, cand_output="x" * 50)
    ctx.dispatch.queue(
        "promote_best",
        decision("rc_cand", "+9% on val. Integrity: the fuller replies are the fix."),
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    assert _promoted(ctx)


async def test_the_code_gate_sends_a_flagged_winner_to_the_agent():
    ctx = _gate_ctx(BASE_PROMPT, cand_output="x" * 50, gate_agent="auto")
    ctx.dispatch.queue(
        "promote_best",
        decision("rc_cand", "Integrity: the fuller replies are the fix."),
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    # The code rule alone would promote it (a clean val win); the flag sends
    # it to the agent, whose rationale addresses it.
    (rule,) = [f for k, f in ctx.log.events if k == "promotion_rule"]
    assert rule["decision"].winner_rc_id == "rc_cand"
    assert ctx.dispatch.calls_to("promote_best")
    assert _promoted(ctx)


async def test_new_write_actions_reach_the_agent_as_a_flag():
    ctx = _gate_ctx(BASE_PROMPT)
    ctx.kiln.pending[("eval_1", "rc_cand", "val")] = [
        _val("v1", "ok", [_call("kestrel_api", method="DELETE")], accuracy=0.0),
        _val("v2", "ok", [_call("kestrel_api", method="DELETE")], accuracy=1.0),
    ]
    ctx.dispatch.queue("promote_best", decision("rc_cand", "Integrity: fine."))
    await orchestrate(ctx, "g", timeout_mins=180)
    (table,) = ctx.dispatch.calls_to("promote_best")[0].candidates
    assert any("kestrel_api DELETE" in f for f in table.integrity_flags)


async def test_under_speed_a_flagged_winner_goes_to_the_agent(tmp_path):
    from test_speed_objective import _speed_ctx

    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    for rc_id, out in (("rc_base", "ok"), ("rc_cand", "x" * 50)):
        ctx.kiln.pending[("eval_1", rc_id, "val")] = [
            {**row, "output": out} for row in ctx.kiln.pending[("eval_1", rc_id, "val")]
        ]
    ctx.dispatch.queue(
        "promote_best", decision("rc_cand", "Integrity: fuller replies, same facts.")
    )
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert ctx.dispatch.calls_to("promote_best")
    assert "rc_rc_cand" in ctx.kiln.memories["mem_baseline"]["tags"]


# ---- the paraphrase review ----


def _review(*flags: str):
    from kiln_harness_tuner.schemas import IntegrityReviewResult

    return ok(IntegrityReviewResult(flags=list(flags)))


PARAPHRASE = BASE_PROMPT + " Be sure your totals agree with the manifest, all of them."


async def test_a_changed_prompt_gets_one_review_with_no_item_data():
    ctx = _gate_ctx(PARAPHRASE, cand_output="SECRET_VAL")
    ctx.dispatch.queue(
        "integrity_review", _review('"totals agree with the manifest" paraphrases X')
    )
    ctx.dispatch.queue("promote_best", decision("rc_cand", "Integrity: kept."))
    await orchestrate(ctx, "g", timeout_mins=180)
    (review,) = ctx.dispatch.calls_to("integrity_review")
    assert review.candidate_rc_id == "rc_cand"
    assert review.candidate_instructions == PARAPHRASE
    assert review.baseline_instructions == BASE_PROMPT
    assert JUDGE in review.judge_texts.values()
    wire = review.model_dump_json()
    assert "SECRET_VAL" not in wire and "v1" not in wire
    assert set(type(review).model_fields) == {
        "candidate_rc_id",
        "candidate_instructions",
        "baseline_instructions",
        "judge_texts",
    }
    (table,) = ctx.dispatch.calls_to("promote_best")[0].candidates
    assert 'review: "totals agree with the manifest" paraphrases X' in (
        table.integrity_flags
    )
    assert not table.ineligible  # a flag, never a block


async def test_the_flag_must_be_answered_like_any_other():
    ctx = _gate_ctx(PARAPHRASE)
    ctx.dispatch.queue("integrity_review", _review("paraphrases the judge"))
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+9% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert not _promoted(ctx)


async def test_an_unchanged_prompt_is_not_reviewed():
    ctx = _gate_ctx(BASE_PROMPT)
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+9% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert ctx.dispatch.calls_to("integrity_review") == []


async def test_a_review_that_fails_is_itself_a_flag():
    from fake_dispatcher import infra

    ctx = _gate_ctx(PARAPHRASE)
    ctx.dispatch.queue("integrity_review", infra("timeout", "timed out"))
    ctx.dispatch.queue("promote_best", decision("rc_cand", "Integrity: read it."))
    await orchestrate(ctx, "g", timeout_mins=180)
    (table,) = ctx.dispatch.calls_to("promote_best")[0].candidates
    assert table.integrity_flags[0].startswith(
        "review: the integrity review did not run (timed out)"
    )


async def test_the_code_gate_sends_a_review_flag_to_the_agent():
    ctx = _gate_ctx(PARAPHRASE, gate_agent="auto")
    ctx.dispatch.queue("integrity_review", _review("paraphrases the judge"))
    ctx.dispatch.queue(
        "promote_best", decision("rc_cand", "Integrity: the task asks for it.")
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    assert ctx.dispatch.calls_to("promote_best")
    assert _promoted(ctx)


# ---- every promotion path, prompts referenced by id ----
#
# A candidate promoted under `--objective speed` by the code rule must still
# get the integrity check. Its prompt can be a saved prompt
# (`prompt_id: id::<id>`) and the baseline's a generator
# (`simple_prompt_builder`). The prompt list names saved prompts `id::<id>`,
# which the lookup must not compare with the bare id, and a generator must be
# rendered. If both texts read as "", they are equal, the review is skipped
# and the quote scan scans nothing.

QUOTING = BASE_PROMPT + " Every count in the answer matches the manifest exactly."


def _by_id(ctx, cand_prompt: str):
    """The live shapes: the baseline on the task's generator, the candidate
    on a saved prompt; neither run config carries a frozen `prompt`."""
    kiln = ctx.kiln
    kiln.task = {"id": "t1", "name": "T", "instruction": BASE_PROMPT}
    kiln.eval_configs["ec_1"] = make_eval_config("ec_1", prompt_template=JUDGE)
    kiln.saved_prompts["424242000777"] = {
        "prompt": cand_prompt,
        "chain_of_thought_instructions": None,
    }
    for row in kiln.run_configs:
        row.pop("prompt", None)
        props = row.setdefault("run_config_properties", {})
        props["prompt_id"] = (
            "simple_prompt_builder" if row["id"] == "rc_base" else "id::424242000777"
        )


async def test_the_speed_code_rule_never_promotes_a_quoting_prompt(tmp_path):
    from test_speed_objective import _speed_ctx

    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    _by_id(ctx, QUOTING)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert "rc_rc_cand" not in ctx.kiln.memories["mem_baseline"]["tags"]
    (event,) = [f for k, f in ctx.log.events if k == "integrity"]
    assert event["flags"][0].startswith("BLOCKED: quotes the judge of eval")


def _blocked(ctx) -> None:
    assert not _promoted(ctx)
    (event,) = [f for k, f in ctx.log.events if k == "integrity"]
    assert event["flags"][0].startswith("BLOCKED: quotes the judge of eval")


async def test_the_cost_code_rule_never_promotes_a_quoting_prompt(tmp_path):
    from test_speed_objective import _speed_ctx

    ctx = _speed_ctx(tmp_path, cand_ms=1500.0, cand_cost=0.0002, objective="cost")
    _by_id(ctx, QUOTING)
    await orchestrate(ctx, "cheaper", timeout_mins=180)
    assert "rc_rc_cand" not in ctx.kiln.memories["mem_baseline"]["tags"]
    (event,) = [f for k, f in ctx.log.events if k == "integrity"]
    assert event["flags"][0].startswith("BLOCKED: quotes the judge of eval")


async def test_the_single_candidate_rule_never_promotes_a_quoting_prompt():
    ctx = _gate_ctx(QUOTING, gate_agent="auto")
    _by_id(ctx, QUOTING)
    await orchestrate(ctx, "g", timeout_mins=180)
    _blocked(ctx)


async def test_the_agent_path_never_promotes_a_quoting_prompt():
    ctx = _gate_ctx(QUOTING)
    _by_id(ctx, QUOTING)
    ctx.dispatch.queue("promote_best", decision("rc_cand", "Integrity: fine."))
    await orchestrate(ctx, "g", timeout_mins=180)
    _blocked(ctx)
    (blocked,) = [f for k, f in ctx.log.events if k == "promotion_blocked"]
    assert any("quotes the judge" in r for r in blocked["reasons"])


async def test_a_merge_never_promotes_a_quoting_prompt():
    from test_orchestrator import MergeWork, WinRef

    merge = fix_item("w1-1").model_copy(
        update={
            "work": MergeWork(
                wins=[
                    WinRef(experiment_memory_id="mem_a", rc_id="rc_a"),
                    WinRef(experiment_memory_id="mem_b", rc_id="rc_b"),
                ]
            )
        }
    )
    ctx = _gate_ctx(QUOTING, item=merge)
    _by_id(ctx, QUOTING)
    ctx.dispatch.queue("promote_best", decision("rc_cand", "Integrity: fine."))
    await orchestrate(ctx, "g", timeout_mins=180)
    _blocked(ctx)


async def test_prompts_by_id_are_read_for_the_review():
    ctx = _gate_ctx(PARAPHRASE)
    _by_id(ctx, PARAPHRASE)
    ctx.dispatch.queue("integrity_review", _review())
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+9% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    (review,) = ctx.dispatch.calls_to("integrity_review")
    assert review.candidate_instructions == PARAPHRASE  # the saved prompt
    assert review.baseline_instructions == BASE_PROMPT  # the generator, rendered
    assert _promoted(ctx)


async def test_an_unreadable_prompt_is_a_flag_the_code_rule_cannot_pass(tmp_path):
    from test_speed_objective import _speed_ctx

    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    _by_id(ctx, QUOTING)
    ctx.kiln.saved_prompts.clear()  # the candidate's prompt id resolves nowhere
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+60% faster"))
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert ctx.dispatch.calls_to("promote_best")  # the rule did not decide
    assert "rc_rc_cand" not in ctx.kiln.memories["mem_baseline"]["tags"]
    (event,) = [f for k, f in ctx.log.events if k == "integrity"]
    assert event["flags"][0].startswith("unread: the candidate's instructions")
    unread = [f for k, f in ctx.log.events if k == "integrity_input_unreadable"]
    assert unread[0]["what"] == "the candidate's instructions"


async def test_every_candidate_gets_an_integrity_line():
    ctx = _gate_ctx(BASE_PROMPT)
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+9% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    (event,) = [f for k, f in ctx.log.events if k == "integrity"]
    assert event["line"] == "integrity rc_cand: no flag"


@pytest.mark.parametrize("path", ["single_candidate_rule", "agent", "speed_rule"])
async def test_no_promotion_is_applied_without_the_checks(monkeypatch, tmp_path, path):
    """Whichever path decides: a table the checks did not mark is refused."""
    import kiln_harness_tuner.orchestrator as orch

    async def skipped(ctx, tables, baseline_rc_id):
        return None

    monkeypatch.setattr(orch, "apply_integrity_checks", skipped)
    if path == "speed_rule":
        from test_speed_objective import _speed_ctx

        ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    else:
        auto = path == "single_candidate_rule"
        ctx = _gate_ctx(BASE_PROMPT, gate_agent="auto" if auto else "always")
        if not auto:
            ctx.dispatch.queue("promote_best", decision("rc_cand", "+9% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert "rc_rc_cand" not in ctx.kiln.memories["mem_baseline"]["tags"]
    (blocked,) = [f for k, f in ctx.log.events if k == "promotion_blocked"]
    assert blocked["reasons"] == ["integrity checks did not run on the winner"]


async def test_the_prompt_listing_is_read_when_rendering_fails():
    ctx = _gate_ctx(QUOTING)
    _by_id(ctx, QUOTING)
    ctx.kiln.gen_prompt_fails = True
    ctx.kiln.run_configs[0]["prompt"] = {"prompt": BASE_PROMPT}  # frozen baseline
    ctx.dispatch.queue("promote_best", decision("rc_cand", "Integrity: fine."))
    await orchestrate(ctx, "g", timeout_mins=180)
    _blocked(ctx)  # listed as `id::<id>`, matched by the prompt id


async def test_an_unreadable_baseline_prompt_is_a_flag():
    ctx = _gate_ctx(BASE_PROMPT, gate_agent="auto")
    _by_id(ctx, BASE_PROMPT)
    ctx.kiln.run_configs[0]["run_config_properties"]["prompt_id"] = "gone::1"
    ctx.dispatch.queue("promote_best", decision("rc_cand", "+9% on val"))
    await orchestrate(ctx, "g", timeout_mins=180)
    (event,) = [f for k, f in ctx.log.events if k == "integrity"]
    assert event["flags"][0].startswith("unread: the baseline's instructions")
    assert not _promoted(ctx)  # a flag the rationale did not answer


class TestTheLastGuard:
    """`_apply_checked` on its own: every earlier check can change, this one
    stays between any decision and the apply."""

    async def _run(self, monkeypatch, table, rationale):
        import kiln_harness_tuner.orchestrator as orch
        from kiln_harness_tuner.schemas import PromotionDecision

        applied: list[str] = []

        async def apply(ctx, decision, candidates, record):
            applied.append(decision.winner_rc_id)

        monkeypatch.setattr(orch, "_apply_promotion", apply)
        ctx = make_ctx()
        decision = PromotionDecision(
            winner_rc_id="rc_cand",
            winner_experiment_memory_id="m",
            rationale=rationale,
            regressed_issues=[],
        )
        ok_ = await orch._apply_checked(ctx, decision, [], None, [table])
        blocked = [f for k, f in ctx.log.events if k == "promotion_blocked"]
        return ok_, applied, blocked

    def _table(self, **kw):
        from kiln_harness_tuner.schemas import CandidateValTable

        return CandidateValTable(
            rc_id="rc_cand", experiment_memory_id="m", per_eval=[], **kw
        )

    async def test_a_clean_checked_table_is_applied(self, monkeypatch):
        ok_, applied, blocked = await self._run(
            monkeypatch, self._table(integrity_checked=True), "+9%"
        )
        assert ok_ and applied == ["rc_cand"] and blocked == []

    async def test_an_ineligible_table_is_refused(self, monkeypatch):
        table = self._table(
            integrity_checked=True,
            ineligible=True,
            integrity_flags=["BLOCKED: quotes x"],
        )
        ok_, applied, blocked = await self._run(monkeypatch, table, "Integrity: ok.")
        assert not ok_ and applied == []
        assert blocked[0]["reasons"] == [
            "the winner is ineligible",
            "BLOCKED: quotes x",
        ]

    async def test_an_unanswered_flag_is_refused_an_answered_one_is_not(
        self, monkeypatch
    ):
        table = self._table(integrity_checked=True, integrity_flags=["review: x"])
        ok_, applied, blocked = await self._run(monkeypatch, table, "+9% on val")
        assert not ok_ and applied == [] and blocked[0]["reasons"][1] == "review: x"
        ok_, applied, _ = await self._run(monkeypatch, table, "Integrity: read it.")
        assert ok_ and applied == ["rc_cand"]
