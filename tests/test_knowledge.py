"""The shipped knowledge skill, `kiln-harness-tuner-knowledge`.

Every structural rule runs on the real tree here. The rules themselves, one
deliberate violation each, are in `test_knowledge_checks.py`.
"""

from __future__ import annotations

import re

import pytest

from kiln_harness_tuner.install_agents import AGENT_CLIS, install_for_run
from kiln_harness_tuner.knowledge import check, default_root, load, render_blocks
from kiln_harness_tuner.knowledge.__main__ import code_side
from kiln_harness_tuner.knowledge.index import read_block
from kiln_harness_tuner.knowledge.load import SKILL_NAME
from kiln_harness_tuner.knowledge.schema import COMPONENT_DIRS, ENTRY_BLOCKS


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
