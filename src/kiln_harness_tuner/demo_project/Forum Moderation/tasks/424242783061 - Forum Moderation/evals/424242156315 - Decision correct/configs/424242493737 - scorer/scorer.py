"""`decision_correct` — the moderation decision, scored by code.

This module is the code of a Kiln code eval, so it is self-contained:
standard library only, and one module-level `score(...)`.

Each post carries its label as reference data: `expected_action` (`keep` or
`remove`) and `expected_rule` (`none`, `spam`, `harassment`, `personal_info`
or `off_topic`).

Two scores, both pass/fail and higher is better:

    decision_correct     the action is right, and a removal names the right rule
    no_wrongful_removal  a post that should stay was not removed

The second score is the guard. A moderator that removes everything gets half
of `decision_correct` and fails `no_wrongful_removal` on every good post. An
answer that does not parse fails `decision_correct` and passes
`no_wrongful_removal`, because nothing was removed.
"""

import json


def _decision(output):
    try:
        answer = json.loads(output or "")
    except (TypeError, ValueError):
        return None, None
    if not isinstance(answer, dict):
        return None, None
    return answer.get("action"), answer.get("rule")


def score(output=None, trace=None, reference_data=None, task_input=None):
    reference = reference_data or {}
    expected_action = reference.get("expected_action")
    expected_rule = reference.get("expected_rule")
    action, rule = _decision(output)

    correct = action == expected_action and (action == "keep" or rule == expected_rule)
    wrongful_removal = expected_action == "keep" and action == "remove"
    return {
        "decision_correct": 1.0 if correct else 0.0,
        "no_wrongful_removal": 0.0 if wrongful_removal else 1.0,
    }
