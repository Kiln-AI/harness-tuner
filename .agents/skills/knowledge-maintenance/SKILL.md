---
name: knowledge-maintenance
description: Add to, change and audit the Kiln Harness Tuner knowledge system (the skill kiln-harness-tuner-knowledge - its levers, techniques, playbooks and smells), and add entries to the backlog in specs/backlog.md. Use it for every change to those files - a new approach from a post, a lesson to record, a new document, an idea for later, a technique that went out of fashion - and for an audit. It gives the templates, the placement rules, the content rules and the commands that end each change.
---

# knowledge-maintenance

## 1. What this is

The knowledge system is the skill
[`kiln-harness-tuner-knowledge`](../../../src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-knowledge/SKILL.md).
It holds what is known about how to improve an agent's harness. The loop
agents of the tuner read it: the planner, the builder, the analyzer, the
report writer and, later, the baseline reviewer. Each role has its own entry
file in `references/`.

It has four component types. Each is one Markdown file, and its directory
gives its type:

| Type | Directory | What it is |
|---|---|---|
| Lever | `references/levers/` | One thing in the harness that the tuner can change, and how. Never when or why. |
| Technique | `references/techniques/` | One optimisation: when to use it, how, a cheap check, priors, confidence. |
| Playbook | `references/playbooks/` | How to solve one problem or reach one goal: an ordered list of things to try. |
| Smell | `references/smells/` | An issue in the design of the agent: how to recognise and confirm it, and links to its remedies. |

The file name without `.md` is the id. The entry files hold generated
blocks. Code writes those blocks. A person or an agent never edits them.

The backlog, [`specs/backlog.md`](../../../specs/backlog.md), lists the known
gaps and the ideas that are not written yet. Loop agents do not read it.

You, a coding agent that works on this repository, read this skill. Loop
agents never read it: it is outside `src/`, so it is not in the package.

## 2. Before you start

1. Read the schema of the component type that you change: section 3 of
   [the knowledge documents design](../../../specs/projects/biological_bootloader/components/knowledge_documents.md).
   It gives the front matter keys, the required sections and the size
   limits. This skill does not repeat them, so that the two cannot disagree.
2. Run the check, to see a clean state before you change anything:

   ```
   uv run python -m kiln_harness_tuner.knowledge check
   ```

   If it reports a problem before your change, tell the person. Do not mix
   a fix of an old problem into your change.

## 3. Choose the request

| The person asks | Procedure |
|---|---|
| "Read this post and add this approach" | 4.1 |
| "We learned a lesson, record this" | 4.2 |
| Add a technique, playbook, smell or lever | 4.3 |
| Add an idea or a gap for later | 4.4 |
| A technique went out of fashion | 4.5 |
| Audit | Section 10 |

If the request fits none of these, ask the person before you change a file.

## 4. The procedures

### 4.1 Add an approach from a source

1. Read the source.
2. State in one sentence what the approach changes in the harness.
3. Search the techniques for one that covers it. Read the technique index
   and search `references/techniques/` for the key words.
4. If a technique covers it, extend that technique: add to `When to use`,
   `How` or `Priors`.
5. If none covers it, add a technique (4.3).
6. Decide if a playbook or a smell must link to it (section 5).
7. If the new text disagrees with existing text, stop (section 7).
8. Finish (section 9).

Reason: one approach with two documents gives two answers to one question.
The search in step 3 prevents a duplicate.

### 4.2 Record a lesson

1. Find the technique that the lesson is about.
2. Apply the approval rule (section 8). Show the person the exact text of
   the prior, and wait for the answer.
3. Add the prior to `Priors`.
4. Change `Confidence` only if this lesson changes it. Say why in the commit
   message.
5. Finish (section 9).

Reason: one case is one sample. It does not change the confidence alone,
unless it is the first case or it disagrees with the others.

### 4.3 Add a document

1. Copy the template of the type to the right directory, with the id as the
   file name:

   | Type | Template | Copy to |
   |---|---|---|
   | Lever | [templates/lever.md](templates/lever.md) | `references/levers/<id>.md` |
   | Technique | [templates/technique.md](templates/technique.md) | `references/techniques/<id>.md` |
   | Playbook | [templates/playbook.md](templates/playbook.md) | `references/playbooks/<id>.md` |
   | Smell | [templates/smell.md](templates/smell.md) | `references/smells/<id>.md` |

   The id is lower case, with words joined by `-`, of at most 60
   characters. A lever's id is the short name that the planner writes in the
   `technique` field of an experiment.
2. Fill every required part. Replace each `{{NAME}}` placeholder. Remove each
   HTML comment after you follow it. A link placeholder names an existing
   document by its id.
3. Add the links in and out. A technique links its levers in `How`. A
   playbook links its techniques in `What to try, in order`. A smell links
   its remedies. Decide which playbook or smell must link to the new
   document (section 5). A playbook change needs the owners.
4. A playbook is a new problem or a new goal. If the problem has a
   playbook, the change goes in that playbook, not in a new one.
5. Finish (section 9).

Reason: a template has every required part, in order, so a check cannot
fail on a missing heading. A test holds each template to the schema.

### 4.4 Add an idea or a gap for later

Add an entry to [`specs/backlog.md`](../../../specs/backlog.md), at the end
of `Unranked`, in the fixed format that the top of that file gives. Do not
rank it. Run `uv run pytest tests/test_backlog.py`.

Reason: an entry needs no debate to be added. Its rank does, and the owners
decide the rank.

### 4.5 A technique went out of fashion

1. Set `status: dormant`, or narrow `When to use`.
2. Change each playbook and each smell that links to it. Use a search for
   its file name. A playbook change needs the owners (section 5).
3. Do not delete the file.
4. Finish (section 9).

Reason: a dormant technique stays in the system, so a reader can see that it
was tried and why it is not suggested. A deleted file loses that history.

## 5. Placement rules

Apply the first row that fits.

| The new material is | It goes in |
|---|---|
| A fact about what the tuner can change, or how | The lever document |
| A way to make one specific improvement | A technique |
| A reason to prefer one technique to another under a condition | `Alternatives` and `When not to use` of the techniques |
| An order of work for a goal or a situation | A playbook. Stop: playbooks are owned (below). |
| A problem to check for in the design of the agent | A smell, with its remedies |
| One case with a result | A prior on a technique |
| An idea with no content yet | The backlog |

Rules that go with the table:

- **New does not mean first.** Add a new technique to a playbook's order at
  the place that its condition gives. State the reason. Reason: a new item
  at the top of every list makes the order a record of age, not of value.
- **Playbooks are owned.** A change to a playbook's order needs the
  agreement of the owners. Propose the change, show it to the person and
  stop. A new technique, smell or lever needs no agreement. Reason:
  playbooks hold the opinions about order, and a small group keeps them
  consistent. The ownership is informal: there is no CODEOWNERS file.
- **Do not copy.** If the material is how-to text that a lever or an
  authoring skill already has, link to it. Reason: two copies go out of
  step, and a reader cannot tell which is correct.
- **Cost of maintenance.** Do not add a fact about one model, or about one
  point in time, that the loop learns from one failed call. Reason: such a
  fact goes out of date fast, and the loop finds it at almost no cost.
- **A smell is a property of the design.** A reason why one eval item
  failed is a failure pattern: it is a playbook of the kind `failure`, not a
  smell. Reason: the baseline reviewer looks for each smell in the design,
  whether an eval passes or fails.

## 6. Content rules

These rules apply to every document that a loop agent reads. A test checks
the structure. It cannot check these rules, so you apply them, and the audit
(section 10) looks for each one.

1. **Experiments decide.** Do not write a rule of the form "you must do X".
   Say what to try, in what order, and under which conditions. Reason: a
   measured result on the target is final. A rule that overrides it makes
   the loop ignore its own evidence.
2. **State conditions, not defaults.** Each recommendation states the
   condition under which it holds. Reason: the research found a measured
   case where each "almost always" claim loses.
3. **State confidence.** Say how sure we are and on what basis. Reason: a
   reader must be able to tell an idea that one person proposed from a
   result seen many times.
4. **Date the guidance.** Set `reviewed` to the date of your review. Reason:
   the field changes each month, and a reader must know how old the text is.
5. **The cost of learning against the cost of maintenance.** Save a lesson
   that is expensive to learn. Leave out a lesson that the loop learns from
   one failed call. Be careful with a fact about one model or one date.
   Reason: a large base of facts is hard to maintain, and a stale fact
   misleads.
6. **Nothing from the target.** No measurement, lever advice or lesson from
   the run's own target or from the moderation demo. Reason: a loop agent
   that reads the answer invalidates the measurement that it is asked to
   make.
7. **General knowledge and other projects are allowed.** Field knowledge,
   public sources, our own synthetic experiments and anonymised cases from
   other projects can go in, under the approval rule (section 8). Reason:
   this is the knowledge that makes the planner's hypotheses better, and it
   does not come from the target.
8. **Simplified Technical English** (ASD-STE100): short sentences, one idea
   each, no metaphor. Reason: the readers are models of many sizes, and
   simple text is read correctly more often.
9. **Do not copy how-to material.** Link to the lever or to the authoring
   skill. Reason: a copy goes out of step with its source.

## 7. Conflicts

When the new material disagrees with existing guidance, stop. Do not choose.
Give the person three things:

1. the existing text, with its file and line;
2. the new text;
3. the two choices:
   - **Deprecate the old.** The old technique becomes `dormant`, or its
     `When to use` is narrowed.
   - **State when to use each.** Both stay. The `Alternatives` of each one
     names the other, with the condition.

Write the decision into the documents, in `Alternatives` or
`When not to use`, so that the next reader sees it.

Never delete an older approach, and never rewrite it to make room for a new
one.

Reason: a conflict is a decision about what the system believes. The person
owns that decision. A silent rewrite loses the old guidance and the reason
for the change.

## 8. Priors and approval

A prior is one real case in which a technique was used. It is one sample,
not a universal lesson, and its text says so.

- A prior never names a client, and never holds client data.
- **Before a prior is committed, a person approves it**, unless it is
  clearly public information: a published paper or post, with its link.
  Show the person the exact text of the prior and ask. Do not commit first
  and ask at review. Reason: a result can be sensitive when no entity is
  named, and a pull request review comes after the text is in the history.
- Never add a prior from the run's own target or from the moderation demo
  (content rule 6).
- Before you write a number from a public source, open the source and check
  the number. Correct or drop a line whose numbers do not match.
- The form of a prior is in the `Priors` comment of
  [templates/technique.md](templates/technique.md). Its `Source:` is
  `public`, `own experiment` or `anonymised`. A test checks that value.

## 9. Finish every change

Run these commands, in order:

```
uv run python -m kiln_harness_tuner.knowledge build
uv run pytest tests/test_knowledge.py tests/test_knowledge_checks.py tests/test_backlog.py
uv run ruff check
uv run ruff format --check
```

`build` rewrites the generated blocks of the entry files and then runs the
check. To check without a rewrite, run
`uv run python -m kiln_harness_tuner.knowledge check`.

A change is not done while a check fails. Do not edit a generated block by
hand: change the documents and run `build`. Reason: a hand edit makes the
index disagree with the files, and the next `build` removes it.

## 10. Audit

The audit covers what a test cannot. It changes nothing. It returns a list.

For each document, report each of these, with the file and the line:

- a rule of the form "you must" (content rule 1);
- a recommendation with no condition (content rule 2);
- a `Confidence` section that states no basis;
- a `reviewed` date older than six months;
- a lever document that says when to use the lever;
- how-to text copied from a lever or an authoring skill;
- a fact about one model or one date that the loop can learn cheaply;
- a prior that could identify a client;
- a playbook that repeats how to recognise its smell.

Run `uv run python -m kiln_harness_tuner.knowledge check` too, and add its
problems to the list.

Reason: a person decides what to fix. An audit that edits files mixes many
decisions into one change.
