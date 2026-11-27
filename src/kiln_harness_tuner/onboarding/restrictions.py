"""The Restrictions question of the guided start, and the start's check of
the routes the run calls whatever the model policy says.

Some limits are known only because the owner states them: the providers,
the origin of the models, the data retention, the tools that must not
change. The question asks which of them apply, then asks for each one that
does. Each answer is a flag (`kiln_harness_tuner.restrictions` has the table
of where code enforces it), so a start with no terminal states the same
restrictions with flags, and the rerun command repeats them.

The questions say what code cannot check, and so does the summary: a
restriction that code cannot enforce is never shown as enforced.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from kiln_harness_tuner.model_catalog import Catalog
from kiln_harness_tuner.onboarding.errors import GoBack
from kiln_harness_tuner.onboarding.keys import Need, provider_name
from kiln_harness_tuner.onboarding.models import offered_models
from kiln_harness_tuner.onboarding.terminal import (
    Option,
    Problem,
    Terminal,
    plural,
)
from kiln_harness_tuner.restrictions import (
    NO_ZERO_RETENTION,
    Restrictions,
    catalog_families,
)

NONE, SET = "none", "set"
PROVIDERS, WEIGHTS, FAMILIES, RETENTION, TOOLS = (
    "providers",
    "weights",
    "families",
    "retention",
    "tools",
)

RESTRICTIONS_QUESTION = "Do you have restrictions for this run?"
RESTRICTIONS_HINT = (
    "A restriction is a hard limit that code enforces: the providers, the"
    " origin of the models, the data retention, the tools that must not"
    " change. State it here, not in the guidance: code has no check for a"
    " limit that only the guidance gives."
)
KINDS_QUESTION = "Which restrictions do you have?"
KINDS_HINT = "↑↓ move · Space mark · Enter done. Each marked one has a question."
PROVIDERS_QUESTION = "Which providers may the task's data go to?"
PROVIDERS_HINT = (
    "↑↓ move · Space mark · Enter done\n"
    "Code refuses a run config on an unmarked provider. The start also"
    " refuses a baseline, a judge or a simulated user on one.\n"
    "The tuner's own agents read the train items through your agent CLI's"
    " account. No restriction covers that account."
)
WEIGHTS_QUESTION = "Which models may the tuner use, by their weights?"
WEIGHTS_HINT = (
    "From the model catalogue. A model that the catalogue has no fact for is refused."
)
FAMILIES_QUESTION = "Which model families must the tuner not use?"
FAMILIES_HINT = (
    "↑↓ move · type to filter · Space mark · Enter done\n"
    "The model catalogue has no country or company of origin. For such a"
    " restriction, mark each family that it covers. A model with no family in"
    " the catalogue is refused."
)
TOOLS_QUESTION = "Which tools must not change?"
TOOLS_HINT = (
    "↑↓ move · Space mark · Enter done\n"
    "Code refuses a run config that drops, replaces or wraps a marked tool:"
    " the tool stays attached, and no new code tool may call it."
)
NO_PROVIDERS = (
    "Kiln offers no models, so there is no provider to choose from: add a"
    " provider key in Kiln's settings."
)
NO_TOOLS = "the task's run config has no tools"
NO_CATALOG = "the model catalogue is not available"
ROUTE_TITLE = "The run calls a provider that your restrictions refuse"


def restrictions_text(restrictions: Restrictions) -> str:
    """The restrictions as the step line and the summary read them."""
    return " · ".join(restrictions.statements()) or "none"


def limits_text(restrictions: Restrictions) -> str:
    """What code cannot check, as one summary line."""
    return " ".join(restrictions.limits())


def _kinds(restrictions: Restrictions) -> list[str]:
    """The kinds of restriction that are stated."""
    stated = {
        PROVIDERS: restrictions.providers,
        WEIGHTS: restrictions.model_weights,
        FAMILIES: restrictions.exclude_families,
        RETENTION: restrictions.zero_data_retention,
        TOOLS: restrictions.locked_tools,
    }
    return [kind for kind, value in stated.items() if value]


def _kind_options(*, families: bool, weights: bool, tools: bool) -> list[Option[str]]:
    """One row for each kind that this start can ask about. The families
    come from Kiln's model file, the weights from models.dev, and a tool
    lock needs a tool."""
    routes = ", ".join(provider_name(p) for p in NO_ZERO_RETENTION)
    options = [
        Option(
            PROVIDERS,
            "Providers",
            ("only the providers you mark",),
            description="The task's data goes to no other provider.",
        )
    ]
    if weights:
        options.append(
            Option(
                WEIGHTS,
                "Model origin: weights",
                ("open-weight or closed-weight models only",),
                description=WEIGHTS_HINT,
            )
        )
    if families:
        options.append(
            Option(
                FAMILIES,
                "Model origin: families",
                ("no model of the families you mark",),
                description="Use it for a restriction by country or company: the"
                " model catalogue has neither, so you mark the families.",
            )
        )
    options.append(
        Option(
            RETENTION,
            "Zero data retention",
            (f"refuses {routes}",),
            description=f"Code refuses {routes}: the tuner does not yet set or"
            " check its zero data retention setting on a run config. It cannot"
            " check the"
            " retention terms of another provider: mark Providers too, and"
            " mark only the providers whose terms you checked.",
        )
    )
    if tools:
        options.append(
            Option(
                TOOLS,
                "Tools that must not change",
                ("the tools you mark stay as they are",),
                description="Code refuses a run config that drops, replaces or"
                " wraps a marked tool.",
            )
        )
    return options


def ask_restrictions(
    terminal: Terminal,
    current: Restrictions,
    *,
    offered: Callable[[], Any] | None,
    catalog: Callable[[], Catalog] | None,
    baseline_provider: str | None,
    baseline_tools: Sequence[str],
    back: bool,
) -> Restrictions:
    """Ask whether the run has restrictions, which kinds, and then each
    kind's own question. Returns the restrictions stated. Esc at a question
    asks the one before it again; at the first one, with `back`, it raises
    `GoBack`.

    `offered` reads Kiln's `GET /api/available_models` payload, whose
    providers the Providers question lists. `catalog` reads the model
    catalogue, whose families the origin questions use. Each is read only
    when its question is asked."""
    answer = current
    stage = 0
    kinds: list[str] = _kinds(current)
    loaded: Catalog | None = None
    warned = False  # the warnings of the kinds that cannot be asked, once
    while True:
        try:
            if stage == 0:
                choice = terminal.pick(
                    RESTRICTIONS_QUESTION,
                    [
                        Option(NONE, "No restrictions"),
                        Option(
                            SET,
                            "Set restrictions…",
                            description="Providers, the origin of the models,"
                            " zero data retention, tools that must not change.",
                        ),
                    ],
                    hint=RESTRICTIONS_HINT,
                    default=SET if current else NONE,
                    back=back,
                )
                if choice == NONE:
                    return Restrictions()
                stage = 1
            elif stage == 1:
                if loaded is None and catalog is not None:
                    with terminal.busy("Reading the model catalogue…"):
                        loaded = catalog()
                families = loaded is not None and bool(catalog_families(loaded))
                weights = (
                    families and loaded is not None and loaded.models_dev.state == "ok"
                )
                if not warned:
                    warned = True
                    if not families:
                        terminal.warn(
                            f"No question about the origin of the models: {NO_CATALOG}."
                        )
                    elif not weights:
                        terminal.warn(
                            "No question about the weights of the models:"
                            " models.dev is not available."
                        )
                    if not baseline_tools:
                        terminal.warn(f"No question about the tools: {NO_TOOLS}.")
                options = _kind_options(
                    families=families, weights=weights, tools=bool(baseline_tools)
                )
                values = [option.value for option in options]
                kinds = terminal.pick_many(
                    KINDS_QUESTION,
                    options,
                    hint=KINDS_HINT,
                    default=[kind for kind in kinds if kind in values],
                    back=True,
                )
                answer = replace(answer, zero_data_retention=RETENTION in kinds)
                stage = 2
            else:
                asked = [kind for kind in kinds if kind != RETENTION]
                if stage - 2 >= len(asked):
                    return _only(answer, kinds)
                answer = _ask_kind(
                    asked[stage - 2],
                    terminal,
                    answer,
                    offered=offered,
                    catalog=loaded,
                    baseline_provider=baseline_provider,
                    baseline_tools=baseline_tools,
                )
                stage += 1
        except GoBack:
            if stage == 0:
                raise
            stage -= 1


def _only(answer: Restrictions, kinds: Sequence[str]) -> Restrictions:
    """`answer`, with each kind that is not marked taken away."""
    return Restrictions(
        providers=answer.providers if PROVIDERS in kinds else (),
        model_weights=answer.model_weights if WEIGHTS in kinds else None,
        exclude_families=answer.exclude_families if FAMILIES in kinds else (),
        zero_data_retention=RETENTION in kinds,
        locked_tools=answer.locked_tools if TOOLS in kinds else (),
    )


def _ask_kind(
    kind: str,
    terminal: Terminal,
    answer: Restrictions,
    *,
    offered: Callable[[], Any] | None,
    catalog: Catalog | None,
    baseline_provider: str | None,
    baseline_tools: Sequence[str],
) -> Restrictions:
    if kind == PROVIDERS:
        return replace(
            answer,
            providers=_ask_providers(terminal, answer, offered, baseline_provider),
        )
    if kind == WEIGHTS:
        weights = terminal.pick(
            WEIGHTS_QUESTION,
            [
                Option("open", "Open-weight models only"),
                Option("closed", "Closed-weight models only"),
            ],
            hint=WEIGHTS_HINT,
            default=answer.model_weights,
            back=True,
        )
        return replace(answer, model_weights=weights)
    if kind == FAMILIES:
        families = catalog_families(catalog) if catalog is not None else {}
        picked = terminal.pick_many(
            FAMILIES_QUESTION,
            [
                Option(family, family, (plural(count, "model"),))
                for family, count in families.items()
            ],
            hint=FAMILIES_HINT,
            default=[f for f in answer.exclude_families if f in families],
            back=True,
        )
        return replace(answer, exclude_families=tuple(picked))
    tools = terminal.pick_many(
        TOOLS_QUESTION,
        [Option(tool, tool) for tool in baseline_tools],
        hint=TOOLS_HINT,
        default=[tool for tool in answer.locked_tools if tool in baseline_tools],
        back=True,
    )
    return replace(answer, locked_tools=tuple(tools))


def _ask_providers(
    terminal: Terminal,
    answer: Restrictions,
    offered: Callable[[], Any] | None,
    baseline_provider: str | None,
) -> tuple[str, ...]:
    """The providers the user marks, from the ones Kiln has a key for. The
    baseline's provider starts marked."""
    with terminal.busy("Reading the providers Kiln offers…"):
        models = offered_models(offered() if offered is not None else [])
    providers = dict.fromkeys((m.provider, m.provider_label) for m in models)
    if not providers:
        terminal.warn(NO_PROVIDERS)
        raise GoBack()
    ids = [provider for provider, _label in providers]
    default = [provider for provider in answer.providers if provider in ids]
    if not default and baseline_provider in ids:
        default = [baseline_provider]
    picked = terminal.pick_many(
        PROVIDERS_QUESTION,
        [Option(provider, label, (provider,)) for provider, label in providers],
        hint=PROVIDERS_HINT,
        default=default,
        back=True,
    )
    return tuple(picked)


# ---- the routes the run calls whatever the policy says ----


def route_problem(needs: Sequence[Need], restrictions: Restrictions) -> Problem | None:
    """The providers the run calls that a restriction on providers or on
    data retention refuses, as one problem, or None. The run calls the
    baseline's provider, each judge's and the simulated user's whatever the
    model policy says, so the start refuses them here. A provider that only
    the model policy names is not called yet: the policy refuses it."""
    refused: dict[str, tuple[str, list[str]]] = {}
    for need in needs:
        if need.from_policy:
            continue
        why = restrictions.route_refusal(need.provider)
        if why is not None:
            refused.setdefault(need.provider, (why, []))[1].append(need.used_by)
    if not refused:
        return None
    return Problem(
        ROUTE_TITLE,
        (
            "The run sends the task's data to each of these, whatever model"
            " the tuner tries:",
            *(
                f"  • {provider_name(provider)}: used by"
                f" {', '.join(dict.fromkeys(used_by))}. Refused: {why}."
                for provider, (why, used_by) in refused.items()
            ),
            "Change it in Kiln (the run config, or the judge of the eval), then"
            " check again. To change the restriction, exit and start again.",
        ),
        retry_label="Check again (I've changed it in Kiln)",
    )
