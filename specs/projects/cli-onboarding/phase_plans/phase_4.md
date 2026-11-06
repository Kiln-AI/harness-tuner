---
status: complete
---

# Phase 4: `--demo-project`

## Overview

After this phase, `kiln-harness-tuner --demo-project` (with any of the usual flags) makes a fresh copy of the demo project, imports it into Kiln, and runs on it (FS §9, architecture §11). A second call makes `Forum Moderation-1`, then `-2`, and nothing is ever overwritten.

The demo is the Kiln project that sits in `examples/moderation/Forum Moderation/` today. It moves into the package as package data, so a clone and an installed wheel read the same files and it always matches the tuner's version (FS §10, architecture §13). The move is a commit of its own with no content change, so git's rename detection keeps the history.

The packaged copy holds the labels of every split, test included, so every run's read fence denies it by path (architecture §12). Today the project scan finds it because it sits in the repository; once the package is installed, it sits in site-packages, where the scan can't see it.

## Steps

1. **The move** (§13). `git mv "examples/moderation/Forum Moderation" "src/kiln_harness_tuner/demo_project/Forum Moderation"`, with no content change. It is committed on its own, before the rest of the phase.

2. **`.gitignore`**:
   - Add `/src/kiln_harness_tuner/demo_project/*/tasks/**/runs/` beside the `examples` rule. The comment above them covers both: a Kiln project committed in this tree (the packaged demo, or a future example) is committed without its runs.
   - The anchoring note at the top names the packaged demo beside `examples/` as the trees whose nested directory names must not be ignored by accident.

3. **`onboarding/demo.py`** (§11):

   ```
   DEMO_DIR = "demo_project"
   DEMO_PROJECT = "Forum Moderation"
   DEMO_TASK_ID = "424242783061"
   KILN_PROJECTS_DIR = "Kiln Projects"     # Kiln's default_project_path(): ~/Kiln Projects
   IMPORT_PATH = "/api/import_project"

   def demo_source() -> Traversable          # files("kiln_harness_tuner") / DEMO_DIR / DEMO_PROJECT
   def demo_fence_root() -> Path             # Path(str(files("kiln_harness_tuner") / DEMO_DIR))
   def claim_folder(parent: Path, name: str) -> Path
   def new_project_id(taken: Collection[str]) -> str
   def write_copy(source: Traversable, dest: Path, *, project_id: str, name: str) -> None

   @dataclass(frozen=True)
   class DemoProject:
       folder: Path
       project_id: str
       task_id: str

   def make_demo(*, terminal: Terminal, run: KilnRunner, home: Path) -> DemoProject
   ```

   - `claim_folder`: `parent.mkdir(parents=True, exist_ok=True)`, then `mkdir()` on `name`, `name-1`, `name-2`, … until one succeeds. `FileExistsError` (a folder, a file or a dangling link of that name) means the next one.
   - `new_project_id`: `_random_id()` (`str(uuid.uuid4().int)[:12]`, Kiln's `generate_model_id`), redrawn while it is in `taken`.
   - `write_copy`: `as_file(source)`, then `shutil.copytree(src, dest, dirs_exist_ok=True)`, ignoring `__pycache__` (an installer that byte-compiles the wheel writes one beside the scorer, and it is no part of the project). Then `project.kiln` gets the new `id` and `name`, written with `indent=2, ensure_ascii=False` and a trailing newline.
   - `make_demo`:
     1. `taken` = the IDs from `fetch_projects`.
     2. `claim_folder(home / KILN_PROJECTS_DIR, DEMO_PROJECT)`. An `OSError` (the parent can't be made) is an `OnboardingError` that names the parent folder.
     3. `write_copy` with a fresh ID and the folder's name. An `OSError` deletes the folder (`shutil.rmtree(..., ignore_errors=True)`) and raises an `OnboardingError` that names the folder and the cause.
     4. `POST /api/import_project` with query `{"trusted": "true", "project_path": "<folder>/project.kiln"}`. A `KilnError` deletes the folder and raises an `OnboardingError` with Kiln's `detail` (for a `KilnAPIError` whose body has a string `detail`), else the error itself.
     5. The project ID is the one Kiln's answer gives when it is a non-empty string, else ours.
     6. `terminal.say` the two FS §9.2 lines.

4. **`onboarding/flow.py`**:
   - `prepare_start` gains `home: Path | None = None` (default `Path.home()`).
   - After the Kiln check, when `args.demo_project`: `make_demo`, then its folder, project ID and task ID go into `args`.
   - `_choose_a_ready_task` gains `demo: bool`. The rerun line is printed when `selection.filled or demo`: the demo fills in all three flags before `select` runs, so `select` sees none missing.

5. **`cli.py`** (§12):
   - `--demo-project` (`store_true`), with help that says what it does and that it can't be combined with the three project flags.
   - `validate_args(parser, args)`: `--demo-project` with any of `--project-id`, `--task-id` or `--project-path` calls `parser.error("--demo-project makes its own project: drop --project-id, --task-id and --project-path")`, exit 2.
   - `main` calls `validate_args` right after parsing.
   - `read_fence_for` denies `demo_fence_root()`, after the `examples/` denials. The docstring says why.

6. **Docs and agent files**:
   - `docs/your-own-task.md`: a `--demo-project` row in "Every flag", after `--task-id` (`tests/test_docs.py` requires one row per flag). The other rows are phase 5's.
   - `docs/architecture.md` §2 (the read fence) and `docs/limitations.md` (the path fence): the denied list names the packaged demo, since the code now denies it.
   - `examples/moderation/README.md`: steps 1–3 become one `kiln-harness-tuner --demo-project` step, with where the copy goes, the `-1` suffix, and that the project's source is the package folder. Step 4 (check on test) stays.
   - `.agents/AGENTS.md`: the project-path sentence names `src/kiln_harness_tuner/demo_project/Forum Moderation/`, and the layout block lists `demo_project/`.

7. **`tests/test_moderation_example.py`**: `TASK` is under `demo_source()`. `EXAMPLE` stays `examples/moderation` for `posts.jsonl` and `scorer.py`.

8. **Test fakes** (`tests/onboarding_fakes.py`):
   - `flags()` gains `demo_project: bool = False`.
   - `import_project_route(kiln)`: a `POST /api/import_project` route that reads the `project.kiln` named by the query from disk, adds the project to `GET /api/projects` (with Kiln's `path`), and registers its tasks (read from `tasks/*/task.kiln`) with `ready_task_routes`. It returns the project, as Kiln does.

9. **The one-time wheel check** (§13): `uv build --wheel` into the scratchpad, then list the wheel and confirm `kiln_harness_tuner/demo_project/Forum Moderation/project.kiln` and the eval's `scorer.py` are in it, with every file of the project. Not a permanent test.

## Tests

- `tests/test_onboarding_demo.py`:
  - `test_demo_source_is_the_packaged_demo`: its `project.kiln` has the demo's project ID and name, and it holds the task folder of `DEMO_TASK_ID`.
  - `test_the_fence_root_holds_the_demo`: `demo_source()` is inside `demo_fence_root()`.
  - `test_claim_folder_takes_the_name_when_it_is_free`, and creates a missing parent.
  - `test_claim_folder_adds_1_then_2`.
  - `test_an_existing_file_counts_as_taken`, and a dangling link too.
  - `test_new_project_id_avoids_taken_ids` (with `_random_id` scripted), and `test_new_project_id_is_12_digits`.
  - `test_write_copy_copies_every_file_and_sets_the_id_and_name`: the set of files equals the packaged demo's; every file but `project.kiln` is byte-identical; `project.kiln` keeps its other fields.
  - `test_write_copy_leaves_out_pycache`.
  - `make_demo`:
    - `test_make_demo_copies_imports_and_says_where`: the import call's method, path and query; the printed lines; the returned `DemoProject`.
    - `test_a_second_demo_goes_to_dash_1_with_its_own_id_and_name`.
    - `test_the_fresh_id_is_not_one_kiln_has`.
    - `test_kiln_s_project_id_is_used_when_its_answer_has_one`, and ours when it doesn't.
    - `test_a_refused_import_removes_the_folder_and_says_why`: Kiln's `detail` and the folder are in the message.
    - `test_an_import_that_never_answers_removes_the_folder`.
    - `test_a_failed_copy_removes_the_partial_folder`: the error names the folder; no import call is made.
    - `test_a_parent_that_cant_be_made_is_an_error_that_names_it`.
- `tests/test_onboarding_flow.py`:
  - `test_demo_project_copies_imports_and_runs_on_the_copy`: the Kiln check comes before the import, readiness after it; the returned flags name the copy; the rerun line has the three flags, keeps `--guidance`, and has no `--demo-project`.
  - `test_a_second_demo_project_makes_a_dash_1_copy`: two starts on one Kiln and one home.
  - `test_demo_project_when_not_interactive_asks_nothing`.
- `tests/test_cli.py`:
  - `test_demo_project_parses_and_defaults_off`.
  - `test_demo_project_with_a_project_flag_exits_2` (parametrized over the three flags): the argparse error names the conflict; `prepare_start` is never called.
  - `test_read_fence_denies_the_packaged_demo`: `demo_fence_root()` is in the fence's `deny`, for a project outside the repository.
- `tests/test_moderation_example.py`: its existing tests pass against the packaged copy.
- `tests/test_docs.py`: passes with the new row.
