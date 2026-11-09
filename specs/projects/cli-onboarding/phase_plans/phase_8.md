---
status: complete
---

# Phase 8: Docs for the guided start

## Overview

Phases 6 and 7 rebuilt the start: step lines, ✗ blocks with single-key action menus, pickers, the questions, the `Ready to start` summary with the multi-line rerun command, the new defaults (`--budget-usd 50`, `--timeout-mins 720`), `--no-interaction`, and `--no-ui` renamed `--no-progress`. The flag table already has its rows (phases 6 and 7). This phase brings the prose and the examples in line (FS §14):

- `docs/install.md`: §4, §6 and "Try the demo" show the new messages and actions, and a new unnumbered section, "Guided or not", explains both kinds of start with the `--no-interaction` / `--no-progress` table.
- `docs/your-own-task.md` §1 and §5: the readiness block, and the guided start's pickers, questions, summary, rerun command and start prompt.
- `docs/costs.md`: the new defaults where it speaks of the two knobs.
- `examples/moderation/README.md`: `kiln-harness-tuner --demo-project` as the one step, and what it asks.

Every message quoted is the code's own text (the onboarding modules), with the FS layouts for the prompts. No code changes. The root `README.md` is Steve's and stays as it is.

## Steps

1. **`docs/install.md`, intro.** The unnumbered sections become four: platform support, guided or not, trying the demo, and the verification block.

2. **`docs/install.md` §4** (a running Kiln):
   - The paragraph after the metadata block: a run needs a project with a task; make one in Kiln, or use the demo (`--demo-project`, or `m` when Kiln has no projects). A guided start lists what Kiln has and asks (`your-own-task.md` section 5).
   - **Check**, **If it is not running** and **Fix** keep the manual `curl …/ping` check.
   - **The Kiln check at start-up**, rewritten:
     - **Passed:** `✓ Kiln      1.2.0 at http://localhost:8757`; `running at <host>` when Kiln reports no version or `unknown`.
     - **Not running:** the FS §4 block and its menu (`r`, `d`, `x`).
     - **Something else on the port:** the block and menu; `HTTP 404` or `an unexpected answer`.
     - **Too old:** the block and menu (`r`, `u`, `d`, `x`); `u` opens `<host>/settings/check_for_update`; the list names only the missing APIs, among the three.
     - **API list unreadable:** the `!` line, and the start goes on.
     - **Not guided:** the same title and explanation on stderr, prefixed, plus the address of each page the menu would open when the explanation doesn't give it; exit 1. One example (not running).
     - The keys of the menus and Ctrl-C / Ctrl-D link to "Guided or not".
   - **Inside a run** stays.

3. **`docs/install.md` §5** (the agent CLI): one short paragraph on what a guided start does with the agent CLIs it finds on `PATH` (the question, `not installed` rows, one installed is used, none is a ✗ block that points here), and an **If the CLI is missing** part with each CLI's npm install command, so the "No agent CLI is installed" message's pointer to this section's install steps holds.

4. **`docs/install.md` §6** (a model provider key), **The key check at start-up**:
   - The four sources of providers, the providers that take no key, and `GET /api/available_models` stay.
   - **Passed:** `✓ API keys  Fireworks AI, OpenAI`, or `none needed (local models only)`.
   - **Missing:** the FS §7 block and menu (`r`, `p`, `x`), `p` opening `<host>/settings/providers`; the singular wording; `, or remove it from the model policy` for a provider only the policy names; each retry reloads the policy file.
   - The check runs again after the questions when an answer set or changed the model policy.
   - Not guided: stderr, with the AI Providers address, exit 1.
   - **Check by hand**, the two corrections, **Fix** and **What a bad key looks like in a run** stay. The message now names providers by display name only, so the by-hand check says where the provider's ID comes from.

5. **`docs/install.md`, "Guided or not"** (new, unnumbered, after §6):
   - The rule: stdin and stdout are terminals and `--no-interaction` isn't given. Without a terminal it's never guided, flag or not.
   - **Guided:** the header, the step lines (✓ ✗ !), spinners on each Kiln call, colour off with `NO_COLOR` or a non-terminal stdout. A failed check is a ✗ block and a `What next?` menu: one key acts at once, arrows and Enter work too; Check again re-runs it with no polling and prints the block again if it still fails; an Open action prints `Opening <url>` and opens the browser only on macOS, Windows, or with `DISPLAY` / `WAYLAND_DISPLAY`, else prints `Open this link in your browser: <url>`; Exit stops with 1. Ctrl-C at any prompt exits 130, Ctrl-D 1, both with `kiln-harness-tuner: stopped.`. Then the questions and the summary (your-own-task.md section 5).
   - **Not guided:** asks nothing and waits for nothing; the step lines as plain text; a failed check's message on stderr and exit 1; a missing ID with two or more choices is an error that lists them; every option not given takes its default; the summary, then the run.
   - **Output and exit codes:** guided all on stdout; not guided, errors and warnings on stderr, prefixed. 1, 2 and 130 as FS §13 lists them.
   - **The table** of `--no-interaction` and `--no-progress` (FS §2), with "Controls" as its first column, so no row's first cell is a lone backticked flag (`tests/test_docs.py`). Then a line on combining them, and `--no-ui`'s rename (exit 2).

6. **`docs/install.md`, "Try the demo"**, rewritten:
   - `kiln-harness-tuner --demo-project`, and the guided transcript: the header, `Kiln`, `Demo`, `Project`, `Task`, `Ready` and `API keys` lines, the answered questions, the summary, the rerun command and the start prompt.
   - The project and task questions are skipped; the guidance is pre-filled with the demo's text; the rest as usual. Not guided, nothing is asked and the guidance is empty unless `--guidance` is given.
   - Where the copy goes, `-1`, `-2`, each copy's project ID and name, the fixed task ID, the trusted import.
   - The rerun command uses the copy's IDs and folder; `--demo-project` again makes another copy. A start stopped before the summary keeps the copy: pick it from the project list next time.
   - `m) Use the demo project` when Kiln has no projects does the same.
   - The three project flags can't be combined with it (exit 2); a refused import or failed copy deletes the folder and exits 1; it needs sections 1 to 6; the link to `examples/moderation`.

7. **`docs/your-own-task.md` §1:**
   - The readiness block and its menu (`r`, `c`, `e`, `t`, `x`) replace the `[r]etry` prompt. `c` opens `<host>/optimize/<pid>/<tid>` and `e` `<host>/specs/<pid>/<tid>`, each only with its line; `t` only for a task picked from a list. The passed line, the `!` load-error warning, and not guided (stderr, the links, exit 1). The key check right after.
   - §1.1 and §1.2: the new lines (`• A run config: …`, `• An eval it can measure: …`, `• A default judge on its active evals: … have none set.`), and that the default judge is the eval's default eval config.
   - §1.5: the mismatch message in its new form.

8. **`docs/your-own-task.md` §5** (the first run), rewritten around the guided start:
   - `kiln-harness-tuner` alone, and the order of the start (FS §1).
   - **Picking the project and the task:** the picker (arrows, filter on name or ID, Enter), the task's description, the step line after the pick, the only-choice note. The flags table (FS §5.1) stays.
   - **When it can't choose:** no projects (`m`, `o`, `r`, `x`), no tasks (`o`, `r`, `p`, `x`), each with its link; unknown IDs and a `--project-path` that isn't a project, in both kinds of start; the not-guided "is needed" listing.
   - **The questions:** a flag on the command line answers its question; the core questions as a table (question, flag, pre-filled, what you can type, step line); the agent CLI and its model; the budget's Codex note; the Advanced menu and its five questions; the flags that are never asked.
   - **The summary and the start:** the `Ready to start` block, Starts from, Advanced; the rerun command (printed only here, how it's built, `''` and `none`); `s`, `e` and `x`.
   - **Not guided:** the plain summary, then the run.
   - The explicit command stays, now with `--no-interaction` as the way to ask nothing in a terminal. The bounded-run paragraph gains the defaults ($50, 12 hours, `none`). The recon and stopping paragraphs stay.

9. **`docs/costs.md`:**
   - "Smoke first": the two knobs have defaults, `--budget-usd 50` and `--timeout-mins 720`, which a guided start pre-fills; `none` runs with no cap; set smaller ones for a first run.
   - The noise check's "With `--budget-usd`" becomes "with a budget (any `--budget-usd` but `none`)".

10. **`examples/moderation/README.md`, "Run the loop on it":**
    - Step 1 is `kiln-harness-tuner --demo-project`. A note lists what it asks in a terminal and the pre-filled answers (Quality, the demo's guidance, the agent CLI and `opus`, $50, 12 hours, Advanced defaults), and that `--budget-usd 20 --timeout-mins 60` on the command line answers those two for a short first run.
    - The copy's folder, `-1`, `-2`, the IDs, and the rerun command now printed in the summary.

## Tests

- `tests/test_docs.py` (existing): exactly one flag row per flag across `docs/` (the new "Guided or not" table's first cell is not a lone flag), and every internal link resolves, including the new anchors' files.
- The full suite, `uv run ruff check` and `uv run ruff format --check` stay green. No new tests: this phase changes no code.
