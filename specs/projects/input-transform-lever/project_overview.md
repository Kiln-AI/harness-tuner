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
  filters and one more, `fromjson`.
- Kiln compiles the template when a run config is saved (HTTP 422 on a
  syntax error). `POST /api/validate_input_transform_template` runs the
  same compile check with no save. No endpoint renders a template.
- The task run keeps the original input. The trace has the rendered text
  as the first user message.
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
5. **The template is one more place where a run config gives the model
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
6. **The lever must be applied.** Two checks, in code:
   - after the build: an item that names the lever, and whose transform is
     the parent's, ends as `lever not applied`, before any review or train
     spend;
   - after the train measurement: the first user message of each stored
     train row against the parent's on the same item. The same message on
     every paired item (at least 2) ends the item as `lever not applied`.
     Fewer pairs leave the lever unverified, and the item goes on.
   `levers.lever_verdict` has the same signal, for arms with timing
   drives.
7. **`get_task` gives `turn_mode`.** The planner and the builder need it
   to know if the lever applies. This closes part of gap 4.
8. **Knowledge.** One lever document, the four techniques of the backlog
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
- the wave log has `input_transform` events: `changed` after the build,
  then `applied`, `unverified` or `not_applied` after the train
  measurement, or `refused` on a multi-turn task;
- the experiment record has one line `input transform: the parent has …,
  the arm has a template of N characters; input transform applied: …`;
- the report shows the technique of the item and the builder's `change`
  block, as for every lever.

## 5. Not in this project

- The other moves of the feature-engineering project: wrap user text in
  tags, reformat, codes into words, relative dates, examples chosen for
  each input. Its research list is open.
- The same moves on a tool result. The `new_tool` lever does that today
  (`cap-and-preview-wrapper`).
- A code detector for the two smells. A reader of the design finds them.
- Multi-turn tasks (decision 1) and screens (decision 3).
- A render operation. Kiln has no endpoint for it. The builder drives a
  train item and reads the first user message in the trace.
