"""The one step that finds smells in a baseline: the detectors in code, then
the baseline reviewer.

`ensure_baseline_reviewed` runs once for each baseline, at the top of a
wave. It runs the detectors (`ensure_detections`), then dispatches the
reviewer, and code writes one smell issue for each finding it validates.
Then code closes each open smell issue of an earlier baseline that the
present baseline no longer shows (`close_earlier_smell_issues`).
`run_detectors` collects the inputs once, runs each registered detector,
and keeps the detections whose smell is a smell document. Neither raises,
apart from `ProviderQuotaError`. Code computes the facts; the planner and
the baseline reviewer interpret them.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from kiln_harness_tuner.gateway.trace_files import parse_trace, trace_stats
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.knowledge import load
from kiln_harness_tuner.memory_schema import (
    AUDITED_RC_TAG_PREFIX,
    SMELL_TAG_PREFIX,
    IssueView,
    fits_tag,
)
from kiln_harness_tuner.prompt_text import SKILL_TOOL_PREFIX, rc_texts
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import (
    SUBSCRIPTION_LANE,
    BaselineRef,
    BaselineReview,
    BaselineReviewInput,
    Detection,
    DetectionCheck,
    EarlierSmellIssue,
    PlannerDetection,
    SmellFinding,
    SmellIssueCheck,
    SmellIssueClosure,
    TraceSample,
)
from kiln_harness_tuner.smells.detectors import DETECTORS
from kiln_harness_tuner.smells.model import Detector, DetectorInputs, ModelFacts

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx


# The trace sample of the baseline reviewer: the cost of the review has a
# limit, and the reviewer can ask the gateway for more.
REVIEW_ITEMS_PER_EVAL = 4
REVIEW_MAX_ITEMS = 20
# The reviewer's skill asks for at most this many findings; code keeps the
# first ones of a longer list.
REVIEW_MAX_FINDINGS = 15
REVIEW_REASON_CHARS = 300
# The earlier smell issues that one review reads again, and how much of the
# text of each one it is given.
REVIEW_MAX_EARLIER_ISSUES = 15
EARLIER_ISSUE_SUMMARY_CHARS = 1_200
# The review sits before the planner on the run's clock: one retry at most,
# and none after a session that hit its time cap (`timeout`).
REVIEW_DISPATCH_BACKOFFS: tuple[float, ...] = (30.0,)
REVIEW_TERMINAL_ERRORS = ("parse_failure", "timeout")


async def ensure_baseline_reviewed(
    ctx: Ctx,
    baseline: BaselineRef,
    *,
    wrap_up: bool = False,
    lineage: Sequence[str] = (),
) -> None:
    """Find the smells of the present baseline, once for each baseline: the
    detectors in code, then the baseline reviewer. Then close each open
    smell issue of an earlier baseline that this one no longer shows.
    `lineage`: the baselines that this one replaced; only their smell
    issues are read. At wave 1 the baseline is
    the first one; after a promotion the next wave sees a new rc id, so the
    step runs again. A run that ends on the wave of a promotion pays for no
    review that no planner would read.

    It never raises, apart from `ProviderQuotaError`: a raise before wave 1
    would end the run with no report. A review that cannot run is recorded
    as `failed`, and the run goes on with the code detections.

    No session is dispatched on a wrap-up wave (`wrap_up`): the wrap-up
    planner only merges pooled wins, so no planner after it would read the
    review. Such a review is `skipped`, and the detectors still run."""
    if ctx.reviewed_rc_id == baseline.rc_id:
        return
    ctx.reviewed_rc_id = baseline.rc_id
    not_run = None
    if not ctx.baseline_review:
        not_run = BaselineReview(
            rc_id=baseline.rc_id, status="off", reason="turned off"
        )
    elif wrap_up:
        not_run = BaselineReview(
            rc_id=baseline.rc_id, status="skipped", reason="a wrap-up wave"
        )
    earlier = await _earlier_smell_issues(ctx, baseline.rc_id, lineage)
    if not_run is not None:
        # No session runs, so no phase opens: the detectors are free reads.
        await ensure_detections(ctx, baseline)
        _record(ctx, await _closed_or_kept(ctx, not_run, earlier))
        return
    started = ctx.clock()
    ctx.log.event(
        "phase_start",
        name="baseline_review",
        label="Design review: checking the agent's design…",
    )
    try:
        review, found = await _review_or_failure(ctx, baseline, earlier)
        _record(ctx, await _closed_or_kept(ctx, review, earlier, found))
    finally:
        ctx.log.event(
            "phase_end", name="baseline_review", elapsed_secs=ctx.clock() - started
        )


async def _review_or_failure(
    ctx: Ctx, baseline: BaselineRef, earlier: Sequence[IssueView] = ()
) -> tuple[BaselineReview, frozenset[str]]:
    """The detectors, then the review; an exception other than
    `ProviderQuotaError` becomes a `failed` review."""
    try:
        await ensure_detections(ctx, baseline)
        return await _review(ctx, baseline.rc_id, earlier)
    except ProviderQuotaError:
        raise
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        failed = BaselineReview(
            rc_id=baseline.rc_id, status="failed", reason=reason[:REVIEW_REASON_CHARS]
        )
        return failed, frozenset()


async def _review(
    ctx: Ctx, rc_id: str, earlier: Sequence[IssueView] = ()
) -> tuple[BaselineReview, frozenset[str]]:
    """One review of the run config `rc_id`: skipped (a resumed lineage
    already holds its smell issues), failed (the session gave no result),
    or done, with one smell issue for each finding. A write that fails is
    logged and named in `reason`; its `issue_ids` entry is empty.
    `earlier`: the open smell issues of earlier baselines; the reviewer
    reads again the ones that need a reader. The second value is the smell
    of every finding that the session returned, before any cut."""
    known_issues = await ctx.memory.find_smell_issues(rc_id)
    if known_issues:
        skipped = BaselineReview(
            rc_id=rc_id,
            status="skipped",
            reason="already reviewed",
            issue_ids=[issue.id for issue in known_issues],
        )
        return skipped, frozenset()
    from kiln_harness_tuner.items import call_with_retries

    titles = _smell_titles(ctx)
    asked = _issues_for_the_reviewer(ctx, earlier)
    result = await call_with_retries(
        ctx.dispatch.baseline_review,
        BaselineReviewInput(
            baseline=BaselineRef(rc_id=rc_id),
            detections=list(ctx.detections),
            samples=await baseline_trace_samples(ctx, rc_id),
            known_smells=sorted(titles or ()),
            earlier_issues=asked,
        ),
        backoffs=REVIEW_DISPATCH_BACKOFFS,
        sleep=ctx.sleep,
        log=ctx.log,
        source="baseline_review",
        terminal_errors=REVIEW_TERMINAL_ERRORS,
    )
    ctx.log.event(
        "spend",
        source="baseline_review",
        lane=SUBSCRIPTION_LANE,
        tokens=result.usage.tokens,
    )
    if result.data is None:
        why = result.error.detail if result.error else (result.detail or "")
        kind = (
            f"{result.status} {result.error.error_class}"
            if result.error
            else (result.status)
        )
        failed = BaselineReview(
            rc_id=rc_id,
            status="failed",
            reason=f"the review did not run ({kind}: {why})"[:REVIEW_REASON_CHARS],
        )
        return failed, frozenset()
    findings = _valid_findings(ctx, result.data.findings, titles)
    issue_ids: list[str] = []
    unwritten: list[str] = []
    for finding in findings:
        # Each write on its own: a failed write keeps the issues already
        # written and the findings after it.
        try:
            issue_ids.append(
                await ctx.memory.record_smell(
                    finding, rc_id, title=(titles or {}).get(finding.smell)
                )
            )
        except ProviderQuotaError:
            raise
        except Exception as exc:
            issue_ids.append("")
            unwritten.append(finding.smell)
            ctx.log.event(
                "smell_issue_write_failed",
                rc=rc_id,
                smell=finding.smell,
                reason=f"{type(exc).__name__}: {exc}"[:REVIEW_REASON_CHARS],
            )
    reason = ""
    if unwritten:
        reason = (
            f"{len(unwritten)} of {len(findings)} finding(s) could not be written"
            f" as issues: {', '.join(unwritten)}"
        )[:REVIEW_REASON_CHARS]
    asked_ids = {issue.issue_id for issue in asked}
    review = BaselineReview(
        rc_id=rc_id,
        status="done",
        reason=reason,
        findings=findings,
        issue_ids=issue_ids,
        detection_checks=result.data.detection_checks,
        notes=result.data.notes,
        # A reading of an issue that was not in the input is dropped.
        issue_checks=[c for c in result.data.issue_checks if c.issue_id in asked_ids],
    )
    return review, frozenset(f.smell for f in result.data.findings)


def _valid_findings(
    ctx: Ctx, returned: list[SmellFinding], titles: dict[str, str] | None
) -> list[SmellFinding]:
    """The findings that code writes, at most `REVIEW_MAX_FINDINGS`, one
    for each smell. A finding is dropped, with `smell_unknown`, when its
    smell is not a smell document, or, when the knowledge skill cannot be
    read, when its id does not fit a memory tag. Two findings of one smell
    are merged."""
    by_smell: dict[str, SmellFinding] = {}
    for finding in returned:
        known = finding.smell in titles if titles is not None else True
        if not known or not fits_tag(finding.smell):
            ctx.log.event(
                "smell_unknown", source="baseline_review", smell=finding.smell
            )
            continue
        first = by_smell.get(finding.smell)
        by_smell[finding.smell] = finding if first is None else _merged(first, finding)
    return list(by_smell.values())[:REVIEW_MAX_FINDINGS]


def _merged(first: SmellFinding, second: SmellFinding) -> SmellFinding:
    """One finding from two of the same smell: the evidence and the
    confidence of both, and the places of both, each cut to its limit."""
    return SmellFinding(
        smell=first.smell,
        evidence=f"{first.evidence} {second.evidence}"[:900],
        where=list(dict.fromkeys([*first.where, *second.where]))[:12],
        confidence=f"{first.confidence} / {second.confidence}"[:300],
    )


def _record(ctx: Ctx, review: BaselineReview) -> None:
    """Keep the review for the report, and emit it. Every status but `done`
    also emits `baseline_review_skipped` with the reason. `flagged` is the
    smells that the review and the code detections flagged."""
    ctx.baseline_reviews.append(review)
    if review.status != "done":
        ctx.log.event(
            "baseline_review_skipped",
            rc=review.rc_id,
            status=review.status,
            reason=review.reason,
        )
    flagged = flagged_smells(review, ctx.detections)
    ctx.log.event(
        "baseline_review",
        review=review,
        flagged=flagged,
        line=review_line(review, flagged),
    )


def flagged_smells(
    review: BaselineReview, detections: Sequence[Detection]
) -> list[str]:
    """The smell ids that the review's findings and the code detections
    flag, each one time: the findings first, then the detections."""
    smells = [f.smell for f in review.findings] + [d.smell for d in detections]
    return list(dict.fromkeys(smells))


def review_line(review: BaselineReview, flagged: Sequence[str]) -> str:
    """The review's one plain line for the displays. A flagged smell is a
    possible issue, not a confirmed one. It ends with the count of the
    earlier smell issues that code closed, when there is one."""
    reason = review.reason.rstrip(".")
    closed = ""
    if review.closed_issues:
        count = len(review.closed_issues)
        noun = "smell issue" if count == 1 else "smell issues"
        closed = f" {count} earlier {noun} closed."
    if review.status == "done":
        if flagged:
            names = ", ".join(smell.replace("-", " ") for smell in flagged)
            noun = "possible issue" if len(flagged) == 1 else "possible issues"
            line = f"Design review complete: {len(flagged)} {noun} flagged ({names})"
        else:
            line = "Design review complete: nothing flagged"
        return line + (f"; {reason}." if reason else ".") + closed
    verb = "failed" if review.status == "failed" else "skipped"
    return f"Design review {verb}: {reason}." + closed


def planner_detections(
    detections: Sequence[Detection],
    reviews: Sequence[BaselineReview],
    rc_id: str | None,
) -> list[PlannerDetection]:
    """The detections of the run config `rc_id` for the planner, each with
    the reading of the last `done` review of `rc_id`, matched by smell and
    detector. Every detection is kept. A review of another run config is
    never used: after a promotion, the new baseline's detections get only
    the new baseline's review. When the review read one pair two times,
    the last reading is used, as in the report's table."""
    review = next(
        (r for r in reversed(reviews) if r.rc_id == rc_id and r.status == "done"),
        None,
    )
    checks: dict[tuple[str, str], DetectionCheck] = {
        (check.smell, check.detector): check
        for check in (review.detection_checks if review is not None else [])
    }
    planned = []
    for detection in detections:
        check = checks.get((detection.smell, detection.detector))
        planned.append(
            PlannerDetection(
                **detection.model_dump(),
                reviewer_holds=check.holds if check is not None else None,
                reviewer_note=(check.note or None) if check is not None else None,
            )
        )
    return planned


async def baseline_trace_samples(ctx: Ctx, rc_id: str) -> list[TraceSample]:
    """The train items whose stored baseline traces the reviewer starts
    from, for each active eval: up to `REVIEW_ITEMS_PER_EVAL`, and at most
    `REVIEW_MAX_ITEMS` in all. The items are the eval's stored train rows,
    sorted by id, which is the order of the train set file
    (`gateway.trainset`), so a second run chooses the same ones. While the
    total is over the limit, the largest share is cut by one, the last eval
    first. Whether an item passed plays no part. An eval whose rows cannot
    be read gives an empty sample."""
    from kiln_harness_tuner.analysis import AnalysisError
    from kiln_harness_tuner.orchestrator import _active_evals

    try:
        active = await _active_evals(ctx)
    except KilnError:
        return []
    found: list[tuple[str, list[str]]] = []
    for eval_def, eval_config_id in active:
        eval_id = str(eval_def["id"])
        try:
            rows = await ctx.analyzer.stored_results(
                eval_id, eval_config_id, rc_id, "train"
            )
        except (KilnError, AnalysisError):
            # The train read refuses held-out rows (`HeldOutLeakError`); then
            # none of that eval's rows are used.
            rows = []
        ids = {
            str(item_id)
            for run in rows
            if isinstance(run, dict)
            and (item_id := run.get("dataset_id") or run.get("eval_input_id"))
        }
        found.append((eval_id, sorted(ids)))
    shares = [min(REVIEW_ITEMS_PER_EVAL, len(ids)) for _, ids in found]
    while sum(shares) > REVIEW_MAX_ITEMS:
        largest = max(range(len(shares)), key=lambda i: (shares[i], i))
        shares[largest] -= 1
    return [
        TraceSample(eval_id=eval_id, item_ids=ids[:share])
        for (eval_id, ids), share in zip(found, shares, strict=True)
    ]


async def ensure_detections(ctx: Ctx, baseline: BaselineRef) -> None:
    """Run the detectors once for each baseline (`ensure_baseline_reviewed`
    calls it first). `ctx.detections` is replaced, never appended to: it
    always describes the present baseline. `ctx.sure_detectors` is replaced
    with it: the detectors whose answer on this baseline is sure.

    It never raises, apart from `ProviderQuotaError`: a raise before wave 1
    would end the run with no report."""
    if ctx.detected_rc_id == baseline.rc_id:
        return
    ctx.detected_rc_id = baseline.rc_id
    sure: set[str] = set()
    ctx.sure_detectors = frozenset()
    try:
        ctx.detections = await run_detectors(ctx, baseline.rc_id, sure=sure)
        ctx.sure_detectors = frozenset(sure)
    except ProviderQuotaError:
        raise
    except Exception as exc:
        ctx.detections = []
        ctx.log.event(
            "smell_detection_skipped",
            rc=baseline.rc_id,
            reason=f"{type(exc).__name__}: {exc}",
        )


async def run_detectors(
    ctx: Ctx,
    rc_id: str,
    *,
    detectors: Sequence[Detector] = DETECTORS,
    sure: set[str] | None = None,
) -> list[Detection]:
    """The detections of the run config `rc_id`, with the events
    `smell_detector_failed`, `smell_unknown`, `smell_detection_skipped` and
    `smell_detections`.

    `sure`: when given, it receives the id of each detector whose answer is
    sure: it ran with no error, and it is not blind on these inputs
    (`Detector.blind`). Only such a detector shows, with an empty answer,
    that its smell is absent.

    The stored train rows are read only when a detector reads traces (the
    component design reads them each time): no V1 detector does, and the
    detectors are meant to cost nothing."""
    try:
        inputs, unread_tools = await _collect(
            ctx, rc_id, traces=any(d.reads_traces for d in detectors)
        )
    except ProviderQuotaError:
        raise
    except Exception as exc:
        # A raise here would end the run before wave 1 with no report.
        ctx.log.event(
            "smell_detection_skipped", rc=rc_id, reason=f"{type(exc).__name__}: {exc}"
        )
        return []
    found: list[Detection] = []
    for detector in detectors:
        try:
            # A return that is not a list of detections counts as a failure
            # of that detector, like a raise.
            found += [Detection.model_validate(d) for d in detector.run(inputs)]
            if sure is not None and not (detector.blind and detector.blind(inputs)):
                sure.add(detector.id)
        except Exception as exc:
            ctx.log.event(
                "smell_detector_failed",
                detector=detector.id,
                reason=f"{type(exc).__name__}: {exc}",
            )
    known = _smell_titles(ctx)
    detections = []
    for detection in found:
        if known is not None and detection.smell not in known:
            ctx.log.event(
                "smell_unknown", detector=detection.detector, smell=detection.smell
            )
            continue
        detections.append(detection)
    ctx.log.event(
        "smell_detections",
        rc=rc_id,
        count=len(detections),
        detections=detections,
        unread_tools=unread_tools,
    )
    return detections


# ---- the smell issues of earlier baselines ----


def _tagged(issue: IssueView, prefix: str) -> str:
    """The rest of the first tag of `issue` that starts with `prefix`, or
    an empty string."""
    return next((t[len(prefix) :] for t in issue.tags if t.startswith(prefix)), "")


async def _earlier_smell_issues(
    ctx: Ctx, rc_id: str, lineage: Sequence[str]
) -> list[IssueView]:
    """The open smell issues of the baselines that `rc_id` replaced. No
    read of memory when the lineage names no earlier baseline. A read that
    fails gives none, with the event `smell_issue_check_skipped`."""
    earlier = [rc for rc in dict.fromkeys(lineage) if rc != rc_id]
    if not earlier:
        return []
    try:
        return await ctx.memory.open_smell_issues(earlier)
    except ProviderQuotaError:
        raise
    except Exception as exc:
        ctx.log.event(
            "smell_issue_check_skipped",
            rc=rc_id,
            reason=f"{type(exc).__name__}: {exc}"[:REVIEW_REASON_CHARS],
        )
        return []


def _smells_for_a_reader() -> frozenset[str] | None:
    """The smells whose document says that an agent can detect them
    (`detected_by` has `agent`). Code cannot decide alone that such a smell
    is absent. None when the knowledge skill cannot be read."""
    try:
        return frozenset(
            doc.id
            for doc in load().by_kind("smells")
            if doc.meta is not None and "agent" in doc.meta.detected_by
        )
    except Exception:  # `_smell_titles` emits the event for the same failure
        return None


def _issues_for_the_reviewer(
    ctx: Ctx, earlier: Sequence[IssueView]
) -> list[EarlierSmellIssue]:
    """The earlier smell issues that the reviewer reads again, at most
    `REVIEW_MAX_EARLIER_ISSUES`: the ones whose smell needs a reader and
    for which no detector fires on the present baseline. A detection shows
    that the smell is still there, so no reading is needed. An issue left
    out of the list stays open."""
    readers = _smells_for_a_reader() or frozenset()
    detected = {d.smell for d in ctx.detections}
    asked = []
    for issue in earlier:
        smell = _tagged(issue, SMELL_TAG_PREFIX)
        if smell not in readers or smell in detected:
            continue
        asked.append(
            EarlierSmellIssue(
                issue_id=issue.id,
                smell=smell,
                rc_id=_tagged(issue, AUDITED_RC_TAG_PREFIX),
                summary=(issue.content or issue.overview).strip()[
                    :EARLIER_ISSUE_SUMMARY_CHARS
                ],
            )
        )
    return asked[:REVIEW_MAX_EARLIER_ISSUES]


def _names(detector_ids: Sequence[str]) -> str:
    noun = "detector" if len(detector_ids) == 1 else "detectors"
    return f"the {noun} " + ", ".join(f"`{i}`" for i in detector_ids)


def earlier_issue_verdict(
    smell: str,
    *,
    detections: Sequence[Detection],
    sure_detectors: Collection[str],
    readers: Collection[str] | None,
    review: BaselineReview,
    check: SmellIssueCheck | None,
    found: Collection[str] = (),
    detectors: Sequence[Detector] = DETECTORS,
) -> tuple[Literal["code", "review"] | None, str]:
    """Whether the present baseline still shows the smell of one earlier
    smell issue. The answer is who decides that it does not (`code` or
    `review`) with the basis, or None with the reason why the issue stays
    open. Each doubt keeps the issue open.

    A smell with a detector: it is absent for code only when each of its
    detectors is sure (`sure_detectors`) and none fires. A smell that only
    code detects (`readers` does not have it) is then closed by code alone,
    with or without a review. A smell that an agent detects also needs the
    reviewer: a `done` review of this baseline whose reading of this issue
    (`check`) is `shows: false`, and no finding of the smell in that review
    (`found`: the smell of each returned finding)."""
    if readers is None:
        return None, "the smell documents cannot be read"
    own = [d.id for d in detectors if d.smell == smell]
    if any(d.smell == smell for d in detections):
        return None, f"{_names(own) if own else 'a detector'} fires on this baseline"
    unsure = [i for i in own if i not in sure_detectors]
    if unsure:
        return None, f"{_names(unsure)} could not tell on this baseline"
    silent = f"{_names(own)} did not fire" if own else ""
    if smell not in readers:
        if not own:
            return None, "no detector and no reader can tell for this smell"
        return "code", silent
    if review.status != "done":
        return None, "no design review of this baseline was done"
    if check is None:
        return None, "the design review did not read this issue again"
    if check.shows is None:
        return None, "the design review could not tell"
    if check.shows or smell in found:
        return None, "the design review still sees the smell"
    basis = "the design review read the issue again and did not see the smell"
    return "review", f"{basis}; {silent}" if silent else basis


async def _closed_or_kept(
    ctx: Ctx,
    review: BaselineReview,
    earlier: Sequence[IssueView],
    found: Collection[str] = (),
) -> BaselineReview:
    """`close_earlier_smell_issues`, which never stops the run: an error
    other than `ProviderQuotaError` keeps every issue open."""
    try:
        return await close_earlier_smell_issues(ctx, review, earlier, found)
    except ProviderQuotaError:
        raise
    except Exception as exc:
        ctx.log.event(
            "smell_issue_check_skipped",
            rc=review.rc_id,
            reason=f"{type(exc).__name__}: {exc}"[:REVIEW_REASON_CHARS],
        )
        return review


async def close_earlier_smell_issues(
    ctx: Ctx,
    review: BaselineReview,
    earlier: Sequence[IssueView],
    found: Collection[str] = (),
) -> BaselineReview:
    """Close each open smell issue of an earlier baseline (`earlier`) that
    the baseline of `review` no longer shows, and return the review with
    the closures. `earlier_issue_verdict` decides each one. Code writes
    every closure (`memory.close_smell`): the reviewer has no write.

    One event, `smell_issues_checked`, names each closed issue and each
    kept issue with its reason. A write that fails keeps that issue open."""
    if not earlier:
        return review
    readers = _smells_for_a_reader()
    checks = {check.issue_id: check for check in review.issue_checks}
    closed: list[SmellIssueClosure] = []
    kept: list[dict[str, str]] = []
    for issue in earlier:
        smell = _tagged(issue, SMELL_TAG_PREFIX)
        who, basis = earlier_issue_verdict(
            smell,
            detections=ctx.detections,
            sure_detectors=ctx.sure_detectors,
            readers=readers,
            review=review,
            check=checks.get(issue.id),
            found=found,
        )
        if who is not None:
            try:
                if not await ctx.memory.close_smell(issue.id, review.rc_id, basis):
                    who, basis = None, "the issue is closed already"
            except ProviderQuotaError:
                raise
            except Exception as exc:
                who = None
                basis = f"the write failed ({type(exc).__name__}: {exc})"
        if who is None:
            kept.append(
                {
                    "issue": issue.id,
                    "smell": smell,
                    "reason": basis[:REVIEW_REASON_CHARS],
                }
            )
            continue
        closed.append(
            SmellIssueClosure(
                issue_id=issue.id,
                smell=smell,
                found_in_rc=_tagged(issue, AUDITED_RC_TAG_PREFIX),
                closed_by=who,
                basis=basis,
            )
        )
    ctx.log.event("smell_issues_checked", rc=review.rc_id, closed=closed, kept=kept)
    return review.model_copy(update={"closed_issues": closed})


def _smell_titles(ctx: Ctx) -> dict[str, str] | None:
    """The id and the title of each smell document, or None when the
    knowledge skill cannot be loaded (the detections and the findings are
    then kept unchecked)."""
    try:
        return {
            doc.id: doc.meta.title if doc.meta is not None else doc.id
            for doc in load().by_kind("smells")
        }
    except Exception as exc:  # KnowledgeError, or an OSError while reading a document
        ctx.log.event("knowledge_unreadable", reason=str(exc))
        return None


async def _collect(
    ctx: Ctx, rc_id: str, *, traces: bool
) -> tuple[DetectorInputs, list[str]]:
    """The inputs of every detector, and the attached tools whose definition
    could not be read. Raises when the run config cannot be read."""
    from kiln_harness_tuner.probes import task_run_configs

    listing = await task_run_configs(ctx)
    row = next((r for r in listing if str(r.get("id")) == rc_id), None)
    texts = (await rc_texts(ctx, [rc_id], listing=listing))[rc_id]
    if row is None or not texts.found:
        raise LookupError(f"run config {rc_id} is not listed")
    properties = row.get("run_config_properties")
    properties = properties if isinstance(properties, dict) else {}
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    definitions: list[dict[str, Any]] = []
    unread: list[str] = []
    for tool_id in texts.tool_ids:
        if tool_id.startswith(SKILL_TOOL_PREFIX):
            # A skill is read through `texts.skills`; Kiln's definition
            # route does not resolve skill ids.
            continue
        try:
            response = await ctx.kiln.call("GET", f"{base}/tools/{tool_id}/definition")
        except KilnError:
            response = None
        definition = _definition(tool_id, response)
        if definition is None:
            unread.append(tool_id)
        else:
            definitions.append(definition)
    return (
        DetectorInputs(
            rc_id=rc_id,
            properties=properties,
            texts=texts,
            tool_definitions=definitions,
            trace_stats=await _train_trace_stats(ctx, rc_id) if traces else [],
            model=_model_facts(ctx, properties),
            run_date=ctx.run_started_at.date() if ctx.run_started_at else None,
            unread_tools=tuple(unread),
        ),
        unread,
    )


def _model_facts(ctx: Ctx, properties: dict[str, Any]) -> ModelFacts | None:
    """The baseline's model, from its properties and the run's model
    catalogue. No call to Kiln: the catalogue is the snapshot of the start
    of the run. None when the properties do not name both a provider and a model."""
    provider = properties.get("model_provider_name")
    model = properties.get("model_name")
    if not (
        isinstance(provider, str) and provider and isinstance(model, str) and model
    ):
        return None
    row = ctx.model_catalog.model(model) if ctx.model_catalog else None
    if row is None:
        return ModelFacts(provider, model, model, None)
    return ModelFacts(provider, model, row.display_name, _iso_date(row.release_date))


def _iso_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _definition(tool_id: str, row: Any) -> dict[str, Any] | None:
    """One tool definition as `tool_id`, `name`, `description` and
    `parameters`. The route answers with `function_name` at the top level;
    the OpenAI form (`{"function": {...}}`) is read too."""
    if not isinstance(row, dict):
        return None
    function = row.get("function") if isinstance(row.get("function"), dict) else row
    return {
        "tool_id": tool_id,
        "name": function.get("function_name") or function.get("name") or tool_id,
        "description": function.get("description"),
        "parameters": function.get("parameters"),
    }


async def _train_trace_stats(ctx: Ctx, rc_id: str) -> list[dict[str, Any]]:
    """The trace stats of each stored train run of the baseline, on every
    active eval. An eval whose rows cannot be read gives none; the train
    read refuses held-out rows (`HeldOutLeakError`), and then none of that
    eval's rows are used."""
    from kiln_harness_tuner.analysis import AnalysisError
    from kiln_harness_tuner.orchestrator import _active_evals

    try:
        active = await _active_evals(ctx)
    except KilnError:
        return []
    stats: list[dict[str, Any]] = []
    for eval_def, eval_config_id in active:
        eval_id = str(eval_def["id"])
        try:
            rows = await ctx.analyzer.stored_results(
                eval_id, eval_config_id, rc_id, "train"
            )
        except (KilnError, AnalysisError):
            continue
        for run in rows:
            if not isinstance(run, dict):
                continue
            messages, _tools = parse_trace(run.get("task_run_trace"))
            if not messages:
                continue
            stats.append(
                {
                    "eval_id": eval_id,
                    "item_id": run.get("dataset_id") or run.get("eval_input_id"),
                    **trace_stats(messages, run.get("task_run_usage")),
                }
            )
    return stats
