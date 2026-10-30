---
name: kiln-harness-tuner-probe
description: The Kiln Harness Tuner probe agent's procedure: answer ONE yes/no question about a mechanism (does this model call a tool under this output mode, read a skill file, parse json_instructions, honour a parameter) by forking an ephemeral run config, driving at most `max_drives` train items and reading the traces — no judge, no measurement, no experiment record; return ProbeResult. Load when dispatched as the Kiln Harness Tuner probe agent (template `probe.md` — input is a ProbeInput with the ProbeItem + baseline rc id).
metadata:
  version: "1.0"
---

# kiln-harness-tuner-probe

A probe is the cheap check the loop runs BEFORE it spends an arm on a
mechanism nobody has seen work on this task. Without a probe, an arm can
build a new tool, pay a full measurement on it, and only then show that the
subject model never calls it — a fact a few smoke drives can show. You answer
that kind of question for a few drives and a few cents, so the planner can build or
retire the idea instead of deferring it.

You are dispatched only for a question that needs READING — "did the
output change in the named way", "does the output still open with the
decision after the new block". Questions a count answers (did the model
call the tool, read the skill, reason more, emit valid JSON, produce
output) are `check` probes that code runs without you
(`kiln_harness_tuner.probes`); if your question is one of those, answer it the same way
code would — count in the traces — and say so in `notes`.

You answer **one** question. You do not measure quality, you do not judge,
you do not build the arm. Your output is `ProbeResult`:

- `answer`: `yes`, `no`, or `unclear` (the drives did not settle it — a real
  answer; say what would).
- `evidence`: the trace-level fact behind the answer, with counts: tool
  calls per drive, which tool, files read, parse failures, output
  differences from the parent. One paragraph.
- `drives`: how many drives you ran.
- `ephemeral_rc_ids`: every run config you created.
- `notes`: anything the planner should know that is not the answer (a Kiln
  refusal, a surprising side effect).

**Kiln is reached through your gateway operations only.** Your session's
Kiln tools are the typed operations named
`mcp__kiln_harness_tuner_gateway__<operation>` that your role needs; the
**`kiln-harness-tuner-gateway`** skill lists each one with its fields and an example.
A loop session has no generic Kiln API tool and no second lane: no HTTP from
Bash, no reading the project's files, no discovering routes. Every answer
holds train records only, and a refusal is final. There is no exception — if
an operation you need does not exist, say so in what you return instead of
going looking.

## Procedure

1. Read the input: `item.question`, `item.method`, `item.max_drives`, and
   the parent — `item.parent_rc_id`, else `baseline.rc_id`. Read that run
   config (`get_run_config`) so the fork is exact.
2. Build the ephemeral config the method describes — a clone of the parent
   with ONE thing changed (a tool attached, a skill attached, the output
   mode switched, a parameter set). Name it `PROBE <item id>: <what>` so
   nobody forks or measures it. Stamp provenance like a build would
   (`derived_from_ids`, notes = the question). Never edit the parent.
   If the question needs a throwaway tool or skill, create it with the same
   `PROBE …` prefix (the `kiln-harness-tuner-code-tool-authoring` skill for a tool; the
   skill installer for a skill).
3. Pick up to `max_drives` items from `train_inputs` in your input — code
   read them from the train split for you. They are the only items you may
   drive: the project files are not readable in your session, and the API
   refuses any split but train. Prefer items whose content exercises the
   mechanism (a tool-use question wants inputs where the tool would matter).
   An empty `train_inputs` is an `unclear` answer that says so.
4. Drive each through the ephemeral config with `run_task`
   (`run_config_id` = the ephemeral config, `item_id` = the train input's id,
   tag `probe`) — the gateway takes the input from the item itself. The run config's model
   must be one the run's model policy allows — a refused model is blocked at
   the call. Keep the parent's model, provider, thinking level and sampling:
   a probe answers a mechanism question, and a variant of those (which
   model, which level, which temperature) is a candidate that code screens
   against the real evals, never a probe. A probe answers no speed or cost
   question either: nothing times your fork, and you do not time drives
   yourself (speed is measured by the loop's measurement engine, paired
   with the baseline). Read the trace: `run_task` answers with a
   `trace_summary` (turns, tool calls, tool errors) and a `trace_path`, and
   the trace is that file, not part of the answer
   (`kiln-harness-tuner-gateway`, "Traces are files"). `Grep` the file for the tool's
   name (`### tool call <name>`), a skill file's name or `TOOL_ERROR` to
   count, and `Read` the lines around a hit for the arguments, the tool
   result and the final output. When the question is about a parameter or
   a mode, drive the PARENT on the same items too and compare.
5. Answer. Two of three drives with a tool call is `yes`; zero of three is
   `no`; one of three is `unclear` unless the question was "ever". Put the
   counts in `evidence`. Return the fenced JSON envelope.

## Hard rules

- No eval jobs, no judges, no val or test data. Your gateway operations
  answer from train records only, and the file fence refuses the project
  tree.
- Never set the task's default run config; never modify the parent, any
  eval, split, issue or experiment record.
- Never read the `baseline`-tagged memory record — the rc id is in your
  input. Do not write memory: code records your answer as a `probe`-tagged
  lesson. Discovery writes (`lesson`/`dead_end`) only for a platform fact
  you proved on the way (a Kiln refusal of a config), and only after
  listing the existing ones (`memory` skill).
- Stay under `max_drives`. If one drive already proves the answer (a Kiln
  422 on the config, a tool called on the first drive when the question is
  "ever"), stop and say so.
- Keep it short: this session should cost cents and minutes. A probe that
  needs an hour is an experiment; return `unclear` with what it would take.

## Related

`kiln-harness-tuner-gateway` (every Kiln operation your role has) ·
`kiln-harness-tuner-batch-selection` (plans probes and reads their answers) ·
`kiln-harness-tuner-experiment-build` (the full build, with the same provenance rules) ·
`kiln-harness-tuner-code-tool-authoring` (when a throwaway tool is the thing to probe) ·
`memory` (discovery-write conventions).
