"""The restrictions the owner states at the start, and how code enforces each.

Some limits are known only because the owner states them: the providers the
task's data may go to, the origin of the models, the data retention, the
tools that must not change. Each one has a flag, and the guided start asks
for them (`onboarding/restrictions.py`). A restriction that only text asks
for is not a restriction, so each answer has a place where code refuses:

- **Providers** (`--providers`): the model policy
  (`model_policy.Restriction`), on every run config an agent creates and
  before any run config is measured or promoted. The start refuses a
  baseline, a judge or a simulated user on another provider
  (`route_refusal`).
- **Model origin, by weights** (`--model-weights`): the model policy, from
  the model catalogue's `open_weights`.
- **Model origin, by family** (`--exclude-model-families`): the model
  policy, from the model catalogue's `family`.
- **Data retention** (`--zero-data-retention`): the model policy refuses
  the routes on which the tuner cannot make sure of zero retention
  (`NO_ZERO_RETENTION`), and so
  does the start for a baseline, a judge or a simulated user.
- **Tools that must not change** (`--lock-tool`): the tool lock
  (`ToolLock`), in the gateway's `create_run_config`, `create_code_tool`
  and archive operations, and in the loop before a run config is measured
  or promoted.

The model restrictions are written into the run's `model_policy.json` as
`restrictions`, which the policy checks before its own rules, so an exception
in a policy file never passes one. The tool lock is the run's
`restrictions.json`, which each gateway process reads.

What code cannot check is said, not hidden (`Restrictions.limits`): the
summary of the start shows it, and so does the planner's input.
"""

from __future__ import annotations

import glob
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kiln_harness_tuner.model_catalog import Catalog
from kiln_harness_tuner.model_policy import Restriction, Rule

WEIGHTS = ("open", "closed")
RESTRICTIONS_FILE = "restrictions.json"
CODE_TOOL_PREFIX = "kiln_tool::code::"
SKILL_TOOL_PREFIX = "kiln_tool::skill::"

# The routes on which the tuner cannot make sure of zero data retention,
# each with the reason. A route is on this list while the tuner does not set
# and check the setting that asks for it. OpenRouter: Kiln may have a run
# config property for it (`openrouter_routing.zero_data_retention`), and the
# tuner does not yet set or check that property.
NO_ZERO_RETENTION: dict[str, str] = {
    "openrouter": (
        "the tuner does not yet set or check OpenRouter's zero data retention"
        " setting (`openrouter_routing.zero_data_retention`) on a run config"
    ),
}

LIST_ERROR = "Give one or more ids, separated by commas"


class RestrictionError(ValueError):
    """A restriction that code cannot turn into a check. A start-up error."""


def parse_id_list(text: str) -> list[str]:
    """`a,b, c` as `["a", "b", "c"]`: each id once, in the order given."""
    ids: list[str] = []
    for part in text.split(","):
        part = part.strip()
        if part and part not in ids:
            ids.append(part)
    if not ids:
        raise ValueError(LIST_ERROR)
    return ids


def _ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value) if value else ()


@dataclass(frozen=True)
class Restrictions:
    """What the owner stated. Each field is empty when nothing was stated."""

    providers: tuple[str, ...] = ()  # only these Kiln provider ids
    model_weights: str | None = None  # one of WEIGHTS: only such models
    exclude_families: tuple[str, ...] = ()  # no models of these families
    zero_data_retention: bool = False
    locked_tools: tuple[str, ...] = ()  # tool ids that must not change

    @classmethod
    def from_args(cls, args: Any) -> Restrictions:
        """The restrictions of the parsed flags, or of the guided start's
        answers: both hold them under the flags' dests."""
        return cls(
            providers=_ids(getattr(args, "providers", None)),
            model_weights=getattr(args, "model_weights", None) or None,
            exclude_families=tuple(
                family.lower()
                for family in _ids(getattr(args, "exclude_model_families", None))
            ),
            zero_data_retention=bool(getattr(args, "zero_data_retention", False)),
            locked_tools=_ids(getattr(args, "lock_tool", None)),
        )

    def apply(self, args: Any) -> None:
        """Write the restrictions into `args`, under the flags' dests."""
        args.providers = list(self.providers) or None
        args.model_weights = self.model_weights
        args.exclude_model_families = list(self.exclude_families) or None
        args.zero_data_retention = self.zero_data_retention
        args.lock_tool = list(self.locked_tools) or None

    @property
    def on_models(self) -> bool:
        """Is a restriction stated that the model policy enforces?"""
        return bool(
            self.providers
            or self.model_weights
            or self.exclude_families
            or self.zero_data_retention
        )

    @property
    def on_origin(self) -> bool:
        """Is a restriction stated that needs the model catalogue?"""
        return bool(self.model_weights or self.exclude_families)

    def __bool__(self) -> bool:
        return self.on_models or bool(self.locked_tools)

    def statements(self) -> list[str]:
        """Each restriction in words, as the summary and the planner read it."""
        lines: list[str] = []
        if self.providers:
            lines.append(f"only these providers: {', '.join(self.providers)}")
        if self.model_weights:
            lines.append(f"{self.model_weights}-weight models only")
        if self.exclude_families:
            lines.append(
                f"no models of these families: {', '.join(self.exclude_families)}"
            )
        if self.zero_data_retention:
            lines.append("zero data retention only")
        if self.locked_tools:
            lines.append(f"these tools must not change: {', '.join(self.locked_tools)}")
        return lines

    def limits(self) -> list[str]:
        """What code cannot check, for each restriction that is stated."""
        lines: list[str] = []
        if self.on_origin:
            lines.append(
                "The model catalogue has no country or company of origin: state"
                " such a restriction as the model families to exclude. A model"
                " that the catalogue has no fact for is refused."
            )
        if self.zero_data_retention:
            routes = ", ".join(NO_ZERO_RETENTION)
            lines.append(
                f"Code refuses the routes on which the tuner cannot make sure"
                f" of zero retention"
                f" ({routes}). It cannot check the retention terms of another"
                " provider: those are your agreements with each provider."
            )
        if self.providers or self.zero_data_retention:
            lines.append(
                "The tuner's own agents read the train items through your agent"
                " CLI's account. No restriction covers that account. Nor does it"
                " cover a tool that sends data on, such as a Kiln task tool on"
                " its own run config, or a remote MCP tool."
            )
        if self.locked_tools:
            lines.append(
                "A locked tool stays attached, and no new code tool may call it."
                " Code does not check the text that the prompt or a skill gives"
                " about the tool."
            )
        return lines

    def route_refusal(self, provider: str) -> str | None:
        """Why the task's data may not go to `provider`, or None. The start
        asks this for the baseline, each judge and the simulated user: the
        run calls them whatever the model policy says."""
        if self.providers and provider not in self.providers:
            return f"only these providers are allowed: {', '.join(self.providers)}"
        if self.zero_data_retention and provider in NO_ZERO_RETENTION:
            return f"zero data retention only: {NO_ZERO_RETENTION[provider]}"
        return None

    def model_restrictions(self, catalog: Catalog) -> tuple[Restriction, ...]:
        """The restrictions on models as policy rules. The origin of a model
        comes from the model catalogue, so an origin restriction with no
        catalogue raises `RestrictionError`, and so does a family that the
        catalogue does not have."""
        found: list[Restriction] = []
        statements = iter(self.statements())
        if self.providers:
            found.append(
                Restriction(
                    name="providers",
                    statement=next(statements),
                    allow=tuple(
                        Rule(glob.escape(provider), "*") for provider in self.providers
                    ),
                    otherwise="its provider is not one of them",
                )
            )
        if self.on_origin and catalog.kiln.state != "ok":
            raise RestrictionError(
                "a restriction on the origin of the models needs the model"
                f" catalogue, which is not available ({catalog.kiln.reason})"
            )
        if self.model_weights and catalog.models_dev.state != "ok":
            # The open-weight fact comes from models.dev only: without it,
            # every model would be refused.
            raise RestrictionError(
                "a restriction on the weights of the models needs models.dev,"
                f" which is not available ({catalog.models_dev.reason})"
            )
        if self.model_weights:
            found.append(_weights(self.model_weights, next(statements), catalog))
        if self.exclude_families:
            found.append(_families(self.exclude_families, next(statements), catalog))
        if self.zero_data_retention:
            found.append(
                Restriction(
                    name="data_retention",
                    statement=next(statements),
                    allow=(Rule("*", "*"),),
                    deny=tuple(
                        Rule(provider, "*", reason)
                        for provider, reason in NO_ZERO_RETENTION.items()
                    ),
                )
            )
        return tuple(found)


def _model_rule(model_id: str, reason: str = "") -> Rule:
    """A rule for one model on every provider. The id is escaped: it is a
    name, not a pattern."""
    return Rule("*", glob.escape(model_id), reason)


def _weights(wanted: str, statement: str, catalog: Catalog) -> Restriction:
    if wanted not in WEIGHTS:
        raise RestrictionError(
            f"unknown model weights {wanted!r}: give one of {', '.join(WEIGHTS)}"
        )
    want_open = wanted == "open"
    other = "closed" if want_open else "open"
    allow: list[Rule] = []
    deny: list[Rule] = []
    for model in catalog.models:
        if model.open_weights is None:
            continue
        if model.open_weights == want_open:
            allow.append(_model_rule(model.model))
        else:
            deny.append(_model_rule(model.model, f"it is a {other}-weight model"))
    return Restriction(
        name="model_weights",
        statement=statement,
        allow=tuple(allow),
        deny=tuple(deny),
        otherwise="the model catalogue does not say if its weights are open",
    )


def catalog_families(catalog: Catalog) -> dict[str, int]:
    """The model families of the catalogue, each with how many models it
    has, in the catalogue's order."""
    families: dict[str, int] = {}
    for model in catalog.models:
        if model.family:
            family = model.family.lower()
            families[family] = families.get(family, 0) + 1
    return families


def _families(excluded: Sequence[str], statement: str, catalog: Catalog) -> Restriction:
    known = catalog_families(catalog)
    unknown = [family for family in excluded if family not in known]
    if unknown:
        raise RestrictionError(
            f"the model catalogue has no model family {', '.join(unknown)}."
            f" Its families: {', '.join(sorted(known))}"
        )
    allow: list[Rule] = []
    deny: list[Rule] = []
    for model in catalog.models:
        if not model.family:
            continue
        family = model.family.lower()
        if family in excluded:
            deny.append(_model_rule(model.model, f"it is of the family {family}"))
        else:
            allow.append(_model_rule(model.model))
    return Restriction(
        name="model_families",
        statement=statement,
        allow=tuple(allow),
        deny=tuple(deny),
        otherwise="the model catalogue does not give its family",
    )


# ---- the tool lock ----


@dataclass(frozen=True)
class ToolLock:
    """The tools that must not change, as the gateway and the loop check
    them. A candidate breaks the lock when it:

    - **drops or replaces** a locked tool: its run config does not list the
      tool id;
    - **wraps** a locked tool: it attaches a code tool that may call the
      locked tool (the code tool's `tool_allowlist` names it). A code tool
      that the baseline attached at the start is not a new wrapper.

    `broken` is why the lock could not be read: it then refuses every
    candidate, so a damaged file never unlocks a tool."""

    locked: tuple[str, ...] = ()
    baseline_tools: frozenset[str] = frozenset()
    broken: str | None = None

    def __bool__(self) -> bool:
        return bool(self.locked) or self.broken is not None

    def dropped(self, tool_ids: Iterable[str]) -> str | None:
        """Why a run config with these tools breaks the lock, or None."""
        if self.broken is not None:
            return f"the tool lock is unreadable ({self.broken})"
        listed = set(tool_ids)
        missing = [tool for tool in self.locked if tool not in listed]
        if not missing:
            return None
        return (
            f"the owner locked {_names(missing)}: the run config must keep"
            " it attached, with the same tool id. A candidate may not drop or"
            " replace a locked tool"
        )

    def new_code_tools(self, tool_ids: Iterable[str]) -> list[str]:
        """The ids of the code tools among `tool_ids` that the baseline did
        not attach: each one could be a new wrapper."""
        return [
            tool.removeprefix(CODE_TOOL_PREFIX)
            for tool in tool_ids
            if tool.startswith(CODE_TOOL_PREFIX) and tool not in self.baseline_tools
        ]

    def wraps(self, allowlist: Any) -> str | None:
        """Why a code tool with this `tool_allowlist` breaks the lock."""
        named = [t for t in allowlist or [] if isinstance(t, str)]
        called = [tool for tool in self.locked if tool in named]
        if not called:
            return None
        return (
            f"the owner locked {_names(called)}: a code tool may not call it"
            " (`tool_allowlist`). A candidate may not wrap a locked tool"
        )

    def to_json(self, statements: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "statements": list(statements),
            "locked_tools": list(self.locked),
            "baseline_tools": sorted(self.baseline_tools),
        }


def _names(tools: Sequence[str]) -> str:
    return ", ".join(tools)


def write_restrictions(
    run_dir: Path,
    restrictions: Restrictions,
    baseline_tools: Iterable[str] = (),
) -> ToolLock:
    """Write the run's `restrictions.json`, and return its tool lock."""
    lock = ToolLock(restrictions.locked_tools, frozenset(baseline_tools))
    (run_dir / RESTRICTIONS_FILE).write_text(
        json.dumps(lock.to_json(restrictions.statements()), indent=1) + "\n",
        encoding="utf-8",
    )
    return lock


def load_tool_lock(run_dir: Path) -> ToolLock:
    """The tool lock of the run's `restrictions.json`. No file or an empty
    file is no lock. A file that cannot be read gives a lock that refuses
    every candidate. It never raises."""
    path = run_dir / RESTRICTIONS_FILE
    try:
        if not path.exists():
            return ToolLock()
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return ToolLock()
        data = json.loads(text)
        if not isinstance(data, Mapping):
            raise ValueError("not a JSON object")
        locked = data.get("locked_tools") or []
        baseline = data.get("baseline_tools") or []
        if not isinstance(locked, list) or not isinstance(baseline, list):
            raise ValueError("'locked_tools' and 'baseline_tools' must be lists")
        return ToolLock(
            tuple(str(tool) for tool in locked),
            frozenset(str(tool) for tool in baseline),
        )
    except (OSError, ValueError) as exc:
        return ToolLock(broken=f"{path}: {exc}")
