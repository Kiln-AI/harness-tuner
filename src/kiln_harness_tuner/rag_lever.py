"""RAG as a lever: what code knows and checks about a RAG config.

A RAG config is a record of the project. It names five parts: an extractor
config, a chunker config, an embedding config, a vector store config (the
search type) and, optionally, a reranker config. Kiln indexes the project's
documents with them, and a run config attaches the result as the search
tool `kiln_tool::rag::<rag_config_id>`.

A build agent creates a RAG config with the gateway operation
`create_rag_config`. This module holds the rules that the gateway and the
loop apply around it. They are in one place so that both apply the same
ones.

**What the tuner creates.** A chunker config, an embedding config, a vector
store config, a reranker config and the RAG config itself. It never creates
an extractor config and never starts an extraction: a new RAG config takes
an extractor config of the project, and every document that it indexes must
already have an extraction for it (`Scope.not_extracted`).

**The index budget.** To index a RAG config is to send the text of its
documents to the embedding model. Kiln reports no cost for that, so the cap
is on what code can count before the call: the bytes of extracted text that
the embedding model will read (`IndexPlan.paid_bytes`). A document that
already has embeddings for the same extractor, chunker and embedding config
costs nothing: Kiln reuses them. One create is refused above
`INDEX_MAX_BYTES`. One run creates at most `MAX_CREATED_PER_RUN` RAG
configs.

**The model rule.** The documents and every search query are sent to the
models of the config: the extractor's, the embedding's and the reranker's.
The model policy is about where the task's data is sent, so it binds them
(`model_refusal`). One exception: a pair that a RAG config of the project
already uses in the same role, when this run did not create that config.
The owner already sends the documents there. A deny rule of a policy file
wins over the exception.

**The document check.** A document of the library is not a train item. But
a library can hold the eval set itself, for example a file of labelled
cases. Then the search returns the item with its answer. Code reads every
eval item of the task from disk, in every split, and looks for its text in
the extracted text of each document that the config indexes
(`scan_documents`):

- a document that holds the whole **input** of an eval item BLOCKS: the
  create is refused, and an arm that attaches such a config ends before any
  train spend;
- a document that holds the whole **expected output** of an eval item is a
  FLAG on the experiment's record. A reference answer that was written from
  a document can repeat a sentence of it, and that is what a search is for.

A finding names documents and counts. It never names an item, a split or a
text, so it shows nothing of a held-out item.

**The lever check.** An arm that attaches a new RAG tool is "applied" when
the search tool is called in the trace of one stored train row
(`applied_verdict`).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.integrity import QUOTE_NGRAM, _trace, _words, strings_in
from kiln_harness_tuner.levers import LeverVerdict
from kiln_harness_tuner.model_policy import ModelPolicy

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)

# The lever's name: what the planner writes in `technique`.
LEVER = "rag"
RAG_TOOL_PREFIX = "kiln_tool::rag::"
# The kinds of the records `create_rag_config` writes in the run's registry
# (`gateway_created.jsonl`). The registry id is "<kind>:<record id>".
RAG_CONFIG_KIND = "rag_config"
PART_KINDS = {
    "chunker": "rag_chunker_config",
    "embedding": "rag_embedding_config",
    "search": "rag_vector_store_config",
    "reranker": "rag_reranker_config",
}
CREATED_FILE = "gateway_created.jsonl"

# The most RAG configs one run creates.
MAX_CREATED_PER_RUN = 6
# The most extracted text one create sends to the embedding model, in
# bytes. English text is about four bytes a token, so this is about five
# million tokens.
INDEX_MAX_BYTES = 20_000_000
# The most extracted text the document check reads. Past it the check says
# that it is not complete.
SCAN_MAX_BYTES = 80_000_000
# The longest one gateway call waits for an index. Kiln goes on from where
# it stopped when the index is run again.
INDEX_WAIT_TIMEOUT_SECS = 600.0
# An input or an expected output shorter than this many words is not looked
# for: a short text is in many documents by chance.
MIN_TEXT_WORDS = QUOTE_NGRAM
# The lever is "not applied" only when this many train rows have a trace
# and none calls the search tool.
MIN_ROWS = 2
# How many document ids a finding names.
FINDING_IDS = 5

MODEL_ROLES = ("extractor", "embedding", "reranker")


# ------------------------------------------------------------------ tool ids


def rag_tool_ids(props: Any) -> tuple[str, ...]:
    """The RAG tool ids a run config attaches, in its tool order."""
    config = props.get("tools_config") if isinstance(props, Mapping) else None
    tools = config.get("tools") if isinstance(config, Mapping) else None
    return tuple(
        dict.fromkeys(str(t) for t in tools or [] if str(t).startswith(RAG_TOOL_PREFIX))
    )


def rag_config_id(tool_id: str) -> str:
    return tool_id.removeprefix(RAG_TOOL_PREFIX)


def added_tools(parent: Any, child: Any) -> tuple[str, ...]:
    """The RAG tool ids the child attaches and the parent does not."""
    before = set(rag_tool_ids(parent))
    return tuple(t for t in rag_tool_ids(child) if t not in before)


# ------------------------------------------------------------------ the library


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _children(folder: Path, filename: str) -> Iterator[tuple[Path, dict[str, Any]]]:
    if not folder.is_dir():
        return
    for child in sorted(folder.iterdir()):
        path = child / filename
        if child.is_dir() and path.is_file():
            yield child, _read(path)


@dataclass(frozen=True)
class ScopeDocument:
    """One document that a RAG config indexes. `text_path`: the extracted
    text for the config's extractor, None when the document has no
    extraction for it. `text_bytes`: the size of that file."""

    id: str
    name: str
    tags: tuple[str, ...]
    text_path: Path | None
    text_bytes: int
    extraction_dir: Path | None

    def text(self) -> str | None:
        if self.text_path is None:
            return None
        try:
            return self.text_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


@dataclass(frozen=True)
class Scope:
    """The documents that a RAG config with these `tags` and this extractor
    indexes, read from the project on disk."""

    documents: tuple[ScopeDocument, ...]

    @property
    def not_extracted(self) -> list[str]:
        return [d.id for d in self.documents if d.text_path is None]

    @property
    def text_bytes(self) -> int:
        return sum(d.text_bytes for d in self.documents)


def _extraction(
    doc_dir: Path, extractor_id: str
) -> tuple[Path | None, int, Path | None]:
    """The extracted text of a document for one extractor config: its path,
    its size and the extraction's directory. The first extraction with a
    file that can be read."""
    for folder, record in _children(doc_dir / "extractions", "extraction.kiln"):
        if str(record.get("extractor_config_id")) != extractor_id:
            continue
        output = record.get("output")
        rel = output.get("path") if isinstance(output, Mapping) else None
        if not isinstance(rel, str) or not rel:
            continue
        path = folder / rel
        try:
            return path, path.stat().st_size, folder
        except OSError:
            continue
    return None, 0, None


def library_tags(project_root: Path) -> dict[str, int]:
    """Each document tag of the library and how many documents carry it.
    The key "" counts every document."""
    counts: dict[str, int] = {"": 0}
    for _folder, record in _children(project_root / "documents", "document.kiln"):
        counts[""] += 1
        for tag in record.get("tags") or []:
            counts[str(tag)] = counts.get(str(tag), 0) + 1
    return counts


def read_scope(
    project_root: Path, extractor_id: str, tags: Sequence[str] | None
) -> Scope:
    """The documents of the library that carry one of `tags` (every
    document when there is none): Kiln's own rule for what a RAG config
    indexes."""
    wanted = {str(t) for t in tags or []}
    found: list[ScopeDocument] = []
    for folder, record in _children(project_root / "documents", "document.kiln"):
        doc_tags = tuple(str(t) for t in record.get("tags") or [])
        if wanted and not wanted & set(doc_tags):
            continue
        path, size, extraction_dir = _extraction(folder, extractor_id)
        found.append(
            ScopeDocument(
                id=str(record.get("id") or folder.name.partition(" - ")[0]),
                name=str(
                    record.get("name_override")
                    or record.get("friendly_name")
                    or record.get("name")
                    or folder.name
                ),
                tags=doc_tags,
                text_path=path,
                text_bytes=size,
                extraction_dir=extraction_dir,
            )
        )
    return Scope(tuple(found))


def _chunked(extraction_dir: Path, chunker_id: str) -> list[Path]:
    return [
        folder
        for folder, record in _children(
            extraction_dir / "chunked_documents", "chunked_document.kiln"
        )
        if str(record.get("chunker_config_id")) == chunker_id
    ]


def _embedded(chunked_dirs: Iterable[Path], embedding_id: str) -> bool:
    return any(
        str(record.get("embedding_config_id")) == embedding_id
        for folder in chunked_dirs
        for _f, record in _children(
            folder / "chunk_embeddings", "chunk_embeddings.kiln"
        )
    )


# ------------------------------------------------------------------ the index budget


@dataclass(frozen=True)
class IndexPlan:
    """What an index of a RAG config will send to the embedding model.
    `paid_bytes`: the extracted text of the documents that have no
    embeddings yet for this extractor, chunker and embedding config, and
    once more for each document a semantic chunker must still chunk (it
    calls the embedding model to find the breaks)."""

    documents: int
    text_bytes: int
    paid_documents: int
    paid_bytes: int

    def refusal(self) -> str | None:
        if self.paid_bytes <= INDEX_MAX_BYTES:
            return None
        return (
            f"the index would send {self.paid_bytes:,} bytes of extracted text"
            f" to the embedding model ({self.paid_documents} document(s)); one"
            f" create sends at most {INDEX_MAX_BYTES:,}. Index fewer documents"
            " (`tags`), or reuse the chunker config and the embedding config"
            " of a RAG config that is already indexed"
        )


def index_plan(
    scope: Scope,
    *,
    chunker_id: str | None,
    embedding_id: str | None,
    semantic: bool,
) -> IndexPlan:
    """`chunker_id` / `embedding_id`: None for a config that the create
    makes new, so no document has records for it."""
    paid_documents = paid_bytes = 0
    for doc in scope.documents:
        if doc.extraction_dir is None:
            continue
        chunked = _chunked(doc.extraction_dir, chunker_id) if chunker_id else []
        embedded = bool(chunked and embedding_id and _embedded(chunked, embedding_id))
        passes = (0 if embedded else 1) + (1 if semantic and not chunked else 0)
        if passes:
            paid_documents += 1
            paid_bytes += passes * doc.text_bytes
    return IndexPlan(
        documents=len(scope.documents),
        text_bytes=scope.text_bytes,
        paid_documents=paid_documents,
        paid_bytes=paid_bytes,
    )


# ------------------------------------------------------------------ the model rule


def _pair(part: Any) -> tuple[str, str] | None:
    if not isinstance(part, Mapping):
        return None
    provider, model = part.get("model_provider_name"), part.get("model_name")
    if isinstance(provider, str) and isinstance(model, str) and provider and model:
        return provider, model
    return None


def config_pairs(row: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """role -> (provider, model), for the parts of one RAG config as the
    API lists it that name a model."""
    pairs: dict[str, tuple[str, str]] = {}
    for role in MODEL_ROLES:
        pair = _pair(row.get(f"{role}_config"))
        if pair is not None:
            pairs[role] = pair
    return pairs


def in_use_pairs(
    rag_rows: Iterable[Mapping[str, Any]], created: Iterable[str]
) -> dict[str, set[tuple[str, str]]]:
    """role -> the (provider, model) pairs that the project's RAG configs
    use in that role, without the configs this run created (`created`:
    their ids)."""
    own = set(created)
    used: dict[str, set[tuple[str, str]]] = {role: set() for role in MODEL_ROLES}
    for row in rag_rows:
        if str(row.get("id")) in own:
            continue
        for role, pair in config_pairs(row).items():
            used[role].add(pair)
    return used


def model_refusal(
    policy: ModelPolicy | None,
    in_use: Mapping[str, set[tuple[str, str]]],
    role: str,
    provider: str | None,
    model: str | None,
) -> str | None:
    """None when a RAG config may use this model in this role, else a
    one-line reason. Allowed: the policy allows the pair, or a RAG config of
    the project that this run did not create uses it in the same role and
    no deny rule of the policy matches it."""
    if policy is None:
        return None
    decision = policy.decide(provider, model)
    if decision.allowed:
        return None
    exempt = any(
        rule.provider == provider and rule.model == model for rule in policy.exceptions
    )
    denied = not exempt and any(
        rule.matches(provider or "", model or "") for rule in policy.deny
    )
    if not denied and (provider, model) in in_use.get(role, set()):
        return None
    return (
        f"the {role} model {provider}/{model} is outside the model policy:"
        f" {decision.why}. The documents and every search query are sent to"
        " it. Use a model that the policy allows, or one that a RAG config of"
        f" the project already uses as its {role} model"
    )


# ------------------------------------------------------------------ the document check


_JSON_ESCAPE_RE = re.compile(r'\\[nrt"/]')


def normalized(text: str) -> str:
    """Lower-case words joined by one space. JSON escapes count as spaces,
    so a text is found in a JSON or JSONL file that holds it."""
    return " ".join(_words(_JSON_ESCAPE_RE.sub(" ", text)))


def _needles(texts: Iterable[str]) -> tuple[str, ...]:
    out = []
    for text in texts:
        norm = normalized(text)
        if len(norm.split(" ")) >= MIN_TEXT_WORDS:
            out.append(norm)
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class ItemTexts:
    """The texts of the task's eval items that the document check looks
    for, in every split. item id -> its texts of `MIN_TEXT_WORDS` words or
    more, normalised. An item with no such text is not in the map."""

    inputs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    references: dict[str, tuple[str, ...]] = field(default_factory=dict)
    items: int = 0


def eval_item_texts(project_root: Path, task_id: str) -> ItemTexts:
    """Every eval item of the task, from disk: the eval inputs, and the
    task runs that carry a tag (the items of an eval whose splits select
    task runs). Read by code only; no answer carries a text from here."""
    from kiln_harness_tuner.probes import input_from_doc
    from kiln_harness_tuner.quarantine import task_dirs

    inputs: dict[str, tuple[str, ...]] = {}
    references: dict[str, tuple[str, ...]] = {}
    items = 0
    for task_dir in task_dirs(project_root, task_id):
        for _folder, doc in _children(task_dir / "eval_inputs", "eval_input.kiln"):
            item_id = str(doc.get("id"))
            items += 1
            inp = input_from_doc(doc)
            if inp is not None:
                source = (
                    strings_in(inp.structured)
                    if inp.structured is not None
                    else [inp.plaintext or ""]
                )
                if needles := _needles(source):
                    inputs[item_id] = needles
            if needles := _needles(strings_in(doc.get("reference"))):
                references[item_id] = needles
        for _folder, run in _children(task_dir / "runs", "task_run.kiln"):
            tags = [str(t) for t in run.get("tags") or []]
            if not tags or "kiln_harness_tuner_gateway" in tags:
                continue
            item_id = str(run.get("id"))
            items += 1
            if needles := _needles(strings_in(run.get("input"))):
                inputs[item_id] = needles
            output = run.get("output")
            output = output.get("output") if isinstance(output, Mapping) else output
            if needles := _needles(strings_in(output)):
                references[item_id] = needles
    return ItemTexts(inputs=inputs, references=references, items=items)


@dataclass
class DocumentFindings:
    """What the document check found. `item_documents`: document id -> how
    many eval items have their whole input in it (a BLOCK).
    `reference_documents`: the same for expected outputs (a FLAG).
    `complete`: False when the check stopped at `SCAN_MAX_BYTES`, or could
    not read the project."""

    item_documents: dict[str, int] = field(default_factory=dict)
    reference_documents: dict[str, int] = field(default_factory=dict)
    scanned: int = 0
    unread: int = 0
    complete: bool = True

    def _named(self, found: Mapping[str, int]) -> str:
        ids = sorted(found)
        more = f" and {len(ids) - FINDING_IDS} more" if len(ids) > FINDING_IDS else ""
        return ", ".join(ids[:FINDING_IDS]) + more

    @property
    def block(self) -> str | None:
        if not self.item_documents:
            return None
        return (
            f"{len(self.item_documents)} document(s) that the search can return"
            " hold the input of an eval item of this task"
            f" (document {self._named(self.item_documents)}). A search would"
            " return the item itself, and its answer when the document has"
            " it. Index a set of documents without them (`tags`), or ask the"
            " owner to remove them from the library"
        )

    @property
    def flag(self) -> str | None:
        if not self.reference_documents:
            return None
        return (
            f"{len(self.reference_documents)} document(s) that the search can"
            " return hold the whole expected output of an eval item"
            f" (document {self._named(self.reference_documents)})"
        )

    @property
    def line(self) -> str:
        """One line for a record: what was read and what was found."""
        parts = [f"document check: {self.scanned} document(s) read"]
        if self.unread:
            parts.append(f"{self.unread} with no extracted text")
        if not self.complete:
            parts.append("not complete")
        parts.append(self.block or "no eval item's input is in a document")
        if self.flag:
            parts.append(self.flag)
        return "; ".join(parts)


def _index(
    texts: Mapping[str, tuple[str, ...]],
) -> dict[tuple[str, ...], list[tuple[str, str]]]:
    """The first `MIN_TEXT_WORDS` words of each text -> (item id, text)."""
    index: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for item_id, needles in texts.items():
        for needle in needles:
            key = tuple(needle.split(" ")[:MIN_TEXT_WORDS])
            index.setdefault(key, []).append((item_id, needle))
    return index


def _held(
    words: list[str],
    padded: str,
    index: Mapping[tuple[str, ...], list[tuple[str, str]]],
    first_words: set[str],
    texts: Mapping[str, tuple[str, ...]],
) -> int:
    """How many items have EVERY one of their texts in this document."""
    hits: dict[str, set[str]] = {}
    for i in range(len(words) - MIN_TEXT_WORDS + 1):
        if words[i] not in first_words:
            continue
        for item_id, needle in index.get(tuple(words[i : i + MIN_TEXT_WORDS]), ()):
            if f" {needle} " in padded:
                hits.setdefault(item_id, set()).add(needle)
    return sum(
        1 for item_id, found in hits.items() if len(found) == len(texts[item_id])
    )


def scan_documents(
    documents: Iterable[ScopeDocument], items: ItemTexts
) -> DocumentFindings:
    """Look for each eval item's input and expected output in each
    document's extracted text. A text is "in" a document when all its words
    are there, in order, as whole words. One document is read at a time."""
    findings = DocumentFindings()
    input_index, reference_index = _index(items.inputs), _index(items.references)
    input_first = {key[0] for key in input_index}
    reference_first = {key[0] for key in reference_index}
    read = 0
    for doc in documents:
        if read + doc.text_bytes > SCAN_MAX_BYTES:
            findings.complete = False
            break
        text = doc.text()
        if text is None:
            findings.unread += 1
            continue
        read += doc.text_bytes
        findings.scanned += 1
        words = _words(_JSON_ESCAPE_RE.sub(" ", text))
        padded = " " + " ".join(words) + " "
        if n := _held(words, padded, input_index, input_first, items.inputs):
            findings.item_documents[doc.id] = n
        if n := _held(
            words, padded, reference_index, reference_first, items.references
        ):
            findings.reference_documents[doc.id] = n
    return findings


def check_documents(
    project_root: Path | None,
    task_id: str,
    extractor_id: str,
    tags: Sequence[str] | None,
) -> DocumentFindings:
    """The document check for one RAG config's documents. With no project
    path the check cannot run, and says so (`complete` False)."""
    if project_root is None:
        return DocumentFindings(complete=False)
    scope = read_scope(project_root, extractor_id, tags)
    return scan_documents(scope.documents, eval_item_texts(project_root, task_id))


# ------------------------------------------------------------------ the index run


def stream_progress(body: Any) -> dict[str, Any]:
    """The last progress record of the index route's answer. The route
    streams server-sent events, one `data: {…}` line per step; the client
    reads the stream whole, as text."""
    if isinstance(body, dict):
        return body
    last: dict[str, Any] = {}
    for line in str(body or "").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            record = json.loads(line[len("data:") :].strip())
        except ValueError:
            continue
        if isinstance(record, dict):
            last = record
    return last


def progress_errors(progress: Mapping[str, Any]) -> list[str]:
    """The error messages of a progress record, and its error counts."""
    errors = [
        str(log.get("message"))[:300]
        for log in progress.get("logs") or []
        if isinstance(log, Mapping) and log.get("level") == "error"
    ]
    for key in (
        "total_document_extracted_error_count",
        "total_document_chunked_error_count",
        "total_document_embedded_error_count",
        "total_chunks_indexed_error_count",
    ):
        count = progress.get(key)
        if isinstance(count, int) and count > 0:
            errors.append(f"{key}: {count}")
    return errors


def _count(progress: Mapping[str, Any], key: str) -> int:
    value = progress.get(key)
    return value if isinstance(value, int) else 0


def index_complete(progress: Mapping[str, Any]) -> bool:
    """Every document went through every step, and every chunk is in the
    index. An index with no chunk is not complete: a search of it returns
    nothing."""
    documents = _count(progress, "total_document_count")
    chunks = _count(progress, "total_chunk_count")
    return (
        documents > 0
        and _count(progress, "total_document_completed_count") >= documents
        and chunks > 0
        and _count(progress, "total_chunk_completed_count") >= chunks
    )


# ------------------------------------------------------------------ the lever check


def search_calls(run: Mapping[str, Any], tool_names: set[str]) -> int | None:
    """How many times one run's trace calls a tool named in `tool_names`.
    None when the run has no trace to read."""
    trace = _trace(run)
    if not trace:
        return None
    calls = 0
    for message in trace:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            fn = call.get("function")
            fn = fn if isinstance(fn, Mapping) else call
            if str(fn.get("name") or "") in tool_names:
                calls += 1
    return calls


def applied_verdict(
    runs: Mapping[str, Mapping[str, Any]], tool_names: set[str]
) -> LeverVerdict:
    """Did the new search tool reach the calls? `runs`: item id -> a stored
    train row of the arm. Applied when one row's trace calls the tool. Not
    applied when `MIN_ROWS` rows or more have a trace and none calls it.
    Unverified with fewer traces."""
    names = ", ".join(f"`{n}`" for n in sorted(tool_names)) or "(no name)"
    counts = [
        c for run in runs.values() if (c := search_calls(run, tool_names)) is not None
    ]
    called = sum(1 for c in counts if c > 0)
    if called:
        return LeverVerdict(
            "applied",
            f"rag applied: the search tool {names} is called in {called} of"
            f" {len(counts)} train row(s), {sum(counts)} call(s) in all",
        )
    if len(counts) >= MIN_ROWS:
        return LeverVerdict(
            "not_applied",
            f"lever not applied: the search tool {names} is attached, but 0 of"
            f" {len(counts)} train rows call it",
        )
    return LeverVerdict(
        "unverified",
        f"lever unverified: {len(counts)} train row(s) of the arm have a trace,"
        f" and the check needs {MIN_ROWS} to say that {names} is never called",
    )


# ------------------------------------------------------------------ one line


def describe(row: Mapping[str, Any]) -> str:
    """One line for a RAG config as the API gives it: the tool, the search
    type, the chunker, the embedding model, the reranker and the tags."""

    def part(key: str) -> Mapping[str, Any]:
        value = row.get(key)
        return value if isinstance(value, Mapping) else {}

    def props(key: str) -> Mapping[str, Any]:
        value = part(key).get("properties")
        return value if isinstance(value, Mapping) else {}

    store = part("vector_store_config")
    chunker = part("chunker_config")
    chunk = props("chunker_config")
    size = (
        f" {chunk.get('chunk_size')}/{chunk.get('chunk_overlap')}"
        if chunk.get("chunk_size") is not None
        else ""
    )
    embedding = _pair(part("embedding_config"))
    reranker = _pair(part("reranker_config"))
    tags = row.get("tags")
    return (
        f"tool `{row.get('tool_name')}` ({RAG_TOOL_PREFIX}{row.get('id')}):"
        f" search {store.get('store_type') or '?'}"
        f" top {props('vector_store_config').get('similarity_top_k', '?')},"
        f" chunker {chunker.get('chunker_type') or '?'}{size},"
        f" embedding {'/'.join(embedding) if embedding else '?'},"
        + (
            f" reranker {'/'.join(reranker)}"
            f" top {part('reranker_config').get('top_n', '?')},"
            if reranker
            else " no reranker,"
        )
        + (f" tags {', '.join(str(t) for t in tags)}" if tags else " every document")
    )


# ------------------------------------------------------------------ the run's records


def created_rag_configs(run_dir: Path | None) -> list[str]:
    """The ids of the RAG configs this run created, in creation order, from
    the gateway's registry."""
    if run_dir is None:
        return []
    path = run_dir / CREATED_FILE
    if not path.exists():
        return []
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == RAG_CONFIG_KIND:
            ids.append(str(row.get("id") or "").removeprefix(f"{RAG_CONFIG_KIND}:"))
    return list(dict.fromkeys(i for i in ids if i))


async def close_rag_configs(ctx: Ctx) -> int:
    """The run-end step for the RAG configs this run created: say which run
    configs of the task attach each one, and archive each one that NO run
    config attaches. Nothing can call the search tool of such a config. A
    config that a run config attaches is never archived, promoted or not,
    so every run config this run measured still runs. Nothing is deleted:
    Kiln has no delete for a RAG config, its parts or its index. One
    `rag_configs` event carries the records, for the report. Best-effort: a
    failure is logged, never fatal. Returns how many configs it archived."""
    from kiln_harness_tuner.kiln import KilnError
    from kiln_harness_tuner.probes import task_run_configs
    from kiln_harness_tuner.schemas import RagConfigRecord

    created = created_rag_configs(ctx.run_dir)
    if not created:
        return 0
    project = f"/api/projects/{ctx.target.project_id}"
    try:
        rows: list[dict[str, Any]] | None = await task_run_configs(ctx)
    except KilnError as exc:
        ctx.log.event("rag_configs_unread", error=repr(exc)[:200])
        rows = None
    attached: dict[str, list[str]] = {}
    for row in rows or []:
        for tool_id in rag_tool_ids(row.get("run_config_properties")):
            attached.setdefault(tool_id, []).append(str(row.get("id")))
    records: list[RagConfigRecord] = []
    archived = 0
    for rag_id in created:
        tool_id = f"{RAG_TOOL_PREFIX}{rag_id}"
        record = RagConfigRecord(
            id=rag_id, tool_id=tool_id, attached_by=attached.get(tool_id, [])
        )
        records.append(record)
        try:
            config = await ctx.kiln.call("GET", f"{project}/rag_configs/{rag_id}")
        except KilnError:
            config = None
        if isinstance(config, dict):
            record.name = config.get("name")
            record.tool_name = config.get("tool_name")
            record.summary = describe(config)
            record.archived = config.get("is_archived") is True
        # `rows is None`: the run configs could not be listed, so nothing is
        # known to be unattached, and nothing is archived.
        if rows is None or record.attached_by or record.archived:
            continue
        try:
            await ctx.kiln.call(
                "PATCH",
                f"{project}/rag_configs/{rag_id}",
                body={"is_archived": True},
            )
        except KilnError as exc:
            logger.warning("could not archive RAG config %s: %s", rag_id, exc)
            continue
        record.archived = True
        archived += 1
    line = f"RAG configs: this run created {len(records)}" + (
        f"; archived {archived} that no run config attaches" if archived else ""
    )
    ctx.log.event(
        "rag_configs",
        rag_configs=[r.model_dump(mode="json") for r in records],
        archived=archived,
        line=line,
    )
    logger.info("%s", line)
    return archived
