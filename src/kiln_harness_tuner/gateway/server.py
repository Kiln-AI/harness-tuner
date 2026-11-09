"""The loop gateway as an MCP server.

One process per agent session, started by Claude Code from the run's
per-role MCP config, OUTSIDE the agent sandbox. It serves the operations of
ONE role (`ops.ROLE_OPS[role]`) as typed tools and nothing else; it reads
the run's train set and model policy from the run directory, which agents
cannot read.

    kiln-harness-tuner-gateway-mcp --role batch_plan --run-dir runs/<run_id>
        --kiln-host http://localhost:8757 --project-id <id> --task-id <id>
        --project-path <path> [--max-concurrent-evals <n>]
        [--traces-dir runs/<run_id>/scratch/traces]

`--traces-dir` is where it writes the train traces it reads,
so the agent reads them as files and not in an answer (`trace_files`).
Without it, the run's `scratch/traces/`.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP

from kiln_harness_tuner.gateway import ops as gw
from kiln_harness_tuner.gateway.trainset import FILE_NAME, TrainSet
from kiln_harness_tuner.model_policy import ModelPolicy, ModelPolicyError

POLICY_FILE = "model_policy.json"


def load_policy(run_dir: Path) -> ModelPolicy | None:
    path = run_dir / POLICY_FILE
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return None
    try:
        return ModelPolicy.load(path)
    except ModelPolicyError as exc:  # fail closed: every model refused
        return ModelPolicy.from_json({"allow": []}, source=f"unreadable policy ({exc})")


def load_state(args: argparse.Namespace) -> gw.State:
    run_dir = Path(args.run_dir)
    state = gw.State(
        project_id=args.project_id,
        task_id=args.task_id,
        role=args.role,
        train=TrainSet.load(run_dir / FILE_NAME),
        run_dir=run_dir,
        project_path=Path(args.project_path) if args.project_path else None,
        policy=load_policy(run_dir),
        max_concurrent_evals=getattr(args, "max_concurrent_evals", None) or None,
        traces_dir=Path(args.traces_dir) if getattr(args, "traces_dir", None) else None,
    )
    state.load_created()
    return state


def _tool(gateway: gw.Gateway, role: str, op: gw.Op):
    """An async function whose SIGNATURE is the operation's request model,
    field by field, so the tool's JSON schema carries every type and bound."""

    async def call(**arguments: Any) -> str:
        try:
            result = await gw.dispatch(gateway, role, op.name, arguments)
        except gw.GatewayError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:  # a validation or Kiln error, as text
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"[:2000]})
        return json.dumps(result)

    params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[field.annotation, field],
            default=inspect.Parameter.empty if field.is_required() else field.default,
        )
        for name, field in op.request.model_fields.items()
    ]
    call.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    call.__name__ = op.name
    call.__doc__ = op.summary
    return call


def create_server(gateway: gw.Gateway, role: str) -> FastMCP:
    mcp = FastMCP("Kiln Harness Tuner gateway")
    for name in gw.ROLE_OPS.get(role, ()):
        op = gw.OPS[name]
        # Text only. A `str` tool is structured by default, and FastMCP then
        # also sends `{"result": <the same JSON as a string>}`. A session can
        # read that wrapper instead of the text: the answer arrives encoded
        # twice, and preflight saw the wrapper in place of `{"ok": true}`.
        mcp.add_tool(
            _tool(gateway, role, op),
            name=name,
            description=op.summary,
            structured_output=False,
        )
    return mcp


def main(argv: list[str] | None = None) -> None:
    from kiln_harness_tuner.kiln import KilnClient

    parser = argparse.ArgumentParser(prog="kiln-harness-tuner-gateway-mcp")
    parser.add_argument("--role", required=True, choices=sorted(gw.ROLE_OPS))
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--kiln-host", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--project-path", default=None)
    parser.add_argument("--max-concurrent-evals", type=int, default=0)
    parser.add_argument("--traces-dir", default=None)
    args = parser.parse_args(argv)
    client = KilnClient(args.kiln_host)
    gateway = gw.Gateway(client.call, load_state(args))
    create_server(gateway, args.role).run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
