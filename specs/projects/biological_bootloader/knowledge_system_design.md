# Knowledge system design: maintainability (owner's notes)

The owner's message of 2026-11-12, saved as written. It is not edited. The
project overview has a distilled version (section 2.1). The functional spec
phase reads this document for the details.

---

## Background (why)
For agent optimization techniques, we acknowledge: 
- Techniques will come and go. They will be invented, and go out of fashion
- Techniques that work on some models, won’t be needed on other models
- There will be many many techniques that work
- Several techniques will be relevant to any given project. It’s rare you only need one. 
- Which techniques apply will change project to project, based on their architecture and goals
- There will be “playbooks” of how to optimize that span techniques: always do X before Y. When trying to optimize for speed, do X, Y, Z. etc

Our project is expected to live through these cycles of new techniques/best-practicces. There will never be a single best way to optimize agents, just many ideas to try. Every agent needs experimentation. But picking the right ideas is still key to success.

As a results I think we want to design a skill/knowledge-system that both scales, and is easy to maintain. One “playbook for optimizing agents” would be impossible to maintain, and never optimal.  We need to componentize. Once we have a high level component architecture, it becomes easier to maintain: 
- adding a new technique is 95% in one file, with a few tactical edits pointing to that file
- adding an idea to the backlog of ideas is easy: we can maintain a few TODO lists for each concept (we should start these lists now, ideas are laced throughout this doc)
- components let a bigger team work in parallel. Some folks working on X, others working on Y. They can work independently, and the areas that require agreement are small and designed to be scoped.
- Less maintenance cost: a technique falls out of fashion, it can stay. We just need to update 2 files (the “when to use” in the technique, and any playbook suggesting it).
- keeping components separate prevents debating technique X vs technique Y in every PR. We can accept PRs for capabilities/techniques without too much debate/predjuce. If a technique ever worked, anywhere, it’s fine to include. The debates happen around a few key “playbook” files, owned by a smaller group.

I’m proposing a rough design for the architecture here. I am open to feedback and iteration on it. It’s more complete than it should probably be at this stage and I don’t want to prematurely lock it, but I wanted to capture all of these ideas. When weighing alternatives, this is a good yardstick: do we lose any “features” of this design (or gain some)? So our future spec phases should definitely 1) consider new high level design, 2) lock in the correct details of the design to much better than we have here.
 
## Architecture: Capabilities, Techniques, Playbooks

- Capabilities: The core capabilities of Kiln/Agent harnesses. 
  - Example RAG, skills, sub-agents, prompt editing, etc. 
  - Describe the core capability, the APIs needed to create/tune this. Not the “when/how”, just the core harness capability itself.
- Techniques: guides on how to do a specific optimizations
  - Receipts and examples are great here when we have them
  - For example “sub-agents” could have multiple techniques. 
    - Sub-agent with smarter model: used to allow a fast user-interactive chat model to dispatch for deeper thinking, while keeping the trace of the primary thread we want to stay really fast for non-deep questions 
    - Sub-agent with specific set of skills: an analysis agent with SQL tools/knowledge. Has a VM for code where main agent doesn’t (but short lived so cheap).
    - Sub-agent with cheaper/faster model for inefficient tools. If the top-level agent is an expensive model, and tools return a lot of tokens, while the thinking required to parse them is low, it can be cheaper/faster to have a sub-agent with cheaper model for calling the APIs, which only returns the answer to the top level trace (more expensive tokens there).
  - And techniques can span capabilities:
    - Example: Moving 200 separate API tools into one “call_api” tool is generally a good idea, but you need to pair it with a well design skill describing the API (examples, schema, params, returns, etc).
- Playbooks: specific playbooks that are centred around a specific goal. Can be user goals, certain red-flags/design issues, eval/process failure patterns. 
  - User Goals:
    - User wants the model to be much cheaper: 
    - User wants to use open models on prem
  - Eval issues
    - What do do if an eval is near zero, and the traces do not give us useful information to even hypothesize a fix.
    - What do do if eval is saturated.
    - What to do if fixes that work on train sets are consistently invalidated by val checks.
    - Variance is too high
  - Design issues / Red Flags
    - Uses tool search: model needs to hypothesize a tool might exist and ask for it, not great. Playbook: give it a progressive disclosure skill first. Can also collapse all tools into a “call_api” style tool (P2)
    - Using old models (GPT 4.1, etc)
    - Evals don’t cover product (missing cover)
- P3: Priors
  - Deeper use cases, in their own doc. Leaving out for now, but could add later. The priors list in Techniques could link to these docs for more info.


## Skill Design

The “Knowledge System” (probably skill) we’re building needs to be well design.

- Lock the list of components 
- Lock the design/schema of each component (see “Design/Schema of a Technique” example)
- Design the folder/hierarchy/structure of the knowledge system
- Design the progressive disclosure system: what do I read first? How to I get to file X? How does our designer agent navigate these? Progressive disclosure in right form. Etc. 
- Design any indexes that are auto-generated from lists of compoennets,a nd where they live.
- Design automated tests that make it maintainable
- design agentic guides/evals

Rough current thinking
- Top level has an overview
  - Could be the SKILL.md level, or high level resource file
  - Has a high level “what this is/how to use it” prompt
  - Lists all capabilities, with short description, and link to detailed documents. Generated by code, automatically updates when you add docs
  - Lists the playbooks, with short description, and link to detailed documents. Generated by code, automatically updates when you add docs
  - Doesn’t list all technuqies. Generally expected to read playbooks, which link to techniques. 
    - Maaaaybe we have a link to technique-index (code generated), but might be a bad idea. Selecting a technique based on name < selecting a technquied from playbook based on goal?

Note: Capabilities/Techniques/Playbooks are not exhaustively all content in our optimization skill. There may be other things (tips, tricks, process, etc). This doc focuses on those 3 though.

We should also add automated tests for each component, and the roll-ups. For example: 
- Title: must be included, max lentth, min tenth
- Short desriotuon: must be included, max length, min tenth
- Full descirpiotn: must be included, max length, min tenth
- Required frontmatter present, and any limits (enums, etc)
- required sections present, etc.
- Every file must be discoverable: link back up to Skill.md through progressive disclosure 

Open questions: how does it work if the user gives explicit guidance: “Try using a sub-agent for XYZ”? Harness tuner allows free-form guidance. Might need the technique index I warn against above, but with warning along lines of “prefer playbook, only use if user specifies a technique”.

## Design/Schema of a Technique

We need to design each of the components. They should have a template, required fields, an agent-guidance for generating/auditing them, tests that check structure/required fields, etc.

This section is an example for Technique. Very very rough dump of ideas, needs a lot of refinement.

- Title
- 1 line description
- When to use: 
  - agent design patterns that imply this might be needed
  - specific goals where this may apply
  - specific failure modes
- When not to use
  - Example: Cerebras not ideal for speed on long-multi-turn conversations, as their billing doesn’t have prompt caching (unless you have enterprise plan)
- Next best alternatives
- Budget info:
  - is this expensive? Should it be left out on contained budgets? Is is cheap and a good idea for low budgets.
- Alternatives to consider: cross link to other techuqies, with a “when”
- What to do if it doesn’t move evals?
  - An agent design change can be a good idea, and simply not covered by evals in this project. The current tuner leans heavily on “only promote if val set shows improvement”. That’s not always best practice. The section of technique should guide/inform
    - Adding a sub-agent for quality and it doesn’t impact any eval? We probably pull it out (unless there are cost/speed benefits).
    - Convert a huge prompt with 50 topics into a skill, so it only reads what’s needed but it doesn’t impact the eval (including not hurting it): probably keep this. It’s a better design. Future optimizations may now be possible.
  - Is this: “Better design, same eval” -> take the win” or “needs to move eval to accept” or “well it depends on X, Y, Z”
- Priors/Cases: Specific examples we’ve seen this technique in action
  - A list of real cases where this technique was used. Include enough details that help inform when/how/scale/lessons.
  - “Project X tried this for an agent with N tools designed to do Y. It make a +26.4 improvement to the eval on report quality.”
  - “Project Y did model-selection experiment, but only after a full harness tune was complete. It found 9.8% variance between models at the same cost, and ended up with a 5.6% gain 
  - “Project Z did model-selection experiment, before a harness tune was complete, and the results were never used. The tool errors were dominant, and the experiment had to be repeated later, at extra cost”.
  - Important: can’t include any private client names or data. Needs to be from public sources, synthetic experiments we own, or anonymized/generalized.
- Confidence on all of the above: nothing is 100% confidence. “What to do if it doesn’t move evals?” isn’t a boolean, it’s a statement about confidence and conditions. Priors aren’t universal lessons, but one off samples. The doc should be clear on these. The template should potentially encourage/force the agent creating them to specify confidence (confidence score, confidence statements, etc). Even overall technique confidence: was this something Steve just came up with he has low confidence in, or something we’ve seen work time and time again?
  - I lean text based, allowing us to specify pre-confitions, etc. Allow the planner to reason about this. Enums can be dangerous. But leaving it out is also dangerous.

Technical: format markdown with front matter or something else? Table? Required sections/template? Etc.

Random Example Techniques (start to seed our pending list)
- Speed using Hardware Acceleration: Cerebras and Groq
- On-device local models (end user phones and laptop scale)
- On-prem models (GPUs, but open weight based)

## Casebook

Just a FYI, but we’ll start a casebook in parallel. A deep repository of examples from our own work, across many agents.

This will be able to have case-studies, in depth. It will be used to drive the “Priors” section of techniques but can be 1) much longer/more depth, and not optimized for the harness-tuner, more a complete DB. 2) can include non-public information.
