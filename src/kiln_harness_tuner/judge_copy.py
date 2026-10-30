"""The judge-copy check at build time.

The promotion gate blocks a candidate whose prompt copies the evals: a run
of `integrity.QUOTE_NGRAM` words shared with a judge prompt, an eval
config, a spec or an eval description, or an eval's full name
(`integrity.quote_findings`, check (a)). That check runs only at the gate,
after the full train measurement and the val pass. A phrase can come in
through a parent prompt from an earlier experiment. The generality check
reads only the lines a build adds and looks only for train text, so it does
not see such a phrase. Without a build-time check, the candidate would be
measured on train and on val, and then blocked.

So the same matcher also runs right after the build, next to the
generality check, before any train spend. It reads the WHOLE text of the
built run config: its prompt, and every file of its attached skills and
code tools. Whole text, not only added lines, so a phrase that a parent
carries is found too.

**The exemption is the baseline, not the parent.** A run of words that the
current baseline already carries, or that the task's own instructions
contain, is not a copy:

- for the prompt, the exempt text is the baseline's prompt and the task's
  instructions, which is the exact rule of the gate. A prompt that passes
  here is not blocked by check (a) at the gate, while the evals stay the
  same;
- for a skill or a code tool file, the exempt text is the baseline's whole
  text (prompt, skills, code tools) and the task's instructions. A skill
  that the baseline attaches is then not a copy in every build that keeps
  it.

The parent is not the exemption, because the parent is where an inherited
phrase comes from: with the parent exempt, an inherited phrase passes. A
phrase that the baseline itself carries does not block a build. The
baseline audit (`generality.audit_baseline`) reports it once per baseline
as a scrub issue, with the task's instructions as the only exempt text.

On a hit the build agent gets the findings for ONE rebuild, together with
any generality findings (`items._review_build`). A second hit ends the item
`dead_end` before any train spend. A reused run config is checked and
never rebuilt. A check that cannot read its inputs does not stop the item:
the record says so, and the gate check stays behind it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from kiln_harness_tuner.integrity import quote_findings, strings_in
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.prompt_text import (
    RcText,
    rc_texts,
    task_instructions,
    whole_texts,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

# How many copied passages one build-time finding quotes.
BUILD_EXAMPLES = 3
# How much of the findings reaches a one-line record or event.
FINDING_CHARS = 300
PROMPT_WHERE = "the prompt"  # `prompt_text.whole_texts` names it so


@dataclass(frozen=True)
class EvalTexts:
    """The text of the active evals that a run config may not copy.
    `texts`: label ("the judge of eval X", "the spec of eval X", "the
    description of eval X") -> text. `names`: the eval names. `unread`: the
    eval ids whose judge could not be read."""

    texts: dict[str, str]
    names: list[str]
    unread: list[str] = field(default_factory=list)


async def read_eval_texts(
    ctx: Ctx, active: Sequence[tuple[dict[str, Any], str]] | None = None
) -> EvalTexts:
    """The judge prompts, eval configs, specs and descriptions of the
    active evals (`active`, or the task's active set when None). One reader
    for the gate and the build check, so both compare against the same
    text."""
    if active is None:
        from kiln_harness_tuner.orchestrator import _active_evals

        active = await _active_evals(ctx)
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    try:
        specs = await ctx.kiln.call("GET", f"{base}/specs") or []
    except KilnError:
        specs = []
    texts: dict[str, str] = {}
    names: list[str] = []
    unread: list[str] = []
    for eval_def, config_id in active:
        eval_id = str(eval_def["id"])
        name = str(eval_def.get("name") or eval_id)
        names.append(name)
        if eval_def.get("description"):
            texts[f"the description of eval {name}"] = str(eval_def["description"])
        try:
            config = await ctx.kiln.call(
                "GET", f"{base}/evals/{eval_id}/eval_config/{config_id}"
            )
            judge = "\n".join(strings_in((config or {}).get("properties")))
            if judge:
                texts[f"the judge of eval {name}"] = judge
        except KilnError:
            unread.append(eval_id)
        for spec in specs if isinstance(specs, list) else []:
            if isinstance(spec, dict) and str(spec.get("eval_id")) == eval_id:
                text = "\n".join(
                    strings_in([spec.get("definition"), spec.get("properties")])
                )
                if text:
                    texts[f"the spec of eval {name}"] = text
    return EvalTexts(texts=texts, names=names, unread=unread)


def copy_findings(
    rc: RcText,
    evals: EvalTexts,
    prompt_allowed: str,
    other_allowed: str,
    examples: int = BUILD_EXAMPLES,
) -> list[str]:
    """One finding per (place, eval text) the run config copies, and per
    eval it names verbatim, as `"<where> <integrity finding>"`.
    `prompt_allowed`: the exempt text for the prompt (the gate's rule);
    `other_allowed`: the exempt text for a skill or code-tool file."""
    findings: list[str] = []
    for where, text in whole_texts(rc).items():
        if not text.strip():
            continue
        allowed = prompt_allowed if where == PROMPT_WHERE else other_allowed
        findings += [
            f"{where} {finding}"
            for finding in quote_findings(
                text, evals.texts, evals.names, allowed, examples=examples
            )
        ]
    return findings


def _unread_parts(rc: RcText) -> list[str]:
    return [
        what
        for what, got in (
            ("the skills' text", rc.skills),
            ("the code tools' text", rc.code_tools),
        )
        if got is None
    ]


@dataclass(frozen=True)
class JudgeCopyCheck:
    """One check's answer. `clean`: no copy. `copied`: `findings` name each
    copy. `unchecked`: it could not run, and `note` says why. `note` on a
    `clean` or `copied` answer names run-config text that could not be
    read. `unread_evals`: the evals whose judge could not be read — for the
    wave log, as at the gate (`integrity_input_unreadable`)."""

    status: Literal["clean", "copied", "unchecked"]
    findings: list[str] = field(default_factory=list)
    note: str = ""
    unread_evals: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.status == "copied":
            first = self.findings[0][:FINDING_CHARS]
            more = (
                f" (+{len(self.findings) - 1} more)" if len(self.findings) > 1 else ""
            )
            return f"{first}{more}"
        return self.note or self.status


async def check_judge_copies(
    ctx: Ctx, rc_id: str, baseline_rc_id: str
) -> JudgeCopyCheck:
    """Look for eval text in the whole text of `rc_id`: its prompt, skills
    and code tools. Code only, no model call, no spend."""
    try:
        evals = await read_eval_texts(ctx)
    except (KilnError, KeyError, TypeError, ValueError) as exc:
        return JudgeCopyCheck(
            "unchecked",
            note=f"not checked — the evals could not be read ({repr(exc)[:120]})",
        )
    if not evals.texts and not evals.names:
        return JudgeCopyCheck("clean")  # no active eval text to copy
    texts = await rc_texts(ctx, list(dict.fromkeys([baseline_rc_id, rc_id])))
    candidate, baseline = texts[rc_id], texts[baseline_rc_id]
    if candidate.prompt is None:
        return JudgeCopyCheck(
            "unchecked", note="not checked — the candidate's prompt could not be read"
        )
    instructions = "\n".join(await task_instructions(ctx))
    notes = [f"{what} could not be read" for what in _unread_parts(candidate)]
    if baseline.prompt is None:
        notes.append(
            "the baseline's prompt could not be read, so only the task's"
            " instructions were exempt"
        )
    notes += [
        f"the baseline's {what.removeprefix('the ')} could not be read, so it"
        " was not exempt"
        for what in _unread_parts(baseline)
    ]
    prompt_allowed = "\n".join([baseline.prompt or "", instructions])
    other_allowed = "\n".join([*whole_texts(baseline).values(), instructions])
    findings = copy_findings(candidate, evals, prompt_allowed, other_allowed)
    return JudgeCopyCheck(
        "copied" if findings else "clean",
        findings=findings,
        note="; ".join(notes),
        unread_evals=list(evals.unread),
    )


async def audit_judge_copies(ctx: Ctx, rc: RcText) -> list[str]:
    """The baseline audit's half for eval text: the findings
    for the baseline's whole text, with only the task's instructions
    exempt. Raises what `read_eval_texts` raises."""
    evals = await read_eval_texts(ctx)
    if not evals.texts and not evals.names:
        return []
    instructions = "\n".join(await task_instructions(ctx))
    return copy_findings(rc, evals, instructions, instructions)
