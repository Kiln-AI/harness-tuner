---
status: draft
---

# Component: Maintenance skill and backlog

## 1. Purpose and scope

This component is what a coding agent, and the person who directs it, use to
add to the knowledge system, change it and audit it. It also holds the
backlog and the new working rule in `AGENTS.md`.

Not in this component: the schemas and the checks
([knowledge_documents.md](./knowledge_documents.md)). The skill refers to
them. It does not repeat them.

## 2. Files

```
maintainers/
  README.md                 one paragraph: what this directory is
  knowledge/
    SKILL.md                the maintenance skill
    templates/
      lever.md
      technique.md
      playbook.md
      smell.md
    BACKLOG.md
```

- `maintainers/` is outside `src/`, so it is not in the wheel and no loop
  agent can read it.
- `SKILL.md` has the front matter of an agent skill (`name`,
  `description`), so an agent CLI that supports skills can load it by path.
  Its use does not depend on that: the root `AGENTS.md` tells every coding
  agent to read it before a change to the knowledge system.
- Pseudo-code in these files is fenced without a language, as the
  repository rule requires. `ruff` does not exclude this directory.

## 3. The maintenance skill

`maintainers/knowledge/SKILL.md`. Maximum 500 lines. It is written in
Simplified Technical English, and each rule has its reason beside it.

### 3.1 Contents, in order

1. **What this is.** The knowledge system, where it lives, who reads it.
2. **Before you start.** Read the component type's schema section in the
   architecture (link). Run `check` to see a clean state.
3. **Choose the request.** A table from the kind of request to the
   procedure (section 3.2).
4. **The procedures.**
5. **Placement rules** (section 3.3).
6. **Content rules.** The nine rules of the functional spec, section 6, each
   with its reason.
7. **Conflicts** (section 3.4).
8. **Priors and approval** (section 3.5).
9. **Finish every change** (section 3.6).
10. **Audit** (section 3.7).

### 3.2 Procedures

| Request | Procedure |
|---|---|
| "Read this post and add this approach" | 1. Read the source. 2. State in one sentence what the approach changes. 3. Search the techniques for one that covers it. 4. If one covers it, extend that technique: add to `When to use`, `How` or `Priors`. 5. If none does, add a technique. 6. Decide if a playbook or a smell must link to it (placement rules). 7. Finish. |
| "We learned a lesson, record this" | 1. Find the technique. 2. Apply the approval rule. 3. Add a prior. 4. Change `Confidence` only if this lesson changes it, and say why in the commit message. 5. Finish. |
| Add a technique, playbook, smell or lever | 1. Copy the template to the right directory, with the id as the file name. 2. Fill every required part. 3. Add the links in and out. 4. Finish. |
| Add an idea or a gap for later | Add an entry to `BACKLOG.md`, at the end, in the fixed format. Do not rank it. |
| A technique went out of fashion | 1. Set `status: dormant`, or narrow `When to use`. 2. Change each playbook and smell that links to it. 3. Do not delete the file. 4. Finish. |
| Audit | Section 3.7. |

### 3.3 Placement rules

The skill gives a test for each level. The agent applies the first that
fits.

| The new material is | It goes in |
|---|---|
| A fact about what the tuner can change, or how | The lever document |
| A way to make one specific improvement | A technique |
| A reason to prefer one technique to another under a condition | `Alternatives` and `When not to use` of the techniques |
| An order of work for a goal or a situation | A playbook. Stop: playbooks are owned (below). |
| A problem to check for | A smell, with its remedies |
| One case with a result | A prior on a technique |
| An idea with no content yet | The backlog |

Rules that go with the table:

- **New does not mean first.** A new technique is not added to the top of a
  playbook's order because it is new. It is added at the place its
  condition gives, and the reason is stated.
- **Playbooks are owned.** A change to a playbook's order needs the
  agreement of the owners. The agent proposes the change and stops. A new
  technique, smell or lever does not need that agreement.
- **Do not copy.** If the material is how-to text that a lever or an
  authoring skill already has, link to it.
- **Cost of maintenance.** Do not add a fact about one model, or one point
  in time, that the loop learns from one failed call.

### 3.4 Conflicts

When the new material disagrees with existing guidance, the agent stops and
gives the person three things: the existing text, the new text, and the two
choices.

1. **Deprecate the old.** The old technique becomes `dormant`, or its
   `When to use` is narrowed.
2. **State when to use each.** Both stay. Each one's `Alternatives` names
   the other with the condition.

The agent does not choose. The decision is written into the documents, in
`Alternatives` or `When not to use`, so that the next reader sees it.

An older approach is never deleted or rewritten to make room.

### 3.5 Priors and approval

- A prior never names a client and never holds client data.
- Before a prior is **committed**, a person approves it, unless it is
  clearly public information (a published paper or post, with its link). A
  result can be sensitive when no entity is named.
- The agent shows the exact text of the prior and asks. It does not commit
  first and ask at review.
- A prior from the run's own target, or from the moderation demo, is never
  added.

### 3.6 Finish every change

```
uv run python -m kiln_harness_tuner.knowledge build
uv run pytest tests/test_knowledge.py tests/test_knowledge_checks.py tests/test_backlog.py
uv run ruff check
uv run ruff format --check
```

A change is not done while a check fails. The agent does not edit a
generated block by hand.

### 3.7 Audit

The audit covers what a test cannot. For each document, the agent reports,
with the line:

- a rule of the form "you must" (content rule 1);
- a recommendation with no condition (rule 2);
- a `Confidence` section that states no basis;
- a `reviewed` date older than six months;
- a lever document that says when to use the lever;
- how-to text copied from a lever or an authoring skill;
- a fact about one model or one date that the loop can learn cheaply;
- a prior that could identify a client;
- a playbook that repeats how to recognise its smell.

The audit changes nothing. It returns a list.

## 4. Templates

One file for each component type, in `maintainers/knowledge/templates/`.
Each is a valid document of its type once its placeholders are filled: the
front matter keys in order, each required heading, and one line of guidance
under each heading in an HTML comment, which the author removes.

A test loads each template into a temporary tree, with the placeholders
replaced by fixed values, and runs `check` on it. So a template cannot go
out of step with the schema.

## 5. The backlog

`maintainers/knowledge/BACKLOG.md`.

### 5.1 Format

```
# Backlog

<a short paragraph: what this is, how to add an entry, who ranks>

## Ranked

### <title>

- Kind: <gap | technique | playbook | smell | lever | detector | code>
- Where: <knowledge | tuner | kiln>
- Depends on: <titles of other entries, or None>
- Source: <a link or a short reference, or None>

<one to three lines>

## Unranked

### <title>
...
```

- The order of the entries under `Ranked` is the rank. The first is the
  next change with the highest leverage.
- A new entry goes at the end of `Unranked`. To move an entry into `Ranked`
  is a decision of the owners.
- A `gap` is something Kiln or the tuner cannot do yet. The other kinds are
  pending ideas.
- A title is unique in the file.

### 5.2 First contents

The V1 coverage phase fills it. The sources are `kiln_gaps.md`, the
overview, the owner's notes, the research summary, and the items this spec
moved out: the tie rule, the rename of the code field `technique`, the
"very old model" detector, input transforms and the structured-output mode
as levers, and the detectors of smells in the process of a run.

## 6. `AGENTS.md` and `CONTRIBUTING.md`

The working rule "Findings go in the state folder's `reports/`…" in the root
`AGENTS.md` is replaced by two rules:

```
**Nothing measured on the target goes in `agent_files/`.** Every agent the
loop dispatches loads the instructions and the skills in `agent_files/`. So
a number, a lesson or lever advice that comes from the run's own target, or
from the moderation example, invalidates what the loop measures. Those
findings go in the state folder's `reports/`.

**The knowledge system is the place for general knowledge.** The skill
`agent_files/skills/kiln-harness-tuner-knowledge/` holds what is known
about how to improve an agent: field knowledge, public sources, our own
synthetic experiments and anonymised cases from other projects. Read
`maintainers/knowledge/SKILL.md` before you change it. A person approves a
case study before it is committed, unless it is clearly public.
```

- The "Layout" block of `AGENTS.md` gets a line for `maintainers/`.
- The sentence in the section on the moderation example that refers to the
  working rule is kept, because the first rule still gives the reason.
- `.github/CONTRIBUTING.md`: the guideline "Do not put measured results,
  lever advice or the outcome of an earlier run in them" is replaced by the
  same two rules in short form, with the link to the maintenance skill.

## 7. Dependencies

- Uses: the `knowledge` package (its command line and its checks).
- Used by: coding agents and people. No code imports anything from
  `maintainers/`.

## 8. Test plan

`tests/test_backlog.py`:

- `test_the_backlog_has_the_two_sections`
- `test_each_entry_has_the_four_fields_with_valid_values`
- `test_each_entry_has_one_to_three_lines_of_text`
- `test_titles_are_unique`
- `test_depends_on_names_entries_that_exist`

`tests/test_knowledge.py`, additions:

- `test_each_template_gives_a_valid_document`
- `test_the_maintenance_skill_is_inside_its_line_limit`
- `test_the_maintenance_skill_names_the_build_and_check_commands`
- `test_agents_md_points_to_the_maintenance_skill`
- `test_maintainers_is_not_in_the_wheel` (in the packaging smoke test)

`tests/test_docs.py`: `maintainers/**/*.md` is added to the files whose
internal links are checked.

The behaviour of an agent that uses the skill (acceptance 1 and 2 of the
functional spec: it adds a technique as one file, and it stops on a
conflict) is checked by hand at the end of the phase, with a short written
record in the phase plan. It is not an automated test, because the offline
suite runs no model.
