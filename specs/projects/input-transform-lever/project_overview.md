---
status: complete
---

# The input transform lever

This project makes the input transform a lever of the tuner. It closes
gap 1 of
[kiln_gaps.md](../biological_bootloader/kiln_gaps.md) and the backlog entry
`Input transforms`. The larger idea that uses the lever, with its research
list, is the project
[feature-engineering](../feature-engineering/project_overview.md). That
project is not finished here.

## 1. The brief (2026-11-25)

Kiln's run config has the property `input_transform`: a Jinja2 template
that Kiln renders against the task input at run time. The result is the
first user message. Before this project the tuner passed the field
through. No lever covered it, the gateway did not validate a template, and
the integrity checks did not read it.

The order of work: the capability first, then the knowledge.

## 2. What Kiln does

Read in the public `kiln-ai` package, version 1.2.1 (`datamodel/input_transform.py`,
`utils/jinja_engine.py`, `adapters/model_adapters/base_adapter.py`), and
in the vendored API reference.

- The value is `{"type": "jinja", "template": "<text>"}` or null. `jinja`
  is the only type.
- The template reads one variable, `input`. A structured input is the
  object. A plaintext input that parses as JSON is the parsed value. Any
  other plaintext input is the text.
- The environment is a Jinja2 sandbox with strict undefined values: a
  field that the input does not have fails the run. It has the built-in
  filters and two more, `fromjson` and `format_trace`. It can select
  fields, cut text, and compute counts, sums, minima, maxima and sorted
  lists. It has no date arithmetic.
- Kiln compiles the template when a run config is saved (HTTP 422 on a
  syntax error). `POST /api/validate_input_transform_template` runs the
  same compile check with no save. No endpoint renders a template.
- The task run keeps the original input. The trace has the rendered text
  as the first user message: the adapter builds the chat messages from the
  rendered input (`base_adapter.invoke`, `litellm_adapter._run`). So a
  judge or a code scorer that reads the task input reads the original, and
  a full-trace judge also sees the rendered message
  (`adapters/eval/g_eval.py`).
- A session on a live server (API version 1.2.0) confirmed, with one small
  run each: the validation route, the rendered first user message (in an
  eval job too), the original input on the saved run, and the parsed JSON
  input.
- The adapter renders the template on each call, so on a multi-turn task
  it renders the user message of each turn. The API reference describes
  the field only as "producing the first user message".

## 3. Decisions

1. **Single-turn tasks only.** The API reference and the owner's notes
   describe the transform for a single-turn task. On a multi-turn task the
   template would also change each later user message, and the tuner has
   no evidence for that. So the first version refuses the lever there, at
   three places: the gateway refuses a new template, the loop ends an item
   that names the lever before its build, and the loop ends an arm that
   changes the transform. A transform that the baseline already has is
   copied on any task. *Owner decision to confirm.*
2. **The lever kind is `input_transform`.** It is the `technique` of an
   experiment and the id of the lever document, like the other levers.
   `FixWork.technique` stays a free string.
3. **A template is authored text, so only a build session changes it.**
   The gap asked to make the field screenable. A screen and a code probe
   fork a run config in code: no validation, no smoke, and the measurement
   comes before the copy checks. So the plan schema refuses
   `input_transform` in a screen candidate's and a probe check's
   `overrides`. The probe agent can still make a variant through the
   gateway. *Owner decision to confirm.*
4. **One validation operation, and the same rules on the create.**
   `validate_input_transform` returns Kiln's compile check and says if the
   lever applies to the task. `create_run_config` refuses a value of the
   wrong shape, a template that Kiln does not compile, a template over
   20,000 characters, and a new template on a multi-turn task. The roles
   that create run configs have the operation: `create_experiment_rc` and
   `probe`.
5. **The gateway renders a new template on every train input.** Kiln's
   compile check finds a syntax error only. A template that reads a field
   that some inputs lack fails at run time on those inputs, and the
   measurement would pay for it. Kiln has no render endpoint, but the
   tuner depends on the `kiln-ai` library, which has Kiln's own engine
   (`kiln_ai.utils.jinja_engine.render_input_transform`). So
   `validate_input_transform` names the train inputs that fail and returns
   the rendered text of one train item, and `create_run_config` refuses a
   new template that fails on a train input. When the installed library
   has no engine, or does not compile a template that the server compiled,
   nothing is rendered and the answer says so: the create is not refused.
   The render reads train inputs only.
6. **The template is one more place where a run config gives the model
   text.** Every text reader gets it from one function
   (`prompt_text.whole_texts`, "the input transform"), so the limits are
   the prompt's:
   - the generality check: the exact-match search on the added lines, and
     the review of the new template or of its diff;
   - the judge-copy check after the build, with the baseline's text
     exempt;
   - the baseline audit, both halves;
   - check (a) of the promotion gate, which blocks, and the integrity
     review, which flags.
   The reference check does not read the template: its expressions name
   input fields, which the model is not sent.
7. **The lever must be applied.** Two checks, in code:
   - after the build: an item that names the lever, and whose transform is
     the parent's, ends as `lever not applied`, before any review or train
     spend;
   - after the train measurement: the first user message of each stored
     train row against the parent's on the same item. The same message on
     every paired item (at least 2) ends the item as `lever not applied`.
     Fewer pairs leave the lever unverified, and the item goes on.
   `levers.lever_verdict` has the same signal, for arms with timing
   drives.
8. **`get_task` gives `turn_mode`.** The planner and the builder need it
   to know if the lever applies. This closes part of gap 4.
9. **Knowledge.** One lever document, the four techniques of the backlog
   entry, two smells (an input with fields the task does not need, a very
   long input field) and one failure playbook (the task input misleads
   the model). Two existing playbooks got one link each: `cheaper-or-faster`
   (a new step 4) and `know-the-agent-before-you-plan` (an input fact and
   one starting point). Playbooks are owned, so these are for the owners
   to confirm. No prior is recorded: no case was measured.

## 4. What a finished run shows

For an experiment that used the lever:

- the plan item has `work.technique: input_transform`, and the experiment
  record's label and `technique:` line carry it;
- the run config of the arm differs from its parent in
  `run_config_properties.input_transform`;
- the wave log, `runs/<run_id>/wave_log.jsonl` in the state folder, has
  `input_transform` events: `changed` after the build, then `applied`,
  `unverified` or `not_applied` after the train measurement, or `refused`
  on a multi-turn task. Its `item_change` event holds the builder's
  `change` block;
- the experiment record (Kiln memory, task scope) has one line
  `input transform: the parent has …, the arm has a template of N
  characters; input transform applied: …`;
- the trace files, `runs/<run_id>/scratch/traces/<run_config_id>/…`, show
  the rendered template as the first `user` block of each item;
- the report, `reports/kiln-harness-tuner-<run_id>.md`, section
  "Experiments, by wave": the report skill tells the writer to put
  `lever: input_transform` and the record's line on the item's line. The
  report writer is an agent, so this line is not code-made.

## 5. Not in this project

- The other moves of the feature-engineering project: wrap user text in
  tags, reformat, codes into words, relative dates, examples chosen for
  each input. Its research list is open.
- The same moves on a tool result. The `new_tool` lever does that today
  (`cap-and-preview-wrapper`).
- A code detector for the two smells. A reader of the design finds them.
- Multi-turn tasks (decision 1) and screens (decision 3).
- A render check on val or test inputs. The loop never reads them, so a
  field that only a held-out input lacks is found at the val gate, as
  failed items.
- A start-up check for the Kiln routes of the lever. See section 6.

## 6. What the lever needs from Kiln

- The run-config property `input_transform` and the route
  `POST /api/validate_input_transform_template`. Both are in the vendored
  API reference and on a server of API version 1.2.0. The start-up check
  (`onboarding/kiln_check.py`) does not test for them, so a run on an
  older server starts. There, `validate_input_transform` is refused with
  the name of the missing route, and `create_run_config` leaves the
  compile check to Kiln's save.
- For the render check: a `kiln-ai` library with
  `kiln_ai.utils.jinja_engine` (read at version 1.2.1). The package asks
  for `kiln-ai>=0.26`. With an older library nothing is rendered, and the
  answer says so.
