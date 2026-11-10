---
status: draft
---

# Biological Bootloader

**Source:** a one-hour voice recording by the project owner, organised by
topic. The recording was a stream of thought, so the order here is not the
order spoken, and later statements replace earlier ones. Sidebars from the
recording are block quotes. Transcription errors are corrected where the
meaning was clear (GEPA, Jinja, OpenEnv, TUI, Kiln). Model names are kept as
spoken and are examples only.

## 1. The problem

Kiln Harness Tuner has two parts:

- **The code loop.** It tries experiments on an agent's harness, measures
  evals, cost and latency, promotes the winners, and repeats. This part is
  well designed and works well. The TUI works well.
- **The hypotheses.** The agents in the loop decide which experiments to run.
  This is the part that can always improve.

If the planner finds the right idea early, the run finishes quickly. If its
ideas are slow or bad, the run may never converge. Today the hypotheses are
acceptable. The tuner can optimise a simple agent. It cannot optimise a very
complex agent.

The repository already has good process guidance: plan, iterate, measure,
promote. It does not have a good sense of **what is likely to work and what
to try next**. The better that sense, the better Harness Tuner is.

## 2. What we build

This project starts "the grand tutorial for harness optimisation" for the
agent that does the optimisation. It has two halves, and two more pieces
that came up during and after the recording.

1. **The knowledge: a skill.** A progressive-disclosure skill (`SKILL.md`
   plus resource files) that the tuner's agents read while they look at the
   target agent's traces and train set. It must be broad and complete enough
   that the tuner can reason about the right *order* of experiments, so that
   convergence is fast. The core is a large body of knowledge about the
   changes that make agents better.
2. **The capabilities: the Kiln surface.** Harness Tuner edits prompts,
   models and hyperparameters easily. Kiln has more harness capabilities than
   Harness Tuner knows about or can reach (sub-agents, RAG configs, input
   transforms and more). Audit what Kiln can do against what the tuner's Kiln
   interface exposes. Add the missing APIs, possibly as separate phases. The
   tuner is itself a harness, so its own tools must be tuned the way we would
   tune a client's.
3. **Red-flag detection.** Cheap software analysis of the harness and of
   traces, run before and during planning, that hands the tuner the obvious
   problems (section 9).
4. **A system to keep the knowledge up to date.** The knowledge and guidance
   must be easy to maintain. In recursive fashion, this is probably a skill
   in this repository. It is not for the Harness Tuner loop agents. It is
   for the coding agents that build Harness Tuner. A skill is often built
   well, and then the next agent starts to break it. The "skill design"
   rules must be written down, so that a request such as "read this blog
   post and add this approach to our tuner" keeps the skill's quality,
   format and rules. It must prevent two common mistakes:
   - **Over-indexing on the new request.** The agent puts the new item at
     the top level, or deletes older approaches. The maintenance skill must
     direct each addition to the right level, so that quality is consistent
     over time.
   - **Silent conflict.** When a new approach conflicts with an existing
     approach X, that is an explicit decision: deprecate X, or describe when
     to use each.

The spec we are building towards adds that skill and adds those APIs.

> **Sidebar: a library of wins.** We do this work with real clients and find
> real examples. The skill is also a library of wins: "when you see pattern
> X, optimisation Y is a strong candidate". The more the tuner can read
> traces, read the skill and name the patterns that apply, the better. We
> start general and add concrete examples over time, from demo projects,
> research, client work and reading. **Never use client names.**

> **Sidebar: this is a living document.** All of this will change in two
> months. Skills are about six months old. Four months ago the best practice
> was "everything is a skill". Now models handle large system prompts better.
> A new approach will launch next week. Treat every statement in the skill as
> current guidance with a date, not as a permanent truth.

## 3. The golden rule: experiments win

Experiments always decide the result. The skill gives ideas for what to try
and in what order. If an experiment shows a better result, that result is
final. **Nothing in the skill is a rule of the form "you must do X".** It is
guidance about which experiments to try, in which order.

**The irony, stated plainly.** This project writes down how to tune
harnesses, when our principle is that tuning is done by iterative
experiment. This is the **biological bootloader**. No great harness tuner
exists yet, so the tuner cannot be told to optimise itself. That has been
tried and does not work. The alternative is a meta-optimiser that learns
from thousands of optimisation runs across dozens or hundreds of agents.
That is probably a million-dollar LLM job. A written guide is the practical
start.

**Two views on the team, both valid.**

- Mike (chief scientist) will bring analytical rigour: does adding this item
  make a measurable difference?
- The owner's view is the bootloader: if a frontier model could simply
  optimise agents, none of this would be needed. Give it a very large amount
  of context first and see how far that goes. Perhaps three quarters of it
  does not help, but we do not know which three quarters. More information is
  preferred to less. Trim and optimise afterwards. That is cheaper than
  building the same corpus by pure experiment.

## 4. Understand what you are optimising first

Before the tuner reads the guide for solutions, it must classify the agent.
These are **degrees of freedom** (a better name is welcome). Every topic in
the guide must say how it applies along each one.

- **Single-turn or multi-turn.** Single-turn is information in, information
  out: a classifier, an extractor. It can still call tools. One request that
  makes a thousand tool calls and returns one answer is still single-turn by
  Kiln's definition. Multi-turn is conversational: each question builds on
  the earlier work.
- **Structured output or free text.**
- **Others.** Search the Kiln task definition for more degrees of freedom.

## 5. Cost efficiency

Cost efficiency is built into the design and must stay there. The evals are
expensive: perhaps 100 items, each of which drives the agent, a synthetic
user and the judges. What the tuner does today:

1. **Targeted experiments on a graph.** An experiment that targets one eval
   runs that eval only. If it does not fix its target, the other evals are
   not run. Wins are merged back only once in a while. Many cheap experiments
   are fundamental to the design.
2. **Coding-agent subscription for the thinking.** Planning and building run
   on a Claude Code or Codex subscription, which is cheaper than per-token
   billing. API cost is paid only to run the target agent, the synthetic user
   and the evals.
3. **Early stopping.** A candidate that can no longer win stops being
   measured. (Shipped: the `futility` stop rule in `stop_rules.py`.)

The link to this project: **being right often is the largest cost saving.**
A planner with the right early hypotheses converges perhaps ten times
faster.

## 6. The levers the tuner knows today

### 6.1 System prompt

Prompt changes are very common. If the prompt does not describe the task
accurately, the eval fails. A frequent case, easy to see in the train set:
the eval wants X and the prompt never asks for X. Methods such as GEPA
optimise the prompt alone. We are holistic: the prompt is optimised with all
the other variables.

### 6.2 Skills, and prompt versus skill

Agent skills are a `SKILL.md` with resource files (and possibly code files;
check whether Kiln supports those). The idea is progressive disclosure. An
agent that can do 25 things gets a short list in the prompt and reads the
skill for thing 16 only when asked to do thing 16. A skill can spend many
more tokens on one topic than a single prompt could, and can have several
levels.

Prompts and skills are closely tied, because both are instructions. What to
know:

- **Moving a very long prompt into a progressive-disclosure skill is almost
  always worth an experiment**, and is usually the right direction (as of
  November 2026).
- **The reverse is also worth testing.** Prompt caching matters. On a capable
  model, a longer prompt that is always cached can be cheaper and faster
  than walking a skill tree over 15 tool calls to collect 10 fragments. A
  large top-level skill file does not get the same benefit, because it is
  rarely read in the exact order that hits the cache. A large system prompt
  hits the cache very well.
- **It depends on model size.** A more capable model can handle a large
  prompt. A less capable model needs progressive disclosure and a focused
  context window.

> **Sidebar: the best harness includes the model.** There is no single best
> harness for an agent. The best harness for Opus is not the best harness
> for Haiku. We have just run that test. See section 6.4.

### 6.3 Tools: often the largest win

Many enterprises give their agents tools that are really their software
APIs. A good API can be a very poor agent tool.

- A list API returns 100 items with every field, including a 2,000-word text
  field. The agent reads 100,000 tokens to find one ID to pass to delete.
- A chain must be called in order: A gives an ID for B, which gives an ID for
  C, then D. The LLM is invoked between each call. In software this is fine,
  because a small wrapper calls all four. For an agent it is slow and costly.
- A powerful general tool (SQL, GraphQL) can do anything, but the agent must
  first learn the whole schema, the gotchas and how dates are handled.
- Extra JSON returned to software costs nothing. Extra JSON returned to an
  agent costs tokens and speed, and floods the context.

**The Kiln approach: wrap, do not rewrite.** We believe in experiments, so
many tool designs must be tried to find the valuable ones. Asking an
enterprise team to rewrite its stack twelve ways would produce meetings and
no result in six months. So we treat the company's tools as fixed and write a
layer over them: **Kiln code tools used as wrappers**. A wrapper can:

- call four APIs and return one token-efficient answer;
- call one large API and return only what this agent needs;
- allow a broad SQL query, but raise an error when the result would be
  300,000 tokens, which forces a narrower query.

The result is a tool layer made for this agent and this harness. Some of it
is general good practice. Some is specific: "the agent often makes these
seven calls in a row, so one tool does it".

> **Sidebar: two styles of agent.** *General coding agents* (Claude Code,
> Codex) take a new abstract task each time. They are best with a very strong
> model and very general tools such as Bash, and they write their own
> software as they go. It is wasteful to use that style for a *recurring
> task*: extract this contract, analyse this finance report, answer this
> support request. A support bot gives the same 25 answers and runs the same
> analysis repeatedly. That software must be written once, from its traces
> and past work, and be ready. This second class often runs 200,000 times a
> day, so cost matters more. **This is why Harness Tuner exists.** The gains
> are compiled into the harness in advance, not found from first principles
> on every run. We can spend 24 hours of a top model (Fable, Astra) on the
> harness and then run it on a cheap model (DeepSeek-V4-1-Flash, Luna).
> The better harness gives quality, correctness, cost and latency together,
> not one at the expense of another. It also solves problems a frontier model
> cannot solve when the tools are too complicated or the instructions are
> underspecified.

> **Sidebar: the harness is a complete object.** A better tool is not an
> experiment by itself. The prompt must say when to call it, and there is
> probably a side skill that describes the tool set by progressive
> disclosure: "for this type of work, read this document", which links to a
> leaf document per tool with its inputs and outputs. Think about the whole
> harness when you redesign a part.

**Tool smells** (start a ranked list; there are more):

- A tool result that returns a very large number of tokens. Almost never
  right. Let software filter, or narrow the query.
- **Tool search.** The agent must imagine that a tool exists, decide to
  search, and pick the right terms. A side skill that lists the tools with
  good progressive disclosure is almost always better.

Tool redesign is large enough to be nearly its own project.

### 6.4 Model and settings

**Settings.** Temperature, top-p, structured-output mode (JSON mode, JSON
schema, or asking for JSON in the prompt), thinking level and others. Do a
full review of what Kiln supports and document it as part of this project.
Kiln tries to set good defaults, so most settings stay at default.
Exceptions exist: one DeepSeek model needed a lower temperature for reliable
tool calls and JSON. Model cards are sometimes a good source.

**Model.** Probably the largest single lever. A top model solves most things
given time. The questions are speed and cost, and hosting cost if it needs a
VM.

**A model guide.** The project needs a current guide: the best roughly 25
models to try, by situation. This knowledge changes weekly. Examples as of
the recording: Opus is nearly always best at coding, and GPT Sol sometimes
catches up and then falls behind again; DeepSeek-V4-1-Flash is excellent
value but reasons a lot, so it is usually not fast; Gemini models
are much better at PDFs and documents; never use an older version when a
newer one of the same line is out. The tuner needs access to benchmarks and
tools that give the latest state, and a maintained set of "reasonable models
to try in this situation".

**A provider guide.** The same idea for providers, and it changes as fast.
Examples: Groq and Cerebras offer extremely fast inference; providers X, Y
and Z offer zero data retention; Cloudflare is slow; OpenRouter has gotchas.

**Model and harness are a pair.** A very large model may do well with one
big prompt in one context. A small model may need a small prompt,
progressive disclosure and sub-agents. You cannot design the harness and
choose a model afterwards.

**The current order of work** (medium confidence on step 1, higher on the
rest):

1. **Pick the class and lock one model.** Choose the size, price and latency
   you want. Try a range of models in that class. Pick one with a reasonable
   propensity for the task. It does not need to be good, but a 0% pass rate
   gives no signal. About 20%, with room to reach 65%, is useful. Do not tune
   on a very large model and expect the result to transfer to a small one.
2. **Tune the harness with the model locked.** Prompt, skills, tools and the
   rest. Changing everything at once is an exponential search. The graph
   exploration covers the harness and leaves the model alone.
3. **Return to model selection.** With the best harness for this class, scan
   again for the most capable, cheapest and fastest model in the class.
4. **Optional: a small hyperparameter scan** as the last step. Usually not
   worth it, sometimes useful.

## 7. The levers the tuner does not know today

For each of these: make the tuner aware of it, say when to use it, rank it
against the other options, and check that the tuner can reach the Kiln APIs
it needs.

### 7.1 RAG, and RAG versus skill

Kiln has a strong RAG tool. You upload documents and create RAG configs:
chunking style, embedding model, and vector, full-text or hybrid search. It
is a very good search space for an optimiser.

- **The first question: RAG or skill?** RAG was applied to everything in
  2025 and is overused. If the documents fit in a skill, a top model that
  designs the progressive-disclosure hierarchy once will probably beat
  arbitrary chunks and vectors. Sixty documents: a RAG is not sensible. Five
  million: a skill is not possible. There is no known number between.
- **The reverse.** When a skill grows too large and too deep and the agent
  does not find its way, RAG retrieves faster.
- Ask both questions when you find either one in a project.
- **Prerequisites.** Documents must exist (ask the user to upload them).
  They must be extracted, which needs API keys and a reasonable extraction
  model (usually Gemini; the app has defaults).
- **Do extraction once.** Do not tune the extraction model. It is very
  expensive and many levels down. In an early round of a general
  optimisation it is nearly always a mistake. It makes sense only in a
  specific RAG optimisation.
- **Order.** First test whether a RAG helps at all. Then tune the chunker
  and the search type.

### 7.2 Sub-agents

Fully supported in Kiln. A sub-agent is another Kiln task, attached with a
**Kiln task as tool**. The main agent calls it like any tool.

- **Different levels of thinking.** A fast, cheap support bot hands deep
  work to an "analyst" agent and gets a report back. Raising the thinking
  level or the model on the main thread fills the context, and you cannot go
  back down afterwards.
- **A cheaper reader.** A cheap sub-agent reads 150,000 tokens of manual and
  returns four lines.
- **The main value is a clean context.** But sub-agents are not a universal
  fix. On frontier models there is a move towards keeping everything in
  context with compaction. This depends on model capability, which is one
  more reason to lock the model class first.
- **Limitation.** One turn only. A sub-agent cannot be called again with its
  state. There are no long-lived sub-agents.
- **A sub-agent can be tuned.** It is a Kiln task, so Harness Tuner can
  target it with its own evals. Usually we tune the top-level agent. For a
  critical or complex sub-agent: write its evals, tune it, then tune the
  top-level agent against the tuned sub-agent.

### 7.3 Input transforms (feature engineering)

Read the Kiln docs on input templates and feature engineering. Before the
user message reaches the model it goes through a Jinja template. A classifier
that receives a large JSON object with timestamps and UUIDs can be given only
the three fields it needs. Text can be truncated (for example to 600
characters) so that it cannot flood the context. Features can be added (sums,
counts). A harmful field can be removed, such as a "last decision" field that
the model over-weights, or user notes that hurt the result. Not often needed,
but valuable in specific cases, mostly classification.

### 7.4 Decision models (Jev)

Jev started the "decision model" category. There are now many (Luna
Decisions, Cloudflare Clef, Kev and others). They are very fast "system 1"
models. The input side is like an LLM. The output is a prediction, in
effect one token for the class. They are very cheap, very fast and capable
for the price. **Consider them for every classification
task**, possibly with input transforms. They do nothing for free-text
output. More uses are coming to the Kiln harness but are not shipped.

### 7.5 Context loading

An agent needs the right context to decide. The information is often
available if the agent thinks to call a tool, but it does not think to. A
support bot will not check for a pending cancellation before it answers. If
it is told "this account cancels in three days because the card was
declined", it answers correctly.

- Find it in the failures: "if it had known that, it would have done the
  right thing".
- List what the agent must be given, not left to request.
- Make it token-efficient: not 17 tools that each return 98% noise.
- The usual fix is a **tool roll-up plus a prompt change**: one tool (for
  example `get_user_data`) that calls the 17 APIs and returns a compact
  result, and an instruction to call it first.

It is a special case of tool design.

### 7.6 Fine-tuning

Fine-tuning is the same idea as harness tuning, pushed into the weights.
Kiln is very capable at it. The current view (which has changed in the last
year):

- Harness tuning gets there faster and is often more powerful. **A
  fine-tuned model with bad tools still makes all the calls and uses all the
  tokens.** Fix the harness first.
- Do not put knowledge into weights. Use prompts and skills for that.
- It is a **late optimisation**: after a very well tuned harness, to move the
  cost and latency frontier, usually to make a small model that is nearly
  good enough pass the quality bar. Also valid for very high volume and for
  on-device models.
- **For Harness Tuner now:** existing fine-tunes are candidates in model
  selection. The tuner does not recommend or start a fine-tune. It knows that
  Kiln can do it. The Kiln assistant can send the user to the UI.

### 7.7 Stateful agents: OpenEnv

An agent that makes stateful changes needs a stateful environment for evals.
Do not run it against production. Evals against a staging MCP server go
stale even for reads: "how many were there last week" has a different answer
next week. Kiln supports OpenEnv-style environments (not all of it is merged
to main), and the Seahaven project can build one. The tuner must sometimes
raise the flag: "these evals fail for an environment reason, because they
were written against staging three weeks ago".

### 7.8 Evals, synthetic data and synthetic users

- Kiln has an eval builder: describe the problem, get synthetic data and a
  train/val/test split. The tuner uses the splits strictly, and the test
  split is withheld from it completely.
- **If there are no evals, the tuner does not write them.** It sends the user
  to the Kiln eval builder and resumes afterwards.
- Multi-turn evals use synthetic users that drive the agent. The user asks
  an ambiguous question, and the agent reaches the right answer only if it
  asks the right follow-up questions. The tuner must know this.

### 7.9 Known about, not yet in Kiln

Record these so the tuner knows them and does not plan around them yet.

- **Compaction.** When it triggers and what the compaction prompt is. Coming
  soon.
- **Switching model or thinking level during a conversation**, for example
  when a hard question arrives. Being considered.
- **Inline guardrails.** Kiln is an "eval first, then optimise" product, not
  a live-guardrail product. Possible later.

## 8. Restrictions

Some restrictions are known only if the user states them. "Google models
only, by contract." "OpenAI only." "No Chinese models, for regulatory
reasons." "Zero data retention only." "OpenRouter is allowed, but only ZDR
models." A sensitive eval data set must not go to a provider the user does
not trust. Knowing this at the start saves, for example, 28 experiments on a
model that is not allowed.

- **Success criterion for this project:** the tuner never breaks a stated
  restriction.
- Collecting restrictions is mostly a UI problem and is **a separate
  project** ("ask the user the five best questions").
- Here: perhaps a skill file on how to follow restrictions.

## 9. Red-flag detection (a new top-level concept)

Feeding traces to an LLM to count the characters in a tool result is
wasteful. The tuner also does not think to ask "does any tool return 600,000
characters?". If it is told at the start, it does much better. This is tool
design and context loading applied to the tuner itself.

**Anything that can be code must be code, and must be in the code loop.**
Four kinds, in order of cost:

1. Code analysis of the harness.
2. Code analysis of traces in bulk.
3. LLM analysis of the harness.
4. LLM analysis of specific results (expensive and slow; use with care).

First examples:

- a tool result of hundreds of thousands of tokens or characters;
- a very long tool description (27,000 characters);
- a very long prompt (100,000 characters);
- a skill with only a `SKILL.md`, no sub-files and about 27 words: put it in
  the prompt, because the skill's description costs more than it saves;
- thousands of skills: group them (25 into one) behind a top-level
  `SKILL.md`.

Some red flags block everything else. If a 300,000-token tool result
sometimes destroys the context, there is no value in a small fix elsewhere
until that is solved.

## 10. The library and the stack rank

**The library.** The skill is a living record of lessons, mostly from real
cases. It includes domain know-how: possibly a hundred small documents of
tips. Example: the side skill that teaches an agent a company's SQL or
GraphQL.

- It needs a section on timestamps. They are always difficult.
- Three sample queries for the most common cases, high in the skill, do more
  than a full generated schema description.

An agent might think of these. The purpose is that the tuner has them
written down: "if you write a side skill for a complex query tool, read
these 25 tips first".

**The stack rank.** "If X is broken, do not optimise Y yet." With a
500,000-character prompt that floods the context, hyperparameter tuning
teaches nothing. Tuning in the wrong order means the early waves teach
nothing. The skill needs a good version of this order, and must say that
there is no universal truth and that the order will change. Where this lives
in the design is open.

## 11. Process

1. **Project overview** (this document).
2. **Research.**
   - *The field.* The owner has done much agent optimisation but is not the
     only expert. Read what others have written on harness engineering. Use
     it for ideas for the skill and to find holes: "the best technique is X
     and Kiln does not support X".
   - *The Kiln gap.* What can Kiln tune that the tuner's interface does not
     know about or cannot reach? Sub-agents and input transforms are known
     examples. Look for others in the Kiln code and docs. The Kiln docs are a
     good source for what Kiln can do.
3. **Functional spec.** The full list of features to cover, including ones
   the recording missed.
4. **Decide how to ship.** This is the main open question. Options, none
   chosen:
   - by layer: the whole skill, then the API changes, then the red-flag
     tools (or red flags first);
   - by lever: a basic skill, then sub-agents with their knowledge and API
     changes, then input transforms with theirs, and so on.

More techniques will arrive from the owner in small pieces after this
overview. Integrate them.
