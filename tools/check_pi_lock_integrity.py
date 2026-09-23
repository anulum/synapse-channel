#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — fail closed on unverified Pi registry tarballs
"""Require a registry URL and SHA-512 SRI digest for every Pi lock entry."""

from __future__ import annotations

import base64
import binascii
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

LOCK = Path(__file__).resolve().parent.parent / "integrations/pi/package-lock.json"


def inspect(lock_path: Path) -> tuple[str, ...]:
    """Return precise refusal reasons without fetching or executing packages."""
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        return (f"Pi lockfile cannot be read: {type(exc).__name__}",)
    if not isinstance(lock, dict) or lock.get("lockfileVersion") != 3:
        return ("Pi lockfile must use version 3",)
    packages = lock.get("packages")
    if not isinstance(packages, dict) or not packages:
        return ("Pi lockfile has no package inventory",)
    issues: list[str] = []
    for name, entry in packages.items():
        if name == "":
            continue
        if not isinstance(name, str) or not isinstance(entry, dict):
            issues.append("Pi lockfile contains a malformed package entry")
            continue
        resolved = entry.get("resolved")
        if not isinstance(resolved, str):
            issues.append(f"{name}: missing resolved tarball")
            continue
        try:
            url = urlsplit(resolved)
            registry_url = (
                url.geturl() == resolved
                and url.scheme == "https"
                and url.hostname == "registry.npmjs.org"
                and url.username is None
                and url.password is None
                and url.port is None
                and url.path.endswith(".tgz")
                and not url.query
                and not url.fragment
            )
        except ValueError:
            registry_url = False
        if not registry_url:
            issues.append(f"{name}: tarball is not an exact npm registry HTTPS URL")
        integrity = entry.get("integrity")
        if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            issues.append(f"{name}: missing SHA-512 integrity")
            continue
        try:
            digest = base64.b64decode(integrity[7:], validate=True)
        except binascii.Error:
            digest = b""
        if len(digest) != 64:
            issues.append(f"{name}: malformed SHA-512 integrity")
    return tuple(issues)


def main() -> int:
    """Exit nonzero until every registry tarball has a verified-byte contract."""
    issues = inspect(LOCK)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print("Pi lockfile integrity complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
