# Skill authoring (skill-lever items)

How to build the skill artifact when the item's lever is `skill`. Two shapes:

- **Flat** (a single instruction page, no resource files): `POST /skills`
  with `{name, description, body, provenance}` as `kiln-task-setup`
  documents. Done — the rest of this file is not for you.
- **Bundle** (the skill carries `references/` and/or `assets/`): author the
  complete bundle under your `scratch_dir` and install it with the
  `install_skill_bundle` MCP tool. Never `POST /skills` plus per-file disk
  writes — the project tree is write-denied for every file tool, and the
  installer is the only lane into `skills/`.

## 1. Author the bundle in scratch

Layout, under any directory inside your `scratch_dir` (e.g.
`<scratch_dir>/skill_bundle/`):

```
skill_bundle/
  SKILL.md                    # required, at the root
  references/…                # optional; nested subdirs fine; UTF-8 text only
  assets/…                    # optional; binary allowed
```

Do NOT write a `skill.kiln` — the installer mints the id and writes the
sidecar itself. Caps: 512 KB per file, 2 MB per bundle. No symlinks.

**Use the Write tool to create the files.** Bash file creation can fail
`Operation not permitted` even inside your own scratch dir (measured), while
Write works and creates missing parent directories, which is also how a
nested `references/policies/` dir gets made.

## 2. Progressive disclosure — the shape of a good skill

A skill is a tiered document; each tier costs context only when the model
actually reaches it:

1. **The description** (frontmatter, duplicated into `skill.kiln` by the
   installer) sits in the tool listing of EVERY conversation, loaded or
   not. It decides whether the skill gets invoked at all, and it carries
   the resource-path index (section 3).
2. **The SKILL.md body** loads on `skill(name='<name>')`: general guidance
   plus a MAP of the reference files — for each one its full
   `references/...` path and one line on what it covers and when to read
   it. That line names what the file CONTAINS — its key terms, the words a
   question about it would use — not only its title: a model matches a
   question to a file by those words. The body orients; it never
   duplicates reference content.
3. **`references/` files** load individually via
   `skill(name='<name>', resource='references/<path>')`, only when the
   task at hand needs that topic.

Partition `references/` by topic, one file per coherent subject
(per-resource endpoints, global conventions, policies, …), so a
conversation pays for exactly the topics it touches — a task about
returns loads `references/returns.md`, not the whole corpus. A
monolithic SKILL.md defeats the tiering: everything loads on every
invocation. The shape that works is a short body mapping 6–8 topic files.

### When the model is lost in a long prompt or one big skill

The knowledge skill's playbook for this failure (lost in long
instructions) makes the FIRST arm structural, not a rewrite of the text.

1. Cut the long text into topics — one `references/<topic>.md` per
   coherent subject, each self-contained (a reader who opens only that file
   can act).
2. Write a SKILL.md body that is only the map: one line per file, its full
   `references/...` path, its key terms, when to read it. No content that
   also lives in a reference file.
3. Put the full path index in the description with a one-clause
   disambiguator per path and close with "No other resource paths exist —
   never guess one".
4. Do NOT let the router narrate an order of operations ("load conventions
   first, then look up the case"): it changes the model's call order and can
   break a criterion the bundle was not aimed at. State what each file covers;
   let the task decide the order.
5. Measure the bundle against EVERY active eval, not only its target.

**Lint before install, for free.** `install_skill_bundle` validates the
whole bundle and returns every error at once with nothing written; a
rejected call costs seconds. Call it as soon as the bundle is on disk, fix
the list, call again — do not proof-read the frontmatter by hand first.

## 3. The SKILL.md contract

Frontmatter with exactly two keys — it is the single source for both:

```
---
name: <kebab-case-name>
description: <the decision-point text — see below>
---

<body — non-empty>
```

**Name rules** (rejected otherwise): ≤64 chars, only `a-z0-9-`, starts with
a lowercase letter, no leading/trailing `-`, no `--`, not the reserved word
`skill`, and not a name any existing skill already carries — names are
install-once, so a new version of a skill is a NEW name (`…-v2`).

**The description is the highest-leverage text in the whole skill.** It is
the only per-skill text at the model's tool-selection decision point.
Within its 1–1024 chars: say what the skill covers, give the invocation
(`skill(name='<name>')` first), then **inline the full resource-path index**
— every `references/...` path with a one-clause disambiguator that names its
key terms — and close with "No other resource paths exist — never guess
one." A resource-less
flat skill instead says it has NO resource files and to never pass a
resource path.

**The body stays short** — the tier-2 map of section 2, nothing more. Index
resources by their full `references/...` path in the body too, never bare
filenames.

## 4. Cloning or rewriting an existing skill

The project tree is not readable in your session (it also holds every
split's eval inputs and results). Get the parent skill's files with the
installer's export: `export_skill_bundle(skill_id=<id>, dest_path=<a new
directory inside your scratch dir>)` copies its `SKILL.md`, `references/`
and `assets/` there. Read them there (Bash `cat`/`diff -r` for verification
is fine), edit the copies into your bundle, then apply the item's
hypothesized change. Copy **content only — never `skill.kiln`**
(identity is minted at install). Rename (name collision is enforced), update
the frontmatter description's name/index to match, and cite the source skill
id in `derived_from_ids`.

### Rewriting an inherited skill

The skills the parent attaches are config, the same as its prompt. "A skill
is immutable" means the OBJECT cannot change after install, not that the
skill is off-limits: a change is a new version. When the item's failure
comes from an inherited skill — it is wrong, stale, monolithic, contradicts
the task, or carries data shaped like test cases — rewrite it instead of
adding prompt rules around it:

1. Export it (`export_skill_bundle`), as above.
2. Change it the way the item says. **Split** a monolithic body into topic
   files with a map; **merge** skills that cover one subject; **restructure**
   it into progressive disclosure; **scrub** it — remove names, ids, numbers
   and examples that copy records or cases, and keep the rule each one was
   standing in for.
3. Install it under a new name, with the old skill's id first in
   `derived_from_ids`: the generality check then diffs it against the
   version it replaces, not as a new skill in full.
4. On the new run config, attach the new version and detach the old one.
   Keeping both attached gives the model two versions of one subject.

A long reference document that the parent serves through a lookup tool
(paged, searched or by section) can move into a skill the same way: a
contents file that names what each section contains, one `references/`
file per section, and the lookup tool dropped on the same run config.

## 5. Install

```
install_skill_bundle(
  bundle_path=<abs path to the bundle dir>,
  provenance_notes=<hypothesis + evidence, cite issue/trace ids as prose>,
  derived_from_ids=[<parent rc / source skill ids>],
)
```

- Success: `{"ok": true, "skill_id", "skill_name", "tool_id"}`. Wire
  `tool_id` (`kiln_tool::skill::<id>`) into the new run config's
  `tools_config.tools`, then sanity-smoke as usual — a smoke turn that reads
  one reference file proves the resource lane end-to-end. To see the read,
  `Grep` the smoke's trace file (`run_task`'s `trace_path`) for the
  reference file's name.
- Rejection: `{"ok": false, "errors": [...]}` lists **every** problem at
  once and **nothing was written** — fix the bundle in scratch and call
  again. No cleanup needed, no half-created skill exists.
- The install is atomic (staged, single rename): the skill appears fully
  formed or not at all, and it is **immutable after install** — there is no
  update path, by design (drive fingerprints do not cover skill files, so a
  mutated skill would silently invalidate stored traces). A body typo or a
  description fix means a new bundle under a new name; note the dead
  predecessor in `build_notes` and archive it with the gateway's
  `archive_skill` operation if it was never wired.

## 6. Prohibitions

- Never write into `skills/` (or anywhere in the project tree) with file
  tools or Bash — denied, and the denial is the design.
- Never modify another experiment's skill, and never re-use a skill name.
- The installer writes `skill.kiln`'s description from your frontmatter —
  they cannot diverge, so don't try to manage the sidecar yourself.
