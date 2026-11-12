"""The gateway's own request and response models.

Every answer an agent gets is one of the response models below, built by
copying NAMED fields out of a Kiln response (`project`). A field Kiln adds
tomorrow, or any field not named here, cannot reach an agent: the models
forbid extra fields, and nothing is ever forwarded whole.

Every request is one of the request models: typed, bounded, and validated
before any Kiln call is made.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from kiln_harness_tuner.kiln_names import kiln_name

M = TypeVar("M", bound=BaseModel)


# Kiln refuses names with `.`, `,`, `:` and the like (HTTP 422). An agent's
# name is made valid here rather than refused, so no create is lost to it.
KilnName120 = Annotated[str, BeforeValidator(lambda v: kiln_name(str(v), 120))]
KilnName32 = Annotated[str, BeforeValidator(lambda v: kiln_name(str(v), 32))]


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def project(model: type[M], source: Any, **overrides: Any) -> M:
    """`model` built from the named fields of `source` only — nested models
    must be passed in `overrides`, already projected."""
    src = source if isinstance(source, dict) else {}
    values = {
        name: src.get(name)
        for name in model.model_fields
        if name in src and name not in overrides
    }
    values.update(overrides)
    return model.model_validate(values)


# ------------------------------------------------------------------ outputs

JsonScalar = str | int | float | bool | None


class Pong(Out):
    ok: bool


class Requirement(Out):
    name: str | None = None
    instruction: str | None = None
    priority: int | None = None


class TaskView(Out):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    thinking_instruction: str | None = None
    requirements: list[Requirement] = []
    input_json_schema: str | None = None
    output_json_schema: str | None = None


class RunConfigPrompt(Out):
    name: str | None = None
    prompt: str | None = None
    chain_of_thought_instructions: str | None = None


class RunConfigView(Out):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    created_at: str | None = None
    model_name: str | None = None
    model_provider_name: str | None = None
    thinking_level: str | None = None
    prompt_id: str | None = None
    structured_output_mode: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    tools: list[str] = []
    run_config_properties: dict[str, Any] = {}
    prompt: RunConfigPrompt | None = None  # the frozen prompt it runs with


class PromptView(Out):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    chain_of_thought_instructions: str | None = None
    created_at: str | None = None


class PromptText(Out):
    prompt_id: str | None = None
    prompt: str | None = None
    chain_of_thought_instructions: str | None = None


class ModelView(Out):
    provider: str
    model: str
    name: str | None = None
    thinking_levels: list[str] = []
    structured_output_mode: str | None = None  # the server's default for it


class ScoreView(Out):
    name: str | None = None
    type: str | None = None
    direction: str | None = None
    instruction: str | None = None


class EvalView(Out):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    current_config_id: str | None = None
    output_scores: list[ScoreView] = []
    train_items: int = 0


class EvalConfigView(Out):
    id: str | None = None
    name: str | None = None
    config_type: str | None = None
    model_name: str | None = None
    model_provider: str | None = None
    properties: dict[str, Any] = {}


class SpecView(Out):
    id: str | None = None
    name: str | None = None
    definition: str | None = None
    priority: int | None = None
    status: str | None = None
    tags: list[str] = []
    eval_id: str | None = None


class TrainResultRow(Out):
    """One train result: the small fields, and the path of the
    trace file the gateway wrote. The trace is never in the answer: it is in
    `trace_path`, made for `Grep` and `Read` with a line range."""

    item_id: str
    eval_run_id: str | None = None
    scored_run_id: str | None = None
    scores: dict[str, float | None] = {}
    output_preview: str | None = None  # the first OUTPUT_PREVIEW_CHARS
    created_at: str | None = None
    task_cost_usd: float | None = None
    latency_ms: float | None = None
    skipped_reason: str | None = None  # set: the run was skipped, not scored
    skipped_detail: str | None = None
    trace_path: str | None = None
    trace_summary: str | None = None  # turns, tool calls, tool errors, reply


class TrainResults(Out):
    eval_id: str
    eval_config_id: str
    run_config_id: str
    rows: list[TrainResultRow] = []
    dropped: int = 0  # rows not provably train, never shown
    total: int = 0  # train rows that match the request, on every page
    offset: int = 0
    next_offset: int | None = None  # set: call again with offset=next_offset
    stats_path: str | None = None  # _stats.md of the run config
    traces_dir: str | None = None


class Usage(Out):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
    total_llm_latency_ms: float | None = None


class TaskRunView(Out):
    id: str | None = None
    item_id: str | None = None  # the train item it ran on
    input: str | None = None
    output: str | None = None
    # The trace is written to a file, never put in the answer.
    trace_path: str | None = None
    trace_summary: str | None = None
    usage: Usage | None = None
    tags: list[str] = []
    created_at: str | None = None
    model_name: str | None = None
    model_provider: str | None = None


class TrainItem(Out):
    id: str
    eval_ids: list[str] = []
    plaintext_input: str | None = None
    structured_input: Any = None
    # A multi-turn item: `plaintext_input` is the opening user message, and a
    # simulated user writes the rest when the item is driven.
    multi_turn: bool = False


class TrainExpected(Out):
    """A TRAIN item's expected output: the eval input's
    `reference` — what a code eval compares against — and its tags.
    `clipped`: the reference was larger than the cap, and `reference` is
    then the first part of its JSON text; the whole of it is in
    `reference_path`."""

    id: str
    eval_ids: list[str] = []
    reference: Any = None
    tags: list[str] = []
    clipped: bool = False
    reference_path: str | None = None


class TrainExpectedPage(Out):
    """One page of expected outputs. `next_offset` set: more
    items follow; call again with `offset=next_offset`."""

    items: list[TrainExpected] = []
    total: int = 0
    offset: int = 0
    next_offset: int | None = None


class AnswerPart(Out):
    """The first part, or a later part, of an answer over the gateway's
    answer cap. `part` is a piece of the answer's JSON text.
    Call `get_answer_part(continuation=…, offset=next_offset)` for the next
    piece; `next_offset` null means this was the last piece."""

    truncated: bool = True
    continuation: str
    answer_chars: int
    offset: int
    next_offset: int | None = None
    part: str
    note: str = ""


class MemoryView(Out):
    id: str | None = None
    overview: str | None = None
    content: str | None = None
    tags: list[str] = []
    scope: str | None = None
    content_length: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryPage(Out):
    listings: list[MemoryView] = []
    matched: int | None = None
    remaining: int | None = None


class MemorySummary(Out):
    total: int | None = None
    scopes: list[dict[str, Any]] = []


class CodeToolView(Out):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    is_archived: bool | None = None
    tool_function_name: str | None = None
    tool_description: str | None = None
    parameters_schema: dict[str, Any] | None = None
    code: str | None = None
    timeout_seconds: int | None = None
    tool_allowlist: list[str] = []
    created_at: str | None = None
    not_trusted: bool | None = None  # true: nothing was saved


class ToolCallEntry(Out):
    tool_name: str | None = None
    arguments: dict[str, Any] = {}
    output_preview: str | None = None
    is_error: bool | None = None
    duration_ms: int | None = None


class CodeToolTest(Out):
    result: str | None = None
    error: str | None = None
    traceback: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    not_trusted: bool | None = None  # true: nothing ran
    tool_call_log: list[ToolCallEntry] = []
    duration_ms: int | None = None


class SkillView(Out):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    is_archived: bool | None = None
    created_at: str | None = None


class SkillContent(Out):
    skill_md: str | None = None
    body: str | None = None


class ToolView(Out):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    function_name: str | None = None  # the name the model sees


class ToolSetView(Out):
    type: str | None = None
    set_name: str | None = None
    tools: list[ToolView] = []
    # Why the set has no tools: an OpenEnv server that did not answer.
    note: str | None = None


class ToolDefinition(Out):
    definition: dict[str, Any] = {}


class RagPart(Out):
    """One stage of a RAG config: the extractor, chunker, embedding, vector
    store or reranker."""

    name: str | None = None
    description: str | None = None
    extractor_type: str | None = None
    chunker_type: str | None = None
    store_type: str | None = None
    output_format: str | None = None
    model_provider_name: str | None = None
    model_name: str | None = None
    top_n: int | None = None
    properties: dict[str, Any] = {}


class RagConfigView(Out):
    """A RAG config, which a `kiln_tool::rag::<id>` search tool runs."""

    id: str | None = None
    name: str | None = None
    description: str | None = None
    tool_name: str | None = None
    tool_description: str | None = None
    is_archived: bool | None = None
    # The document tags the config indexes; none means every document.
    tags: list[str] | None = None
    extractor: RagPart | None = None
    chunker: RagPart | None = None
    embedding: RagPart | None = None
    vector_store: RagPart | None = None
    reranker: RagPart | None = None


class RagSearchResult(Out):
    document_id: str | None = None
    chunk_idx: int | None = None
    chunk_text: str | None = None
    similarity: float | None = None


class DocumentView(Out):
    """A document of the project's library: what it is, not its text. Its
    text reaches an agent as the chunks a RAG search returns."""

    id: str | None = None
    name: str | None = None
    description: str | None = None
    kind: str | None = None
    tags: list[str] = []
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None


class KilnTaskToolView(Out):
    """A Kiln task attached as a tool (`kiln_task::<tool server id>`): the
    task it calls and the run config it calls it with. Never the task's runs
    or evals."""

    tool_id: str | None = None
    tool_name: str | None = None
    tool_description: str | None = None
    task: TaskView | None = None
    run_config: RunConfigView | None = None
    prompt: PromptText | None = None
    note: str | None = None


class Created(Out):
    id: str
    kind: str


class JobView(Out):
    job_id: str
    status: str | None = None
    success: int | None = None
    error: int | None = None  # items that failed
    total: int | None = None
    error_message: str | None = None  # why the job failed, when it did


class JudgeTest(Out):
    scores: dict[str, float | None] = {}
    skipped_reason: str | None = None
    skipped_detail: str | None = None
    score_range_errors: list[str] = []


class Statistics(Out):
    values: dict[str, JsonScalar | list[JsonScalar]] = {}


class ValSummary(Out):
    candidates: list[dict[str, Any]] = []
    note: str = ""


# ------------------------------------------------------------------ inputs

Id = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:\-]+$")
Tag = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_\-]+$")


class NoArgs(In):
    pass


class ById(In):
    id: str = Id


class RagSearchRequest(In):
    id: str = Id  # the RAG config id, or its `kiln_tool::rag::<id>` tool id
    query: str = Field(min_length=1, max_length=2000)


class EvalConfigRef(In):
    eval_id: str = Id
    eval_config_id: str = Id


class TrainResultsRequest(In):
    eval_id: str = Id
    run_config_id: str = Id
    item_ids: list[str] | None = Field(default=None, max_length=500)
    # A page of rows; a page also ends at the answer cap.
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class TrainItemsRequest(In):
    eval_id: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class TrainExpectedRequest(In):
    eval_id: str | None = None
    item_ids: list[str] | None = Field(default=None, max_length=200)
    # A page also ends at the answer cap, and `offset` and
    # `limit` page through `item_ids` too.
    limit: int = Field(default=10, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class AnswerPartRequest(In):
    continuation: str = Field(pattern=r"^[0-9a-f]{12}$")
    offset: int = Field(ge=0)


class MemoryListRequest(In):
    scope: str = Field(min_length=1, max_length=255)
    tags: list[str] = Field(default=[], max_length=10)
    content_match: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class MemoryIdsRequest(In):
    ids: list[str] = Field(min_length=1, max_length=100)


class MemorySummaryRequest(In):
    scope: str | None = Field(default=None, max_length=255)


class MemoryCreate(In):
    overview: str = Field(min_length=1, max_length=280)
    scope: str = Field(min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=4000)
    tags: list[str] = Field(default=[], max_length=20)


class MemoryUpdate(In):
    id: str = Id
    overview: str | None = Field(default=None, max_length=280)
    content: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=20)


class RunConfigCreate(In):
    name: KilnName120 = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    run_config_properties: dict[str, Any]
    provenance: dict[str, Any] | None = None


class RunConfigRename(In):
    id: str = Id
    name: KilnName120 = Field(min_length=1, max_length=120)


class PromptCreate(In):
    name: KilnName120 = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=100_000)
    chain_of_thought_instructions: str | None = Field(default=None, max_length=20_000)
    description: str | None = Field(default=None, max_length=2000)
    provenance: dict[str, Any] | None = Field(
        default=None,
        description=(
            "notes, derived_from_ids, origin. derived_from_ids names parent "
            "prompts: the parent run config's prompt id without the 'id::' "
            "prefix. Kiln refuses a run-config id here."
        ),
    )


class RunTaskRequest(In):
    run_config_id: str = Id
    item_id: str = Id
    tags: list[str] = Field(default=[], max_length=5)


class CodeToolCreate(In):
    name: KilnName120 = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    tool_function_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    tool_description: str = Field(min_length=1, max_length=4000)
    parameters_schema: dict[str, Any]
    code: str = Field(min_length=1, max_length=100_000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    tool_allowlist: list[str] = []
    provenance: dict[str, Any] | None = None


class CodeToolTestRequest(In):
    tool_function_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    tool_description: str = Field(default="test", max_length=4000)
    parameters_schema: dict[str, Any]
    code: str = Field(min_length=1, max_length=100_000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    tool_allowlist: list[str] = []
    params: dict[str, Any] = {}


class Archive(In):
    id: str = Id
    archived: bool = True


class SpecCreate(In):
    name: KilnName32 = Field(min_length=1, max_length=32)
    definition: str = Field(min_length=1, max_length=20_000)
    priority: Literal[0, 1, 2, 3] = 1
    status: Literal["active", "future"] = "active"
    tags: list[str] = []
    properties: dict[str, Any]
    evaluate_full_trace: bool | None = None


class EvalCreate(In):
    name: KilnName120 = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    output_scores: list[dict[str, Any]] = Field(min_length=1, max_length=10)
    evaluation_data_type: Literal["final_answer", "full_trace", "reference_answer"]
    template: str | None = None
    template_properties: dict[str, Any] | None = None


class EvalItem(In):
    input: str = Field(min_length=1, max_length=100_000)
    output: str = Field(default="", max_length=100_000)


class EvalItemsCreate(In):
    eval_id: str = Id
    split: Literal["train", "val", "test"]
    items: list[EvalItem] = Field(min_length=1, max_length=200)
    model_name: str = Field(default="kiln_harness_tuner_eval_build", max_length=120)
    model_provider: str = Field(default="kiln_harness_tuner", max_length=120)


class EvalConfigCreate(In):
    eval_id: str = Id
    name: KilnName120 = Field(min_length=1, max_length=120)
    type: str = Field(min_length=1, max_length=60)
    properties: dict[str, Any]
    model_name: str | None = None
    provider: str | None = None


class SetCurrentConfig(In):
    eval_id: str = Id
    eval_config_id: str = Id


class JudgeTestRequest(In):
    eval_id: str = Id
    properties: dict[str, Any]
    final_message: str = Field(min_length=1, max_length=100_000)
    task_input: str | None = Field(default=None, max_length=100_000)


class TrainEvalRequest(In):
    eval_id: str = Id
    run_config_id: str = Id
    item_ids: list[str] | None = Field(default=None, max_length=500)


class StatisticsRequest(In):
    operation: str = Field(min_length=1, max_length=60)
    confidence: float | None = None
    proportion: float | None = None
    n: int | None = None
    proportion_a: float | None = None
    n_a: int | None = None
    proportion_b: float | None = None
    n_b: int | None = None
    outcomes_a: list[float] | None = None
    outcomes_b: list[float] | None = None
    values_a: list[float | None] | None = None  # a null skips that pair
    values_b: list[float | None] | None = None
