# Acceptance record: Biological Bootloader

This record says how each acceptance item of
[functional spec section 15](./functional_spec.md) was checked at the end
of phase 8, on 2026-11-13. Items 1 and 2 need an agent, and item 5 needs a
live run, so the offline suite cannot check them. This record says how
items 1 and 2 were checked, and why item 5 is still open.

| # | Item | Checked by | Result |
|---|---|---|---|
| 1 | An agent with the maintenance skill adds a technique as one file | A fresh agent session (below) | Passed |
| 2 | The same agent stops on guidance that conflicts | A fresh agent session (below) | Passed |
| 3 | The templates load their entry files; the lever ladder is gone | `test_each_role_template_names_its_entry_file`, `test_the_lever_ladder_file_is_gone_and_nothing_names_it`; the place of each ladder row was checked by hand, row by row, in phase 5 (commit 8c43373) and phase 6 (6fe372b). Row 14's "pipeline step outside the model" has no technique; it is in the backlog. | Passed |
| 4 | Each structural rule has a test that a violation fails | `tests/test_knowledge_checks.py` (one violation for each rule), `tests/test_backlog.py` | Passed |
| 5 | A demo-project run shows a detection in the planner's input and in the report | Not run live (below). The offline path is shown, labelled as such. | Open: for the owner |
| 6 | The reviewer runs before wave 1 and after a promotion; its findings are smell issues; a failed review does not stop the run | `tests/test_baseline_review.py`. Two recorded deviations (phase 7, commit edd6db4): the review runs at the top of wave 1, before the planner; and it is not dispatched on a wrap-up wave, or with less than 35 minutes left (the 15-minute wave floor plus the review's 20-minute timeout). The merge of main removed the time-based skip: main checks the time limit at wave boundaries only, and has no wave floor, so the review is now skipped on a wrap-up wave only. | Passed (offline) |
| 7 | `AGENTS.md` has the new rule | `test_agents_md_points_to_the_maintenance_skill`, and the working rules in `AGENTS.md` | Passed |
| 8 | `pytest`, `ruff check`, `ruff format --check` pass | The three commands | Passed |

The wheel smoke test (`uv build`, then
`python tests/packaging/smoke_installed_wheel.py --dist dist`) also passed.

## Items 1 and 2: the maintenance skill

**Method.** Two clones of the repository at commit `edd6db4` (phase 7), in
a scratch folder outside this tree. In each clone, one new `claude -p`
session (Claude Code 2.1.322), with the tools Bash, Read, Edit, Write, Glob
and Grep, and no other context. The session loads the root `AGENTS.md`
through `CLAUDE.md`, as any coding agent in this repository does. Each
prompt was a request as a person writes it. Neither prompt named the
maintenance skill. Nothing from either clone is committed.

**Item 1.** The request: add the approach "readable names in place of
opaque ids" (a tool result gives a readable name next to each opaque id,
and keeps the id), with a short description, no source and no case study.

What the agent did:

- It read the maintenance skill, ran the check first, searched the
  techniques for an existing one, and found none that covers the approach.
- It copied the technique template to
  `references/techniques/readable-names-next-to-ids.md` and filled every
  section. Lever `new_tool`, shape `tools`, cost `low`, Priors "None yet.",
  Confidence with its basis. Alternatives link three existing techniques,
  each with its condition.
- It ran `build`. The generated blocks of `builder.md`, `report.md` and
  `technique-index.md` got one row each.
- It removed the idea's entry from `specs/backlog.md`, because the idea
  is now written.
- It did not edit a playbook, because playbooks are owned. It proposed a
  new step for `fit-the-tool-layer-to-the-agent` and stopped for the
  owners' agreement.
- It did not commit.

The diff: one new file, three generated rows, and one backlog entry
removed. The backlog edit is outside the literal criterion ("one new file
plus index and link edits"). It is accepted, because the maintenance skill
keeps the backlog as the list of ideas that are not written yet, and this
idea is now written: the removal keeps that list true. An independent run in the clone after the session: the knowledge
check reports no problem; `test_knowledge.py`, `test_knowledge_checks.py`,
`test_backlog.py`, `test_docs.py` and `test_agent_templates.py` pass;
`ruff check` and `ruff format --check` pass.

One observation for the owners. The agent noted that a technique that no
playbook links to is reachable by the builder (`builder.md`) and through
`technique-index.md`, but the planner reaches techniques through
playbooks. So a new technique has little effect on the plans until a
playbook owner adds it to a playbook. This is the design (functional spec
section 5), not a defect.

**Item 2.** The request, exactly:

```
Please record this in the tuner's knowledge system.

We learned that a prompt line about the output format does not help. When
the output breaks a format rule, the first fix is to switch to a stronger
model, or to add a JSON schema. Do not add format rules to the prompt:
models ignore them. Please update the knowledge system so that the planner
tries a model swap first for format failures.

Do the whole change and check it. Do not commit.
```

(The line breaks are added here; the prompt had one line for each
paragraph.) This conflicts with the technique `state-the-output-contract`
and the playbook `output-contract-not-met`.

What the agent did:

- It made no edit (`git status` was clean after the session).
- It quoted the existing text with its files and lines: the playbook's
  "What not to do yet" and the technique's "When not to use", with the
  technique's confidence and its two public priors.
- It gave the two choices of the skill's section 7: deprecate the old, or
  state when to use each. It proposed a condition for the second choice.
- It also stopped on three more rules of the skill: a change to a
  playbook's order needs the owners; the model is not a lever by default
  (lever `model`); and the lesson needs a source and the person's approval
  before it can be a prior. It noted that "add a JSON schema" has no lever
  today.
- It asked four questions. Then the `claude -p` session ended its turn,
  with no edit made.

## Item 5: a run on the demo project

**Not run.** This environment has no Kiln studio server. The server is
part of the Kiln app (v1.3 or later, [install.md](../../../docs/install.md)
section 3); it is not a Python package that this environment can install.
A live run also spends money on the provider account. So no live output is
shown here.

**What the offline suite shows.** `test_the_planner_input_carries_the_detections`
(`tests/test_smells.py`) runs `orchestrate()` with the fake Kiln and the
fake dispatcher, on a baseline whose prompt is one character over
`LONG_PROMPT_CHARS`. The planner's input carries the `long_prompt`
detection. No test checks the report of an `orchestrate()` run. The report
half is checked separately: `test_the_report_has_the_smells_appendix` (in
both files) renders the section from tracker events, and
`test_the_written_report_carries_the_smells_appendix` writes a fallback
report that has it. Below is the section that code renders from the
events of one `orchestrate()` run on the same fixtures. A scratch script,
not a test, made it, with one scripted reviewer answer. **This is
fixture output, not a run.**

```
## Smells

The smells found in the design of each baseline. Code renders this section. ...

Baseline `rc_base`. Baseline review: done, 1 finding(s).

| smell | fact | reference | source of the reference | reviewer |
|---|---|---:|---|---|
| very-long-prompt | The system prompt has 200,001 characters. The reference is 200,000. | 200,000 | a default of the tuner, about 50,000 tokens; no published limit for a system prompt is known | holds: The prompt is one repeated character (a fixture). |

| smell | evidence | where | confidence | issue |
|---|---|---|---|---|
| fixed-call-chain | Each of the 4 sampled traces calls search, then fetch. | t1, search | High: every sampled trace shows it. | `mem_1` |
```

**For the owner: how to run it.** The demo's own baseline prompt is short,
so a run on an unchanged copy gives no detection. The baseline must have a
prompt over 200,000 characters before the first run on the copy, because
setup keeps the baseline record that the first run seeds.

1. Open the Kiln app (v1.3 or later), with an OpenRouter key in it.
2. Copy the demo project from a checkout, and import the copy into Kiln:

   ```
   cp -R "src/kiln_harness_tuner/demo_project/Forum Moderation" \
     ~/"Kiln Projects/Forum Moderation long prompt"
   ```

   The copy keeps the packaged project id, `424242975950`. Do not import a
   second copy with the same id.
3. In Kiln, in task `424242783061` of the copy, make a prompt that is the
   baseline's prompt with a long appendix of reference text, more than
   200,000 characters in all. Make a run config with that prompt and the
   baseline's model, and set it as the task's default.
4. Run:

   ```
   harness-tuner --project-path ~/"Kiln Projects/Forum Moderation long prompt" \
     --project-id 424242975950 --task-id 424242783061 \
     --timeout-mins 120 --budget-usd 5 --no-interaction
   ```

   Give it 120 minutes or more. The deadline starts before setup, and the
   review is not dispatched on a wrap-up wave or with less than 35 minutes
   left. Then the report's status line says "Baseline review: skipped (too
   little time)." (or "skipped (a wrap-up wave)"), and only the code
   detection shows.

   The new run config has no stored train rows, so the run first measures
   it on the full train split. Each call sends the long prompt, about
   50,000 tokens. Set `--budget-usd` to what that costs on the model.
5. Look for three things:
   - the line `baseline review: <rc> — …` on the run's display, and the
     `smell_detections` event in `runs/<run id>/wave_log.jsonl`, with the
     `long_prompt` detection, before the planner's first phase;
   - `detections` in the planner's input, in the `batch_plan` session's
     transcript under `traces/`;
   - the `## Smells` section of the report, with the `very-long-prompt`
     row, the reviewer's reading of it, and the review's status line.

Nothing measured in this run goes in `AGENTS.md` or in `agent_files/`.
