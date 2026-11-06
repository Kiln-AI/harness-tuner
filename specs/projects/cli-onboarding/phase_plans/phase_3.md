---
status: complete
---

# Phase 3: The API-key check

## Overview

After this phase, the start-up checks that Kiln has an API key for every provider the run will call, before the run starts (FS §8, architecture §10). The providers come from four places: the baseline run config, the judge of each measurable eval, the simulated user of a multi-turn task, and the providers `--model-policy` names exactly. A missing key is reported with the provider, what uses it, and the link to Kiln's AI Providers page, through the shared retry prompt.

The baseline is found by the rule setup seeds it with. So that rule moves out of `seed_baseline_if_missing` into a pure `choose_baseline_rc` in `setup.py`, which both use.

`--model-policy` is now also loaded by the start-up, before the key check. `prepare_start` takes cli's loader as `policy_loader`, so `onboarding` still doesn't import `cli`.

With this phase, `kiln-harness-tuner` with no flags walks every check the functional spec lists except the demo (phase 4).

## Steps

1. **`setup.py`: `choose_baseline_rc`** (§10.1). Pulled out of `seed_baseline_if_missing` with no behavior change:

   ```
   @dataclass(frozen=True)
   class BaselineChoice:
       rc_id: str
       rule: str                          # BASELINE_RULE_TASK_DEFAULT or BASELINE_RULE_MOST_RECENT
       unresolvable_default: str | None = None

   def choose_baseline_rc(task: dict[str, Any], run_configs: list[dict[str, Any]]) -> BaselineChoice:
       """The task's default run config when the list has it, else the most recent.
       `run_configs` are `_usable_run_configs` rows, at least one."""
   ```

   - `seed_baseline_if_missing` keeps its reads and its errors, then calls `choose_baseline_rc` and seeds `choice.rc_id` with `choice.rule`. Its `BaselineSeedOutcome` is built from the choice.
   - The comments on the dangling default move with the code.

2. **`model_policy.py`: `Rule.exact_provider`**. A property, true when the provider has no glob character (`*?[]`). "Names a provider exactly" (FS §8.1) is the same test the module already makes for `exact`, on the provider alone.

3. **`onboarding/keys.py`** (§10):
   - `NO_KEY_PROVIDERS`, as the architecture lists them.
   - `Need(provider, used_by, from_policy=False)`, frozen.
   - `async needed_providers(kiln, *, project_id, task_id, project_path: Path, policy) -> list[Need]`, in this order:
     1. **Baseline.** One `asyncio.gather` of `GET {base}`, `{base}/run_configs`, `{base}/evals` and `{base}/specs`. The task must be a dict, else `SetupPreconditionError(unreadable_response_message(...))`, as seeding says it. Then the baseline's run-config ID:
        - A `MemoryWriter` for the task. If `find_baseline_record()` finds the record, `baseline_rc_id(await require_baseline_record())`. A `BaselineRecordMissingError` gives no baseline; the run's start-up reports it later with its own message.
        - Else `choose_baseline_rc(task, usable).rc_id`, or no baseline when there are no usable run configs.
        - The run config with that ID gives `model_provider_name` through `model_policy.run_config_pair`. `used_by` is `the task's run config "<name>"`, the name falling back to the ID.
     2. **Judges.** For each `measurable_evals(evals, specs)` pair, `GET {base}/evals/{eid}/eval_config/{cid}`, bounded by `SETUP_CONFIG_READ_CONCURRENCY`. A 404 or 410 is skipped (`CONFIG_GONE_STATUSES`). `judge_provider(config)`: the root `model_provider` when it is a non-empty string, else `properties.model_provider` when `properties.type == "llm_judge"`, else none (a code eval). `used_by` is `eval "<name>"'s judge`.
     3. **Simulated user.** Only when the task's `turn_mode` is `multiturn` (`job_drives.MULTI_TURN_MODE`). `simulated_user_providers(project_path, task_id) -> set[str]`: for each `quarantine.task_dirs`, each `eval_inputs/*/eval_input.kiln`, the `data.drive_config.model_provider`. `data` may be a JSON string, as the other readers allow. Files that can't be read or parsed, and odd shapes, are skipped. One `Need` per provider, sorted, `used_by` `the simulated user in the task's eval inputs`.
     4. **Policy.** For each rule in `policy.allow + policy.exceptions` with `exact_provider`: `Need(rule.provider, "--model-policy", from_policy=True)`.
   - `async key_problem(kiln, *, host, needs) -> Problem | None` (§10.2):
     - The needs whose provider is in `NO_KEY_PROVIDERS` are dropped. With none left, `None`, and no call is made.
     - `GET /api/available_models` through `_require_rows`. The available set is each dict row's string `provider_id`.
     - The missing providers are grouped in first-seen order. Each line is `  • <name> (<id>), used by <used_by, …>`, with duplicate `used_by` texts once. When every need of a provider is from the policy, the line ends `, or remove it from --model-policy`. When something else needs the provider too, removing it from the policy wouldn't fix it, so the suffix is left out.
     - The message, with the count and its plural (`1 provider` / `Add it`, `2 providers` / `Add them`):

       ```
       Kiln has no API key for 2 providers this run needs:
         • Fireworks AI (fireworks_ai), used by the task's run config "Baseline"
         • OpenAI (openai), used by eval "Tone"'s judge
       Add them in Kiln under AI Providers: http://localhost:8757/settings/providers
       ```

     - `open_url` is `kiln_urls.providers(host)`, `open_label` `AI Providers`.
   - **Display names.** The architecture reads them from `GET /api/settings/available_providers`. That route lists only the providers whose keys are set (Kiln's `get_available_providers` applies the same rule as `get_available_models`), so it can never name a missing provider. Instead, `PROVIDER_NAMES` holds Kiln's own display names for the key providers (`provider_name_from_id` in `kiln_ai/adapters/provider_tools.py`). An ID not in it is shown alone.

4. **`onboarding/flow.py`**:
   - `prepare_start` gains a keyword `policy_loader: Callable[[str | None], ModelPolicy | None]`.
   - The readiness loop now ends with `break` instead of `return`. After it: `policy = policy_loader(args.model_policy)`, then `_wait_for_keys`.
   - `_wait_for_keys(run, terminal, host, args, policy)`: `needs = run(needed_providers(...))` once, then `resolve_problem(lambda: run(key_problem(..., needs=needs)), terminal)`. A retry re-reads only the available models; what the run needs doesn't change while the user adds a key.

5. **`cli.py`**: `main` passes `policy_loader=load_model_policy`. A bad policy file raises `ConfigRenderError`, which `main` already prints as one line.

6. **Test fakes** (`tests/onboarding_fakes.py`):
   - `ready_task_routes` also answers the key check's reads, and the key is there: the task (`turn_mode` `single_turn`), a run config on `fireworks_ai`, a code-eval config with no provider, an empty memory listing, and `/api/available_models` with `fireworks_ai`.
   - New builders: `memory_listing(*rows)`, `baseline_memory(rc_id)` (the listing and `by_ids` routes for a baseline record), `available_models(*provider_ids)`, `run_config(rc_id, provider, ...)` and `eval_config(...)`.
   - `flags()` gains `model_policy=None`.
   - The flow and Kiln-check tests pass `policy_loader=load_model_policy`.

## Tests

- `tests/test_setup.py`:
  - `test_choose_baseline_rc_honours_the_task_default`.
  - `test_choose_baseline_rc_falls_back_to_the_most_recent_for_a_dangling_default`, with `unresolvable_default` set.
  - `test_choose_baseline_rc_takes_the_most_recent_with_no_default`, for a missing, `None` or `""` default, with `unresolvable_default` `None`.
  - The existing seeding tests stay green unchanged.
- `tests/test_model_policy.py`: `test_exact_provider_is_false_for_any_glob_character`.
- `tests/test_onboarding_keys.py`:
  - Baseline: from the memory record (the record's run config wins over the task default); from the task default; from the most recent; a record with no `rc_` tag gives no baseline need; no usable run configs gives none; a run config with no provider (an MCP run config) gives none; the name falls back to the ID.
  - `test_a_task_that_isnt_a_dict_is_a_precondition_error`.
  - Judges: a legacy judge's root `model_provider`; a V2 `llm_judge`'s `properties.model_provider`; a code eval adds nothing; a 404 config is skipped; a 500 propagates; evals that aren't measurable (inactive spec, no default config) are not read.
  - Simulated user: providers from `tmp_path` eval inputs, `data` as a dict and as a JSON string, deduplicated and sorted; unreadable files, non-JSON files and inputs with no `drive_config` are skipped; a single-turn task doesn't read the disk.
  - Policy: exact providers from `allow` and `exceptions`; `*`, `?` and `[...]` providers add none; no policy adds none.
  - The order of the needs: baseline, judges, simulated user, policy.
  - `key_problem`: no needs makes no call; only no-key providers make no call; every provider available gives `None`; the FS message for two providers with names; one provider is singular; an unknown ID is shown alone; `used_by` texts are grouped and deduplicated; the policy suffix only when the policy alone needs the provider; `open_url` and `open_label`; a drifted `/api/available_models` raises `SetupPreconditionError`.
- `tests/test_onboarding_flow.py`:
  - `test_no_flags_walks_every_check_and_returns_the_filled_flags`: two projects and two tasks picked by number, a Fireworks key that is missing at first, then `r` after it is added. The key check's reads come after readiness, and the returned namespace holds the IDs and Kiln's folder.
  - `test_a_missing_key_when_not_interactive_fails_with_the_message`.
  - `test_open_opens_the_ai_providers_page`.
  - `test_the_policy_is_loaded_before_the_key_check`: the loader sees `args.model_policy`, and its providers join the check.
  - `test_a_bad_policy_file_stops_before_the_key_check`: the loader's `ConfigRenderError` propagates, and `/api/available_models` is never read.
- `tests/test_cli.py`: `test_main_runs_the_start_up_checks_before_the_run` also checks that `policy_loader` is `load_model_policy`.
