# Objectives and models

Three flags set what a run improves and which models it may use:
`--objective`, and `--models` or `--model-policy`. [cli.md](cli.md) lists
every flag, with its default.

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
  By default the task keeps its model
  ([below](#which-models-a-run-may-use)). When no eval can
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
`--objective cost`. To choose the models the task may use, see
[below](#which-models-a-run-may-use).
[how-it-works.md](how-it-works.md#the-objectives) has the stop rules and the
promotion rules in full, and [architecture.md](architecture.md) section 16
has the method.

## Which models a run may use

You choose the models the task may use at start-up. The choice is enforced
in code: the planner sees only the allowed models, and the code refuses a
run config on any other model before it is built, measured or promoted.

| Choice | Flag | What the task may use |
|---|---|---|
| Keep the task's model | `--models keep` | The baseline's model and provider only (`model_name` and `model_provider_name`). The default under `quality`. |
| Any model Kiln offers | `--models any` | Every model on the providers that have a key in Kiln. The default under `speed` and `cost`. |
| Pick models | `--model-policy <file>` | The models you picked. The guided start writes the policy file for you. |
| A policy file | `--model-policy <file>` | The models the file allows. |

`--models` and `--model-policy` are alternatives: give one of them. With
neither, the objective sets the default.

Under every choice the planner may change all the other parts of the run
config. That includes the provider parameters: the thinking level, the
temperature and `top_p`. Guidance can still forbid a swap ("Do not change
the model"), but guidance cannot allow a model that the choice does not
allow.

**`--models any` can cost more.** The tuner may move the task to a more
expensive model. The cost per request can go up, and so can the run's
spend. Under `quality` the promotion gate checks the evals only, so a more
expensive model that scores better can become the new baseline. To keep
costly models out, pick the models, or use a policy file. To set the order
or a cost limit, say it in the guidance, for example "Fix it on the current
model first. Move to a bigger model only if it stays stuck, at no more than
twice the cost."

### Keep the task's model: `--models keep`

At start-up, once the baseline is known, the run makes a policy of the
baseline's one (provider, model) pair. It writes the policy to the run
folder, where the code that checks run configs reads it. So "keep the
model" is enforced by the same checks as a policy file. The model preflight
then tests the baseline's model only.

### Pick models, in the guided start

The guided start's Models question offers the four choices. **Pick
models** shows the models of the providers that have a key in Kiln:

- Type to filter the list. Tab and Shift+Tab show one provider at a time.
- Space marks or unmarks a model. The task's own model starts marked.
- Enter accepts. You can mark at most 12 models: the model preflight tests
  12 models.

The start writes the policy to the state folder's `policies/` folder, as
`picked-<date>-<time>.json`, with one exception for each model. The rerun
command passes it with `--model-policy`.

## A model policy file: `--model-policy`

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
- **A guided start can set it too**, at its Models question. The file is
  loaded at once, so a bad file shows the loader's message under the field.
  A policy set there gets the API-key check again, for the providers it
  names.

The policy covers the task's models. The agents that run the loop are set
apart, with `--agent-cli` and `--agent-model`, and are billed to the agent
CLI's own account.
