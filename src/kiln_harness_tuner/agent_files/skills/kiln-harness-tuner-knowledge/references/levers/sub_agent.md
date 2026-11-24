---
title: Sub-agents
description: A second Kiln task that the subject task calls as a tool - its own prompt, model, tools and output form - created with its tool and attached on a new run config.
reviewed: 2026-11-25
gateway_ops:
  - get_run_config
  - get_kiln_task_tool
  - list_available_tools
  - get_model_details
  - create_sub_agent
  - create_run_config
  - run_task
---

# Sub-agents

## What it is

A sub-agent is another Kiln task in the same project. A Kiln task tool
pins that task and one of its run configs. A run config of the subject
task attaches the tool as `kiln_task::<tool_server_id>`. The subject model
sees one tool: a name, a description and parameters. Each call is one new
run of the sub-agent's task, and the output of that run is the tool's
result.

The sub-agent has its own context, its own prompt, its own model and
thinking level, its own tools and skills, and its own output schema. The
subject model sees only what the sub-agent returns.

## How the tuner changes it

1. Read the parent: `get_run_config` gives its tools. `get_kiln_task_tool`
   shows a sub-agent that it already attaches.
2. Create the sub-agent with one call, `create_sub_agent`. It makes the
   task, one run config and the tool. Its fields:
   - `instruction`: the sub-agent's prompt.
   - `tool_name` and `tool_description`: what the subject model reads to
     decide when to call it.
   - `input_json_schema`: an object schema. Its properties are the tool's
     parameters. Without it, the tool has one string parameter, `input`.
   - `output_json_schema`: an object schema, the form of what comes back.
     Without it, the sub-agent returns plain text.
   - `run_config_properties`: the model, the provider, the output mode,
     and the tools and skills of the sub-agent, as for a run config.
     `get_model_details` gives a model's output mode and context window,
     and `list_available_tools` gives the tools that it can attach.
   The answer gives `tool_id`.
3. Create a new run config of the subject task with `create_run_config`.
   Copy every property of the parent, and add `tool_id` to
   `tools_config.tools`. Drop on the same run config each tool that the
   sub-agent takes over.
4. Smoke it with `run_task` on a train item. The answer gives
   `sub_agent_calls`, and its `usage` includes the sub-agent's calls.

Rules of the object:

- **A call is one turn, with no memory.** The sub-agent sees the arguments
  of the call and nothing else: not the conversation, not an earlier call.
  The subject model writes each fact that the sub-agent needs into the
  arguments.
- **The tool pins its run config.** A new prompt, model or schema is a new
  sub-agent, attached in place of the old one on a new run config. Never
  change a sub-agent that a run config uses.
- **The subject model decides to call it.** Nothing calls a sub-agent by
  rule. The tool description and the subject's prompt say when.
- **The tool's name is snake_case**, starts with a letter and has at most
  64 characters. No other tool on the run config has the same name. The
  description has at most 128 characters.

## Limits

- The tuner creates a sub-agent and does not tune it on evals of its own.
  A sub-agent has no evals here. Its effect is measured on the subject's
  evals.
- A sub-agent created here cannot attach another sub-agent, or a tool that
  reaches the Kiln server or the project's memory.
- The model policy holds for the sub-agent. A sub-agent on a model outside
  the policy is refused, and so is a run config that attaches one. When
  the run keeps the baseline's model, a new sub-agent runs on that model,
  with any thinking level.
- No value from a train item goes into the sub-agent's prompt, its tool
  description or its schemas. Code searches each build for copies of train
  content and of eval text, in the sub-agent's text as in the subject's.
- Kiln's usage numbers of a subject run do not include the sub-agent's
  calls. The loop adds them on the drives it makes through the run
  endpoint, and it times each arm that adds or removes a sub-agent. The
  cost and the latency of stored eval results (`get_train_results`) leave
  them out. A drive through an eval job (a multi-turn task, an OpenEnv
  tool) keeps no record of them.
- Code checks that the lever was applied: a new sub-agent that no timed
  drive calls ends the arm as `lever not applied`.
- The gateway does not show the sub-agent's own trace. The subject's trace
  shows the arguments of the call and the returned text.
- Two calls in one turn run at the same time. The loop sums their
  latencies, so the number can be above the wall-clock time.
- The sub-agent's task and its tool stay in the project when the arm is
  not promoted. The run archives a tool that no run config attaches.
- When the parent's output mode sends the schema as a tool
  (`function_calling`, `function_calling_weak`, or `default` outside Ollama
  and Docker Model Runner), Kiln refuses every call once a tool or a skill
  is attached. Set the mode on the same run config by the fallback of
  [model](model.md), step 4, and list it in `change`. The same rule holds
  for the sub-agent's own run config when it attaches a tool or a skill.

## See also

- The `kiln-harness-tuner-gateway` skill: the fields of `create_sub_agent`
  and the checks of `create_run_config`.
- The `kiln-harness-tuner-experiment-build` skill: the build discipline,
  the smoke, and the text that a build may not copy.
- The lever [tool_config](tool_config.md): how to attach and drop tools.
