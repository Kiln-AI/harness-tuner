# Static checks on a harness configuration

Research date: 2026-11-10. These checks read the run config, the prompt, the
tool definitions and the skills. They need no trace. They are tier 1 in the
overview's section 9.

## 1. Published numbers

Every number below is a vendor product limit or a spec recommendation. None
is a measured optimum for a recurring-task agent. Use them as "worth an
experiment" flags, not as rules (overview section 3).

| Check | Number | Source (date read or published) |
|---|---|---|
| Skill `name` length | max 64 chars, lowercase, digits, hyphens | [Anthropic skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) (read 2026-11-10); [Agent Skills spec](https://agentskills.io/specification) |
| Skill `description` length | max 1,024 chars, non-empty | same |
| Skill listing entry in Claude Code | `description` + `when_to_use` cut at 1,536 chars | [Claude Code skills](https://code.claude.com/docs/en/skills) (read 2026-11-10) |
| Skill listing total in Claude Code | budget "scales at 1% of the model's context window"; descriptions of least-used skills dropped first | same |
| Skill metadata cost | "~100 tokens" per skill, loaded at start for all skills | [Agent Skills spec](https://agentskills.io/specification) |
| `SKILL.md` body | "under 500 lines"; spec: "< 5000 tokens recommended" | Anthropic best practices; Agent Skills spec |
| Reference depth | "Keep references one level deep from `SKILL.md`" | both |
| Long reference file | "longer than 100 lines, include a table of contents" | Anthropic best practices |
| Tool description (loaded up front) in Claude Code | cut at 4,096 chars | [Claude Code MCP](https://code.claude.com/docs/en/mcp) (read 2026-11-10) |
| Tool description (loaded by tool search) | cut at 16,384 chars | same |
| MCP server instructions | cut at 4,096 chars | same |
| Tool count before selection degrades | "degrades once you exceed 30–50 available tools" | [Anthropic tool search docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) (read 2026-11-10) |
| When to use tool search | "10 or more tools" or "definitions consume more than 10k tokens" | same |
| When not to | "fewer than 10 tools, every tool is used in every request, or … less than 100 tokens total" | same |
| Claude Code auto tool search | on when tool definitions reach 10% of context window | Claude Code MCP docs |
| Tool definitions cost (example) | "58 tools consuming approximately 55K tokens"; "134K tokens before optimization" | Anthropic advanced tool use post, 2025-11-24, as quoted by [eSEOspace](https://eseospace.com/blog/mcp-context-window-and-too-many-tools) (secondary; I did not read the original) |
| Instruction count | best of 20 models "only achieve 68% accuracy at the max density of 500 instructions"; "bias towards earlier instructions" | [IFScale, arXiv 2507.11538](https://arxiv.org/html/2507.11538v1) (2025-07) |
| Minimum cacheable prompt | 512 to 4,096 tokens by model | [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) (read 2026-11-10) |

I found **no published threshold for system prompt length** for agents. The
overview's "100,000 characters" example (section 9) is not contradicted by
any source, but it is also not supported by a number. IFScale is about
instruction count, not length. Chroma's context-rot work is about total
input length, and gives no prompt-specific figure.

## 2. Tool-description smells

Two 2026 studies give a code-and-LLM split for tool descriptions.

**"MCP Tool Descriptions Are Smelly!"**
([arXiv 2602.14878 v3](https://arxiv.org/html/2602.14878v3), 2026-02):
856 tools across 103 MCP servers. Six components: Purpose, Guidelines,
Limitations, Parameter Explanation, Length and Completeness, Examples. Smells
come from a rubric scored by an "LLM-as-Jury". Results: "97.1% of the
analyzed tool descriptions contain at least one smell, with 56% failing to
state their purpose clearly", and "89.3% lack Usage Guidance". Fixing all
components "improves task success rates by a median of 5.85 percentage
points … but it also increases the number of execution steps by 67.46% and
regresses performance in 16.67% of cases." One smaller model
(Qwen3-Next-80B-A3B) reduced its steps.

**"From Docs to Descriptions"**
([arXiv 2602.18914](https://arxiv.org/html/2602.18914v1), 2026-02): 10,831
MCP servers, 18 smell categories in 4 dimensions (accuracy, functionality,
completeness, conciseness). "73% suffer from repeated tool names, 3,449
exhibit wrong parameter meanings, and 3,093 lack" return descriptions.
Mutation study: smells "significantly affect LLM tool selection, with
functionality and accuracy having the largest effects (+11.6% and +8.8%,
p < 0.001)".

What this means for a length check (inference): a tool description can be
too long (overview's 27,000-character example; Claude Code cuts at 4,096)
**and** too short (most real descriptions lack purpose and usage guidance).
The flag must be two-sided. A longer description also has a cost in steps
and context, so "make it longer" is an experiment, not a fix.

## 3. Checks that are plain code

- Prompt: characters, tokens, count of imperative lines or bullets (a crude
  instruction count), share of the context window, presence of a timestamp
  or other per-request value near the start (breaks caching, per Manus),
  duplicate paragraphs.
- Tools: count; total definition tokens; per-tool description length (flag
  both very short and very long); parameters with no description;
  parameters with no type or enum where one is obvious; missing return
  description; duplicate or near-duplicate names (string distance);
  namespacing consistency (Anthropic: "namespacing tools by service … and by
  resource" helps, [tools post](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- Skills: count; `SKILL.md` with no sub-files and very few words (the
  overview's 27-word case); description length against 1,024; body length
  against 500 lines; reference depth above one; reference files over 100
  lines with no table of contents; total listing size; many skills with
  overlapping names (the overview's "thousands of skills" case).
- Model settings: settings that the model card or provider marks as
  unsupported (the Model-selection subtopic owns this list).
- Output mode: structured output requested but no schema; schema present but
  prompt also asks for JSON in text (possible conflict). This is inference;
  I found no source that measures it.

Validators exist: the Agent Skills spec points to `skills-ref validate`,
which "checks that your `SKILL.md` frontmatter is valid and follows all
naming conventions" ([spec](https://agentskills.io/specification)). It
checks format, not quality.

## 4. Checks that need an LLM

- Does a tool description state its purpose, when to use it, and its limits
  (the six-component rubric above uses an LLM jury).
- Do two tools overlap so that "a human engineer can't definitively say which
  tool should be used"
  ([Anthropic, context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
- Is the prompt at "the right altitude" (neither brittle hard-coded logic nor
  vague guidance), same source.
- Does the prompt ask for what the eval wants (overview 6.1: "the eval wants
  X and the prompt never asks for X"). This needs the eval's rubric and the
  prompt side by side. Inference: a cheap first pass is code (keyword overlap
  between the eval rubric and the prompt), then an LLM only on low overlap.
  Harness Tuner already has a judge-copy matcher that compares baseline text
  with eval text (`docs/architecture.md` section 3.1 step 11), so the
  inverse check must respect that boundary: the tuner must not copy judge
  text into the prompt.
