---
title: RAG or a skill for a knowledge source
description: Choose how the agent gets a body of documents - all in the prompt, a skill with one level of contents, or a RAG search tool - by its size in tokens and by the kind of question.
reviewed: 2026-11-25
status: active
levers:
  - rag
  - skill
applies_to: all
cost: medium
---

# RAG or a skill for a knowledge source

## When to use

- The answers need facts from a body of documents, and the agent does not
  get them in a form that works: no source, a source that it does not
  find its way in, or a source that floods its context.
- The project has the documents: in its document library (for a RAG
  config), or as text that the builder can read (for a skill).
- A skill or a RAG tool exists, and the traces show that it is the wrong
  form: the agent opens many files of a large skill and does not find the
  part, or a RAG search returns chunks that cut the fact in two.

Ask both questions each time: is RAG better than the skill that exists,
and is a skill better than the RAG tool that exists?

## When not to use

- **The agent already reads the right part** and still answers wrong. The
  form of the source is not the problem: see
  [answer only from the source](answer-only-from-the-source.md).
- **The knowledge is not in the documents.** No form adds a fact that the
  documents do not have.
- **The task does not need outside facts**, and the failures are of
  reasoning or of format.

## Alternatives

- [Attach the knowledge source](attach-the-knowledge-source.md), when a
  search tool or a RAG config that fits already exists and the run config
  does not attach it. Attach it first. This technique is for the choice of
  form.
- [Document into a skill](document-into-a-skill.md), when the source is a
  long document behind a lookup tool, and the decision is already "skill".
- [Test that RAG helps first](test-that-rag-helps-first.md), when the
  decision is "RAG": it gives the first RAG config to build.

## How

Measure the source in tokens, not in documents. A token count says what
fits in the context. A document count does not: one manual can be larger
than five hundred notes. A fair estimate is the extracted text's size in
bytes divided by four, for English text.

1. **Small: put it all in front of the model.** Below about 200,000 tokens
   (Anthropic's figure for a model with a large context window), the whole
   body can go in the prompt or in one skill, with prompt caching where the
   provider has it. On a small model or a short context window, the limit
   is lower: the body must leave room for the task.
2. **Medium, and stable: a [skill](../levers/skill.md) with one level of
   contents.** A `SKILL.md` body that names each reference file and what it
   holds, and one reference file per topic. One level is enough. A deeper
   tree of contents did not help in a public study, and broke some
   answers. A skill suits a body that a person or a strong model can
   organise once, and questions that need a whole section, not a sentence.
3. **Large, or many small parts: [RAG](../levers/rag.md).** A search finds
   a few chunks in a body that is too large to organise or to read. RAG
   suits questions that one or two passages answer, and a body that grows.
   Begin with [test that RAG helps first](test-that-rag-helps-first.md).
4. **The kind of question changes the search.** A question that names an
   exact id, code or rare term suits a full-text or hybrid search. A
   question in other words than the documents suits a vector or hybrid
   search.
5. **The reverse.** When a skill has grown to many files and the traces
   show the agent open file after file, a RAG config over the same
   documents can find the part faster. Build it as a second arm, with the
   same documents, and keep the skill on the parent.

The build makes one form per arm. When both forms are candidates, plan two
arms on the same parent and compare them.

## Check that it took effect

Smoke on two or three train items that failed for lack of a fact. In each
trace, find the read of the skill file or the call of the search tool, and
the fact in the answer. For RAG, read the chunks that the search returned:
the fact must be in them.

## Budget

Medium cost. A skill needs the builder to read and organise the documents.
A RAG config needs an index of the documents, which Kiln does not price
and `--budget-usd` does not count. On a small budget, take the form that
the size in step 1 to step 3 gives, and do not build both.

## If it does not move the evals

It must move an eval. A new form for the source is a fix for answers that
the source should ground. When the traces show the agent reach the right
part and the answers do not improve, the source does not hold what the
answers need, or the agent answers past it.

## Priors

None yet.

## Confidence

Medium on the size rule: the 200,000-token figure is one vendor's
guidance (Anthropic, "Introducing Contextual Retrieval", 2024-09-19), and
it depends on the model's context window. Medium on "one level is
enough", from one controlled public study (He et al., "Is Progressive
Disclosure All You Need for Long-Context Agents?", 2026). Low on the line
between a skill and RAG for a medium body: no public source gives a
number, and the kind of question matters as much as the size.
