# Error-analysis practice for LLM systems

Research date: 2026-11-10. Eval design is out of scope for this subtopic.
This file covers only how practitioners read traces to find what is wrong,
because that is what the tuner's planner does before it proposes an
experiment.

## 1. The Husain and Shankar method

Primary sources: Hamel Husain and Shreya Shankar,
["Why is error analysis so important… and how is it performed?"](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html)
(2025-06-27; the same text is in the
[evals FAQ](https://hamel.dev/blog/posts/evals-faq/), updated 2026-09-18).

The steps, quoted:

1. **Create a dataset.** "Gathering representative traces".
2. **Open coding.** "review and write open-ended notes about traces, noting
   any issues … Start by annotating at least 30 traces yourself before
   reviewing suggestions from an agent. When beginning, it is recommended to
   focus on noting the first failure observed in a trace, as upstream errors
   can cause downstream issues".
3. **Axial coding.** "Categorize the open-ended notes into a 'failure
   taxonomy.' … Axial coding is the most important step. At the end, count
   the number of failures in each category. You can use an LLM to help with
   this step."
4. **Iterative refinement.** "After your first 30 annotations, let it [the
   agent] search the remaining traces for likely instances of the failures
   you described … keep iterating until you reach theoretical saturation".
   "A working pool of roughly 100 diverse traces is a useful guardrail".

They name ways to choose which traces to read: "clustering, sorting by user
feedback, and sorting by high probability failure patterns."

The minimum setup: "Spend 30 minutes manually reviewing 20-50 LLM outputs
whenever you make significant changes"
([FAQ](https://hamel.dev/blog/posts/evals-faq/)).

On prioritisation, Husain's video
["Stop Guessing: Use Axial Coding to Prioritize Your LLM Fixes"](https://www.youtube.com/watch?v=h30mams2WaI)
says the long tail is fine, but "probably like some 80/20 rule. 80% of the
failures are going to be explained by a small number of categories."

## 2. What LLMs can and cannot do in error analysis

Source: ["What parts of evals can be automated with LLMs?"](https://hamel.dev/blog/posts/evals-faq/what-parts-of-evals-can-be-automated-with-llms.html)
(2025-07-24).

LLMs can help with: "First-pass axial coding", "Mapping annotations to
failure modes", "Suggesting prompt improvements", "Analyzing annotation
data".

Do not hand to an LLM: "Initial open coding", "Validating failure
taxonomies", "Ground truth labeling", "Root cause analysis". Example given:
an LLM groups "app crashes after login" and "login takes too long" under one
"login issues" category, although they need different fixes.

Husain's later note ["Automating Error Analysis"](https://hamel.dev/notes/llm/ai-product-engineering/evals-error-analysis.html)
(2026-06-24) describes an "Error Discovery skill" that "updates a
failure-mode taxonomy as you annotate and checks earlier records for each new
pattern. It uses active learning to choose the next examples". The human
still annotates.

**Tension with Harness Tuner.** The method assumes a human domain expert
does open coding. In Harness Tuner the planner is an LLM and there is no
human in the loop during a run. The research results below support the
caution: LLMs are weak at locating errors in long traces (TRAIL best 11%
joint accuracy; Who&When best 14.2% step accuracy; see
[failure-taxonomies.md](./failure-taxonomies.md)). Inference: the tuner
cannot copy the human method. It can copy its structure (first failure,
categories, counts, saturation) and use code to do what code can do
reliably, so that the LLM reads less and reads the right part.

## 3. Anthropic's practice

- ["Demystifying evals for AI agents"](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
  (2026-01-09): "You won't know if your graders are working well unless you
  read the transcripts and grades from many trials." Its example task specs
  track transcript metrics `n_turns`, `n_toolcalls`, `n_total_tokens` and
  latency metrics `time_to_first_token`, `output_tokens_per_sec`,
  `time_to_last_token`, and set a `max_turns` bound.
- Same post, on broken tasks: "With frontier models, a 0% pass rate across
  many trials (i.e. 0% pass@100) is most often a signal of a broken task, not
  an incapable agent". Example: "Opus 4.5 initially scored 42% on
  CORE-Bench, until an Anthropic researcher found multiple issues: rigid
  grading … ambiguous task specs, and stochastic tasks … Opus 4.5's score
  jumped to 95%."
- ["Writing effective tools for agents"](https://www.anthropic.com/engineering/writing-tools-for-agents)
  (2025-09-11): "Review the raw transcripts (including tool calls and tool
  responses) to catch any behavior not explicitly described in the agent's
  CoT." "Lots of redundant tool calls might suggest some rightsizing of
  pagination or token limit parameters is warranted; lots of tool errors for
  invalid parameters might suggest tools could use clearer descriptions or
  better examples." "Tracking tool calls can help reveal common workflows
  that agents pursue and offer some opportunities for tools to
  consolidate."

The last three quotes are the clearest published mapping from a
code-computable trace metric to a harness fix. They support the overview's
section 6.3 and section 9.

## 4. Implications for the tuner (inference)

1. **First failure per trace.** Code can find the first tool error, the
   first repeated call and the first oversize result in each trace. Give the
   LLM the trace position of these, not the whole trace.
2. **Count before you theorise.** Axial coding ends with counts per
   category. The tuner can do the same for code-detected flags across all
   train traces at no LLM cost.
3. **Saturation.** Read failing traces until new ones stop giving new
   categories. This bounds LLM spend (overview section 9, tier 4).
4. **Suspect the environment when all fail.** A target eval with 0% pass on
   every item is first a signal about the task, the eval or the
   environment. This matches the overview section 7.7 ("these evals fail for
   an environment reason"). Note: the tuner already has a detectability
   check in setup (`docs/architecture.md` section 5.1); the Kiln-gap
   subtopic should confirm what it covers.
