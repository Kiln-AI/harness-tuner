# Objectives and model policy

Two flags set what a run improves and which models it may use:
`--objective` and `--model-policy`. [cli.md](cli.md) lists every flag, with
its default.

## What to optimize: `--objective`

One objective per run. Every candidate is measured on train, and the
promotion gate decides on val.

| Objective | The goal | What it holds fixed | A candidate is promoted when, on val |
|---|---|---|---|
| `quality` (default) | Higher eval scores. | Every eval: no eval may go down beyond doubt. Latency and cost are reported, not held. | No eval goes down beyond doubt, and at least one eval it targets goes up beyond doubt. |
| `speed` | Lower latency per request. | Quality: equal or better. Cost is reported, not held. | It is clearly faster (a sign test on paired requests, a median ratio below 1, no significant p90 regression), and quality is equal or better. |
| `cost` | Lower provider cost per request. | Quality: equal or better. Latency is reported, not held. | It is clearly cheaper, by the same test, and quality is equal or better. |

"Quality is equal or better" means: no eval score down significantly, net
items over all evals (weighted by eval priority) at zero or above, and no
single eval down by more than max(1, round(0.10 × its items)) items.

- **`quality`** works on issues: one failure class of one eval. The planner
  builds a fix for an issue, such as a prompt change, a skill, a tool or a
  provider parameter, and the fix must show on the items the baseline fails.
  The task keeps its model, unless the guidance allows other models
  ([below](#which-objectives-may-change-the-model)). When no eval can
  measure an issue, the report names it, so that you can create the
  eval in the Kiln app. The loop builds that eval itself only with
  `--experimental-eval-building`, which is experimental and off by default.
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
against its parent, read from the stored runs at no extra cost. So when
latency is the goal, use `--objective speed`, and when cost is the goal, use
`--objective cost`. To keep costly models out, use `--model-policy`.
[how-it-works.md](how-it-works.md#the-objectives) has the stop rules and the
promotion rules in full, and [architecture.md](architecture.md) section 16
has the method.

### Which objectives may change the model

Under `quality` the task keeps the baseline's model and provider
(`model_name` and `model_provider_name`). The planner may change all the
other parts of the run config. That includes the provider parameters: the
thinking level, the temperature and `top_p`.

A model or provider swap is a lever only when one of these is true:

- The objective is `speed` or `cost`.
- The guidance allows other models explicitly, for example "You may try
  other models."

`--model-policy` does not enable a swap. It only limits the (provider, model)
pairs that a swap may use.

## Which models a run may use: `--model-policy`

A model policy limits the models that a model swap may use. Without a
policy, a run that may swap models
([above](#which-objectives-may-change-the-model)) may use any model Kiln
offers. A policy does not enable a swap: under `quality` the task keeps its
model, with or without a policy, unless the guidance allows other models.

The policy is a JSON file of `allow`, `deny` and `exceptions` rules over
Kiln's (provider, model) ids, with `*` wildcards.

### The file format

```
{
  "description": "free text, shown to the planner",
  "allow":      [{"provider": "<glob>", "model": "<glob>"}, ...],
  "deny":       [{"provider": "<glob>", "model": "<glob>", "reason": "..."}],
  "exceptions": [{"provider": "<exact>", "model": "<exact>", "reason": "..."}]
}
```

| Key | What it holds |
|---|---|
| `description` | Free text. The planner reads it. |
| `allow` | The rules that allow a pair. |
| `deny` | The rules that refuse a pair. |
| `exceptions` | Exact pairs, with no wildcards, that are allowed whatever the other rules say. |

Each rule has a `provider` and a `model`, and an optional `reason`. The
`provider` is Kiln's provider id (`model_provider_name`, such as
`fireworks_ai`). The `model` is Kiln's model id (`model_name`). Both are
case-sensitive `fnmatch` patterns: `*` matches any text, `?` matches one
character, and `[…]` matches one character of a set. A deny rule's reason is
part of the refusal message.

The first step that matches decides a pair:

1. An exception (one exact pair) allows it.
2. A `deny` match refuses it.
3. An `allow` match allows it.
4. Anything else is refused. A policy is an allowlist.

A file that the loader refuses stops the start, with the fix. It refuses: a
file that it cannot read, or that is not valid JSON, or not a JSON object; an
unknown key, at the top or in a rule; an `allow`, `deny` or `exceptions`
value that is not a list; a rule without a non-empty `provider` and `model`;
an exception with a wildcard; and a policy with no `allow` rule and no
exception, which allows nothing.

### The example policy

[`examples/model-policy.json`](../examples/model-policy.json):

```
{
  "description": "Direct providers only: Fireworks, Together, OpenAI, Anthropic, and Google (Gemini API or Vertex). Closed models from OpenAI, Anthropic and Google may also go through OpenRouter. Open-weight models (gpt-oss, llama, qwen, glm, kimi, deepseek, minimax, mistral and the like) never go through OpenRouter. Groq and Cerebras are not allowed.",
  "allow": [
    {"provider": "fireworks_ai", "model": "*"},
    {"provider": "together_ai", "model": "*"},
    {"provider": "openai", "model": "*"},
    {"provider": "anthropic", "model": "*"},
    {"provider": "gemini_api", "model": "*"},
    {"provider": "vertex", "model": "*"},
    {"provider": "openrouter", "model": "gpt_*"},
    {"provider": "openrouter", "model": "claude_*"},
    {"provider": "openrouter", "model": "gemini_*"}
  ],
  "deny": [
    {"provider": "openrouter", "model": "gpt_oss_*", "reason": "open-weight: never through OpenRouter (it matches gpt_* above)"},
    {"provider": "groq", "model": "*", "reason": "provider not allowed"},
    {"provider": "cerebras", "model": "*", "reason": "provider not allowed"}
  ],
  "exceptions": []
}
```

`openrouter`/`gpt_oss_120b` matches the `gpt_*` allow rule and the `gpt_oss_*`
deny rule. Deny comes before allow, so it is refused. A model of a provider
that no rule names, such as `ollama`, is refused, because nothing allows it.

### What the policy covers

- **Code enforces it**, not the agents: on every run config an agent creates
  or drives, before any run config is measured, and before a candidate is
  promoted. [architecture.md](architecture.md) section 17 lists each place.
- **The baseline is exempt.** It is your starting point. A baseline outside
  the policy is named once at start-up.
- **Judges are not covered.** An eval's judge model is part of the eval, and
  the loop does not change evals.
- **Kiln needs a key for each provider the policy names.** The start-up
  checks it for every provider that an `allow` or `exceptions` rule names
  exactly. A rule with a wildcard in its provider names none
  ([install.md](install.md) section 5).
- **Every allowed model is tested at start-up.** One short call goes to the
  baseline's model and to each allowed model the planner may pick (at most
  12). A model that fails is left out of the run. `--skip-model-preflight`
  skips this.
- **A guided start can set it too**, in its advanced options. The file is
  loaded at once, so a bad file shows the loader's message under the field.
  A policy set there gets the API-key check again, for the providers it
  names.

The policy covers the task's models. The agents that run the loop are set
apart, with `--agent-cli` and `--agent-model`, and are billed to the agent
CLI's own account.
