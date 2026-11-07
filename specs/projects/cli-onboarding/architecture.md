---
status: draft
---

# Architecture: CLI onboarding

The functional spec (FS) says what the user sees. This document says how it is built. Section numbers like "FS §5" point into `functional_spec.md`.

**Two parts.**
- §1–§16 are the design phases 1–5 built.
- §17–§25 are the guided start that phases 6–8 build on top of it. Where they disagree, Part 2 wins.
- §3 (the terminal), §5 (`prepare_start`) and §8 (`rerun_command`) are replaced by §18, §22 and §21.

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

```
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

*Replaced by §18 in phase 6.*

```
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

```
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

```
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

*Replaced by §22 in phase 7.*

```
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
def check_keys():                                         # every try: the first and each [r]
    policy = policy_loader(args.model_policy)             # cli's loader, passed in as a callable
    return run(needed_providers(..., policy=policy) → key_problem(..., needs))
resolve_problem(check_keys, terminal)
return args
```

- `load_model_policy` lives in `cli.py`. It is passed in as `policy_loader: Callable[[str | None], ModelPolicy | None]`, so `onboarding` doesn't import `cli`. `_run` still loads the policy itself, as today. The file is read twice; that's harmless, and it keeps `_run` untouched.
- `readiness_problem` returns `(Problem | None, warnings)`. The driver prints the warnings once per check, through `terminal.warn`.

## 6. Kiln check (`kiln_check.py`)

```
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

```
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

```
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

*Replaced by §21 in phase 7.*

```
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

```
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

```
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

```
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
   - `memory = MemoryWriter(kiln, project_id=..., task_id=...)`. `record = await memory.require_baseline_record()`; a `BaselineRecordMissingError` there means no record, and the next bullet applies. Otherwise `rc_id = memory.baseline_rc_id(record)`, and a `BaselineRecordMissingError` from it (no `rc_` tag) sets `rc_id = None`; the start-up raises it properly later.
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
4. **Policy.** For each rule in `policy.allow + policy.exceptions` whose `provider` has no glob character (`Rule.exact_provider`): `Need(provider, "--model-policy", from_policy=True)`.

### 10.2 `key_problem`

1. `available = {row["provider_id"] for row in GET /api/available_models if dict with str provider_id}`.
2. `missing` is the needs whose provider is not in `NO_KEY_PROVIDERS` and not in `available`, grouped by provider in first-seen order, with their `used_by` texts joined by `", "`.
3. With no missing providers, return `None`.
4. Display names come from `PROVIDER_NAMES` in `keys.py`, a copy of Kiln's `provider_name_from_id`. `GET /api/settings/available_providers` can't be used: it lists only providers whose keys are set. An ID not in the table is shown alone.
5. Build the FS §8.2 message. A policy need adds ", or remove it from --model-policy". Set `open_url=f"{host}/settings/providers"`, `open_label="AI Providers"`.

## 11. Demo (`demo.py`)

```
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

```
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
- `read_fence_for`: append `demo_fence_root().resolve()` to `deny`, resolved like the fence's other roots, so an install reached through a symlink still matches. On `main` the walk over the repo's Kiln projects already finds the packaged copy, so this adds nothing there. It matters once the package sits in site-packages.

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

# Part 2: The guided start (phases 6–8)

## 17. Shape

```
main(argv)
  parse flags, then validate_args                     (cli.py)
  prepare_start(args, raw_argv, prog, option_dests)   (onboarding/flow.py)
      header → Kiln check → demo → project/task/folder → readiness → keys
      → questions (guided) → keys again (policy changed) → summary → start prompt
  asyncio.run(_run_with_signals(args))                (unchanged)
```

- **New dependency:** `questionary>=2.1.1,<2.2`, which brings `prompt_toolkit` 3. It goes in `pyproject.toml` with a regenerated `uv.lock`. The range is one minor version because the terminal uses questionary internals (§25). `rich` is already a dependency.
- **The flow stays synchronous,** for the reason in §1: questionary's `unsafe_ask()` runs its own prompt_toolkit loop, and each prompt runs while no asyncio loop is running. Kiln reads keep their own short `asyncio.run` through the runner.

New and changed modules:

| Module | Contents |
|---|---|
| `onboarding/terminal.py` | The `Terminal` protocol, `Option`, `Action`, `ActionKind`, `Problem`, `resolve_problem` (§18, §19). |
| `onboarding/console.py` (new) | `RichTerminal` (guided) and `PlainTerminal` (not guided), and `open_url`'s browser rule (§18). |
| `onboarding/questions.py` (new) | `Answers`, the question registry, the value parsers, and agent CLI detection (§20). |
| `onboarding/summary.py` (new) | `Baseline`, the summary lines, `given_dests`, and the rerun command (§21). Replaces `flow.rerun_command`. |
| `onboarding/flow.py` | The new order (§22). |
| `onboarding/kiln_check.py`, `readiness.py`, `keys.py`, `choose.py`, `demo.py` | Their messages become `Problem`s with titles, bodies and actions (FS §4–§9). `keys.needed_providers` also returns the `Baseline`. `choose` turns no projects or no tasks into `Problem`s. |
| `cli.py` | `--no-interaction`; `--no-progress` (with `--no-ui` in `REMOVED_FLAGS`); the `--budget-usd` parser and the two new defaults; `option_dests` (§23). |

## 18. The terminal

```
StepStatus = Literal["ok", "fail", "warn"]

@dataclass(frozen=True)
class Option(Generic[T]):
    value: T
    label: str                        # first column
    columns: tuple[str, ...] = ()     # more columns, aligned across rows
    description: str | None = None    # shown under the highlighted row
    disabled: str | None = None       # why it can't be picked, shown dimmed

class ActionKind(Enum):
    RETRY, OPEN, PICK_TASK, PICK_PROJECT, USE_DEMO, EXIT, START, CHANGE, KEEP, CUSTOMIZE

@dataclass(frozen=True)
class Action:
    key: str                          # one lower-case letter, unique in its menu
    label: str
    kind: ActionKind
    url: str | None = None            # for OPEN

class Terminal(Protocol):
    interactive: bool                 # True = a guided start
    def header(self, text: str) -> None
    def step(self, status: StepStatus, label: str, text: str) -> None
    def block(self, title: str, body: Sequence[str]) -> None       # a failed check, ✗ title + body
    def say(self, text: str) -> None
    def warn(self, text: str) -> None
    def busy(self, text: str) -> AbstractContextManager[None]      # a spinner while Kiln is called
    def pick(self, question: str, options: Sequence[Option[T]], *, hint: str | None = None) -> T
    def act(self, question: str, actions: Sequence[Action]) -> Action
    def text(self, question: str, *, default: str = "", hint: str | None = None,
             validate: Callable[[str], str | None] | None = None) -> str   # validate → error or None
    def open_url(self, url: str) -> None
```

**Choosing the terminal.** `prepare_start` builds `RichTerminal(prog)` when `sys.stdin.isatty() and sys.stdout.isatty() and not args.no_interaction`, and `PlainTerminal(prog)` otherwise. Tests pass their own terminal.

**`RichTerminal`**
- **Output:**
  - One `rich.console.Console(highlight=False)` writes stdout, every line with `soft_wrap=True`, so a long command copies out whole.
  - `warn` prints `! text`, the `!` in yellow, to the same stdout console. The guided start is all on stdout (FS §13). A second console on stderr would also write past rich's live-display redirect and break the spinner's line.
  - `step` prints `✓` (green), `✗` (red) or `!` (yellow), then the label padded to 9 characters, then the text.
  - `block` prints a blank line, the bold red `✗ title`, a blank line, each body line indented two spaces, and a blank line (the FS §4 layout).
  - `header` prints bold text and a blank line.
  - `busy` returns `console.status(text, spinner="dots")`, which is transient. rich disables it and the colours on a stream that isn't a terminal, and honours `NO_COLOR`.
- **`pick`:**
  - `questionary.select(question, choices=[questionary.Choice(title=row_i, value=i, disabled=o.disabled, description=o.description) …], use_search_filter=True, use_jk_keys=False, instruction=hint or "↑↓ move · type to filter · Enter pick", erase_when_done=True).unsafe_ask()`, then return `options[i].value`.
  - `row_i` is the label and the columns, each padded to its column's widest cell and joined by three spaces. The search filter matches the whole row text, so typing an ID works.
  - **Its own keys, merged over questionary's.** Under a search filter, questionary 2.1.1 gets disabled rows and empty filters wrong. The bindings work on the select's `InquirerControl`, found in `question.application.layout`:
    - **Enter:** does nothing on a disabled row, or while the filter matches nothing (questionary then shows the whole list). Otherwise it picks the highlighted row.
    - **Up, Down, Ctrl-P, Ctrl-N:** skip disabled rows by their place in the filtered list. questionary checks the unfiltered list.
    - **Each filter key** (the printable characters and Backspace) updates the filter, then moves the cursor to the first enabled row of the filtered list. questionary puts it on row 0 even when that row is disabled.
- **`act`:**
  - `questionary.select(question, choices=[Choice(title=a.label, value=i, shortcut_key=a.key) …], use_shortcuts=True, use_jk_keys=False, erase_when_done=True)`.
  - Then one eager `KeyBinding` per key, merged into `question.application.key_bindings` with `merge_key_bindings`, calls `event.app.exit(result=i)`. A shortcut on its own only moves the cursor in questionary 2.1; this binding makes it act at once, which the research run confirmed.
- **`text`:** `questionary.text(question, default=default, instruction=hint, validate=lambda s: True if (e := validate(s)) is None else e, erase_when_done=True).unsafe_ask()`. questionary shows the error under the field and keeps the typed text.
- **Stopping:** each prompt maps `KeyboardInterrupt` to `OnboardingStopped(130)` and `EOFError` to `OnboardingStopped(1)`. Each prompt gets an eager Ctrl-D binding that raises `EOFError`: questionary's select swallows Ctrl-D, and its text field deletes a character with it.
- **Answer records:** the caller, not the terminal, prints the step line after `pick` and `text`. `act` leaves no record.
- **`open_url(url)`:**
  - When `graphical_browser_expected()` is true (`sys.platform in ("darwin", "win32")`, or `DISPLAY` or `WAYLAND_DISPLAY` is set), it prints `Opening <url>` and calls `webbrowser.open(url)`.
  - Otherwise it prints `Open this link in your browser: <url>`.
  - This closes backlog item 1: no console browser is ever started.

**`PlainTerminal`**
- `interactive = False`.
- `header` and `busy` do nothing.
- `step` prints `<label padded to 9> <text>` to stdout, with no symbol.
- `say` prints to stdout, and `warn` prints `<prog>: text` to stderr.
- `block`, `pick`, `act`, `text` and `open_url` raise `RuntimeError("not a guided start")`. `resolve_problem` raises before a block or a menu, the flow never prompts when not interactive, and a test enforces that.

**`ScriptedTerminal`** (`tests/onboarding_fakes.py`) takes `interactive` and a queue of answers:
- for `pick`, a value, or a callable that returns one;
- for `act`, a key;
- for `text`, a string, or a callable that gets the `validate` function and returns the text.

It records every `step`, `block`, `say`, `warn`, `busy` text and opened URL, each prompt's question and offer, and an ordered transcript of all of them. It fails the test on:
- a prompt in a start that isn't guided;
- a key or value the prompt didn't offer;
- a prompt while a spinner runs;
- a spinner inside a spinner.

## 19. Problems and `resolve_problem`

```
@dataclass(frozen=True)
class Problem:
    title: str                        # "Kiln isn't running at http://localhost:8757"
    body: tuple[str, ...]             # explanation and numbered steps, no prompt
    actions: tuple[Action, ...] = ()  # besides Check again and Exit
    retry_label: str = "Check again"
    retry_first: bool = True          # False: the problem's own actions lead
    warnings: tuple[str, ...] = ()    # printed after the block
    def menu(self, extra: Sequence[Action] = ()) -> tuple[Action, ...]
    def error(self) -> OnboardingError

class OnboardingError(Exception):     # errors.py
    def __init__(self, title: str, body: Sequence[str] = ()): ...
    title: str
    body: tuple[str, ...]
    shown: bool                       # a guided start has shown it as a block
    # str(): the title, then each body line indented two spaces

def resolve_problem(check: Callable[[], Problem | None], terminal: Terminal, *,
                    busy: str, extra: Sequence[Action] = ()) -> Action | None
```

**`Problem.menu(extra)`**
- `retry_first` true: `[Action("r", retry_label, RETRY), *actions, *extra, Action("x", "Exit", EXIT)]`.
- `retry_first` false: `[*actions, r, *extra, x]`. The no-projects and no-tasks problems lead with what to do in Kiln, as FS §5.2 shows (`m, o, r, x` and `o, r, p, x`).

**`Problem.error()`** is the `OnboardingError` for a start with no one to ask: the title and the body. With no menu to open a page from, it adds `"<label>: <url>"` for each Open action whose address the body doesn't already give.

**`resolve_problem`**
1. Run `check()` inside `terminal.busy(busy)`.
2. While it returns a `Problem`:
   - **Not interactive:** print the problem's `warnings` through `terminal.warn`, then raise `problem.error()`.
   - **Interactive:**
     1. `terminal.block(problem.title, problem.body)`, then each of its `warnings`.
     2. The answer is `terminal.act("What next?", problem.menu(extra))`.
     3. `OPEN` calls `terminal.open_url(action.url)` and asks again without re-checking. `RETRY` runs `check()` again (back to step 1). `EXIT` raises `OnboardingStopped(1)`. Any other kind (`PICK_TASK`, `PICK_PROJECT`, `USE_DEMO`) is returned to the caller.
3. Return `None` when `check()` returns `None`.

**Errors that aren't problems** (an unknown ID, a path mismatch, a failed demo copy) are `OnboardingError`s with a title and a body.
- In a guided start, `prepare_start` shows each one with `terminal.block` and sets `shown`. `main` then prints nothing and exits 1.
- Otherwise `main` prints `<prog>: <str(error)>` to stderr and exits 1.

**Warnings.** A check that passes prints its warnings after its step line: the Kiln check's unreadable API list, and readiness's evals Kiln couldn't load. A readiness problem carries its warnings, so they come after its block.

**Selection** (`choose.py`):
- `select` returns `Selection | Literal[ActionKind.USE_DEMO]`; the flow makes the demo and selects again.
- `Selection` also records `project_picked`, whether the user picked the project from a list.
- `select(…, same_project=selection)` keeps an earlier selection's project, for `t) Pick a different task`. The project isn't read or announced again, and it keeps its `project_picked`, so `p) Pick a different project` is still offered if it has no tasks left.
- `PICK_PROJECT` is handled inside `select`, which goes back to the project picker.
- The flow writes the project, the task and the folder into `args` only once the task is ready. Until then `args` holds only what was given, so a choice made again asks for the same things.

**Step lines.** A project or task given by a flag also gets its step line. The only choice gets `· the only <noun> <where>`.

**The messages.**
- **Where the text lives:** each check module builds its titles and bodies with the FS §4–§9 text.
- **The links:** `kiln_urls.py` gains `check_for_update(host)`, and the download link `KILN_DOWNLOAD_URL = "https://kiln.tech/download"`.
- **Readiness:** each problem line carries its own `Action` (`c` for run configs, `e` for evals), and duplicates are dropped.
- **Keys:** one `p` action.
- **Kiln check:** `d` always, plus `u` when Kiln is too old.

## 20. The questions (`questions.py`)

```
@dataclass
class Answers:
    objective: str
    guidance: str
    agent_cli: str                    # the whole command, e.g. "claude -p --model sonnet"
    budget_usd: float | None
    timeout_mins: int
    model_policy: str | None
    min_effect: float | None
    max_concurrent_evals: int
    max_concurrent_agents: int
    noise_check: int

    @classmethod
    def from_args(cls, args) -> Answers
    def apply(self, args) -> None     # writes every field back into the Namespace

@dataclass(frozen=True)
class Question:
    name: str                         # "objective", "guidance", ...
    dests: frozenset[str]             # the argparse dests that answer it
    advanced: bool
    ask: Callable[[Answers, QuestionContext], None]   # asks, sets the field, prints its step line
    show: Callable[[Answers], str]    # the step-line / summary text

QUESTIONS: tuple[Question, ...]       # the order in FS §10.1, then §10.2

@dataclass(frozen=True)
class QuestionContext:
    terminal: Terminal
    demo: bool
    policy_loader: Callable[[str | None], ModelPolicy | None]
    installed: tuple[str, ...]        # agent CLI kinds on PATH, in the order claude, codex, opencode

def ask_all(answers: Answers, ctx: QuestionContext, *, given: frozenset[str]) -> Answers
def ask_one(name: str, answers: Answers, ctx: QuestionContext) -> Answers
def installed_agent_clis(which: Callable[[str], str | None] = shutil.which) -> tuple[str, ...]
```

**`ask_all`**
- It asks every core `Question` whose `dests` don't meet `given`, then the Advanced action.
- `KEEP` leaves the advanced values alone.
- `CUSTOMIZE` asks each advanced `Question` whose `dests` don't meet `given`.

**The agent questions**
- **The agent CLI** is skipped when `agent_cli` is in `given`. With one CLI installed, it records that one and prints its step line. With none installed, it goes through `resolve_problem` with the "No agent CLI is installed" `Problem`, whose check re-runs detection.
- **The model** is asked whenever the CLI question was not skipped by the flag.
- **The command** is built as follows:
  - `claude` → `claude -p`, plus `--model <m>` when `m` isn't `AGENT_MODEL` (`opus`);
  - `codex` → `codex exec`, plus `-m <m>` when `m` isn't empty;
  - `opencode` → `opencode run`, plus `-m <m>` when `m` isn't empty.

  The result goes through `parse_agent_cli`, so a bad model name with a space in it is caught as the CLI would see it.

**The guidance** default is `DEMO_GUIDANCE` with `ctx.demo`, else `answers.guidance`.

**The value parsers** are pure functions, shared with `cli.py`. Each returns the value, or raises `ValueError` with the FS message:

| Function | Accepts | Returns |
|---|---|---|
| `parse_budget(text)` | a number above 0, an optional leading `$`, or `none` (any case) | `float \| None` |
| `parse_duration(text)` | minutes, or a number with `m` or `h` | whole minutes ≥ 1, rounded |
| `parse_fraction(text)` | above 0, at most 1 | `float` |
| `parse_count(text)` | a whole number ≥ 0 | `int` |

- **The model policy question:** its validate callback runs `ctx.policy_loader(path)` and returns the loader's message on `ConfigRenderError`.
- **The step lines** use each question's `show`: `$50.00`, `no cap`, `12 hours`, `90 minutes`, `"…"` or `none`.

## 21. The summary and the rerun command (`summary.py`)

```
@dataclass(frozen=True)
class Baseline:
    name: str
    model: str | None
    provider: str | None              # display name

def given_dests(argv: Sequence[str], option_dests: Mapping[str, OptionInfo]) -> frozenset[str]
def summary_lines(selection: Selection, answers: Answers, baseline: Baseline | None) -> list[tuple[str, str]]
def rerun_command(prog: str, argv: Sequence[str], *, option_dests: Mapping[str, OptionInfo],
                  selection: Selection, answers: Answers) -> str

@dataclass(frozen=True)
class OptionInfo:
    dest: str
    takes_value: bool

SET_BY_START = frozenset({"project_id", "task_id", "project_path", "demo_project", "objective",
    "guidance", "agent_cli", "budget_usd", "timeout_mins", "model_policy", "min_effect",
    "max_concurrent_evals", "max_concurrent_agents", "noise_check", "no_interaction"})
```

**`given_dests`** reads `argv` with the abbreviation rule of §8 (`_long_option`, moved here) and returns the dests of the options found. It stops at `--`.

**`rerun_command`** is built in three steps:
1. **Kept tokens:** `argv` minus every option whose dest is in `SET_BY_START`, plus the next token when the option takes a value and the token has no `=`.
2. **Lines:** `prog`, then
   - `--project-id P --task-id T`
   - `--project-path PATH`
   - `--objective O`
   - `--guidance G`
   - `--agent-cli A`
   - `--budget-usd B --timeout-mins M`
   - one line of the advanced flags whose values differ from the parser defaults
   - one line of the kept tokens, when there are any
   - `--no-interaction`
3. **Joining:** each line is `" ".join(shlex.quote(t) for t in tokens)`, indented two spaces after the first, and joined with ` \` and a newline.

Special values: no cap is `none`, empty guidance is `''`, and `B` is printed without a trailing `.0`.

**`summary_lines`** returns the FS §11 rows in order. "Starts from" is left out when `baseline` is `None`. "Advanced" lists only the fields that differ from the parser defaults, or reads `defaults`.

**The baseline.** `keys.needed_providers` returns `tuple[list[Need], Baseline | None]`. The `Baseline` is built from the baseline row it already finds: the row's `name`, `run_config_properties.model_name`, and the display name of `model_provider_name`. `key_problem` is unchanged.

## 22. `prepare_start` (replaces §5)

```
def prepare_start(args, argv, *, prog, option_dests, policy_loader,
                  terminal=None, runner=None, home=None, which=shutil.which) -> argparse.Namespace
```

```
terminal = terminal or (RichTerminal(prog) if both ttys and not args.no_interaction else PlainTerminal(prog))
given = given_dests(argv, option_dests)
terminal.header(f"Kiln Harness Tuner {__version__}")
resolve_problem(kiln check, busy="Checking Kiln at <host>…")       → step ok "Kiln"
if args.demo_project: demo = make_demo(...)                          → step ok "Demo"
loop:
    selection = select(...)        # pickers; no projects / no tasks → Problem with USE_DEMO / PICK_PROJECT
        USE_DEMO → make_demo, fill args, continue the loop
    resolve readiness              → step ok "Ready"; PICK_TASK → args.task_id = None, continue
    break
needs, baseline = keys check with the flag policy                    → step ok "API keys"
answers = Answers.from_args(args)
if terminal.interactive:
    answers = ask_all(answers, ctx, given=given)
    if answers.model_policy != args.model_policy: keys check again (with answers.model_policy)
    loop:
        print the summary and the rerun command
        act Start / Change an answer / Exit
            START  → break
            CHANGE → pick a question (only those asked or answerable), ask_one,
                     keys check again if the policy changed, continue
            EXIT   → OnboardingStopped(1)
else:
    print the summary
answers.apply(args)
return args
```

- **Spinner texts:** `Checking Kiln at <host>…`, `Reading projects…`, `Reading <project>'s tasks…`, `Checking the task is ready…`, `Checking API keys…`, `Copying the demo project…` and `Importing it into Kiln…`.
- **`USE_DEMO`** runs exactly what `--demo-project` runs and sets `args.demo_project = True`. The demo guidance default and the rerun command then follow it.
- **The version** in the header is `kiln_harness_tuner.__version__`.

## 23. `cli.py` changes

- **`--no-interaction`** (`store_true`). Help: `never ask a question or wait: checks fail fast, a missing project or task is an error, and every option not given takes its default`.
- **`--no-progress`** (`store_true`, dest `no_progress`) replaces `--no-ui`.
  - `REMOVED_FLAGS["--no-ui"] = "--no-ui was renamed --no-progress: plain log lines instead of the live progress display"`.
  - `_run` reads `args.no_progress`.
  - The docstrings in `cli.py` and `ui.py` that name `--no-ui` follow.
- **`--budget-usd`:** `type=_budget` (wrapping `parse_budget`, and raising `argparse.ArgumentTypeError`), `default=DEFAULT_BUDGET_USD = 50.0`. Help says `none` is no cap.
- **`--timeout-mins`:** `DEFAULT_TIMEOUT_MINS = 720`.
- **`_Parser.add_argument`** also records `option_dests[option] = OptionInfo(action.dest, takes_value=action.nargs != 0)` for each long option. `main` passes `option_dests=parser.option_dests` instead of `parser_long_options`.

## 24. Tests (phases 6–7)

| File | Cases |
|---|---|
| `tests/test_onboarding_console.py` (new) | `RichTerminal` driven through `prompt_toolkit.input.create_pipe_input()` and `prompt_toolkit.output.DummyOutput()`, passed to questionary as `input=` and `output=`: a pick by arrow keys, a pick by filter text, a disabled option skipped, an action by its single key with no Enter, an action by arrows and Enter, a text with its default accepted, an invalid text then a valid one, Ctrl-C → 130, Ctrl-D → 1. Step and block text through a `Console(file=StringIO(), force_terminal=True, color_system=None)`; no ANSI with `NO_COLOR`. `busy` prints nothing on a non-terminal console. `open_url` with and without `DISPLAY`. `PlainTerminal` raises on prompts. |
| `tests/test_onboarding_questions.py` (new) | Each parser's valid and invalid cases. `ask_all` with nothing given, and with each flag given. The demo guidance default. Agent CLI detection with 0, 1 and 3 installed. The model question per CLI and the command it builds. Keep, and Customize with each value. A bad policy file re-asked. `ask_one`. |
| `tests/test_onboarding_summary.py` (new) | `given_dests` with abbreviations and `=`. The rerun command: answers present, set flags replaced, kept tokens kept, `none` and `''`, advanced only when not default, `--no-interaction` last. It parses back (`build_parser().parse_args(shlex.split(...))`) to the same `Answers`. `summary_lines` with and without a baseline. |
| `tests/test_onboarding_flow.py` | The whole guided flow on fakes, from no flags to the returned `Namespace`. The policy re-check. Change an answer. Exit at the summary. `USE_DEMO` with no projects. `PICK_PROJECT` with no tasks. Not guided: no prompt called, the summary printed, defaults applied. |
| The existing onboarding tests | Expected text updated to the FS §4–§9 messages. `ScriptedTerminal` answers by key. |
| `tests/test_cli.py` | `--no-interaction`. `--no-progress`, and `--no-ui`'s removal message (exit 2). `--budget-usd` default 50, `none`, `$12.50`, and an invalid value (exit 2). `--timeout-mins` default 720. `option_dests`. |

## 25. Risks (phases 6–8)

- **questionary internals.** The terminal relies on two things outside questionary's public API, both true in 2.1.1, and each driven by a test:
  - Making a shortcut act at once relies on `Question.application.key_bindings` being assignable.
  - The picker's Enter, arrow and filter keys read and move the select's `InquirerControl`: `filtered_choices`, `found_in_search`, `search_filter`, `pointed_at`, `is_answered` and `add_search_character`.

  A minor release may change either, so the range is `>=2.1.1,<2.2`. `uv tool install` resolves the range afresh, without the repository's lock, so the range is what users get.
- **Rendering on narrow terminals:** questionary's list scrolls with the cursor (confirmed with 99 items in a 20-row window), but it has no page size. A list taller than the window scrolls the lines above it out of view while it's open; the erased list then leaves the step line in their place.
- **The rerun command is POSIX-quoted.** On Windows `cmd.exe` the line breaks and quoting differ. Windows is out of scope (FS §16).
- **Spinners and `asyncio.run`:** rich's status runs on its own thread, and each Kiln read's `asyncio.run` runs inside the `with`. A Ctrl-C there still reaches `prepare_start`'s `KeyboardInterrupt` handler and exits 130, as in §4.


# Part 3: Polish and robustness (phases 9–10)

## 26. The welcome box (FS §18)

- `Terminal.header(text)` becomes `Terminal.welcome(title: str, version: str, description: str)`.
  - `PlainTerminal.welcome` does nothing.
  - `ScriptedTerminal` records `(title, version, description)`.
  - The flow calls `terminal.welcome("Kiln Harness Tuner", __version__, WELCOME_DESCRIPTION)`.
- `WELCOME_DESCRIPTION` is a constant in `onboarding/flow.py`, holding the FS §18 text.
- **`RichTerminal.welcome`** prints a `rich.panel.Panel` with `box=ROUNDED`, `padding=(0, 1)`, `width=min(console.width, 68)` and `border_style="#415CF5"`.
  - Its content is a `Group` of three parts:
    1. a `Table.grid(expand=True)` row with the title (`bold #415CF5`) on the left and `v<version>` (`dim`, right-justified) on the right;
    2. a blank line;
    3. the description as `Text(description)`, which wraps inside the panel.
  - A blank line follows the panel.
  - `KILN_BLUE = "#415CF5"` is a constant in `console.py`.

## 27. The error screen (FS §19)

**`kiln.py`.** `KilnAPIError` gains a `kiln_message` property, and the extraction is a pure function: `kiln_error_text(body: Any) -> str`. It reads, in order:
1. `error_messages` (a list of strings, joined with `; `);
2. `detail` (a string, or a list of dicts whose `msg` values are joined with `; `);
3. a string body;
4. `repr(body)`.

It then strips a leading `": "` and `"Value error, "` and cuts the result to 500 characters with `…`. Phase 10's readiness warning uses it too.

**`onboarding/error_screen.py` (new).**

```
@dataclass
class RunProgress:                    # how far the start got, for the ending
    cli_checked: bool = False
    run_dir: Path | None = None
    spend: SpendMeter | None = None   # typed as a Protocol with `.total: float`, to keep orchestrator out
    report_path: Path | None = None

def error_body(exc: BaseException, *, host: str) -> list[str]
def ending_lines(progress: RunProgress) -> list[str]
def show_error(title: str, body: Sequence[str], ending: Sequence[str], *, prog: str,
               stream: TextIO = sys.stderr) -> None
def show_closing_line(prog: str, *, stream: TextIO = sys.stderr) -> None
```

**`error_body`**
- **`KilnAPIError`:** `[f"{exc.method} {exc.url_path}", f"HTTP {exc.status_code}: {exc.kiln_message}"]`.
- **`KilnTransportError`:** the message, plus `Is Kiln still running at <host>?`.
- **Anything else:** `str(exc).splitlines()`, with blank lines kept as paragraph breaks.

**`show_error`** builds a `Console(file=stream, highlight=False, soft_wrap=False, emoji=False)`.

- **When `console.is_terminal`,** it prints:
  1. a blank line;
  2. `✗ ` + the title, in `bold red`;
  3. a blank line;
  4. each body line through `Padding(Text(line), (0, 0, 0, 2))`, which wraps;
  5. a blank line;
  6. the ending lines, indented the same way;
  7. a blank line;
  8. the dimmed closing line.

  Every `Text` is built without markup.
- **Otherwise** it writes plain lines:
  - `f"{prog}: error: {title}"`;
  - the body and the ending, indented two spaces;
  - `f"{prog} stopped with an error (exit 1)"`.

**`cli.py`**
- **The titles:** `ERROR_TITLES: tuple[tuple[type[BaseException], str], ...]` holds the FS §19.1 table, most specific first. `title_for(exc)` returns the first `isinstance` match. An `OnboardingError` uses its own `title`. Anything else is `The run stopped`.

  This table lives in `cli.py` because it names `PreflightError` from `dispatch/claude_cli.py`, and only `cli.py` may import the dispatchers (`tests/test_dispatch_boundary.py`).
- **`_RUN_PROGRESS = RunProgress()`** is module-level and reset at the top of `main`, like `_stopped_by`. `_run` sets it as it goes:
  - `cli_checked = True` after `check_agent_cli`;
  - `run_dir` after `_unique_run_id`;
  - `spend = ctx.spend` after `build_ctx`;
  - `report_path = REPO_ROOT / "reports" / f"kiln-harness-tuner-{run_id}.md"` (the `ReportWriter` path).
- **`ending_lines`**
  - It picks the FS §19.3 row from those fields.
  - The report line appears only when `report_path.exists()`.
  - Spend is formatted `f"${total:.2f}"`.
- **The `main` handlers**
  - `OnboardingError` with `shown`: `show_closing_line`.
  - `OnboardingError` without `shown`: `show_error(exc.title, exc.body, ending_lines(...))`.
  - The handled-errors tuple: `show_error(title_for(exc), error_body(exc, host=args.kiln_host), ending_lines(_RUN_PROGRESS))`.
  - Return codes are unchanged.

## 28. Evals Kiln can't load (FS §20)

**One shared reader, in `setup.py`.**

```
UNLOADABLE_STATUSES = frozenset({400, 422})

@dataclass(frozen=True)
class EvalConfigRead:
    config: dict[str, Any] | None     # None when gone or unloadable
    unloadable: str | None = None     # Kiln's reason (kiln_error_text), for 400/422

async def read_eval_config(kiln, base: str, eval_id: str, config_id: str) -> EvalConfigRead
```

- A status in `CONFIG_GONE_STATUSES` gives `EvalConfigRead(None)`.
- A status in `UNLOADABLE_STATUSES` gives `EvalConfigRead(None, unloadable=exc.kiln_message)`.
- Any other `KilnAPIError` propagates.
- A body that isn't a dict gives `EvalConfigRead(None)`, as `_read_config_properties` does today.

**Setup**
- `_read_config_properties` is rebuilt on `read_eval_config`.
- `EvalView` gains `unloadable: str | None`.
- `read_eval_views` fills it.
- **Skipped for an unloadable view:** `build_sync_plan`, `describe_all` and `audit_splits`. They act as if the eval weren't active; the plan's existing records for it are neither refreshed nor repaired.
- **`run_setup`**
  - sets `ctx.unloadable_evals = {view.id: (view.name, reason)}`;
  - logs one `eval_unloadable` event per eval (`eval_id`, `name`, `reason`, `line`), whose `line` is the FS §20 warning text without the link.
- `Ctx` gains `unloadable_evals: dict[str, tuple[str, str]] = field(default_factory=dict)`.

**The measurement set.** `orchestrator._active_evals` drops rows whose id is in `ctx.unloadable_evals`, next to its `_new_eval_refused` filter. Every run-time reader of the active set (analysis, job drives, noise, judge copy, the detectability check) follows it.

**Onboarding**
- **`readiness.readiness_problem`**
  - After `measurable_evals`, it runs `read_eval_config` for each pair through `asyncio.gather`, bounded like setup's `SETUP_CONFIG_READ_CONCURRENCY`.
  - Unloadable pairs become warnings, with the FS text and the eval's link. The link uses the spec row whose `eval_id` matches, else the evals list.
  - Unloadable pairs are left out of the measurable count, and `Readiness` gains `left_out: int` for the step line.
  - When none load, the eval problem line is the FS §20 one.
- **`keys`:** `_eval_config` is replaced by `read_eval_config`. An unloadable read adds no `Need`.

**The report.**
- `RunStatusTracker` records `eval_unloadable` events into `RunStatus.unloadable_evals`.
- `ReportWriter` adds one `Left out:` line to its evals section when there are any, as `"<name>" (Kiln can't load it: <reason>)`, joined with `; `.
- `PlainUi` and `LiveUi` print the event's `line` as they do other warnings.

## 29. Tests (phases 9–10)

| File | Cases |
|---|---|
| `tests/test_onboarding_console.py` | The welcome panel's title, version and description; the width cap at 68 and wrapping at 40; no colour codes with `NO_COLOR`. |
| `tests/test_error_screen.py` (new) | `kiln_error_text` for each body shape and the cut; `error_body` per error kind; `ending_lines` for each FS §19.3 row; `show_error` on a terminal console (title, indentation, closing line) and to a plain stream. |
| `tests/test_cli.py` | `title_for` for each class in `ERROR_TITLES`, most specific first; `main` with a `KilnAPIError` raised from `_run` before and after the run folder (spend, folder, report); the shown `OnboardingError` closing line. |
| `tests/test_setup.py` | `read_eval_config` per status; an unloadable view skipped by sync, describe and audit with its memories untouched; `ctx.unloadable_evals` and the event. |
| `tests/test_orchestrator.py` | `_active_evals` drops unloadable evals. |
| `tests/test_onboarding_readiness.py`, `test_onboarding_keys.py`, `test_onboarding_flow.py` | The warning and link, `(N left out)`, every eval unloadable, the key check's skip, and 401/429/500 still stopping the start. |
| `tests/test_report.py` | The Left out line. |
