# Install

Five things have to be true before a run starts. Each of the five **numbered**
sections below has the same four parts: what it is for, the command that checks
it, what a failure looks like, and the command that fixes it. The three
unnumbered sections — platform support, where the tuner keeps its files, and
the verification block at the end — are not requirements and do not have that
shape.

The order is dependency-first. A reader who stops halfway has a working subset
rather than a half-configured machine.

Some failure messages below carry a value from your own machine: the shell's
`command not found` carries the name you typed, `curl`'s carries the port you
asked for, and the Python-floor error carries the exact version of the
interpreter that was refused.

## Platform support

> **macOS on Intel (x86_64) is not supported.** The lock pins `lancedb` and
> `pylance` versions that publish no macOS x86_64 wheel and no source
> distribution, so `uv sync` in a clone fails there. `uv tool install`
> resolves without the lock, so what it installs there depends on what PyPI
> has at the time, and it can need a source build.
> No test covers that combination. Linux (x86_64 and arm64), Windows (x86_64)
> and macOS on Apple silicon work.

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
Installed 5 executables: harness-tuner, harness-tuner-gateway-mcp,
harness-tuner-install-agents, harness-tuner-skill-installer-mcp, kiln_api_mcp
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

Get Kiln from there, start it, and make a project and a task in it — the
`--project-id` and `--task-id` the loop needs are that project's and that
task's, and they do not exist until you have. Everything after this section
assumes a server you can reach and a project inside it.

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

**Inside a run**, this failure is caught by the once-per-run pre-flight, which
dispatches one small agent session whose only job is to call the gateway's
`ping` operation. It is fatal, and its message names the rendered MCP config
file, the host it is using, and this document.

## 4. Claude Code, signed in

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

## 5. A model provider key in Kiln

Your task's run configs name a model, and Kiln needs a key for that model's
provider. This key is **not** the Claude Code sign-in of section 4: the agents
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
then raises `JSONDecodeError` on the empty input. That is section 3, not this
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

A run prints where its files are. When the run folder exists, before the
live display starts:

```
harness-tuner: run 260601-0930 — files in /home/you/.kiln_ai/harness_tuner/runs/260601-0930/
```

At a clean finish:

```
harness-tuner: run finished — report: /home/you/.kiln_ai/harness_tuner/reports/kiln-harness-tuner-260601-0930.md
```

When a run stops after its folder exists — on an error the tuner reports, or
on Ctrl-C, SIGTERM or SIGHUP — it also prints
`harness-tuner: run folder: <run folder>`. That folder's
`kiln-harness-tuner.log` holds the details. When the error exit wrote a
fallback report, a `harness-tuner: report: <path>` line follows.

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
