---
status: draft
---

# Component: Model and provider tools

## 1. Purpose and scope

Loop agents choose models and providers. Today they get a list of names,
thinking levels and one output mode. This component gives them tools with
the data that Kiln publishes about each model: its family, its editorial
rank and notes, what it is suggested for, and what it supports.

Not in this component: knowledge about how to choose a model (playbooks and
techniques), and changes to Kiln's model file (section 6 lists proposals for
the Kiln repository).

## 2. The source

Kiln publishes its model list at
`https://remote-config.getkiln.ai/kiln_config_v2.json`. The file is
`{"model_list": [...], "embedding_model_list": [...],
"reranker_model_list": [...]}`. Each entry of `model_list` is a dump of
Kiln's `KilnModel`: `family`, `name`, `friendly_name`, `featured_rank`,
`editorial_notes`, and `providers`, a list of `KilnModelProvider` dumps.

The tuner does not import `kiln_ai`. It reads the file with its own models,
which keep only the fields it uses and ignore the rest.

## 3. `model_catalog.py`

`src/kiln_harness_tuner/model_catalog.py`

```
CATALOG_URL = "https://remote-config.getkiln.ai/kiln_config_v2.json"
CATALOG_TIMEOUT_SECS = 10.0
CATALOG_FILE = "model_catalog.json"        # in the run directory

class CatalogProvider(BaseModel, extra="ignore"):
    name: str                               # Kiln's provider id
    model_id: str | None = None
    deprecated: bool = False
    supports_function_calling: bool = True
    supports_structured_output: bool = True
    supports_vision: bool = False
    supports_doc_extraction: bool = False
    reasoning_capable: bool = False
    structured_output_mode: str | None = None
    available_thinking_levels: dict[str, str] | None = None
    default_thinking_level: str | None = None
    suggested_for_evals: bool = False
    suggested_for_data_gen: bool = False
    suggested_for_doc_extraction: bool = False
    suggested_for_synthetic_user: bool = False
    untested_model: bool = False

class CatalogModel(BaseModel, extra="ignore"):
    name: str
    family: str
    friendly_name: str
    featured_rank: int | None = None
    editorial_notes: str | None = None
    providers: list[CatalogProvider] = []

class Catalog(BaseModel):
    source: Literal["published", "none"]
    fetched_at: str | None = None           # ISO time, UTC
    models: list[CatalogModel] = []
    def find(self, provider: str, model: str) -> tuple[CatalogModel, CatalogProvider] | None
    def providers_of(self, model: str) -> list[CatalogProvider]

def parse_catalog(data: Any) -> Catalog
async def fetch_catalog(client: httpx.AsyncClient | None = None) -> tuple[Catalog, str]
    # the catalogue, and the reason when it is not available (empty on success)
def write_catalog(run_dir: Path, catalog: Catalog) -> None
def load_catalog(run_dir: Path) -> Catalog
```

- **`parse_catalog` is tolerant.** `data` must be a mapping with a list at
  `model_list`. Each model is validated alone, and each of its providers
  alone. An entry that is not valid is dropped. A field that the tuner does
  not know is ignored. So a newer file with more fields, or one bad entry,
  does not lose the rest.
- **`fetch_catalog` never raises.** A timeout, a status that is not 200, a
  body that is not JSON, or a body of the wrong shape gives
  `Catalog(source="none")` with the reason as the second value. The caller
  logs the reason.
- **`load_catalog`** gives `Catalog(source="none")` when the file is
  missing or not valid.

### 3.1 When it runs

Once for each run, in `cli.py`, where the run directory is prepared and the
model policy is written, before the first agent is dispatched:

```
if args.no_model_catalog: catalog, reason = Catalog(source="none"), "turned off"
else: catalog, reason = await fetch_catalog()
write_catalog(run_dir, catalog)
log event "model_catalog" source=<...> models=<n>      # or "model_catalog_unavailable" reason=<...>
```

- Flag: `--no-model-catalog`. `docs/cli.md` gets the row.
- This is the tuner's first call to an outside host. The request is a plain
  GET of a public file. It sends nothing about the run.
- The fetch has no retry. A slow or failed fetch costs at most 10 seconds
  and the run goes on.

### 3.2 The fallback

With `source="none"`, the tools still work. They serve what the Kiln
server's `/api/available_models` gives, which is the model list of the
installed Kiln. The fields that only the published file has (`family`,
`featured_rank`, `editorial_notes`, `reasoning_capable`) are then null, and
each answer carries `catalog: "none"` so that the agent knows.

## 4. The gateway operations

The gateway process loads the catalogue from the run directory at start, as
it loads the model policy (`gateway/server.py`), and keeps it on `State`.

A model is **available** when the Kiln server offers it (its provider has a
key) and the model policy allows it. The tools return available models
only: an agent cannot run any other. The catalogue adds data to an available
model. It does not add models.

The join key is `(provider id, model name)`: `provider_id` and `id` in the
server's answer, `CatalogProvider.name` and `CatalogModel.name` in the
catalogue. A model that the server offers and the catalogue does not have
(a fine-tune, a custom model) is returned with the catalogue fields null.

### 4.1 `list_models`

```
class ListModelsArgs(In):
    provider: str | None = None
    family: str | None = None
    featured_only: bool = False
    suggested_for: Literal["evals", "data_gen", "doc_extraction", "synthetic_user"] | None = None
    tools: bool | None = None                # supports function calling
    reasoning: bool | None = None            # has thinking levels, or is reasoning capable
    include_deprecated: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

class ModelSummary(Out):
    provider: str
    model: str
    name: str | None
    family: str | None
    featured_rank: int | None
    thinking_levels: list[str]
    deprecated: bool
    other_providers: list[str]               # other available providers of the same model

class ModelList(Out):
    catalog: Literal["published", "none"]
    total: int
    models: list[ModelSummary]
```

- Filters are joined with AND. A filter on a field that is null for a model
  leaves that model out, except `include_deprecated`.
- Order: `featured_rank` ascending with null last, then the order of the
  catalogue (Kiln keeps each family newest first), then models that are not
  in the catalogue, by name.
- Deprecated entries are left out unless `include_deprecated` is true.

### 4.2 `get_model_details`

```
class ModelRef(In):
    provider: str
    model: str

class ModelDetails(Out):
    catalog: Literal["published", "none"]
    provider: str
    model: str
    name: str | None
    family: str | None
    featured_rank: int | None
    editorial_notes: str | None
    deprecated: bool
    untested: bool
    thinking_levels: list[str]
    default_thinking_level: str | None
    reasoning_capable: bool | None
    structured_output_mode: str | None
    supports_tools: bool | None
    supports_structured_output: bool | None
    supports_vision: bool | None
    supports_doc_extraction: bool | None
    suggested_for: list[str]
    other_providers: list[str]
```

A model that is not available is refused with the message that names the
reason: not offered by the server, or not allowed by the policy. The text
for the policy case is the one `_policy_check` gives today.

### 4.3 `list_providers`

```
class ProviderSummary(Out):
    provider: str
    models: int                  # available models on this provider
    notes: str | None            # from the catalogue's provider data, when Kiln publishes it

class ProviderList(Out):
    catalog: Literal["published", "none"]
    providers: list[ProviderSummary]
```

Today Kiln publishes no data about a provider beyond its id, so `notes` is
null. The operation exists now so that the provider data of section 6
arrives through a tool that agents already use, with no change to the role
wiring.

### 4.4 Registration

- Three entries in `OPS`, with summaries that become the tool descriptions.
- `ROLE_OPS`: the three roles that have `list_available_models` today
  (`batch_plan`, `create_experiment_rc`, `probe`) get the three operations.
- `list_available_models` stays, with no change. The planner's input still
  carries `allowed_models`, which is the list an arm must use. The new tools
  add detail. They do not replace that list.
- The models that the model preflight flagged are not in the gateway's
  state today, and this component does not change that. The planner's input
  already leaves them out.
- `agent_files/skills/kiln-harness-tuner-gateway/SKILL.md` gets a section
  "Models and providers" with the three operations, their fields and one
  example each. An existing test requires that each operation name is in
  that file.

## 5. What the skills say

- The planner's skill and the experiment-costing skill say today that model
  prices come "from a live model-info lookup, never from memory". No such
  lookup exists, and this component adds no price. The sentence is changed
  to say what is true: the price of a model on this task is the one the
  loop measures (`measurement_prices`, `cost_per_item_usd`), and the model
  tools give no price.
- The planner's skill gets one paragraph: use `list_models` to find
  candidates in a family or with a capability, use `get_model_details`
  before an arm on a model the run has not used, and treat
  `editorial_notes` as an opinion with no date.
- Guidance about which model to choose is knowledge. It goes in playbooks
  and techniques, in the V1 coverage phase.

## 6. Proposals for Kiln's model file

These are changes in the Kiln repository. They are not part of this
project's code, and no phase waits for them. Each field is optional with a
default, so an older Kiln ignores it, and Kiln's publish workflow already
tests that. When a field is in the published file, the tools take it with a
small change here: a field on `CatalogModel` or `CatalogProvider`, a field
on the answer, and a filter.

| Field | On | Use | Maintenance cost |
|---|---|---|---|
| `superseded_by: str \| None` | model | "A newer model of the same line exists." It makes the "very old model" detector possible. | Low: set once when the newer model is added. |
| `size_class: "small" \| "medium" \| "large" \| None` | model | "Pick the class and lock one model." A filter for models of the same class. | Low: a model's class does not change. |
| `release_date: str \| None` | model | Order and age. | Low. |
| `open_weights: bool \| None` | model | The restriction "open models only", which today is written as name patterns in the model policy. | Low. |
| A top-level `provider_list`, with `name`, `notes`, `zero_data_retention: bool \| None` | file | The provider tool. Restrictions on data retention. | Medium: a provider's terms change. |

**Not proposed: prices and speeds.** They change often, they differ by
provider, and the loop measures both on the real task. A stored value would
have a high maintenance cost and would go out of date, which the content
rule on the cost of maintenance warns against.

Kiln's skill for the model list (`claude-maintain-models`) has no guidance
on `featured_rank` or `editorial_notes`, and Kiln's scheduled sweep is told
not to change them. New editorial fields need guidance there.

## 7. Dependencies

- Uses: `httpx` (already a dependency), Pydantic, `model_policy`,
  `KilnClient` for `/api/available_models`.
- Used by: `cli.py` (the fetch), `gateway/server.py` and `gateway/ops.py`
  (the operations).

## 8. Test plan

`tests/test_model_catalog.py`:

- `test_a_published_file_parses_to_models_and_providers`
- `test_an_unknown_field_is_ignored`
- `test_a_bad_provider_is_dropped_and_its_model_kept`
- `test_a_bad_model_is_dropped_and_the_rest_kept`
- `test_a_body_of_the_wrong_shape_gives_no_catalogue`
- `test_a_timeout_gives_no_catalogue_and_a_reason` (with `respx`)
- `test_a_status_that_is_not_200_gives_no_catalogue_and_a_reason`
- `test_write_then_load_gives_the_same_catalogue`
- `test_a_missing_file_loads_as_no_catalogue`
- `test_the_flag_turns_the_fetch_off`

`tests/test_gateway.py`, additions:

- `test_list_models_returns_only_available_models`
- `test_list_models_filters_are_joined_with_and`
- `test_list_models_orders_by_rank_then_catalogue_order`
- `test_list_models_leaves_out_deprecated_by_default`
- `test_list_models_without_a_catalogue_serves_the_server_data`
- `test_a_model_the_catalogue_does_not_have_is_still_listed`
- `test_get_model_details_refuses_a_model_the_policy_does_not_allow`
- `test_get_model_details_names_the_other_available_providers`
- `test_list_providers_counts_available_models`
- `TestRoles`: the three roles are served the three operations

`tests/test_agent_templates.py`: the gateway skill names the three
operations (the existing test covers it once they are in `OPS`).
`tests/test_cli_reference.py`: the flag.
