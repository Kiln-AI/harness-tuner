# Objectives and model policy

Two flags set what a run improves and which models it may use:
`--objective` and `--model-policy`. [your-own-task.md](your-own-task.md)
lists every other flag.

## What to optimize: `--objective`

One objective per run. Every candidate is measured on train, and the
promotion gate decides on val.

| Objective | The goal | A candidate is promoted when, on val |
|---|---|---|
| `quality` (default) | Higher eval scores. | No eval goes down beyond doubt, and at least one eval it targets goes up beyond doubt. |
| `speed` | Lower latency per request, with quality held. | It is clearly faster (a sign test on paired requests, a median ratio below 1, no significant p90 regression), and quality is equal or better. |
| `cost` | Lower provider cost per request, with quality held. | It is clearly cheaper, by the same test, and quality is equal or better. |

"Quality is equal or better" means: no eval score down significantly, net
items over all evals at zero or above, and no single eval down by more than a
few items.

- **`quality`** works on issues: one failure class of one eval. The planner
  builds a fix for an issue, such as a prompt change, a skill, a tool or a
  model change, and the fix must show on the items the baseline fails. When no
  eval can measure an issue, the loop builds that eval first.
- **`speed`** measures the sum of the LLM call latencies of one task run. A
  simulated user's calls are not counted. Before wave 1 it tries each lower
  thinking level of the baseline's model. It judges the items the baseline
  already passes first, because the question is whether a faster candidate
  keeps them.
- **`cost`** measures the task run's own `usage.cost`, and otherwise works as
  `speed` does. A model with no price in Kiln reports no cost and counts as
  $0, so check that every model the planner may try has a price. The levers
  that keep quality are fewer tokens: fewer turns, a shorter prompt, fewer
  attached tools and skills. A cheaper model usually loses some quality.

Under every objective, each experiment also reports its latency and cost
against its parent, read from the stored runs at no extra cost.
[how-it-works.md](how-it-works.md#the-objectives) has the stop rules
and the promotion rules in full.

## Which models a run may use: `--model-policy`

By default the planner may move the task to any model Kiln offers. A model
policy limits that. It is a JSON file of `allow`, `deny` and `exceptions`
rules over Kiln's (provider, model) ids, with `*` wildcards:

```
{
  "description": "Direct providers only. No Groq or Cerebras.",
  "allow": [
    {"provider": "fireworks_ai", "model": "*"},
    {"provider": "anthropic", "model": "*"},
    {"provider": "openrouter", "model": "claude_*"}
  ],
  "deny": [
    {"provider": "groq", "model": "*", "reason": "provider not allowed"}
  ],
  "exceptions": []
}
```

An exception (one exact pair) allows a model. Otherwise a `deny` match refuses
it, an `allow` match allows it, and anything else is refused.
[`examples/model-policy.json`](../examples/model-policy.json) is a complete one.

- **Code enforces it**, not the agents: on every run config an agent creates
  or drives, before any run config is measured, and before a candidate is
  promoted.
- **The baseline is exempt.** It is your starting point. A baseline outside
  the policy is named once at start-up.
- **Judges are not covered.** An eval's judge model is part of the eval, and
  the loop does not change evals.
- **Kiln needs a key for each provider the policy names.** The start-up
  checks it for every provider an `allow` or `exceptions` rule names exactly
  ([install.md](install.md) section 5).
- **Every allowed model is tested at start-up.** One short call goes to the
  baseline's model and to each allowed model the planner may pick (at most
  12). A model that fails is left out of the run. `--skip-model-preflight`
  skips this.

The policy covers the task's models. The agents that run the loop are set
apart, with `--agent-cli` and `--agent-model`, and are billed to the agent
CLI's own account.
