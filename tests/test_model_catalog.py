"""The model catalogue: the tolerant parse of Kiln's published file, the
models.dev join, the fetch and the snapshot. Offline: the fixtures are small
slices of the two real files, and the fetch runs on `respx` routes."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from kiln_harness_tuner.model_catalog import (
    CATALOG_FILE,
    Catalog,
    Source,
    build_catalog,
    fetch_catalog,
    load_catalog,
    write_catalog,
)
from kiln_harness_tuner.model_catalog import fetch as fetch_module
from kiln_harness_tuner.model_catalog.build import NO_MODEL_LIST
from kiln_harness_tuner.model_catalog.fetch import KILN_URL, MODELS_DEV_URL
from kiln_harness_tuner.model_catalog.providers import models_dev_provider

FIXTURES = Path(__file__).parent / "fixtures" / "model_catalog"
OK = Source(state="ok")
FETCHED_AT = "2026-11-13T12:00:00+00:00"


def kiln_file() -> dict[str, Any]:
    return json.loads((FIXTURES / "kiln_config.json").read_text())


def models_dev_file() -> dict[str, Any]:
    return json.loads((FIXTURES / "models_dev.json").read_text())


def build(kiln_raw: Any = None, models_dev_raw: Any = None) -> Catalog:
    return build_catalog(
        kiln_file() if kiln_raw is None else kiln_raw,
        models_dev_file() if models_dev_raw is None else models_dev_raw,
        fetched_at=FETCHED_AT,
        kiln=OK,
        models_dev=OK,
    )


def one_model(*providers: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """A Kiln file with one model on the given provider entries."""
    model = {"name": "m", "friendly_name": "M", "family": "f", **fields}
    return {"model_list": [{**model, "providers": list(providers)}]}


def models_dev_with(provider: str, model_id: str, **entry: Any) -> dict[str, Any]:
    return {provider: {"id": provider, "models": {model_id: entry}}}


def entry_of(catalog: Catalog, model: str, provider: str):
    found = catalog.model(model)
    assert found is not None
    (entry,) = [e for e in found.entries if e.provider == provider]
    return entry


# ---- the build ----


def test_the_fixture_builds_with_prices_limits_and_dates():
    catalog = build()
    assert catalog.kiln == OK and catalog.models_dev == OK
    assert catalog.fetched_at == FETCHED_AT
    assert [m.model for m in catalog.models] == [
        "claude_sonnet_5_5",
        "gpt_6_1_sol",
        "gemini_3_8_flash",
        "deepseek_4_pro",
        "qwen_3p8_max_0902",
        "glm_4_6",
        "jev_1_13",
    ]
    gpt = catalog.model("gpt_6_1_sol")
    assert gpt is not None
    assert (gpt.name, gpt.family, gpt.featured_rank) == ("GPT-6.1 Sol", "gpt", 4)
    assert gpt.release_date == "2026-09-29" and gpt.release_date_match == "exact"
    assert (gpt.knowledge_cutoff, gpt.open_weights) == ("2026-04-30", False)
    assert (gpt.context_window, gpt.max_output_tokens) == (1050000, 128000)
    openai = entry_of(catalog, "gpt_6_1_sol", "openai")
    assert openai.model_id == "gpt-6.1-sol"
    assert (openai.input_price, openai.output_price) == (2.0, 10.0)
    assert (openai.cache_read_price, openai.cache_write_price) == (0.1, 2.5)
    assert openai.takes_temperature is False
    assert openai.thinking_levels == ["none", "low", "medium", "high"]
    assert openai.default_thinking_level == "medium"
    assert openai.suggested_for == ["evals", "data_gen"]
    assert openai.supports_tools is False and openai.supports_vision is True
    assert openai.structured_output_mode == "json_schema"
    assert openai.tags == ["fast"]
    # The new Kiln fields, and the provider list.
    assert gpt.agent_notes is not None and gpt.agent_notes.startswith("Follows")
    assert gpt.tags == ["strong_agentic", "strong_coding"]
    assert [p.provider for p in catalog.providers] == [
        "openai",
        "openrouter",
        "cloudflare",
        "featherless_ai",
        "typesafe",
    ]
    openrouter = catalog.provider("openrouter")
    assert openrouter is not None and openrouter.name == "OpenRouter"
    assert openrouter.notes is not None and openrouter.models_dev_id == "openrouter"
    assert catalog.provider("ollama") is None
    dated = [m.model for m in catalog.models if m.release_date is not None]
    assert dated == [m.model for m in catalog.models if m.model != "jev_1_13"]


def test_price_is_matched_on_the_exact_pair():
    catalog = build()
    openrouter = entry_of(catalog, "deepseek_4_pro", "openrouter")
    siliconflow = entry_of(catalog, "deepseek_4_pro", "siliconflow_cn")
    assert (openrouter.input_price, openrouter.output_price) == (0.9, 1.5)
    assert siliconflow.input_price == pytest.approx(1.79336973)
    assert siliconflow.context_window == 1049000
    # No models.dev entry for this pair: nothing is borrowed from another one.
    featherless = entry_of(catalog, "deepseek_4_pro", "featherless_ai")
    assert featherless.input_price is None and featherless.context_window is None
    deprecated = entry_of(catalog, "deepseek_4_pro", "fireworks_ai")
    assert deprecated.deprecated is True and deprecated.input_price is None


def test_model_facts_follow_the_provider_preference():
    kiln = kiln_file()
    deepseek = next(m for m in kiln["model_list"] if m["name"] == "deepseek_4_pro")
    # OpenRouter last in Kiln's order: the preference, not the order, decides.
    deepseek["providers"].append(deepseek["providers"].pop(0))
    model = build(kiln).model("deepseek_4_pro")
    assert model is not None
    assert model.release_date == "2026-04-24"
    assert model.max_output_tokens == 393216  # OpenRouter's, not SiliconFlow's
    # Without OpenRouter, the other providers by id: cloudflare before
    # siliconflow_cn.
    deepseek["providers"] = [
        p for p in deepseek["providers"] if p["name"] != "openrouter"
    ]
    model = build(kiln).model("deepseek_4_pro")
    assert model is not None and model.release_date == "2026-08-13"


def test_the_loose_match_dates_a_model_with_no_exact_match():
    glm = build().model("glm_4_6")
    assert glm is not None
    assert glm.release_date == "2025-09-30" and glm.release_date_match == "loose"
    # The other model facts are not taken from a loose match.
    assert glm.open_weights is None and glm.context_window is None
    assert all(e.input_price is None for e in glm.entries)
    jev = build().model("jev_1_13")
    assert jev is not None
    assert jev.release_date is None and jev.release_date_match is None


def test_the_loose_match_takes_the_earliest_date():
    models_dev = {
        "a": {"models": {"vendor/Big-Model-2": {"release_date": "2026-03-01"}}},
        "b": {
            "models": {
                "big-model-2": {"release_date": "2025-12-01T00:00:00Z"},
                "other": {
                    "release_date": "2025-06-01",
                    "canonical_model_id": "lab/big.model.2",
                },
                "bad-date": {"release_date": "soon", "canonical_model_id": "bigmodel2"},
            }
        },
    }
    kiln = one_model({"name": "openrouter", "model_id": "x/big_model_2:free"})
    model = build(kiln, models_dev).model("m")
    assert model is not None
    assert model.release_date == "2025-06-01" and model.release_date_match == "loose"


def test_a_loose_date_counts_a_deprecated_entry():
    models_dev = {"z": {"models": {"old-name-1": {"release_date": "2025-01-02"}}}}
    kiln = one_model(
        {"name": "openai", "model_id": "new-name"},
        {"name": "groq", "model_id": "old-name-1", "deprecated": True},
    )
    model = build(kiln, models_dev).model("m")
    assert model is not None and model.release_date == "2025-01-02"


def test_a_short_loose_key_is_not_used():
    models_dev = {"z": {"models": {"o3": {"release_date": "2025-04-16"}}}}
    model = build(one_model({"name": "openai", "model_id": "o-3"}), models_dev)
    found = model.model("m")
    assert found is not None and found.release_date is None


def test_an_exact_match_with_no_date_falls_back_to_the_loose_date():
    models_dev = {
        "openai": {"models": {"gpt-x-1": {"limit": {"context": 1000}}}},
        "other": {"models": {"gpt-x-1": {"release_date": "2025-05-05"}}},
    }
    model = build(one_model({"name": "openai", "model_id": "gpt-x-1"}), models_dev)
    found = model.model("m")
    assert found is not None
    assert found.context_window == 1000
    assert (found.release_date, found.release_date_match) == ("2025-05-05", "loose")


def test_the_published_models_dev_id_wins_over_the_built_in_map():
    kiln = one_model({"name": "together_ai", "model_id": "m-1"})
    kiln["provider_list"] = [{"id": "together_ai", "models_dev_id": "together-v2"}]
    models_dev = {
        **models_dev_with("togetherai", "m-1", cost={"input": 9}),
        **models_dev_with("together-v2", "m-1", cost={"input": 1}),
    }
    assert entry_of(build(kiln, models_dev), "m", "together_ai").input_price == 1.0
    # A null published id does not override the table.
    kiln["provider_list"] = [{"id": "together_ai", "models_dev_id": None}]
    assert entry_of(build(kiln, models_dev), "m", "together_ai").input_price == 9.0


def test_a_provider_without_models_dev_is_not_joined():
    # models.dev has a `huggingface` key; the Kiln server leaves it out.
    kiln = one_model({"name": "huggingface", "model_id": "org/m-1"})
    models_dev = models_dev_with("huggingface", "org/m-1", cost={"input": 1})
    entry = entry_of(build(kiln, models_dev), "m", "huggingface")
    assert entry.input_price is None
    assert models_dev_provider("kiln_fine_tune", None) is None
    assert models_dev_provider("kiln_fine_tune", "published") == "published"
    assert models_dev_provider("openai", None) == "openai"


def test_cloudflare_maps_to_cloudflare_workers_ai():
    assert models_dev_provider("cloudflare", None) == "cloudflare-workers-ai"
    kiln = kiln_file()
    kiln.pop("provider_list")  # the built-in table alone
    entry = entry_of(build(kiln), "deepseek_4_pro", "cloudflare")
    assert (entry.input_price, entry.output_price) == (1.32, 3.96)


def test_a_bad_price_and_a_bad_limit_are_dropped():
    catalog = build()
    qwen = entry_of(catalog, "qwen_3p8_max_0902", "openrouter")
    assert qwen.cache_read_price is None  # negative in the fixture
    assert qwen.input_price == 2.0
    anthropic = entry_of(catalog, "claude_sonnet_5_5", "anthropic")
    assert anthropic.max_output_tokens is None  # a string in the fixture
    assert anthropic.context_window == 1000000
    models_dev = models_dev_with(
        "openai",
        "m-1",
        cost={
            "input": float("nan"),
            "output": float("inf"),
            "cache_read": True,
            "cache_write": "1",
        },
        limit={"context": 0, "output": True},
        temperature="yes",
    )
    entry = entry_of(
        build(one_model({"name": "openai", "model_id": "m-1"}), models_dev),
        "m",
        "openai",
    )
    assert entry.input_price is None and entry.output_price is None
    assert entry.cache_read_price is None and entry.cache_write_price is None
    assert entry.context_window is None and entry.max_output_tokens is None
    assert entry.takes_temperature is None
    zero = models_dev_with("openai", "m-1", cost={"input": 0})
    built = build(one_model({"name": "openai", "model_id": "m-1"}), zero)
    assert entry_of(built, "m", "openai").input_price == 0.0
    huge = models_dev_with("openai", "m-1", cost={"input": 10**400})
    built = build(one_model({"name": "openai", "model_id": "m-1"}), huge)
    assert entry_of(built, "m", "openai").input_price is None


def test_a_lookup_does_not_change_equality():
    catalog = build()
    copy = catalog.model_copy(deep=True)
    assert catalog.model("glm_4_6") is not None
    assert catalog.provider("openai") is not None
    assert catalog == copy


def test_a_bad_model_is_dropped_and_the_rest_kept():
    kiln = kiln_file()
    kiln["model_list"][1:1] = [
        {"friendly_name": "no name"},
        "not a model",
        {"name": "x", "featured_rank": "first", "providers": []},
        {"name": "claude_sonnet_5_5", "friendly_name": "A second copy"},
    ]
    catalog = build(kiln)
    assert len(catalog.models) == 7
    sonnet = catalog.model("claude_sonnet_5_5")
    assert sonnet is not None and sonnet.name == "Claude 5.5 Sonnet"


def test_a_bad_provider_entry_is_dropped_and_its_model_kept():
    kiln = one_model(
        {"name": "openai", "model_id": "m-1"},
        {"model_id": "no provider name"},
        {"name": "groq", "deprecated": "maybe"},
        {"name": "anthropic", "available_thinking_levels": ["low"]},
        42,
    )
    model = build(kiln).model("m")
    assert model is not None
    assert [e.provider for e in model.entries] == ["openai"]


def test_an_unknown_field_is_ignored():
    kiln = one_model(
        {"name": "openai", "model_id": "m-1", "a_new_flag": True},
        a_new_field={"nested": 1},
    )
    kiln["a_new_top_level_key"] = []
    kiln["provider_list"] = [{"id": "openai", "region": "us"}]
    catalog = build(kiln)
    assert entry_of(catalog, "m", "openai").model_id == "m-1"
    assert catalog.provider("openai") is not None


def test_tags_are_cleaned_deduplicated_and_capped():
    tags = ["fast", "Fast", "very fast", "fast", "x" * 41, "a" * 40, 7, ""]
    tags += [f"t{i}" for i in range(20)]
    kiln = one_model({"name": "openai", "tags": "fast"}, tags=tags)
    kiln["provider_list"] = [{"id": "openai", "tags": ["zdr", "ZDR"]}]
    catalog = build(kiln)
    model = catalog.model("m")
    assert model is not None
    assert model.tags == ["fast", "a" * 40] + [f"t{i}" for i in range(10)]
    assert model.entries[0].tags == []
    provider = catalog.provider("openai")
    assert provider is not None and provider.tags == ["zdr"]


def test_a_long_note_is_cut():
    long = "word " * 500
    kiln = one_model(agent_notes=long, editorial_notes="  ")
    kiln["provider_list"] = [{"id": "openai", "notes": "x" * 2000}]
    catalog = build(kiln)
    model = catalog.model("m")
    assert model is not None and model.agent_notes is not None
    assert len(model.agent_notes) <= 2000
    assert model.agent_notes.endswith("word [cut]")
    assert model.editorial_notes is None
    provider = catalog.provider("openai")
    assert provider is not None and provider.notes == "x" * 2000


def test_a_kiln_file_without_model_list_gives_kiln_none():
    for raw in ({"model_list": {}}, [], "text"):
        catalog = build(raw)
        assert catalog.kiln == Source(state="none", reason=NO_MODEL_LIST)
        assert catalog.models == [] and catalog.models_dev == OK
    # A Kiln fetch that failed keeps its own reason.
    failed = Source(state="none", reason="HTTP 503")
    catalog = build_catalog(None, {}, fetched_at=FETCHED_AT, kiln=failed, models_dev=OK)
    assert catalog.kiln == failed


def test_no_models_dev_gives_null_prices_and_kiln_data():
    failed = Source(state="none", reason="timeout after 10 s")
    catalog = build_catalog(
        kiln_file(), None, fetched_at=FETCHED_AT, kiln=OK, models_dev=failed
    )
    assert catalog.models_dev == failed
    assert len(catalog.models) == 7
    assert all(m.release_date is None for m in catalog.models)
    assert all(e.input_price is None for m in catalog.models for e in m.entries)
    gpt = catalog.model("gpt_6_1_sol")
    assert gpt is not None and gpt.featured_rank == 4 and gpt.tags
    # A models.dev body that is not an object is no models.dev.
    catalog = build(models_dev_raw=["not", "an", "object"])
    assert catalog.models_dev.state == "none"
    assert all(m.release_date is None for m in catalog.models)


def test_unavailable_has_both_sources_none_with_the_reason():
    catalog = Catalog.unavailable("turned off")
    assert (
        catalog.kiln == catalog.models_dev == Source(state="none", reason="turned off")
    )
    assert catalog.models == [] and catalog.model("x") is None


# ---- the fetch ----


def fixture_routes() -> tuple[respx.Route, respx.Route]:
    kiln = respx.get(KILN_URL).mock(return_value=httpx.Response(200, json=kiln_file()))
    models_dev = respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=models_dev_file())
    )
    return kiln, models_dev


@respx.mock
async def test_fetch_turned_off_makes_no_request():
    kiln, models_dev = fixture_routes()
    catalog = await fetch_catalog(enabled=False)
    assert not kiln.called and not models_dev.called
    assert catalog.kiln == Source(state="none", reason="turned off")
    assert catalog.models_dev.state == "none"


@respx.mock
async def test_fetch_gets_both_sources_and_stamps_the_time():
    kiln, models_dev = fixture_routes()
    local = timezone(timedelta(hours=2))
    catalog = await fetch_catalog(
        now=lambda: datetime(2026, 11, 13, 14, 0, 0, 123, tzinfo=local)
    )
    assert kiln.called and models_dev.called
    assert catalog.kiln == OK and catalog.models_dev == OK
    assert catalog.fetched_at == "2026-11-13T12:00:00+00:00"
    assert len(catalog.models) == 7
    gpt = catalog.model("gpt_6_1_sol")
    assert gpt is not None and gpt.release_date == "2026-09-29"
    # A plain GET: no header of the tuner's own.
    request = kiln.calls.last.request
    assert request.method == "GET"
    assert set(request.headers) <= {
        "host",
        "accept",
        "accept-encoding",
        "connection",
        "user-agent",
    }


@respx.mock
async def test_fetch_uses_a_given_client_and_leaves_it_open():
    fixture_routes()
    async with httpx.AsyncClient() as client:
        catalog = await fetch_catalog(client=client)
        assert not client.is_closed
    assert catalog.kiln == OK


@respx.mock
async def test_fetch_timeout_gives_a_reason_and_never_raises():
    respx.get(KILN_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    respx.get(MODELS_DEV_URL).mock(side_effect=httpx.ConnectError("no route"))
    catalog = await fetch_catalog()
    assert catalog.kiln == Source(state="none", reason="timeout after 10 s")
    assert catalog.models_dev == Source(
        state="none", reason="transport error: ConnectError"
    )
    assert catalog.models == []


@respx.mock
async def test_fetch_is_bounded_in_total_time(monkeypatch):
    monkeypatch.setattr(fetch_module, "FETCH_TIMEOUT_SECS", 0.05)

    async def stalls(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    respx.get(KILN_URL).mock(side_effect=stalls)
    respx.get(MODELS_DEV_URL).mock(side_effect=RuntimeError("bug"))
    catalog = await asyncio.wait_for(fetch_catalog(), timeout=2)
    assert catalog.kiln == Source(state="none", reason="timeout after 0.05 s")
    assert catalog.models_dev == Source(
        state="none", reason="fetch failed: RuntimeError"
    )


@respx.mock
async def test_fetch_non_2xx_gives_a_reason():
    respx.get(KILN_URL).mock(return_value=httpx.Response(503))
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=models_dev_file())
    )
    catalog = await fetch_catalog()
    assert catalog.kiln == Source(state="none", reason="HTTP 503")
    assert catalog.models_dev == OK
    assert catalog.models == []


@respx.mock
async def test_fetch_over_the_byte_cap_gives_a_reason(monkeypatch):
    monkeypatch.setattr(fetch_module, "MAX_BYTES", 1024 * 1024)
    respx.get(KILN_URL).mock(
        return_value=httpx.Response(200, content=b" " * (1024 * 1024 + 1))
    )
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(200, json={}))
    catalog = await fetch_catalog()
    assert catalog.kiln == Source(state="none", reason="body over 1 MiB")


@respx.mock
async def test_fetch_bad_json_gives_a_reason():
    respx.get(KILN_URL).mock(return_value=httpx.Response(200, json=kiln_file()))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(200, content=b"{"))
    catalog = await fetch_catalog()
    assert catalog.kiln == OK
    assert catalog.models_dev == Source(state="none", reason="not valid JSON")
    assert all(m.release_date is None for m in catalog.models)


# ---- the snapshot ----


def test_write_then_load_round_trips(tmp_path):
    catalog = build()
    write_catalog(tmp_path, catalog)
    assert not (tmp_path / f"{CATALOG_FILE}.tmp").exists()
    text = (tmp_path / CATALOG_FILE).read_text()
    assert "\n" not in text  # compact
    loaded = load_catalog(tmp_path)
    assert loaded == catalog
    assert loaded.model("glm_4_6") == catalog.model("glm_4_6")


def test_a_newer_snapshot_with_unknown_fields_loads(tmp_path):
    data = json.loads(build().model_dump_json())
    data["a_new_key"] = 1
    data["models"][0]["a_new_field"] = "x"
    (tmp_path / CATALOG_FILE).write_text(json.dumps(data))
    assert len(load_catalog(tmp_path).models) == 7


@pytest.mark.parametrize("text", [None, "", "  \n", "{not json", '{"models": 3}'])
def test_an_empty_or_missing_snapshot_loads_as_unavailable(tmp_path, text):
    if text is not None:
        (tmp_path / CATALOG_FILE).write_text(text)
    catalog = load_catalog(tmp_path)
    assert catalog == Catalog.unavailable("no snapshot")
