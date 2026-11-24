"""The input transform lever: the gateway's rules on the template, the
integrity checks that read it, and the check that the lever was applied.
Every name and value is invented."""

from __future__ import annotations

import json

import pytest
from conftest import fix_item, make_ctx, make_result
from fake_dispatcher import ok
from fake_kiln import make_eval, make_eval_config, make_run
from test_gateway import BASE as GATEWAY_BASE
from test_gateway import _gateway, _run
from test_items import BASELINE
from test_quarantine import TASK

from kiln_harness_tuner import input_transform as it
from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.generality import (
    TrainDoc,
    added_text,
    audit_baseline,
    exact_matches,
    input_transform_changes,
)
from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.judge_copy import check_judge_copies
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.levers import lever_evidence, lever_verdict
from kiln_harness_tuner.memory_schema import ExperimentView, MemoryRecord
from kiln_harness_tuner.prompt_text import RcText, rc_texts, whole_texts
from kiln_harness_tuner.schemas import (
    GeneralityReviewResult,
    ProbeCheck,
    RcBuildResult,
    ScreenCandidate,
)

PROMPT = "You are a freight depot assistant. Answer the operator's question."
JUDGE = (
    "Score the reply PASS only when every count in the answer matches the"
    " manifest exactly and the reply names the depot it counted in."
)
KEEP_TWO = "Depot: {{ input.depot }}\nQuestion: {{ input.question }}"
KEEP_THREE = KEEP_TWO + "\nOpen: {{ input.is_open }}"
VALIDATE = "/api/validate_input_transform_template"


def jinja(template: str) -> dict:
    return {"type": "jinja", "template": template}


def props(template: str | None = None, **extra) -> dict:
    out = {
        "type": "kiln_agent",
        "model_name": "m",
        "model_provider_name": "p",
        "prompt_id": "id::1",
        **extra,
    }
    if template is not None:
        out["input_transform"] = jinja(template)
    return out


def _rc(rc_id: str, template: str | None = None, prompt: str = PROMPT) -> dict:
    return {
        "id": rc_id,
        "name": rc_id,
        "prompt": {"prompt": prompt},
        "run_config_properties": props(template),
    }


def _text(template: str = "", prompt: str = PROMPT) -> RcText:
    return RcText(prompt=prompt, skill_ids=(), skills={}, input_transform=template)


# ---- the shape, the change, and the drives ----


class TestTheShape:
    def test_null_and_a_jinja_template_are_accepted(self):
        assert it.transform_problem(None) is None
        assert it.transform_problem(jinja(KEEP_TWO)) is None

    @pytest.mark.parametrize(
        "value, why",
        [
            ("{{ input }}", "must be null or"),
            ({"type": "python", "template": "x"}, "type must be 'jinja'"),
            ({"type": "jinja"}, "non-empty text"),
            ({"type": "jinja", "template": "  "}, "non-empty text"),
            ({"type": "jinja", "template": "x", "code": "y"}, "unknown keys"),
            (jinja("x" * (it.TEMPLATE_MAX_CHARS + 1)), "the limit is"),
        ],
    )
    def test_every_other_value_is_a_problem(self, value, why):
        assert why in it.transform_problem(value)

    def test_the_template_text_of_properties(self):
        assert it.template_of(props(KEEP_TWO)) == KEEP_TWO
        assert it.template_of(props()) == ""
        assert it.template_of({"input_transform": None}) == ""
        assert it.template_of(None) == ""

    def test_a_missing_key_and_null_are_the_same_transform(self):
        assert not it.changed(props(), {**props(), "input_transform": None})
        assert not it.changed(props(KEEP_TWO), props(KEEP_TWO))
        assert it.changed(props(), props(KEEP_TWO))
        assert it.changed(props(KEEP_TWO), props(KEEP_THREE))
        assert it.changed(props(KEEP_TWO), props())

    def test_the_change_in_one_line(self):
        line = it.change_line(props(), props(KEEP_TWO))
        assert line == (
            f"the parent has none, the arm has a template of {len(KEEP_TWO)} characters"
        )


def _traced(item: str, message: str | None, **scores) -> dict:
    run = make_run(item, **scores)
    trace = [{"role": "system", "content": PROMPT}]
    if message is not None:
        trace.append({"role": "user", "content": message})
    trace.append({"role": "assistant", "content": "14 pallets"})
    run["task_run_trace"] = json.dumps(trace)
    return run


class TestTheDrives:
    def test_the_first_user_message_of_a_trace(self):
        assert it.first_user_message(_traced("i1", "Depot: Halden")) == "Depot: Halden"
        assert it.first_user_message({"trace": [{"role": "assistant"}]}) is None
        assert it.first_user_message({}) is None
        parts = {"trace": [{"role": "user", "content": [{"text": "a"}, {"text": "b"}]}]}
        assert it.first_user_message(parts) == "a\nb"

    def test_one_moved_message_is_applied(self):
        verdict = it.applied_verdict(
            {"i1": "raw one", "i2": "raw two"}, {"i1": "Depot: A", "i2": "raw two"}
        )
        assert verdict.status == "applied"
        assert "on 1 of 2 paired train item(s)" in verdict.reason

    def test_the_same_message_on_every_pair_is_not_applied(self):
        same = {"i1": "raw one", "i2": "raw two"}
        verdict = it.applied_verdict(same, dict(same))
        assert verdict.status == "not_applied"
        assert verdict.reason.startswith("lever not applied: the input transform")

    def test_too_few_pairs_are_unverified(self):
        assert it.applied_verdict({"i1": "raw"}, {"i1": "raw"}).status == "unverified"
        assert it.applied_verdict({}, {"i1": "x"}).status == "unverified"


class TestTheRender:
    """Kiln's own engine, from the installed library, on task inputs."""

    def test_a_structured_and_a_json_text_input_render_the_same(self):
        report = it.render_on_inputs(
            KEEP_TWO,
            {
                "a": {"depot": "A", "question": "q1"},
                "b": '{"depot": "A", "question": "q1"}',
            },
        )
        assert (report.checked, report.failures) == (2, [])
        assert report.sample == ("a", "Depot: A\nQuestion: q1")

    def test_a_plain_text_input_is_the_text(self):
        report = it.render_on_inputs(
            "Post: {{ input | truncate(9, true, '…') }}", {"a": "0123456789" * 3}
        )
        assert report.sample == ("a", "Post: 01234567…")

    def test_a_missing_field_is_a_failure_of_that_input(self):
        report = it.render_on_inputs(
            KEEP_TWO, {"a": {"depot": "A", "question": "q"}, "b": {"depot": "B"}}
        )
        ((item_id, error),) = report.failures
        assert item_id == "b" and error.startswith("UndefinedError")
        assert "1 of 2 train inputs, for example b" in report.failure_line

    def test_a_computed_feature_renders(self):
        template = (
            "Open: {{ not input.closed and not input.archived }}\n"
            "Rows: {{ input.rows | length }},"
            " total {{ input.rows | sum(attribute='n') }}"
        )
        report = it.render_on_inputs(
            template,
            {"a": {"closed": False, "archived": False, "rows": [{"n": 2}, {"n": 3}]}},
        )
        assert report.sample == ("a", "Open: True\nRows: 2, total 5")

    def test_no_input_and_no_engine_are_notes_not_failures(self, monkeypatch):
        assert "no train input" in it.render_on_inputs(KEEP_TWO, {}).note

        def unavailable(_template):
            raise it.RenderUnavailable("no engine")

        monkeypatch.setattr(it, "_renderer", unavailable)
        report = it.render_on_inputs(KEEP_TWO, {"a": {"depot": "A"}})
        assert (report.checked, report.failures) == (0, [])
        assert report.note == "not rendered: no engine"

    def test_a_template_the_library_does_not_compile_is_unavailable(self):
        report = it.render_on_inputs(
            "{{ input | a_filter_of_a_newer_kiln }}", {"a": "x"}
        )
        assert report.checked == 0 and report.note.startswith("not rendered:")


class TestTheLeverVerdict:
    def test_the_timing_check_does_not_judge_the_transform(self):
        """`lever_verdict` does not know the arm's technique, so it never
        stops an arm on the transform: the item path does, for an arm of
        that lever only."""
        evidence = {f"i{k}": {"output_tokens": 10.0} for k in range(3)}
        verdict = lever_verdict(props(KEEP_TWO), evidence, props(), dict(evidence))
        assert verdict.status == "applied"
        assert "user_message" not in lever_evidence(
            {"trace": [{"role": "user", "content": "Depot: Halden"}]}
        )


class TestThePlan:
    """A template is authored text: only a build session changes it."""

    def test_a_screen_candidate_may_not_override_the_transform(self):
        with pytest.raises(ValueError, match="technique: input_transform"):
            ScreenCandidate(id="s1-1", overrides={"input_transform": jinja(KEEP_TWO)})
        ScreenCandidate(id="s1-1", overrides={"thinking_level": "low"})

    def test_a_probe_check_may_not_override_the_transform(self):
        with pytest.raises(ValueError, match="technique: input_transform"):
            ProbeCheck(count="output_chars", overrides={"input_transform": None})
        ProbeCheck(count="output_chars", overrides={"structured_output_mode": "x"})


# ---- the gateway ----


def _routes(*rows: dict, valid: bool = True, turn_mode: str = "single_turn") -> dict:
    return {
        ("GET", f"{GATEWAY_BASE}/run_configs"): list(rows),
        ("GET", GATEWAY_BASE): {"id": "t", "turn_mode": turn_mode},
        ("POST", VALIDATE): {
            "valid": valid,
            "error": None if valid else "unexpected '}' (line 1)",
        },
        ("POST", f"{GATEWAY_BASE}/run_configs"): lambda _q, body: {
            "id": "rc_new",
            **body,
        },
    }


def _json_inputs(tmp_path, *, drop_question_on_tr2: bool = False) -> None:
    """Make the two train inputs of the gateway's project JSON records."""
    from test_quarantine import Project

    project = Project(tmp_path / "proj")
    second = (
        {"depot": "B"} if drop_question_on_tr2 else {"depot": "B", "question": "q2"}
    )
    project.eval_input(
        "tr1",
        ["train_a"],
        text=json.dumps({"depot": "A", "question": "q1", "audit": 7}),
    )
    project.eval_input("tr2", ["train_a", "train_b"], text=json.dumps(second))


def _create(gateway, properties: dict):
    return _run(
        gateway, "create_run_config", name="n", run_config_properties=properties
    )


class TestTheGateway:
    def test_validate_answers_kilns_compile_check_and_renders_train_inputs(
        self, tmp_path
    ):
        gateway, kiln = _gateway(tmp_path, _routes())
        _json_inputs(tmp_path)
        out = _run(gateway, "validate_input_transform", template=KEEP_TWO)
        assert out == {
            "valid": True,
            "error": None,
            "applies": True,
            "train_items_rendered": 2,
            "render_failure_count": 0,
            "render_failures": [],
            "sample_item_id": "tr1",
            "sample": "Depot: A\nQuestion: q1",
            "note": None,
        }
        assert ("POST", VALIDATE, None, {"template": KEEP_TWO}) in kiln.calls

    def test_validate_shows_the_rendered_text_of_a_named_train_item(self, tmp_path):
        gateway, _ = _gateway(tmp_path, _routes())
        _json_inputs(tmp_path)
        out = _run(
            gateway, "validate_input_transform", template=KEEP_TWO, item_id="tr2"
        )
        assert (out["sample_item_id"], out["sample"]) == (
            "tr2",
            "Depot: B\nQuestion: q2",
        )
        # Never a held-out item, and never an unknown one.
        for item_id in ("va1", "nope"):
            with pytest.raises(gw.GatewayError, match="not a train item"):
                _run(
                    gateway,
                    "validate_input_transform",
                    template=KEEP_TWO,
                    item_id=item_id,
                )

    def test_validate_names_the_train_inputs_the_template_fails_on(self, tmp_path):
        gateway, _ = _gateway(tmp_path, _routes())
        _json_inputs(tmp_path, drop_question_on_tr2=True)
        out = _run(gateway, "validate_input_transform", template=KEEP_TWO)
        assert out["valid"] is True  # Kiln compiles it: the fault is in the data
        assert (out["train_items_rendered"], out["render_failure_count"]) == (2, 1)
        (failure,) = out["render_failures"]
        assert failure["item_id"] == "tr2"
        assert "has no attribute 'question'" in failure["error"]
        assert out["sample_item_id"] == "tr1"
        guarded = KEEP_TWO.replace(
            "{{ input.question }}", "{{ input.question | default('none') }}"
        )
        out = _run(gateway, "validate_input_transform", template=guarded)
        assert out["render_failure_count"] == 0

    def test_validate_on_a_server_with_no_compile_route_is_a_clear_refusal(
        self, tmp_path
    ):
        def missing(_q, _body):
            raise KilnAPIError("POST", VALIDATE, 404, {"detail": "Not Found"})

        routes = _routes()
        routes[("POST", VALIDATE)] = missing
        gateway, _ = _gateway(tmp_path, routes)
        with pytest.raises(gw.GatewayError, match="needs the route POST"):
            _run(gateway, "validate_input_transform", template=KEEP_TWO)

    def test_a_template_the_library_cannot_render_is_reported_not_refused(
        self, tmp_path, monkeypatch
    ):
        def unavailable(_template):
            raise it.RenderUnavailable("the installed kiln-ai library has no engine")

        monkeypatch.setattr(it, "_renderer", unavailable)
        gateway, _ = _gateway(tmp_path, _routes(_rc("rc_base")))
        out = _run(gateway, "validate_input_transform", template=KEEP_TWO)
        assert out["valid"] is True and out["train_items_rendered"] == 0
        assert out["note"].startswith("not rendered: the installed kiln-ai library")
        assert _create(gateway, props(KEEP_TWO))["id"] == "rc_new"

    def test_validate_gives_the_compile_error(self, tmp_path):
        gateway, _ = _gateway(tmp_path, _routes(valid=False))
        out = _run(gateway, "validate_input_transform", template="{{ input }")
        assert out["valid"] is False
        assert out["error"] == "unexpected '}' (line 1)"
        assert out["train_items_rendered"] == 0 and out["sample"] is None

    def test_validate_says_the_lever_does_not_apply_to_a_multi_turn_task(
        self, tmp_path
    ):
        gateway, _ = _gateway(tmp_path, _routes(turn_mode="multiturn"))
        out = _run(gateway, "validate_input_transform", template=KEEP_TWO)
        assert out["valid"] is True and out["applies"] is False
        assert "single-turn tasks only" in out["note"]
        assert out["train_items_rendered"] == 0

    def test_validate_refuses_an_empty_or_too_long_template(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, _routes())
        for template in ("", "x" * (it.TEMPLATE_MAX_CHARS + 1)):
            with pytest.raises(ValueError):
                _run(gateway, "validate_input_transform", template=template)
        # The rule of create_run_config, so validate never says yes to a
        # template that the create refuses.
        with pytest.raises(gw.GatewayError, match="non-empty text"):
            _run(gateway, "validate_input_transform", template="   ")
        assert kiln.calls == []

    def test_a_task_that_cannot_be_read_refuses_a_new_template(self, tmp_path):
        """The multi-turn rule fails closed: on a read error the gateway
        does not take the task to be single-turn."""

        def down(_q, _body):
            raise KilnAPIError("GET", GATEWAY_BASE, 503, {"detail": "busy"})

        routes = _routes(_rc("rc_base"))
        routes[("GET", GATEWAY_BASE)] = down
        gateway, kiln = _gateway(tmp_path, routes)
        with pytest.raises(gw.GatewayError, match="turn mode"):
            _run(gateway, "validate_input_transform", template=KEEP_TWO)
        with pytest.raises(gw.GatewayError, match="turn mode"):
            _create(gateway, props(KEEP_TWO))
        assert ("POST", f"{GATEWAY_BASE}/run_configs") not in [
            c[:2] for c in kiln.calls
        ]

    def test_a_single_turn_input_with_an_openenv_reset_is_rendered(self, tmp_path):
        """Such an input is driven through an eval job, and its user
        message is still rendered by the template."""
        from test_quarantine import _write

        gateway, _ = _gateway(tmp_path, _routes())
        _json_inputs(tmp_path)
        doc_path = (
            tmp_path / "proj" / "tasks" / f"{TASK} - T" / "eval_inputs" / "tr1"
        ) / "eval_input.kiln"
        doc = json.loads(doc_path.read_text())
        doc["openenv_reset"] = {"seed": 1}
        _write(doc_path, doc)
        out = _run(
            gateway, "validate_input_transform", template=KEEP_TWO, item_id="tr1"
        )
        assert out["train_items_rendered"] == 2
        assert out["sample"] == "Depot: A\nQuestion: q1"

    def test_only_the_roles_that_make_run_configs_can_validate(self):
        with_op = {
            r for r, ops in gw.ROLE_OPS.items() if "validate_input_transform" in ops
        }
        assert with_op == {"create_experiment_rc", "probe"}
        assert with_op == {
            r for r, ops in gw.ROLE_OPS.items() if "create_run_config" in ops
        }

    def test_a_new_template_is_compiled_rendered_then_created(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, _routes(_rc("rc_base")))
        _json_inputs(tmp_path)
        out = _create(gateway, props(KEEP_TWO))
        assert out["run_config_properties"]["input_transform"] == jinja(KEEP_TWO)
        posts = [c[1] for c in kiln.calls if c[0] == "POST"]
        assert posts == [VALIDATE, f"{GATEWAY_BASE}/run_configs"]

    def test_a_template_kiln_does_not_compile_is_refused(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, _routes(_rc("rc_base"), valid=False))
        with pytest.raises(gw.GatewayError, match="not a valid Jinja2 template"):
            _create(gateway, props("{{ input }"))
        assert ("POST", f"{GATEWAY_BASE}/run_configs") not in [
            c[:2] for c in kiln.calls
        ]

    def test_a_template_that_fails_on_a_train_input_is_refused(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, _routes(_rc("rc_base")))
        _json_inputs(tmp_path, drop_question_on_tr2=True)
        with pytest.raises(gw.GatewayError) as refused:
            _create(gateway, props(KEEP_TWO))
        assert "fails to render on 1 of 2 train inputs, for example tr2" in str(
            refused.value
        )
        assert ("POST", f"{GATEWAY_BASE}/run_configs") not in [
            c[:2] for c in kiln.calls
        ]

    @pytest.mark.parametrize(
        "value", ["{{ input }}", {"type": "python", "template": "x"}, {"type": "jinja"}]
    )
    def test_a_value_of_the_wrong_shape_is_refused_before_any_call(
        self, tmp_path, value
    ):
        gateway, kiln = _gateway(tmp_path, _routes())
        with pytest.raises(gw.GatewayError, match="input_transform"):
            _create(gateway, {**props(), "input_transform": value})
        assert kiln.calls == []

    def test_no_transform_needs_no_check(self, tmp_path):
        gateway, kiln = _gateway(tmp_path, _routes())
        _create(gateway, {**props(), "input_transform": None})
        _create(gateway, props())
        assert {c[:2] for c in kiln.calls} == {("POST", f"{GATEWAY_BASE}/run_configs")}

    def test_a_new_template_is_refused_on_a_multi_turn_task(self, tmp_path):
        gateway, kiln = _gateway(
            tmp_path, _routes(_rc("rc_base"), turn_mode="multiturn")
        )
        with pytest.raises(gw.GatewayError, match="single-turn tasks only"):
            _create(gateway, props(KEEP_TWO))
        assert all(c[0] == "GET" for c in kiln.calls)

    def test_a_transform_the_task_already_has_is_copied_on_any_task(self, tmp_path):
        gateway, kiln = _gateway(
            tmp_path, _routes(_rc("rc_base", KEEP_TWO), turn_mode="multiturn")
        )
        out = _create(gateway, props(KEEP_TWO, thinking_level="high"))
        assert out["id"] == "rc_new"
        # Not compiled again, and the task's turn mode is not read.
        assert [c[:2] for c in kiln.calls] == [
            ("GET", f"{GATEWAY_BASE}/run_configs"),
            ("POST", f"{GATEWAY_BASE}/run_configs"),
        ]

    def test_a_server_with_no_compile_route_leaves_the_check_to_the_create(
        self, tmp_path
    ):
        def missing(_q, _body):
            raise KilnAPIError("POST", VALIDATE, 404, {"detail": "Not Found"})

        routes = _routes(_rc("rc_base"))
        routes[("POST", VALIDATE)] = missing
        gateway, _ = _gateway(tmp_path, routes)
        _json_inputs(tmp_path)
        assert _create(gateway, props(KEEP_TWO))["id"] == "rc_new"

    def test_the_task_view_carries_the_turn_mode(self, tmp_path):
        gateway, _ = _gateway(tmp_path, _routes(turn_mode="multiturn"))
        assert _run(gateway, "get_task")["turn_mode"] == "multiturn"


# ---- the integrity checks read the template ----


def _docs() -> list[TrainDoc]:
    return [
        TrainDoc(
            id=f"t{k}",
            input_text=f"How many pallets does depot D-{k}417 hold on aisle {k}?",
            reference={"answer": f"{k}4 pallets"},
        )
        for k in range(1, 8)
    ]


class TestTheTextReaders:
    async def test_a_run_config_text_carries_its_template(self):
        ctx = make_ctx()
        ctx.kiln.run_configs = [_rc("rc_base"), _rc("rc_new", KEEP_TWO)]
        texts = await rc_texts(ctx, ["rc_base", "rc_new"])
        assert texts["rc_base"].input_transform == ""
        assert texts["rc_new"].input_transform == KEEP_TWO
        assert whole_texts(texts["rc_new"])[it.WHERE] == KEEP_TWO
        assert it.WHERE not in whole_texts(texts["rc_base"])

    def test_a_new_template_is_shown_whole(self):
        (block,) = input_transform_changes(_text(), _text(KEEP_TWO))
        assert block == f"the input transform: new — whole template\n{KEEP_TWO}"

    def test_a_changed_template_is_shown_as_a_diff(self):
        (block,) = input_transform_changes(_text(KEEP_TWO), _text(KEEP_THREE))
        assert block.startswith("the input transform: changed — unified diff")
        assert "+Open: {{ input.is_open }}" in block
        assert "+Depot" not in block

    def test_the_same_a_removed_or_a_shorter_template_adds_nothing(self):
        assert input_transform_changes(_text(KEEP_TWO), _text(KEEP_TWO)) == []
        assert input_transform_changes(_text(KEEP_TWO), _text()) == []
        assert input_transform_changes(_text(KEEP_THREE), _text(KEEP_TWO)) == []

    def test_a_train_value_in_the_template_is_an_exact_match(self):
        leaky = (
            KEEP_TWO + "\n{% if input.depot == 'D-3417' %}Answer: 34 pallets{% endif %}"
        )
        added = added_text(_text(KEEP_TWO), _text(leaky))
        assert list(added) == [it.WHERE]
        findings = exact_matches(added, _docs())
        assert any(
            "in the input transform is copied from train item t3" in f for f in findings
        )
        assert exact_matches(added_text(_text(), _text(KEEP_TWO)), _docs()) == []


def _eval_ctx(*rows: dict):
    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1", name="Count accuracy")
    kiln.eval_configs["ec_1"] = make_eval_config("ec_1", prompt_template=JUDGE)
    kiln.run_configs = [_rc("rc_base"), *rows]
    return ctx


COPIES_JUDGE = (
    KEEP_TWO + "\nEvery count in the answer matches the manifest exactly, so check it."
)


class TestTheJudgeCopyCheck:
    async def test_a_template_that_copies_the_judge_is_found(self):
        ctx = _eval_ctx(_rc("rc_new", COPIES_JUDGE))
        check = await check_judge_copies(ctx, "rc_new", "rc_base")
        assert check.status == "copied"
        (finding,) = check.findings
        assert finding.startswith(
            "the input transform quotes the judge of eval Count accuracy"
        )

    async def test_a_template_with_field_selection_only_is_clean(self):
        ctx = _eval_ctx(_rc("rc_new", KEEP_THREE))
        assert (await check_judge_copies(ctx, "rc_new", "rc_base")).status == "clean"

    async def test_the_baselines_own_template_is_exempt(self):
        ctx = _eval_ctx(_rc("rc_new", COPIES_JUDGE + "\nOpen: {{ input.is_open }}"))
        ctx.kiln.run_configs[0] = _rc("rc_base", COPIES_JUDGE)
        assert (await check_judge_copies(ctx, "rc_new", "rc_base")).status == "clean"


# ---- the experiment item ----


def _built(rc_id: str = "rc_new"):
    return ok(RcBuildResult(rc_id=rc_id, build_notes="built"))


def _review(*leaks: str):
    return ok(GeneralityReviewResult(leaks=list(leaks)))


def _item(technique: str = it.LEVER):
    item = fix_item()
    item.work.technique = technique
    return item


def _seed(kiln, parent_messages: list[str | None], messages: list[str | None]) -> None:
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.results[("eval_1", "rc_base", "train")] = [
        _traced(f"item_{k + 1}", m, accuracy=float(k))
        for k, m in enumerate(parent_messages)
    ]
    kiln.pending[("eval_1", "rc_new", "train")] = [
        _traced(f"item_{k + 1}", m, accuracy=1.0) for k, m in enumerate(messages)
    ]


def _item_ctx(*rows: dict, turn_mode: str | None = None):
    ctx = make_ctx()
    ctx.kiln.run_configs = [_rc("rc_base"), *rows]
    if turn_mode is not None:
        ctx.kiln.task = {**ctx.kiln.task, "turn_mode": turn_mode}
    return ctx


def _events(ctx, kind: str = "input_transform") -> list[dict]:
    return [f for k, f in ctx.log.events if k == kind]


def _record(ctx, outcome) -> ExperimentView:
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    return ExperimentView.from_record(MemoryRecord.from_api(record))


RAW = [
    '{"depot": "A", "question": "q1", "audit": 1}',
    '{"depot": "B", "question": "q2"}',
]
RENDERED = ["Depot: A\nQuestion: q1", "Depot: B\nQuestion: q2"]


class TestTheExperimentItem:
    async def test_a_changed_transform_is_reviewed_measured_and_recorded(self):
        ctx = _item_ctx(_rc("rc_new", KEEP_TWO))
        _seed(ctx.kiln, RAW, RENDERED)
        ctx.dispatch.queue("create_experiment_rc", _built())
        ctx.dispatch.queue("generality_review", _review())
        ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
        outcome = await execute_plan_item(ctx, _item(), BASELINE, ["rc_base"], 1)

        assert outcome.status == "completed" and outcome.rc_id == "rc_new"
        (review,) = ctx.dispatch.calls_to("generality_review")
        assert review.input_transform_changes == [
            f"the input transform: new — whole template\n{KEEP_TWO}"
        ]
        statuses = [e["status"] for e in _events(ctx)]
        assert statuses == ["changed", "applied"]
        details = [e["detail"] for e in _events(ctx, "item_detail")]
        assert any(
            d.startswith("input transform: input transform applied") for d in details
        )
        content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
        assert (
            "input transform: the parent has none, the arm has a template of"
            f" {len(KEEP_TWO)} characters; input transform applied: the first user"
            " message differs from the parent's on 2 of 2 paired train item(s)"
        ) in content

    async def test_the_lever_on_a_multi_turn_task_ends_before_the_build(self):
        ctx = _item_ctx(turn_mode="multiturn")
        outcome = await execute_plan_item(ctx, _item(), BASELINE, ["rc_base"], 1)
        assert outcome.status == "dead_end"
        assert ctx.dispatch.calls_to("create_experiment_rc") == []
        (event,) = _events(ctx)
        assert event["status"] == "refused"
        assert "single-turn tasks only" in _record(ctx, outcome).overview

    async def test_a_reused_run_config_that_changes_the_transform_on_a_multi_turn_task(
        self,
    ):
        """A reused run config skips the guard before the build: the check
        after it reads the two run configs and ends the arm."""
        ctx = _item_ctx(_rc("rc_old", KEEP_TWO), turn_mode="multiturn")
        item = _item("prompt")
        item.work.existing_rc_id = "rc_old"
        outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)
        assert outcome.status == "dead_end" and outcome.rc_id == "rc_old"
        assert ctx.dispatch.calls_to("create_experiment_rc") == []
        assert ctx.dispatch.calls_to("generality_review") == []
        assert [e["status"] for e in _events(ctx)] == ["refused"]

    async def test_a_rebuild_that_drops_the_new_transform_is_not_applied(self):
        """The one rebuild is checked again: back to the parent's transform
        on an arm of the lever is "lever not applied"."""
        ctx = _item_ctx(_rc("rc_new", COPIES_JUDGE), _rc("rc_new2"))
        ctx.kiln.eval_configs["ec_1"] = make_eval_config("ec_1", prompt_template=JUDGE)
        _seed(ctx.kiln, RAW, RENDERED)
        ctx.dispatch.queue("create_experiment_rc", _built(), _built("rc_new2"))
        ctx.dispatch.queue("generality_review", _review())
        outcome = await execute_plan_item(ctx, _item(), BASELINE, ["rc_base"], 1)
        (_first, rebuild) = ctx.dispatch.calls_to("create_experiment_rc")
        assert rebuild.judge_copy_findings  # the rebuild was for the judge copy
        assert outcome.status == "dead_end" and outcome.rc_id == "rc_new2"
        assert [e["status"] for e in _events(ctx)] == ["changed", "not_applied"]
        assert "lever not applied" in _record(ctx, outcome).overview
        assert ctx.dispatch.calls_to("analyze_experiment_results") == []

    async def test_another_arm_whose_transform_change_has_no_effect_goes_on(self):
        """A prompt arm that copied the parent's template with one more blank
        line: the template changed, the message did not. Its prompt change
        is what it measures, so it is analysed, and the verdict is a note."""
        parent_template = "Q: {{ input }}"
        ctx = _item_ctx(
            _rc("rc_new", parent_template + "\n", prompt=PROMPT + " Be brief.")
        )
        ctx.kiln.run_configs[0] = _rc("rc_base", parent_template)
        _seed(ctx.kiln, RENDERED, RENDERED)
        ctx.dispatch.queue("create_experiment_rc", _built())
        ctx.dispatch.queue("generality_review", _review())
        ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
        outcome = await execute_plan_item(
            ctx, _item("prompt"), BASELINE, ["rc_base"], 1
        )
        assert outcome.status == "completed"
        assert [e["status"] for e in _events(ctx)] == ["changed", "not_applied"]
        content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
        assert "input transform: the parent has a template" in content
        assert "lever not applied: the input transform changed" in content

    async def test_an_arm_that_names_the_lever_must_change_the_transform(self):
        ctx = _item_ctx(_rc("rc_new", prompt=PROMPT + " Be brief."))
        _seed(ctx.kiln, RAW, RAW)
        ctx.dispatch.queue("create_experiment_rc", _built())
        outcome = await execute_plan_item(ctx, _item(), BASELINE, ["rc_base"], 1)
        assert outcome.status == "dead_end" and outcome.rc_id == "rc_new"
        assert "lever not applied" in _record(ctx, outcome).overview
        # Before any review and any train spend.
        assert ctx.dispatch.calls_to("generality_review") == []
        assert ("eval_1", "rc_new", "train") in ctx.kiln.pending
        (event,) = _events(ctx)
        assert event["status"] == "not_applied"

    async def test_no_arm_changes_the_transform_on_a_multi_turn_task(self):
        ctx = _item_ctx(_rc("rc_new", KEEP_TWO), turn_mode="multiturn")
        ctx.dispatch.queue("create_experiment_rc", _built())
        outcome = await execute_plan_item(
            ctx, _item("prompt"), BASELINE, ["rc_base"], 1
        )
        assert outcome.status == "dead_end"
        assert ctx.dispatch.calls_to("generality_review") == []
        assert _events(ctx)[0]["status"] == "refused"

    async def test_a_transform_that_renders_the_parents_message_is_a_dead_end(self):
        ctx = _item_ctx(_rc("rc_new", "{{ input }}"))
        _seed(ctx.kiln, RAW, RAW)
        ctx.dispatch.queue("create_experiment_rc", _built())
        ctx.dispatch.queue("generality_review", _review())
        outcome = await execute_plan_item(ctx, _item(), BASELINE, ["rc_base"], 1)
        assert outcome.status == "dead_end" and outcome.rc_id == "rc_new"
        assert ctx.dispatch.calls_to("analyze_experiment_results") == []
        assert [e["status"] for e in _events(ctx)] == ["changed", "not_applied"]
        assert "is the parent's on all 2 paired train items" in (
            _record(ctx, outcome).overview
        )

    async def test_rows_with_no_trace_leave_the_lever_unverified(self):
        ctx = _item_ctx(_rc("rc_new", KEEP_TWO))
        _seed(ctx.kiln, [None, None], [None, None])
        ctx.dispatch.queue("create_experiment_rc", _built())
        ctx.dispatch.queue("generality_review", _review())
        ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
        outcome = await execute_plan_item(ctx, _item(), BASELINE, ["rc_base"], 1)
        assert outcome.status == "completed"
        assert [e["status"] for e in _events(ctx)] == ["changed", "unverified"]
        content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
        assert "lever unverified: input transform" in content

    async def test_another_lever_that_keeps_the_transform_has_no_line(self):
        ctx = _item_ctx(_rc("rc_new", prompt=PROMPT + " Be brief."))
        _seed(ctx.kiln, RAW, RAW)
        ctx.dispatch.queue("create_experiment_rc", _built())
        ctx.dispatch.queue("generality_review", _review())
        ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
        outcome = await execute_plan_item(
            ctx, _item("prompt"), BASELINE, ["rc_base"], 1
        )
        assert outcome.status == "completed"
        assert _events(ctx) == []
        content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
        assert "input transform" not in content

    async def test_a_train_value_in_the_template_is_a_leak_with_no_review(
        self, tmp_path
    ):
        from test_quarantine import TASK, Project

        project = Project(tmp_path / "proj")
        project.eval("eval_1", "a")
        for k in range(1, 8):
            project.eval_input(
                f"t{k}",
                ["train_a"],
                text=f"How many pallets does depot D-{k}417 hold on aisle {k}?",
            )
        leaky = KEEP_TWO + "\n{% if 'D-3417' in input %}Say 34 pallets.{% endif %}"
        ctx = _item_ctx(_rc("rc_new", leaky), _rc("rc_new2", KEEP_TWO))
        ctx.target = ctx.target.model_copy(
            update={"project_path": str(project.root), "task_id": TASK}
        )
        _seed(ctx.kiln, RAW, RENDERED)
        ctx.kiln.pending[("eval_1", "rc_new2", "train")] = ctx.kiln.pending.pop(
            ("eval_1", "rc_new", "train")
        )
        ctx.dispatch.queue("create_experiment_rc", _built(), _built("rc_new2"))
        ctx.dispatch.queue("generality_review", _review())
        ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
        outcome = await execute_plan_item(ctx, _item(), BASELINE, ["rc_base"], 1)
        assert outcome.status == "completed" and outcome.rc_id == "rc_new2"
        _first, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
        (finding,) = rebuild.generality_findings
        assert finding.startswith('exact match: "d-3417" in the input transform')
        # One review only: the rebuild's. The first build's leak was certain.
        assert len(ctx.dispatch.calls_to("generality_review")) == 1


async def test_the_baseline_audit_reads_the_baselines_template(tmp_path):
    from test_quarantine import TASK, Project

    project = Project(tmp_path / "proj")
    project.eval("eval_1", "a")
    for k in range(1, 8):
        project.eval_input(
            f"t{k}",
            ["train_a"],
            text=f"How many pallets does depot D-{k}417 hold on aisle {k}?",
        )
    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(project.root), "task_id": TASK}
    )
    ctx.kiln.run_configs = [
        _rc("rc_base", KEEP_TWO + "\n{% if 'D-5417' in input %}Careful.{% endif %}")
    ]
    audit = await audit_baseline(ctx, "rc_base")
    assert audit.status == "leaked"
    assert any("in the input transform" in f for f in audit.findings)
    assert ctx.dispatch.calls_to("generality_review") == []
