"""The models each agent CLI offers, for the picker of the model question.

- Claude Code takes an alias or a full model name. Its list is fixed here.
- Codex keeps the models of the signed-in account in
  `$CODEX_HOME/models_cache.json` (`~/.codex` by default). That file is
  Codex's own cache, not a documented format. A file that is missing or has
  an unexpected shape gives no list.
- OpenCode prints its models with `opencode models`, one `PROVIDER/MODEL` a
  line. A command that fails or prints no model gives no list.

With no list, the question asks for the model name as text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# OpenAI's page of the models that Codex can use.
CODEX_MODELS_URL = "https://developers.openai.com/codex/models"
OPENCODE_MODELS_TIMEOUT_SECS = 20.0

_OPENCODE_MODEL = re.compile(r"[^\s/]+/\S+")


@dataclass(frozen=True)
class AgentModel:
    """One model the picker shows: the name the CLI takes, and what the row
    says under it when it is highlighted."""

    name: str
    description: str | None = None


CLAUDE_MODELS = (
    AgentModel("opus", "The default. The strongest plans."),
    AgentModel("sonnet", "Cheaper, with weaker plans."),
    AgentModel("haiku", "The cheapest, with the weakest plans."),
    AgentModel("claude-fable-5-2", "Claude Fable 5.2."),
)

# Gives the models of one agent CLI by its kind, or None when it has none.
ModelLister = Callable[[str], "ModelList | None"]


def codex_home(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    configured = env.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def codex_models(home: Path) -> tuple[AgentModel, ...] | None:
    """The models in Codex's cache that Codex itself lists, in its order."""
    try:
        data: Any = json.loads((home / "models_cache.json").read_text())
    except (OSError, ValueError):
        return None
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    models = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug.strip() or " " in slug.strip():
            continue
        # Codex hides a model whose visibility isn't "list" from its own
        # picker. A model with no visibility field is kept.
        if entry.get("visibility", "list") != "list":
            continue
        description = entry.get("description")
        models.append(
            AgentModel(
                slug.strip(),
                description.strip() if isinstance(description, str) else None,
            )
        )
    return tuple(models) or None


def codex_default_model(home: Path) -> str | None:
    """The `model` that Codex's `config.toml` sets, when it sets one."""
    try:
        config = tomllib.loads((home / "config.toml").read_text())
    except (OSError, ValueError):
        return None
    model = config.get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


def opencode_models(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[AgentModel, ...] | None:
    """The `PROVIDER/MODEL` lines that `opencode models` prints."""
    try:
        done = run(
            ["opencode", "models"],
            capture_output=True,
            text=True,
            timeout=OPENCODE_MODELS_TIMEOUT_SECS,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    names = [line.strip() for line in done.stdout.splitlines()]
    models = [AgentModel(name) for name in names if _OPENCODE_MODEL.fullmatch(name)]
    return tuple(models) or None


@dataclass(frozen=True)
class ModelList:
    """The models of one agent CLI, and the CLI's own default model when it
    is known."""

    models: tuple[AgentModel, ...]
    own_default: str | None = None


def listed_models(kind: str) -> ModelList | None:
    """The models of the agent CLI `kind`, or None when it has no list."""
    if kind == "claude":
        return ModelList(CLAUDE_MODELS)
    if kind == "codex":
        home = codex_home()
        models = codex_models(home)
        return ModelList(models, codex_default_model(home)) if models else None
    if kind == "opencode":
        models = opencode_models()
        return ModelList(models) if models else None
    return None
