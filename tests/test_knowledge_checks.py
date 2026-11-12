"""The structural rules of the knowledge system, one deliberate violation at a
time.

`make_tree` writes a small valid tree. Each test applies one change and
asserts that the check reports exactly the rule it breaks, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from kiln_harness_tuner.knowledge import (
    KnowledgeError,
    check,
    load,
    render_blocks,
    write_blocks,
)
from kiln_harness_tuner.knowledge.__main__ import main
from kiln_harness_tuner.knowledge.index import read_block
from kiln_harness_tuner.knowledge.schema import ENTRY_BLOCKS, MAX_CHARS, Problem

GATEWAY_OPS = frozenset({"create_prompt", "get_prompt_text"})
REGISTRY = {"long_thing": "long-thing"}

LEVER = """\
---
title: The prompt
description: The prompt text that a run config sends to the model, in a test tree.
reviewed: 2026-01-01
gateway_ops:
  - create_prompt
---

# The prompt

## What it is

The prompt.

## How the tuner changes it

A new prompt.

## Limits

None.

## See also

None.
"""

TECHNIQUE = """\
---
title: One technique
description: A technique of the test tree, which edits the prompt of the agent.
reviewed: 2026-01-01
status: active
levers:
  - prompt
applies_to: all
cost: low
---

# One technique

## When to use

When a test needs it.

## When not to use

Never otherwise.

## Alternatives

None.

## How

Edit the [prompt](../levers/prompt.md).

## Check that it took effect

Read a trace.

## Budget

Cheap.

## If it does not move the evals

It depends. On the test.

## Priors

- **A test agent.** One line was tried. It worked.
  Source: own experiment. 2026-01-01.

## Confidence

High, in a test.
"""

PLAYBOOK = """\
---
title: One playbook
description: A playbook of the test tree, for a failure that the traces show.
reviewed: 2026-01-01
kind: failure
applies_to:
  - tools
  - single_turn
---

# One playbook

## Situation

The trace shows a failure.

## What to try, in order

1. [One technique](../techniques/t-one.md), when the test says so.

## What not to do yet

Nothing else.

## Budget

The order does not change with the budget.
"""

SMELL = """\
---
title: One smell
description: A smell of the test tree, which a reader of the design can find.
reviewed: 2026-01-01
applies_to: all
detected_by:
  - agent
---

# One smell

## What it is

A smell.

## How to recognise it

A sign.

## How to confirm it

A check.

## What it blocks

Nothing.

## Remedies

- [One playbook](../playbooks/p-one.md), when it fits.
"""

CODE_SMELL = (
    SMELL.replace("One smell", "Long thing")
    .replace("which a reader of the design can find", "which a code detector finds")
    .replace("  - agent\n", "  - code\ndetectors:\n  - long_thing\n")
)


def _entry(name: str, blocks: tuple[str, ...]) -> str:
    body = "".join(
        f"\n## {block}\n\n<!-- BEGIN GENERATED: {block} -->\n"
        f"<!-- END GENERATED: {block} -->\n"
        for block in blocks
    )
    return f"# {name}\n\nHand-written text.\n{body}"


@dataclass
class Tree:
    root: Path

    @property
    def references(self) -> Path:
        return self.root / "references"

    def write(self, relative: str, text: str) -> None:
        path = self.references / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read(self, relative: str) -> str:
        return (self.references / relative).read_text(encoding="utf-8")

    def edit(self, relative: str, old: str, new: str) -> None:
        text = self.read(relative)
        assert old in text, f"{old!r} is not in {relative}"
        self.write(relative, text.replace(old, new, 1))

    def problems(self, *, rebuild: bool = True, **code_side) -> list[Problem]:
        """The check's problems. With `rebuild`, the blocks are written first,
        so that a change is not also reported as a stale index."""
        if rebuild:
            write_blocks(load(self.root))
        sides = {"gateway_ops": GATEWAY_OPS, "detectors": REGISTRY} | code_side
        return check(load(self.root), **sides)

    def rules(self, **kwargs) -> set[str]:
        return {p.rule for p in self.problems(**kwargs)}


def build_tree(parent: Path) -> Tree:
    """A small valid tree in `parent`: the entry files, the lever `prompt`,
    the technique `t-one`, the playbook `p-one`, and the smells `s-one` and
    `long-thing` (the smell of the detector `long_thing`)."""
    tree = Tree(parent / "kiln-harness-tuner-knowledge")
    tree.references.mkdir(parents=True)
    (tree.root / "SKILL.md").write_text("---\nname: test\n---\n")
    for entry, blocks in ENTRY_BLOCKS.items():
        tree.write(entry, _entry(entry, blocks))
    tree.write("levers/prompt.md", LEVER)
    tree.write("techniques/t-one.md", TECHNIQUE)
    tree.write("playbooks/p-one.md", PLAYBOOK)
    tree.write("smells/s-one.md", SMELL)
    tree.write("smells/long-thing.md", CODE_SMELL)
    write_blocks(load(tree.root))
    return tree


@pytest.fixture
def make_tree(tmp_path):
    def make() -> Tree:
        return build_tree(tmp_path)

    return make


@pytest.fixture
def tree(make_tree) -> Tree:
    return make_tree()


def _only(tree: Tree, rule: str, **kwargs) -> list[Problem]:
    problems = tree.problems(**kwargs)
    assert {p.rule for p in problems} == {rule}, problems
    return problems


def test_a_valid_minimal_tree_has_no_problem(tree):
    assert tree.problems(rebuild=False) == []


def test_check_takes_a_one_shot_iterable(tree):
    kb = load(tree.root)
    assert check(kb, gateway_ops=iter(GATEWAY_OPS), detectors=REGISTRY) == []


# ---- front-matter ----


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("reviewed: 2026-01-01\n", ""),
        ("reviewed: 2026-01-01\n", "reviewed: 2026-01-01\nowner: someone\n"),
        ("title: One playbook", "title: P"),
        ("title: One playbook", "title: " + "x" * 61),
        (
            "description: A playbook of the test tree, for",
            "description: A playbook | of the test tree, for",
        ),
        (
            "description: A playbook of the test tree, for a failure that the"
            " traces show.",
            "description: Short.",
        ),
        ("reviewed: 2026-01-01", f"reviewed: {date.today() + timedelta(days=2)}"),
        ("kind: failure", "kind: wish"),
        ("applies_to:\n  - tools\n  - single_turn\n", "applies_to: []\n"),
        ("title: One playbook", "title: [unclosed"),
        ("---\ntitle", "title"),
        ("\n---\n\n# One playbook", "\n\n# One playbook"),
        ("---\ntitle", "---\n- a list\n---\ntitle"),
        ("reviewed: 2026-01-01", "reviewed: 2026-02-30"),
    ],
    ids=[
        "missing-key",
        "unknown-key",
        "short-title",
        "long-title",
        "pipe-in-description",
        "short-description",
        "future-review",
        "bad-kind",
        "empty-applies-to",
        "bad-yaml",
        "no-front-matter",
        "front-matter-not-closed",
        "front-matter-not-a-map",
        "impossible-date",
    ],
)
def test_front_matter_is_reported(tree, old, new):
    tree.edit("playbooks/p-one.md", old, new)
    # The playbook loses its index rows, and stays reachable through the
    # smell that links it.
    [problem] = _only(tree, "front-matter")
    assert problem.path == "playbooks/p-one.md"


def test_a_title_on_two_lines_is_reported(tree):
    tree.edit("levers/prompt.md", "title: The prompt", 'title: "The\\nprompt"')
    _only(tree, "front-matter")


def test_load_records_a_problem_and_does_not_raise(tree):
    tree.edit("smells/s-one.md", "title: One smell", "title: : : [")
    doc = load(tree.root).get("smells", "s-one")
    assert doc is not None and doc.meta is None
    assert [p.rule for p in doc.problems] == ["front-matter"]


def test_a_document_that_is_not_utf8_is_reported_and_does_not_raise(tree):
    (tree.references / "levers" / "prompt.md").write_bytes(b"---\ntitle: \xff\n")
    problems = tree.problems()
    assert any(p.rule == "encoding" and p.path == "levers/prompt.md" for p in problems)
    # The lever has no readable sections, and the technique that links it
    # still does.
    assert {p.rule for p in problems} == {"encoding", "sections"}


def test_load_raises_when_the_root_is_missing(tmp_path):
    with pytest.raises(KnowledgeError):
        load(tmp_path / "nothing")


# ---- id, sections, size, shape ----


def test_id_is_reported(tree):
    tree.write("techniques/Bad_Id.md", TECHNIQUE)
    [problem] = _only(tree, "id")
    assert problem.path == "techniques/Bad_Id.md"


def test_a_lever_id_with_an_underscore_is_accepted(tree):
    # A lever's id is the planner's short name, and `new_tool` is one.
    tree.write("levers/new_tool.md", LEVER.replace("The prompt", "New tools"))
    tree.edit("techniques/t-one.md", "  - prompt\n", "  - prompt\n  - new_tool\n")
    tree.edit(
        "techniques/t-one.md",
        "Edit the [prompt](../levers/prompt.md).",
        "Edit the [prompt](../levers/prompt.md) and add a"
        " [tool](../levers/new_tool.md).",
    )
    assert tree.problems() == []


@pytest.mark.parametrize("name", ["_tool", "new__tool", "new_tool_", "New_tool"])
def test_a_lever_id_with_a_bad_underscore_is_reported(tree, name):
    tree.write(f"levers/{name}.md", LEVER)
    [problem] = _only(tree, "id")
    assert problem.path == f"levers/{name}.md"
    assert "'-' or '_'" in problem.message


def test_an_underscore_outside_a_lever_id_is_reported(tree):
    tree.write("techniques/t_two.md", TECHNIQUE)
    [problem] = _only(tree, "id")
    assert problem.path == "techniques/t_two.md"
    assert "'_'" not in problem.message


def test_sections_is_reported(tree):
    tree.edit("techniques/t-one.md", "## When not to use\n\nNever otherwise.\n\n", "")
    _only(tree, "sections")


def test_sections_out_of_order_is_reported(tree):
    tree.edit(
        "smells/s-one.md",
        "## How to confirm it\n\nA check.\n\n## What it blocks\n\nNothing.\n",
        "## What it blocks\n\nNothing.\n\n## How to confirm it\n\nA check.\n",
    )
    _only(tree, "sections")


def test_an_empty_section_is_reported(tree):
    tree.edit("levers/prompt.md", "## Limits\n\nNone.\n", "## Limits\n\n")
    [problem] = _only(tree, "sections")
    assert "'Limits' is empty" in problem.message


def test_other_sections_can_follow_the_required_ones(tree):
    tree.write("levers/prompt.md", LEVER + "\n## Notes\n\nMore.\n")
    assert tree.problems() == []


def test_size_is_reported(tree):
    tree.write("smells/s-one.md", SMELL + "\n## Notes\n\n" + "x" * MAX_CHARS["smells"])
    _only(tree, "size")


def test_shape_is_reported(tree):
    tree.edit("playbooks/p-one.md", "  - single_turn", "  - quantum")
    [problem] = _only(tree, "shape")
    assert "'quantum'" in problem.message


# ---- links, reachability and depth ----


def test_link_is_reported(tree):
    tree.edit("levers/prompt.md", "## See also\n\nNone.", "## See also\n\n[x](gone.md)")
    _only(tree, "link")


def test_a_link_spelled_in_another_case_is_reported(tree):
    tree.edit("techniques/t-one.md", "../levers/prompt.md", "../levers/Prompt.md")
    # The How section then links no lever document either.
    assert tree.rules() == {"link", "technique-lever-links"}


def test_a_link_out_of_the_skill_is_reported(tree):
    (tree.root.parent / "outside.md").write_text("x")
    tree.edit(
        "levers/prompt.md",
        "## See also\n\nNone.",
        "## See also\n\n[x](../../../outside.md)",
    )
    _only(tree, "link")


def test_an_external_link_and_an_anchor_are_not_reported(tree):
    tree.edit(
        "levers/prompt.md",
        "## See also\n\nNone.",
        "## See also\n\n[a](https://example.com/a) [b](#limits)",
    )
    assert tree.problems() == []


@pytest.mark.parametrize(
    "fence",
    [
        "```\n[x](gone.md)\n```python\n[y](gone.md)\n```",
        "````\n[x](gone.md)\n```\n[y](gone.md)\n````",
        "~~~\n[x](gone.md)\n```\n~~~",
    ],
    ids=["info-string-does-not-close", "shorter-fence-does-not-close", "tilde"],
)
def test_a_fence_closes_only_on_a_matching_fence(tree, fence):
    tree.edit("levers/prompt.md", "## See also\n\nNone.", f"## See also\n\n{fence}")
    assert tree.problems() == []
    tree.edit("levers/prompt.md", fence, fence + "\n\n[z](gone.md)")
    [problem] = _only(tree, "link")
    assert "gone.md" in problem.message


def test_a_link_inside_a_fence_is_not_a_link(tree):
    tree.edit(
        "levers/prompt.md",
        "## See also\n\nNone.",
        "## See also\n\n```\n[x](gone.md)\n```",
    )
    assert tree.problems() == []
    lever = load(tree.root).get("levers", "prompt")
    assert lever is not None and lever.links == ()


def test_reachable_is_reported(tree):
    # A dormant technique has no index row, and nothing else links it.
    tree.write("techniques/t-asleep.md", TECHNIQUE.replace("active", "dormant"))
    [problem] = _only(tree, "reachable")
    assert problem.path == "techniques/t-asleep.md"


def test_depth_is_reported(tree):
    """A chain of dormant techniques, each linked only from the one before
    it, walks past the limit."""
    chain = [f"t-deep-{n}" for n in range(1, 7)]
    for this, after in zip(chain, [*chain[1:], None], strict=True):
        alternatives = f"[next](./{after}.md), when deeper." if after else "None."
        tree.write(
            f"techniques/{this}.md",
            TECHNIQUE.replace("active", "dormant").replace(
                "## Alternatives\n\nNone.", f"## Alternatives\n\n{alternatives}"
            ),
        )
    tree.edit(
        "techniques/t-one.md",
        "## Alternatives\n\nNone.",
        "## Alternatives\n\n[deep](./t-deep-1.md), when deep.",
    )
    # builder.md (0) -> t-one (1) -> t-deep-1 (2) ... t-deep-5 (6)
    problems = _only(tree, "depth")
    assert {p.path for p in problems} == {
        "techniques/t-deep-5.md",
        "techniques/t-deep-6.md",
    }


# ---- levers and techniques ----


def test_technique_lever_ids_is_reported(tree):
    tree.edit("techniques/t-one.md", "  - prompt\n", "  - prompt\n  - ghost\n")
    [problem] = _only(tree, "technique-lever-ids")
    assert "'ghost'" in problem.message


def test_lever_ops_is_reported(tree):
    tree.edit("levers/prompt.md", "  - create_prompt", "  - make_it_so")
    _only(tree, "lever-ops")


def test_technique_lever_links_is_reported(tree):
    tree.edit(
        "techniques/t-one.md", "Edit the [prompt](../levers/prompt.md).", "Edit it."
    )
    [problem] = _only(tree, "technique-lever-links")
    assert "does not link" in problem.message


def test_a_lever_linked_and_not_named_is_reported(tree):
    tree.write(
        "levers/skill.md",
        LEVER.replace("The prompt", "Skills").replace("prompt text", "skill text"),
    )
    tree.edit(
        "techniques/t-one.md",
        "(../levers/prompt.md).",
        "(../levers/prompt.md) and a [skill](../levers/skill.md).",
    )
    [problem] = _only(tree, "technique-lever-links")
    assert "'skill'" in problem.message


def test_technique_stance_is_reported(tree):
    tree.edit("techniques/t-one.md", "It depends. On the test.", "Perhaps keep it.")
    _only(tree, "technique-stance")


@pytest.mark.parametrize("stance", ["Keep it.", "It must move an eval."])
def test_each_stance_is_accepted(tree, stance):
    tree.edit("techniques/t-one.md", "It depends.", stance)
    assert tree.problems() == []


def test_technique_priors_is_reported(tree):
    tree.edit("techniques/t-one.md", "Source: own experiment.", "Source: a friend.")
    _only(tree, "technique-priors")


def test_priors_that_are_not_a_list_are_reported(tree):
    tree.edit("techniques/t-one.md", "## Priors\n\n", "## Priors\n\nSome text.\n\n")
    _only(tree, "technique-priors")


def test_no_prior_is_accepted(tree):
    tree.edit(
        "techniques/t-one.md",
        "- **A test agent.** One line was tried. It worked.\n"
        "  Source: own experiment. 2026-01-01.",
        "None yet.",
    )
    assert tree.problems() == []


def test_alternatives_is_reported(tree):
    tree.edit(
        "techniques/t-one.md",
        "## Alternatives\n\nNone.",
        "## Alternatives\n\n[a playbook](../playbooks/p-one.md), when it fits.",
    )
    _only(tree, "alternatives")


# ---- playbooks and smells ----


def test_playbook_steps_is_reported(tree):
    tree.edit("playbooks/p-one.md", "../techniques/t-one.md", "../smells/s-one.md")
    # The step links a smell (not allowed), and so no technique (required).
    problems = _only(tree, "playbook-steps")
    assert len(problems) == 2


def test_a_playbook_with_no_step_link_is_reported(tree):
    tree.edit(
        "playbooks/p-one.md",
        "1. [One technique](../techniques/t-one.md), when the test says so.",
        "1. Try something.",
    )
    _only(tree, "playbook-steps")


def test_smell_remedies_is_reported(tree):
    tree.edit("smells/s-one.md", "../playbooks/p-one.md", "../levers/prompt.md")
    _only(tree, "smell-remedies")


def test_a_smell_with_no_remedy_is_reported(tree):
    tree.edit(
        "smells/s-one.md",
        "- [One playbook](../playbooks/p-one.md), when it fits.",
        "- Fix it.",
    )
    [problem] = _only(tree, "smell-remedies")
    assert "at least one" in problem.message


@pytest.mark.parametrize(
    ("change", "registry"),
    [
        # detected_by has code, and no detector is named
        (("detectors:\n  - long_thing\n", ""), {}),
        # an agent-only smell names a detector
        (("  - code\n", "  - agent\n"), REGISTRY),
        # the named detector is not registered
        (("  - long_thing", "  - long_other"), REGISTRY),
        # the registered detector names this smell, and the smell does not list it
        (None, REGISTRY | {"long_extra": "long-thing"}),
        # the named detector is registered for another smell
        (None, {"long_thing": "s-one"}),
        # the registry names a smell that has no document
        (None, REGISTRY | {"long_ghost": "ghost"}),
    ],
    ids=[
        "code-without-detectors",
        "detectors-without-code",
        "unknown-detector",
        "unlisted-detector",
        "detector-of-another-smell",
        "detector-of-no-smell",
    ],
)
def test_smell_detectors_is_reported(tree, change, registry):
    if change:
        tree.edit("smells/long-thing.md", *change)
    _only(tree, "smell-detectors", detectors=registry)


# ---- sub-files, index, strays ----


def test_a_linked_sub_file_is_accepted(tree):
    tree.write("techniques/t-one/recipe.md", "# Recipe\n\nMore detail.\n")
    tree.edit(
        "techniques/t-one.md",
        "Read a trace.",
        "Read a trace. See [the recipe](t-one/recipe.md).",
    )
    assert tree.problems() == []


def test_subfile_is_reported(tree):
    tree.write("techniques/t-one/recipe.md", "# Recipe\n")
    # Linked from another document, so that it is reachable.
    tree.edit(
        "playbooks/p-one.md",
        "Nothing else.",
        "See [it](../techniques/t-one/recipe.md).",
    )
    [problem] = _only(tree, "subfile")
    assert "does not link" in problem.message


def test_a_sub_file_that_is_not_markdown_is_reported(tree):
    tree.write("techniques/t-one/data.txt", "data")
    _only(tree, "subfile")


def test_a_sub_file_with_no_owner_is_reported(tree):
    tree.write("techniques/ghost/recipe.md", "# Recipe\n")
    tree.edit(
        "playbooks/p-one.md",
        "Nothing else.",
        "See [it](../techniques/ghost/recipe.md).",
    )
    [problem] = _only(tree, "subfile")
    assert "no component" in problem.message


def test_size_of_a_sub_file_is_reported(tree):
    tree.write("techniques/t-one/recipe.md", "x" * (MAX_CHARS["subfile"] + 1))
    tree.edit(
        "techniques/t-one.md", "Read a trace.", "See [the recipe](t-one/recipe.md)."
    )
    _only(tree, "size")


def test_index_is_reported(tree):
    tree.edit(
        "techniques/t-one.md", "which edits the prompt", "which changes the prompt"
    )
    problems = _only(tree, "index", rebuild=False)
    # The techniques block is in three entry files.
    assert {p.path for p in problems} == {
        "builder.md",
        "report.md",
        "technique-index.md",
    }


def test_a_missing_block_is_reported(tree):
    tree.edit("analyzer.md", "<!-- END GENERATED: playbooks-failure -->", "")
    [problem] = _only(tree, "index")
    assert problem.path == "analyzer.md"


def test_a_missing_entry_file_is_reported(tree):
    (tree.references / "analyzer.md").unlink()
    [problem] = _only(tree, "index")
    assert "missing" in problem.message


@pytest.mark.parametrize("path", ["notes.md", "levers/data.txt", "other/x.md"])
def test_stray_is_reported(tree, path):
    tree.write(path, "x")
    [problem] = _only(tree, "stray")
    assert problem.path == path


def test_a_file_the_installer_leaves_out_is_ignored(tree):
    tree.write(".DS_Store", "x")
    tree.write("levers/__pycache__/x.pyc", "x")
    assert tree.problems() == []


def test_a_hidden_markdown_file_is_a_stray(tree):
    # The installer ships it to every loop agent, so the check reads it.
    tree.write(".draft.md", "x")
    [problem] = _only(tree, "stray")
    assert problem.path == ".draft.md"


# ---- the parser ----


def test_a_heading_inside_a_fence_is_not_a_section(tree):
    tree.edit(
        "techniques/t-one.md",
        "Edit the [prompt](../levers/prompt.md).",
        "Edit the [prompt](../levers/prompt.md).\n\n```\n## Budget\n```",
    )
    assert tree.problems() == []
    doc = load(tree.root).get("techniques", "t-one")
    assert doc is not None
    assert [s.heading for s in doc.sections].count("Budget") == 1
    how = doc.section("How")
    assert how is not None and "## Budget" in how.text


# ---- the generated blocks ----


def test_a_dormant_technique_is_left_out_of_the_index(tree):
    tree.write("techniques/t-asleep.md", TECHNIQUE.replace("active", "dormant"))
    blocks = render_blocks(load(tree.root))
    assert "t-asleep" not in blocks[("builder.md", "techniques")]
    assert "t-one" in blocks[("builder.md", "techniques")]


def test_render_blocks_is_stable(tree):
    first = render_blocks(load(tree.root))
    assert first == render_blocks(load(tree.root))
    assert first[("planner.md", "playbooks-failure")] == (
        "\n| Playbook | Applies to | Description |\n|---|---|---|\n"
        "| [p-one](playbooks/p-one.md) | tools, single_turn | A playbook of the"
        " test tree, for a failure that the traces show. |\n"
    )
    assert first[("planner.md", "playbooks-goal")] == "\nNone yet.\n"


def test_build_rewrites_only_between_the_markers(tree):
    before = tree.read("builder.md")
    tree.write("techniques/t-two.md", TECHNIQUE.replace("One technique", "Two"))
    changed = write_blocks(load(tree.root))
    after = tree.read("builder.md")
    assert tree.references / "builder.md" in changed
    assert tree.references / "smells.md" not in changed
    block = "techniques"
    outside = before.replace(read_block(before, block), "")
    assert after.replace(read_block(after, block), "") == outside
    assert "t-two" in read_block(after, block)


# ---- the command line ----


@pytest.fixture
def cli_tree(tree) -> Tree:
    """The tree without its code-detected smell: the command line passes the
    package's own detector registry, which does not know `long_thing`."""
    (tree.references / "smells" / "long-thing.md").unlink()
    write_blocks(load(tree.root))
    return tree


def test_the_command_line_exits_1_on_a_problem(cli_tree, capsys):
    tree = cli_tree
    assert main(["check", "--root", str(tree.root)]) == 0
    tree.edit("levers/prompt.md", "## See also\n\nNone.", "## See also\n\n[x](gone.md)")
    assert main(["check", "--root", str(tree.root)]) == 1
    out = capsys.readouterr().out
    assert "link  levers/prompt.md  gone.md" in out


def test_the_command_line_build_writes_the_blocks_and_exits_0(cli_tree):
    tree = cli_tree
    tree.write("techniques/t-two.md", TECHNIQUE.replace("One technique", "Two"))
    assert main(["check", "--root", str(tree.root)]) == 1
    assert main(["build", "--root", str(tree.root)]) == 0
    assert "t-two" in tree.read("technique-index.md")
    assert main(["check", "--root", str(tree.root)]) == 0


def test_the_command_line_build_reports_a_file_that_is_not_utf8(cli_tree):
    (cli_tree.references / "analyzer.md").write_bytes(b"# x\n\xff\n")
    assert main(["build", "--root", str(cli_tree.root)]) == 1


def test_the_command_line_exits_2_without_a_tree(tmp_path, capsys):
    assert main(["check", "--root", str(tmp_path)]) == 2
    assert "no references/" in capsys.readouterr().err
