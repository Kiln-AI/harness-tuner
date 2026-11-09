"""The skill_installer stdio MCP server.

Spawned per `claude -p` session by the CLI from the rendered per-run
`mcp_config.json` (see `cli.render_run_configs`). It runs OUTSIDE the agent's
sandbox with the operator's normal permissions — that asymmetry is the whole
design: the agent's own tools cannot write to the Kiln project tree (denied
by settings), so the only lane into `skills/` is this server, and this server
only ever installs a validated, complete bundle atomically (bundle.py).

Trust split: the agent controls WHEN install happens and WHAT content it
proposes; this code alone decides what is accepted and performs every write
outside the scratch tree. `--scratch-root` bounds what the server will read,
`--project-path` fixes where it writes — both are rendered by trusted code,
never supplied by the agent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# `FastMCP`, not mcp 2.0's `MCPServer`: this repository pins `mcp<2` because
# `kiln-ai` — a dependency of the vendored `kiln_api_mcp` package — still
# imports `mcp.client.streamable_http.streamablehttp_client`, which 2.0
# dropped. 2.0 renamed this exact class (`mcp.server.fastmcp.FastMCP` ->
# `mcp.server.MCPServer`); the constructor, `.tool()`, `.list_tools()` and
# `.run()` are unchanged.
from mcp.server.fastmcp import FastMCP

from kiln_harness_tuner.skill_installer.bundle import export_bundle, install_bundle


def build_server(project_path: Path, scratch_root: Path) -> FastMCP:
    mcp = FastMCP("skill_installer")

    @mcp.tool()
    def install_skill_bundle(
        bundle_path: str, provenance_notes: str, derived_from_ids: list[str]
    ) -> dict:
        """Atomically install a complete Kiln skill bundle you authored in your
        scratch dir. The bundle directory must contain exactly: `SKILL.md` at
        its root (frontmatter with exactly `name` + `description` — the single
        source for both; kebab-case name, description <=1024 chars, non-empty
        body), plus optional `references/` and `assets/` directories (nested
        subdirs fine; references must be UTF-8 text; no symlinks; do NOT write
        a skill.kiln — the installer creates it). `provenance_notes` = your
        hypothesis + evidence; `derived_from_ids` = parent artifact ids (e.g.
        the skill you forked, the parent rc) or [].

        Returns `{"ok": true, "skill_id", "skill_name", "tool_id"}` — wire
        `tool_id` into your new run config. On rejection returns
        `{"ok": false, "errors": [...]}` listing EVERY problem at once and
        writing nothing: fix the bundle in scratch and call again. Installed
        skills are immutable — a new version means a new bundle with a new
        name."""
        return install_bundle(
            Path(bundle_path),
            scratch_root,
            project_path,
            provenance_notes,
            derived_from_ids,
        )

    @mcp.tool()
    def export_skill_bundle(skill_id: str, dest_path: str) -> dict:
        """Copy an installed project skill's files — `SKILL.md`, `references/`
        and `assets/`, never `skill.kiln` — into a NEW directory in your
        scratch dir, so you can read or clone them. The project tree itself
        is not readable in your session. `dest_path` = an absolute path
        inside your scratch dir that does not exist yet.

        Returns `{"ok": true, "path", "files"}` or `{"ok": false, "errors"}`."""
        return export_bundle(skill_id, Path(dest_path), scratch_root, project_path)

    return mcp


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="harness-tuner-skill-installer-mcp",
        description="Kiln Harness Tuner skill-bundle installer MCP server (stdio)",
    )
    parser.add_argument(
        "--project-path",
        required=True,
        help="Kiln project directory (writes land in <project>/skills/)",
    )
    parser.add_argument(
        "--scratch-root",
        required=True,
        help="run scratch root — the only tree bundles are read from",
    )
    args = parser.parse_args()
    project_path = Path(args.project_path).expanduser().resolve()
    scratch_root = Path(args.scratch_root).expanduser().resolve()
    if not project_path.is_dir():
        print(f"--project-path is not a directory: {project_path}", file=sys.stderr)
        return 2
    build_server(project_path, scratch_root).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
