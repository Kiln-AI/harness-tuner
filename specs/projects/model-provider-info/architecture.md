---
status: complete
---

# Architecture: Model Info MCP

Read [functional_spec.md](./functional_spec.md) first. This document says
how to build it. Section numbers in the form "FS 5.4" point to the
functional spec.

## 1. Overview

```
start of run (cli._run)
  └─ model_catalog.fetch_catalog()      two GETs at the same time, 10 s each
       └─ model_catalog.build_catalog() pure: tolerant parse + join
  └─ model_catalog.write_catalog()      run_dir/model_catalog.json
  └─ build_ctx(..., model_catalog=...)  ctx.model_catalog, ctx.run_started_at

gateway process (one per agent session)
  └─ server.load_state()                State.catalog = load_catalog(run_dir)
  └─ ops.list_models / get_model_details / list_providers
       └─ GET /api/available_models     at each call
       └─ gateway/model_info.py         pure: offered pairs, status, rows

baseline step (smells.run._collect)
  └─ DetectorInputs.model, .run_date    from ctx.model_catalog, ctx.run_started_at
  └─ smells/detectors/old_model.py      pure
```

New code:

```
src/kiln_harness_tuner/model_catalog/
  __init__.py        exports Catalog, fetch_catalog, build_catalog,
                     write_catalog, load_catalog, CATALOG_FILE
  schema.py          the snapshot models
  build.py           the tolerant parse of Kiln's file and the join
  providers.py       the built-in Kiln -> models.dev provider table
  fetch.py           the two GETs
  store.py           write and load the snapshot
src/kiln_harness_tuner/gateway/model_info.py
                     offered pairs, status, list rows, details, providers
src/kiln_harness_tuner/smells/detectors/old_model.py
```

No new dependency. `httpx` is a dependency today. `respx` is a test
dependency today.

## 2. The snapshot models (`model_catalog/schema.py`)

All are Pydantic models with `extra="ignore"`, so an older tuner reads a
newer snapshot.

```
SourceState = Literal["ok", "none"]

class Source(BaseModel):
    state: SourceState = "none"
    reason: str = ""                     # empty when ok

class CatalogEntry(BaseModel):
    """One provider entry of one model."""
    provider: str                        # Kiln's provider id
    model_id: str | None = None          # the provider's own id for the model
    deprecated: bool = False
    untested: bool = False
    supports_tools: bool | None = None
    supports_structured_output: bool | None = None
    supports_vision: bool | None = None
    supports_doc_extraction: bool | None = None
    reasoning_capable: bool | None = None
    thinking_levels: list[str] = []      # the values of available_thinking_levels, in Kiln's order
    default_thinking_level: str | None = None
    structured_output_mode: str | None = None
    suggested_for: list[str] = []        # "evals", "data_gen", "doc_extraction", "synthetic_user"
    tags: list[str] = []
    # from models.dev, null when this entry has no exact match
    input_price: float | None = None     # USD per million tokens
    output_price: float | None = None
    cache_read_price: float | None = None
    cache_write_price: float | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    takes_temperature: bool | None = None

class CatalogModel(BaseModel):
    model: str                           # Kiln's model id (KilnModel.name)
    name: str                            # friendly_name
    family: str | None = None
    featured_rank: int | None = None
    editorial_notes: str | None = None
    agent_notes: str | None = None
    tags: list[str] = []
    entries: list[CatalogEntry] = []
    # model facts from models.dev (FS 3.3)
    release_date: str | None = None      # ISO date "YYYY-MM-DD"
    release_date_match: Literal["exact", "loose"] | None = None
    knowledge_cutoff: str | None = None
    open_weights: bool | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None

class CatalogProvider(BaseModel):
    provider: str
    name: str | None = None
    notes: str | None = None
    tags: list[str] = []
    models_dev_id: str | None = None

class Catalog(BaseModel):
    fetched_at: str | None = None        # ISO time, UTC
    kiln: Source = Source()
    models_dev: Source = Source()
    models: list[CatalogModel] = []      # in the order of Kiln's file
    providers: list[CatalogProvider] = []

    @classmethod
    def unavailable(cls, reason: str) -> "Catalog"   # both sources "none" with this reason
    def model(self, model_id: str) -> CatalogModel | None
    def provider(self, provider_id: str) -> CatalogProvider | None
```

`model` and `provider` use a dict built on first use (a private attribute),
not a scan.

## 3. The build (`model_catalog/build.py`)

```
def build_catalog(
    kiln_raw: Any, models_dev_raw: Any, *,
    fetched_at: str, kiln: Source, models_dev: Source,
) -> Catalog
```

Pure. No I/O. `kiln_raw` is the parsed JSON of Kiln's file or None.
`models_dev_raw` is the parsed JSON of models.dev or None.

### 3.1 The Kiln parse

Private Pydantic models with `extra="ignore"` read Kiln's raw names:

```
_RawProvider: name, model_id, deprecated, untested_model,
    supports_function_calling, supports_structured_output, supports_vision,
    supports_doc_extraction, reasoning_capable, available_thinking_levels
    (dict[str, str] | None), default_thinking_level, structured_output_mode,
    suggested_for_evals, suggested_for_data_gen, suggested_for_doc_extraction,
    suggested_for_synthetic_user, tags (list[str] = [])
_RawModel: name, friendly_name, family, featured_rank, editorial_notes,
    agent_notes, tags, providers (list[Any])
_RawProviderInfo: id, friendly_name, notes, tags, models_dev_id
```

- `kiln_raw` must be a dict with a list at `model_list`. Otherwise the
  build returns a catalogue with `kiln.state = "none"` and the reason
  "Kiln's file has no model_list".
- Each model is validated alone with `_RawModel.model_validate`. On
  `ValidationError` it is dropped. Each item of its `providers` is validated
  alone the same way. A model keeps the providers that are valid.
- `provider_list` is read when it is a list. Each item is validated alone.
- `_tags(values) -> list[str]`: keep a value that matches
  `^[a-z0-9_]{1,40}$`, in order, without duplicates, at most 12.
- `_note(text) -> str | None`: None for an empty or blank text. A text over
  2,000 characters becomes `text[:1994].rstrip() + " [cut]"`.
- `editorial_notes`, `agent_notes` and the provider `notes` go through
  `_note`. Every `tags` goes through `_tags`.

### 3.2 The models.dev join

The provider map (`providers.py`):

```
KILN_TO_MODELS_DEV: dict[str, str]   # port of KILN_TO_MODELS_DEV_PROVIDER
NO_MODELS_DEV: frozenset[str]        # port of PROVIDERS_WITHOUT_MODELS_DEV
PREFERENCE: tuple[str, ...]          # port of MODELS_DEV_PROVIDER_PREFERENCE

def models_dev_provider(kiln_provider: str, published: str | None) -> str | None:
    if published: return published
    if kiln_provider in NO_MODELS_DEV: return None
    return KILN_TO_MODELS_DEV.get(kiln_provider, kiln_provider)
```

`published` is `provider_list[].models_dev_id` for that provider, when Kiln
publishes it.

For each entry:

```
md = models_dev_raw.get(md_provider, {}).get("models", {}).get(entry.model_id)
```

When `md` is a dict, the entry takes: `cost.input`, `cost.output`,
`cost.cache_read`, `cost.cache_write` (each through `_price`),
`limit.context` and `limit.output` (each through `_limit`), and
`temperature` (a bool, else None).

- `_price(v)`: a float or int that is finite and 0 or more, else None. Port
  of `_is_valid_price`.
- `_limit(v)`: an int above 0, else None. A bool is not an int here.

Model facts: the entries in the order `PREFERENCE`, then the others sorted
by provider id. The first entry with a match gives `release_date`,
`knowledge` (to `knowledge_cutoff`), `open_weights`, `limit.context` and
`limit.output`. `release_date_match = "exact"`.

### 3.3 The loose release date

When no entry matched, or the match had no `release_date`:

```
def _key(model_id: str) -> str:
    tail = model_id.split("/")[-1].split(":")[0].lower()
    return re.sub(r"[^a-z0-9]", "", tail)
```

- The index is built once for each build. For each models.dev entry with a
  valid `release_date`, the keys are `_key(id)` and, when present,
  `_key(canonical_model_id)`. When two entries give one key, the earliest
  date wins. An early date is the first release, and a later date on a
  key is usually a provider adding the model later.
- A Kiln model takes the earliest date among the keys of its entries'
  `model_id`s. Deprecated entries count here: an old id is still evidence
  of the release date. Without them the count falls from 242 to 231.
  `release_date_match = "loose"`.
- A key shorter than 4 characters is not used. A short key such as `o3`
  matches too much.
- A `release_date` is valid when it parses with `date.fromisoformat`. Only
  the first 10 characters are kept.

The other model facts are not taken from a loose match.

### 3.4 What the coverage check found

With the models.dev file of 2026-11-13 and Kiln `main` at `a1dc3e4`, this
algorithm gave a release date for 242 of 264 models (212 exact). A test
fixture (section 9) keeps a small slice of both files, so that the
algorithm is tested offline.

## 4. The fetch and the store

### 4.1 `model_catalog/fetch.py`

```
KILN_URL = "https://remote-config.getkiln.ai/kiln_config_v2.json"
MODELS_DEV_URL = "https://models.dev/api.json"
FETCH_TIMEOUT_SECS = 10.0
MAX_BYTES = 50 * 1024 * 1024

async def fetch_catalog(
    *, enabled: bool = True, client: httpx.AsyncClient | None = None,
    now: Callable[[], datetime] = ...,
) -> Catalog
```

- `enabled=False` returns `Catalog.unavailable("turned off")` with no
  request.
- Otherwise it runs `_get_json(client, KILN_URL)` and
  `_get_json(client, MODELS_DEV_URL)` with `asyncio.gather`.
- `_get_json` returns `(data, reason)`. It never raises. It streams the body
  with `client.stream("GET", url)` and stops when the bytes pass
  `MAX_BYTES`. A timeout, a transport error, a status that is not 2xx, a
  body over the cap, or JSON that does not parse gives `(None, reason)`. The
  reason is short text, for example "timeout after 10 s" or "HTTP 503".
- The client, when not given, is
  `httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECS, follow_redirects=True)`,
  closed after the fetch. The request has no header beyond httpx's
  defaults.
- Each source's `Source` is `ok` when its data came back, else `none` with
  the reason. Then `build_catalog`. When the build finds Kiln's data of the
  wrong shape, the build sets `kiln` to `none` (section 3.1).
- `fetched_at` is `now()` in UTC, ISO format, to seconds.

### 4.2 `model_catalog/store.py`

```
CATALOG_FILE = "model_catalog.json"

def write_catalog(run_dir: Path, catalog: Catalog) -> None
def load_catalog(run_dir: Path) -> Catalog
```

- `write_catalog` writes to `model_catalog.json.tmp`, then
  `os.replace`. Compact JSON (no indent). The file is about 0.5 MB.
- `load_catalog` never raises. A missing file, an empty file, or a file that
  is not a valid `Catalog` gives `Catalog.unavailable("no snapshot")`.

## 5. Start-up wiring

### 5.1 `cli.py`

- A new flag `--no-model-catalog` (store true), in the group of the other
  `--no-*` flags.
- `RUN_DIR_ENTRIES` gets `"model_catalog.json"`, so the read fence denies it
  from the first dispatch. `prepare_run_dir` creates it empty. The
  loader treats an empty file as "no snapshot".
- In `_run`, after the policy copy and before `render_run_configs`:

```
catalog = await fetch_catalog(enabled=not args.no_model_catalog)
write_catalog(run_dir, catalog)
logger.info("model catalogue: kiln %s, models.dev %s, %d models", ...)
```

- `build_ctx` gets two new keyword parameters, `model_catalog: Catalog |
  None = None` and the `run_started_at` it already takes, and sets them on
  `Ctx`.
- When the wave log is wired (`ctx.log = FanoutLog(...)`), `_run` emits
  one `model_catalog_unavailable` with `source` and `reason` for each
  source that is `none`. Then it always emits `model_catalog` with `kiln`,
  `models_dev`, `models` (count) and `release_dates` (count). Before the
  wave log is wired, the context's log is a `NullLog`.

### 5.2 `orchestrator.Ctx`

Two new fields:

```
model_catalog: Catalog | None = None
run_started_at: datetime | None = None
```

Tests that build a `Ctx` without them keep working.

## 6. The gateway

### 6.1 State and server

- `State.catalog: Catalog = field(default_factory=lambda: Catalog.unavailable("no snapshot"))`.
- `server.load_state` sets `catalog=load_catalog(run_dir)`.

### 6.2 `gateway/model_info.py`

Pure functions. They take the catalogue, the parsed `/api/available_models`
payload, the policy and the request, and return the gateway's `Out`
models.

```
Status = Literal["ready", "blocked_by_policy", "not_offered", "not_connected"]

@dataclass(frozen=True)
class Offered:
    """What the Kiln server offers: from one /api/available_models payload."""
    pairs: dict[tuple[str, str], dict[str, Any]]   # (provider, model) -> the server's model dict
    connected: frozenset[str]                       # provider ids in the payload
    order: list[tuple[str, str]]                    # the server's order

    @classmethod
    def from_payload(cls, payload: Any) -> "Offered"

def status(offered: Offered, policy: ModelPolicy | None, provider: str, model: str) -> Status

def list_models(catalog, offered, policy, req: m.ListModelsArgs) -> m.ModelList
def model_details(catalog, offered, policy, req: m.ModelDetailsArgs) -> m.ModelDetailsView
def list_providers(catalog, offered, policy, req: m.ListProvidersArgs) -> m.ProviderList
```

`status`:

```
if (provider, model) in offered.pairs:
    if policy is None or policy.decide(provider, model).allowed: return "ready"
    return "blocked_by_policy"
if provider in offered.connected: return "not_offered"
return "not_connected"
```

#### The merged view

`_merged(catalog, offered) -> list[CatalogModel]` gives one list that the
three functions share:

1. A deep copy of each catalogue model, in the catalogue's order.
2. For each offered pair that matches an entry: overlay the server's
   `SERVER_FIELDS` (`thinking_levels` from `available_thinking_levels`,
   `default_thinking_level`, `structured_output_mode`) onto the copied
   entry. The probes and the model preflight check these against the
   server, and the installed Kiln can run with another remote config. For
   each offered pair that matches no entry: when the model is in the
   catalogue, add an entry built from the server's dict
   (`_entry_from_server`) to that model. Otherwise collect it.
3. The collected pairs, grouped by model id, become new `CatalogModel`s
   built from the server's dicts, sorted by model id, after the catalogue's
   models. Their `name` is the server's `name`. Their catalogue fields are
   null.

`_entry_from_server(provider, d)` reads `supports_function_calling`,
`supports_structured_output`, `supports_vision`, `supports_doc_extraction`,
`available_thinking_levels` (values in order), `default_thinking_level`,
`structured_output_mode`, `untested_model`, `deprecated` and the
`suggested_for_*` flags.

The merge runs on each call. It is about 300 models and 800 entries, so the
cost is small.

#### `list_models`

1. `rows = _merged(...)`.
2. For each row, keep the entries that pass: the status is `ready`, or
   `include_not_available`; not deprecated, or `include_deprecated`;
   `provider` when given; `tools`, `vision`, `reasoning` when given (FS
   5.1); `max_input_price` when given. An entry with a null value for a
   filter it is tested on does not pass.
3. Drop a row with no entry left.
4. Row filters: `family` (equal); `open_weights` (equal, null does not
   pass); `tags` (any requested tag in the row's tags or in a kept entry's
   tags).
5. Sort (FS 5.4). Python's sort is stable, and the rows are already in the
   `featured` base order, so `price` and `newest` sort by their key and keep
   the base order for ties. The `featured` key is
   `(featured_rank is None, featured_rank or 0)`.
6. `total = len(rows)`. Page with `offset` and `limit`.
7. Map to `m.ModelRow`. `thinking` is true when a kept entry has
   `thinking_levels` or `reasoning_capable`.

`reasoning` filter on an entry: `bool(thinking_levels) or
reasoning_capable is True`.

`price` key: the lowest `input_price + output_price` among kept entries
where both are known; `math.inf` when none.

`newest` key: `release_date` descending, null last. Use the key
`(release_date is None, _neg(release_date))`, where `_neg` turns the ISO
date into a negative ordinal.

#### `model_details`

- Find the row in `_merged(...)` by exact `model`. When there is none,
  raise `GatewayError("Model not found: <id>. Use list_models to find
  models.")`.
- With `provider`, keep only that entry. When the row has no such entry,
  raise `GatewayError("<model> has no entry for provider <provider>.")`.
- Each entry gets its status. No entry is dropped for its status or for
  `deprecated`.

#### `list_providers`

- The ids: `catalog.providers`, then the providers of the catalogue's
  entries, then `offered.connected`, without duplicates.
- `status`: `connected` when in `offered.connected`.
- `ready_models`: the count of offered pairs of that provider whose status
  is `ready`.
- `name`, `tags`, `notes` from `catalog.provider(id)`, else null or empty.
- Without `include_not_available`, only connected providers.
- Order: `ready_models` descending, then provider id.

### 6.3 The gateway's models (`gateway/models.py`)

```
class ListModelsArgs(In):
    family: str | None = None
    provider: str | None = None
    tags: list[str] | None = None
    tools: bool | None = None
    vision: bool | None = None
    reasoning: bool | None = None
    open_weights: bool | None = None
    max_input_price: float | None = Field(default=None, ge=0)
    include_not_available: bool = False
    include_deprecated: bool = False
    sort: Literal["featured", "price", "newest"] = "featured"
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

class ModelDetailsArgs(In):
    model: str
    provider: str | None = None

class ListProvidersArgs(In):
    include_not_available: bool = False

class CatalogState(Out):
    kiln: Literal["ok", "none"]
    models_dev: Literal["ok", "none"]
    fetched_at: str | None

class ProviderEntryRow(Out):
    provider: str
    status: str
    input_price: float | None
    output_price: float | None
    tags: list[str]
    deprecated: bool

class ModelRow(Out):
    model: str
    name: str
    family: str | None
    featured_rank: int | None
    tags: list[str]
    release_date: str | None
    context_window: int | None
    open_weights: bool | None
    thinking: bool
    providers: list[ProviderEntryRow]

class ModelList(Out):
    catalog: CatalogState
    total: int
    models: list[ModelRow]

class ProviderEntryDetails(Out):
    provider: str
    status: str
    model_id: str | None
    deprecated: bool
    untested: bool
    input_price: float | None
    output_price: float | None
    cache_read_price: float | None
    cache_write_price: float | None
    context_window: int | None
    max_output_tokens: int | None
    tags: list[str]
    supports_tools: bool | None
    supports_structured_output: bool | None
    supports_vision: bool | None
    supports_doc_extraction: bool | None
    reasoning_capable: bool | None
    thinking_levels: list[str]
    default_thinking_level: str | None
    structured_output_mode: str | None
    takes_temperature: bool | None
    suggested_for: list[str]

class ModelDetailsView(Out):
    catalog: CatalogState
    model: str
    name: str
    family: str | None
    featured_rank: int | None
    editorial_notes: str | None
    agent_notes: str | None
    tags: list[str]
    release_date: str | None
    release_date_match: str | None
    knowledge_cutoff: str | None
    open_weights: bool | None
    context_window: int | None
    max_output_tokens: int | None
    thinking: bool
    providers: list[ProviderEntryDetails]

class ProviderRow(Out):
    provider: str
    name: str | None
    status: Literal["connected", "not_connected"]
    ready_models: int
    tags: list[str]
    notes: str | None

class ProviderList(Out):
    catalog: CatalogState
    providers: list[ProviderRow]
```

`ModelView` is removed with `list_available_models`, unless another module
uses it. The coding agent checks with a search and keeps it if so.

### 6.4 `gateway/ops.py`

```
async def _offered(self) -> Offered:
    return Offered.from_payload(await self.call("GET", "/api/available_models"))

async def list_models(self, req: m.ListModelsArgs) -> m.ModelList:
    return model_info.list_models(self.s.catalog, await self._offered(), self.s.policy, req)
# get_model_details and list_providers the same way
```

- `list_available_models` is removed.
- `OPS`: three new entries, with these summaries (they become the tool
  descriptions):
  - `list_models`: "Models with price, context, release date and tags,
    one row per model. Only ready models unless include_not_available."
  - `get_model_details`: "Everything about one model: notes for agents,
    each provider's prices, capabilities and thinking levels."
  - `list_providers`: "Providers with their status, ready model count,
    tags and notes."
- `ROLE_OPS`: in `batch_plan`, `create_experiment_rc` and `probe`, replace
  `"list_available_models"` with the three operations. Use a tuple
  `_MODELS = ("list_models", "get_model_details", "list_providers")`.
- The answer cap applies to the three, as to every operation.

## 7. The old-model detector

### 7.1 Inputs (`smells/model.py`)

```
@dataclass(frozen=True)
class ModelFacts:
    provider: str
    model: str
    name: str
    release_date: date | None

class DetectorInputs:  # two new fields, both with defaults
    model: ModelFacts | None = None
    run_date: date | None = None
```

In `smells/run._collect`:

- `provider, model` come from `properties` (`model_provider_name`,
  `model_name`).
- `facts = ModelFacts(provider, model, row.name, release_date)` when
  `ctx.model_catalog` has the model, else
  `ModelFacts(provider, model, model, None)`. `release_date` is parsed with
  `date.fromisoformat`. A bad value gives None.
- `run_date = ctx.run_started_at.date()` when it is set, else None.
- No new call to Kiln.

### 7.2 `smells/thresholds.py`

```
OLD_MODEL_MONTHS = 8
# A house default, set by the project owner on 2026-11-13. No published
# value. A model line usually has a newer release within this time.
```

### 7.3 `smells/detectors/old_model.py`

```
def _add_months(d: date, months: int) -> date      # day clamped to the month's last day
def _whole_months(start: date, end: date) -> int   # floor

def run(inputs: DetectorInputs) -> list[Detection]:
    facts, today = inputs.model, inputs.run_date
    if facts is None or facts.release_date is None or today is None:
        return []
    if not _add_months(facts.release_date, OLD_MODEL_MONTHS) < today:
        return []
    months = _whole_months(facts.release_date, today)
    return [Detection(
        smell="old-model", detector="old_model", scope="harness",
        fact=(f"The baseline's model, {facts.name} ({facts.model}), was released"
              f" on {facts.release_date.isoformat()}, {months} months before"
              f" this run. The reference is {OLD_MODEL_MONTHS} months."),
        value=float(months), reference=float(OLD_MODEL_MONTHS),
        reference_source="smells.thresholds.OLD_MODEL_MONTHS: a house default",
    )]

DETECTOR = Detector(id="old_model", smell="old-model", run=run)
```

`detectors/__init__.py` adds `old_model.DETECTOR` to `DETECTORS`.

The fact stays under 300 characters. A friendly name and a model id are each
under 80 characters in Kiln's file today. The detector cuts `name` at 80
characters to be safe.

### 7.4 The smell document

`agent_files/skills/kiln-harness-tuner-knowledge/references/smells/old-model.md`,
with the front matter of the other smell documents:

```
title: Old model
description: The baseline's model was released more than the reference time before the run. A code detector reads the release date, and the detection gives the age, the reference and its source.
reviewed: <date of the phase>
applies_to: all
detected_by:
  - code
detectors:
  - old_model
```

The body follows FS 8.3 and the sections of `very-long-prompt.md`. The
coding agent reads `.agents/skills/knowledge-maintenance/SKILL.md` first,
runs `python -m kiln_harness_tuner.knowledge check`, and runs `build` to
update the index. The remedies link to `levers/model.md` and to the
playbooks and techniques that exist when the phase runs.

## 8. Text and docs

As FS 10. Two notes:

- The test that each operation name is in the gateway skill
  (`tests/test_agent_templates.py`) covers the three names once they are in
  `OPS`.
- `docs/architecture.md`: a short section "The model catalogue" next to
  the model policy, and the snapshot file in the run directory's file list.

## 9. Tests

All offline. No live server, no network.

**Fixtures** in `tests/fixtures/model_catalog/`:

- `kiln_config.json`: about 8 Kiln models, cut from the real file. Include:
  a featured model on two providers; a model on OpenRouter only; a model
  with a deprecated entry; a model with no models.dev match; a model that
  only the loose match dates; a fine-tune-style provider
  (`kiln_fine_tune`); and the new fields (`agent_notes`, `tags`,
  `provider_list`) on some entries.
- `models_dev.json`: the matching slice of models.dev, with one entry that
  has a negative price and one with a string limit.

`tests/test_model_catalog.py`:

- `test_the_fixture_builds_with_prices_limits_and_dates`
- `test_price_is_matched_on_the_exact_pair`
- `test_model_facts_follow_the_provider_preference`
- `test_the_loose_match_dates_a_model_with_no_exact_match`
- `test_the_loose_match_takes_the_earliest_date`
- `test_a_short_loose_key_is_not_used`
- `test_the_published_models_dev_id_wins_over_the_built_in_map`
- `test_a_provider_without_models_dev_is_not_joined`
- `test_a_bad_price_and_a_bad_limit_are_dropped`
- `test_a_bad_model_is_dropped_and_the_rest_kept`
- `test_a_bad_provider_entry_is_dropped_and_its_model_kept`
- `test_an_unknown_field_is_ignored`
- `test_tags_are_cleaned_deduplicated_and_capped`
- `test_a_long_note_is_cut`
- `test_a_kiln_file_without_model_list_gives_kiln_none`
- `test_no_models_dev_gives_null_prices_and_kiln_data`
- `test_fetch_turned_off_makes_no_request` (respx: no route called)
- `test_fetch_timeout_gives_a_reason_and_never_raises` (respx)
- `test_fetch_non_2xx_gives_a_reason` (respx)
- `test_fetch_over_the_byte_cap_gives_a_reason` (respx)
- `test_write_then_load_round_trips`
- `test_an_empty_or_missing_snapshot_loads_as_unavailable`

`tests/test_gateway_model_info.py` (the pure functions):

- `test_status_ready_blocked_not_offered_not_connected`
- `test_list_models_returns_ready_entries_only_by_default`
- `test_include_not_available_lists_every_status`
- `test_deprecated_entries_are_left_out_by_default`
- `test_entry_filters_remove_entries_and_empty_rows`
- `test_row_filters_family_open_weights_and_tags`
- `test_a_tag_on_a_provider_entry_matches_the_row`
- `test_sort_featured_price_newest_and_ties`
- `test_paging_and_total`
- `test_a_pair_the_catalogue_lacks_is_listed_with_null_fields`
- `test_a_model_the_catalogue_lacks_is_listed_after_the_catalogue`
- `test_an_unavailable_catalogue_serves_the_server_list`
- `test_model_details_lists_every_entry_with_its_status`
- `test_model_details_refuses_an_unknown_model`
- `test_model_details_with_a_provider_keeps_one_entry`
- `test_list_providers_counts_ready_models_and_orders_them`
- `test_list_providers_hides_unconnected_by_default`

`tests/test_gateway.py`:

- `TestRoles`: the three roles are served the three operations, and no role
  is served `list_available_models`.
- One test that `load_state` reads the snapshot from the run directory.

`tests/test_smells.py`:

- `test_old_model_fires_after_eight_months`
- `test_old_model_is_silent_at_exactly_eight_months`
- `test_old_model_is_silent_without_a_date`
- `test_old_model_clamps_the_day_at_month_end` (released 31 May, run on
  1 Feb)
- `test_collect_reads_the_release_date_from_the_catalogue`

`tests/test_cli*.py` and `tests/test_cli_reference.py`: the flag; the
snapshot is written in the run directory; the flag makes no request.

`tests/test_knowledge*.py`: the existing checks cover the new smell
document and its detector.

## 10. Error handling

| Where | Failure | What happens |
|---|---|---|
| Fetch | any | Never raises. The source is `none`, with a reason. One log line, one event. |
| Build | a bad model or entry | Dropped alone. No event for each one. The counts in the `model_catalog` event show the size. |
| Store | write fails (disk) | Raises. A run that cannot write its run directory fails at start-up today too. |
| Gateway load | missing or bad snapshot | `Catalog.unavailable("no snapshot")`. The tools still serve the server's list. |
| Gateway call | `/api/available_models` fails | The `KilnError` goes up as for every operation today. |
| Detector | raises | `run_detectors` catches it today (`smell_detector_failed`). |

## 11. Phases

The implementation plan has four phases:

1. The Kiln-side `project_overview.md` (FS 9).
2. The catalogue package, the fetch at start-up, the flag and the snapshot
   (sections 2 to 5).
3. The gateway operations, the roles, and the skill and doc text
   (sections 6 and 8).
4. The old-model detector, its smell document, and the backlog entries
   (section 7).
