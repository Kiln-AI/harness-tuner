---
status: complete
---

# CLI onboarding (Kiln and API-key checks, project and task picker, demo project)

**Goal:** a new user runs one command and it either works or tells them exactly what to do next. The README quickstart becomes `uv tool install kiln-harness-tuner`, then `kiln-harness-tuner`.

**Coordination:** other open branches touch `cli.py`: the install branch (`claude/upbeat-mccarthy-rpkjet`), the Windows support task, and the "agent CLI" rename (PR #1). Start from them if they've merged; otherwise keep your changes to argument parsing and startup.

## 1. Check Kiln first

- Before anything else, check that the Kiln server answers (default `http://localhost:8757`, or the configured host).
- If it doesn't: say Kiln isn't running, tell the user to start the Kiln app or download it from https://kiln.tech (offer to open the page), then wait. "Press Enter when Kiln is running" re-checks, in a loop, until it answers or the user quits.
- If it's running but too old for the APIs the tuner needs (the memory API, the eval-job API, run-config provenance), say so and tell them to update Kiln. Use a version endpoint if there is one; otherwise probe an endpoint the tuner needs.
- When the CLI isn't run interactively (CI, a script, or another agent running it), there's no one to press Enter, so fail fast with the same message instead of waiting.

## 2. Project and task are optional; ask when they're missing

- `--project-id`, `--task-id` and `--project-path` become optional. When the path isn't given, get it from the Kiln API.
- When an ID is missing, list the projects (then that project's tasks) through the Kiln API and let the user pick in the terminal. Arrow keys or a numbered list are both fine. When there's only one choice, use it and say which one.
- If the chosen task isn't ready to tune (no eval, or missing train/val splits), say what's missing and where to fix it in the Kiln app, instead of a stack trace.
- When the CLI isn't run interactively and an ID is missing, exit with a clear error that lists the projects or tasks it found.

## 3. Check the API key the task needs (after the task is known)

- Before the run starts, check that Kiln has a key for the provider of the task's model (and of any model the run will use).
- If one is missing, say which provider, and link to Kiln's AI providers settings screen to add it. Then wait and re-check, like the Kiln check.

## 4. `--demo-project`

- A shortcut for "download the demo project, then pass its folder, project ID and task ID". It copies the demo (today's `examples/moderation/` project) into a fresh folder, imports it into Kiln through the API (the same call as the README's `curl` to `import_project` today), prints the folder, then runs on it. Every other flag works as usual (`--guidance`, `--timeout-mins`, `--budget-usd`).
- Folder: Kiln's default projects folder if it has one (check what the Kiln app uses), otherwise `~/kiln-projects/`. If the default name is taken, add `-1`, `-2`, and so on. Never overwrite.
- Source: the demo project ships inside the package, so it always matches the tuner's version and needs no download.
- The demo's model needs a Fireworks key today; the API-key check (step 3) covers it.

## Out of scope

Eval building, Windows, and state paths (the install branch owns those).

## Docs

Update `docs/install.md` and `docs/your-own-task.md` for the Kiln and API-key checks, the optional IDs, the picker and `--demo-project`. Steve owns the README; leave it.

## Done when

- `kiln-harness-tuner` with no arguments walks through the Kiln check, the project, the task and the API-key check, then starts a run.
- `kiln-harness-tuner --demo-project` (with the usual flags) copies, imports and starts a run, and a second call makes a `-1` folder.
- Tests cover: Kiln unreachable (interactive and not), a missing API key, the picker with 0, 1 and several projects, missing IDs when not interactive, and the folder-name suffix.
- Your summary lists every new flag and behavior.
