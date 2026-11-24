"""The prompt text a run config sends, and the text of the skills attached
to it, read by code.

Two readers need it: the integrity checks at the promotion gate and the
generality check before the train measurement (`kiln_harness_tuner.generality`). One
reader, so they agree on what "the instructions" of a run config are.

A skill is attached as a `kiln_tool::skill::<id>` tool. Its text is read
from the project's `skills/<id> - <name>/` directory on disk: `SKILL.md`,
every file under `references/` and every text file under `assets/` — what
the subject model can read. The Kiln API serves only `SKILL.md`
(`/skills/{id}/content`), never the other files, so disk is the one
complete source. A skill that is not on disk, or whose `SKILL.md` or a
reference file is not UTF-8 text, makes the run config's skill text
unreadable (None), and each reader says what it then did without it. An
asset may be binary (the installer allows it): a binary asset is left out,
so no reader sees a change to one. An asset over `ASSET_MAX_BYTES` is left
out too.

A code tool is attached as a `kiln_tool::code::<id>` tool. Its
text is read from `code_tools/<id> - <name>/` on disk: every text file of its
directory (the code), and the description the model is shown — the function
name, the tool description and the parameters schema from `code_tool.kiln`.
A build can write the train set into a code tool as easily as into a prompt,
so the generality check reads it too.

A sub-agent is attached as a `kiln_task::<tool server id>` tool
(`kiln_harness_tuner.sub_agents`). Its text is two things. The subject model
reads the tool's name, description and parameters. The sub-agent's model
reads the prompt of the run config that the tool pins, the output schema of
its task, and the skills and code tools that run config attaches. A build
can write a train answer into a sub-agent's prompt as easily as into the
subject's, so every reader of this module reads the sub-agent too. The task,
its run config and the tool server come from the API. A prompt that Kiln
builds from the sub-agent task's stored runs is not read, and the
sub-agents' text is then unreadable (None).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.integrity import strings_in
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.skill_installer.bundle import installed_skill_dir
from kiln_harness_tuner.sub_agents import (
    SubAgent,
    read_sub_agent,
    sub_agent_tool_ids,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx


# The keys of a prompt that the model is sent — a frozen prompt's dict also
# carries its `name`, `description` and `generator_id`, which are not.
PROMPT_TEXT_KEYS = ("prompt", "chain_of_thought_instructions")


def prompt_strings(row: dict[str, Any]) -> list[str]:
    prompt = row.get("prompt")
    props = row.get("run_config_properties") or {}
    if isinstance(prompt, dict):
        texts = [str(prompt[k]) for k in PROMPT_TEXT_KEYS if prompt.get(k)]
    else:
        texts = list(strings_in(prompt)) if prompt is not None else []
    for key in ("prompt", "system_prompt", "instructions"):
        value = props.get(key) if isinstance(props, dict) else None
        texts += list(strings_in(value)) if value is not None else []
    return texts


async def rc_prompt_text(
    ctx: Ctx, row: dict[str, Any] | None, task_id: str | None = None
) -> str | None:
    """The prompt text a run config sends, or None when it cannot be read.
    `task_id`: the task the run config belongs to, when it is not the run's
    own task (a sub-agent's).

    Its frozen prompt when it carries one; else the prompt its `prompt_id`
    names, as the server renders it (`gen_prompt` renders every kind: a
    saved prompt `id::<id>`, a generator such as `simple_prompt_builder`, a
    frozen `task_run_config::…`); else the saved-prompt listing, whose ids
    are `id::<id>` (the lookup must compare them in that form, not with the
    bare id, or it matches nothing and the prompt is never read)."""
    if row is None:
        return None
    texts = prompt_strings(row)
    if texts:
        return "\n".join(texts)
    props = row.get("run_config_properties") or {}
    prompt_id = props.get("prompt_id") if isinstance(props, dict) else None
    if not isinstance(prompt_id, str) or not prompt_id:
        # No frozen prompt and no prompt id: no instructions of its own to
        # read (Kiln requires a prompt id, so only a hand-made row is this).
        return ""
    base = (
        f"/api/projects/{ctx.target.project_id}/tasks/{task_id or ctx.target.task_id}"
    )
    try:
        rendered = await ctx.kiln.call("GET", f"{base}/gen_prompt/{prompt_id}")
    except KilnError:
        rendered = None
    if isinstance(rendered, dict) and rendered.get("prompt"):
        return "\n".join(str(rendered[k]) for k in PROMPT_TEXT_KEYS if rendered.get(k))
    try:
        prompts = await ctx.kiln.call("GET", f"{base}/prompts") or {}
    except KilnError:
        prompts = {}
    rows = prompts.get("prompts") if isinstance(prompts, dict) else prompts
    wanted = {prompt_id, prompt_id.removeprefix("id::")}
    for saved in rows or []:
        if isinstance(saved, dict) and str(saved.get("id")) in wanted:
            found = [str(saved.get(k)) for k in PROMPT_TEXT_KEYS if saved.get(k)]
            if found:
                return "\n".join(found)
    return None


def normalized(text: str) -> str:
    return " ".join(text.split())


async def task_instructions(ctx: Ctx) -> list[str]:
    """The task's own instruction, thinking instruction and requirements —
    what a prompt may restate without copying anything. Empty when the task
    cannot be read."""
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    try:
        task = await ctx.kiln.call("GET", base) or {}
    except KilnError:
        return []
    if not isinstance(task, dict):
        return []
    texts: list[str] = []
    for key in ("instruction", "thinking_instruction", "requirements"):
        texts += list(strings_in(task.get(key)))
    return texts


SKILL_TOOL_PREFIX = "kiln_tool::skill::"
CODE_TOOL_PREFIX = "kiln_tool::code::"
CODE_TOOL_RECORD = "code_tool.kiln"
# The model-facing description of a code tool, as one pseudo-file.
CODE_TOOL_DESCRIPTION = "description"
SKILL_MD = "SKILL.md"
# The largest asset read as text (256 KiB). A larger asset is data, not
# instructions, and a diff of it would be slow (`difflib` is quadratic).
ASSET_MAX_BYTES = 256 * 1024


def _rc_tool_ids(row: dict[str, Any], prefix: str) -> tuple[str, ...]:
    props = row.get("run_config_properties") or {}
    tools_config = props.get("tools_config") if isinstance(props, dict) else None
    tools = tools_config.get("tools") if isinstance(tools_config, dict) else None
    return tuple(
        str(t).removeprefix(prefix) for t in tools or [] if str(t).startswith(prefix)
    )


def rc_tool_ids(row: dict[str, Any]) -> tuple[str, ...]:
    """Every tool id a run config row attaches — skills, code tools, OpenEnv
    and MCP tools — in its tool order."""
    return _rc_tool_ids(row, "")


def rc_skill_ids(row: dict[str, Any]) -> tuple[str, ...]:
    """The ids of the skills a run config row attaches, in its tool order."""
    return _rc_tool_ids(row, SKILL_TOOL_PREFIX)


def rc_code_tool_ids(row: dict[str, Any]) -> tuple[str, ...]:
    """The ids of the code tools a run config row attaches, in tool order."""
    return _rc_tool_ids(row, CODE_TOOL_PREFIX)


@dataclass(frozen=True)
class SkillText:
    """One installed skill's text: `files` maps "SKILL.md", each
    "references/..." path and each text "assets/..." path to its text.
    `derived_from`: the skill ids its provenance names (the first is the
    version it replaces)."""

    skill_id: str
    name: str
    files: dict[str, str]
    derived_from: tuple[str, ...] = ()


def _tree_files(directory: Path) -> list[Path]:
    """Every regular file under `directory`, sorted; none when it is absent."""
    if not directory.is_dir():
        return []
    return [
        p for p in sorted(directory.rglob("*")) if p.is_file() and not p.is_symlink()
    ]


def read_skill(project_path: str | Path, skill_id: str) -> SkillText | None:
    """The skill's text from `<project>/skills/`, or None when it is not
    there, its directory cannot be listed or read, or `SKILL.md` or a
    reference file is not UTF-8 text. Text assets are read too; a binary
    asset, or one over `ASSET_MAX_BYTES`, is left out."""
    skills_dir = Path(project_path).expanduser() / "skills"
    try:
        directory = installed_skill_dir(skills_dir, skill_id)
        if directory is None:
            return None
        files = {SKILL_MD: (directory / SKILL_MD).read_text(encoding="utf-8")}
        for path in _tree_files(directory / "references"):
            files[path.relative_to(directory).as_posix()] = path.read_text(
                encoding="utf-8"
            )
        for path in _tree_files(directory / "assets"):
            if path.stat().st_size > ASSET_MAX_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            files[path.relative_to(directory).as_posix()] = text
    except (OSError, UnicodeDecodeError):
        return None
    name = directory.name.partition(" - ")[2]
    derived: tuple[str, ...] = ()
    try:
        sidecar = json.loads((directory / "skill.kiln").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sidecar = None
    if isinstance(sidecar, dict):
        if isinstance(sidecar.get("name"), str):
            name = sidecar["name"]
        provenance = sidecar.get("provenance")
        if isinstance(provenance, dict):
            ids = provenance.get("derived_from_ids")
        else:
            ids = None
        derived = tuple(str(i) for i in ids or [] if i)
    return SkillText(skill_id=skill_id, name=name, files=files, derived_from=derived)


@dataclass(frozen=True)
class CodeToolText:
    """One code tool's text: `files` maps each text file of its directory
    (its code) and `CODE_TOOL_DESCRIPTION` (what the model is shown) to its
    text. `derived_from`: the code tool ids its provenance names.
    `function_name`: the name the model calls it by
    (`kiln_harness_tuner.reference_check`). `allowlist`: the tool ids its
    code can call (`tool_allowlist`)."""

    tool_id: str
    name: str
    files: dict[str, str]
    derived_from: tuple[str, ...] = ()
    function_name: str = ""
    allowlist: tuple[str, ...] = ()


def _derived_from(record: Any) -> tuple[str, ...]:
    provenance = record.get("provenance") if isinstance(record, dict) else None
    ids = provenance.get("derived_from_ids") if isinstance(provenance, dict) else None
    return tuple(str(i) for i in ids or [] if i)


def read_code_tool(project_path: str | Path, tool_id: str) -> CodeToolText | None:
    """The code tool's text from `<project>/code_tools/`, or None when it is
    not there, or its record or a text file cannot be read. A binary file,
    or one over `ASSET_MAX_BYTES`, is left out."""
    tools_dir = Path(project_path).expanduser() / "code_tools"
    try:
        matches = (
            [
                d
                for d in tools_dir.iterdir()
                if d.is_dir() and d.name.split(" - ")[0] == tool_id
            ]
            if tools_dir.is_dir()
            else []
        )
        if len(matches) != 1:
            return None
        (directory,) = matches
        record = json.loads((directory / CODE_TOOL_RECORD).read_text(encoding="utf-8"))
        files: dict[str, str] = {}
        for path in _tree_files(directory):
            if path.name == CODE_TOOL_RECORD or path.stat().st_size > ASSET_MAX_BYTES:
                continue
            try:
                files[path.relative_to(directory).as_posix()] = path.read_text(
                    encoding="utf-8"
                )
            except UnicodeDecodeError:
                continue
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    description = {
        k: record.get(k)
        for k in ("tool_function_name", "tool_description", "parameters_schema")
        if record.get(k) is not None
    }
    files[CODE_TOOL_DESCRIPTION] = json.dumps(description, indent=2, ensure_ascii=False)
    name = record.get("name")
    function_name = record.get("tool_function_name")
    return CodeToolText(
        tool_id=tool_id,
        name=name if isinstance(name, str) else directory.name.partition(" - ")[2],
        files=files,
        derived_from=_derived_from(record),
        function_name=function_name if isinstance(function_name, str) else "",
        allowlist=tuple(
            str(t) for t in record.get("tool_allowlist") or [] if isinstance(t, str)
        ),
    )


@dataclass(frozen=True)
class SubAgentText:
    """One attached sub-agent's text. `tool_id`: its `kiln_task::` tool id.
    `name`: the tool name the subject model calls. `files` maps
    `SUB_AGENT_DESCRIPTION` (what the subject model is shown),
    `SUB_AGENT_PROMPT` (what the sub-agent's model is told),
    `SUB_AGENT_OUTPUT_SCHEMA` when its task has one, and each file of each
    skill and code tool its run config attaches, to its text."""

    tool_id: str
    name: str
    files: dict[str, str]
    derived_from: tuple[str, ...] = ()


SUB_AGENT_DESCRIPTION = "description"
SUB_AGENT_PROMPT = "prompt"
SUB_AGENT_OUTPUT_SCHEMA = "output schema"


def _schema(value: Any) -> Any:
    """A task's schema as data: Kiln stores it as JSON text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


async def read_sub_agent_text(ctx: Ctx, sub: SubAgent) -> SubAgentText | None:
    """The sub-agent's text, or None when a part cannot be read: its run
    config is missing, its prompt is built from stored runs or does not
    render, or a skill or code tool of its run config is not readable."""
    if sub.run_config is None or sub.prompt_reads_runs:
        return None
    prompt = await rc_prompt_text(ctx, sub.run_config, task_id=sub.task_id)
    if prompt is None:
        return None
    thinking = sub.task.get("thinking_instruction")
    if isinstance(thinking, str) and thinking.strip() and thinking not in prompt:
        prompt = f"{prompt}\n{thinking}"
    description = {
        "tool_name": sub.tool_name,
        "tool_description": sub.tool_description,
        "parameters": _schema(sub.task.get("input_json_schema")),
    }
    files = {
        SUB_AGENT_DESCRIPTION: json.dumps(description, indent=2, ensure_ascii=False),
        SUB_AGENT_PROMPT: prompt,
    }
    output_schema = _schema(sub.task.get("output_json_schema"))
    if output_schema is not None:
        files[SUB_AGENT_OUTPUT_SCHEMA] = json.dumps(
            output_schema, indent=2, ensure_ascii=False
        )
    for skill_id in rc_skill_ids(sub.run_config):
        skill = read_skill(ctx.target.project_path, skill_id)
        if skill is None:
            return None
        for path, text in skill.files.items():
            files[f"skill {skill.name}, {path}"] = text
    for tool_id in rc_code_tool_ids(sub.run_config):
        tool = read_code_tool(ctx.target.project_path, tool_id)
        if tool is None:
            return None
        for path, text in tool.files.items():
            files[f"code tool {tool.name}, {path}"] = text
    return SubAgentText(
        tool_id=sub.tool_id, name=sub.tool_name or sub.server_id, files=files
    )


@dataclass(frozen=True)
class RcText:
    """Everything a run config gives the subject model to read. `prompt`:
    None when it cannot be read. `skills`: the attached skills by id, None
    when one of them cannot be read (or the run config row is missing).
    `code_tools`: the same for the attached code tools.
    `sub_agents`: the same for the attached sub-agents, by tool id. A
    sub-agent's text is also what its own model is told.
    `tool_ids`: every tool id the run config attaches, in its tool order;
    `found`: False when its row is missing."""

    prompt: str | None
    skill_ids: tuple[str, ...]
    skills: dict[str, SkillText] | None
    code_tool_ids: tuple[str, ...] = ()
    code_tools: dict[str, CodeToolText] | None = None
    tool_ids: tuple[str, ...] = ()
    found: bool = True
    sub_agent_ids: tuple[str, ...] = ()
    sub_agents: dict[str, SubAgentText] | None = None


async def rc_texts(
    ctx: Ctx, rc_ids: list[str], *, listing: list[dict[str, Any]] | None = None
) -> dict[str, RcText]:
    """rc id -> its prompt and skill text, over one listing of the task's
    run configs. A listing that fails, or a missing row, reads as nothing.
    `listing`: the task's run configs, when the caller has read them
    already."""
    from kiln_harness_tuner.probes import task_run_configs

    unread = RcText(prompt=None, skill_ids=(), skills=None, found=False)
    tool_cache: dict[str, CodeToolText | None] = {}
    try:
        if listing is None:
            listing = await task_run_configs(ctx)
        rows = {str(r.get("id")): r for r in listing}
    except KilnError:
        return dict.fromkeys(rc_ids, unread)
    cache: dict[str, SkillText | None] = {}
    sub_cache: dict[str, SubAgentText | None] = {}
    texts: dict[str, RcText] = {}
    for rc_id in rc_ids:
        row = rows.get(rc_id)
        if row is None:
            texts[rc_id] = unread
            continue
        skill_ids = rc_skill_ids(row)
        for skill_id in skill_ids:
            if skill_id not in cache:
                cache[skill_id] = read_skill(ctx.target.project_path, skill_id)
        read = {i: cache[i] for i in skill_ids}
        tool_ids = rc_code_tool_ids(row)
        for tool_id in tool_ids:
            if tool_id not in tool_cache:
                tool_cache[tool_id] = read_code_tool(ctx.target.project_path, tool_id)
        tools = {i: tool_cache[i] for i in tool_ids}
        sub_ids = tuple(
            dict.fromkeys(sub_agent_tool_ids(row.get("run_config_properties")))
        )
        for sub_id in sub_ids:
            if sub_id not in sub_cache:
                sub_cache[sub_id] = await _sub_agent_text(ctx, sub_id)
        subs = {i: sub_cache[i] for i in sub_ids}
        texts[rc_id] = RcText(
            prompt=await rc_prompt_text(ctx, row),
            skill_ids=skill_ids,
            skills=(
                None
                if any(v is None for v in read.values())
                else {i: v for i, v in read.items() if v is not None}
            ),
            code_tool_ids=tool_ids,
            code_tools=(
                None
                if any(v is None for v in tools.values())
                else {i: v for i, v in tools.items() if v is not None}
            ),
            tool_ids=rc_tool_ids(row),
            sub_agent_ids=sub_ids,
            sub_agents=(
                None
                if any(v is None for v in subs.values())
                else {i: v for i, v in subs.items() if v is not None}
            ),
        )
    return texts


def sub_agent_texts(rc: RcText) -> dict[str, SubAgentText] | None:
    """The run config's sub-agent texts: {} when it attaches no sub-agent,
    None when it attaches one whose text cannot be read."""
    return rc.sub_agents if rc.sub_agent_ids else {}


async def _sub_agent_text(ctx: Ctx, tool_id: str) -> SubAgentText | None:
    try:
        sub = await read_sub_agent(ctx.kiln.call, ctx.target.project_id, tool_id)
    except KilnError:
        return None
    return await read_sub_agent_text(ctx, sub) if sub is not None else None


def whole_texts(rc: RcText) -> dict[str, str]:
    """Where a run config gives the model text -> that whole text: "the
    prompt", "skill <name>, <file>", "code tool <name>, <file>" and
    "sub-agent <name>, <file>". The generality check and the judge-copy
    check name a place this way."""
    texts = {"the prompt": rc.prompt or ""}
    for kind, bundles in (
        ("skill", rc.skills),
        ("code tool", rc.code_tools),
        ("sub-agent", rc.sub_agents),
    ):
        for bundle in (bundles or {}).values():
            for path, text in bundle.files.items():
                texts[f"{kind} {bundle.name}, {path}"] = text
    return texts
