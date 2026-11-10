# The agent CLI seam

The loop dispatches every judgment to an agent. **Which agent CLI runs that
agent is a seam**, and this document is its contract.

Three implementations ship, chosen with `--agent-cli` (section 9):

- `ClaudeCliDispatcher` (`claude -p`, the default): one `claude -p`
  subprocess per call.
- `CodexCliDispatcher` (`codex exec`): one `codex exec`
  subprocess per call. Section 10 says what it fences and what it does not
  have.
- `OpenCodeCliDispatcher` (`opencode run`): one `opencode run`
  subprocess per call. Section 11.

The second and the third are subclasses of the first. The prompt, the
envelope, the repair retry, the error routing, the step records and the
start-up check are shared; the four steps that differ by agent CLI are methods
each overrides — `_argv` (the command), `_cwd` (where it runs),
`_parse_cli_output` (what it printed) and `_transcript_source` (where its
transcript is).

## 1. The protocol

[`dispatch/base.py`](../src/kiln_harness_tuner/dispatch/base.py) holds
`AgentDispatcher`: **one typed method per agent role**, plus `preflight`. There
is no `agent: str` parameter, no `payload: dict` and no switch statement
re-deriving what the type system already knew.

```python
class AgentDispatcher(Protocol):
    async def preflight(self) -> Usage: ...
    async def batch_plan(self, req: BatchPlanInput) -> AgentResult[BatchPlan]: ...
    async def describe_eval(
        self, req: DescribeEvalInput
    ) -> AgentResult[EvalDescription]: ...
    async def eval_build(self, req: EvalBuildInput) -> AgentResult[EvalBuildReport]: ...
    async def create_experiment_rc(
        self, req: RcBuildInput
    ) -> AgentResult[RcBuildResult]: ...
    async def analyze_experiment_results(
        self, req: AnalysisInput
    ) -> AgentResult[ExperimentResult]: ...
    async def promote_best(
        self, req: PromotionInput
    ) -> AgentResult[PromotionDecision]: ...
    async def probe(self, req: ProbeInput) -> AgentResult[ProbeResult]: ...
    async def report_writer(self, req: ReportInput) -> AgentResult[ReportMarkdown]: ...
    async def integrity_review(
        self, req: IntegrityReviewInput
    ) -> AgentResult[IntegrityReviewResult]: ...
    async def generality_review(
        self, req: GeneralityReviewInput
    ) -> AgentResult[GeneralityReviewResult]: ...
```

`preflight` runs once per run, before any paid work, and proves the agents'
Kiln lane end to end. It raises on failure. A broken lane must never start a
run.

**The import boundary.** `orchestrator.py` and `items.py` know
`AgentDispatcher` and nothing else. No module outside `dispatch/` imports
`ClaudeCliDispatcher` except `cli.py`, which constructs it — the composition
root. `tests/test_dispatch_boundary.py` asserts that the exception set is
exactly that one module, so the seam cannot erode by accident.

## 2. What a dispatcher receives

Three things, and they are the whole of an agent's context.

1. **The input model.** Code-assembled, per method, defined in
   [`schemas.py`](../src/kiln_harness_tuner/schemas.py). This is a deliberate design:
   the agent's whole passed context is its input model, so what a role can see
   is decided in code and reviewed like code.
2. **The target block.** `project_id` and `task_id`. (`project_path` stays
   on `TargetBlock` for the orchestrator's code, and is not in the prompt:
   the project tree is read-denied to every agent.) The working
   directory identifies nothing — it is a folder of the tuner's (section 7),
   not the project, and a project can hold many tasks — so the dispatcher
   injects the ids and no agent ever guesses them.

   The Kiln host is deliberately **not** in the rendered block. The agents
   reach Kiln through their gateway operations, and the gateway holds the
   host itself. A host in the prompt invites an agent to call the server
   directly from its shell.
3. **The prompt.** The role's template from
   [`agents/`](../src/kiln_harness_tuner/agents/), the input as JSON, and the envelope
   instruction.

Everything else the agent reads for itself, through its gateway operations.
A trace is the one exception to "in the answer": the gateway
writes it to a file in the run folder's `scratch/traces/`
(`<state>/runs/<run_id>/scratch/traces/`, where `<state>` is the state folder,
[install.md](install.md#where-the-tuner-keeps-its-files)) and answers with the
path, so a dispatcher must let the roles that read traces read that folder.
Every gateway answer stays under 40,000 characters, below the limit at
which Claude Code moves an MCP answer to a file the fence denies
([architecture.md](architecture.md) section 12).

## 3. What a dispatcher must return

`AgentResult[T]`:

| Field | Meaning |
|---|---|
| `status` | `ok`, `failed_expected`, or `failed_infra` |
| `data` | the typed output, validated against the method's model |
| `error` | an `ErrorInfo` — infrastructure failures only, set by the dispatcher |
| `detail` | the agent's own prose, notably the *why* on a `failed_expected` |
| `usage` | tokens and cost across **every** attempt |
| `attempts` | how many dispatches the retry ladder spent on this result |

**`failed_expected` is a verdict, not a failure.** An eval builder that could
not reproduce an issue, an analyst judging an arm unsuccessful, a probe
answering `unclear` — each is a measured negative and is recorded normally. The
agent's own status passes through untouched. Only transport, parse and crash
failures become `failed_infra`.

`usage` and `attempts` sit together because they answer the same question: what
did this result cost. Callers must never re-derive `attempts` from `status` —
the ladder has two terminal cases that skip the retry.

### The envelope

`ClaudeCliDispatcher` imposes a text contract because a subprocess returns
text: the agent's **final message is exactly one fenced `json` block** of
`{"status": …, "data": …, "detail": …}`. The extractor requires the `json`
tag on the opening fence, so the closing fence of some other fenced block can
never open a candidate, and it refuses to fall back to an earlier complete
block when the final opener has no complete block after it — a result truncated
mid-JSON is a failure, not a reason to return a stale one.

**A different dispatcher does not have to use this mechanism.** The contract
is the typed return, not the fence. A dispatcher whose agent CLI has
structured output at the tool layer should use it, which removes the
`parse_failure` class entirely.

## 4. Error classes

`ErrorClass` in `schemas.py`. This union is the contract a second dispatcher
must satisfy: the loop routes on it, so a new literal is a change to the loop,
not to one dispatcher.

| Class | Produced by | What the loop does |
|---|---|---|
| `rate_limit` | a throttled provider | the backoff ladder |
| `timeout` | the per-call wall-clock cap | the ladder, with a resume for three roles (section 5) |
| `provider_error` | a provider failure, and — see below — a data-shape condition | the ladder |
| `agent_crash` | a non-zero exit, or a session that produced no envelope | the ladder |
| `parse_failure` | an unparseable or invalid final block | one in-session repair retry, then terminal |
| `quota_exhausted` | out of credits | **terminal.** Circuit-breaks the run to the code-rendered report |
| `auth` | a bad or missing credential | **terminal**, same path |

The last two are terminal by design. They are operator-actionable conditions
that no amount of retrying resolves, and riding them down the generic ladder
means a long silence and no report.

`provider_error` carries one honest mislabel: the union has no literal for a
**data-shape** condition — an eval with no direction flag, an unmappable score
type, a missing default eval config — so those are reported as
`provider_error`. It is a mislabel, not lost information, and
[limitations.md](limitations.md) names it.

## 5. The four hard cases

**A timeout.** The subprocess is killed and the result is
`failed_infra(timeout)`. Every dispatch runs under a per-role cap, and the cap
is what bounds a hang *inside* a wave. The run deadline does not do that job: it
stops the wave, cancelling the item and losing what it had paid for, and it only
arrives at the end of the time the operator asked for. A per-role cap turns a
wedged session into one failed item the ladder can retry, minutes after it
wedged rather than hours.

For three roles — `create_experiment_rc`, `eval_build` and `probe` — the
session is **resumed once** with a short cap and a finish-up instruction,
rather than re-run. Those are the roles that create Kiln artifacts as they go,
and a fresh retry duplicates every artifact the timed-out session had already
created. The instruction is explicit that the artifacts exist and are to be
reused.

*An agent CLI without resume*: re-run from scratch and accept the duplicates.
The loop still works; the cost is spend and litter.

**A non-zero exit.** Always `agent_crash`, and it rides the ladder. The message
is not read: a process that died returned no envelope, and taking a terminal
verdict from unvalidated stderr text is how a run ends on a phrase that happened
to match. The tail of stderr reaches the run log, bounded, so one wedged session
cannot bury the file — the diagnosis lives there, not in the class.

Message classification belongs to the **other** failure path: a session that
exits **zero** and returns a result marked `is_error`. There the agent CLI has
handed back a structured result, so `_classify_cli_error` reads it — a
recognisable quota or authentication wording becomes one of the two terminal
classes, a throttling wording becomes `rate_limit`, and everything else is
`provider_error` and rides the ladder. The distinction is a contract for a
second dispatcher: report a crash as a crash, and reserve the terminal classes
for what the provider actually said.

**A malformed result.** One repair retry **in the same session**, with the
validation errors handed back — cheaper and more likely to work than a fresh
session, which has to redo the work as well as the formatting. Then
`failed_infra(parse_failure)`, which is terminal: the ladder does not re-run a
session that produced a bad shape twice.

**A resumable session.** See the timeout case. Nothing else in the loop resumes:
a crashed *wave* is not resumed, and [limitations.md](limitations.md) says what
that costs.

## 6. Capabilities an agent CLI may or may not have

`ClaudeCliDispatcher` uses five capabilities that are not in the protocol. Four
are optional. Each row says what is lost.

| Capability | If the agent CLI lacks it |
|---|---|
| **A read fence over the filesystem** | The loop runs. Measurement integrity now rests on the operator. What an agent could read to recall an answer instead of measuring it: the run's own project — every split's eval inputs and results — another run's scratch and transcripts, this run's wave log and step records (they carry val tables), the reports, and the memory store on disk. Stored val and test results stay in the project too, so without this fence an agent can read them. |
| **Per-run MCP configuration** | The dispatcher must expose each role's loop-gateway operations (and no generic Kiln tool), pinned to *this run's* Kiln host and train set, by some other means. The loop cannot verify that it did, and says so at pre-flight rather than pretending. |
| **A pre-tool hook on MCP tools** | An agent could call an MCP tool outside its role's gateway operations if one were in its session's config. The gateway itself still serves only the role it was started for, answers from train records only, and validates every write; this hook is the third check, not the first. |
| **Session resume** | A session that hits its wall-clock cap is re-run from scratch instead of resumed, and may duplicate artifacts it had already created. Three roles create artifacts as they go and are the ones this costs: the builder, the eval builder and the probe. |
| **Structured result return** | **Not optional.** A dispatcher that cannot return the agent's final structured output cannot drive the loop. |

The first three are all **containment**, and losing them does not stop a run —
it changes what the run's numbers mean. That is the harder failure to notice,
which is why each row names the specific thing an agent could reach.

The hook is **one** row because `cli.py` renders **one** hook per role, with
one matcher on MCP tools: the role's gateway operations pass, every other
MCP tool is refused. The other rules — this run's project and task, memory
scopes, train only, the model policy — are the gateway's own, in code
(`kiln_harness_tuner.gateway`), so a dispatcher that serves the gateway per
role inherits them.

## 7. What `ClaudeCliDispatcher` does beyond the protocol

A second dispatcher may skip all of these. Each is written here with what it is
worth, so the decision is informed.

- **Where a session runs.** A Claude session's working folder is the agents
  folder, `<state>/agents/claude/<version>/`, which holds the `.claude/skills/`
  and the `CLAUDE.md` that `install_agents.install_for_run` installs before
  the run's first session. So the skills and the instructions load from
  there. Claude Code keeps the session's transcript under
  `~/.claude/projects/`, in a folder named from the agents folder's path,
  and the dispatcher copies it from there. The fence denies the file tools
  any write to the agents folder ([architecture.md](architecture.md) section
  12). Worth: the skills load with no file in any folder of yours.
- **Transcript copying, redacted.** Every session's transcript is copied to
  `<state>/traces/`, through a redactor that blanks known credential shapes —
  an agent's tool result can carry whatever it read, including a configuration
  file with a token in it. Worth: this is the only record of *how* an agent
  reached its answer, and it is where a wrong verdict is diagnosed.
- **Environment stripping.** Names that carry credentials never reach the
  subprocess or the MCP servers it spawns, because agents hold a shell and
  transcripts are files. One of them also changes billing: with an API key
  present, the CLI bills the API instead of the operator's login, and the
  reported cost silently turns from a shadow price into real money.
  `--budget-usd` never counts the agents, for any agent CLI: the loop shows
  their tokens for information ([costs.md](costs.md)).
- **Per-role model and effort.** Pinned in code so that runs do not differ
  across machines, overridable per role from the CLI. Worth: the agent side is
  where a run's spend concentrates, and the mechanical roles do not need the
  same model as the planner.
- **A per-dispatch scratch directory.** Under the run folder's `scratch/`,
  and the only place the session may write files.
- **A provider-quota circuit breaker.** A classified quota or authentication
  error ends the run through the code-rendered report instead of down the
  ladder.
- **Tool allow lists per role.** Each role is granted its gateway
  operations by full name. **Two** roles additionally get the skill-installer server: the builder,
  and the probe that asks "does this model read a skill file" and needs a
  throwaway skill to ask with. The count is the point of the sentence — that
  server is the one lane that writes into the project's skills directory, so
  every role holding it is a role that can change the subject task's surface.
  The allow list trims what a role *can* call, but it cannot deliver
  eval-freeze on its own. The gateway gives each role only
  its operations, and no role but the eval builder has an operation that
  writes an eval; eval-freeze for the eval builder still rests on
  sequencing and skill text.

## 8. Writing a second dispatcher

The short version:

1. Implement the eleven methods (ten roles and `preflight`). Return `AgentResult[T]` with a validated `data`
   on success.
2. Make `preflight` prove the agents' Kiln lane and raise with an actionable
   message when it cannot.
3. Map your agent CLI's failures onto the `ErrorClass` union. Do not add a
   literal without changing the loop that routes on it.
4. Pass the agent's own `failed_expected` through. Do not convert a measured
   negative into an infrastructure failure.
5. Report `usage` across every attempt, and `attempts` honestly.
6. Read section 6 and decide, per row, what you are giving up.

Construct it in `cli.py` and nowhere else, or
`tests/test_dispatch_boundary.py` will say so. `cli.DISPATCHERS` maps the
executable's name to the class.

## 9. `--agent-cli`: choosing the CLI

The operator names the CLI the way they type it:

```
--agent-cli 'claude -p'
--agent-cli 'codex exec -m gpt-6.1-sol'
--agent-cli 'opencode run -m zai-coding-plan/glm-5.3-flash --variant high'
```

[`dispatch/agent_cli.py`](../src/kiln_harness_tuner/dispatch/agent_cli.py) splits the
text. The executable's name (`claude`, `codex` or `opencode`, a full path is
also accepted) selects the dispatcher. The one-shot token (`-p`, `exec`,
`run`) may be left out; the dispatcher adds it. Two options are taken out of
the text:

| Option | Becomes |
|---|---|
| the model: `--model`, or `-m` for Codex and OpenCode | the run-wide model. `--agent-model ROLE=MODEL` still overrides it per role. |
| the effort: `--effort` for Claude, `-c model_reasoning_effort=…` for Codex, `--variant` for OpenCode | the run-wide effort. `--agent-effort ROLE=EFFORT` still overrides it per role. |

The effort is `high` for every role unless the text or `--agent-effort` sets
another. The CLI's own setting is not used: Codex runs with
`--ignore-user-config`, so its `config.toml` is not read. For example:

```
--agent-cli 'claude -p --effort xhigh'
--agent-cli 'codex exec -m gpt-6.1-sol -c model_reasoning_effort=xhigh'
--agent-cli 'opencode run -m zai-coding-plan/glm-5.3-flash --variant max'
```

For Codex, `-c model_reasoning_effort=VALUE`, `--config model_reasoning_effort=VALUE`
and the `=` form of each are taken out of the text; a TOML-quoted value works
too. Other `-c` options go into every session's command.

A level the CLI does not have is handled like this:

| CLI | Levels | Another value |
|---|---|---|
| Claude | `low`, `medium`, `high`, `xhigh`, `max` | Refused at start-up, before any session, in `--agent-cli` and in `--agent-effort`. Claude Code itself would only print a warning and use its own default. |
| Codex | `low`, `medium`, `high`, `xhigh`, `max`, also `none` and `minimal`; some models take `ultra` | A name Codex does not know fails the start-up check with Codex's own message (HTTP 400, with the names it takes). Nothing else is spent. |
| OpenCode | the model's own variants | Ignored by OpenCode: the model runs at its own default. |

Every other option goes, unchanged, into every session's command. Do not
repeat an option the dispatcher sets itself (the output format, the MCP
config, the permissions): the CLI then sees it twice.

With no model in the text, Claude keeps its pinned defaults (`opus`, and
`sonnet` for `generality_review`). Codex and OpenCode then pass no model
option, so the CLI's own default model is used. A model in the text replaces
Claude's per-role defaults too.

**Credentials are the CLI's.** The loop does not take an API key and does not
manage a sign-in. Secret-looking environment variables are still withheld
from every session (section 7), so a CLI that reads its key from one needs
that name in `KILN_HARNESS_TUNER_CHILD_ENV_KEEP`.

**The start-up check.** Before the run lock and the run directory, `cli.py`
sends one short prompt through the command, with no tools and no MCP server
(`check_cli`). The CLI must answer with a fixed word. If it is not on the
`PATH`, exits non-zero, reports an error, prints output its dispatcher cannot
read, or answers something else, the start stops with a `PreflightError`
that names the reason and the last lines the CLI printed. A wrong model name,
an expired sign-in and a missing install all stop here, once, instead of as
an `agent_crash` on every session. The MCP-lane pre-flight of section 1 then
runs as before.

## 10. `CodexCliDispatcher`

Codex reads files through its shell, so a Codex session keeps its shell, and
Codex's own OS sandbox is the fence (Seatbelt on macOS; bubblewrap and
Landlock on Linux). Every session runs under the permission profile
`kiln_harness_tuner`, passed per call with `-c`, so nothing is written into the user's
Codex home:

| Rule | Effect |
|---|---|
| `":root" = "deny"` | The whole disk is unreadable and unwritable. |
| `":minimal" = "read"` | The platform paths common tools need. |
| `":workspace_roots" = {"." = "write"}` | The session's scratch directory, its workspace. `"read"` for the val readers. |
| the agents folder's `.agents/skills` = `"read"` | The skills, and nothing else of the state folder. |
| the run folder's `scratch/traces` = `"read"` | The gateway's trace folder, for the roles that read traces (`TRACE_READER_ROLES`) only. |
| `network.enabled = false` | No network for any command: no `curl` to the Kiln server. |

This is an **allow list**, where the Claude fence is a deny list: a directory
nobody named is still fenced. Under this profile, reads of the state
folder's `runs/`, of the home directory and of `~/.codex/auth.json`, and a
disk-wide `grep`, are all refused; the workspace and the skills are
readable. A sub-agent a session spawns runs under the same profile.

MCP servers run **outside** the Codex sandbox (Codex's own rule), so the loop
gateway and the skill installer work unchanged. Each server is passed with
`enabled_tools` set to the role's own tools and `default_tools_approval_mode =
"approve"`: a `codex exec` session has no one to ask.

What the profile does not govern is switched off: `web_search = "disabled"`,
and the features `apps`, `plugins`, `remote_plugin`, `browser_use`,
`browser_use_external`, `browser_use_full_cdp_access`, `computer_use`,
`in_app_browser`, `in_app_local_automation`, `multi_agent`,
`skill_mcp_dependency_install`, `tool_suggest` and `image_generation`.
`--ignore-user-config` keeps the user's `~/.codex/config.toml` — their MCP
servers and settings — out of every session, and `--strict-config` makes an
unknown key or feature fail the session rather than pass unnoticed.

Codex finds skills in `.agents/skills` and instructions in `AGENTS.md`
under its working directory. Before its first session, a run installs both
into the agents folder, `<state>/agents/codex/<version>/`. Each workspace
holds `.agents/skills` as a link to the agents folder's `.agents/skills`, and
a copy of its `AGENTS.md` (`codex_cli.stage_workspace`). A link that the
session plants in place of `AGENTS.md` is removed before the copy.

Against section 6:

| Capability | Codex |
|---|---|
| Read fence | Yes: the OS sandbox, an allow list. |
| Per-run MCP configuration | Yes: `-c mcp_servers.<name>=…` from the same rendered files as Claude. |
| Pre-tool hook | Not used. The session's only MCP servers are the ones passed, each limited to the role's tools. |
| Session resume | For the repair retry: `codex exec resume <thread id>`. **Not** after a timeout: the id cannot be chosen before the spawn, and a killed session never prints it. The ladder re-runs the session. |
| Structured result return | Yes: the final `agent_message` event holds the envelope. |

What else differs:

- **Cost.** Codex reports tokens, not cost. That loses nothing: the loop
  shows the agents in tokens for every agent CLI, and `--budget-usd` never
  counts them.
- **`--max-turns`** has no Codex equivalent and is ignored, with a warning.
- **Errors.** A failed session exits non-zero and prints a `turn.failed`
  event with the provider's message. That event is structured output, so it
  is classified like Claude's `is_error` result
  (`READS_ERROR_ON_NONZERO_EXIT`): quota and auth stop the run. A non-zero
  exit with no such event is `agent_crash`, as for Claude.
- **Transcripts** are copied, redacted, from
  `~/.codex/sessions/<date>/rollout-…-<thread id>.jsonl`.
- **The user's global instructions and skills.** `~/.codex/AGENTS.md` and
  the user's own skills are still offered to the session, as
  `~/.claude/CLAUDE.md` is to a Claude session. The skills' files sit under
  the home directory, which the profile denies, so only their descriptions
  reach the session.
- **Credentials.** A Codex session authenticates with the `codex login`
  state. `CODEX_API_KEY` is withheld like every other secret-looking name,
  unless `KILN_HARNESS_TUNER_CHILD_ENV_KEEP` names it.
- **The install folders.** Codex starts a helper by running its own binary
  again, under the session's profile, by the path it was started from. A
  standalone install is a link chain inside the home directory:
  `~/.local/bin/codex` links to
  `~/.codex/packages/standalone/current/bin/codex`, and `current` links to
  `releases/<version>`. With all of it denied, the helper cannot start and
  every session fails. So the profile makes readable each folder that holds
  a link on that chain, and the release folder. It never opens the home
  directory or a folder above it, and never `~/.codex` itself, which holds
  `auth.json`. A profile entry for the link file alone is not enough.

## 11. `OpenCodeCliDispatcher`

OpenCode has no OS sandbox. The fence is its permission rules, which apply
to its own tools. Every session runs under a config file the dispatcher
writes under the run directory (outside the session's workspace, so the
session cannot read or change it) and names with `OPENCODE_CONFIG`:

| Rule | Effect |
|---|---|
| `"*": "deny"` | Every tool is denied, so the shell, web fetch, web search, grep, glob, sub-agents and the rest are not in the session at all. |
| `read` | Allowed in the session's workspace and in the agents folder's `.agents/skills`, and in the gateway's trace folder for the roles that read traces; denied everywhere else. |
| `edit` | Allowed in the workspace, except its `.agents/`: that holds the link to the agents folder's skills, which every later session and run reads. Denied for the val readers. |
| `external_directory` | Denied, except the workspace, the skills and, for the roles that read traces, the trace folder. |
| `skill` | Allowed, so the skills load as they do under Claude Code. |
| `kiln_harness_tuner_gateway_*` | Allowed: the gateway serves only the role's operations. |
| `skill_installer_<tool>` | Allowed by name, for the two roles that hold the installer. |

A no-tool role (`integrity_review`, `generality_review`) gets `"*": "deny"` and
no MCP server. OpenCode matches a path rule against the path relative to its
worktree (the git root above the workspace, or `/` when there is none), so each
rule is written in that form and in the absolute form. The `edit` deny on
`.agents/` is written from the workspace's own patterns, so it matches a path
through the link as written; the same path resolved is in the agents folder,
which no `edit` rule allows. The deny is the last and the longest rule, so it
wins whichever precedence OpenCode applies. Under these rules, reads of a file
in the run directory and of `~/.codex/auth.json` are refused; the shell, web
fetch and search tools are absent; a write and a read in the workspace and a
read of a skill file work; the `kiln-harness-tuner-*` skills are found; and the
gateway answers through the rendered MCP config.

Every session also runs with `--format json`, `--pure` (no external
plugins) and `--auto` (approve what the rules do not deny, so a session
never waits on a question nobody can answer). The effort is `--variant`,
`high` by default; OpenCode ignores a variant the model does not have.

Against section 6:

| Capability | OpenCode |
|---|---|
| Read fence | Yes, at the tool level: an allow list in its permission rules. Not an OS sandbox — as strong as OpenCode's own checks. |
| Per-run MCP configuration | Yes: `mcp` in the session's config, from the same rendered files as Claude. Each server's request timeout is 30 minutes (OpenCode's default is 5 seconds). |
| Pre-tool hook | Not used. The session's only MCP servers are the ones passed, and the installer's tools are allowed by name. |
| Session resume | For the repair retry: `--session <id>`. **Not** after a timeout: the id is not known before the spawn. The ladder re-runs the session. |
| Structured result return | Yes: the text of the last step holds the envelope. |

What else differs:

- **Cost.** OpenCode reports a cost and tokens per step. The loop shows the
  tokens, as for every agent CLI.
- **`--max-turns`** becomes the build agent's `steps` cap.
- **Errors.** A failed session exits non-zero and prints an `error` event;
  that event is classified, as for Codex.
- **Transcripts** are copied, redacted, from `opencode export <session id>`.
- **Instructions and skills.** OpenCode finds `.agents/skills` and
  `AGENTS.md` in its workspace. As for Codex, each workspace holds a link to
  the agents folder's skills (`<state>/agents/opencode/<version>/`) and a copy
  of its `AGENTS.md`. OpenCode also loads the user's own skills, as Claude
  Code does.
