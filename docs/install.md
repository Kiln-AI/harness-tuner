# Install

Six things have to be true before a run starts. Each of the six **numbered**
sections below has the same four parts: what it is for, the command that checks
it, what a failure looks like, and the command that fixes it. The two unnumbered
sections — platform support, and the verification block at the end — are not
requirements and do not have that shape.

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

> **Linux (x86_64 and arm64) and macOS on Apple silicon are supported.**
>
> **Windows is not supported.** `cli.py` imports `fcntl`, which is Unix only,
> so `kiln-harness-tuner` cannot start. The agent sandbox fences are Seatbelt
> on macOS and bubblewrap on Linux.
>
> **macOS on Intel (x86_64) is not supported.** Section 2's `uv sync` fails
> there. The lock pins `lancedb` and `pylance` versions that publish no macOS
> x86_64 wheel and no source distribution.

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
`kiln-harness-tuner-install-agents --harness claude` writes, is for
interactive Claude Code sessions; a loop run does not use it. Install the console scripts for those sessions and for `kiln-harness-tuner`
itself.

**Install.** From the repository root:

```
uv tool install .
```

```
Installed 5 executables: kiln-harness-tuner, kiln-harness-tuner-gateway-mcp, kiln-harness-tuner-install-agents, kiln-harness-tuner-skill-installer-mcp, kiln_api_mcp
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

Get Kiln from there, start it, and make a project and a task in it — the
`--project-id` and `--task-id` the loop needs are that project's and that
task's, and they do not exist until you have. Everything after this section
assumes a server you can reach and a project inside it.

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
every `kiln-harness-tuner` invocation with `--kiln-host`, because the loop, its agents
and the MCP server all have to agree on one host.

**Inside a run**, this failure is caught by the once-per-run pre-flight, which
dispatches one small agent session whose only job is to call the gateway's
`ping` operation. It is fatal, and its message names the rendered MCP config
file, the host it is using, and this document.

**If the server is older than v1.3**, the check above passes, because `/ping`
exists. A run passes its start-up checks too: the agent CLI check and the
gateway `ping` pre-flight each run one short agent session, and you pay for
them. Then the first memory read of setup gets HTTP 404, and the run stops
with exit status 1:

```
kiln-harness-tuner: GET /api/projects/<project-id>/memories -> HTTP 404: …
```

**Fix.** Upgrade Kiln to v1.3 or later, then run again.

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
[harness-seam.md](harness-seam.md).

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
is its own permission rules (harness-seam.md sections 10 and 11).

## 6. A model provider key in Kiln

Your task's run configs name a model, and Kiln needs a key for that model's
provider. This key is **not** the Claude Code sign-in of section 5: the agents
that run the loop and the model being optimized are different accounts, metered
separately, and reported as two separate spend lanes
([costs.md](costs.md)). The moderation example (`examples/moderation/`) uses
Fireworks.

**Check.** Read the server's settings and print whether the key is set, without
printing the key:

```
curl -sS http://localhost:8757/api/settings \
  | python3 -c "import json,sys; print(bool(json.load(sys.stdin).get('fireworks_api_key')))"
```

`True` means a key is stored. Substitute the field for whichever provider your
task uses.

If the server is not running, `curl -sS` prints its `(7)` line and `python3`
then raises `JSONDecodeError` on the empty input. That is section 4, not this
section — fix it there and run this again.

That endpoint returns the stored keys themselves, so print the boolean rather
than the response — do not paste its raw output anywhere.

`GET /api/available_models` is **not** a substitute: it lists
providers whose key is unset, so it cannot tell you a key is present.

**Fix.** Connect the provider in the Kiln studio UI, under settings, which is
where the key is validated before it is stored.

**What a bad key looks like in a run.** Kiln serves job failures as free text, so the loop classifies them by matching
the message (`provider_errors.py`, which lists the families it was built from).
A key the provider rejects — "invalid api key", "unauthorized", a 401 — is
classified `auth`, which is terminal: the run stops and writes a report rather
than retrying something no retry fixes. A key that is simply **absent** may word
itself differently, match none of those families, and land in the generic
`provider_error` class, which is retried. So the symptom to watch for is not one
message but a run that gets no eval job to complete. Check this section first
when that happens.

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
