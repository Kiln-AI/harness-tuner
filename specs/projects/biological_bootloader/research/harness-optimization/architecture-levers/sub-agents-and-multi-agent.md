# Sub-agents and multi-agent designs against one context

Research date: 2026-11-10. Overview baseline: section 7.2 (sub-agents) and the
sidebar on model and harness as a pair (section 6.4).

## Summary

The evidence from the last 12 months is consistent. A single agent is the
default. Sub-agents help in a small number of named conditions. Outside those
conditions they cost 3–15x more tokens and often lower quality. The largest
recent controlled studies find a capability threshold: coordination helps a
weak single agent and hurts a strong one, with a crossing near 45%
single-agent success. A 2026 study on a conversational benchmark with a user
in the loop found no multi-agent benefit at all.

## When sub-agents help

**Anthropic, "When to use multi-agent systems (and when not to)", 2026-01-23**
([claude.com](https://claude.com/resources/articles/building-multi-agent-systems-when-and-how-to-use-them)).
Three conditions, quoted:

> "when context pollution degrades performance, when tasks can run in
> parallel, and when specialization improves tool selection or task focus.
> Outside these situations, the coordination costs typically exceed the
> benefits."

On context isolation, the same page gives a concrete test:

> "Context isolation is most effective when subtasks generate high context
> volume (more than 1000 tokens) but most of that information is irrelevant
> to the main task, when the subtask is well-defined with clear criteria for
> what information to extract, and for lookup or retrieval operations that
> require filtering before use."

Other rules from the same page (quoted through a fetch summary):

- "Group work by what context it requires, not by what kind of work it is."
- Splitting by role (planner, implementer, tester) is an anti-pattern. In
  one Anthropic experiment "the subagents spent more tokens on coordination
  than on actual work."
- "The primary benefit of parallelization is thoroughness, not speed."
  Parallel agents "often take longer overall than single-agent systems".
- Verification sub-agents work because "Verification requires minimal
  context transfer by nature." But "verification agents take shortcuts"
  without explicit instructions (the "early victory problem").

**Anthropic, multi-agent research system, 2025-06-13**
([anthropic.com](https://www.anthropic.com/engineering/multi-agent-research-system)).
The orchestrator–worker research system "outperformed single-agent Claude
Opus 4 by 90.2% on our internal research eval". But "token usage by itself
explains 80% of the variance" on BrowseComp, and "multi-agent systems use
about 15× more tokens than chats". Domains "that require all agents to share
the same context" are "not a good fit for multi-agent systems today", with
coding named as an example. *Inference:* much of the research gain is extra
token spend, so a fair comparison must hold tokens equal.

**Cognition (Walden Yan).** "Don't Build Multi-Agents" (June 2025) gave two
principles: share full context and traces, and "actions carry implicit
decisions", so parallel writers conflict. The April 2026 follow-up,
"Multi-Agents: What's Actually Working"
([cognition.com](https://cognition.com/blog/multi-agents-working)), narrows
this: a class works where extra agents contribute intelligence (review,
advice) while **writes stay single-threaded**. I could not fetch
cognition.ai directly (network block); these points come from search
summaries and secondary write-ups
([jxnl.co](https://jxnl.co/writing/2025/09/11/why-cognition-does-not-use-multi-agent-systems/)).

## When sub-agents hurt — controlled evidence

- **Kim et al., "Towards a Science of Scaling Agent Systems", arXiv
  2512.08296, Dec 2025** ([arXiv](https://arxiv.org/abs/2512.08296v1)). 180
  configurations, five architectures, three model families. Reported
  findings (from search summary, not full read): "coordination yields
  diminishing or negative returns once single-agent baselines exceed ~45%";
  on sequential reasoning tasks every multi-agent variant degraded
  performance by 39–70%; independent agents amplify errors 17.2x, centralised
  coordination 4.4x.
- **Yang et al., "Does Multi-Agent Collaboration Pay When Agents Serve
  Users?", arXiv 2610.27655, 2026-10-20**
  ([arXiv](https://arxiv.org/abs/2610.27655)). Pre-registered, about 7,950
  τ²-bench conversations, six models. Quote: "Under a fixed simulated user no
  cell shows a MAS benefit … weighted mean of −28 points (95% CI [−44,−12])".
  Removing the budget cap "brings it at best level with the SAS at 5.5 times
  its median token spend." Conclusion: "For agents that serve people, these
  results make the SAS the default". This is directly relevant to Kiln's
  multi-turn tasks with synthetic users.
- **Deep agentic search study, arXiv 2608.01507, 2026-08-02**
  ([arXiv](https://arxiv.org/html/2608.01507)). Grep-search by sub-agent
  against a semantic index on SWE-QA: 46.2% against 65.2% correct, at more
  than twice the cost per correct answer. "41.8% [of failures] occurred at
  the hand-off between the planner and its sub-agent, and these were usually
  silent, ending in a fluent and confident answer that was wrong."
- **ECER 2026 benchmark** (six task families, 48 runs;
  [pdf](https://ecer.eraa.at/archive/ecer-2026/papers/When_Do_Multi-Agent_LLM_Systems_Outperform_Single-Agent_Approaches_An_Empirical_Comparison_Across_Different_Task_Types.pdf)).
  Single agent 100% pass, 8.91/10, 0.93 cents, 41.7 s. Fixed multi-agent
  pipeline 83.3% pass, 7.90/10, 1.9 cents, 83.7 s. Small study.
- **Gao et al., arXiv 2505.18286, May 2025.** Multi-agent systems used
  "4–220× more input (prefill) tokens" than the single agent across seven
  datasets ([arXiv](https://arxiv.org/pdf/2505.18286)).
- **Efficiency survey** (undated PDF,
  [link](https://lexiang-xiong.github.io/Awesome-Efficient-MAS/assets/Efficiency_LLM_MAS_Survey.pdf)):
  when all agents see the same complete context, "communication may
  reorganize or compress the context, but it cannot add evidence"; under
  matched thinking budgets strong single agents "generally match or
  outperform". Collaboration helps when the relay "preserves nearly
  sufficient evidence while reducing the context each participant must
  process."

Microsoft's Cloud Adoption Framework
([learn.microsoft.com](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai-agents/single-agent-multiple-agents))
adds non-quality reasons for separate agents: security or compliance
boundaries and separate owning teams. It says: "Many scalability issues stem
from retrieval design, not architecture."

## Implications for the tuner (inference)

1. Do not propose a sub-agent as a general quality fix. Propose it when a
   trace shows a high-volume, low-relevance sub-step (large reads, search
   results, manuals) whose useful output is short. This matches the
   overview's "cheaper reader" case and Anthropic's >1,000-token test.
2. Expect the gain to shrink as the main model gets stronger. Re-test a
   sub-agent design after a model change. This supports overview 6.4
   (model and harness are a pair).
3. For multi-turn, user-facing agents, the 2026 τ²-bench study says the
   single agent is the default. A planner/specialist split needs evidence
   at matched token budgets.
4. Measure the hand-off. The 41.8% hand-off failure share says the
   sub-agent's return contract (what it must return, in what form) is a
   lever in itself.
5. Verification sub-agents are the most defensible split (small context
   transfer), but they need explicit instructions against shallow checks.
6. Kiln's one-turn "task as tool" limitation (overview 7.2) is consistent
   with the evidence: most supported patterns (reader, verifier, advisor)
   are one-shot.

## Confirms, contradicts, misses (against the overview)

- **Confirms:** 7.2 "main value is a clean context", "not a universal fix",
  dependence on model capability, and "cheaper reader".
- **Nuance:** 7.2 says "a small model may need … sub-agents". The 45%
  threshold supports this for weak single agents, but sub-agent hand-offs
  also fail more often with weaker models. Evidence is mixed.
- **Misses:** the hand-off contract as a tuning target; matched-token
  comparison as the fair test; role-based splitting as an anti-pattern; the
  advisor pattern (see [routing-and-advisor.md](./routing-and-advisor.md)),
  which inverts the orchestrator/worker split.
