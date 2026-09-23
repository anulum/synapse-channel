# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — Pi registry lock supply-chain gate
"""Refuse missing integrity and alternate tarball hosts before npm executes."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from tools.check_pi_lock_integrity import LOCK, inspect


def test_shipped_pi_lock_has_integrity_for_every_tarball() -> None:
    """The actual release lock cannot retain an unchecked nested Pi package."""
    assert inspect(LOCK) == ()


def test_missing_digest_and_foreign_host_are_rejected(tmp_path: Path) -> None:
    """A registry omission or URL replacement fails before npm installation."""
    entry = {
        "version": "1.0.0",
        "resolved": "https://registry.npmjs.org/example/-/example-1.0.0.tgz",
    }
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/example": entry,
        },
    }
    path = tmp_path / "package-lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert inspect(path) == ("node_modules/example: missing SHA-512 integrity",)

    entry["integrity"] = "sha512-" + base64.b64encode(b"a" * 64).decode("ascii")
    entry["resolved"] = "https://example.invalid/example-1.0.0.tgz"
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert inspect(path) == (
        "node_modules/example: tarball is not an exact npm registry HTTPS URL",
    )
