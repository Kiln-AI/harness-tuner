"""The API-key check: which providers the run needs (`needed_providers`), and
which of them Kiln has no key for (`key_problem`)."""

import json
from pathlib import Path

import pytest
from onboarding_fakes import (
    HOST,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    Routes,
    available_models,
    eval_config,
    memories_path,
    memory_listing,
    ready_task_routes,
    run_config,
    task_base,
)

from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.model_policy import ModelPolicy, Rule
from kiln_harness_tuner.onboarding.keys import (
    NO_KEY_PROVIDERS,
    SIMULATED_USER,
    Baseline,
    Need,
    judge_provider,
    key_problem,
    keys_passed_text,
    needed_providers,
    simulated_user_providers,
)
from kiln_harness_tuner.onboarding.terminal import Action, ActionKind
from kiln_harness_tuner.setup import SetupPreconditionError

BASE = task_base(PROJECT_ID, TASK_ID)
BASELINE_RC = 'the task\'s run config "Baseline"'
TONE_JUDGE = 'eval "Tone"\'s judge'
POLICY = "--model-policy"
LEGACY_JUDGE = eval_config({"eval_steps": ["Is it polite?"]}, model_provider="openai")
V2_JUDGE = eval_config(
    {
        "type": "llm_judge",
        "model_name": "claude_4_5_haiku",
        "model_provider": "anthropic",
        "prompt_template": "Is it polite?",
    }
)
EMPTY = Path("/no/such/project")


def on(suffix: str, body: object) -> Routes:
    """The task's own read at `suffix` ("" for the task itself)."""
    return {("GET", f"{BASE}{suffix}"): body}


def kiln_with(*overrides: Routes) -> RouteKiln:
    """A Kiln whose task is ready, with a baseline on `fireworks_ai` and a
    code eval, except for the reads given."""
    routes = ready_task_routes(PROJECT_ID, TASK_ID)
    for override in overrides:
        routes.update(override)
    return RouteKiln(routes)


def baseline_memory(*tags: str) -> Routes:
    """The tuner's `baseline` record for the task, with `tags` beside
    `baseline`: the listing and the read of the full record."""
    record = {
        "id": "424242000001",
        "overview": "Baseline: run config rc_2",
        "tags": ["baseline", *tags],
        "scope": f"task::{TASK_ID}",
        "content": None,
    }
    path = memories_path(PROJECT_ID)
    return {
        ("GET", path): memory_listing(record),
        ("GET", f"{path}/by_ids"): [record],
    }


async def needs_and_baseline(
    kiln: RouteKiln, *, project_path: Path = EMPTY, policy: ModelPolicy | None = None
) -> tuple[list[Need], Baseline | None]:
    return await needed_providers(
        kiln,
        project_id=PROJECT_ID,
        task_id=TASK_ID,
        project_path=project_path,
        policy=policy,
    )


async def needs_of(
    kiln: RouteKiln, *, project_path: Path = EMPTY, policy: ModelPolicy | None = None
) -> list[Need]:
    needs, _baseline = await needs_and_baseline(
        kiln, project_path=project_path, policy=policy
    )
    return needs


async def baseline_of(kiln: RouteKiln) -> Baseline | None:
    _needs, baseline = await needs_and_baseline(kiln)
    return baseline


# ---- the baseline ----

TWO_RUN_CONFIGS = [
    run_config("rc_1", "Default", "openai", created_at="2026-07-01T09:00:00-04:00"),
    run_config("rc_2", "Tuned", "groq", created_at="2026-07-30T09:00:00-04:00"),
]


async def test_the_baseline_is_the_one_the_tuner_recorded():
    kiln = kiln_with(
        on("", {"id": TASK_ID, "default_run_config_id": "rc_1"}),
        on("/run_configs", TWO_RUN_CONFIGS),
        baseline_memory("rc_rc_2"),
    )
    assert await needs_of(kiln) == [Need("groq", 'the task\'s run config "Tuned"')]
    # One listing finds the record, and one read hydrates it.
    (listing,) = [c for c in kiln.calls if c.path == memories_path(PROJECT_ID)]
    assert (listing.query["scope"], listing.query["tags"]) == (
        f"task::{TASK_ID}",
        ["baseline"],
    )
    assert kiln.paths().count(f"{memories_path(PROJECT_ID)}/by_ids") == 1
    # The check reads the rule; it never writes the record.
    assert {call.method for call in kiln.calls} == {"GET"}


async def test_with_no_record_the_baseline_is_the_task_default():
    kiln = kiln_with(
        on("", {"id": TASK_ID, "default_run_config_id": "rc_1"}),
        on("/run_configs", TWO_RUN_CONFIGS),
    )
    assert await needs_of(kiln) == [Need("openai", 'the task\'s run config "Default"')]
    assert {call.method for call in kiln.calls} == {"GET"}
    assert kiln.paths().count(memories_path(PROJECT_ID)) == 1
    assert f"{memories_path(PROJECT_ID)}/by_ids" not in kiln.paths()


async def test_with_no_record_and_no_default_the_baseline_is_the_most_recent():
    kiln = kiln_with(on("/run_configs", TWO_RUN_CONFIGS))
    assert await needs_of(kiln) == [Need("groq", 'the task\'s run config "Tuned"')]


async def test_a_record_that_names_no_run_config_gives_no_baseline_need():
    kiln = kiln_with(baseline_memory())
    assert await needs_of(kiln) == []


@pytest.mark.parametrize("rows", [None, [], [{"name": "no id"}]])
async def test_no_usable_run_config_gives_no_baseline_need(rows):
    assert await needs_of(kiln_with(on("/run_configs", rows))) == []


async def test_a_run_config_with_no_model_gives_no_baseline_need():
    kiln = kiln_with(on("/run_configs", [run_config(provider=None)]))
    assert await needs_of(kiln) == []


async def test_a_run_config_with_no_name_is_named_by_its_id():
    kiln = kiln_with(on("/run_configs", [run_config("rc_9", name="")]))
    assert await needs_of(kiln) == [
        Need("fireworks_ai", 'the task\'s run config "rc_9"')
    ]


# ---- the baseline, for the summary ----

LLAMA = "llama-v3p1-8b-instruct"


async def test_the_baseline_is_named_with_its_model_and_provider():
    kiln = kiln_with(
        on("", {"id": TASK_ID, "default_run_config_id": "rc_1"}),
        on("/run_configs", TWO_RUN_CONFIGS),
    )
    assert await baseline_of(kiln) == Baseline(
        "Default", LLAMA, "OpenAI", "rc_1", "openai"
    )


async def test_the_baseline_the_tuner_recorded_is_the_one_named():
    kiln = kiln_with(on("/run_configs", TWO_RUN_CONFIGS), baseline_memory("rc_rc_2"))
    assert await baseline_of(kiln) == Baseline("Tuned", LLAMA, "Groq", "rc_2", "groq")


async def test_a_baseline_with_no_model_is_named_alone():
    kiln = kiln_with(on("/run_configs", [run_config("rc_9", name="", provider=None)]))
    assert await baseline_of(kiln) == Baseline("rc_9", None, None, "rc_9")


async def test_a_provider_with_no_display_name_is_named_by_its_id():
    kiln = kiln_with(on("/run_configs", [run_config(provider="new_cloud")]))
    assert await baseline_of(kiln) == Baseline(
        "Baseline", LLAMA, "new_cloud", "rc_1", "new_cloud"
    )


@pytest.mark.parametrize(
    "routes",
    [on("/run_configs", []), baseline_memory()],
    ids=["no run configs", "a record that names none"],
)
async def test_no_baseline_run_config_names_none(routes):
    assert (
        await baseline_of(kiln_with(on("/run_configs", TWO_RUN_CONFIGS), routes))
        is None
    )


async def test_a_task_that_isnt_an_object_is_a_precondition_error():
    with pytest.raises(SetupPreconditionError, match=BASE):
        await needs_of(kiln_with(on("", "<html>not json</html>")))


# ---- the judges ----


@pytest.mark.parametrize(
    ("config", "provider"),
    [
        (LEGACY_JUDGE, "openai"),
        (V2_JUDGE, "anthropic"),
        (eval_config(), None),  # a code eval calls no model
        (eval_config({"type": "llm_judge", "model_provider": ""}), None),
        (eval_config({"type": "exact_match", "model_provider": "openai"}), None),
        (eval_config({"type": "llm_judge"}, model_provider=""), None),
        ("not a config", None),
        (None, None),
    ],
)
def test_judge_provider(config, provider):
    assert judge_provider(config) == provider


async def test_each_measured_evals_judge_is_needed():
    kiln = kiln_with(
        on(
            "/evals",
            [
                {"id": "eval_1", "name": "Tone", "current_config_id": "ec_1"},
                {"id": "eval_2", "name": "Accuracy", "current_config_id": "ec_2"},
                {"id": "eval_3", "name": "Format", "current_config_id": "ec_3"},
            ],
        ),
        on("/evals/eval_1/eval_config/ec_1", LEGACY_JUDGE),
        on("/evals/eval_2/eval_config/ec_2", V2_JUDGE),
        on("/evals/eval_3/eval_config/ec_3", eval_config()),
    )
    assert await needs_of(kiln) == [
        Need("fireworks_ai", BASELINE_RC),
        Need("openai", TONE_JUDGE),
        Need("anthropic", 'eval "Accuracy"\'s judge'),
    ]


async def test_evals_the_loop_doesnt_measure_are_not_read():
    kiln = kiln_with(
        on(
            "/evals",
            [
                {"id": "eval_1", "name": "Tone", "current_config_id": "ec_1"},
                {"id": "eval_2", "name": "Archived", "current_config_id": "ec_2"},
                {"id": "eval_3", "name": "No config"},
            ],
        ),
        on("/specs", [{"eval_id": "eval_2", "status": "archived"}]),
        on("/evals/eval_1/eval_config/ec_1", LEGACY_JUDGE),
    )
    assert await needs_of(kiln) == [
        Need("fireworks_ai", BASELINE_RC),
        Need("openai", TONE_JUDGE),
    ]
    assert [path for path in kiln.paths() if "/eval_config/" in path] == [
        f"{BASE}/evals/eval_1/eval_config/ec_1"
    ]


@pytest.mark.parametrize("status", [404, 410])
async def test_a_gone_eval_config_is_skipped(status):
    gone = KilnAPIError("GET", "config", status, {"detail": "Not Found"})
    kiln = kiln_with(on("/evals/eval_1/eval_config/ec_1", gone))
    assert await needs_of(kiln) == [Need("fireworks_ai", BASELINE_RC)]


@pytest.mark.parametrize("status", [400, 422])
async def test_an_eval_kiln_cant_load_needs_no_key(status):
    unloadable = KilnAPIError(
        "GET", "config", status, {"error_messages": ["Invalid Jinja2 template"]}
    )
    kiln = kiln_with(
        on(
            "/evals",
            [
                {"id": "eval_1", "name": "Tone", "current_config_id": "ec_1"},
                {"id": "eval_2", "name": "Accuracy", "current_config_id": "ec_2"},
            ],
        ),
        on("/evals/eval_1/eval_config/ec_1", unloadable),
        on("/evals/eval_2/eval_config/ec_2", V2_JUDGE),
    )
    assert await needs_of(kiln) == [
        Need("fireworks_ai", BASELINE_RC),
        Need("anthropic", 'eval "Accuracy"\'s judge'),
    ]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_an_eval_config_that_cant_be_read_raises(status):
    broken = KilnAPIError("GET", "config", status, {"detail": "boom"})
    with pytest.raises(KilnAPIError):
        await needs_of(kiln_with(on("/evals/eval_1/eval_config/ec_1", broken)))


# ---- the simulated user ----


def write_input(task_dir: Path, name: str, content: object) -> None:
    folder = task_dir / "eval_inputs" / name
    folder.mkdir(parents=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (folder / "eval_input.kiln").write_text(text)


def multi_turn_input(provider: object) -> dict:
    return {
        "id": "424242000100",
        "data": {
            "type": "multi_turn_synthetic",
            "first_message": {"text": "My order is late."},
            "drive_config": {"model_name": "m", "model_provider": provider, "turns": 3},
        },
        "reference": {"expected_action": "refund"},
    }


@pytest.fixture
def project_with_inputs(tmp_path: Path) -> Path:
    """A project whose task has multi-turn eval inputs, with odd files beside
    them, and a sibling task whose inputs must not be read."""
    task_dir = tmp_path / "tasks" / f"{TASK_ID} - Support Chat"
    write_input(task_dir, "a", multi_turn_input("openrouter"))
    write_input(
        task_dir,
        "b",
        {"id": "b", "data": json.dumps(multi_turn_input("anthropic")["data"])},
    )
    write_input(task_dir, "c", multi_turn_input("openrouter"))
    write_input(task_dir, "d", "{not json")
    write_input(task_dir, "e", {"data": {"type": "single_turn"}})
    write_input(task_dir, "f", multi_turn_input(""))
    write_input(task_dir, "g", {"data": "not json either"})
    write_input(task_dir, "h", ["not", "an", "object"])
    write_input(task_dir, "i", {"data": {"drive_config": "not an object"}})
    sibling = tmp_path / "tasks" / "424242000999 - Other"
    write_input(sibling, "a", multi_turn_input("groq"))
    return tmp_path


def test_simulated_user_providers_reads_each_inputs_drive_config(project_with_inputs):
    assert simulated_user_providers(project_with_inputs, TASK_ID) == {
        "openrouter",
        "anthropic",
    }


def test_a_project_with_no_task_folder_has_no_simulated_user(tmp_path):
    assert simulated_user_providers(tmp_path, TASK_ID) == set()


async def test_a_multi_turn_task_needs_its_simulated_users_providers(
    project_with_inputs,
):
    kiln = kiln_with(on("", {"id": TASK_ID, "turn_mode": "multiturn"}))
    needs = await needs_of(kiln, project_path=project_with_inputs)
    assert needs == [
        Need("fireworks_ai", BASELINE_RC),
        Need("anthropic", SIMULATED_USER),
        Need("openrouter", SIMULATED_USER),
    ]


async def test_a_single_turn_task_has_no_simulated_user(project_with_inputs):
    needs = await needs_of(kiln_with(), project_path=project_with_inputs)
    assert needs == [Need("fireworks_ai", BASELINE_RC)]


# ---- the policy, and the order of it all ----


async def test_the_policy_adds_each_provider_it_names_exactly():
    policy = ModelPolicy(
        allow=(
            Rule("openai", "gpt-*"),
            Rule("*", "llama-*"),
            Rule("fireworks_*", "*"),
            Rule("open?i", "*"),
            Rule("[ab]nthropic", "*"),
        ),
        deny=(Rule("groq", "*"),),
        exceptions=(Rule("anthropic", "claude_4_5_haiku"),),
    )
    needs = await needs_of(kiln_with(), policy=policy)
    assert needs == [
        Need("fireworks_ai", BASELINE_RC),
        Need("openai", POLICY, from_policy=True),
        Need("anthropic", POLICY, from_policy=True),
    ]


async def test_the_needs_come_baseline_judges_simulated_user_then_policy(
    project_with_inputs,
):
    kiln = kiln_with(
        on("", {"id": TASK_ID, "turn_mode": "multiturn"}),
        on("/evals/eval_1/eval_config/ec_1", LEGACY_JUDGE),
    )
    policy = ModelPolicy(allow=(Rule("cerebras", "*"),))
    needs = await needs_of(kiln, project_path=project_with_inputs, policy=policy)
    assert [need.used_by for need in needs] == [
        BASELINE_RC,
        TONE_JUDGE,
        SIMULATED_USER,
        SIMULATED_USER,
        POLICY,
    ]


# ---- key_problem ----


async def problem_for(needs: list[Need], available: object) -> tuple:
    kiln = RouteKiln({("GET", "/api/available_models"): available})
    return await key_problem(kiln, host=HOST, needs=needs), kiln


async def test_no_needs_make_no_call():
    problem, kiln = await problem_for([], available_models())
    assert problem is None and kiln.calls == []


async def test_providers_that_take_no_key_make_no_call():
    needs = [Need(provider, "x") for provider in sorted(NO_KEY_PROVIDERS)]
    problem, kiln = await problem_for(needs, available_models())
    assert problem is None and kiln.calls == []


async def test_every_provider_with_a_key_is_no_problem():
    needs = [Need("fireworks_ai", BASELINE_RC), Need("openai", TONE_JUDGE)]
    problem, kiln = await problem_for(needs, available_models("openai", "fireworks_ai"))
    assert problem is None
    # Only the list of providers with keys is read, never the keys.
    assert kiln.paths() == ["/api/available_models"]


PROVIDERS_ACTION = Action(
    "p",
    "Open AI Providers in Kiln",
    ActionKind.OPEN,
    url="http://localhost:8757/settings/providers",
)


async def test_the_message_names_each_missing_provider_and_what_needs_it():
    needs = [
        Need("fireworks_ai", BASELINE_RC),
        Need("anthropic", 'eval "Accuracy"\'s judge'),
        Need("openai", TONE_JUDGE),
    ]
    problem, _kiln = await problem_for(needs, available_models("anthropic"))
    assert problem.title == "Kiln is missing 2 API keys this run needs"
    assert problem.body == (
        "The tuner calls these providers through Kiln, which needs a key for each:",
        '  • Fireworks AI: used by the task\'s run config "Baseline"',
        '  • OpenAI: used by eval "Tone"\'s judge',
        "Add the keys in Kiln under Settings → AI Providers.",
    )
    assert problem.actions == (PROVIDERS_ACTION,)
    assert problem.retry_label == "Check again (I've added the keys)"
    assert [a.key for a in problem.menu()] == ["r", "p", "x"]


async def test_one_missing_provider_is_singular():
    problem, _kiln = await problem_for([Need("groq", TONE_JUDGE)], [])
    assert problem.title == "Kiln is missing an API key this run needs"
    assert problem.body == (
        "The tuner calls this provider through Kiln, which needs a key for it:",
        '  • Groq: used by eval "Tone"\'s judge',
        "Add the key in Kiln under Settings → AI Providers.",
    )
    assert problem.retry_label == "Check again (I've added the key)"


async def test_not_guided_the_message_names_the_providers_page():
    problem, _kiln = await problem_for([Need("groq", TONE_JUDGE)], [])
    assert problem.error().body[-1] == (
        "Open AI Providers in Kiln: http://localhost:8757/settings/providers"
    )


async def test_a_provider_with_no_known_name_is_shown_by_its_id():
    problem, _kiln = await problem_for([Need("acme_ai", TONE_JUDGE)], None)
    assert problem.body[1] == '  • acme_ai: used by eval "Tone"\'s judge'


async def test_the_uses_of_a_provider_are_grouped_and_each_named_once():
    needs = [
        Need("openai", BASELINE_RC),
        Need("groq", SIMULATED_USER),
        Need("openai", TONE_JUDGE),
        Need("openai", TONE_JUDGE),
        Need("ollama", 'eval "Local"\'s judge'),
    ]
    problem, _kiln = await problem_for(needs, [])
    assert problem.body[1:3] == (
        '  • OpenAI: used by the task\'s run config "Baseline", eval "Tone"\'s judge',
        f"  • Groq: used by {SIMULATED_USER}",
    )
    assert not any("ollama" in line for line in problem.body)


async def test_dropping_a_provider_from_the_policy_is_offered_only_if_it_fixes_it():
    needs = [
        Need("openai", POLICY, from_policy=True),
        Need("openai", POLICY, from_policy=True),
        Need("anthropic", TONE_JUDGE),
        Need("anthropic", POLICY, from_policy=True),
    ]
    problem, _kiln = await problem_for(needs, [])
    assert problem.body[1:3] == (
        "  • OpenAI: used by --model-policy, or remove it from the model policy",
        '  • Anthropic: used by eval "Tone"\'s judge, --model-policy',
    )


def test_the_passed_step_names_the_providers_that_take_a_key():
    needs = [
        Need("fireworks_ai", BASELINE_RC),
        Need("ollama", TONE_JUDGE),
        Need("openai", TONE_JUDGE),
        Need("fireworks_ai", SIMULATED_USER),
        Need("acme_ai", POLICY, from_policy=True),
    ]
    assert keys_passed_text(needs) == "Fireworks AI, OpenAI, acme_ai"


@pytest.mark.parametrize(
    "needs", [[], [Need("ollama", TONE_JUDGE)]], ids=["none", "local"]
)
def test_the_passed_step_with_nothing_that_takes_a_key(needs):
    assert keys_passed_text(needs) == "none needed (local models only)"


async def test_rows_that_name_no_provider_are_ignored():
    available = ["junk", {"provider_id": 5}, {"provider_name": "OpenAI"}]
    problem, _kiln = await problem_for([Need("openai", TONE_JUDGE)], available)
    assert problem is not None


async def test_a_drifted_available_models_body_is_a_precondition_error():
    with pytest.raises(SetupPreconditionError, match="/api/available_models"):
        await problem_for([Need("openai", TONE_JUDGE)], {"models": []})
