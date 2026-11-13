"""The catalogue build: a tolerant parse of Kiln's published model file, and
the join of models.dev onto it. Pure: no I/O.

The parse validates each model and each provider entry alone. One that is
not valid is dropped, and the rest are kept. A field the tuner does not know
is ignored. So a newer file with more fields, or one bad entry, does not lose
the rest.

The join follows the Kiln server's model info tool (`kiln_server`,
`api/kiln_fastapi_api/tools/model_info/model_builder.py`): price and limits
for each (provider, model) pair, and the model facts from the first matched
entry in a fixed provider order. When no entry gives a release date, the date
is looked up across all of models.dev by a loose key.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from kiln_harness_tuner.model_catalog.providers import PREFERENCE, models_dev_provider
from kiln_harness_tuner.model_catalog.schema import (
    Catalog,
    CatalogEntry,
    CatalogModel,
    CatalogProvider,
    Source,
)

NOTE_LIMIT = 2000
CUT_MARK = " [cut]"
MAX_TAGS = 12
_TAG_RE = re.compile(r"[a-z0-9_]{1,40}")
# A short key such as `o3` matches too much.
MIN_LOOSE_KEY = 4
NO_MODEL_LIST = "Kiln's file has no model_list"
MODELS_DEV_NOT_AN_OBJECT = "models.dev's file is not an object"
SUGGESTED_FOR = ("evals", "data_gen", "doc_extraction", "synthetic_user")


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _RawProvider(_Raw):
    name: str
    model_id: str | None = None
    deprecated: bool = False
    untested_model: bool = False
    supports_function_calling: bool | None = None
    supports_structured_output: bool | None = None
    supports_vision: bool | None = None
    supports_doc_extraction: bool | None = None
    reasoning_capable: bool | None = None
    available_thinking_levels: dict[str, str] | None = None
    default_thinking_level: str | None = None
    structured_output_mode: str | None = None
    suggested_for_evals: bool = False
    suggested_for_data_gen: bool = False
    suggested_for_doc_extraction: bool = False
    suggested_for_synthetic_user: bool = False
    tags: Any = None  # cleaned by `_tags`, so a bad value drops no entry


class _RawModel(_Raw):
    name: str
    friendly_name: str | None = None
    family: str | None = None
    featured_rank: int | None = None
    editorial_notes: Any = None
    agent_notes: Any = None
    tags: Any = None
    providers: list[Any] = []


class _RawProviderInfo(_Raw):
    id: str
    friendly_name: str | None = None
    notes: Any = None
    tags: Any = None
    models_dev_id: str | None = None


def build_catalog(
    kiln_raw: Any,
    models_dev_raw: Any,
    *,
    fetched_at: str,
    kiln: Source,
    models_dev: Source,
) -> Catalog:
    """The catalogue from the parsed JSON of Kiln's file and of models.dev.
    Either may be None (its fetch failed)."""
    if not isinstance(models_dev_raw, Mapping):
        if models_dev.state == "ok":
            models_dev = Source(state="none", reason=MODELS_DEV_NOT_AN_OBJECT)
        models_dev_raw = {}
    model_list = kiln_raw.get("model_list") if isinstance(kiln_raw, Mapping) else None
    if not isinstance(model_list, list):
        if kiln.state == "ok":
            kiln = Source(state="none", reason=NO_MODEL_LIST)
        return Catalog(fetched_at=fetched_at, kiln=kiln, models_dev=models_dev)

    providers = _provider_list(kiln_raw.get("provider_list"))
    published = {p.provider: p.models_dev_id for p in providers}
    loose_dates = _loose_index(models_dev_raw)
    models: list[CatalogModel] = []
    seen: set[str] = set()
    for item in model_list:
        model = _model(item)
        if model is None or model.model in seen:
            continue
        seen.add(model.model)
        _join(model, models_dev_raw, published, loose_dates)
        models.append(model)
    return Catalog(
        fetched_at=fetched_at,
        kiln=kiln,
        models_dev=models_dev,
        models=models,
        providers=providers,
    )


# ---- the Kiln parse ----


def _tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    tags: list[str] = []
    for value in values:
        if isinstance(value, str) and _TAG_RE.fullmatch(value) and value not in tags:
            tags.append(value)
            if len(tags) == MAX_TAGS:
                break
    return tags


def _note(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    if len(text) <= NOTE_LIMIT:
        return text
    return text[: NOTE_LIMIT - len(CUT_MARK)].rstrip() + CUT_MARK


def _provider_list(raw: Any) -> list[CatalogProvider]:
    if not isinstance(raw, list):
        return []
    providers: list[CatalogProvider] = []
    seen: set[str] = set()
    for item in raw:
        try:
            info = _RawProviderInfo.model_validate(item)
        except ValidationError:
            continue
        if info.id in seen:
            continue
        seen.add(info.id)
        providers.append(
            CatalogProvider(
                provider=info.id,
                name=info.friendly_name,
                notes=_note(info.notes),
                tags=_tags(info.tags),
                models_dev_id=info.models_dev_id,
            )
        )
    return providers


def _model(item: Any) -> CatalogModel | None:
    try:
        raw = _RawModel.model_validate(item)
    except ValidationError:
        return None
    return CatalogModel(
        model=raw.name,
        name=raw.friendly_name or raw.name,
        family=raw.family,
        featured_rank=raw.featured_rank,
        editorial_notes=_note(raw.editorial_notes),
        agent_notes=_note(raw.agent_notes),
        tags=_tags(raw.tags),
        entries=[e for e in map(_entry, raw.providers) if e is not None],
    )


def _entry(item: Any) -> CatalogEntry | None:
    try:
        raw = _RawProvider.model_validate(item)
    except ValidationError:
        return None
    return CatalogEntry(
        provider=raw.name,
        model_id=raw.model_id,
        deprecated=raw.deprecated,
        untested=raw.untested_model,
        supports_tools=raw.supports_function_calling,
        supports_structured_output=raw.supports_structured_output,
        supports_vision=raw.supports_vision,
        supports_doc_extraction=raw.supports_doc_extraction,
        reasoning_capable=raw.reasoning_capable,
        thinking_levels=list((raw.available_thinking_levels or {}).values()),
        default_thinking_level=raw.default_thinking_level,
        structured_output_mode=raw.structured_output_mode,
        suggested_for=[
            use for use in SUGGESTED_FOR if getattr(raw, f"suggested_for_{use}")
        ],
        tags=_tags(raw.tags),
    )


# ---- the models.dev join ----


def _price(value: Any) -> float | None:
    """Port of the Kiln server's `_is_valid_price`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        price = float(value)
    except OverflowError:
        return None
    if not math.isfinite(price) or price < 0:
        return None
    return price


def _limit(value: Any) -> int | None:
    """Port of the Kiln server's `_extract_limits` check. A bool is not an int
    here."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _models_dev_entry(
    models_dev_raw: Mapping[str, Any], md_provider: str | None, model_id: str | None
) -> Mapping[str, Any] | None:
    if md_provider is None or model_id is None:
        return None
    models = _mapping(_mapping(models_dev_raw.get(md_provider)).get("models"))
    entry = models.get(model_id)
    return entry if isinstance(entry, Mapping) else None


def _preference_rank(entry: CatalogEntry) -> tuple[int, str]:
    """`PREFERENCE` first, then the other providers by id."""
    if entry.provider in PREFERENCE:
        return PREFERENCE.index(entry.provider), ""
    return len(PREFERENCE), entry.provider


def _join(
    model: CatalogModel,
    models_dev_raw: Mapping[str, Any],
    published: Mapping[str, str | None],
    loose_dates: Mapping[str, str],
) -> None:
    matched: list[tuple[CatalogEntry, Mapping[str, Any]]] = []
    for entry in model.entries:
        md_provider = models_dev_provider(entry.provider, published.get(entry.provider))
        md = _models_dev_entry(models_dev_raw, md_provider, entry.model_id)
        if md is None:
            continue
        matched.append((entry, md))
        cost = _mapping(md.get("cost"))
        limit = _mapping(md.get("limit"))
        entry.input_price = _price(cost.get("input"))
        entry.output_price = _price(cost.get("output"))
        entry.cache_read_price = _price(cost.get("cache_read"))
        entry.cache_write_price = _price(cost.get("cache_write"))
        entry.context_window = _limit(limit.get("context"))
        entry.max_output_tokens = _limit(limit.get("output"))
        temperature = md.get("temperature")
        entry.takes_temperature = temperature if isinstance(temperature, bool) else None

    if matched:
        _, first = min(matched, key=lambda pair: _preference_rank(pair[0]))
        limit = _mapping(first.get("limit"))
        model.release_date = _iso_date(first.get("release_date"))
        knowledge = first.get("knowledge")
        model.knowledge_cutoff = knowledge if isinstance(knowledge, str) else None
        open_weights = first.get("open_weights")
        model.open_weights = open_weights if isinstance(open_weights, bool) else None
        model.context_window = _limit(limit.get("context"))
        model.max_output_tokens = _limit(limit.get("output"))
    if model.release_date is not None:
        model.release_date_match = "exact"
        return
    loose = _loose_date(model, loose_dates)
    if loose is not None:
        model.release_date = loose
        model.release_date_match = "loose"


# ---- the loose release date ----


def _key(model_id: str) -> str:
    tail = model_id.split("/")[-1].split(":")[0].lower()
    return re.sub(r"[^a-z0-9]", "", tail)


def _loose_index(models_dev_raw: Mapping[str, Any]) -> dict[str, str]:
    """Loose key -> the earliest valid release date among the models.dev
    entries with that key. An early date is the first release; a later date
    on a key is usually a provider adding the model later."""
    index: dict[str, str] = {}
    for provider in models_dev_raw.values():
        for model_id, md in _mapping(_mapping(provider).get("models")).items():
            if not isinstance(md, Mapping):
                continue
            released = _iso_date(md.get("release_date"))
            if released is None:
                continue
            ids = [model_id, md.get("canonical_model_id")]
            for key in {_key(i) for i in ids if isinstance(i, str)}:
                if len(key) < MIN_LOOSE_KEY:
                    continue
                if key not in index or released < index[key]:
                    index[key] = released
    return index


def _loose_date(model: CatalogModel, loose_dates: Mapping[str, str]) -> str | None:
    # Deprecated entries count: an old id is still evidence of the release.
    dates = [
        loose_dates[key]
        for entry in model.entries
        if entry.model_id is not None
        and len(key := _key(entry.model_id)) >= MIN_LOOSE_KEY
        and key in loose_dates
    ]
    return min(dates, default=None)
