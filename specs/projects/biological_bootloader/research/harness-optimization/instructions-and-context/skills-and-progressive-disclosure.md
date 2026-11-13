# Skills and progressive disclosure

Research date: 2026-11-10. **Inference** marks this author's reasoning.

## 1. What a skill is, mechanically

Anthropic introduced Agent Skills on 2025-10-16
([Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
The format is now an open specification at
[agentskills.io](https://agentskills.io/specification).

Three levels of disclosure (spec, "Progressive disclosure"):

1. "Metadata (~100 tokens): The `name` and `description` fields are loaded at
   startup for all skills"
2. "Instructions (< 5000 tokens recommended): The full `SKILL.md` body is
   loaded when the skill is activated"
3. "Resources (as needed): Files (e.g. those in `scripts/`, `references/`, or
   `assets/`) are loaded only when required"

"Keep your main `SKILL.md` under 500 lines." Frontmatter limits: `name` up to
64 characters, lowercase, digits and hyphens, matching the folder name;
`description` 1–1024 characters, "Should describe both what the skill does
and when to use it". Optional: `license`, `compatibility` (≤500 chars),
`metadata`, `allowed-tools` (experimental).

Code in a skill: "Claude can run this script without loading either the
script or the PDF into context. And because code is deterministic, this
workflow is consistent and repeatable"
([Anthropic, 2025-10-16](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
Whether Kiln skills can hold code files belongs to the "Kiln capability
gap" subtopic.

### 1.1 What happens in the context window

From the Claude Code skills reference
([code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills)):

- "When you or Claude invoke a skill, the rendered `SKILL.md` content enters
  the conversation as a single message and stays there across later turns."
  "every line is a recurring token cost."
- The listing of names and descriptions "adds to your context on every turn,
  whether or not Claude ever uses it". Its budget "scales at 1% of the
  model's context window"; on overflow, Claude Code drops descriptions of the
  least-used skills. Each entry's description plus `when_to_use` "is
  truncated at 1,536 characters".
- After compaction, Claude Code re-attaches each invoked skill, "keeping the
  first 5,000 tokens of each", inside "a combined budget of 25,000 tokens".

**Inference:** the skill body lands in the *message* history, after the
user's message. That position decides its caching behaviour (see the
caching document).

## 2. Evidence that skills help

### 2.1 SkillsBench (2026)

SkillsBench is the first paired benchmark (no skills, curated skills,
self-generated skills) ([arXiv 2602.12670](https://arxiv.org/html/2602.12670),
latest version dated 2026-06; [blog, 2026-02-10](https://www.skillsbench.ai/blogs/introducing-skillsbench)).

Latest aggregate, 87 tasks, 18 model–harness configurations:

- "Curated Skills raise the average pass rate from 33.9% to 50.5% (+16.6
  percentage points)". Range "+4.1 to +25.7 pp". Every configuration
  improved.
- "Focused Skills with at most three modules outperform larger or
  exhaustive bundles, and smaller models with Skills can match larger
  models without them."
- "Skill efficacy is therefore an empirical property of a specific agent
  stack rather than a universal constant."
- Self-generated skills (authored first with Anthropic's skill-creator)
  landed **below** the no-skill baseline: −8.1 pp (Claude Code + Opus 4.7),
  −11.3 pp (Codex + GPT-5.5), −11.5 pp (Gemini CLI + Gemini 3.1 Pro). The
  audit cites "generated packs the solver never discovers, creator-side
  authoring that displaces solver work, and confidently wrong pack content".

From version 1 (84 tasks, 7 configurations) on size and count
([v1 HTML](https://arxiv.org/html/2602.12670v1);
[blog](https://www.skillsbench.ai/blogs/introducing-skillsbench)):

| Skill complexity | Δ pass rate |
| --- | --- |
| Detailed | +18.8 pp |
| Compact | +17.1 pp |
| Standard | +10.1 pp |
| Comprehensive | −2.9 pp |

| Skills per task | Δ pass rate |
| --- | --- |
| 1 | +17.8 pp |
| 2–3 | +18.6 pp |
| 4+ | +5.9 pp |

- v1 also: "16 of 84 tasks show negative deltas"; gains by domain ran from
  +4.5 pp (software engineering) to +51.9 pp (healthcare).
- v1 model-size example: Claude Haiku 4.5 with skills (27.7%) beat Opus 4.5
  without (22.0%)
  ([The Register, 2026-02-19](https://www.theregister.com/software/2026/02/19/ai-agents-cant-teach-themselves-new-tricks-people-can/4977233)).

Caveats (this author): the skills are mounted for the task, so "4+ skills"
may also mean "harder task". The skills are written for the task, so the
benchmark measures good content, not discovery among many skills.

### 2.2 Model size in SkillsBench

From the latest table: the weakest configuration, OpenHands + Gemini 3.1
Flash Lite, gained only +4.1 pp (16.0% → 20.1%); GPT-5.4 Mini +11.5 pp;
DeepSeek V4 Flash +17.2 pp; OpenHands + Claude Opus 4.8 only +8.4 pp; GLM
5.1 +25.7 pp ([arXiv 2602.12670](https://arxiv.org/html/2602.12670)).

**Inference:** gain is not monotonic in model size. Strong models gain less
when they already know the procedure. Very weak models gain little because
they cannot use the content. Mid-size models gain most. This partly
contradicts a simple reading of the overview's "a less capable model needs
progressive disclosure": it needs *focused* content, but it may not be able
to *navigate* to it.

## 3. Evidence that skills fail: triggering

- Vercel: the skill "was never invoked" in 56% of cases; pass rate equal to
  no docs (53%). With an instruction to use it, trigger rate "95%+" and pass
  rate 79%. The wording mattered: "You MUST invoke the skill" made the agent
  read docs first and miss project context; "Explore project first, then
  invoke skill" did better
  ([Vercel, 2026-01-27](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)).
- Vercel also saw the unused skill do slightly worse than baseline on tests
  (58% vs 63%), "suggesting that an unused skill in the environment may
  introduce noise or distraction".
- SkillsBench self-generated packs that "the solver never discovers" (above).
- Anthropic logs skill invocations with a `PreToolUse` hook to find skills
  that are "undertriggering compared to our expectations"
  ([Shihipar, 2026-06-03](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/)).
- Claude Code ships `/skill-doctor` to show each skill's context cost and
  use frequency ([skills docs](https://code.claude.com/docs/en/skills)), and
  the skill-creator plugin has "Description tuning: generates should-trigger
  and should-not-trigger prompts, measures the hit rate"
  (same page).

**Inference:** a skill has two failure points that a prompt does not have:
the decision to open it, and the path through its files. Trigger rate is a
measurable property. A red-flag check can compute it from traces (skill
available, relevant, not read).

## 4. When a long prompt beats a skill tree, and the reverse

No source runs the exact experiment the overview describes (cached long
prompt against a 15-call skill walk). The evidence that bears on it:

| Evidence | Points to |
| --- | --- |
| Vercel: always-loaded 8 KB index beat skill (100% vs 79%) | Prompt, for knowledge needed on most tasks |
| Vercel: skills better "for vertical, action-specific workflows that users explicitly trigger" | Skill, for rare, named procedures |
| SkillsBench: comprehensive skill −2.9 pp; 4+ skills +5.9 pp | Neither: keep any single load focused |
| Gloaguen et al.: context files +20% cost, little gain | Prompt only for non-inferable facts |
| IFScale: small models fail early as instruction count grows | Skill or routing, for small models |
| Claude Code docs: CLAUDE.md for facts needed "in every session"; procedures and part-of-codebase rules to skills or path-scoped rules ([memory docs](https://code.claude.com/docs/en/memory)) | Frequency of need decides |
| Caching mechanics (caching document) | Prompt, when the same prefix is re-used across many runs |

**Inference — a working decision rule** (for the skill, as dated guidance):

1. Content needed on most runs → system prompt (cached), or an index in the
   prompt that points to files.
2. Content needed on a minority of runs and nameable by a trigger → skill.
3. Content needed on a minority of runs, where the model fails to trigger →
   either move a short index into the prompt (Vercel pattern) or route in
   code before the call.
4. Many skills (4+ per task) or a skill that is "comprehensive" → split or
   trim before anything else.
5. A skill of a few dozen words → into the prompt (the overview's own red
   flag; consistent with the ~100-token metadata cost per skill above).

The **index-in-prompt** pattern is the middle option the overview does not
name: it keeps the always-loaded cost small (8 KB in Vercel's case,
compressed from 40 KB with no loss), removes the trigger decision, and still
loads detail on demand.

## 5. Structure rules for the tree itself

From [Anthropic skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices):

- "Keep references one level deep from `SKILL.md`." Nested references lead
  Claude to "use commands such as `head -100` to preview content rather than
  reading entire files, resulting in incomplete information."
- "For reference files longer than 100 lines, include a table of contents at
  the top."
- Organize by domain so "Claude only needs to read sales-related schemas,
  not finance or marketing data."
- Watch navigation: "Overreliance on certain sections: If Claude repeatedly
  reads the same file, consider whether that content should be in the main
  `SKILL.md` instead"; "Ignored content: If Claude never accesses a bundled
  file, it might be unnecessary or poorly signaled".

**Contradiction with the overview:** the overview says a skill "can have
several levels" (section 6.2) and sketches skill → document → leaf per tool
(section 6.3 sidebar). Anthropic's guide advises one level of references
below `SKILL.md`. **Inference:** the depth is safe only where each level is
a full read of a short file. The skill should treat depth as a parameter to
test, not a default.

## 6. Relevance to Harness Tuner

- Confirmed: moving a long prompt into a skill is a valid experiment, and so
  is the reverse (overview 6.2).
- Add: measure trigger rate before judging a skill's content. A skill that
  is not read scores as "no skill".
- Add: the index-in-prompt pattern as a third option between prompt and
  skill.
- Add: skill count and skill size limits as red flags (4+ skills per task,
  "comprehensive" skills, `SKILL.md` over 500 lines or 5,000 tokens,
  references more than one level deep).
- Add: do not let a loop agent write the target's skills from its own
  knowledge alone. SkillsBench shows self-authored skills below baseline.
  Skills must come from traces, train-set failures and human sources.
  **Inference:** this does not stop the tuner from writing skills; it says
  the content must carry information the model lacks, and an eval must
  confirm it.
