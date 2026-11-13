# Model and thinking-level routing during a conversation; the advisor pattern

Research date: 2026-11-10. Overview baseline: section 7.9 ("Switching model
or thinking level during a conversation … Being considered") and 7.2
("Raising the thinking level or the model on the main thread fills the
context, and you cannot go back down afterwards").

## Summary

There are four distinct forms, with different evidence:

1. **Per-request routing** to a cheap or a strong model (RouteLLM style).
   Solid 2024 evidence; 2026 production figures are vendor ranges.
2. **Confidence cascades**: run the cheap model, escalate when unsure.
   Several 2025–2026 papers; calibration is the main problem.
3. **Switching the model mid-conversation.** Breaks the prompt cache (each
   model has its own cache), and is reported to hurt consistency.
4. **The advisor pattern** (new, not in the overview): a cheap executor
   model runs the whole task and calls a stronger model as a tool at
   decision points. Anthropic shipped it as a server tool (beta
   `advisor-tool-2026-03-01`, April 2026). Vendor results: better scores
   and **lower** cost per task than the executor alone.

## 1. Per-request routing

RouteLLM (LMSYS, July 2024, [blog](https://lmsys.org/blog/2024-07-01-routellm/))
reported cost cuts of over 85% on MT Bench, 45% on MMLU and 35% on GSM8K
against a strong-only baseline; one router reached 95% of GPT-4 quality with
14% of queries to the strong model (via search summary). 2026 sources give
ranges of 20–85% with no methodology. Savings depend on how easy the
traffic is. Older than 12 months.

## 2. Confidence cascades

- C3PO (NeurIPS 2025, [arXiv](https://arxiv.org/html/2511.07396v1)): under
  20% of the strongest model's cost on 16 benchmarks (abstract via search).
- Zellinger et al. 2025 (early abstention): about −13% cost and −5% error
  (secondary review).
- Conformal Cascade (2026): raw confidence thresholds "have no formal tie to
  error rates".
- *Inference:* this is the natural partner of decision models, which return
  probabilities (see [decision-models.md](./decision-models.md)).

## 3. Mid-conversation switches and caching

- Claude Code docs ([prompt caching](https://code.claude.com/docs/en/prompt-caching)):
  "Each model has its own cache. Switching models recomputes the entire
  request even when the content is identical" (via search summary).
- Effort/thinking level: Anthropic docs state effort is not part of the
  cache key, but a September 2026 bug report says mid-session effort changes
  invalidate the cache on Sonnet 5 and Opus 5
  ([claudeissues.com #92444](https://claudeissues.com/issue/92444-bug-effort-mid-session-invalidates-the-prompt-cache-on-sonnet-5-and-opus-5-full)).
  Unresolved conflict; check cache-read tokens after a switch.
- LiteLLM's auto-router docs claim router plus caching beat caching alone
  on one fixed model ([docs](https://docs.litellm.ai/docs/auto_router/prompt_caching));
  vendor claim, not verified.
- MindStudio argues mid-conversation switches lower quality, citing Cursor
  research with no figures I could check
  ([link](https://www.mindstudio.ai/blog/never-switch-models-mid-conversation-ai-agents)).

*Inference:* the overview's claim that you "cannot go back down" is about
context, not the model. Cache cost is a second reason a switch is
expensive. Both favour routing at the start of a conversation, or a
sub-agent/advisor call that keeps the main thread on one model.

## 4. The advisor pattern

Anthropic, "The advisor strategy", 2026-04-09
([claude.com](https://claude.com/resources/articles/the-advisor-strategy)):

> "Sonnet or Haiku runs the task end-to-end as the executor … When the
> executor hits a decision it can't reasonably solve, it consults Opus for
> guidance as the advisor. Opus accesses the shared context and returns a
> plan, a correction, or a stop signal … The advisor never calls tools or
> produces user-facing output".

> "This inverts a common sub-agent pattern, where a larger orchestrator model
> decomposes work and delegates to smaller worker models."

Results (vendor):

- Sonnet + Opus advisor: +2.7 points on SWE-bench Multilingual, −11.9% cost
  per agentic task. Secondary sources give 72.1% at $1.09/task (solo) vs
  74.8% at $0.96/task.
- Haiku + Opus advisor on BrowseComp: 41.2% against 19.7% solo; "trails
  Sonnet solo by 29% in score but costs 85% less per task."
- Advisor output is typically 400–700 tokens per call. `max_uses` caps calls.

Mechanics (Claude Code docs, [advisor](https://code.claude.com/docs/en/advisor);
[mager.co](https://mager.co/blog/2026-05-08-advisor-strategy-principal-agent/)):
the executor calls a tool with empty input; the server runs the advisor on
the full transcript; advice returns as a tool result in the same request.
The advisor must rank at or above the executor. When to call, from
Anthropic's suggested prompt: before substantive work, when stuck, and once
before declaring done. Experimental; Anthropic API only (not Bedrock,
Vertex, Foundry). OpenRouter offers a cross-provider `openrouter:advisor`
server tool (beta).

Fit (Claude Code docs): "long, multi-step tasks where most turns are routine
but plan quality determines the outcome". Less value "on short tasks where
there is little to plan".

## Implications for the tuner (inference)

- The advisor pattern solves the overview's 7.2 problem ("support bot hands
  deep work to an analyst") without decomposition, and keeps the main
  context on the cheap model. It is a strong candidate for multi-turn and
  long single-turn agents on a cheap model.
- For classification, a confidence cascade (decision model or small LLM,
  then a larger model) is the routing form with the best fit.
- Thinking level is not monotone. Inverse-scaling results (arXiv
  2507.14417) show longer reasoning can lower accuracy on distractor-heavy
  inputs. Test low thinking levels, not only high.
- Kiln: per overview 7.9, mid-conversation switching is not in Kiln. I did
  not check whether Kiln can pass provider server tools such as the advisor
  tool; that is the Kiln capability gap subtopic.

## Confirms, contradicts, misses

- **Confirms:** 7.2 and 7.9 — switching on the main thread is costly; a
  handoff to a stronger agent is valuable.
- **Misses:** the advisor pattern (shipped by Anthropic and OpenRouter in
  2026, with published cost and score gains); confidence cascades; cache
  invalidation as a cost of switching; non-monotone thinking-level effects.
