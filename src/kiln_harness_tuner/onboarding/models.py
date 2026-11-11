"""Which models the task may use: the guided start's Models question.

Four answers, and the flags each one sets:

- **Keep the task's model** — `--models keep`. The run pins the baseline's
  (provider, model) in code at start-up (`model_policy.keep_policy`).
- **Any model Kiln offers** — `--models any`: every model on the providers
  that have a key. It may move the task to a more expensive model.
- **Pick models** — a policy file of the picked (provider, model) pairs,
  written to the state folder's `policies/` and passed as `--model-policy`.
- **Use a policy file** — `--model-policy` with the path typed.

The models to pick from are Kiln's `GET /api/available_models`, which lists
only the providers that have a key.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from kiln_harness_tuner.model_policy import MAX_CHECKED_MODELS
from kiln_harness_tuner.onboarding.keys import provider_name
from kiln_harness_tuner.onboarding.terminal import Option

Pair = tuple[str, str]  # (Kiln provider id, Kiln model id)

KEEP, ANY, PICK, FILE = "keep", "any", "pick", "file"
PICK_LIMIT = MAX_CHECKED_MODELS
PICKED_PREFIX = "picked-"

ANY_COST_WARNING = (
    "The tuner may move the task to a more expensive model. The cost per"
    " request can go up, and so can the run's spend."
)


@dataclass(frozen=True)
class OfferedModel:
    """One model Kiln offers, as the picker shows it."""

    provider: str  # Kiln's provider id
    provider_label: str  # Kiln's display name for it
    model: str  # Kiln's model id
    name: str  # Kiln's display name for the model

    @property
    def pair(self) -> Pair:
        return (self.provider, self.model)


def offered_models(available: Any) -> list[OfferedModel]:
    """The models in a `GET /api/available_models` payload, in its order:
    one entry per provider with a `models` list. Rows it can't read are
    left out."""
    models: list[OfferedModel] = []
    for entry in available if isinstance(available, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("provider_id"), str):
            continue
        provider = entry["provider_id"]
        label = entry.get("provider_name")
        label = label if isinstance(label, str) and label else provider_name(provider)
        for row in entry.get("models") or []:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            name = row.get("name")
            models.append(
                OfferedModel(
                    provider,
                    label,
                    row["id"],
                    name if isinstance(name, str) and name else row["id"],
                )
            )
    return models


def pick_options(models: Sequence[OfferedModel]) -> list[Option[Pair]]:
    """One picker row per model: its name, its provider and its id, grouped
    by provider. The provider is a column too: one model is often offered
    by several providers, and the All facet shows them together."""
    return [
        Option(
            model.pair,
            model.name,
            (model.provider_label, model.model),
            group=model.provider_label,
        )
        for model in models
    ]


def picked_policy(pairs: Iterable[Pair]) -> dict[str, Any]:
    """The policy of the picked pairs: one exception each, nothing else."""
    return {
        "description": (
            "Models picked at the guided start. The task may use these"
            " models and no other."
        ),
        "exceptions": [
            {"provider": provider, "model": model, "reason": "picked"}
            for provider, model in pairs
        ],
    }


def write_picked_policy(
    folder: Path, pairs: Sequence[Pair], *, now: datetime | None = None
) -> Path:
    """Write the policy of `pairs` to a new file in `folder`, and return its
    path. The name has the time, so an earlier pick is kept."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = folder / f"{PICKED_PREFIX}{stamp}.json"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{PICKED_PREFIX}{stamp}-{n}.json"
    path.write_text(json.dumps(picked_policy(pairs), indent=1) + "\n")
    return path


def picked_pairs(path: str | None) -> list[Pair] | None:
    """The pairs of a policy file the picker wrote, or None for any other
    file (or none): the picker can start from its own earlier pick."""
    if not path or not Path(path).name.startswith(PICKED_PREFIX):
        return None
    try:
        data = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError):
        return None
    rows = data.get("exceptions") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    return [
        (row["provider"], row["model"])
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("provider"), str)
        and isinstance(row.get("model"), str)
    ]
