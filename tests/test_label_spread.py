"""The check of the spread of labels, for a classifier task
(`kiln_harness_tuner.label_spread`): which task is a classifier, which
field holds the label, the counts, when a difference is called a collapse,
and that only train items are read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import ok
from fake_kiln import make_eval, make_run
from test_items import BASELINE
from test_orchestrator import seed_active_eval, seed_baseline

from kiln_harness_tuner.analysis import SIGNIFICANCE_ALPHA
from kiln_harness_tuner.detectability import binary_floor
from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.label_spread import (
    COLLAPSE_FRACTION,
    SHOWN_CLASSES,
    LabelField,
    LabelSetup,
    closed_fields,
    exact_two_sided_p,
    load_setup,
    pair_fields,
    prepare,
    spread,
    spreads_of,
)
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.report import render_label_spread_appendix
from kiln_harness_tuner.schemas import LabelSpread, RcBuildResult, RunStatus
from kiln_harness_tuner.status import RunStatusTracker

# An invented task: a help desk sends each ticket to one queue.
QUEUES = ("billing", "shipping", "returns")
SCHEMA = {
    "type": "object",
    "properties": {
        "queue": {"type": "string", "enum": list(QUEUES)},
        "summary": {"type": "string"},
    },
    "required": ["queue", "summary"],
}
QUEUE = LabelField(name="queue", values=QUEUES, reference_key="expected_queue")


def _expected(counts: dict[str, int], prefix: str = "t") -> dict[str, str]:
    """Item id -> expected label: `counts[label]` items for each label."""
    labels = [label for label, n in counts.items() for _ in range(n)]
    return {f"{prefix}{i}": label for i, label in enumerate(labels)}


def _row(expected: dict[str, str], given: dict[str, str | None]) -> LabelSpread:
    row = spread(QUEUE, expected, given, rc_id="rc_1", role="baseline")
    assert row is not None
    return row


# ---- Which task is a classifier, and which field holds the label ----


def test_a_closed_set_is_an_enum_a_boolean_a_const_union_or_a_ref():
    schema = {
        "type": "object",
        "$defs": {"Level": {"enum": ["low", "high"]}},
        "properties": {
            "queue": {"type": "string", "enum": list(QUEUES)},
            "urgent": {"type": "boolean"},
            "level": {"$ref": "#/$defs/Level"},
            "tone": {
                "anyOf": [{"const": "calm"}, {"const": "angry"}, {"type": "null"}]
            },
            "count": {"type": "integer", "enum": [1, 2, 3]},
            "summary": {"type": "string"},
            "one": {"const": "only"},
            "tags": {"type": "array", "items": {"enum": ["a", "b"]}},
            "inner": {"type": "object", "properties": {"x": {"enum": ["a", "b"]}}},
        },
    }
    assert closed_fields(schema) == {
        "queue": QUEUES,
        "urgent": ("true", "false"),
        "level": ("low", "high"),
        "tone": ("calm", "angry"),
        "count": ("1", "2", "3"),
    }


@pytest.mark.parametrize(
    "schema",
    [
        None,  # a free-text task
        {"type": "object", "properties": {"answer": {"type": "string"}}},
        {"type": "object", "properties": {"score": {"type": "number"}}},
    ],
)
def test_a_task_that_is_not_a_classifier_gets_no_field_and_a_note(schema):
    setup = prepare(schema, {"t0": {"expected_queue": "billing"}})
    assert setup.fields == () and setup.expected == {}
    assert setup.note is not None and "classifier" in setup.note
    assert spreads_of(setup, [], {"t0"}, rc_id="rc_1", role="baseline") == []


def test_a_field_pairs_with_its_reference_key_by_name():
    references = {
        "t0": {"expected_queue": "billing", "expected_urgent": True, "note": "x"},
        "t1": {"expected_queue": "returns", "expected_urgent": False},
    }
    fields = {"queue": QUEUES, "urgent": ("true", "false")}
    paired, left_out = pair_fields(fields, references)
    assert [(f.name, f.reference_key) for f in paired] == [
        ("queue", "expected_queue"),
        ("urgent", "expected_urgent"),
    ]
    assert left_out == []


def test_a_field_pairs_by_values_when_no_name_fits():
    references = {
        "t0": {"route": "billing", "author": "Ines"},
        "t1": {"route": "shipping", "author": "Tomas"},
    }
    paired, notes = pair_fields({"queue": QUEUES}, references)
    assert [(f.name, f.reference_key) for f in paired] == [("queue", "route")]
    assert notes == ["paired by values, not by name: `queue` with `route`"]


def test_a_reference_that_is_one_value_is_the_label():
    paired, _ = pair_fields({"queue": QUEUES}, {"t0": "billing", "t1": "returns"})
    assert [(f.name, f.reference_key) for f in paired] == [("queue", "")]


def test_a_closed_set_in_a_one_branch_allof_or_a_nullable_boolean():
    schema = {
        "type": "object",
        "$defs": {"Queue": {"enum": list(QUEUES)}},
        "properties": {
            "queue": {"allOf": [{"$ref": "#/$defs/Queue"}], "description": "x"},
            "urgent": {"type": ["boolean", "null"]},
            "note": {"type": ["string", "null"]},
        },
    }
    assert closed_fields(schema) == {"queue": QUEUES, "urgent": ("true", "false")}


def test_a_yes_or_no_field_is_not_paired_by_values_beside_other_fields():
    """A two-value set fits any yes-or-no key by chance: `has_link` is not
    the expected value of `urgent`."""
    setup = prepare(
        {
            "type": "object",
            "properties": {
                "action": {"enum": ["keep", "remove"]},
                "urgent": {"type": "boolean"},
            },
        },
        {
            "t0": {"expected_action": "keep", "has_link": True},
            "t1": {"expected_action": "remove", "has_link": False},
        },
    )
    assert [(f.name, f.reference_key) for f in setup.fields] == [
        ("action", "expected_action")
    ]
    assert setup.note is not None and "`urgent`" in setup.note


def test_a_pairing_by_values_is_named_in_the_note():
    setup = prepare(SCHEMA, {"t0": {"route": "billing"}, "t1": {"route": "returns"}})
    assert [(f.name, f.reference_key) for f in setup.fields] == [("queue", "route")]
    assert setup.note == "paired by values, not by name: `queue` with `route`"


def test_a_key_that_two_fields_claim_by_name_pairs_with_neither_by_name():
    fields = {"queue": QUEUES, "expected_queue": QUEUES}
    paired, notes = pair_fields(
        fields, {"t0": {"queue": "billing"}, "t1": {"queue": "returns"}}
    )
    assert paired == []
    assert notes and "more than one pairing" in notes[0]


def test_a_field_with_no_expected_label_is_left_out_and_named():
    """A spread with nothing to compare it with says nothing, so a closed-set
    field that the references do not cover is not a label field."""
    setup = prepare(
        {
            "type": "object",
            "properties": {
                "queue": {"enum": list(QUEUES)},
                "confidence": {"enum": ["low", "medium", "high"]},
            },
        },
        {"t0": {"expected_queue": "billing"}, "t1": {"expected_queue": "returns"}},
    )
    assert [f.name for f in setup.fields] == ["queue"]
    assert setup.note is not None and "`confidence`" in setup.note
    assert "no key of the train references" in setup.note


def test_two_fields_that_fit_one_key_are_both_left_out():
    """Code does not guess: two closed-set fields and one key that fits
    both. Neither pairs, and the note names both."""
    fields = {"refund": ("yes", "no", "maybe"), "escalate": ("yes", "no", "maybe")}
    paired, notes = pair_fields(fields, {"t0": {"gold": "yes"}, "t1": {"gold": "no"}})
    assert paired == []
    (line,) = notes
    assert "`refund`: more than one" in line and "`escalate`: more than one" in line


def test_two_yes_or_no_fields_are_not_paired_by_values():
    fields = {"refund": ("yes", "no"), "escalate": ("yes", "no")}
    paired, notes = pair_fields(fields, {"t0": {"gold": "yes"}, "t1": {"gold": "no"}})
    assert paired == []
    (line,) = notes
    assert "`refund`" in line and "`escalate`" in line and "yes-or-no" in line


def test_an_expected_value_outside_the_closed_set_leaves_that_item_out():
    setup = prepare(
        SCHEMA,
        {
            "t0": {"expected_queue": "billing"},
            "t1": {"expected_queue": "Returns "},  # case and spaces are ignored
            "t2": {"expected_queue": "legal"},  # not a value of the enum
            "t3": {"expected_queue": None},
        },
    )
    assert setup.expected == {"queue": {"t0": "billing", "t1": "returns"}}


# ---- The counts, and when a difference is a collapse ----


def test_a_balanced_classifier_is_not_collapsed():
    expected = _expected({"billing": 10, "shipping": 10, "returns": 10})
    given = dict(expected)
    # Four wrong answers, spread over the classes: errors, not a collapse.
    given.update(
        {"t0": "shipping", "t10": "returns", "t20": "billing", "t21": "billing"}
    )
    row = _row(expected, given)
    assert row.n_items == 30
    assert {c.label: (c.expected, c.predicted) for c in row.classes} == {
        "billing": (10, 11),
        "shipping": (10, 10),
        "returns": (10, 9),
    }
    assert row.top_label == "billing"
    assert row.top_predicted_share == pytest.approx(11 / 30)
    assert row.top_expected_share == pytest.approx(10 / 30)
    assert row.collapse_fraction == pytest.approx(1 / 20)
    assert not row.collapsed
    assert row.fact.endswith(": not collapsed")


def test_a_classifier_that_gives_one_class_to_most_inputs_is_collapsed():
    expected = _expected({"billing": 10, "shipping": 10, "returns": 10})
    given: dict[str, str | None] = {i: "billing" for i in expected}
    given.update({"t10": "shipping", "t11": "shipping", "t20": "returns"})
    row = _row(expected, given)
    assert row.top_label == "billing"
    assert row.top_predicted_share == pytest.approx(27 / 30)
    # 17 of the 20 items that expect another queue got `billing`.
    assert row.collapse_fraction == pytest.approx(17 / 20)
    assert row.p_value == pytest.approx(2 * 0.5**17)
    assert row.collapsed and row.collapse_limit == COLLAPSE_FRACTION
    assert "`billing` is given to 27 (90%) and expected on 10 (33%)" in row.fact
    assert row.fact.endswith(": collapsed")


def test_a_common_class_that_is_also_expected_is_not_a_collapse():
    """One class to most inputs is correct when the data says so: the two
    spreads agree, so the fraction is zero."""
    expected = _expected({"billing": 27, "shipping": 2, "returns": 1})
    row = _row(expected, dict(expected))
    assert row.top_predicted_share == pytest.approx(0.9)
    assert row.collapse_fraction == pytest.approx(0.0)
    assert row.p_value is None and not row.collapsed


def test_the_limit_is_at_one_half_of_the_other_classes_items():
    expected = _expected({"billing": 10, "shipping": 20})

    def moved(n: int) -> LabelSpread:
        given = dict(expected)
        given.update({f"t{10 + i}": "billing" for i in range(n)})
        return _row(expected, given)

    assert moved(9).collapse_fraction == pytest.approx(0.45)
    assert not moved(9).collapsed
    assert moved(10).collapse_fraction == pytest.approx(COLLAPSE_FRACTION)
    assert moved(10).collapsed
    # At the limit, the class has more than half of ALL the items.
    assert moved(10).top_predicted_share > 0.5


def test_at_the_limit_the_class_has_at_least_half_of_the_items():
    """Every item expects `shipping`, and half get `billing`: the fraction
    is exactly the limit, and the class has half of the items, not more."""
    expected = _expected({"shipping": 20})
    given = dict(expected)
    given.update({f"t{i}": "billing" for i in range(10)})
    row = _row(expected, given)
    assert row.top_label == "billing"
    assert row.top_predicted_share == pytest.approx(0.5)
    assert row.collapse_fraction == pytest.approx(COLLAPSE_FRACTION)
    assert row.collapsed


def test_a_class_given_less_often_than_expected_has_a_zero_fraction():
    """Most outputs hold no label: the class given most often still takes
    no items of other classes. The fraction is 0, never negative, and there
    is no p-value for a move into the class."""
    expected = _expected({"billing": 10, "shipping": 10})
    given: dict[str, str | None] = {i: None for i in expected}
    given["t0"] = "billing"
    row = _row(expected, given)
    assert row.top_label == "billing"
    assert row.collapse_fraction == pytest.approx(0.0)
    assert row.p_value is None and not row.collapsed
    assert "net, 0% of the items" in row.fact
    assert "p =" not in row.fact


def test_a_tie_goes_to_the_class_with_the_larger_excess():
    expected = _expected({"billing": 6, "shipping": 2})
    given = {**{f"t{i}": "billing" for i in range(4)}}
    given.update({f"t{i}": "shipping" for i in range(4, 8)})
    row = _row(expected, given)
    # Both are given 4 times: `shipping` is 2 over its expected count.
    assert row.top_label == "shipping"


def test_a_collapse_on_a_few_items_is_not_called():
    """Every item gets one class, on a set too small for the test to reach
    the alpha: the fraction is 1, and the flag stays false."""
    floor = binary_floor(SIGNIFICANCE_ALPHA)
    assert exact_two_sided_p(floor, 0) < SIGNIFICANCE_ALPHA
    assert exact_two_sided_p(floor - 1, 0) > SIGNIFICANCE_ALPHA
    expected = _expected({"billing": 3, "shipping": floor - 1})
    row = _row(expected, {i: "billing" for i in expected})
    assert row.collapse_fraction == pytest.approx(1.0)
    assert not row.collapsed
    more = _expected({"billing": 3, "shipping": floor})
    assert _row(more, {i: "billing" for i in more}).collapsed


def test_the_exact_test_is_two_sided_and_symmetric():
    assert exact_two_sided_p(0, 0) is None
    assert exact_two_sided_p(3, 3) == pytest.approx(1.0)
    assert exact_two_sided_p(8, 1) == pytest.approx(exact_two_sided_p(1, 8))
    assert exact_two_sided_p(8, 1) == pytest.approx(2 * (1 + 9) / 2**9)


def test_an_output_with_no_value_of_the_closed_set_is_counted_apart():
    expected = _expected({"billing": 4, "shipping": 4})
    given: dict[str, str | None] = dict(expected)
    given.update({"t0": None, "t5": None})
    row = _row(expected, given)
    assert row.predicted_outside == 2
    assert sum(c.predicted for c in row.classes) == 6
    assert "2 output(s) hold no value of the closed set" in row.fact


def test_only_an_item_with_both_labels_is_counted():
    expected = _expected({"billing": 2, "shipping": 2})
    row = _row(expected, {"t0": "billing", "t2": "shipping", "other": "returns"})
    assert row.n_items == 2
    assert spread(QUEUE, expected, {}, rc_id="rc_1", role="baseline") is None


def test_a_long_class_list_is_cut_and_the_rest_is_counted():
    values = tuple(f"c{i}" for i in range(SHOWN_CLASSES + 5))
    wide = LabelField(name="queue", values=values, reference_key="expected_queue")
    expected = {f"t{i}": value for i, value in enumerate(values)}
    row = spread(wide, expected, dict(expected), rc_id="rc_1", role="baseline")
    assert row is not None
    assert len(row.classes) == SHOWN_CLASSES and row.more_classes == 5


# ---- Stored rows, and train only ----


def _run(item_id: str, queue: str | None, **scores: float) -> dict:
    run = make_run(item_id, **(scores or {"accuracy": 1.0}))
    if queue is not None:
        run["output"] = json.dumps({"queue": queue, "summary": "s"})
    return run


def test_the_given_label_is_read_from_the_stored_output():
    setup = prepare(SCHEMA, {f"t{i}": {"expected_queue": "billing"} for i in range(5)})
    runs = [
        _run("t0", "billing"),
        _run("t1", "SHIPPING"),  # case is ignored
        _run("t2", "legal"),  # not a value of the enum
        {**make_run("t3"), "output": "not json"},
        _run("t4", None),  # no stored output: the item is not counted
    ]
    (row,) = spreads_of(
        setup, runs, {f"t{i}" for i in range(5)}, rc_id="r", role="parent"
    )
    assert row.n_items == 4 and row.predicted_outside == 2
    assert {c.label: c.predicted for c in row.classes} == {"billing": 1, "shipping": 1}


def test_a_row_of_an_item_that_is_not_train_is_dropped():
    setup = prepare(SCHEMA, {"t0": {"expected_queue": "billing"}})
    # A setup built from train references has no label for `v0`. Even with
    # one, a stored row of an item outside the train set is not read.
    leaky = LabelSetup(
        fields=setup.fields,
        expected={"queue": {"t0": "billing", "v0": "returns"}},
    )
    runs = [_run("t0", "billing"), _run("v0", "billing")]
    (row,) = spreads_of(leaky, runs, {"t0"}, rc_id="r", role="baseline")
    assert row.n_items == 1


def test_an_item_with_several_rows_counts_one_time():
    """Replicates, or one item in two evals: the item counts one time, with
    the last row that has an output."""
    setup = prepare(SCHEMA, {"t0": {"expected_queue": "billing"}})
    runs = [
        _run("t0", "shipping"),
        _run("t0", "billing"),
        _run("t0", None),  # no output: not the row that counts
    ]
    (row,) = spreads_of(setup, runs, {"t0"}, rc_id="r", role="baseline")
    assert row.n_items == 1
    assert {c.label: c.predicted for c in row.classes} == {"billing": 1}


def _project(
    tmp_path: Path,
    train: dict[str, str],
    val: dict[str, str],
    schema: dict | None = SCHEMA,
) -> Path:
    """A project on disk: one eval with tag splits, the task record, and one
    eval input for each train and val item, with its expected queue."""
    task = tmp_path / "proj" / "tasks" / "t1 - Tickets"
    splits = {
        n: {"source": "eval_input", "filter_id": f"tag::{n}"}
        for n in ("train", "val", "test")
    }
    folder = task / "evals" / "eval_1 - E"
    folder.mkdir(parents=True)
    (folder / "eval.kiln").write_text(json.dumps({"id": "eval_1", "splits": splits}))
    (task / "task.kiln").write_text(
        json.dumps(
            {
                "id": "t1",
                "output_json_schema": json.dumps(schema) if schema else None,
            }
        )
    )
    for split, items in (("train", train), ("val", val)):
        for item_id, queue in items.items():
            item = task / "eval_inputs" / item_id
            item.mkdir(parents=True)
            (item / "eval_input.kiln").write_text(
                json.dumps(
                    {
                        "id": item_id,
                        "tags": [split],
                        "data": {
                            "type": "single_turn",
                            "user_message": {"text": f"ticket {item_id}"},
                        },
                        "reference": {"expected_queue": queue},
                    }
                )
            )
    return tmp_path / "proj"


TRAIN = _expected({"billing": 8, "shipping": 8, "returns": 8})
VAL = _expected({"returns": 9}, prefix="heldout_")


def _ctx(tmp_path: Path, schema: dict | None = SCHEMA):
    ctx = make_ctx(gate_agent="auto")
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(_project(tmp_path, TRAIN, VAL, schema))}
    )
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    return ctx


def test_the_setup_reads_the_expected_labels_of_train_items_only(tmp_path):
    ctx = _ctx(tmp_path)
    setup = load_setup(ctx)
    assert [(f.name, f.reference_key) for f in setup.fields] == [
        ("queue", "expected_queue")
    ]
    assert setup.expected == {"queue": TRAIN}
    assert ctx.label_train_ids == frozenset(TRAIN)
    assert ctx.label_setup is setup  # read one time


def test_dataset_items_of_a_v1_eval_are_named_in_the_note(tmp_path):
    """A V1 eval splits dataset task runs, which have no reference: they
    are not counted, and the note says so."""
    ctx = _ctx(tmp_path)
    task = Path(ctx.target.project_path) / "tasks" / "t1 - Tickets"
    splits = {
        n: {"source": "task_run", "filter_id": f"tag::{n}"}
        for n in ("train", "val", "test")
    }
    folder = task / "evals" / "eval_v1 - E"
    folder.mkdir(parents=True)
    (folder / "eval.kiln").write_text(json.dumps({"id": "eval_v1", "splits": splits}))
    for n in range(3):
        run = task / "runs" / f"run{n}"
        run.mkdir(parents=True)
        (run / "task_run.kiln").write_text(
            json.dumps({"id": f"run{n}", "tags": ["train"], "input": "ticket"})
        )
    setup = load_setup(ctx)
    assert [f.name for f in setup.fields] == ["queue"]
    assert setup.note is not None
    assert "3 train item(s) are dataset task runs" in setup.note


async def test_the_planner_gets_the_baseline_spread_and_no_val_number(tmp_path):
    ctx = _ctx(tmp_path)
    # The baseline sends every train ticket to `billing`.
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        _run(item_id, "billing", accuracy=1.0 if label == "billing" else 0.0)
        for item_id, label in TRAIN.items()
    ]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    (row,) = plan_input.label_spreads
    assert (row.rc_id, row.role, row.output_field) == ("rc_base", "baseline", "queue")
    assert row.n_items == len(TRAIN) == 24
    assert {c.label: (c.expected, c.predicted) for c in row.classes} == {
        "billing": (8, 24),
        "shipping": (8, 0),
        "returns": (8, 0),
    }
    assert row.collapse_fraction == pytest.approx(1.0) and row.collapsed
    assert plan_input.label_spread_note is None
    # Val holds 9 `returns` tickets. With them the expected count of
    # `returns` would be 17 of 33: neither number is in the planner's input.
    assert all(c.expected == 8 for c in row.classes)
    assert sum(c.expected for c in row.classes) == 24
    # The event, the terminal line (one time) and the report section.
    events = [f for k, f in ctx.log.events if k == "label_spread"]
    assert events and all(f["rc"] == "rc_base" and f["collapsed"] for f in events)
    assert [f["line"] for f in events if "line" in f] == [
        f"label spread, baseline rc_base: {row.fact}"
    ]
    tracker = RunStatusTracker(project_id="p1", task_id="t1", guidance="")
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)
    appendix = render_label_spread_appendix(tracker.snapshot())
    assert appendix.startswith("## Label spread (code-computed)\n")
    assert (
        "Baseline `rc_base`, field `queue`, 24 train item(s): **collapsed**" in appendix
    )
    assert "| billing | 8 | 24 |" in appendix


async def test_a_task_that_is_not_a_classifier_gets_no_row_and_a_note(tmp_path):
    free_text = {"type": "object", "properties": {"answer": {"type": "string"}}}
    ctx = _ctx(tmp_path, schema=free_text)
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        _run(item_id, "billing") for item_id in TRAIN
    ]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.label_spreads == []
    assert "not a classifier" in plan_input.label_spread_note
    assert not [k for k, _f in ctx.log.events if k == "label_spread"]
    status = RunStatus(
        project_id="p1",
        task_id="t1",
        guidance="",
        baseline_history=[],
        waves=[],
        spend_total_usd=0.0,
        time_remaining_secs=0.0,
        exit_reason=None,
    )
    assert render_label_spread_appendix(status) == ""


async def test_a_held_out_row_drops_its_eval_and_not_the_others(tmp_path):
    """An eval whose train read returns a held-out item raises in the
    analyzer. The check leaves that eval out and counts the rest."""
    from kiln_harness_tuner.label_spread import run_config_spreads

    ctx = _ctx(tmp_path)
    ctx.analyzer.heldout_ids = frozenset(VAL)
    ctx.kiln.evals["eval_2"] = make_eval("eval_2", "ec_1")
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        _run(item_id, label) for item_id, label in TRAIN.items()
    ]
    ctx.kiln.results[("eval_2", "rc_base", "train")] = [_run("heldout_0", "billing")]
    (row,) = await run_config_spreads(
        ctx, "rc_base", "baseline", [("eval_1", "ec_1"), ("eval_2", "ec_1")]
    )
    assert row.n_items == 24 and row.collapse_fraction == pytest.approx(0.0)


async def test_an_empty_spread_keeps_the_note_of_the_left_out_fields(tmp_path):
    schema = {
        "type": "object",
        "properties": {
            "queue": {"enum": list(QUEUES)},
            "confidence": {"enum": ["low", "medium", "high"]},
        },
    }
    ctx = _ctx(tmp_path, schema=schema)
    # Stored baseline rows with no output: nothing can be counted.
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [make_run("t0")]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.label_spreads == []
    note = plan_input.label_spread_note
    assert note.startswith("no train item has both")
    assert "`confidence`" in note


async def test_a_project_that_is_not_on_disk_costs_no_wave():
    """Most tests, and a run whose project folder moved: no row, a note."""
    ctx = make_ctx(gate_agent="auto")
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [_run("t0", "billing")]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.label_spreads == []
    assert plan_input.label_spread_note is not None


async def test_the_analysis_table_holds_the_candidate_and_the_parent(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.run_configs = [
        {"id": "rc_base", "name": "b", "prompt": {"prompt": "Route the ticket."}},
        {"id": "rc_new", "name": "n", "prompt": {"prompt": "Route the ticket."}},
    ]
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        _run(item_id, "billing", accuracy=1.0 if label == "billing" else 0.0)
        for item_id, label in TRAIN.items()
    ]
    ctx.kiln.pending[("eval_1", "rc_new", "train")] = [
        _run(item_id, label, accuracy=1.0) for item_id, label in TRAIN.items()
    ]
    # A stored val row of the candidate: never read by the check.
    ctx.kiln.results[("eval_1", "rc_new", "val")] = [
        _run(item_id, "billing", accuracy=0.0) for item_id in VAL
    ]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="built"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed"
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    candidate, parent = analysis.bundle.label_spreads
    assert (candidate.rc_id, candidate.role) == ("rc_new", "candidate")
    assert candidate.n_items == 24 and not candidate.collapsed
    assert candidate.collapse_fraction == pytest.approx(0.0)
    assert (parent.rc_id, parent.role) == ("rc_base", "parent")
    assert parent.collapsed and parent.top_label == "billing"
    assert sum(c.expected for c in candidate.classes) == 24
    splits = {query[2] for query in ctx.kiln.results_queries}
    assert splits == {"train"}
    (arm,) = [f for k, f in ctx.log.events if k == "arm_results"]
    assert len(arm["label_spreads"]) == 2
