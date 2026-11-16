# When fine-tuning is worth it after harness work

Research date: 2026-11-10. Overview baseline: section 7.6. Note: prompt
optimisers (GEPA, DSPy) are out of scope; results that only compare against
them are left out.

## Summary

The sources agree with the overview's order: harness first, fine-tune late.
They add three specifics:

1. **Closed label sets with plenty of examples** are where fine-tuning wins
   most clearly, by large margins, and mainly by removing long instructions
   from every call.
2. **Volume is the trigger.** Practitioner guides put break-even at about
   10,000 requests per day (vendor estimate).
3. **SFT copies; RL generalises.** For behaviour beyond the training
   distribution, outcome-reward RL beat SFT in a controlled study. SFT is
   still useful to fix output format first.

A new option appeared in October 2026: **RL fine-tuning of an open decision
model** (Cloudflare offers this for Clef). For classification, this may be
cheaper than fine-tuning an LLM.

## Evidence

- **Highlighter.ai 2025 study** (via llm-stats.com, 2026-04-30,
  [link](https://llm-stats.com/blog/research/fine-tuning-vs-prompt-engineering-2026)):
  fine-tuned Qwen2.5-7B against prompted Claude Sonnet 3.5/3.7 on report
  classification. Power outages: 88% vs 31%. Serious injuries: 78% vs 59%.
  Cost: $789 vs $11,485 per million classifications; "The 14× cost gap came
  almost entirely from token efficiency: the prompted model needed an
  exhaustive instruction set on every call." Secondary report; the primary
  study was not read. Older Claude models.
- **Chu et al., "SFT Memorizes, RL Generalizes", ICML 2025** (via the same
  article): with scaled compute, outcome-reward RL kept improving on unseen
  rule and visual variants while SFT got worse; SFT is "still essential as a
  warmup" for format.
- **LoRA Land, Predibase, arXiv 2405.00732, 2024**
  ([arXiv](https://arxiv.org/html/2405.00732v1)): 310 LoRA fine-tunes over 31
  tasks; fine-tuned models beat their base models by 34 points and GPT-4 by
  10 points on average. Older than 12 months, against an old baseline, and
  from a fine-tuning vendor.
- **distil labs, 2025-03-09** ([link](https://www.distillabs.ai/learn/is-fine-tuning-worth-it/)):
  fine-tune "when you feel the pain": prompt fragility, cost, latency or an
  accuracy ceiling. Signals: stable task, "enough volume to clear the
  break-even". Distillation from a teacher model with as few as 10 seed
  examples. Vendor.
- **Practitioner consensus 2026** (search summary across several guides):
  the common pattern is "prompt-optimize on a frontier model, then distill
  to a small fine-tuned model when latency or cost requires it"; fine-tuning
  is a poor fit when labels change often; facts that change belong in
  retrieval, not weights. Vendor and blog sources.
- **Cloudflare Clef, 2026-10-01**
  ([blog](https://blog.cloudflare.com/clef-decision-models/)): an RL
  fine-tuning product for the Clef decision model, first through their
  engineers, later self-serve.

## Implications for the tuner (inference)

- The overview's position (tuner does not start a fine-tune; existing
  fine-tunes are model candidates) is consistent with the evidence. Signals
  the tuner can report to the user as "fine-tuning is now worth a look":
  - the task is classification or fixed-format extraction with a stable
    label set;
  - the tuned harness has a long instruction block that every call pays
    for;
  - a small model is close to the bar and the volume is high;
  - the harness is already well tuned (tools, inputs, prompts).
- After a fine-tune, re-tune the harness on the new model. The model and
  harness are a pair (overview 6.4).
- A fine-tuned model inherits a bad tool layer. This agrees with the
  overview's main point, though I found no source that measures it directly.

## Confirms, contradicts, misses

- **Confirms:** 7.6 — fine-tune late; not for knowledge; mainly to move a
  small model across the bar; high volume and on-device as valid reasons.
- **Misses:** classification with a fixed label set as the strongest case;
  removal of long instructions as the main cost saving; SFT vs RL choice;
  RL fine-tuning of decision models as a cheaper route for classifiers.
- **Gap:** no independent 2026 benchmark compares a well-tuned harness with
  a fine-tuned small model on agentic (tool-using) tasks.
