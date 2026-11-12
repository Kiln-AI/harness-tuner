"""The command line of the knowledge system.

`check` prints one line for each problem and exits 1 when there is one.
`build` rewrites the generated blocks of the entry files, then checks.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from kiln_harness_tuner.knowledge.check import check
from kiln_harness_tuner.knowledge.index import write_blocks
from kiln_harness_tuner.knowledge.load import KnowledgeError, load
from kiln_harness_tuner.knowledge.schema import Problem


def code_side() -> tuple[frozenset[str], Mapping[str, str]]:
    """The gateway operations and the detector registry (detector id to
    smell id) that the check holds the documents to."""
    from kiln_harness_tuner.gateway.ops import OPS

    # No smell detector exists yet. The `smells` package and its
    # `detector_smells()` come in a later phase of the biological_bootloader
    # project, and its registry is passed here then.
    return frozenset(OPS), {}


def run_check(root: Path | None) -> list[Problem]:
    gateway_ops, detectors = code_side()
    return check(load(root), gateway_ops=gateway_ops, detectors=detectors)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kiln_harness_tuner.knowledge",
        description="Check the knowledge skill, or rebuild its indexes.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("check", "report each broken rule; exit 1 when there is one"),
        ("build", "rewrite the generated blocks of the entry files, then check"),
    ):
        command = commands.add_parser(name, help=text, description=text)
        command.add_argument(
            "--root",
            type=Path,
            default=None,
            help="the skill folder to read (default: the package's own)",
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            for path in write_blocks(load(args.root)):
                print(f"rewrote {path}")
        problems = run_check(args.root)
    except KnowledgeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(f"{problem.rule}  {problem.path}  {problem.message}")
    if problems:
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("knowledge: no problem")
    return 0


if __name__ == "__main__":
    sys.exit(main())
