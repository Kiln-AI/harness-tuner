---
status: complete
---

# RAG as a lever

This project makes RAG a lever of Harness Tuner. It closes gap 5 of
[kiln_gaps.md](../biological_bootloader/kiln_gaps.md) and the entry "RAG"
of the [backlog](../../backlog.md).

## 1. The brief (2026-11-25)

Section 7.1 of the
[biological_bootloader overview](../biological_bootloader/project_overview.md)
gives the picture. Kiln has a RAG tool. A RAG config has five parts: an
extractor, a chunker, an embedding, a vector store (the search type:
full-text, vector or hybrid) and an optional reranker. The first question
is RAG or a skill. Documents and their extraction come first. Extract once
and do not tune the extractor. Test that RAG helps at all before you tune
the chunker and the search type.

### What existed

- Read operations for loop agents: `list_rag_configs`, `get_rag_config`,
  `rag_search` and `list_documents`.
- A run config could attach an existing RAG config as the tool
  `kiln_tool::rag::<rag_config_id>` (the lever `tool_config`).
- No operation created a part, a RAG config or an index. No document said
  when RAG wins.

### What the brief asked for

1. **The capability.** Operations for a build agent to create a RAG config
   from parts and attach its search tool on a new run config, with the
   rules around them: which parts the tuner creates and which it only
   reuses, a budget rule for paid indexing, the model policy, the lever
   kind string, the "lever applied" check, the integrity rules and the
   cleanup.
2. **The knowledge.** The lever document, and the techniques of the
   backlog entry, linked from the playbooks.

## 2. Decisions

Each decision is built as stated. The decisions marked **Owner** are
choices that the owner can change: each one is the conservative choice.

1. **The lever kind string is `rag`.** The planner writes it in
   `technique`. An arm that names it and attaches no RAG tool that its
   parent does not attach ends as "lever not applied", before any train
   spend.
2. **The tuner creates the chunker, the embedding, the vector store (search
   type), the reranker and the RAG config. It reuses the extractor.** It
   creates no extractor config and starts no extraction. A create is
   refused when a document to index has no extraction for the chosen
   extractor config. Reason: extraction is the most expensive step, it is
   many levels down from the answer, and the backlog says to do it once.
   The owner or the user runs it in Kiln.
3. **One operation, `create_rag_config`, makes the new parts, the RAG
   config and the index, in that order.** Every check runs before the first
   write. Each part is either a config of the project (its id) or a new
   one. Only the build agent has it. `list_rag_parts` (every role that
   reads artifacts) shows what can be reused and which models are allowed.
   `index_rag_config` runs the index again for a create whose index did not
   end. `archive_rag_config` archives a config of this run.
4. **A RAG config never changes.** A new chunker, search type or reranker
   is a new RAG config. Reason: a measured run config attaches the old one.
5. **Owner: the index budget.** Kiln reports no cost for an embedding call.
   So the cap is on what code can count before the call: the bytes of
   extracted text that the index sends to the embedding model. One create
   sends at most 20,000,000 bytes (`INDEX_MAX_BYTES`, about five million
   tokens). A document that already has embeddings for the same extractor,
   chunker and embedding config costs nothing, because Kiln uses them
   again. A semantic chunker counts twice, because it calls the embedding
   model to find its breaks. One run creates at most six RAG configs
   (`MAX_CREATED_PER_RUN`). None of this is in `--budget-usd`.
6. **The model policy binds the extractor, embedding and reranker models.**
   The documents and every search query are sent to them. The check runs
   at create, on every model of the config (a semantic chunker's own
   embedding model too).
7. **Owner: an exception for the models the project already uses.** A pair
   that a RAG config uses in the same role, when this run did not create
   that config, is allowed. A deny rule of a policy file wins over it.
   Reason: the owner already sends the documents to that model. Without the
   exception, `--models keep` (the default under the quality objective)
   allows only the baseline's chat model, and the lever could never run.
   So an existing RAG config can always be attached, as before.
8. **Owner: the document check, block and flag.** A document of the library
   is not a train item. But a library can hold eval cases, for example a
   JSONL file of labelled posts. Code reads every eval item of the task in
   every split (eval inputs, and task runs that carry a tag), and looks for
   each text of eight words or more, whole and in order, in the extracted
   text of each document to index.
   - A document that holds the whole **input** of an eval item **blocks**:
     the create is refused, and an arm that attaches such a config ends
     before any train spend. This holds for every lever, so an arm under
     `tool_config` that attaches a RAG config of the user is checked too.
   - A document that holds the whole **expected output** of an eval item
     is a **flag** on the experiment's record. A reference answer written
     from a document can repeat a sentence of it, and that is what a
     search is for.
   - A finding names documents and counts, never an item, a split or a
     text. The check reads at most 80,000,000 bytes, and says "not
     complete" past it.
9. **A screen or a probe cannot attach a RAG tool.** They fork a run config
   in code, with none of the checks above.
10. **The lever check is a call in a trace.** After the train measurement,
    code reads the stored train rows of the arm. The lever is applied when
    one row's trace calls the new search tool. It is "not applied" (a dead
    end) when two rows or more have a trace and none calls it. With fewer
    traces it is unverified, and the arm goes on.
11. **`create_run_config` refuses a RAG tool** that is not a RAG config of
    the project, that is archived, or that this run created and whose index
    is not complete. A search of an empty index returns nothing, so the arm
    would measure its parent.
12. **Owner: the run deletes nothing.** At the end of a run, code archives
    each RAG config of this run that no run config of the task attaches.
    Kiln has no delete for a RAG config, a part or an index, and no archive
    for a part. A config that a run config attaches is never archived, so
    every run config the run measured still runs.
13. **Not in this project.** Tuning the extractor. Uploading documents.
    Reading the RAG tool's description in the generality and judge-copy
    checks (it is at most 128 characters). A guard for the cost and the
    latency of a search, which Kiln does not count (section 3).

## 3. The finding on cost and latency

Read in the Kiln library, version 1.1.2 (`kiln_ai/tools/rag_tools.py`,
`kiln_ai/adapters/embedding/litellm_embedding_adapter.py`), and in the Kiln
server's `document_api.py`:

- A search embeds the query (a vector or hybrid store), searches the local
  LanceDB store and, with a reranker, calls the reranker model. The tool
  result is the chunks as text. It carries no usage.
- The `usage` of a task run sums its model calls. The embedding call and
  the reranker call of a search are not model calls of the run. So the
  cost of a run that searches leaves them out, and its latency
  (`total_llm_latency_ms`) leaves out the time of the search.
- The index reports progress counts and error logs, no cost.

So a RAG arm's cost and latency are a little low. The query embedding is
small next to a chat call. The reranker call and the search time can
matter under the speed objective. No guard is built: the report section
says it, and the backlog has an entry.

## 4. What a finished run shows

For a run that used the lever:

- The experiment's `technique` is `rag`, in the wave log
  (`wave_start.items`) and on the experiment's memory record.
- The run's `gateway_created.jsonl` names each record that a create made,
  with the kinds `rag_config`, `rag_chunker_config`,
  `rag_embedding_config`, `rag_vector_store_config` and
  `rag_reranker_config`.
- The promoted run config has the tool id `kiln_tool::rag::<id>` in
  `tools_config.tools`.
- The wave log has `rag` events: `changed` after the build (with the
  config in one line and the document check's line), then `applied`,
  `unverified` or `not_applied` after the train measurement, or `blocked`
  or `refused`. The experiment's record has the line "rag: adds tool …;
  document check: …; rag applied: the search tool … is called in N of M
  train row(s)".
- The report has a `## RAG configs (code-computed)` section: each RAG
  config this run created, with its tool id, its parts in one line, the
  run configs that attach it, and its state against the final baseline.
- The wave log has one `rag_configs` event at the end of the run, which
  lists the same records and how many configs it archived.
