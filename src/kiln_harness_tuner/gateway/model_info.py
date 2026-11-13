"""The answers of the model operations: `list_models`, `get_model_details`
and `list_providers`.

Pure functions. Each one joins three things: the run's model catalogue
snapshot (`model_catalog`), what the Kiln server offers now (one
`GET /api/available_models` payload, `Offered`), and the run's model
policy. The server decides what is offered. The policy decides what is
allowed. The catalogue adds price, limits, dates, notes and tags.

A (provider, model) pair has one of four statuses (`m.ModelStatus`). The
status is information. `create_run_config` refuses a pair that the policy
refuses, and nothing more: a pair that the server does not offer can become
a run config, and fails when it runs. So agents are told to use `ready`
pairs only.

For a pair that the server offers, the fields that code checks against the
server (`SERVER_FIELDS`) are the server's, not the published file's: the
installed Kiln can run with another version of the remote config.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from kiln_harness_tuner.gateway import models as m
from kiln_harness_tuner.gateway.errors import GatewayError
from kiln_harness_tuner.model_catalog import Catalog, CatalogEntry, CatalogModel
from kiln_harness_tuner.model_catalog.build import SUGGESTED_FOR
from kiln_harness_tuner.model_policy import ModelPolicy

Status = m.ModelStatus
Pair = tuple[str, str]  # (Kiln provider id, Kiln model id)

# The entry fields that come from the server for an offered pair, even when
# the catalogue has the pair. The probes and the model preflight check the
# thinking levels and the output mode against the server's model list.
SERVER_FIELDS = ("thinking_levels", "default_thinking_level", "structured_output_mode")


@dataclass(frozen=True)
class Offered:
    """What the Kiln server offers: from one /api/available_models payload."""

    pairs: dict[Pair, dict[str, Any]]  # (provider, model) -> the server's model dict
    connected: frozenset[str]  # provider ids in the payload
    order: list[Pair]  # the server's order

    @classmethod
    def from_payload(cls, payload: Any) -> Offered:
        pairs: dict[Pair, dict[str, Any]] = {}
        connected: set[str] = set()
        for provider_entry in payload if isinstance(payload, list) else []:
            if not isinstance(provider_entry, dict):
                continue
            provider = provider_entry.get("provider_id")
            if not isinstance(provider, str) or not provider:
                continue
            connected.add(provider)
            for model in provider_entry.get("models") or []:
                if isinstance(model, dict) and isinstance(model.get("id"), str):
                    pairs.setdefault((provider, model["id"]), model)
        return cls(pairs=pairs, connected=frozenset(connected), order=list(pairs))


def status(
    offered: Offered, policy: ModelPolicy | None, provider: str, model: str
) -> Status:
    if (provider, model) in offered.pairs:
        if policy is None or policy.decide(provider, model).allowed:
            return "ready"
        return "blocked_by_policy"
    if provider in offered.connected:
        return "not_offered"
    return "not_connected"


# ---- the merged view: the catalogue, and what only the server knows


def _flag(d: dict[str, Any], key: str) -> bool | None:
    value = d.get(key)
    return value if isinstance(value, bool) else None


def _text(d: dict[str, Any], key: str) -> str | None:
    value = d.get(key)
    return value if isinstance(value, str) and value else None


def _server_fields(d: dict[str, Any]) -> dict[str, Any]:
    """The `SERVER_FIELDS` of one server model dict."""
    levels = d.get("available_thinking_levels")
    return {
        # The server's order, lowest first, as `probes.thinking_levels` reads it.
        "thinking_levels": [str(v) for v in levels.values()]
        if isinstance(levels, dict)
        else [],
        "default_thinking_level": _text(d, "default_thinking_level"),
        "structured_output_mode": _text(d, "structured_output_mode"),
    }


def _entry_from_server(provider: str, d: dict[str, Any]) -> CatalogEntry:
    """A catalogue entry for a pair the catalogue does not have (a
    fine-tune, a custom model, a model newer than the snapshot). The
    models.dev fields stay null."""
    return CatalogEntry(
        provider=provider,
        deprecated=d.get("deprecated") is True,
        untested=d.get("untested_model") is True,
        supports_tools=_flag(d, "supports_function_calling"),
        supports_structured_output=_flag(d, "supports_structured_output"),
        supports_vision=_flag(d, "supports_vision"),
        supports_doc_extraction=_flag(d, "supports_doc_extraction"),
        suggested_for=[k for k in SUGGESTED_FOR if d.get(f"suggested_for_{k}") is True],
        **_server_fields(d),
    )


def _merged(catalog: Catalog, offered: Offered) -> list[CatalogModel]:
    """The catalogue's models in its order, each with an entry added for each
    offered pair the catalogue lacks; then the models only the server
    offers, by model id. An entry of an offered pair takes the server's
    `SERVER_FIELDS`. The catalogue itself is not changed."""
    rows = [row.model_copy(deep=True) for row in catalog.models]
    by_id = {row.model: row for row in rows}
    known = {(e.provider, row.model): e for row in rows for e in row.entries}
    server_only: dict[str, list[Pair]] = {}
    for pair in offered.order:
        provider, model = pair
        if pair in known:
            for name, value in _server_fields(offered.pairs[pair]).items():
                setattr(known[pair], name, value)
            continue
        if model in by_id:
            by_id[model].entries.append(
                _entry_from_server(provider, offered.pairs[pair])
            )
        else:
            server_only.setdefault(model, []).append(pair)
    for model in sorted(server_only):
        pairs = server_only[model]
        first = offered.pairs[pairs[0]]
        rows.append(
            CatalogModel(
                model=model,
                name=_text(first, "name") or model,
                entries=[
                    _entry_from_server(p, offered.pairs[(p, model)]) for p, _ in pairs
                ],
            )
        )
    return rows


def _catalog_state(catalog: Catalog) -> m.CatalogState:
    return m.CatalogState(
        kiln=catalog.kiln.state,
        models_dev=catalog.models_dev.state,
        fetched_at=catalog.fetched_at,
    )


def _thinking(entries: list[CatalogEntry]) -> bool:
    return any(_reasons(e) for e in entries)


def _reasons(entry: CatalogEntry) -> bool:
    return bool(entry.thinking_levels) or entry.reasoning_capable is True


# ---- list_models


def _keeps(entry: CatalogEntry, entry_status: Status, req: m.ListModelsArgs) -> bool:
    """The entry filters (FS 5.1). An entry with a null value for a filter
    it is tested on does not pass."""
    if entry_status != "ready" and not req.include_not_available:
        return False
    if entry.deprecated and not req.include_deprecated:
        return False
    if req.provider is not None and entry.provider != req.provider:
        return False
    for wanted, actual in (
        (req.tools, entry.supports_tools),
        (req.vision, entry.supports_vision),
        (req.reasoning, _reasons(entry)),
    ):
        if wanted is not None and actual is not wanted:
            return False
    if req.max_input_price is not None:
        price = entry.input_price
        if price is None or price > req.max_input_price:
            return False
    return True


def _row_keeps(
    row: CatalogModel, entries: list[CatalogEntry], req: m.ListModelsArgs
) -> bool:
    if not entries:
        return False
    if req.family is not None and row.family != req.family:
        return False
    if req.open_weights is not None and row.open_weights is not req.open_weights:
        return False
    if req.tags:
        tags = set(row.tags).union(*(e.tags for e in entries))
        if not tags & set(req.tags):
            return False
    return True


def _price_key(entries: list[CatalogEntry]) -> float:
    prices = [
        e.input_price + e.output_price
        for e in entries
        if e.input_price is not None and e.output_price is not None
    ]
    return min(prices, default=math.inf)


def _newest_key(row: CatalogModel) -> tuple[bool, int]:
    try:
        ordinal = date.fromisoformat(row.release_date or "").toordinal()
    except ValueError:
        return (True, 0)
    return (False, -ordinal)


def _featured_key(row: CatalogModel) -> tuple[bool, int]:
    return (row.featured_rank is None, row.featured_rank or 0)


def list_models(
    catalog: Catalog,
    offered: Offered,
    policy: ModelPolicy | None,
    req: m.ListModelsArgs,
) -> m.ModelList:
    kept: list[tuple[CatalogModel, list[tuple[CatalogEntry, Status]]]] = []
    for row in _merged(catalog, offered):
        entries = [
            (e, s)
            for e in row.entries
            if _keeps(e, s := status(offered, policy, e.provider, row.model), req)
        ]
        if _row_keeps(row, [e for e, _ in entries], req):
            kept.append((row, entries))
    # The merged rows are in the base order of `featured` (Kiln's file, then
    # the server-only models by id), and the sort is stable, so each key
    # keeps the base order for its ties.
    kept.sort(key=lambda r: _featured_key(r[0]))
    if req.sort == "price":
        kept.sort(key=lambda r: _price_key([e for e, _ in r[1]]))
    elif req.sort == "newest":
        kept.sort(key=lambda r: _newest_key(r[0]))
    page = kept[req.offset : req.offset + req.limit]
    return m.ModelList(
        catalog=_catalog_state(catalog),
        total=len(kept),
        models=[_model_row(row, entries) for row, entries in page],
    )


def _model_row(
    row: CatalogModel, entries: list[tuple[CatalogEntry, Status]]
) -> m.ModelRow:
    return m.ModelRow(
        model=row.model,
        name=row.name,
        family=row.family,
        featured_rank=row.featured_rank,
        tags=row.tags,
        release_date=row.release_date,
        context_window=row.context_window,
        open_weights=row.open_weights,
        thinking=_thinking([e for e, _ in entries]),
        providers=[
            m.ProviderEntryRow(
                provider=e.provider,
                status=s,
                input_price=e.input_price,
                output_price=e.output_price,
                tags=e.tags,
                deprecated=e.deprecated,
            )
            for e, s in entries
        ],
    )


# ---- get_model_details

# The fields a details answer copies from the merged row and its entries.
_DETAIL_FIELDS = set(m.ModelDetailsView.model_fields) - {
    "catalog",
    "thinking",
    "providers",
}
_ENTRY_FIELDS = set(m.ProviderEntryDetails.model_fields) - {"status"}


def model_details(
    catalog: Catalog,
    offered: Offered,
    policy: ModelPolicy | None,
    req: m.ModelDetailsArgs,
) -> m.ModelDetailsView:
    row = next((r for r in _merged(catalog, offered) if r.model == req.model), None)
    if row is None:
        raise GatewayError(
            f"Model not found: {req.model}. Use list_models to find models."
        )
    entries = row.entries
    if req.provider is not None:
        entries = [e for e in entries if e.provider == req.provider]
        if not entries:
            raise GatewayError(f"{req.model} has no entry for provider {req.provider}.")
    return m.ModelDetailsView(
        catalog=_catalog_state(catalog),
        **row.model_dump(include=_DETAIL_FIELDS),
        thinking=_thinking(entries),
        providers=[
            m.ProviderEntryDetails(
                **e.model_dump(include=_ENTRY_FIELDS),
                status=status(offered, policy, e.provider, row.model),
            )
            for e in entries
        ],
    )


# ---- list_providers


def list_providers(
    catalog: Catalog,
    offered: Offered,
    policy: ModelPolicy | None,
    req: m.ListProvidersArgs,
) -> m.ProviderList:
    ids = list(
        dict.fromkeys(
            [
                *(p.provider for p in catalog.providers),
                *(e.provider for row in catalog.models for e in row.entries),
                *sorted(offered.connected),
            ]
        )
    )
    ready: dict[str, int] = {}
    for provider, model in offered.pairs:
        if status(offered, policy, provider, model) == "ready":
            ready[provider] = ready.get(provider, 0) + 1
    rows = []
    for provider in ids:
        connected = provider in offered.connected
        if not connected and not req.include_not_available:
            continue
        info = catalog.provider(provider)
        rows.append(
            m.ProviderRow(
                provider=provider,
                name=info.name if info else None,
                status="connected" if connected else "not_connected",
                ready_models=ready.get(provider, 0),
                tags=info.tags if info else [],
                notes=info.notes if info else None,
            )
        )
    rows.sort(key=lambda r: (-r.ready_models, r.provider))
    return m.ProviderList(catalog=_catalog_state(catalog), providers=rows)
