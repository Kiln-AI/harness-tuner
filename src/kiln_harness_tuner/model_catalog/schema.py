"""The model catalogue's snapshot models: Kiln's published model file, with
the models.dev facts joined on (`build.py`). One snapshot is written for each
run (`store.py`). Every model ignores fields it does not know, so an older
tuner reads a newer snapshot."""

from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import BaseModel, ConfigDict

SourceState = Literal["ok", "none"]


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Source(_Snapshot):
    state: SourceState = "none"
    reason: str = ""  # empty when ok


class CatalogEntry(_Snapshot):
    """One provider entry of one model."""

    provider: str  # Kiln's provider id
    model_id: str | None = None  # the provider's own id for the model
    deprecated: bool = False
    untested: bool = False
    supports_tools: bool | None = None
    supports_structured_output: bool | None = None
    supports_vision: bool | None = None
    supports_doc_extraction: bool | None = None
    reasoning_capable: bool | None = None
    # The values of Kiln's `available_thinking_levels`, in Kiln's order.
    thinking_levels: list[str] = []
    default_thinking_level: str | None = None
    structured_output_mode: str | None = None
    # "evals", "data_gen", "doc_extraction", "synthetic_user"
    suggested_for: list[str] = []
    tags: list[str] = []
    # From models.dev. Null when this entry has no exact match.
    input_price: float | None = None  # USD per million tokens
    output_price: float | None = None
    cache_read_price: float | None = None
    cache_write_price: float | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    takes_temperature: bool | None = None


class CatalogModel(_Snapshot):
    model: str  # Kiln's model id (KilnModel.name)
    name: str  # friendly_name
    family: str | None = None
    featured_rank: int | None = None
    editorial_notes: str | None = None
    agent_notes: str | None = None
    tags: list[str] = []
    entries: list[CatalogEntry] = []
    # The model facts from models.dev: the first matched entry, except the
    # release date, which has a loose fallback (`release_date_match`).
    release_date: str | None = None  # ISO date "YYYY-MM-DD"
    release_date_match: Literal["exact", "loose"] | None = None
    knowledge_cutoff: str | None = None
    open_weights: bool | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None


class CatalogProvider(_Snapshot):
    provider: str
    name: str | None = None
    notes: str | None = None
    tags: list[str] = []
    models_dev_id: str | None = None


class Catalog(_Snapshot):
    fetched_at: str | None = None  # ISO time, UTC
    kiln: Source = Source()
    models_dev: Source = Source()
    models: list[CatalogModel] = []  # in the order of Kiln's file
    providers: list[CatalogProvider] = []

    @classmethod
    def unavailable(cls, reason: str) -> Catalog:
        return cls(
            kiln=Source(state="none", reason=reason),
            models_dev=Source(state="none", reason=reason),
        )

    @cached_property
    def _models_by_id(self) -> dict[str, CatalogModel]:
        return {m.model: m for m in self.models}

    @cached_property
    def _providers_by_id(self) -> dict[str, CatalogProvider]:
        return {p.provider: p for p in self.providers}

    def model(self, model_id: str) -> CatalogModel | None:
        return self._models_by_id.get(model_id)

    def provider(self, provider_id: str) -> CatalogProvider | None:
        return self._providers_by_id.get(provider_id)
