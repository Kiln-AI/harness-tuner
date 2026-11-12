"""The knowledge system: load, check and index the skill
`kiln-harness-tuner-knowledge`.

    uv run python -m kiln_harness_tuner.knowledge check
    uv run python -m kiln_harness_tuner.knowledge build
"""

from kiln_harness_tuner.knowledge.check import check
from kiln_harness_tuner.knowledge.index import render_blocks, write_blocks
from kiln_harness_tuner.knowledge.load import (
    Document,
    KnowledgeBase,
    KnowledgeError,
    default_root,
    load,
)
from kiln_harness_tuner.knowledge.schema import Problem

__all__ = [
    "Document",
    "KnowledgeBase",
    "KnowledgeError",
    "Problem",
    "check",
    "default_root",
    "load",
    "render_blocks",
    "write_blocks",
]
