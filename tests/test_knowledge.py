"""The shipped knowledge skill, `kiln-harness-tuner-knowledge`, and the
maintenance skill that changes it.

Every structural rule runs on the real tree here. The rules themselves, one
deliberate violation each, are in `test_knowledge_checks.py`. The maintenance
skill, `.agents/skills/knowledge-maintenance/`, is held to the same schema
through its templates, and the repository files that point to it are held to
it here too.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

import pytest
from test_knowledge_checks import build_tree

from kiln_harness_tuner.dispatch.claude_cli import DEFAULT_TEMPLATES_DIR
from kiln_harness_tuner.install_agents import AGENT_CLIS, install_for_run, source_dir
from kiln_harness_tuner.knowledge import check, default_root, load, render_blocks
from kiln_harness_tuner.knowledge.__main__ import code_side
from kiln_harness_tuner.knowledge.index import read_block
from kiln_harness_tuner.knowledge.load import SKILL_NAME
from kiln_harness_tuner.knowledge.schema import (
    COMPONENT_DIRS,
    ENTRY_BLOCKS,
    META_MODELS,
    REQUIRED_SECTIONS,
)
from kiln_harness_tuner.schemas import BatchPlanInput

REPO_ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE = REPO_ROOT / ".agents" / "skills" / "knowledge-maintenance"
MAINTENANCE_SKILL = MAINTENANCE / "SKILL.md"
MAX_MAINTENANCE_LINES = 500


def _problems(root=None):
    gateway_ops, detectors = code_side()
    return check(load(root), gateway_ops=gateway_ops, detectors=detectors)


def test_the_shipped_knowledge_passes_every_check():
    problems = _problems()
    assert not problems, "\n".join(f"{p.rule}  {p.path}  {p.message}" for p in problems)


def test_the_shipped_knowledge_has_a_document_of_each_type():
    """The guard that stops the check above passing on an empty tree."""
    kb = load()
    for kind in COMPONENT_DIRS:
        assert kb.by_kind(kind), f"no document in {kind}/"


# V1 coverage, sections 1 and 3: the six levers, and the twenty-eight
# techniques with the levers each one uses.
V1_LEVERS = {"prompt", "skill", "new_tool", "tool_config", "params", "model"}
V1_TECHNIQUE_LEVERS = {
    "add-the-missing-requirement": ["prompt"],
    "state-the-output-contract": ["prompt"],
    "answer-only-from-the-source": ["prompt"],
    "decline-rules-paragraph": ["prompt"],
    "policy-as-step-by-step-procedures": ["prompt", "skill"],
    "structure-a-long-prompt": ["prompt"],
    "one-general-rule-per-edit": ["prompt", "skill"],
    "long-prompt-into-a-skill": ["skill", "prompt"],
    "skill-into-the-prompt": ["prompt", "skill"],
    "path-index-in-the-skill-description": ["skill"],
    "rewrite-an-inherited-skill": ["skill"],
    "prune-one-group-at-a-time": ["prompt", "skill", "tool_config"],
    "attach-the-knowledge-source": ["tool_config", "skill"],
    "document-into-a-skill": ["skill", "tool_config"],
    "cap-and-preview-wrapper": ["new_tool"],
    "roll-up-tool": ["new_tool", "prompt"],
    "guide-skill-for-a-tool-set": ["skill"],
    "new-tool-description": ["new_tool"],
    "directive-in-the-tool-response": ["new_tool"],
    "normalize-inputs-wrapper": ["new_tool"],
    "errors-the-agent-can-act-on": ["new_tool"],
    "guard-tool-calls-at-the-boundary": ["new_tool", "prompt"],
    "remove-or-scope-tools": ["tool_config"],
    "consolidate-tools": ["new_tool", "tool_config"],
    "thinking-level-ladder": ["params"],
    "model-swap-inside-a-class": ["model"],
    "faster-provider-for-the-same-model": ["model"],
    "decision-model-for-a-classifier": ["model"],
}

# V1 coverage, section 3: the owner's tips on a query tool, as sub-files of
# the guide skill technique.
GUIDE_SKILL_SUB_FILES = ("sample-queries", "schema-as-leaf-files", "timestamps")


def test_the_v1_levers_are_present():
    assert {d.id for d in load().by_kind("levers")} >= V1_LEVERS


@pytest.mark.parametrize("technique", sorted(V1_TECHNIQUE_LEVERS))
def test_the_v1_technique_is_present_with_its_levers(technique):
    doc = load().get("techniques", technique)
    assert doc is not None, f"techniques/{technique}.md is missing"
    assert doc.meta is not None and doc.meta.status == "active"
    assert doc.meta.levers == V1_TECHNIQUE_LEVERS[technique]


def test_the_guide_skill_for_a_tool_set_has_its_sub_files():
    kb = load()
    owner = kb.get("techniques", "guide-skill-for-a-tool-set")
    assert owner is not None
    linked = {link.resolved for link in owner.links}
    folder = kb.references / "techniques" / "guide-skill-for-a-tool-set"
    for name in GUIDE_SKILL_SUB_FILES:
        path = folder / f"{name}.md"
        assert path.is_file(), path
        assert path in linked, f"guide-skill-for-a-tool-set.md does not link {name}.md"


# V1 coverage, section 4: the twenty-one playbooks, each with its kind.
V1_PLAYBOOK_KINDS = {
    "know-the-agent-before-you-plan": "order",
    "ordering-work": "order",
    "spending-the-budget": "order",
    "cheaper-or-faster": "goal",
    "working-inside-restrictions": "goal",
    "fit-the-tool-layer-to-the-agent": "design",
    "requirement-missing-from-the-prompt": "failure",
    "output-contract-not-met": "failure",
    "answers-not-grounded": "failure",
    "wrong-scope-or-refusal": "failure",
    "missing-context-the-agent-could-be-given": "failure",
    "failure-at-a-tool-call": "failure",
    "malformed-tool-calls": "failure",
    "wrong-tool-chosen": "failure",
    "skill-is-not-read": "failure",
    "lost-in-long-instructions": "failure",
    "change-the-surface-not-the-words": "failure",
    "dealing-with-a-saturated-eval": "eval",
    "dealing-with-an-eval-near-zero": "eval",
    "dealing-with-a-noisy-eval": "eval",
    "dealing-with-train-wins-rejected-at-val": "eval",
}


@pytest.mark.parametrize("playbook", sorted(V1_PLAYBOOK_KINDS))
def test_the_v1_playbook_is_present_with_its_kind(playbook):
    doc = load().get("playbooks", playbook)
    assert doc is not None, f"playbooks/{playbook}.md is missing"
    assert doc.meta is not None and doc.meta.kind == V1_PLAYBOOK_KINDS[playbook]


# V1 coverage, section 2: the thirteen smells, each with who detects it, and
# the code detectors of the three that code detects. Then the smells added
# after V1: `old-model` (the model-provider-info project).
V1_SMELL_DETECTION = {
    "very-long-prompt": ["long_prompt"],
    "one-file-skill-with-many-topics": [],
    "skill-too-small-to-earn-its-place": [],
    "very-long-skill-description": ["long_skill_description"],
    "faulty-inherited-skill": [],
    "tool-results-flood-the-context": [],
    "fixed-call-chain": [],
    "general-query-tool-without-a-guide": [],
    "too-many-narrow-tools": [],
    "tool-search": [],
    "document-behind-a-lookup-tool": [],
    "very-long-tool-description": ["long_tool_description"],
    "tools-on-a-live-server": [],
    "old-model": ["old_model"],
}


@pytest.mark.parametrize("smell", sorted(V1_SMELL_DETECTION))
def test_the_v1_smell_is_present_with_its_detection(smell):
    doc = load().get("smells", smell)
    assert doc is not None, f"smells/{smell}.md is missing"
    detectors = V1_SMELL_DETECTION[smell]
    assert doc.meta is not None
    assert doc.meta.detected_by == (["code"] if detectors else ["agent"])
    assert doc.meta.detectors == detectors


def test_the_check_uses_the_real_detector_registry():
    from kiln_harness_tuner.smells import detector_smells

    _ops, detectors = code_side()
    assert detectors == detector_smells()
    assert detectors


def test_spending_the_budget_names_real_planner_input_fields():
    """The playbook tells the planner which input fields carry the budget,
    the prices and the noise. A renamed field would send it to nothing."""
    doc = load().get("playbooks", "spending-the-budget")
    assert doc is not None
    situation = doc.section("Situation").text
    named = set(re.findall(r"^- `([a-z_]+)`", situation, re.MULTILINE))
    assert len(named) >= 4, named
    missing = named - set(BatchPlanInput.model_fields)
    assert not missing, f"not fields of BatchPlanInput: {sorted(missing)}"


# Each role that reads the knowledge, by its prompt template, and the entry
# file that the template names. The baseline reviewer comes in a later phase.
ROLE_ENTRY_FILES = {
    "batch_plan": "planner.md",
    "create_experiment_rc": "builder.md",
    "analyze_experiment_results": "analyzer.md",
    "report_writer": "report.md",
}


@pytest.mark.parametrize("template", sorted(ROLE_ENTRY_FILES))
def test_each_role_template_names_its_entry_file(template):
    """A loop agent loads a skill because its template says so in plain words,
    so each reading role is sent to its own entry file, not to SKILL.md."""
    text = " ".join((DEFAULT_TEMPLATES_DIR / f"{template}.md").read_text().split())
    entry = ROLE_ENTRY_FILES[template]
    assert f"`{SKILL_NAME}`" in text, f"{template}.md does not name {SKILL_NAME}"
    assert f"`references/{entry}`" in text, f"{template}.md does not name {entry}"
    assert (default_root() / "references" / entry).is_file()


LADDER = (
    source_dir()
    / "skills"
    / "kiln-harness-tuner-batch-selection"
    / "references"
    / "lever-ladder.md"
)
LADDER_NAMES = re.compile(r"lever-ladder\.md|lever\s+ladder", re.IGNORECASE)


def test_the_lever_ladder_file_is_gone_and_nothing_names_it():
    """The knowledge system took over the lever ladder (V1 coverage, section
    6). One source of truth: no file that an agent, a user or the code reads
    still sends a reader to the old table."""
    assert not LADDER.exists()
    roots = (REPO_ROOT / "src", REPO_ROOT / "docs")
    naming = [
        path.relative_to(REPO_ROOT).as_posix()
        for root in roots
        for path in sorted(root.rglob("*"))
        if path.suffix in {".md", ".py"}
        and LADDER_NAMES.search(path.read_text(encoding="utf-8"))
    ]
    assert not naming, naming


def test_the_generated_blocks_are_current():
    kb = load()
    for (entry, block), text in render_blocks(kb).items():
        current = read_block((kb.references / entry).read_text(encoding="utf-8"), block)
        assert current == text, (
            f"{entry}: the block {block!r} is stale. Run"
            " `uv run python -m kiln_harness_tuner.knowledge build`."
        )


def test_each_entry_file_exists_and_has_its_blocks():
    references = default_root() / "references"
    for entry, blocks in ENTRY_BLOCKS.items():
        text = (references / entry).read_text(encoding="utf-8")
        for block in blocks:
            assert f"<!-- BEGIN GENERATED: {block} -->" in text, (entry, block)
            assert f"<!-- END GENERATED: {block} -->" in text, (entry, block)


def test_skill_md_names_its_folder_and_links_each_entry_file():
    text = (default_root() / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(rf"^name: {SKILL_NAME}$", text, re.MULTILINE)
    links = set(re.findall(r"\]\(([^)\s]+)\)", text))
    for entry in ENTRY_BLOCKS:
        assert f"references/{entry}" in links, f"SKILL.md does not link {entry}"
        assert (default_root() / "references" / entry).is_file()


@pytest.mark.parametrize("agent_cli", sorted(AGENT_CLIS))
def test_the_knowledge_skill_installs_for_each_agent_cli(tmp_path, agent_cli):
    """A run's agents folder holds the whole skill, and the copy passes every
    check where the agent reads it: no link leans on a file that is not
    installed."""
    folder = install_for_run(agent_cli, tmp_path / "agents", "9.9.9")
    installed = folder / AGENT_CLIS[agent_cli].skills / SKILL_NAME
    assert (installed / "SKILL.md").is_file()
    shipped = {d.path for d in load().documents}
    assert {d.path for d in load(installed).documents} == shipped
    assert not _problems(installed)


# --- the maintenance skill --------------------------------------------------

# The template of each component type, by its directory.
TEMPLATES = {
    "levers": "lever.md",
    "techniques": "technique.md",
    "playbooks": "playbook.md",
    "smells": "smell.md",
}

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
COMMENT_RE = re.compile(r"[ \t]*<!--.*?-->[ \t]*\n?", re.DOTALL)

# What an author writes in place of each placeholder. The ids name documents
# of the tree that `build_tree` writes.
FILLED = {
    "TITLE": "A filled template",
    "DESCRIPTION": "A document made from its template, with each placeholder filled.",
    "REVIEWED": "2026-01-01",
    "TEXT": "Text.",
    "GATEWAY_OP": "create_prompt",
    "COST": "low",
    "KIND": "failure",
    "STANCE": "It depends.",
    "LEVER_ID": "prompt",
    "LEVER_TITLE": "prompt",
    "TECHNIQUE_ID": "t-one",
    "TECHNIQUE_TITLE": "One technique",
    "PLAYBOOK_ID": "p-one",
    "PLAYBOOK_TITLE": "One playbook",
}


def _template(kind: str) -> str:
    return (MAINTENANCE / "templates" / TEMPLATES[kind]).read_text(encoding="utf-8")


def _fill(template: str) -> str:
    """The template as an author leaves it: each placeholder filled and each
    HTML comment of guidance removed."""
    unknown = set(PLACEHOLDER_RE.findall(template)) - FILLED.keys()
    assert not unknown, f"placeholders with no fixed value: {sorted(unknown)}"
    filled = PLACEHOLDER_RE.sub(lambda m: FILLED[m.group(1)], template)
    return COMMENT_RE.sub("", filled)


def test_there_is_a_template_for_each_component_type():
    assert set(TEMPLATES) == set(COMPONENT_DIRS)
    for name in TEMPLATES.values():
        assert (MAINTENANCE / "templates" / name).is_file(), name


@pytest.mark.parametrize("kind", COMPONENT_DIRS)
def test_each_template_gives_a_valid_document(tmp_path, kind):
    """A template cannot go out of step with the schema: filled, it passes
    every check in a valid tree."""
    tree = build_tree(tmp_path)
    tree.write(f"{kind}/filled-template.md", _fill(_template(kind)))
    assert tree.problems() == []
    assert load(tree.root).get(kind, "filled-template") is not None


@pytest.mark.parametrize("kind", COMPONENT_DIRS)
def test_each_template_has_the_required_headings_and_front_matter_keys(kind):
    """The order of the template is the order of the schema, so an author who
    fills it in place gets each part where the check looks for it."""
    text = _template(kind)
    headings = tuple(re.findall(r"^## (.+)$", text, re.MULTILINE))
    assert headings == REQUIRED_SECTIONS[kind]
    front = text.split("---\n")[1]
    keys = re.findall(r"^([a-z_]+):", front, re.MULTILINE)
    # Every key of the model, apart from `detectors`, which a smell has only
    # when a code detector finds it.
    expected = [k for k in META_MODELS[kind].model_fields if k != "detectors"]
    assert keys == expected


def test_the_maintenance_skill_is_inside_its_line_limit():
    lines = MAINTENANCE_SKILL.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= MAX_MAINTENANCE_LINES, len(lines)


def test_the_maintenance_skill_names_its_folder():
    text = MAINTENANCE_SKILL.read_text(encoding="utf-8")
    assert re.search(rf"^name: {MAINTENANCE.name}$", text, re.MULTILINE)
    assert re.search(r"^description: .{40,}$", text, re.MULTILINE)


def test_the_maintenance_skill_names_the_build_and_check_commands():
    text = MAINTENANCE_SKILL.read_text(encoding="utf-8")
    for command in (
        "uv run python -m kiln_harness_tuner.knowledge build",
        "uv run python -m kiln_harness_tuner.knowledge check",
        "uv run pytest tests/test_knowledge.py tests/test_knowledge_checks.py"
        " tests/test_backlog.py",
        "uv run ruff check",
        "uv run ruff format --check",
    ):
        assert command in text, command
    for test_file in (
        "test_knowledge.py",
        "test_knowledge_checks.py",
        "test_backlog.py",
    ):
        assert (REPO_ROOT / "tests" / test_file).is_file(), test_file


def test_agents_md_points_to_the_maintenance_skill():
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert ".agents/skills/knowledge-maintenance/SKILL.md" in text
    assert MAINTENANCE_SKILL.is_file()


def _ignores(pattern: str, path: str) -> bool:
    """True when the `.gitignore` line `pattern` ignores `path` or a folder
    above it. A pattern with a `/` before its end is anchored at the root; a
    bare name matches at any depth."""
    anchored = "/" in pattern.rstrip("/")
    bare = pattern.strip("/")
    parts = path.split("/")
    if anchored:
        prefixes = ("/".join(parts[:n]) for n in range(1, len(parts) + 1))
        return any(fnmatch(prefix, bare) for prefix in prefixes)
    return any(fnmatch(part, bare) for part in parts)


def test_the_maintenance_skill_is_not_git_ignored():
    """The `/.agents/` rule was from the time when a run installed the loop
    skills into this repository. The maintenance skill lives there now."""
    files = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in MAINTENANCE.rglob("*")
        if p.is_file()
    ]
    assert files
    assert _ignores("/.agents/", files[0]), "the reader misses the old rule"
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    patterns = [line.strip() for line in lines if line.strip() and line[0] not in "#!"]
    ignored = [(p, f) for p in patterns for f in files if _ignores(p, f)]
    assert not ignored, ignored


def test_contributing_does_not_install_into_the_repository_root():
    """An install of the loop skills goes into a new, clean folder. In the
    root of this repository it would mix with the maintenance skill."""
    text = (REPO_ROOT / ".github" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert not re.search(r"--dest\s+\./?(?![\w/.])", text)
