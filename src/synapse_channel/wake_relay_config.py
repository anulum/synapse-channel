# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact owner-supplied wake relay bindings
"""Load a bounded owner-only binding without inferring a native session.

The binding is an operator declaration, not evidence that a model consumed a
notice. The caller must supply the same seat, UUID and execution host each time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.secret_files import read_secret_file
from synapse_channel.wake_adapters import WakeTarget
from synapse_channel.wake_adapters.codex_queue import canonical_session
from synapse_channel.waker_config import clean_waker_text

ADAPTERS = frozenset({"harness_waiter", "native_queue", "pane_bridge"})
"""Adapters that the public waker relay can construct."""


@dataclass(frozen=True, slots=True)
class RelayBinding:
    """One exact native target, durable marks namespace and transport order.

    ``journal_db`` and ``key_file`` select the read-only receipt reader;
    ``codex_bin`` is an absolute executable path for the native queue.
    ``pane_bridge`` uses the existing configuration of exactly ``target.seat``.
    Marks must stay with their journal across process and session restarts.
    """

    target: WakeTarget
    marks_dir: Path
    adapters: tuple[str, ...]
    journal_db: Path | None = None
    key_file: Path | None = None
    codex_bin: Path | None = None
    row_mac_key_file: Path | None = None


def _text(document: dict[str, object], field: str) -> str:
    """Read a non-empty terminal-safe field without coercing its type."""
    return clean_waker_text(document.get(field), field=field)


def _path(document: dict[str, object], field: str) -> Path | None:
    """Read an optional absolute path, rejecting relative execution policy."""
    if document.get(field) is None:
        return None
    value = Path(_text(document, field))
    if not value.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return value


def _decode(document: object) -> RelayBinding:
    """Validate the complete v1 transport binding before constructing adapters."""
    required = {
        "schema_version",
        "seat",
        "native_session",
        "execution_host",
        "marks_dir",
        "adapters",
    }
    optional = {"journal_db", "key_file", "codex_bin", "row_mac_key_file"}
    if (
        not isinstance(document, dict)
        or not required <= document.keys()
        or document.keys() - required - optional
    ):
        raise ValueError("relay binding keys do not match schema v1")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("unsupported relay binding schema")
    seat, session, host = (
        _text(document, key) for key in ("seat", "native_session", "execution_host")
    )
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", seat) is None:
        raise ValueError("seat must name one exact project/identity")
    if canonical_session(session) is None:
        raise ValueError("native_session must be a canonical lower-case UUID")
    names = _adapter_names(document["adapters"])
    marks = _path(document, "marks_dir")
    if marks is None:
        raise ValueError("marks_dir is required")
    journal, key, executable = (
        _path(document, key) for key in ("journal_db", "key_file", "codex_bin")
    )
    row_key = _path(document, "row_mac_key_file")
    if (key is not None or row_key is not None) and journal is None:
        raise ValueError("receipt keys require journal_db")
    return RelayBinding(
        WakeTarget(seat, session, host), marks, names, journal, key, executable, row_key
    )


def _adapter_names(names: object) -> tuple[str, ...]:
    """Require unique supported adapter names in an explicit non-empty order."""
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or name not in ADAPTERS for name in names)
    ):
        raise ValueError("adapters must be a non-empty array of supported adapter names")
    if len(set(names)) != len(names):
        raise ValueError("adapters must not contain duplicates")
    return tuple(names)


def load_relay_binding(path: Path) -> RelayBinding:
    """Read a single-link owner-only v1 binding, at most 16 KiB.

    Raises
    ------
    ValueError, OSError
        The policy is unsafe, malformed, ambiguous or unavailable.
    """
    content = read_secret_file(
        path, flag="wake relay binding", require_single_link=True, limit=16_384
    )
    try:
        document: object = json.loads(content)
    except (ValueError, RecursionError) as exc:
        raise ValueError("wake relay binding is not valid JSON") from exc
    return _decode(document)
