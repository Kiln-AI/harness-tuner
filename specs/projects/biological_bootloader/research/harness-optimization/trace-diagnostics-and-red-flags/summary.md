# Trace Diagnostics and Red Flags

Research date: 2026-11-10.

## Bottom Line

Plain code can compute most red flags of a weak harness from a Kiln trace:
- oversize tool results
- tool errors by class
- repeated calls and loops
- turns and calls per task
- context growth
- cache hit rate
- unused tools and skills
- frequent call chains
- output parse failures

Code can also run static checks on the size and structure of the prompt, the tools and the skills.

Code should run first. LLMs are poor at finding where a failure starts in a long trace: the best model got 11% on TRAIL and 14.2% step accuracy on Who&When. They are better at naming the kind of failure once code points to the place.

The published thresholds are product limits from agent vendors (Claude Code, Codex, OpenHands, Gemini CLI) and spec recommendations. They are not measured optima for recurring-task agents.

No published stack rank exists. The evidence supports this order: environment and eval faults, then context floods, then loops, then tool-surface faults, then missing context, then prompt specification. Caching and hyperparameters come last.

Harness Tuner already computes most raw trace counts in `gateway/trace_files.py`. The overview does not say so.

## Key Findings

- **Failure taxonomies share a common core.**
  - MAST has 14 failure modes in 3 categories. The largest are step repetition (15.7%), reasoning-action mismatch (13.2%), unaware of termination (12.4%) and disobey task spec (11.8%).
  - TRAIL has 20+ leaf categories under reasoning, system execution and planning. Each error has a Low/Medium/High impact field.
  - AgentErrorTaxonomy centres on cascading root causes.
  - Sources: [MAST](https://arxiv.org/abs/2503.13657), [TRAIL](https://www.patronus.ai/research/trail), [AgentDebug](https://arxiv.org/abs/2509.25370).
- **LLMs are poor at locating failures in long traces.**
  - TRAIL: the best joint accuracy is 11%, and context-handling failures are "almost never caught".
  - Who&When: 53.5% accuracy for the failing agent, but 14.2% for the failing step. Accuracy falls as context length grows.
  - So code should find the positions and an LLM should name the cause. Source: [Who&When](https://arxiv.org/abs/2505.00212).
- **Error-analysis practice** (Husain and Shankar):
  - Note the first failure in each trace.
  - Group the notes into categories and count each one.
  - Stop when new traces give no new categories.
  - Do not hand the first reading of traces or root-cause analysis to an LLM.
  - The tuner has no human in the loop, so it can copy the structure of this method but not the human steps.
  - Source: [Husain and Shankar](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html).
- **Anthropic maps trace metrics to fixes.**
  - Many redundant calls: look at pagination and token limits.
  - Many invalid-parameter errors: improve the tool descriptions.
  - Fixed call sequences: consolidate them into one tool.
  - A 0% pass rate across many trials "is most often a signal of a broken task".
  - Sources: [tools post, 2025-09-11](https://www.anthropic.com/engineering/writing-tools-for-agents), [evals post, 2026-01-09](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- **Tool result size thresholds.**
  - Claude Code warns at 10,000 tokens of MCP output and caps it at 25,000 tokens. It saves text results over 50,000 characters to a file.
  - The Codex fallback profile truncates tool output at 10,000 bytes.
  - Sources: [Claude Code MCP docs](https://code.claude.com/docs/en/mcp), [Codex source](https://github.com/openai/codex/blob/main/codex-rs/models-manager/src/model_info.rs).
- **Loop thresholds in shipped code.**
  - OpenHands flags 4 identical action-observation pairs, 3 identical action-error pairs, 3 monologue messages, or 6 ping-pong cycles.
  - Gemini CLI flags 5 repeats of a tool-call cycle of length 1 to 5.
  - Gemini CLI runs an LLM loop check only after 30 turns, and the check must reach 0.9 confidence.
  - Sources: [OpenHands](https://docs.openhands.dev/sdk/guides/agent-stuck-detector), [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/loopDetectionService.ts).
- **Static limits.**
  - A skill description has a maximum of 1,024 characters.
  - A `SKILL.md` body should be under 500 lines (fewer than 5,000 tokens).
  - References should go one level deep only.
  - Each skill costs about 100 tokens of metadata.
  - The Claude Code skill listing has a budget of 1% of the context window.
  - Claude Code cuts a tool description at 4,096 characters when the tool loads up front.
  - Tool selection "degrades once you exceed 30–50 available tools". Anthropic recommends tool search at 10 or more tools or more than 10k tokens of definitions.
  - Sources: [skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), [Agent Skills spec](https://agentskills.io/specification), [tool search docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool).
- **The tool-description check must flag both directions.**
  - 97.1% of 856 MCP tool descriptions had at least one smell, and 56% did not state their purpose.
  - Improving the descriptions gave a median +5.85 points of task success.
  - But it also added 67% more steps and made results worse in 16.67% of cases.
  - Source: [arXiv 2602.14878](https://arxiv.org/html/2602.14878v3).
- **Cache hit rate.**
  - Manus calls it "the single most important metric".
  - Common cache breakers: a timestamp at the start of the prompt, an unstable JSON key order, and edits to earlier history.
  - A cache read costs 0.1x the base input price.
  - A zero hit rate is not always a flag: a prompt below the provider's minimum length is not cached.
  - Sources: [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus), [Anthropic caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
- **The tuner already counts most of this.**
  - `trace_stats` gives turns, calls, errors, result size per tool, tokens per turn, cached tokens and latency.
  - `probes.py` runs code probes before wave 1.
  - Missing: repeats and cycles, unused tools, a cache ratio, context growth, distributions across items, a pass-versus-fail comparison, thresholds, and a report given to the planner at the start.
  - `analysis.py` says "no verdict labels anywhere", which conflicts with red flags. Proposal: state each flag as a fact plus a reference number and its source.

## Details

- [failure-taxonomies.md](./failure-taxonomies.md): MAST, TRAIL, AgentErrorTaxonomy and Who&When, with a table that maps each failure family to a harness lever and a symptom code can see.
- [error-analysis-practice.md](./error-analysis-practice.md): the Husain and Shankar method, the limits of LLMs, Anthropic's practice, and what the tuner can copy.
- [trace-metrics.md](./trace-metrics.md): what a Kiln trace holds, what the tuner computes today, and a catalogue of metrics with sources and thresholds.
- [static-harness-checks.md](./static-harness-checks.md): a table of published limits, the tool-smell studies, and which checks are code and which need an LLM.
- [code-versus-llm.md](./code-versus-llm.md): the four tiers in detail, and the conflict with "no verdict labels".
- [blocking-order.md](./blocking-order.md): a draft stack rank of nine levels, with evidence for each.
- [baseline-comparison.md](./baseline-comparison.md): what confirms, contradicts or is missing from overview sections 9 and 10.

## Open Questions / Gaps

- No published threshold for tool error rate, turns per task, context growth or prompt length. The overview's 100,000-character example has no source for or against it.
- No published stack rank. The order in `blocking-order.md` is my synthesis, and tuner runs must test it.
- All numeric thresholds come from coding agents on frontier models. I found no data for cheap models on recurring tasks.
- Not checked: whether Kiln stores tool execution time, and whether `cached_tokens` is filled for each provider.
- Read second-hand: the Anthropic "advanced tool use" post (2025-11-24) and its 55K/134K token figures, and the Chroma ~113K figure. The Exa tools failed with an authentication error. arxiv.org and manus.im refused direct fetch, so I read them through Tavily.

## Sources

- [MAST](https://arxiv.org/abs/2503.13657) (v3 2025-10-26, NeurIPS 2025)
- [TRAIL](https://www.patronus.ai/research/trail) and its [dataset](https://huggingface.co/datasets/PatronusAI/TRAIL) (2025-05/06)
- [AgentDebug](https://arxiv.org/abs/2509.25370) (2025-09)
- [Who&When](https://arxiv.org/abs/2505.00212) (ICML 2025)
- [Success and Failure Trajectories](https://arxiv.org/pdf/2511.00197) (2025-11)
- Husain and Shankar: [error analysis](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html) (2025-06-27), [what parts of evals LLMs can automate](https://hamel.dev/blog/posts/evals-faq/what-parts-of-evals-can-be-automated-with-llms.html) (2025-07-24)
- Anthropic posts: [tools](https://www.anthropic.com/engineering/writing-tools-for-agents) (2025-09-11), [context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (2025-09-29), [evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) (2026-01-09)
- Claude Code docs: [MCP](https://code.claude.com/docs/en/mcp), [skills](https://code.claude.com/docs/en/skills)
- Claude platform docs: [skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), [tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool), [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Agent Skills spec](https://agentskills.io/specification)
- [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) (2025-07)
- [Chroma, Context Rot](https://www.trychroma.com/research/context-rot) (2025-07-14)
- [IFScale](https://arxiv.org/html/2507.11538v1) (2025-07)
- MCP tool-smell papers: [2602.14878](https://arxiv.org/html/2602.14878v3), [2602.18914](https://arxiv.org/html/2602.18914v1) (2026-02)
- Loop detectors: [OpenHands StuckDetector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector), [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/loopDetectionService.ts)
- [Codex `model_info.rs`](https://github.com/openai/codex/blob/main/codex-rs/models-manager/src/model_info.rs)
- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)
- Kiln repository at `3e67b49` (`datamodel/task_run.py`, `utils/usage.py`)
- Harness Tuner `gateway/trace_files.py`, `probes.py`, `analysis.py`

All web sources read 2026-11-10 unless a date is given.
