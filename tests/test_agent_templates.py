"""Template ↔ skill coherence.

The dispatcher renders `agents/<agent>.md` as the prompt head; each
skill-backed template tells the agent to load a `kiln-harness-tuner-*` skill that must
exist in the package's `agent_files/skills/` with a matching frontmatter `name:`
(repo convention: kebab-case directory == `name:`). These tests freeze that
cross-file contract — a renamed or missing skill breaks dispatch silently
otherwise (the subagent would just fail to load its procedure).
"""

import re
from pathlib import Path

import pytest
from conftest import make_tracker

from kiln_harness_tuner.dispatch.claude_cli import (
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_TEMPLATES_DIR,
    NO_TOOL_ROLES,
)
from kiln_harness_tuner.install_agents import source_dir
from kiln_harness_tuner.memory_schema import (
    baseline_rc_tag,
    is_rejected_at_val,
    rc_ids_from_tags,
)
from kiln_harness_tuner.report import render_spend_header
from kiln_harness_tuner.schemas import BaselineRef, BatchPlanInput

# The agent files the package ships, as `install_agents` reads them: what every
# install copies into an agent CLI's layout.
AGENT_FILES = source_dir().resolve()
SKILLS_DIR = AGENT_FILES / "skills"

# eval_build is deliberately absent: the eval-build boundary keeps it
# skill-less.
# So is describe_eval: a two-read session too small to earn one.
TEMPLATE_SKILLS = {
    "batch_plan": "kiln-harness-tuner-batch-selection",
    "create_experiment_rc": "kiln-harness-tuner-experiment-build",
    "analyze_experiment_results": "kiln-harness-tuner-experiment-analysis",
    "promote_best": "kiln-harness-tuner-promote",
    "report_writer": "kiln-harness-tuner-report",
    "probe": "kiln-harness-tuner-probe",
}


# The stamp code writes onto every passed-over candidate's experiment
# record; both doctrine skills teach planners to detect it.
REJECTION_STAMP = "— REJECTED AT VAL"

# The spend appendix's code-rendered header, as the skills and
# the report_writer template quote it.
SPEND_HEADER_SHAPE = "budget <N> min · elapsed <M> min · exit <reason>"


def _template_text(agent: str) -> str:
    return (DEFAULT_TEMPLATES_DIR / f"{agent}.md").read_text()


def _normalized_template(agent: str) -> str:
    return " ".join(_template_text(agent).split())


def _normalized_doc(path: Path) -> str:
    """Any agent-loadable doc with runs of whitespace collapsed — doctrine
    freezes must survive markdown re-wrapping (pattern from `538e025a`)."""
    return " ".join(path.read_text().split())


def _normalized_skill(skill: str) -> str:
    return _normalized_doc(SKILLS_DIR / skill / "SKILL.md")


def _frontmatter_name(skill_dir: Path) -> str | None:
    text = (skill_dir / "SKILL.md").read_text()
    match = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def test_every_dispatched_agent_has_a_template():
    # `_render_prompt` reads `agents/<agent>.md` — a missing one is a
    # FileNotFoundError mid-run, at the first dispatch of that role.
    # `preflight` renders its prompt inline and has no template by design.
    for agent in set(DEFAULT_ALLOWED_TOOLS) - {"preflight"}:
        assert (DEFAULT_TEMPLATES_DIR / f"{agent}.md").is_file(), (
            f"no agents/{agent}.md template"
        )


def test_describe_eval_template_pins_its_boundary():
    # It reads the CURRENT config (superseded
    # configs are not the criterion), states the problem rather than the
    # eval's name, and writes nothing.
    text = _template_text("describe_eval")
    normalized = " ".join(text.split())
    assert "current_config_id" in normalized
    assert "stating the **problem**" in normalized
    assert "no memory writes" in normalized


def test_templates_name_their_skills():
    for agent, skill in TEMPLATE_SKILLS.items():
        assert f"`{skill}`" in _template_text(agent), (
            f"{agent}.md does not name its skill {skill}"
        )


def test_skill_dirs_exist_with_matching_names():
    for skill in TEMPLATE_SKILLS.values():
        skill_dir = SKILLS_DIR / skill
        assert (skill_dir / "SKILL.md").is_file(), f"missing skill {skill}"
        assert _frontmatter_name(skill_dir) == skill, (
            f"{skill}/SKILL.md frontmatter name does not match its directory"
        )


def test_merging_reference_exists():
    # One file, loaded by the build agent for MergeWork and linked from the
    # analysis skill.
    merging = (
        SKILLS_DIR / "kiln-harness-tuner-experiment-build" / "references" / "merging.md"
    )
    assert merging.is_file()
    build_skill = (
        SKILLS_DIR / "kiln-harness-tuner-experiment-build" / "SKILL.md"
    ).read_text()
    assert "references/merging.md" in build_skill
    analysis_skill = (
        SKILLS_DIR / "kiln-harness-tuner-experiment-analysis" / "SKILL.md"
    ).read_text()
    assert "references/merging.md" in analysis_skill


def test_head_to_head_field_documented_on_both_work_types():
    # The schema carries `head_to_head_group` on both work types; the two
    # guidance files that teach the head-to-head rule must keep saying so — a
    # skill edit that drops the note would silently let planners encode merge
    # exclusivity in prose.
    merging = (
        SKILLS_DIR / "kiln-harness-tuner-experiment-build" / "references" / "merging.md"
    ).read_text()
    assert "both FixWork and MergeWork" in merging
    planner = (
        SKILLS_DIR / "kiln-harness-tuner-batch-selection" / "SKILL.md"
    ).read_text()
    assert "Groups may span item types" in planner


def test_loop_exit_doctrine_documented_in_planner_skill():
    # A planner that composes a full wave in-session and then returns
    # loop_exit conflates the exit channel with "the run should end after my
    # wave", and the composed items are dropped. The schema rejects items +
    # loop_exit; the planner skill must keep teaching the rule (and its
    # side-effect half: no memory writes for work not returned for execution)
    # — a skill edit dropping either clause would silently re-open the gap.
    # Whitespace-normalized: markdown re-wrapping must not break the freeze.
    normalized = _normalized_skill("kiln-harness-tuner-batch-selection")
    assert "Don't compose work you won't execute" in normalized
    assert "nothing worth doing NOW" in normalized
    assert "not returning for execution" in normalized
    # OPEN: records stay orchestrator-only (Hard rules) — the side-effect
    # clause must not read as permission for the planner to write them.
    assert "never yours to write" in normalized


def test_promote_through_hole_exception_documented():
    # A candidate whose designed-to-move eval never measured must not be
    # promoted on discretion. The three conditions are the contract — freeze
    # each one, and freeze the absence of the phrasing that made them optional.
    normalized = _normalized_skill("kiln-harness-tuner-promote")
    assert "usually disqualifies" not in normalized
    # The trigger reuses the analysis skill's term rather than minting a
    # synonym, stays at `(eval, score)` pair granularity like the rows and
    # the targeting it talks about, and pins which pair "primary" means on a
    # multi-target item.
    assert "primary designed-to-move pair" in normalized
    assert "first/highest-priority one" in normalized
    assert "never whichever happened to measure" in normalized
    assert "disqualifies that candidate this round unless all three" in normalized
    assert "**train** evidence on that pair is overwhelming AND statistically" in (
        normalized
    )
    # Condition 2 is anchored to the gate above — the loop has no
    # per-eval tolerance number for an agent to look up.
    assert "no significant regression the gate above would reject" in normalized
    assert "discloses the hole and states that the promotion rests on train" in (
        normalized
    )
    assert "Any one absent ⇒ the hole disqualifies" in normalized
    # Guard holes keep the softer treatment — the contract is scoped, and a
    # hole on any guard must not start disqualifying candidates.
    assert "**non-target (guard) pairs** are not disqualifying" in normalized


def test_promote_skill_gives_a_retrieval_path_for_the_train_evidence():
    # Condition 1 is unactionable unless the agent knows it may read the
    # candidate's experiment record and how — the skill otherwise reads as
    # scoped to the `baseline` record plus a hard no-writes rule.
    normalized = _normalized_skill("kiln-harness-tuner-promote")
    assert "memory_get(ids=[experiment_memory_id])" in normalized
    assert "Reading experiment records is allowed" in normalized
    # …and the record may not carry the table: content is clamped from the
    # middle at 4,000 chars. Unreadable train evidence must fail condition 1,
    # not get inferred from the overview's headline.
    assert "condition 1 is unmet" in normalized
    assert "Never infer it from the overview's headline numbers" in normalized


def test_promote_skill_does_not_claim_losers_are_unwritten():
    # Code stamps the loser's own experiment record, and a val-rejected win is
    # not re-merged unchanged. Both the Hard rule and the OPEN-escalation
    # paragraph (which runs the very same zero-winner path) must say so.
    normalized = _normalized_skill("kiln-harness-tuner-promote")
    assert "Losers get no writes anywhere" not in normalized
    assert "Losers get no writes **from you**" in normalized
    assert f"experiment record `{REJECTION_STAMP} <MM-DD>`" in normalized
    # Narrow reading: still merge-eligible, just never re-submitted as-is.
    assert "stay merge-eligible as ingredients, never as a re-submission" in normalized
    # The pointer names both halves of the planner rule: the ingredient/
    # re-submission line (Merge timing) is what binds mid-run merges.
    assert "Merge timing + the val-gate carve-out" in normalized
    # OPEN-escalation no-winner: a HOLD, not a loss — code stamps
    # HELD naming the constraint, and the SAME rc is resubmitted unchanged
    # once a human answers; REJECTED AT VAL stays the numbers-loss stamp.
    assert "pooled and merge-eligible while the question gets answered" not in (
        normalized
    )
    assert f"code stamps **every** candidate `{REJECTION_STAMP}` on this path" not in (
        normalized
    )
    assert (
        "code stamps every candidate `— HELD (constraint <id>) <MM-DD>`" in normalized
    )
    assert "resubmits the SAME rc unchanged (`FixWork.existing_rc_id`" in normalized
    assert "put the constraint's memory id in **`blocked_on`**" in normalized


def test_val_rejection_carve_out_documented_in_planner_skill():
    # The rule "never loop_exit while unpromoted wins sit pooled"
    # otherwise forces a wrap-up wave to re-merge a win the val gate just
    # rejected, unchanged. The carve-out is scoped to exactly that — holding
    # `loop_exit` open and re-submitting as-is — and must not grow into a
    # blanket ban that empties the merge pool (every gated win is stamped).
    normalized = _normalized_skill("kiln-harness-tuner-batch-selection")
    assert "failed a val gate** does not count as an unpromoted win" in normalized
    assert "never holds `loop_exit` open" in normalized
    assert "never spend a wave re-submitting it unchanged" in normalized
    assert "that rc alone, or the identical merge that just lost" in normalized
    assert "remains an eligible **ingredient** of a genuinely different" in normalized
    assert f"the overview carries `{REJECTION_STAMP} <MM-DD>` and no `— PROMOTED`" in (
        normalized
    )
    # The stamp is never removed and names no run — what it can
    # and cannot tell a planner has to be said, or the bar reads as forever.
    assert "never removed and names no run" in normalized
    assert "not as a dead lever, and not as a this-run-only fact" in normalized


def test_merge_eligible_wins_admit_val_rejected_rcs_as_ingredients_only():
    # The carve-out binds where `MergeWork.wins` is assembled, but as the
    # narrow rule: a stamped win is an ingredient, never a re-submission.
    # Excluding it outright would retire the merge lane — every gated
    # `promotion_candidate` win is stamped in the wave it is measured.
    normalized = _normalized_skill("kiln-harness-tuner-batch-selection")
    assert f"passed over — `{REJECTION_STAMP}`, no `— PROMOTED` — is still an" in (
        normalized
    )
    assert "eligible **ingredient**" in normalized
    assert "what it may never be is a **re-submission**" in normalized
    assert "never re-propose that rc alone" in normalized
    assert "never re-propose the same combination that already lost" in normalized
    # Step 1 is where a planner first builds its pool, 110 lines earlier — it
    # must point at the rule, and must not summarize it as an eligibility
    # verdict (the stamp decides reuse SHAPE, not whether the win is in).
    assert "suffixes decide how a win may be reused: see Merge timing" in normalized
    # `MergeWork.wins` needs the rc id from the record's head lines, which an
    # append can clamp away — no guessing an rc when they are gone.
    assert "the rc id is not recoverable from the record" in normalized


def test_rejection_stamp_is_in_the_memory_vocabulary():
    # `memory` is the single source of truth for the lifecycle vocabulary a
    # planner reads overviews against; an unlisted suffix reads as noise, and
    # its merge-eligible definition is the one planners inherit.
    normalized = _normalized_skill("memory")
    assert f"`{REJECTION_STAMP} <date>` appended when a val gate compared" in normalized
    assert "never removed" in normalized
    assert "stays eligible as a merge ingredient" in normalized
    assert f"a `{REJECTION_STAMP}` one is an ingredient only" in normalized


def test_report_skill_states_the_narrow_rule_where_it_names_pooled_wins():
    # The report is the human-facing account of what a run left behind. Its
    # three pooled-wins passages must not over-claim; the freeze
    # keeps both halves — stamped rcs return only inside a different
    # combination, and a `promotion_skipped` wave stamps nothing at all
    # (promote hard-fails before `_record_rejections`).
    normalized = _normalized_skill("kiln-harness-tuner-report")
    assert "when the gate returned a decision, every candidate it passed over" in (
        normalized
    )
    assert "a `promotion_skipped` wave stamps nothing" in normalized
    assert f"any stamped `{REJECTION_STAMP}` only as ingredients" in normalized
    assert "say which are val-rejected: ingredients for a different" in normalized


def test_report_skill_separates_cumulative_from_run_scoped_counts():
    # A report once presented hole/recurrence counts read out of memory
    # records as this run's census. Memory outlives the run — the two
    # populations overlap, so quoting one as the other (or summing them)
    # inflates whatever the reader is being told about this run.
    normalized = _normalized_skill("kiln-harness-tuner-report")
    # The divide is the RECORD's lifetime, not the medium: this run's own
    # experiment records live in memory too, and a rule phrased as
    # "memory = cumulative" would have the agent discount them.
    assert "the divide is the record, not the medium" in normalized
    assert "records that **outlive the run** (`lesson` / `issue`" in normalized
    assert "are **cumulative across runs**: label them as such" in normalized
    assert "never add it to a run-scoped count" in normalized
    # The run-scoped side must be honest about what the schema has: there is
    # no hole counter on RunStatus (`eval_job_errors`
    # names no field), so the doctrine says so and points at what does carry
    # them — a skill pointing at a phantom field is worse than the prose it
    # replaces. Both sources, or the agent under-counts the run by whichever
    # one it skips.
    assert "`RunStatus` has **no hole counter**" in normalized
    assert "which never reaches you" in normalized
    assert "the analyzers' `findings` prose on each item's `outcome`" in normalized
    assert "**this run's own `experiment` records** — run-scoped even though" in (
        normalized
    )
    assert "Count by reading both, de-duplicate across them" in normalized
    # …and the three populations stay three: whole items that died and
    # aborted waves are not holes.
    assert "`outcome.status` is `failed_infra`" in normalized
    assert "separate populations with their own counts" in normalized


def test_report_skill_points_at_the_code_stated_run_clock():
    # The header exists so the run's budget and clock are code's, not prose.
    # A skill that lost the no-second-version instruction would put an
    # agent-computed elapsed in the same report as the code-computed one.
    normalized = _normalized_skill("kiln-harness-tuner-report")
    assert f"`{SPEND_HEADER_SHAPE}` header" in normalized
    assert "Code states the run's budget, elapsed and exit" in normalized
    assert "never compute a second version of a number code already states" in (
        normalized
    )
    assert "no elapsed of your own summed out of `phase_segments`" in normalized
    # The prohibition is on DISAGREEING, not on naming: the skill's own
    # template headline restates exit + spend, and an absolute rule would
    # have the agent drop those fields to obey the newer instruction.
    assert "quoting the appendix's own figures is not a restatement" in normalized


def test_report_skill_field_map_carries_the_run_clock():
    # The field map must document the run-level clock fields. It tells the
    # agent that `phase_segments`/`phase_secs` are where every time sentence
    # comes from. Under the "source every claim by naming a field" rule, a map
    # without the clock leaves exactly one way to write "the run took N
    # minutes": summing the segments — recomputing the number the header
    # exists to own.
    normalized = _normalized_skill("kiln-harness-tuner-report")
    assert "The run's own clock: `elapsed_secs`" in normalized
    assert "`timeout_mins` (its budget, `None` if it had none)" in normalized
    assert "meaningful only when `time_remaining_known`" in normalized
    assert "quote that header, never a total of your own" in normalized
    assert "explain the **breakdown**, and are never summed into the run's total" in (
        normalized
    )


def test_report_skill_and_template_quote_the_header_code_renders():
    # Cross-file freeze (pattern: the rejection stamp below): the shape the
    # doctrine quotes is exactly what `render_spend_header` emits, so a
    # format change fails here instead of leaving the skill describing a
    # header nobody writes.
    tracker = make_tracker()
    tracker.event("run_start", deadline=9000.0, timeout_mins=120)
    tracker.event("exit", reason="wrap_up")
    status = tracker.snapshot()
    status.elapsed_secs = 47 * 60  # snapshot-computed; pinned for the compare
    assert render_spend_header(status) == (
        SPEND_HEADER_SHAPE.replace("<N>", "120")
        .replace("<M>", "47")
        .replace("<reason>", "wrap_up")
    )
    assert SPEND_HEADER_SHAPE in _normalized_skill("kiln-harness-tuner-report")
    # The template names what code appends, so the agent knows before it
    # reaches the skill. Its elided form is DERIVED from the same shape —
    # hardcoding the literal would let a separator or word-order change fail
    # the skill assertion above and pass silently here.
    elided = (
        SPEND_HEADER_SHAPE.replace("<N> min", "…")
        .replace("<M> min", "…")
        .replace("<reason>", "…")
    )
    assert f"`{elided}` header" in _normalized_template("report_writer")


def test_analysis_skill_defines_bundle_cost_as_the_one_spend_figure():
    # "spend" meant four different things across one run's six
    # EXP records. One figure, one label, and both of its blind spots stated
    # — an analyst who doesn't know the holes are missing from it will read a
    # cheap-looking experiment as cheap.
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    assert "ONE spend figure an EXP record reports" in normalized
    assert "**always labeled `bundle cost`**" in normalized
    assert "**both arms** (candidate + baseline)" in normalized
    assert "It excludes agent session cost" in normalized
    assert "It **under-reports by the holes**" in normalized
    assert "Never patch it with a hand-computed estimate" in normalized
    # …and the frozen record format spends the label, not a bare dollar sign.
    assert "`bundle cost: $<n>` (that label, that figure" in normalized


def test_memory_skill_names_the_same_spend_slot_as_the_analysis_skill():
    # `memory` is the conventions source every Kiln Harness Tuner agent loads, and its
    # experiment-record row listed a bare `spend` field — two names for the
    # one slot item 3 exists to unify, which is the same defect in miniature.
    normalized = _normalized_skill("memory")
    assert "inside the Kiln Harness Tuner loop that slot is `bundle cost`" in normalized
    assert 'that label, never a bare "spend"' in normalized
    assert "defined in `kiln-harness-tuner-experiment-analysis`" in normalized
    # `memory` is project-wide and its `experiment` type explicitly covers
    # hand-run n=3 checks: renaming the slot loop-only would leave every
    # non-loop session with no name for what an experiment cost.
    assert "`spend` everywhere else (a hand-run n=3 check has no bundle)" in normalized


def test_rejection_stamp_text_matches_the_code_that_writes_it():
    # Cross-file freeze: the marker the skills teach planners to detect is
    # exactly what memory.py writes. A rename on either side fails here rather
    # than silently leaving the doctrine pointed at a stamp nobody writes.
    stamped_overview = f"EXP 0730-w2-1: retry skill {REJECTION_STAMP} 07-30"
    assert is_rejected_at_val(stamped_overview)
    for skill in (
        "kiln-harness-tuner-promote",
        "kiln-harness-tuner-batch-selection",
        "memory",
        "kiln-harness-tuner-report",
    ):
        assert REJECTION_STAMP in _normalized_skill(skill)


# ---- the measurement-row format ----
#
# There is NO code render path for a `PerEvalResult` — `ui.py` renders the
# agent-authored `memory_overview` and agent inputs are raw
# `model_dump_json`. The format is a string contract owned by the analysis
# skill, so it is frozen here rather than in a formatter's unit tests.


def test_analysis_skill_freezes_the_measurement_row_format():
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    assert (
        "<score>[↓]: <signed delta>[pp], base=<baseline score>,"
        " cand=<candidate score>, n=<n>, p=<p>"
    ) in normalized
    # Single-target, multi-target (`|`-joined) and the overflow rule — the
    # three shapes an analyzer has to choose between.
    assert "accuracy: +18pp, base=62, cand=80, n=34, p=.04" in normalized
    assert (
        "accuracy: +14pp, base=62, cand=76, n=34, p=.04 | correctness: +3pp,"
        " base=80, cand=83, n=34, p=.4"
    ) in normalized
    assert '`"| +N more"`' in normalized
    # The delta and the absolutes must share units, or `cand − base` no
    # longer reproduces the delta — the row's whole point is that the reader
    # can check it. Binary rows scale all three ×100 (delta suffixed `pp`);
    # rating rows leave all three in the scale's units, unsuffixed.
    assert "**The delta and the absolutes always share units**" in normalized
    assert "render all three ×100, the delta suffixed `pp`" in normalized
    assert "render all three as-is, **no suffix**" in normalized
    # …stated as the GAP, not as `cand − base`: on a `↓` row the delta is
    # sign-normalized and the absolutes are not, so the subtraction's sign
    # disagrees by design — the claim has to survive its own example.
    assert "the *gap* between them is the delta's magnitude" in normalized
    assert "on a `↓` row sign-normalization flips the sign" in normalized
    # `%` stays banned on a delta: it invites a relative reading.
    assert "never `%` on a delta" in normalized
    # Labelled fields only: `x/y` already means coverage on these rows.
    assert "never write a bare `x/y`" in normalized
    assert "`p=` stays" in normalized


def test_analysis_skill_owns_the_overflow_budget_consequence():
    # The 280-char cap is deliberately left alone while the row grew from
    # ~28 to ~50 chars, so overviews overflow at roughly five targets instead
    # of eight. Stating it keeps the next reader from filing it as a bug.
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    assert "This row is ~50 chars, so a labelled overview holds about five" in (
        normalized
    )
    assert "the cap did not move" in normalized


def test_analysis_skill_marks_lower_is_better_rows():
    # The absolutes stay raw while `delta` stays sign-normalized, so a
    # lower_is_better row shows FALLING scores beside a `+` delta. Without
    # the marker that row reads as a contradiction (or as a data bug).
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    assert "**Direction marker** — append `↓` to the score name on a" in normalized
    assert "`lower_is_better` row" in normalized
    assert "tool errors↓: +1.4, base=3.1, cand=1.7" in normalized


def test_analysis_skill_no_longer_makes_the_ui_infer_a_verdict():
    # The ✓/✗ marker is gone from `ui.py`; the rule that made the tail's
    # leading character load-bearing must go with it, or the next format
    # revision is constrained by a consumer that no longer exists. The
    # first-` — `-split rule survives — `outcome_cell` still splits there.
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    assert "opens with a signed" not in normalized
    assert "marks ✗" not in normalized
    assert "the UI splits on the **first** ` — ` and renders the tail" in normalized
    assert "never a ✓/✗ mark of your own" in normalized


def test_analysis_skill_documents_the_new_row_fields():
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    # Absolutes: named, and named RAW — an analyzer that assumes they were
    # sign-normalized would report numbers matching no stored score.
    assert "`baseline_score`/`candidate_score`" in normalized
    assert "**raw**, never sign-normalized" in normalized
    # Job ids: named beside the coverage fields they exist to explain — but
    # NOT as an endpoint the analyzer should call. Its copies of those error
    # messages are already inlined in `bundle.errors`, so a fetch here is
    # duplicate spend on the lane whose doctrine is never to hand-recover a
    # hole. The endpoint belongs to `kiln-harness-tuner-promote`, whose val tables carry
    # no error text.
    assert "`baseline_job_id`/`candidate_job_id`" in normalized
    assert "**The ids are for the promote agent**" in normalized
    assert "do not fetch a job with them" in normalized
    assert "/api/jobs/" not in normalized


def test_promote_skill_explains_a_short_coverage_row_from_the_table():
    # Per-item job errors name val items one by one, so the
    # promote agent no longer fetches them; the table's own errors explain a
    # short row, and the agent is told the rest is not available.
    normalized = _normalized_skill("kiln-harness-tuner-promote")
    assert "**Only when coverage is short**" in normalized
    assert "Per-item job errors are not available to you" in normalized
    assert "On a fully-measured row there is nothing to explain" in normalized
    assert "/api/jobs/" not in normalized


def test_promote_skill_pins_the_sign_convention_on_the_new_absolutes():
    # Every val row carries `baseline_score`/`candidate_score` too, and the
    # promote agent is the highest-stakes reader of them: `delta` is
    # sign-normalized and the absolutes are not, so on a lower_is_better eval
    # an improvement shows FALLING scores. Unstated, that reads as a
    # regression — and the failure mode is a wrong promotion.
    normalized = _normalized_skill("kiln-harness-tuner-promote")
    assert "**Read magnitude off the absolutes, direction off the delta.**" in (
        normalized
    )
    assert "`baseline_score` and `candidate_score`" in normalized
    assert "`delta` is sign-normalized (positive is always the good direction);" in (
        normalized
    )
    assert "the absolutes are NOT" in normalized
    assert "the scores fall because falling is the win" in normalized
    assert "never re-derive direction by subtracting the absolutes" in normalized


# ---- format-drift guard over the FULL doc set, references/ included ----
#
# The freeze tests above call `_normalized_skill`, which reads only a skill's
# `SKILL.md`. That blind spot let a stale copy of the measurement row survive
# in `kiln-harness-tuner-experiment-build/references/merging.md` — a file the
# analysis skill links the merge analyzer to, so both texts sit in one context.
# These two checks are shape-level rather than verbatim, so they cover every
# current and future file without freezing prose.


def _kiln_harness_tuner_doc_paths() -> list[Path]:
    """Every markdown file a Kiln Harness Tuner agent can load: the
    `kiln-harness-tuner-*` skills and their `references/`, plus `memory`, which each
    role is told to load for conventions."""
    return [
        *sorted(SKILLS_DIR.glob("kiln-harness-tuner-*/**/*.md")),
        SKILLS_DIR / "memory" / "SKILL.md",
    ]


def _agent_prompt_paths() -> list[Path]:
    """The Kiln Harness Tuner documents plus the agent templates themselves. A template
    is the prompt head the dispatcher renders, so a link inside one reaches the model
    exactly as a link inside a skill does — and its `../` walk is calibrated against a
    different directory, which is its own way to go wrong."""
    return [
        *_kiln_harness_tuner_doc_paths(),
        *sorted(DEFAULT_TEMPLATES_DIR.glob("*.md")),
    ]


def _installed_doc_paths() -> list[Path]:
    """Every markdown file this repository installs for an agent to read: all
    of `agent_files/skills/`, not only the `kiln-harness-tuner-*` ones, plus the
    templates.
    Another skill a Kiln Harness Tuner document routes to can misdirect an agent
    exactly as a Kiln Harness Tuner document can."""
    return [
        *sorted(SKILLS_DIR.glob("*/**/*.md")),
        *sorted(DEFAULT_TEMPLATES_DIR.glob("*.md")),
    ]


def _doc_label(path: Path) -> str:
    """A short name for a document in an assertion message, whichever of the
    two roots it lives under."""
    for root in (SKILLS_DIR, DEFAULT_TEMPLATES_DIR.parent):
        if root in path.parents:
            return str(path.relative_to(root))
    return str(path)


# A backticked example carrying `n=` is a measurement row (the format's own
# signature). A signed `%` inside one is a delta rendered the banned way.
# Scoping to `n=`-bearing spans is what keeps the promotion val summary —
# `"+9% mean win rate vs rc_207"`, a genuine relative percentage across
# evals, not a per-score delta — from tripping this. (That summary lives in
# the `val` record.)
_BACKTICKED = re.compile(r"`([^`]+)`")
_SIGNED_PERCENT = re.compile(r"[+\-−]\s?\d+(?:\.\d+)?\s?%")

# A fenced block eats one backtick from its opening fence and one from its
# closing fence, so every `code span` AFTER it in the file pairs with the wrong
# neighbour: from that point on `_BACKTICKED` captures the GAPS BETWEEN spans
# instead of the spans. It is silent — the regex still matches, just inverted —
# and it voids every derived guard downstream of it for the rest of the file.
# Without that, `_skills_named_by_kiln_harness_tuner_docs()` would miss a skill
# that a `## Related` line past a fence tells its agent to load — so the no-host
# guard for routed-to skills would not cover a skill it advertises covering.
#
# Fences in these docs are indented (inside list items), so this is
# indent-tolerant rather than anchored at column 0.
_FENCED_BLOCK = re.compile(r"^[ \t]*```.*?^[ \t]*```[^\n]*$", re.MULTILINE | re.DOTALL)


def _code_spans(text: str) -> list[str]:
    """Every `backticked` span in `text`, fenced blocks removed first."""
    return _BACKTICKED.findall(_FENCED_BLOCK.sub("", text))


def test_no_kiln_harness_tuner_doc_renders_a_measurement_delta_as_a_percentage():
    for path in _kiln_harness_tuner_doc_paths():
        for span in _code_spans(path.read_text()):
            if "n=" not in span:
                continue
            assert not _SIGNED_PERCENT.search(span), (
                f"{path.relative_to(SKILLS_DIR)} renders a measurement delta as"
                f" `%`: {span!r} — the frozen format bans it (binary deltas are"
                " `pp`, rating deltas unsuffixed)"
            )


# The retired `ui.py` heuristic: ✗ iff the memory-overview
# tail opened with a signed negative number. Every frozen example now opens
# the tail with a SCORE NAME, so any doc still asserting the old rule tells an
# agent the examples are wrong.
_DELETED_UI_RULE = re.compile(r"opens with (?:a|the)[^.]{0,60}signed")


def test_no_tuner_doc_still_asserts_the_deleted_leading_character_ui_rule():
    for path in _kiln_harness_tuner_doc_paths():
        normalized = " ".join(path.read_text().split())
        match = _DELETED_UI_RULE.search(normalized)
        assert match is None, (
            f"{path.relative_to(SKILLS_DIR)} still teaches the tail-opens-with-"
            f"a-signed-delta UI rule ({match.group(0)!r}) — `ui.py` stopped"
            " reading the tail's leading character"
        )


# ---- the baseline/val record split ----
#
# Val metrics live in a `val`-tagged record, not in the `baseline` one. Two
# failure modes need freezing: a train-side skill whose ban still names only
# `tags=baseline` guards the empty room while the record holding the metrics is
# unnamed; and `kiln-harness-tuner-promote` telling the agent its `rationale`
# lands in the baseline record's overview points it at a field that does not
# exist.

TRAIN_SIDE_SKILLS = (
    "kiln-harness-tuner-batch-selection",
    "kiln-harness-tuner-experiment-analysis",
    "kiln-harness-tuner-experiment-build",
)


def test_train_side_skills_ban_the_val_tagged_record():
    for skill in TRAIN_SIDE_SKILLS:
        normalized = _normalized_skill(skill)
        assert "`tags=val`" in normalized, (
            f"{skill} never names `tags=val` — since the split that is the"
            " record holding every promotion's val metrics, so a ban naming"
            " only `tags=baseline` guards the wrong record"
        )
        # The baseline ban survives too: defence in depth, and the two
        # records sit side by side in one task scope.
        assert "`tags=baseline`" in normalized


def test_train_side_templates_ban_the_val_tagged_record():
    # The rendered prompt head reaches the agent BEFORE any skill does, so a
    # template naming only the baseline record teaches the gap first.
    for agent in ("batch_plan", "analyze_experiment_results", "create_experiment_rc"):
        assert "`tags=val`" in _normalized_template(agent), (
            f"agents/{agent}.md does not ban `tags=val`"
        )


def test_promote_skill_routes_the_rationale_to_the_val_stats_record():
    normalized = _normalized_skill("kiln-harness-tuner-promote")
    assert "lands verbatim in the **`val` stats record's content**" in normalized
    # …and says plainly that the baseline record is not where it goes —
    # unstated, the agent still composes an overview for a field that is gone.
    assert "does **not** reach the `baseline` record, which is metric-free" in (
        normalized
    )
    assert 'tags=["val"]' in normalized  # it must know how to READ the record


def test_promote_template_does_not_send_the_rationale_to_the_baseline_record():
    assert "lands verbatim in the `val`" in _normalized_template("promote_best")


# Two shape-level guards over the full doc set. The `BASELINE: <rc> — …`
# overview is not a format contract; every copy of it teaches an overview
# an agent is meant to compose or parse, and there is no such thing.
_OLD_BASELINE_OVERVIEW = re.compile(r"BASELINE:\s*\S+\s*—")


def test_no_kiln_harness_tuner_doc_quotes_the_deleted_baseline_overview_format():
    for path in _kiln_harness_tuner_doc_paths():
        normalized = " ".join(path.read_text().split())
        match = _OLD_BASELINE_OVERVIEW.search(normalized)
        assert match is None, (
            f"{path.relative_to(SKILLS_DIR)} still quotes the deleted"
            f" `BASELINE: <rc> — …` overview format ({match.group(0)!r}) —"
            " the split made that overview free prose and moved the rc id to"
            " an `rc_<id>` tag"
        )


# The val-metrics monopoly claim is the sentence that decides where agents
# put numbers, so it must sit on the `val` tag row and nowhere near the
# `baseline` one. Read per table ROW rather than shape-matched over the whole
# file: the claim's own phrasing ("This is the only memory allowed to…")
# names no record, so only its position answers which record it is about —
# which is exactly how it survived the split's first draft.
_MONOPOLY_CLAIM = "memory allowed to carry val-split metrics"


def _memory_skill_tag_row(tag: str) -> str:
    rows = [
        line
        for line in (SKILLS_DIR / "memory" / "SKILL.md").read_text().splitlines()
        if line.startswith(f"| `{tag}` |")
    ]
    assert len(rows) == 1, f"expected exactly one `{tag}` row, got {len(rows)}"
    return rows[0]


def test_no_kiln_harness_tuner_doc_documents_the_deleted_baselineref_provenance_field():
    # Agent inputs are `req.model_dump_json`, so a skill promising a field
    # the JSON does not carry is the doc-drift-misleads-the-agent failure
    # this project exists to close. Derived from the model, not a literal, so
    # a future field change cannot leave this test asserting the wrong shape.
    assert set(BaselineRef.model_fields) == {"rc_id"}
    for path in _kiln_harness_tuner_doc_paths():
        normalized = " ".join(path.read_text().split())
        assert "rc_id, provenance" not in normalized, (
            f"{path.relative_to(SKILLS_DIR)} documents `baseline` as"
            " `{rc_id, provenance}` — `BaselineRef.provenance` was deleted"
        )


def test_the_baseline_rows_tag_example_matches_its_own_rc_id():
    # The skill tells interactive sessions to "follow the same formats by
    # hand", so the `baseline` cell's two examples must agree: the tag has to
    # be what `baseline_rc_tag` composes from the rc id the same cell names.
    # An overview reading `run config rc_207` beside a tag `rc_207` teaches
    # that the prefix is already part of the id; `rc_ids_from_tags` then
    # yields `207`, a wrong id that 404s every downstream call, silently.
    row = _memory_skill_tag_row("baseline")
    rc_ids = re.findall(r"run config `?(\S+?)`? ", row)
    assert rc_ids, "the `baseline` row names no rc id in its overview example"
    tags = re.findall(r'"(rc_[^"]+)"', row)
    assert tags, "the `baseline` row shows no `rc_<id>` tag example"
    for rc_id in rc_ids:
        assert baseline_rc_tag(rc_id) in tags, (
            f"the `baseline` row names rc id {rc_id!r}, whose tag is"
            f" {baseline_rc_tag(rc_id)!r}, but its tag examples are {tags} —"
            " the cell contradicts itself"
        )
    for tag in tags:
        (from_tag,) = rc_ids_from_tags([tag])
        assert from_tag in rc_ids, (
            f"tag example {tag!r} decodes to rc id {from_tag!r}, which the"
            f" row never names (it names {rc_ids})"
        )


def test_the_val_metrics_monopoly_belongs_to_the_val_tag_row():
    assert _MONOPOLY_CLAIM in _memory_skill_tag_row("val")


def test_the_baseline_tag_row_disclaims_val_metrics():
    row = _memory_skill_tag_row("baseline")
    assert _MONOPOLY_CLAIM not in row
    assert "It carries NO val metrics" in row


# ---- API paths in skill text ----
#
# `GET …/memories/by_ids` goes wrong two ways: an agent guesses the elided
# prefix into a 404, or guesses the query parameter as `memory_ids`. Both
# halves are frozen here.
#
# The classifier has to separate an API path from the nine backticked spans
# that legitimately end in an ellipsis (`— FAILED (infra: …)`, `"w3: …"`,
# `EXP <MMDD>-w1-…`, `phase_secs/spend_usd…`). A span is a path when it opens
# with an HTTP method, a `/`, or an elided prefix `…/`.

# Both groups are captured: the path checks need the path, the deeplink
# coverage needs the method too, and re-splitting the span at the call site to
# recover it is how the two drift apart.
_HTTP_METHOD = re.compile(r"^(GET|POST|PATCH|PUT|DELETE)\s+(\S+)")


def _api_paths_in(text: str) -> list[tuple[str, str]]:
    """(whole backticked span, the path it names) for every span that names
    an API path."""
    found = []
    for span in _code_spans(text):
        candidate = span.strip()
        method = _HTTP_METHOD.match(candidate)
        if method:
            found.append((span, method.group(2)))
        elif candidate.startswith(("/", "…/")):
            found.append((span, candidate.split()[0]))
    return found


def test_no_kiln_harness_tuner_doc_elides_an_api_path_with_an_ellipsis():
    for path in _kiln_harness_tuner_doc_paths():
        for span, api_path in _api_paths_in(path.read_text()):
            # Split at `?`: elided query VALUES (`?ids=…&ids=…`) are a
            # complete, callable path — it is an elided path PREFIX that
            # sends an agent guessing.
            assert "…" not in api_path.split("?")[0], (
                f"{path.relative_to(SKILLS_DIR)} elides an API path: {span!r}"
                " — write the project-scoped path in full; an agent cannot"
                " reconstruct the prefix and will guess it into a 404"
            )


def test_the_ellipsis_rule_still_admits_the_spans_that_are_not_paths():
    # Guards the classifier, not the docs: a naive "backtick containing `…`"
    # rule flags all of these, and a rule that flagged them would be relaxed
    # until it caught nothing.
    not_paths = (
        "`— FAILED (infra: …)`",
        '`"w3: …"`',
        "`EXP <MMDD>-w1-…`",
        "`item's phase_secs/spend_usd…`",
    )
    for span in not_paths:
        assert _api_paths_in(span) == [], f"{span!r} is not an API path"
    # …and a genuinely complete path with elided query values passes.
    complete = "`GET /api/projects/{project_id}/memories/by_ids?ids=…&ids=…`"
    ((_, api_path),) = _api_paths_in(complete)
    assert "…" not in api_path.split("?")[0]
    # …while the elided-prefix forms this item deleted are caught.
    for span in ("`GET …/memories/by_ids`", "`…/results`"):
        ((_, api_path),) = _api_paths_in(span)
        assert "…" in api_path.split("?")[0]


def test_no_kiln_harness_tuner_doc_names_the_wrong_by_ids_parameter():
    # `/by_ids` takes `ids`; three of the run's analyzer sessions died on
    # `memory_ids`, which is the Python-side name in `memory.py`.
    for path in _kiln_harness_tuner_doc_paths():
        assert "memory_ids=" not in path.read_text(), (
            f"{path.relative_to(SKILLS_DIR)} names the `/by_ids` query"
            " parameter `memory_ids` — the API takes `ids`"
        )


def test_the_skills_that_fetch_a_memory_by_id_spell_the_parameter():
    for skill in (
        "kiln-harness-tuner-experiment-analysis",
        "kiln-harness-tuner-experiment-build",
        "kiln-harness-tuner-promote",
    ):
        assert "memory_get(ids=" in _normalized_skill(skill), (
            f"{skill} tells its agent to fetch a memory by id without showing"
            " the gateway operation and its `ids` field"
        )


# ---- no host reaches an agent ----


_HOST_SHAPES = re.compile(r"https?://|localhost|:8757|:12842")


def test_no_kiln_harness_tuner_doc_carries_a_kiln_host():
    # Agents reach Kiln through `call_kiln_api`, which holds the host and
    # takes a path. Every host string in agent-visible text is an invitation
    # to curl.
    for path in _kiln_harness_tuner_doc_paths():
        match = _HOST_SHAPES.search(path.read_text())
        assert match is None, (
            f"{path.relative_to(SKILLS_DIR)} carries a host ({match.group(0)!r})"
        )


def _skills_named_by_kiln_harness_tuner_docs() -> list[str]:
    """Non-Kiln Harness Tuner skills a Kiln Harness Tuner doc tells its agent to load,
    by backticked name — today `experiment-costing`, `kiln-api` and `kiln-task-setup`.
    Derived, not listed: a future Kiln Harness Tuner skill pointing at a new skill
    pulls it into the no-host check without anyone remembering to widen a constant."""
    all_skills = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    named = set()
    for path in _kiln_harness_tuner_doc_paths():
        for span in _code_spans(path.read_text()):
            name = span.strip().strip("*")
            if (
                name in all_skills
                and not name.startswith("kiln-harness-tuner-")
                and name != "memory"
            ):
                named.add(name)
    return sorted(named)


# A backticked span is read as a skill reference when it is kebab-case with at
# least one hyphen. Kebab is the convention for the skills this repository
# installs, and the directory name and the frontmatter `name:` are the same
# string — but it is not the whole convention elsewhere: snake_case skills
# exist.
#
# **No document in this tree cites one.** The names below stay listed
# anyway, and the list makes the guard STRONGER rather than
# weaker: a re-added `build_eval` is recognised as a skill name, found not to
# resolve, and reported. Emptying the list would make that citation invisible
# instead.
#
# Snake_case cannot be derived by shape: `build_notes` is a field name and
# `build_eval` is a skill, and nothing in the span tells them apart — widening
# the pattern to `_` turns ~190 field names into candidates, which would fill
# the exception lists with noise and destroy what membership in them means. So
# the snake_case names are LISTED. The list is a maintenance point: a new
# snake_case skill cited in a document must be added here to be seen.
_SKILL_NAME_SHAPE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
_SNAKE_CASE_SKILL_NAMES = {"build_eval", "build_skill", "build_tool", "optimize_loop"}


def _is_skill_reference(name: str) -> bool:
    return bool(_SKILL_NAME_SHAPE.match(name)) or name in _SNAKE_CASE_SKILL_NAMES


# Two exception lists, not one, because membership means two different
# things and a single list lets a real dangling reference be filed as noise.

# 1. Real skill names an installed document names but this repository does not
#    install. The set is EMPTY, and that is the intended state: every skill an
#    installed document names is installed here. An entry here is a dangling
#    reference that an agent will follow and not find, so an entry needs a
#    plan for its removal, not just a comment saying why it is allowed.
_SKILLS_NOT_INSTALLED_HERE: set[str] = set()

# 2. Spans this matcher cannot tell from a skill name, and which are not one.
#    `_SKILL_NAME_SHAPE` is "lowercase with a hyphen", so ordinary prose can
#    land here; an entry is a statement that the span is not a name at all.
_NOT_SKILL_NAMES = {
    # A rating range (`r0`–`r4`), written with a hyphen.
    "r0-4",
}


def test_every_skill_name_an_installed_doc_backticks_resolves():
    """The guard `_skills_named_by_kiln_harness_tuner_docs()` cannot be.

    That derivation keeps only names that already resolve, so a routed-to skill
    that goes missing drops out of it silently and every check built on it
    quietly covers less. This one fails instead.

    It sweeps every installed document, not just the `kiln-harness-tuner-*` ones: a
    skill those documents route an agent to can name a missing skill in exactly the same
    way, and the agent is misdirected either way.
    """
    known = _SKILLS_NOT_INSTALLED_HERE | _NOT_SKILL_NAMES
    unresolved: dict[str, set[str]] = {}
    for path in _installed_doc_paths():
        for span in _code_spans(path.read_text()):
            name = span.strip().strip("*")
            if not _is_skill_reference(name) or name in known:
                continue
            if not (SKILLS_DIR / name).is_dir():
                unresolved.setdefault(name, set()).add(_doc_label(path))
    assert not unresolved, (
        "an installed document names a skill that is not installed: "
        + "; ".join(f"`{n}` in {sorted(d)}" for n, d in sorted(unresolved.items()))
        + " — either the skill belongs in agent_files/skills/, or the document"
        " should stop naming it, or the span is not a skill name and belongs"
        " in _NOT_SKILL_NAMES"
    )


def test_no_skill_a_kiln_harness_tuner_doc_loads_carries_a_kiln_host():
    # `kiln-harness-tuner-experiment-build` tells builders to load
    # `kiln-task-setup`, whose description — which sits in EVERY session's skill
    # listing, loaded or not — must not carry `:8757`. Scoping the check to
    # `kiln-harness-tuner-*` would leave a host reaching every agent through a
    # skill a Kiln Harness Tuner skill names.
    #
    # Only the SKILL.md, deliberately: it is the entry point and the half that
    # is always in context. The carve-out for dated
    # provenance lines ("verified on branch X, studio_server :8757, <date>")
    # stays — those are records of where something was proven, not live
    # instructions, and they live in skills no Kiln Harness Tuner doc names.
    named = _skills_named_by_kiln_harness_tuner_docs()
    assert named, "no routed-to skills found — the derivation broke, not the docs"
    for skill in named:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text()
        match = _HOST_SHAPES.search(text)
        assert match is None, (
            f"{skill}/SKILL.md carries a host ({match.group(0)!r}) and a Kiln "
            "Harness Tuner skill tells its agent to load it"
        )


@pytest.mark.parametrize(
    "instructions",
    [
        AGENT_FILES / "AGENTS.md",
        # The contributor guide: a contributor's interactive session loads
        # it, and it was the loop agents' file until the split.
        Path(__file__).resolve().parents[1] / "AGENTS.md",
    ],
    ids=["loop-agents", "contributors"],
)
def test_the_instructions_carry_no_kiln_port(instructions):
    # Installed as CLAUDE.md or AGENTS.md, the loop agents' instructions load
    # into EVERY agent session — it is agent-visible text like any skill.
    assert ":8757" not in instructions.read_text()


# ---- the planner learns eval sizes ----


def test_planner_skill_names_the_eval_sizes_input():
    normalized = _normalized_skill("kiln-harness-tuner-batch-selection")
    assert "`eval_sizes`" in normalized
    assert set(BatchPlanInput.model_fields) >= {"eval_sizes"}


def test_planner_skill_ties_eval_size_to_the_predicted_effect():
    # The read-out on the next run is "does the planner cite eval sizes in
    # its rationale?" — which it cannot do if the skill never connects the
    # number to the decision it should change.
    normalized = _normalized_skill("kiln-harness-tuner-batch-selection")
    detectability = normalized[normalized.index("Detectability") :][:1600]
    assert "`predicted_effect`" in detectability
    assert "`eval_sizes`" in detectability
    # Qualitative, never a formula: item count alone is not a noise floor.
    assert "a noise floor" in detectability


def test_planner_skill_gives_the_floor_as_a_fact_not_a_threshold():
    # The per-score train rows reach the planner, and the skill
    # names them — and keeps the decision a judgment.
    normalized = _normalized_skill("kiln-harness-tuner-batch-selection")
    floor = normalized[normalized.index("The floor, per score") :][:1800]
    for name in ("`train_detectability`", "`room`", "`floor`", "`train_noise`"):
        assert name in floor
    assert "It is still judgment" in floor
    assert "train-only" in floor
    assert set(BatchPlanInput.model_fields) >= {
        "train_detectability",
        "train_noise",
    }


# ---- the parent_rc_id hatch ----


def test_parent_rc_id_hatch_carries_the_val_rejection_constraint():
    # A val-rejected rc must not be forked through this hatch on FixWork
    # items. The val-rejection carve-out is merge-shaped, so it does not bind
    # them.
    normalized = _normalized_skill("kiln-harness-tuner-batch-selection")
    hatch = normalized[normalized.index("`parent_rc_id` is a rare escape hatch") :]
    hatch = hatch[:900]
    assert REJECTION_STAMP in hatch, (
        "the `parent_rc_id` escape hatch does not mention"
        f" {REJECTION_STAMP!r} — it is the route the reviewed run actually took"
    )


# ---- analysis and build skill rules ----


def test_analysis_skill_does_not_assert_a_non_random_missing_set():
    # Measured both ways on one run: judge-side holes clustered by content,
    # concurrent-load generation holes showed no content signal at all. The
    # skill asserted the first as fact and three analyzers repeated it into
    # the round report.
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    assert "missing set is not random" not in normalized
    assert "Whether the missing set is random" in normalized


def test_analysis_skill_asks_fix_items_for_landed_false_too():
    normalized = _normalized_skill("kiln-harness-tuner-experiment-analysis")
    assert "`landed: false` on a fix item too" in normalized
    # And it says WHY, because the consequence is invisible from the analyzer's
    # own seat: planners skip `FIXED:` records as fix candidates.
    assert "`FIXED:`" in normalized


def test_build_skill_tells_builders_to_read_lessons_before_writing_one():
    normalized = _normalized_skill("kiln-harness-tuner-experiment-build")
    assert "Read the existing `lesson` records before you write one" in normalized


def test_build_skill_names_the_skill_bundle_installer_lane():
    # Multi-file skills go through the trusted installer, never per-file
    # writes into the project tree (the Write tool is deny-ruled there). The
    # skill must keep teaching the lane and the wire-up of its return value,
    # or builders fall back to the path the deny rule closes.
    normalized = _normalized_skill("kiln-harness-tuner-experiment-build")
    assert "`install_skill_bundle`" in normalized
    assert "wire the returned `tool_id`" in normalized
    assert "never `POST /skills` plus per-file" in normalized
    # kiln-task-setup is loaded by the same builders for the skill procedure;
    # its disk-workaround sentence must keep redirecting Kiln Harness Tuner sessions.
    assert "`install_skill_bundle`" in _normalized_skill("kiln-task-setup")


def test_skill_authoring_reference_exists_and_is_routed():
    # The merging.md pattern: one reference file per work shape, loaded only
    # when the lever calls for it. The SKILL.md routes to it, the Related
    # line names it, and the template points skill-lever items at it before
    # the skill is even loaded.
    authoring = (
        SKILLS_DIR
        / "kiln-harness-tuner-experiment-build"
        / "references"
        / ("skill-authoring.md")
    )
    assert authoring.is_file()
    build_skill = _normalized_skill("kiln-harness-tuner-experiment-build")
    assert "references/skill-authoring.md" in build_skill
    assert "references/skill-authoring.md" in _normalized_template(
        "create_experiment_rc"
    )
    # The contract's load-bearing pieces live in the reference, not the
    # SKILL.md summary: the tool call, the Write-tool-in-scratch gotcha, the
    # clone rule, and the description contract's closing line.
    doc = _normalized_doc(authoring)
    assert "`install_skill_bundle`" in doc
    assert "Use the Write tool to create the files" in doc
    assert "content only — never `skill.kiln`" in doc
    assert "No other resource paths exist — never guess one" in doc
    # …and the design doctrine, not just the mechanics: skills are tiered
    # (description → body map → per-topic references), so context cost stays
    # proportional to need.
    assert "Progressive disclosure" in doc
    assert "a MAP of the reference files" in doc
    assert "Partition `references/` by topic" in doc


# ---- other skills' deeplinks into `kiln-api` ----
#
# `agent_files/skills/kiln-api/` is generated output (built from a live
# studio_server's OpenAPI document). Its filenames are
# `openapi2skill` slugs, so deeplinks written from memory 404 silently. Generated
# filenames must be stable across runs, or deeplinks break silently.
#
# The tests below therefore read the tree instead of listing it: they build a
# (method, path) -> filename index from the generated files' own
# `**METHOD /path**` lines. A regenerated tree that renames a slug fails the
# suite rather than rotting a link.

KILN_API_DOCS = SKILLS_DIR / "kiln-api" / "references" / "api_docs"

# The link shape the skills use, e.g.
# [`GET /api/jobs/{job_id}/errors`](../kiln-api/…/get_api_jobs_id_errors.md)
_DEEPLINK = re.compile(r"\[`([A-Z]+) ([^`]+)`\]\(([^)]*kiln-api/[^)]+)\)")

# The `**GET /api/jobs/{id}/errors**` line every generated reference file
# carries directly under its `# Title`.
_GENERATED_ENDPOINT = re.compile(
    r"^\*\*(GET|POST|PATCH|PUT|DELETE) (/\S*)\*\*$", re.MULTILINE
)

_PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _endpoint_key(method: str, path: str) -> tuple[str, str]:
    """A method + path, comparable across the two places it is written.

    Placeholder NAMES differ on purpose: the skills say `{job_id}` and
    `{rc_id}` where the server's spec says `{id}` and `{run_config_id}`,
    because the skill-side names match the fields the agent is actually
    holding. A placeholder is substituted before the call, so neither spelling
    changes a URL — this compares the endpoint, not the prose. Query strings
    go the same way: `?ids={memory_id}` is one endpoint with an argument, not
    a different route.
    """
    return method.upper(), _PLACEHOLDER.sub("{}", path.split("?")[0])


def _generated_endpoint_index() -> dict[tuple[str, str], str]:
    index = {}
    for path in sorted(KILN_API_DOCS.glob("*.md")):
        if path.name.endswith("_api_list.md"):  # a catalog, not an endpoint
            continue
        match = _GENERATED_ENDPOINT.search(path.read_text())
        if match:
            index[_endpoint_key(*match.groups())] = path.name
    return index


def _deeplinks_in(doc: Path) -> list[tuple[str, str, Path]]:
    """(method, path, resolved target) for every api_docs deeplink in `doc`."""
    return [
        (method, path, (doc.parent / target).resolve())
        for method, path, target in _DEEPLINK.findall(doc.read_text())
    ]


def test_the_generated_endpoint_index_is_readable():
    # The derivation's own negative control. Every deeplink test below is
    # vacuously true against an empty or half-parsed index, so pin its shape
    # against the committed tree before trusting anything built on it.
    index = _generated_endpoint_index()
    endpoint_files = [
        p for p in KILN_API_DOCS.glob("*.md") if not p.name.endswith("_api_list.md")
    ]
    assert len(index) == len(endpoint_files), (
        f"{len(endpoint_files) - len(index)} generated reference files carry no"
        " `**METHOD /path**` line — the parse, not the tree, is what changed"
    )
    assert index[("GET", "/api/jobs/{}/errors")] == "get_api_jobs_id_errors.md"


def test_every_api_deeplink_targets_a_file_that_exists():
    for doc in _agent_prompt_paths():
        for method, path, target in _deeplinks_in(doc):
            # Containment BEFORE existence. A `../` walk with one hop too many
            # leaves the skills tree, which is all an install copies, and
            # whether that is caught by the existence check alone depends on
            # what happens to sit at the escaped location on the machine
            # running the test — a developer with the generated `kiln-api`
            # skill under `~/.claude/skills/` would see an escaping link
            # resolve and pass.
            assert SKILLS_DIR in target.parents, (
                f"{_doc_label(doc)} deeplinks `{method} {path}` to {target},"
                " which is outside the skills tree — the `../` walk has one hop"
                " too many"
            )
            assert target.is_file(), (
                f"{_doc_label(doc)} deeplinks `{method} {path}` to"
                f" {target.name}, which does not exist — generated slugs are"
                " read off the tree, never predicted"
            )


def test_every_api_deeplink_names_the_endpoint_it_points_at():
    # Existence alone passes a link to the wrong-but-real file
    # (`get_api_jobs_id.md` where `get_api_jobs_id_errors.md` was meant), which
    # is worse than a 404: the agent reads a plausible doc for another
    # endpoint.
    for doc in _agent_prompt_paths():
        for method, path, target in _deeplinks_in(doc):
            documented = _GENERATED_ENDPOINT.search(target.read_text())
            assert documented, f"{target.name} documents no endpoint"
            assert _endpoint_key(method, path) == _endpoint_key(*documented.groups()), (
                f"{_doc_label(doc)} deeplinks `{method} {path}` to"
                f" {target.name}, which documents"
                f" `{' '.join(documented.groups())}`"
            )


def test_every_endpoint_a_kiln_harness_tuner_doc_names_is_deeplinked():
    # The completeness half. A path spelled in prose with no link beside it is
    # a catalog hop the agent has to make itself, which the deeplinks exist to remove.
    # Two kinds of span are skipped, both by construction rather than by
    # exemption: one with no HTTP method (a bare path in prose — nothing to key
    # on, and the `…/results` mention inside the never-hand-recover-a-hole
    # prohibition is deliberately one of these), and one the generated tree
    # documents no file for (`/openapi.json` and `/docs`, named by the discovery ban).
    # The two link-integrity checks above also sweep the agent templates; this
    # one does not. They ask whether a link that exists is sound, which is the
    # same question wherever the link sits. This one asks for a link to be
    # WRITTEN where prose names an endpoint — an authoring rule the templates
    # have never been held to, and imposing it here would mean editing their
    # text rather than checking it.
    index = _generated_endpoint_index()
    for doc in _kiln_harness_tuner_doc_paths():
        linked = {_endpoint_key(m, p) for m, p, _ in _deeplinks_in(doc)}
        for span, api_path in _api_paths_in(doc.read_text()):
            method = _HTTP_METHOD.match(span.strip())
            if not method:
                continue
            key = _endpoint_key(method.group(1), api_path)
            if key not in index:
                continue
            assert key in linked, (
                f"{_doc_label(doc)} names `{span}` but never"
                f" deeplinks it — add"
                f" `[…](../kiln-api/references/api_docs/{index[key]})`"
            )


def test_the_analyzer_names_the_memory_operation_it_reads():
    text = (
        SKILLS_DIR / "kiln-harness-tuner-experiment-analysis" / "SKILL.md"
    ).read_text()
    assert "memory_get(ids=[experiment_memory_id])" in text


def test_the_hole_prohibition_does_not_deeplink_the_results_endpoint():
    # `kiln-harness-tuner-experiment-analysis` names `…/results` twice: once in its one
    # sanctioned use (reading traces to explain numbers) and once inside
    # "never hand-recover a hole — don't re-pair raw results". The second gets
    # no link on purpose; hanging a convenient Read off a prohibition invites
    # the call the sentence forbids. Guarded so a later sweep does not
    # "complete" it.
    text = (
        SKILLS_DIR / "kiln-harness-tuner-experiment-analysis" / "SKILL.md"
    ).read_text()
    _, marker, rest = text.partition("Never hand-recover a hole")
    assert marker, (
        "`kiln-harness-tuner-experiment-analysis` no longer says"
        " 'Never hand-recover a hole' — this test guards the paragraph that"
        " phrase opens, so reword the test with the skill rather than"
        " deleting it"
    )
    prohibition, _, _ = rest.partition("Spend:")
    assert "get_train_results" in prohibition, (
        "the prohibition stopped naming the operation"
    )
    assert "kiln-api/" not in prohibition, (
        "the never-hand-recover-a-hole paragraph now deeplinks the results"
        " endpoint — hanging a convenient Read off a prohibition invites the"
        " call the sentence forbids; the sanctioned mention below it is the one"
        " that carries the link"
    )


# ---- the Kiln-API-discovery ban ----
#
# Banning discovery without the replacement removes agents' only route out of
# a wrong-path dead end. Agents escalate to shell reflexively, so the rule has
# to hand over the replacement (`kiln-api`) in the same breath as the ban.

KILN_HARNESS_TUNER_SKILLS = sorted(TEMPLATE_SKILLS.values())

# The two dispatched roles that are deliberately skill-less (see
# `TEMPLATE_SKILLS` above). Both are granted `_COMMON_TOOLS` — `call_kiln_api`
# AND Bash — so a ban that lives only in skills leaves two of seven roles with
# Kiln access and no rule. They carry the block in their prompt template
# instead, which is the only agent-visible text they have.
# A role with no tools (the integrity reviewer) cannot reach
# the Kiln API or discover it, so it carries no ban.
SKILL_LESS_ROLES = sorted(
    set(DEFAULT_ALLOWED_TOOLS) - set(TEMPLATE_SKILLS) - {"preflight"} - NO_TOOL_ROLES
)

# Verbatim everywhere, because each role is dispatched with exactly one of
# these. Loop agents reach Kiln through the loop gateway only — this rule
# replaces the Kiln-API discovery ban.
GATEWAY_RULE = (
    "**Kiln is reached through your gateway operations only.** Your session's"
    " Kiln tools are the typed operations named"
    " `mcp__kiln_harness_tuner_gateway__<operation>` that your role needs; the"
    " **`kiln-harness-tuner-gateway`** skill lists each one with its fields and an"
    " example. A loop session has no generic Kiln API tool and no second lane:"
    " no HTTP from Bash, no reading the project's files, no discovering routes."
    " Every answer holds train records only, and a refusal is final. There is"
    " no exception — if an operation you need does not exist, say so in what"
    " you return instead of going looking."
)


def test_every_kiln_harness_tuner_skill_carries_the_gateway_rule():
    for skill in KILN_HARNESS_TUNER_SKILLS:
        assert GATEWAY_RULE in _normalized_skill(skill), (
            f"{skill} does not carry the gateway rule verbatim — an agent"
            " loads exactly one of these skills"
        )


def test_the_skill_less_roles_carry_the_rule_in_their_template():
    assert SKILL_LESS_ROLES == ["describe_eval", "eval_build"], (
        f"the skill-less role set changed to {SKILL_LESS_ROLES} — a new"
        " dispatched role with no skill needs the rule in its template"
    )
    for role in SKILL_LESS_ROLES:
        assert GATEWAY_RULE in _normalized_template(role), (
            f"agents/{role}.md does not carry the gateway rule, and the role"
            " has no skill that could"
        )


def test_the_describe_eval_fallbacks_are_reconciled_with_the_rule():
    normalized = _normalized_template("describe_eval")
    assert "collection fallbacks above are **not** an exception" in normalized
    assert "looking for a *third* way" in normalized


def test_no_loop_doc_or_prompt_tells_an_agent_to_call_the_generic_kiln_tool():
    """No loop session has `call_kiln_api`; a loop doc that still
    tells an agent to call it, or links a `kiln-api` route reference, sends
    it after a tool it does not have. The one allowed mention is the
    code-tool skill's warning never to allowlist it inside a subject tool."""
    allowed = "kiln-harness-tuner-code-tool-authoring"
    texts = {
        path.relative_to(SKILLS_DIR).as_posix(): path.read_text()
        for path in SKILLS_DIR.glob("kiln-harness-tuner-*/**/*.md")
        if allowed not in path.as_posix()
        and "kiln-harness-tuner-gateway" not in path.as_posix()
    } | {
        f"agents/{path.name}": path.read_text()
        for path in DEFAULT_TEMPLATES_DIR.glob("*.md")
    }
    for name, text in texts.items():
        assert "call_kiln_api" not in text, name
        assert "kiln-api/references" not in text, name


def test_every_gateway_operation_is_documented_in_the_gateway_skill():
    from kiln_harness_tuner.gateway.ops import OPS

    text = (SKILLS_DIR / "kiln-harness-tuner-gateway" / "SKILL.md").read_text()
    missing = [op for op in OPS if f"`{op}`" not in text]
    assert not missing, f"undocumented gateway operations: {missing}"


def test_every_kiln_harness_tuner_skill_points_at_the_gateway_skill():
    for skill in KILN_HARNESS_TUNER_SKILLS:
        related = _normalized_skill(skill).split("## Related")[-1]
        assert "`kiln-harness-tuner-gateway`" in related, (
            f"{skill}'s Related line does not name `kiln-harness-tuner-gateway`"
        )


def test_the_gateway_rule_does_not_leak_out_of_the_kiln_harness_tuner_lane():
    # The rule is about loop sessions; interactive sessions keep the generic
    # Kiln API tool and the `kiln-api` reference, and no other skill should
    # tell them otherwise.
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        if (
            skill_dir.name in KILN_HARNESS_TUNER_SKILLS
            or not (skill_dir / "SKILL.md").is_file()
        ):
            continue
        assert GATEWAY_RULE not in _normalized_skill(skill_dir.name), skill_dir.name


def test_planner_skill_names_the_lever_attempts_input_and_the_knowledge():
    skill = _normalized_skill("kiln-harness-tuner-batch-selection")
    assert "`lever_attempts`" in skill
    assert set(BatchPlanInput.model_fields) >= {"lever_attempts"}
    # The text-arm rule is stated on the count the code supplies, with its
    # limit (any outcome, not "failed") said out loud.
    assert "ANY outcome" in skill and "which arms failed" in skill
    # What to try comes from the knowledge skill, which took over the lever
    # ladder; its lever `new_tool` links the wrapper templates.
    assert "`kiln-harness-tuner-knowledge`" in skill
    assert "references/planner.md" in skill
    new_tool = _normalized_doc(
        SKILLS_DIR
        / "kiln-harness-tuner-knowledge"
        / "references"
        / "levers"
        / "new_tool.md"
    )
    assert "wrappers/README.md" in new_tool
