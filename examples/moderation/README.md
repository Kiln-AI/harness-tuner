# Forum Moderation: the small example

A Kiln task to run the loop on. The task moderates posts on Green Thumbs, an
invented forum about home gardening. One input is one post, and the output
is a decision:

```
{"action": "remove", "rule": "spam"}
```

The task has no tools, no simulated user and no state. Every post is invented.

## The task

**The input** is the text of one post.

**The output** is `action` (`keep` or `remove`) and `rule` (`none`, `spam`,
`harassment`, `personal_info` or `off_topic`). A kept post has the rule
`none`.

**The baseline** is the run config `Baseline`: a short prompt on DeepSeek
V4.1 Flash 1017 (`deepseek_4_1_flash_1017`) through Fireworks. The prompt
names the four rules in one line each. It does not give the house rules below,
so the baseline removes some good posts and keeps many bad ones. Those
mistakes are what the loop can find on train and fix.

**The eval** is `Decision correct`, a code eval. It costs nothing to score.
It has two scores, and both are pass/fail:

| Score | Passes when |
|---|---|
| `decision_correct` | the action matches the label, and a removal names the labelled rule |
| `no_wrongful_removal` | a post labelled `keep` was not removed |

The second score is a guard. A moderator that removes everything scores 50% at
most on the first and fails the second on every good post.
[`scorer.py`](scorer.py) is the whole eval.

## The house rules

The labels follow these house rules. They are stricter than common sense, so
a model cannot guess them. The baseline prompt does not contain them.

| Rule | Remove | Keep |
|---|---|---|
| `spam` | Adverts and selling, also a plant sale for a charity, and a swap that asks for money "just for postage". Any link to a shop, a product page or an affiliate site, also in an honest recommendation. Referral links and discount codes. Asking people to follow, like, subscribe to or visit any account, channel, blog or newsletter, also a non-commercial one. | Free swaps and giveaways of seeds, cuttings or plants, with nothing asked in return. A product or shop named as a recommendation, with no link and no code. A free community event. |
| `harassment` | Insults, name-calling and threats aimed at a person. Mockery of a member, also when it is polite on the surface or done only with emoji. Telling a member to quit gardening or to leave the forum. | Blunt disagreement with an idea, a method or a product. Swearing at pests, plants or the weather. Jokes about yourself. |
| `personal_info` | The address, phone number or email of another private person. The full name of another private person, together with a complaint or an accusation. Your own phone number, email or street address: members must use private messages for contact. | Your own region, town or growing zone. The public address or phone number of a business. First names and forum usernames. |
| `off_topic` | Posts about something that is not gardening, also when they mention the garden first. Lawn care and turf (grass seed, mowing, lawn weed-and-feed): the forum has no lawn section. Hardscaping: patios, decking, paving, fences, sheds. Growing cannabis. | Topics next to gardening: cooking or preserving food from your own garden, chickens, bees, pets in the garden, houseplants, garden tools, weather as it affects the garden. |

A removed post names exactly one rule. When a post breaks more than one rule,
it names the first one in this order: `personal_info`, `harassment`, `spam`,
`off_topic`.

## The corpus

[`posts.jsonl`](posts.jsonl) holds 624 posts. Each line has the `id` of its
eval input, the post `text`, its label (`action` and `rule`), its `split`, and
the `group` and `subtopic` it was generated under.

| Split | Posts | Keep | Remove |
|---|---|---|---|
| train | 252 | 99 | 153 |
| val | 217 | 84 | 133 |
| test | 155 | 60 | 95 |

There are 16 groups. Some are posts that any moderator would decide the same
way. Most test one house rule, a post that breaks two rules, or a post that
looks like a rule break but stays. Each subtopic is split 40% train, 35% val
and 25% test, so the three splits have the same mix.

The split is a tag on each eval input: `train`, `val` or `test`. The loop
measures on train, promotes on val, and never reads test.

### How the posts were made

The corpus was made with Kiln's synthetic data generation. Every call used the
task's own model, DeepSeek V4.1 Flash 1017. The steps were:

1. `generate_categories` writes subtopics for each group. Each group is a root
   of the topic tree and has one label, so each post has the label of its
   group.
2. `generate_inputs` writes posts under each subtopic.
3. `generate_sample` runs the task on each post, with the full house rules as
   guidance. A post whose answer disagrees with its label is dropped. This
   check dropped 4 of 628 posts.
4. Duplicates are dropped, and each subtopic is split.

Every person, shop, address, phone number, email and link in the posts is
invented.

## Run the loop on it

You need the requirements in the [main README](../../README.md#requirements):
the loop installed, a running Kiln studio server, and a signed-in agent CLI.
You also need a Fireworks key in Kiln, because the baseline calls DeepSeek
V4.1 Flash 1017 through Fireworks.

1. Copy the project out of a clone of this repository. The example is not in
   the installed package. Kiln and the loop write runs into the project
   folder, and a copy keeps your clone clean.

   ```
   git clone https://github.com/Kiln-AI/harness-tuner.git
   cd harness-tuner
   mkdir -p ~/kiln-projects
   cp -R "examples/moderation/Forum Moderation" ~/kiln-projects/
   ```

2. Import the copy into Kiln. In the Kiln app, import an existing project and
   select `~/kiln-projects/Forum Moderation/project.kiln`. Or use the API:

   ```
   curl -X POST "http://localhost:8757/api/import_project?trusted=true&project_path=$HOME/kiln-projects/Forum%20Moderation/project.kiln"
   ```

   `trusted=true` is required because the eval runs code: the scorer.

3. Start the loop:

   ```
   harness-tuner --project-path ~/kiln-projects/"Forum Moderation" \
             --project-id 424242975950 --task-id 424242783061 \
             --guidance "Improve the moderation decisions. Keep the output format." \
             --timeout-mins 60 --budget-usd 20
   ```

   `--budget-usd` is a ceiling, not an estimate. It covers both the agent
   sessions and the model calls of the task. Read
   [docs/costs.md](../../docs/costs.md) to estimate a run.

   The loop shows its progress in the terminal. When the run ends, it prints
   the report's path. The report is in `~/.kiln_ai/harness_tuner/reports/` by
   default, not in the clone.

4. Check the result on test yourself. The loop never reads test. In Kiln, run
   the `Decision correct` eval on the test split for the baseline and for the
   run config the loop promoted, and compare the two.
