---
status: complete
---

# Phase 7: The baseline reviewer

## Overview

This phase adds the loop agent `baseline_review`, end to end. Once for each
baseline (at wave 1, and on the wave after each promotion), code runs the
smell detectors, then dispatches one read-only session that reads the smell
documents, the baseline's harness and a fixed sample of its train traces.
The session names the smells it finds, with evidence, and says whether each
code detection holds. Code validates each smell id, writes one smell issue
for each finding, and records the review for the run report.

The step never raises, apart from `ProviderQuotaError`. It never reads val.
The role gets read-only gateway operations and no memory write. The skill
tells the reviewer not to diagnose a failed eval item and not to judge
whether an answer is right.

Phase 6 left three things for this phase:

- `ensure_detections` is called first inside `ensure_baseline_reviewed`,
  and the orchestrator calls `ensure_baseline_reviewed` in its place.
- The smells appendix gets the review status and the reviewer's findings.
- The planner skill's "Smells" section gets the smell issues in memory.

The documents `docs/agents.md`, `docs/architecture.md` and
`docs/how-it-works.md` are phase 8. `docs/cli.md` gets the flag row now,
because `tests/test_cli_reference.py` holds it to the parser.

## Steps

1. **`schemas.py`.** The models of component design section 2:
   `SmellFinding` (`evidence` at most 900 characters, `where` at most 12
   entries, `confidence` at most 300), `DetectionCheck`, `TraceSample`,
   `BaselineReviewInput`, `BaselineReviewResult`, `BaselineReview`.
   `BaselineReview` also keeps `detection_checks` and `notes`, so that the
   report can show the reviewer's reading of each detection.
   `"baseline_review"` joins `CLAUDE_CODE_SPEND_SOURCES`.
   `RunStatus.baseline_reviews: list[BaselineReview] = []`.

2. **`memory_schema.py`.** `SMELL_TAG = "smell"`,
   `SMELL_TAG_PREFIX = "smell_"`, `smell_tag(smell_id) -> str`.

3. **`memory.py`.**

   ```
   async def find_smell_issues(self, rc_id: str) -> list[IssueView]
   async def record_smell(self, finding: SmellFinding, rc_id: str, *, title: str | None = None) -> str
   ```

   `find_smell_issues` lists `[issue, smell, audited_rc_<rc>]` in the task
   scope, in any state, hydrated. `record_smell` first looks up an open issue
   with `[issue, smell, smell_<id>, audited_rc_<rc>]` and returns its id
   when one exists. Else it posts: overview `issue: <title> — <first 120
   characters of the evidence>` with the open prefix; content: the evidence,
   a `where:` line, a `confidence:` line, and "This is the smell `<id>`. Its
   document gives the remedies."; tags `[issue, p1, smell, smell_<id>,
   audited_rc_<rc>]`. `title` is the smell document's title, passed by the
   caller so that `memory` does not import `knowledge`; the id is used when
   it is None.

4. **`setup.py` and `report.py`.** The binding repair, `unbound_unresolved`
   and `read_issues_without_eval` skip an issue tagged `smell`, beside the
   two audit tags.

5. **`smells/run.py`.**

   ```
   REVIEW_ITEMS_PER_EVAL = 4
   REVIEW_MAX_ITEMS = 20
   REVIEW_MAX_FINDINGS = 15

   async def baseline_trace_samples(ctx: Ctx, rc_id: str) -> list[TraceSample]
   async def ensure_baseline_reviewed(ctx: Ctx, baseline: BaselineRef) -> None
   ```

   - `baseline_trace_samples`: for each active eval, the item ids of the
     baseline's stored train rows, sorted (the order of the train set
     file), up to 4 for each eval. While the total is over 20, the largest
     share is cut by one, the last eval first. An eval whose rows cannot be
     read gives an empty sample.
   - `ensure_baseline_reviewed` follows section 5 of the component design:
     the `reviewed_rc_id` guard, `phase_start name="baseline_review"`,
     `ensure_detections` first, then off / skipped (smell issues of this rc
     already in memory) / the dispatch through `call_with_retries` (source
     `baseline_review`), the `spend` event, the findings whose smell is not
     a smell document dropped with `smell_unknown`, at most 15 findings, one
     `record_smell` for each. A result with no data is `failed`. Any other
     exception is `failed`, with `baseline_review_skipped`. Every status but
     `done` emits `baseline_review_skipped` with the reason.
     `ProviderQuotaError` is raised. `phase_end` in `finally`.
   - "record": append to `ctx.baseline_reviews` and emit
     `baseline_review review=<BaselineReview> line=<one plain line>`.
   - `_smell_ids` becomes `_smell_titles` (id to title, or None), used by
     the detectors and the review.

6. **`orchestrator.py`.** `Ctx.baseline_review: bool = False`,
   `Ctx.reviewed_rc_id`, `Ctx.baseline_reviews`. `_run_loop` calls
   `ensure_baseline_reviewed` where it called `ensure_detections`.

7. **Dispatch.** `dispatch/base.py`: `baseline_review` on the protocol.
   `dispatch/claude_cli.py`: the one-line method,
   `AGENT_TIMEOUTS_SECS["baseline_review"] = 20 * 60`, the role in
   `READ_ONLY_ROLES`. `tests/fake_dispatcher.py`: the method; an empty
   result when it was never scripted.

8. **`gateway/ops.py`.** `ROLE_OPS["baseline_review"]` as in component
   design section 4.3. The gateway skill's role table gets the row, and the
   list of trace readers gets the role.

9. **Agent files.**
   - `agents/baseline_review.md`: names the skill
     `kiln-harness-tuner-baseline-review` and `references/smells.md` of the
     knowledge skill. Train only, no writes, no diagnosis of a failed item.
   - `agent_files/skills/kiln-harness-tuner-baseline-review/SKILL.md`: the
     procedure of section 4.4, the hard rules, the output, the gateway rule
     verbatim, and `## Related` with the gateway skill.
   - The planner skill: the "Smells" section names the smell issues in
     memory (tags `smell`, `smell_<id>`, `audited_rc_<rc>`), and the fix
     arm is planned on that issue. The hard rule "an issue that no eval
     measures is not a candidate" gets the exception for smell issues and
     the two audit issues.
   - The report writer's template: code appends the smells appendix with
     the review.

10. **`report.py`.** `render_smells_appendix` shows each baseline that has
    a detection or a review, in order: a status line (done with the count
    of findings; skipped, failed or off with the reason), the detections
    table (with the reviewer's reading of each, when there is one), the
    findings table (smell, evidence, where, confidence, issue) and the
    reviewer's notes. It is left out when there is nothing to show and
    every review was done.

11. **`status.py`.** `_on_baseline_review` appends a valid `BaselineReview`.

12. **CLI.** `--no-baseline-review` in `build_parser`; `build_ctx` takes
    `baseline_review: bool = True`; `main` passes
    `not args.no_baseline_review`. `docs/cli.md` gets the row.

13. **Displays.** `ui.py`: the phase noun "baseline review" and the
    `baseline_review` event in `SETUP_LINE_EVENTS`. `tui/model/words.py`:
    `REVIEW_STEP = "Baseline review"` and its activity. `tui/model/fold.py`:
    the step is busy on `phase_start name="baseline_review"` and is set by
    the `baseline_review` event while the run is starting; at a later wave
    the event's line goes to the log.

## Tests

`tests/test_baseline_review.py`, with the fake dispatcher and the fake Kiln:

- `test_the_review_runs_before_the_first_plan`: the dispatch comes after
  the detections and before the first planning phase, with the detections,
  the samples and the known smells.
- `test_the_planner_input_carries_the_detections`
- `test_each_finding_becomes_one_smell_issue_with_its_tags`: overview,
  content and tags.
- `test_a_second_review_of_the_same_baseline_writes_nothing`: a resumed
  lineage is `skipped`; the dedup in `record_smell`.
- `test_the_review_runs_again_after_a_promotion`
- `test_a_failed_review_does_not_stop_the_run_and_is_in_the_report`: a
  failed dispatch and a raise.
- `test_a_quota_error_in_the_review_still_ends_the_run`: exit
  `quota_exhausted` with a report.
- `test_the_flag_turns_the_review_off_and_keeps_the_detections`, and the
  parser and `build_ctx`.
- `test_a_finding_with_an_unknown_smell_is_dropped`
- `test_at_most_fifteen_findings_are_kept`
- `test_the_sample_is_stable_and_inside_its_limits`
- `test_a_smell_issue_is_not_listed_as_an_issue_with_no_eval`: report and
  setup.
- `test_the_report_has_the_smells_appendix`: status, findings, the
  reviewer's reading of a detection; left out when nothing to show.
- `test_the_tracker_keeps_each_review`
- `test_the_tui_step_and_the_log_line`

Extended tests: `tests/test_agent_templates.py` (`TEMPLATE_SKILLS`),
`tests/test_dispatch_claude_cli.py` (`READ_ONLY_ROLES`, the allowed tools,
the timeout), `tests/test_gateway.py` (the role is served its operations and
has no write), `tests/test_dispatch_codex_cli.py`,
`tests/test_dispatch_opencode_cli.py` (the role is read-only),
`tests/test_status.py` (the spend source), `tests/test_smells.py` (the
appendix with no review).
