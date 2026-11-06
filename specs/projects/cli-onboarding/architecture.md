---
status: complete
---

# Architecture: CLI onboarding

The functional spec (FS) says what the user sees. This document says how it is built. Section numbers like "FS §5" point into `functional_spec.md`.

## 1. Shape

All new code goes in one subpackage, `kiln_harness_tuner/onboarding/`. It runs **before** the existing start-up and returns the parsed flags with `project_id`, `task_id` and `project_path` filled in. `_run` and everything after it see a complete set of flags, the same as today.

```
main(argv)
  parse flags, then validate_args             (cli.py)
  prepare_start(args, raw_argv, prog)         (onboarding/flow.py)  ← new, synchronous
      kiln check → demo → select → print command → readiness → keys
  asyncio.run(_run_with_signals(args))        (unchanged)
```

**Why the prompts are synchronous.** `asyncio.run` installs its own SIGINT handler. While the event loop thread is blocked in `input()`, that handler only cancels the main task, and `input()` keeps waiting. The first Ctrl-C would do nothing visible. So the flow driver is plain synchronous code: prompts use `input()`, and Ctrl-C raises `KeyboardInterrupt` normally.

**Each batch of Kiln reads** runs in its own short `asyncio.run` through a runner. No prompt ever happens inside one. That keeps `KilnClient`, `MemoryWriter` and setup's helpers usable as they are.

```python
KilnRunner = Callable[[Callable[[KilnApi], Awaitable[T]]], T]

def kiln_runner(host: str) -> KilnRunner:
    """Runs `fn` with a fresh KilnClient in its own asyncio.run, then closes it."""
```

Tests pass a runner that calls `fn` on a fake.

## 2. Modules

| Module | Contents |
|---|---|
| `onboarding/__init__.py` | Re-exports `prepare_start`, `OnboardingError`, `OnboardingStopped`. |
| `onboarding/errors.py` | The two exceptions (§4). |
| `onboarding/terminal.py` | `Terminal` protocol, `StdTerminal`, `Problem`, `resolve_problem` (§3). |
| `onboarding/kiln_check.py` | Ping and the API check (§6). |
| `onboarding/choose.py` | Project and task lists, the picker, the selection rules, and the path check (§7). |
| `onboarding/readiness.py` | The readiness check (§9). |
| `onboarding/keys.py` | The providers a run needs, and the key check (§10). |
| `onboarding/demo.py` | The packaged demo: claim a folder, copy, fresh ID, import (§11). |
| `onboarding/flow.py` | `prepare_start`, `kiln_runner`, `rerun_command` (§5, §8). |
| `onboarding/kiln_urls.py` | The Kiln web UI links, one function each: `create_project`, `create_task`, `run_configs`, `evals`, `providers` (§16). |

Changes outside the package:

| File | Change |
|---|---|
| `cli.py` | `PROG` constant. The three flags become optional. `--demo-project`. `validate_args`. `main` calls `prepare_start` and catches the two new exceptions. `read_fence_for` denies the packaged demo. See §12. |
| `setup.py` | Two pure helpers pulled out of existing code, with no behavior change: `choose_baseline_rc` and `active_eval_rows` / `measurable_evals` (§9, §10). |
| `orchestrator.py` | `_active_evals` calls `measurable_evals`, then keeps its `_new_eval_refused` filter. |
| `examples/moderation/Forum Moderation/` | Moved to `src/kiln_harness_tuner/demo_project/Forum Moderation/` (§13). |

`onboarding/` never imports `cli.py`, so there is no import cycle. `prog` comes in as an argument.

## 3. Terminal and prompts

```python
class Terminal(Protocol):
    interactive: bool
    def say(self, text: str) -> None: ...      # stdout
    def warn(self, text: str) -> None: ...     # stderr, prefixed "<prog>: "
    def ask(self, prompt: str) -> str: ...     # EOF → OnboardingStopped(1); Ctrl-C → OnboardingStopped(130)
    def open_url(self, url: str) -> None: ...  # webbrowser.open(url), then say(url)

class StdTerminal:
    def __init__(self, prog: str): ...
    # interactive = sys.stdin.isatty() and sys.stdout.isatty()
    # ask(): prints a newline before raising, so the shell prompt starts clean.
```

Plain `print`/`input`, no `rich`: the picker is a few numbered lines, and plain I/O is what the scripted test terminal replays.

```python
@dataclass(frozen=True)
class Problem:
    message: str                 # multi-line; no prompt line
    open_url: str | None = None
    open_label: str | None = None  # "kiln.tech", "AI Providers", "the first link"

class Resolution(Enum):
    OK = "ok"
    PICK_TASK = "pick_task"

def resolve_problem(
    check: Callable[[], Problem | None], terminal: Terminal, *, offer_pick_task: bool = False
) -> Resolution:
```

`resolve_problem` runs `check()`. While it returns a `Problem`:

- **Not interactive:** raise `OnboardingError(problem.message)`.
- **Interactive:**
  1. `say(message)`, then ask with `[r]etry  [o]pen <label>  [t]ask: pick another  e[x]it: `. Each option shows only when it applies (`open_url` set, `offer_pick_task`).
  2. Normalise the answer with `strip().lower()`:
     - `""` or `r`: run `check()` again.
     - `o`: `open_url`, then ask again without re-checking.
     - `t`: return `PICK_TASK`.
     - `x`: raise `OnboardingStopped(1)`.
     - Anything else: ask again.

When `check()` returns `None`, it returns `OK`.

## 4. Errors and exit codes

```python
class OnboardingError(Exception):
    """A start-up problem the user must fix. The message is complete; main prints
    '<prog>: <message>' to stderr and exits 1."""

class OnboardingStopped(Exception):
    def __init__(self, exit_code: int): ...   # 1 = x or EOF, 130 = Ctrl-C
```

`main` prints `<prog>: stopped.` to stderr and returns `exit_code`.

`prepare_start` wraps its whole body in `except KeyboardInterrupt: raise OnboardingStopped(130)`. That covers a Ctrl-C during an `asyncio.run` read too: since 3.11, `Runner.run` re-raises `KeyboardInterrupt` after cancelling.

Other exceptions keep today's handling. An unexpected `KilnAPIError` (HTTP 500 from `/api/projects`, say) or a `SetupPreconditionError` from a drifted response shape is already in `main`'s except tuple, so it prints one line and no traceback.

## 5. `prepare_start`

```python
def prepare_start(
    args: argparse.Namespace,
    argv: Sequence[str],
    *,
    prog: str,
    terminal: Terminal | None = None,     # default StdTerminal(prog)
    runner: KilnRunner | None = None,     # default kiln_runner(args.kiln_host)
    home: Path | None = None,             # default Path.home(); the demo folder hangs off it
) -> argparse.Namespace:
```

```
host = args.kiln_host.rstrip("/")
resolve_problem(lambda: run(check_kiln(host)) → print its warning once, return its problem)
filled = False
if args.demo_project:
    demo = make_demo(...)                               §11
    args.project_id, args.task_id, args.project_path = demo.project_id, demo.task_id, str(demo.folder)
    filled = True
while True:
    sel = select(args, terminal, runner, prog, host)    §7; raises OnboardingError
    args.project_id, args.task_id, args.project_path = sel.project.id, sel.task.id, str(sel.project_path)
    if filled or sel.filled:
        terminal.say("To run this again without the questions:\n  " + rerun_command(prog, argv, ...))
    r = resolve_problem(lambda: run(readiness_problem(...)), terminal, offer_pick_task=sel.task_picked)
    if r is Resolution.PICK_TASK:
        args.task_id = None; filled = True; continue   # back to the task picker, same project
    break
policy = load_model_policy(args.model_policy)            # cli's loader, passed in as a callable
resolve_problem(lambda: run(key_problem(...)), terminal)
return args
```

- `load_model_policy` lives in `cli.py`. It is passed in as `policy_loader: Callable[[str | None], ModelPolicy | None]`, so `onboarding` doesn't import `cli`. `_run` still loads the policy itself, as today. The file is read twice; that's harmless, and it keeps `_run` untouched.
- `readiness_problem` returns `(Problem | None, warnings)`. The driver prints the warnings once per check, through `terminal.warn`.

## 6. Kiln check (`kiln_check.py`)

```python
KILN_DOWNLOAD_URL = "https://kiln.tech"
PING_TIMEOUT_SECS = 5.0
OPENAPI_TIMEOUT_SECS = 10.0

@dataclass(frozen=True)
class RequiredApi:
    label: str
    path: str | None = None
    schema: str | None = None
    field: str | None = None

REQUIRED_APIS = (
    RequiredApi("the memory API", path="/api/projects/{project_id}/memories"),
    RequiredApi("the eval-job API", path="/api/jobs/evals/run"),
    RequiredApi("run-config provenance", schema="CreateTaskRunConfigRequest", field="provenance"),
)

@dataclass(frozen=True)
class KilnCheck:
    problem: Problem | None
    warning: str | None

async def check_kiln(kiln: KilnApi, host: str) -> KilnCheck
def missing_apis(openapi: dict) -> list[str]   # pure; labels in REQUIRED_APIS order
```

1. `kiln.call("GET", "/ping", timeout_secs=PING_TIMEOUT_SECS, retries=False)`:
   - `KilnTransportError`: the not-running problem (FS §4.1).
   - `KilnAPIError`: the not-Kiln problem, naming the status.
   - A body other than `"pong"`: the not-Kiln problem, naming "an unexpected answer".
2. `kiln.call("GET", "/openapi.json", timeout_secs=OPENAPI_TIMEOUT_SECS, retries=False)`:
   - A `KilnError`, or a non-dict body: `warning`, no problem.
   - Otherwise `missing_apis`. A schema is read from `openapi["components"]["schemas"][schema]["properties"]`, treating each missing level as absent.
   - Missing APIs: the too-old problem.
   - `info.version` is quoted unless it's missing, empty or `unknown`.

Every problem from this check has `open_url=KILN_DOWNLOAD_URL`, `open_label="kiln.tech"`.

## 7. Choosing (`choose.py`)

```python
@dataclass(frozen=True)
class ProjectRow:
    id: str
    name: str
    path: str | None   # Kiln's project.kiln path

@dataclass(frozen=True)
class TaskRow:
    id: str
    name: str

@dataclass(frozen=True)
class Selection:
    project: ProjectRow
    task: TaskRow
    project_path: Path
    filled: bool        # True when any of the three flags was filled here
    task_picked: bool   # True only when the user chose from 2+ tasks

async def fetch_projects(kiln) -> list[ProjectRow]   # GET /api/projects
async def fetch_tasks(kiln, project_id) -> list[TaskRow]   # GET /api/projects/{id}/tasks
```

- **Parsing:** rows that aren't dicts, or have no `id`, are dropped. `name` falls back to the ID. A non-list body raises `SetupPreconditionError(unreadable_response_message(path, body))`, so drift reads the same as setup's.
- **`/api/projects` sets `path` explicitly** for each project (`get_projects` in Kiln's `project_api.py`). The single-project GET may not, so only the list is used.

### 7.1 `choose_one`

```python
def choose_one(rows, *, noun, heading, flag, where, terminal, prog) -> tuple[row, bool]:
```

- 1 row: `say(f"Using {noun} {name} ({id}), the only {noun} {where}.")`, return `(row, False)`.
- 2+ rows, not interactive: raise `OnboardingError(f"{flag} is needed when not run interactively. {heading}:\n" + listing)`. The listing has one line per row: `  {id}  {name}`.
- 2+ rows, interactive: print `heading:` and the numbered rows, then ask `"{Noun} [1-N, x to exit]: "` until the answer is an integer in range. `x` raises `OnboardingStopped(1)`. Returns `(row, True)`.
- Callers handle 0 rows, because each has its own message (FS §5.2).

### 7.2 `select`

`select` implements the FS §5.1 table, in this order:

1. **Project:**
   - `--project-id` given: find it in `fetch_projects`. If it isn't there, `OnboardingError("Kiln has no project {id}. Projects in Kiln:\n…")`.
   - Else, `--project-path` given: find the row whose `Path(path).parent.resolve()` equals the flag's `expanduser().resolve()`. If none, `OnboardingError` saying the folder isn't a project in Kiln, then the listing, then "import it in Kiln first".
   - Else, `--task-id` given: run `fetch_tasks` for every project, keep the projects that contain the ID, and `choose_one` among them. If none, `OnboardingError("No project in Kiln has task {id}. …")`. (Two demo copies share a task ID.)
   - Else: `choose_one` over all projects. With 0 projects, the "no projects" error, which names `<host>/settings/create_project` and `<prog> --demo-project`.
2. **Task:**
   - `--task-id` given: it must be in that project's tasks, else `OnboardingError("Project {name} has no task {id}. Tasks in {name}:\n…")`.
   - Else: `choose_one` over the tasks. With 0 tasks, the "no tasks" error with `<host>/settings/create_task/<project_id>`.
3. **Path** (FS §5.4):
   - Kiln's folder is `Path(row.path).parent`, when `row.path` is set.
   - Flag given and Kiln has a path: the two must match after `expanduser().resolve()`, else the mismatch `OnboardingError`.
   - Flag given and Kiln has no path: use the flag.
   - Flag missing and Kiln has no path: `OnboardingError` asking for `--project-path`.
   - Flag missing and Kiln has a path: use Kiln's.

`filled` is True when any of the three flags was None before `select` ran. `task_picked` is the flag that `choose_one` returned for the task.

## 8. `rerun_command`

```python
def rerun_command(prog, argv, *, parser_long_options, project_path, project_id, task_id) -> str:
```

1. Drop from `argv` every occurrence of `--demo-project`, `--project-id`, `--task-id` and `--project-path`, in both the `--flag value` and `--flag=value` forms. Matching follows argparse's abbreviations (`allow_abbrev` is on):
   - A token `--x` (before any `=`) resolves to the long option it equals.
   - Otherwise it resolves to the single long option it is a prefix of.
   - When the resolved option is one of the four, drop the token, plus the next token if it was the space-separated form. `--demo-project` takes no value.
   - Stop scanning at `--`.
2. Return `shlex.join([prog, "--project-path", path, "--project-id", pid, "--task-id", tid, *rest])`.

`cli.main` passes `parser_long_options` as the parser's `--` option strings, so this needs no private argparse API.

## 9. Readiness (`readiness.py`) and the setup helpers

New pure helpers in `setup.py`:

```python
def active_eval_rows(eval_rows: list[Any], spec_rows: list[Any]) -> list[dict[str, Any]]:
    """Dicts with an id, whose spec is absent or has status 'active'."""

def measurable_evals(eval_rows: list[Any], spec_rows: list[Any]) -> list[tuple[dict[str, Any], str]]:
    """active_eval_rows that carry a current_config_id, paired with it — the
    loop's measurement set before any per-run filter."""
```

`orchestrator._active_evals` becomes:

1. Read and unwrap the evals as today, including the load-error warning.
2. Read the specs with `or []`.
3. Return `[(e, c) for e, c in measurable_evals(evals, specs) if not _new_eval_refused(ctx, str(e["id"]))]`.

The status map is built as today (`s.get("eval_id")`), and specs that aren't dicts are skipped. The one difference is that an eval row with no `id` is now skipped instead of raising `KeyError`.

```python
async def readiness_problem(kiln, *, host, project_id, task_id, task_name) -> tuple[Problem | None, list[str]]:
```

- **Reads:** `{base}/run_configs`, `{base}/evals` (through `eval_rows_from_response`) and `{base}/specs` (through `_require_rows`), in one `asyncio.gather`.
- **Problem lines,** in this order:
  - `_usable_run_configs(rows)` is empty: the run-config line, with `<host>/optimize/<pid>/<tid>`.
  - `measurable_evals` is empty, and `active_eval_rows` is non-empty: the "no default eval config" line, naming those evals in quotes, with `<host>/specs/<pid>/<tid>`.
  - `measurable_evals` is empty, and so is `active_eval_rows`: the "no eval" line, with the same link.
- `open_url` is the first line's link, with `open_label="the first link"`.
- A non-zero `load_error_count` adds a warning.

## 10. API keys (`keys.py`)

```python
NO_KEY_PROVIDERS = frozenset({"ollama", "docker_model_runner", "openai_compatible",
                              "kiln_custom_registry", "kiln_fine_tune"})

@dataclass(frozen=True)
class Need:
    provider: str
    used_by: str
    from_policy: bool = False

async def needed_providers(kiln, *, project_id, task_id, project_path: Path,
                           policy: ModelPolicy | None) -> list[Need]
async def key_problem(kiln, *, host, needs: list[Need]) -> Problem | None
def simulated_user_providers(project_path: Path, task_id: str) -> set[str]   # sync, disk
```

### 10.1 `needed_providers`

1. **Baseline.**
   - `memory = MemoryWriter(kiln, project_id=..., task_id=...)`. If `await memory.find_baseline_record()` is set, `rc_id = memory.baseline_rc_id(await memory.require_baseline_record())`. A `BaselineRecordMissingError` sets `rc_id = None`; the start-up raises it properly later.
   - Else, `rc_id = choose_baseline_rc(task, _usable_run_configs(rows)).rc_id`, reading `GET {base}` and `GET {base}/run_configs`. With no run configs there is no baseline; readiness has already refused that.
   - The new pure `choose_baseline_rc(task, run_configs) -> BaselineChoice(rc_id, rule, unresolvable_default)` is pulled out of `seed_baseline_if_missing`, which then calls it. No behavior change.
   - Find the row by ID. `run_config_properties.model_provider_name`, when it's a string, gives `Need(provider, f'the task\'s run config "{name}"')`.
2. **Judges.**
   - For each `(eval_def, config_id)` in `measurable_evals`, `GET {base}/evals/{eid}/eval_config/{cid}`. A 404 or 410 is skipped, as in setup's `_read_config_properties`.
   - The provider is the root `model_provider` when it's a string (a legacy judge), else `properties.model_provider` when `properties.type == "llm_judge"`.
   - `used_by = f'eval "{name}"\'s judge'`.
3. **Simulated user.**
   - Only when the task's `turn_mode == "multiturn"` (from the `GET {base}` read in step 1).
   - Run `simulated_user_providers(project_path, task_id)`: for each `quarantine.task_dirs(project_root, task_id)`, glob `eval_inputs/*/eval_input.kiln`, `json.load` each, and collect `data.drive_config.model_provider` (Kiln's `MultiTurnSyntheticEvalInputData.drive_config`). Unreadable or odd files are skipped.
   - Only `drive_config` is read, never the input text or the labels.
   - `used_by = "the simulated user in the task's eval inputs"`.
4. **Policy.** For each rule in `policy.allow + policy.exceptions` whose `provider` has no `*`: `Need(provider, "--model-policy", from_policy=True)`.

### 10.2 `key_problem`

1. `available = {row["provider_id"] for row in GET /api/available_models if dict with str provider_id}`.
2. `missing` is the needs whose provider is not in `NO_KEY_PROVIDERS` and not in `available`, grouped by provider in first-seen order, with their `used_by` texts joined by `", "`.
3. With no missing providers, return `None`.
4. Display names come from `GET /api/settings/available_providers` (`{id: name}`). Any `KilnError` there gives `{}`, and the ID is shown alone.
5. Build the FS §8.2 message. A policy need adds ", or remove it from --model-policy". Set `open_url=f"{host}/settings/providers"`, `open_label="AI Providers"`.

## 11. Demo (`demo.py`)

```python
DEMO_DIR = "demo_project"
DEMO_PROJECT = "Forum Moderation"
DEMO_TASK_ID = "424242783061"
KILN_PROJECTS_DIR = "Kiln Projects"     # Kiln's default_project_path(): ~/Kiln Projects

def demo_source() -> Traversable                   # files("kiln_harness_tuner") / DEMO_DIR / DEMO_PROJECT
def demo_fence_root() -> Path                      # Path(str(files("kiln_harness_tuner") / DEMO_DIR))
def claim_folder(parent: Path, name: str) -> Path
def new_project_id(taken: set[str]) -> str
def write_copy(source: Traversable, dest: Path, *, project_id: str, name: str) -> None

@dataclass(frozen=True)
class DemoProject:
    folder: Path
    project_id: str
    task_id: str

def make_demo(*, terminal, runner, home: Path) -> DemoProject
```

- **`claim_folder`**
  1. `parent.mkdir(parents=True, exist_ok=True)`.
  2. For `n = 0, 1, 2, …`: `candidate = parent / (name if n == 0 else f"{name}-{n}")`, then `candidate.mkdir()`.
  3. `FileExistsError` means the next `n`; a file, a folder or a dangling link all raise it. Return the first folder created.
- **`new_project_id`:** `str(uuid.uuid4().int)[:12]`, Kiln's own `generate_model_id` scheme, redrawn until it's not in `taken`.
- **`write_copy`**
  1. `with importlib.resources.as_file(source) as src: shutil.copytree(src, dest, dirs_exist_ok=True)`.
  2. Load `dest/"project.kiln"` as JSON, set `id` and `name`, and write it back with `indent=2, ensure_ascii=False` and a trailing newline.
- **`make_demo`**
  1. `taken = {p.id for p in run(fetch_projects)}`, then `folder = claim_folder(home / KILN_PROJECTS_DIR, DEMO_PROJECT)`.
  2. `try`:
     - `write_copy(...)`.
     - `run(kiln.call("POST", "/api/import_project", {"trusted": "true", "project_path": str(folder / "project.kiln")}))`.
     - `except` either the copy failing or the import refusing (`KilnError`, `OSError`): `shutil.rmtree(folder, ignore_errors=True)`, then raise `OnboardingError`. The message names the folder and the cause; for a `KilnAPIError` that's Kiln's `detail`.
  3. Use the returned project's `id` when it's a non-empty string, else ours. Print the FS §9.2 lines.

## 12. `cli.py`

```python
PROG = "kiln-harness-tuner"
```

- `build_parser`:
  - `prog=PROG`.
  - `--project-path`, `--project-id` and `--task-id` lose `required=True`; their help texts say what happens when each is left out.
  - New `--demo-project` (`store_true`).
- `validate_args(parser, args)`: `--demo-project` with any of the three raises `parser.error("--demo-project makes its own project: drop --project-id, --task-id and --project-path")`, exit 2.
- `main(argv)`:
  - `raw = sys.argv[1:] if argv is None else list(argv)`, then parse, then `validate_args`.
  - Then, in the existing `try`: `args = prepare_start(args, raw, prog=PROG, policy_loader=load_model_policy, parser_long_options=...)`, then `asyncio.run(...)`.
  - `except OnboardingStopped` returns its code after the `stopped.` line. `OnboardingError` joins the existing except tuple.
  - `main` calls `prepare_start` through the module attribute (`kiln_harness_tuner.cli.prepare_start`), so tests can monkeypatch it.
- `read_fence_for`: append `demo_fence_root()` to `deny`. On `main` the walk over the repo's Kiln projects already finds the packaged copy, so this adds nothing there. It matters once the package sits in site-packages.

## 13. Packaging the demo

- One commit with no content change: `git mv "examples/moderation/Forum Moderation" "src/kiln_harness_tuner/demo_project/Forum Moderation"`.
- Hatch builds the wheel from `src/kiln_harness_tuner`, data files included, minus anything `.gitignore` matches. Nothing in the demo is ignored: `*.py[codz]` doesn't match `scorer.py`.
- `.gitignore` gains `/src/kiln_harness_tuner/demo_project/*/tasks/**/runs/`, beside the `examples` rule, with its comment.
- **Once, in the phase:** `uv build --wheel`, then list the wheel and confirm `kiln_harness_tuner/demo_project/Forum Moderation/project.kiln` and the eval's `scorer.py` are in it. It's not a permanent test; the install branch's packaging smoke test covers wheels later.
- `tests/test_moderation_example.py`: `TASK` points at the package copy. `EXAMPLE` stays `examples/moderation` for `posts.jsonl` and `scorer.py`.
- `examples/moderation/README.md`: steps 1–3 become `kiln-harness-tuner --demo-project`, plus a note that the project's source is the package folder.
- `.agents/AGENTS.md`: the project-path sentence names the new location.

## 14. Docs

- **`docs/install.md`:**
  - §4: what the start-up Kiln check prints, the version check, and the wait or fail-fast behavior. The `curl /ping` check stays as a manual check.
  - §6: the start-up key check and its message. Fix both wrong claims (`/api/settings` returns `[hidden]`; `/api/available_models` lists only providers with keys). The manual check becomes `curl …/api/available_models` filtered for the provider's ID.
  - A new section after §6, "Try the demo".
- **`docs/your-own-task.md`:**
  - §1: say that §1.1 and §1.2 are checked at start-up, with links.
  - §5: the picker, the non-interactive error, and the rerun line.
  - "Every flag": the three flags as optional, and a row for `--demo-project`. `tests/test_docs.py` requires exactly one row per flag across `docs/`.

## 15. Tests

New `tests/onboarding_fakes.py`:

- **`ScriptedTerminal(inputs, interactive)`:** records `out`, `err` and `opened`. `ask` pops the next input, and raises `OnboardingStopped(1)` when the inputs run out, like EOF.
- **`RouteKiln(routes)`:** an async `call` that looks up `(method, path)` and returns the value, raises it if it's an exception, or calls it if it's callable. Every call is recorded with its query.
- **`runner_for(kiln)`:** a runner that does `asyncio.run(fn(kiln))`.

| File | Cases |
|---|---|
| `tests/test_onboarding_terminal.py` | `resolve_problem`: retry until OK; Enter = retry; `o` opens and asks again without re-checking; `t` only when offered; `x`, EOF and Ctrl-C give exit codes 1, 1, 130; not interactive raises `OnboardingError` with the message and no prompt. |
| `tests/test_onboarding_kiln_check.py` | Transport error → not running (interactive retry succeeds; not interactive fails, with no input read). 404 and non-`pong` → not Kiln. `missing_apis` with each API absent and all present. The version quoted or left out (`unknown`). Unreadable `openapi.json` → warning only. |
| `tests/test_onboarding_choose.py` | 0, 1 and 3 projects (interactive pick, invalid then valid input, not-interactive listing error). The same for tasks. Unknown project or task ID. A task ID in two projects. Project found by `--project-path`. Path mismatch error. Kiln path missing, with and without the flag. `filled` and `task_picked`. |
| `tests/test_onboarding_rerun.py` | Flags added and replaced. `--x=v` and `--x v` forms. Abbreviations (`--project-i`). `--demo-project` dropped. Quoting of spaces. |
| `tests/test_onboarding_readiness.py` | No run configs; no evals; active evals without a config; inactive-spec evals ignored; both lines at once; `load_error_count` warning; `measurable_evals` and `active_eval_rows` unit cases. |
| `tests/test_onboarding_keys.py` | Baseline from the memory record; from the default run config; from the most recent. Legacy and V2 judge providers. A code eval adds nothing. A simulated user read from `tmp_path` eval inputs (multiturn only). Policy exact providers added, `*` not. No-key providers skipped. Grouping, display names, the fallback to ID, and the policy fix suffix. Interactive retry after the key appears. Not interactive exits. |
| `tests/test_onboarding_demo.py` | `claim_folder`: first, `-1`, `-2`, and an existing file counts as taken. `new_project_id` avoids taken IDs. `write_copy` sets the ID and name and copies every file of the packaged demo. `make_demo`: the import call's query, a refusal removes the folder, a copy failure removes the folder. |
| `tests/test_onboarding_flow.py` | End to end on fakes: no flags → Kiln check → project picker → task picker → rerun line → readiness → keys → returns a filled `Namespace`. `--demo-project` → copy, import, rerun line, returned IDs; a second call gives `Forum Moderation-1`. `[t]` returns to the task picker. Ctrl-C → 130. |
| `tests/test_cli.py` | An autouse fixture patches `cli.prepare_start` to pass args through, so the existing tests stay offline. `test_missing_required_flag_errors` is replaced by: the three flags are optional; `--demo-project` with `--task-id` exits 2. `main` returns 1 on `OnboardingError` and the stop code on `OnboardingStopped`. `read_fence_for` denies `demo_fence_root()`. |
| `tests/test_setup.py` | `choose_baseline_rc` cases: default honoured, dangling default, most recent. The existing seeding tests stay green. |

All of these run offline under `uv run pytest`, with ruff clean.

## 16. Risks

- **Kiln UI routes** (`/optimize/…`, `/specs/…`, `/settings/providers`, `/settings/create_task/…`) come from Kiln's web UI source as of this writing. A Kiln rename breaks a link, not a check. They are kept in one place, as constants in `onboarding/kiln_urls.py`, the one extra module, so they're easy to update.
- **`/api/available_models` semantics** come from Kiln's current `get_available_models`. An older Kiln that listed every provider would make the key check pass wrongly. The model preflight still catches a hard failure on the baseline's model.
- **The task-only lookup** makes one tasks call per project. That's fine for the tens of projects a user has.
- **Merge with the install branch:**
  - `read_fence_for`'s signature changes there; the demo deny entry has to be re-added in its new body.
  - `PROG` becomes `harness-tuner`.
  - `.agents/AGENTS.md` moves to `agent_files/`. Git's rename detection carries the one-line edit.
