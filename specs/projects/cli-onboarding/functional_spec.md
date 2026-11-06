---
status: draft
---

# Functional Spec: CLI onboarding

A new user runs `kiln-harness-tuner`, with no flags or with `--demo-project`. The command either starts a run or says exactly what to fix and where. Four checks run first, in plain code: Kiln is up, it is new enough, the task is ready, and the keys are there. No agent session, model call or run folder happens before they pass.

The command name stays `kiln-harness-tuner`, as on `main`. Every message, every printed command and the docs use the name from one constant (the parser's `prog`). When the install branch renames the command, that is a one-line change here.

## 1. Startup order

1. Parse flags. Argparse errors work as today.
2. **Kiln check** (§4): Kiln answers, and it has the APIs the tuner needs.
3. **`--demo-project` only:** copy and import the demo (§9). That gives the project ID, the task ID and the path.
4. **Choose the project, then the task** (§5), from flags, the picker, or the only choice.
5. **Find the project folder** (§5.4) when `--project-path` isn't given.
6. **Print the full command** (§6) if anything was filled in.
7. **Readiness check** (§7): a run config and an eval the loop can measure.
8. **API-key check** (§8): a key for every provider the run will call.
9. The existing start-up, unchanged: install the agent files, check the agent CLI, take the run lock, and so on.

Loading `--model-policy` moves from after the agent CLI check to before step 8, because step 8 reads it. Nothing else in the existing start-up moves.

## 2. Interactive or not

The CLI is **interactive** when both stdin and stdout are terminals (`isatty()`). Otherwise it is **not interactive**: CI, a script, a pipe, or another agent running it.

- Interactive: a failed check shows a prompt (§3) and waits.
- Not interactive: a failed check prints the same message to stderr and exits with code 1. A missing ID is an error that lists what Kiln has (§5.3). The CLI never waits for input.

## 3. Prompts

Every check that can fail and be fixed outside the tuner ends with the same prompt:

```
[r]etry  [o]pen <what>  e[x]it:
```

- `r` or Enter: run the check again. There is no polling; it re-checks only when asked.
- `o`: open the fix page in the browser (`webbrowser.open`), print its URL too (a remote shell has no browser), and show the prompt again. This option appears only when the check has a page to open.
- `x`, or end of input (Ctrl-D): stop. The CLI exits with code 1 and prints `kiln-harness-tuner: stopped.`
- Ctrl-C: stop with exit code 130 and no traceback.
- Anything else: show the prompt again.

After a failed retry, the CLI prints the message again, so the user can see what is still wrong.

The picker (§5.2) has its own prompt.

## 4. Kiln check

### 4.1 Kiln answers

`GET <kiln-host>/ping`, one try, 5-second timeout. Kiln answers `"pong"`.

| Result | Meaning |
|---|---|
| HTTP 200 with body `"pong"` | Kiln is up. Go on to §4.2. |
| No connection, timeout, or another transport error | Kiln isn't running. |
| Any other answer | Something else is listening on that port. |

**Not running:**

```
Kiln isn't running at http://localhost:8757.
Start the Kiln app. If you don't have it, download it from https://kiln.tech.
If Kiln runs on another port, pass --kiln-host.
[r]etry  [o]pen kiln.tech  e[x]it:
```

**Something else on the port:**

```
Something answered at http://localhost:8757, but it isn't Kiln (GET /ping returned HTTP 404).
Check --kiln-host, or start the Kiln app.
[r]etry  [o]pen kiln.tech  e[x]it:
```

### 4.2 Kiln is new enough

Kiln has no version endpoint. The tuner reads `GET <kiln-host>/openapi.json` once and checks that three APIs are there:

| API the tuner needs | Present when |
|---|---|
| Memory API | `paths` has `/api/projects/{project_id}/memories` |
| Eval-job API | `paths` has `/api/jobs/evals/run` |
| Run-config provenance | the `CreateTaskRunConfigRequest` schema has a `provenance` property |

When any is missing:

```
Kiln at http://localhost:8757 (version 1.0.3) is too old for kiln-harness-tuner.
It's missing: the memory API, run-config provenance.
Update Kiln: in the app, open Settings → Check for update, or download the latest from https://kiln.tech.
Then restart it.
[r]etry  [o]pen kiln.tech  e[x]it:
```

- `(version …)` comes from the document's `info.version`. It is left out when that reads `unknown`, which packaged Kiln builds can report.
- If `/openapi.json` can't be read (non-200, or not JSON), the CLI prints one warning line and goes on: `kiln-harness-tuner: couldn't read Kiln's API list, so its version wasn't checked.` A later call to a missing API fails with its own error.

## 5. Choosing the project and the task

### 5.1 Flags

`--project-id`, `--task-id` and `--project-path` become optional. Each combination resolves as follows:

| Given | What happens |
|---|---|
| project and task | Both are used. Each is checked against Kiln (an unknown ID is an error, §5.3). |
| project only | Pick the task among that project's tasks. |
| task only | Find the projects that contain that task ID. If one does, use it. If several do (demo copies share a task ID), pick among them. If none does, error. |
| `--project-path` only | Find the project whose Kiln path is that folder. If none is, it's an error: the folder isn't imported in Kiln. Then pick the task. |
| nothing | Pick the project, then the task. |

The lists come from `GET /api/projects` and `GET /api/projects/{project_id}/tasks`.

### 5.2 The picker

Rows are numbered from 1, in the order Kiln returns them. Each shows the name and the ID:

```
Projects in Kiln:
  1. Forum Moderation  (424242975950)
  2. Support Triage  (118230554411)
Project [1-2, x to exit]:
```

- A number in range picks that row. Anything else asks again. `x` or end of input stops (exit 1), and Ctrl-C exits 130.
- The task picker looks the same, under `Tasks in Forum Moderation:`.
- **One choice:** no question. The CLI uses it and prints, for example, `Using project Forum Moderation (424242975950), the only project in Kiln.` This applies in both modes, since nothing needs to be asked.
- **No choices:** an error, the same in both modes:
  - No projects: `Kiln has no projects. Create one in the Kiln app (http://localhost:8757/settings/create_project), or try the demo: kiln-harness-tuner --demo-project`
  - No tasks: `Project Forum Moderation has no tasks. Create one in Kiln: http://localhost:8757/settings/create_task/<project_id>`

### 5.3 Errors

The CLI exits 1, printing to stderr, when:

- **An ID is missing, there's more than one choice, and the CLI isn't interactive:**

  ```
  kiln-harness-tuner: --project-id is needed when not run interactively. Projects in Kiln:
    424242975950  Forum Moderation
    118230554411  Support Triage
  ```

  Tasks are listed the same way under `--task-id is needed …`. With a task ID that matches several projects, the projects that contain it are listed.
- **An unknown project or task ID is passed:** the same list, headed `Kiln has no project 999.` or `Project Forum Moderation has no task 999.` This applies in both modes: a wrong ID is a typo to fix, not a question to ask.

### 5.4 The project folder

- When `--project-path` isn't given, the folder is the parent of the project's `path` in Kiln, which names its `project.kiln`. If Kiln gives no path, that's an error asking for `--project-path`.
- When `--project-path` is given, its resolved folder must match Kiln's resolved path for the project. If they differ, it's an error:

  ```
  kiln-harness-tuner: --project-path is /a/b, but Kiln has project 424242975950 at /c/d.
  fix: drop --project-path, or pass /c/d.
  ```

  The read fence denies the folder it's given. A wrong path would leave the real project, with its val and test data, open to the agents.

## 6. The full command

When the tuner filled in any of `--project-id`, `--task-id` or `--project-path` (by picking, by the only choice, by looking up the folder, or through `--demo-project`), it prints a command that re-runs the same thing with no questions:

```
To run this again without the questions:
  kiln-harness-tuner --project-path '/Users/me/Kiln Projects/Forum Moderation' --project-id 424242975950 --task-id 424242783061 --guidance 'Keep the output format.'
```

- It is built from the arguments the user typed, with `--demo-project` removed and the three flags added or replaced. It is quoted with `shlex.join`.
- It is printed before the readiness and key checks, so a user who stops there can re-run on the same project without picking again, and without making another demo copy.

## 7. Readiness check

There are three GETs: the task's run configs, its evals and its specs. The task is ready when both of these hold:

1. **At least one run config.** This is the same rule setup uses to seed the baseline: a row with an ID.
2. **At least one eval the loop can measure:** an active eval with a default eval config. This is the loop's measurement rule (`_active_evals`): active means no spec, or a spec whose status is `active`.

Splits are not checked here. Setup's existing split audit stays where it is and keeps its own message.

When it fails, every problem is listed at once:

```
Task Forum Moderation (424242783061) isn't ready to tune:
  • It has no run configs. Create one in Kiln: http://localhost:8757/optimize/<project_id>/<task_id>
  • It has no eval the loop can measure. Add one in Kiln: http://localhost:8757/specs/<project_id>/<task_id>
[r]etry  [o]pen the first link  [t]ask: pick another  e[x]it:
```

- If there are active evals but none has a default eval config, the eval line says that instead: `Its active evals have no default eval config: "Tone", "Accuracy". Set one on each eval's page in Kiln: <link>`.
- `[t]` appears only when the task came from the picker. It goes back to the task picker for the same project.
- If Kiln reports evals it couldn't load (`load_error_count`), one warning line names the count. That doesn't fail the check.
- The check runs on every start, including when IDs are passed, and including the demo, which passes.

## 8. API-key check

### 8.1 Which providers

The run needs a provider for each of these:

| Source | Blocks with the fix line |
|---|---|
| The baseline run config's `model_provider_name` | "the task's run config <name>" |
| Each measurable eval's definition and default eval config: every `model_provider` value found in them. That covers a legacy judge, a V2 `llm_judge`, and a simulated user | "eval <name>'s judge" (or "simulated user") |
| Each provider that `--model-policy` names exactly in an allow rule or an exception | "--model-policy" |

- **The baseline** is the run config the run will start from, by the rule setup already uses: the one the tuner recorded for this task in Kiln's memory, else the task's default run config, else the most recent one. The check reads that rule; it doesn't write the record.
- **A policy rule whose provider is `*`** names no provider, so it adds none. Without a policy, the planner only sees models Kiln already offers, which by definition have keys.
- **Providers that don't take an API key** are not checked: `ollama`, `docker_model_runner`, `openai_compatible`, `kiln_custom_registry` and `kiln_fine_tune`. The model preflight still tests their models, as today.

### 8.2 The check

`GET /api/available_models` lists only the providers whose keys Kiln has (Kiln's `get_available_models` adds a key provider only when every required key is set). A provider is missing when its ID isn't in that list.

```
Kiln has no API key for 2 providers this run needs:
  • Fireworks AI (fireworks_ai), used by the task's run config "Baseline"
  • OpenAI (openai), used by eval "Tone"'s judge
Add them in Kiln under AI Providers: http://localhost:8757/settings/providers
[r]etry  [o]pen AI Providers  e[x]it:
```

- The display name comes from `GET /api/settings/available_providers`. If that has no entry, only the ID is shown.
- For a `--model-policy` provider, the line adds: `or remove it from --model-policy`.
- This check never reads `/api/settings`, which holds the stored keys.

## 9. `--demo-project`

### 9.1 The flag

`--demo-project` makes a fresh copy of the demo project, imports it into Kiln, and runs on it. Every other flag works as usual (`--guidance`, `--timeout-mins`, `--budget-usd`, and the rest), with the usual defaults.

It can't be combined with `--project-id`, `--task-id` or `--project-path`; argparse reports that with its usual error.

### 9.2 The copy

1. **Folder.** `~/Kiln Projects/`, the folder Kiln's own "create project" uses (`default_project_path()` in `kiln_server/project_api.py`). It is created if it's missing.
2. **Name.** `Forum Moderation`. If anything already exists with that name (a folder, a file or a link), the CLI tries `Forum Moderation-1`, then `-2`, and so on. A name is claimed by creating its folder (`mkdir`, failing if it exists), so two runs started together can't share a folder. Nothing is ever overwritten.
3. **Files.** The packaged project folder (§10) is copied into it.
4. **Fresh project ID.** The copy's `project.kiln` gets a new 12-digit ID that no project in Kiln already has. Its `name` becomes the folder name (e.g. `Forum Moderation-1`), so copies can be told apart in Kiln. The task ID stays `424242783061`.
5. **Import.** `POST /api/import_project?trusted=true&project_path=<folder>/project.kiln`, the call the README's `curl` makes today. `trusted=true` is needed because the eval runs code: the scorer that ships with the demo.
6. **Output:**

   ```
   Demo project copied to /Users/me/Kiln Projects/Forum Moderation-1
   and imported into Kiln as project 731954460212 (task 424242783061).
   ```

Then the run goes on from step 6 of §1. The full command is printed, so the same copy can be run again.

### 9.3 Failures

- **Import refused by Kiln:** Kiln's message is printed, the folder this call created is deleted (it holds nothing but the fresh copy), and the CLI exits 1.
- **Copy failed** (disk full, permissions): the error names the folder. The partial folder is deleted, and the CLI exits 1.
- **The demo's model needs a Fireworks key.** The API-key check (§8) covers it like any task. A user who stops there keeps the imported copy and the printed command.

## 10. The demo in the package

- `examples/moderation/Forum Moderation/` moves into the package as package data, for example `src/kiln_harness_tuner/demo_project/Forum Moderation/`. Git keeps one copy, and a clone and an installed wheel read the same files through `importlib.resources`.
- Only the Kiln project moves. `examples/moderation/posts.jsonl`, `scorer.py` and `README.md` stay in `examples/`, out of the package, because they hold every split's labels and the house rules.
- **Read fence.** The packaged copy holds the eval inputs and labels of every split, test included. Every run's read fence denies the packaged demo folder by path. Today the fence catches it only because it sits under the repository; the install branch moves agents out of the repository, and an installed wheel puts it in site-packages.
- `examples/moderation/README.md` replaces its copy, import and run steps with `kiln-harness-tuner --demo-project`. The tests that read the example's files follow the move.
- The root README's quickstart still copies from `examples/moderation/Forum Moderation`, so it breaks with the move. Steve owns that file; the summary flags it for him.

## 11. Flags and behavior summary

| Flag | Change |
|---|---|
| `--project-id` | Optional. Picked, or taken from the only choice, when missing. |
| `--task-id` | Optional, the same way. Can find its project on its own. |
| `--project-path` | Optional. Taken from Kiln when missing. When given, it must match Kiln's path for the project. |
| `--demo-project` | New. Copies, imports and runs the demo. Can't be combined with the three flags above. |
| `--kiln-host` | Unchanged. All the checks and links use it. |
| `--model-policy` | Unchanged, except that it is loaded earlier. Its providers are part of the key check. |

New start-up behavior: the Kiln check, the version check, the picker, the full command, the readiness check, the API-key check, and the shared retry prompt.

## 12. Output and exit codes

- Prompts, picker lists, "Using project …" lines and the full command go to stdout.
- Errors and warnings go to stderr, prefixed `kiln-harness-tuner:`, as today.
- Exit codes:
  - 1: a failed check when not interactive, `x` or end of input at a prompt, an unknown ID, an import or copy failure.
  - 130: Ctrl-C at a prompt.
  - Every existing exit path is unchanged.

## 13. Docs

- **`docs/install.md`:**
  - Section 4: the Kiln check and the version check, what each message means, and that the CLI waits or fails fast.
  - Section 6: the API-key check. Also correct two claims there: `/api/settings` returns `[hidden]`, not the keys; and `/api/available_models` lists only providers with a key, so it is the check, not "not a substitute".
  - A short `--demo-project` section.
  - Step zero and the verify block stay; the install branch owns them.
- **`docs/your-own-task.md`:** the optional IDs, the picker and its non-interactive error, the full-command line, what the readiness check needs and where to fix it in Kiln, `--demo-project`, and the flag table.
- **Not changed:** the root `README.md`, which belongs to Steve.

## 14. Tests

All offline, through a fake Kiln and a scripted terminal (input lines plus the `isatty` answers):

- Kiln unreachable, interactive: the message, then `r` succeeds on the second try, and `x` exits 1.
- Kiln unreachable, not interactive: the message on stderr and exit 1, with no input read.
- Something other than Kiln on the port, too-old Kiln (each missing API named), and an unreadable `openapi.json` (a warning, then it goes on).
- The picker with 0, 1 and several projects. The same for tasks. Invalid input asks again.
- Missing IDs when not interactive: the error lists the projects or tasks. Unknown IDs. A task ID matching several projects. A `--project-path` mismatch.
- The full command: the right flags, quoted, and printed only when something was filled in.
- Readiness: no run config, no measurable eval, active evals without a default config, and `[t]` back to the task picker.
- API key missing: the baseline's provider, a judge's provider, a policy provider, and the skipped no-key providers. Interactive retry, and not-interactive exit.
- The demo folder name: the first copy is `Forum Moderation`, then `-1`, then `-2`, and an existing file of that name also counts as taken. The fresh ID is unique, the name matches the folder, the import call is right, and a failed import removes the folder.
- The read fence denies the packaged demo folder.

## 15. Out of scope

- Building evals.
- Windows.
- State paths: where runs, traces and reports live, which the install branch owns.
- Starting Kiln for the user.
- Checking that a split has enough items (the detectability check does that later), and checking that a split exists (setup's audit).

## 16. Coordination

- The work starts from `main`. PR #1 has merged and the install branch hasn't.
- The new code lives in its own modules. `cli.py` changes only in argument parsing, one call at the top of `_run`, and moving the `--model-policy` load earlier. The fence change is one deny entry in `read_fence_for`.
- When the install branch merges, the demo folder's deny moves with the fence code. The rename to `harness-tuner` is the one constant from the opening section.
