"""The API-key check: Kiln has a key for every provider the run will call.

The providers come from the run's own sources: the baseline run config, the
judge of each eval the loop measures, the simulated user of a multi-turn
task, and each provider `--model-policy` names exactly. Kiln's
`GET /api/available_models` lists a provider that takes a key only when
every key it needs is set, so a provider missing from that list has no key.
The check never reads `/api/settings`, which holds the keys themselves.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.job_drives import MULTI_TURN_MODE
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.memory import BaselineRecordMissingError, MemoryWriter
from kiln_harness_tuner.model_policy import ModelPolicy, run_config_pair
from kiln_harness_tuner.onboarding import kiln_urls
from kiln_harness_tuner.onboarding.terminal import Action, ActionKind, Problem
from kiln_harness_tuner.quarantine import task_dirs
from kiln_harness_tuner.setup import (
    CONFIG_GONE_STATUSES,
    SETUP_CONFIG_READ_CONCURRENCY,
    SetupPreconditionError,
    _require_rows,
    _run_config_id,
    _usable_run_configs,
    choose_baseline_rc,
    eval_rows_from_response,
    measurable_evals,
    unreadable_response_message,
)

# Providers that run without an API key. The model preflight still tests
# their models, as it does today.
NO_KEY_PROVIDERS = frozenset(
    {
        "ollama",
        "docker_model_runner",
        "openai_compatible",
        "kiln_custom_registry",
        "kiln_fine_tune",
    }
)

AVAILABLE_MODELS_PATH = "/api/available_models"

# Kiln's own display names for the providers that take a key
# (`provider_name_from_id` in kiln_ai/adapters/provider_tools.py), kept here
# because `GET /api/settings/available_providers` lists only the providers
# whose keys are set, by the same rule as `/api/available_models`, so it can
# never name a missing provider. An ID not here is shown alone.
PROVIDER_NAMES = {
    "amazon_bedrock": "Amazon Bedrock",
    "anthropic": "Anthropic",
    "azure_openai": "Azure OpenAI",
    "cerebras": "Cerebras",
    "cloudflare": "Cloudflare",
    "featherless_ai": "Featherless AI",
    "fireworks_ai": "Fireworks AI",
    "gemini_api": "Gemini API",
    "groq": "Groq",
    "huggingface": "Hugging Face",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "siliconflow_cn": "SiliconFlow",
    "together_ai": "Together AI",
    "typesafe": "TypeSafe AI",
    "vertex": "Gemini Enterprise Agent Platform",
}

SIMULATED_USER = "the simulated user in the task's eval inputs"
POLICY_FLAG = "--model-policy"


@dataclass(frozen=True)
class Need:
    """A provider the run calls, and what calls it, as the fix line says."""

    provider: str
    used_by: str
    from_policy: bool = False


@dataclass(frozen=True)
class Baseline:
    """The run config the run starts from, as the summary names it."""

    name: str
    model: str | None
    provider: str | None  # Kiln's display name


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


async def needed_providers(
    kiln: KilnApi,
    *,
    project_id: str,
    task_id: str,
    project_path: Path,
    policy: ModelPolicy | None,
) -> tuple[list[Need], Baseline | None]:
    """Every provider the run will call, in the order the fix lines name
    them: the baseline, the judges, the simulated user, then the policy.
    With them, the baseline run config, or `None` when none is found."""
    base = f"/api/projects/{project_id}/tasks/{task_id}"
    task, run_config_body, eval_body, spec_body = await asyncio.gather(
        kiln.call("GET", base),
        kiln.call("GET", f"{base}/run_configs"),
        kiln.call("GET", f"{base}/evals"),
        kiln.call("GET", f"{base}/specs"),
    )
    if not isinstance(task, dict):
        raise SetupPreconditionError(unreadable_response_message(base, task))
    run_configs = _usable_run_configs(
        _require_rows(f"{base}/run_configs", run_config_body)
    )
    # The readiness check has already warned about evals Kiln couldn't load.
    evals, _load_errors = eval_rows_from_response(f"{base}/evals", eval_body)
    specs = _require_rows(f"{base}/specs", spec_body)

    memory = MemoryWriter(kiln, project_id=project_id, task_id=task_id)
    baseline_rc_id = await _baseline_rc_id(memory, task, run_configs)
    baseline_row = next(
        (rc for rc in run_configs if _run_config_id(rc) == baseline_rc_id), None
    )
    needs = _baseline_needs(baseline_row)
    needs += await _judge_needs(kiln, base, measurable_evals(evals, specs))
    if task.get("turn_mode") == MULTI_TURN_MODE:
        needs += [
            Need(provider, SIMULATED_USER)
            for provider in sorted(simulated_user_providers(project_path, task_id))
        ]
    needs += _policy_needs(policy)
    return needs, _baseline(baseline_row)


async def _baseline_rc_id(
    memory: MemoryWriter, task: dict[str, Any], run_configs: list[dict[str, Any]]
) -> str | None:
    """The run config the run starts from, by the rule setup seeds the
    baseline with: the one the tuner recorded for the task in Kiln's memory,
    else `choose_baseline_rc`. Read only; setup writes the record later."""
    try:
        record = await memory.require_baseline_record()
    except BaselineRecordMissingError:
        # No record yet: setup will seed one by this rule.
        return choose_baseline_rc(task, run_configs).rc_id if run_configs else None
    try:
        return memory.baseline_rc_id(record)
    except BaselineRecordMissingError:
        # A record that names no run config. The run's start-up refuses it
        # later, with its own message.
        return None


def _run_config_name(row: dict[str, Any]) -> str:
    return _text(row.get("name")) or _run_config_id(row)


def _baseline_needs(row: dict[str, Any] | None) -> list[Need]:
    if row is None:
        return []
    provider, _model = run_config_pair(row)
    if not provider:
        return []
    return [Need(provider, f'the task\'s run config "{_run_config_name(row)}"')]


def _baseline(row: dict[str, Any] | None) -> Baseline | None:
    if row is None:
        return None
    provider, model = run_config_pair(row)
    return Baseline(
        _run_config_name(row),
        model or None,
        provider_name(provider) if provider else None,
    )


def judge_provider(config: Any) -> str | None:
    """The provider an eval config's judge calls: the root `model_provider`
    of a legacy judge, or a V2 `llm_judge`'s `properties.model_provider`.
    `None` for a config that calls no model, such as a code eval."""
    if not isinstance(config, dict):
        return None
    legacy = _text(config.get("model_provider"))
    if legacy is not None:
        return legacy
    properties = config.get("properties")
    if isinstance(properties, dict) and properties.get("type") == "llm_judge":
        return _text(properties.get("model_provider"))
    return None


async def _judge_needs(
    kiln: KilnApi, base: str, measurable: Sequence[tuple[dict[str, Any], str]]
) -> list[Need]:
    """The judge provider of each eval the loop measures, from the eval's
    default config. Bounded like setup's own config reads."""
    semaphore = asyncio.Semaphore(SETUP_CONFIG_READ_CONCURRENCY)

    async def provider_of(eval_id: str, config_id: str) -> str | None:
        async with semaphore:
            config = await _eval_config(
                kiln, f"{base}/evals/{eval_id}/eval_config/{config_id}"
            )
        return judge_provider(config)

    providers = await asyncio.gather(
        *(provider_of(str(row["id"]), config_id) for row, config_id in measurable)
    )
    return [
        Need(provider, f'eval "{_text(row.get("name")) or row["id"]}"\'s judge')
        for (row, _config_id), provider in zip(measurable, providers, strict=True)
        if provider is not None
    ]


async def _eval_config(kiln: KilnApi, path: str) -> Any:
    """The eval config, or `None` when it is gone. Setup's rule: a 404 or a
    410 is a real answer, and any other error means the config couldn't be
    read, so it propagates."""
    try:
        return await kiln.call("GET", path)
    except KilnAPIError as exc:
        if exc.status_code not in CONFIG_GONE_STATUSES:
            raise
        return None


def simulated_user_providers(project_path: Path, task_id: str) -> set[str]:
    """The providers of a multi-turn task's simulated user. Kiln stores the
    simulated user per eval input (`data.drive_config`), not on the eval, so
    they are read from the task's eval inputs on disk. Only `drive_config`
    is used, never the input text or the labels."""
    providers: set[str] = set()
    for task_dir in task_dirs(project_path, task_id):
        for path in task_dir.glob("eval_inputs/*/eval_input.kiln"):
            provider = _drive_provider(path)
            if provider is not None:
                providers.add(provider)
    return providers


def _drive_provider(path: Path) -> str | None:
    """One eval input's `data.drive_config.model_provider`, or `None`. A
    file that can't be read, or has another shape, is skipped: the run's own
    readers of the file report it."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data = doc.get("data") if isinstance(doc, dict) else None
    if isinstance(data, str):  # some inputs store `data` as a JSON string
        try:
            data = json.loads(data)
        except ValueError:
            return None
    drive_config = data.get("drive_config") if isinstance(data, dict) else None
    if not isinstance(drive_config, dict):
        return None
    return _text(drive_config.get("model_provider"))


def _policy_needs(policy: ModelPolicy | None) -> list[Need]:
    """The providers the policy names exactly. A rule with a wildcard
    provider names none; without a policy, the planner sees only the models
    Kiln already offers, which have their keys."""
    if policy is None:
        return []
    return [
        Need(rule.provider, POLICY_FLAG, from_policy=True)
        for rule in (*policy.allow, *policy.exceptions)
        if rule.exact_provider
    ]


def _keyed(needs: Sequence[Need]) -> list[Need]:
    """The needs whose provider takes a key."""
    return [need for need in needs if need.provider not in NO_KEY_PROVIDERS]


def provider_name(provider: str) -> str:
    """Kiln's display name for `provider`, or the ID when it has none."""
    return PROVIDER_NAMES.get(provider, provider)


def keys_passed_text(needs: Sequence[Need]) -> str:
    """The step line's text for a passed key check: the providers that
    needed a key, in the order the run needs them."""
    providers = dict.fromkeys(need.provider for need in _keyed(needs))
    if not providers:
        return "none needed (local models only)"
    return ", ".join(provider_name(provider) for provider in providers)


async def key_problem(
    kiln: KilnApi, *, host: str, needs: Sequence[Need]
) -> Problem | None:
    """The providers in `needs` that Kiln has no key for, as one problem, or
    `None` when it has them all."""
    checked = _keyed(needs)
    if not checked:
        return None
    body = await kiln.call("GET", AVAILABLE_MODELS_PATH)
    available = {
        row["provider_id"]
        for row in _require_rows(AVAILABLE_MODELS_PATH, body)
        if isinstance(row, dict) and isinstance(row.get("provider_id"), str)
    }
    missing: dict[str, list[Need]] = {}
    for need in checked:
        if need.provider not in available:
            missing.setdefault(need.provider, []).append(need)
    if not missing:
        return None
    if len(missing) == 1:
        title = "Kiln is missing an API key this run needs"
        intro = "The tuner calls this provider through Kiln, which needs a key for it:"
        keys = "key"
    else:
        title = f"Kiln is missing {len(missing)} API keys this run needs"
        intro = (
            "The tuner calls these providers through Kiln, which needs a key for each:"
        )
        keys = "keys"
    return Problem(
        title,
        (
            intro,
            *(_missing_line(p, ns) for p, ns in missing.items()),
            f"Add the {keys} in Kiln under Settings → AI Providers.",
        ),
        actions=(
            Action(
                "p",
                "Open AI Providers in Kiln",
                ActionKind.OPEN,
                url=kiln_urls.providers(host),
            ),
        ),
        retry_label=f"Check again (I've added the {keys})",
    )


def _missing_line(provider: str, needs: Sequence[Need]) -> str:
    used_by = ", ".join(dict.fromkeys(need.used_by for need in needs))
    # Removing the provider from the policy fixes the line only when nothing
    # else needs it.
    from_policy_only = all(need.from_policy for need in needs)
    fix = ", or remove it from the model policy" if from_policy_only else ""
    return f"  • {provider_name(provider)}: used by {used_by}{fix}"
