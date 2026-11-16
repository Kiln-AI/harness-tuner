# Trace metrics: what code can compute from agent traces

Research date: 2026-11-10.

## 1. What a Kiln trace holds (read from code, 2026-11-10)

Kiln repository at `/home/user/kiln-ai/kiln`, commit `3e67b49`
(2026-11-10):

- `TaskRun.trace` is "The trace of the task run in OpenAI format. This is the
  list of messages that were sent to/from the model."
  (`libs/core/kiln_ai/datamodel/task_run.py`).
- `TaskRun.usage` (`Usage`): `input_tokens`, `output_tokens`,
  `total_tokens`, `cost`, `cached_tokens`, `total_llm_latency_ms`
  (`libs/core/kiln_ai/utils/usage.py`).
- `TaskRun.cumulative_usage`: "Sum of per-message token usage and cost across
  the entire trace". `TaskRun.synthetic_user_usage` keeps the synthetic
  user's spend apart from the agent's.
- Per message: "Per-call latency lives on the individual message's
  `latency_ms` field". Assistant messages carry `usage` per call (comment in
  `usage.py`, and Harness Tuner's `gateway/trace_files.py` docstring: "Kiln
  puts `usage` and `latency_ms` on each assistant message; an older trace has
  neither").
- Tool results can be marked as errors (`is_error` or `error_message`,
  per `USAGE_NOTE` in `gateway/trace_files.py`).

So per-turn input tokens, per-turn cached tokens, per-turn latency, every
tool call with its arguments, and every tool result text are all in the
stored trace. Everything in section 3 can be computed with no LLM.

## 2. What Harness Tuner already computes (read from code)

`src/kiln_harness_tuner/gateway/trace_files.py`, function `trace_stats`,
writes per item and per run config (`_stats.json`, `_stats.md` under
`runs/<run_id>/scratch/traces/<run_config_id>/`):

- `assistant_turns`, `user_turns`, `tool_calls`, `tool_errors`,
  `final_reply_chars`;
- per tool: `calls`, `errors`, `result_chars`, `max_result_chars`;
- `chars_by_role`, `system_chars` (system prompt plus tool definitions);
- `turn_usage`: per assistant turn, `input_tokens`, `output_tokens`,
  `cached_tokens`, `cost`, `latency_ms`, `tool_calls`;
- `run_usage`.

The `_stats.md` table shows, per item, turns, calls, errors, tokens, cached
tokens, cost and "largest tool result (chars)".

Also, `src/kiln_harness_tuner/probes.py` runs a "reconnaissance battery" of
code probes before wave 1 ("does it call a tool under its output mode, read
a skill, … emit valid JSON, produce output on the longest inputs").

And `src/kiln_harness_tuner/analysis.py` states a design rule: "Code computes
judgment-free numbers; agents interpret them (no verdict labels anywhere)."

**What is not computed today** (from reading `trace_stats`): repeated
identical calls (the arguments are parsed by `_calls` but not compared),
loops, tools defined but never called (the `tools` dict has only called
tools), a cache hit ratio, a context-growth figure, distribution statistics
across items (median, p95), and any threshold or flag. The stats are
written when an agent asks for train results through the gateway, not as a
report given to the planner at the start.

The overview does not mention that this module exists. Section 9 of the
overview reads as if red-flag detection starts from zero. It does not: the
raw counts exist; the flags, the cross-item roll-up and the "hand it to the
planner first" step do not.

## 3. Metric catalogue

Each row: what to compute, how, and what published source supports the
metric. "Threshold" is filled only where a source gives a number.

### 3.1 Tool result size

- **Compute:** characters and estimated tokens per tool result; max, p95 and
  total per tool across train traces; share of all input tokens that tool
  results take.
- **Sources and thresholds:**
  - Claude Code "displays a warning when MCP tool output exceeds 10,000
    tokens and limits output to 25,000 tokens by default"; text results over
    50,000 characters are saved to a file
    ([Claude Code MCP docs](https://code.claude.com/docs/en/mcp), read
    2026-11-10).
  - Anthropic: "For Claude Code, we restrict tool responses to 25,000 tokens
    by default"
    ([Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents),
    2025-09-11).
  - OpenAI Codex CLI: the fallback model profile truncates tool output at
    `TruncationPolicyConfig::bytes(10_000)`, configurable with
    `tool_output_token_limit`
    ([codex-rs/models-manager/src/model_info.rs](https://github.com/openai/codex/blob/main/codex-rs/models-manager/src/model_info.rs),
    main, read 2026-11-10). Per-model profiles can differ; I did not read
    them all.
- **Why it matters:** context rot. Chroma tested 18 models and "model
  performance consistently degrades with increasing input length"
  ([Chroma, Context Rot](https://www.trychroma.com/research/context-rot),
  2025-07-14). Anthropic: "as the number of tokens in the context window
  increases, the model's ability to accurately recall information from that
  context decreases"
  ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents),
  2025-09-29).
- **Inference:** 10k tokens (warning) and 25k tokens (hard cap) are the two
  published numbers from agent-harness vendors. They are product limits for
  coding agents, not measured optima for a recurring-task agent. A smaller
  target agent on a cheap model is likely to suffer earlier.

### 3.2 Tool error rate

- **Compute:** errors / calls per tool; share of traces with at least one
  error; error class (HTTP status, timeout, schema/argument error) by regex
  on the result text.
- **Source:** "lots of tool errors for invalid parameters might suggest tools
  could use clearer descriptions or better examples"
  ([Anthropic tools post](https://www.anthropic.com/engineering/writing-tools-for-agents)).
  TRAIL's system-execution branch names 401/403, 404, 429, 500 and timeouts
  as separate classes
  ([TRAIL](https://www.patronus.ai/research/trail)).
- **Threshold:** none published. I searched for a production alert threshold
  for tool error rate and found only vendor dashboards with no number. AWS's
  AgentCore framework tracks "Tool pass rate: % of tool calls returning a
  valid response" with no stated target
  ([AWS ML blog](https://aws.amazon.com/blogs/machine-learning/building-trust-in-ai-agents-a-production-ready-evaluation-framework-with-amazon-bedrock-agentcore)).
- **Inference:** split errors by class. Argument errors point to the tool
  description (harness lever). 401/403/404/5xx errors point to the
  environment (overview 7.7), and no harness change will fix them.

### 3.3 Repeated calls and loops

- **Compute:** hash of (tool name, canonical JSON arguments). Count exact
  repeats per trace. Detect cycles of length 1 to k.
- **Published thresholds (code, not papers):**
  - OpenHands SDK `StuckDetector`
    ([docs](https://docs.openhands.dev/sdk/guides/agent-stuck-detector);
    [source](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/stuck_detector.py)):
    "The same action produces the same observation repeatedly (4+ times)";
    "The same action repeatedly results in errors (3+ times)"; "Agent
    Monologue … (3+ messages)"; "Two different action-observation pairs
    alternate in a ping-pong pattern (6+ cycles)"; repeated context-window
    errors. It scans the last 20 events.
  - Gemini CLI `loopDetectionService.ts`
    ([source](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/loopDetectionService.ts),
    main, read 2026-11-10): `TOOL_CALL_LOOP_THRESHOLD = 5`, checked for
    repeating cycles of length 1 to 5 over the tool-call key history;
    `CONTENT_LOOP_THRESHOLD = 10` over 50-character chunks of streamed text;
    and an **LLM check** that starts only after `LLM_CHECK_AFTER_TURNS = 30`
    turns, reads the last 20 turns, runs every 5 to 15 turns, and needs
    confidence `0.9`, with a second "double-check" model.
- **Paper support:** MAST FM-1.3 "Step repetition" is the most frequent
  failure mode at 15.7%
  ([arXiv 2503.13657](https://arxiv.org/abs/2503.13657)). TRAIL "Resource
  Abuse: Unnecessary repetition of tool calls".
- **Note:** the Gemini CLI design is a good model for this project: cheap
  code checks every turn, and an LLM check only after a code-defined
  trigger. That is the overview's cost order in section 9, already shipped
  by another vendor.
- **Caution:** exact repeats are not always wrong (polling, retries after a
  fix). The overview's recurring-task agents rarely need to poll, but the
  flag must say "repeated", not "wrong". OpenHands skips the check when
  invalid calls share a response (`_invalid_calls_share_response`).

### 3.4 Turns and tool calls per task

- **Compute:** assistant turns and tool calls per trace; median and p95 per
  eval; ratio of failing-item median to passing-item median.
- **Sources:** Anthropic tracks `n_turns`, `n_toolcalls`, `n_total_tokens`
  and sets `max_turns`
  ([Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents),
  2026-01-09). The OpenTelemetry metric `gen_ai.invoke_agent.tool_calls` is a
  histogram with buckets `[1, 2, 4, 8, 16, 32, 64, 128]`
  ([OTel GenAI metrics](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)).
  "failed trajectories are consistently longer"
  ([arXiv 2511.00197](https://arxiv.org/pdf/2511.00197)). Anthropic:
  "Tracking tool calls can help reveal common workflows that agents pursue
  and offer some opportunities for tools to consolidate."
- **Threshold:** none in absolute terms; it depends on the task.
- **Derived metric worth computing:** the most frequent ordered n-grams of
  tool names across traces (for example A→B→C appears in 80% of traces).
  This is the code evidence for the overview's "the agent often makes these
  seven calls in a row, so one tool does it" (section 6.3) and for the
  context-loading roll-up (section 7.5). I found no published tool that does
  this for harness tuning; it is plain counting.

### 3.5 Context growth

- **Compute:** from `turn_usage`, the input tokens at each assistant turn;
  growth per turn; the largest single-turn jump and which tool result caused
  it; the peak as a share of the model's context window.
- **Sources:** Manus: "the context grows with every step, while the output …
  remains relatively short … the average input-to-output token ratio is
  around 100:1"
  ([Manus, Context Engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus),
  2025-07). Chroma: degradation on "~113K-token inputs" in its
  conversational-memory test, as summarised by
  [Upsolve](https://upsolve.ai/blog/context-rot) (secondary).
- **Threshold:** none published as a "flag at X%" for agents. Claude Code
  turns on tool search when tool definitions reach 10% of the context window
  (section 3.8), which is the nearest published percentage.

### 3.6 Cache hit rate

- **Compute:** sum(cached_tokens) / sum(input_tokens) per trace and per run
  config. Also per turn: a turn after the first with near zero cached tokens
  means the prefix changed.
- **Sources:** Manus: "the KV-cache hit rate is the single most important
  metric for a production-stage AI agent"; "cached input tokens cost 0.30
  USD/MTok, while uncached ones cost 3 USD/MTok—a 10x difference". Common
  breakers: "including a timestamp … at the beginning of the system prompt";
  non-deterministic JSON key order; editing earlier actions or observations.
  Anthropic prompt caching
  ([docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
  read 2026-11-10): cache read is 0.1x base input price; 5-minute writes
  1.25x; "Modifying tool definitions (names, descriptions, parameters)
  invalidates the entire cache"; the lookback window is 20 blocks; minimum
  cacheable length is 512 to 4,096 tokens depending on model.
- **Caveat:** `cached_tokens` is "None if not reported" (Kiln API docs in
  `agent_files/skills/kiln-api/references/api_docs/`). Some providers do not
  report it, and some cache only above a minimum length. A zero must be
  read as "unknown or zero", by provider. Overview 6.2 already relies on the
  cache argument; this metric is the evidence for it.
- **Inference:** a single-turn classifier with a short prompt may sit below
  the provider's minimum cacheable length. Then a 0% hit rate is expected
  and is not a flag.

### 3.7 Unused tools and skills

- **Compute:** tools in the run config's tool list with zero calls across all
  train traces; skills never read. For each, its definition size in tokens.
- **Sources:** Claude Code `/skill-doctor`: "Every skill in the skill listing
  adds to your context on every turn, whether or not Claude ever uses it …
  It flags skills in the listing that have never been invoked … start with
  the ones that have the highest context cost"
  ([Claude Code skills docs](https://code.claude.com/docs/en/skills), read
  2026-11-10). Anthropic: "curating a minimal viable set of tools"
  ([context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
- **Caution:** "never called" on train may mean "the description is poor"
  (the tool should be called and is not), not "remove it". The flag is a
  fact; the experiment decides.

### 3.8 Errors in the final answer channel

- **Compute:** trace ends with no final assistant text; final output fails
  JSON parse or schema; output truncated at max tokens (finish reason).
- **Source:** TRAIL: formatting errors and instruction non-compliance are
  "nearly 42% of all errors" ([dataset card](https://huggingface.co/datasets/PatronusAI/TRAIL)).
  Harness Tuner's recon battery already checks "emit valid JSON" and
  "produce output on the longest inputs" (`probes.py`).

### 3.9 Latency per turn and per tool

- **Compute:** `latency_ms` per assistant turn; total; share of wall time in
  LLM calls versus tools. Kiln stores per-message LLM latency; I did not
  confirm that Kiln stores tool execution time.
- **Source:** Anthropic recommends collecting "the total runtime of
  individual tool calls and tasks"
  ([tools post](https://www.anthropic.com/engineering/writing-tools-for-agents)).

## 4. Standards for names

The OpenTelemetry GenAI semantic conventions (status: Development) name the
spans `invoke_agent`, `chat` and `execute_tool`, and the attributes
`gen_ai.usage.input_tokens` ("SHOULD include cached tokens"),
`gen_ai.usage.cache_read.input_tokens`, `gen_ai.tool.name`,
`gen_ai.tool.call.id`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`
and `error.type`
([agent spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md);
[metrics](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md);
read 2026-11-10). Kiln traces are OpenAI message lists, not OTel spans.
Inference: using OTel names for the red-flag report costs nothing and keeps
the door open to TRAIL-style tooling, but there is no need to convert
traces.
