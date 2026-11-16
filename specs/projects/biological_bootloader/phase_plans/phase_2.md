---
status: complete
---

# Phase 2: The maintenance skill, the backlog and the repository rules

## Overview

This phase adds the tools that people and coding agents use to keep the
knowledge system correct as it grows. It adds the maintenance skill
`.agents/skills/knowledge-maintenance/` with one template for each component
type, the backlog `specs/backlog.md` with its first contents (section 7 of
the V1 coverage document), and the repository rules that point to both: the
`.gitignore`, `.github/CONTRIBUTING.md` and root `AGENTS.md` changes. It
adds `tests/test_backlog.py` and extends `tests/test_knowledge.py` and
`tests/test_docs.py`. No loop agent reads anything that this phase adds, and
no code of the package changes.

The design is `components/maintenance_skill.md`.

## Steps

1. `.gitignore`: remove the line `/.agents/`. Rewrite the comment above the
   remaining lines (`/.claude/skills/`, `/.claude/settings.json`,
   `/.mcp.json`) so that it names only those, and says that `.agents/skills/`
   is tracked because it holds the maintenance skill.
2. `pyproject.toml`: the comment above `extend-exclude` says that
   `.agents/skills/**` holds the maintenance skill. The exclusion stays.
3. `.agents/skills/knowledge-maintenance/SKILL.md`: front matter `name:
   knowledge-maintenance` and a `description`. The ten parts of section 3.1
   of the component design, in order: what this is; before you start (link to
   the schema section of `components/knowledge_documents.md`, run `check`);
   the request table; the procedures; the placement rules; the nine content
   rules, each with its reason; conflicts; priors and approval; finish every
   change (the four commands); audit. Under 500 lines, in Simplified
   Technical English.
4. `.agents/skills/knowledge-maintenance/templates/lever.md`, `technique.md`,
   `playbook.md`, `smell.md`. Each has the front matter keys of its type in
   order, with a YAML comment where a key has a fixed set of values, and each
   required H2 heading with one HTML comment of guidance under it.
   Placeholders have the form `{{NAME}}` (upper case and `_`). A link
   placeholder names an existing document by id:
   `[{{LEVER_TITLE}}](../levers/{{LEVER_ID}}.md)`. Prose is `{{TEXT}}`.
5. `specs/backlog.md`: the format of section 5.1 of the component design.
   - The intro paragraph: what the backlog is, that loop agents do not read
     it, how to add an entry (at the end of `Unranked`, in the fixed format),
     that the owners rank.
   - `Where` is one value or a list joined by `, `. `Depends on` is `None`
     or titles joined by `; `, because titles can hold commas.
   - The text of an entry is one to three lines, each at most 100
     characters.
   - `Ranked`: the twelve entries of V1 coverage section 7, in that order.
   - `Unranked`: one entry for each idea of the unranked lists. A list item
     that joins ideas with `;` becomes one entry per idea. The gaps come from
     `kiln_gaps.md` and the other kinds from V1 coverage section 7 and the
     functional spec (section 12.3: the detectors for later).
6. `AGENTS.md` (root): the "Layout" block gets a line for `.agents/skills/`
   and one for `specs/`. The working rule "Findings go in the state folder's
   `reports/`…" is replaced by the two rules of section 6 of the component
   design. The fence rule names `.agents/skills/**` as also excluded from
   `ruff format`.
7. `.github/CONTRIBUTING.md`: remove the setup step "To use the skills in
   your own agent CLI while you work here", its command and the paragraph
   after it, and keep the sentence that the root `AGENTS.md` is the
   contributor guide. Replace the guideline "Do not put measured results,
   lever advice…" with the two rules in short form, with a link to the
   maintenance skill.
8. `tests/test_knowledge_checks.py`: the body of the `make_tree` fixture
   becomes the function `build_tree(root: Path) -> Tree`, so that
   `tests/test_knowledge.py` can build the same valid tree.
9. `tests/test_docs.py`: `linked_files()` adds
   `.agents/skills/knowledge-maintenance/**/*.md` and `specs/backlog.md`. A
   link target that holds `{{` is a template placeholder and is skipped. The
   coverage test asserts that the new files are in the set.

## Tests

`tests/test_backlog.py` — a parser `backlog_problems(text) -> list[Problem]`
with the rules `sections`, `fields`, `text`, `unique`, `depends`. Tests on
the real file:

- `test_the_backlog_has_the_two_sections`: `## Ranked` then `## Unranked`,
  and no other H2.
- `test_each_entry_has_the_four_fields_with_valid_values`: `Kind`, `Where`,
  `Depends on`, `Source`, in order, with valid values.
- `test_each_entry_has_one_to_three_lines_of_text`
- `test_titles_are_unique`
- `test_depends_on_names_entries_that_exist`
- `test_the_backlog_is_not_empty`: both sections have entries, so the tests
  above cannot pass on an empty file.

Tests on deliberate violations, each asserting exactly one rule:

- `test_a_valid_backlog_has_no_problem`
- `test_a_violation_is_reported` (parametrised): a missing `Unranked`, an
  extra H2, the sections in the wrong order, text before the first entry in
  a section, a missing field, a field out of order, an unknown kind, an
  unknown `Where`, an empty source, no text, four lines of text, a line over
  the width limit, a duplicate title, an unknown dependency.

`tests/test_knowledge.py`, additions:

- `test_each_template_gives_a_valid_document` (parametrised by type): the
  placeholders filled from a fixed map, the HTML comments removed, the file
  written into the `build_tree` tree; `check` reports nothing. An unknown
  placeholder fails the test.
- `test_each_template_has_the_required_headings_and_front_matter_keys`
- `test_the_maintenance_skill_is_inside_its_line_limit`
- `test_the_maintenance_skill_names_the_build_and_check_commands`
- `test_the_maintenance_skill_names_its_folder`
- `test_agents_md_points_to_the_maintenance_skill`
- `test_the_maintenance_skill_is_not_git_ignored`: reads `.gitignore`, with
  a self-test that the reader finds the old `/.agents/` line.
- `test_contributing_does_not_install_into_the_repository_root`

`tests/test_docs.py`:

- `test_the_root_documents_are_link_checked` also asserts the maintenance
  skill files and the backlog.
