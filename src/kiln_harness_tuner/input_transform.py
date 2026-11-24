"""The input transform lever, as code reads it.

A run config's `input_transform` is `{"type": "jinja", "template": <text>}`
or null. Kiln renders the template against the task input at run time. The
result is the first user message the model is sent. The stored input of the
task run stays the original input.

This module holds what every reader of the lever shares:

- **The shape** (`transform_problem`). The gateway refuses a run config
  whose `input_transform` is not null and not a Jinja template of at most
  `TEMPLATE_MAX_CHARS` characters.
- **The template text** (`template_of`). The integrity checks read it as
  one more place where a run config gives the model text
  (`prompt_text.whole_texts`, "the input transform").
- **The render check** (`render_on_inputs`). Kiln compiles a template when
  it saves it, and that finds a syntax error only. A template that reads a
  field that some inputs lack fails at run time, on those inputs. So the
  gateway renders a new template on every train input before it creates
  the run config, with Kiln's own engine from the installed `kiln-ai`
  library. A failure refuses the create. The same render gives the build
  agent the first user message of one train item, with no model call.
  When the library has no template engine, or does not compile a template
  that the server compiled, nothing is rendered and the report says so.
- **The change** (`change_line`). One line that says what an arm did to its
  parent's transform.
- **Was the lever applied** (`applied_verdict`). The first user message of
  each drive is the rendered template. An arm that changes the transform is
  applied when that message differs from its parent's on at least one
  paired train item. It is not applied when the message is the same on
  every paired item, with at least `MIN_PAIRS` of them: the template renders
  what the parent's did. With fewer pairs the lever is unverified.

**The lever applies to single-turn tasks only.** On a multi-turn task Kiln
renders the template for the user message of each turn, and the tuner has
no evidence for what that does to a conversation. The gateway refuses a new
template there, and the loop ends an arm that changes the transform there
(`items.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from kiln_harness_tuner.integrity import _trace
from kiln_harness_tuner.levers import LeverVerdict

# The lever's id: the `technique` of an experiment that changes the
# transform, and the name of its lever document.
LEVER = "input_transform"
TRANSFORM_KEY = "input_transform"
TEMPLATE_TYPE = "jinja"
# The longest template the gateway accepts. A template is short code, not a
# place for text: a longer one usually carries text that belongs in the
# prompt.
TEMPLATE_MAX_CHARS = 20_000
# How many paired train items the "not applied" verdict needs.
MIN_PAIRS = 2
# `prompt_text.whole_texts` names the template so.
WHERE = "the input transform"
# How many render failures a report names, and how much of each error.
RENDER_FAILURES_SHOWN = 5
RENDER_ERROR_CHARS = 300
# How much of the rendered sample a report carries.
RENDER_SAMPLE_CHARS = 4000
MULTI_TURN_REFUSAL = (
    "the input transform applies to single-turn tasks only, and this task is multi-turn"
)


def transform_of(props: Any) -> Any:
    """The `input_transform` of run-config properties, or None."""
    return props.get(TRANSFORM_KEY) if isinstance(props, Mapping) else None


def template_of(props: Any) -> str:
    """The template text of run-config properties, or "" when they carry no
    Jinja transform."""
    transform = transform_of(props)
    if not isinstance(transform, Mapping):
        return ""
    template = transform.get("template")
    return template if isinstance(template, str) else ""


def transform_problem(transform: Any) -> str | None:
    """Why `transform` is not a value the tuner accepts, or None. Null is
    accepted: it means no transform."""
    if transform is None:
        return None
    if not isinstance(transform, Mapping):
        return 'input_transform must be null or {"type": "jinja", "template": "…"}'
    if transform.get("type") != TEMPLATE_TYPE:
        return (
            f"input_transform.type must be {TEMPLATE_TYPE!r}:"
            f" got {transform.get('type')!r}"
        )
    template = transform.get("template")
    if not isinstance(template, str) or not template.strip():
        return "input_transform.template must be a non-empty text"
    if len(template) > TEMPLATE_MAX_CHARS:
        return (
            f"input_transform.template has {len(template)} characters: the"
            f" limit is {TEMPLATE_MAX_CHARS}"
        )
    extra = sorted(set(transform) - {"type", "template"})
    if extra:
        return f"input_transform has unknown keys: {extra}"
    return None


def changed(parent_props: Any, props: Any) -> bool:
    """Does the run config's transform differ from its parent's? A missing
    key and null are the same: no transform."""
    return transform_of(parent_props) != transform_of(props)


def _size(props: Any) -> str:
    template = template_of(props)
    return f"a template of {len(template)} characters" if template else "none"


def change_line(parent_props: Any, props: Any) -> str:
    """What an arm did to its parent's transform, in one line."""
    return f"the parent has {_size(parent_props)}, the arm has {_size(props)}"


def first_user_message(run: Mapping[str, Any]) -> str | None:
    """The first user message of a drive's trace, as one text. None when the
    run carries no trace or no user message."""
    for message in _trace(run):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(part.get("text"))
                for part in content
                if isinstance(part, Mapping) and part.get("text") is not None
            ]
            return "\n".join(parts) if parts else None
        return None
    return None


def user_messages(
    runs: Iterable[tuple[str, Mapping[str, Any]]],
) -> dict[str, str]:
    """item id -> the first user message of its drive, for the drives that
    have one."""
    found: dict[str, str] = {}
    for item_id, run in runs:
        message = first_user_message(run)
        if message is not None:
            found[item_id] = message
    return found


def applied_verdict(
    parent_messages: Mapping[str, str], messages: Mapping[str, str]
) -> LeverVerdict:
    """The lever against the drives: `messages` and `parent_messages` are
    item id -> first user message, for the arm and for its parent."""
    paired = sorted(set(messages) & set(parent_messages))
    moved = [i for i in paired if messages[i] != parent_messages[i]]
    if moved:
        return LeverVerdict(
            "applied",
            f"input transform applied: the first user message differs from the"
            f" parent's on {len(moved)} of {len(paired)} paired train item(s)",
        )
    if len(paired) < MIN_PAIRS:
        return LeverVerdict(
            "unverified",
            "lever unverified: input transform: fewer than"
            f" {MIN_PAIRS} train items have a first user message for the arm"
            " and for its parent",
        )
    return LeverVerdict(
        "not_applied",
        "lever not applied: the input transform changed, but the first user"
        f" message is the parent's on all {len(paired)} paired train items",
    )


# ---- the render check ----


class RenderUnavailable(RuntimeError):
    """The installed `kiln-ai` library cannot render this template."""


@dataclass(frozen=True)
class RenderReport:
    """A template rendered on task inputs. `checked`: how many inputs were
    rendered. `failures`: (item id, error) for each input the template
    fails on, in id order. `sample`: (item id, the rendered text) for one
    input that rendered. `note`: why nothing was rendered."""

    checked: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    sample: tuple[str, str] | None = None
    note: str = ""

    @property
    def failure_line(self) -> str:
        """The failures in one sentence, with the first of them."""
        item_id, error = self.failures[0]
        return (
            f"the template fails to render on {len(self.failures)} of"
            f" {self.checked} train inputs, for example {item_id}: {error}"
        )


def _renderer(template: str) -> Callable[[Any], str]:
    """Kiln's own render of `template`, from the installed library."""
    try:
        from kiln_ai.datamodel.input_transform import JinjaInputTransform
        from kiln_ai.utils.jinja_engine import render_input_transform
    except ImportError as exc:
        raise RenderUnavailable(
            "the installed kiln-ai library has no template engine"
        ) from exc
    try:
        transform = JinjaInputTransform(template=template)
    except ValueError as exc:
        raise RenderUnavailable(
            "the installed kiln-ai library does not compile the template"
            f" ({str(exc)[:RENDER_ERROR_CHARS]})"
        ) from exc
    return lambda task_input: render_input_transform(transform, task_input)


def render_on_inputs(
    template: str, inputs: Mapping[str, Any], sample_id: str | None = None
) -> RenderReport:
    """Render `template` on each task input (`inputs`: item id -> the
    structured input, or the plaintext). `sample_id`: the input whose
    rendered text the report carries, else the first that renders."""
    if not inputs:
        return RenderReport(note="there is no train input to render the template on")
    try:
        render = _renderer(template)
    except RenderUnavailable as exc:
        return RenderReport(note=f"not rendered: {exc}")
    failures: list[tuple[str, str]] = []
    sample: tuple[str, str] | None = None
    for item_id in sorted(inputs):
        try:
            text = render(inputs[item_id])
        except Exception as exc:  # any error fails that item's run in Kiln too
            error = f"{type(exc).__name__}: {exc}"
            failures.append((item_id, error[:RENDER_ERROR_CHARS]))
            continue
        if sample is None or item_id == sample_id:
            sample = (item_id, text[:RENDER_SAMPLE_CHARS])
    return RenderReport(checked=len(inputs), failures=failures, sample=sample)
