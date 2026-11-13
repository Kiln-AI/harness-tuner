"""The built-in map from Kiln's provider ids to models.dev's.

A port of the Kiln server's `KILN_TO_MODELS_DEV_PROVIDER`,
`PROVIDERS_WITHOUT_MODELS_DEV` and `MODELS_DEV_PROVIDER_PREFERENCE`
(`kiln_server`, `api/kiln_fastapi_api/tools/model_info/model_builder.py`).
A `models_dev_id` that Kiln publishes in its `provider_list` wins over this
table, so the published file is the place to add or correct a mapping.
"""

from __future__ import annotations

# A provider that is not here, and not in `NO_MODELS_DEV`, has the same id
# in models.dev.
KILN_TO_MODELS_DEV: dict[str, str] = {
    "amazon_bedrock": "amazon-bedrock",
    "azure_openai": "azure",
    # Not in the Kiln server's table. models.dev keeps Cloudflare's models
    # under this id, and Kiln's `claude-maintain-models` skill uses it.
    "cloudflare": "cloudflare-workers-ai",
    "fireworks_ai": "fireworks-ai",
    "gemini_api": "google",
    "siliconflow_cn": "siliconflow-cn",
    "together_ai": "togetherai",
    "vertex": "google-vertex",
}

NO_MODELS_DEV: frozenset[str] = frozenset(
    {
        "docker_model_runner",
        "ollama",
        "kiln_fine_tune",
        "kiln_custom_registry",
        "openai_compatible",
        "huggingface",
    }
)

# The providers whose models.dev entry gives a model's facts first.
PREFERENCE: tuple[str, ...] = (
    "openrouter",
    "openai",
    "anthropic",
    "gemini_api",
    "groq",
    "together_ai",
)


def models_dev_provider(kiln_provider: str, published: str | None) -> str | None:
    """The models.dev id of a Kiln provider, or None when models.dev has no
    entry for it. `published` is Kiln's `provider_list[].models_dev_id`."""
    if published:
        return published
    if kiln_provider in NO_MODELS_DEV:
        return None
    return KILN_TO_MODELS_DEV.get(kiln_provider, kiln_provider)
