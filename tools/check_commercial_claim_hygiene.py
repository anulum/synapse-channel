#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — audit commercial documentation claim hygiene
"""Check commercial documentation for licensing-boundary claim drift.

SYNAPSE CHANNEL is dual-licensed: the public package is the full product, and a
commercial licence changes usage terms rather than unlocking extra code. This
checker keeps that boundary visible across the commercial documentation and
fails on wording that implies paid code paths not present in the repository.
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
DEFAULT_PATHS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "commercial.md",
    REPO_ROOT / "COMMERCIAL-LICENSE.md",
    REPO_ROOT / "NOTICE.md",
    REPO_ROOT / "SUPPORT.md",
    REPO_ROOT / "docs" / "faq.md",
    REPO_ROOT / "docs" / "index.md",
)
KEY_BOUNDARY_FILES = {
    "README.md",
    "docs/commercial.md",
    "COMMERCIAL-LICENSE.md",
}
KEY_BOUNDARY_FILENAMES = {
    "commercial.md",
    "COMMERCIAL-LICENSE.md",
}
REQUIRED_KEY_BOUNDARIES = (
    "no feature difference",
    "commercial",
    "terms",
    "not the code",
)
REQUIRED_LICENSE_TERMS = (
    "AGPL",
    "commercial licence",
)


@dataclass(frozen=True)
class ForbiddenPattern:
    """One forbidden commercial-documentation claim pattern."""

    category: str
    regex: Pattern[str]


@dataclass(frozen=True)
class Finding:
    """One commercial-claim hygiene finding."""

    path: Path
    line_number: int
    category: str
    snippet: str

    def format(self) -> str:
        """Render the finding as a stable diagnostic line."""
        return f"{self.path}:{self.line_number}: {self.category}: {self.snippet}"


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for the commercial claim checker."""

    paths: tuple[Path, ...]
    check: bool


FORBIDDEN_PATTERNS = (
    ForbiddenPattern(
        category="feature-split-claim",
        regex=re.compile(
            r"\b(?:commercial|paid|enterprise|managed)[ -]?only\s+features?\b"
            r"|\bcommercial\s+build\s+(?:adds|includes|unlocks|contains)\b"
            r"|\b(?:features?|capabilities)\s+(?:are\s+)?(?:reserved|locked|exclusive)\s+for\b"
            r"|\bupgrade\s+to\s+unlock\b"
            r"|\bAGPL\s+(?:version|build)\s+(?:lacks|omits|excludes)\b",
            re.IGNORECASE,
        ),
    ),
    ForbiddenPattern(
        category="proprietary-split-claim",
        regex=re.compile(
            r"\bopen[- ]core\b"
            r"|\bproprietary\s+(?:edition|build|features?)\b"
            r"|\bclosed[- ]source\s+(?:edition|build)\b",
            re.IGNORECASE,
        ),
    ),
)
"""Forbidden commercial claim patterns for public docs."""


def _normalise(text: str) -> str:
    """Return lowercase prose with markdown emphasis removed."""
    return re.sub(r"[*_`]+", "", text).lower()


def _relative_display(path: Path) -> str:
    """Return a repository-relative display path when possible."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _boundary_findings(path: Path, text: str) -> tuple[Finding, ...]:
    """Return missing-boundary findings for one documentation file."""
    display = _relative_display(path)
    normalised = _normalise(text)
    findings: list[Finding] = []

    if display in KEY_BOUNDARY_FILES or path.name in KEY_BOUNDARY_FILENAMES:
        for phrase in REQUIRED_KEY_BOUNDARIES:
            if phrase not in normalised:
                findings.append(
                    Finding(
                        path=path,
                        line_number=1,
                        category="missing-boundary",
                        snippet=f"missing required commercial boundary phrase: {phrase}",
                    )
                )

    if any(token in normalised for token in ("commercial licence", "commercial license")):
        for term in REQUIRED_LICENSE_TERMS:
            if term.lower() not in normalised:
                findings.append(
                    Finding(
                        path=path,
                        line_number=1,
                        category="missing-boundary",
                        snippet=f"missing required licensing term near commercial prose: {term}",
                    )
                )

    return tuple(findings)


def scan_path(path: Path) -> tuple[Finding, ...]:
    """Return commercial-claim findings for one documentation path."""
    text = path.read_text(encoding="utf-8")
    findings = list(_boundary_findings(path, text))
    for index, line in enumerate(text.splitlines(), start=1):
        for forbidden in FORBIDDEN_PATTERNS:
            if forbidden.regex.search(line):
                findings.append(
                    Finding(
                        path=path,
                        line_number=index,
                        category=forbidden.category,
                        snippet=line.strip(),
                    )
                )
    return tuple(findings)


def scan_paths(paths: Sequence[Path]) -> tuple[Finding, ...]:
    """Return commercial-claim findings for all requested paths."""
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_path(path))
    return tuple(findings)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse CLI arguments for the commercial claim checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        default=[],
        help="Commercial markdown surface to scan; repeatable.",
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
    """Run the commercial claim checker and return a process exit code."""
    args = parse_args(argv)
    _ = args.check
    findings = scan_paths(args.paths)
    if findings:
        for finding in findings:
            print(finding.format(), file=sys.stderr)
        return 1

    print(f"commercial claim hygiene passed: {len(args.paths)} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
