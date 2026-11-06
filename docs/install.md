# Install

Six things have to be true before a run starts. Each of the six **numbered**
sections below has the same four parts: what it is for, the command that checks
it, what a failure looks like, and the command that fixes it. The three
unnumbered sections — platform support, trying the demo, and the verification
block at the end — are not requirements and do not have that shape.

The order is dependency-first. A reader who stops halfway has a working subset
rather than a half-configured machine.

**Step zero: get the repository.** Clone it, or download and unpack it, and run
every command below from its root. It has to be a real checkout: a run reads
the agents' skills and instructions (`.agents/`) out of that directory, and
`uv sync` and `uv tool install .` both act on the project in the current
directory.

Some failure messages below carry a value from your own machine: the shell's
`command not found` carries the name you typed, `curl`'s carries the port you
asked for, and the Python-floor error carries the exact version of the
interpreter that was refused.

## Platform support

> **macOS on Intel (x86_64) is not supported.** Section 2's `uv sync` fails
> there. The lock pins `lancedb` and `pylance` versions that publish no macOS
> x86_64 wheel and no source distribution. Linux (x86_64 and arm64), Windows
> (x86_64) and macOS on Apple silicon work.

## 1. Python 3.11+ and `uv`

`uv` builds the environment, runs the tests and installs the console scripts.
Nothing else in this document works without it.

**Check.**

```
uv --version
uv run python -V
```

The first prints `uv`'s version. The second prints the interpreter this
repository will actually use, and it is the one that has to be 3.11 or newer —
your system `python3` is not consulted.

**If `uv` is missing**, your shell says so:

```
zsh:1: command not found: uv
```

**Fix.** Install `uv` — the installer and the package-manager routes are at
`https://docs.astral.sh/uv/` — then open a new shell so its directory is on
`PATH`.

**The Python floor is 3.11**, declared in `pyproject.toml`. Below it, `uv` refuses before anything is built:

```
error: The requested interpreter resolved to Python 3.10.11, which is
incompatible with the project's Python requirement: `>=3.11` (from
`project.requires-python`)
```

**Fix.** Let `uv` fetch a supported interpreter:

```
uv python install 3.11
```

You do not have to make it your system Python. `uv run` picks an interpreter
that satisfies `requires-python` on its own.

## 2. The package

**Install.** From the repository root:

```
uv sync
```

**Check.**

```
uv run kiln-harness-tuner --help
```

It prints the usage block. A failure here is almost always section 1: an
interpreter below the floor, or `uv` not on `PATH`.

## 3. The console scripts

The loop's agents reach Kiln through the **loop gateway**: each
agent session's MCP config starts it with `uv run --project <this
repository> kiln-harness-tuner-gateway-mcp --role <role> …`, so it needs `uv` on the
`PATH` that `claude -p` inherits and nothing installed. The `kiln_api_mcp`
server (`call_kiln_api`), spawned by bare name from the `.mcp.json` that
`kiln-harness-tuner-install-agents --agent-cli claude` writes, is for
interactive Claude Code sessions; a loop run does not use it. Install the console scripts for those sessions and for `kiln-harness-tuner`
itself.

**Install.** From the repository root:

```
uv tool install .
```

```
Installed 4 executables: kiln-harness-tuner, kiln-harness-tuner-gateway-mcp,
kiln-harness-tuner-skill-installer-mcp, kiln_api_mcp
```

**Check.**

```
kiln_api_mcp --help
kiln-harness-tuner --help
```

Both print usage, from anywhere, without `uv run`.

**If the directory `uv` installs into is not on `PATH`**, your shell says:

```
zsh:1: command not found: kiln_api_mcp
```

**Fix.** `uv tool update-shell`, then open a new shell. `uv tool dir --bin`
prints the directory that has to be on `PATH`.

The name is `kiln_api_mcp`, with underscores. It is half the tool name an
interactive session calls (`mcp__kiln_api_mcp__call_kiln_api`) and the
server key in `.mcp.json`, so it is not renamed to match the others. Loop
agents never have it: their Kiln tools are `mcp__kiln_harness_tuner_gateway__…`.

## 4. A running Kiln studio server

Every measurement the loop takes is a Kiln eval job, and every agent's only
access to your project is the REST API of this server. The loop defaults to
`http://localhost:8757`; `--kiln-host` overrides it, and that host is injected
into every agent's MCP configuration, so the orchestrator and its agents always
agree.

**This repository does not ship that server, and it is not a Python package you
can install from here.** It is part of Kiln itself. The addresses below are not
chosen by this document — they are what `kiln-ai`, this package's own
dependency, declares in its distribution metadata, and you can read them back
off your own install:

```
uv run python -c "from importlib.metadata import metadata; \
print('\n'.join(metadata('kiln-ai').get_all('Project-URL')))"
```

```
Homepage, https://kiln.tech
Repository, https://github.com/Kiln-AI/kiln
Documentation, https://kiln-ai.github.io/Kiln/kiln_core_docs/kiln_ai.html
Issues, https://github.com/Kiln-AI/kiln/issues
```

Get Kiln from there and start it. A run needs a project with a task in it:
make one in Kiln, or let `--demo-project` copy and import one (see
[Try the demo](#try-the-demo) below). You don't have to look the IDs up: when
`--project-id` or `--task-id` is left out, `kiln-harness-tuner` lists what Kiln
has and asks ([your-own-task.md](your-own-task.md) section 5). Everything
after this section assumes a server you can reach.

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
every `kiln-harness-tuner` invocation with `--kiln-host`, because the loop, its agents
and the MCP server all have to agree on one host.

### The Kiln check at start-up

`kiln-harness-tuner` makes this check itself, first, on the host it was given
(`--kiln-host`, default `http://localhost:8757`). It runs before any agent
session, model call or run folder, and it costs nothing.

**Kiln answers.** One `GET /ping`, with a 5-second timeout. When nothing
answers:

```
Kiln isn't running at http://localhost:8757.
Start the Kiln app. If you don't have it, download it from https://kiln.tech.
If Kiln runs on another port, pass --kiln-host.
[r]etry  [o]pen kiln.tech  e[x]it:
```

When something answers but not with Kiln's `"pong"` (another program on the
port, or the wrong `--kiln-host`):

```
Something answered at http://localhost:8757, but it isn't Kiln (GET /ping returned HTTP 404).
Check --kiln-host, or start the Kiln app.
[r]etry  [o]pen kiln.tech  e[x]it:
```

The part in parentheses names the HTTP status, or says `an unexpected answer`
for a 200 that isn't `"pong"`.

**Kiln is new enough.** Kiln has no version endpoint, so the CLI reads Kiln's
API list once (`GET /openapi.json`) and checks that three APIs the loop calls
are there: the memory API, the eval-job API and run-config provenance. When any
is missing:

```
Kiln at http://localhost:8757 (version 1.0.3) is too old for kiln-harness-tuner.
It's missing: the memory API, run-config provenance.
Update Kiln: in the app, open Settings → Check for update, or download the latest from https://kiln.tech.
Then restart it.
[r]etry  [o]pen kiln.tech  e[x]it:
```

The version is left out when Kiln reports none, or reports `unknown`, as
packaged builds can. When the API list can't be read at all, the CLI prints one
warning and goes on, and a later call to a missing API fails with its own
error:

```
kiln-harness-tuner: couldn't read Kiln's API list, so its version wasn't checked.
```

**It waits, or it fails fast.** The CLI is interactive when its stdin and its
stdout are both terminals.

- **Interactive:** it prints the message and waits at the prompt.
  - Enter or `r` checks again. It never polls; it checks only when you ask.
  - `o` opens the page the prompt names in your browser, and prints its
    address too, for a shell with no browser.
  - `x` or Ctrl-D stops, with exit code 1 and `kiln-harness-tuner: stopped.`
  - Ctrl-C stops with exit code 130.
- **Not interactive** (CI, a script, a pipe, another agent): there is no one
  to answer. The same message goes to stderr, prefixed `kiln-harness-tuner:`,
  and the CLI exits 1 without reading any input.

The later start-up checks follow the same rules. The readiness of the task
([your-own-task.md](your-own-task.md) section 1) and the API keys (section 6 below)
use the same prompt. Picking the project and the task has its own prompt
([your-own-task.md](your-own-task.md) section 5), and it never asks when not
run interactively.

**Inside a run**, the once-per-run pre-flight checks the server again, this
time from where the agents stand: it dispatches one small agent session whose
only job is to call the gateway's `ping` operation. A failure there is fatal,
and its message names the rendered MCP config file, the host it is using, and
this document.

## 5. Claude Code, signed in

By default every agent the loop dispatches is a `claude -p` subprocess, billed
to your Claude Code account. The loop takes no API key. `--agent-cli` selects
Codex or OpenCode instead (the subsection at the end of this section).

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

`auth` is a different lane, and it is section 6's: it is how the loop labels a
**credential failure your model provider reports**. Nothing about the Claude
Code sign-in produces it. The classes are listed in
[agent-cli.md](agent-cli.md).

### Or: Codex or OpenCode, signed in

`--agent-cli` selects the agent CLI. With `--agent-cli 'codex exec -m MODEL'`
every agent is a `codex exec` subprocess, and with
`--agent-cli 'opencode run -m PROVIDER/MODEL'` every agent is an
`opencode run` subprocess. This section's Claude Code check then does not
apply.

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

## 6. A model provider key in Kiln

Your task's run configs name a model, and Kiln needs a key for that model's
provider. This key is **not** the Claude Code sign-in of section 5: the agents
that run the loop and the model being optimized are different accounts, metered
separately, and reported as two separate spend lanes
([costs.md](costs.md)). The moderation example (`examples/moderation/`), which
is also the demo project, uses Fireworks.

### The key check at start-up

`kiln-harness-tuner` checks this itself, once it knows the task and the task is
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
- **Each provider `--model-policy` names exactly**, in an allow rule or an
  exception. A rule whose provider is `*` names none.

Providers that take no key are not checked: `ollama`, `docker_model_runner`,
`openai_compatible`, `kiln_custom_registry` and `kiln_fine_tune`. The model
preflight still tests their models.

The check reads `GET /api/available_models`. Kiln lists a provider that takes a
key there only when every key it needs is set, so a provider missing from that
list has no key. The check never reads `/api/settings`. When a key is missing:

```
Kiln has no API key for 2 providers this run needs:
  • Fireworks AI (fireworks_ai), used by the task's run config "Baseline"
  • OpenAI (openai), used by eval "Tone"'s judge
Add them in Kiln under AI Providers: http://localhost:8757/settings/providers
[r]etry  [o]pen AI Providers  e[x]it:
```

A provider that only `--model-policy` names ends its line with
`, or remove it from --model-policy`. Each retry reads the policy file again
and works out again what the run needs, so a fix in Kiln or in the policy
counts on `r`. It waits, or it fails fast, the same way as the Kiln check
(section 4).

**Check by hand.** Ask Kiln whether it lists the provider:

```
curl -sS http://localhost:8757/api/available_models \
  | python3 -c "import json,sys; print(any(p.get('provider_id') == 'fireworks_ai' for p in json.load(sys.stdin)))"
```

`True` means Kiln has the key. Substitute your task's provider ID (for example
`openai`, `anthropic` or `openrouter`) for `fireworks_ai`.

If the server is not running, `curl -sS` prints its `(7)` line and `python3`
then raises `JSONDecodeError` on the empty input. That is section 4, not this
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
start (`--skip-model-preflight` in [your-own-task.md](your-own-task.md#every-flag)).
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

## Try the demo

Not a requirement: a first run on a task that is known to be ready.
`--demo-project` copies the demo project — the moderation task of
[examples/moderation](../examples/moderation/README.md) — into a new folder,
imports the copy into Kiln, and runs on it. It needs sections 1 to 6, including
a Fireworks key in Kiln, because the demo's baseline calls its model through
Fireworks. Section 6's check says so if the key is missing.

```
kiln-harness-tuner --demo-project --timeout-mins 60 --budget-usd 20
```

```
Demo project copied to /Users/me/Kiln Projects/Forum Moderation
and imported into Kiln as project 731954460212 (task 424242783061).
To run this again without the questions:
  kiln-harness-tuner --project-path '/Users/me/Kiln Projects/Forum Moderation' --project-id 731954460212 --task-id 424242783061 --timeout-mins 60 --budget-usd 20
```

- **Where the copy goes.** `~/Kiln Projects/`, the folder Kiln's own "create
  project" uses, made if it is missing. The first copy is `Forum Moderation`.
  When that name is taken (by a folder, a file or a link), the copy goes to
  `Forum Moderation-1`, then `-2`, and so on. Nothing is overwritten.
- **Each copy is its own project.** It gets a new project ID, and its name in
  Kiln is its folder's name, so copies can be told apart. The task ID is always
  `424242783061`.
- **The import is trusted** (`trusted=true`), because the demo's eval runs
  code: the scorer that ships with it.
- **The printed command runs on the same copy again.** `--demo-project` again
  makes another copy. If the start stops at the key check, the imported copy
  stays: add the key, then run the printed command.
- **Every other flag works as usual** (`--guidance`, `--timeout-mins`,
  `--budget-usd` and the rest). `--demo-project` can't be combined with
  `--project-id`, `--task-id` or `--project-path`: argparse says so, and the
  exit code is 2.
- **When Kiln refuses the import, or the copy fails** (a full disk,
  permissions), the folder this start made is deleted, the message says why,
  and the CLI exits 1.

[examples/moderation](../examples/moderation/README.md) describes the task,
and how to check the result on test yourself.

## Verify the whole install

```
uv sync
uv tool install .
uv run pytest
kiln-harness-tuner --help
kiln_api_mcp --help
curl -sS http://localhost:8757/ping
claude auth status
```

**What a green suite proves, and what it does not.** The suite is fully offline
— a fake dispatcher, a fake Kiln client, canned transcripts — so it proves the
package works on this machine. It spends nothing, contacts no server, and
therefore says nothing about sections 4, 5 and 6. Those three are proven by the
pre-flight of your first run, which is the first thing that costs money.

## Next

- [your-own-task.md](your-own-task.md) — what your task needs before the loop
  can work on it.
- [costs.md](costs.md) — estimate the run before you start it.
- [concepts.md](concepts.md) — the vocabulary the report will use.
