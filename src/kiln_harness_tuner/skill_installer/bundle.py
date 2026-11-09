"""Skill-bundle validation + atomic install into a Kiln project's skills/ tree.

Pure logic — no MCP imports — so every rule is unit-testable. The contract:
an agent authors a complete bundle (SKILL.md + references/** + assets/**) in
its sandboxed scratch dir; trusted code (this module, invoked via the
skill_installer MCP server or the run-start sweep) validates the whole
subtree, mints the id, writes skill.kiln itself, stages the finished tree at
`<project>/skills/.staging-<id>/` and commits with a single `os.rename` — a
skill either doesn't exist or exists fully formed. Install-once: the module
only ever creates directories that didn't exist, never updates one, so every
installed skill is immutable by construction (the Kiln-side property the
drive-fingerprint blind spot relies on).

Format facts are exact ports from the Kiln source:
`string_to_valid_name`/`build_child_dirname` from datamodel/basemodel.py,
`skill_name_validator` from utils/validation.py, the skill.kiln field set
from datamodel/skill.py (reference on disk: any installed skill's own file,
`<project>/skills/<id> - <name>/skill.kiln`). The
studio server reads disk live, so no API call is needed — same mechanism
that makes `git checkout` the reset lane.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from kiln_harness_tuner.path_order import path_key

# Kiln basemodel.py ports (FORBIDDEN_CHARS_REGEX + string_to_valid_name).
_FORBIDDEN_CHARS_REGEX = r"[/\\?%*:|\"<>.,;=\n]"

# Caps: observed real skills run 6-8 refs x ~46 KB — 10x headroom.
MAX_FILE_BYTES = 512 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024

_ALLOWED_ROOT_DIRS = ("references", "assets")
_MINT_ATTEMPTS = 100


class InstallError(RuntimeError):
    """Install failed after validation passed (staging/rename phase)."""


def string_to_valid_name(name: str) -> str:
    """Exact port of Kiln's `string_to_valid_name` (sans the truncate branch,
    which no call site here needs)."""
    valid_name = unicodedata.normalize("NFKD", name)
    valid_name = re.sub(_FORBIDDEN_CHARS_REGEX, " ", valid_name)
    valid_name = re.sub(r"[\x00-\x1F]", " ", valid_name)
    valid_name = re.sub(r"\s+", " ", valid_name)
    valid_name = re.sub(r"_+", "_", valid_name)
    return valid_name.strip("_").strip()


def build_skill_dirname(skill_id: str, name: str) -> str:
    """Exact port of Kiln's `build_child_dirname`: `{id} - {name[:32] sanitized}`."""
    name_part = string_to_valid_name(name[:32])
    if not name_part:
        # Unreachable for a validated kebab-case name; kept for Kiln parity.
        raise InstallError(
            "skill name sanitizes to an empty dirname — cannot build the skill dir"
        )
    return f"{skill_id} - {name_part}"


def validate_skill_name(name: object) -> list[str]:
    """Kiln's `skill_name_validator`, returning every failure instead of raising."""
    if not isinstance(name, str):
        return ["skill name must be a string"]
    if len(name.strip()) == 0:
        return ["skill name cannot be empty"]
    errors: list[str] = []
    if len(name) > 64:
        errors.append("skill name must be 64 characters or fewer")
    if not re.match(r"^[a-z0-9-]+$", name):
        errors.append(
            "skill name may only contain lowercase letters (a-z), numbers (0-9),"
            " and hyphens (-)"
        )
    if name.startswith("-") or name.endswith("-"):
        errors.append("skill name must not start or end with a hyphen")
    if "--" in name:
        errors.append("skill name must not contain consecutive hyphens")
    if not re.match(r"^[a-z]", name):
        errors.append("skill name must start with a lowercase letter")
    if name.lower() == "skill":
        errors.append('"skill" is a reserved skill name — choose a different name')
    return errors


def parse_skill_md(raw: str) -> tuple[str | None, str | None, str | None, list[str]]:
    """Parse SKILL.md into (name, description, body, errors).

    Mirrors Kiln's `_parse_skill_md_body`: frontmatter must open the file with
    `---\\n` and close with `\\n---\\n`. Frontmatter is the single source for
    name/description — there is no separate manifest file."""
    if not raw.startswith("---\n"):
        return (
            None,
            None,
            None,
            [
                "SKILL.md must start with a `---` YAML frontmatter block containing"
                " `name` and `description`"
            ],
        )
    closing = raw.find("\n---\n", len("---\n") - 1)
    if closing == -1:
        return None, None, None, ["SKILL.md frontmatter has no closing `---` line"]
    fm_raw = raw[len("---\n") : closing]
    body = raw[closing + len("\n---\n") :]
    try:
        fm = yaml.safe_load(fm_raw)
    except yaml.YAMLError as exc:
        return None, None, body, [f"SKILL.md frontmatter is not valid YAML: {exc}"]
    if not isinstance(fm, dict):
        return None, None, body, ["SKILL.md frontmatter must be a YAML mapping"]
    errors: list[str] = []
    extra = set(fm) - {"name", "description"}
    if extra:
        errors.append(
            "SKILL.md frontmatter must contain exactly `name` and `description`"
            f" — unexpected keys: {sorted(extra)}"
        )
    name = fm.get("name")
    description = fm.get("description")
    if "name" not in fm:
        errors.append("SKILL.md frontmatter is missing `name`")
    if "description" not in fm:
        errors.append("SKILL.md frontmatter is missing `description`")
    if "description" in fm and not isinstance(description, str):
        errors.append("SKILL.md frontmatter `description` must be a string")
        description = None
    return name, description if isinstance(description, str) else None, body, errors


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _existing_skill_names(skills_dir: Path) -> set[str]:
    """Names already taken: skill.kiln `name` fields plus (belt and braces)
    the ` - <name>` suffix of every skill dir name."""
    names: set[str] = set()
    if not skills_dir.is_dir():
        return names
    for child in skills_dir.iterdir():
        if not child.is_dir():
            continue
        _, sep, suffix = child.name.partition(" - ")
        if sep:
            names.add(suffix)
        kiln = child / "skill.kiln"
        if kiln.is_file():
            try:
                data = json.loads(kiln.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # malformed sidecar is not this bundle's problem
            name = data.get("name") if isinstance(data, dict) else None
            if isinstance(name, str):
                names.add(name)
    return names


def validate_bundle(
    bundle_path: Path,
    scratch_root: Path,
    skills_dir: Path,
    provenance_notes: str,
    derived_from_ids: list[str],
) -> list[str]:
    """Run every rule, accumulate every failure — the agent fixes the bundle
    once, not one ping-pong round trip per defect."""
    errors: list[str] = []

    # Tool args first — they can fail regardless of the tree.
    if not isinstance(provenance_notes, str) or not provenance_notes.strip():
        errors.append("provenance_notes must be a non-empty string")
    if not isinstance(derived_from_ids, list) or any(
        not isinstance(i, str) or not i.strip() for i in derived_from_ids
    ):
        errors.append("derived_from_ids must be a list of non-empty strings")

    # Location/shape: realpath containment before any read — the server must
    # not read outside the scratch tree.
    scratch_real = scratch_root.resolve()
    bundle_real = bundle_path.resolve()
    if bundle_real == scratch_real or not _inside(bundle_real, scratch_real):
        errors.append(
            f"bundle_path must be a directory strictly inside the scratch dir"
            f" ({scratch_real})"
        )
        return errors
    if not bundle_real.is_dir():
        errors.append("bundle_path is not a directory")
        return errors

    for entry in bundle_real.iterdir():
        if entry.name == "SKILL.md" and entry.is_file():
            continue
        if (
            entry.name in _ALLOWED_ROOT_DIRS
            and entry.is_dir()
            and not entry.is_symlink()
        ):
            continue
        errors.append(
            f"unexpected bundle entry: {entry.name!r} — allowed: SKILL.md (file),"
            f" references/ and assets/ (dirs); skill.kiln is written by the installer"
        )

    total_bytes = 0
    for root, dirs, files in os.walk(bundle_real, followlinks=False):
        root_p = Path(root)
        for d in dirs:
            if (root_p / d).is_symlink():
                rel_d = (root_p / d).relative_to(bundle_real)
                errors.append(f"symlinked directory not allowed: {rel_d}")
        for f in files:
            fp = root_p / f
            rel = fp.relative_to(bundle_real)
            if fp.is_symlink():
                errors.append(f"symlinked file not allowed: {rel}")
                continue
            if not fp.is_file():
                errors.append(f"not a regular file: {rel}")
                continue
            size = fp.stat().st_size
            total_bytes += size
            if size > MAX_FILE_BYTES:
                errors.append(
                    f"file too large: {rel} is {size} bytes (cap {MAX_FILE_BYTES})"
                )
            # Kiln's runtime resource read decodes every references/ file as
            # UTF-8 and raises otherwise — reject at install, not at runtime.
            if rel.parts and rel.parts[0] == "references":
                try:
                    fp.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    errors.append(f"references file is not UTF-8 text: {rel}")
    if total_bytes > MAX_BUNDLE_BYTES:
        errors.append(
            f"bundle too large: {total_bytes} bytes total (cap {MAX_BUNDLE_BYTES})"
        )

    skill_md = bundle_real / "SKILL.md"
    if not skill_md.is_file() or skill_md.is_symlink():
        errors.append("bundle is missing SKILL.md at its root")
        return errors

    try:
        # As Kiln reads it (`Skill.skill_md_raw`): text mode, so a file with
        # Windows line endings parses with `\n` lines.
        raw = skill_md.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append("SKILL.md is not UTF-8 text")
        return errors

    name, description, body, fm_errors = parse_skill_md(raw)
    errors.extend(fm_errors)
    if name is not None:
        name_errors = validate_skill_name(name)
        errors.extend(name_errors)
        if not name_errors and name in _existing_skill_names(skills_dir):
            errors.append(
                f"skill name {name!r} already exists in {skills_dir} — skills are"
                " install-once; pick a new name (new version = new skill)"
            )
    if description is not None and not 1 <= len(description) <= 1024:
        errors.append(f"description must be 1-1024 characters (got {len(description)})")
    if body is not None and not body.strip():
        errors.append("SKILL.md body (after the frontmatter) must be non-empty")

    return errors


def mint_skill_id(skills_dir: Path) -> tuple[str, Path]:
    """Mint a fresh 12-digit id (Kiln scheme: `str(uuid.uuid4().int)[:12]`) and
    reserve it by creating `.staging-<id>` with `exist_ok=False` — the mkdir is
    the cross-process lock between concurrent build-agent sessions."""
    for _ in range(_MINT_ATTEMPTS):
        skill_id = str(uuid.uuid4().int)[:12]
        if any(skills_dir.glob(f"{skill_id} - *")):
            continue
        staging = skills_dir / f".staging-{skill_id}"
        try:
            staging.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            continue
        return skill_id, staging
    raise InstallError("could not mint a fresh skill id (exhausted attempts)")


def install_bundle(
    bundle_path: Path,
    scratch_root: Path,
    project_path: Path,
    provenance_notes: str,
    derived_from_ids: list[str],
) -> dict:
    """Validate → stage → atomic rename. Returns `{"ok": True, skill_id,
    skill_name, tool_id}` or `{"ok": False, "errors": [...]}` — never raises
    for agent-fixable problems, and a rejected bundle writes nothing."""
    skills_dir = project_path / "skills"
    errors = validate_bundle(
        bundle_path, scratch_root, skills_dir, provenance_notes, derived_from_ids
    )
    if errors:
        return {"ok": False, "errors": errors}

    bundle_real = bundle_path.resolve()
    raw = (bundle_real / "SKILL.md").read_text(encoding="utf-8")
    name, description, _body, _ = parse_skill_md(raw)
    assert isinstance(name, str) and isinstance(description, str)  # validated

    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_id, staging = mint_skill_id(skills_dir)
    try:
        # SKILL.md is installed byte-identical to the bundle's — skill.kiln is
        # derived from its frontmatter, so the two cannot diverge.
        shutil.copyfile(bundle_real / "SKILL.md", staging / "SKILL.md")
        for sub in _ALLOWED_ROOT_DIRS:
            src = bundle_real / sub
            if src.is_dir():
                shutil.copytree(src, staging / sub, symlinks=False)
            else:
                # Kiln parity: save_skill_md always creates both dirs.
                (staging / sub).mkdir()
        sidecar = {
            "v": 1,
            "id": skill_id,
            "created_at": datetime.now().astimezone().isoformat(),
            "created_by": getpass.getuser(),
            "name": name,
            "description": description,
            "is_archived": False,
            "provenance": {
                "notes": provenance_notes,
                "derived_from_ids": derived_from_ids,
                "origin": "agent",
            },
            "model_type": "skill",
        }
        (staging / "skill.kiln").write_text(
            json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        dest = skills_dir / build_skill_dirname(skill_id, name)
        if dest.exists():
            raise InstallError(f"destination already exists: {dest} — install-once")
        # The commit point: one atomic rename on the same filesystem. Before
        # it the skill doesn't exist anywhere visible; after it, it exists
        # fully formed. No observer can see a partial skill.
        os.rename(staging, dest)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "errors": [f"install failed: {exc}"]}

    return {
        "ok": True,
        "skill_id": skill_id,
        "skill_name": name,
        "tool_id": f"kiln_tool::skill::{skill_id}",
    }


def sweep_staging(project_path: Path) -> int:
    """Delete `.staging-*` crash debris under `<project>/skills/`. Run-start
    only (the one-run-per-task lock guarantees no live install)."""
    skills_dir = project_path / "skills"
    if not skills_dir.is_dir():
        return 0
    count = 0
    for staging in skills_dir.glob(".staging-*"):
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
            count += 1
    return count


def installed_skill_dir(skills_dir: Path, skill_id: str) -> Path | None:
    """The one `<id> - <name>` directory of an installed skill, or None when
    there is no such directory or more than one."""
    if not skills_dir.is_dir():
        return None
    matches = [
        d
        for d in skills_dir.iterdir()
        if d.is_dir() and d.name.split(" - ")[0] == skill_id
    ]
    return matches[0] if len(matches) == 1 else None


# The ceiling on one exported skill's size: a skill bundle is text, and the
# export is a copy into the agent's scratch, never a way to move bulk data.
EXPORT_MAX_BYTES = 4 * 1024 * 1024


def export_bundle(
    skill_id: str, dest_path: Path, scratch_root: Path, project_path: Path
) -> dict:
    """Copy an installed skill's `SKILL.md`, `references/` and `assets/` into
    a NEW directory inside the scratch tree. The project tree is
    read-denied to agents — it also holds every split's eval inputs and
    results — so this trusted lane is how a builder reads a skill it clones.
    `skill.kiln` is never copied (identity is minted at install). Symlinks
    are refused, and nothing outside the skill's own directory is read.

    Returns `{"ok": True, "path", "files"}` or `{"ok": False, "errors"}`."""
    errors: list[str] = []
    if not isinstance(skill_id, str) or not re.fullmatch(r"[0-9A-Za-z_-]+", skill_id):
        return {"ok": False, "errors": ["skill_id must be a bare skill id"]}
    source = installed_skill_dir(project_path / "skills", skill_id)
    if source is None:
        return {"ok": False, "errors": [f"no installed skill with id {skill_id}"]}
    scratch_real = scratch_root.resolve()
    dest = dest_path.resolve() if dest_path.is_absolute() else dest_path
    if (
        not dest_path.is_absolute()
        or not _inside(dest, scratch_real)
        or dest == scratch_real
    ):
        errors.append(
            f"dest_path must be an absolute path strictly inside the scratch dir"
            f" ({scratch_real})"
        )
    elif dest.exists():
        errors.append(f"dest_path {dest} already exists — name a new directory")
    wanted = [source / "SKILL.md"]
    for sub in ("references", "assets"):
        if (source / sub).is_dir():
            wanted += sorted((source / sub).rglob("*"), key=path_key)
    total = 0
    for path in wanted:
        if path.is_symlink():
            errors.append(f"{path.relative_to(source)} is a symlink — not exported")
        elif path.is_file():
            total += path.stat().st_size
    if total > EXPORT_MAX_BYTES:
        errors.append(f"the skill is {total} bytes, over the {EXPORT_MAX_BYTES} cap")
    if not (source / "SKILL.md").is_file():
        errors.append("the installed skill has no SKILL.md")
    if errors:
        return {"ok": False, "errors": errors}
    files: list[str] = []
    for path in wanted:
        relative = path.relative_to(source)
        target = dest / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        files.append(str(relative))
    return {"ok": True, "path": str(dest), "files": files}
