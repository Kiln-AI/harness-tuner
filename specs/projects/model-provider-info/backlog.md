---
status: complete
---

# Backlog: Model Info MCP

Open items found during this project. Phase 5 reviews each one with the
user, then closes or dismisses it.

## Open

None.

## Dismissed

- **Flagged models are listed as `ready`.** The model preflight flags
  models (`Ctx.flagged_models`). `list_models` and `get_model_details`
  still report a flagged model as `ready`, but the orchestrator refuses an
  arm on it, because it is not in `allowed_models`. A planner that trusts
  `ready` can waste an arm. A fix needs the flagged set in the gateway's
  state.

  Dismissed by the user. The model tools and the model preflight stay
  separate. The planner already gets `flagged_models` in its input. Do not
  write the flagged set to a file.
