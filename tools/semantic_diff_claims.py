#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tree-sitter Git-diff semantic claim CLI
"""Infer conservative function-level claim paths from a local Git diff."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from synapse_channel.git import semantic_claims, semantic_diff  # noqa: E402


@dataclass(frozen=True)
class CliArgs:
    """Parsed semantic-diff command-line arguments."""

    repo_root: Path
    base: str
    head: str | None
    paths: tuple[str, ...]
    json_output: bool
    claim_args: bool
    check: bool


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True, help="Base Git revision for the diff.")
    parser.add_argument(
        "--head",
        default=None,
        help="Optional head revision; omit to compare the base with the working tree.",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Limit inference to a repository-relative path; repeatable.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit receipt-ready JSON evidence.")
    output.add_argument(
        "--claim-args",
        action="store_true",
        help="Emit only --paths arguments for synapse git-claim.",
    )
    parser.add_argument("--check", action="store_true", help="Print a pass diagnostic.")
    namespace = parser.parse_args(argv)
    return CliArgs(
        repo_root=namespace.repo_root,
        base=str(namespace.base),
        head=None if namespace.head is None else str(namespace.head),
        paths=tuple(str(path) for path in namespace.path),
        json_output=bool(namespace.json),
        claim_args=bool(namespace.claim_args),
        check=bool(namespace.check),
    )


def combined_claim_paths(
    repo_root: Path,
    records: Sequence[semantic_diff.SemanticDiffRecord],
) -> tuple[str, ...]:
    """Return inferred source scopes plus owning tests and generated outputs."""
    sources = tuple(record.source for record in records)
    paths = (
        *(path for record in records for path in record.claim_paths),
        *semantic_claims.companion_claim_paths(repo_root, sources),
    )
    return tuple(dict.fromkeys(paths))


def evidence_document(
    *,
    base: str,
    head: str | None,
    records: Sequence[semantic_diff.SemanticDiffRecord],
    claim_paths: Sequence[str],
) -> dict[str, object]:
    """Return stable receipt-ready evidence for one diff resolution."""
    return {
        "base": base,
        "head": head,
        "records": semantic_diff.records_to_json(records),
        "claim_paths": list(claim_paths),
        "note": ("tree-sitter evidence; every incomplete mapping widens to a whole-file claim"),
    }


def render_human(records: Sequence[semantic_diff.SemanticDiffRecord]) -> str:
    """Render semantic versus widened decisions as compact text."""
    lines: list[str] = []
    for record in records:
        label = "symbols=" + ",".join(record.symbols) if record.narrowed else "whole-file"
        lines.append(f"{record.status} {record.source}: {label} ({record.reason})")
    return "\n".join(lines)


def render_claim_args(paths: Sequence[str]) -> str:
    """Render claim paths as shell-quoted ``--paths`` arguments."""
    parts: list[str] = []
    for path in paths:
        parts.extend(("--paths", path))
    return " ".join(shlex.quote(part) for part in parts)


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve one diff and return a shell-friendly exit code."""
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        records = semantic_diff.resolve_git_diff(
            repo_root,
            base=args.base,
            head=args.head,
            paths=args.paths,
        )
        claim_paths = combined_claim_paths(repo_root, records)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"semantic diff claim error: {exc}", file=sys.stderr)
        return 2

    if args.claim_args:
        print(render_claim_args(claim_paths))
    elif args.json_output:
        print(
            json.dumps(
                evidence_document(
                    base=args.base,
                    head=args.head,
                    records=records,
                    claim_paths=claim_paths,
                ),
                indent=2,
                sort_keys=True,
            )
        )
    else:
        rendered = render_human(records)
        if rendered:
            print(rendered)

    if args.check:
        narrowed = sum(record.narrowed for record in records)
        print(
            "semantic diff claim resolution passed: "
            f"{len(records)} file(s), {narrowed} narrowed, "
            f"{len(records) - narrowed} whole-file"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
