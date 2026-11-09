"""Skill-bundle installer tests: Kiln-format parity, the full validation rule
set (every failure reported at once), atomic install, crash-debris sweep, and
the MCP server surface. All tmp_path — never a real Kiln project tree."""

import getpass
import json
from datetime import datetime
from pathlib import Path

import pytest
from platform_marks import requires_symlinks

from kiln_harness_tuner.skill_installer import bundle as b
from kiln_harness_tuner.skill_installer.bundle import (
    MAX_BUNDLE_BYTES,
    MAX_FILE_BYTES,
    build_skill_dirname,
    install_bundle,
    string_to_valid_name,
    sweep_staging,
    validate_bundle,
    validate_skill_name,
)
from kiln_harness_tuner.skill_installer.mcp_server import build_server


def make_bundle(
    tmp_path,
    name="fresh-skill",
    description="A fresh skill.",
    body="Use the thing.\n",
    frontmatter=None,
    refs=None,
    assets=None,
):
    """A valid bundle under <tmp>/scratch/bundle unless overridden."""
    scratch = tmp_path / "scratch"
    bundle = scratch / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n\n"
    (bundle / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    for rel, content in (refs or {}).items():
        p = bundle / "references" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    for rel, content in (assets or {}).items():
        p = bundle / "assets" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return bundle, scratch


def make_project(tmp_path, existing_name="existing-skill", existing_id="424242111111"):
    """A fake Kiln project with one installed skill."""
    project = tmp_path / "proj"
    skill_dir = project / "skills" / f"{existing_id} - {existing_name}"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.kiln").write_text(
        json.dumps(
            {"v": 1, "id": existing_id, "name": existing_name, "model_type": "skill"}
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {existing_name}\ndescription: x\n---\n\nBody.\n", encoding="utf-8"
    )
    return project


def validate(
    tmp_path, bundle, scratch, project, notes="hypothesis + evidence", derived=None
):
    return validate_bundle(
        bundle,
        scratch,
        project / "skills",
        notes,
        derived if derived is not None else [],
    )


class TestSanitizeParity:
    # Port fidelity vs Kiln's string_to_valid_name / build_child_dirname.

    def test_kebab_names_pass_through(self):
        assert string_to_valid_name("depot-api-v3") == "depot-api-v3"

    def test_forbidden_chars_become_single_spaces(self):
        assert string_to_valid_name('a/b\\c?d%e*f:g|h"i<j>k') == (
            "a b c d e f g h i j k"
        )
        assert string_to_valid_name("v4.1, beta; x=y") == "v4 1 beta x y"

    def test_control_chars_and_whitespace_collapse(self):
        assert string_to_valid_name("a\x01b\n c\t d") == "a b c d"

    def test_underscores_collapse_and_strip(self):
        assert string_to_valid_name("__a__b__") == "a_b"

    def test_nfkd_normalization(self):
        assert string_to_valid_name("café") == "café"

    def test_dirname_truncates_name_at_32_then_sanitizes(self):
        long = "population-and-breakdown-contract-extra"
        # Matches the observed real dir `… - population-and-breakdown-contrac`.
        assert build_skill_dirname("424242119077", long) == (
            "424242119077 - population-and-breakdown-contrac"
        )

    def test_empty_after_sanitize_raises(self):
        try:
            build_skill_dirname("424242789012", "...")
        except b.InstallError:
            return
        raise AssertionError("expected InstallError")


class TestValidateSkillName:
    def test_valid(self):
        assert validate_skill_name("depot-api-v3") == []

    def test_each_branch(self):
        assert validate_skill_name(123)  # not a string
        assert validate_skill_name("  ")  # empty
        assert validate_skill_name("a" * 65)  # too long
        assert validate_skill_name("Upper-Case")  # charset
        assert validate_skill_name("café-skill")  # unicode
        assert validate_skill_name("-leading")
        assert validate_skill_name("trailing-")
        assert validate_skill_name("a--b")
        assert validate_skill_name("9starts-with-digit")
        assert validate_skill_name("skill")  # reserved
        assert validate_skill_name("Skill")  # reserved is case-insensitive


class TestValidateBundle:
    def test_happy_path(self, tmp_path):
        bundle, scratch = make_bundle(
            tmp_path,
            refs={"guide.md": "g", "policies/deep.md": "d"},
            assets={"logo.png": b"\x89PNG\x00binary"},
        )
        project = make_project(tmp_path)
        assert validate(tmp_path, bundle, scratch, project) == []

    def test_bundle_outside_scratch(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path)
        project = make_project(tmp_path)
        errors = validate(tmp_path, project, scratch, project)  # project as bundle
        assert any("strictly inside the scratch dir" in e for e in errors)

    def test_bundle_is_scratch_root_itself(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path)
        project = make_project(tmp_path)
        errors = validate(tmp_path, scratch, scratch, project)
        assert any("strictly inside the scratch dir" in e for e in errors)

    def test_bundle_is_a_file(self, tmp_path):
        _, scratch = make_bundle(tmp_path)
        f = scratch / "not-a-dir.md"
        f.write_text("x", encoding="utf-8")
        project = make_project(tmp_path)
        assert any(
            "not a directory" in e for e in validate(tmp_path, f, scratch, project)
        )

    def test_missing_skill_md(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path)
        (bundle / "SKILL.md").unlink()
        project = make_project(tmp_path)
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("missing SKILL.md" in e for e in errors)

    def test_stray_root_entries_rejected(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path)
        (bundle / "skill.kiln").write_text("{}", encoding="utf-8")
        (bundle / "notes.txt").write_text("x", encoding="utf-8")
        project = make_project(tmp_path)
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("'skill.kiln'" in e for e in errors)
        assert any("'notes.txt'" in e for e in errors)

    @requires_symlinks
    def test_symlinks_rejected(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path, refs={"real.md": "x"})
        project = make_project(tmp_path)
        real = bundle / "references" / "real.md"
        (bundle / "references" / "link.md").symlink_to(real)
        outside = tmp_path / "outside-dir"
        outside.mkdir()
        (bundle / "assets").mkdir()
        (bundle / "assets" / "sneaky").symlink_to(outside)
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("symlinked file" in e for e in errors)
        assert any("symlinked directory" in e for e in errors)

    def test_frontmatter_missing(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path, frontmatter="no frontmatter\n\n")
        project = make_project(tmp_path)
        assert any(
            "must start with a `---`" in e
            for e in validate(tmp_path, bundle, scratch, project)
        )

    def test_frontmatter_unclosed(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path, frontmatter="---\nname: x\n")
        project = make_project(tmp_path)
        assert any(
            "no closing" in e for e in validate(tmp_path, bundle, scratch, project)
        )

    def test_frontmatter_extra_and_missing_keys(self, tmp_path):
        bundle, scratch = make_bundle(
            tmp_path, frontmatter="---\nname: ok-name\nlicense: mit\n---\n\n"
        )
        project = make_project(tmp_path)
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("unexpected keys" in e for e in errors)
        assert any("missing `description`" in e for e in errors)

    def test_bad_name_reported(self, tmp_path):
        bundle, scratch = make_bundle(
            tmp_path, frontmatter="---\nname: Bad--Name-\ndescription: d\n---\n\n"
        )
        project = make_project(tmp_path)
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("lowercase letters" in e for e in errors)
        assert any("consecutive hyphens" in e for e in errors)

    def test_name_collision_via_sidecar_and_dirname(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path, name="existing-skill")
        project = make_project(tmp_path, existing_name="existing-skill")
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("already exists" in e for e in errors)
        # Sidecar-less dir still collides via the ` - <name>` suffix.
        (project / "skills" / "424242111111 - existing-skill" / "skill.kiln").unlink()
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("already exists" in e for e in errors)

    def test_description_bounds(self, tmp_path):
        bundle, scratch = make_bundle(
            tmp_path,
            frontmatter=f"---\nname: ok-name\ndescription: {'x' * 1025}\n---\n\n",
        )
        project = make_project(tmp_path)
        assert any("1-1024" in e for e in validate(tmp_path, bundle, scratch, project))

    def test_empty_body(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path, body="  \n")
        project = make_project(tmp_path)
        assert any(
            "must be non-empty" in e
            for e in validate(tmp_path, bundle, scratch, project)
        )

    def test_non_utf8_reference_rejected_binary_asset_accepted(self, tmp_path):
        bundle, scratch = make_bundle(
            tmp_path,
            refs={"bad.md": b"\xff\xfe\x00binary"},
            assets={"fine.png": b"\xff\xfe\x00binary"},
        )
        project = make_project(tmp_path)
        errors = validate(tmp_path, bundle, scratch, project)
        assert any("not UTF-8" in e and "bad.md" in e for e in errors)
        assert not any("fine.png" in e for e in errors)

    def test_size_caps(self, tmp_path):
        bundle, scratch = make_bundle(
            tmp_path, refs={"big.md": "a" * (MAX_FILE_BYTES + 1)}
        )
        project = make_project(tmp_path)
        assert any(
            "file too large" in e for e in validate(tmp_path, bundle, scratch, project)
        )
        bundle2, scratch2 = make_bundle(
            tmp_path,
            assets={
                f"blob{i}.bin": b"a" * (MAX_FILE_BYTES - 1)
                for i in range(MAX_BUNDLE_BYTES // MAX_FILE_BYTES + 2)
            },
        )
        assert any(
            "bundle too large" in e
            for e in validate(tmp_path, bundle2, scratch2, project)
        )

    def test_provenance_args(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path)
        project = make_project(tmp_path)
        errors = validate(tmp_path, bundle, scratch, project, notes="  ")
        assert any("provenance_notes" in e for e in errors)
        errors = validate(tmp_path, bundle, scratch, project, derived=["ok", " "])
        assert any("derived_from_ids" in e for e in errors)

    def test_all_errors_reported_at_once(self, tmp_path):
        bundle, scratch = make_bundle(
            tmp_path,
            frontmatter="---\nname: Bad Name\ndescription: d\n---\n\n",
            body="",
            refs={"bad.md": b"\xff\xfe"},
        )
        (bundle / "stray.txt").write_text("x", encoding="utf-8")
        project = make_project(tmp_path)
        errors = validate(tmp_path, bundle, scratch, project, notes="")
        # One call surfaces every defect class — no ping-pong.
        assert len(errors) >= 4
        joined = "\n".join(errors)
        for needle in ("stray.txt", "not UTF-8", "provenance_notes", "non-empty"):
            assert needle in joined


class TestInstall:
    def install(self, tmp_path, **kwargs):
        bundle, scratch = make_bundle(
            tmp_path,
            refs={"guide.md": "guide\n", "policies/deep.md": "deep\n"},
            **kwargs,
        )
        project = make_project(tmp_path)
        result = install_bundle(bundle, scratch, project, "notes", ["424242451835"])
        return result, project, bundle

    def test_a_skill_md_with_windows_line_endings_installs(self, tmp_path):
        # Written on Windows (an editor, the agent's Write tool, Python's
        # text mode), SKILL.md has CRLF lines. Kiln reads it in text mode.
        bundle, scratch = make_bundle(tmp_path)
        crlf = (
            b"---\r\nname: fresh-skill\r\ndescription: A fresh skill.\r\n---\r\n"
            b"\r\nUse the thing.\r\n"
        )
        (bundle / "SKILL.md").write_bytes(crlf)
        project = make_project(tmp_path)
        assert validate(tmp_path, bundle, scratch, project) == []
        result = install_bundle(bundle, scratch, project, "notes", [])
        assert result["ok"] is True, result
        dest = project / "skills" / f"{result['skill_id']} - fresh-skill"
        assert (dest / "SKILL.md").read_bytes() == crlf  # installed as written
        sidecar = json.loads((dest / "skill.kiln").read_text(encoding="utf-8"))
        assert (sidecar["name"], sidecar["description"]) == (
            "fresh-skill",
            "A fresh skill.",
        )

    def test_installed_tree_and_sidecar(self, tmp_path):
        result, project, bundle_dir = self.install(tmp_path)
        assert result["ok"] is True
        skill_id = result["skill_id"]
        assert len(skill_id) == 12 and skill_id.isdigit()
        assert result["skill_name"] == "fresh-skill"
        assert result["tool_id"] == f"kiln_tool::skill::{skill_id}"
        dest = project / "skills" / f"{skill_id} - fresh-skill"
        assert dest.is_dir()
        # SKILL.md byte-identical; nested refs land nested; empty assets/
        # created anyway (Kiln save_skill_md parity).
        assert (dest / "SKILL.md").read_bytes() == (
            bundle_dir / "SKILL.md"
        ).read_bytes()
        assert (dest / "references" / "policies" / "deep.md").read_text(
            encoding="utf-8"
        ) == "deep\n"
        assert (dest / "assets").is_dir()
        sidecar = json.loads((dest / "skill.kiln").read_text(encoding="utf-8"))
        assert sidecar == {
            "v": 1,
            "id": skill_id,
            "created_at": sidecar["created_at"],
            "created_by": getpass.getuser(),
            "name": "fresh-skill",
            "description": "A fresh skill.",
            "is_archived": False,
            "provenance": {
                "notes": "notes",
                "derived_from_ids": ["424242451835"],
                "origin": "agent",
            },
            "model_type": "skill",
        }
        # created_at is tz-aware ISO-8601 (Kiln: now().astimezone()).
        assert datetime.fromisoformat(sidecar["created_at"]).tzinfo is not None
        # No staging debris after a successful install.
        assert not list((project / "skills").glob(".staging-*"))

    def test_rejection_writes_nothing(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path, name="existing-skill")
        project = make_project(tmp_path, existing_name="existing-skill")
        before = sorted(p.name for p in (project / "skills").iterdir())
        result = install_bundle(bundle, scratch, project, "notes", [])
        assert result["ok"] is False and result["errors"]
        assert sorted(p.name for p in (project / "skills").iterdir()) == before

    def test_id_collision_retries(self, tmp_path, monkeypatch):
        bundle, scratch = make_bundle(tmp_path)
        project = make_project(tmp_path, existing_id="424242111111")

        class FakeUuid:
            def __init__(self, value):
                self.int = value

        # First mint collides with the existing skill's id; second is fresh.
        values = iter([424242111111_9, 424242222222_9])
        monkeypatch.setattr(b.uuid, "uuid4", lambda: FakeUuid(next(values)))
        result = install_bundle(bundle, scratch, project, "notes", [])
        assert result["ok"] is True
        assert result["skill_id"] == "424242222222"

    def test_failure_after_staging_cleans_up(self, tmp_path, monkeypatch):
        bundle, scratch = make_bundle(tmp_path)
        project = make_project(tmp_path)
        monkeypatch.setattr(
            b.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
        )
        result = install_bundle(bundle, scratch, project, "notes", [])
        assert result["ok"] is False
        assert any("install failed" in e for e in result["errors"])
        assert not list((project / "skills").glob(".staging-*"))
        assert not list((project / "skills").glob("* - fresh-skill"))


class TestSweep:
    def test_sweeps_staging_only(self, tmp_path):
        project = make_project(tmp_path)
        staging = project / "skills" / ".staging-424242999999"
        staging.mkdir()
        (staging / "junk.txt").write_text("x", encoding="utf-8")
        assert sweep_staging(project) == 1
        assert not staging.exists()
        assert (project / "skills" / "424242111111 - existing-skill").is_dir()

    def test_tolerates_missing_skills_dir(self, tmp_path):
        assert sweep_staging(tmp_path / "nowhere") == 0


def _tool_payload(result):
    """The JSON body of a `FastMCP.call_tool` result.

    mcp 1.x hands back the content sequence itself; 2.0 wraps it in an object
    carrying `.content`. This repository pins `mcp<2`, because `kiln-ai` — a
    dependency of the vendored `kiln_api_mcp` package — still imports
    `mcp.client.streamable_http.streamablehttp_client`, which 2.0 dropped.
    """
    return json.loads(result[0].text)


class TestServer:
    async def test_registers_exactly_the_install_and_export_tools(self, tmp_path):
        server = build_server(tmp_path / "proj", tmp_path / "scratch")
        tools = await server.list_tools()
        assert sorted(t.name for t in tools) == [
            "export_skill_bundle",
            "install_skill_bundle",
        ]

    async def test_tool_rejects_outside_scratch_in_band(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path)
        project = make_project(tmp_path)
        server = build_server(project, scratch)
        out = await server.call_tool(
            "install_skill_bundle",
            {
                "bundle_path": str(project),
                "provenance_notes": "n",
                "derived_from_ids": [],
            },
        )
        payload = _tool_payload(out)
        assert payload["ok"] is False
        assert any("scratch" in e for e in payload["errors"])

    async def test_tool_installs_end_to_end(self, tmp_path):
        bundle, scratch = make_bundle(tmp_path, refs={"guide.md": "g\n"})
        project = make_project(tmp_path)
        server = build_server(project, scratch)
        out = await server.call_tool(
            "install_skill_bundle",
            {
                "bundle_path": str(bundle),
                "provenance_notes": "hypothesis",
                "derived_from_ids": [],
            },
        )
        payload = _tool_payload(out)
        assert payload["ok"] is True
        dest = project / "skills" / f"{payload['skill_id']} - fresh-skill"
        assert (dest / "references" / "guide.md").read_text(encoding="utf-8") == "g\n"

    def test_main_requires_flags(self, monkeypatch, capsys):
        import pytest

        from kiln_harness_tuner.skill_installer.mcp_server import main

        monkeypatch.setattr("sys.argv", ["kiln-harness-tuner-skill-installer-mcp"])
        with pytest.raises(SystemExit):
            main()

    def test_main_rejects_missing_project_dir(self, monkeypatch, tmp_path):
        from kiln_harness_tuner.skill_installer.mcp_server import main

        monkeypatch.setattr(
            "sys.argv",
            [
                "kiln-harness-tuner-skill-installer-mcp",
                "--project-path",
                str(tmp_path / "nope"),
                "--scratch-root",
                str(tmp_path),
            ],
        )
        assert main() == 2


class TestExport:
    """`export_bundle`: with the project tree read-denied, this
    is how a builder reads a skill it clones — a copy into its own scratch,
    never `skill.kiln`, never a path outside the scratch."""

    def _project(self, tmp_path):
        from kiln_harness_tuner.skill_installer.bundle import export_bundle

        project = make_project(tmp_path)
        skill = project / "skills" / "424242111111 - existing-skill"
        (skill / "references" / "deep").mkdir(parents=True)
        (skill / "references" / "deep" / "a.md").write_text("ref", encoding="utf-8")
        (tmp_path / "scratch").mkdir()
        return export_bundle, project

    def test_copies_the_bundle_without_the_identity_file(self, tmp_path):
        export_bundle, project = self._project(tmp_path)
        dest = (tmp_path / "scratch" / "copy").resolve()
        result = export_bundle("424242111111", dest, tmp_path / "scratch", project)
        assert result["ok"] is True
        assert (dest / "SKILL.md").is_file()
        assert (dest / "references" / "deep" / "a.md").read_text(
            encoding="utf-8"
        ) == "ref"
        assert not (dest / "skill.kiln").exists()

    @pytest.mark.parametrize(
        "dest", ["outside/copy", "scratch", "scratch/exists", "relative"]
    )
    def test_refuses_a_destination_that_is_not_new_inside_scratch(self, tmp_path, dest):
        export_bundle, project = self._project(tmp_path)
        (tmp_path / "scratch" / "exists").mkdir()
        path = tmp_path / dest if dest != "relative" else Path("scratch/copy")
        result = export_bundle("424242111111", path, tmp_path / "scratch", project)
        assert result["ok"] is False

    def test_refuses_an_unknown_or_malformed_id(self, tmp_path):
        export_bundle, project = self._project(tmp_path)
        dest = (tmp_path / "scratch" / "copy").resolve()
        for skill_id in ("424242999999", "../424242111111", "*"):
            result = export_bundle(skill_id, dest, tmp_path / "scratch", project)
            assert result["ok"] is False, skill_id

    @requires_symlinks
    def test_refuses_a_symlink_inside_the_skill(self, tmp_path):
        export_bundle, project = self._project(tmp_path)
        skill = project / "skills" / "424242111111 - existing-skill"
        (tmp_path / "secret.txt").write_text("val data", encoding="utf-8")
        (skill / "references" / "link.md").symlink_to(tmp_path / "secret.txt")
        dest = (tmp_path / "scratch" / "copy").resolve()
        result = export_bundle("424242111111", dest, tmp_path / "scratch", project)
        assert result["ok"] is False and not dest.exists()
