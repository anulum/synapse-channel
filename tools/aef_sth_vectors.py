# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reproducible AEF signed-tree-head vectors
"""Build ``tests/fixtures/aef_sth_v0_1.json``, the AEF signed-tree-head vectors.

One tree head covers the first four receipts of the test log. Each vector is an
export (its receipts) and the verdict it must get against that head, or against
the head named in the vector. Receipts come from ``tools/aef_chain_vectors.py``,
and both are signed with the published test key (the Ed25519 seed
``00 01 .. 1f``). Ed25519 signatures are deterministic and the head's timestamp
is its last receipt's ``issued_at``, so the file is reproducible byte for byte.

Usage::

    PYTHONPATH=src python tools/aef_sth_vectors.py --write
    PYTHONPATH=src python tools/aef_sth_vectors.py --check
"""

from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.aef_sth import sign_aef_sth
from synapse_channel.core.receipt_signing import ReceiptSigningKey


def _sibling(name: str) -> Any:
    """Load a sibling tool module by path, so this runs from any directory."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


chain = _sibling("aef_chain_vectors")

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "aef_sth_v0_1.json"
HUB_ID = "hub-ops-01.example.com"
_OTHER_SEED = bytes(range(32, 64))


def _key(seed: bytes, key_id: str) -> ReceiptSigningKey:
    return ReceiptSigningKey(key_id=key_id, private_key=Ed25519PrivateKey.from_private_bytes(seed))


def _vector(
    name: str, detail: str, receipts: list[dict[str, Any]], expected: str, **extra: Any
) -> dict[str, Any]:
    return {"name": name, "detail": detail, "receipts": receipts, "expected": expected, **extra}


def _export_vectors() -> list[dict[str, Any]]:
    """Return the vectors that vary the export against the published head."""
    grown = chain.linked_receipts(range(1, 7))
    signed = grown[:4]
    rewritten = chain.signed_receipt(3, signed[1]["receipt_id"], task="rewritten-task")
    bad_seq = copy.deepcopy(signed)
    bad_seq[1]["seq"] = "2"
    return [
        _vector("sth-complete", "the four signed positions match", signed, "STH_COMPLETE"),
        _vector(
            "sth-complete-with-growth",
            "two receipts past tree_size are growth",
            grown,
            "STH_COMPLETE",
        ),
        _vector("sth-truncated-end", "seq 4 was cut off", signed[:3], "STH_TRUNCATED"),
        _vector(
            "sth-inner-gap-with-growth",
            "seq 2 is missing although the export goes past tree_size",
            [grown[0], *grown[2:]],
            "STH_TRUNCATED",
        ),
        _vector(
            "sth-rewritten",
            "seq 3 holds another receipt validly signed by the log key",
            [*signed[:2], rewritten, signed[3]],
            "STH_ROOT_MISMATCH",
        ),
        _vector(
            "sth-fork",
            "seq 3 holds two different receipts",
            [*signed, rewritten],
            "STH_ROOT_MISMATCH",
        ),
        _vector(
            "sth-identical-duplicate",
            "an identical second copy of seq 2 counts once",
            [*signed, copy.deepcopy(signed[1])],
            "STH_COMPLETE",
        ),
        _vector(
            "sth-forged-past-size",
            "a forged receipt past tree_size does not change the verdict",
            [*signed, chain.forged_receipt(grown[4])],
            "STH_COMPLETE",
        ),
        _vector(
            "sth-non-integer-seq",
            'a receipt whose seq is the string "2" fills no position',
            bad_seq,
            "STH_TRUNCATED",
        ),
    ]


def _head_vectors(head: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the vectors whose tree head itself fails."""
    signed = chain.linked_receipts(range(1, 5))
    tampered = copy.deepcopy(head)
    tampered["root"] = "0" * 64
    foreign = copy.deepcopy(head)
    foreign["log_id"] = "f" * 64
    stranger = copy.deepcopy(head)
    stranger_key = Ed25519PrivateKey.from_private_bytes(_OTHER_SEED)
    stranger["signature"]["value"] = base64.urlsafe_b64encode(
        stranger_key.sign(b"not the log key")
    ).decode("ascii")
    return [
        _vector(
            "sth-tampered-root",
            "the head's root was changed after signing",
            signed,
            "STH_INVALID",
            sth=tampered,
        ),
        _vector(
            "sth-untrusted-log",
            "the head names a log the trust file does not hold",
            signed,
            "STH_UNTRUSTED",
            sth=foreign,
        ),
        _vector(
            "sth-wrong-signature",
            "the head's signature was made by another key",
            signed,
            "STH_INVALID",
            sth=stranger,
        ),
    ]


def _vectors(head: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every vector, in a fixed order."""
    vectors = _export_vectors() + _head_vectors(head)
    return sorted(vectors, key=lambda vector: str(vector["name"]))


def build() -> str:
    """Return the fixture text."""
    head = sign_aef_sth(
        chain.linked_receipts(range(1, 5)),
        hub_id=HUB_ID,
        signing_key=_key(bytes(range(32)), chain.KEY_ID),
    )
    document = {
        "format": "aef-sth-vectors-v0.1",
        "trust": {
            "format": "aef-trust-v0.1",
            "keys": {chain.KEY_ID: {"public_key_hex": chain.PUBLIC_KEY_HEX, "revoked": False}},
            "logs": {chain.LOG_ID: chain.KEY_ID},
        },
        "sth": head,
        "leaf_rule": "leaf_hash = SHA-256(0x00 || JCS(receipt)); leaf index = seq - 1",
        "vectors": _vectors(head),
    }
    return json.dumps(document, indent=1, sort_keys=True) + "\n"


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
        out(f"{FIXTURE} is stale; run tools/aef_sth_vectors.py --write")
        return 1
    out(f"{FIXTURE} is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
