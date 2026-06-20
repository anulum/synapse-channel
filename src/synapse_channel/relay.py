# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only NDJSON relay log and compact wire format
"""Token-thrifty NDJSON relay format and append-only log helpers.

The lite/heavy relay lets a token-budgeted agent observe and drive the channel
through newline-delimited JSON files instead of a live socket. This module
provides the halves of that bridge:

* a symmetric codec — :func:`encode_lite` shrinks a full Synapse message into a
  short-key envelope before it is logged, and :func:`decode_lite` reconstructs
  the full envelope from it — sharing one key schema (:data:`LITE_KEYS`) so the
  two halves can never drift apart;
* append-only log helpers (:func:`append_jsonl`, :func:`read_jsonl_since`,
  :func:`trim_jsonl_tail`) with a resumable byte cursor (:func:`load_offset`,
  :func:`save_offset`) that survives partial writes and file truncation;
* a command normaliser (:func:`normalize_core_command`) that maps the verbose
  and short spellings an agent might emit onto one canonical short form.

Every function is filesystem- or dict-pure and fully unit-testable.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# -- lite/heavy relay codec ---------------------------------------------------

LITE_VERSION = 1
"""Schema version stamped into every lite envelope under the ``v`` key."""

LITE_KEYS: dict[str, str] = {
    "msg_id": "i",
    "type": "ty",
    "sender": "s",
    "target": "to",
    "payload": "p",
    "timestamp": "t",
    "hub_id": "h",
}
"""Heavy envelope field → compact relay key.

The single source of truth both halves of the codec read so the lite wire
format cannot drift between :func:`encode_lite` and :func:`decode_lite`.
"""


def encode_lite(message: dict[str, Any]) -> dict[str, Any]:
    """Pack a full Synapse message into a short-key relay envelope.

    The encode half of the relay codec; :func:`decode_lite` is its inverse.

    Parameters
    ----------
    message : dict[str, Any]
        A full message envelope as produced by
        :func:`synapse_channel.protocol.build_envelope`.

    Returns
    -------
    dict[str, Any]
        A mapping with single/double-letter keys: ``v`` (version), ``i``
        (msg id), ``ty`` (type), ``s`` (sender), ``to`` (target), ``p``
        (payload), ``t`` (millisecond timestamp), ``h`` (hub id). Malformed
        ``timestamp``/``msg_id`` fields fall back to the current time and ``0``.
    """
    ts = message.get("timestamp")
    try:
        ts_val = float(ts) if ts is not None else time.time()
    except (TypeError, ValueError):
        ts_val = time.time()

    msg_id = message.get("msg_id")
    try:
        msg_id_val = int(msg_id) if msg_id is not None else 0
    except (TypeError, ValueError):
        msg_id_val = 0

    return {
        "v": LITE_VERSION,
        "i": msg_id_val,
        "ty": str(message.get("type", "chat")),
        "s": str(message.get("sender", "?")),
        "to": str(message.get("target", "all")),
        "p": str(message.get("payload", "")),
        "t": int(ts_val * 1000.0),
        "h": str(message.get("hub_id", "")),
    }


def decode_lite(lite: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a full message envelope from a compact relay event.

    The inverse of :func:`encode_lite`. Because the lite format stores the
    timestamp as a millisecond integer, the reconstructed ``timestamp`` is
    precise only to the millisecond — sub-millisecond detail from the original
    envelope is not recoverable. Missing or malformed short keys fall back to
    the same defaults :func:`encode_lite` itself emits.

    Parameters
    ----------
    lite : dict[str, Any]
        A short-key envelope as produced by :func:`encode_lite`.

    Returns
    -------
    dict[str, Any]
        A full envelope with ``sender``, ``target``, ``type``, ``payload``,
        ``timestamp`` (seconds), ``msg_id``, and ``hub_id``.
    """
    try:
        ts_ms = int(lite.get("t", 0))
    except (TypeError, ValueError):
        ts_ms = 0
    try:
        msg_id_val = int(lite.get("i", 0))
    except (TypeError, ValueError):
        msg_id_val = 0

    return {
        "sender": str(lite.get("s", "?")),
        "target": str(lite.get("to", "all")),
        "type": str(lite.get("ty", "chat")),
        "payload": str(lite.get("p", "")),
        "timestamp": ts_ms / 1000.0,
        "msg_id": msg_id_val,
        "hub_id": str(lite.get("h", "")),
    }


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    """Append one JSON object as a line to an NDJSON log, creating parents.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination log file. Missing parent directories are created.
    payload : dict[str, Any]
        The object to serialise; written compactly with ASCII escaping.
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")


def read_jsonl_since(path: str | Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read NDJSON objects appended after a byte ``offset``.

    The returned cursor is resilient: a partial final line (no trailing newline
    and not yet valid JSON) leaves the cursor before it for a later retry, and a
    cursor beyond the current file size is treated as truncation and reset to
    the start.

    Parameters
    ----------
    path : str or pathlib.Path
        NDJSON log to read.
    offset : int
        Byte position returned by a previous call (``0`` to read from the start).

    Returns
    -------
    tuple[list[dict[str, Any]], int]
        The newly consumed objects (non-dict and blank lines are skipped) and
        the updated byte cursor.
    """
    src = Path(path)
    if not src.exists():
        return [], offset

    try:
        size = src.stat().st_size
    except OSError:
        return [], offset

    start = max(int(offset), 0)
    # A cursor past EOF means the file was truncated/rotated; restart so relay
    # consumers do not get stuck forever beyond the new end.
    if start > size:
        start = 0

    rows: list[dict[str, Any]] = []
    with src.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read()
        end_offset = handle.tell()

    if not chunk:
        return [], end_offset

    consumed_offset = start
    for raw_line in chunk.splitlines(keepends=True):
        has_newline = raw_line.endswith(b"\n") or raw_line.endswith(b"\r")
        is_tail = consumed_offset + len(raw_line) == end_offset

        # A final line without a newline that is not yet valid JSON is a partial
        # write: keep the cursor before it so it can be retried on the next poll.
        if is_tail and not has_newline:
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text:
                break
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                break
            if isinstance(obj, dict):
                rows.append(obj)
            consumed_offset = end_offset
            break

        consumed_offset += len(raw_line)
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, consumed_offset


def load_offset(path: str | Path) -> int:
    """Read a persisted byte cursor, returning ``0`` when absent or unreadable.

    Parameters
    ----------
    path : str or pathlib.Path
        File holding a single integer cursor value.

    Returns
    -------
    int
        The stored cursor, or ``0`` on a missing/corrupt/unreadable file.
    """
    marker = Path(path)
    if not marker.exists():
        return 0
    try:
        return int(marker.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def save_offset(path: str | Path, offset: int) -> None:
    """Persist a byte cursor (clamped to be non-negative), creating parents.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination cursor file.
    offset : int
        Cursor value to store; negative values are clamped to ``0``.
    """
    marker = Path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(max(int(offset), 0)), encoding="utf-8")


def trim_jsonl_tail(path: str | Path, max_lines: int) -> int:
    """Truncate an NDJSON log to its last ``max_lines`` lines.

    Parameters
    ----------
    path : str or pathlib.Path
        NDJSON log to trim in place.
    max_lines : int
        Maximum number of trailing lines to keep. Values ``<= 0`` are a no-op.

    Returns
    -------
    int
        The number of leading lines dropped (``0`` when nothing was trimmed).
    """
    if max_lines <= 0:
        return 0
    src = Path(path)
    if not src.exists():
        return 0
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_lines:
        return 0
    dropped = len(lines) - max_lines
    kept = lines[-max_lines:]
    src.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return dropped


def normalize_core_command(data: dict[str, Any]) -> dict[str, Any]:
    """Map a verbose or short command spelling onto one canonical short form.

    Agents may emit commands with long keys (``payload``, ``task_id``) or the
    short relay keys (``p``, ``id``); this normaliser accepts either and returns
    the canonical short form the relay core consumes.

    Parameters
    ----------
    data : dict[str, Any]
        Raw command mapping. Must carry a command kind under ``k`` or ``kind``.

    Returns
    -------
    dict[str, Any]
        The canonical command with a ``k`` kind and kind-specific short keys.

    Raises
    ------
    ValueError
        If the kind is missing or unsupported, or a ``spawn``/``stop`` command
        omits required fields.
    """
    kind = str(data.get("k") or data.get("kind") or "").strip().lower()
    if not kind:
        raise ValueError("Missing command kind.")

    out: dict[str, Any] = {"k": kind}
    if kind == "chat":
        out["p"] = str(data.get("p") or data.get("payload") or "").strip()
        out["to"] = str(data.get("to") or data.get("target") or "all")
        return out
    if kind == "claim":
        out["id"] = str(data.get("id") or data.get("task_id") or "").strip()
        out["n"] = str(data.get("n") or data.get("note") or "")
        return out
    if kind == "release":
        out["id"] = str(data.get("id") or data.get("task_id") or "").strip()
        return out
    if kind in {"who", "state"}:
        return out
    if kind == "history":
        raw = data.get("n") if "n" in data else data.get("limit")
        if isinstance(raw, str) and raw.strip().lower() == "all":
            return {"k": "history", "n": "all"}
        try:
            out["n"] = max(1, int(raw)) if raw is not None else 20
        except (TypeError, ValueError):
            out["n"] = 20
        return out
    if kind == "task_update":
        out["id"] = str(data.get("id") or data.get("task_id") or "").strip()
        if data.get("status"):
            out["status"] = str(data.get("status"))
        if data.get("note") is not None:
            out["note"] = str(data.get("note"))
        if data.get("data_ref") is not None:
            out["data_ref"] = str(data.get("data_ref"))
        return out
    if kind in {"resource", "resource_offer"}:
        out["kind"] = str(data.get("kind") or data.get("resource_kind") or "").strip()
        out["name"] = str(data.get("name") or data.get("resource_name") or "").strip()
        out["capacity"] = int(data.get("capacity", 1))
        if data.get("meta"):
            out["meta"] = data.get("meta")
        return out
    raise ValueError(f"Unsupported command kind '{kind}'.")
