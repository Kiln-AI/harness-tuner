# Instructions and context

Research date: 2026-11-10. Sources are from July 2025 to November 2026.

## Bottom Line

The instruction layer has one rule that all current sources share: give the model the smallest set of tokens that it does not already have and that it needs on most runs. Content needed on most runs belongs in the system prompt, where it is cached across runs; a short always-loaded index that points to files often beats a skill, because skills fail when the agent does not open them (Vercel: 56% of cases never opened, 79% against 100% for an index). Curated, focused skills still help on average (+16.6 pp across 18 stacks in SkillsBench), but comprehensive skills, many skills per task, and skills an agent writes from its own knowledge do not. Model size matters in two ways: small models degrade sooner as instructions grow, and they are also weaker at deciding to fetch context, so code-side routing may beat agent-driven disclosure for them. For maintenance, the measured failures are monotonic growth, collapse under full rewrites, silent contradictions and piled-up cautions; the measured fix is to keep the rationale next to each rule, plus compact-on-add, dated deprecation and conflict audits. One Kiln finding needs follow-up: Kiln appears to enable Anthropic prompt caching only for multi-turn synthetic-user drives, so the "long cached prompt" lever may not exist for single-turn Claude targets today.

## Key Findings

- **Smallest high-signal context; minimal is not short** — Anthropic's governing rule, with the right "altitude" between brittle logic and vague guidance. [Anthropic, 2025-09-29](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- **Always-loaded index beat a skill** — 8 KB docs index in `AGENTS.md`: 100%; skill with explicit instruction: 79%; skill alone: 53% (= no docs). Reason: no decision point. [Vercel, 2026-01-27](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)
- **Generic context files add ~20% cost without success gains** — instructions are followed, but repository overviews do not help; include only non-inferable content. [Gloaguen et al., arXiv 2602.11988](https://arxiv.org/abs/2602.11988)
- **Curated skills help, with limits** — +16.6 pp average; focused skills (≤3 modules) beat exhaustive bundles; "comprehensive" skills −2.9 pp; 4+ skills per task +5.9 pp; small models with skills can match large ones without. [SkillsBench, arXiv 2602.12670](https://arxiv.org/html/2602.12670)
- **Self-written skills are worse than none** — −8.1 to −11.5 pp below the no-skill baseline on three frontier stacks. [SkillsBench](https://arxiv.org/html/2602.12670)
- **Small models fail early under instruction load** — exponential decay for small models, threshold near 150+ instructions for reasoning models. [IFScale, 2025-07](https://arxiv.org/html/2507.11538v1); Haiku 5.5 stops early with long agent prompts at low effort. [Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5)
- **Caching is a prefix match; a skill read is never shared across runs** — tools → system → messages; a skill body sits after the per-run user message. Reads cost 0.1× (0.05× or 0.025× on newest Claude models); minimum cacheable length 512–4,096 tokens by model; 5-minute default lifetime. [Claude caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [OpenAI](https://platform.openai.com/docs/guides/prompt-caching)
- **Harness rules for cache hit rate** — static first, no per-run values in the system prompt, no tool or model change mid-run, monitor hit rate. [Claude Code team, 2026-04-30](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/); [Manus, 2025-07-18](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- **Prompts carry model-specific debt** — lines written for older models (delegation pushes, token budgets, "think less", stacked cautions) raise cost or stop working on new ones. [Anthropic, Fable 5.2 guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)
- **Contradictions cost on every model** — random choice or wasted reasoning. [OpenAI GPT-5 guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide); [Claude Code memory docs](https://code.claude.com/docs/en/memory)
- **Authoring rules** — description is a trigger, third person, key use case first; `SKILL.md` <500 lines; references one level deep; gotchas section; evals before content. [Anthropic best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices); [Shihipar, 2026-06-03](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/)
- **Instruction files only grow; rationale stops it** — +226% over life in 1,867 repos; inline rationale cut excess growth from +211% to +1.4% and raised instruction following up to 23.1%. [Chakrabarti, arXiv 2608.11095](https://arxiv.org/abs/2608.11095)
- **Kiln caching (inference from code)** — `automatic_prompt_caching` defaults to `False` and is set only in synthetic-user drives, with the breakpoint on the last message. See [caching §5](./prompt-caching-mechanics.md).

## Details

- [System prompts and context loading](./system-prompts-and-context-loading.md) — prompt design rules, model-size effects, up-front against just-in-time context, prompt debt after a model change.
- [Skills and progressive disclosure](./skills-and-progressive-disclosure.md) — skill mechanics, SkillsBench and Vercel evidence, trigger failure, a dated decision rule for prompt against skill, tree-depth rules.
- [Prompt caching mechanics](./prompt-caching-mechanics.md) — provider facts (Anthropic, OpenAI, Gemini), harness rules, a worked cost illustration of cached prompt against skill walk, the Kiln caching note.
- [Skill authoring and maintenance](./skill-authoring-and-maintenance.md) — published authoring rules, instruction-file rules, measured failure modes of growing knowledge bases, fixes, and a proposed placement rule for the maintenance skill.
- [Baseline check](./baseline-check.md) — what confirms, qualifies and is missing from the project overview.

## Open Questions / Gaps

- No source runs the overview's exact experiment (cached long prompt against a multi-call skill walk). The cost comparison in the caching document is an illustration with assumed numbers, not a measurement.
- No study measures skill trigger rate by model size directly. The small-model claims combine IFScale, Anthropic model guides and the SkillsBench table.
- The Kiln caching behaviour is read from code, not from a live run. Whether LiteLLM also marks the system prompt was not checked. Handed to the "Kiln capability gap" subtopic.
- No published placement algorithm for a layered knowledge skill, and no study of the "editing agent promotes new material to the top" failure. The placement rule in the maintenance document is a proposal.
- Some figures come through secondary summaries: SkillsBench v1 counts (blog and v1 HTML), Gloaguen v1 percentages, ACE's 18,282 → 122 token example, Skills API version pinning.
- arXiv could not be fetched with curl in this environment; arXiv pages were read through Exa and Tavily.

## Sources

- [Anthropic, Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — governing principles, hybrid loading; 2025-09-29.
- [Anthropic, Equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) — skill model and levels; 2025-10-16.
- [Anthropic, Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — authoring rules; read 2026-11-10.
- [Agent Skills specification](https://agentskills.io/specification) — open format, token and line guidance; read 2026-11-10.
- [Claude Code skills docs](https://code.claude.com/docs/en/skills) and [memory docs](https://code.claude.com/docs/en/memory) — listing budget, lifecycle, CLAUDE.md rules, prompt audit; read 2026-11-10.
- [Shihipar, How we use skills](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/) — practice at Anthropic; 2026-06-03.
- [Shihipar, Prompt caching is everything](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/) — harness caching rules; 2026-04-30.
- [Claude prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — mechanics, prices, minimums, invalidation; read 2026-11-10.
- [OpenAI prompt caching](https://platform.openai.com/docs/guides/prompt-caching) — mechanics and prices; read 2026-11-10.
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching) — implicit caching minimums; read 2026-11-10.
- [Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), [Haiku 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5), [Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2) — prompt rules and model-specific debt; read 2026-11-10.
- [OpenAI, GPT-5 prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide) — contradictions; 2025-08.
- [Vercel, AGENTS.md outperforms skills](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals) — prompt index against skill; 2026-01-27.
- [Gloaguen et al., Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988) — context-file cost and effect; v3 2026-09.
- [SkillsBench](https://arxiv.org/html/2602.12670) ([v1](https://arxiv.org/html/2602.12670v1), [blog](https://www.skillsbench.ai/blogs/introducing-skillsbench)) — skill efficacy; 2026-02 to 2026-06.
- [IFScale](https://arxiv.org/html/2507.11538v1) — instruction density by model size; 2025-07-15.
- [Chroma, Context Rot](https://www.trychroma.com/research/context-rot) — long-context degradation; 2025-07-14.
- [Manus, Context engineering lessons](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) — KV-cache-first design; 2025-07-18.
- [HumanLayer, 12-factor agents, Factor 13](https://github.com/humanlayer/12-factor-agents/blob/main/content/appendix-13-pre-fetch.md) — pre-fetch context.
- [Chakrabarti, Catastrophic Remembering](https://arxiv.org/abs/2608.11095) — instruction-file growth and rationale comments; 2026-08-11.
- [Shanto, Evidence-based template for agent instruction files](https://arxiv.org/abs/2611.02383) — divergence and maintenance; 2026-11-07.
- [Zarzycki, Census of AGENTS.md and CLAUDE.md](https://arxiv.org/html/2610.31715) — adoption and bridging; 2026-10-26.
- [agentpatterns.ai, Agent context file evolution](https://agentpatterns.ai/instructions/agent-context-file-evolution/) — compact-on-add practice (practitioner synthesis).
- [adr.github.io](https://adr.github.io/) — decision records and supersession.
- Kiln repository `/home/user/kiln-ai/kiln`, commit `3e67b49` — `base_adapter.py`, `litellm_adapter.py`, `synthetic_user/eval_drive.py`, `synthetic_user/runner.py`.
