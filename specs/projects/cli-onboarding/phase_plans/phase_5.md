---
status: complete
---

# Phase 5: Docs

## Overview

Phases 1 to 4 built the start-up checks, the picker, the rerun line and `--demo-project`. This phase brings the two user docs in line with them (FS §13, architecture §14). `docs/install.md` says what the Kiln check and the key check print and how they wait or fail fast. It also corrects its two wrong claims about Kiln's settings and model list, and it gains a short "Try the demo" section. `docs/your-own-task.md` says which of the task's needs are checked at start-up, how the project and task are chosen, what the rerun line is, and what the three project flags do when left out.

No code changes. The root `README.md` is Steve's, and install.md's step zero and verify block are the install branch's. None of them change.

## Steps

1. **`docs/install.md`, intro.** Name the new unnumbered section beside the other two: "The three unnumbered sections — platform support, trying the demo, and the verification block at the end — are not requirements."

2. **`docs/install.md` §4** (a running Kiln):
   - The paragraph "Get Kiln from there, start it, and make a project and a task in it …" now says a run needs a project with a task. You make one in Kiln, or `--demo-project` imports one ("Try the demo"). The IDs don't need looking up: when left out, they're asked for (your-own-task.md §5).
   - **Check**, **If it is not running** and **Fix** keep the manual `curl …/ping` check.
   - A new subsection, "The check at start-up", covers:
     - **Kiln answers:** one `GET /ping`, 5-second timeout. The not-running message and the not-Kiln message, as `kiln_check.py` prints them, each with its `[r]etry  [o]pen kiln.tech  e[x]it:` line.
     - **Kiln is new enough:** Kiln has no version endpoint, so `GET /openapi.json` is read once for the three APIs (the memory API, the eval-job API, run-config provenance). The too-old message. The version is left out when Kiln reports `unknown`. An unreadable list is the one warning line, and the start goes on.
     - **It waits, or it fails fast:** interactive means stdin and stdout are both terminals. Interactive, `r` or Enter re-checks (no polling), `o` opens the page and prints its address, and `x` or Ctrl-D stops with exit 1 and `kiln-harness-tuner: stopped.`. Ctrl-C exits 130. Not interactive, the same message goes to stderr with the `kiln-harness-tuner:` prefix, and the exit is 1 with no input read. Every later start-up check follows the same rules.
   - **Inside a run** is reworded: the pre-flight's gateway `ping` checks again, from where the agents stand.

3. **`docs/install.md` §6** (a model provider key):
   - The example line names the demo as well as the moderation example: both use Fireworks.
   - **The check at start-up**, once the task is known. It covers the four sources of providers: the baseline run config, by setup's rule; each measured eval's judge, where a code eval names none; a multi-turn task's simulated user, from the eval inputs' `drive_config`; and the providers `--model-policy` names exactly, where `*` names none. The providers that take no key are listed by ID. The check reads `GET /api/available_models` and never `/api/settings`. The FS §8.2 message, the `--model-policy` suffix, and a retry that reloads the policy file. The same wait or fail-fast rules as §4.
   - **Check by hand:** `curl …/api/available_models`, filtered for the provider's ID with `python3 -c`, prints `True` or `False`. The `(7)` / `JSONDecodeError` note stays.
   - **The two corrections:**
     - `/api/settings` returns `[hidden]` in place of each stored key, not the keys.
     - `/api/available_models` lists a provider that takes a key only when Kiln has every key it needs, so it is the check. The old text said it is "not a substitute".
   - **Fix:** the AI Providers page, `http://localhost:8757/settings/providers`.
   - **What a bad key looks like in a run:**
     - The start-up check proves a key is set, not that the provider accepts it.
     - A rejected key is a hard failure in the model preflight, which stops the start on the baseline's model, and it is the `auth` class inside a run.
     - A key removed after the start can still land in `provider_error`. The "a run that gets no eval job to complete" symptom stays.

4. **`docs/install.md`, "Try the demo"**, a new section after §6:
   - The command (`kiln-harness-tuner --demo-project --timeout-mins 60 --budget-usd 20`) and the lines it prints: the two demo lines and the rerun line.
   - Where the copy goes (`~/Kiln Projects/`), the `-1`, `-2` suffix, and that nothing is overwritten. Each copy has its own project ID, its name in Kiln is the folder's name, and the task ID is fixed.
   - The import is `trusted=true` because the demo's eval runs its scorer.
   - It can't be combined with the three project flags (exit 2).
   - A refused import or a failed copy deletes the folder and exits 1.
   - A user stopped at the key check keeps the copy. They add the key and run the printed command, not a second `--demo-project`.
   - It needs §1–§6, including the Fireworks key. It links `examples/moderation/README.md` for the task itself and for checking the result on test.

5. **`docs/your-own-task.md` §1** (what the loop needs):
   - After the intro, a paragraph says §1.1 and §1.2 are checked at start-up by the readiness check, every start, IDs passed or not. It shows the readiness message with both lines, its Kiln links and the `[t]` option, which appears only when the task was picked from a list. It notes the `load_error_count` warning, and that the keys are checked right after (install.md §6).
   - §1.1: the setup quote is replaced by the readiness line and its link. The "one state code cannot decide" paragraph stays.
   - §1.2: the readiness rule (at least one active eval with a default eval config), its two lines, and that one eval without a default beside others with one still passes the check, then measures nothing.
   - §1.5: `--project-path` left out is taken from Kiln. Given, it must be Kiln's folder; the mismatch message, and why (the read fence denies the folder it is given). A project Kiln has no folder for needs the flag.

6. **`docs/your-own-task.md` §5** (the first run):
   - The command without project flags, then the order of the start-up checks.
   - The picker and its prompt, and the one-choice line.
   - A table of which project flags decide what (FS §5.1). No first cell is a lone backticked flag, so `tests/test_docs.py` doesn't read a row as a flag entry.
   - The errors, in both modes:
     - No projects, and no tasks, each with its link.
     - An unknown ID, with the listing.
     - The non-interactive "is needed" listing, exit 1.
   - The rerun line: when it is printed, why before readiness and keys, and how it's built.
   - The explicit command stays as the second form. The existing paragraphs (bounded first run, recon, stopping a run) stay.

7. **`docs/your-own-task.md`, "Every flag":**
   - `--project-path`, `--project-id` and `--task-id` lose **Required.** Each row says what happens when the flag is left out, as the help texts do, and points to §1.5 or §5.
   - The `--demo-project` row gains a link to install.md's "Try the demo".

## Tests

- `tests/test_docs.py` (existing): one row per flag across `docs/` and every internal link resolves, with the new links to `install.md`, `your-own-task.md` and `examples/moderation/README.md`.
- The full suite, `uv run ruff check` and `uv run ruff format --check` stay green. No new tests: this phase changes no code.
