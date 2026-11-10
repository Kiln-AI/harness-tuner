"""The generality check before the train measurement.

A build can raise its train score without doing the task better: by writing
the train set into the prompt. It names an entity or an id it saw in a train
input, it states the answer a train item expects, it adds a rule that fits
only the items it was shown, or it tells the model what the judge looks for.
The train measurement then rewards text that does nothing for a new input,
and the val gate is the only filter left.

So every build that changes the run config's instructions — its prompt or the
text of its attached skills — gets one `generality_review` session before its
train measurement. The session has no tools; its input is the built prompt
beside its parent's, what the build added to the skills' text
(`skill_changes`), the task's own instructions, the item's hypothesis and a
sample of train inputs (the item's target items first, and named in
`target_items`). It names the added text that memorises the train set or games
the measurement. Naming a tool or a skill the prompt needs is allowed.

A skill is immutable once installed, so a skill change is always a new
skill id: the same ids on both run configs mean the skills did not change.
A new skill is diffed against the skill it replaces: the first parent skill
its provenance names, attached or detached; else the one skill the build
detached, when it swapped exactly one skill for one. A skill with no
predecessor is new text in full. Skill text that cannot be read leaves the
prompt to be checked alone, and the record says so.

On a leak the build agent gets the findings back for ONE rebuild, and the
rebuilt run config is checked again. A rebuild that still leaks ends the
experiment as a dead end, before any train budget is spent (`items.py`). A
reused run config (`existing_rc_id`) is checked too but never rebuilt: a leak
ends it a dead end at once.

The check is a guard, not a gate on its own failures: a review that cannot
run, or instructions that cannot be read, let the item go on to its
measurement, and the experiment record says the check did not run. The
integrity checks at the val gate still stand behind it.

The check has three further parts:

- **Code tools.** A code tool's code and its model-facing description are
  instructions too, and a lookup table of train answers fits in one. A new
  or changed code tool is diffed against the one it replaces, exactly like a
  skill (`code_tool_changes`).
- **The exact-match pre-check** (`exact_matches`), in code, before the
  review: the distinctive strings of EVERY train item — its id, the ids and
  long numbers in its input and expected output, eight-word runs of its
  input, and its expected values — searched for in the added text. A string
  in more than a fifth of the train items is boilerplate and is not
  searched; a string the parent or the task instructions already have is
  not added. A match is a leak with no review: it costs nothing, and it is
  certain where the review is a judgment.
- **Expected outputs for the reviewer.** Agents can read train labels
  (`get_train_expected`), so a build can copy one. The reviewer gets the
  expected output of every train item it is shown (`train_expected`).

**The baseline audit** (`audit_baseline`). The check above
reads only what a build ADDS, so text the baseline already carries is never
checked: an inherited skill that copies train records passes every build.
Once per baseline, at setup, the same two checks read the baseline's WHOLE
text — prompt, skills, code tools — as if all of it were added: the
exact-match pre-check first, the review only when it finds nothing. Leaks
become one issue record (`baseline_audit`) that asks for a scrub. The audit
never stops the run.

The audit's second half: eval text. The build-time judge-copy
check (`kiln_harness_tuner.judge_copy`) exempts text the baseline carries, so the
audit reads the baseline's whole text with the same matcher, with only the
task's instructions exempt. A copy becomes one `baseline_judge_copy` issue
that asks for a scrub. Code only; once per baseline, like the first half.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.latency import sample_inputs, split_inputs
from kiln_harness_tuner.memory_schema import BASELINE_JUDGE_COPY_TAG
from kiln_harness_tuner.prompt_text import (
    CodeToolText,
    RcText,
    SkillText,
    normalized,
    rc_texts,
    task_instructions,
    whole_texts,
)
from kiln_harness_tuner.schemas import (
    SUBSCRIPTION_LANE,
    ExperimentItem,
    FixWork,
    GeneralityReviewInput,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

# How many train inputs the reviewer sees, and how much of each.
GENERALITY_TRAIN_INPUTS = 12
TRAIN_INPUT_CHARS = 600
# How much of a leak finding reaches a one-line record or event.
FINDING_CHARS = 200
# How much added skill text the reviewer sees, over every changed file.
SKILL_CHANGE_CHARS = 30_000
# How much added code-tool text the reviewer sees, over every changed file.
CODE_TOOL_CHANGE_CHARS = 20_000
# How much of each train item's expected output the reviewer sees.
TRAIN_EXPECTED_CHARS = 600
# The exact-match pre-check: an id-like token has at least this many
# characters and a digit; a run of input text is this many words; an
# expected string value has at least this many characters and a digit or a
# space; a string in more than this share of the train items (and more than
# `COMMON_MIN_ITEMS`) is boilerplate, never a match.
MATCH_MIN_ID_CHARS = 5
MATCH_RUN_WORDS = 8
MATCH_MIN_VALUE_CHARS = 8
COMMON_SHARE = 0.2
COMMON_MIN_ITEMS = 2
# How many exact matches become findings; the rest are counted.
MATCH_FINDINGS = 8


@dataclass(frozen=True)
class GeneralityCheck:
    """One check's answer. `unchanged`: the instructions are the parent's,
    nothing to review. `clean` / `leaked`: the review ran. `unchecked`: it
    could not run, and `note` says why. `review_notes`: the reviewer's own
    notes on what it was not sure of — for the wave log, not the record."""

    status: Literal["unchanged", "clean", "leaked", "unchecked"]
    leaks: list[str] = field(default_factory=list)
    note: str = ""
    review_notes: str = ""

    @property
    def summary(self) -> str:
        if self.status == "leaked":
            first = self.leaks[0][:FINDING_CHARS]
            more = f" (+{len(self.leaks) - 1} more)" if len(self.leaks) > 1 else ""
            return f"{first}{more}"
        return self.note or self.status


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def train_input_lines(ctx: Ctx, first_ids: list[str]) -> list[str]:
    """`"<item id>: <input>"` for the reviewer, read from disk by CODE: the
    item's named targets first, then a fixed sample of the train split.
    Train only — no expected output, no score, no trace."""
    try:
        inputs = split_inputs(ctx.target.project_path, ctx.target.task_id, "train")
    except Exception as exc:  # the review then judges the text alone
        ctx.log.event("generality_inputs_unavailable", error=repr(exc)[:200])
        return []
    by_id = {i.id: i for i in inputs}
    chosen = [by_id[i] for i in first_ids if i in by_id][:GENERALITY_TRAIN_INPUTS]
    taken = {i.id for i in chosen}
    rest = [i for i in inputs if i.id not in taken]
    chosen += sample_inputs(rest, GENERALITY_TRAIN_INPUTS - len(chosen))
    lines = []
    for inp in chosen:
        text = (
            inp.plaintext
            if inp.plaintext is not None
            else json.dumps(inp.structured, ensure_ascii=False)
        )
        lines.append(f"{inp.id}: {_clip(text, TRAIN_INPUT_CHARS)}")
    return lines


Bundle = SkillText | CodeToolText


def _ident(bundle: Bundle) -> str:
    return bundle.skill_id if isinstance(bundle, SkillText) else bundle.tool_id


def _replaced(
    new: Bundle,
    parent_bundles: dict[str, Any],
    detached: list[Any],
    added: int,
) -> Any:
    """The parent skill (or code tool) `new` replaces: the first one its
    provenance names (attached or detached); else the one detached when the
    build swapped exactly one for one; else none."""
    for bundle_id in new.derived_from:
        if bundle_id in parent_bundles:
            return parent_bundles[bundle_id]
    return detached[0] if len(detached) == 1 and added == 1 else None


def _added_lines(before: str, after: str) -> str:
    """The unified diff of two texts (one line of context), or "" when it
    adds no line."""
    hunks = list(
        difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=1)
    )[2:]
    if not any(h.startswith("+") for h in hunks):
        return ""
    return "\n".join(hunks)


def _file_blocks(new: Bundle, old: Bundle | None, kind: str = "skill") -> list[str]:
    blocks: list[str] = []
    for path, text in new.files.items():
        head = f"{kind} {new.name} ({_ident(new)}), {path}"
        if old is None or path not in old.files:
            source = f"replaces {kind} {old.name}" if old is not None else f"new {kind}"
            blocks.append(f"{head}: new file ({source}) — whole text\n{text}")
            continue
        added = _added_lines(old.files[path], text)
        if added and normalized(old.files[path]) != normalized(text):
            blocks.append(
                f"{head}: changed from {kind} {old.name} ({_ident(old)})"
                f" — unified diff\n{added}"
            )
    return blocks


def _new_bundles(
    parent_ids: tuple[str, ...],
    parent: dict[str, Any],
    candidate_ids: tuple[str, ...],
    candidate: dict[str, Any],
) -> list[tuple[Any, Any]]:
    """(new bundle, the parent bundle it replaces or None), per bundle the
    candidate attaches and the parent does not."""
    detached = [parent[i] for i in parent_ids if i not in candidate]
    added = [candidate[i] for i in candidate_ids if i not in parent]
    return [(new, _replaced(new, parent, detached, len(added))) for new in added]


def _changes(
    parent_ids: tuple[str, ...],
    parent: dict[str, Any] | None,
    candidate_ids: tuple[str, ...],
    candidate: dict[str, Any] | None,
    kind: str,
    cap: int,
) -> list[str] | None:
    if set(parent_ids) == set(candidate_ids):
        return []
    if parent is None or candidate is None:
        return None
    blocks = [
        block
        for new, old in _new_bundles(parent_ids, parent, candidate_ids, candidate)
        for block in _file_blocks(new, old, kind)
    ]
    return _clipped(blocks, cap)


def _clipped(blocks: list[str], cap: int, what: str = "changed file(s)") -> list[str]:
    """`blocks` within `cap` characters; the rest are counted, not shown."""
    shown: list[str] = []
    left = cap
    for block in blocks:
        if left <= 0:
            shown.append(f"… {len(blocks) - len(shown)} more {what} not shown")
            break
        shown.append(block if len(block) <= left else block[: left - 1] + "…")
        left -= len(block)
    return shown


def skill_changes(parent: RcText, candidate: RcText) -> list[str] | None:
    """What the candidate added to its parent's skill text, one block per
    changed or new file, within `SKILL_CHANGE_CHARS`. [] when the skills
    did not change; None when they changed and cannot be read."""
    return _changes(
        parent.skill_ids,
        parent.skills,
        candidate.skill_ids,
        candidate.skills,
        "skill",
        SKILL_CHANGE_CHARS,
    )


def code_tool_changes(parent: RcText, candidate: RcText) -> list[str] | None:
    """The same for the attached code tools: their code and their
    model-facing description, within `CODE_TOOL_CHANGE_CHARS`."""
    return _changes(
        parent.code_tool_ids,
        parent.code_tools,
        candidate.code_tool_ids,
        candidate.code_tools,
        "code tool",
        CODE_TOOL_CHANGE_CHARS,
    )


# ---- the train items, read by code ----


@dataclass(frozen=True)
class TrainDoc:
    """One TRAIN eval input as code reads it from disk: every string of its
    input, and its expected output (the eval input's `reference`)."""

    id: str
    input_text: str
    reference: Any = None


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _train_ids(ctx: Ctx, project: Path) -> frozenset[str]:
    """The run's train item set — the one the gateway serves agents from —
    or, with no run directory, the same set computed from disk."""
    from kiln_harness_tuner.gateway.trainset import TrainSet, compute

    path = ctx.train_set_path
    if path is not None and Path(path).is_file():
        return TrainSet.load(Path(path)).eval_inputs
    return compute(project, ctx.target.task_id).eval_inputs


def train_documents(ctx: Ctx) -> list[TrainDoc]:
    """Every train eval input, from disk, by CODE. [] when the project or
    the train set cannot be read; the check then goes on without it."""
    from kiln_harness_tuner.quarantine import _children, task_dirs

    try:
        project = Path(ctx.target.project_path).expanduser()
        train = _train_ids(ctx, project)
        docs: list[TrainDoc] = []
        for task_dir in task_dirs(project, ctx.target.task_id):
            for _p, doc in _children(task_dir / "eval_inputs", "eval_input.kiln"):
                item_id = str(doc.get("id"))
                if item_id not in train:
                    continue
                data = doc.get("data")
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        pass
                docs.append(
                    TrainDoc(
                        id=item_id,
                        input_text="\n".join(_strings(data)),
                        reference=doc.get("reference"),
                    )
                )
    except Exception as exc:  # the check then goes on without them
        ctx.log.event("generality_train_unavailable", error=repr(exc)[:200])
        return []
    return sorted(docs, key=lambda d: d.id)


def train_expected_lines(docs: list[TrainDoc], ids: list[str]) -> list[str]:
    """`"<item id>: <expected output>"` for the items the reviewer is shown,
    in that order, clipped; none for an item with no expected output."""
    by_id = {d.id: d for d in docs}
    lines = []
    for item_id in ids:
        doc = by_id.get(item_id)
        if doc is None or doc.reference is None:
            continue
        text = json.dumps(doc.reference, ensure_ascii=False, default=str)
        lines.append(f"{item_id}: {_clip(text, TRAIN_EXPECTED_CHARS)}")
    return lines


# ---- the exact-match pre-check ----

_ID_LIKE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./#]*[A-Za-z0-9]")
_WORD = re.compile(r"\w+")


def _id_tokens(text: str) -> set[str]:
    return {
        t.casefold()
        for t in _ID_LIKE.findall(text)
        if len(t) >= MATCH_MIN_ID_CHARS and any(c.isdigit() for c in t)
    }


def _runs(text: str) -> set[str]:
    words = _WORD.findall(text.casefold())
    n = MATCH_RUN_WORDS
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _values(reference: Any) -> set[str]:
    """The expected output's string values long enough to be specific, and
    its long numbers, normalized."""
    out: set[str] = set()
    stack = [reference]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, bool) or value is None:
            continue
        elif isinstance(value, (int, float)):
            text = str(value)
            if sum(c.isdigit() for c in text) >= MATCH_MIN_ID_CHARS:
                out.add(text)
        elif isinstance(value, str):
            text = " ".join(value.split()).casefold()
            if len(text) >= MATCH_MIN_VALUE_CHARS and (
                " " in text or any(c.isdigit() for c in text)
            ):
                out.add(text)
    return out


def _flat(text: str) -> str:
    return " ".join(text.split()).casefold()


def _contains(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _distinctive(docs: list[TrainDoc]) -> dict[tuple[str, str], set[str]]:
    """(kind, string) -> the train items it comes from, boilerplate out.
    Kinds: "id" (an id-like token: the item id, or one in its input or
    expected output), "run" (an eight-word run of its input), "value" (an
    expected string value)."""
    found: dict[tuple[str, str], set[str]] = {}
    for doc in docs:
        keys = {("id", doc.id.casefold())}
        keys |= {("id", t) for t in _id_tokens(doc.input_text)}
        keys |= {("run", r) for r in _runs(doc.input_text)}
        for value in _values(doc.reference):
            keys |= {("id", t) for t in _id_tokens(value)}
            if " " in value:
                keys.add(("value", value))
            elif len(value) >= MATCH_MIN_ID_CHARS:
                keys.add(("id", value))
        for key in keys:
            found.setdefault(key, set()).add(doc.id)
    limit = max(COMMON_MIN_ITEMS, int(COMMON_SHARE * len(docs)))
    return {k: v for k, v in found.items() if len(v) <= limit}


def added_text(parent: RcText, candidate: RcText) -> dict[str, str]:
    """Where the candidate's instructions say something the parent's do not:
    "the prompt", "skill <name>, <file>" or "code tool <name>, <file>" -> the
    lines it added there (a line the parent has anywhere is not added)."""
    parent_lines = {
        normalized(line)
        for text in whole_texts(parent).values()
        for line in text.splitlines()
    }
    out: dict[str, str] = {}
    for where, text in whole_texts(candidate).items():
        lines = [
            line
            for line in text.splitlines()
            if line.strip() and normalized(line) not in parent_lines
        ]
        if lines:
            out[where] = "\n".join(lines)
    return out


def exact_matches(
    added: dict[str, str], docs: list[TrainDoc], known: str = ""
) -> list[str]:
    """One finding per train string the added text repeats verbatim:
    `"<string>" in <where> is from train item <id> (<kind>)`. `known`: text
    the run config may repeat (the task's instructions and the parent's own
    text). Code, no model:
    the same answer every time, at no cost."""
    if not added or not docs:
        return []
    distinctive = _distinctive(docs)
    known_flat = _flat(known)
    known_keys = {("id", t) for t in _id_tokens(known)} | {
        ("run", r) for r in _runs(known)
    }
    kinds = {
        "id": "an id or number",
        "run": "its input",
        "value": "its expected output",
    }
    findings: list[str] = []
    for where, text in added.items():
        flat = _flat(text)
        hits = {("id", t) for t in _id_tokens(text)} | {("run", r) for r in _runs(text)}
        hits |= {
            key for key in distinctive if key[0] == "value" and _contains(flat, key[1])
        }
        keys = sorted(hits & set(distinctive) - known_keys)
        # A copied passage is many overlapping runs: one finding per train
        # item, its earliest run in the text.
        runs = sorted((k for k in keys if k[0] == "run"), key=lambda k: flat.find(k[1]))
        first_run: dict[frozenset[str], tuple[str, str]] = {}
        for key in runs:
            first_run.setdefault(frozenset(distinctive[key]), key)
        keys = [k for k in keys if k[0] != "run"] + list(first_run.values())
        for key in keys:
            if key[0] == "value" and _contains(known_flat, key[1]):
                continue
            items = ", ".join(sorted(distinctive[key])[:3])
            findings.append(
                f'"{key[1][:80]}" in {where} is copied from train item {items}'
                f" ({kinds[key[0]]})"
            )
    if len(findings) > MATCH_FINDINGS:
        more = len(findings) - MATCH_FINDINGS
        findings = [*findings[:MATCH_FINDINGS], f"… and {more} more exact match(es)"]
    return findings


async def check_generality(
    ctx: Ctx,
    item: ExperimentItem,
    rc_id: str,
    parent_rc_id: str,
    before_review: Callable[[], None] | None = None,
) -> GeneralityCheck:
    """Review what the build at `rc_id` added to `parent_rc_id`'s
    instructions — its prompt and its skills' text. One `generality_review`
    dispatch, on the item retry ladder, when either differs; none when both
    are the same. `before_review` runs just before that dispatch (the
    item's state)."""
    from kiln_harness_tuner.items import ITEM_DISPATCH_BACKOFFS, call_with_retries

    texts = await rc_texts(ctx, [parent_rc_id, rc_id])
    parent_text, candidate_text = texts[parent_rc_id], texts[rc_id]
    parent, candidate = parent_text.prompt, candidate_text.prompt
    if parent is None or candidate is None:
        which = "the parent's" if parent is None else "the candidate's"
        return GeneralityCheck(
            "unchecked", note=f"not checked — {which} instructions could not be read"
        )
    prompt_changed = normalized(parent) != normalized(candidate)
    changes = skill_changes(parent_text, candidate_text)
    tool_changes = code_tool_changes(parent_text, candidate_text)
    if not prompt_changed and changes == [] and tool_changes == []:
        return GeneralityCheck("unchanged")
    unread = [
        what
        for what, got in (
            ("the skills' text", changes),
            ("the code tools' text", tool_changes),
        )
        if got is None
    ]
    if not prompt_changed and not changes and not tool_changes:
        return GeneralityCheck(
            "unchecked", note=f"not checked — {' and '.join(unread)} could not be read"
        )
    checked = [
        what
        for what, shown in (
            ("the prompt", True),
            ("the skills", changes),
            ("the code tools", tool_changes),
        )
        if shown
    ]
    note = (
        f"{' and '.join(unread)} could not be read — only"
        f" {' and '.join(checked)} {'was' if len(checked) == 1 else 'were'} checked"
        if unread
        else ""
    )
    # Known text: the task's instructions, and the prompt and skills of a
    # parent on the baseline chain. A new line that repeats an id such a
    # parent already has (a route in an inherited skill, say) copies nothing
    # from train that the parent did not carry. Not a code tool's files (a
    # data table there is what the baseline audit reports), and not a parent
    # off the chain (an earlier arm's text was never audited or promoted).
    instructions = "\n".join(await task_instructions(ctx))
    inherited = (
        [
            text
            for where, text in whole_texts(parent_text).items()
            if not where.startswith("code tool ")
        ]
        if parent_rc_id in ctx.policy_exempt_rcs
        else []
    )
    known = "\n".join([instructions, *inherited])
    docs = train_documents(ctx)
    added = added_text(parent_text, candidate_text)
    matches = exact_matches(added, docs, known)
    if inherited:
        passed = len(exact_matches(added, docs, instructions)) - len(matches)
        if passed > 0:
            ctx.log.event("generality_parent_known", item_id=item.id, matches=passed)
    if matches:
        ctx.log.event("generality_exact_match", item_id=item.id, matches=len(matches))
        return GeneralityCheck(
            "leaked", leaks=[f"exact match: {m}" for m in matches], note=note
        )
    work = item.work
    targets = list(work.target_items) if isinstance(work, FixWork) else []
    lines = train_input_lines(ctx, targets)
    shown_ids = [line.split(": ", 1)[0] for line in lines]
    shown = set(shown_ids)
    if before_review is not None:
        before_review()
    result = await call_with_retries(
        ctx.dispatch.generality_review,
        GeneralityReviewInput(
            item_id=item.id,
            candidate_rc_id=rc_id,
            candidate_instructions=candidate,
            parent_instructions=parent,
            task_instructions=instructions,
            hypothesis=work.hypothesis if isinstance(work, FixWork) else None,
            train_inputs=lines,
            train_expected=train_expected_lines(docs, shown_ids),
            target_items=[t for t in dict.fromkeys(targets) if t in shown],
            skill_changes=changes or [],
            code_tool_changes=tool_changes or [],
        ),
        backoffs=ITEM_DISPATCH_BACKOFFS,
        sleep=ctx.sleep,
        log=ctx.log,
        source="generality_review",
        item_id=item.id,
    )
    ctx.log.event(
        "spend",
        item_id=item.id,
        source="generality_review",
        lane=SUBSCRIPTION_LANE,
        cost_usd=result.usage.cost_usd,
        tokens=result.usage.tokens,
        unmeasured_tokens=result.usage.unmeasured_tokens,
    )
    if result.data is None:
        why = result.error.detail if result.error else (result.detail or "no answer")
        return GeneralityCheck(
            "unchecked", note=f"not checked — the review did not run ({why[:160]})"
        )
    leaks = [leak.strip() for leak in result.data.leaks if leak.strip()]
    return GeneralityCheck(
        "leaked" if leaks else "clean",
        leaks=leaks,
        note=note,
        review_notes=result.data.notes.strip(),
    )


# ---- the baseline audit ----

BASELINE_AUDIT_ITEM = "baseline-audit"


@dataclass(frozen=True)
class BaselineAudit:
    """One audit's answer. `known`: an open audit issue already covers this
    baseline, so nothing ran. `clean` / `leaked`: it ran. `unchecked`: it
    could not run, and `note` says why. `issue_id`: the issue it wrote."""

    status: Literal["known", "clean", "leaked", "unchecked"]
    findings: list[str] = field(default_factory=list)
    note: str = ""
    issue_id: str | None = None
    # The audit's eval-text half (`judge_copy`), its own answer
    #   in the same shape ("leaked" = the baseline copies eval text).
    judge_copy: BaselineAudit | None = None


def _whole_blocks(rc: RcText) -> tuple[list[str], list[str]]:
    """Every file of every attached skill and code tool, each as a new file:
    the reviewer reads the baseline's whole text as added."""
    skills = [b for x in (rc.skills or {}).values() for b in _file_blocks(x, None)]
    tools = [
        b
        for x in (rc.code_tools or {}).values()
        for b in _file_blocks(x, None, "code tool")
    ]
    return (
        _clipped(skills, SKILL_CHANGE_CHARS, "file(s)"),
        _clipped(tools, CODE_TOOL_CHANGE_CHARS, "file(s)"),
    )


async def audit_baseline(ctx: Ctx, rc_id: str) -> BaselineAudit:
    """Audit the baseline's own text, once per baseline: for train-specific
    content, and for eval text it copies. Each half writes its
    own issue on a find and logs its own event either way
    (`baseline_audit`, `baseline_judge_copy_audit`)."""
    train = await _audit_train_copies(ctx, rc_id)
    return replace(train, judge_copy=await _audit_eval_copies(ctx, rc_id))


async def _audit_eval_copies(ctx: Ctx, rc_id: str) -> BaselineAudit:
    """The eval-text half: the judge-copy matcher on the baseline's WHOLE
    text, with only the task's instructions exempt. Code only. The
    build-time check exempts what the baseline carries, so this is where
    such a phrase is reported: one `baseline_judge_copy` scrub issue."""
    from kiln_harness_tuner.judge_copy import audit_judge_copies

    def done(audit: BaselineAudit) -> BaselineAudit:
        line = ""
        if audit.status == "leaked":
            line = (
                f"baseline audit: {rc_id} copies eval text"
                f" ({len(audit.findings)} finding(s)) — scrub issue"
                f" {audit.issue_id}: {audit.findings[0][:FINDING_CHARS]}"
            )
        ctx.log.event(
            "baseline_judge_copy_audit",
            rc_id=rc_id,
            status=audit.status,
            findings=list(audit.findings),
            issue_id=audit.issue_id,
            note=audit.note[:300],
            line=line,
        )
        return audit

    known = await ctx.memory.find_baseline_audit(rc_id, BASELINE_JUDGE_COPY_TAG)
    if known is not None:
        return done(BaselineAudit("known", issue_id=known))
    text = (await rc_texts(ctx, [rc_id]))[rc_id]
    if text.prompt is None:
        return done(
            BaselineAudit("unchecked", note="the baseline's prompt could not be read")
        )
    try:
        findings = await audit_judge_copies(ctx, text)
    except (KilnError, KeyError, TypeError, ValueError) as exc:
        return done(
            BaselineAudit(
                "unchecked", note=f"the evals could not be read ({repr(exc)[:160]})"
            )
        )
    if not findings:
        return done(BaselineAudit("clean"))
    issue_id = await ctx.memory.record_baseline_judge_copy(rc_id, findings)
    return done(BaselineAudit("leaked", findings, issue_id=issue_id))


async def _audit_train_copies(ctx: Ctx, rc_id: str) -> BaselineAudit:
    """The train half: the generality check's two checks on
    the baseline's whole text. Writes an issue on a leak."""
    from kiln_harness_tuner.items import ITEM_DISPATCH_BACKOFFS, call_with_retries

    def done(audit: BaselineAudit) -> BaselineAudit:
        ctx.log.event(
            "baseline_audit",
            rc_id=rc_id,
            status=audit.status,
            findings=len(audit.findings),
            issue_id=audit.issue_id,
            note=audit.note[:300],
        )
        return audit

    known = await ctx.memory.find_baseline_audit(rc_id)
    if known is not None:
        return done(BaselineAudit("known", issue_id=known))
    text = (await rc_texts(ctx, [rc_id]))[rc_id]
    if text.prompt is None:
        return done(
            BaselineAudit("unchecked", note="the baseline's prompt could not be read")
        )
    unread = [
        what
        for what, got in (
            ("the skills' text", text.skills),
            ("the code tools' text", text.code_tools),
        )
        if got is None
    ]
    note = f"{' and '.join(unread)} could not be read" if unread else ""
    instructions = "\n".join(await task_instructions(ctx))
    docs = train_documents(ctx)
    whole = {where: t for where, t in whole_texts(text).items() if t.strip()}
    findings = [f"exact match: {m}" for m in exact_matches(whole, docs, instructions)]
    if not findings:
        lines = train_input_lines(ctx, [])
        skill_blocks, tool_blocks = _whole_blocks(text)
        result = await call_with_retries(
            ctx.dispatch.generality_review,
            GeneralityReviewInput(
                item_id=BASELINE_AUDIT_ITEM,
                candidate_rc_id=rc_id,
                candidate_instructions=text.prompt,
                parent_instructions="",
                task_instructions=instructions,
                train_inputs=lines,
                train_expected=train_expected_lines(
                    docs, [line.split(": ", 1)[0] for line in lines]
                ),
                skill_changes=skill_blocks,
                code_tool_changes=tool_blocks,
            ),
            backoffs=ITEM_DISPATCH_BACKOFFS,
            sleep=ctx.sleep,
            log=ctx.log,
            source="baseline_audit",
            item_id=BASELINE_AUDIT_ITEM,
        )
        ctx.log.event(
            "spend",
            item_id=BASELINE_AUDIT_ITEM,
            source="baseline_audit",
            lane=SUBSCRIPTION_LANE,
            cost_usd=result.usage.cost_usd,
            tokens=result.usage.tokens,
            unmeasured_tokens=result.usage.unmeasured_tokens,
        )
        if result.data is None:
            why = result.error.detail if result.error else (result.detail or "")
            return done(
                BaselineAudit("unchecked", note=f"the review did not run ({why[:160]})")
            )
        findings = [leak.strip() for leak in result.data.leaks if leak.strip()]
    if not findings:
        return done(BaselineAudit("clean", note=note))
    issue_id = await ctx.memory.record_baseline_audit(rc_id, findings)
    return done(BaselineAudit("leaked", findings, note=note, issue_id=issue_id))
