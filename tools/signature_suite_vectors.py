# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — test vectors of the signature suites and the AEF tree-head countersignature
"""Build or check ``tests/fixtures/signature_suite_v0_1.json``.

The keys come from published test seeds: Ed25519 ``00 01 .. 1f`` and ML-DSA-65
``20 21 .. 3f``. ML-DSA signing is hedged, so two runs of ``--write`` give
different signatures. Vectors are therefore verify-only: ``--check`` rebuilds
the public key from the seeds and requires every stored vector to get its
recorded verdict.

Each suite vector is ``(suite, domain, message, signature)`` with the expected
verdict ``valid``, ``invalid`` or ``refused`` (the suite or domain is refused
before any verification). Each countersignature vector is a tree head from
``tests/fixtures/aef_sth_v0_1.json``, a countersignature, and its verdict.

Usage::

    PYTHONPATH=src python tools/signature_suite_vectors.py --write
    PYTHONPATH=src python tools/signature_suite_vectors.py --check
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from synapse_channel.core.aef_countersign import countersign_sth, verify_countersignature
from synapse_channel.core.aef_trust_file import parse_aef_trust
from synapse_channel.core.signature_suite import (
    COMPOSITE_LABEL,
    SUITE_ED25519,
    SUITE_HYBRID,
    SignatureSuiteError,
    SuiteKey,
    TrustedKey,
    preimage,
    verify,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "signature_suite_v0_1.json"
STH_FIXTURE = ROOT / "tests" / "fixtures" / "aef_sth_v0_1.json"
FORMAT = "synapse-signature-suite-vectors-v0.1"
ED25519_SEED = bytes(range(32))
ML_DSA_SEED = bytes(range(32, 64))
DOMAIN = "SYNAPSE-CHANNEL:VECTOR:v1"
MESSAGE = b"synapse signature suite vector"
_ED = 64


def hybrid_key() -> SuiteKey:
    """Return the published hybrid test key."""
    return SuiteKey(SUITE_HYBRID, ED25519_SEED, ML_DSA_SEED)


def _plain_halves(key: SuiteKey) -> bytes:
    """Sign each half over the plain domain preimage instead of the composite one."""
    from cryptography.hazmat.primitives.asymmetric import mldsa
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    plain = preimage(SUITE_ED25519, DOMAIN, MESSAGE)
    ed = Ed25519PrivateKey.from_private_bytes(key.ed25519_seed).sign(plain)
    ml = mldsa.MLDSA65PrivateKey.from_seed_bytes(key.ml_dsa_seed).sign(
        plain, context=DOMAIN.encode("utf-8")
    )
    return ed + ml


def _flip(data: bytes, index: int) -> bytes:
    return data[:index] + bytes([data[index] ^ 1]) + data[index + 1 :]


def _suite_vectors() -> list[dict[str, Any]]:
    hybrid = hybrid_key()
    ed_key = SuiteKey(SUITE_ED25519, ED25519_SEED)
    good = hybrid.sign(DOMAIN, MESSAGE)
    other = hybrid.sign(DOMAIN, MESSAGE)
    cases: list[tuple[str, str, str, bytes, str]] = [
        ("ed25519-valid", SUITE_ED25519, DOMAIN, ed_key.sign(DOMAIN, MESSAGE), "valid"),
        ("hybrid-valid", SUITE_HYBRID, DOMAIN, good, "valid"),
        ("hybrid-valid-second-hedged-signature", SUITE_HYBRID, DOMAIN, other, "valid"),
        ("hybrid-ed25519-half-corrupted", SUITE_HYBRID, DOMAIN, _flip(good, 3), "invalid"),
        ("hybrid-ml-dsa-half-corrupted", SUITE_HYBRID, DOMAIN, _flip(good, _ED + 7), "invalid"),
        ("hybrid-halves-swapped", SUITE_HYBRID, DOMAIN, good[_ED:] + good[:_ED], "invalid"),
        ("hybrid-truncated", SUITE_HYBRID, DOMAIN, good[:-1], "invalid"),
        ("hybrid-ml-dsa-half-alone", SUITE_HYBRID, DOMAIN, good[_ED:], "invalid"),
        ("hybrid-wrong-domain", SUITE_HYBRID, DOMAIN + "-other", good, "invalid"),
        ("hybrid-plain-preimage-halves", SUITE_HYBRID, DOMAIN, _plain_halves(hybrid), "invalid"),
        ("unknown-suite", "ed25519+ml-dsa-87/v1", DOMAIN, good, "refused"),
        ("nul-in-domain", SUITE_HYBRID, "SYNAPSE\x00CHANNEL", good, "refused"),
    ]
    return [
        {
            "name": name,
            "suite": suite,
            "domain": domain,
            "message_hex": MESSAGE.hex(),
            "signature_hex": signature.hex(),
            "public_key_hex": (hybrid if suite != SUITE_ED25519 else ed_key).public_bytes().hex(),
            "expected": expected,
        }
        for name, suite, domain, signature, expected in cases
    ]


def _countersign_vectors() -> list[dict[str, Any]]:
    sth_fixture = json.loads(STH_FIXTURE.read_text(encoding="utf-8"))
    head = sth_fixture["sth"]
    trust = parse_aef_trust(json.dumps(sth_fixture["trust"]).encode("utf-8"))
    document = countersign_sth(head, trust_store=trust, key=hybrid_key())
    resigned = copy.deepcopy(head)
    value = resigned["signature"]["value"]
    resigned["signature"]["value"] = ("A" if value[0] != "A" else "B") + value[1:]
    other_root = copy.deepcopy(head)
    other_root["root"] = "0" * 64
    return [
        {
            "name": "countersign-valid",
            "sth": head,
            "countersignature": document,
            "expected": "VALID",
        },
        {
            # Differs only in the head's Ed25519 signature bytes, so its digest differs.
            "name": "countersign-head-signature-differs",
            "sth": resigned,
            "countersignature": document,
            "expected": "MISMATCH",
        },
        {
            "name": "countersign-other-root",
            "sth": other_root,
            "countersignature": document,
            "expected": "MISMATCH",
        },
    ]


def build() -> str:
    """Return a fresh fixture text; its hybrid signatures differ on every call."""
    hybrid = hybrid_key()
    document = {
        "format": FORMAT,
        "composite_label": COMPOSITE_LABEL.decode("ascii"),
        "ed25519_seed_hex": ED25519_SEED.hex(),
        "ml_dsa_seed_hex": ML_DSA_SEED.hex(),
        "hybrid_key_id": hybrid.key_id(),
        "suite_vectors": _suite_vectors(),
        "countersign_vectors": _countersign_vectors(),
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def suite_verdict(vector: dict[str, Any]) -> str:
    """Return the verdict the implementation gives one suite vector."""
    try:
        good = verify(
            vector["suite"],
            bytes.fromhex(vector["public_key_hex"]),
            vector["domain"],
            bytes.fromhex(vector["message_hex"]),
            bytes.fromhex(vector["signature_hex"]),
        )
    except SignatureSuiteError:
        return "refused"
    return "valid" if good else "invalid"


def check(text: str) -> list[str]:
    """Return one line per vector whose verdict differs from the recorded one."""
    document = json.loads(text)
    hybrid = hybrid_key()
    problems: list[str] = []
    if document["hybrid_key_id"] != hybrid.key_id():
        problems.append("the hybrid key id does not come from the published seeds")
    for vector in document["suite_vectors"]:
        if (verdict := suite_verdict(vector)) != vector["expected"]:
            problems.append(f"{vector['name']}: {verdict}, expected {vector['expected']}")
    trust = {hybrid.key_id(): TrustedKey(SUITE_HYBRID, hybrid.public_bytes())}
    for vector in document["countersign_vectors"]:
        report = verify_countersignature(vector["countersignature"], vector["sth"], trust=trust)
        if report.verdict.value != vector["expected"]:
            problems.append(
                f"{vector['name']}: {report.verdict.value}, expected {vector['expected']}"
            )
    return problems


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    """Write or check the fixture; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Write fresh vectors.")
    mode.add_argument("--check", action="store_true", help="Verify the stored vectors.")
    args = parser.parse_args(argv)
    if args.write:
        FIXTURE.write_text(build(), encoding="utf-8")
        out(f"wrote {FIXTURE}")
        return 0
    problems = check(FIXTURE.read_text(encoding="utf-8"))
    for problem in problems:
        out(problem)
    if problems:
        return 1
    out(f"{FIXTURE} verifies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
