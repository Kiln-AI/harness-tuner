---
status: complete
---

# Phase 1: License, CLA, doc contradictions and skill corrections

## Overview

This phase does every item in the functional spec. It makes the repository
MIT open source (a `LICENSE`, a CLA, the package metadata, the README and
CONTRIBUTING text). It then makes the docs and the agent skills agree with
the code: the installed executables, the held-out tag rule, the minimum Kiln
version, the supported platforms, the run-config parameters, how a tool gets
a new description, and which tools a wrapper can call. Item 2.2 is out of
scope. The only behaviour change is the `pyproject.toml` license metadata.

## Steps

1. **`LICENSE` (new).** The standard MIT text, as in Kiln's
   `libs/core/LICENSE.txt` without its "applies only to" preamble. Line:
   `Copyright (c) 2026 Chesterfield Laboratories Inc.`
2. **`pyproject.toml` `[project]`.** Add after `readme`:
   ```
   license = "MIT"
   license-files = ["LICENSE"]
   ```
   and after `keywords`:
   ```
   classifiers = [
       "License :: OSI Approved :: MIT License",
   ]
   ```
   Run `uv lock`; if `uv.lock` changes, keep it. Confirm `uv sync --locked`.
   Build a wheel into the scratchpad and confirm `License-Expression: MIT`,
   `License-File: LICENSE`, the classifier, and `licenses/LICENSE`.
3. **`.github/CLA.md` (new).** `cp` of Kiln `.config/CLA.md` at `6a04519`
   from the scratchpad clone. Confirm byte-identical with `cmp`.
4. **`.github/CONTRIBUTING.md` "Licence".** Replace the two-sentence body
   with: the project is under the MIT License (link `../LICENSE`); a new
   contributor must agree to the CLA in `.github/CLA.md` (link `CLA.md`).
   Keep `## Licence`.
5. **`README.md`.**
   - L16 Kiln row: "Kiln **v1.3 or later**", keep where to get it.
   - L24-26 platform paragraph: supported = Linux (x86_64 and arm64) and
     macOS on Apple silicon. Not supported = Windows (`cli.py` imports
     `fcntl`, Unix only; the sandbox fences are macOS and Linux
     mechanisms) and macOS on Intel (existing reason).
   - New `## License` section after `## Contributing`: MIT, link to
     `LICENSE`, then "The Kiln name and logos are trademarks of
     Chesterfield Laboratories Inc."
6. **`docs/install.md`.**
   - L23-28 "Platform support": the same platform facts at the same depth.
   - L107-110: replace the sample with the line `uv tool install .`
     printed in a scratch `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` (one line, 5
     executables, sorted).
   - §4 (L135-164): state Kiln v1.3 as the minimum; name the three APIs
     v1.2 lacks (memory, eval jobs, run-config provenance); give the v1.2
     symptom (start-up sessions run, then
     `kiln-harness-tuner: GET /api/projects/<project-id>/memories -> HTTP 404: …`,
     exit 1). Keep the metadata block.
7. **Held-out tags (item 2.3).**
   - `docs/limitations.md` L291-297: the full rule (`val`, `test`,
     `val_*`, `test_*`, `holdout*`, `heldout*`, any case); unrecognised
     examples become `held_out`, `eval_set`.
   - `docs/architecture.md` L1037-1038: add `holdout*` and `heldout*`.
   - `.agents/skills/kiln-harness-tuner-gateway/SKILL.md` L147: add
     `heldout…`.
   - `src/kiln_harness_tuner/quarantine.py` L30-33 docstring: add
     `holdout*` and `heldout*`.
8. **"max tokens" (item 3.1).** Remove it from
   `kiln-harness-tuner-batch-selection/SKILL.md` L61 and L446.
9. **Tool descriptions (item 3.2).** Reword so a new description is a new
   code tool (a clone) or a wrapper:
   - `code-tool-authoring/SKILL.md` L52-53 (`tool_config` = attach or drop;
     a new description is a clone or a wrapper).
   - `batch-selection/SKILL.md` L391.
   - `experiment-build/SKILL.md` L51-52.
   - `batch-selection/references/lever-ladder.md` L29-34 (add one sentence
     after the skill-immutability sentence), L43, L50, L60, L99 (rename the
     step to "code-tool clone with a new description", same position, with
     the MCP/built-in note).
   - `docs/concepts.md` L68 ("rewrite the tool description" as a lever).
   - Check and leave: `experiment-analysis/SKILL.md` L338 and L353,
     `docs/limitations.md` L190 (name the surface only).
10. **Wrappers (item 3.3).** Reword so a wrapper can call any tool a run
    config can attach, except skills:
    - `code-tool-authoring/SKILL.md` L92-94: wrap targets are the parent
      rc's tools of any kind (code, MCP, built-in, …); nested code-tool
      calls keep the depth cap 10.
    - `code-tool-authoring/SKILL.md` L110-115: schema from `get_code_tool`
      for a code tool, `get_tool_definition` for any other tool; name the
      accepted allowlist kinds; function names unique in one allowlist
      except among MCP tools.
    - `code-tool-authoring/SKILL.md` L132 operations table: add
      `get_tool_definition` for a non-code tool.
    - `code-tool-authoring/SKILL.md` L163 onwards: the worked example's
      allowlist entry becomes the wrapped tool's id, of any kind.
    - `code-tool-authoring/references/wrappers/README.md` L6-8 and the
      `actionable_errors.py` docstring (L13-14): the same schema sources.
    - Keep the allowlist hygiene rule unchanged.

## Tests

- `tests/test_license_metadata.py::test_license_file_is_mit` — `LICENSE`
  exists, starts with "MIT License", carries the Chesterfield Laboratories
  Inc. copyright line, and has no "applies only to" preamble.
- `tests/test_license_metadata.py::test_pyproject_declares_mit` —
  `pyproject.toml` has `license = "MIT"`, `license-files = ["LICENSE"]` and
  the MIT classifier, and every `license-files` entry exists.
- `tests/test_license_metadata.py::test_cla_is_present` — `.github/CLA.md`
  exists and CONTRIBUTING's Licence section links it and `LICENSE`.
- `tests/test_docs.py::test_install_doc_lists_every_console_script` —
  the `Installed N executables:` line in `docs/install.md` names exactly
  the `[project.scripts]` keys, sorted, with the right count.
- Existing suite (`uv run pytest`), `ruff check`, `ruff format --check`,
  `uv sync --locked` pass.
