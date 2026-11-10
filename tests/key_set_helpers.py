# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — write per-message key sets and their secret files for tests
"""Write owner-only per-message key sets and secret files the way an operator would."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def instant(seconds: float) -> str:
    """Format POSIX ``seconds`` the way a key-set file states an instant."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def replace_owner_only(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Replace ``path`` atomically with ``text`` (a new inode, so a change is seen)."""
    staging = path.with_name(path.name + ".new")
    staging.write_text(text, "utf-8")
    staging.chmod(mode)
    os.replace(staging, path)


def write_secret(directory: Path, key_id: str, secret: str) -> str:
    """Write ``secret`` to ``<key_id>.key`` in ``directory``; return the file name."""
    name = f"{key_id}.key"
    replace_owner_only(directory / name, secret + "\n")
    return name


def key_entry(key_id: str, senders: list[str], **extra: Any) -> dict[str, Any]:
    """Return one key-set entry whose secret lives in ``<key_id>.key`` beside the set."""
    return {"key_id": key_id, "secret_file": f"{key_id}.key", "senders": senders, **extra}


def write_key_set(path: Path, *entries: dict[str, Any]) -> None:
    """Replace the owner-only key-set file with ``entries``."""
    replace_owner_only(path, json.dumps({"version": 1, "keys": list(entries)}))
