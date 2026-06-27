#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — audit changelog and release-note claim hygiene
"""Check changelog and release-note prose for forbidden public claims.

The changelog is a public release surface, so it should record concrete changes
without authorship-by-agent claims, self-awarded quality labels, or unsupported
certification/conformance claims. Factual third-party product names remain
allowed when they describe compatibility or integration context; this checker
targets identity and claim phrasing rather than every provider or tool token.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from re import Pattern

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = (REPO_ROOT / "CHANGELOG.md",)


@dataclass(frozen=True)
class ClaimPattern:
    """One forbidden release-note claim pattern."""

    category: str
    regex: Pattern[str]


@dataclass(frozen=True)
class Finding:
    """One release-note hygiene finding."""

    path: Path
    line_number: int
    category: str
    snippet: str

    def format(self) -> str:
        """Render the finding as a stable diagnostic line."""
        return f"{self.path}:{self.line_number}: {self.category}: {self.snippet}"


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for the release-claim hygiene checker."""

    paths: tuple[Path, ...]
    check: bool


CLAIM_PATTERNS = (
    ClaimPattern(
        category="agent-authorship",
        regex=re.compile(
            r"\b(?:authored|generated|written|implemented|produced|created)\s+by\s+"
            r"(?:Codex|OpenAI|ChatGPT|Claude(?: Code)?|Gemini|Kimi|Grok|Aider)\b",
            re.IGNORECASE,
        ),
    ),
    ClaimPattern(
        category="quality-label",
        regex=re.compile(
            r"\b(?:enterprise|industrial|production)[ -]?grade\b"
            r"|\belite\b"
            r"|\bsuperior\b"
            r"|\bworld[- ]class\b"
            r"|\bbest[- ]in[- ]class\b",
            re.IGNORECASE,
        ),
    ),
    ClaimPattern(
        category="conformance-overclaim",
        regex=re.compile(
            r"\bofficial(?:ly)?\s+certified\b"
            r"|\bexternally\s+certified\b"
            r"|\bfull\s+(?:A2A|MCP)\s+conformance\b"
            r"|\bguarantees?\s+(?:A2A|MCP)\s+conformance\b",
            re.IGNORECASE,
        ),
    ),
)
"""Forbidden claim patterns enforced on public release prose."""


def scan_path(path: Path) -> tuple[Finding, ...]:
    """Return claim-hygiene findings for one markdown release surface."""
    findings: list[Finding] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for claim_pattern in CLAIM_PATTERNS:
            if claim_pattern.regex.search(line):
                findings.append(
                    Finding(
                        path=path,
                        line_number=index,
                        category=claim_pattern.category,
                        snippet=line.strip(),
                    )
                )
    return tuple(findings)


def scan_paths(paths: Sequence[Path]) -> tuple[Finding, ...]:
    """Return claim-hygiene findings for all requested release surfaces."""
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_path(path))
    return tuple(findings)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse CLI arguments for the release-claim hygiene checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        default=[],
        help="Changelog or release-note markdown path to scan; repeatable.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check-only mode; kept for consistency with other repository guards.",
    )
    namespace = parser.parse_args(argv)
    paths = tuple(namespace.path) if namespace.path else DEFAULT_PATHS
    return CliArgs(paths=paths, check=namespace.check)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the release-claim hygiene checker and return a process exit code."""
    args = parse_args(argv)
    _ = args.check
    findings = scan_paths(args.paths)
    if findings:
        for finding in findings:
            print(finding.format(), file=sys.stderr)
        return 1

    print(f"release claim hygiene passed: {len(args.paths)} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
