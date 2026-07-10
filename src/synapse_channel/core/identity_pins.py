# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — trust-on-first-use pins binding an agent name to its identity key
"""Trust-on-first-use identity pins — the hub's durable name→key memory.

The ownership lease (:mod:`synapse_channel.core.name_ownership`) protects a
name across *reconnects* with a bearer token; it deliberately lives in memory,
so a hub restart clears it. This store is the durable half of the ownership
keystone: the first time a name registers with a verified identity signature,
the hub pins the name to that public key, and from then on the name binds only
to a frame that proves possession of the pinned key — across reconnects *and*
hub restarts, with zero operator configuration. A different key, or a missing
signature on a pinned name, is refused with an actionable error.

Trust-on-first-use is the stated posture: the pin protects *continuity* of an
identity, not its first claim — exactly the guarantee an SSH ``known_hosts``
file gives, and the right default for the loopback single-user hub a
``pip install`` user runs. Operator-managed multi-tenant trust remains the
``--require-identity-binding`` bundle, which takes precedence over this store.

Pins persist as one small JSON file (atomic replace), so an operator can
inspect them. Recovery from a lost machine key goes through the hub's governed
reclaim verb: it compares the expected key id, enforces the owner-liveness
policy, and records the action durably before a different key may pin the name.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("synapse.hub")

_ED25519_RAW_PUBLIC_KEY_BYTES = 32


@dataclass(frozen=True)
class IdentityPin:
    """One durable name→key binding.

    Attributes
    ----------
    key_id : str
        The signature-envelope key id the name is pinned to.
    public_key : str
        Base64 raw Ed25519 public key the name must prove possession of.
    pinned_at : float
        Wall-clock time the pin was recorded, for operator inspection only.
    """

    key_id: str
    public_key: str
    pinned_at: float


class IdentityPinStore:
    """Own the hub's name→key pin table and its optional durability.

    Parameters
    ----------
    path : pathlib.Path or None, optional
        JSON file persisting the pins (``{"pins": {name: {key_id, public_key,
        pinned_at}}}``). ``None`` keeps the table in memory only — pins then
        protect the hub's lifetime, not restarts (the bare in-process posture
        tests use). A missing file starts empty; a malformed one is refused
        loudly rather than silently discarded, because dropping pins is a
        security downgrade.

    Raises
    ------
    ValueError
        When an existing pin file is not valid JSON of the expected shape.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path
        self._pins: dict[str, IdentityPin] = {}
        if path is not None and path.is_file():
            self._pins = _load_pins(path)

    @property
    def path(self) -> Path | None:
        """Return the backing file, or ``None`` for an in-memory store."""
        return self._path

    def pinned(self, name: str) -> IdentityPin | None:
        """Return the pin recorded for ``name``, or ``None`` when unpinned."""
        return self._pins.get(name)

    def pin(self, name: str, *, key_id: str, public_key: str, now: float | None = None) -> None:
        """Record — and, when backed by a file, persist — a name→key pin.

        Parameters
        ----------
        name : str
            The agent name being pinned.
        key_id : str
            The signature key id that just proved the name.
        public_key : str
            Base64 raw Ed25519 public key (validated: decodable, 32 bytes).
        now : float or None, optional
            Wall-clock stamp; ``None`` uses :func:`time.time`.

        Raises
        ------
        ValueError
            When ``public_key`` is not a valid raw Ed25519 key, so a malformed
            key can never be pinned and brick the name.
        """
        _validate_public_key(public_key)
        self._pins[name] = IdentityPin(
            key_id=str(key_id),
            public_key=str(public_key),
            pinned_at=time.time() if now is None else float(now),
        )
        if self._path is not None:
            _write_pins(self._path, self._pins)
        logger.info("identity pinned name=%s key_id=%s", name, key_id)

    def reclaim(self, name: str, *, expected_key_id: str) -> IdentityPin | None:
        """Remove ``name`` only when it is pinned to ``expected_key_id``.

        The expected-key comparison is a compare-and-swap guard for the
        operator path: a delayed request cannot remove a pin that was rotated
        or re-established after the operator inspected it. When the store is
        file-backed, the replacement file is committed before the live table
        changes, so an I/O failure leaves both views on the old, protective
        pin rather than weakening only the running hub.

        Parameters
        ----------
        name : str
            Pinned agent name to remove.
        expected_key_id : str
            Exact key id the operator observed and intends to reclaim.

        Returns
        -------
        IdentityPin or None
            The removed pin, or ``None`` when the name is absent or its current
            key id differs. A mismatch never mutates memory or disk.
        """
        pin = self._pins.get(name)
        if pin is None or pin.key_id != expected_key_id:
            return None
        remaining = dict(self._pins)
        remaining.pop(name)
        if self._path is not None:
            _write_pins(self._path, remaining)
        self._pins = remaining
        logger.warning("identity pin reclaimed name=%s key_id=%s", name, pin.key_id)
        return pin

    def __len__(self) -> int:
        """Return the number of pinned names."""
        return len(self._pins)


def _validate_public_key(public_key: str) -> None:
    """Raise :class:`ValueError` unless ``public_key`` is base64 raw Ed25519."""
    try:
        raw = base64.b64decode(public_key, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("identity pin public_key must be valid base64") from exc
    if len(raw) != _ED25519_RAW_PUBLIC_KEY_BYTES:
        raise ValueError(
            f"identity pin public_key must be {_ED25519_RAW_PUBLIC_KEY_BYTES} raw Ed25519 bytes"
        )


def _load_pins(path: Path) -> dict[str, IdentityPin]:
    """Parse a pin file, refusing a malformed one loudly."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid identity pin file {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("pins"), dict):
        raise ValueError(f"identity pin file {path} must be a mapping with a 'pins' object")
    pins: dict[str, IdentityPin] = {}
    for name, entry in data["pins"].items():
        if not isinstance(entry, dict):
            raise ValueError(f"identity pin for {name!r} must be an object")
        public_key = str(entry.get("public_key", ""))
        _validate_public_key(public_key)
        raw_stamp = entry.get("pinned_at", 0.0)
        stamp = float(raw_stamp) if isinstance(raw_stamp, (int, float)) else 0.0
        pins[str(name)] = IdentityPin(
            key_id=str(entry.get("key_id", "")),
            public_key=public_key,
            pinned_at=stamp,
        )
    return pins


def _write_pins(path: Path, pins: dict[str, IdentityPin]) -> None:
    """Persist the pin table atomically (temp file then :func:`os.replace`)."""
    payload = {
        "pins": {
            name: {
                "key_id": pin.key_id,
                "public_key": pin.public_key,
                "pinned_at": pin.pinned_at,
            }
            for name, pin in sorted(pins.items())
        }
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
