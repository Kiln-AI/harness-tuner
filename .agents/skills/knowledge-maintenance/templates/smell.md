---
title: {{TITLE}}
description: {{DESCRIPTION}}
reviewed: {{REVIEWED}}
# all, or a list of: single_turn, multi_turn, structured_output, free_text,
# classifier, tools, no_tools
applies_to: all
# code, agent, or both. With code, add `detectors:` with the id of each code
# detector of this smell, as the detector registry names it.
detected_by:
  - agent
---

# {{TITLE}}

## What it is

<!-- One paragraph. A smell is an issue in the design of the agent: it is true whether an eval passes or fails. A reason why one eval item failed is a failure pattern, which is a playbook. -->

{{TEXT}}

## How to recognise it

<!-- The signs in the harness (prompt, skills, tools, model, settings), and the signs in how the agent runs (the traces). -->

{{TEXT}}

## How to confirm it

<!-- What to check before it is reported, and what looks like this smell and is not. -->

{{TEXT}}

## What it blocks

<!-- What is not worth an experiment until this smell is fixed. Or the line "Nothing." -->

Nothing.

## Remedies

<!-- A list, with at least one item. Each item links to a playbook or a technique and states its condition. This section holds links, not the fix. -->

- [{{PLAYBOOK_TITLE}}](../playbooks/{{PLAYBOOK_ID}}.md), when {{TEXT}}
