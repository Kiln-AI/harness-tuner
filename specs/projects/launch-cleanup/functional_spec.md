---
status: draft
---

# Functional Spec: Launch cleanup (license and docs)

## Goal

Make the repository MIT open source, and make every doc and agent skill agree
with the code, before the repository goes public. This pass changes text. It
does not change behavior, with one exception: the `pyproject.toml` license
metadata (item 1.2).

"Doc" means `README.md`, `docs/*.md`, `.github/CONTRIBUTING.md`,
`examples/**/README.md`, and Python docstrings that describe a rule in prose.
"Skill" means `.agents/AGENTS.md`, `.agents/skills/**` (the `kiln-api`
`api_docs` are vendored from Kiln and not edited), and the agent prompt
templates in `src/kiln_harness_tuner/agents/*.md`.

## Baseline

- Branch `claude/magical-ritchie-jc02f5`, from `main` at `57fc57d`.
- The rename branch `claude/loving-mccarthy-esry86` is not on the remote and
  has not merged (checked 2026-11-06). Work from `main`. Expect small
  conflicts in `README.md` and `docs/install.md` when either branch merges.
  Do not do any part of the rename.

## Writing rules (from `.agents/AGENTS.md` and `CONTRIBUTING.md`)

- ASD-STE100 Simplified Technical English: short sentences, one idea each, no
  metaphor. Describe what the code does now. No dated change notes.
- Fence pseudo-code without a language.
- No measured results, lever advice or earlier-run outcomes in `.agents/` or
  the agent prompts. A rewording must not add any.
- Edit the smallest span that fixes the item. Do not reflow or restyle
  neighbouring text.

## Items

Each item gives where the problem is, the fact as checked, the change, and the
check. Line numbers are at `57fc57d`.

### 1. License (MIT)

**1.1 `LICENSE` (new, repository root).** The standard MIT text.

- Holder: **Chesterfield Laboratories Inc.** This is the holder of the
  MIT-licensed parts of the main Kiln repo (`libs/core/LICENSE.txt` and
  `libs/server/LICENSE.txt` say `Copyright 2024 - Chesterfield Laboratories
  Inc.`; checked at Kiln commit `6a04519`).
- Line: `Copyright (c) 2026 Chesterfield Laboratories Inc.` (year: Q3).
- No "this license applies only to…" preamble. The whole repository is MIT.

**1.2 `pyproject.toml`.** In `[project]`:

```
license = "MIT"
license-files = ["LICENSE"]
classifiers = [
    "License :: OSI Approved :: MIT License",
]
```

- Checked: hatchling 1.33.0 builds a wheel with this. The wheel has
  `Metadata-Version: 2.5`, `License-Expression: MIT`, `License-File: LICENSE`,
  the classifier, and `dist-info/licenses/LICENSE`.
- PEP 639 deprecates `License ::` classifiers when `license` is an
  expression, and lets build tools reject them. Hatchling does not reject
  them today (Q4).
- `uv sync --locked` (CI's install step) must still pass. If `uv.lock`
  changes, commit the regenerated lock.

**1.3 `.github/CONTRIBUTING.md`, "Licence" section (L67-70).** Replace "The
`LICENSE` file is not in the repository yet. Ask before you invest in a large
change." with: the project is under the MIT License; see `LICENSE`. Plus the
contribution terms from Q5. Keep the heading `## Licence`.

**1.4 `README.md`.** Add a `## License` section after `## Contributing`
(L256-259): MIT, with a link to `LICENSE`. Plus the trademark line if Q6 says
so.

### 2. Doc contradictions

**2.1 Installed executables.** `docs/install.md` L107-110 shows
`Installed 4 executables: …`. `pyproject.toml` `[project.scripts]` declares
5: `kiln-harness-tuner`, `kiln-harness-tuner-skill-installer-mcp`,
`kiln-harness-tuner-gateway-mcp`, `kiln-harness-tuner-install-agents`,
`kiln_api_mcp`.

- Change: make the sample output list all 5.
- Check: run `uv tool install .` with `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` in a
  scratch directory, and copy the line exactly as `uv` prints it (count,
  order, wrapping). If that cannot run, list the 5 in the order `uv` uses
  (sorted), and record the step as unverified.
- Sweep: no other doc gives a count. README L43-46 names no count and stays.

**2.2 Eval builder splits — BLOCKED, reported, not changed.** The overview
says to stop and report if the code cannot set a val split. It cannot:

- `gateway/ops.py` L1031-1049, `create_eval`: it POSTs `create_evaluator` with
  `eval_set_filter_id = tag::kl_<token>_test`, then PATCHes
  `train_set_filter_id = tag::kl_<token>_train`. Nothing sets a val split.
- `gateway/models.py` L520-526, `EvalCreate`: no split field.
- Kiln's own API cannot set one either. The vendored PATCH doc
  (`patch_api_…_evals_eval_id.md`) takes only `name`, `description` and
  `train_set_filter_id`. Upstream Kiln `eval_api.py` (commit `6a04519`):
  when `eval_set_filter_id` is given, `create_evaluator` makes **only** a test
  split, and `UpdateEvalRequest` takes `train_set_filter_id` but no val
  filter. (When `eval_set_filter_id` is omitted, Kiln makes test, train and
  val splits from tags it derives from the eval name. The gateway never
  omits it.)
- `add_eval_items(split="val")` exists (`models.py` L536). It tags task runs
  `kl_<token>_val`, but no split of the eval selects that tag.

So `docs/limitations.md` L274-277 matches the code. These do not:

- `docs/agents.md` L90-92: "must carry pre-sized train, val and test splits".
- `src/kiln_harness_tuner/agents/eval_build.md` L6-8: the agent prompt asks
  for "pre-sized train/val/test splits (… val the promotion gate)".

Likely effects, from reading the code only (not run):

- An agent-built eval with a current config is active. It has no spec
  (`orchestrator.py` L2463), so the promotion gate's val pass
  (`_assemble_val_tables`) asks it for `split=val`. A failed val fill makes
  the candidate ineligible.
- On the next run, setup's `audit_splits` (`setup.py` L1040) refuses to start
  while any active eval has no val split.

What to do is Q2. Until Q2 is answered, 2.2 makes no edits.

**2.3 Held-out tags.** `quarantine.py` L51-54: `_HELDOUT_TAG_RE` matches, in
any case, `val` or `test` alone or followed by `_`, and any tag that starts
with `holdout` or `heldout`. `held_out` and `eval_set` do not match.

- `docs/limitations.md` L291-297: list the full rule, and change the
  examples of an unrecognised name to ones the code does not match (for
  example `held_out`, `eval_set`).
- `docs/architecture.md` L1037-1038: same list; add `holdout*` and
  `heldout*`.
- `.agents/skills/kiln-harness-tuner-gateway/SKILL.md` L146-147: it says
  "`val…`, `test…`, `holdout…`". Add `heldout…`. The gateway refuses by the
  same function (`gateway/ops.py` L230 → `trainset.held_out_by_name` →
  `looks_held_out`).
- `src/kiln_harness_tuner/quarantine.py` L30-33, module docstring: same list;
  add `holdout*` and `heldout*`. Docstring only.
- Sweep: `val_*`, `test_*`, `holdout`, `heldout`, `held out under`.

**2.4 Minimum Kiln version: v1.3.** Checked against Kiln tag `v1.2.0`:
it has no `libs/server/kiln_server/memory_api.py`, no `/api/jobs/evals/run`
and no `/api/jobs/wait`. Kiln `main` has all three.

What a run on Kiln v1.2 does (from the code, not run against a v1.2 server):
the start-up checks and the gateway `ping` pre-flight pass (`/ping` exists),
so one small agent session is paid for. Then setup's first memory read
(`memory.find_baseline_record` → `GET /api/projects/<id>/memories…`) gets
HTTP 404, and the CLI exits 1 with
`kiln-harness-tuner: GET /api/projects/<id>/memories… -> HTTP 404: …`
(`kiln.py` L33-41, `cli.py` L1302-1318). Confirm the exact path from
`memory.py` `_list` before you quote it.

- `README.md` L16, the Kiln row: say Kiln **v1.3 or later**, and keep where
  to get it.
- `docs/install.md` §4 (L135-164): state v1.3 as the minimum. Name the three
  missing APIs (memory, eval jobs, run-config provenance). Give the v1.2
  symptom above. Keep the metadata block (L149-159); it shows where Kiln
  comes from, not which version.
- The existing "older server" passages (`docs/limitations.md` L248,
  `docs/your-own-task.md` L148, `docs/architecture.md` L1205) are about a
  server without judge-free eval jobs. Do not change them. List them as
  unverified against v1.3 in the summary.
- Out of scope: `kiln-ai>=0.26` in `pyproject.toml`. That is the Python
  library, not the server.

**2.5 Platforms: Linux and macOS (Apple silicon) yes, Windows no.**

- Facts: `cli.py` L16 imports `fcntl` at module level (Unix only), so
  `kiln-harness-tuner` cannot start on Windows. The fences are Seatbelt
  (macOS) and bubblewrap (Linux) (`docs/install.md` L274-276,
  `docs/harness-seam.md`). The macOS x86_64 limit stays as it is (no wheels
  for `lancedb`, `pylance`).
- `README.md` L24-28: supported = Linux (x86_64 and arm64) and macOS on Apple
  silicon. Not supported = Windows (with the two reasons, in one short
  sentence each) and macOS on Intel (the existing reason).
- `docs/install.md` L23-28 ("Platform support"): the same, at the same depth
  as the current text.
- Make no claim about WSL. It is not tested.
- Sweep: `Windows` (case-insensitive). Only these two places name it as a
  platform; `architecture.md` L1209 and `limitations.md` L250 say "windows"
  as in time windows and stay.

### 3. Small corrections

**3.1 "max tokens" is not a run-config field.** The gateway's
`RunConfigView` (`gateway/models.py` L82-96) and the run-config properties in
`kiln-task-setup/SKILL.md` L31 have `thinking_level`, `temperature`, `top_p`,
`structured_output_mode`, and no max-tokens field.

- `.agents/skills/kiln-harness-tuner-batch-selection/SKILL.md` L61 and L446:
  remove "max tokens" from both lists.
- Sweep result: no other doc, skill or prompt lists it as a lever. The
  output-token and `max_tokens` mentions elsewhere (`limitations.md` L172,
  `costs.md` L46, `provider_errors.py` L34, …) are about billing or provider
  errors and stay.

**3.2 Tool descriptions: a new description means a new tool or a wrapper.**
Facts:

- A code tool's `tool_description` cannot change after create. The vendored
  PATCH doc takes only `description` (UI notes). The code-tool skill already
  says functional fields are immutable (`code-tool-authoring/SKILL.md`
  L137-138).
- A run config attaches tools by id only (`tools_config: {tools: [ToolId]}`).
  It has no per-config description.
- An MCP tool's description comes from its server. A built-in tool's comes
  from Kiln.

So a new description is either a new code tool (a clone of a code tool, with
the new description) or a wrapper (for any tool).

Reword each place that says or implies the description of an existing tool is
changed in place. Naming a tool's description as the surface the model reads
is correct and stays. Known places:

- `code-tool-authoring/SKILL.md` L52-53: `tool_config` "attaching, dropping,
  or re-describing existing tools on the new rc, no new code".
  Re-describing is not `tool_config` and is not "no new code".
- `batch-selection/SKILL.md` L391: "re-describe, wrap, consolidate or drop a
  tool".
- `experiment-build/SKILL.md` L51-52: "re-describe, wrap, consolidate or drop
  them on the new config".
- `batch-selection/references/lever-ladder.md`: L30 ("each tool and its
  description"), L43 (the description as the lever), L50 ("Better
  descriptions on the existing tools"), L60, and L99 (the cost order's "tool
  description edit"). In L99, rename the step to what is built (a code-tool
  clone with a new description) and keep its position. Note in the same line
  that an MCP or built-in tool gets a new description only through a
  wrapper. Do not reorder the rest (Q7).
- `experiment-analysis/SKILL.md` L338 and L353: check; likely naming the
  surface only.
- Sweep: `re-describ`, `redescrib`, `description edit`, `edit … description`,
  `better description`, `tool description`, `tool's description`,
  `tool_description`, in docs, skills and prompts.

**3.3 Wrappers work on any tool a run config can attach, except skills.**
Checked against Kiln's code-tool runtime (Kiln `main`, commit `6a04519`):

- `kiln_ai/datamodel/tool_id.py` `validate_tool_allowlist` rejects only skill
  ids and `kiln_unmanaged::` ids, plus duplicates and self-reference.
- `kiln_ai/tools/tool_registry.py` resolves an allowlisted id to a built-in
  tool, an MCP tool (remote or local), a Kiln task tool, a world tool or a
  code tool. Sandboxed code calls each by its function name.
- The wrapper templates call `tools.<tool_function_name>(…)` through `_call`.
  Nothing in them is specific to code tools (`tests/test_wrapper_templates.py`
  stubs `kiln.tools` the same way for any name).
- The gateway puts no limit of its own on `tool_allowlist`
  (`gateway/models.py` L295, L491, L501).
- Function names must be unique in one allowlist, except among MCP tools
  (`tool_registry.py`). Say so where the allowlist rules are listed.
- A wrapper reads a non-code tool's parameter schema with the gateway's
  `get_tool_definition` (`gateway/ops.py` L849-851, any tool id).
  `get_code_tool` gives it only for a code tool.

Known places to reword:

- `code-tool-authoring/SKILL.md` L92-94: "The wrap targets are the tools
  already on the parent rc (they are code tools themselves — nested
  code-tool → code-tool calls are supported, depth cap 10)."
- `code-tool-authoring/SKILL.md` L111-115: "code-tool ids allowed; skill ids
  and `kiln_unmanaged::` rejected". Name the accepted kinds.
- `code-tool-authoring/SKILL.md` L111-113 and
  `code-tool-authoring/references/wrappers/README.md` L6-8: "read it from the
  clone-source GET". Add `get_tool_definition` for MCP and built-in tools.
- `code-tool-authoring/SKILL.md` L163 onwards (the worked example): check
  that it does not imply a code-tool-only target.
- Keep the allowlist hygiene rule (never `kiln_tool::call_kiln_api`) exactly
  as it is.
- Sweep: `wrap`, `wrapper`, `code tool`, `code-tool`, `tool_allowlist`,
  `nested`, in docs, skills and prompts.

## Out of scope

- Installing without a clone (PyPI).
- Provenance checks.
- Promotion, or "shipping" a winner.
- The "harness" → "agent CLI" rename.
- The scrub pass. One thing seen in passing, for that pass and not changed
  here: `lever-ladder.md` L43 and L52 hold measured lever outcomes ("Five
  instruction-text arms … all lost at val"), which `.agents/AGENTS.md` keeps
  out of agent-facing files.
- Any code change for item 2.2.

## Done when

- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` and
  `uv sync --locked` pass.
- Every item above is fixed (or, for 2.2, reported) in every doc and skill
  where it appears, by the sweeps listed.
- The final summary lists each change with file and line, the verification
  for each fact, and what could not be verified: the v1.2 failure message
  (not run against a server), the v1.3 contents (unreleased; checked against
  Kiln `main`), the "older server" passages, and the uv output, if it could
  not run.
