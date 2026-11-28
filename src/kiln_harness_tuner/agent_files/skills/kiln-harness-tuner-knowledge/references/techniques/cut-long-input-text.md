---
title: Cut long input text
description: An input transform that passes only the first part of a very long text field, and says in the message that the text was cut, when the task can be decided from that part.
reviewed: 2026-11-25
status: active
levers:
  - input_transform
applies_to:
  - single_turn
cost: low
---

# Cut long input text

## When to use

- One text field of the input is very long on some items: a description,
  a thread, a log, a page of text.
- The answer can be decided from a part of that text that has a known
  place, for example its start.
- A few very long items take most of the input tokens, or the errors are
  more frequent on the long items than on the short ones.
- A run ends at the context limit of the model on the longest items.

## When not to use

- **The decisive part can be anywhere in the text**, or is often at the
  end. A cut then removes the answer on some items. Check where the
  decisive sentence is in a sample of long train items before you build.
- **The task is to summarise or to quote the whole text.** The model
  needs all of it.
- **The long content is many fields, not one text.** Use
  [keep only the fields the task needs](keep-only-the-fields-the-task-needs.md).
- **The task is multi-turn.** The lever does not apply.

## Alternatives

- [Keep only the fields the task needs](keep-only-the-fields-the-task-needs.md),
  when the input is wide, not long.
- [Cap and preview wrapper](cap-and-preview-wrapper.md), when the long
  text comes from a tool result. The wrapper can also give the agent a way
  to ask for more, which a template cannot.

## How

The change is a new template on a new run config. See the lever
[input_transform](../levers/input_transform.md) for the template language,
the steps and the limits.

1. **Measure the field** on the train items: its usual length, and its
   length on the longest items.
2. **Choose the limit** from where the decisive content is, not from a
   round number. Set it above the usual length, so that most items are
   not cut.
3. **Cut in the template**, with the `truncate` filter or a slice.
4. **Say that the text was cut**, in the message, next to the field. A
   model that reads a cut text as complete can answer "the text does not
   say" when the text did say.
5. Pass the other fields as they are.

## Check that it took effect

Drive the longest train item and one short item. In the trace of the long
item, the first user message ends the field at the limit and has the note
that it was cut. In the trace of the short item, the field is whole and
has no note. Compare the input tokens of the long item with its parent's.

## Budget

Low cost: one template and a measurement. Under a cost or speed objective
it is a good early step when a few long items take most of the tokens.
Under a quality objective, leave it out unless the long items fail more
often than the short ones.

## If it does not move the evals

It depends. Keep it when the input tokens or the latency fall and no eval
goes down. Read the result on the long items alone: they are the only
items that the change touches, and a loss there is hidden in the total
score when they are few. When the long items lose, the cut removed
content that decides the answer: raise the limit, or drop the change.

## Priors

None yet.

## Confidence

Medium that a very long input can lower accuracy and that it raises cost:
public research reports lower accuracy as the context grows and as
distractors are added (Chroma, "Context Rot", 2025). Low for a given
task, because the result depends on where the decisive content is. No
measured case is recorded here yet.
