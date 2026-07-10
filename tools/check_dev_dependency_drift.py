#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — verify the local development environment mirrors pyproject extras
"""Check that the active Python environment satisfies repository dev extras.

The checker compares selected ``[project.optional-dependencies]`` groups from
``pyproject.toml`` with distributions installed in the interpreter that runs the
checker. It is intentionally local and dependency-free: only lower-bound
requirements of the form ``package>=version`` are enforced, matching the way this
repository declares its development, documentation, and benchmark toolchain.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

if sys.version_info >= (3, 11):  # pragma: no cover - version branch.
    import tomllib
else:  # pragma: no cover - covered on Python 3.10.
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_EXTRAS = ("dev", "docs", "benchmark")
REQUIREMENT_PATTERN = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*>=\s*(?P<version>[^;,\s]+)"
)


@dataclass(frozen=True)
class Requirement:
    """One lower-bound optional dependency requirement."""

    extra: str
    name: str
    minimum: str


@dataclass(frozen=True)
class Finding:
    """One development dependency drift finding."""

    category: str
    requirement: Requirement
    installed: str | None

    def format(self) -> str:
        """Render the finding as one stable diagnostic line."""
        installed = self.installed if self.installed is not None else "not installed"
        return (
            f"{self.category}: {self.requirement.extra}: {self.requirement.name}"
            f">={self.requirement.minimum} ({installed})"
        )


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for the drift checker."""

    pyproject: Path
    extras: tuple[str, ...]
    check: bool


def _normalise_name(name: str) -> str:
    """Return a PEP 503 style normalised distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_key(version: str) -> tuple[object, ...]:
    """Return a comparison key for the numeric version floors used by this repo."""
    key: list[object] = []
    for part in re.split(r"[.+_-]", version):
        if not part:
            continue
        key.append(int(part) if part.isdigit() else part)
    return tuple(key)


def parse_requirement(extra: str, raw: str) -> Requirement | None:
    """Parse one supported ``package>=version`` requirement string."""
    requirement = raw.split(";", maxsplit=1)[0].strip()
    match = REQUIREMENT_PATTERN.match(requirement)
    if match is None:
        return None
    return Requirement(
        extra=extra,
        name=_normalise_name(match.group("name")),
        minimum=match.group("version"),
    )


def load_pyproject_extras(path: Path) -> Mapping[str, Sequence[str]]:
    """Load optional dependency groups from ``path``."""
    with path.open("rb") as handle:
        pyproject = tomllib.load(handle)
    project = pyproject.get("project", {})
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        return {}
    return {
        str(extra): tuple(str(item) for item in values)
        for extra, values in optional.items()
        if isinstance(values, list)
    }


def collect_requirements(
    optional_dependencies: Mapping[str, Sequence[str]], extras: Sequence[str]
) -> tuple[Requirement, ...]:
    """Return supported lower-bound requirements for selected extras."""
    requirements: list[Requirement] = []
    for extra in extras:
        for raw in optional_dependencies.get(extra, ()):
            requirement = parse_requirement(extra, raw)
            if requirement is not None:
                requirements.append(requirement)
    return tuple(requirements)


def installed_versions() -> Mapping[str, str]:
    """Return installed distribution versions for the active interpreter."""
    versions: dict[str, str] = {}
    for distribution in metadata.distributions():
        # Distribution.name (3.10+) reads metadata Name and stays typed on the
        # 3.10 floor, where the PackageMetadata protocol lacks .get().
        name = distribution.name
        if not name:
            continue
        normalised = _normalise_name(name)
        previous = versions.get(normalised)
        if previous is None or _version_key(previous) < _version_key(distribution.version):
            versions[normalised] = distribution.version
    return versions


def scan_requirements(
    requirements: Sequence[Requirement], installed: Mapping[str, str]
) -> tuple[Finding, ...]:
    """Return missing or stale findings for ``requirements``."""
    findings: list[Finding] = []
    for requirement in requirements:
        installed_version = installed.get(requirement.name)
        if installed_version is None:
            findings.append(Finding(category="missing", requirement=requirement, installed=None))
            continue
        if _version_key(installed_version) < _version_key(requirement.minimum):
            findings.append(
                Finding(
                    category="stale",
                    requirement=requirement,
                    installed=installed_version,
                )
            )
    return tuple(findings)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse CLI arguments for the dev dependency drift checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Optional dependency group to check; repeatable.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check-only mode; kept for consistency with other repository guards.",
    )
    namespace = parser.parse_args(argv)
    extras = tuple(namespace.extra) if namespace.extra else DEFAULT_EXTRAS
    return CliArgs(pyproject=namespace.pyproject, extras=extras, check=namespace.check)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the drift checker and return a process exit code."""
    args = parse_args(argv)
    _ = args.check
    optional_dependencies = load_pyproject_extras(args.pyproject)
    requirements = collect_requirements(optional_dependencies, args.extras)
    findings = scan_requirements(requirements, installed_versions())
    if findings:
        for finding in findings:
            print(finding.format(), file=sys.stderr)
        return 1

    print(
        "dev dependency mirror passed: "
        f"{len(requirements)} requirement(s) across {', '.join(args.extras)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
