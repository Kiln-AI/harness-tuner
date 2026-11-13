# Input transforms and feature engineering for LLM classifiers

Research date: 2026-11-10. Overview baseline: section 7.3.

## Summary

Direct, recent evidence on "remove fields from the JSON and accuracy goes
up" is thin. I did not find a controlled 2025–2026 study that measures
accuracy loss from irrelevant input fields in an LLM classifier. The
supporting evidence is indirect but consistent:

- Irrelevant context lowers accuracy (Shi et al. 2023; Chroma context rot
  2025; Anthropic inverse-scaling 2025, where Claude models "get more
  distracted by irrelevant information" with longer reasoning).
- Decision-model vendors list irrelevant text, numbers and dates as
  documented failure points and say "Trim the state before you send it".
- Input format matters by model (tabular-serialisation studies).
- Retrieved few-shot examples per input (dynamic few-shot) beat static
  examples in several classification studies. This is an input transform
  the overview does not mention.

## Kiln mechanism (code check, not web)

Kiln has `JinjaInputTransform` in
`/home/user/kiln-ai/kiln/libs/core/kiln_ai/datamodel/input_transform.py`:

> "Render the task input via a Jinja2 template, producing the first user
> message sent to the model."

The template is validated at save time. It refers to
`specs/projects/templates/functional_spec.md` in the Kiln repo for the
contract. It is used from `datamodel/test_run_config.py` and
`adapters/model_adapters/base_adapter.py`, so it is a run-config field.
Whether Harness Tuner can reach it belongs to the Kiln capability gap
subtopic.

## Evidence

- **Shi et al., "Large Language Models Can Be Easily Distracted by
  Irrelevant Context", ICML 2023** ([arXiv](https://arxiv.org/pdf/2302.00093)).
  GSM-IC adds one irrelevant sentence to math problems; "model performance
  is dramatically decreased". An instruction to ignore irrelevant
  information helps partly. Older than 12 months; foundational.
- **Chroma, "Context Rot", July 2025**
  ([trychroma.com](https://trychroma.com/research/context-rot)): even one
  distractor degrades results, and more distractors compound (distractor
  detail via secondary summary).
- **Gema et al., "Inverse Scaling in Test-Time Compute", arXiv 2507.14417,
  TMLR Dec 2025** ([Anthropic blog](https://alignment.anthropic.com/2025/inverse-scaling)):
  tasks include "counting with distractors" and "regression with spurious
  features". Longer reasoning amplified distraction for Claude and drift
  "toward spurious correlations". *Inference:* a misleading field (the
  overview's "last decision" example) can become worse at higher thinking
  levels, so input cleaning and thinking level interact.
- **Decision-model vendor documentation** (TypeSafe Jev, via benchlm.ai,
  2026-09-22, [link](https://benchlm.ai/blog/posts/what-is-jev)):
  "Irrelevant text distracts it … Trim the state before you send it";
  "Unreliable arithmetic … Do not ask it to count"; "Parse dates in code".
  The phishing case: five derived Jev signals plus logistic regression
  reached 95.0% against 62.6% for a direct verdict. That is feature
  engineering on the output side as well as the input side.
- **Tabular serialisation.** TabLLM (Hegselmann et al., 2022) found LLM
  performance "very sensitive to the precise details of the natural-language
  input" ([arXiv](https://arxiv.org/pdf/2210.10723)). arXiv 2508.17391 (2025)
  found GPT-5 stable across CSV/JSON/Markdown while DeepSeek was more
  sensitive, with JSON better. *Inference:* format is a small, model-specific
  lever; test it only on weaker models.
- **Dynamic few-shot.** Retrieval of similar labelled examples per input
  beats random or fixed examples in several studies (Sun et al. 2023 "Text
  Classification via LLMs", [arXiv](https://arxiv.org/pdf/2305.08377);
  arXiv 2409.01466 on political-science classification). A 2026 blog cites
  a clinical-note study with +21.1% macro-F1 for dynamic retrieval, and a
  counter-case where a TF-IDF retriever collapsed one model to 35%
  ([tianpan.co](https://tianpan.co/blog/2026-04-12-dynamic-few-shot-retrieval-static-examples-accuracy));
  I could not verify the primary study. MedPrompt (2023) used dynamic
  few-shot as a core part of beating a fine-tuned model.

## Transform catalogue for the skill (inference, from the above)

| Transform | When the trace shows | Risk |
| --- | --- | --- |
| Select fields | Large JSON input with IDs, timestamps, metadata | Dropping a field the label depends on |
| Truncate long text | Few very long inputs dominate tokens or errors | Losing the decisive sentence at the end |
| Remove a misleading field | Model copies a prior label/decision field | Real signal removed — check per-class |
| Compute features (counts, sums, date deltas) | Errors on arithmetic or date comparison | Template bugs; keep it simple |
| Reformat (key: value, Markdown) | Weaker model, nested JSON | Small, model-specific effect |
| Mark untrusted user text | Injection-like content in inputs | None significant |
| Dynamic few-shot | Many classes, confusable labels | Needs a labelled pool and retrieval; Kiln support not checked |

## Confirms, contradicts, misses

- **Confirms:** 7.3 — value is mostly in classification; field selection,
  truncation, computed features and removal of harmful fields are all
  supported by the indirect evidence.
- **Strengthens:** for decision models (7.4), input transforms are close to
  required, not optional.
- **Misses:** dynamic few-shot retrieval; computing dates and numbers in
  the template because models are unreliable at them; the interaction with
  thinking level.
- **Gap:** no direct controlled study of field removal for LLM classifiers
  was found. The tuner should treat each transform as an experiment.
