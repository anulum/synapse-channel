# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — shared helpers for the MCP bearer gate tests
"""Owner-only files and issuer-token-shaped strings for the gate tests."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path


def owner_file(path: Path, content: str | bytes) -> Path:
    """Write ``content`` to a new owner-only file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return path


def seat_token(expiry: object) -> str:
    """Return an issuer-token-shaped string with the given ``exp`` claim."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).rstrip(b"=")
    return f"eyJhbGciOiJFZERTQSJ9.{payload.decode()}.c2lnbmF0dXJl"
