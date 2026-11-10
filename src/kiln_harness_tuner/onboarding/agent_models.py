"""The models the model question lists for each agent CLI.

The lists are fixed here, newest models first, and are updated by hand when
a CLI's provider releases new models. The question also has an Other row
for a typed name, so a model that is not listed can still be used. OpenCode
has no list: its models depend on the providers the user connected, so its
model is always typed.
"""

from __future__ import annotations

from dataclasses import dataclass

# OpenAI's page of the models that Codex can use.
CODEX_MODELS_URL = "https://developers.openai.com/codex/models"


@dataclass(frozen=True)
class AgentModel:
    """One model the picker shows: the name the CLI takes, and what the row
    says under it when it is highlighted."""

    name: str
    description: str | None = None


# Claude Code's aliases, each for the latest model of its family. A full
# model name, such as claude-opus-5-5, is typed after Other.
CLAUDE_MODELS = (
    AgentModel("opus", "The default. The strongest plans."),
    AgentModel("sonnet", "Cheaper, with weaker plans."),
    AgentModel("haiku", "The cheapest, with the weakest plans."),
    AgentModel("fable", "Claude Fable: the latest Fable model."),
)

CODEX_MODELS = (
    AgentModel("gpt-6.1-sol", "The latest workhorse model for coding."),
    AgentModel("gpt-6-astra", "Frontier intelligence for the most demanding work."),
    AgentModel("gpt-6.1-terra", "GPT-6.1 Terra."),
    AgentModel("gpt-6-luna", "Fast and affordable, for easier tasks."),
)

# The models each agent CLI lists, by its kind. A kind that is not here has
# no list.
AGENT_MODELS = {"claude": CLAUDE_MODELS, "codex": CODEX_MODELS}
