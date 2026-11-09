# Wrapper templates — bake the fix into the tool

Eight code-tool templates, one per mechanical failure class in the lever
ladder (`kiln-harness-tuner-batch-selection/references/lever-ladder.md`). Each is a
complete `run()` module you copy into a `POST /code_tools` payload after
three edits: rename `call_subject_api` to the wrapped tool's real
function name, match its real parameter schema (read it from the
clone-source GET, `get_code_tool`, for a code tool, or from
`get_tool_definition` for an MCP, built-in, Kiln task or RAG tool — never guess), and set
the routes and field names of the subject's API. Every template is exercised by
`tests/test_wrapper_templates.py` against a stub of `kiln.tools`
(success, error and raising-tool paths, and the input coercion), so the
plumbing shape is tested; the subject-specific edits are yours to dry-run
with `test_code_tool` (reads only, or a nonexistent id).

| Failure in the trace | Template | What it does for the model |
|---|---|---|
| Wrong case, wrong id form, display number instead of id, string where a number goes | `normalize_inputs.py` | Accepts every variant, maps it, and on a value it cannot map returns the valid values instead of the API's error |
| One call per page, or a truncated list the model never notices | `paginate_all.py` | Fetches every page up to a cap; returns `total`, `items`, `truncated`, `next_cursor` |
| One call per item, a loop that dies on the first error | `batch_with_errors.py` | Runs a list of operations; returns `done` and `errors` so partial success is usable |
| A list result floods the context | `preview_and_cap.py` | Returns `total`, the first N rows projected to the fields the task needs, and how to get more |
| Resolve-then-fetch idiom the model gets wrong | `composite_resolve_fetch.py` | One call: search, disambiguate, fetch |
| Raw 4xx bodies the model cannot act on | `actionable_errors.py` | Rewrites 400/404/405/422/429 into "what to do": valid values, nearest matches, "GET takes no body", "wait N s" |
| Instruction text is not read at the moment it matters (the model speaks before or with a tool call) | `context_on_tool_surface.py` | A context tool whose DESCRIPTION is read at the pre-tool decision point and whose RESPONSE carries a directive for the post-tool reply |
| A read scenario produces a spurious write | `readonly_guard.py` | A read-only variant of a read-write tool; rejects every non-GET with the name of the write tool |

## Rules that apply to all of them

- **Allowlist only subject-task tools.** Never `kiln_tool::call_kiln_api`.
- **The wrapper replaces, or sits beside, the wrapped tool on the NEW rc
  only.** Both ids on one rc means the model chooses, which is a different
  experiment; say which in `build_notes`.
- **The description carries half of the effect.** Say WHEN to prefer the wrapper
  over the tool it wraps, in the model's terms: "Prefer this whenever the
  user gave a shipment number instead of an id".
- **`{status_code, body}` envelope.** Nested results arrive parsed OR as a
  JSON string, and the body inside can be a string again — every template
  carries `_parse` for that reason. Keep it.
- **Errors are data, not exceptions.** Return `{"ok": false, "error": …}`
  with the guidance; a raised exception reaches the model as a traceback
  it cannot use. A crashed NESTED tool raises out of the bridge too (a dry
  run against a missing database shows it), so every template routes nested
  calls through `_call`, which turns a raise into `{"status_code": -1,
  "body": "<error>"}`. Keep it.
- **Every template declares its `parameters_schema`** in the docstring.
  The `run(...)` parameter names must match it exactly; numeric parameters
  are typed `["integer", "string"]` because the model sends both, and the
  template coerces.
- **Dry-run with reads.** `test_code_tool` executes nested calls for real.
