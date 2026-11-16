"""The model catalogue: Kiln's published model file joined with models.dev,
fetched once at the start of a run and kept as one snapshot in the run
directory (`docs/architecture.md`, "The model catalogue")."""

from kiln_harness_tuner.model_catalog.build import build_catalog
from kiln_harness_tuner.model_catalog.fetch import fetch_catalog
from kiln_harness_tuner.model_catalog.schema import (
    Catalog,
    CatalogEntry,
    CatalogModel,
    CatalogProvider,
    Source,
)
from kiln_harness_tuner.model_catalog.store import (
    CATALOG_FILE,
    load_catalog,
    write_catalog,
)

__all__ = [
    "CATALOG_FILE",
    "Catalog",
    "CatalogEntry",
    "CatalogModel",
    "CatalogProvider",
    "Source",
    "build_catalog",
    "fetch_catalog",
    "load_catalog",
    "write_catalog",
]
