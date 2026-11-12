---
status: complete
---

# Component: Knowledge documents, indexes and checks

## 1. Purpose and scope

This component is the knowledge system that loop agents read: the files, the
schema of each component type, the entry files, the generated indexes, and
the Python package that loads, checks and generates them.

Not in this component: which documents V1 contains (the V1 coverage phase),
the maintenance skill and the backlog
([maintenance_skill.md](./maintenance_skill.md)), and the code that detects
a smell ([smell_detection.md](./smell_detection.md)).

## 2. Files

```
src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-knowledge/
  SKILL.md
  references/
    planner.md            entry file: planner
    builder.md            entry file: builder
    smells.md             entry file: baseline reviewer
    analyzer.md           entry file: analyzer
    report.md             entry file: report writer
    technique-index.md    generated; for a technique the user names
    levers/<id>.md
    techniques/<id>.md
    techniques/<id>/      optional sub-files of that technique
    playbooks/<id>.md
    smells/<id>.md
```

- **The id** of a component is its file name without `.md`. It is lower
  case, with words joined by `-`, of at most 60 characters:
  `^[a-z0-9]+(-[a-z0-9]+)*$`.
- **The type** is the directory. A component does not state its type.
- **Sub-files.** A component of any type can have a directory beside it with
  its id as the name. Only Markdown files go there. The primary document
  links to each one and says when to read it.
- **`SKILL.md`** is required, as for every skill. It has the front matter
  that every skill has (`name`, `description`) and a short body: what the
  knowledge system is, the golden rule, and a table from role to entry
  file with one line on each. Its `name` equals the folder name, as an
  existing test requires. A role's prompt template names its entry file
  directly, so an agent does not depend on `SKILL.md` to find it.
## 3. Schemas

Each component starts with YAML front matter between `---` lines. Unknown
keys are an error. The body is Markdown with the required H2 sections of its
type, in the order given. Other H2 sections can follow the required ones.

### 3.1 Common front matter

| Key | Type | Rule |
|---|---|---|
| `title` | string | 3 to 60 characters, one line |
| `description` | string | 40 to 200 characters, one line. It is the text of the index row, so it must let an agent decide whether to open the file. |
| `reviewed` | date | `YYYY-MM-DD`. Not in the future. |

### 3.2 Task shapes

`applies_to` is the word `all`, or a list of one or more of:

| Shape | Meaning |
|---|---|
| `single_turn` | One request, one answer. Tool calls are allowed. |
| `multi_turn` | A conversation. |
| `structured_output` | The task has an output schema. |
| `free_text` | The task returns text. |
| `classifier` | The output is a small closed set of values. |
| `tools` | The agent has tools. |
| `no_tools` | The agent has no tools. |

A document applies to a task that has every shape in its list. `all` means
that shape does not matter. The list is the constant `TASK_SHAPES`.

### 3.3 Lever (`levers/`)

**The id of a lever document is the lever's name**, for example
`levers/prompt.md`. It is the short name that the planner writes in the
`technique` field of an experiment (`prompt`, `skill`, `tool_config`,
`new_tool`, `params`), so that the count in `lever_attempts` and the lever
documents use the same words. This is a convention in the documents. No
code constant lists the levers, and no test holds the code to the
documents: `FixWork.technique` stays a free string.

Front matter, in addition to the common keys:

| Key | Type | Rule |
|---|---|---|
| `gateway_ops` | list of strings | Each is an operation in `gateway.ops.OPS`. Can be empty. |

Required sections:

1. `## What it is`
2. `## How the tuner changes it` — the operations, the rules of the object,
   and the steps.
3. `## Limits`
4. `## See also` — links to the existing how-to material. Can say "None."

Maximum size: 6,000 characters.

A lever document has no "when to use" text. A check cannot prove that, so
the maintenance skill's audit covers it.

### 3.4 Technique (`techniques/`)

Front matter, in addition to the common keys:

| Key | Type | Rule |
|---|---|---|
| `status` | string | `active` or `dormant` |
| `levers` | list of strings | One or more ids of lever documents |
| `applies_to` | see 3.2 | |
| `cost` | string | `low`, `medium` or `high`: the cost to build and measure it, compared with a prompt edit |

Required sections:

1. `## When to use`
2. `## When not to use`
3. `## Alternatives` — each a link to another technique with its condition.
   Can say "None."
4. `## How` — the recipe. It links to each lever document it uses.
5. `## Check that it took effect` — a probe, or a count in a few traces.
6. `## Budget` — when to leave it out, and when it is a good first step.
7. `## If it does not move the evals` — starts with one of the three
   phrases `Keep it.`, `It must move an eval.` or `It depends.`, then the
   conditions.
8. `## Priors` — a list of cases, or the line `None yet.`
9. `## Confidence` — text.

Maximum size: 12,000 characters. A sub-file: 20,000 characters.

Checks on the body:

- The `How` section links to each lever document in `levers`, and to no
  other lever document.
- Each link in `Alternatives` points to a file in `techniques/`.

`cost` is an enum because an index sorts and filters on it. Confidence is
text because it carries conditions.

### 3.5 Playbook (`playbooks/`)

One playbook for each problem or goal. Its title names the problem.

Front matter, in addition to the common keys:

| Key | Type | Rule |
|---|---|---|
| `kind` | string | `goal`, `order`, `failure`, `eval` or `design`. It groups the indexes. It changes no other rule. |
| `applies_to` | see 3.2 | |

| Kind | Meaning |
|---|---|
| `goal` | What the user wants |
| `order` | How to plan a run |
| `failure` | A class of failure seen in the traces of eval items |
| `eval` | A problem with an eval or with the measurement |
| `design` | The response to a smell that needs more than one technique |

Required sections:

1. `## Situation` — the goal, or how to recognise the problem. For the kind
   `failure`: what the trace shows, and where in the trace. A playbook that
   responds to a smell links to the smell here and does not repeat how to
   recognise it.
2. `## What to try, in order` — a numbered list. Each item links to a
   technique and states its condition.
3. `## What not to do yet`
4. `## Budget` — how the order changes with the budget, or the line
   `The order does not change with the budget.`

Maximum size: 8,000 characters.

Checks on the body: `What to try, in order` has at least one link to a file
in `techniques/` or `playbooks/`, and each link there points to one of those
two directories.

### 3.6 Smell (`smells/`)

A smell document is a guide to recognise and diagnose one issue in the
design of an agent. Its reader is the baseline reviewer.

Front matter, in addition to the common keys:

| Key | Type | Rule |
|---|---|---|
| `applies_to` | see 3.2 | |
| `detected_by` | list of strings | One or more of `code`, `agent` |
| `detectors` | list of strings | The ids of the code detectors of this smell. Required and not empty when `detected_by` has `code`. Empty or absent otherwise. |

Required sections:

1. `## What it is`
2. `## How to recognise it` — the signs in the harness, and the signs in
   how the agent runs.
3. `## How to confirm it` — what to check before it is reported, and what
   looks like this smell and is not.
4. `## What it blocks` — or the line `Nothing.`
5. `## Remedies` — a list. Each item links to a playbook or a technique and
   states its condition. This section holds links, not the fix.

Maximum size: 4,000 characters.

Checks:

- `Remedies` has at least one link, and each link there points to a file in
  `playbooks/` or `techniques/`.
- Each id in `detectors` is the id of a registered detector whose smell is
  this smell. Each registered detector names a smell document that lists it.

### 3.7 Priors

A prior is one list item in the `Priors` section of a technique:

```
- **<shape of the agent, without a name>.** <what was tried>. <what happened>.
  Source: <public | own experiment | anonymised>. <date>.
```

The check requires that each item has a `Source:` with one of the three
values. Everything else about a prior is for the maintenance skill.

## 4. Entry files

An entry file has hand-written text and generated blocks. A block is:

```
<!-- BEGIN GENERATED: <name> -->
...
<!-- END GENERATED: <name> -->
```

The generator replaces what is between the markers. It changes nothing else.

| Entry file | Hand-written | Generated blocks |
|---|---|---|
| `planner.md` | How to use the knowledge. First step: read the three playbooks of the kind `order` (know the agent, ordering work, spending the budget) before you plan. Then: read the detections and the smell issues, find the playbook for each failure and each eval problem, reach a technique through a playbook or a smell | `smells`, `playbooks-order`, `playbooks-goal`, `playbooks-failure`, `playbooks-eval`, `playbooks-design` |
| `builder.md` | How to use the knowledge: open the technique named on the item, then the levers it links | `techniques`, `levers` |
| `smells.md` | How to review a design: read each smell, look for it, state the evidence, do not state a fix | `smells-detail` |
| `analyzer.md` | How to name a failure pattern in a finding | `playbooks-failure` |
| `report.md` | What the report can take from the knowledge | `smells`, `playbooks`, `techniques` |
| `technique-index.md` | "Prefer a playbook. Use this index only when the user's guidance names a technique." | `techniques` |

Block contents. Each is a Markdown table, sorted by id:

| Block | Columns |
|---|---|
| `smells` | Smell (link), Applies to, Description |
| `smells-detail` | Smell (link), Applies to, Detected by, Description |
| `playbooks` | Playbook (link), Kind, Applies to, Description |
| `playbooks-<kind>` | Playbook (link), Applies to, Description, for the playbooks of that kind |
| `techniques` | Technique (link), Levers, Applies to, Cost, Description. Dormant techniques are left out. |
| `levers` | Lever (link), Kind, Description |

A cell holds no `|` and no line break. The check on `description` refuses
both.

## 5. Reachability and depth

- The roots are the five role entry files and `technique-index.md`.
- A file is reachable when a chain of Markdown links leads to it from a
  root.
- **Every file** under `references/` must be reachable.
- **The depth limit is 5 links** from the nearest root. The longest designed
  chain is: entry file, smell, playbook, technique, lever (4). A sub-file
  adds 1.
- `SKILL.md` links to each entry file, but it is not a root for this check,
  because the templates send each role past it.

## 6. The Python package

`src/kiln_harness_tuner/knowledge/`

```
__init__.py     exports load, check, render_blocks, KnowledgeBase
schema.py       constants and the front-matter models
load.py         files to objects
check.py        the structural checks
index.py        the generated blocks
__main__.py     the command line
```

### 6.1 `schema.py`

```
TASK_SHAPES: tuple[str, ...]
COMPONENT_DIRS = ("levers", "techniques", "playbooks", "smells")
MAX_CHARS = {"levers": 6000, "techniques": 12000, "playbooks": 8000,
             "smells": 4000, "subfile": 20000}
MAX_DEPTH = 5
REQUIRED_SECTIONS: dict[str, tuple[str, ...]]
EVAL_STANCES = ("Keep it.", "It must move an eval.", "It depends.")
PRIOR_SOURCES = ("public", "own experiment", "anonymised")

class CommonMeta(BaseModel, extra="forbid"):
    title: str; description: str; reviewed: date
class LeverMeta(CommonMeta): gateway_ops: list[str] = []
class TechniqueMeta(CommonMeta):
    status: Literal["active", "dormant"]; levers: list[str]
    applies_to: Literal["all"] | list[str]; cost: Literal["low", "medium", "high"]
class PlaybookMeta(CommonMeta):
    kind: Literal["goal", "order", "failure", "eval", "design"]
    applies_to: Literal["all"] | list[str]
class SmellMeta(CommonMeta):
    applies_to: Literal["all"] | list[str]
    detected_by: list[Literal["code", "agent"]]; detectors: list[str] = []
```

### 6.2 `load.py`

```
@dataclass(frozen=True)
class Section: heading: str; text: str; links: tuple[Link, ...]
@dataclass(frozen=True)
class Link: target: str; resolved: Path | None   # None: external, or leaves the tree
@dataclass(frozen=True)
class Document:
    path: Path            # relative to references/
    kind: str             # a COMPONENT_DIRS value, "entry" or "subfile"
    id: str
    meta: CommonMeta | None
    sections: tuple[Section, ...]
    links: tuple[Link, ...]
    chars: int
    problems: tuple[Problem, ...]   # what load itself found: bad YAML, bad front matter
@dataclass(frozen=True)
class KnowledgeBase:
    root: Path
    documents: tuple[Document, ...]
    def by_kind(self, kind: str) -> tuple[Document, ...]
    def get(self, kind: str, id: str) -> Document | None

def default_root() -> Path          # the skill folder in the installed package
def load(root: Path | None = None) -> KnowledgeBase
```

- `load` never raises for a bad document. It records a `Problem` on the
  document. It raises `KnowledgeError` only when the root is missing.
- Front matter: the text between the first two `---` lines, parsed with
  `yaml.safe_load`, then validated with the model of the directory.
- Sections: split on lines that start with `## `. Fenced code blocks are
  skipped, so a `##` in a fence is not a heading.
- Links: the inline form `](target)`, outside fences. A target with a
  scheme is external. The part after `#` is dropped. The rest resolves
  against the file's directory, case-exact, and must stay inside the skill
  folder. This is the rule of `tests/test_docs.py`, and the helper is shared
  with it.

### 6.3 `check.py`

```
@dataclass(frozen=True)
class Problem: rule: str; path: str; message: str

def check(kb: KnowledgeBase, *, gateway_ops, detectors) -> list[Problem]
```

The caller passes the two code-side sets, so the function is pure and the
tests can pass small sets. `detectors` is a mapping from detector id to
smell id.

| Rule id | What it checks |
|---|---|
| `front-matter` | Front matter is present, parses, and fits the model |
| `id` | The file name fits the id pattern |
| `sections` | The required sections are present, in order, and not empty |
| `size` | The file is inside its size limit |
| `shape` | Each `applies_to` value is in `TASK_SHAPES` |
| `link` | Each internal link resolves |
| `reachable` | Each file is reachable from a root |
| `depth` | Each file is inside `MAX_DEPTH` |
| `technique-lever-ids` | Each value in a technique's `levers` is the id of a lever document |
| `lever-ops` | Each `gateway_ops` value is a gateway operation |
| `technique-lever-links` | The `How` section links the lever documents of `levers`, and only those |
| `technique-stance` | `If it does not move the evals` starts with one of the three phrases |
| `technique-priors` | Each prior has a valid `Source:` |
| `alternatives` | Each link in `Alternatives` is a technique |
| `playbook-steps` | `What to try, in order` links at least one technique or playbook, and only those |
| `smell-remedies` | `Remedies` links at least one playbook or technique, and only those |
| `smell-detectors` | `detectors` and the detector registry agree |
| `subfile` | Each sub-file is Markdown, is in a directory named after a component, and is linked from that component |
| `index` | Each generated block equals what `render_blocks` gives |
| `stray` | No file under `references/` is outside the layout of section 2 |

### 6.4 `index.py`

```
def render_blocks(kb: KnowledgeBase) -> dict[tuple[str, str], str]
    # (entry file name, block name) -> the text between the markers
def write_blocks(kb: KnowledgeBase) -> list[Path]
    # rewrites the entry files in place; returns the files it changed
```

The output is a pure function of the documents, with a fixed sort and fixed
column widths, so two runs give the same bytes.

### 6.5 Command line

```
uv run python -m kiln_harness_tuner.knowledge check
uv run python -m kiln_harness_tuner.knowledge build
```

- `check` prints one line for each problem (`rule  path  message`) and
  exits 1 when there is a problem.
- `build` runs `write_blocks`, then `check`.
- Both take `--root DIR`, for a tree that is not the package's own.

## 7. Changes to the existing agent files

- **Templates.** Each of `batch_plan.md`, `create_experiment_rc.md`,
  `analyze_experiment_results.md` and `report_writer.md` gets one sentence
  that names the knowledge skill and the entry file of the role, for
  example: "Read `references/planner.md` of the
  `kiln-harness-tuner-knowledge` skill before Step 1."
- **The lever ladder.** `references/lever-ladder.md` is removed. Each row
  becomes one playbook of the kind `failure`, as the V1 coverage document
  lists. Each reference to it is changed:
  `batch-selection/SKILL.md` (lines 411, 424, 430, 792, 795, 902, 1014),
  `experiment-build/SKILL.md:296`,
  `code-tool-authoring/references/wrappers/README.md:4`,
  `code-tool-authoring/SKILL.md:64`, `experiment-analysis/SKILL.md` (318,
  334), `agents/batch_plan.md:8-9`, `docs/agents.md:163`, and the comments
  at `schemas.py:208` and `:222`.
- **The planner's skill** keeps its process. Step 1 points to the planner
  entry file in place of the ladder. The rule that counts attempts by
  `lever_attempts` stays there.
- **The planner's skill** says that the short name in `technique` is the id
  of a lever document where one exists.

## 8. Dependencies

- Uses: PyYAML, Pydantic, `gateway.ops.OPS`, the detector registry of
  `smells`.
- Used by: the tests, the maintenance skill, and `smells` at run time (the
  list of smell ids).

`knowledge` does not import `smells`. The caller passes the detector
mapping, so the two packages do not depend on each other in a circle.

## 9. Test plan

`tests/test_knowledge.py` — the real tree:

- `test_the_shipped_knowledge_passes_every_check`
- `test_the_generated_blocks_are_current`
- `test_each_role_template_names_its_entry_file`
- `test_each_entry_file_exists_and_has_its_blocks`
- `test_the_lever_ladder_file_is_gone_and_nothing_names_it`
- `test_the_knowledge_skill_installs_for_each_agent_cli`

`tests/test_knowledge_checks.py` — one small tree for each rule, built by a
`make_tree` fixture that writes a valid minimal tree and applies one change:

- `test_a_valid_minimal_tree_has_no_problem`
- one test for each rule id of section 6.3, named
  `test_<rule>_is_reported`, which asserts that exactly that rule is
  reported
- `test_a_heading_inside_a_fence_is_not_a_section`
- `test_a_link_inside_a_fence_is_not_a_link`
- `test_a_dormant_technique_is_left_out_of_the_index`
- `test_render_blocks_is_stable`
- `test_build_rewrites_only_between_the_markers`
- `test_load_records_a_problem_and_does_not_raise`
- `test_the_command_line_exits_1_on_a_problem`
