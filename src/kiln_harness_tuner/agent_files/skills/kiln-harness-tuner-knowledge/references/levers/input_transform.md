---
title: The input transform
description: The Jinja template on a run config that renders the task input into the first user message. A change is a new run config. Single-turn tasks only.
reviewed: 2026-11-25
gateway_ops:
  - get_task
  - get_run_config
  - list_train_items
  - validate_input_transform
  - create_run_config
  - run_task
---

# The input transform

## What it is

The input transform is a property of a run config: `input_transform`. It
is null, or `{"type": "jinja", "template": "<text>"}`. When it is set,
Kiln renders the template against the task input on each run, and the
result is the first user message that the model reads. When it is null,
the model reads the input as it is.

Kiln stores the original input on the task run. The rendered text is in
the trace, as the first user message. In a trace file of the gateway, the
`Input` of the header is the original, and the first `user` block is what
the model read. A judge or a code scorer that reads the task input reads
the original. A judge that reads the full trace also sees the rendered
message.

The template reads one variable, `input`:

- a structured input is the object itself, so a field is `input.field`;
- a plaintext input that parses as JSON is the parsed value;
- any other plaintext input is the text.

The template language is Jinja2 in a sandbox. It has the built-in Jinja
filters and tests (`truncate`, `length`, `join`, `sum`, `min`, `max`,
`sort`, `default`, `selectattr`, `tojson`) and two more filters:
`fromjson`, and `format_trace` for a conversation trace. It has no
import, no file and no network.

## How the tuner changes it

1. Read the parent's value: `get_run_config` gives
   `run_config_properties.input_transform`. Read `get_task`:
   `turn_mode` and the input schema.
2. Read real inputs with `list_train_items`, to see which fields exist,
   which are optional and how long they are.
3. Write the template. Start from the parent's template when it has one.
   Change only what the experiment's hypothesis names.
4. Check it with `validate_input_transform`. The answer says if Kiln
   compiles the template (`valid`, `error`), if the lever applies to this
   task (`applies`), and on which train inputs the template fails
   (`render_failures`). `sample` is the first user message for one train
   item, with no model call. Name an item with `item_id` to see its text.
5. Create a new run config with `create_run_config`. Copy every property
   of the parent, and set `input_transform`. A template that fails on a
   train input is refused.
6. Drive two or three train items with `run_task`, and read the first
   `user` block in each trace file. It is the rendered template.

Rules of the object:

- **The properties of a run config are fixed when it is created.** A new
  template is a new run config.
- **A field that the input does not have fails the run.** The template is
  strict: an expression that prints `input.note` on an input with no
  `note` is an error, and the item gets no output. Guard an optional field
  with `{% if input.note is defined %}` or with `| default("")`.
- **The template renders text.** Write an object with `| tojson` when the
  model must read JSON.
- **Whitespace**: a line that holds only a block tag (`{% if %}`,
  `{% for %}`) leaves no empty line.
- **`truncate` has a tolerance.** It leaves a text that is up to 5
  characters over the limit whole. A slice (`input.text[:500]`) cuts at
  the exact length.
- On every other lever, copy the parent's `input_transform` as it is.

## Limits

- **Single-turn tasks only.** On a multi-turn task `validate_input_transform`
  answers `applies: false`, `create_run_config` refuses a new template,
  and the loop ends an arm that changes the transform.
- **No value from a train item goes into the template**: no item id,
  number, sentence or expected value, and no condition that fits only a
  few train inputs. Code searches each build for copies before it measures
  the build.
- **No text copied from an eval or its judge.** The judge-copy check reads
  the template.
- **A template uses the input only.** It never has the expected output or
  a label, and it cannot call a tool or a model.
- **It changes what the model reads, not what the model returns.** The
  output schema and the output mode are other levers.
- The render check reads train inputs only. An input of another split,
  or of production, can lack a field that every train input has: guard
  each field that the input schema does not require.
- The language has no date parsing. Dates in one ISO form can be compared
  as text. A difference in days cannot be computed.
- A template is at most 20,000 characters. Instructions belong in the
  prompt: a template holds the data and its labels.
- Code checks that the lever was applied. An arm whose `technique` is
  `input_transform` and whose transform is the parent's ends as "lever not
  applied". So does such an arm whose first user message is the parent's
  on every train item.

## See also

- The `kiln-harness-tuner-gateway` skill: the fields of each operation.
- The `kiln-harness-tuner-experiment-build` skill: the build discipline,
  provenance, and what text you may add.
- The `kiln-api` skill, `post_api_validate_input_transform_template.md`:
  Kiln's compile check.
