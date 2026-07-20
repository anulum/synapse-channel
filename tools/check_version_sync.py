#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — guard that the project version is identical on every surface
"""Fail when the project version drifts between its declared surfaces.

``pyproject.toml`` is the source of truth; the package ``__version__``, the README
citation, ``CITATION.cff``, archive metadata, and both MCP registry version fields
must all match it. Run by pre-commit and CI so a release bump that misses one
surface is caught before it ships a stale number.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):  # pragma: no cover - version branch.
    import tomllib
else:  # pragma: no cover - covered on Python 3.10.
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version(root: Path = ROOT) -> str:
    """Return the canonical version from ``pyproject.toml``."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _search(root: Path, path: str, pattern: str) -> str:
    """Return the first capture of ``pattern`` in ``path``, or ``""``."""
    match = re.search(pattern, (root / path).read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1).strip() if match else ""


def _server_versions(root: Path) -> tuple[str, str]:
    """Return the MCP server and PyPI-package versions from ``server.json``."""
    data = json.loads((root / "server.json").read_text(encoding="utf-8"))
    top_level = str(data.get("version", ""))
    package = next(
        (
            str(item.get("version", ""))
            for item in data.get("packages", ())
            if item.get("identifier") == "synapse-channel"
        ),
        "",
    )
    return top_level, package


def discover(root: Path = ROOT) -> dict[str, str]:
    """Return the version each surface declares, keyed by a human label."""
    server_version, server_package_version = _server_versions(root)
    return {
        "src/synapse_channel/__init__.py": _search(
            root, "src/synapse_channel/__init__.py", r'^__version__ = "([^"]+)"'
        ),
        "README.md citation": _search(root, "README.md", r"version\s*=\s*\{([^}]+)\}"),
        "CITATION.cff": _search(root, "CITATION.cff", r'^version:\s*"?([^"\n]+?)"?\s*$'),
        ".zenodo.json": _search(root, ".zenodo.json", r'"version":\s*"([^"]+)"'),
        "server.json top-level": server_version,
        "server.json package synapse-channel": server_package_version,
    }


def main(root: Path = ROOT) -> int:
    """Compare every surface against ``pyproject.toml``; return non-zero on drift."""
    canonical = _pyproject_version(root)
    drifted = {label: found for label, found in discover(root).items() if found != canonical}
    if drifted:
        detail = "; ".join(f"{label}={found!r}" for label, found in drifted.items())
        print(f"Version desync — pyproject is {canonical!r} but: {detail}")
        return 1
    print(f"Version in sync across all surfaces: {canonical}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
