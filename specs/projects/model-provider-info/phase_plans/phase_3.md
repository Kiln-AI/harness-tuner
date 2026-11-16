---
status: complete
---

# Phase 3: The model gateway operations, the roles, and the text

## Overview

This phase gives the loop agents the model catalogue of phase 2. The
gateway process loads the run's `model_catalog.json` at start. Three new
gateway operations, `list_models`, `get_model_details` and
`list_providers`, join the snapshot with the Kiln server's
`/api/available_models` at each call and with the run's model policy. They
replace `list_available_models` in the roles `batch_plan`,
`create_experiment_rc` and `probe`. The skill text and the docs name the
new operations.

Architecture sections 6 and 8. The manager's notes from the phase 2
review: `docs/cli.md`'s `--no-model-catalog` row and the
`model_catalog/__init__.py` docstring already say that agents read the
snapshot and that `docs/architecture.md` has a section "The model
catalogue". This phase makes both true. The fixture has no
`kiln_fine_tune` entry, so a test builds that pair inline.

## Steps

1. `src/kiln_harness_tuner/gateway/models.py`:
   - remove `ModelView` (only `list_available_models` uses it);
   - add `ModelStatus = Literal["ready", "blocked_by_policy", "not_offered",
     "not_connected"]`;
   - add the requests `ListModelsArgs`, `ModelDetailsArgs`,
     `ListProvidersArgs` and the answers `CatalogState`,
     `ProviderEntryRow`, `ModelRow`, `ModelList`, `ProviderEntryDetails`,
     `ModelDetailsView`, `ProviderRow`, `ProviderList`, as architecture
     6.3. A model id and a provider id are bounded strings (1 to 200
     characters). `tags` takes at most 12 values.

2. `src/kiln_harness_tuner/gateway/model_info.py` (new, pure):

   ```
   Status = m.ModelStatus

   @dataclass(frozen=True)
   class Offered:
       pairs: dict[tuple[str, str], dict[str, Any]]
       connected: frozenset[str]
       order: list[tuple[str, str]]
       @classmethod
       def from_payload(cls, payload: Any) -> Offered

   def status(offered, policy, provider, model) -> Status
   def list_models(catalog, offered, policy, req: m.ListModelsArgs) -> m.ModelList
   def model_details(catalog, offered, policy, req: m.ModelDetailsArgs) -> m.ModelDetailsView
   def list_providers(catalog, offered, policy, req: m.ListProvidersArgs) -> m.ProviderList
   ```

   - `_merged(catalog, offered) -> list[CatalogModel]`: deep copies of the
     catalogue's models in order; an offered pair with no entry becomes an
     entry on its catalogue model (`_entry_from_server`), or a new
     `CatalogModel` after the catalogue's models, sorted by id.
   - The entry filters, the row filters, the three sort keys and the
     paging of architecture 6.2.
   - `model_details` raises `GatewayError` for an unknown model and for a
     provider the model has no entry for. To avoid an import cycle with
     `ops.py`, `GatewayError` moves to `gateway/errors.py` and `ops.py`
     imports it from there (the name `ops.GatewayError` stays).

3. `src/kiln_harness_tuner/gateway/ops.py`:
   - `State.catalog: Catalog`, default `Catalog.unavailable("no snapshot")`;
   - remove `list_available_models` and the `allowed_models` import;
   - add `_offered`, `list_models`, `get_model_details`, `list_providers`;
   - `OPS`: the three entries with the summaries of architecture 6.4;
   - `ROLE_OPS`: `_MODELS = ("list_models", "get_model_details",
     "list_providers")` in place of `list_available_models` in
     `batch_plan`, `create_experiment_rc` and `probe`;
   - the module docstring: no operation that CHANGES settings or
     providers.

4. `src/kiln_harness_tuner/gateway/server.py`: `load_state` sets
   `catalog=load_catalog(run_dir)`.

5. Skill and knowledge text:
   - `kiln-harness-tuner-gateway/SKILL.md`: the section "Models and
     providers" in place of "Reading the models", with the three
     operations, their arguments, the four statuses, and one example each.
   - `experiment-costing/SKILL.md`: the model prices come from
     `list_models` and `get_model_details`; a listed price is a list price;
     the price the loop measures on the task wins.
   - `levers/model.md` and `levers/params.md`: `gateway_ops` and the text
     name `list_models` and `get_model_details`. The line that says the tool
     does not give capability flags goes. Then `knowledge build` rewrites
     `builder.md`.

6. Docs:
   - `docs/architecture.md`: a section "17.1 The model catalogue" after
     the model policy: the two outside hosts, the fetch at start-up, the
     snapshot `runs/<run_id>/model_catalog.json` (fenced like the other run
     files), the gateway load, the three operations and the four statuses.
     Section 12's line "no operation for settings, providers" becomes "no
     operation that changes settings or providers".
   - `docs/cli.md`: the `--no-model-catalog` row points to the new
     section.

## Tests

`tests/test_gateway_model_info.py` (the pure functions, on the phase 2
fixture built with `build_catalog`, and an inline server payload):

- `test_status_ready_blocked_not_offered_not_connected`
- `test_offered_from_a_bad_payload_is_empty`
- `test_list_models_returns_ready_entries_only_by_default`
- `test_include_not_available_lists_every_status`
- `test_deprecated_entries_are_left_out_by_default`
- `test_entry_filters_remove_entries_and_empty_rows`
- `test_max_input_price_drops_entries_with_no_price`
- `test_row_filters_family_open_weights_and_tags`
- `test_a_tag_on_a_provider_entry_matches_the_row`
- `test_sort_featured_price_newest_and_ties`
- `test_paging_and_total`
- `test_a_pair_the_catalogue_lacks_is_listed_with_null_fields`
- `test_a_model_the_catalogue_lacks_is_listed_after_the_catalogue` (an
  inline `kiln_fine_tune` pair)
- `test_an_unavailable_catalogue_serves_the_server_list`
- `test_model_details_lists_every_entry_with_its_status`
- `test_model_details_refuses_an_unknown_model`
- `test_model_details_with_a_provider_keeps_one_entry`
- `test_list_providers_counts_ready_models_and_orders_them`
- `test_list_providers_hides_unconnected_by_default`

`tests/test_gateway.py`:

- `TestRoles`: the three roles are served the three operations, and no role
  is served `list_available_models`. The test of forbidden operation names
  allows the read `list_providers` by name.
- `test_load_state_reads_the_catalogue_snapshot`, and a default of
  "no snapshot" without a file.
- The two existing tests of `list_available_models` move to `list_models`
  and `get_model_details` through `dispatch` (policy and
  `structured_output_mode`).
