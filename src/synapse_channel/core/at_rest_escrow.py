# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — threshold key escrow and recovery for at-rest data keys
"""Threshold (Shamir) escrow for at-rest data keys — recovery without a single escrow secret.

Lost-key recovery is impossible by design unless the operator planned for it. This module
implements that plan: split a 32-byte at-rest data key into ``n`` shares with threshold ``k``
using Shamir secret sharing over GF(2^8). Any ``k`` shares reconstruct the key; fewer than
``k`` reveal nothing. Each share is written as an owner-only JSON document; operators store
shares in separate custody (safes, offline media, distinct roles).

Recovery rewrites a **new** raw key file from the reconstructed data key — it never bypasses
encryption. Escrow is opt-in and explicit; the live hub never reads share files automatically.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.at_rest import (
    KEY_BYTES,
    _write_new_key_file,
    check_key_file,
    load_key_file,
)

ESCROW_SHARE_SCHEMA = "synapse-at-rest-escrow-share.v1"
"""Schema marker for one threshold share of an at-rest data key."""

# AES field polynomial x^8 + x^4 + x^3 + x + 1 (0x11b), standard for AES GF(2^8).
_GF_MOD = 0x11B
_GF_ORDER = 256


def _gf_mul(left: int, right: int) -> int:
    """Multiply two GF(2^8) elements under the AES reduction polynomial."""
    product = 0
    a, b = left & 0xFF, right & 0xFF
    for _ in range(8):
        if b & 1:
            product ^= a
        carry = a & 0x80
        a = (a << 1) & 0xFF
        if carry:
            a ^= _GF_MOD & 0xFF
        b >>= 1
    return product


def _gf_pow(base: int, exp: int) -> int:
    """Raise a GF(2^8) element to a non-negative integer power."""
    result = 1
    value = base & 0xFF
    exponent = exp
    while exponent > 0:
        if exponent & 1:
            result = _gf_mul(result, value)
        value = _gf_mul(value, value)
        exponent >>= 1
    return result


def _gf_inv(value: int) -> int:
    """Return the multiplicative inverse in GF(2^8); raises on zero."""
    if value & 0xFF == 0:
        raise ValueError("zero has no multiplicative inverse in GF(2^8)")
    # a^(255) = 1 for non-zero a, so a^(254) = a^{-1}.
    return _gf_pow(value, 254)


def _eval_poly(coefficients: list[int], x: int) -> int:
    """Evaluate a polynomial with GF coefficients at ``x`` (Horner)."""
    acc = 0
    for coeff in reversed(coefficients):
        acc = _gf_mul(acc, x) ^ (coeff & 0xFF)
    return acc


def _lagrange_at_zero(shares: list[tuple[int, int]]) -> int:
    """Reconstruct the secret (constant term) from distinct (x, y) shares at x=0."""
    secret = 0
    for i, (x_i, y_i) in enumerate(shares):
        numerator = 1
        denominator = 1
        for j, (x_j, _) in enumerate(shares):
            if i == j:
                continue
            numerator = _gf_mul(numerator, x_j)
            denominator = _gf_mul(denominator, x_j ^ x_i)
        secret ^= _gf_mul(y_i, _gf_mul(numerator, _gf_inv(denominator)))
    return secret & 0xFF


@dataclass(frozen=True)
class EscrowShare:
    """One threshold share of an at-rest data key.

    Attributes
    ----------
    group_id : str
        Shared identifier binding the set of shares that recover one key.
    index : int
        Share x-coordinate in ``1..255`` (unique within the group).
    threshold : int
        Minimum number of shares required to recover the key.
    share_count : int
        Total number of shares issued for this group.
    payload : bytes
        Exactly :data:`KEY_BYTES` share bytes (one GF element per secret byte).
    """

    group_id: str
    index: int
    threshold: int
    share_count: int
    payload: bytes

    def to_document(self) -> dict[str, Any]:
        """Serialise this share to a JSON-ready document (no raw data key)."""
        return {
            "schema": ESCROW_SHARE_SCHEMA,
            "group_id": self.group_id,
            "index": self.index,
            "threshold": self.threshold,
            "share_count": self.share_count,
            "payload": base64.b64encode(self.payload).decode("ascii"),
        }


def split_data_key(
    data_key: bytes,
    *,
    threshold: int,
    share_count: int,
    group_id: str | None = None,
) -> tuple[EscrowShare, ...]:
    """Split a data key into ``share_count`` Shamir shares with the given threshold.

    Parameters
    ----------
    data_key : bytes
        Exactly :data:`KEY_BYTES` secret bytes to protect.
    threshold : int
        Minimum shares needed to reconstruct (``2 <= threshold <= share_count``).
    share_count : int
        Total shares to issue (``threshold <= share_count <= 255``).
    group_id : str, optional
        Explicit group identifier; a random UUID is drawn when omitted.

    Returns
    -------
    tuple[EscrowShare, ...]
        Ordered shares with indices ``1..share_count``.

    Raises
    ------
    ValueError
        When lengths or threshold parameters are invalid.
    """
    if len(data_key) != KEY_BYTES:
        raise ValueError(f"data key must be {KEY_BYTES} bytes, got {len(data_key)}")
    if threshold < 2:
        raise ValueError("escrow threshold must be at least 2")
    if share_count < threshold:
        raise ValueError("share_count must be >= threshold")
    if share_count > 255:
        raise ValueError("share_count must be <= 255")
    gid = group_id or str(uuid.uuid4())
    # One degree-(threshold-1) polynomial per secret byte; constant term = secret byte.
    polys: list[list[int]] = []
    for secret_byte in data_key:
        coeffs = [secret_byte & 0xFF]
        for _ in range(threshold - 1):
            coeffs.append(secrets.randbelow(_GF_ORDER))
        polys.append(coeffs)

    shares: list[EscrowShare] = []
    for index in range(1, share_count + 1):
        payload = bytes(_eval_poly(poly, index) for poly in polys)
        shares.append(
            EscrowShare(
                group_id=gid,
                index=index,
                threshold=threshold,
                share_count=share_count,
                payload=payload,
            )
        )
    return tuple(shares)


def recover_data_key(shares: Sequence[EscrowShare]) -> bytes:
    """Reconstruct the data key from at least ``threshold`` distinct shares.

    Parameters
    ----------
    shares : sequence of EscrowShare
        Shares from the same group; at least ``threshold`` required.

    Returns
    -------
    bytes
        The recovered :data:`KEY_BYTES` data key.

    Raises
    ------
    ValueError
        When shares disagree on group/threshold, are too few, or have duplicate indices.
    """
    if not shares:
        raise ValueError("at least one escrow share is required")
    first = shares[0]
    if any(s.group_id != first.group_id for s in shares):
        raise ValueError("escrow shares must share the same group_id")
    if any(s.threshold != first.threshold for s in shares):
        raise ValueError("escrow shares must share the same threshold")
    if any(s.share_count != first.share_count for s in shares):
        raise ValueError("escrow shares must share the same share_count")
    if any(len(s.payload) != KEY_BYTES for s in shares):
        raise ValueError(f"each escrow payload must be {KEY_BYTES} bytes")
    if len(shares) < first.threshold:
        raise ValueError(
            f"need at least {first.threshold} shares to recover, got {len(shares)}"
        )
    by_index: dict[int, EscrowShare] = {}
    for share in shares:
        if share.index in by_index:
            raise ValueError(f"duplicate escrow share index {share.index}")
        if not (1 <= share.index <= 255):
            raise ValueError(f"escrow share index out of range: {share.index}")
        by_index[share.index] = share
    selected = list(by_index.values())[: first.threshold]
    if len(selected) < first.threshold:
        raise ValueError(
            f"need at least {first.threshold} distinct share indices, got {len(selected)}"
        )
    recovered = bytearray(KEY_BYTES)
    for byte_i in range(KEY_BYTES):
        points = [(s.index, s.payload[byte_i]) for s in selected]
        recovered[byte_i] = _lagrange_at_zero(points)
    return bytes(recovered)


def write_escrow_share(path: str | Path, share: EscrowShare) -> Path:
    """Write one share document as an owner-only file, never overwriting."""
    document = json.dumps(share.to_document(), ensure_ascii=True, indent=2, sort_keys=True)
    return _write_new_key_file(Path(path), document.encode("utf-8") + b"\n")


def load_escrow_share(path: str | Path) -> EscrowShare:
    """Load and validate one escrow share document.

    Raises
    ------
    ValueError
        When the file is not a valid share document.
    """
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != ESCROW_SHARE_SCHEMA:
        raise ValueError(f"not a Synapse at-rest escrow share: {path}")
    try:
        group_id = str(raw["group_id"])
        index = int(raw["index"])
        threshold = int(raw["threshold"])
        share_count = int(raw["share_count"])
        payload = base64.b64decode(raw["payload"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed escrow share: {path}") from exc
    if len(payload) != KEY_BYTES:
        raise ValueError(f"escrow share payload must be {KEY_BYTES} bytes: {path}")
    if threshold < 2 or share_count < threshold or not (1 <= index <= 255):
        raise ValueError(f"escrow share has invalid threshold/index parameters: {path}")
    return EscrowShare(
        group_id=group_id,
        index=index,
        threshold=threshold,
        share_count=share_count,
        payload=payload,
    )


def split_key_file(
    key_path: str | Path,
    *,
    threshold: int,
    share_count: int,
    out_dir: str | Path,
    group_id: str | None = None,
) -> tuple[Path, ...]:
    """Split an owner-only raw key file into threshold shares under ``out_dir``.

    Parameters
    ----------
    key_path : str or pathlib.Path
        Existing raw 32-byte key file (mode/ownership checked).
    threshold, share_count : int
        Shamir parameters (see :func:`split_data_key`).
    out_dir : str or pathlib.Path
        Directory for share files ``share-01.json`` … (created if needed).
    group_id : str, optional
        Explicit share group id.

    Returns
    -------
    tuple[pathlib.Path, ...]
        Paths of the written share files.
    """
    ok, reason = check_key_file(key_path)
    if not ok:
        raise ValueError(reason)
    data_key = load_key_file(key_path)
    shares = split_data_key(
        data_key, threshold=threshold, share_count=share_count, group_id=group_id
    )
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for share in shares:
        path = destination / f"share-{share.index:02d}.json"
        written.append(write_escrow_share(path, share))
    return tuple(written)


def recover_key_file(
    share_paths: Sequence[str | Path | EscrowShare],
    *,
    out_path: str | Path,
) -> Path:
    """Recover a raw key file from share paths (or loaded shares).

    Parameters
    ----------
    share_paths : sequence
        Paths to share files, or :class:`EscrowShare` instances.
    out_path : str or pathlib.Path
        Destination raw key file; refused when it already exists.

    Returns
    -------
    pathlib.Path
        The written owner-only raw key file.
    """
    loaded: list[EscrowShare] = []
    for item in share_paths:
        if isinstance(item, EscrowShare):
            loaded.append(item)
        else:
            loaded.append(load_escrow_share(item))
    data_key = recover_data_key(loaded)
    return _write_new_key_file(Path(out_path), data_key)
