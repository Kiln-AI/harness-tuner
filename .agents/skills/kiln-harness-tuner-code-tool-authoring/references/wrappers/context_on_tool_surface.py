"""context_on_tool_surface — deliver context where the model reads it.

The measured finding behind this template: five instruction-text arms
told the model to address the user by name, and every one won on train
and lost at val, because the failing message was emitted BEFORE or WITH
the first tool call — the name did not exist in context yet. The one arm
that survived put the contract on the tool surface: the tool DESCRIPTION
is read at the pre-tool decision point, and a `directive` field in the
RESPONSE is read when the post-tool reply is composed.

tool_description (the half that matters — write it for that decision
point): "Call this FIRST, before any other tool or any reply. It returns
who you are talking to and the current time. Your first sentence must
address the user by the name it returns."

parameters_schema: {"type": "object", "properties": {}}

No nested calls; allowlist empty. `_context()` is a PLACEHOLDER: replace
it with the subject's real context source (a user lookup, a clock, a
config record) before you POST the tool. The values it returns are
markers, not data.
"""

from datetime import datetime, timezone


def _context():
    """REPLACE ME: the subject's real user/context lookup."""
    return {
        "id": "<REPLACE: user id>",
        "display_name": "<REPLACE: display name>",
        "role": "<REPLACE: role>",
    }


def run():
    user = _context()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "user": user,
        "now": now,
        "directive": (
            f"Address the user as {user['display_name']} in your first sentence, "
            "then answer. Do not narrate which tools you will call."
        ),
    }
