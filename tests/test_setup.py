"""setup.py tests — the baseline-seeding decision table.

All against the fakes: no live server, no dispatch. The written record's
FORMAT is memory.py's contract and is string-asserted in test_memory.py;
here we assert the DECISION (which rc, which rule, which reads and writes
happened at all).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from conftest import baseline_record, make_ctx
from fake_dispatcher import FakeDispatcher, expected, infra, ok
from fake_kiln import (
    FakeKiln,
    make_eval,
    make_eval_config,
    make_run_config,
    make_spec,
)

from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.memory import (
    BASELINE_RULE_MOST_RECENT,
    BASELINE_RULE_TASK_DEFAULT,
)
from kiln_harness_tuner.memory_schema import (
    DEFAULT_ISSUE_PRIORITY,
    eval_tag,
    join_owned,
)
from kiln_harness_tuner.schemas import EvalDescription
from kiln_harness_tuner.setup import (
    _RESPONSE_EXCERPT_MAX_CHARS,
    ALREADY_PRESENT,
    CONFIG_GONE_STATUSES,
    SETUP_CONFIG_READ_CONCURRENCY,
    SETUP_DESCRIBE_BACKOFFS,
    SETUP_DESCRIBE_CONCURRENCY,
    BaselineChoice,
    DescribeEvals,
    EvalSplitAuditError,
    EvalView,
    SetupDescribeError,
    SetupPreconditionError,
    SpecView,
    SyncPlan,
    _most_recent,
    apply_sync_plan,
    audit_splits,
    build_sync_plan,
    choose_baseline_rc,
    describe_all,
    eval_rows_from_response,
    fingerprint,
    read_eval_views,
    rubric_text,
    run_setup,
    seed_baseline_if_missing,
    split_filter_id,
    unreadable_response_message,
)

TASK_PATH = "/api/projects/p1/tasks/t1"
RUN_CONFIGS_PATH = f"{TASK_PATH}/run_configs"


def read_paths(kiln: FakeKiln) -> list[str]:
    return [path for _method, path, _params, _body in kiln.calls]


def issue_posts(kiln: FakeKiln) -> list[dict]:
    """The `issue` records a sync wrote — `run_setup` also POSTs the
    `baseline` singleton, which is a different question."""
    return [post for post in kiln.memory_posts if "issue" in (post.get("tags") or [])]


# ---- state A: a record exists ----


async def test_existing_record_is_never_repointed():
    kiln = FakeKiln()
    baseline_record(
        kiln, "Baseline: run config rc_old — seeded 07-15 by kiln-harness-tuner setup"
    )
    # A NEWER user-set default rc must not move the loop's baseline
    # (explicit "leave it exactly as is").
    kiln.task["default_run_config_id"] = "rc_new"
    kiln.run_configs = [make_run_config("rc_new")]

    assert await seed_baseline_if_missing(make_ctx(kiln)) is ALREADY_PRESENT
    assert kiln.memory_posts == []
    assert kiln.memory_patches == []
    # Nothing beyond the existence check is read — the warm-run budget.
    assert TASK_PATH not in read_paths(kiln)
    assert RUN_CONFIGS_PATH not in read_paths(kiln)


# ---- state B: no record, task default set ----


async def test_task_default_run_config_is_seeded():
    kiln = FakeKiln()
    kiln.task["default_run_config_id"] = "rc_default"
    kiln.run_configs = [make_run_config("rc_default"), make_run_config("rc_other")]
    ctx = make_ctx(kiln)

    outcome = await seed_baseline_if_missing(ctx)

    assert (outcome.seeded, outcome.rc_id, outcome.rule) == (
        True,
        "rc_default",
        BASELINE_RULE_TASK_DEFAULT,
    )
    (post,) = kiln.memory_posts
    # The rc id the loop reads is the TAG — the overview is
    # prose for humans and nothing parses it.
    assert post["tags"] == ["baseline", "rc_rc_default"]
    assert post["overview"].startswith("Baseline: run config rc_default — ")


async def test_task_default_wins_over_a_newer_run_config():
    kiln = FakeKiln()
    kiln.task["default_run_config_id"] = "rc_default"
    kiln.run_configs = [
        make_run_config("rc_default", created_at="2026-01-01T00:00:00+00:00"),
        make_run_config("rc_newer", created_at="2026-07-31T00:00:00+00:00"),
    ]

    outcome = await seed_baseline_if_missing(make_ctx(kiln))

    assert outcome.rc_id == "rc_default"


async def test_dangling_default_falls_back_to_most_recent():
    # A default naming an rc the list route doesn't serve (deleted, or
    # otherwise gone) must NOT be seeded: BaselineRef.rc_id is always a real
    # run config, or create_experiment_rc has no parent to build from — a
    # failure that would otherwise surface an agent dispatch later.
    kiln = FakeKiln()
    kiln.task["default_run_config_id"] = "rc_deleted"
    kiln.run_configs = [
        make_run_config("rc_a", created_at="2026-07-01T09:00:00-04:00"),
        make_run_config("rc_b", created_at="2026-07-30T09:00:00-04:00"),
    ]

    outcome = await seed_baseline_if_missing(make_ctx(kiln))

    assert (outcome.rc_id, outcome.rule) == ("rc_b", BASELINE_RULE_MOST_RECENT)
    assert "rc_deleted" not in kiln.memory_posts[0]["overview"]
    # The breadcrumb the record's provenance can't carry: a user who DID set
    # a default must be able to see why a different rc is being optimized
    # (it is logged beside `baseline_seeded`).
    assert outcome.unresolvable_default == "rc_deleted"


async def test_no_default_at_all_reports_no_unresolvable_default():
    kiln = FakeKiln()
    kiln.run_configs = [make_run_config("rc_only")]

    outcome = await seed_baseline_if_missing(make_ctx(kiln))

    # Same rule as the dangling case — the outcome is what tells them apart.
    assert outcome.rule == BASELINE_RULE_MOST_RECENT
    assert outcome.unresolvable_default is None


async def test_honored_default_reports_no_unresolvable_default():
    kiln = FakeKiln()
    kiln.task["default_run_config_id"] = "rc_default"
    kiln.run_configs = [make_run_config("rc_default")]

    outcome = await seed_baseline_if_missing(make_ctx(kiln))

    assert outcome.unresolvable_default is None


# ---- state C: no record, no default ----


async def test_most_recent_is_seeded_when_no_default():
    kiln = FakeKiln()
    kiln.run_configs = [
        make_run_config("rc_a", created_at="2026-07-01T09:00:00-04:00"),
        make_run_config("rc_b", created_at="2026-07-30T09:00:00-04:00"),
        make_run_config("rc_c", created_at="2026-07-15T09:00:00-04:00"),
    ]

    outcome = await seed_baseline_if_missing(make_ctx(kiln))

    assert (outcome.seeded, outcome.rc_id, outcome.rule) == (
        True,
        "rc_b",
        BASELINE_RULE_MOST_RECENT,
    )
    (post,) = kiln.memory_posts
    # The provenance names the rule AND the ambiguity, so
    # the survey and the report can see the choice was mechanical.
    assert "most recent of 3 run configs; no default set" in post["overview"]


async def test_empty_string_default_falls_back_to_most_recent():
    # A task that never had a default serves it as null/"" — both falsy, both
    # the "no default set" state.
    kiln = FakeKiln()
    kiln.task["default_run_config_id"] = ""
    kiln.run_configs = [make_run_config("rc_only")]

    outcome = await seed_baseline_if_missing(make_ctx(kiln))

    assert (outcome.rc_id, outcome.rule) == ("rc_only", BASELINE_RULE_MOST_RECENT)


# ---- state D: no record, zero run configs ----


async def test_zero_run_configs_is_a_precondition_error():
    kiln = FakeKiln()
    ctx = make_ctx(kiln)

    with pytest.raises(SetupPreconditionError) as excinfo:
        await seed_baseline_if_missing(ctx)

    message = str(excinfo.value)
    assert "task t1" in message  # names the task…
    assert "Create a run config for this task" in message  # …and the fix
    assert "will not invent one" in message
    assert kiln.memory_posts == []  # fatal BEFORE any write or spend


# ---- shapes setup cannot read (memory.py's list-shape doctrine) ----


async def test_run_configs_without_ids_are_a_precondition_error():
    # Rows came back but none is identifiable: seeding would write a
    # `baseline` record naming "None" — which parses fine and poisons the
    # run. Fail instructively instead, and distinguish it from state D.
    kiln = FakeKiln()
    kiln.run_configs = [{"name": "no id here"}, {"id": None}, {"id": "  "}]

    with pytest.raises(SetupPreconditionError) as excinfo:
        await seed_baseline_if_missing(make_ctx(kiln))

    message = str(excinfo.value)
    assert RUN_CONFIGS_PATH in message
    assert "re-verifying against the live API" in message
    assert "has no run configs" not in message  # not the documented state D
    assert kiln.memory_posts == []


async def test_identifiable_run_configs_survive_an_unusable_sibling():
    kiln = FakeKiln()
    kiln.run_configs = [
        {"name": "no id here", "created_at": "2026-07-31T09:00:00-04:00"},
        make_run_config("rc_real", created_at="2026-07-01T09:00:00-04:00"),
    ]

    outcome = await seed_baseline_if_missing(make_ctx(kiln))

    assert outcome.rc_id == "rc_real"
    # rc_count counts what setup could actually use.
    assert "most recent of 1 run configs" in kiln.memory_posts[0]["overview"]


async def test_unreadable_task_response_is_a_precondition_error():
    kiln = FakeKiln()
    kiln.task = "<html>not json</html>"  # KilnClient.call returns str bodies

    with pytest.raises(SetupPreconditionError) as excinfo:
        await seed_baseline_if_missing(make_ctx(kiln))

    assert TASK_PATH in str(excinfo.value)
    assert kiln.memory_posts == []


async def test_unreadable_run_configs_response_is_a_precondition_error():
    kiln = FakeKiln()
    kiln.run_configs = {"run_configs": [], "matched": 0}  # a paging envelope

    with pytest.raises(SetupPreconditionError) as excinfo:
        await seed_baseline_if_missing(make_ctx(kiln))

    message = str(excinfo.value)
    assert RUN_CONFIGS_PATH in message
    # The message shows the shape, not just its type — the fix instruction is
    # "re-verify the shape", so a paging envelope must be legible as one.
    assert "keys ['matched', 'run_configs']" in message
    assert kiln.memory_posts == []


# ---- choose_baseline_rc: the decision, without the reads or the write ----


def test_choose_baseline_rc_honours_the_task_default():
    run_configs = [
        make_run_config("rc_default", created_at="2026-01-01T00:00:00+00:00"),
        make_run_config("rc_newer", created_at="2026-07-31T00:00:00+00:00"),
    ]
    choice = choose_baseline_rc({"default_run_config_id": "rc_default"}, run_configs)
    assert choice == BaselineChoice("rc_default", BASELINE_RULE_TASK_DEFAULT, None)


def test_choose_baseline_rc_falls_back_to_the_most_recent_for_a_dangling_default():
    run_configs = [
        make_run_config("rc_a", created_at="2026-07-01T09:00:00-04:00"),
        make_run_config("rc_b", created_at="2026-07-30T09:00:00-04:00"),
    ]
    choice = choose_baseline_rc({"default_run_config_id": "rc_deleted"}, run_configs)
    assert choice == BaselineChoice(
        "rc_b", BASELINE_RULE_MOST_RECENT, unresolvable_default="rc_deleted"
    )


@pytest.mark.parametrize(
    "task", [{}, {"default_run_config_id": None}, {"default_run_config_id": " "}]
)
def test_choose_baseline_rc_takes_the_most_recent_with_no_default(task):
    run_configs = [
        make_run_config("rc_a", created_at="2026-07-30T09:00:00-04:00"),
        make_run_config("rc_b", created_at="2026-07-01T09:00:00-04:00"),
    ]
    choice = choose_baseline_rc(task, run_configs)
    assert choice == BaselineChoice("rc_a", BASELINE_RULE_MOST_RECENT, None)


# ---- an eval row's split filter ids: the two homes they have had ----


def test_split_filter_id_reads_the_splits_map():
    raw = {
        "splits": {
            "test": {"source": "eval_input", "filter_id": "tag::t"},
            "train": {"source": "eval_input", "filter_id": "tag::tr"},
            "val": {"source": "eval_input", "filter_id": "tag::v"},
        }
    }

    assert split_filter_id(raw, "train") == "tag::tr"
    assert split_filter_id(raw, "val") == "tag::v"


def test_split_filter_id_reads_either_store_the_split_names():
    # A split's `source` picks the store its filter runs over. Both are real,
    # so the filter id is read the same way for either — the server resolves it.
    raw = {"splits": {"train": {"source": "task_run", "filter_id": "tag::tr"}}}

    assert split_filter_id(raw, "train") == "tag::tr"


def test_split_filter_id_falls_back_to_the_flat_fields():
    # A server predating `splits` still serves these, and kiln-harness-tuner runs
    # against one.
    raw = {"train_set_filter_id": "tag::tr", "eval_set_filter_id": "tag::t"}

    assert split_filter_id(raw, "train") == "tag::tr"
    assert split_filter_id(raw, "test") == "tag::t"


def test_split_filter_id_prefers_splits_over_a_stale_flat_field():
    raw = {
        "splits": {"train": {"source": "eval_input", "filter_id": "tag::new"}},
        "train_set_filter_id": "tag::old",
    }

    assert split_filter_id(raw, "train") == "tag::new"


def test_split_filter_id_has_no_flat_fallback_for_val():
    # Kiln dropped `val_set_filter_id` from the Eval model, so a row still
    # carrying one is not a val split any more — reading it as one would let a
    # promotion gate run against a filter the server no longer resolves.
    raw = {"val_set_filter_id": "tag::v"}

    assert split_filter_id(raw, "val") is None


def test_split_filter_id_is_none_for_a_split_the_eval_does_not_have():
    raw = {"splits": {"test": {"source": "eval_input", "filter_id": "tag::t"}}}

    assert split_filter_id(raw, "train") is None
    assert split_filter_id(raw, "val") is None


def test_split_filter_id_ignores_a_split_ref_with_no_filter_id():
    raw = {"splits": {"train": {"source": "eval_input"}}}

    assert split_filter_id(raw, "train") is None


# ---- GET …/evals: the two shapes the route has served ----


def test_eval_rows_reads_the_envelope_the_server_returns_now():
    # Kiln 38e07d4ff wrapped the listing so one unreadable eval can't 404 the
    # whole route. `evals` is the rows; `load_error_count` is what it dropped.
    rows, load_errors = eval_rows_from_response(
        "/api/x/evals",
        {"evals": [{"id": "e1"}, {"id": "e2"}], "load_error_count": 3},
    )

    assert rows == [{"id": "e1"}, {"id": "e2"}]
    assert load_errors == 3


def test_eval_rows_still_reads_the_bare_list_the_older_server_returns():
    rows, load_errors = eval_rows_from_response("/api/x/evals", [{"id": "e1"}])

    assert rows == [{"id": "e1"}]
    # No envelope means no count to report — not "zero errors known", but the
    # same absence the old server always had.
    assert load_errors == 0


def test_eval_rows_reads_an_empty_2xx_as_an_empty_listing():
    assert eval_rows_from_response("/api/x/evals", None) == ([], 0)


def test_eval_rows_treats_a_non_int_load_error_count_as_zero():
    # The count only ever gates a warning, so a drifted one must not become a
    # TypeError three frames later, and must not be reported as a real count.
    rows, load_errors = eval_rows_from_response(
        "/api/x/evals", {"evals": [], "load_error_count": "3"}
    )

    assert rows == []
    assert load_errors == 0


def test_eval_rows_rejects_an_envelope_that_is_not_the_eval_envelope():
    # A dict whose `evals` is missing (or not a list) is drift, not a shape to
    # guess at: reading it as empty would seed a baseline off no evals at all.
    with pytest.raises(SetupPreconditionError) as excinfo:
        eval_rows_from_response("/api/x/evals", {"items": [], "matched": 0})

    message = str(excinfo.value)
    assert "/api/x/evals" in message
    assert "keys ['items', 'matched']" in message


async def test_eval_load_errors_are_warned_about_not_swallowed(caplog):
    # The server serving a short list is not fatal — one broken eval file must
    # not block every run — but the evals it dropped are measurements this run
    # will never take, so the run log has to carry it.
    kiln = FakeKiln()
    kiln.eval_load_error_count = 2

    with caplog.at_level(logging.WARNING, logger="kiln_harness_tuner.setup"):
        await read_eval_views(make_ctx(kiln))

    assert any(
        "could not read 2 eval(s)" in record.getMessage() for record in caplog.records
    )


def test_unreadable_response_message_bounds_a_long_body():
    message = unreadable_response_message("/api/x", "y" * 500)
    described = message.split("(got ", 1)[1].split(") — ", 1)[0]
    assert described.startswith("a str: 'yyy")
    assert described.endswith("…")  # quoted, but never the whole body
    assert len(described) == len("a str: ") + _RESPONSE_EXCERPT_MAX_CHARS


async def test_null_run_configs_response_reads_as_zero_run_configs():
    kiln = FakeKiln()
    kiln.run_configs = None  # empty 2xx body → KilnClient.call returns None

    with pytest.raises(SetupPreconditionError, match="has no run configs"):
        await seed_baseline_if_missing(make_ctx(kiln))


async def test_falsy_non_list_run_configs_response_is_drift_not_state_d():
    # Only None collapses to state D. An empty envelope is drift — telling a
    # task that may have plenty of run configs to "create a run config"
    # would be the exact misread the shape guard exists to prevent.
    kiln = FakeKiln()
    kiln.run_configs = {}

    with pytest.raises(SetupPreconditionError) as excinfo:
        await seed_baseline_if_missing(make_ctx(kiln))

    message = str(excinfo.value)
    assert "re-verifying against the live API" in message
    assert "has no run configs" not in message


# ---- _most_recent ordering ----


def test_most_recent_breaks_ties_by_id():
    same = "2026-07-30T09:00:00-04:00"
    rcs = [
        make_run_config("rc_1", created_at=same),
        make_run_config("rc_3", created_at=same),
        make_run_config("rc_2", created_at=same),
    ]
    assert _most_recent(rcs)["id"] == "rc_3"
    assert _most_recent(list(reversed(rcs)))["id"] == "rc_3"  # order-independent


def test_most_recent_sorts_unparseable_created_at_last():
    parseable = make_run_config("rc_good", created_at="2020-01-01T00:00:00+00:00")
    rcs = [
        make_run_config("rc_junk", created_at="last tuesday"),
        make_run_config("rc_missing", created_at=None),
        make_run_config("rc_null_value"),
        parseable,
    ]
    rcs[2]["created_at"] = None  # explicit null, not an absent key
    # Even the OLDEST parseable timestamp beats an unreadable one, and no
    # shape raises — an unknown row must never fail a run.
    assert _most_recent(rcs) is parseable


def test_most_recent_all_unparseable_still_picks_deterministically():
    rcs = [make_run_config("rc_1", created_at=None), make_run_config("rc_2", None)]
    assert _most_recent(rcs)["id"] == "rc_2"
    assert _most_recent(list(reversed(rcs)))["id"] == "rc_2"


def test_most_recent_mixes_naive_and_aware_timestamps():
    # Naive timestamps are read as UTC — comparing them against the live
    # server's offset-aware ones must never raise TypeError.
    rcs = [
        make_run_config("rc_naive", created_at="2026-07-30T12:00:00"),
        make_run_config("rc_aware", created_at="2026-07-30T13:00:00+00:00"),
    ]
    assert _most_recent(rcs)["id"] == "rc_aware"


# ===========================================================================
# eval ↔ issue sync
# ===========================================================================

HANDOFF_ID = "424242966189"
MANIFEST_ID = "424242005456"
OUT_OF_SCOPE_ID = "424242150703"

# Config shapes copied off a live project tree.
CODE_EVAL_PROPERTIES = {
    "type": "code_eval",
    "code": "import xml.etree.ElementTree as ET\n...",
    "reference_keys": {},
    "timeout_seconds": 30,
}
LLM_JUDGE_PROPERTIES = {
    "type": "llm_judge",
    "prompt_template": "1. Did the reply stay in scope?",
    "system_prompt": "You are an evaluator.",
    "g_eval": True,
}
# A live manifest-accuracy config observed in the wild has NO `type` key at
# all — the legacy g_eval shape.
G_EVAL_PROPERTIES = {
    "eval_steps": ["Read the reference answer.", "Compare the facts."],
    "task_description": "depot records assistant",
}
DETERMINISTIC_PROPERTIES = {"type": "tool_call_check", "tool_name": "search"}

DESCRIPTION = EvalDescription(
    overview="replies do not end with the required <handoff> XML block",
    description="Every reply must end with a <handoff> block using <item> tags.",
)


def add_eval(
    kiln: FakeKiln,
    eval_id: str = HANDOFF_ID,
    *,
    name: str | None = None,
    properties: dict | None = None,
    spec_status: str | None = None,
    spec_id: str = "424242331708",
    priority: int = 1,
    definition: str = "## Desired Behaviour\nend with <handoff>",
    train_filter: str | None = "tag::train",
    val_filter: str | None = "tag::val",
) -> None:
    """One eval, its current config, and (optionally) the Spec that enriches
    it. `spec_status=None` = an eval with no Spec at all — active by rule."""
    config_id = f"ec_{eval_id}"
    kiln.evals[eval_id] = make_eval(
        eval_id,
        config_id,
        name=name or f"Eval {eval_id}",
        train_filter=train_filter,
        val_filter=val_filter,
    )
    kiln.eval_configs[config_id] = make_eval_config(
        config_id, **(properties if properties is not None else CODE_EVAL_PROPERTIES)
    )
    if spec_status is not None:
        kiln.specs.append(
            make_spec(
                spec_id,
                eval_id=eval_id,
                status=spec_status,
                priority=priority,
                definition=definition,
            )
        )


def add_issue(
    kiln: FakeKiln,
    *,
    overview: str = "issue: replies drop the block",
    eval_ids: tuple[str, ...] = (),
    content: str | None = None,
    priority: str = "p1",
    memory_id: str | None = None,
) -> str:
    return kiln.add_memory(
        overview,
        "task::t1",
        ["issue", priority, *(eval_tag(e) for e in eval_ids)],
        content=content,
        memory_id=memory_id,
    )


def setup_owned_content(fp: str, tail: str = "", state: str = "unmeasured") -> str:
    return join_owned(f"a description\nstatus: {state} · fp: {fp} · synced x", tail)


def is_config_read(url_path: str) -> bool:
    return "/eval_config/" in url_path


def is_listing_read(url_path: str) -> bool:
    return url_path.endswith(("/evals", "/specs"))


def is_read_group_entry(url_path: str) -> bool:
    """The first call of each of `build_sync_plan`'s two independent read
    groups: `read_eval_views` opens on `…/evals`, `read_issue_views` on the
    memory listing."""
    return url_path.endswith(("/evals", "/memories"))


class ConcurrencyProbe:
    """Wraps FakeKiln to watch how many of the matched reads are in flight at
    once.

    The probe awaits inside the call because the fake never suspends on its
    own: without a real suspension point every gathered coroutine would run
    to completion in turn and ANY bound would look respected."""

    def __init__(self, kiln: FakeKiln, matches: Callable[[str], bool]):
        self._kiln = kiln
        self._matches = matches
        self.max_in_flight = 0
        self.matched_reads = 0
        self._in_flight = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._kiln, name)

    async def call(self, method: str, url_path: str, *args: Any, **kwargs: Any) -> Any:
        if not self._matches(url_path):
            return await self._kiln.call(method, url_path, *args, **kwargs)
        self.matched_reads += 1
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(0)
            return await self._kiln.call(method, url_path, *args, **kwargs)
        finally:
            self._in_flight -= 1


class FailingConfigRoute:
    """FakeKiln with the eval-config route failing at a scripted status —
    everything setup must NOT confuse with "the config isn't there"."""

    def __init__(self, kiln: FakeKiln, status_code: int):
        self._kiln = kiln
        self._status_code = status_code

    def __getattr__(self, name: str) -> Any:
        return getattr(self._kiln, name)

    async def call(self, method: str, url_path: str, *args: Any, **kwargs: Any) -> Any:
        if is_config_read(url_path):
            raise KilnAPIError(method, url_path, self._status_code, {"detail": "no"})
        return await self._kiln.call(method, url_path, *args, **kwargs)


def stub_describe(recorder: list[list[str]] | None = None) -> DescribeEvals:
    """Stands in for the describe fan-out: one description per requested eval."""

    async def describe(ctx, views):
        if recorder is not None:
            recorder.append([v.id for v in views])
        return {v.id: DESCRIPTION for v in views}

    return describe


_DEFAULT_PROPERTIES = object()  # sentinel: `properties=None` means "no config"


def eval_view(
    eval_id: str = HANDOFF_ID,
    *,
    properties: Any = _DEFAULT_PROPERTIES,
    spec: SpecView | None = None,
    name: str = "Handoff Block",
) -> EvalView:
    return EvalView(
        id=eval_id,
        name=name,
        current_config_id=f"ec_{eval_id}",
        train_filter="tag::train",
        val_filter="tag::val",
        config_properties=(
            CODE_EVAL_PROPERTIES if properties is _DEFAULT_PROPERTIES else properties
        ),
        spec=spec,
    )


SPEC = SpecView(
    id="424242331708",
    status="active",
    priority=1,
    definition="## Desired Behaviour\nend with <handoff>",
    issue_description=None,
)


# ---- fingerprint + rubric_text ----


def test_rubric_text_reads_every_live_config_shape():
    assert rubric_text(CODE_EVAL_PROPERTIES).startswith("import xml.etree")
    assert rubric_text(LLM_JUDGE_PROPERTIES) == (
        "1. Did the reply stay in scope?You are an evaluator."
    )
    # The legacy `type: null` g_eval shape.
    assert rubric_text(G_EVAL_PROPERTIES) == (
        "Read the reference answer.\nCompare the facts.depot records assistant"
    )
    # Unknown/deterministic shapes fall through to canonical JSON: they still
    # fingerprint, they just hash more than they need to.
    assert rubric_text(DETERMINISTIC_PROPERTIES) == (
        '{"tool_name": "search", "type": "tool_call_check"}'
    )
    assert rubric_text(None) == "" and rubric_text({}) == ""


def test_rubric_text_never_raises_on_an_unreadable_config():
    # A config shape we don't recognize must not fail a run.
    assert rubric_text({"type": "future_kind", "blob": {1, 2}})  # a set isn't JSON
    assert rubric_text({"eval_steps": [None, 3]})


def test_fingerprint_is_stable_and_distinguishes_the_config_shapes():
    shapes = [
        CODE_EVAL_PROPERTIES,
        LLM_JUDGE_PROPERTIES,
        G_EVAL_PROPERTIES,
        DETERMINISTIC_PROPERTIES,
        None,
    ]
    prints = [fingerprint(eval_view(properties=p)) for p in shapes]
    assert len(set(prints)) == len(shapes)
    assert all(len(p) == 8 for p in prints)
    # Stable across recomputation — the whole point of storing it.
    assert prints == [fingerprint(eval_view(properties=p)) for p in shapes]


def test_fingerprint_moves_with_rubric_spec_definition_and_priority():
    base = fingerprint(eval_view(spec=SPEC))
    rewritten = dict(CODE_EVAL_PROPERTIES, code="totally different")
    assert fingerprint(eval_view(properties=rewritten, spec=SPEC)) != base
    assert fingerprint(eval_view(spec=replace(SPEC, definition="new"))) != base
    assert fingerprint(eval_view(spec=replace(SPEC, priority=0))) != base
    assert fingerprint(eval_view(spec=replace(SPEC, issue_description="x"))) != base
    assert fingerprint(eval_view(name="Renamed", spec=SPEC)) != base
    # A superseded config swapped in as the live one is drift too.
    assert fingerprint(replace(eval_view(spec=SPEC), current_config_id="ec_2")) != base
    # A spec STATUS change is not: it moves the eval in/out of the active
    # set, which the partition handles — the description doesn't change.
    assert fingerprint(eval_view(spec=replace(SPEC, status="archived"))) == base


# ---- partition ----


@pytest.mark.parametrize(
    "status,active",
    [("active", True), ("future", False), ("deprecated", False), ("archived", False)],
)
async def test_spec_status_decides_the_active_set(status, active):
    kiln = FakeKiln()
    add_eval(kiln, spec_status=status)

    plan = await build_sync_plan(make_ctx(kiln))

    assert (plan.evals_total, plan.evals_active) == (1, int(active))
    assert [v.id for v in plan.seed] == ([HANDOFF_ID] if active else [])


async def test_eval_without_a_spec_is_active_and_seeded():
    kiln = FakeKiln()
    add_eval(kiln)  # no spec row at all

    plan = await build_sync_plan(make_ctx(kiln))

    assert [v.id for v in plan.seed] == [HANDOFF_ID]
    assert plan.seed[0].spec is None and plan.seed[0].priority == 1


async def test_bound_eval_with_an_unchanged_fingerprint_is_a_no_op():
    # The warm-path guarantee: nothing to describe, nothing to write.
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(kiln)
    described: list[list[str]] = []
    await apply_sync_plan(ctx, await build_sync_plan(ctx), stub_describe(described))
    writes_after_seed = len(kiln.memory_posts) + len(kiln.memory_patches)

    plan = await build_sync_plan(ctx)
    report = await apply_sync_plan(ctx, plan, stub_describe(described))

    assert plan.is_empty and plan.describe_targets == []
    assert described == [[HANDOFF_ID]]  # the SECOND pass described nothing
    assert len(kiln.memory_posts) + len(kiln.memory_patches) == writes_after_seed
    assert report.seeded_eval_ids == [] and report.refreshed_eval_ids == []


async def test_changed_rubric_is_refreshed():
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(kiln)
    await apply_sync_plan(ctx, await build_sync_plan(ctx), stub_describe())
    kiln.eval_configs[f"ec_{HANDOFF_ID}"]["properties"]["code"] = "rewritten"

    plan = await build_sync_plan(ctx)

    assert plan.seed == []
    assert [(v.id, i.id) for v, i in plan.refresh] == [(HANDOFF_ID, "mem_1")]
    assert plan.describe_targets == [plan.refresh[0][0]]


async def test_loop_authored_issue_is_never_refreshed():
    # An issue the LOOP discovered and bound has no setup-owned head — its
    # prose is the analyst's, and setup never rewrites it.
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    add_issue(kiln, eval_ids=(HANDOFF_ID,), content="found while analyzing w1-1")

    plan = await build_sync_plan(make_ctx(kiln))

    assert plan.is_empty


async def test_issue_bound_to_a_deleted_eval_is_unmeasurable():
    kiln = FakeKiln()
    issue = add_issue(kiln, eval_ids=(MANIFEST_ID,), content="seen in trace 42")

    plan = await build_sync_plan(make_ctx(kiln))

    assert [(i.id, cause) for i, cause in plan.unmeasurable] == [
        (issue, f"eval {MANIFEST_ID} deleted")
    ]


async def test_issue_bound_to_an_archived_spec_is_unmeasurable():
    kiln = FakeKiln()
    add_eval(kiln, MANIFEST_ID, spec_status="archived", spec_id="424242130650")
    issue = add_issue(kiln, eval_ids=(MANIFEST_ID,), content="seen in trace 42")

    plan = await build_sync_plan(make_ctx(kiln))

    # Same marker, different cause line.
    assert [(i.id, cause) for i, cause in plan.unmeasurable] == [
        (
            issue,
            f"spec 424242130650 archived; eval {MANIFEST_ID} left the active set",
        )
    ]


async def test_already_flagged_issue_is_skipped():
    kiln = FakeKiln()
    add_issue(
        kiln,
        overview="UNMEASURABLE: issue: quantities do not match the manifest",
        eval_ids=(MANIFEST_ID,),
    )

    plan = await build_sync_plan(make_ctx(kiln))

    assert plan.unmeasurable == []  # the note is written once


async def test_issue_bound_to_two_evals_with_one_active_is_not_unmeasurable():
    kiln = FakeKiln()
    add_eval(kiln, MANIFEST_ID, spec_status="active", spec_id="424242130650")
    add_issue(kiln, eval_ids=(MANIFEST_ID, "424242999999"))

    plan = await build_sync_plan(make_ctx(kiln))

    assert plan.unmeasurable == []
    assert plan.seed == []  # the live binding covers the eval


# ---- repair ----

# Five unbound issue records, built from a live task's memory store.
#
# THE RULE, rather than an enumeration — the line between "structure" and
# "content" runs through individual sentences:
#
#     Every id is invented. Every STATEMENT ABOUT WHAT A TASK MEASURED is
#     invented — the subject lines and the evidence lines. Everything else may
#     be, and mostly is, verbatim: the `- eval:` line and its parentheticals,
#     the `repros on` / `fixed by` / `observed on` / `related` stamps, the
#     `Sub-mechanism of issues X and Y` cross-reference, the provenance
#     sentences that say only where a record came from, the dates, and the EXP
#     labels. Those are the shapes the binding rules parse and the shapes a
#     reader needs in order to recognise a real record, and they carry no
#     finding.
#
# Read as: nothing here reports a result. Anything that looks like structure
# probably is structure, and is copied on purpose.
#
# Four bind by rule 1, one by rule 2.
UNBOUND_ISSUES = {
    "424242550342": (
        "Every quantity in a reply must match the manifest it came from.\n"
        f"- eval: {MANIFEST_ID} (built 07-30, reproduced)\n"
        "- repros on baseline 424242110868 (noted at promotion)."
    ),
    "424242354308": (
        "Every reply must end with a <handoff> XML block.\n"
        f"- eval: {HANDOFF_ID} (built 07-30, reproduced)\n"
        "- fixed 07-31 by 424242110868, EXP 0731-w2-1"
    ),
    "424242237042": (
        f"observed on: merge rc 424242110868, train, eval {MANIFEST_ID} Manifest "
        "Accuracy, eval_config 424242201354.\n"
        "evidence: 6 of 23 manifest items answered with a decline.\n"
        f"- eval: {MANIFEST_ID} (reproduced 07-31 on rc 424242110868)\n"
        "- fixed 07-31 by 424242701727, EXP 0731-w3-1"
    ),
    "424242264489": (
        "Observed 07-31 on EXP w5-2 rc 424242759225, eval 424242005456 (train).\n"
        "Sub-mechanism of issues 424242881027 and 424242550342.\n"
        f"- eval: {MANIFEST_ID} (train, n=23, full coverage); repros on rc "
        "424242759225."
    ),
    # Rule 2: no `- eval:` line, exactly one eval id in prose.
    "424242515304": (
        "Second failure shape seen while reviewing EXP 0731-w1-1 (rc "
        f"424242160015, eval {HANDOFF_ID} Handoff Block, train).\n"
        "- Failing dataset ids: 424242622503, 424242268195, 424242387763.\n"
        "- related: issue 424242354308 (missing handoff block)"
    ),
}


async def test_the_five_real_unbound_issues_are_repaired():
    kiln = FakeKiln()
    add_eval(kiln, HANDOFF_ID, spec_status="active", spec_id="424242331708")
    add_eval(kiln, MANIFEST_ID, spec_status="active", spec_id="424242130650")
    for memory_id, content in UNBOUND_ISSUES.items():
        add_issue(kiln, content=content, memory_id=memory_id)

    plan = await build_sync_plan(make_ctx(kiln))

    assert [(b.issue.id, b.eval_id, b.add_line) for b in plan.repair] == [
        ("424242550342", MANIFEST_ID, False),
        ("424242354308", HANDOFF_ID, False),
        ("424242237042", MANIFEST_ID, False),
        ("424242264489", MANIFEST_ID, False),
        ("424242515304", HANDOFF_ID, True),  # rule 2 — prose only
    ]
    assert plan.unbound_unresolved == []


async def test_repaired_binding_suppresses_a_duplicate_seed():
    # Without repair-before-partition both evals would be seeded a SECOND
    # issue apiece — the duplication the sync exists to prevent.
    kiln = FakeKiln()
    add_eval(kiln, HANDOFF_ID, spec_status="active")
    add_eval(kiln, MANIFEST_ID, spec_status="active", spec_id="424242130650")
    for memory_id, content in UNBOUND_ISSUES.items():
        add_issue(kiln, content=content, memory_id=memory_id)

    plan = await build_sync_plan(make_ctx(kiln))

    assert plan.seed == []
    assert plan.unmeasurable == []


async def test_ambiguous_dangling_and_bare_issues_are_left_untouched():
    kiln = FakeKiln()
    add_eval(kiln, HANDOFF_ID, spec_status="active")
    add_eval(kiln, MANIFEST_ID, spec_status="active", spec_id="424242130650")
    ambiguous = add_issue(
        kiln,
        content=f"- eval: {HANDOFF_ID} (built)\n- eval: {MANIFEST_ID} (built)",
    )
    dangling = add_issue(kiln, content="- eval: 424242999999 (built 07-30)")
    bare = add_issue(kiln, content="no eval was ever built for this")
    empty = add_issue(kiln, content=None)

    plan = await build_sync_plan(make_ctx(kiln))

    assert plan.repair == []
    assert [i.id for i in plan.unbound_unresolved] == [
        ambiguous,
        dangling,
        bare,
        empty,
    ]
    # Both evals are still uncovered, so both are seeded — never guessed at.
    assert [v.id for v in plan.seed] == [HANDOFF_ID, MANIFEST_ID]


async def test_prose_rule_ignores_non_eval_ids():
    kiln = FakeKiln()
    add_eval(kiln, HANDOFF_ID, spec_status="active")
    add_issue(
        kiln,
        content=(
            f"seen on rc 424242160015 and dataset 424242622503; eval "
            f"{HANDOFF_ID} Handoff Block; related issue 424242354308"
        ),
    )

    plan = await build_sync_plan(make_ctx(kiln))

    assert [(b.eval_id, b.add_line) for b in plan.repair] == [(HANDOFF_ID, True)]


# ---- apply ----


async def test_apply_seeds_refreshes_repairs_and_flags():
    kiln = FakeKiln()
    add_eval(kiln, HANDOFF_ID, spec_status="active", spec_id="424242331708")
    add_eval(kiln, MANIFEST_ID, spec_status="active", spec_id="424242130650")
    add_eval(kiln, OUT_OF_SCOPE_ID, spec_status="active", spec_id="424242270337")
    drifted = add_issue(
        kiln, eval_ids=(MANIFEST_ID,), content=setup_owned_content("00000000", "- x")
    )
    repairable = add_issue(kiln, content=f"- eval: {OUT_OF_SCOPE_ID} (built 07-30)")
    orphan = add_issue(kiln, eval_ids=("424242999999",), content="stale finding")
    ctx = make_ctx(kiln)

    plan = await build_sync_plan(ctx)
    report = await apply_sync_plan(ctx, plan, stub_describe())

    assert report.seeded_eval_ids == [HANDOFF_ID]
    assert report.refreshed_eval_ids == [MANIFEST_ID]
    assert report.repaired_issue_ids == [repairable]
    assert report.unmeasurable_issue_ids == [orphan]
    assert report.unbound_unresolved_issue_ids == []
    assert (report.evals_total, report.evals_active) == (3, 3)

    (post,) = kiln.memory_posts
    assert post["tags"] == ["issue", "p1", eval_tag(HANDOFF_ID)]
    assert kiln.memories[drifted]["content"].endswith(
        "- x\n- eval rubric changed; description resynced 07-22"
    )
    assert eval_tag(OUT_OF_SCOPE_ID) in kiln.memories[repairable]["tags"]
    assert kiln.memories[orphan]["overview"].startswith("UNMEASURABLE: ")


async def test_empty_plan_writes_nothing_and_never_describes():
    kiln = FakeKiln()
    ctx = make_ctx(kiln)
    described: list[list[str]] = []

    report = await apply_sync_plan(ctx, SyncPlan(), stub_describe(described))

    assert described == []
    assert kiln.memory_posts == [] and kiln.memory_patches == []
    assert report.seeded_eval_ids == []


async def test_apply_is_idempotent_across_two_runs():
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(kiln)
    await apply_sync_plan(ctx, await build_sync_plan(ctx), stub_describe())
    seeded = dict(kiln.memories["mem_1"])

    await apply_sync_plan(ctx, await build_sync_plan(ctx), stub_describe())

    assert len(kiln.memory_posts) == 1
    assert kiln.memories["mem_1"] == seeded


# ---- reads ----


async def test_non_list_evals_response_is_a_precondition_error():
    kiln = FakeKiln()
    kiln.evals = "<html>not json</html>"

    with pytest.raises(SetupPreconditionError) as excinfo:
        await build_sync_plan(make_ctx(kiln))

    assert f"{TASK_PATH}/evals" in str(excinfo.value)


async def test_non_list_specs_response_is_a_precondition_error():
    kiln = FakeKiln()
    kiln.specs = {"specs": [], "matched": 0}

    with pytest.raises(SetupPreconditionError) as excinfo:
        await build_sync_plan(make_ctx(kiln))

    assert f"{TASK_PATH}/specs" in str(excinfo.value)


async def test_unreadable_eval_config_degrades_to_no_properties():
    # An unreadable config never fails a run — the eval still
    # fingerprints, and re-fingerprints once the shape is readable again.
    kiln = FakeKiln()
    add_eval(kiln)
    kiln.eval_configs[f"ec_{HANDOFF_ID}"] = "<html>not json</html>"

    plan = await build_sync_plan(make_ctx(kiln))

    assert plan.seed[0].config_properties is None
    assert plan.seed[0].config_kind == "unknown"


async def test_eval_without_a_current_config_is_still_read():
    kiln = FakeKiln()
    kiln.evals[HANDOFF_ID] = make_eval(HANDOFF_ID, config_id=None)

    plan = await build_sync_plan(make_ctx(kiln))

    assert [v.id for v in plan.seed] == [HANDOFF_ID]
    assert plan.seed[0].config_properties is None
    # No config route call was made for it.
    assert not [c for c in kiln.calls if "/eval_config/" in c[1]]


# ---- regressions: repair and binding edge cases ----


async def test_repair_onto_an_inactive_eval_still_names_its_cause():
    # Repair resolves against LIVE evals, not active ones, so an issue can be
    # bound AND flagged unmeasurable in one pass. The cause line is built
    # from the RESOLVED binding — reading the (still-untagged) issue wrote a
    # bare `-  (noted 07-22)`, destroying the diagnosis.
    kiln = FakeKiln()
    add_eval(kiln, MANIFEST_ID, spec_status="archived", spec_id="424242130650")
    issue = add_issue(kiln, content=f"- eval: {MANIFEST_ID} (built 07-30, reproduced)")
    ctx = make_ctx(kiln)

    plan = await build_sync_plan(ctx)

    assert [(b.issue.id, b.eval_id) for b in plan.repair] == [(issue, MANIFEST_ID)]
    assert [(i.id, cause) for i, cause in plan.unmeasurable] == [
        (
            issue,
            f"spec 424242130650 archived; eval {MANIFEST_ID} left the active set",
        )
    ]

    await apply_sync_plan(ctx, plan, stub_describe())

    record = kiln.memories[issue]
    assert record["overview"].startswith("UNMEASURABLE: ")
    assert record["content"].endswith(
        f"- spec 424242130650 archived; eval {MANIFEST_ID} "
        "left the active set (noted 07-22)"
    )


async def test_repair_onto_a_deleted_eval_is_impossible():
    # The other half of the same rule: rule 1/2 both gate on live_eval_ids,
    # so a dangling reference is never bound — it is reported instead.
    kiln = FakeKiln()
    issue = add_issue(kiln, content="- eval: 424242999999 (built 07-30)")

    plan = await build_sync_plan(make_ctx(kiln))

    assert plan.repair == []
    assert [i.id for i in plan.unbound_unresolved] == [issue]
    assert plan.unmeasurable == []  # nothing bound ⇒ nothing to flag


async def test_a_dangling_eval_line_does_not_suppress_the_bound_line():
    # Rule 1 falls through (the line names a dead eval), rule 2 binds the
    # live one from prose — and the line for THAT id must still be
    # written, keyed on the id bound rather than on "any line at all".
    kiln = FakeKiln()
    add_eval(kiln, HANDOFF_ID, spec_status="active")
    issue = add_issue(
        kiln,
        content=(
            "- eval: 424242999999 (built 07-30, deleted since)\n"
            f"superseded by eval {HANDOFF_ID} Handoff Block"
        ),
    )
    ctx = make_ctx(kiln)

    plan = await build_sync_plan(ctx)

    assert [(b.eval_id, b.add_line) for b in plan.repair] == [(HANDOFF_ID, True)]

    await apply_sync_plan(ctx, plan, stub_describe())

    assert kiln.memories[issue]["content"].endswith(
        f"- eval: {HANDOFF_ID} (linked by kiln-harness-tuner setup 07-22)"
    )


async def test_config_reads_run_bounded_parallel():
    kiln = FakeKiln()
    for index in range(9):
        add_eval(kiln, f"424242{index:06d}", spec_status="active")
    ctx = make_ctx(ConcurrencyProbe(kiln, is_config_read))

    plan = await build_sync_plan(ctx)

    assert len(plan.seed) == 9
    assert ctx.kiln.max_in_flight == SETUP_CONFIG_READ_CONCURRENCY
    assert ctx.kiln.matched_reads == 9  # one per eval, none repeated


async def test_missing_description_names_the_eval():
    # Fatal either way; this only makes it legible.
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(kiln)

    async def describe_nothing(_ctx, _views):
        return {}

    with pytest.raises(SetupPreconditionError) as excinfo:
        await apply_sync_plan(ctx, await build_sync_plan(ctx), describe_nothing)

    assert HANDOFF_ID in str(excinfo.value)
    assert kiln.memory_posts == []


async def test_repair_backfills_a_missing_priority_tag():
    # A repaired issue is never setup-owned, so it is never refreshed —
    # bind_eval is its only chance at the always-present priority tag.
    kiln = FakeKiln()
    add_eval(kiln, HANDOFF_ID, spec_status="active")
    issue = kiln.add_memory(
        "issue: bare & breaks XML parsing",
        "task::t1",
        ["issue"],  # the shape live records have been found in
        content=f"- eval: {HANDOFF_ID} (built 07-30, reproduced)",
    )
    ctx = make_ctx(kiln)

    await apply_sync_plan(ctx, await build_sync_plan(ctx), stub_describe())

    assert kiln.memories[issue]["tags"] == [
        "issue",
        f"p{DEFAULT_ISSUE_PRIORITY}",
        eval_tag(HANDOFF_ID),
    ]  # seed_issue's order, so repaired and seeded records read alike


async def test_unserializable_spec_issue_description_still_fingerprints():
    # `issue_description` comes straight off the Spec's properties — nothing
    # about a source shape may fail a run.
    view = eval_view(spec=replace(SPEC, issue_description={1, 2}))
    assert len(fingerprint(view)) == 8


# ---- regressions: dangling config, terminal states, read budget ----


async def test_dangling_eval_config_degrades_instead_of_killing_the_run():
    # A `current_config_id` naming a deleted config 404s, and kiln.py never
    # softens a 4xx — unhandled it aborted EVERY invocation with a raw
    # traceback (cli.py catches only the instructive setup errors), with no
    # in-product repair path.
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    del kiln.eval_configs[f"ec_{HANDOFF_ID}"]

    plan = await build_sync_plan(make_ctx(kiln))

    assert [v.id for v in plan.seed] == [HANDOFF_ID]
    assert plan.seed[0].config_properties is None


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
async def test_an_unreadable_eval_config_propagates(status_code):
    # "Could not read it" is NOT "it isn't there". Degrading these would
    # fingerprint the eval over an empty rubric, refresh the whole active set
    # (a PAID describe fan-out), store the wrong fingerprint, and refresh
    # again once the read heals — silently. Failing is free: setup runs
    # before any paid work.
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(FailingConfigRoute(kiln, status_code))

    with pytest.raises(KilnAPIError):
        await build_sync_plan(ctx)

    assert kiln.memory_posts == []


@pytest.mark.parametrize("status_code", sorted(CONFIG_GONE_STATUSES))
async def test_a_gone_eval_config_degrades(status_code):
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(FailingConfigRoute(kiln, status_code))

    plan = await build_sync_plan(ctx)

    assert plan.seed[0].config_properties is None


async def test_a_closed_issue_is_never_flagged_unmeasurable():
    # flag_unmeasurable REPLACES the overview prefix, so flagging a closed
    # issue would erase the loop's own decision — and, since the state would
    # stay terminal, re-flag on every subsequent run.
    kiln = FakeKiln()
    add_eval(kiln, MANIFEST_ID, spec_status="archived", spec_id="424242130650")
    fixed = add_issue(
        kiln,
        overview="FIXED: issue: quantities do not match the manifest",
        eval_ids=(MANIFEST_ID,),
    )
    wont_fix = add_issue(
        kiln, overview="WONT_FIX: issue: phantom timeout", eval_ids=(MANIFEST_ID,)
    )
    ctx = make_ctx(kiln)

    plan = await build_sync_plan(ctx)
    await apply_sync_plan(ctx, plan, stub_describe())

    assert plan.unmeasurable == []
    assert (
        kiln.memories[fixed]["overview"]
        == "FIXED: issue: quantities do not match the manifest"
    )
    assert kiln.memories[wont_fix]["overview"] == "WONT_FIX: issue: phantom timeout"
    assert kiln.memory_patches == []


async def test_an_open_issue_on_the_same_eval_is_still_flagged():
    # The guard is about terminal states only — the finding on an OPEN issue
    # may still be true, which is why it flags rather than closes.
    kiln = FakeKiln()
    add_eval(kiln, MANIFEST_ID, spec_status="archived", spec_id="424242130650")
    issue = add_issue(kiln, eval_ids=(MANIFEST_ID,))

    plan = await build_sync_plan(make_ctx(kiln))

    assert [i.id for i, _cause in plan.unmeasurable] == [issue]


@pytest.mark.parametrize(
    "raw,expected", [(0, 0), (3, 3), (7, 3), (-1, 0), (None, 1), (True, 1), ("2", 1)]
)
def test_spec_priority_is_bounded_at_the_read_boundary(raw, expected):
    # The priority becomes a `p<n>` tag and the tag regex only knows p0–p3:
    # `p7` would be written once and never recognized as a priority tag
    # again, leaving two priority-shaped tags on the record.
    spec = SpecView.from_api(make_spec(priority=raw))
    assert spec.priority == expected


async def test_out_of_range_spec_priority_writes_a_replaceable_tag():
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active", priority=9)
    ctx = make_ctx(kiln)

    await apply_sync_plan(ctx, await build_sync_plan(ctx), stub_describe())

    assert kiln.memory_posts[0]["tags"] == ["issue", "p3", eval_tag(HANDOFF_ID)]


async def test_has_writes_is_false_while_unbound_issues_remain():
    # `is_empty` can never be True on a task carrying report-only issues —
    # most of one live task's records were exactly that — so the
    # `setup_ok` event needs has_writes.
    kiln = FakeKiln()
    unresolved = add_issue(kiln, content="no eval was ever built for this")
    ctx = make_ctx(kiln)

    plan = await build_sync_plan(ctx)

    assert not plan.has_writes
    assert not plan.is_empty
    assert [i.id for i in plan.unbound_unresolved] == [unresolved]

    await apply_sync_plan(ctx, plan, stub_describe())

    assert kiln.memory_posts == [] and kiln.memory_patches == []


async def test_a_baseline_audit_issue_is_neither_repaired_nor_reported():
    # It names an eval id in its prose, which would repair an ordinary
    # unbound issue onto that eval; it is unbound on purpose.
    kiln = FakeKiln()
    eval_id = add_eval(kiln, spec_status="active")
    audit = kiln.add_memory(
        "issue: the baseline's own text copies train items (1 finding(s))",
        "task::t1",
        ["issue", "p1", "baseline_audit", "audited_rc_rc1"],
        content=f"- the prompt quotes eval {eval_id} by id",
    )
    ctx = make_ctx(kiln)

    plan = await build_sync_plan(ctx)

    assert audit not in [i.id for i in plan.unbound_unresolved]
    assert audit not in [r.issue.id for r in plan.repair]


async def test_eval_and_spec_listings_are_read_in_parallel():
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(ConcurrencyProbe(kiln, is_listing_read))

    await build_sync_plan(ctx)

    assert ctx.kiln.max_in_flight == 2


async def test_warm_invocation_read_budget():
    """The warm-run read budget is `5 + one config per eval`.

    Through `run_setup`, the whole phase: the baseline decision, the sync, and
    the split audit — which is a pure local pass and adds no read at all,
    which is why this is an extension rather than a second test. Writes and
    describe dispatches are zero on a fully synced task."""
    kiln = FakeKiln()
    for index in range(3):
        add_eval(kiln, f"424242{index:06d}", spec_status="active")
    ctx = make_ctx(kiln)
    # Cold pass first, so the task is fully synced going into the warm one.
    await apply_sync_plan(ctx, await build_sync_plan(ctx), stub_describe())
    baseline_record(
        kiln, "Baseline: run config rc_1 — seeded 07-22 by kiln-harness-tuner setup"
    )
    writes_before = len(kiln.memory_posts) + len(kiln.memory_patches)
    kiln.calls.clear()

    report = await run_setup(ctx)

    # Warm: the record was left exactly as it was, and nothing was described.
    assert report.baseline_rule is None
    assert ctx.dispatch.calls == []
    assert [k for k, _ in ctx.log.events] == ["setup_ok"]
    assert len(kiln.memory_posts) + len(kiln.memory_patches) == writes_before
    # Exact equality over EVERY call the phase made — so this also asserts no
    # stray read and no write to any route. Compared as a sorted multiset
    # (duplicates preserved) rather than a sequence: three of the read groups
    # are gathered, and the interleaving is an implementation detail while
    # the read SET is the contract.
    assert sorted(path for _method, path, _params, _body in kiln.calls) == sorted(
        [
            "/api/projects/p1/memories",  # find_baseline_record listing
            f"{TASK_PATH}/evals",
            f"{TASK_PATH}/specs",
            f"{TASK_PATH}/evals/424242000000/eval_config/ec_424242000000",
            f"{TASK_PATH}/evals/424242000001/eval_config/ec_424242000001",
            f"{TASK_PATH}/evals/424242000002/eval_config/ec_424242000002",
            "/api/projects/p1/memories",  # list_issues listing
            "/api/projects/p1/memories/by_ids",  # list_issues hydration
        ]
    )


# ===========================================================================
# the description fan-out
# ===========================================================================


class DescribeProbe:
    """Wraps a dispatcher to watch how many `describe_eval` sessions are in
    flight at once — the ConcurrencyProbe shape, on the dispatch lane.

    Same reason for the inner await: the fake never suspends on its own, so
    without a real suspension point every gathered coroutine would run to
    completion in turn and ANY bound would look respected."""

    def __init__(self, dispatcher: Any):
        self._dispatcher = dispatcher
        self.max_in_flight = 0
        self._in_flight = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._dispatcher, name)

    async def describe_eval(self, req: Any) -> Any:
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(0)
            return await self._dispatcher.describe_eval(req)
        finally:
            self._in_flight -= 1


class ScriptedDescribe:
    """A dispatcher scripted PER EVAL, which the shared FIFO queues cannot
    express — the fan-out's failure and cancellation paths need to fail one
    eval while another is still running. `"hang"` waits forever (only a
    cancellation ends it) and records that it was cancelled."""

    def __init__(self, responses: dict[str, list[Any]]):
        self.responses = responses
        self.calls: list[Any] = []
        self.cancelled: list[str] = []

    async def describe_eval(self, req: Any) -> Any:
        self.calls.append(req)
        entry = self.responses[req.eval_id].pop(0)
        if entry == "hang":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.append(req.eval_id)
                raise
        return entry


def yielding_sleep(recorder: list[float]):
    """A `ctx.sleep` that actually suspends, so the retry backoff hands the
    loop to the fan-out's other tasks (the default fake sleep never yields,
    and a task would then run start-to-finish without interleaving)."""

    async def sleep(secs: float) -> None:
        recorder.append(secs)
        await asyncio.sleep(0)

    return sleep


async def test_describe_all_dispatches_one_agent_per_eval():
    # One session per eval, never batched: batching invites
    # cross-contamination between unrelated criteria.
    ctx = make_ctx()
    with_spec = eval_view(HANDOFF_ID, spec=SPEC)
    without_spec = eval_view(MANIFEST_ID, spec=None, name="Manifest Accuracy")

    descriptions, cost_usd = await describe_all(ctx, [with_spec, without_spec])

    assert set(descriptions) == {HANDOFF_ID, MANIFEST_ID}
    requests = ctx.dispatch.calls_to("describe_eval")
    assert [(r.eval_id, r.eval_name, r.spec_id) for r in requests] == [
        (HANDOFF_ID, "Handoff Block", SPEC.id),
        (MANIFEST_ID, "Manifest Accuracy", None),  # spec-less evals are active too
    ]
    assert cost_usd == 0.0


async def test_describe_all_is_bounded_parallel():
    ctx = make_ctx(dispatcher=DescribeProbe(FakeDispatcher()))
    views = [eval_view(f"424242{index:06d}") for index in range(9)]

    descriptions, _cost = await describe_all(ctx, views)

    assert len(descriptions) == 9
    assert ctx.dispatch.max_in_flight == SETUP_DESCRIBE_CONCURRENCY


async def test_describe_all_retries_once_then_succeeds():
    ctx = make_ctx()
    ctx.dispatch.queue("describe_eval", infra("provider_error"), ok(DESCRIPTION))

    descriptions, _cost = await describe_all(ctx, [eval_view()])

    assert descriptions == {HANDOFF_ID: DESCRIPTION}
    assert ctx.sleeps == list(SETUP_DESCRIBE_BACKOFFS)  # one retry, not the ladder


async def test_two_failures_are_fatal_and_name_the_eval():
    # Deliberately harsher than the item ladder — a cheap agent against
    # an expensive loop. The message has to say WHICH eval and why.
    ctx = make_ctx()
    ctx.dispatch.queue(
        "describe_eval",
        infra("rate_limit", "429 slow down"),
        infra("rate_limit", "429 slow down"),
    )

    with pytest.raises(SetupDescribeError) as excinfo:
        await describe_all(ctx, [eval_view(name="Handoff Block")])

    message = str(excinfo.value)
    assert HANDOFF_ID in message and "Handoff Block" in message
    # The ladder's own count, never inferred from the status here.
    assert "after 2 attempts (rate_limit: 429 slow down)" in message
    assert excinfo.value.eval_id == HANDOFF_ID


@pytest.mark.parametrize(
    "result,reason",
    [
        # The agent reported it cannot read the eval — `describe_eval.md`
        # instructs exactly this, and the ladder never retries a verdict.
        (
            expected("eval 424242966189 returned 404"),
            "eval 424242966189 returned 404",
        ),
        # `parse_failure` IS failed_infra and is STILL terminal (its
        # in-session repair already ran) — the likeliest failure for a small
        # agent that has to return a fenced envelope, and the case a
        # status-derived guess got wrong.
        (
            infra("parse_failure", "no fenced block"),
            "parse_failure: no fenced block",
        ),
    ],
    ids=["agent_verdict", "parse_failure"],
)
async def test_an_unretried_failure_is_never_described_as_a_retry(result, reason):
    ctx = make_ctx()
    ctx.dispatch.queue("describe_eval", result)

    with pytest.raises(SetupDescribeError) as excinfo:
        await describe_all(ctx, [eval_view()])

    assert f"on its first attempt ({reason})" in str(excinfo.value)
    assert ctx.sleeps == []  # and no backoff was spent, either
    assert len(ctx.dispatch.calls_to("describe_eval")) == 1


async def test_a_fatal_description_writes_nothing():
    # Every description is in hand before the first write, so a fatal leaves
    # the store untouched — no half-seeded requirement set.
    kiln = FakeKiln()
    kiln.run_configs = [make_run_config("rc_1")]
    add_eval(kiln, spec_status="active")
    ctx = make_ctx(kiln)
    ctx.dispatch.queue("describe_eval", infra(), infra())

    with pytest.raises(SetupDescribeError):
        await run_setup(ctx)

    assert issue_posts(kiln) == [] and kiln.memory_patches == []


async def test_describe_spend_is_logged_even_when_the_dispatch_fails():
    # A failed attempt cost real money and must reach the spend accounting.
    ctx = make_ctx()
    ctx.dispatch.queue(
        "describe_eval",
        infra().model_copy(update={"usage": ok(DESCRIPTION, cost_usd=0.02).usage}),
        infra().model_copy(update={"usage": ok(DESCRIPTION, cost_usd=0.03).usage}),
    )

    with pytest.raises(SetupDescribeError):
        await describe_all(ctx, [eval_view()])

    spends = [f for k, f in ctx.log.events if k == "spend"]
    assert [(f["source"], f["lane"], f["cost_usd"]) for f in spends] == [
        ("describe_eval", "subscription_equivalent", 0.05)  # both attempts
    ]


async def test_a_failed_description_cancels_its_siblings():
    # `asyncio.gather` propagates the first exception but does NOT cancel the
    # others: left alone they outlive the run that paid for them (real
    # `claude -p` subprocesses, and a pending-task warning at teardown).
    dispatcher = ScriptedDescribe(
        {HANDOFF_ID: [infra(), infra()], MANIFEST_ID: ["hang"]}
    )
    ctx = make_ctx(dispatcher=dispatcher)
    ctx.sleep = yielding_sleep([])

    with pytest.raises(SetupDescribeError):
        await describe_all(ctx, [eval_view(HANDOFF_ID), eval_view(MANIFEST_ID)])

    assert dispatcher.cancelled == [MANIFEST_ID]


# ===========================================================================
# split readiness audit
# ===========================================================================


def test_audit_passes_when_every_active_eval_has_both_splits():
    assert audit_splits([eval_view(HANDOFF_ID), eval_view(MANIFEST_ID)]) is None


def test_audit_lists_every_failing_eval_at_once():
    # Never one at a time — the whole point is that an owner
    # can fix the task in a single pass.
    views = [
        replace(eval_view("424242000003", name="No val"), val_filter=None),
        replace(eval_view("424242000004", name="No train"), train_filter=None),
        replace(
            eval_view("424242000005", name="Neither"),
            train_filter=None,
            val_filter=None,
        ),
        eval_view("424242000006", name="Fine"),
    ]

    with pytest.raises(EvalSplitAuditError) as excinfo:
        audit_splits(views)

    message = str(excinfo.value)
    assert message.startswith("3 evals are missing a train/val split")
    assert '  • 424242000003 "No val" — no val split' in message
    assert '  • 424242000004 "No train" — no train split' in message
    # "and", not "or": an eval missing both must not read as missing one.
    assert '  • 424242000005 "Neither" — no train and no val split' in message
    assert "424242000006" not in message
    # The fix instruction, and the promise that setup will not do it for them.
    assert "does not create or repair splits" in message
    assert "then re-invoke `kiln-harness-tuner`" in message


def test_audit_ignores_inactive_evals():
    # An eval outside the active set is never measured, so its missing split
    # cannot kill a run — flagging it would just be noise an owner can't act
    # on (they already deactivated it).
    archived = replace(SPEC, status="archived")
    views = [replace(eval_view(spec=archived), val_filter=None)]

    assert audit_splits(views) is None


async def test_audit_runs_after_the_sync_so_a_cold_task_is_still_seeded():
    # One round trip, not two — the invocation that reports what to
    # fix is the same one that seeds the store.
    kiln = FakeKiln()
    kiln.run_configs = [make_run_config("rc_1")]
    add_eval(kiln, spec_status="active", val_filter=None)
    ctx = make_ctx(kiln)

    with pytest.raises(EvalSplitAuditError):
        await run_setup(ctx)

    (seeded,) = issue_posts(kiln)
    assert seeded["tags"] == ["issue", "p1", eval_tag(HANDOFF_ID)]
    # The sync event landed before the fatal — the wave log records writes
    # that really happened.
    assert [k for k, _ in ctx.log.events if k.startswith("setup")] == ["setup_sync"]


# ===========================================================================
# run_setup
# ===========================================================================


async def test_run_setup_on_a_cold_task_seeds_syncs_and_reports():
    kiln = FakeKiln()
    kiln.run_configs = [make_run_config("rc_only")]
    add_eval(kiln, HANDOFF_ID, spec_status="active")
    add_eval(kiln, MANIFEST_ID, spec_status="active", spec_id="424242130650")
    unresolved = add_issue(kiln, content="no eval was ever built for this")
    ctx = make_ctx(kiln)

    report = await run_setup(ctx)

    assert report.seeded_eval_ids == [HANDOFF_ID, MANIFEST_ID]
    assert report.unbound_unresolved_issue_ids == [unresolved]
    assert (report.baseline_rc_id, report.baseline_rule) == (
        "rc_only",
        BASELINE_RULE_MOST_RECENT,
    )
    events = dict(ctx.log.events)
    assert events["baseline_seeded"] == {
        "rc_id": "rc_only",
        "rule": BASELINE_RULE_MOST_RECENT,
        "unresolvable_default": None,
    }
    # The field set, including the fan-out's cost.
    assert events["setup_sync"] == {
        "evals_total": 2,
        "evals_active": 2,
        "seeded": 2,
        "refreshed": 0,
        "repaired": 0,
        "unmeasurable": 0,
        "unbound_unresolved": 1,
        "cost_usd": 0.0,
        "report": report,
    }
    assert "setup_ok" not in events


async def test_run_setup_reports_an_unresolvable_default():
    # The one diagnosis the record's provenance cannot carry: a default WAS
    # set, and could not be honored.
    kiln = FakeKiln()
    kiln.task["default_run_config_id"] = "rc_deleted"
    kiln.run_configs = [make_run_config("rc_live")]
    ctx = make_ctx(kiln)

    await run_setup(ctx)

    assert dict(ctx.log.events)["baseline_seeded"]["unresolvable_default"] == (
        "rc_deleted"
    )


async def test_run_setup_sums_the_fan_outs_cost_into_the_sync_event():
    # Two evals with distinct costs: proves both accumulators — the one
    # `describe_one` shares across concurrent tasks, and `run_setup`'s own.
    kiln = FakeKiln()
    kiln.run_configs = [make_run_config("rc_1")]
    add_eval(kiln, HANDOFF_ID, spec_status="active")
    add_eval(kiln, MANIFEST_ID, spec_status="active", spec_id="424242130650")
    ctx = make_ctx(kiln)
    ctx.dispatch.queue(
        "describe_eval", ok(DESCRIPTION, cost_usd=0.07), ok(DESCRIPTION, cost_usd=0.03)
    )

    await run_setup(ctx)

    events = dict(ctx.log.events)
    assert events["setup_sync"]["cost_usd"] == pytest.approx(0.10)
    # …and each session's own spend still lands separately for the spend accounting.
    assert sorted(
        f["cost_usd"] for k, f in ctx.log.events if k == "spend"
    ) == pytest.approx([0.03, 0.07])


async def test_run_setup_on_a_warm_task_emits_setup_ok_with_the_same_shape():
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    baseline_record(
        kiln, "Baseline: run config rc_1 — seeded 07-22 by kiln-harness-tuner setup"
    )
    ctx = make_ctx(kiln)
    await run_setup(ctx)  # cold pass: seeds the issue
    ctx.log.events.clear()

    report = await run_setup(ctx)

    (kind, fields) = next(
        (k, f) for k, f in ctx.log.events if k in ("setup_sync", "setup_ok")
    )
    assert kind == "setup_ok"
    # Same field set as setup_sync — every consumer reads one shape.
    assert fields == {
        "evals_total": 1,
        "evals_active": 1,
        "seeded": 0,
        "refreshed": 0,
        "repaired": 0,
        "unmeasurable": 0,
        "unbound_unresolved": 0,
        "cost_usd": 0.0,
        "report": report,
    }


async def test_setup_ok_fires_while_unbound_issues_remain():
    # `has_writes`, not `is_empty`: a permanently report-only issue store —
    # most of one live task's records — is still a warm run.
    kiln = FakeKiln()
    baseline_record(
        kiln, "Baseline: run config rc_1 — seeded 07-22 by kiln-harness-tuner setup"
    )
    unresolved = add_issue(kiln, content="a project note, not an eval issue")
    ctx = make_ctx(kiln)

    report = await run_setup(ctx)

    assert [k for k, _ in ctx.log.events] == ["setup_ok"]
    assert report.unbound_unresolved_issue_ids == [unresolved]


async def test_eval_and_issue_reads_run_in_parallel():
    # The outermost of build_sync_plan's three gathers: the eval-side reads
    # and the issue store are independent, and a wide task pays for every
    # sequential round trip on EVERY invocation.
    kiln = FakeKiln()
    add_eval(kiln, spec_status="active")
    add_issue(kiln, eval_ids=(HANDOFF_ID,))
    ctx = make_ctx(ConcurrencyProbe(kiln, is_read_group_entry))

    await build_sync_plan(ctx)

    assert ctx.kiln.max_in_flight == 2
