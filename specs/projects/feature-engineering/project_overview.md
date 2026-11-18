---
status: draft
---

# Feature engineering on the data that reaches the model

**Status of this document.** A record of the owner's idea, so that the
project is easy to start later. The owner gave it on 2026-11-18. It is not
started. The research is not done. Section 7 lists what to find out.

## 1. The owner's notes

These are the owner's words, kept as they were given:

> Basic idea: filter/fix the data reaching the model
>
> * remove unnecessary data
> * change the set of fields returned (same field 3 ways can make model
>   over index on it)
> * apply logic for better features: `is_open = !closed && !archived && !expired`
> * truncate very long data: maybe we accept first 500 chars of
>   "description" but don't want to flood the context with it. For token
>   efficiency
> * Formatting: improve the format of the data reaching LLM (wrap user
>   inputs in <xml> blocks, etc)
> * fix labels: if a json field named "closed_date" isn't the date it
>   closed... rename it!
> * etc
>
> It can be done with input transforms for single turn tasks with
> structured input, or with code tool wrappers for tools calls.

## 2. The idea

The model can only reason about the data that it is given. Often that data
was made for a different reader: a database row, an API response, a form
export. It has fields that the task does not need, the same fact more than
one time, names that do not say what a field holds, and values that the
model must combine before they are useful.

Feature engineering is the classic ML name for this work: change the inputs,
not the model. For an LLM agent, the harness can do it in code before the
model sees the data. Code does it the same way on every item, at no token
cost, and the model does not have to work it out again on each run.

## 3. The two surfaces

One technique, two places to apply it:

1. **The input transform** of a single-turn task with structured input.
   This is the Jinja template on the run config (`JinjaInputTransform` in
   Kiln). It renders the task input into the first user message. The tuner
   cannot reach this field today: see the backlog entry `Input transforms`.
   So this surface depends on that gap.
2. **A code tool wrapper** for tool calls. The wrapper calls the original
   tool and changes its result before the model reads it. The `new_tool`
   lever can do this today. So this surface does not wait on the gap.

The moves in section 4 are the same on both surfaces. Only the mechanism
is different.

## 4. The moves

Each move has the owner's example and the reason it helps.

### 4.1 Remove data the task does not need

Internal ids, timestamps, audit fields, metadata, empty and null fields.
Irrelevant context lowers accuracy, and even one distractor can do harm
(see the research in section 6). It also costs tokens on every call.

### 4.2 Change the set of fields: one fact, one field

When the same fact is in three fields (for example `status: "closed"`,
`is_closed: true` and `closed_at: "2026-01-04"`), the model can give that
fact too much weight. It reads three signals where there is one. Keep one
field for each fact.

### 4.3 Compute better features with logic

Example: `is_open = !closed && !archived && !expired`. The model does not
have to combine three flags on each item, and it cannot get the logic wrong.
The same applies to counts, sums, date differences ("days since the last
reply") and comparisons. Models are not reliable at arithmetic and at date
logic. Code is.

### 4.4 Cut very long data

Example: keep the first 500 characters of `description`. The agent gets
enough to decide, and the context is not flooded. This is for token
efficiency, and it also removes distractors. Say in the data that the text
was cut, so that the model does not treat it as complete. For tool results,
the knowledge skill already has `cap-and-preview-wrapper`.

### 4.5 Improve the format

Example: wrap user inputs in XML blocks (`<user_post>…</user_post>`). Clear
boundaries tell the model which text is data and which is instruction. This
also marks untrusted text. Other format changes: flatten deep nesting, use
`key: value` lines, keep the same field order on each item.

### 4.6 Fix wrong labels

Example: a JSON field named `closed_date` that is not the date it closed:
rename it. The model reads a field name as its meaning. A wrong name is a
wrong instruction that comes with every item, and no prompt can fully
correct it. Rename the field to what it holds, or add a computed field with
the correct meaning.

### 4.7 More moves (to research)

The owner's list ends with "etc". Candidates to check, not from the owner:

- Codes into words: `status: 3` into `status: "shipped"`. The backlog entry
  `Readable names in place of opaque ids` is the same move for ids.
- Relative dates: "3 days ago", computed against the item's own date.
- One form for each kind of value: ISO dates, one unit, one currency.
- Sort a list so that the rows that matter come first.
- Examples chosen for each input (dynamic few-shot). The research lists it
  as an input transform. The backlog has `Examples chosen for each input`.

## 5. How the tuner finds the need

The knowledge file must tell the planner what in the traces points to this
technique. Signals to start from:

- The model quotes or relies on a field that the task does not need.
- The model misreads a field in the way its name suggests (4.6).
- Errors on items where flags must be combined (4.3), or on date and number
  logic.
- One long field takes most of the input tokens, or errors cluster on items
  with long fields (4.4).
- The model follows text inside the user input as an instruction (4.5).
- The same failure on many items, and the data that causes it is the same
  kind on each.

## 6. Risks

- **A field the label depends on is removed.** Check per class, not only
  the total score.
- **A cut removes the decisive part.** For example, the important sentence
  is at the end of a long text.
- **A computed feature has a bug.** Then it is wrong on every item, not on
  some. Keep the logic simple and test it on train items first.
- **Leakage.** A computed feature uses only the input. It never uses the
  expected output, a label or anything from the eval.
- **Overfit.** A feature made to fix a few train items can fail at val.
  The val confirmation in the loop guards against this, but the planner
  must not tune a feature to single items.
- **The data changes in production.** The transform must handle a missing
  or new field without an error.

## 7. Research to do

The research note
[input-transforms.md](../biological_bootloader/research/harness-optimization/architecture-levers/input-transforms.md)
already covers part of this: irrelevant context, cutting text, computed
features, format by model, and untrusted text. Its gap: it found no
controlled study of field removal for LLM classifiers. It is also mostly
about classifiers. Still to find:

- Evidence for duplicate fields and over-weight (4.2). Is there a study, or
  only practice?
- Evidence that a misleading field name changes model output (4.6).
- Field naming and schema design advice for tool results: vendor guides on
  tool design (Anthropic, OpenAI) and the MCP practice.
- Classic ML feature engineering: which lessons carry over to LLM inputs
  and which do not.
- Format effects on current strong models compared with weak ones.
- Cases where a cleaner input made no difference, so the technique says
  when not to use it.

## 8. Open questions

- One technique file for both surfaces, or one for each? One file keeps
  the moves in one place. Two files match the two levers.
- Does `cap-and-preview-wrapper` stay separate, with this technique
  pointing to it for 4.4 on tool results?
- Can the tool-result half ship first, before the `Input transforms` gap is
  closed?
