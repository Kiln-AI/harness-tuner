"""The model policy: which (provider, model) pairs a run config may use.

Some deployments may run the subject task only on some providers. Some
models may go through a router only when they are closed models, and some
providers are not allowed at all. That is a rule
about where the task's data is sent, so it is enforced in code at every place
a run config comes into being or is measured — never only in a prompt.

A policy is a JSON file passed with `--model-policy`:

```
{
  "description": "free text, shown to the planner",
  "allow":      [{"provider": "<glob>", "model": "<glob>"}, ...],
  "deny":       [{"provider": "<glob>", "model": "<glob>", "reason": "..."}],
  "exceptions": [{"provider": "<exact>", "model": "<exact>", "reason": "..."}]
}
```

A pair is decided in this order, and the first step that matches decides:

1. **exceptions** — an exact (provider, model) pair, no wildcards. Allowed,
   whatever the other rules say. This is how a one-off pair is let through
   that a general deny (or the absence of an allow) would refuse.
2. **deny** — any glob match refuses.
3. **allow** — any glob match allows.
4. Otherwise refused. A policy is an allowlist.

Globs are `fnmatch` patterns, case-sensitive, over the Kiln provider id
(`model_provider_name`) and the Kiln model id (`model_name`). No policy means
every pair is allowed — the behaviour before this module.

Without a policy, `--models` says which models the task may use
(`resolve_models`). `keep` becomes a policy too, at start-up, once the
baseline's pair is known (`keep_policy`): one exception, the baseline's
(provider, model), and nothing else. So "keep the model" is enforced by the
same checks as a policy file.

Judges are not subject to the policy: an eval config names its model under
`model_provider`/`model_name` (not `model_provider_name`), evals are frozen,
and the policy is about the task under optimisation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from kiln_harness_tuner.objective import is_efficiency

_WILDCARDS = set("*?[]")

# `--models`: keep = the baseline's (provider, model) only; any = every model
# Kiln offers. The default follows the objective (`resolve_models`).
MODEL_CHOICES = ("keep", "any")
# The most models the start-up model preflight tests; past it, a run's other
# allowed models go untested (`model_preflight`). The guided start's picker
# takes at most this many.
MAX_CHECKED_MODELS = 12
KEEP_SOURCE = "--models keep"


def resolve_models(models: str | None, objective: str) -> str:
    """`--models` as the run uses it: the flag, else `keep` under the
    quality objective and `any` under speed and cost, where a model or
    provider swap is the first lever. A `--model-policy` replaces it."""
    if models is not None:
        return models
    return "any" if is_efficiency(objective) else "keep"


def keep_policy(provider: str, model: str) -> dict[str, Any]:
    """The policy `--models keep` stands for, as JSON: the baseline's one
    pair, as an exception. Every other pair is refused."""
    return {
        "description": (
            f"Keep the task's model: {provider}/{model} ({KEEP_SOURCE})."
            " No other model or provider is allowed. Thinking level,"
            " temperature and top_p may change."
        ),
        "exceptions": [
            {"provider": provider, "model": model, "reason": "the baseline's model"}
        ],
    }


class ModelPolicyError(ValueError):
    """The policy file cannot be loaded. A start-up error with a fix line."""


@dataclass(frozen=True)
class Rule:
    provider: str
    model: str
    reason: str = ""

    def matches(self, provider: str, model: str) -> bool:
        return fnmatchcase(provider, self.provider) and fnmatchcase(model, self.model)

    def label(self) -> str:
        return f"{self.provider}/{self.model}"

    @property
    def exact(self) -> bool:
        """No wildcard: the rule names one provider and one model."""
        return not _WILDCARDS & set(self.provider + self.model)

    @property
    def exact_provider(self) -> bool:
        """No wildcard in the provider: the rule names one provider."""
        return not _WILDCARDS & set(self.provider)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    why: str


@dataclass(frozen=True)
class ModelPolicy:
    allow: tuple[Rule, ...]
    deny: tuple[Rule, ...] = ()
    exceptions: tuple[Rule, ...] = ()
    description: str = ""

    def decide(self, provider: str | None, model: str | None) -> Decision:
        if not provider or not model:
            return Decision(
                False,
                "the run config names no model provider and model, so the"
                " policy cannot place it",
            )
        for rule in self.exceptions:
            if rule.provider == provider and rule.model == model:
                return Decision(True, f"exception {rule.label()}")
        for rule in self.deny:
            if rule.matches(provider, model):
                reason = f" — {rule.reason}" if rule.reason else ""
                return Decision(False, f"denied by {rule.label()}{reason}")
        for rule in self.allow:
            if rule.matches(provider, model):
                return Decision(True, f"allowed by {rule.label()}")
        return Decision(False, "no allow rule matches it")

    def refusal(self, provider: str | None, model: str | None) -> str | None:
        """None when the pair is allowed, else a one-line reason."""
        decision = self.decide(provider, model)
        if decision.allowed:
            return None
        return f"model {provider}/{model} is outside the model policy: {decision.why}"

    def summary(self) -> dict[str, Any]:
        """The policy as the planner reads it (its input carries this)."""
        return {
            "description": self.description,
            "allow": [r.label() for r in self.allow],
            "deny": [
                r.label() + (f" ({r.reason})" if r.reason else "") for r in self.deny
            ],
            "exceptions": [
                r.label() + (f" ({r.reason})" if r.reason else "")
                for r in self.exceptions
            ],
            "order": "exceptions, then deny, then allow; anything else is refused",
        }

    @staticmethod
    def from_json(data: Any, source: str = "the policy") -> ModelPolicy:
        if not isinstance(data, dict):
            raise ModelPolicyError(f"{source} must be a JSON object")
        unknown = set(data) - {"description", "allow", "deny", "exceptions"}
        if unknown:
            raise ModelPolicyError(
                f"{source} has unknown key(s) {sorted(unknown)} — a misspelled"
                " rule list would be silently ignored"
            )
        allow = _rules(data.get("allow"), "allow", source)
        if not allow and not data.get("exceptions"):
            raise ModelPolicyError(
                f"{source} allows nothing: give at least one allow rule or exception"
            )
        exceptions = _rules(data.get("exceptions"), "exceptions", source)
        for rule in exceptions:
            if _WILDCARDS & set(rule.provider + rule.model):
                raise ModelPolicyError(
                    f"{source}: exception {rule.label()} has a wildcard — an"
                    " exception is one exact provider/model pair"
                )
        return ModelPolicy(
            allow=allow,
            deny=_rules(data.get("deny"), "deny", source),
            exceptions=exceptions,
            description=str(data.get("description") or ""),
        )

    @staticmethod
    def load(path: Path) -> ModelPolicy:
        try:
            data = json.loads(path.read_text())
        except OSError as exc:
            raise ModelPolicyError(f"cannot read {path} ({exc})") from exc
        except ValueError as exc:
            raise ModelPolicyError(f"{path} is not valid JSON ({exc})") from exc
        return ModelPolicy.from_json(data, str(path))


def _rules(raw: Any, name: str, source: str) -> tuple[Rule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ModelPolicyError(f"{source}: '{name}' must be a list of rules")
    rules: list[Rule] = []
    for entry in raw:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("provider"), str)
            or not isinstance(entry.get("model"), str)
            or not entry["provider"]
            or not entry["model"]
        ):
            raise ModelPolicyError(
                f"{source}: every '{name}' rule needs non-empty string 'provider'"
                f" and 'model' fields, got {entry!r}"
            )
        extra = set(entry) - {"provider", "model", "reason"}
        if extra:
            raise ModelPolicyError(
                f"{source}: '{name}' rule {entry!r} has unknown key(s) {sorted(extra)}"
            )
        rules.append(
            Rule(entry["provider"], entry["model"], str(entry.get("reason") or ""))
        )
    return tuple(rules)


def model_pairs(node: Any) -> list[tuple[str | None, str | None]]:
    """Every (provider, model) a request body or a run config sets for the
    SUBJECT task, at any depth: each dict carrying `model_provider_name`
    (the run-config-properties key) contributes its pair. A dict with a
    `model_name` and a run-config `type` but no provider still counts, with
    provider None, so a half-written config is refused, not skipped."""
    found: list[tuple[str | None, str | None]] = []
    if isinstance(node, dict):
        if "model_provider_name" in node or (
            "model_name" in node and node.get("type") == "kiln_agent"
        ):
            provider = node.get("model_provider_name")
            model = node.get("model_name")
            found.append(
                (
                    provider if isinstance(provider, str) else None,
                    model if isinstance(model, str) else None,
                )
            )
        for value in node.values():
            found.extend(model_pairs(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(model_pairs(value))
    return found


def run_config_pair(rc: dict[str, Any]) -> tuple[str | None, str | None]:
    """The (provider, model) of one run config as the API returns it. An MCP
    run config, or one with no properties, has (None, None)."""
    props = rc.get("run_config_properties")
    if not isinstance(props, dict):
        return None, None
    provider = props.get("model_provider_name")
    model = props.get("model_name")
    return (
        provider if isinstance(provider, str) else None,
        model if isinstance(model, str) else None,
    )


def allowed_models(
    policy: ModelPolicy | None, available: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The models the server offers that the policy allows, as
    `{"provider", "model", "name"}` rows. `available` is the
    `GET /api/available_models` payload: one entry per provider with a
    `models` list."""
    rows: list[dict[str, str]] = []
    for provider_entry in available:
        if not isinstance(provider_entry, dict):
            continue
        provider = provider_entry.get("provider_id")
        if not isinstance(provider, str):
            continue
        for model in provider_entry.get("models") or []:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                continue
            if policy is not None and not policy.decide(provider, model["id"]).allowed:
                continue
            row: dict[str, Any] = {
                "provider": provider,
                "model": model["id"],
                "name": str(model.get("name") or model["id"]),
            }
            levels = model.get("available_thinking_levels")
            if isinstance(levels, dict) and levels:
                # The server's order, lowest first — a level outside this list
                # is refused by the code probes.
                row["thinking_levels"] = [str(v) for v in levels.values()]
            rows.append(row)
    return rows
