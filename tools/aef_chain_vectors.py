# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — build the AEF chain-continuity conformance vectors
"""Build ``tests/fixtures/aef_chain_v0_1.json``, the AEF chain vectors.

Each vector is a small export: signed AEF v0.1 receipts of one log, the time
to verify them at, the verdict each receipt must get, and the chain verdict
and findings the export must get. Receipts are signed with the same published
test key as the receipt vectors (the Ed25519 seed ``00 01 .. 1f``), and Ed25519
signatures are deterministic, so the file is reproducible byte for byte.

Usage::

    PYTHONPATH=src python tools/aef_chain_vectors.py --write
    PYTHONPATH=src python tools/aef_chain_vectors.py --check
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.aef_chain import GENESIS_RECEIPT
from synapse_channel.core.aef_domain import AEF_RECEIPT_DOMAIN
from synapse_channel.core.aef_verification import receipt_id_for

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "aef_chain_v0_1.json"
LOG_ID = "f3320c94a8b070b04d652b5b0099baa9e12ff8cf8f375093282c7c31becbe0d6"
KEY_ID = "56475aa75463474c"
PUBLIC_KEY_HEX = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
NOW_MS = 1783941000000
_SEED = bytes(range(32))


def _receipt(
    seq: int, prev: str, *, expired: bool = False, task: str = "chain-task"
) -> dict[str, Any]:
    """Return one signed lease-grant receipt of the test log."""
    issued_at = NOW_MS - 3_600_000 + seq * 1000
    receipt: dict[str, Any] = {
        "aef": "0.1",
        "receipt_type": "lease",
        "action": "grant",
        "hub_id": "hub-ops-01.example.com",
        "log_id": LOG_ID,
        "seq": seq,
        "issued_at": issued_at,
        "actor": {"agent_id": "agent-7"},
        "subject": {
            "task_id": task,
            "worktree": "repo-chain",
            "paths": ["src/"],
            "epoch": seq,
            "lease_expires_at": issued_at + 3_600_000,
            "ttl_seconds": 3600,
        },
        "decision": "allow",
        "prev_receipt": prev,
        "signature": {"alg": "ed25519", "domain": str(AEF_RECEIPT_DOMAIN), "key_id": KEY_ID},
    }
    if expired:
        receipt["expires_at"] = NOW_MS - 1000
    receipt["receipt_id"] = receipt_id_for(receipt)
    unsigned = copy.deepcopy(receipt)
    value = Ed25519PrivateKey.from_private_bytes(_SEED).sign(
        AEF_RECEIPT_DOMAIN.preimage(canonical_json(unsigned))
    )
    receipt["signature"]["value"] = base64.urlsafe_b64encode(value).decode("ascii")
    return receipt


def _chain(seqs: range, *, expired: bool = False) -> list[dict[str, Any]]:
    """Return linked receipts for ``seqs``, the first linked to genesis or nothing."""
    receipts: list[dict[str, Any]] = []
    prev = GENESIS_RECEIPT if seqs.start == 1 else "aef1:" + "e" * 64
    for seq in seqs:
        receipt = _receipt(seq, prev, expired=expired)
        receipts.append(receipt)
        prev = receipt["receipt_id"]
    return receipts


def _forged(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return ``receipt`` with a signature that no longer verifies."""
    forged = copy.deepcopy(receipt)
    value = bytearray(base64.urlsafe_b64decode(forged["signature"]["value"]))
    value[0] ^= 0xFF
    forged["signature"]["value"] = base64.urlsafe_b64encode(bytes(value)).decode("ascii")
    return forged


def _vector(
    name: str,
    detail: str,
    receipts: list[dict[str, Any]],
    verdicts: list[str],
    chain: str,
    issues: list[tuple[str, int]],
) -> dict[str, Any]:
    return {
        "name": name,
        "detail": detail,
        "now_ms": NOW_MS,
        "receipts": receipts,
        "expected_receipt_verdicts": verdicts,
        "expected_chain": {"verdict": chain, "issues": [list(issue) for issue in issues]},
    }


def _vectors() -> list[dict[str, Any]]:
    """Return every chain vector, in a fixed order."""
    linked = _chain(range(1, 4))
    vectors = _link_vectors(linked) + _fork_vectors(linked) + _hole_vectors(linked)
    return sorted(vectors, key=lambda vector: str(vector["name"]))


def _link_vectors(linked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the vectors of whole, late-starting and mislinked chains."""
    four = _chain(range(1, 5))
    stray = _receipt(3, "aef1:" + "d" * 64)
    bad_start = _receipt(1, "aef1:" + "c" * 64)
    continuous, gap, broken = "CHAIN_CONTINUOUS", "CHAIN_GAP", "CHAIN_BROKEN_LINK"
    return [
        _vector(
            "c01-continuous",
            "three linked receipts from genesis",
            linked,
            ["VALID"] * 3,
            continuous,
            [],
        ),
        _vector(
            "c02-expired-continuous",
            "expired receipts are authentic and still link",
            _chain(range(1, 4), expired=True),
            ["EXPIRED"] * 3,
            continuous,
            [],
        ),
        _vector(
            "c03-missing-seq",
            "seq 3 is absent from the export",
            [four[0], four[1], four[3]],
            ["VALID"] * 3,
            gap,
            [(gap, 3)],
        ),
        _vector(
            "c04-missing-prev",
            "seq 3 names a predecessor that is not seq 2",
            [linked[0], linked[1], stray],
            ["VALID"] * 3,
            broken,
            [(broken, 3)],
        ),
        _vector(
            "c06-genesis-invalid",
            "seq 1 does not link to genesis",
            [bad_start],
            ["VALID"],
            "CHAIN_GENESIS_INVALID",
            [("CHAIN_GENESIS_INVALID", 1)],
        ),
        _vector(
            "c07-open-start",
            "a window from seq 5 links but starts late",
            _chain(range(5, 7)),
            ["VALID"] * 2,
            continuous,
            [("CHAIN_OPEN_START", 5)],
        ),
    ]


def _fork_vectors(linked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the vectors of two receipts claiming one place in a chain."""
    aged = _chain(range(1, 3), expired=True)
    rival = _receipt(2, aged[0]["receipt_id"], expired=True, task="rival-task")
    skipper = _receipt(3, linked[0]["receipt_id"])
    fork = "CHAIN_FORK"
    return [
        _vector(
            "c05-fork",
            "two different expired receipts hold seq 2 after seq 1",
            [aged[0], aged[1], rival],
            ["EXPIRED"] * 3,
            fork,
            [(fork, 2), (fork, 2)],
        ),
        _vector(
            "c10-prev-fork",
            "seq 2 and seq 3 both name seq 1 as their predecessor",
            [linked[0], linked[1], skipper],
            ["VALID"] * 3,
            "CHAIN_BROKEN_LINK",
            [("CHAIN_BROKEN_LINK", 3), (fork, 3)],
        ),
    ]


def _hole_vectors(linked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the vectors of a repeated receipt and of a receipt that fails."""
    old = _chain(range(1, 3), expired=True)
    return [
        _vector(
            "c08-duplicate",
            "an expired receipt appears twice",
            [old[0], old[1], old[1]],
            ["EXPIRED"] * 3,
            "CHAIN_DUPLICATE",
            [("CHAIN_DUPLICATE", 2)],
        ),
        _vector(
            "c09-hole-from-invalid",
            "a forged seq 2 is excluded and leaves a gap",
            [linked[0], _forged(linked[1]), linked[2]],
            ["VALID", "INVALID_SIGNATURE", "VALID"],
            "CHAIN_GAP",
            [("CHAIN_GAP", 2)],
        ),
    ]


def build() -> str:
    """Return the fixture text."""
    document = {
        "format": "aef-chain-conformance-v0.1",
        "description": (
            "AEF v0.1 chain-continuity vectors. Each export is verified receipt by "
            "receipt at now_ms with the trust below, then checked as a chain per log."
        ),
        "trust_store": {
            "logs": {LOG_ID: KEY_ID},
            "keys": {KEY_ID: {"public_key_hex": PUBLIC_KEY_HEX, "revoked": False}},
        },
        "private_key_seed_hex": _SEED.hex(),
        "genesis_receipt": GENESIS_RECEIPT,
        "vectors": _vectors(),
    }
    return json.dumps(document, indent=1, sort_keys=True) + "\n"


signed_receipt = _receipt
"""Public name for one signed receipt of the test log, shared with other vector tools."""

linked_receipts = _chain
"""Public name for a linked run of test-log receipts."""

forged_receipt = _forged
"""Public name for a receipt whose signature no longer verifies."""


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    """Write or check the fixture; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Rewrite the fixture.")
    mode.add_argument("--check", action="store_true", help="Fail if the fixture is stale.")
    args = parser.parse_args(argv)
    text = build()
    if args.write:
        FIXTURE.write_text(text, encoding="utf-8")
        out(f"wrote {FIXTURE}")
        return 0
    if FIXTURE.read_text(encoding="utf-8") != text:
        out(f"{FIXTURE} is stale; run tools/aef_chain_vectors.py --write")
        return 1
    out(f"{FIXTURE} is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
