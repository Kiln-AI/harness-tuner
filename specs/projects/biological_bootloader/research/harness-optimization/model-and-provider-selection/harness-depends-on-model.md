# Evidence That the Best Harness Depends on the Model

Research date: 2026-11-10. All sources are from 2026.

The overview says: "There is no single best harness for an agent. The best
harness for Opus is not the best harness for Haiku", and "You cannot design
the harness and choose a model afterwards." The 2026 literature strongly
confirms this. It also gives evidence on the overview's order of work.

## Evidence

| Source | Setup | Finding |
| --- | --- | --- |
| Li et al., "Finding the Right Fit: Model–Harness Interactions across Agent Tasks" ([arXiv 2610.00917, 2026-10-01](https://arxiv.org/html/2610.00917v1)) | 66 configurations: 4 harnesses × 5 models on 3 benchmarks, plus native vendor pairings. 6,204 scored trajectories released | "Model rankings reverse across harnesses." On Terminal-Bench 4, Claude goes from 57.14% (OpenHands) to 30.16% (PI) while GPT goes from 49.21% to 60.32%. For four of five models the best harness changes by benchmark. "A model's own vendor harness is not reliably its best." Mechanism: whether the harness hands failures back in a form the model can use. A model that often issues malformed tool calls (Kimi) does best in a harness that catches them |
| Yang et al., "Better Harnesses, Smaller Models" ([arXiv 2607.08938, CMU, 2026-07-09](https://arxiv.org/html/2607.08938)) | 7 business agent tasks × 3 small-model families; automated harness optimiser driven by failure trajectories | Adapted harnesses improved 16 of 21 task–model pairs; 7 pairs closed the gap to a large model; the best recovered 89.7% of large-model performance at 4% of the cost. "Each model's failure patterns require tailored harness adaptation that does not cleanly transfer ... harness optimization needs to be re-run each time we migrate to a new SLM." |
| Bi et al., "Co-Evolving Harnesses and Models" ([arXiv 2609.09134, 2026-09-08](https://arxiv.org/html/2609.09134)) | Harness evolved on a weak model (Qwen3-Coder 30B-A3B), then used by a strong model (Gemini 3.1 Pro) | Evolved harness lifts the weak model from 29.2% to 78.0%. The strong model also gains: 84.4% to 93.6%, and uses nearly every evolved edit. Imitation fine-tuning on the strong model's trajectories then *hurts* the weak model (−4 to −30 points), because it breaks the fit to the harness |
| Fu et al., "Self-Harness" ([arXiv 2606.09498, 2026-08-20](https://arxiv.org/html/2606.09498v3)) | Minimal harness, three model families, three benchmarks | "Because different models exhibit distinct behaviors, effective harness design is inherently model-specific." Every final harness improved held-in and held-out pass rates |
| Zhao et al., "Strong-to-Weak Capability Transfer via Harnesses" ([arXiv 2608.12307](https://arxiv.org/html/2608.12307v1), Salesforce) | Strong builder model writes a scaffold for a fixed weak target | GPT-5.4-mini goes from 0.49 to 0.91 macro accuracy. "Scaffolding helps the weaker GPT-5.4-mini target more than the already stronger Gemini-3.5-flash target." Gains come from structure, deterministic offloading and strict format enforcement |
| LangChain, "Tuning Deep Agents to Work Well with Different Models" ([blog, 2026-04-29](https://www.langchain.com/blog/tuning-deep-agents-different-models)) | Per-model harness profiles (prompt, tool names, middleware) | 10 to 20 points on a hard τ²-bench subset (GPT 5.3 Codex 33% → 53%; Claude Opus 4.7 43% → 53%). Vendor prompting guides prescribe different tool names and conventions (for example `apply_patch` for Codex models). "A single harness can't be optimal for every model." |
| BFCL v4 format sensitivity ([Berkeley](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html)) | 26 prompt and format variants | Smaller models are more sensitive to the return format; some large Claude models drop on XML return formats |

## What this confirms in the overview

- **Model and harness are a pair.** Strongly confirmed by four independent
  2026 studies.
- **"Do not tune on a very large model and expect the result to transfer to a
  small one."** Confirmed indirectly: harness fixes for one small model do
  not transfer cleanly to another small model (CMU). No study I found tunes on
  a large model and moves *down*; the co-evolution paper tests the reverse.
- **Step 1, lock the class and the model, then tune the harness.** Supported
  by the CMU and Self-Harness designs, which both fix the model and evolve
  the harness.
- **Step 3, re-scan models after tuning.** Supported for moves *up*: a
  harness tuned on a weak model is used well by a stronger one (co-evolution
  paper). For moves *across* or *down* (to a different small model), the
  sources say re-tune. Inference: the re-scan in step 3 should rank
  candidates by their score on the tuned harness, and treat a winner from a
  different family as the start of a short re-tune, not as the end.

## What this adds that the overview does not have

1. **The biggest model-specific gains come from error handling at the tool
   boundary**, not from the prompt alone: how the harness returns malformed
   tool calls, timeouts and failures to the model (Li et al.). A model that
   makes many malformed calls needs a harness that catches and explains them.
   Kiln's code tools as wrappers are a place to do this.
2. **Vendor prompting guides are a cheap source of model-specific harness
   ideas** (LangChain): tool names, edit-tool format, planning instructions.
   A model guide entry should link the vendor's prompting and migration
   guides.
3. **The weaker the model, the bigger the harness gain** (Salesforce, CMU).
   This supports the overview's "compile the gains into the harness, then run
   a cheap model" thesis with numbers.
4. **The sweet-spot propensity in the overview (about 20%, with room to reach
   65%)** is consistent with the reported lifts (29% → 78%, 49% → 91%), but no
   source tests a starting-pass-rate rule directly. **Unverified as a rule.**
