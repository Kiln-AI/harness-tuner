# Skill authoring and maintenance

Research date: 2026-11-10. This part feeds the overview's maintenance skill
(section 2, item 4): the skill that coding agents in this repository use to
add material to the knowledge skill without breaking it. **Inference**
marks this author's reasoning.

## Part A. Published rules for writing skills

### A.1 Content

| Rule | Source |
| --- | --- |
| "Only add context Claude doesn't already have." Ask "Does this paragraph justify its token cost?" | [Anthropic best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) |
| "Don't state the obvious ... focus on information that pushes Claude out of its normal way of thinking." | [Shihipar, 2026-06-03](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/) |
| "The highest-signal content in any skill is the Gotchas section ... built up from common failure points" | same |
| "Avoid railroading Claude ... State the goal and the constraints, not every step." | same |
| Match freedom to fragility: heuristics where many paths work; exact scripts where "Operations are fragile" | [Anthropic best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) |
| "Avoid offering too many options ... Provide a default (with escape hatch)" | same |
| "Use consistent terminology. Choose one term and use it throughout" | same |
| "Avoid time-sensitive information"; keep superseded methods in an "Old patterns" section, marked "deprecated 2025-08" | same |
| Explain why, not only what ("Claude can generalize from the explanation") | [Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) |
| Compact or detailed skills beat comprehensive ones (+17 to +19 pp against −2.9 pp) | [SkillsBench v1](https://arxiv.org/html/2602.12670v1) |
| Working examples and scripts give the largest gain | [SkillsBench blog](https://www.skillsbench.ai/blogs/introducing-skillsbench) |

### A.2 Description (the trigger)

- "the description field is not a summary, it's a description of when to
  trigger this skill"; include trigger words users say
  ([Shihipar](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/)).
- "Always write in third person. The description is injected into the
  system prompt"; include "what the Skill does and when to use it"
  ([Anthropic best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)).
- Put the key use case first; Claude Code truncates each entry at 1,536
  characters and drops descriptions when the listing passes 1% of context
  ([skills docs](https://code.claude.com/docs/en/skills)).

### A.3 Structure

- `SKILL.md` under 500 lines / under ~5,000 tokens
  ([agentskills.io spec](https://agentskills.io/specification)).
- References one level deep from `SKILL.md`; table of contents in any file
  over 100 lines ([Anthropic best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)).
- Organize by domain so one task reads one branch (same).
- Code: "Solve, don't defer" (handle errors in scripts), and no "voodoo
  constants" (same). Say whether a script is to be run or read.
- One category per skill: "The best skills fit cleanly into one; the ones
  that try to do too much straddle several and confuse the agent"
  ([Shihipar](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/)).

### A.4 Process

- "Build evaluations first ... Create evaluations BEFORE writing extensive
  documentation." Baseline without the skill, then "Write minimal
  instructions" ([Anthropic best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)).
- The "Claude A / Claude B" loop: one instance edits, a fresh instance uses,
  observations go back (same).
- Watch navigation: unexpected order, missed links, over-read files (move
  into `SKILL.md`), never-read files (remove or signal better) (same).
- Test on each model that will use it (same).
- Measure: skill-creator benchmarks with-skill against without-skill,
  "blind A/B between two versions", and description hit rate
  ([skills docs](https://code.claude.com/docs/en/skills)).
- Self-generated skills scored below no skill in SkillsBench (−8 to −12 pp)
  ([arXiv 2602.12670](https://arxiv.org/html/2602.12670)).
  **Inference:** an agent that adds to a skill from its own knowledge,
  without a source or an eval, is the measured failure case.

## Part B. Rules for agent instruction files (AGENTS.md, CLAUDE.md)

- "Keep it to facts Claude should hold in every session ... If an entry is a
  multi-step procedure or only matters for one part of the codebase, move it
  to a skill or a path-scoped rule"
  ([Claude Code memory docs](https://code.claude.com/docs/en/memory)).
- "target under 200 lines per CLAUDE.md file. Longer files consume more
  context and reduce adherence." Imports "don't reduce its context cost"
  (same).
- Write instructions "concrete enough to verify" (same).
- When to add: "Claude makes the same mistake a second time" or a review
  catches what Claude "should have known" (same).
- If it must happen every time, use a hook, not prose (same).
- Codex: combined instruction files are capped by `project_doc_max_bytes`,
  32 KiB by default; files past the cap are not loaded
  ([openai/codex issue 42601](https://github.com/openai/codex/issues/42601);
  [Builders Camp guide](https://builderscamp.com/guides/tools/how-to-write-agents-md-for-codex)).
- Only non-inferable content helps: repository overviews did not help; files
  added ~20% cost ([Gloaguen et al.](https://arxiv.org/abs/2602.11988)).

## Part C. Keeping a growing knowledge base consistent

### C.1 What goes wrong, measured

1. **Monotonic growth.** Chakrabarti (arXiv 2608.11095, 2026-08-11) traced
   247,694 instruction lifetimes in 1,867 repositories. Agent instruction
   files grow "+226%" over their life, "+4.9 net instructions every
   commit", and "the older an instruction gets, the less likely it is to be
   deleted". The stated cause: "once an instruction's rationale is gone,
   deleting it without risking a correctness regression costs O(2^|D|)"
   ([paper](https://arxiv.org/abs/2608.11095)). The author names it
   "catastrophic remembering".
2. **Rewrites do not fix it.** Per a summary of the same paper, "a refactored
   file resumes growing at a faster per-commit rate than it had before the
   rewrite" ([Datapace, 2026-08-20](https://datapace.ai/blog/claude-md-catastrophic-remembering)).
   Not checked in the paper text.
3. **Divergent copies.** Shanto (arXiv 2611.02383, 2026-11-07): of 386
   matched section pairs across coexisting formats, "16.1% provide divergent
   guidance"; "40.4% of the analyzed files have only one recorded
   modification event"; median one author per file
   ([paper](https://arxiv.org/abs/2611.02383)).
4. **Context collapse under full rewrites.** ACE (Zhang et al., 2025) reports
   that an LLM that rewrites a whole context condenses it; one case fell
   from 18,282 tokens (66.7% accuracy) to 122 tokens (57.1%) in one step.
   Their fix is "Incremental delta updates instead of monolithic rewrites"
   with de-duplication ([secondary summary](https://www.sundeepteki.org/blog/agentic-context-engineering);
   [repository](https://github.com/ace-agent/ace)). ACE is an optimisation
   system and out of scope here; only this editing finding is used.
5. **Caution pile-up.** Anthropic, on Claude Fable 5.2: "Cautious
   instructions build up wherever context carries over: memory, notes,
   instruction files". One stale "never retry" note among a dozen cut
   retries from nearly all to about four in ten. "Write the exception into
   the rule": a rule that itself said to get a go-ahead let the model act in
   about 9 of 10 tries, against about 1 in 10 with a separate exception line
   ([Prompting Claude Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)).
6. **Contradiction.** "Claude may pick one arbitrarily"
   ([memory docs](https://code.claude.com/docs/en/memory)); GPT-5 "expends
   reasoning tokens" reconciling it
   ([OpenAI](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide)).

### C.2 What works, with evidence

1. **Keep the rationale next to the rule.** In Chakrabarti's controlled
   setting (inverted IFEval), informative comments cut excess instructions
   "from +211.3% to +1.4%" and improved real-world instruction following "by
   up to 23.1%" ([arXiv 2608.11095](https://arxiv.org/abs/2608.11095)).
   Anthropic's "add context to improve performance" points the same way.
2. **Pair every addition with a compaction pass** ("compact-on-add"): the
   same change removes a stale rule, merges an overlap, or moves a rule to a
   hook. Adaptive changes "replace, do not append"; "Preventive" rules get
   the "Highest scrutiny" ([agentpatterns.ai](https://agentpatterns.ai/instructions/agent-context-file-evolution/),
   a practitioner synthesis citing Chatlatanagulchai et al. 2025 and Voria
   et al. 2026; the taxonomy is a registered report, not yet validated).
3. **Deprecate in place, with a date.** Anthropic's "Old patterns" section
   with "deprecated 2025-08" ([best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)).
   The same idea as the ADR "superseded" status: each decision record keeps
   its rationale and the log keeps history ([adr.github.io](https://adr.github.io/)).
4. **Scope absolute rules.** "rewrite absolute rules so that they state
   their scope and reason"; "delete the cautions whose reason no longer
   holds" ([Fable 5.2 guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)).
5. **Audit for conflict and staleness.** Claude Code's `/doctor
   prompt-audit` looks for "instructions written for older models,
   references to files or commands that don't exist, and files that
   contradict each other" and proposes edits without applying them
   ([memory docs](https://code.claude.com/docs/en/memory)).
6. **Measure usage.** A `PreToolUse` hook logs skill reads to find unused or
   undertriggering skills ([Shihipar](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/));
   `/skill-doctor` reports cost and use ([skills docs](https://code.claude.com/docs/en/skills)).
7. **Version and pin.** The Claude Skills API lets a request pin a skill
   `version_id` or float to latest ([AI Insiders](https://aiinsiders.net/article/anthropic-ships-computer-use-browser-tool-skills-api-to-ga),
   secondary; not checked in the API reference).
8. **One source of truth.** Most repositories with both files make
   `CLAUDE.md` point to `AGENTS.md` (1,540 of 2,388)
   ([Zarzycki, arXiv 2610.31715](https://arxiv.org/html/2610.31715)). This
   repository already does that.

### C.3 Placement of new material

No source gives a placement algorithm for a layered knowledge skill. The
published heuristics are:

- by **frequency of need**: every session → top level; one area or
  procedure → skill or path-scoped file
  ([memory docs](https://code.claude.com/docs/en/memory));
- by **domain**, mutually exclusive branches
  ([best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices));
- by **observed reads**: often-read leaf → promote; never-read → demote or
  delete (same).

**Inference — a proposed placement rule for the maintenance skill:**

1. New material is a leaf by default. It goes to the top level only if it
   changes the order of experiments for most agents, and the change says so.
2. Every addition states: source and date; which degrees of freedom it
   applies to (overview section 4); which existing entries it touches.
3. Before writing, search the skill for entries on the same lever. If one
   conflicts, the change must do one of: deprecate the old entry (move to
   "Old patterns" with a date and a reason), or write a "when to use each"
   condition into both. Silent coexistence fails review.
4. No deletion of an older approach without a stated reason (newer evidence,
   with a link). This counters over-indexing on the new request.
5. One addition, one compaction check: does the addition make another entry
   redundant?
6. Size budgets as checks code can run: `SKILL.md` lines and tokens,
   reference depth, file length without a table of contents.
7. Rationale stays with the rule, in the same file.

## Part D. Gaps

- No study measures the overview's specific failure (an editing agent
  promotes new material to the top level). The nearest evidence is growth
  and collapse (C.1).
- No published "conflict register" practice for agent knowledge bases was
  found beyond audits and ADR-style supersession.
