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
citation, ``CITATION.cff``, and the ``.zenodo.json`` archive metadata must all match
it. Run by pre-commit and CI so a release bump that misses one surface (the README
citation and the Zenodo metadata are easy to forget — a stale ``.zenodo.json``
mislabels the archived DOI) is caught before it ships a stale number.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no stdlib tomllib.
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    """Return the canonical version from ``pyproject.toml``."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _search(path: str, pattern: str) -> str:
    """Return the first capture of ``pattern`` in ``path``, or ``""``."""
    match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1).strip() if match else ""


def discover() -> dict[str, str]:
    """Return the version each surface declares, keyed by a human label."""
    return {
        "src/synapse_channel/__init__.py": _search(
            "src/synapse_channel/__init__.py", r'^__version__ = "([^"]+)"'
        ),
        "README.md citation": _search("README.md", r"version\s*=\s*\{([^}]+)\}"),
        "CITATION.cff": _search("CITATION.cff", r'^version:\s*"?([^"\n]+?)"?\s*$'),
        ".zenodo.json": _search(".zenodo.json", r'"version":\s*"([^"]+)"'),
    }


def main() -> int:
    """Compare every surface against ``pyproject.toml``; return non-zero on drift."""
    canonical = _pyproject_version()
    drifted = {label: found for label, found in discover().items() if found != canonical}
    if drifted:
        detail = "; ".join(f"{label}={found!r}" for label, found in drifted.items())
        print(f"Version desync — pyproject is {canonical!r} but: {detail}")
        return 1
    print(f"Version in sync across all surfaces: {canonical}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
