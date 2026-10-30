"""`kiln_name` must produce names Kiln accepts.

Kiln accepts a name only when `string_to_valid_name(name) == name`. The oracle
below is Kiln's function copied as it is (kiln_ai/datamodel/basemodel.py), so a
change on either side shows up here as a failing case."""

from __future__ import annotations

import re
import unicodedata

import pytest

from kiln_harness_tuner.kiln_names import (
    PROVENANCE_NOTES_MAX_CHARS,
    agent_provenance,
    kiln_name,
)

_KILN_FORBIDDEN = r"[/\\?%*:|\"<>.,;=\n]"


def kiln_string_to_valid_name(name: str) -> str:
    valid_name = unicodedata.normalize("NFKD", name)
    valid_name = re.sub(_KILN_FORBIDDEN, " ", valid_name)
    valid_name = re.sub(r"[\x00-\x1F]", " ", valid_name)
    valid_name = re.sub(r"\s+", " ", valid_name)
    valid_name = re.sub(r"_+", "_", valid_name)
    return valid_name.strip("_").strip()


def kiln_accepts(name: str, max_length: int = 120) -> bool:
    return 1 <= len(name) <= max_length and kiln_string_to_valid_name(name) == name


CASES = [
    # the labels that lost run 260924-2010's screening round
    "SCREEN s1-1 - gemini 3.8 flash @ gemini_api, thinking low",
    "SCREEN s1-9 - qwen 3.8 27b @ cerebras, thinking none",
    "PROBE r0-4 - With thinking_level=low (baseline: medium), does it parse?",
    'quotes "inside" and <angles>',
    "  __a:b__  ",
    " _x_ ",
    "a  b__c",
    "tab\there\nnewline\r\x07bell",
    "_leading and trailing_",
    "ünïcödé — dash",
    "...",
    "",
    "x" * 300,
    "y" * 119 + " _z",
]


@pytest.mark.parametrize("text", CASES)
def test_every_name_is_one_kiln_accepts(text: str) -> None:
    name = kiln_name(text)
    assert kiln_accepts(name), repr(name)


@pytest.mark.parametrize("limit", [32, 60, 120])
def test_the_limit_holds(limit: int) -> None:
    name = kiln_name("a b. " * 100, limit)
    assert len(name) <= limit
    assert kiln_accepts(name, limit)


def test_a_valid_name_is_left_as_it_is() -> None:
    assert (
        kiln_name("SCREEN s1-1 - gemini 3-8 flash") == "SCREEN s1-1 - gemini 3-8 flash"
    )


def test_an_empty_result_gets_a_placeholder() -> None:
    assert kiln_name("...") == "unnamed"


def test_agent_provenance_is_origin_agent_with_its_parents() -> None:
    assert agent_provenance("a fork", ["rc_1", None, ""]) == {
        "notes": "a fork",
        "derived_from_ids": ["rc_1"],
        "origin": "agent",
    }


def test_agent_provenance_notes_stay_within_kilns_limit() -> None:
    notes = agent_provenance("x" * 5000, [])["notes"]
    assert len(notes) == PROVENANCE_NOTES_MAX_CHARS


def test_agent_provenance_drops_repeated_and_fine_tune_parents() -> None:
    body = agent_provenance(
        "a fork",
        ["424242684434", "finetune_run_config::p::t::f", "424242684434", None],
    )
    assert body["derived_from_ids"] == ["424242684434"]
