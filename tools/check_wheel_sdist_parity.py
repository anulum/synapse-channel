#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wheel/sdist package-tree parity check for release integrity
"""Prove a built wheel and sdist ship the same importable package and no tests.

Strong release provenance records *which* artifact was built; it does not prove
the artifact works. A sdist that ships ``test_*.py`` modules without their
helpers (external review finding 7), or that omits a package module the wheel
carries, passes every provenance step and still installs a broken tree.

This check is the artifact-content half of the release integrity gate: it
compares the ``synapse_channel`` package modules inside the wheel (a zip) and the
sdist (a tar), and refuses a sdist that carries a ``tests/`` tree. The clean-
environment install smoke (import + CLI) lives in the workflow that calls this.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

_PACKAGE_PREFIX = "synapse_channel/"


def wheel_package_modules(wheel_path: Path) -> set[str]:
    """Return the ``synapse_channel/*.py`` module paths inside a wheel."""
    with zipfile.ZipFile(wheel_path) as archive:
        return {
            name
            for name in archive.namelist()
            if name.startswith(_PACKAGE_PREFIX) and name.endswith(".py")
        }


def _sdist_members(sdist_path: Path) -> list[str]:
    with tarfile.open(sdist_path, "r:gz") as archive:
        return archive.getnames()


def sdist_package_modules(sdist_path: Path) -> set[str]:
    """Return the ``synapse_channel/*.py`` module paths inside a sdist.

    Sdist members are ``<name>-<version>/src/synapse_channel/…``; the leading
    project-and-``src`` prefix is stripped so the set is comparable with the
    wheel's.
    """
    modules: set[str] = set()
    for name in _sdist_members(sdist_path):
        _, separator, tail = name.partition("/src/")
        if separator and tail.startswith(_PACKAGE_PREFIX) and tail.endswith(".py"):
            modules.add(tail)
    return modules


def sdist_ships_tests(sdist_path: Path) -> bool:
    """Return whether the sdist carries any ``tests/`` member."""
    return any("/tests/" in name for name in _sdist_members(sdist_path))


def parity_problems(wheel_path: Path, sdist_path: Path) -> list[str]:
    """Return every reason the wheel and sdist fail the parity contract."""
    problems: list[str] = []
    if sdist_ships_tests(sdist_path):
        problems.append(
            "sdist ships a tests/ tree; setuptools packages test modules without "
            "their helpers, so the shipped suite cannot run — prune tests from the sdist"
        )
    wheel_modules = wheel_package_modules(wheel_path)
    sdist_modules = sdist_package_modules(sdist_path)
    missing = wheel_modules - sdist_modules
    extra = sdist_modules - wheel_modules
    if missing:
        problems.append(f"sdist is missing package modules the wheel ships: {sorted(missing)[:5]}")
    if extra:
        problems.append(f"sdist ships package modules absent from the wheel: {sorted(extra)[:5]}")
    if not wheel_modules:
        problems.append("wheel ships no synapse_channel package modules")
    return problems


def main(argv: list[str]) -> int:
    """Compare ``argv[0]`` (wheel) and ``argv[1]`` (sdist); return 0 when they agree."""
    if len(argv) != 2:
        print("usage: check_wheel_sdist_parity.py <wheel> <sdist>", file=sys.stderr)
        return 2
    wheel_path, sdist_path = Path(argv[0]), Path(argv[1])
    problems = parity_problems(wheel_path, sdist_path)
    if problems:
        for problem in problems:
            print(f"distribution-integrity FAIL: {problem}", file=sys.stderr)
        return 1
    count = len(wheel_package_modules(wheel_path))
    print(f"distribution-integrity OK: {count} package modules match; sdist carries no tests/")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
