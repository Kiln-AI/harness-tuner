"""The judge-copy check at build time: the gate's quote
matcher (integrity check (a)) runs right after the build, on the WHOLE
text of the run config — prompt, skills, code tools — so a phrase a parent
carries is found before any train spend. A hit gets ONE rebuild; a second
hit is a dead end. A gate block is printed, written on the record and
handed to the next planner. The baseline audit reports eval text the
baseline itself carries."""

from __future__ import annotations

from conftest import fix_item, make_ctx, make_result
from fake_dispatcher import ok
from fake_kiln import make_eval, make_eval_config, make_run, make_spec
from test_items import BASELINE
from test_prompt_text import install_skill, on_disk, skill_md, skill_rc

from kiln_harness_tuner.generality import audit_baseline
from kiln_harness_tuner.integrity import QUOTE_NGRAM, quote_findings
from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.judge_copy import JudgeCopyCheck, check_judge_copies
from kiln_harness_tuner.memory_schema import (
    BASELINE_JUDGE_COPY_TAG,
    ExperimentView,
    MemoryRecord,
)
from kiln_harness_tuner.schemas import (
    GeneralityReviewResult,
    MergeWork,
    RcBuildResult,
    WinRef,
)
from kiln_harness_tuner.ui import SETUP_LINE_EVENTS, own_line

BASE = "You are a freight depot assistant. Answer the operator's question."
JUDGE = (
    "Score the reply PASS only when every count in the answer matches the"
    " manifest exactly and the reply names the depot it counted in."
)
SPEC = (
    "## Core Requirement\nThe reply says a shipment is missing only after two"
    " separate manifest lookups agree that it is absent."
)
# Eleven words of the spec, copied into a prompt line.
COPY = "Say a shipment is missing only after two separate manifest lookups agree."
GENERAL = "Check the manifest before you state a count."


def _lines(*lines: str) -> str:
    return "\n".join(lines)


def _ctx(*prompts: tuple[str, str]):
    """One active eval with a judge and a spec; the baseline `rc_base`
    carries BASE only."""
    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1", name="Count accuracy")
    kiln.eval_configs["ec_1"] = make_eval_config("ec_1", prompt_template=JUDGE)
    kiln.specs = [make_spec("spec_1", "eval_1", definition=SPEC)]
    kiln.run_configs = [
        {"id": "rc_base", "name": "b", "prompt": {"prompt": BASE}},
        *(
            {"id": rc_id, "name": rc_id, "prompt": {"prompt": text}}
            for rc_id, text in prompts
        ),
    ]
    return ctx


def _seed(kiln, candidate: str, *parents: str) -> None:
    for parent in ("rc_base", *parents):
        kiln.results[("eval_1", parent, "train")] = [
            make_run("item_1", accuracy=0.0),
            make_run("item_2", accuracy=1.0),
        ]
    kiln.pending[("eval_1", candidate, "train")] = [
        make_run("item_1", accuracy=1.0),
        make_run("item_2", accuracy=1.0),
    ]


def _built(rc_id: str):
    return ok(RcBuildResult(rc_id=rc_id, build_notes="built"))


def _clean_review():
    return ok(GeneralityReviewResult(leaks=[]))


def _states(ctx) -> list[str]:
    return [f["state"] for k, f in ctx.log.events if k == "item_state"]


def _view(record: dict) -> ExperimentView:
    return ExperimentView.from_record(MemoryRecord.from_api(record))


# ---- the matcher ----


class TestTheEightWordRule:
    def test_seven_shared_words_are_not_a_copy(self):
        seven = "every count in the answer matches the"
        assert len(seven.split()) == QUOTE_NGRAM - 1
        cand = f"{BASE} Make sure {seven} totals."
        assert quote_findings(cand, {"the judge": JUDGE}, [], BASE) == []

    def test_eight_shared_words_are(self):
        cand = f"{BASE} Make sure every count in the answer matches the manifest."
        (finding,) = quote_findings(cand, {"the judge": JUDGE}, [], BASE)
        assert 'e.g. "every count in the answer matches the manifest"' in finding

    async def test_short_common_phrases_are_clean(self):
        ctx = _ctx(
            (
                "rc_new",
                _lines(BASE, "Answer the reply in the manifest.", "Name the depot."),
            )
        )
        check = await check_judge_copies(ctx, "rc_new", "rc_base")
        assert check.status == "clean"
        assert check.findings == []


# ---- the item path ----


async def test_a_phrase_inherited_from_the_parent_is_caught_and_rebuilt():
    """The phrase came in through the parent's prompt, so the build added no
    line with it. The check reads the whole text."""
    parent = _lines(BASE, COPY)
    ctx = _ctx(
        ("rc_parent", parent),
        ("rc_new", _lines(BASE, COPY, GENERAL)),
        ("rc_new2", _lines(BASE, GENERAL)),
    )
    _seed(ctx.kiln, "rc_new2", "rc_parent")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _clean_review(), _clean_review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    item = fix_item(parent_rc_id="rc_parent")
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new2"  # the rebuild is what was measured
    first, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    assert first.judge_copy_findings == []
    assert rebuild.rejected_rc_id == "rc_new"
    assert rebuild.generality_findings == []  # the review found nothing
    (finding,) = rebuild.judge_copy_findings
    assert finding.startswith(
        "the prompt quotes the spec of eval Count accuracy verbatim"
    )
    assert "shipment is missing only after two separate manifest lookups" in finding
    # Logged like a generality finding: an event with a line, a detail line.
    events = [f for k, f in ctx.log.events if k == "judge_copy"]
    assert [e["status"] for e in events] == ["copied", "clean"]
    assert events[0]["line"].startswith("judge copy rc_new: copied — the prompt")
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert any(d.startswith("judge copy: copied — the prompt quotes") for d in details)
    # The finding outlives the verdict, on the record the planner reads.
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    view = _view(record)
    assert view.judge_copy is not None
    assert view.judge_copy.startswith("first build rc_new copies eval text")
    assert "rebuilt once as rc_new2 — clean" in view.judge_copy
    assert view.rc_id == "rc_new2"


async def test_a_second_hit_is_a_dead_end_before_any_train_spend():
    ctx = _ctx(("rc_new", _lines(BASE, COPY)), ("rc_new2", _lines(BASE, COPY)))
    _seed(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _clean_review(), _clean_review())
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert outcome.rc_id == "rc_new2"
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    assert "measuring" not in _states(ctx)
    assert not [f for k, f in ctx.log.events if k == "queue_drawn"]
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        "— DEAD END (judge copy: still copies eval text after one rebuild)"
    )
    content = record["content"]
    assert "judge copy: first build rc_new copies eval text" in content
    assert "the rebuild rc_new2 still copies eval text" in content


async def test_one_rebuild_carries_both_kinds_of_finding():
    ctx = _ctx(
        ("rc_new", _lines(BASE, COPY, "For the Halden depot, say 14 pallets.")),
        ("rc_new2", _lines(BASE, GENERAL)),
    )
    _seed(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue(
        "generality_review",
        ok(GeneralityReviewResult(leaks=["names the Halden depot"])),
        _clean_review(),
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    _, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    assert rebuild.generality_findings == ["names the Halden depot"]
    assert len(rebuild.judge_copy_findings) == 1


async def test_a_reused_run_config_is_checked_and_never_rebuilt():
    ctx = _ctx(("rc_old", _lines(BASE, COPY)))
    _seed(ctx.kiln, "rc_old")
    ctx.dispatch.queue("generality_review", _clean_review())
    item = fix_item()
    item.work.existing_rc_id = "rc_old"
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert outcome.rc_id == "rc_old"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        "— DEAD END (judge copy: the reused run config copies eval text)"
    )
    assert "a reused run config is not rebuilt" in record["content"]


async def test_a_merge_goes_through_the_same_check():
    merge = fix_item().model_copy(
        update={
            "work": MergeWork(
                wins=[
                    WinRef(experiment_memory_id="mem_a", rc_id="rc_a"),
                    WinRef(experiment_memory_id="mem_b", rc_id="rc_b"),
                ]
            )
        }
    )
    ctx = _ctx(("rc_new", _lines(BASE, COPY)), ("rc_new2", _lines(BASE, COPY)))
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _clean_review(), _clean_review())
    outcome = await execute_plan_item(ctx, merge, BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    _, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    assert rebuild.judge_copy_findings
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []


# ---- the exemption: the baseline, not the parent ----


async def test_a_phrase_the_baseline_carries_does_not_block_a_build():
    ctx = _ctx(("rc_new", _lines(BASE, COPY, GENERAL)))
    ctx.kiln.run_configs[0]["prompt"] = {"prompt": _lines(BASE, COPY)}
    _seed(ctx.kiln, "rc_new")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _clean_review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert len(ctx.dispatch.calls_to("create_experiment_rc")) == 1
    (event,) = [f for k, f in ctx.log.events if k == "judge_copy"]
    assert event["status"] == "clean"


async def test_the_task_instructions_are_exempt_too():
    ctx = _ctx(("rc_new", _lines(BASE, COPY)))
    ctx.kiln.task = {"id": "t1", "name": "T", "instruction": COPY}
    check = await check_judge_copies(ctx, "rc_new", "rc_base")
    assert check.status == "clean"


async def test_a_parent_that_is_not_the_baseline_exempts_nothing():
    ctx = _ctx(("rc_parent", _lines(BASE, COPY)), ("rc_new", _lines(BASE, COPY)))
    check = await check_judge_copies(ctx, "rc_new", "rc_base")
    assert check.status == "copied"
    assert check.summary.startswith("the prompt quotes the spec of eval")


# ---- skills ----


async def test_a_phrase_in_a_skill_file_is_caught(tmp_path):
    ctx = _ctx()
    on_disk(ctx, tmp_path)
    install_skill(tmp_path, "sk2", "depot-api", skill_md("depot-api", COPY))
    ctx.kiln.run_configs.append(skill_rc("rc_new", BASE, "sk2"))
    check = await check_judge_copies(ctx, "rc_new", "rc_base")

    assert check.status == "copied"
    (finding,) = check.findings
    assert finding.startswith(
        "skill depot-api, SKILL.md quotes the spec of eval Count accuracy verbatim"
    )


async def test_a_skill_the_baseline_attaches_is_exempt(tmp_path):
    ctx = _ctx()
    on_disk(ctx, tmp_path)
    install_skill(tmp_path, "sk2", "depot-api", skill_md("depot-api", COPY))
    ctx.kiln.run_configs[0] = skill_rc("rc_base", BASE, "sk2")
    ctx.kiln.run_configs.append(skill_rc("rc_new", _lines(BASE, GENERAL), "sk2"))
    check = await check_judge_copies(ctx, "rc_new", "rc_base")
    assert check.status == "clean"


async def test_unreadable_candidate_text_is_unchecked_not_a_pass():
    ctx = _ctx()
    check = await check_judge_copies(ctx, "rc_missing", "rc_base")
    assert check.status == "unchecked"
    assert "could not be read" in check.note


# ---- the gate: a block is visible and reaches the planner ----


async def test_a_gate_block_is_logged_recorded_and_handed_to_the_planner(
    monkeypatch,
):
    from test_integrity import QUOTING, _by_id, _gate_ctx
    from test_orchestrator import decision

    from kiln_harness_tuner.orchestrator import orchestrate

    async def clean(*_a, **_k):  # the evals changed after the build, say
        return JudgeCopyCheck("clean")

    monkeypatch.setattr("kiln_harness_tuner.items.check_judge_copies", clean)
    ctx = _gate_ctx(QUOTING)
    _by_id(ctx, QUOTING)
    ctx.dispatch.queue("promote_best", decision(None, rationale="no winner"))
    await orchestrate(ctx, "g", timeout_mins=180)

    # One printed line, with the phrase and the eval object.
    (block,) = [f for k, f in ctx.log.events if k == "gate_block"]
    assert "gate_block" in SETUP_LINE_EVENTS
    line = own_line("gate_block", block)
    assert line.startswith("gate block rc_cand: integrity — the candidate's prompt")
    assert "quotes the judge of eval" in line
    assert "every count in the answer matches the manifest exactly" in line
    # The record: stamped, with the reason, and no false "did not beat it".
    record = ctx.kiln.memories[block["exp_mem_id"]]
    view = _view(record)
    assert view.rejected_at_val
    assert view.gate_block is not None
    assert "quotes the judge of eval" in view.gate_block
    assert "remove the copied phrase" in view.gate_block
    assert not view.rejection_noted
    assert [k for k, _ in ctx.log.events if k == "gate_block_recorded"]
    # The next planner reads it.
    _, second = ctx.dispatch.calls_to("batch_plan")
    (handed,) = second.gate_blocks
    assert handed.rc_id == "rc_cand"
    assert handed.experiment_memory_id == block["exp_mem_id"]
    assert any("quotes the judge of eval" in r for r in handed.reasons)


async def test_only_the_val_free_part_of_a_block_reaches_the_planner(monkeypatch):
    """The split rule: the planner is train-side. A blocked
    candidate that also carries the val-derived flags — (b) output shape,
    (c) a new write action, the integrity review's findings — hands the
    planner and its train-readable record the (a) reason alone."""
    from test_integrity import QUOTING, _by_id, _call, _gate_ctx, _val
    from test_orchestrator import decision

    from kiln_harness_tuner.orchestrator import orchestrate
    from kiln_harness_tuner.schemas import IntegrityReviewResult

    async def clean(*_a, **_k):
        return JudgeCopyCheck("clean")

    monkeypatch.setattr("kiln_harness_tuner.items.check_judge_copies", clean)
    ctx = _gate_ctx(QUOTING)
    _by_id(ctx, QUOTING)
    delete = [_call("kestrel_api", method="DELETE")]
    ctx.kiln.pending[("eval_1", "rc_cand", "val")] = [
        _val("v1", "SECRET_VAL " * 20, delete, accuracy=1.0),
        _val("v2", "SECRET_VAL " * 20, delete, accuracy=1.0),
    ]
    ctx.dispatch.queue(
        "integrity_review",
        ok(IntegrityReviewResult(flags=["paraphrases the judge's count rule"])),
    )
    ctx.dispatch.queue("promote_best", decision(None, rationale="no winner"))
    await orchestrate(ctx, "g", timeout_mins=180)

    # The val readers still see every flag.
    (integrity,) = [f for k, f in ctx.log.events if k == "integrity"]
    flags = " ".join(integrity["flags"])
    assert "median output length" in flags
    assert "kestrel_api DELETE" in flags
    assert "paraphrases the judge's count rule" in flags
    # The planner and the record get the (a) reason, and nothing else.
    _, second = ctx.dispatch.calls_to("batch_plan")
    (handed,) = second.gate_blocks
    assert handed.reasons and all(
        r.startswith("the candidate's prompt quotes ") for r in handed.reasons
    )
    record = ctx.kiln.memories[handed.experiment_memory_id]
    for carrier in (second.model_dump_json(), record["content"], record["overview"]):
        for val_derived in (
            "median output length",
            "write action",
            "DELETE",
            "paraphrases",
            "review:",
            "SECRET_VAL",
        ):
            assert val_derived not in carrier
    (block,) = [f for k, f in ctx.log.events if k == "gate_block"]
    assert "median output length" not in block["line"]


def test_the_plain_display_prints_a_blocked_promotion():
    from datetime import datetime

    from rich.console import Console

    from kiln_harness_tuner.status import RunStatusTracker
    from kiln_harness_tuner.ui import PlainUi

    console = Console(record=True, width=200)
    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="g")
    ui = PlainUi(tracker, console=console, now=lambda: datetime(2026, 10, 27))
    ui.event("promotion_blocked", winner_rc_id="rc_cand", reasons=["the winner is x"])
    tracker.event(
        "gate_block", rc_id="rc_cand", exp_mem_id="m1", reasons=["copies the judge"]
    )
    assert "promotion blocked: rc_cand — the winner is x" in console.export_text()
    (block,) = tracker.snapshot().gate_blocks
    assert block.reasons == ["copies the judge"]


# ---- the baseline audit ----


def _judge_copy_issues(ctx) -> list[dict]:
    return [
        m for m in ctx.kiln.memories.values() if BASELINE_JUDGE_COPY_TAG in m["tags"]
    ]


async def test_the_baseline_audit_reports_a_judge_copy_as_a_scrub_issue():
    ctx = _ctx()
    ctx.kiln.run_configs[0]["prompt"] = {"prompt": _lines(BASE, COPY)}
    ctx.dispatch.queue("generality_review", _clean_review())
    audit = await audit_baseline(ctx, "rc_base")

    assert audit.status == "clean"  # the train half
    copy = audit.judge_copy
    assert copy is not None and copy.status == "leaked"
    (finding,) = copy.findings
    assert finding.startswith("the prompt quotes the spec of eval Count accuracy")
    (issue,) = _judge_copy_issues(ctx)
    assert issue["id"] == copy.issue_id
    assert "issue" in issue["tags"] and "audited_rc_rc_base" in issue["tags"]
    assert issue["overview"].startswith("issue: the baseline's own text copies eval")
    assert "shipment is missing only after two separate" in issue["content"]
    (event,) = [f for k, f in ctx.log.events if k == "baseline_judge_copy_audit"]
    assert own_line("baseline_judge_copy_audit", event).startswith(
        "baseline audit: rc_base copies eval text (1 finding(s))"
    )
    # Once per baseline.
    ctx.dispatch.queue("generality_review", _clean_review())
    again = await audit_baseline(ctx, "rc_base")
    assert again.judge_copy is not None and again.judge_copy.status == "known"
    assert again.judge_copy.issue_id == copy.issue_id
    assert len(_judge_copy_issues(ctx)) == 1


async def test_a_clean_baseline_writes_no_judge_copy_issue():
    ctx = _ctx()
    ctx.dispatch.queue("generality_review", _clean_review())
    audit = await audit_baseline(ctx, "rc_base")
    assert audit.judge_copy is not None and audit.judge_copy.status == "clean"
    assert _judge_copy_issues(ctx) == []
    (event,) = [f for k, f in ctx.log.events if k == "baseline_judge_copy_audit"]
    assert own_line("baseline_judge_copy_audit", event) == ""
