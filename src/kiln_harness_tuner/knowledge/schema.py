"""The fixed rules of the knowledge system: its layout, its limits and the
front matter of each component type.

The design is `specs/projects/biological_bootloader/components/
knowledge_documents.md`, sections 2 to 5. A component is one Markdown file.
Its directory gives its type, and its file name without `.md` gives its id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

TASK_SHAPES: tuple[str, ...] = (
    "single_turn",
    "multi_turn",
    "structured_output",
    "free_text",
    "classifier",
    "tools",
    "no_tools",
)

COMPONENT_DIRS: tuple[str, ...] = ("levers", "techniques", "playbooks", "smells")

# Characters, not tokens: a check that needs no tokenizer gives the same
# answer on every machine. A sub-file of any component has one limit.
MAX_CHARS: dict[str, int] = {
    "levers": 6000,
    "techniques": 12000,
    "playbooks": 8000,
    "smells": 4000,
    "subfile": 20000,
}

# Links from the nearest root. The longest designed chain is entry file,
# smell, playbook, technique, lever (4); a sub-file adds 1.
MAX_DEPTH = 5

REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "levers": ("What it is", "How the tuner changes it", "Limits", "See also"),
    "techniques": (
        "When to use",
        "When not to use",
        "Alternatives",
        "How",
        "Check that it took effect",
        "Budget",
        "If it does not move the evals",
        "Priors",
        "Confidence",
    ),
    "playbooks": (
        "Situation",
        "What to try, in order",
        "What not to do yet",
        "Budget",
    ),
    "smells": (
        "What it is",
        "How to recognise it",
        "How to confirm it",
        "What it blocks",
        "Remedies",
    ),
}

EVAL_STANCES: tuple[str, ...] = ("Keep it.", "It must move an eval.", "It depends.")
PRIOR_SOURCES: tuple[str, ...] = ("public", "own experiment", "anonymised")
NO_PRIORS = "None yet."

ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_ID_CHARS = 60

ENTRY_FILES: tuple[str, ...] = (
    "planner.md",
    "builder.md",
    "smells.md",
    "analyzer.md",
    "report.md",
)
INDEX_FILE = "technique-index.md"
ROOTS: tuple[str, ...] = (*ENTRY_FILES, INDEX_FILE)

PlaybookKind = Literal["goal", "order", "failure", "eval", "design"]
PLAYBOOK_KINDS: tuple[str, ...] = get_args(PlaybookKind)

# Entry file -> the generated blocks it holds, in the order they appear.
ENTRY_BLOCKS: dict[str, tuple[str, ...]] = {
    "planner.md": (
        "smells",
        "playbooks-order",
        "playbooks-goal",
        "playbooks-failure",
        "playbooks-eval",
        "playbooks-design",
    ),
    "builder.md": ("techniques", "levers"),
    "smells.md": ("smells-detail",),
    "analyzer.md": ("playbooks-failure",),
    "report.md": ("smells", "playbooks", "techniques"),
    INDEX_FILE: ("techniques",),
}


@dataclass(frozen=True)
class Problem:
    """One broken rule: the rule id, the file relative to `references/`, and
    what is wrong."""

    rule: str
    path: str
    message: str


AppliesTo = Literal["all"] | list[str]


class CommonMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    reviewed: date

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _one_line(value, "title", 3, 60)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        # The description is the text of an index row: a `|` would end the
        # table cell.
        if "|" in value:
            raise ValueError("description must not hold a '|'")
        return _one_line(value, "description", 40, 200)

    @field_validator("reviewed")
    @classmethod
    def _reviewed(cls, value: date) -> date:
        if value > date.today():
            raise ValueError(f"reviewed {value} is in the future")
        return value

    @field_validator("applies_to", check_fields=False)
    @classmethod
    def _applies_to(cls, value: AppliesTo) -> AppliesTo:
        # Each value is checked against TASK_SHAPES by the rule `shape`, so
        # that an unknown shape is reported as such and not as bad front
        # matter.
        if isinstance(value, list) and not value:
            raise ValueError("applies_to must be 'all' or a non-empty list")
        return value


class LeverMeta(CommonMeta):
    gateway_ops: list[str] = []


class TechniqueMeta(CommonMeta):
    status: Literal["active", "dormant"]
    levers: list[str] = Field(min_length=1)
    applies_to: AppliesTo
    cost: Literal["low", "medium", "high"]


class PlaybookMeta(CommonMeta):
    kind: PlaybookKind
    applies_to: AppliesTo


class SmellMeta(CommonMeta):
    applies_to: AppliesTo
    detected_by: list[Literal["code", "agent"]] = Field(min_length=1)
    detectors: list[str] = []


META_MODELS: dict[str, type[CommonMeta]] = {
    "levers": LeverMeta,
    "techniques": TechniqueMeta,
    "playbooks": PlaybookMeta,
    "smells": SmellMeta,
}


def _one_line(value: str, field: str, low: int, high: int) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field} must be one line")
    if not low <= len(value) <= high:
        raise ValueError(
            f"{field} has {len(value)} characters; the limit is {low} to {high}"
        )
    return value
