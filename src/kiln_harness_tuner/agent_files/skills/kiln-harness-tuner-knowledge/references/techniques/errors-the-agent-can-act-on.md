---
title: Errors the agent can act on
description: A wrapper that turns a raw tool error into an error the agent can correct from - what was wrong, the valid values or nearest matches, and what to do next.
reviewed: 2026-11-13
status: active
levers:
  - new_tool
applies_to:
  - tools
cost: medium
---

# Errors the agent can act on

## When to use

- A tool returns raw errors: a status code, a stack trace, a body written
  for software. The traces show the agent retry the same call, guess, or
  give up after such an error.
- The agent ends with an answer that hides a failed call, or says that no
  data exists when the call failed.
- A tool reports an error as a success: a result with an error field that
  the agent does not read, or an empty result when the traces or the
  tool's contract show that empty means failure.

## When not to use

- **The errors are already clear**, and the agent corrects itself on the
  next call. A new wrapper changes nothing.
- **Code can fix the input with no doubt.** Then the wrapper fixes it and
  no error is needed: [normalize inputs wrapper](normalize-inputs-wrapper.md).
- **The error is a defect outside the harness**: a broken tool, stale
  data, a quota, a server that is down. A better message does not fix it.
  Report it as a defect, and do not tune around it.

## Alternatives

- [Normalize inputs wrapper](normalize-inputs-wrapper.md), when most
  errors come from input forms that code can map.
- [Guard tool calls at the boundary](guard-tool-calls-at-the-boundary.md),
  when the call must be refused before it reaches the tool, with an error
  that says why.

## How

The change is a wrapper of the tool on a new run config, with the wrapped
tool dropped. See the lever [new_tool](../levers/new_tool.md) for the
build. The template `actionable_errors.py` of the code-tool authoring
skill has this shape, and its rules say that errors are data, not
exceptions.

1. **List the errors** from a few traces: the call, the raw error, and
   what the agent did next.
2. **Write the error for the agent.** For each class of error, return:
   - what was wrong, in the words of the parameter ("`status` must be one
     of ...");
   - the valid values, or the nearest matches of a value that does not
     exist;
   - what to do next ("wait and call again", "call `list_orders` first").
3. **Return an error as data**, in the result, with a field that marks it
   as an error. A raised exception reaches the agent as text that it
   cannot use.
4. **Mark a hidden failure as a failure.** When the tool returns an error
   field inside a success, the wrapper says so in plain words. An empty
   result is a failure only when the traces or the tool's contract show
   that empty means failure (for example, a status field, or a parameter
   that the tool ignores). Otherwise an empty result is often the correct
   answer ("no orders match"): return it with the filters that produced
   it.
5. **Keep the successful result** as the wrapped tool returns it, unless
   another change trims it.
6. **Handle a transient error** (a timeout, a rate limit) that a trace shows.
   Before you add a retry, know how the wrapped tool works and what a
   retry can do. A write that timed out can already be applied, so a
   retry writes twice. The nested calls of one call share one timeout
   (`timeout_seconds`), so a retry with a wait can use all of it. No one
   answer is correct for every tool. The options include:
   - retry reads only;
   - retry a fixed number of times, with a wait that fits the timeout;
   - return the error, and let the agent decide what to do.

   Choose the option for this tool, and say why in `build_notes`. No
   template retries yet: when you choose a retry, add it to the copy of
   the template.

## Check that it took effect

Dry-run the wrapper with a call that fails (a value that does not exist,
a read only) and read the error. It says what was wrong and what to do.
Then smoke two or three train items whose traces had raw errors, and
count the retries and the calls that fail again in the same way.

## Budget

Medium cost: a wrapper, a dry run for each class of error, and a
measurement on its target evals. When it fixes its target, the val gate
measures every eval before a promotion, the evals that the tool touches
too. A good step on any budget when the traces show retries after raw
errors.

## If it does not move the evals

It depends. Keep it when the retries or the cost per item fall and no
eval goes down: a clear error saves a model turn each time. When the
retries do not fall, the agent did not read the error, or the errors were
not the cause.

## Priors

None yet.

## Confidence

Medium. It is field practice from public guidance on tool design. It
follows from what the agent reads: a tool error is the last text before
the next call, so it works as a prompt at that moment. Low, for the size
of the gain on a given agent: no measured case is recorded here yet.
