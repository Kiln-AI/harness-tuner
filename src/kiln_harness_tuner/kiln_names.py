"""Names the loop gives Kiln objects (run configs, prompts, specs, evals),
and the provenance it stamps on the objects that its own code creates.

Kiln accepts a name only when its own clean-up (`string_to_valid_name` in
`kiln_ai.datamodel.basemodel`) leaves it unchanged. That clean-up normalises
to Unicode NFKD, turns `/ \\ ? % * : | " < > . , ; =`, newlines and other
control characters into spaces, collapses runs of whitespace and of
underscores, and trims underscores and whitespace from both ends. Any other
name is refused with HTTP 422 and the object is not made.

A screening round can be lost to this rule: every candidate's
run config was named from a label such as "gemini 3.8 flash @ gemini_api,
thinking low", and all nine were refused. So every name the loop sends is made
here, in one place, and never built inline.

The rule is mirrored, not imported: this repository does not depend on Kiln's
Python package. `tests/test_kiln_names.py` pins it to Kiln's behaviour.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_FORBIDDEN = re.compile(r"[/\\?%*:|\"<>.,;=\n]")
_CONTROL = re.compile(r"[\x00-\x1F]")
_SPACES = re.compile(r"\s+")
_UNDERSCORES = re.compile(r"_+")


def _kiln_clean(name: str) -> str:
    """One pass of Kiln's `string_to_valid_name`, without truncation."""
    name = unicodedata.normalize("NFKD", name)
    name = _FORBIDDEN.sub(" ", name)
    name = _CONTROL.sub(" ", name)
    name = _SPACES.sub(" ", name)
    name = _UNDERSCORES.sub("_", name)
    return name.strip("_").strip()


def kiln_name(text: str, limit: int = 120) -> str:
    """`text` made into a name Kiln accepts, at most `limit` characters.

    Kiln's clean-up is applied until the name no longer changes (one pass is
    not always enough: " _x_ " becomes "_x_", then "x"), then cut to `limit`
    and cleaned again. A name that would be empty becomes "unnamed"."""
    name = text
    for _ in range(5):
        cleaned = _kiln_clean(name[:limit])
        if cleaned == name:
            break
        name = cleaned
    return name or "unnamed"


# Kiln's documented limit on `provenance.notes`.
PROVENANCE_NOTES_MAX_CHARS = 2000

# The id prefix of a fine-tune's run config, which the run-config list
# includes but which has no run-config file a provenance id can name.
FINETUNE_RUN_CONFIG_PREFIX = "finetune_run_config::"


def agent_provenance(notes: str, parent_ids: list[str | None]) -> dict[str, Any]:
    """The `provenance` body for an object that the loop's code creates
    without a human request. `origin` is "agent". `parent_ids` become
    `derived_from_ids`: the same-type objects that this one is a fork, clone
    or copy of, primary parent first. An empty or repeated id is dropped, and
    so is a fine-tune run config's id: Kiln checks each id against the
    task's run-config files, and a fine-tune is not one of them, so the create
    would fail. The notes still name that parent.

    Kiln's web UI gives an "agent" object its badge and puts it in the lineage
    tree from `derived_from_ids`. Notes longer than Kiln's limit are cut."""
    if len(notes) > PROVENANCE_NOTES_MAX_CHARS:
        notes = notes[: PROVENANCE_NOTES_MAX_CHARS - 1] + "…"
    ids: list[str] = []
    for raw in parent_ids:
        if not raw or str(raw).startswith(FINETUNE_RUN_CONFIG_PREFIX):
            continue
        if str(raw) not in ids:
            ids.append(str(raw))
    return {"notes": notes, "derived_from_ids": ids, "origin": "agent"}
