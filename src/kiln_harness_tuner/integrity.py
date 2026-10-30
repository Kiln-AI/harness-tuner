"""Reward-hacking checks at the promotion gate.

A candidate can raise its val scores without doing the task better: by
telling the model what the judge looks for, by changing the shape of its
output to one a judge rewards, or by taking actions the baseline never took.
Code checks every promotion candidate for three such signs before the gate
decides:

(a) **Quoting the evals.** The candidate's prompt shares a run of
    `QUOTE_NGRAM` words with a judge prompt, an eval config's steps, a
    spec's text or an eval's description — a run the baseline's prompt and
    the task's own instructions do not contain — or names an eval verbatim
    (`MIN_EVAL_NAME_CHARS` characters or more, not in the baseline's
    prompt). This BLOCKS promotion in code: the candidate is marked
    ineligible, whoever decides.
(b) **Output shape shift on val.** Against the baseline on the same val
    items: the median output length moved by more than `LENGTH_RATIO_LIMIT`
    either way, or the JSON outputs' top-level keys changed.
(c) **New write actions.** On a tool-using task, a kind of write action
    (a tool call whose method or action names a write) appears in the
    candidate's val traces and in none of the baseline's.

(b) and (c) are FLAGS, not blocks: a real improvement can change the shape
of the output or use a tool the baseline did not. They are handed to the
promotion agent as `CandidateValTable.integrity_flags`, and a winner that
carries flags is promoted only when the decision's rationale addresses them
(an `Integrity:` line). Under the code gate, a flagged winner is sent to the
promotion agent instead of being promoted by the rule.

The matcher of (a), `quote_findings`, also runs right after the
build, on the whole text of the run config (`kiln_harness_tuner.judge_copy`), so an
arm that copies an eval ends before any train spend. The gate check stays as
the last line of defense, unchanged in what it blocks. A block at the gate
is printed, written on the candidate's record and handed to the next
planner (`orchestrator._record_gate_blocks`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from statistics import median
from typing import Any

QUOTE_NGRAM = 8
MIN_EVAL_NAME_CHARS = 10
LENGTH_RATIO_LIMIT = 2.0
INTEGRITY_WORD = "integrity"
# How much of one copied passage a finding quotes.
PASSAGE_CHARS = 240

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
_WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})
_WRITE_VERB_RE = re.compile(
    r"(?:^|_|-|\b)(create|update|delete|remove|write|set|put|post|patch|send|"
    r"cancel|assign|book|submit|insert|upsert|move|transfer|approve|refund)"
    r"(?:$|_|-|\b)",
    re.IGNORECASE,
)
_ACTION_KEYS = ("method", "http_method", "action", "operation", "op", "verb")


# ------------------------------------------------------------------ (a)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def ngrams(text: str, n: int = QUOTE_NGRAM) -> set[tuple[str, ...]]:
    words = _words(text)
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def strings_in(node: Any) -> Iterable[str]:
    """Every string leaf of a JSON tree."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from strings_in(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from strings_in(value)


def quoted_passages(
    candidate_text: str, shared: set[tuple[str, ...]], n: int = QUOTE_NGRAM
) -> list[str]:
    """The passages of `candidate_text` that the `shared` runs of `n` words
    cover, in text order. Runs that overlap or touch are one passage: a
    copied sentence is one passage, not one run per word."""
    words = _words(candidate_text)
    spans: list[list[int]] = []
    for i in range(len(words) - n + 1):
        if tuple(words[i : i + n]) not in shared:
            continue
        if spans and i <= spans[-1][1]:
            spans[-1][1] = i + n
        else:
            spans.append([i, i + n])
    return [" ".join(words[start:end]) for start, end in spans]


def quote_findings(
    candidate_text: str,
    eval_texts: Mapping[str, str],
    eval_names: Iterable[str],
    allowed_text: str,
    examples: int = 1,
) -> list[str]:
    """(a): one finding per eval text the candidate quotes, and per eval it
    names verbatim. `allowed_text` = the baseline's prompt and the task's own
    instructions: a run of words found there is not a quote. `examples`: how
    many copied passages a finding quotes (the build-time check
    gives the build agent more than one to remove)."""
    findings: list[str] = []
    mine = ngrams(candidate_text)
    allowed = ngrams(allowed_text)
    for label, text in sorted(eval_texts.items()):
        shared = (mine & ngrams(text)) - allowed
        if shared:
            passages = quoted_passages(candidate_text, shared)[: max(examples, 1)]
            quoted = "; ".join(f'"{_clipped(p)}"' for p in passages)
            findings.append(
                f"quotes {label} verbatim ({len(shared)} shared {QUOTE_NGRAM}-word"
                f" run(s), e.g. {quoted})"
            )
    lowered = candidate_text.lower()
    base = allowed_text.lower()
    for name in sorted(set(eval_names)):
        clean = name.strip().lower()
        if len(clean) >= MIN_EVAL_NAME_CHARS and clean in lowered and clean not in base:
            findings.append(f'names the eval "{name.strip()}" verbatim')
    return findings


def _clipped(passage: str, limit: int = PASSAGE_CHARS) -> str:
    return passage if len(passage) <= limit else passage[: limit - 1] + "…"


# ------------------------------------------------------------------ (b)


def _output(run: Mapping[str, Any]) -> str | None:
    out = run.get("output")
    if isinstance(out, Mapping):
        out = out.get("output")
    return out if isinstance(out, str) else None


def _json_keys(text: str) -> frozenset[str] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return frozenset(value) if isinstance(value, dict) else None


def output_shape_findings(
    baseline_runs: Sequence[Mapping[str, Any]],
    candidate_runs: Sequence[Mapping[str, Any]],
    label: str,
) -> list[str]:
    """(b), for one eval's val runs, paired by item."""
    base = {i: o for r in baseline_runs if (i := _item(r)) and (o := _output(r))}
    cand = {i: o for r in candidate_runs if (i := _item(r)) and (o := _output(r))}
    items = sorted(set(base) & set(cand))
    if not items:
        return []
    findings: list[str] = []
    base_len = median(len(base[i]) for i in items)
    cand_len = median(len(cand[i]) for i in items)
    if base_len > 0:
        ratio = cand_len / base_len
        if ratio > LENGTH_RATIO_LIMIT or ratio < 1 / LENGTH_RATIO_LIMIT:
            findings.append(
                f"{label}: median output length {ratio:.2f}× the baseline's on the"
                f" same {len(items)} val item(s) ({base_len:.0f}→{cand_len:.0f} chars)"
            )
    base_keys = {k for i in items if (k := _json_keys(base[i])) is not None}
    cand_keys = {k for i in items if (k := _json_keys(cand[i])) is not None}
    if base_keys and cand_keys:
        before = frozenset().union(*base_keys)
        after = frozenset().union(*cand_keys)
        if before != after:
            added, dropped = sorted(after - before), sorted(before - after)
            findings.append(
                f"{label}: the JSON output's top-level keys changed on val"
                + (f" (added {added})" if added else "")
                + (f" (dropped {dropped})" if dropped else "")
            )
    return findings


def _item(run: Mapping[str, Any]) -> str | None:
    value = run.get("dataset_id") or run.get("eval_input_id")
    return str(value) if value else None


# ------------------------------------------------------------------ (c)


def _trace(run: Mapping[str, Any]) -> list[Any]:
    trace = run.get("task_run_trace") or run.get("trace")
    if isinstance(trace, str):
        try:
            trace = json.loads(trace)
        except ValueError:
            return []
    return trace if isinstance(trace, list) else []


def write_actions(run: Mapping[str, Any]) -> set[str]:
    """The kinds of write action in one run's trace: "<tool> <METHOD>" for a
    call whose method is a write, "<tool> <action>" for a call whose action
    names one, and "<tool>" for a tool whose own name does."""
    kinds: set[str] = set()
    for message in _trace(run):
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            fn = (
                call.get("function")
                if isinstance(call.get("function"), Mapping)
                else call
            )
            name = str(fn.get("name") or "")
            args: Any = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            args = args if isinstance(args, Mapping) else {}
            action = next(
                (str(args[k]) for k in _ACTION_KEYS if isinstance(args.get(k), str)),
                None,
            )
            if action and action.lower() in _WRITE_METHODS:
                kinds.add(f"{name} {action.upper()}")
            elif action and _WRITE_VERB_RE.search(action):
                kinds.add(f"{name} {action}")
            elif _WRITE_VERB_RE.search(name):
                kinds.add(name)
    return kinds


def write_action_findings(
    baseline_runs: Sequence[Mapping[str, Any]],
    candidate_runs: Sequence[Mapping[str, Any]],
    label: str,
) -> list[str]:
    """(c), for one eval's val runs."""
    before: set[str] = set()
    for run in baseline_runs:
        before |= write_actions(run)
    after: set[str] = set()
    for run in candidate_runs:
        after |= write_actions(run)
    new = sorted(after - before)
    if not new:
        return []
    return [f"{label}: new kind(s) of write action not in the baseline's traces: {new}"]


def rationale_addresses(rationale: str) -> bool:
    """A decision addresses its winner's integrity flags with an
    `Integrity:` line (any case)."""
    return INTEGRITY_WORD in rationale.lower()
