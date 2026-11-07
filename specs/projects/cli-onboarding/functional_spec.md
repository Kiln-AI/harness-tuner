---
status: complete
---

# Functional Spec: CLI onboarding

A new user runs `kiln-harness-tuner`, with no flags or with `--demo-project`. In a terminal it is a **guided start**, which:

1. checks that Kiln is up and new enough;
2. picks the project and the task;
3. checks the task is ready and the keys are there;
4. asks the questions that matter for a run;
5. shows a summary and starts on Enter.

Without a terminal, or with `--no-interaction`, it asks nothing. It runs the same checks, fails fast with the same messages, and uses defaults for everything not given.

No agent session, model call or run folder happens before the checks pass.

The command name stays `kiln-harness-tuner`, as on `main`. Every message, every printed command and the docs use the name from one constant (the parser's `prog`). When the install branch renames the command, that is a one-line change.

**Build history.** Phases 1–5 built the checks, the picker, `--demo-project` and the docs with plain `print`/`input` prompts. Phases 6–8 rebuild the terminal side as described here:
- the look and feel (§3);
- every message (§4–§9);
- the questions (§10);
- the summary and the rerun command (§11);
- the new defaults (§12).

The check logic from phases 1–5 stays: which API, which rule, which provider.

## 1. Start-up order

1. Parse flags. Argparse errors work as today.
2. **Welcome box** (guided start only, §18).
3. **Kiln check** (§4).
4. **`--demo-project` only:** copy and import the demo (§9).
5. **Project, task and folder** (§5).
6. **Readiness check** (§6).
7. **API-key check** (§7): the baseline's provider, the judges' providers, a multi-turn task's simulated user, and the providers a `--model-policy` given on the command line names.
8. **The questions** (§10), guided start only.
9. **The key check again**, only when §10 set or changed the model policy, so the policy's providers are covered.
10. **The summary** (§11). In a guided start it ends with the rerun command and the start prompt. Without a guided start it is printed and the run starts.
11. The existing start-up, unchanged: install the agent files, check the agent CLI, take the run lock, and so on.

## 2. Guided or not

- The start is **guided** when both stdin and stdout are terminals (`isatty()`) and `--no-interaction` is not given.
- Without a terminal (CI, a script, a pipe, another agent), the start is never guided, flag or not.

**A flag on the command line answers its question.** A guided start asks only what the flags didn't answer. A flag counts as given when it appears on the command line, even with its default value spelled out.

**Not guided:**
- A failed check prints its message to stderr and exits 1.
- A missing project or task ID, with more than one choice, is an error that lists what Kiln has (§5.3).
- Every option not given takes its default (§12). Nothing waits for input.

**`--no-interaction` and `--no-progress` are independent.** Both are documented side by side (§14):

| Flag | Controls | Effect |
|---|---|---|
| `--no-interaction` | the start, before the run | Never asks a question or waits. |
| `--no-progress` | the run itself | Plain log lines instead of the live progress display. Renamed from `--no-ui` (§12). |

## 3. Look and feel

**Output** (rich, already a dependency):
- One header line, then one **step line** per finished step:
  - `✓` in green for a step that passed, with a fixed-width label: `Kiln`, `Project`, `Task`, `Ready`, `API keys`, and one per answered question.
  - `✗` in red for a failed check, followed by its explanation.
  - `!` in yellow for a warning.
- A blank line separates each failed check, each question and the summary from what comes before.
- **Spinners:** every call to Kiln shows a spinner with what it is doing, for example `Checking Kiln…` or `Reading the task's evals…`. The spinner disappears when the call ends.
- **Colour** turns off when `NO_COLOR` is set or stdout isn't a terminal (rich's own rules).
- A start that isn't guided prints plain text: no colour, no spinners, no step symbols beyond the words.

**Input** (questionary, on prompt_toolkit):
- **Pickers** (project, task, objective, agent CLI):
  - arrow keys move, typing filters (on name or ID), and Enter picks;
  - long lists scroll;
  - after the pick, the list is erased and replaced by the step line.
- **Action menus** (what to do after a failed check, and the start prompt):
  - each action has a single key that acts at once, with no Enter;
  - arrow keys plus Enter work too;
  - the menu is erased after the choice.
- **Text questions** (guidance, budget, time limit, model, the advanced values):
  - the default is pre-filled, Enter accepts it, and the hint line says what the value means;
  - an invalid answer shows the reason under the field and asks again, without clearing what was typed.
- **Stopping:** Ctrl-C at any prompt stops with exit 130, and Ctrl-D with exit 1. Both print `kiln-harness-tuner: stopped.`

**Opening a page:**
1. The action prints `Opening <url>` first.
2. It then opens the browser only where a graphical one can be expected: macOS, Windows, or Linux with `DISPLAY` or `WAYLAND_DISPLAY` set.
3. Otherwise it prints `Open this link in your browser: <url>`.

This keeps an SSH session from dropping into a console browser (backlog item 1).

**After a failed check**, a retry runs the check again with its spinner. If it still fails, the ✗ block is printed again, so the user sees what is still wrong. Nothing polls.

## 4. Kiln check

The checks themselves are unchanged from phases 1–5:
- `GET /ping`: one try, 5 seconds, and the answer must be `"pong"`.
- `GET /openapi.json`: it must list the memory API, the eval-job API, and `provenance` on `CreateTaskRunConfigRequest`.

**Passed:** `✓ Kiln      1.2.0 at http://localhost:8757`. The version is left out when Kiln reports none or `unknown`.

**Not running:**

```
✗ Kiln isn't running at http://localhost:8757

  The tuner works through the Kiln app, so Kiln has to be open on this computer.
    1. Install Kiln if you don't have it: https://kiln.tech/download
    2. Open the Kiln app and wait for its window to appear.
  Kiln on another port? Exit and pass --kiln-host http://localhost:<port>.

? What next?
 » r) Check again (Kiln is open now)
   d) Open the download page
   x) Exit
```

**Something else on the port:**

```
✗ Something other than Kiln is answering at http://localhost:8757

  GET /ping returned HTTP 404, so this isn't the Kiln app.
    1. If Kiln is running on another port, exit and pass --kiln-host with its address.
    2. Otherwise close the program using this port, then open the Kiln app.

? What next?
 » r) Check again
   d) Open the download page
   x) Exit
```

When the answer is a 200 that isn't `"pong"`, the second line reads "returned an unexpected answer".

**Too old:**

```
✗ Kiln 1.0.3 is too old for the tuner

  The tuner needs the memory API and run-config provenance, which this Kiln doesn't have.
    1. Update Kiln: in the app, open Settings → Check for update,
       or download the latest version from https://kiln.tech/download.
    2. Quit and reopen Kiln so the update takes effect.

? What next?
 » r) Check again (I've updated and reopened Kiln)
   u) Open Kiln's update page
   d) Open the download page
   x) Exit
```

- `u` opens `<host>/settings/check_for_update`.
- The list in the first line names only the missing APIs.

**API list unreadable:** `! Couldn't read Kiln's API list, so its version wasn't checked.` Then the start goes on.

**Not guided:** each ✗ title and its explanation go to stderr, prefixed `kiln-harness-tuner:`, without the menu. Then exit 1.

## 5. Project, task and folder

### 5.1 Which flags decide what

Unchanged from phases 1–5:

| Given | What happens |
|---|---|
| project and task | Both are used. Each is checked against Kiln (an unknown ID is an error, §5.3). |
| project only | Pick the task among that project's tasks. |
| task only | Find the projects that contain that task ID. If one does, use it. If several do (demo copies share a task ID), pick among them. If none does, error. |
| `--project-path` only | Find the project whose Kiln path is that folder. If none is, it's an error: the folder isn't imported in Kiln. Then pick the task. |
| nothing | Pick the project, then the task. |

### 5.2 The pickers

```
? Which project do you want to tune?   ↑↓ move · type to filter · Enter pick
 » Forum Moderation      424242975950   ~/Kiln Projects/Forum Moderation
   Support Triage        118230554411   ~/Kiln Projects/Support Triage
```

- Rows appear in Kiln's order, in aligned columns. The home folder is shown as `~`.
- The task picker reads `? Which task in Forum Moderation?` and shows each task's name and ID, with the task's description (from Kiln) under the highlighted row when there is one.
- **After the pick:** `✓ Project   Forum Moderation (424242975950)`, then likewise for the task.
- **One choice:** no question. The step line adds `· the only project in Kiln` (or `· the only task in this project`). This applies to both kinds of start.
- **No projects** (guided):

  ```
  ✗ Kiln has no projects yet

    The tuner works on a task in a Kiln project. Create one in Kiln, or start with
    the demo: a small moderation task with its evals and data already set up.

  ? What next?
   » m) Use the demo project
     o) Open Kiln to create a project
     r) Check again
     x) Exit
  ```

  - `m` does exactly what `--demo-project` does (§9), then the start goes on.
  - `o` opens `<host>/settings/create_project`.
- **No tasks** (guided): `✗ Forum Moderation has no tasks yet`, with the actions:
  - `o) Open Kiln to create a task`, which opens `<host>/settings/create_task/<project_id>`;
  - `r) Check again`;
  - `p) Pick a different project`, only when the project was picked from a list;
  - `x) Exit`.
- **Not guided:** the same titles and explanations go to stderr, then exit 1. The no-projects text names `kiln-harness-tuner --demo-project`.

### 5.3 Errors in both kinds of start

These are unchanged in substance, restyled as ✗ blocks:
- An ID is missing, there are several choices, and the start isn't guided. The error names the flag and lists what Kiln has: one line per row, ID then name.
- An unknown project or task ID, or a task ID that no project has. The error lists what Kiln has. A wrong ID is a typo to fix, not a question to ask.
- `--project-path` doesn't match Kiln's folder for the project. The error names both folders and the fix. This keeps the read fence on the real project.
- A project Kiln has no folder for, when `--project-path` isn't given.

### 5.4 The project folder

Unchanged:
- The folder is Kiln's (the parent of the project's `project.kiln` path).
- A given `--project-path` must match it.
- `~` is expanded.

## 6. Readiness check

The rule is unchanged: at least one run config with an ID, and at least one active eval with a default eval config. Splits are not checked here.

**Passed:** `✓ Ready     2 run configs · 3 evals the tuner can measure`.

**Not ready:**

```
✗ Forum Moderation isn't ready to tune yet

  The tuner starts from one of the task's run configs and measures every change
  with its evals. This task is missing:
    • A run config: the model and prompt the tuner starts from.
    • An eval it can measure: an active eval with a default judge set.

? What next?
 » r) Check again (I've fixed it in Kiln)
   c) Open Run configs in Kiln
   e) Open Evals in Kiln
   t) Pick a different task
   x) Exit
```

- **One action per link:** `c` opens `<host>/optimize/<project_id>/<task_id>`, and `e` opens `<host>/specs/<project_id>/<task_id>`. Each appears only when its line does.
- **Active evals with no default eval config:** the eval line reads `• A default judge on its active evals: "Tone" and "Accuracy" have none set.`, with the same `e` action.
- **`t) Pick a different task`** appears only when the task was picked from a list.
- **Evals Kiln couldn't load** (`load_error_count`): `! Kiln couldn't load 1 eval of this task, so the run can't measure it.` This doesn't fail the check.

## 7. API-key check

The providers and the rule are unchanged from phases 1–5:
- **What is checked:** the baseline's provider, each measured eval's judge, a multi-turn task's simulated user, and every provider `--model-policy` names exactly.
- **What is skipped:** providers that take no key.
- **The source:** `GET /api/available_models`.
- **Display names:** Kiln's own (`provider_name_from_id`), kept in the tuner.
- **Retries:** every try reloads the policy and works out the needs again.

**Passed:** `✓ API keys  Fireworks AI, OpenAI`. When nothing needs a key, it reads `✓ API keys  none needed (local models only)`.

**Missing:**

```
✗ Kiln is missing 2 API keys this run needs

  The tuner calls these providers through Kiln, which needs a key for each:
    • Fireworks AI: used by the task's run config "Baseline"
    • OpenAI: used by eval "Tone"'s judge
  Add the keys in Kiln under Settings → AI Providers.

? What next?
 » r) Check again (I've added the keys)
   p) Open AI Providers in Kiln
   x) Exit
```

- `p` opens `<host>/settings/providers`.
- The title is singular or plural to match the count. With one provider:
  - the title reads `Kiln is missing an API key this run needs`;
  - the body says `this provider`, `a key for it` and `Add the key`;
  - the retry label says `I've added the key`.
- A provider needed only because `--model-policy` names it adds `, or remove it from the model policy`.

**The baseline for the summary.** The check also records the baseline run config's name, model and provider, for the summary (§11).

## 8. Agent CLI detection

In a guided start, the agent CLI question (§10) lists Claude Code (`claude`), Codex (`codex`) and OpenCode (`opencode`).
- A CLI not found on `PATH` is shown dimmed as `not installed` and can't be picked.
- **One installed:** no question. The step line reads `✓ Agents    Claude Code (claude -p) · the only agent CLI installed`.
- **None installed:**

  ```
  ✗ No agent CLI is installed

    The tuner's agents run through an agent CLI, signed in with its own account:
    Claude Code (claude), Codex (codex) or OpenCode (opencode).
    docs/install.md section 5 has the install and sign-in steps for each.

  ? What next?
   » r) Check again (I've installed one)
     x) Exit
  ```

- **Not guided,** or with `--agent-cli` given: no detection. The existing start-up check of the agent CLI covers it, unchanged.

## 9. `--demo-project`

The copy is unchanged from phase 4:
1. It goes into `~/Kiln Projects/`, as `Forum Moderation`, then `-1`, `-2`, and so on. Nothing is overwritten.
2. The copy gets a fresh project ID, and its name matches the folder.
3. It is imported with `trusted=true`.
4. A failed copy, a refused import, or a Ctrl-C during the copy deletes the folder.

**Output:**
- spinners for `Copying the demo project…` and `Importing it into Kiln…`;
- then `✓ Demo      ~/Kiln Projects/Forum Moderation-1, imported as project 731954460212`.

**Guided start with the demo:**
- the project and task questions are skipped;
- the guidance question is pre-filled with the demo's own text: `Improve the moderation decisions. Keep the output format.`;
- every other question works as usual.

**Rerun command:** the copy's own IDs and folder, so a rerun uses the same copy instead of making a new one.

`--demo-project` still can't be combined with `--project-id`, `--task-id` or `--project-path` (exit 2).

## 10. The questions

These are asked in a guided start only. Each is skipped when its flag is on the command line.

### 10.1 Core questions, in order

| # | Question | Flag | Kind | Pre-filled | Step line |
|---|---|---|---|---|---|
| 1 | What should the tuner improve? | `--objective` | picker | Quality | `✓ Improve   Quality` |
| 2 | Guidance for the tuner's planner (optional) | `--guidance` | text | empty; the demo's text with `--demo-project` | `✓ Guidance  "…"` or `✓ Guidance  none` |
| 3 | Which agent CLI should run the tuner's agents? | `--agent-cli` | picker (§8) | Claude Code | `✓ Agents    Claude Code (claude -p)` |
| 4 | Model for the agents | `--agent-cli` (its `--model`/`-m`) | text | `opus` for Claude Code; empty (the CLI's own default) for Codex and OpenCode | added to the Agents line: `· opus` |
| 5 | Budget for this run | `--budget-usd` | text | `50` | `✓ Budget    $50.00` or `✓ Budget    no cap` |
| 6 | Time limit | `--timeout-mins` | text | `12h` | `✓ Time      12 hours` |
| 7 | Advanced options | the flags in §10.2 | action menu | Keep these | `✓ Advanced  defaults` or a list of what changed |

Each question's text, hint and validation:

1. **Objective.** The rows:
   - `Quality: higher eval scores`
   - `Speed: lower latency per request, without losing quality`
   - `Cost: lower cost per request, without losing quality`

   Hint: `One objective per run. Speed and cost only promote a change that keeps quality equal or better.`
2. **Guidance.** Hint: `What to try first and what not to touch, in your own words. For example: "Keep the output format. Don't switch models." Enter to skip.` Any text is valid, and empty means none.
3. **Agent CLI.** Each row shows the command it will run: `Claude Code: claude -p`, `Codex: codex exec`, `OpenCode: opencode run`. Hint: `The tuner's own agents (planner, builders, reviewers) run through this CLI and are billed to its account, not to your Kiln provider keys.`
4. **Model.**
   - Hint for Claude Code: `Enter keeps opus. sonnet or haiku makes the run cheaper, with weaker plans.`
   - Hint for Codex and OpenCode: `Enter uses the CLI's own default model. OpenCode models are PROVIDER/MODEL.`
   - The answer becomes part of `--agent-cli`: `claude -p --model sonnet`, `codex exec -m <model>`, `opencode run -m <model>`.
   - It is asked only when question 3 was asked or skipped by detection, not when `--agent-cli` was given.
5. **Budget.**
   - Hint: `The most this run may spend, in USD: the tuner's agents and the task's model calls together. No wave starts past it. Type none for no cap.`
   - When the agent CLI doesn't report its cost (Codex), the hint adds `<CLI> doesn't report its cost, so this caps the task's model calls only.`
   - **Valid answers:** a number above 0, with an optional leading `$` (`50`, `$12.50`), or `none`.
   - An invalid answer shows `Enter an amount in USD, like 50, or none`.
6. **Time limit.**
   - Hint: `No wave starts after this, and work still running at the limit is cancelled, so leave room for a whole wave.`
   - **Valid answers:** minutes (`90`), or a number with `m` or `h` (`90m`, `12h`, `1.5h`), at least 1 minute. The flag takes whole minutes, so the answer is rounded to the nearest minute.
   - An invalid answer shows `Enter a time like 90m or 12h`.
7. **Advanced.** One action menu, with the current values on one line:

   ```
   ? Advanced options: any model · 4 evals and 4 agents at once · no minimum effect · no noise check
    » Enter) Keep these
      c) Customize
   ```

   `c` asks each §10.2 question in turn, with the current value pre-filled. Values given by flags are shown and skipped.

### 10.2 Advanced questions (after `c`)

| Question | Flag | Pre-filled | Valid answers |
|---|---|---|---|
| Model policy file | `--model-policy` | empty: any model Kiln offers | a path to a policy file, which is loaded at once (a bad file shows the loader's message under the field), or empty |
| Smallest improvement worth acting on | `--min-effect` | empty: not set | a fraction above 0 and at most 1 (`0.1` = 10%), or empty |
| Eval jobs at once | `--max-concurrent-evals` | `4` | a whole number of 0 or more (0 = no limit) |
| Agent sessions at once | `--max-concurrent-agents` | `4` | a whole number of 0 or more (0 = no limit) |
| Noise check: items per eval re-run before wave 1 | `--noise-check` | `0` (off) | a whole number of 0 or more |

Hints:
- **Model policy:** `Which models the tuner may move the task to. examples/model-policy.json shows the format.`
- **Minimum effect:** `The run stops before spending when no eval can show this change on val.`
- **Concurrency:** `Lower these if your provider rate-limits you.`
- **Noise check:** `Measures how often the same config scores differently. Costs extra; it is estimated first.`

### 10.3 Never asked

These are flags only, documented in `docs/your-own-task.md`:
- `--kiln-host`
- `--coverage-floor`
- `--agent-model`
- `--agent-effort`
- `--max-turns`
- `--gate-agent`
- `--reference-check`
- `--no-recon`
- `--no-baseline-audit`
- `--allow-underpowered`
- `--skip-model-preflight`
- `--no-progress`

## 11. The summary and the start

**Guided:**

```
Ready to start
  Task         Forum Moderation (424242783061) in project Forum Moderation (424242975950)
  Starts from  run config "Baseline": deepseek_4_1_flash_1017 on Fireworks AI
  Improve      Quality
  Guidance     "Improve the moderation decisions. Keep the output format."
  Agents       Claude Code (claude -p) · opus
  Limits       $50.00 · 12 hours
  Advanced     defaults

To run this again with the same answers and no questions:
  kiln-harness-tuner \
    --project-id 424242975950 --task-id 424242783061 \
    --project-path '/Users/you/Kiln Projects/Forum Moderation' \
    --objective quality \
    --guidance 'Improve the moderation decisions. Keep the output format.' \
    --agent-cli 'claude -p' \
    --budget-usd 50 --timeout-mins 720 \
    --no-interaction

? Start the run?
 » s) Start the run
   e) Change an answer
   x) Exit
```

- **Starts from** names the baseline run config the key check found (§7), with its model and provider. If the check couldn't name one (a memory record with no run config), the line is left out.
- **Advanced** lists only what differs from the defaults, for example `model policy ./policy.json · 2 evals at once`. With nothing changed it reads `defaults`.
- **The rerun command** is printed here and nowhere else:
  - It is built from the command line as typed, with every flag the start fills in removed, plus every answer as an explicit flag, plus `--no-interaction`.
  - Flags the start never sets, such as `--no-progress` or `--agent-model`, are kept as typed.
  - It is split over lines with `\`, each value quoted with `shlex.quote`, so it pastes into a POSIX shell as is.
  - A guidance of none is `--guidance ''`, and a budget of no cap is `--budget-usd none`.
- **`e) Change an answer`** picks one of the answered questions (core or advanced), asks it again, and shows the summary again. Changing the model policy runs the key check again (§1 step 9).
- **`x) Exit`** stops with exit 1.

**Not guided:** the `Ready to start` block is printed (plain text, to stdout), with no rerun command and no prompt, and the run starts.

## 12. Defaults

The flag defaults change, and the questions pre-fill the same values:

| Flag | Before | Now |
|---|---|---|
| `--budget-usd` | no cap | `50`. Accepts `none` for no cap. |
| `--timeout-mins` | `180` | `720` (12 hours) |

Every other default is unchanged.

**`--no-ui` is renamed `--no-progress`**, which says what it turns off: the live progress display during the run. The old name joins `REMOVED_FLAGS` in `cli.py`, so `--no-ui` exits 2 with `--no-ui was renamed --no-progress: plain log lines instead of the live progress display`, instead of argparse's "unrecognized arguments".

## 13. Output and exit codes

- **stdout:** the guided start (header, step lines, prompts, summary). A start that isn't guided prints its summary there too.
- **stderr:** errors and warnings when the start isn't guided, prefixed `kiln-harness-tuner:`.
- **Exit codes:**
  - 1: a failed check when not guided; `x`, Exit or Ctrl-D at a prompt; an unknown ID; an import or copy failure.
  - 2: argparse errors, including `--demo-project` with a project flag.
  - 130: Ctrl-C.
  - Every existing exit path is unchanged.

## 14. Docs

- **`docs/install.md`:**
  - §4 and §6 and "Try the demo" are rewritten for the guided start's messages and actions.
  - A short section explains guided and not guided, with the `--no-interaction` and `--no-progress` table from §2.
- **`docs/your-own-task.md`:**
  - §5 describes the guided start: the questions, the summary and the rerun command.
  - The flag table gains `--no-interaction`, renames `--no-ui` to `--no-progress`, and gives the new defaults for `--budget-usd` (with `none`) and `--timeout-mins`.
- **`docs/costs.md`:** any statement of the old budget or timeout default.
- **`examples/moderation/README.md`:** `kiln-harness-tuner --demo-project` stays the one step. It notes the questions it asks.
- **Not changed:** the root `README.md`, which belongs to Steve. The summary flags the new defaults, `--no-interaction` and the `--no-progress` rename for him.

## 15. Tests

All offline, through a fake Kiln and a scripted terminal. The new behavior:

- **Guided or not:**
  - A terminal with `--no-interaction` asks nothing.
  - No terminal asks nothing.
  - A flag on the command line skips its question.
- **Each core question:**
  - the pre-filled value accepted;
  - a typed value;
  - each invalid answer rejected with its message;
  - the demo's pre-filled guidance.
- **Agent CLI detection:** none, one or several CLIs installed. The model question for each CLI, and how the answer becomes `--agent-cli`.
- **Advanced:**
  - Keep these.
  - Customize, with each value.
  - A bad policy file re-asks.
  - A policy set here runs the key check again.
- **The summary:**
  - each line, including the Starts-from line and its absence;
  - Advanced listing only changes;
  - Change an answer;
  - Exit;
  - the not-guided summary block.
- **The rerun command:**
  - every answer present;
  - flags it fills replaced, others kept;
  - `--no-interaction` added;
  - `none` values;
  - it parses back to the same answers.
- **The defaults:** `--budget-usd` defaults to 50 and accepts `none`; `--timeout-mins` defaults to 720.
- **The rename:** `--no-progress` gives the plain display, and `--no-ui` exits 2 with the renamed message.
- **Action menus:** one action per link, `m) Use the demo project` with no projects, and `p) Pick a different project` with no tasks.
- **Opening a page:** the URL is printed first, and the browser opens only where a graphical one can be expected.
- **The real terminal:**
  - its pickers, action menus and text questions, driven with prompt_toolkit's piped input;
  - its colour-off and spinner-off behavior when not a terminal.

The phase 1–5 tests keep passing, with their expected text updated to the new messages.

## 16. Out of scope

- Building evals.
- Windows. The chosen libraries support it, but this project doesn't test it.
- State paths, which the install branch owns.
- Starting Kiln for the user.
- Checking split sizes (the detectability check) and split presence (setup's audit).
- A combined objective ("better and cheaper"). One objective per run, as today.

## 17. Coordination

- The work starts from `main`. PR #1 has merged and the install branch hasn't.
- `cli.py` changes in argument parsing:
  - `--no-interaction`;
  - `--no-ui` renamed `--no-progress`;
  - the two new defaults;
  - `--budget-usd none`;
  - recording which flags were given.

  It also gets the `prepare_start` call. The fence change from phase 4 is one deny entry in `read_fence_for`.
- When the install branch merges:
  - the demo folder's deny moves with the fence code;
  - the rename to `harness-tuner` is the one `PROG` constant.

# Part 3: Polish and robustness (phases 9–10)

## 18. The welcome box

A guided start opens with a rounded box instead of the plain header line:

```
╭────────────────────────────────────────────────────────────────╮
│ Kiln Harness Tuner                                      v0.1.0 │
│                                                                │
│ Automatically improves an AI agent by tuning its harness: the  │
│ prompt, model, tools, skills and settings around it.           │
╰────────────────────────────────────────────────────────────────╯
```

- **Title:** `Kiln Harness Tuner`, in bold Kiln blue (`#415CF5`, the `primary` colour of Kiln's web UI).
- **Version:** `v<version>`, dimmed and right-aligned on the title row.
- **Description,** exactly: `Automatically improves an AI agent by tuning its harness: the prompt, model, tools, skills and settings around it.` It wraps inside the box.
- **Width:** the terminal's width, capped at 68 columns. With `NO_COLOR`, the box and the text stay and the colour goes.
- A blank line follows the box. A start that isn't guided prints no box, as before.

## 19. Error screens

Every error that stops the start or the run is shown the same way. This covers each error `main` catches:
- `OnboardingError`;
- `KilnError` and its subclasses;
- `SetupError` and its subclasses;
- `PreflightError`, `ConfigRenderError`, `RunLockError`, `AnalysisError`, `BaselineRecordMissingError`, `MemoryWriteRefused`, `RecordKindError`, `QuarantineError` and `TrainSetError`.

**On a terminal** (stderr is a terminal):

```
✗ Kiln returned an error

  GET /api/projects/424242189194/tasks/424242920784/evals/424242136270/eval_config/424242913607
  HTTP 422: Invalid Jinja2 template: No filter named 'trace_overview'

  The run stopped. Spent $3.20 so far (agents and the task's model calls).
  Run folder: /Users/you/harness-tuner/runs/261107-1412
  Report: /Users/you/harness-tuner/reports/kiln-harness-tuner-261107-1412.md

kiln-harness-tuner stopped with an error (exit 1)
```

**Without a terminal,** the same lines go to stderr in plain text, with the first line `kiln-harness-tuner: error: <title>` and no symbol or colour.

### 19.1 Titles

The title is red and bold, one short line per kind of error. The most specific class wins.

| Error | Title |
|---|---|
| `KilnTransportError` | Lost the connection to Kiln |
| `KilnAPIError` | Kiln returned an error |
| `RunLockError` | Another run is already using this task |
| `PreflightError` | The tuner's agents couldn't start |
| `ConfigRenderError` | A start-up option needs fixing |
| `EvalSplitAuditError` | Some evals have no train or val split |
| `SetupDescribeError` | The tuner couldn't describe an eval |
| `UnderpoweredEvalsError` | No eval can show a win on val |
| `ModelPreflightError` | The task's model failed its test call |
| `SetupPreconditionError` | The task isn't set up the way the tuner needs |
| any other `SetupError` | Setup stopped the run |
| `BaselineRecordMissingError` | The task has no baseline the tuner can use |
| `ContractPreflightError` | Kiln's API isn't what the tuner expects |
| `AnalysisError` | Measuring a candidate failed |
| `MemoryWriteRefused` | The tuner refused an unsafe memory write |
| `RecordKindError` | An agent named the wrong kind of record |
| `QuarantineError` | The tuner couldn't tell the splits apart |
| `TrainSetError` | The train split isn't clear |
| `OnboardingError` | its own title |
| anything else in the list | The run stopped |

### 19.2 The body

- **The body is the error's own message**, wrapped to the terminal and indented two spaces. A line starting `fix:` is kept on its own line.
- **A `KilnAPIError`** becomes two lines:
  - `<METHOD> <path>`;
  - `HTTP <status>: <message>`. The message is Kiln's own text, taken from the body's `error_messages` (joined with `; `) or `detail` (a string, or each item's `msg` joined with `; `), else the body as text. A leading `: ` and `Value error, ` are dropped, and the message is cut to 500 characters with `…`.
- **A `KilnTransportError`** keeps its message and adds `Is Kiln still running at <host>?`.

### 19.3 The ending

The ending says what state things were left in:

| How far the start got | Ending |
|---|---|
| Before the agent CLI check | `Nothing was spent and no run folder was created.` |
| After the agent CLI check, before the run folder | `The only spend was the agent CLI's one test prompt. No run folder was created.` |
| After the run folder was made | `The run stopped. Spent $<total> so far (agents and the task's model calls).`, then `Run folder: <path>`, then `Report: <path>` when the report file exists |

`<total>` is the run's spend meter total, with two decimals.

The last line is `kiln-harness-tuner stopped with an error (exit 1)`, dimmed. The exit code is unchanged (1).

**A guided start's own failures.** These are an `OnboardingError` that the guided start has already shown as a ✗ block. Only the last line is printed after them; the block is not shown twice. Nothing has been spent at that point.

**Unchanged:**
- `stopped.` after `x`, Ctrl-C or Ctrl-D at a prompt;
- the interrupted line after a signal;
- the clean-finish line.

## 20. Evals Kiln can't load

**A problem that belongs to one eval leaves that eval out of the run,** with a warning. A problem with Kiln itself still stops the start: Kiln unreachable, an auth or rate-limit error, a 5xx, or a response whose shape has changed.

**What counts:**
- An eval whose default eval config Kiln answers with **HTTP 400 or 422** is *an eval Kiln can't load*. The reason is Kiln's message, extracted as in §19.2.
- 404 and 410 stay as before: the config is gone.
- 401, 403, 429 and 5xx still stop the start with the §19 screen.

**Readiness** (§6) reads each measurable eval's default config, under the readiness spinner. For each eval Kiln can't load, it prints:

```
! Kiln can't load eval "Tone": Invalid Jinja2 template: No filter named 'trace_overview'.
  The run leaves it out. To use it, fix its judge in Kiln, or update Kiln if
  the eval was made with a newer version: http://localhost:8757/specs/<project>/<task>/<spec>/<eval>
```

- The link is the eval's own page when its spec is known, and the evals list otherwise.
- The passed step line counts only the evals that load, and adds `(N left out)`: `✓ Ready     2 run configs · 2 evals the tuner can measure (1 left out)`.
- **When every measurable eval is one Kiln can't load,** readiness fails. Its eval line reads `• An eval it can measure: Kiln can't load any of this task's evals (see the warnings).`, with the `e) Open Evals in Kiln` action.
- **Not guided,** the warnings go to stderr as `kiln-harness-tuner: …` lines, and the run goes on.

**The key check** (§7) skips an eval Kiln can't load, because its judge's provider can't be read.

**The run** leaves the same evals out, decided by the same rule from its own reads:
- **Setup** neither describes nor syncs them. Their existing issue memories are left exactly as they are.
- **The measurement set** (`_active_evals`) excludes them for the whole run, so they are never measured or gated.
- **The wave log** gets one `eval_unloadable` event per eval (id, name, reason).
- **The report** lists them under the evals it measured: `Left out: "Tone" (Kiln can't load it: …)`.
- **An eval that becomes unloadable mid-run** (someone edits it in Kiln) is handled by the existing per-item error handling. No new rule applies.

**Why not degrade them to an empty rubric,** as a 404 does in setup? An empty rubric changes the eval's fingerprint, so a paid re-describe would follow on every invocation while the eval stays broken. Leaving the eval out instead costs nothing and changes no stored record.

## 21. Tests (phases 9–10)

- **The welcome box:**
  - its text and the version;
  - the width cap on wide terminals, and wrapping on narrow ones;
  - no colour codes with `NO_COLOR`;
  - none when the start isn't guided.
- **The error screen:**
  - the title for each error class in §19.1;
  - the two-line `KilnAPIError` body for an `error_messages` body, a `detail` string, a `detail` list, and a plain body, with the 500-character cut;
  - the transport hint;
  - each ending row of §19.3, with spend, folder and report;
  - the plain stderr form without a terminal;
  - a guided start's shown `OnboardingError`, which gets the last line only.
- **Evals Kiln can't load:**
  - a 422 and a 400 leave the eval out;
  - 401, 403, 429 and 5xx still stop the start;
  - the readiness warning text and its link (with and without a spec);
  - the `(N left out)` step line;
  - every eval unloadable fails readiness;
  - the key check skips them;
  - setup neither describes nor syncs them, and leaves their memories untouched;
  - `_active_evals` excludes them;
  - the `eval_unloadable` event and the report's Left out line.
