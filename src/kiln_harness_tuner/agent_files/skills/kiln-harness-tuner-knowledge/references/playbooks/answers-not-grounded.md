---
title: Answers are not grounded
description: The answers need knowledge that is not in the agent's context - attach the source, then add scope discipline. A source behind a lookup tool can move into a skill.
reviewed: 2026-11-25
kind: failure
applies_to: all
---

# Answers are not grounded

## Situation

The answer states facts that are wrong, invented or out of date, and the
right facts are not in the agent's context. Look at the trace before the
final answer:

- no tool call or skill read brought the facts in, because the agent has
  no source for them;
- or the agent has a source, and it did not reach the part that holds the
  answer: it searched with the wrong terms, read the wrong page or
  section, or did not look;
- or the source was read, and the answer goes past what the source says.

The knowledge exists somewhere the tuner can reach: a search tool or a
RAG tool of the project, a document, a skill. When the source is a long
document that the agent reaches only by page, by search or by section, the
smell [document behind a lookup tool](../smells/document-behind-a-lookup-tool.md)
says how to recognise it.

## What to try, in order

1. [Attach the knowledge source](../techniques/attach-the-knowledge-source.md),
   when the agent has no source for the facts. Attach an existing search
   or RAG tool, or write the knowledge into a skill.
2. [RAG or a skill for a knowledge source](../techniques/rag-or-a-skill-for-a-knowledge-source.md),
   when no source exists in a form that the agent can use, and the
   project's document library holds the facts. Choose the form by the
   size in tokens. For RAG, start with
   [test that RAG helps first](../techniques/test-that-rag-helps-first.md),
   then [add a reranker](../techniques/add-a-reranker.md) and
   [tune the search type and the chunker](../techniques/tune-the-search-type-and-the-chunker.md)
   when the searches miss.
3. [Answer only from the source](../techniques/answer-only-from-the-source.md),
   when the source is in the context and the answers go past it. A short
   rule of scope discipline: answer from the source, and say so when the
   source does not cover the question.
4. [Document into a skill](../techniques/document-into-a-skill.md), when a
   long reference document sits behind a lookup tool (by page, by search
   or by section), and the agent picks the wrong part or never looks. A
   contents file that names what each section holds, one reference file
   per section, and the lookup tool dropped on the same arm.

## What not to do yet

- No model swap. A stronger model does not know facts that are not in its
  context, and it can state wrong facts with more confidence.
- No closed list of what the agent may answer. A list breaks on each
  question that it does not name, and the model matches it by words, not
  by meaning.
- No prompt rules on which page or section to choose. The agent reads the
  rule before it sees the contents, and the rule cannot name every case.

## Budget

The order does not change with the budget. Steps 1, 2 and 4 have a
medium cost. On a small budget, step 3 alone is worth an arm when the
source is already attached. A RAG config's index is not in
`--budget-usd`: on a small budget, take step 2 only when its parts are
already indexed.
