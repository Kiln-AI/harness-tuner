# Baseline check against the project overview

Overview: `specs/projects/biological_bootloader/project_overview.md`.
Research date: 2026-11-10. Links go to the deep documents in this folder,
which carry the primary sources.

## Confirms

| Overview statement | Evidence | Where |
| --- | --- | --- |
| 6.1 The prompt often fails to ask for what the eval wants | Anthropic: start minimal, add instructions from observed failures; Gloaguen: instructions in context files "are well followed" | [prompts](./system-prompts-and-context-loading.md) §2.2, §4.4 |
| 6.2 Moving a long prompt into a skill is worth an experiment | SkillsBench: curated skills +16.6 pp on average across 18 stacks | [skills](./skills-and-progressive-disclosure.md) §2.1 |
| 6.2 The reverse is worth testing too | Vercel: an 8 KB always-loaded index scored 100% against 79% for a skill | [skills](./skills-and-progressive-disclosure.md) §3, §4 |
| 6.2 A large system prompt caches well; skill reads do not | Prefix caching: system prompt shared across runs; a skill body sits after the per-run user message | [caching](./prompt-caching-mechanics.md) §4.1 |
| 6.2 / 6.4 Model size changes the best harness | IFScale degradation curves by size; Anthropic: "What works perfectly for Opus might need more detail for Haiku"; Haiku 5.5 stops early with long prompts at low effort | [prompts](./system-prompts-and-context-loading.md) §3 |
| 7.5 Load context up front instead of waiting for a tool call | Anthropic hybrid strategy; HumanLayer Factor 13; Vercel "no decision point" | [prompts](./system-prompts-and-context-loading.md) §4 |
| 9 A 27-word skill should go in the prompt | Each skill costs ~100 tokens of metadata on every turn; Claude Code drops descriptions past a 1% budget | [skills](./skills-and-progressive-disclosure.md) §1.1 |
| 9 Thousands of skills: group them | SkillsBench 4+ skills per task: +5.9 pp against +18.6 pp for 2–3 | [skills](./skills-and-progressive-disclosure.md) §2.1 |
| 2.4 Silent conflict is a defect | Contradictions: random choice (Claude Code docs) or wasted reasoning (OpenAI GPT-5 guide) | [maintenance](./skill-authoring-and-maintenance.md) §C.1 |
| 2.4 Agents over-index on new material and delete old | Files only grow (+226%); full rewrites collapse detail (ACE) | [maintenance](./skill-authoring-and-maintenance.md) §C.1 |
| 2 Sidebar: treat every statement as dated guidance | Anthropic's own advice: deprecated content in an "Old patterns" section with a date; per-model prompt pages list instructions that stopped working | [maintenance](./skill-authoring-and-maintenance.md) §C.2 |

## Contradicts or qualifies

1. **"Moving a very long prompt into a skill ... is usually the right
   direction."** Qualified. Skills fail when the agent does not open them
   (56% never invoked in Vercel's test). Skill content helps only when it is
   focused; "comprehensive" skills scored −2.9 pp. Gloaguen found generic
   up-front context also fails. The deciding variable is how often runs need
   the content and whether the model triggers it, not prompt length alone.
2. **"A less capable model needs progressive disclosure."** Qualified.
   Small models need *focused* context (IFScale, Haiku). But the weakest
   model in SkillsBench gained least (+4.1 pp). **Inference:** a weak model
   may not navigate a skill tree; routing in code can be better.
3. **"A skill ... can have several levels"** and the skill → document →
   leaf-per-tool sketch. Anthropic advises references one level deep below
   `SKILL.md`, because nested files get partial reads (`head -100`).
4. **Why a skill file misses the cache.** The overview's reason ("rarely
   read in the exact order") is secondary. The main reason is position:
   skill content is after the per-run user message, so no other run can
   share it.
5. **"Give it a very large amount of context first"** (owner's view) and
   Anthropic's "start by testing a minimal prompt". Not a contradiction of
   the overview's golden rule; both are starting points. Noted because the
   skill's order of experiments must pick one default and say why.
6. **Long cached prompt as a lever in Kiln today.** **Inference from Kiln
   code** (commit `3e67b49`): Kiln enables Anthropic `cache_control` only
   for multi-turn synthetic-user drives, and then on the last message. A
   single-turn Claude task may get no caching at all. Needs confirmation by
   the Kiln-gap subtopic.

## Missing from the overview

1. **Skill trigger rate as a first measurement.** A skill that is not read
   scores as no skill. Measure reads in traces before judging content.
2. **The index-in-prompt pattern.** An always-loaded, compressed index that
   points to files (Vercel: 40 KB → 8 KB with no loss). A third option
   between prompt and skill.
3. **Code-side routing** (the harness picks the instructions before the
   call) as an alternative to agent-driven disclosure, mainly for small
   models.
4. **Prompt debt after a model change.** Current model guides list prompt
   lines that hurt on newer models (delegation pushes, token budgets in the
   prompt, "think less" lines, stacked cautions). Step 3 of the order of
   work should re-test them.
5. **Cache hygiene as harness design.** Static content first; no per-run
   values in the system prompt; no tool changes mid-run; per-model minimum
   cacheable lengths (512–4,096 tokens); 5-minute lifetime; cache hit rate
   as a metric. Eval traffic is bursty and may overstate production hit rate.
6. **Self-authored skill content is a measured failure** (−8 to −12 pp in
   SkillsBench). Skill content written by the tuner must come from traces,
   train data or human sources, and an eval must confirm it.
7. **Provenance for the maintenance skill.** Rationale next to each rule cut
   regrowth from +211% to +1.4% in a controlled test. Compact-on-add,
   dated deprecation, scope-and-reason on absolute rules, and an audit for
   conflicts and stale references.
8. **Redundant up-front context costs about 20%.** Context the agent can find
   itself (repository overviews) added cost without success gains.
