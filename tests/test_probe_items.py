"""Probe items: one yes/no question about a mechanism, answered
by a short agent session — no judge, no measurement, no experiment record.
Code writes the answer to memory as a `probe`-tagged lesson and to the wave
log; an agent that could not run the probe still ends the item `completed`
with answer `unclear`."""

from __future__ import annotations

import pytest
from conftest import make_ctx
from fake_dispatcher import expected, infra, ok

from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.memory_schema import PROBE_TAG
from kiln_harness_tuner.schemas import (
    BaselineRef,
    BatchPlan,
    FixWork,
    ProbeItem,
    ProbeResult,
)
from kiln_harness_tuner.status import item_kind, item_label, plan_headline

pytestmark = pytest.mark.anyio


def probe_item(item_id: str = "w1-3") -> ProbeItem:
    return ProbeItem(
        id=item_id,
        type="probe",
        question="Does the model call a tool when one is attached under json_schema?",
        method="fork the baseline, attach the citation checker, drive 3 train items",
        reason="the skill and tool levers are inert if not",
        for_idea="reference cards into a skill",
    )


def test_probe_parses_in_a_plan_and_has_its_own_kind_label_and_headline():
    plan = BatchPlan.model_validate(
        {
            "items": [probe_item().model_dump()],
            "rejected_summary": "w1: x",
            "open_questions": [],
        }
    )
    item = plan.items[0]
    assert isinstance(item, ProbeItem) and item.max_drives == 3
    assert item_kind(item) == "probe"
    assert item_label(item).startswith("probe: Does the model")
    assert plan_headline([item]) == "1 probe"
    assert plan_headline([item, probe_item("w1-4")]) == "2 probes"


def test_probe_max_drives_is_bounded():
    with pytest.raises(ValueError):
        ProbeItem(
            id="w1-9", type="probe", question="q", method="m", reason="r", max_drives=50
        )


def test_wildcard_flag_parses_and_labels_the_arm():
    work = FixWork(
        issue_memory_id="i1",
        hypothesis="progressive-disclosure skill in place of the 52 KB prompt",
        technique="skill",
        predicted_effect="large or nothing",
        needs_eval=False,
        eval_id="e1",
        wildcard=True,
    )
    assert work.wildcard is True
    assert FixWork.model_validate(work.model_dump()).wildcard is True


async def test_probe_answer_lands_in_memory_and_the_wave_log():
    ctx = make_ctx()
    ctx.dispatch.queue(
        "probe",
        ok(
            ProbeResult(
                answer="no",
                evidence="0 tool calls in 3 of 3 drives; the hook was in every prompt",
                drives=3,
                ephemeral_rc_ids=["rc_probe_1"],
            ),
            cost_usd=0.4,
        ),
    )
    outcome = await execute_plan_item(
        ctx, probe_item(), BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.status == "completed" and outcome.result is None
    assert outcome.probe is not None and outcome.probe.answer == "no"
    assert outcome.rc_id is None and outcome.experiment_memory_id is None
    # one probe-tagged lesson, overview carries the item, question and answer
    lessons = [m for m in ctx.kiln.memories.values() if PROBE_TAG in m["tags"]]
    assert len(lessons) == 1
    assert lessons[0]["overview"].startswith("PROBE w1-3: ")
    assert lessons[0]["overview"].endswith(" → NO")
    assert "lesson" in lessons[0]["tags"]
    assert "0 tool calls in 3 of 3 drives" in lessons[0]["content"]
    assert "rc_probe_1" in lessons[0]["content"]
    kinds = [k for k, _ in ctx.log.events]
    assert "item_probe" in kinds and "item_outcome" in kinds
    spend = [
        f for k, f in ctx.log.events if k == "spend" and f.get("source") == "probe"
    ]
    assert spend and spend[0]["cost_usd"] == pytest.approx(0.4)
    states = [f["state"] for k, f in ctx.log.events if k == "item_state"]
    assert states[0] == "probing"


async def test_probe_the_agent_could_not_run_is_an_unclear_answer_not_infra():
    ctx = make_ctx()
    ctx.dispatch.queue("probe", expected("the parent config has no tool to attach"))
    outcome = await execute_plan_item(
        ctx, probe_item(), BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.status == "completed"
    assert outcome.probe is not None and outcome.probe.answer == "unclear"
    assert "no tool to attach" in outcome.probe.evidence
    lessons = [m for m in ctx.kiln.memories.values() if PROBE_TAG in m["tags"]]
    assert len(lessons) == 1 and lessons[0]["overview"].endswith(" → UNCLEAR")


async def test_probe_dispatcher_failure_is_failed_infra_with_nothing_written():
    ctx = make_ctx()
    ctx.dispatch.queue("probe", infra(), infra(), infra(), infra())
    outcome = await execute_plan_item(
        ctx, probe_item(), BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.status == "failed_infra" and outcome.probe is None
    assert not [m for m in ctx.kiln.memories.values() if PROBE_TAG in m["tags"]]
