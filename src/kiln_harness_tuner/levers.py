"""Did the declared lever take effect?

A candidate that changes the thinking level, the model or the provider is
paid for on the assumption that the change reached the call. It does not
always: a provider can ignore a reasoning parameter, and a run config can
route to another model than it names. So before a candidate is paid for
further, code reads the evidence off the drives it already made.

Every lever has more than one signal, and a candidate is stopped as
"lever not applied" only when EVERY available signal says the lever did
nothing (`lever_verdict`):

- **thinking**, set above none and different from the parent's level:
  (1) reasoning content, stored reasoning or reasoning tokens in any drive;
  (2) the output-token count against the parent on the SAME items (a
  provider that hides its reasoning still bills the tokens, so they rise or
  fall with the level). The lever is applied when (1) shows reasoning or
  (2) moved by more than `TOKEN_MOVE` either way; not applied when (1)
  shows none AND (2) is available and did not move; UNVERIFIED when (1)
  shows none and (2) is not available (no token counts on
  `MIN_TOKEN_PAIRS` paired items);
- **model or provider**: the model and provider each drive records — in
  `output.source`, in its `usage`, or on a trace message. A drive served by
  another model or provider than the run config names is not applied (a
  direct contradiction); a switch no drive records is UNVERIFIED.

An unverified lever is logged and the candidate goes on; only "not
applied" stops it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

NO_THINKING = frozenset({None, "", "none", "off", "disabled"})
_REASONING_TOKEN_KEYS = ("reasoning_tokens", "thinking_tokens")


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


TOKEN_MOVE = 1.25  # a median ratio at or beyond 1.25x (or 1/1.25x) moved
MIN_TOKEN_PAIRS = 2
_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens")
_MODEL_KEYS = ("model_name", "model")
_PROVIDER_KEYS = ("model_provider", "model_provider_name", "provider")


def _first(mapping: Any, keys: Sequence[str]) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    return next((mapping[k] for k in keys if mapping.get(k)), None)


def lever_evidence(run: Mapping[str, Any]) -> dict[str, Any]:
    """What one task run shows about the levers: `reasoning` (any reasoning
    content or reasoning tokens), and the `model` / `provider` that served
    it (None when the run does not record them)."""
    trace = [m for m in (run.get("trace") or []) if isinstance(m, Mapping)]
    reasoning = any(
        m.get("role") == "assistant" and (m.get("reasoning_content") or "")
        for m in trace
    )
    intermediate = run.get("intermediate_outputs")
    if isinstance(intermediate, Mapping) and intermediate.get("reasoning"):
        reasoning = True
    usage = run.get("usage")
    if isinstance(usage, Mapping):
        details = usage.get("completion_tokens_details")
        tokens = sum(_num(usage.get(k)) for k in _REASONING_TOKEN_KEYS)
        if isinstance(details, Mapping):
            tokens += _num(details.get("reasoning_tokens"))
        reasoning = reasoning or tokens > 0
    model = provider = None
    output = run.get("output")
    source = output.get("source") if isinstance(output, Mapping) else None
    if isinstance(source, Mapping):
        props = source.get("properties")
        config = source.get("run_config")
        if isinstance(config, Mapping):
            inner = config.get("run_config_properties")
            config = inner if isinstance(inner, Mapping) else config
            model = config.get("model_name") or model
            provider = config.get("model_provider_name") or provider
        if isinstance(props, Mapping):
            model = props.get("model_name") or model
            provider = props.get("model_provider") or provider
    usage = run.get("usage")
    model = model or _first(usage, _MODEL_KEYS)
    provider = provider or _first(usage, _PROVIDER_KEYS)
    for message in trace:
        model = model or _first(message, _MODEL_KEYS)
        provider = provider or _first(message, _PROVIDER_KEYS)
    tokens = _first(usage, _OUTPUT_TOKEN_KEYS)
    return {
        "reasoning": reasoning,
        "model": model,
        "provider": provider,
        "output_tokens": float(tokens) if isinstance(tokens, (int, float)) else None,
    }


@dataclass(frozen=True)
class LeverVerdict:
    status: str  # "applied" | "not_applied" | "unverified"
    reason: str = ""


def _token_ratio(
    evidence: Mapping[str, Mapping[str, Any]],
    parent: Mapping[str, Mapping[str, Any]],
) -> tuple[float | None, int]:
    ratios = [
        c / p
        for item, e in evidence.items()
        if (c := e.get("output_tokens"))
        and (p := (parent.get(item) or {}).get("output_tokens"))
    ]
    return (
        (median(ratios), len(ratios))
        if len(ratios) >= MIN_TOKEN_PAIRS
        else (
            None,
            len(ratios),
        )
    )


def lever_verdict(
    props: Mapping[str, Any],
    evidence: Mapping[str, Mapping[str, Any]],
    parent: Mapping[str, Any] | None = None,
    parent_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> LeverVerdict:
    """The candidate's declared levers against every signal its drives and
    its parent's drives on the same items carry. `evidence` and
    `parent_evidence` = item id -> `lever_evidence` of that drive."""
    if not evidence:
        return LeverVerdict("applied")
    n = len(evidence)
    unverified: list[str] = []
    level = props.get("thinking_level")
    declared = parent is None or level != parent.get("thinking_level")
    if declared and level not in NO_THINKING:
        if not any(e.get("reasoning") for e in evidence.values()):
            ratio, pairs = _token_ratio(evidence, parent_evidence or {})
            if ratio is None:
                unverified.append(
                    f"thinking_level={level}: no drive shows reasoning and"
                    f" output tokens are not recorded on {MIN_TOKEN_PAIRS}+"
                    " items paired with the parent"
                )
            elif 1 / TOKEN_MOVE < ratio < TOKEN_MOVE:
                return LeverVerdict(
                    "not_applied",
                    f"lever not applied: thinking_level={level} is set, but 0 of"
                    f" {n} drives show reasoning and output tokens are"
                    f" {ratio:.2f}x the parent's on the same {pairs} items",
                )
    want_model = props.get("model_name")
    want_provider = props.get("model_provider_name")
    served = [
        (e.get("model"), e.get("provider"))
        for e in evidence.values()
        if e.get("model") or e.get("provider")
    ]
    wrong = [
        (m, p)
        for m, p in served
        if (m and want_model and m != want_model)
        or (p and want_provider and p != want_provider)
    ]
    if wrong:
        m, p = wrong[0]
        return LeverVerdict(
            "not_applied",
            f"lever not applied: the run config names {want_model}@{want_provider},"
            f" but {len(wrong)} of {n} drives were served by {m}@{p}",
        )
    switched = parent is not None and (
        want_model != parent.get("model_name")
        or want_provider != parent.get("model_provider_name")
    )
    if switched and not served:
        unverified.append(
            f"{want_model}@{want_provider}: no drive records the model that served it"
        )
    if unverified:
        return LeverVerdict("unverified", "lever unverified: " + "; ".join(unverified))
    return LeverVerdict("applied")


def lever_not_applied(
    props: Mapping[str, Any],
    evidence: Mapping[str, Mapping[str, Any]],
    parent: Mapping[str, Any] | None = None,
    parent_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> str | None:
    """The "not applied" reason, or None (applied or unverified)."""
    verdict = lever_verdict(props, evidence, parent, parent_evidence)
    return verdict.reason if verdict.status == "not_applied" else None
