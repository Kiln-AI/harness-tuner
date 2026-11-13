---
status: complete
---

# Phase 2: The model catalogue, the fetch at start-up and the snapshot

## Overview

This phase adds the package `kiln_harness_tuner.model_catalog`. At the start
of each run, the CLI fetches Kiln's published model file and models.dev at
the same time, joins them into one `Catalog`, and writes it to
`model_catalog.json` in the run directory. The flag `--no-model-catalog`
turns both fetches off. The context (`Ctx`) carries the catalogue and the
run's start time for the detector of phase 4. The gateway does not read the
snapshot yet: that is phase 3.

Architecture sections 2 to 5. The manager's note from the phase 1 review:
the ported provider table has `cloudflare` -> `cloudflare-workers-ai`,
which the Kiln server's table does not have.

## Steps

1. `src/kiln_harness_tuner/model_catalog/schema.py`: `Source`,
   `CatalogEntry`, `CatalogModel`, `CatalogProvider` and `Catalog`, as
   architecture section 2, all with `extra="ignore"`. `Catalog.unavailable
   (reason)`, and `Catalog.model(id)` / `Catalog.provider(id)` through dicts
   built on first use in private attributes.

2. `src/kiln_harness_tuner/model_catalog/providers.py`:
   `KILN_TO_MODELS_DEV` (the Kiln server's seven entries, and `cloudflare`),
   `NO_MODELS_DEV` (the server's six), `PREFERENCE` (the server's six), and
   `models_dev_provider(kiln_provider, published)`.

3. `src/kiln_harness_tuner/model_catalog/build.py`:

   ```
   def build_catalog(kiln_raw, models_dev_raw, *, fetched_at, kiln, models_dev) -> Catalog
   ```

   - The private raw models `_RawProvider`, `_RawModel`, `_RawProviderInfo`
     (architecture 3.1). Each model and each provider item is validated
     alone. A bad one is dropped.
   - `_tags`, `_note`, `_price`, `_limit`.
   - A Kiln file with no list at `model_list` gives a catalogue with
     `kiln = none` and the reason "Kiln's file has no model_list".
   - The exact join for each entry. The model facts from the first matched
     entry in `PREFERENCE` order, then the rest by provider id.
   - The loose release-date index (`_key`, earliest date wins, keys under
     4 characters unused, deprecated entries count).
   - A `models_dev_raw` that is not a dict joins nothing.

4. `src/kiln_harness_tuner/model_catalog/fetch.py`: `KILN_URL`,
   `MODELS_DEV_URL`, `FETCH_TIMEOUT_SECS = 10.0`, `MAX_BYTES = 50 MiB`,
   `_get_json(client, url) -> (data, reason)` that never raises and streams
   the body against the cap, and

   ```
   async def fetch_catalog(*, enabled=True, client=None, now=...) -> Catalog
   ```

   `enabled=False` returns `Catalog.unavailable("turned off")` and sends no
   request. The two GETs run with `asyncio.gather`.

5. `src/kiln_harness_tuner/model_catalog/store.py`: `CATALOG_FILE`,
   `write_catalog` (tmp file, then `os.replace`, compact JSON) and
   `load_catalog` (never raises; an empty, missing or bad file gives
   `Catalog.unavailable("no snapshot")`).

6. `src/kiln_harness_tuner/model_catalog/__init__.py`: export `Catalog`,
   `fetch_catalog`, `build_catalog`, `write_catalog`, `load_catalog`,
   `CATALOG_FILE`.

7. `src/kiln_harness_tuner/orchestrator.py`: `Ctx.model_catalog: Catalog |
   None = None` and `Ctx.run_started_at: datetime | None = None`.

8. `src/kiln_harness_tuner/cli.py`:
   - the flag `--no-model-catalog` beside the other `--no-*` flags;
   - `"model_catalog.json"` in `RUN_DIR_ENTRIES`;
   - in `_run`, after the policy copy and before `render_run_configs`: the
     fetch, `write_catalog`, and one log line;
   - `build_ctx(..., model_catalog=...)` sets `ctx.model_catalog` and
     `ctx.run_started_at`;
   - one `model_catalog_unavailable` (`source`, `reason`) for each source
     that is `none`, and then always the event `model_catalog` (`kiln`,
     `models_dev`, `models`, `release_dates`). They are emitted when the wave
     log is wired (`ctx.log = FanoutLog(...)`), because before that the
     context's log is a `NullLog` and the event would be lost.

9. `docs/cli.md`: the row for `--no-model-catalog`
   (`tests/test_cli_reference.py` needs a row for each flag).

10. `tests/conftest.py`: an autouse fixture that replaces
    `kiln_harness_tuner.cli.fetch_catalog` with one that returns
    `Catalog.unavailable("turned off")`, so no test that reaches `cli.main`
    goes to the network. The CLI tests of the fetch put the real one back
    and route it with `respx`.

## Tests

Fixtures in `tests/fixtures/model_catalog/`: `kiln_config.json` (about 8
models cut from Kiln's model list, with the new fields on some) and
`models_dev.json` (the matching slice of models.dev, with one negative price
and one string limit).

`tests/test_model_catalog.py`:

- `test_the_fixture_builds_with_prices_limits_and_dates`: every fixture
  model is kept, with the expected prices, limits and dates.
- `test_price_is_matched_on_the_exact_pair`: each entry has its own
  provider's price, not another provider's.
- `test_model_facts_follow_the_provider_preference`: the model facts come
  from the preferred provider, not from an earlier entry in Kiln's order.
- `test_the_loose_match_dates_a_model_with_no_exact_match`.
- `test_the_loose_match_takes_the_earliest_date`.
- `test_a_short_loose_key_is_not_used`.
- `test_the_published_models_dev_id_wins_over_the_built_in_map`.
- `test_a_provider_without_models_dev_is_not_joined`.
- `test_cloudflare_maps_to_cloudflare_workers_ai`.
- `test_a_bad_price_and_a_bad_limit_are_dropped`.
- `test_a_bad_model_is_dropped_and_the_rest_kept`.
- `test_a_bad_provider_entry_is_dropped_and_its_model_kept`.
- `test_an_unknown_field_is_ignored`.
- `test_tags_are_cleaned_deduplicated_and_capped`.
- `test_a_long_note_is_cut`.
- `test_a_kiln_file_without_model_list_gives_kiln_none`.
- `test_no_models_dev_gives_null_prices_and_kiln_data`.
- `test_catalog_lookups_by_model_and_provider`.
- `test_fetch_turned_off_makes_no_request` (respx: no route called).
- `test_fetch_gets_both_sources_and_stamps_the_time` (respx).
- `test_fetch_timeout_gives_a_reason_and_never_raises` (respx).
- `test_fetch_non_2xx_gives_a_reason` (respx).
- `test_fetch_over_the_byte_cap_gives_a_reason` (respx).
- `test_fetch_bad_json_gives_a_reason` (respx).
- `test_write_then_load_round_trips`.
- `test_an_empty_or_missing_snapshot_loads_as_unavailable`.

`tests/test_cli.py`:

- `test_no_model_catalog_parses_and_defaults_off`.
- `test_the_run_writes_the_catalogue_snapshot_and_logs_the_event`: the
  real fetch on `respx` routes; the snapshot is in the run directory, the
  context carries it and the run's start time, and the wave log has the
  `model_catalog` event.
- `test_no_model_catalog_makes_no_request`: with the flag, no route is
  called, the snapshot says both sources are `none`, and the wave log has
  two `model_catalog_unavailable` events.
- The existing read-fence tests cover the new `RUN_DIR_ENTRIES` entry.
