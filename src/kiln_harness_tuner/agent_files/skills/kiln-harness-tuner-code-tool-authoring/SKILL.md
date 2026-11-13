---
name: kiln-harness-tuner-code-tool-authoring
description: Author sandboxed Python code tools for the SUBJECT task in a Kiln Harness Tuner experiment — new tools, and wrappers over the subject's existing tools (minify, preview, batch, compose, validate, guard) — with allowlist hygiene, dry-run testing, and new-rc wiring. Load when building a `new_tool` or `tool_config` experiment item. This is the Kiln Harness Tuner build agent's tool-authoring skill.
---

# kiln-harness-tuner-code-tool-authoring

You are building a code tool as an **experiment lever**: it ships wired into
ONE new run config, additive to the baseline, per the `kiln-harness-tuner-experiment-build`
discipline. The tool surface is the subject model's decision point — a
wrapper can reshape what the model sees (smaller responses, previews, batch
semantics, validated inputs) without touching the prompt or the model.

## When the tool lever is the right lever

Reach for a code tool when the failure is **mechanical** — the model calls
the wrong route, floods itself with rows, malforms a payload, loops one call
per item, or repeats an expensive fetch — because code can make the failing
move impossible or unnecessary. When the failure is **behavioral** (tone,
ordering, ignoring policy it was shown), prompt and skill levers are usually
cheaper and more direct. Tool + directive pairs are common: the tool makes
the right move possible, one description line makes it preferred. Put that
line in the tool's description, not in the prompt: text in a tool moves
with the tool, so a later change of the tool set needs no prompt change.

## What you can build

Four families:

- **Response-shaping wrappers** — call the subject tool, transform what
  comes back before the model sees it:
  - *projection / minifier*: return only the fields the task needs
    (token-diet);
  - *preview + cap*: materialize a big list, return `total` + first N + how
    to get the rest (flood control);
  - *error enrichment*: rewrite a raw error body into corrective guidance —
    e.g. a not-found that lists the nearest valid alternatives.
- **Call-pattern wrappers** — change how many calls a job takes:
  - *batch with per-item errors*: loop a list of specs, collect
    `{done, errors}` instead of raising, so partial success is usable;
  - *composite*: fuse a multi-step idiom into one call — resolve a
    display-number to its opaque id, then fetch the record;
  - *write-then-verify*: perform the write, read the record back, return
    both — making unverified-write failures structurally impossible.
- **Guardrail wrappers** — subtract capability instead of adding it: a
  read-only variant of a read-write tool (reject non-GET), a scope cap, a
  confirmation token required for destructive methods.
- **New pure-code tools** — no nested calls at all: date/window math,
  dedup/diff, table or report rendering, a static context tool that serves
  identity/time/config as a tool response instead of prompt text (context
  served on demand is a real lever — variants of a context tool are cheap
  experiments).

`tool_config` items overlap this space from the other side: attaching or
dropping existing tools on the new rc, no new code. A new description is
not `tool_config`. It is a new tool: a clone of a code tool with the new
`tool_description`, or a wrapper of any tool.

## Wrapper templates — bake it into code first

When the failure in the trace is MECHANICAL (wrong case, display number
for an id, a body on GET, one call per page or per item, a list that
floods the context, an error body the model cannot act on, a rule about a
tool that sits in the prompt, and the model breaks it), reach for a
template in [references/wrappers/README.md](references/wrappers/README.md)
BEFORE any prompt edit — the knowledge skill's playbook "change the
surface, not the words". Eight complete `run()` modules, one per failure
class, each tested by `tests/test_wrapper_templates.py` against a stub of
`kiln.tools`:

| Failure | Template |
|---|---|
| Wrong case / id form / number where an id goes | `normalize_inputs.py` — maps every variant, returns the valid values when it cannot |
| One call per page, truncated lists | `paginate_all.py` — follows cursors to the end or a cap |
| One call per item, stops at the first error | `batch_with_errors.py` — `done` + `errors` per item |
| A list floods the context | `preview_and_cap.py` — total + first N rows, projected |
| Resolve-then-fetch idiom | `composite_resolve_fetch.py` |
| Raw 4xx bodies | `actionable_errors.py` — valid values, nearest matches, "GET takes no body", "wait N s" |
| A rule about a call or its reply sits in the prompt, and the model breaks it | `context_on_tool_surface.py` — the rule in the description, the tool's own status in a response `notes` field |
| Spurious writes on a read scenario | `readonly_guard.py` |

Three edits turn a template into the subject's tool: rename
`call_subject_api` to the wrapped tool's real function name, match its
real parameter schema, set the subject's routes and field names. Then the
build discipline below applies unchanged (fresh function name, dry-run with
reads, wire into the new rc, say in `build_notes` whether it replaces or sits
beside the wrapped tool).

## The one hard Kiln Harness Tuner rule: allowlist hygiene

**A subject-task tool may allowlist ONLY subject-task tools.** Never put
`kiln_tool::call_kiln_api` (or any tool that can reach the studio server)
into a subject tool's `tool_allowlist` — that hands the subject model a live
channel into the Kiln API (eval definitions, memories, run configs). Advice
to "route HTTP through `tools.call_kiln_api`" is for tools built *for the
Kiln assistant*; for a subject tool it is exactly
backwards. The wrap targets are the subject-task tools already on the parent
rc, of any kind a run config attaches except skills: code tools, MCP tools
and built-in tools alike. The rule above still applies to each of them (the
Contract below lists what Kiln accepts). Nested code-tool → code-tool calls
are supported, depth cap 10.

## Prerequisites

`trust_code_tools` (the gateway operation) — in-memory per server process,
so grant it once per session before authoring. ⚠️ Untrusted
`create_code_tool` / `test_code_tool` answer with **`not_trusted: true`**
and persist or run nothing — check the field.

## Contract

- `code` (≤64KB) defines module-level `run(...)` (sync or async); its
  parameter names must match `parameters_schema` (JSON Schema, root
  `type: object`) — the schema is what the subject model fills in.
- Return `str` (passthrough) or JSON-serializable dict/list/number/bool/None
  (auto-dumped).
- **Nested tool calls**: `from kiln import tools` (or `async_tools`) →
  `tools.<function_name>(**kwargs)` — kwargs must match the *wrapped*
  tool's own parameter schema. Read it from the clone-source GET
  (`get_code_tool`, its `parameters_schema`) for a code tool, or from
  `get_tool_definition` (`definition.parameters`) for an MCP, built-in,
  Kiln task or RAG tool; do not guess its signature. Every callee must be in
  `tool_allowlist`:
  - Kiln accepts code-tool, MCP (remote and local), built-in, Kiln task,
    RAG, memory and world tool ids (a world tool resolves only inside an
    episode of its world). The allowlist hygiene rule above still applies
    to every kind: never `kiln_tool::call_kiln_api`, and never a
    `kiln_tool::memory::…` tool, which reads and writes the project's
    memory store;
  - Kiln rejects skill ids, `kiln_unmanaged::` ids, a duplicate id, and the
    tool's own id;
  - function names must be unique in one allowlist, except among MCP tools:
    a call to a name that two MCP tools share fails as ambiguous;
  - depth cap 10; process concurrency cap 8.
- Nested results may arrive parsed OR as a JSON string — parse defensively,
  and note the *body inside* a `{status_code, body}` envelope may itself be
  a JSON string again.
- `timeout_seconds` defaults to 60 — size it up for batch/composite tools
  (N nested calls share one budget).
- Design `tool_description` for the subject model, not for humans: inputs,
  outputs, and WHEN to prefer this tool over the tool it wraps.

## Operations

Your gateway operations (the `kiln-harness-tuner-gateway` skill has their fields):

| Step | Operation |
|---|---|
| Dry-run (nothing persisted) | `test_code_tool` — create fields + `params` (the invocation args) → `{result, error, traceback, not_trusted, tool_call_log, …}` |
| Create | `create_code_tool` — `{name, tool_function_name, tool_description, parameters_schema, code, timeout_seconds, tool_allowlist}` |
| Read an existing tool (clone source) | `get_code_tool` |
| Read a non-code tool's schema (wrap target) | `get_tool_definition` — `definition`: `function_name`, `description`, `parameters` |
| Wire into the new rc | add `kiln_tool::code::<new_ctid>` to the new run config's `tools_config.tools` |

## Build discipline (the Kiln Harness Tuner deltas)

1. **Additive only — never archive or PATCH a tool another rc references.**
   Functional fields are immutable post-create; "create new + archive old"
   versioning is for a solo operator. In Kiln Harness Tuner the old tool
   stays live (the baseline uses it); you create a NEW tool and
   wire it into YOUR rc only. If it *replaces* a tool, drop the old id from
   your rc's tool list — both ids on one rc means the model chooses, which
   is a different experiment.
2. **Fresh `tool_function_name`.** Duplicate non-archived function names are
   rejected (400), so a variant needs a new name — and the name + description
   are what the model decides by, so make the variant's "when to use me"
   explicit.
3. **Clone-and-modify**: GET the existing code_tool, take its `code` /
   `parameters_schema` / allowlist as the base, change only what the
   hypothesis names, POST as new. Stamp the rc's provenance as usual.
4. **Dry-run first, reads only.** `test_code_tool` executes nested calls FOR
   REAL, and outside an eval episode a subject-domain tool hits its shared
   fallback state — so dry-run with harmless GETs or a nonexistent id
   (exercises plumbing + error path, zero side effects). Never dry-run a
   write. Read `tool_call_log` in the response — it shows each nested call's
   arguments and output preview, which is where signature mismatches with
   the wrapped tool surface.
5. **A tool nobody is told about goes unused.** If the hypothesis needs the
   model to *prefer* the new tool, the usage directive (description text,
   in preference to a prompt line) is part of the same lever — build it in
   the same rc and say so in `build_notes`; do not silently widen the
   hypothesis.

## Worked example — a composite wrapper, complete

The shape: users hand the model display numbers; the API's path segments
need opaque ids; the baseline model guesses. The composite makes the
resolve-then-fetch idiom one call. `call_subject_api` stands for whatever
the parent rc's domain API tool is named; match its real signature and
routes, taken from its own definition. `<subject_api_tool_id>` is that
tool's id on the parent rc, of any kind (`kiln_tool::code::…`,
`mcp::local|remote::…`), but never `kiln_tool::call_kiln_api` (the rule
above).

Create payload:

```json
{
  "name": "Find record by display number",
  "tool_function_name": "find_record",
  "tool_description": "Resolve a display number, name, or other human-facing handle to its record: searches, disambiguates, and fetches in one call. Prefer this over call_subject_api whenever the user gave anything other than an opaque id.",
  "parameters_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "the handle exactly as the user gave it"}
    },
    "required": ["query"]
  },
  "timeout_seconds": 60,
  "tool_allowlist": ["<subject_api_tool_id>"],
  "code": "<the module below as a string>"
}
```

```python
import json


def _parse(r):
    # Nested results arrive parsed OR as a JSON string.
    if isinstance(r, str):
        try:
            return json.loads(r)
        except Exception:
            return {"status_code": -1, "body": r}
    return r


def run(query):
    from kiln import tools

    listing = _parse(tools.call_subject_api(
        method="GET", url_path="/api/v1/records",
        query_params={"q": query}))
    if listing.get("status_code") != 200:
        return {"found": False, "error": listing}
    body = _parse(listing.get("body"))  # the envelope's body can be a string too
    matches = body.get("results") or []
    if not matches:
        return {"found": False,
                "hint": f"no record matches {query!r}; try a shorter fragment"}
    if len(matches) > 1:
        return {"found": False, "ambiguous": [
            {"id": m.get("id"), "display": m.get("display_number"),
             "name": m.get("name")} for m in matches]}
    record = _parse(tools.call_subject_api(
        method="GET", url_path=f"/api/v1/records/{matches[0]['id']}"))
    return {"found": True, "record": record}
```

Dry-run with `params: {"query": "no-such-record-zzz"}` — full plumbing, both
branches reachable, zero side effects — then create, wire
`kiln_tool::code::<new_ctid>` into the new rc, and smoke per
`kiln-harness-tuner-experiment-build`.

## Related

`kiln-harness-tuner-experiment-build` (the build discipline this slots into — provenance,
smoke, one-crack) · `kiln-harness-tuner-gateway` (every operation above) ·
`kiln-task-setup` (rc composition + known bugs).
