# System prompts and context loading

Research date: 2026-11-10. Every claim has a link. Text marked
**Inference** is this author's reasoning, not a source statement.

## 1. The governing principle: smallest set of high-signal tokens

Anthropic's context-engineering post (2025-09-29) is still the most cited
primary statement. Its rule:

> "good context engineering means finding the smallest possible set of
> high-signal tokens that maximize the likelihood of some desired outcome."
> — [Anthropic, Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

It qualifies "minimal" at once:

> "Note that minimal does not necessarily mean short; you still need to give
> the agent sufficient information up front to ensure it adheres to the
> desired behavior."

The reason it gives is **context rot**: recall falls as the context grows,
on all models, as "a performance gradient rather than a hard cliff". The
Chroma technical report (2025-07-14) measured this on 18 models (GPT-4.1,
Claude 4, Gemini 2.5, Qwen3): "performance grows increasingly unreliable as
input length grows", even on simple tasks
([Chroma, Context Rot](https://www.trychroma.com/research/context-rot)).

## 2. System prompt design for agents

### 2.1 Altitude

Anthropic names two failure modes
([source](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)):

- **Too low.** "hardcoding complex, brittle logic in their prompts to elicit
  exact agentic behavior". It is fragile and costly to maintain.
- **Too high.** "vague, high-level guidance that fails to give the LLM
  concrete signals ... or falsely assumes shared context."

The target is "specific enough to guide behavior effectively, yet flexible
enough to provide the model with strong heuristics".

The Claude Code team repeats this for skills: "Avoid railroading Claude ...
State the goal and the constraints, not every step"
([Shihipar, How we use skills, 2026-06-03](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/)).
The skill best-practice page gives the same idea as "degrees of freedom":
high freedom (heuristics) where many approaches are valid, low freedom
(exact script) where the operation is fragile
([Anthropic, Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)).

### 2.2 Start minimal, add from failures

> "It's best to start by testing a minimal prompt with the best model
> available to see how it performs on your task, and then add clear
> instructions and examples to improve performance based on failure modes
> found during initial testing."
> — [Anthropic, context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

**Inference:** this is the same loop Harness Tuner runs. It conflicts with
nothing in the overview, but it states the opposite default to the owner's
"give it a very large amount of context first" view (overview section 3).
Both are defensible; they are two starting points for an experiment, and
the overview already says experiments decide.

### 2.3 Examples, not edge-case lists

> "teams will often stuff a laundry list of edge cases into a prompt ... We
> do not recommend this. Instead, we recommend working to curate a set of
> diverse, canonical examples"
> — [Anthropic, context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

The current Claude prompting guide says: "Include 3–5 examples for best
results", wrapped in `<example>` tags, "Relevant", "Diverse", "Structured"
([Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

The counter-case from Manus (2025-07-18): in agents with repetitive actions,
similar past action/observation pairs in context make the model copy the
pattern ("Don't get few-shotted"). Their fix is small, structured variation
in serialization ([Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).

### 2.4 Explain the reason

The Claude guide: "Providing context or motivation behind your instructions
... Claude can generalize from the explanation." Its example replaces
"NEVER use ellipses" with the reason (a text-to-speech engine)
([source](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

### 2.5 Structure

Anthropic recommends sections with XML tags or Markdown headers, "although
the exact formatting of prompts is likely becoming less important as models
become more capable"
([context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

For long inputs (20k+ tokens) the Claude guide says: put long documents at
the top and the query at the end. "Queries at the end can improve response
quality by up to 30 percent in tests"
([source](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

### 2.6 Contradictions cost more on careful models

OpenAI's GPT-5 prompting guide:

> "poorly-constructed prompts containing contradictory or vague instructions
> can be more damaging to GPT-5 than to other models, as it expends
> reasoning tokens searching for a way to reconcile the contradictions
> rather than picking one instruction at random."
> — [OpenAI, GPT-5 prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide)

Anthropic's Claude Code memory docs say the same for instruction files: "if
two instructions contradict each other, Claude may pick one arbitrarily"
([Claude Code memory docs](https://code.claude.com/docs/en/memory)).

**Inference:** a contradiction is a defect with a cost on both kinds of
model: random choice on one, wasted reasoning on the other. This is direct
support for the overview's "silent conflict" rule (section 2, item 4).

### 2.7 Prompts carry model-specific debt

Current Anthropic model pages list instructions that were right for an
earlier model and now hurt. From the Claude Fable 5.2 page
([Prompting Claude Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)):

- Standing instructions to delegate to sub-agents start more sub-agents than
  needed.
- A token budget written into the prompt changes spend; "removing a large
  budget lowered cost, with equivalent performance".
- "Instructions to think less" no longer work at `medium` effort and above;
  lower the effort setting instead.
- Hard style rules ("one instruction per sentence, in 20 words or fewer")
  make prose choppy.
- Cautions pile up in notes, memory and instruction files (see the
  maintenance document).

The general guide says, for migration to 4.6+ models: "If your prompts
previously encouraged the model to be more thorough or use tools more
aggressively, reduce that guidance ... might overtrigger"
([source](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

**Inference:** a prompt tuned on model A is a hypothesis on model B. This
supports the overview's "model and harness are a pair" (section 6.4). It
also implies that step 3 of the overview's order of work (return to model
selection) should re-test prompt lines that were added for the old model.

## 3. How this changes with model size

### 3.1 Instruction density

IFScale (Distyl AI, 2025-07-15) gave 20 models 10 to 500 simultaneous
keyword instructions ([paper](https://arxiv.org/html/2507.11538v1)):

- Best accuracy at 500 instructions was 68%.
- Three curves, correlated with size and reasoning:
  - **threshold decay** (o3, gemini-2.5-pro): near perfect "through 150 or
    more instructions", then decline;
  - **linear decay** (gpt-4.1, claude-3.7-sonnet);
  - **exponential decay** (claude-3.5-haiku, llama-4-scout): "steep
    performance drops after minimal instruction densities", floor at 7-15%.
- "larger models outperform smaller counterparts"; reasoning models hold to
  "100-250 instructions".
- A primacy effect: earlier instructions are followed more.

Caveat: the instructions are keyword inclusions, not agent rules. The models
are a 2025 generation. The shape (small models fail early) is the useful
part, not the numbers.

### 3.2 Vendor guidance by size

- Anthropic's skill guide: test with every model you will use. "Claude
  Haiku ... Does the Skill provide enough guidance? ... Claude Opus ... Does
  the Skill avoid over-explaining? What works perfectly for Opus might need
  more detail for Haiku"
  ([best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)).
- Claude Haiku 5.5: "With a short system prompt, Claude Haiku 5.5 rarely
  stops before the work is done. With a long coding-agent system prompt at
  `low` effort, it sometimes stops early". Moving from `low` to `medium`
  effort "roughly halved early stopping. It also more than doubled the
  output tokens"
  ([Prompting Claude Haiku 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5)).
- Claude Haiku 5.5 also needs an explicit line to keep a chatbot to its
  system prompt under user pressure (same page).

**Inference:** two separate small-model weaknesses show up. (1) Long prompts
degrade them sooner (IFScale, Haiku early stopping). (2) They are also less
able to decide to fetch context (see the skills document: SkillsBench's
weakest model gains least). Agent-driven progressive disclosure needs (2).
So for small models the strongest design may be **code-side routing**:
the harness selects which instructions to include before the call, and the
model does not have to decide. The overview does not name this option.

## 4. Up-front context against just-in-time retrieval

### 4.1 The sources agree on a hybrid

Anthropic describes "just in time" context: the agent keeps "lightweight
identifiers (file paths, stored queries, web links, etc.)" and loads data
with tools. It then states the trade-off:

> "runtime exploration is slower than retrieving pre-computed data ...
> Without proper guidance, an agent can waste context by misusing tools,
> chasing dead-ends, or failing to identify key information."

> "the most effective agents might employ a hybrid strategy, retrieving some
> data up front for speed, and pursuing further autonomous exploration at
> its discretion ... The hybrid strategy might be better suited for contexts
> with less dynamic content, such as legal or finance work."
> — [Anthropic, context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### 4.2 Pre-fetch when the call is likely

HumanLayer's 12-factor agents, "Factor 13 — pre-fetch all the context you
might need":

> "If there's a high chance that your model will call tool X, don't waste
> token round trips telling the model to fetch it"
> — [12-factor-agents, appendix 13](https://github.com/humanlayer/12-factor-agents/blob/main/content/appendix-13-pre-fetch.md)

Its example puts `{{ git_tags }}` in the prompt in place of an instruction
to call `list_git_tags`.

### 4.3 Measured: passive context beat on-demand retrieval

Vercel (2026-01-27) tested Next.js 16 API knowledge for coding agents
([Vercel](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)):

| Configuration | Pass rate |
| --- | --- |
| No docs | 53% |
| Skill, default | 53% |
| Skill, with an instruction to use it | 79% |
| 8 KB docs index in `AGENTS.md` | 100% |

- "In 56% of eval cases, the skill was never invoked."
- The index pointed to files; the agent still read files on demand. So the
  winner was "always-loaded **map** + on-demand **content**", not "load
  everything".
- Their three reasons: "No decision point", "Consistent availability", "No
  ordering issues".

### 4.4 Measured: generic up-front context can hurt

Gloaguen et al., ETH Zurich and LogicStar (arXiv 2602.11988, v3 2026-09)
([paper](https://arxiv.org/abs/2602.11988)):

> "providing context files does not generally improve task success rates,
> while increasing inference cost by over 20% on average ... instructions in
> the context files are well followed by coding agents, repository
> overviews, although popular and recommended by model providers, are not
> helpful."

Figures reported from v1: LLM-generated files about −2% success and +20%
cost; developer-written files about +4% success and +19% cost
([summary of the paper](https://www.linkedin.com/posts/tobyohwhite_evaluating-agentsmd-are-repository-level-activity-7429438620828078083-XClC)).
The authors' advice: include only "instructions required for coding agents
that are not already present in the README (e.g., specific conventions or
non-functional requirements)".

### 4.5 What reconciles 4.3 and 4.4

**Inference.** Up-front context wins when it carries information the model
does not have and needs on most runs (Vercel: post-training APIs; the
overview's "this account cancels in three days"). It loses when it repeats
what the agent can find or already knows (repository overviews). The
deciding test is "would a failing trace have passed if the agent had known
this?", which is exactly the test the overview proposes in section 7.5.

## 5. Manus practices that bear on the instruction layer

From [Manus, 2025-07-18](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus):

- "Manipulate attention through recitation": a `todo.md` rewritten each step
  puts the plan at the end of context and counters lost-in-the-middle.
- "Keep the wrong stuff in": failed actions in context shift the model away
  from repeating them.
- "Use the file system as context", with restorable compression (drop a page
  but keep its URL).

Compaction and memory are owned by "Architecture levers"; these lines are
here only because they change what the prompt should say.

## 6. Relevance to Harness Tuner

- The prompt-change hypotheses the overview lists (section 6.1) are
  confirmed. Add three named, testable moves: **remove** model-specific debt
  after a model change (2.7); **resolve contradictions** (2.6); **add the
  reason** to bare rules (2.4).
- Context loading (overview 7.5) is confirmed by Anthropic, HumanLayer and
  Vercel. Add the qualifier from 4.4: up-front context that the agent does
  not need on most runs costs about 20% more and may not help.
- For small models, add **code-side routing** as an alternative to
  agent-driven progressive disclosure (3.2).
