# Agent failure taxonomies

Research date: 2026-11-10. Each source has its date. "Inference" marks a
statement that is mine and not the source's.

## Why this matters for the tuner

A taxonomy gives the planner a fixed set of names for what it sees in a
trace. Each name can then map to a harness lever. The three taxonomies below
are the ones with published, annotated trace data. None of them is made for
single-agent recurring tasks of the kind Harness Tuner tunes, so each needs
mapping (see the last section).

## 1. MAST: Multi-Agent System Failure Taxonomy (Berkeley)

Source: Cemri et al., "Why Do Multi-Agent LLM Systems Fail?",
[arXiv 2503.13657](https://arxiv.org/abs/2503.13657) (v3, 2025-10-26),
NeurIPS 2025 Datasets and Benchmarks track
([paper PDF](https://proceedings.neurips.cc/paper_files/paper/2025/file/b1041e52d3be19f0a9bc491657488e4a-Paper-Datasets_and_Benchmarks_Track.pdf)).
Code, data and the LLM annotator:
[github.com/multi-agent-systems-failure-taxonomy/MAST](https://github.com/multi-agent-systems-failure-taxonomy/MAST).

Method: Grounded Theory on more than 150 traces ("each averaging over 15,000
lines of text"), six expert annotators, inter-annotator agreement κ = 0.88.
MAST-Data has 1600+ annotated traces over 7 frameworks. 14 failure modes in 3
categories. Frequencies, as the paper gives them:

| Category | Failure mode | Share |
|---|---|---|
| FC1 System design | FM-1.1 Disobey task specification | 11.8% |
| | FM-1.2 Disobey role specification | 1.5% |
| | FM-1.3 Step repetition | 15.7% |
| | FM-1.4 Loss of conversation history | 2.8% |
| | FM-1.5 Unaware of termination conditions | 12.4% |
| FC2 Inter-agent misalignment | FM-2.1 Conversation reset | 2.2% |
| | FM-2.2 Fail to ask for clarification | 6.8% |
| | FM-2.3 Task derailment | 7.4% |
| | FM-2.4 Information withholding | 0.85% |
| | FM-2.5 Ignored other agent's input | 1.9% |
| | FM-2.6 Reasoning-action mismatch | 13.2% |
| FC3 Task verification | FM-3.1 Premature termination | 6.2% |
| | FM-3.2 No or incomplete verification | 8.2% |
| | FM-3.3 Incorrect verification | 9.1% |

Statements from the paper that matter here:

- "Failures in FC1 occur during execution but often reflect flaws in
  pre-execution design choices regarding system architecture, prompt
  instructions, or state management." The paper gives three root causes for
  FM-1.1/1.2: MAS design, "poor user prompt specifications", or "limitations
  of the underlying LLM".
- "improving agent role specifications alone yields a +9.4% success rate
  increase for ChatDev with the same user prompt and LLM (GPT-4o)."
- "adding a high-level task objective verification step to ChatDev yields a
  +15.6% improvement".
- "many existing verifiers perform only superficial checks".
- MAST ships an LLM-as-judge annotator "with high agreement with human
  annotations".

Relevance: FM-1.3 (step repetition) and FM-1.5 (termination) together are
28% of failures, and both have code-visible symptoms (repeated calls, very
long traces). FM-2.2 (fail to ask for clarification) is the multi-turn
case that the overview section 7.8 describes.

## 2. TRAIL: Trace Reasoning and Agentic Issue Localization (Patronus AI)

Sources: [Patronus blog](https://www.patronus.ai/blog/introducing-trail-a-benchmark-for-agentic-evaluation)
(2025-06-05); [paper page](https://www.patronus.ai/research/trail)
(Deshpande et al., 2025-05-13);
[dataset](https://huggingface.co/datasets/PatronusAI/TRAIL);
[code](https://github.com/patronus-ai/trail-benchmark).

148 traces from GAIA and SWE-Bench, 1,987 OpenTelemetry spans, 841 errors,
"an average of 5.68 errors and 110 annotation minutes per trace". Average
input over 200k tokens, maximum 6M. Three roots, 20+ leaves:

- **Reasoning errors**: text-only hallucination; tool-related hallucination
  ("fabricate tool outputs or misunderstand tool capabilities"); poor
  information retrieval; tool output misinterpretation; incorrect problem
  identification; tool selection error; formatting errors; instruction
  non-compliance.
- **System execution errors**: incorrect tool definition ("Agent environment
  or prompt misconfiguration that misstates tool behavior"); environment
  setup errors; rate limiting (429); authentication (401/403); service
  errors (500); resource not found (404); resource exhaustion; timeout
  issues ("Infinite loops or excessively long computations").
- **Planning and coordination errors**: context handling failures; resource
  abuse ("Unnecessary repetition of tool calls or context in planning");
  goal deviation; task orchestration errors.

(Leaf definitions as quoted by
[Emergent Mind's TRAIL summary](https://www.emergentmind.com/topics/trail-taxonomy);
category list from the repository's `calculate_scores.py` as shown by
[DeepWiki](https://deepwiki.com/patronus-ai/trail-benchmark/3.3-error-categories-and-annotations).)

Each annotation carries a span ID, a category, an evidence quote, a
description and an **impact level (Low, Medium, High)**. That impact field is
the only published per-error severity scale I found.

Findings that matter here:

- "the best model (Gemini-2.5-Pro) achieving only 11% joint accuracy" at
  localising errors in traces
  ([dataset card](https://huggingface.co/datasets/PatronusAI/TRAIL)).
- "context handling failures are almost never caught" by LLM judges
  ([blog](https://www.patronus.ai/blog/introducing-trail-a-benchmark-for-agentic-evaluation)).
- "Output Generation errors (particularly Formatting Errors and Instruction
  Non-compliance) accounting for nearly 42% of all errors" (dataset card).
- Annotator disagreement was highest in "Resource Abuse, Hallucinations, and
  Information Retrieval" (Emergent Mind summary).

Inference: the whole "System execution" branch except "incorrect tool
definition" is visible to code: HTTP status codes, timeouts, error flags on
tool results. "Resource abuse" is partly visible to code (identical repeated
calls). Most of "Reasoning" needs an LLM or a human.

## 3. AgentErrorTaxonomy and AgentDebug (UIUC)

Source: Zhu et al., "Where LLM Agents Fail and How They Can Learn From
Failures", [arXiv 2509.25370](https://arxiv.org/abs/2509.25370) (2025-09);
code [github.com/ulab-uiuc/AgentDebug](https://github.com/ulab-uiuc/AgentDebug).

Modules: memory, reflection, planning, action, system. The repository lists
for **action**: misalignment, invalid_action, format_error, parameter_error;
for **system**: step_limit, tool_execution_error, llm_limit,
environment_error. Data from ALFWorld, GAIA and WebShop.

Central claim: agents are vulnerable "to cascading failures, where a single
root-cause error propagates through subsequent decisions". AgentDebug
"isolates root-cause failures and provides corrective feedback", with "24%
higher all-correct accuracy and 17% higher step accuracy compared to the
strongest baseline", and "up to 26% relative improvements in task success".

Relevance: the root-cause idea is the basis for "fix the first failure
first" (see [blocking-order.md](./blocking-order.md)).

## 4. Who&When: automated failure attribution (ICML 2025)

Source: Zhang et al., "Which Agent Causes Task Failures and When?",
[arXiv 2505.00212](https://arxiv.org/abs/2505.00212);
[PMLR PDF](https://raw.githubusercontent.com/mlresearch/v267/main/assets/zhang25cq/zhang25cq.pdf).

127 multi-agent systems with annotated decisive error steps. "The best method
achieves 53.5% accuracy in identifying failure-responsible agents but only
14.2% in pinpointing failure steps, with some methods performing below
random. Even SOTA reasoning models, such as OpenAI o1 and DeepSeek R1, fail
to achieve practical usability." The paper's Finding 4: "Failure attribution
performance declines as context length increases, with step-level accuracy
being more sensitive." A hybrid method (all-at-once to pick the agent, then
step-by-step) is best on step accuracy but uses about 149k tokens against
17k for all-at-once
([alphaXiv summary](https://www.alphaxiv.org/abs/2505.00212)).

Relevance: an LLM given a whole long trace finds *which* component failed
much better than *where*. Code that narrows the trace first (to the first
error flag, the largest tool result, the first repeated call) should make the
LLM pass cheaper and more accurate. That last step is inference; I found no
paper that measures "code pre-filter then LLM" on these datasets.

## 5. Single-agent trajectory evidence

"An Empirical Study of Success and Failure Trajectories"
([arXiv 2511.00197](https://arxiv.org/pdf/2511.00197), 2025-11) analyses
OpenHands, SWE-agent and Prometheus on SWE-Bench: "failed trajectories are
consistently longer". OpenHands and SWE-agent take "around 20 more steps in
failures in SWE-Bench Lite". Trace length is a code-computable proxy for
failure, but its scale depends on the agent, so the paper itself compares
lengths as a percentage of the successful length.

## 6. Mapping to Harness Tuner levers (inference)

This table is my synthesis. It is not from any one source.

| Failure family (source) | First harness lever to test | Code-visible symptom |
|---|---|---|
| Disobey task spec, instruction non-compliance (MAST 1.1, TRAIL) | system prompt (overview 6.1) | none reliable; eval failures cluster |
| Step repetition, resource abuse (MAST 1.3, TRAIL) | tool design, pagination, roll-up tool (6.3, 7.5) | identical call repeated; same tool many times per trace |
| Unaware of termination, premature termination (MAST 1.5, 3.1) | prompt stop conditions; max turns | turns per trace far above the median; trace ends with no final reply |
| Tool output misinterpretation, poor retrieval (TRAIL) | tool output shape, response format, RAG config (7.1) | very large tool results; low share of result text quoted later (needs LLM) |
| Tool selection error, incorrect tool definition (TRAIL) | tool descriptions, tool count, namespacing | calls to a tool that errors on arguments; unused tools |
| Context handling failure, loss of history (TRAIL, MAST 1.4) | sub-agent, compaction, input transform (7.2, 7.3) | input tokens per turn grow fast; context near the window |
| Fail to ask for clarification (MAST 2.2) | prompt, multi-turn eval with synthetic user (7.8) | no question mark in turns before the first tool call (weak) |
| No or incorrect verification (MAST 3.2, 3.3) | add a check step or a validator tool | no call to a validation tool before the final reply |
| System execution: 401/403/404/429/500, timeouts (TRAIL) | environment, keys, OpenEnv (7.7) | error flags and status codes in tool results |
