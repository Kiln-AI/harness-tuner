---
status: complete
---

# Component: Smell detection and the baseline reviewer

## 1. Purpose and scope

This component finds smells in a run: the code detectors, the new loop
agent that reviews the baseline, the way a found smell reaches the planner
and the report, and the one step in the loop that runs them.

Not in this component: the smell documents themselves
([knowledge_documents.md](./knowledge_documents.md)), and which smells V1
has beyond those the detectors here name (the V1 coverage phase).

## 2. The data model

In `src/kiln_harness_tuner/schemas.py`:

```
class Detection(BaseModel):
    """One fact that code found, which is evidence of one smell."""
    smell: str            # the id of a smell document
    detector: str         # the id of the detector
    scope: Literal["harness", "train"]
    fact: str             # one sentence with its numbers, at most 300 characters
    value: float | None = None
    reference: float | None = None      # what the value was compared with
    reference_source: str = ""          # where the reference comes from
    eval_id: str | None = None          # when the fact is about one eval

class SmellFinding(BaseModel):
    """One smell that the baseline reviewer names."""
    smell: str
    evidence: str         # what it saw, at most 900 characters
    where: list[str] = [] # train item ids, tool names, "prompt", skill names; at most 12
    confidence: str       # text, at most 300 characters

class DetectionCheck(BaseModel):
    """The reviewer's reading of one code detection."""
    smell: str
    detector: str
    holds: bool | None    # None: reading could not tell
    note: str = ""

class TraceSample(BaseModel):
    eval_id: str
    item_ids: list[str]

class BaselineReviewInput(BaseModel):
    baseline: BaselineRef
    detections: list[Detection]
    samples: list[TraceSample]
    known_smells: list[str]      # the ids of every smell document

class BaselineReviewResult(BaseModel):
    findings: list[SmellFinding] = []
    detection_checks: list[DetectionCheck] = []
    notes: str = ""

class BaselineReview(BaseModel):
    """What the run keeps about one review, for the report."""
    rc_id: str
    status: Literal["done", "skipped", "failed", "off"]
    reason: str = ""
    findings: list[SmellFinding] = []
    issue_ids: list[str] = []
```

**A detection states a fact.** `fact` gives the count and the rule, for
example "The system prompt has 240,310 characters. The reference is 200,000."
It holds no word of judgement or blame. This agrees with the rule of
`analysis.py`: code computes numbers, and agents interpret them. The word
"smell" is a pointer to a document, not a verdict on this run.

**No val.** `scope` has no `val` value. A detector gets no val data, and a
`Detection` can go to an agent and to memory.

## 3. Code detectors

Package `src/kiln_harness_tuner/smells/`:

```
__init__.py        exports run_detectors, DETECTORS, detector_smells
model.py           Detector, DetectorInputs
thresholds.py      the named reference values
run.py             run_detectors, ensure_baseline_reviewed
detectors/
  __init__.py      DETECTORS: the registration
  long_prompt.py
  long_tool_description.py
  long_skill_description.py
```

### 3.1 The interface

```
@dataclass(frozen=True)
class DetectorInputs:
    rc_id: str
    properties: dict[str, Any]          # the baseline run config's properties
    texts: RcTexts                      # prompt_text.rc_texts: prompt, skills, code tools
    tool_definitions: list[dict[str, Any]]   # name, description, parameters of each attached tool
    trace_stats: list[dict[str, Any]]   # trace_files.trace_stats of the baseline's stored train runs
                                        # can be empty

@dataclass(frozen=True)
class Detector:
    id: str                             # lower case with "_", for example "long_prompt"
    smell: str                          # the smell document's id
    run: Callable[[DetectorInputs], list[Detection]]
```

- A detector is a pure function. It makes no call to Kiln and reads no
  file. `run_detectors` collects the inputs once.
- A detector returns an empty list when the smell is not seen.
- **Registration** is one line in `detectors/__init__.py`:
  `DETECTORS: tuple[Detector, ...] = (long_prompt.DETECTOR, ...)`. A new
  detector is one file and that one line.
- `detector_smells() -> dict[str, str]` gives detector id to smell id. The
  knowledge check uses it (rule `smell-detectors`).

### 3.2 Thresholds

`thresholds.py` holds each reference value as a named constant with a
comment that gives its source. The research found no measured optimum, so
each value is a default that can change.

```
LONG_PROMPT_CHARS = 200_000
# No published limit for a system prompt. A house default, set by the
# project owner: about 50,000 tokens.

LONG_TOOL_DESCRIPTION_CHARS = 4_096
# Claude Code cuts a tool description at this length when the tool loads
# up front (Claude Code documentation, read 2026-11-10).

LONG_SKILL_DESCRIPTION_CHARS = 1_024
# The maximum length of a skill description in the Agent Skills
# specification (agentskills.io, read 2026-11-10). Every skill description
# is in the context of every run.
```

### 3.3 The V1 detectors

**`long_prompt`**, smell `very-long-prompt`. It reads the length of the
system prompt text in `texts`. Above `LONG_PROMPT_CHARS` it returns one
`Detection` with `scope="harness"`, `value` the length and `reference` the
constant.

**`long_tool_description`**, smell `very-long-tool-description`. For each
attached tool in `tool_definitions`, it reads the length of the description.
Above `LONG_TOOL_DESCRIPTION_CHARS` it returns one `Detection` for that
tool, with the tool's name in `fact`.

**`long_skill_description`**, smell `very-long-skill-description`. For each
attached skill in `texts`, it reads the length of the description in the
skill's front matter. Above `LONG_SKILL_DESCRIPTION_CHARS` it returns one
`Detection` for that skill. Kiln's own limit for this field is 1,024
characters today, so this detector reports only when that limit changes or
a skill comes from another source. It costs nothing to keep.

**Not detectors: a very long tool name and a very long skill name.** Kiln
limits both names to 64 characters (`kiln_ai/utils/validation.py`), and the
model providers limit a function name to the same length. A longer name
cannot reach a run, so a detector for it would never report.

The three smell documents that these detectors name are part of V1.

### 3.4 What is not a detector

- **The detectability check and the noise check** stay as they are. They
  report on the evals, not on the design of the agent, so they are not smell
  detectors and their results are not detections.
- **Patterns in the history of a run** (for example train wins that
  validation keeps rejecting) are not smells. The planner gets that history
  today, and a playbook of the kind `eval` covers what to do.
- **The failure of an eval item** is not a smell. The loop records it as an
  issue today.

### 3.5 `run_detectors`

```
async def run_detectors(ctx: Ctx, rc_id: str) -> list[Detection]
```

1. Read the run config's properties and `rc_texts` (as the baseline audit
   does), the tool definitions, and the stored train trace stats of the
   baseline.
2. Call each detector inside `try/except Exception`. A detector that raises
   is skipped, with the event `smell_detector_failed detector=<id>`.
3. Drop a detection whose `smell` is not a smell document, with the event
   `smell_unknown`.
4. Emit the event `smell_detections rc=<rc_id> count=<n>` with the list.

If step 1 fails, the function returns an empty list, with the event
`smell_detection_skipped reason=…`.

## 4. The baseline reviewer

A new role, `baseline_review`.

### 4.1 What it does

It reviews the design of the baseline before the planner plans against it.
It reads each smell document, looks for that smell in the agent, and names
the smells it finds, with evidence. It also reads each code detection and
says whether it holds. It does not plan, build or measure, and it does not
diagnose why an eval item failed.

### 4.2 Wiring

| Place | Change |
|---|---|
| `agents/baseline_review.md` | New prompt template. It names the skill `kiln-harness-tuner-baseline-review` and the entry file `references/smells.md` of the knowledge skill. |
| `agent_files/skills/kiln-harness-tuner-baseline-review/SKILL.md` | New skill: the procedure (4.4). It ends with the `## Related` section that points to the gateway skill, as the test for every loop skill requires. |
| `schemas.py` | The models of section 2. |
| `dispatch/base.py` | `async def baseline_review(self, req: BaselineReviewInput) -> AgentResult[BaselineReviewResult]` on the protocol. |
| `dispatch/claude_cli.py` | The one-line method. `AGENT_TIMEOUTS_SECS["baseline_review"] = 1200`. Add to `READ_ONLY_ROLES`. `_dispatch_label` gives `loop` (no item id), which is correct. |
| `dispatch/codex_cli.py`, `opencode_cli.py` | Nothing: they inherit, and they read the role sets. |
| `gateway/ops.py` | `ROLE_OPS["baseline_review"]` (4.3). |
| `tests/fake_dispatcher.py` | The method. Not scripted: it returns an empty result. |
| `schemas.py` | `"baseline_review"` in `CLAUDE_CODE_SPEND_SOURCES`. |
| `ui.py`, `tui/model/words.py`, `tui/model/fold.py` | A phase noun and a start-checklist step, "Baseline review", after the opening screen. |
| `docs/agents.md` | The role count, the role table, a section for the role, the per-role model table. |
| `docs/architecture.md`, `docs/how-it-works.md` | The step in the start of a run and after a promotion. |

The role uses the default model and effort of the agent CLI (`opus`, `high`
for Claude). It runs on the agent CLI's own account. It spends nothing on
evals.

### 4.3 Gateway operations

```
ROLE_OPS["baseline_review"] = (
    *_TASK,          # get_task, get_run_config, get_prompt_text, ...
    *_EVALS,         # list_evals, get_eval, ...
    "get_train_results", "get_task_run", "get_train_expected", "list_train_items",
    "get_skill", "get_skill_content", "get_code_tool",
    "list_available_tools", "get_tool_definition",
    *_MEM_READ,
)
```

- No write operation of any kind: no memory write, no `run_task`, no
  `create_run_config`.
- It holds `get_train_results`, so it is a trace reader, and the trace
  folder is opened to it by the existing rule.
- It is not in `VAL_READERS`. Every answer it gets holds train records
  only.

### 4.4 The skill's procedure

1. Read `references/smells.md` of the knowledge skill.
2. Read the baseline: the prompt, each attached skill, each tool's name,
   description and parameters, the model and the settings.
3. Read the sampled traces to see how the agent runs: which tools it calls,
   in what order, how large each result is, and what it reads. Do not judge
   whether an answer is right.
4. Read each code detection in the input. For each, look at the thing it
   names and return a `DetectionCheck`.
5. For each smell in the list, open its document when the one-line
   description is not enough, and apply its "How to recognise it" and "How
   to confirm it".
6. Return one `SmellFinding` for each smell that the evidence shows.
   - State what was seen and where. Do not state a fix.
   - Use only the ids in `known_smells`.
   - A smell with weak evidence is returned with that said in `confidence`.
     A smell with no evidence is not returned.
7. A design issue that fits no smell goes in `notes`, in a few lines.

Hard rules in the skill: train records only; no write to Kiln or to memory;
no plan and no experiment; no diagnosis of a failed eval item; at most 15
findings.

### 4.5 The trace sample

Code chooses the sample, so that the reviewer starts from the same place
each time and the cost has a limit. The sample shows how the agent runs. It
is not chosen by whether an item passed.

```
def baseline_trace_samples(ctx, rc_id) -> list[TraceSample]
```

- For each active eval, from the baseline's stored train rows
  (`ctx.analyzer.stored_results(..., "train")`): up to
  `REVIEW_ITEMS_PER_EVAL = 4` items.
- The items are taken in the order of the train set file, so the choice is
  the same on a second run.
- At most `REVIEW_MAX_ITEMS = 20` items in total. When the evals give more,
  each eval's share is cut in turn.
- An eval with no stored train row gives an empty sample. A new baseline can
  have partial rows, because the stop rules ended its measurement early.
  The reviewer uses what there is, and it can ask the gateway for more.

## 5. The step in the loop

In `smells/run.py`:

```
async def ensure_baseline_reviewed(ctx: Ctx, baseline: BaselineRef) -> None
```

Called once at the top of each wave in `_run_loop`, where the wave reads the
baseline again (`orchestrator.py`, near line 757), before the planner is
dispatched.

```
if baseline.rc_id == ctx.reviewed_rc_id: return
ctx.reviewed_rc_id = baseline.rc_id
phase_start name="baseline_review"
try:
    ctx.detections = await run_detectors(ctx, baseline.rc_id)
    if not ctx.baseline_review:
        record BaselineReview(status="off"); return
    if find_smell_issues(baseline.rc_id) is not empty:      # a resumed lineage
        record BaselineReview(status="skipped", reason="already reviewed"); return
    result = await call_with_retries(ctx.dispatch.baseline_review, input, ...)
    emit spend source="baseline_review", lane=SUBSCRIPTION_LANE
    findings = [f for f in result.findings if f.smell is a smell document]   # else event smell_unknown
    issue_ids = [await ctx.memory.record_smell(f, baseline.rc_id) for f in findings]
    record BaselineReview(status="done", findings, issue_ids)
except ProviderQuotaError: raise
except Exception as exc:
    record BaselineReview(status="failed", reason=<short text>)
    emit baseline_review_skipped reason=...
finally:
    phase_end name="baseline_review"
```

- **One call site covers both cases.** At wave 1 the baseline is the first
  one, so the review runs before the first plan. After a promotion the next
  wave sees a new `rc_id`, so it runs again. A run that ends on the wave of
  a promotion does not pay for a review that no planner would read.
- **It never raises**, apart from `ProviderQuotaError`. A raise before wave
  1 would be fatal and would leave no report.
- "record" means: append to `ctx.baseline_reviews` and emit the event
  `baseline_review` with the object.
- `ctx.detections` is replaced, not appended to. It always describes the
  present baseline.

New fields on `Ctx`: `baseline_review: bool = False`,
`reviewed_rc_id: str | None = None`, `detections: list[Detection]`,
`baseline_reviews: list[BaselineReview]`.

### 5.1 The flag

`--no-baseline-review` in `cli.build_parser`. The CLI passes
`baseline_review=not args.no_baseline_review` to `build_ctx`. The `Ctx`
default is off, as for the other optional steps, so that existing tests do
not change. `docs/cli.md` gets the row. The code detectors have no flag:
they cost nothing.

## 6. Smell issues in memory

In `memory.py` and `memory_schema.py`, after the pattern of the baseline
audit:

```
SMELL_TAG = "smell"
def smell_tag(smell_id: str) -> str        # "smell_" + id; fits the tag pattern [A-Za-z0-9_-]
async def record_smell(self, finding: SmellFinding, rc_id: str) -> str   # returns the issue id
async def find_smell_issues(self, rc_id: str) -> list[IssueView]
```

- **Overview:** `issue: <the smell's title> — <first 120 characters of the
  evidence>`, with the open prefix.
- **Content:** the evidence, a `where:` line, a `confidence:` line, and a
  last line: "This is the smell `<id>`. Its document gives the remedies."
- **Tags:** `[issue, p1, smell, smell_<id>, audited_rc_<rc_id>]`. The
  priority is the default. The planner can weigh it from the smell's
  `What it blocks`.
- **No eval binding.** A smell issue is about the harness, not about one
  eval.
- **Dedup:** before a write, an open issue with the tags `smell`,
  `smell_<id>` and `audited_rc_<rc_id>` is looked up. If one exists, no
  second one is written.
- **A new baseline:** the issues of the earlier baseline stay as they are.
  The planner reads the tag `audited_rc_<present rc>` to know which are
  about the present baseline. A smell that the new review does not find is
  not closed by code: lifecycle writes stay with the existing rules.

Two places that treat an issue with no eval binding as a missing eval get
the same exclusion that the audit issues have:

- `setup.py` (near line 927): the binding repair and `unbound_unresolved`.
- `report.py` `read_issues_without_eval` (near line 769).

## 7. The planner

- `BatchPlanInput` gets `detections: list[Detection] = []`, filled with
  `list(ctx.detections)` in `_dispatch_plan`. The field comment says that
  they describe the present baseline.
- The planner's skill gets a short section, "Smells":
  - the detections in the input are facts about the baseline, each with the
    id of a smell;
  - the smell issues in memory are the reviewer's findings;
  - for each one, open the smell document from the planner entry file and
    read its remedies;
  - a fix for a smell is planned in the way the skill plans a baseline-audit
    issue today: a fix arm with `needs_eval: false` and the `eval_id` of an
    active eval that the change serves;
  - a smell is evidence. An experiment decides.
- The planner's rule "an issue that no eval measures is not a candidate"
  gets the exception for smell issues, beside the one for audit issues.

The analyzer does not find smells. Its template gets one sentence of a
different kind: it reads `references/analyzer.md` of the knowledge skill,
and its `findings` can name the failure-pattern playbook that fits what the
candidate's traces show.

## 8. The report

- `RunStatus` gets `detections: list[Detection]` and
  `baseline_reviews: list[BaselineReview]`.
- `RunStatusTracker` gets `_on_smell_detections` and `_on_baseline_review`.
- `report.py` gets `render_smells_appendix(status)`, appended in `_write`
  beside the other appendices, so that the agent's report and the fallback
  report both have it. Its content:
  - for each reviewed baseline: the run config, the status of the review,
    and the reason when it was not done;
  - a table of the code detections: smell, fact, reference and its source;
  - a table of the reviewer's findings: smell, evidence, confidence.
- The appendix is left out when there is nothing to show and every review
  was done.

## 9. Dependencies

- Uses: `knowledge.load` (the smell ids and titles), `prompt_text.rc_texts`,
  `gateway.trace_files.trace_stats`, `memory`, `dispatch`.
- Used by: `orchestrator._run_loop`, `orchestrator._dispatch_plan`,
  `report`, `status`, and the knowledge check (through
  `detector_smells()`).

`smells` imports `knowledge`. `knowledge` does not import `smells`.

## 10. Test plan

`tests/test_smells.py`:

- `test_long_prompt_is_silent_at_the_reference`
- `test_long_prompt_reports_above_the_reference_with_value_and_source`
- two tests of the same form for `long_tool_description`, and two for
  `long_skill_description`
- `test_a_detection_fact_has_no_judgement_word` (a short list of banned
  words, applied to every detector's output on a triggering input)
- `test_a_detector_that_raises_is_skipped_and_the_others_run`
- `test_a_detection_of_an_unknown_smell_is_dropped`
- `test_every_registered_detector_has_a_unique_id`
- `test_every_threshold_has_a_source_comment`

`tests/test_baseline_review.py`, with the fake dispatcher and the fake
Kiln:

- `test_the_review_runs_before_the_first_plan`
- `test_the_planner_input_carries_the_detections`
- `test_each_finding_becomes_one_smell_issue_with_its_tags`
- `test_a_second_review_of_the_same_baseline_writes_nothing`
- `test_the_review_runs_again_after_a_promotion`
- `test_a_failed_review_does_not_stop_the_run_and_is_in_the_report`
- `test_a_quota_error_in_the_review_still_ends_the_run`
- `test_the_flag_turns_the_review_off_and_keeps_the_detections`
- `test_a_finding_with_an_unknown_smell_is_dropped`
- `test_the_sample_is_stable_and_inside_its_limits`
- `test_a_smell_issue_is_not_listed_as_an_issue_with_no_eval`
- `test_the_report_has_the_smells_appendix`

Existing tests that pin the role sets are extended:
`tests/test_agent_templates.py` (`TEMPLATE_SKILLS`, the template for every
role), `tests/test_dispatch_claude_cli.py` (`READ_ONLY_ROLES`, the allowed
tools), `tests/test_gateway.py` (the role is served exactly its operations
and has no write), `tests/test_dispatch_codex_cli.py`,
`tests/test_dispatch_opencode_cli.py`, `tests/test_status.py` (the spend
sources), `tests/test_cli_reference.py` (the flag).
