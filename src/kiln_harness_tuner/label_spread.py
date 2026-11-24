"""The check of the spread of labels, for a classifier task.

A classifier can give one class to most inputs. The pass rate of an eval
shows that items fail. It does not show that the failures have one shape.
This check shows it: for each label field of the task output, it counts the
labels that a run config gave on the train items, and puts those counts
beside the counts of the expected labels of the same items.

Code only. It reads the project on disk and the stored train rows, so it
starts no eval job and no agent, and it costs nothing.

**Which task is a classifier.** Code decides from the task's output schema
(`output_json_schema` of the task record on disk), never from the prompt or
from a name. A label field is a top-level property of the output whose
schema is a closed set of two or more values (`closed_values`): an `enum`
of strings, numbers or booleans, a `boolean` (also with `null`), a
`const`, an `anyOf` or `oneOf` whose every branch is one of those (a
`null` branch is ignored), a one-branch `allOf`, or a local `$ref` to one
of those. A task is a classifier, for this check,
when it has such a field. The shape is not clear, and the check does not
run, for:

- a task with no output schema: a free-text answer can be a label, but
  code cannot know the set of labels;
- a closed set below the top level, or a list of labels;
- a label field with no expected label in the train references (below).

In each case the planner's `label_spread_note` and the `label_spread` event
say why. Nothing is guessed.

**Which field holds the label.** An output can have several closed-set
fields. A field is compared only when the train references hold its
expected value, because a spread with nothing to compare it with says
nothing: a correct classifier on data where one class is common also gives
one class to most inputs. The reference of an eval input is an object or
one value. `pair_fields` pairs a field with a key of the reference in two
steps:

1. by name: the key and the field have the same name, after the words
   in `NAME_AFFIXES` (`expected`, `label`, …) are removed from both, and at
   least one train value at the key is in the field's closed set;
2. else by values: the key is the only key whose every train value is in
   the field's closed set, and it fits no other field. A two-value set (a
   yes or no) fits too many keys by chance, so this step pairs one only
   when it is the only closed-set field of the output.

A field that pairs with no key, or with more than one, is left out, and a
field paired by values is named, in the note. Each paired field gets its
own row, so a task with two label fields gets two rows.

**Train only.** An expected label on val or test is held-out knowledge.
The expected labels are read only for the items of the run's train set
(`gateway.trainset`, the same set that decides what an agent can read), and
the given labels only from stored TRAIN rows whose item is in that set. No
record of another item is opened for its reference. So the numbers can go
to the planner and the analyzer before the gate.

**The numbers.** Over the items that have both an expected label and a
stored output, for one field (`spread`):

- the count of each class, expected and given;
- `predicted_outside`: outputs that hold no value of the closed set (not
  JSON, no such field, or another value);
- `top_label`: the class given most often, with its given share and its
  expected share;
- `collapse_fraction`: (given share - expected share) / (1 - expected
  share), and 0 when that is below 0. It is the net share of the items of
  OTHER classes that got `top_label`. 0 means that this class takes no
  items of other classes, 1 means that every item got it;
- `p_value`: the exact two-sided test (McNemar) on the items where the
  expected class and the given class disagree about `top_label`. None
  when the items moved into `top_label` do not outnumber the items moved
  out of it.

**When it is called a collapse.** `collapsed` is true when
`collapse_fraction` is at least `COLLAPSE_FRACTION`, and `p_value` is below
the significance alpha that the rest of the measurement uses
(`analysis.SIGNIFICANCE_ALPHA`). The flag is a fact about the counts. It is
not a verdict on the run config, and no code decision reads it.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from kiln_harness_tuner.analysis import (
    SIGNIFICANCE_ALPHA,
    AnalysisError,
    _item_id,
    _row_output,
    _runs_by_item,
)
from kiln_harness_tuner.gateway.trainset import TrainSet, TrainSetError
from kiln_harness_tuner.gateway.trainset import compute as compute_train_set
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.quarantine import QuarantineError, _children, task_dirs
from kiln_harness_tuner.schemas import LabelCount, LabelSpread

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

# The one limit of this check. A difference between the two spreads is
# called a collapse when, net, at least this share of the items that expect
# ANOTHER class got the class given most often.
#
# It rests on the definition of the failure, not on a measurement: "a
# classifier gives one class to most inputs". At 0.5 or above, the class
# given most often always has at least half of all the items, whatever the
# expected spread is: its given share is at least 0.5 + 0.5 x its expected
# share. Below 0.5, most of the items of the other classes still get
# another answer, so the name does not fit. The
# measure is a share of the OTHER classes' items so that one limit holds for
# two classes and for twenty, and for a class that is common in the data and
# for one that is rare.
COLLAPSE_FRACTION = 0.5

# A table for an agent has a size limit: the classes with the largest
# counts are shown, and the rest are counted in `more_classes`.
SHOWN_CLASSES = 12

# Words that a reference key or a field name can carry and that do not name
# the label: `expected_category` pairs with the field `category`.
NAME_AFFIXES = frozenset(
    {"expected", "gold", "true", "target", "reference", "correct", "label", "answer"}
)

Role = Literal["baseline", "candidate", "parent"]

_REF_DEPTH = 8
_WORD = re.compile(r"[a-z0-9]+")
_WHOLE = ""  # the reference key of a reference that is the label itself


def label_text(value: Any) -> str | None:
    """A label as text, or None for a value that cannot be a label. A
    boolean reads `true` or `false`, as JSON writes it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _fold(text: str) -> str:
    return text.strip().casefold()


def _resolve(schema: Any, root: Mapping[str, Any]) -> Any:
    """Follow local `$ref`s (`#/$defs/Name`, `#/definitions/Name`)."""
    for _ in range(_REF_DEPTH):
        ref = schema.get("$ref") if isinstance(schema, Mapping) else None
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return schema
        node: Any = root
        for part in ref[2:].split("/"):
            node = node.get(part) if isinstance(node, Mapping) else None
        schema = node
    return None


def closed_values(schema: Any, root: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The closed set of values that this property schema allows, as label
    text in schema order, or None when the schema is not a closed set. A
    set of one value is returned as it is: the caller wants two or more."""
    schema = _resolve(schema, root)
    if not isinstance(schema, Mapping):
        return None
    values: list[str] = []
    if isinstance(schema.get("enum"), list):
        for raw in schema["enum"]:
            if raw is None:
                continue
            text = label_text(raw)
            if text is None:
                return None
            values.append(text)
    elif "const" in schema:
        text = label_text(schema["const"])
        if text is None:
            return None
        values.append(text)
    elif schema.get("type") == "boolean" or (
        isinstance(schema.get("type"), list)
        and set(schema["type"]) in ({"boolean"}, {"boolean", "null"})
    ):
        values += ["true", "false"]
    elif isinstance(schema.get("allOf"), list) and len(schema["allOf"]) == 1:
        # Pydantic wraps a `$ref` with a description in a one-branch `allOf`.
        return closed_values(schema["allOf"][0], root)
    else:
        branches = schema.get("anyOf") or schema.get("oneOf")
        if not isinstance(branches, list) or not branches:
            return None
        for branch in branches:
            resolved = _resolve(branch, root)
            if isinstance(resolved, Mapping) and resolved.get("type") == "null":
                continue
            part = closed_values(resolved, root)
            if part is None:
                return None
            values += part
    return tuple(dict.fromkeys(values)) or None


def closed_fields(schema: Any) -> dict[str, tuple[str, ...]]:
    """Each top-level property of the output schema that is a closed set of
    two or more values: field name -> the values. Empty for a task with no
    output schema, and for a schema with no such property."""
    if not isinstance(schema, Mapping):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return {}
    found: dict[str, tuple[str, ...]] = {}
    for name, prop in properties.items():
        values = closed_values(prop, schema)
        if values is not None and len(values) >= 2:
            found[str(name)] = values
    return found


def _stem(name: str) -> str:
    """A field or key name without the words of `NAME_AFFIXES`."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    words = [w for w in _WORD.findall(spaced.lower()) if w not in NAME_AFFIXES]
    return "_".join(words)


def _canonical(value: Any, values: Sequence[str]) -> str | None:
    """The value of the closed set that `value` is, or None. An exact match
    first, then a match that ignores case and outer spaces when exactly one
    value fits."""
    text = label_text(value)
    if text is None:
        return None
    if text in values:
        return text
    folded = [v for v in values if _fold(v) == _fold(text)]
    return folded[0] if len(folded) == 1 else None


def _reference_values(references: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """reference key -> item id -> the value at that key. A reference that
    is one value is under the key `""`."""
    by_key: dict[str, dict[str, Any]] = {}
    for item_id, reference in references.items():
        if isinstance(reference, Mapping):
            for key, value in reference.items():
                if value is not None and label_text(value) is not None:
                    by_key.setdefault(str(key), {})[item_id] = value
        elif reference is not None and label_text(reference) is not None:
            by_key.setdefault(_WHOLE, {})[item_id] = reference
    return by_key


@dataclass(frozen=True)
class LabelField:
    """One label field: where the given label is, and where the expected
    label is."""

    name: str  # the field of the task output
    values: tuple[str, ...]  # its closed set
    reference_key: str  # the key of the reference; "" = the whole reference


@dataclass(frozen=True)
class LabelSetup:
    """What the check knows about the task, read once: the label fields,
    and the expected label of each train item for each field. `note` says
    why there is no field, or which field was left out."""

    fields: tuple[LabelField, ...] = ()
    expected: dict[str, dict[str, str]] = field(default_factory=dict)
    # field name -> item id -> the expected label, a value of the closed set
    note: str | None = None


def pair_fields(
    fields: Mapping[str, tuple[str, ...]], references: Mapping[str, Any]
) -> tuple[list[LabelField], list[str]]:
    """Pair each closed-set field with the reference key that holds its
    expected label (the module doc gives the two steps). Returns the paired
    fields and the lines of the note: the fields left out, and the fields
    paired by values."""
    by_key = _reference_values(references)

    def hits(key: str, values: Sequence[str]) -> int:
        return sum(1 for v in by_key[key].values() if _canonical(v, values) is not None)

    def fits(key: str, values: Sequence[str]) -> bool:
        return hits(key, values) == len(by_key[key])

    paired: dict[str, str] = {}
    for name, values in fields.items():
        same = [k for k in by_key if k != _WHOLE and _stem(k) == _stem(name)]
        same = [k for k in same if hits(k, values) > 0]
        exact = [k for k in same if k == name]
        if len(exact) == 1 or len(same) == 1:
            paired[name] = (exact or same)[0]
    # A key that two fields claim by name pairs with neither by name.
    claimed = Counter(paired.values())
    paired = {n: k for n, k in paired.items() if claimed[k] == 1}

    left_out: list[str] = []
    by_values: list[str] = []
    for name, values in fields.items():
        if name in paired:
            continue
        if len(values) == 2 and len(fields) > 1:
            left_out.append(
                f"`{name}`: no key of the train references has its name, and a"
                " yes-or-no field is not paired by its values when the output"
                " has other closed-set fields"
            )
            continue
        free = [k for k in by_key if k not in paired.values() and fits(k, values)]
        others = [
            n
            for n, v in fields.items()
            if n != name and n not in paired and len(free) == 1 and fits(free[0], v)
        ]
        if len(free) == 1 and not others:
            paired[name] = free[0]
            key = f"`{free[0]}`" if free[0] != _WHOLE else "the whole reference"
            by_values.append(f"`{name}` with {key}")
        elif not free:
            left_out.append(
                f"`{name}`: no key of the train references holds its expected label"
            )
        else:
            keys = ", ".join(
                f"`{k}`" if k != _WHOLE else "the whole reference" for k in sorted(free)
            )
            left_out.append(
                f"`{name}`: more than one pairing is possible ({keys}"
                + (f"; also fits `{'`, `'.join(others)}`" if others else "")
                + ")"
            )
    ordered = [
        LabelField(name=n, values=fields[n], reference_key=paired[n])
        for n in fields
        if n in paired
    ]
    notes = [f"left out: {'; '.join(left_out)}"] if left_out else []
    if by_values:
        notes.append(f"paired by values, not by name: {'; '.join(by_values)}")
    return ordered, notes


def prepare(schema: Any, references: Mapping[str, Any]) -> LabelSetup:
    """The label fields of a task and the expected labels of its train
    items. `references` holds TRAIN items only: item id -> the reference of
    the eval input. The caller makes sure of that (`load_setup`)."""
    if not isinstance(schema, Mapping):
        return LabelSetup(
            note="the task has no output schema, so code cannot tell that it"
            " is a classifier"
        )
    fields = closed_fields(schema)
    if not fields:
        return LabelSetup(
            note="the output schema has no top-level field with a closed set"
            " of values, so the task is not a classifier for this check"
        )
    paired, notes = pair_fields(fields, references)
    by_key = _reference_values(references)
    expected: dict[str, dict[str, str]] = {}
    for label_field in paired:
        labels = {
            item_id: label
            for item_id, value in by_key.get(label_field.reference_key, {}).items()
            if (label := _canonical(value, label_field.values)) is not None
        }
        expected[label_field.name] = labels
    return LabelSetup(
        fields=tuple(paired), expected=expected, note=" ".join(notes) or None
    )


def given_label(run: Mapping[str, Any], label_field: LabelField) -> str | None:
    """The label that one stored row gave for this field: a value of the
    closed set, or None when the output holds none (not JSON, no such
    field, another value)."""
    output = _row_output(dict(run))
    if output is None:
        return None
    try:
        parsed = json.loads(output)
    except ValueError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    return _canonical(parsed.get(label_field.name), label_field.values)


def exact_two_sided_p(b: int, c: int) -> float | None:
    """The exact two-sided McNemar p for `b` disagreements one way and `c`
    the other way: a binomial test of b against b + c at one half. None
    when there is no disagreement."""
    n = b + c
    if n == 0:
        return None
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def _percent(share: float) -> str:
    return f"{share * 100:.0f}%"


def spread(
    label_field: LabelField,
    expected: Mapping[str, str],
    given: Mapping[str, str | None],
    *,
    rc_id: str,
    role: Role,
    alpha: float = SIGNIFICANCE_ALPHA,
) -> LabelSpread | None:
    """One field's spread. `expected`: item id -> the expected label.
    `given`: item id -> the label the run config gave, or None for an
    output with no value of the closed set. Only an item in both is
    counted. None when no item is in both."""
    items = sorted(set(expected) & set(given))
    if not items:
        return None
    n = len(items)
    expected_counts = Counter(expected[i] for i in items)
    given_counts = Counter(g for i in items if (g := given[i]) is not None)
    outside = n - sum(given_counts.values())
    order = {value: index for index, value in enumerate(label_field.values)}
    counted = sorted(
        (v for v in label_field.values if expected_counts[v] or given_counts[v]),
        key=lambda v: (-max(expected_counts[v], given_counts[v]), order[v]),
    )
    # The class given most often. A tie goes to the class with the larger
    #   excess over its expected count, then to schema order.
    top = min(
        label_field.values,
        key=lambda v: (
            -given_counts[v],
            -(given_counts[v] - expected_counts[v]),
            order[v],
        ),
    )
    top_given = given_counts[top] / n
    top_expected = expected_counts[top] / n
    # From the counts, not the shares: the same value, and exact at the limit.
    #   A class given less often than expected (many outputs hold no label)
    #   takes no items of other classes: 0, not a negative share.
    fraction = (
        max(given_counts[top] - expected_counts[top], 0) / (n - expected_counts[top])
        if expected_counts[top] < n
        else None
    )
    into = sum(1 for i in items if given[i] == top and expected[i] != top)
    out_of = sum(1 for i in items if expected[i] == top and given[i] != top)
    # The test is about items moved INTO `top_label`: none when they do not
    #   outnumber the items moved out of it.
    p_value = exact_two_sided_p(into, out_of) if into > out_of else None
    collapsed = (
        fraction is not None
        and fraction >= COLLAPSE_FRACTION
        and p_value is not None
        and p_value < alpha
    )
    fact = (
        f"label spread of `{label_field.name}` on {n} train item(s): `{top}` is"
        f" given to {given_counts[top]} ({_percent(top_given)}) and expected on"
        f" {expected_counts[top]} ({_percent(top_expected)})"
    )
    if fraction is not None:
        fact += (
            f"; net, {_percent(fraction)} of the items that expect"
            f" another class got `{top}` (limit {_percent(COLLAPSE_FRACTION)}"
            + (f", p = {p_value:.3g}" if p_value is not None else "")
            + ")"
        )
    if outside:
        fact += f"; {outside} output(s) hold no value of the closed set"
    fact += ": collapsed" if collapsed else ": not collapsed"
    return LabelSpread(
        rc_id=rc_id,
        role=role,
        output_field=label_field.name,
        reference_key=label_field.reference_key,
        n_items=n,
        classes=[
            LabelCount(label=v, expected=expected_counts[v], predicted=given_counts[v])
            for v in counted[:SHOWN_CLASSES]
        ],
        more_classes=max(len(counted) - SHOWN_CLASSES, 0),
        predicted_outside=outside,
        top_label=top,
        top_predicted_share=top_given,
        top_expected_share=top_expected,
        collapse_fraction=fraction,
        p_value=p_value,
        collapsed=collapsed,
        collapse_limit=COLLAPSE_FRACTION,
        fact=fact,
    )


def spreads_of(
    setup: LabelSetup,
    runs: Iterable[Mapping[str, Any]],
    train_ids: frozenset[str] | set[str],
    *,
    rc_id: str,
    role: Role,
) -> list[LabelSpread]:
    """The spread of each label field for one run config, from its stored
    train rows. A row whose item is not in `train_ids` is dropped before
    anything is read from it. Several rows for one item (several evals, or
    replicates) count one time: the last row with an output."""
    kept = [dict(r) for r in runs if _item_id(dict(r)) in train_ids]
    with_output = [r for r in kept if _row_output(r) is not None]
    by_item = _runs_by_item(with_output)
    out: list[LabelSpread] = []
    for label_field in setup.fields:
        given = {i: given_label(run, label_field) for i, run in by_item.items()}
        row = spread(
            label_field,
            setup.expected.get(label_field.name, {}),
            given,
            rc_id=rc_id,
            role=role,
        )
        if row is not None:
            out.append(row)
    return out


# ---- The project on disk and the run ----


def train_references(
    project_root: Path, task_id: str, train: TrainSet
) -> tuple[Any, dict[str, Any]]:
    """(the task's output schema, item id -> reference) from the project on
    disk, for the eval inputs of the train set only. The folder of an eval
    input is named by its id, so the record of an item that is not train is
    never opened. An eval input in a folder with another name is read for
    its id and kept only when it is train."""
    schema: Any = None
    references: dict[str, Any] = {}
    for task_dir in task_dirs(project_root, task_id):
        task_file = task_dir / "task.kiln"
        if schema is None and task_file.is_file():
            raw = json.loads(task_file.read_text()).get("output_json_schema")
            schema = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
        inputs = task_dir / "eval_inputs"
        by_name = {i: inputs / i / "eval_input.kiln" for i in train.eval_inputs}
        if all(path.is_file() for path in by_name.values()):
            docs = [json.loads(path.read_text()) for path in by_name.values()]
        else:
            docs = [d for _p, d in _children(inputs, "eval_input.kiln")]
        for doc in docs:
            item_id = str(doc.get("id"))
            if item_id in train.eval_inputs and "reference" in doc:
                references[item_id] = doc["reference"]
    return schema, references


def load_setup(ctx: Ctx) -> LabelSetup:
    """The run's `LabelSetup`, read from disk one time and kept on the
    context. A project that cannot be read gives a setup with no field and
    a note, and it is read again at the next call."""
    if ctx.label_setup is not None:
        return ctx.label_setup
    project_root = Path(ctx.target.project_path).expanduser().resolve()
    try:
        if ctx.train_set_path is not None and ctx.train_set_path.is_file():
            train = TrainSet.load(ctx.train_set_path)
        else:
            train = compute_train_set(project_root, ctx.target.task_id)
        schema, references = train_references(project_root, ctx.target.task_id, train)
    except (TrainSetError, QuarantineError, OSError, ValueError) as exc:
        return LabelSetup(note=f"the project on disk could not be read ({exc})"[:300])
    setup = prepare(schema, references)
    if train.task_runs:
        v1 = (
            f"{len(train.task_runs)} train item(s) are dataset task runs (a V1"
            " eval), which have no expected label, so they are not counted"
        )
        setup = LabelSetup(
            fields=setup.fields,
            expected=setup.expected,
            note=f"{setup.note} {v1}" if setup.note else v1,
        )
    ctx.label_setup = setup
    ctx.label_train_ids = train.eval_inputs
    return setup


def log_spreads(
    ctx: Ctx, rc_id: str, role: Role, rows: Sequence[LabelSpread], note: str | None
) -> None:
    """One `label_spread` event for one run config: its rows, and a line
    for the terminal when a row is collapsed. The planner's read repeats
    every wave, so the line is given one time for each run config."""
    collapsed = [row for row in rows if row.collapsed]
    line: dict[str, object] = {}
    if collapsed and rc_id not in ctx.label_spread_said:
        ctx.label_spread_said.add(rc_id)
        line = {"line": f"label spread, {role} {rc_id}: {collapsed[0].fact}"}
    ctx.log.event(
        "label_spread",
        rc=rc_id,
        role=role,
        collapsed=bool(collapsed),
        spreads=[row.model_dump(mode="json") for row in rows],
        note=note,
        **line,
    )


async def run_config_spreads(
    ctx: Ctx,
    rc_id: str,
    role: Role,
    evals: Sequence[tuple[str, str]],
    *,
    runs: Sequence[Mapping[str, Any]] | None = None,
) -> list[LabelSpread]:
    """The spread of each label field for the run config `rc_id`, from its
    stored train rows on `evals` (eval id, eval config id), or from `runs`
    when the caller has just read them. Empty, with no read of the server,
    for a task that is not a classifier. A read that fails drops that eval,
    never the wave."""
    setup = load_setup(ctx)
    if not setup.fields:
        return []
    rows: list[Mapping[str, Any]] = list(runs or [])
    if runs is None:
        for eval_id, config_id in evals:
            try:
                rows += await ctx.analyzer.stored_results(
                    eval_id, config_id, rc_id, "train"
                )
            except (KilnError, AnalysisError):
                # A failed read, or a train split that returned a held-out
                #   item (`HeldOutLeakError`): that eval only.
                continue
    return spreads_of(setup, rows, ctx.label_train_ids, rc_id=rc_id, role=role)
