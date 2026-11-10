# Install

Five things have to be true before a run starts. Each of the five **numbered**
sections below has the same four parts: what it is for, the command that checks
it, what a failure looks like, and the command that fixes it. Section 4 also
says how to install the agent CLI. The five unnumbered sections — platform
support, guided or not, trying the demo, where the tuner keeps its files, and
the verification block at the end — are not requirements and do not have that
shape.

The order is dependency-first. A reader who stops halfway has a working subset
rather than a half-configured machine.

Some failure messages below carry a value from your own machine: the shell's
`command not found` carries the name you typed, `curl`'s carries the port you
asked for, and the Python-floor error carries the exact version of the
interpreter that was refused.

## Platform support

> **Linux (x86_64 and arm64) and macOS on Apple silicon are supported.**
>
> **Windows is not supported.** `cli.py` imports `fcntl`, which is Unix only,
> so `harness-tuner` cannot start. The agent sandbox fences are Seatbelt
> on macOS and bubblewrap on Linux.
>
> **macOS on Intel (x86_64) is not supported.** The lock pins `lancedb` and
> `pylance` versions that publish no macOS x86_64 wheel and no source
> distribution, so `uv sync` in a clone fails there. `uv tool install`
> resolves without the lock, so what it installs there depends on what PyPI
> has at the time, and it can need a source build.
> No test covers that combination.

## 1. Python 3.11+ and `uv`

`uv` installs the package and the interpreter it runs on. Nothing else in this
document works without it.

**Check.**

```
uv --version
```

It prints `uv`'s version.

**If `uv` is missing**, your shell says so:

```
zsh:1: command not found: uv
```

**Fix.** Install `uv` — the installer and the package-manager routes are at
`https://docs.astral.sh/uv/` — then open a new shell so its directory is on
`PATH`.

**The Python floor is 3.11**, declared in `pyproject.toml`. Your system
`python3` does not have to meet it: `uv` uses a Python 3.11 or newer that it
finds, and downloads one when it finds none. It cannot do that when automatic
downloads are off (`UV_PYTHON_DOWNLOADS`, or `python-downloads` in `uv`'s
settings, is `manual` or `never`) and the only Python it finds is older. Then the install
refuses before anything is installed, with a message that starts:

```
  × No solution found when resolving dependencies:
  ╰─▶ Because the current Python version (3.10.20) does not satisfy
      Python>=3.11 and harness-tuner==0.1.0 depends on Python>=3.11, we can
      conclude that harness-tuner==0.1.0 cannot be used.
```

**Fix.** Let `uv` fetch a supported interpreter:

```
uv python install 3.11
```

Under `never` this command refuses too (`Python downloads are not allowed`). Then run it as `UV_PYTHON_DOWNLOADS=manual uv python install 3.11`, or install a Python 3.11 or newer another way.

You do not have to make it your system Python. `uv` picks it for the package
on its own.

## 2. The package

**Install.** From any folder:

```
uv tool install harness-tuner
```

```
Installed 5 executables: harness-tuner, harness-tuner-gateway-mcp, harness-tuner-install-agents, harness-tuner-skill-installer-mcp, kiln_api_mcp
```

**Check.**

```
harness-tuner --help
kiln_api_mcp --help
```

Both print usage, from any folder.

**If the directory `uv` installs into is not on `PATH`**, your shell says:

```
zsh:1: command not found: harness-tuner
```

**Fix.** `uv tool update-shell`, then open a new shell. `uv tool dir --bin`
prints the directory that has to be on `PATH`.

**With no install**, `uvx` runs the tuner from `uv`'s cache:

```
uvx harness-tuner --help
```

Each `uvx harness-tuner …` works like `harness-tuner …`. The interpreter it
runs on is in `uv`'s cache, and every agent session of the run starts its MCP
servers with that interpreter. So do not clean the cache (`uv cache clean`)
while a run is live: its later sessions then fail
([limitations.md](limitations.md#operational)). For long runs, install the
tool.

**What a run starts.** Each agent session's MCP config starts the **loop
gateway**, the agents' only access to Kiln, with the interpreter that runs the
tuner: `<python> -P -m kiln_harness_tuner.gateway.server --role <role> …`. The
builder and the probe also get the skill installer, started the same way. So
no `uv`, no checkout and none of the tuner's commands has to be on the `PATH`
that the agent CLI inherits. The `harness-tuner-gateway-mcp` and
`harness-tuner-skill-installer-mcp` commands start the same servers by hand.

The `kiln_api_mcp` server (`call_kiln_api`) is for interactive Claude Code
sessions; a loop run does not use it. The `.mcp.json` that
`harness-tuner-install-agents --agent-cli claude --dest DIR` writes starts it
by bare name, so it has to be on your `PATH`: install the tool rather than use
`uvx` for it.

The name is `kiln_api_mcp`, with underscores. It is half the tool name an
interactive session calls (`mcp__kiln_api_mcp__call_kiln_api`) and the
server key in `.mcp.json`, so it is not renamed to match the others. Loop
agents never have it: their Kiln tools are `mcp__kiln_harness_tuner_gateway__…`.

### From a clone

Contributors run the tuner from a clone, in the development environment that
`uv sync` builds:

```
git clone https://github.com/Kiln-AI/harness-tuner.git
cd harness-tuner
uv sync
uv run harness-tuner --help
```

The last command prints the usage block. A failure here is almost always
section 1: an interpreter below the floor, or `uv` not on `PATH`.

A run from a clone reads the agents' files from
`src/kiln_harness_tuner/agent_files/` and keeps its state in the same folder
as an installed tuner. No code looks for the clone.

## 3. A running Kiln studio server

Every measurement the loop takes is a Kiln eval job, and every agent's only
access to your project is the REST API of this server. The tuner itself also
reads and writes the project on disk: the split check, the eval inputs, the
stored eval rows, skill installs and the clean-up of its probe configs. So it
runs on the same machine as the Kiln server, and `--project-path` names the
folder of the project that the server has open. The loop defaults to
`http://localhost:8757`; `--kiln-host` overrides it, and that host is injected
into every agent's MCP configuration, so the orchestrator and its agents always
agree.

**This repository does not ship that server, and it is not a Python package you
can install from here.** It is part of Kiln itself. The addresses below are not
chosen by this document — they are what `kiln-ai`, this package's own
dependency, declares in its distribution metadata, and you can read them back
off your own install:

```
uvx --from harness-tuner python -c "from importlib.metadata import metadata; \
print('\n'.join(metadata('kiln-ai').get_all('Project-URL')))"
```

```
Homepage, https://kiln.tech
Repository, https://github.com/Kiln-AI/kiln
Documentation, https://kiln-ai.github.io/Kiln/kiln_core_docs/kiln_ai.html
Issues, https://github.com/Kiln-AI/kiln/issues
```

Get Kiln from there and start it. A run needs a project with a task in it:
make one in Kiln, or let the tuner copy and import the demo project
(`--demo-project`, or `m` when Kiln has no projects; see
[Try the demo](#try-the-demo) below). You don't have to look the IDs up: when
`--project-id` or `--task-id` is left out, `harness-tuner` lists what
Kiln has and asks ([your-own-task.md](your-own-task.md) section 5). Everything
after this section assumes a server you can reach.

**The minimum version is Kiln v1.3.** The loop uses three server APIs that
Kiln v1.2 does not have: the memory API, the eval-job API and run-config
provenance.

**Check.**

```
curl -sS http://localhost:8757/ping
```

A running server answers:

```
"pong"
```

**If it is not running**, `curl` fails to connect. The port in the message is
the one you asked for:

```
curl: (7) Failed to connect to localhost port 9999 after 0 ms: Couldn't connect to server
```

**Fix.** Start Kiln (see the addresses above if you do not have it yet) and run
the check again. If it does not listen on the default port, pass its host to
every `harness-tuner` invocation with `--kiln-host`, because the loop, its agents
and the MCP server all have to agree on one host.

### The Kiln check at start-up

`harness-tuner` makes this check itself, first, on the host it was given
(`--kiln-host`, default `http://localhost:8757`). It runs before any agent
session, model call or run folder, and it costs nothing. What it shows, and
how its menu works, depend on whether the start is guided
([Guided or not](#guided-or-not) below). The examples here are a guided start,
in the terminal app: a failed check is a card with a red border, and a
`What next?` menu follows it.

**Kiln answers.** One `GET /ping`, with a 5-second timeout. When nothing
answers:

```
╭─ ✗ Kiln isn't running at http://localhost:8757 ────────────────────────────────╮
│ The tuner works through the Kiln app, so Kiln has to be open on this computer. │
│   1. Install Kiln if you don't have it: https://kiln.tech/download ↗           │
│   2. Open the Kiln app and wait for its window to appear.                      │
│ Kiln on another port? Exit and pass --kiln-host http://localhost:<port>.       │
╰────────────────────────────────────────────────────────────────────────────────╯

What next?
 r) Check again (Kiln is open now)
 d) Open the download page
 x) Exit
```

The `↗` marks a link: a click on it opens the page. The first row of the menu
is highlighted.

When something answers but not with Kiln's `"pong"` (another program on the
port, or the wrong `--kiln-host`):

```
╭─ ✗ Something other than Kiln is answering at http://localhost:8757 ──────────────────╮
│ GET /ping returned HTTP 404, so this isn't the Kiln app.                             │
│   1. If Kiln is running on another port, exit and pass --kiln-host with its address. │
│   2. Otherwise close the program using this port, then open the Kiln app.            │
╰──────────────────────────────────────────────────────────────────────────────────────╯

What next?
 r) Check again
 d) Open the download page
 x) Exit
```

The explanation names the HTTP status, or says `returned an unexpected
answer` for a 200 that isn't `"pong"`.

**Kiln is new enough.** Kiln has no version endpoint, so the CLI reads Kiln's
API list once (`GET /openapi.json`) and checks that three APIs the loop calls
are there: the memory API, the eval-job API and run-config provenance. When any
is missing:

```
╭─ ✗ Kiln 1.0.3 is too old for the tuner ─────────────────────────────────────────────────╮
│ The tuner needs the memory API and run-config provenance, which this Kiln doesn't have. │
│   1. Update Kiln: in the app, open Settings → Check for update,                         │
│      or download the latest version from https://kiln.tech/download ↗.                  │
│   2. Quit and reopen Kiln so the update takes effect.                                   │
╰─────────────────────────────────────────────────────────────────────────────────────────╯

What next?
 r) Check again (I've updated and reopened Kiln)
 u) Open Kiln's update page
 d) Open the download page
 x) Exit
```

The explanation names only the APIs that are missing. `u` opens Kiln's own
update page, `http://localhost:8757/settings/check_for_update` on the default
host. When Kiln reports no version, or reports `unknown` as packaged builds
can, the title reads `This Kiln is too old for the tuner`.

**When it passes**, one step line says so, and the start goes on:

```
✓ Kiln      1.2.0 at http://localhost:8757
```

Without a version to show, it reads `running at http://localhost:8757`. When
the API list can't be read at all, the check can't tell the version, so it
warns and goes on, and a later call to a missing API fails with its own error:

```
! Couldn't read Kiln's API list, so its version wasn't checked.
```

**Not guided** (CI, a script, a pipe, another agent, or `--no-interaction`),
there is no menu. The error goes to stderr: the title, then the explanation,
with the address of each page the menu would have opened when the explanation
doesn't already give it. The CLI exits 1 without reading any input. Where
stderr is not a terminal, it reads:

```
harness-tuner: error: Kiln isn't running at http://localhost:8757
  The tuner works through the Kiln app, so Kiln has to be open on this computer.
    1. Install Kiln if you don't have it: https://kiln.tech/download
    2. Open the Kiln app and wait for its window to appear.
  Kiln on another port? Exit and pass --kiln-host http://localhost:<port>.
  Nothing was spent and no run folder was created.
harness-tuner stopped with an error (exit 1)
```

The later start-up checks — the project and the task, the readiness of the
task ([your-own-task.md](your-own-task.md) sections 1 and 5) and the API keys
(section 5 below) — show their problems the same way.

**Inside a run**, the once-per-run pre-flight checks the server again, this
time from where the agents stand: it dispatches one small agent session whose
only job is to call the gateway's `ping` operation. A failure there is fatal,
and its message names the rendered MCP config file, the host it is using, and
this document.

**If the server is older than v1.3**, the check above passes, because `/ping`
exists. A run passes its start-up checks too: the agent CLI check and the
gateway `ping` pre-flight each run one short agent session, and you pay for
them. Then the first memory read of setup gets HTTP 404, and the run stops
with exit status 1:

```
harness-tuner: GET /api/projects/<project-id>/memories -> HTTP 404: …
```

**Fix.** Upgrade Kiln to v1.3 or later, then run again.

## 4. Claude Code, signed in

By default every agent the loop dispatches is a `claude -p` subprocess, billed
to your Claude Code account. The loop takes no API key. `--agent-cli` selects
Codex or OpenCode instead (the first subsection below), and a guided start asks
which of the installed ones to use (the second).

**Install.** With Anthropic's installer, on macOS, Linux or WSL:

```
curl -fsSL https://claude.ai/install.sh | bash
```

Or as an npm package, with Node.js 22 or later (not with `sudo`):

```
npm install -g @anthropic-ai/claude-code
```

`command -v claude` then prints where it is. A guided start looks for it on
`PATH` the same way.

**Check.**

```
claude auth status
```

Signed in, it prints (among other fields):

```
"loggedIn": true
```

**If it is not signed in**, the same command prints:

```
"loggedIn": false,
"authMethod": "none",
```

and a dispatch fails immediately, with exit code 1:

```
Not logged in · Please run /login
```

**Fix.**

```
claude auth login
```

Inside a run, a signed-out machine is caught by the start-up check of
`--agent-cli`: before the run directory exists, one short prompt goes through
the CLI, and a CLI that does not answer stops the start with the reason and
the last lines it printed. Each later session is a separate dispatch. The dispatcher classifies **every** non-zero exit
as `agent_crash`, whatever the message says, so the diagnosis is not in the
class — it is in the stderr tail, which reaches the run log and the trace beside
it, carrying the line above.

`auth` is a different lane, and it is section 5's: it is how the loop labels a
**credential failure your model provider reports**. Nothing about the Claude
Code sign-in produces it. The classes are listed in
[agent-cli.md](agent-cli.md).

### Or: Codex or OpenCode, signed in

`--agent-cli` selects the agent CLI. With `--agent-cli 'codex exec -m MODEL'`
every agent is a `codex exec` subprocess, and with
`--agent-cli 'opencode run -m PROVIDER/MODEL'` every agent is an
`opencode run` subprocess. This section's Claude Code check then does not
apply.

**Install.** Each is an npm package:

```
npm install -g @openai/codex
npm install -g opencode-ai
```

`opencode-ai` is OpenCode's 1.x line, which the tuner's OpenCode support was
built against (OpenCode 1.19.3). OpenCode's own docs now point at
`@opencode/cli`, a separate 2.x line whose output changed; the tuner hasn't
been tested with it.

**Check.** Send the CLI one prompt, the way the loop does at start-up:

```
echo 'Reply with OK' | codex exec -m gpt-6.1-sol --skip-git-repo-check -
```

```
echo 'Reply with OK' | opencode run -m zai-coding-plan/glm-5.3-flash
```

Each one prints `OK`.

**Fix.** `codex login`, or `opencode auth login`. The loop uses the CLI's own
sign-in and takes no API key. It withholds secret-looking environment
variables from every session: a CLI that reads its key from one needs that
name in `KILN_HARNESS_TUNER_CHILD_ENV_KEEP`.

A run refuses to start when the CLI is not on the `PATH`, or when it does not
answer the start-up prompt. The message names the reason (for example a model
the account does not have) and the last lines the CLI printed.

Codex's sandbox is Seatbelt on macOS and bubblewrap on Linux; the fence every
Codex session runs under depends on it. OpenCode has no OS sandbox; its fence
is its own permission rules ([agent-cli.md](agent-cli.md) sections 10 and 11).

### The agent CLI question

When `--agent-cli` isn't given, a guided start asks which agent CLI runs the
tuner's agents ([your-own-task.md](your-own-task.md) section 5). It lists
Claude Code (`claude`), Codex (`codex`) and OpenCode (`opencode`), and shows a
CLI it can't find on `PATH` dimmed, as `not installed`, which can't be picked.
With only one installed, that one is used without the question (its model is
still asked), and the step line says so:

```
✓ Agents    Claude Code (claude -p) · opus · the only agent CLI installed
```

With none installed:

```
╭─ ✗ No agent CLI is installed ────────────────────────────────────────────────╮
│ The tuner's agents run through an agent CLI, signed in with its own account: │
│ Claude Code (claude), Codex (codex) or OpenCode (opencode).                  │
│ docs/install.md section 4 has the install and sign-in steps for each.        │
╰──────────────────────────────────────────────────────────────────────────────╯

What next?
 r) Check again (I've installed one)
 x) Exit
```

The question finds a CLI, not its sign-in. Whichever CLI the start ends with,
the run's own start-up sends it the test prompt above. Not guided, or with
`--agent-cli` given, nothing is detected and that test prompt is the check.

## 5. A model provider key in Kiln

Your task's run configs name a model, and Kiln needs a key for that model's
provider. This key is **not** the Claude Code sign-in of section 4: the agents
that run the loop and the model being optimized are different accounts, metered
separately, and reported as two separate spend lanes
([costs.md](costs.md)). The moderation example (`examples/moderation/`), which
is also the demo project, uses OpenRouter.

### The key check at start-up

`harness-tuner` checks this itself, once it knows the task and the task is
ready, before the run starts. Kiln must have a key for every provider the run
will call:

- **The baseline run config's provider.** The baseline is the run config the
  run will start from, by setup's rule: the one recorded for the task in Kiln's
  memory, else the task's default, else the most recent
  ([your-own-task.md](your-own-task.md) section 2.1).
- **The judge's provider of each eval the loop measures**, read from the eval's
  default config. A code eval calls no model, so it adds none.
- **The simulated user's provider, for a multi-turn task.** Kiln stores it on
  each eval input, so it is read from the `drive_config` of the task's eval
  inputs on disk. Nothing else in those files is read.
- **Each provider the model policy names exactly**, in an allow rule or an
  exception: the policy of `--model-policy`, or the one a guided start's
  advanced options set. A rule whose provider is `*` names none.

Providers that take no key are not checked: `ollama`, `docker_model_runner`,
`openai_compatible`, `kiln_custom_registry` and `kiln_fine_tune`. The model
preflight still tests their models.

The check reads `GET /api/available_models`. Kiln lists a provider that takes a
key there only when every key it needs is set, so a provider missing from that
list has no key. The check never reads `/api/settings`. When every key is
there, the step line names the providers, in the order the run needs them:

```
✓ API keys  Fireworks AI, OpenAI
```

When nothing the run calls takes a key, it reads `none needed (local models
only)`. When keys are missing, a guided start shows:

```
╭─ ✗ Kiln is missing 2 API keys this run needs ─────────────────────────────╮
│ The tuner calls these providers through Kiln, which needs a key for each: │
│   • Fireworks AI: used by the task's run config "Baseline"                │
│   • OpenAI: used by eval "Tone"'s judge                                   │
│ Add the keys in Kiln under Settings → AI Providers.                       │
╰───────────────────────────────────────────────────────────────────────────╯

What next?
 r) Check again (I've added the keys)
 p) Open AI Providers in Kiln
 x) Exit
```

- `p` opens the AI Providers page, `http://localhost:8757/settings/providers`
  on the default host.
- With one provider missing, the title reads `Kiln is missing an API key this
  run needs`, and the rest says `this provider`, `a key for it`, `Add the key`
  and `I've added the key`.
- A provider that only the model policy names ends its line with
  `, or remove it from the model policy`.
- Each `r` reads the policy file again and works out again what the run
  needs, so a fix in Kiln or in the policy counts.
- When an answer to the start's questions sets or changes the model policy,
  the check runs again with that policy before the summary
  ([your-own-task.md](your-own-task.md) section 5).
- Not guided, the same title and lines go to stderr with the AI Providers
  address, and the CLI exits 1, as for the Kiln check (section 3).

The check also notes the baseline run config's name, model and provider, which
the summary before the run shows as its "Starts from" line.

**Check by hand.** Ask Kiln whether it lists the provider:

```
curl -sS http://localhost:8757/api/available_models \
  | python3 -c "import json,sys; print(any(p.get('provider_id') == 'openrouter' for p in json.load(sys.stdin)))"
```

`True` means Kiln has the key. Substitute your provider's ID for
`openrouter`. The message names providers as Kiln's UI does, and
`PROVIDER_NAMES` in
[`onboarding/keys.py`](../src/kiln_harness_tuner/onboarding/keys.py) maps
each ID to that name: `gemini_api` is "Gemini API", `together_ai` is
"Together AI". A run config names its provider's ID in
`run_config_properties.model_provider_name`.

If the server is not running, `curl -sS` prints its `(7)` line and `python3`
then raises `JSONDecodeError` on the empty input. That is section 3, not this
section — fix it there and run this again.

`GET /api/settings` is not a better check. It shows `[hidden]` in place of each
stored key, never the key itself. And some providers need more than one
setting (Amazon Bedrock needs an access key and a secret key), which
`/api/available_models` checks for you.

**Fix.** Connect the provider in Kiln under AI Providers
(`http://localhost:8757/settings/providers`), which is where the key is
validated before it is stored.

**What a bad key looks like in a run.** The start-up check proves a key is set,
not that the provider accepts it. When the baseline's provider rejects its key,
the model preflight catches it before the baseline measurement: its one short
call on the baseline's model fails on auth, a hard failure, and that stops the
start (`--skip-model-preflight` in [cli.md](cli.md#checks-that-you-can-turn-off)).
Inside a run, Kiln serves job failures as free text, so the loop classifies
them by matching the message (`provider_errors.py`, which lists the families it
was built from). A key the provider rejects — "invalid api key",
"unauthorized", a 401 — is classified `auth`, which is terminal: the run stops
and writes a report rather than retrying something no retry fixes. A key
removed from Kiln after the start may word itself differently, match none of
those families, and land in the generic `provider_error` class, which is
retried. So the symptom to watch for is not one
message but a run that gets no eval job to complete. Check this section first
when that happens.

## Guided or not

Not a requirement: how the start behaves, which sections 3 to 5 refer to. The
start is **guided** when its stdin and its stdout are both terminals and
`--no-interaction` isn't given. Without a terminal (CI, a script, a pipe,
another agent) it is never guided, flag or not.

**In a terminal, `harness-tuner` opens a full-screen terminal app.** The app
shows the start and then the run, and the terminal shows its scrollback again
when the app closes. "A terminal" here means that stdin and stdout are both
terminals:

| Where it runs | The start | The run |
|---|---|---|
| A terminal | Guided, in the app | The run screen, in the app |
| A terminal, with `--no-interaction` | Step lines in the app, and no questions | The run screen, in the app |
| A terminal, with `--no-progress` | Guided, in the app. Then the app closes. | Plain log lines |
| A terminal, with both flags | Plain step lines | Plain log lines |
| No terminal | Plain step lines | Plain log lines |

The app reads its keys from stdin and draws on a real terminal. So a run with
stdin redirected (`harness-tuner < /dev/null`), or with stdout piped, gets
plain lines, and `FORCE_COLOR` or `TTY_COMPATIBLE` does not change that.
[your-own-task.md](your-own-task.md) section 5.5 describes the run screen.

**A guided start** opens with a card that names the tool, its version and
what it does. Then each finished step leaves one step line: `✓` in green for
a step that passed, `✗` in red for a check that failed, and `!` in yellow for
a warning, each with a fixed-width label. While it calls Kiln, a spinner line
says what it is doing, such as `Reading projects…` or
`Checking Kiln at http://localhost:8757…`. The spinner line goes when the call
ends. The newest line is always in view, and the title bar names the task
once the start has it. Colour is off when `NO_COLOR` is set.

```
╭─ Kiln Harness Tuner ─────────────────────────────────────────────╮
│ Automatically improves an AI agent by tuning its harness: the    │
│ prompt, model, tools, skills and settings around it.             │
╰───────────────────────────────────────────────────────── v0.1.0 ─╯

⠸ Checking Kiln at http://localhost:8757…
```

A failed check is a card with a red border: the `✗` title on the border, then
what to do. A `What next?` menu follows it.

- Each action has one key that acts at once, with no Enter. The arrow keys
  and Enter work too. The highlighted row is in reverse blue. When you choose,
  the menu goes.
- `r` runs the check again, with its spinner. If it still fails, a new card
  shows what is still wrong. Nothing polls: it checks only when you ask.
- An Open action opens your browser only where a graphical one can be
  expected: macOS, or Linux with `DISPLAY` or `WAYLAND_DISPLAY` set,
  and not over SSH. There the app shows `Opening <url>` and opens the page.
  Elsewhere it shows `Open this link in your browser: <url>` and starts no
  browser. Then the menu asks again. A link in the app also opens with a
  click.
- `x` stops with exit code 1.
- At any time in the start, Ctrl-C stops with exit code 130 and Ctrl-D with
  exit code 1, also while a check runs. Both print `harness-tuner: stopped.`
  The footer shows the two keys.

Once the checks pass, a guided start asks the questions its flags didn't
answer, and shows a summary before it starts the run
([your-own-task.md](your-own-task.md) section 5).

- **Esc goes back one step** at each of these questions but the first. The
  question before it is asked again, pre-filled with your answer, and the
  step line of that answer goes. At `Start the run?`, Esc asks the last
  question again. The footer shows `esc Back` only where Esc goes back. The
  project and task pickers and the `What next?` menus do not go back.
- In a picker with filter text, Esc first clears the filter, and the footer
  shows `esc Clear filter`.

**When the app closes**, it prints a plain closing block to the terminal,
because the app's own screen goes with it. The block holds the start's
summary, the command that runs it again (after a guided start), and, after a
run, the run's last lines ([your-own-task.md](your-own-task.md) section 5.5). With
`--no-progress`, the app closes once you start the run: the block prints, and
the run's plain log lines follow it.

**A start that isn't guided** asks nothing and waits for nothing. It runs the
same checks in the same order, and every option not given takes its default.

- **Without a terminal, or with both flags**, it prints the step lines as
  plain text, with no symbols, colour or spinners, and no welcome card. Then
  it prints the summary, and the run starts.
- **In a terminal with `--no-interaction` alone**, the app shows the welcome
  card, the step lines, the spinners and the summary card, as for a guided
  start. It asks no question, shows no menu and no command to run it again,
  and the run screen follows the summary.
- A failed check stops the start, and the CLI exits 1. Its error goes to
  stderr in the form below: a red `✗` title where stderr is a terminal, else
  `harness-tuner: error: <title>`. In the app, the error prints once the app
  has closed.
- A missing `--project-id` or `--task-id`, when Kiln has more than one to
  choose from, is an error that lists them
  ([your-own-task.md](your-own-task.md) section 5).

**An error that stops the start or the run** is shown the same way every
time, on stderr (in the app, once the app has closed). On a terminal it is a
red `✗` title, the error's own lines, what the stop left behind, and a last
line:

```
✗ Kiln returned an error

  GET /api/projects/424242189194/tasks/424242920784/evals/424242136270/eval_config/424242913607
  HTTP 500: Internal Server Error

  The run stopped. Spent $3.20 so far on the task's model calls.
  The agents used about 1,240,000 tokens, which the budget does not count.
  Run folder: /Users/you/.kiln_ai/harness_tuner/runs/261107-1412
  Report: /Users/you/.kiln_ai/harness_tuner/reports/kiln-harness-tuner-261107-1412.md

harness-tuner stopped with an error (exit 1)
```

- **The title** says what kind of error it is, for example
  `Lost the connection to Kiln` or `Another run is already using this task`.
- **A Kiln error** shows the request and Kiln's own message. A lost connection
  also asks whether Kiln is still running.
- **What the stop left behind** is one of three: nothing spent and no run
  folder; only the agent CLI's one test prompt spent; or the run's spend so
  far, its run folder, and its report when one was written.
- **Without a terminal** the same lines are plain text, and the first line is
  `harness-tuner: error: <title>`.
- A guided start shows an error that stops it as a card in the app first. The
  card goes with the app, so the error prints in full when the app closes.

The app draws on the terminal, and its closing block goes to stdout. A plain
start (without a terminal, or with both flags) writes its step lines and
summary to stdout, and its warnings to stderr. The stop lines
(`harness-tuner: stopped.`) and the errors always go to stderr. The exit
code is 1 for a failed check (not guided), `x` or Ctrl-D in the start, an
unknown ID, or a failed demo copy or import; 2 for an argparse error, such as
`--demo-project` with a project flag; and 130 for Ctrl-C.

Two flags control the two parts, the start and the run, and they are
independent:

| Controls | Flag | Effect |
|---|---|---|
| The start, before the run | `--no-interaction` | Never asks a question or waits. |
| The run itself | `--no-progress` | Plain log lines instead of the run screen. |

Without a terminal you need neither: the start isn't guided, and the run
shows plain log lines on its own. `--no-progress` was called `--no-ui`; the
old name now stops with exit code 2 and says so.

## Try the demo

Not a requirement: a first run on a task that is known to be ready.
`--demo-project` copies the demo project — the moderation task of
[examples/moderation](../examples/moderation/README.md) — into a new folder,
imports the copy into Kiln, and runs on it. It needs sections 1 to 5, including
an OpenRouter key in Kiln, because the demo's baseline calls its model
through OpenRouter. Section 5's check says so if the key is missing.

```
harness-tuner --demo-project
```

In a terminal, the start checks Kiln, makes the copy, checks it, and asks its
questions. With each pre-filled answer accepted, the app shows this:

```
╭─ Kiln Harness Tuner ─────────────────────────────────────────────╮
│ Automatically improves an AI agent by tuning its harness: the    │
│ prompt, model, tools, skills and settings around it.             │
╰───────────────────────────────────────────────────────── v0.1.0 ─╯

✓ Kiln      1.2.0 at http://localhost:8757
✓ Demo      ~/Kiln Projects/Forum Moderation, imported as project 731954460212
✓ Project   Forum Moderation (731954460212)
✓ Task      Forum Moderation (424242783061)
✓ Ready     1 run config · 1 eval the tuner can measure
✓ API keys  OpenRouter
✓ Improve   Quality
✓ Guidance  "Improve the moderation decisions. Keep the output format."
✓ Agents    Claude Code (claude -p) · opus
✓ Budget    $50.00
✓ Time      12 hours
✓ Advanced  defaults

╭─ Ready to start ────────────────────────────────────────────────────────────────────────╮
│ Task         Forum Moderation (424242783061) in project Forum Moderation (731954460212) │
│ Starts from  run config "Baseline": deepseek_4_1_flash_1017 on OpenRouter               │
│ Improve      Quality                                                                    │
│ Guidance     "Improve the moderation decisions. Keep the output format."                │
│ Agents       Claude Code (claude -p) · opus                                             │
│ Limits       $50.00 on the task's model calls · about 12 hours                          │
│ Advanced     defaults                                                                   │
│                                                                                         │
│ To run this again with the same answers and no questions:                               │
│ harness-tuner \                                                                         │
│   --project-id 731954460212 --task-id 424242783061 \                                    │
│   --project-path '/Users/you/Kiln Projects/Forum Moderation' \                          │
│   --objective quality \                                                                 │
│   --guidance 'Improve the moderation decisions. Keep the output format.' \              │
│   --agent-cli 'claude -p' \                                                             │
│   --budget-usd 50 --timeout-mins 720 \                                                  │
│   --no-interaction                                                                      │
╰─────────────────────────────────────────────────────────────────────────────────────────╯

Start the run?
 s) Start the run
 e) Change an answer
 x) Exit
```

- **The questions.** The project and the task are the copy's, so they aren't
  asked. The guidance is pre-filled with the demo's own text. Every other
  question works as usual ([your-own-task.md](your-own-task.md) section 5), and
  a flag answers its question, so
  `harness-tuner --demo-project --budget-usd 20 --timeout-mins 60` asks
  neither the budget nor the time limit. Not guided, nothing is asked, and the
  guidance is empty unless `--guidance` gives it.
- **Where the copy goes.** `~/Kiln Projects/`, the folder Kiln's own "create
  project" uses, made if it is missing. The first copy is `Forum Moderation`.
  When that name is taken (by a folder, a file or a link), the copy goes to
  `Forum Moderation-1`, then `-2`, and so on. Nothing is overwritten. While it
  copies and imports, a spinner says which.
- **Each copy is its own project.** It gets a new project ID, and its name in
  Kiln is its folder's name, so copies can be told apart. The task ID is always
  `424242783061`.
- **The import is trusted** (`trusted=true`), because the demo's eval runs
  code: the scorer that ships with it.
- **The printed command runs on the same copy again**, with the copy's IDs and
  folder. `--demo-project` again makes another copy. A start that stops before
  the summary, at the key check say, keeps its copy in Kiln: the next
  `harness-tuner` offers it in the project list, or uses it when it is
  the only project.
- **Kiln with no projects.** A guided start that finds none offers
  `m) Use the demo project`, which does exactly what `--demo-project` does.
- **`--demo-project` can't be combined** with `--project-id`, `--task-id` or
  `--project-path`: argparse says so, and the exit code is 2.
- **When Kiln refuses the import, or the copy fails** (a full disk,
  permissions), the folder this start made is deleted, the message says why,
  and the CLI exits 1.

[examples/moderation](../examples/moderation/README.md) describes the task,
and how to check the result on test yourself.

## Where the tuner keeps its files

The tuner writes nothing in the folder it is started from. Everything a run
keeps is in one **state folder**:

- `~/.kiln_ai/harness_tuner/` by default. Kiln keeps its own settings in
  `~/.kiln_ai`.
- The folder that `KILN_HARNESS_TUNER_HOME` names, when it is set. An empty
  value counts as unset, `~` is expanded, and a relative path is read from
  the folder the tuner is started in.

The tuner creates the folder on first use, with mode `0700`: it holds session
transcripts and val data. It does not change the mode of a folder that is
already there.

```
<state>/
  runs/<run_id>/                       one run: its log (kiln-harness-tuner.log),
                                       wave log, rendered settings and MCP
                                       configs, and each agent's scratch
  runs/.lock-<project>-<task>          the one-run-per-task lock
  runs/.quarantine/<project>-<task>/   the loop's archived probe and eval
                                       configs
  traces/<project>/<task>/<run_id>/    the copied session transcripts
  reports/kiln-harness-tuner-<run_id>.md   the run reports
  agents/<cli>/<version>/              the skills and instructions this
                                       agent CLI's sessions read
```

A run prints where its files are. With plain log lines, it prints this line
when the run folder exists, before the run's first log line:

```
harness-tuner: run 260601-0930 — files in /home/you/.kiln_ai/harness_tuner/runs/260601-0930/
```

In the app, that line goes to the run folder's `kiln-harness-tuner.log`, and
the title bar shows the run id. When the app closes, its closing block names
the run folder as `Run folder: <run folder>`
([your-own-task.md](your-own-task.md) section 5.5).

At a clean finish:

```
harness-tuner: run finished — report: /home/you/.kiln_ai/harness_tuner/reports/kiln-harness-tuner-260601-0930.md
```

When a run stops after its folder exists, it names that folder. On an error,
the error screen shows `Run folder: <run folder>`, and `Report: <path>` when
the error exit wrote a fallback report ([Guided or not](#guided-or-not)). On
a stop (Ctrl-C, "Stop the run" in the app, SIGTERM or SIGHUP) it prints
`harness-tuner: run folder: <run folder>`. That folder's
`kiln-harness-tuner.log` holds the details. The run log also holds anything
that was written to stdout or stderr while the app was open.

**The agents folder.** Before its first session, a run checks
`agents/<cli>/<version>/` against the package's agent files, for its own
agent CLI and the installed version. It builds the folder when it is missing
or different, and uses it as it is when it matches. A new version gets a new
folder, so a live run on the old version is not disturbed. Old version
folders are not deleted; each is about 2 MB. Claude Code sessions run in this
folder. Codex and OpenCode sessions run in their own scratch folders, which
link to it.

**Refusals.** The run stops at start-up, before any agent session, with a
`fix:` line:

- when the state folder cannot be created or written;
- when the state folder and the run's Kiln project overlap (one inside the
  other): the agents may not read the project, and they must write in the
  state folder;
- when the package has no agent files (reinstall `harness-tuner`), or the
  agents folder cannot be built.

**The lock is per state folder.** Two runs on the same task with the same
state folder cannot run at once. Two runs with different
`KILN_HARNESS_TUNER_HOME` values are not stopped.

**Old state in a clone.** A clone used before the state folder existed can
hold `runs/`, `traces/` and `reports/` at its root. The tuner does not move or
read them, and no fence denies them to the agents any more. Delete them, or
move them out of the clone.

## Verify the whole install

```
uv tool install harness-tuner
harness-tuner --help
kiln_api_mcp --help
curl -sS http://localhost:8757/ping
claude auth status
```

From a clone, `uv sync` and then `uv run pytest` run the test suite.

**What this proves, and what it does not.** The commands prove that the
package is installed, that the server answers and that the agent CLI is
signed in. The suite is fully offline — a fake dispatcher, a fake Kiln client,
canned transcripts — so it proves the code works on this machine. It spends
nothing, contacts no server, and therefore says nothing about sections 3, 4
and 5. The whole lane is proven by the pre-flight of your first run, which is
the first thing that costs money.

## Next

- [your-own-task.md](your-own-task.md) — what your task needs before the loop
  can work on it.
- [costs.md](costs.md) — estimate the run before you start it.
- [concepts.md](concepts.md) — the vocabulary the report will use.
