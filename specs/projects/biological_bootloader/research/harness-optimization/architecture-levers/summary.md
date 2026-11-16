# Architecture levers

Research date: 2026-11-10. Baseline: `project_overview.md` sections 7.1–7.6 and 7.9.

## Bottom Line

The evidence supports the overview's direction on every lever. It also shows that the overview names one primitive where there are several. Use a single agent by default. Sub-agents help when a sub-step produces a lot of low-relevance context. They hurt once the single agent is strong (about 45% success). A pre-registered 2026 study found no benefit for multi-agent designs in user-facing conversations. For long contexts, clearing old tool results is the cheapest primitive, and it has the largest published gain. Compaction is lossy, and its summaries cause measurable failures. For retrieval, the useful measure is tokens, not documents. Below about 200k tokens, put the documents in a cached prompt. One level of progressive disclosure is enough. Reranking is the most reliable RAG setting. Decision models are real and very cheap, but less accurate than frontier LLMs on vendor evals, and they depend on input transforms. Two highly rated levers are missing from the overview: the **advisor pattern** and **confidence cascades**.

## Key Findings

- **Sub-agents: use them for context isolation, not for roles.** Anthropic (2026-01) says: use them when sub-steps produce more than 1,000 tokens that are mostly irrelevant, and "Group work by what context it requires, not by what kind of work it is". Multi-agent designs cost 3–15x the tokens. Kim et al. (Dec 2025) found negative returns above about 45% single-agent success, and −39% to −70% on sequential tasks. On τ²-bench (2026-10), multi-agent designs lost by a mean of −28 points. [details](./sub-agents-and-multi-agent.md)
- **The sub-agent hand-off is a failure point.** In one study, 41.8% of failures in deep agentic search were silent hand-off errors (arXiv 2608.01507). The return contract is a lever that can be tuned. [details](./sub-agents-and-multi-agent.md)
- **Context editing is separate from compaction, and cheaper.** In Anthropic's internal eval, `clear_tool_uses` gave +29%, and +39% with the memory tool, with 84% fewer tokens over 100 turns. Each clear breaks the prompt cache. `clear_at_least` controls how often that happens. [details](./context-management-compaction.md)
- **Compaction has costs.** It causes replayed actions and instability (arXiv 2608.06503). 83% of failed runs traced to summary entries with no source in the environment (Oct 2026). Server compaction has a trigger (default 150k, minimum 50k), custom `instructions`, and an option to keep recent turns verbatim. All of these can be tuned. [details](./context-management-compaction.md)
- **RAG, skill or the whole corpus in the prompt.** Below about 200k tokens, put the corpus in the prompt (Anthropic). A controlled study (arXiv 2607.17598, Jul 2026) found that skills help only when the harness navigates badly, or when there are many books. A second level of disclosure "never helps and sometimes breaks accuracy". Grep or semantic search: the winner changes with the query type and the harness. Cursor reports +12.5% with both together. [details](./rag-search-skills.md)
- **The RAG parameters in order.** In a 2026 factorial study, reranking was positive in all 240 cells. Chunking and BM25 fusion changed sign with the dataset and the retriever. Small eval sets give the wrong answer (a 10-item result reversed at 100 items). Kiln has a reranker config (`RagConfig.reranker_config_id`, `RerankerConfig`), and the overview does not mention it. [details](./rag-search-skills.md)
- **Decision models.** The overview's "Luna Decisions" is very probably the OpenAI Decisions API on GPT-6 Luna. "Kev" is an open clone of the Jev interface. Clef (Cloudflare, Apache 2.0) is compatible with the Jev API. Jev costs $0.042 per million input tokens and answers in about 70–500 ms. Its agreement with frontier labels is 67.8%, against 74.1% for GPT-5.6 Sol, on vendor evals. Splitting one decision into five sub-questions plus logistic regression reached 95.0%, against 62.6% for the direct question. [details](./decision-models.md)
- **Input transforms.** No direct study of field removal was found. The indirect evidence is consistent: distractors lower accuracy, and longer reasoning can make that worse. Decision-model vendors require trimmed input and dates and numbers computed in code. Kiln has `JinjaInputTransform` on the run config. The overview does not mention dynamic few-shot retrieval. [details](./input-transforms.md)
- **The advisor pattern (missing from the overview).** A cheap executor runs the task and calls a stronger model as a server tool. Sonnet with an Opus advisor scored +2.7 points on SWE-bench Multilingual at −11.9% cost. Haiku with an Opus advisor scored 41.2% on BrowseComp, against 19.7% for Haiku alone. Shipped April 2026, beta, Anthropic API only. OpenRouter has a version for other providers. [details](./routing-and-advisor.md)
- **Switching model during a conversation breaks the prompt cache.** Each model has its own cache. There is a disputed report that effort changes also break it on some models. Confidence cascades (cheap model first, escalate when unsure) have 2025–2026 research behind them. More thinking can lower accuracy (inverse scaling). [details](./routing-and-advisor.md)
- **Fine-tuning comes late, and works best for closed-label classification.** In one study, a fine-tuned 7B model reached 88% against 31% for prompted Claude, at 14x lower cost, mainly because it dropped the long instructions (secondary source). SFT copies the training data, and RL generalises. Cloudflare now offers RL fine-tuning of the Clef decision model. [details](./fine-tuning.md)

### Against the overview

- **Confirms:** single agent first (7.2). Sub-agents work for clean context and cheap reading. Gains depend on the model. Test whether RAG helps before tuning it (7.1). Do extraction once. Decision models fit only classification (7.4). Input transforms are mostly for classification (7.3). Fine-tune late (7.6). A model switch on the main thread is costly (7.9).
- **Contradicts in part:** 7.1 implies that a well-designed progressive-disclosure hierarchy beats chunks. The only controlled study found deep hierarchies harmful and one level enough. 7.4 says "capable"; vendor evals show a quality gap to frontier LLMs.
- **Missing:** the advisor pattern; confidence cascades and thresholds; tool-result clearing and memory as levers separate from compaction; compaction-summary failures; reranking, top-k and contextual retrieval; tokens as the size measure; the return contract of a sub-agent; dynamic few-shot; compatibility of the Jev API across vendors and open-weight decision models (relevant to restrictions in section 8); RL fine-tuning of decision models; eval-set size for RAG decisions.

## Details

- [sub-agents-and-multi-agent.md](./sub-agents-and-multi-agent.md): when sub-agents help or hurt, with the controlled studies, the 45% threshold, the τ²-bench result and the hand-off failures. Read it before you propose a sub-agent.
- [context-management-compaction.md](./context-management-compaction.md): context rot, context-editing parameters, compaction parameters, and the 2026 evidence on compaction failures.
- [rag-search-skills.md](./rag-search-skills.md): size thresholds, skill against raw navigation against RAG, grep against vector search, and a ranked list of RAG parameters with conflicting evidence.
- [decision-models.md](./decision-models.md): the decision-model products, the interface, measured numbers, the vendor's documented weak points, and when to use one.
- [input-transforms.md](./input-transforms.md): the Kiln mechanism, the evidence, and a catalogue of transforms with their risks.
- [routing-and-advisor.md](./routing-and-advisor.md): routing, cascades, the cache cost of a switch, and the advisor pattern.
- [fine-tuning.md](./fine-tuning.md): when fine-tuning pays off after harness work.

## Open Questions / Gaps

- **Network limits.** arxiv.org, benchlm.ai and cognition.ai did not resolve through WebFetch. The Exa fetch reached arXiv and benchlm.ai. Cognition's posts are known only from secondary summaries. Tavily was rate-limited for the whole run, and Exa search failed partway through (invalid key). Some paper results (Kim et al. 2512.08296, the compaction preprints, the Highlighter.ai study, the 128k crossover benchmark) come from search summaries only. They are marked as secondary in the deep docs.
- **No document-count threshold exists** for skill against RAG. Only token thresholds (about 128k to 200k) were found.
- **No controlled study of field removal for LLM classifiers** was found.
- **Decision-model accuracy** comes almost entirely from vendors. The category is eight weeks old.
- **The effect of thinking level on the prompt cache** is disputed (the docs and a bug report disagree).
- **Kiln reachability** was not checked: context editing pass-through, advisor server tool, decision-model adapters, dynamic few-shot. These belong to the "Kiln capability gap" subtopic. Confirmed in code only: `JinjaInputTransform` and `RerankerConfig`/`reranker_config_id` exist.

## Sources

- [When to use multi-agent systems](https://claude.com/resources/articles/building-multi-agent-systems-when-and-how-to-use-them): Anthropic guidance on sub-agents, 2026-01-23
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system): Anthropic, 90.2% gain and 15x tokens, 2025-06-13
- [Multi-Agents: What's Actually Working](https://cognition.com/blog/multi-agents-working): Cognition, Apr 2026 (via summaries)
- [Towards a Science of Scaling Agent Systems](https://arxiv.org/abs/2512.08296v1): Kim et al., 45% threshold, Dec 2025
- [Does Multi-Agent Collaboration Pay When Agents Serve Users?](https://arxiv.org/abs/2610.27655): pre-registered τ²-bench study, 2026-10-20
- [Deep Agentic Search for Repository-Level Code QA](https://arxiv.org/html/2608.01507): hand-off failures, 2026-08-02
- [Single-agent or Multi-agent Systems? Why Not Both?](https://arxiv.org/pdf/2505.18286): token overhead, 2025-05
- [Microsoft CAF: single vs multiple agents](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai-agents/single-agent-multiple-agents): undated vendor guidance
- [Context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing): parameters and defaults, current
- [Compaction at a token threshold](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold) and [Compaction overview](https://platform.claude.com/docs/en/build-with-claude/compaction): current
- [Managing context on the Claude Developer Platform](https://www.anthropic.com/news/context-management): +29% and +39%, −84% tokens, 2025-09-29
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents): Anthropic, 2025-09-29
- [Context Rot](https://trychroma.com/research/context-rot): Chroma, July 2025
- [Toward Reliable Context Compression for Long-Horizon Agents](https://arxiv.org/html/2608.06503v1): 2026-08-06
- [Paper ties 83% of agent failures to compaction summaries](https://aiweekly.co/alerts/paper-ties-83-of-agent-failures-to-compaction-summaries): secondary report, 2026-10-19
- [Introducing Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval): 200k threshold and reranking gains, 2024-09-19
- [Is Progressive Disclosure All You Need for Long-Context Agents?](https://arxiv.org/pdf/2607.17598): controlled skill study, Jul 2026
- [Improving agent with semantic search](https://cursor.com/blog/semsearch): Cursor, 2025-11-06
- [Is Grep All You Need?](https://arxiv.org/html/2605.15184v1): grep against vector across harnesses, 2026-05
- [Does RAG Actually Help AI Coding Tools?](https://www.mikeayles.com/blog/rag-coding-tools/): 2026-04-07
- [RAG Components Are Not Independently Rankable](https://aclanthology.org/2026.docinsights-1.22.pdf): factorial RAG study, 2026
- [Hybrid search and reranking, measured](https://sbeconis.com/writing/hybrid-search-and-reranking-measured/): counter-evidence, 2026-10-07
- [Benchmarking 18 RAG configurations](https://www.alibabacloud.com/blog/how-accurate-is-your-rag-benchmarking-18-configurations_603461): chunk size and top-k, 2026-08-17
- [Jev cannot write](https://benchlm.ai/blog/posts/what-is-jev): Jev interface, limits and numbers, 2026-09-22
- [Jev in the Wild](https://arxiv.org/html/2609.30216v1): ecosystem study, 2026-09-24
- [Introducing Clef](https://blog.cloudflare.com/clef-decision-models/): Cloudflare, benchmarks and RL fine-tuning, 2026-10-01
- [Decision Models](https://metavert.io/decision-models): category overview, Oct 2026
- [AI decision models you can run locally](https://pasqualepillitteri.it/en/news/20652/ai-decision-models-run-locally): list of products and clones, 2026-10-04
- [Jev AI Tested](https://www.mindstudio.ai/blog/jev-system-one-model-classification): hands-on test, 2026-09-18
- [The advisor strategy](https://claude.com/resources/articles/the-advisor-strategy): Anthropic, 2026-04-09
- [Advisor tool in Claude Code](https://code.claude.com/docs/en/advisor): mechanics and model pairings, current
- [Inverse Scaling in Test-Time Compute](https://alignment.anthropic.com/2025/inverse-scaling): TMLR 2025
- [Large Language Models Can Be Easily Distracted by Irrelevant Context](https://arxiv.org/pdf/2302.00093): ICML 2023, foundational
- [RouteLLM](https://lmsys.org/blog/2024-07-01-routellm/): routing, 2024
- [C3PO cascades](https://arxiv.org/html/2511.07396v1): NeurIPS 2025
- [Is Fine-Tuning Better Than Prompt Engineering in 2026?](https://llm-stats.com/blog/research/fine-tuning-vs-prompt-engineering-2026): secondary summary of studies, 2026-04-30
- [LoRA Land](https://arxiv.org/html/2405.00732v1): 2024, vendor
- [Is Fine-Tuning Worth It?](https://www.distillabs.ai/learn/is-fine-tuning-worth-it/): vendor guide, 2025-03-09
- Kiln code: `/home/user/kiln-ai/kiln/libs/core/kiln_ai/datamodel/input_transform.py`, `rag.py`, `reranker.py` (main branch, read 2026-11-10)
