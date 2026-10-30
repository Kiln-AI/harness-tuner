"""The prompt and skill text a run config gives the subject model, read by
code (`kiln_harness_tuner.prompt_text`) — what the generality check and the integrity
checks compare."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_ctx

from kiln_harness_tuner.prompt_text import prompt_strings, rc_texts, read_skill

PROMPT = "You answer depot questions. Be concise. Use the depot-api skill."
SKILL_LINES = ("Call the API once per question.", "Retry once on a 429.")
REFERENCE = "Code 7 means a held pallet.\nCode 9 means a lost pallet.\n"


def skill_md(name: str, *lines: str) -> str:
    head = ["---", f"name: {name}", "description: How to call the API.", "---"]
    return "\n".join([*head, "", *lines, ""])


def install_skill(
    project: Path,
    skill_id: str,
    name: str,
    skill_text: str,
    references: dict[str, str | bytes] | None = None,
    derived_from: tuple[str, ...] = (),
) -> None:
    """One installed skill on disk, in the layout the skill installer writes."""
    directory = project / "skills" / f"{skill_id} - {name}"
    (directory / "references").mkdir(parents=True)
    (directory / "SKILL.md").write_text(skill_text, encoding="utf-8")
    for path, text in (references or {}).items():
        target = directory / "references" / path
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text, encoding="utf-8")
    sidecar = {
        "id": skill_id,
        "name": name,
        "provenance": {"derived_from_ids": list(derived_from)},
    }
    (directory / "skill.kiln").write_text(json.dumps(sidecar), encoding="utf-8")


def skill_rc(rc_id: str, prompt: str, *skill_ids: str) -> dict:
    tools = [f"kiln_tool::skill::{i}" for i in skill_ids]
    return {
        "id": rc_id,
        "name": rc_id,
        "prompt": {"prompt": prompt},
        "run_config_properties": {"tools_config": {"tools": tools}},
    }


def on_disk(ctx, project: Path) -> None:
    ctx.target = ctx.target.model_copy(update={"project_path": str(project)})


def _skill_ctx(project: Path, *run_configs: dict):
    ctx = make_ctx()
    on_disk(ctx, project)
    install_skill(
        project,
        "sk1",
        "depot-api",
        skill_md("depot-api", *SKILL_LINES),
        {"codes.md": REFERENCE},
    )
    ctx.kiln.run_configs = [skill_rc("rc_base", PROMPT, "sk1"), *run_configs]
    return ctx


def test_a_frozen_prompt_is_read_by_its_text_not_its_name():
    # A frozen prompt dict carries name, description and generator_id: only
    # `prompt` and `chain_of_thought_instructions` are what the model sees.
    frozen = {
        "name": "Baseline prompt",
        "description": "frozen at setup",
        "generator_id": "simple_prompt_builder",
        "prompt": PROMPT,
        "chain_of_thought_instructions": "Think first.",
    }
    assert prompt_strings({"prompt": frozen}) == [PROMPT, "Think first."]


class TestSkillText:
    async def test_a_run_config_reads_its_skills_from_disk(self, tmp_path):
        ctx = _skill_ctx(tmp_path)
        texts = await rc_texts(ctx, ["rc_base", "rc_missing"])
        base = texts["rc_base"]
        assert base.prompt == PROMPT
        assert base.skill_ids == ("sk1",)
        assert base.skills is not None
        skill = base.skills["sk1"]
        assert skill.name == "depot-api"
        assert skill.files == {
            "SKILL.md": skill_md("depot-api", *SKILL_LINES),
            "references/codes.md": REFERENCE,
        }
        missing = texts["rc_missing"]
        assert missing.prompt is None and missing.skills is None

    async def test_a_skill_not_on_disk_makes_the_skills_unreadable(self, tmp_path):
        ctx = _skill_ctx(tmp_path, skill_rc("rc_two", PROMPT, "sk1", "sk_gone"))
        texts = await rc_texts(ctx, ["rc_base", "rc_two"])
        assert texts["rc_two"].skill_ids == ("sk1", "sk_gone")
        assert texts["rc_two"].skills is None
        assert texts["rc_two"].prompt == PROMPT  # the prompt still reads
        assert texts["rc_base"].skills is not None

    def test_a_reference_that_is_not_text_makes_the_skill_unreadable(self, tmp_path):
        install_skill(tmp_path, "sk9", "binary", skill_md("binary"), {"x.md": b"\xff"})
        assert read_skill(tmp_path, "sk9") is None
        assert read_skill(tmp_path, "sk_none") is None

    def test_text_assets_are_read_and_binary_assets_left_out(self, tmp_path):
        install_skill(tmp_path, "sk4", "assets", skill_md("assets"))
        assets = tmp_path / "skills" / "sk4 - assets" / "assets"
        (assets / "img").mkdir(parents=True)
        (assets / "template.txt").write_text("Dear {name},", encoding="utf-8")
        (assets / "img" / "logo.png").write_bytes(b"\x89PNG\xff")
        skill = read_skill(tmp_path, "sk4")
        assert skill is not None
        assert set(skill.files) == {"SKILL.md", "assets/template.txt"}

    def test_an_asset_over_the_size_cap_is_left_out(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kiln_harness_tuner.prompt_text.ASSET_MAX_BYTES", 10)
        install_skill(tmp_path, "sk4", "assets", skill_md("assets"))
        assets = tmp_path / "skills" / "sk4 - assets" / "assets"
        assets.mkdir()
        (assets / "small.txt").write_text("short", encoding="utf-8")
        (assets / "big.txt").write_text("x" * 11, encoding="utf-8")
        skill = read_skill(tmp_path, "sk4")
        assert skill is not None
        assert set(skill.files) == {"SKILL.md", "assets/small.txt"}

    def test_a_skills_tree_that_cannot_be_listed_is_unreadable(
        self, tmp_path, monkeypatch
    ):
        def denied(*_a, **_k):
            raise PermissionError("denied")

        monkeypatch.setattr(
            "kiln_harness_tuner.prompt_text.installed_skill_dir", denied
        )
        assert read_skill(tmp_path, "sk1") is None

    def test_a_skill_without_a_sidecar_takes_its_name_from_the_directory(
        self, tmp_path
    ):
        install_skill(tmp_path, "sk3", "depot-api", skill_md("depot-api"))
        (tmp_path / "skills" / "sk3 - depot-api" / "skill.kiln").unlink()
        skill = read_skill(tmp_path, "sk3")
        assert skill is not None
        assert skill.name == "depot-api" and skill.derived_from == ()
