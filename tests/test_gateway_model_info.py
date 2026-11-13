"""The model operations' answers (`gateway/model_info.py`): the run's
catalogue snapshot, joined with what the Kiln server offers and with the
model policy. Pure functions, on the phase 2 fixture and inline server
payloads. Every id is invented."""

from __future__ import annotations

from typing import Any

import pytest
from test_model_catalog import build

from kiln_harness_tuner.gateway import models as m
from kiln_harness_tuner.gateway.errors import GatewayError
from kiln_harness_tuner.gateway.model_info import (
    _DETAIL_FIELDS,
    _ENTRY_FIELDS,
    SERVER_FIELDS,
    Offered,
    list_models,
    list_providers,
    model_details,
    status,
)
from kiln_harness_tuner.model_catalog import Catalog, CatalogEntry, CatalogModel
from kiln_harness_tuner.model_policy import ModelPolicy

FINE_TUNE = "424242975950::424242783061::424242300001"
CATALOG = build()

# What a Kiln server with keys for OpenRouter, OpenAI and Anthropic offers.
SERVER: dict[str, list[str]] = {
    "openrouter": [
        "claude_sonnet_5_5",
        "gpt_6_1_sol",
        "gemini_3_8_flash",
        "deepseek_4_pro",
        "qwen_3p8_max_0902",
        "glm_4_6",
    ],
    "openai": ["gpt_6_1_sol"],
    "anthropic": ["claude_sonnet_5_5"],
}


def payload(
    server: dict[str, list[str]] | None = None, **extra: dict[str, Any]
) -> list[dict[str, Any]]:
    """A `/api/available_models` payload. A pair in the catalogue has the
    catalogue's thinking levels and output mode, as on a server that loaded
    the same remote config. `extra` maps a provider to one more model dict
    on it."""
    server = SERVER if server is None else server
    rows = [
        {
            "provider_id": provider,
            "provider_name": provider.title(),
            "models": [server_model(provider, model) for model in models],
        }
        for provider, models in server.items()
    ]
    for provider, model in extra.items():
        row = next((r for r in rows if r["provider_id"] == provider), None)
        if row is None:
            row = {"provider_id": provider, "models": []}
            rows.append(row)
        row["models"].append(model)
    return rows


def server_model(provider: str, model: str) -> dict[str, Any]:
    row: dict[str, Any] = {"id": model, "name": model.upper()}
    found = CATALOG.model(model)
    entry = (
        next((e for e in found.entries if e.provider == provider), None)
        if found
        else None
    )
    if entry is not None:
        row["available_thinking_levels"] = {v: v for v in entry.thinking_levels} or None
        row["default_thinking_level"] = entry.default_thinking_level
        row["structured_output_mode"] = entry.structured_output_mode
    return row


OFFERED = Offered.from_payload(payload())
NO_ANTHROPIC = ModelPolicy.from_json(
    {
        "allow": [{"provider": "*", "model": "*"}],
        "deny": [{"provider": "anthropic", "model": "*"}],
    }
)


def models_of(
    catalog: Catalog | None = None,
    offered: Offered = OFFERED,
    policy: ModelPolicy | None = None,
    **args: Any,
) -> m.ModelList:
    return list_models(
        build() if catalog is None else catalog,
        offered,
        policy,
        m.ListModelsArgs(**args),
    )


def ids(answer: m.ModelList) -> list[str]:
    return [row.model for row in answer.models]


def pairs(answer: m.ModelList) -> dict[str, list[tuple[str, str]]]:
    return {
        row.model: [(p.provider, p.status) for p in row.providers]
        for row in answer.models
    }


# ---- the status


def test_status_ready_blocked_not_offered_not_connected():
    assert status(OFFERED, None, "openai", "gpt_6_1_sol") == "ready"
    assert status(OFFERED, NO_ANTHROPIC, "openai", "gpt_6_1_sol") == "ready"
    assert (
        status(OFFERED, NO_ANTHROPIC, "anthropic", "claude_sonnet_5_5")
        == "blocked_by_policy"
    )
    assert status(OFFERED, None, "openai", "claude_sonnet_5_5") == "not_offered"
    assert status(OFFERED, None, "vertex", "gemini_3_8_flash") == "not_connected"


@pytest.mark.parametrize(
    "bad",
    [None, {"provider_id": "openai"}, ["x", {"provider_id": 3}, {"models": []}]],
)
def test_offered_from_a_bad_payload_is_empty(bad):
    offered = Offered.from_payload(bad)
    assert offered.pairs == {} and offered.connected == frozenset()


def test_offered_skips_a_bad_model_and_keeps_the_provider_connected():
    offered = Offered.from_payload(
        [{"provider_id": "groq", "models": [{"name": "no id"}, "x", {"id": "k"}]}]
    )
    assert list(offered.pairs) == [("groq", "k")]
    assert offered.connected == {"groq"}


# ---- list_models


def test_list_models_returns_ready_entries_only_by_default():
    answer = models_of(policy=NO_ANTHROPIC)
    assert answer.catalog == m.CatalogState(
        kiln="ok", models_dev="ok", fetched_at="2026-11-13T12:00:00+00:00"
    )
    listed = pairs(answer)
    assert listed["gpt_6_1_sol"] == [("openai", "ready"), ("openrouter", "ready")]
    assert listed["claude_sonnet_5_5"] == [("openrouter", "ready")]
    assert listed["gemini_3_8_flash"] == [("openrouter", "ready")]
    # Not offered by the server: the typesafe model.
    assert "jev_1_13" not in listed
    assert all(s == "ready" for row in listed.values() for _, s in row)


def test_include_not_available_lists_every_status():
    answer = models_of(policy=NO_ANTHROPIC, include_not_available=True)
    listed = pairs(answer)
    assert listed["claude_sonnet_5_5"] == [
        ("openrouter", "ready"),
        ("anthropic", "blocked_by_policy"),
    ]
    assert dict(listed["gemini_3_8_flash"]) == {
        "openrouter": "ready",
        "gemini_api": "not_connected",
        "vertex": "not_connected",
    }
    assert listed["jev_1_13"] == [("typesafe", "not_connected")]
    # A server with OpenAI connected that does not offer this model on it.
    offered = Offered.from_payload(payload({"openai": ["gpt_6_1_sol"], "typesafe": []}))
    answer = models_of(offered=offered, include_not_available=True)
    assert pairs(answer)["jev_1_13"] == [("typesafe", "not_offered")]


def test_deprecated_entries_are_left_out_by_default():
    deprecated = Offered.from_payload(
        payload({"openrouter": ["glm_4_6"], "cerebras": ["glm_4_6"]})
    )
    assert pairs(models_of(offered=deprecated))["glm_4_6"] == [("openrouter", "ready")]
    row = models_of(offered=deprecated, include_deprecated=True).models[0]
    assert [(p.provider, p.deprecated) for p in row.providers] == [
        ("openrouter", False),
        ("cerebras", True),
    ]


def test_entry_filters_remove_entries_and_empty_rows():
    # openai's gpt entry has no tool calls in the fixture; openrouter's has.
    listed = pairs(models_of(tools=True))
    assert listed["gpt_6_1_sol"] == [("openrouter", "ready")]
    assert pairs(models_of(tools=False)) == {"gpt_6_1_sol": [("openai", "ready")]}
    # deepseek and glm have no vision: their rows go.
    assert set(pairs(models_of(vision=False))) == {"deepseek_4_pro", "glm_4_6"}
    # qwen has no thinking levels; glm is reasoning capable with none.
    no_thinking = models_of(reasoning=False)
    assert ids(no_thinking) == ["qwen_3p8_max_0902"]
    assert no_thinking.models[0].thinking is False
    assert "glm_4_6" in ids(models_of(reasoning=True))
    assert pairs(models_of(provider="openai")) == {"gpt_6_1_sol": [("openai", "ready")]}


def test_max_input_price_drops_entries_with_no_price():
    # glm on openrouter has no models.dev price, so a price filter drops it.
    assert ids(models_of(max_input_price=1.0)) == ["deepseek_4_pro", "gemini_3_8_flash"]
    assert "glm_4_6" not in ids(models_of(max_input_price=1000.0))


def test_a_tag_filter_value_is_bounded():
    with pytest.raises(ValueError):
        m.ListModelsArgs(tags=["x" * 41])
    with pytest.raises(ValueError):
        m.ListModelsArgs(tags=[""])


def test_row_filters_family_open_weights_and_tags():
    assert ids(models_of(family="claude")) == ["claude_sonnet_5_5"]
    assert ids(models_of(open_weights=True)) == ["deepseek_4_pro"]
    # glm's open weights are unknown: a filter on it leaves the row out.
    assert "glm_4_6" not in ids(models_of(open_weights=False))
    assert ids(models_of(tags=["strong_coding", "best_value"])) == [
        "gpt_6_1_sol",
        "deepseek_4_pro",
    ]
    assert ids(models_of(tags=["strong_agentic"], family="claude")) == [
        "claude_sonnet_5_5"
    ]


def test_a_tag_on_a_provider_entry_matches_the_row():
    assert pairs(models_of(tags=["fast"])) == {
        "gpt_6_1_sol": [("openai", "ready"), ("openrouter", "ready")]
    }
    # The tag is on the openai entry, which the provider filter removes.
    assert ids(models_of(tags=["fast"], provider="openrouter")) == []


def test_sort_featured_price_newest_and_ties():
    assert ids(models_of()) == [
        "gpt_6_1_sol",  # rank 4
        "qwen_3p8_max_0902",  # 6
        "deepseek_4_pro",  # 8
        "claude_sonnet_5_5",  # 10
        "gemini_3_8_flash",  # 12
        "glm_4_6",  # no rank: last
    ]
    assert ids(models_of(sort="price")) == [
        "deepseek_4_pro",  # 2.4
        "gemini_3_8_flash",  # 4.5
        "qwen_3p8_max_0902",  # 8
        "gpt_6_1_sol",  # 12, a tie: the featured order
        "claude_sonnet_5_5",  # 12
        "glm_4_6",  # no price: last
    ]
    assert ids(models_of(sort="newest")) == [
        "gpt_6_1_sol",  # 2026-09-29
        "claude_sonnet_5_5",  # 2026-09-28
        "qwen_3p8_max_0902",  # 2026-09-02, a tie: the featured order
        "gemini_3_8_flash",  # 2026-09-02
        "deepseek_4_pro",  # 2026-04-24
        "glm_4_6",  # 2025-09-30
    ]
    undated = models_of(sort="newest", include_not_available=True)
    assert ids(undated)[-1] == "jev_1_13"


def test_paging_and_total():
    page = models_of(limit=2, offset=1)
    assert page.total == 6
    assert ids(page) == ["qwen_3p8_max_0902", "deepseek_4_pro"]
    assert models_of(offset=10).models == [] and models_of(offset=10).total == 6


def test_a_pair_the_catalogue_lacks_is_listed_with_null_fields():
    offered = Offered.from_payload(
        payload(
            {},
            groq={
                "id": "qwen_3p8_max_0902",
                "name": "Qwen",
                "supports_function_calling": True,
                "available_thinking_levels": {"a": "low", "b": "high"},
            },
        )
    )
    (row,) = models_of(offered=offered).models
    assert row.model == "qwen_3p8_max_0902" and row.release_date == "2026-09-02"
    assert row.providers == [
        m.ProviderEntryRow(provider="groq", status="ready", tags=[], deprecated=False)
    ]
    assert row.thinking is True
    details = model_details(
        build(), offered, None, m.ModelDetailsArgs(model=row.model, provider="groq")
    )
    (entry,) = details.providers
    assert entry.thinking_levels == ["low", "high"]
    assert entry.supports_tools is True and entry.model_id is None


def test_a_model_the_catalogue_lacks_is_listed_after_the_catalogue():
    fine_tune = {
        "id": FINE_TUNE,
        "name": "Moderator fine-tune",
        "supports_structured_output": True,
        "structured_output_mode": "json_schema",
        "suggested_for_evals": True,
        "untested_model": True,
    }
    offered = Offered.from_payload(
        payload(
            {"openrouter": ["glm_4_6"]},
            kiln_fine_tune=fine_tune,
            kiln_custom={"id": "aaa_custom", "name": "Custom"},
        )
    )
    answer = models_of(offered=offered)
    # After the catalogue's models, by model id.
    assert ids(answer) == ["glm_4_6", FINE_TUNE, "aaa_custom"]
    row = answer.models[ids(answer).index(FINE_TUNE)]
    assert row.name == "Moderator fine-tune"
    assert (row.family, row.featured_rank, row.release_date) == (None, None, None)
    assert row.providers[0].provider == "kiln_fine_tune"
    assert row.providers[0].input_price is None
    (entry,) = model_details(
        build(), offered, None, m.ModelDetailsArgs(model=FINE_TUNE)
    ).providers
    assert entry.structured_output_mode == "json_schema"
    assert entry.suggested_for == ["evals"] and entry.untested is True
    assert entry.supports_tools is None


def test_the_server_values_win_for_an_offered_pair():
    # The installed Kiln loaded another remote config: other thinking levels
    # and another output mode for gpt on openai. Code checks the levels
    # against the server, so the answers give the server's.
    offered = Offered.from_payload(
        payload(
            {"openrouter": ["gpt_6_1_sol"]},
            openai={
                "id": "gpt_6_1_sol",
                "available_thinking_levels": {"x": "low", "y": "xhigh"},
                "default_thinking_level": "xhigh",
                "structured_output_mode": "json_mode",
            },
        )
    )
    details = model_details(
        CATALOG, offered, None, m.ModelDetailsArgs(model="gpt_6_1_sol")
    )
    entries = {e.provider: e for e in details.providers}
    assert entries["openai"].thinking_levels == ["low", "xhigh"]
    assert entries["openai"].default_thinking_level == "xhigh"
    assert entries["openai"].structured_output_mode == "json_mode"
    # The catalogue's other fields stay, and the snapshot is not changed.
    assert entries["openai"].input_price == 2.0
    assert entries["openai"].model_id == "gpt-6.1-sol"
    (catalogue_entry,) = [
        e for e in CATALOG.model("gpt_6_1_sol").entries if e.provider == "openai"
    ]
    assert catalogue_entry.thinking_levels == ["none", "low", "medium", "high"]
    # A server with no thinking levels for the pair: none are listed.
    offered = Offered.from_payload(payload({}, openai={"id": "gpt_6_1_sol"}))
    (entry,) = model_details(
        CATALOG,
        offered,
        None,
        m.ModelDetailsArgs(model="gpt_6_1_sol", provider="openai"),
    ).providers
    assert entry.thinking_levels == [] and entry.structured_output_mode is None


def test_the_details_fields_are_catalogue_fields():
    assert _DETAIL_FIELDS <= set(CatalogModel.model_fields)
    assert _ENTRY_FIELDS <= set(CatalogEntry.model_fields)
    assert set(SERVER_FIELDS) <= set(CatalogEntry.model_fields)


def test_an_unavailable_catalogue_serves_the_server_list():
    catalog = Catalog.unavailable("turned off")
    answer = models_of(catalog=catalog, policy=NO_ANTHROPIC)
    assert answer.catalog == m.CatalogState(kiln="none", models_dev="none")
    assert ids(answer) == sorted(
        {model for provider in SERVER.values() for model in provider}
    )
    assert pairs(answer)["claude_sonnet_5_5"] == [("openrouter", "ready")]
    assert all(row.release_date is None for row in answer.models)


# ---- get_model_details


def test_model_details_lists_every_entry_with_its_status():
    details = model_details(
        build(), OFFERED, NO_ANTHROPIC, m.ModelDetailsArgs(model="deepseek_4_pro")
    )
    assert details.name == "DeepSeek V4 Pro"
    assert details.release_date_match == "exact" and details.open_weights is True
    assert [(p.provider, p.status, p.deprecated) for p in details.providers] == [
        ("openrouter", "ready", False),
        ("fireworks_ai", "not_connected", True),
        ("together_ai", "not_connected", True),
        ("featherless_ai", "not_connected", False),
        ("siliconflow_cn", "not_connected", False),
        ("cloudflare", "not_connected", False),
    ]
    openrouter = details.providers[0]
    assert (openrouter.input_price, openrouter.output_price) == (0.9, 1.5)
    assert openrouter.model_id == "deepseek/deepseek-v4-pro"
    gpt = model_details(build(), OFFERED, None, m.ModelDetailsArgs(model="gpt_6_1_sol"))
    assert gpt.agent_notes and gpt.thinking is True


def test_model_details_refuses_an_unknown_model():
    with pytest.raises(GatewayError, match=r"^Model not found: nope\. Use list_models"):
        model_details(build(), OFFERED, None, m.ModelDetailsArgs(model="nope"))


def test_model_details_with_a_provider_keeps_one_entry():
    details = model_details(
        build(),
        OFFERED,
        NO_ANTHROPIC,
        m.ModelDetailsArgs(model="claude_sonnet_5_5", provider="anthropic"),
    )
    assert [(p.provider, p.status) for p in details.providers] == [
        ("anthropic", "blocked_by_policy")
    ]
    with pytest.raises(GatewayError, match="has no entry for provider groq"):
        model_details(
            build(),
            OFFERED,
            None,
            m.ModelDetailsArgs(model="claude_sonnet_5_5", provider="groq"),
        )


# ---- list_providers


def test_list_providers_counts_ready_models_and_orders_them():
    answer = list_providers(build(), OFFERED, NO_ANTHROPIC, m.ListProvidersArgs())
    assert [(p.provider, p.status, p.ready_models) for p in answer.providers] == [
        ("openrouter", "connected", 6),
        ("openai", "connected", 1),
        ("anthropic", "connected", 0),  # its one pair is blocked by the policy
    ]
    openai = answer.providers[1]
    assert openai.name == "OpenAI" and openai.notes
    assert answer.providers[2].name is None  # not in the provider list


def test_list_providers_hides_unconnected_by_default():
    answer = list_providers(
        build(), OFFERED, None, m.ListProvidersArgs(include_not_available=True)
    )
    rows = {p.provider: p for p in answer.providers}
    # The provider list, the providers of the entries, and the server's.
    assert {"typesafe", "cloudflare", "vertex", "cerebras", "anthropic"} <= set(rows)
    assert rows["cloudflare"].status == "not_connected"
    assert rows["cloudflare"].name == "Cloudflare Workers AI"
    assert rows["typesafe"].ready_models == 0
    hidden = list_providers(build(), OFFERED, None, m.ListProvidersArgs())
    assert all(p.status == "connected" for p in hidden.providers)
    assert len(hidden.providers) == 3
