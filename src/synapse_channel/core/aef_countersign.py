# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a hybrid post-quantum countersignature over an AEF signed tree head
"""Countersign an AEF tree head with a hybrid Ed25519 + ML-DSA-65 key.

A tree head commits to every receipt of its log up to its size. A hybrid key
countersigning that head while Ed25519 is still sound anchors those receipts:
after a break of Ed25519, an export whose root matches the countersigned root
is still bound to the post-quantum signature, whatever its Ed25519 receipt
signatures say.

What a countersignature proves is limited:

* it proves that the post-quantum key holder endorsed this exact head, at a
  time it does not prove;
* the countersigner checks the head with Ed25519, so a countersignature made
  after a break may endorse a forged head.

Countersign routinely, and keep the countersignatures in custody independent
of the log host.

The document (``aef-sth-countersignature`` ``0.1``) binds the head by the
SHA-256 of the head's canonical JSON, signature included, and repeats its
``log_id``, ``tree_size`` and ``root``. Its signature covers the canonical JSON
of the document without ``signature.value``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from synapse_channel.core.aef_canonical import AefCanonicalizationError, canonical_json
from synapse_channel.core.aef_verification import (
    AefInclusionVerdict,
    AefTrustStore,
    verified_sth,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.signature_suite import (
    SUITE_HYBRID,
    SignatureSuiteError,
    SuiteKey,
    TrustedKey,
    b64,
    unb64,
    verify,
)

COUNTERSIGN_FORMAT = "aef-sth-countersignature"
COUNTERSIGN_VERSION = "0.1"
COUNTERSIGN_DOMAIN = "SYNAPSE-CHANNEL:AEF-STH-COUNTERSIGN:v1"
_FIELDS = frozenset({"format", "version", "sth_sha256", "log_id", "tree_size", "root", "signature"})
_SIGNATURE_FIELDS = frozenset({"alg", "domain", "key_id", "value"})


class AefCountersignError(SynapseError, ValueError):
    """A tree head cannot be countersigned."""

    code = "aef_countersign"


class CountersignVerdict(str, Enum):
    """The verdict on one countersignature against one tree head."""

    VALID = "VALID"
    UNTRUSTED = "UNTRUSTED"
    INVALID = "INVALID"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class CountersignReport:
    """A countersignature's verdict, the key it names and the head it binds."""

    verdict: CountersignVerdict
    key_id: str
    sth_sha256: str


def sth_digest(sth: Mapping[str, object]) -> str:
    """Return the SHA-256 of a tree head's canonical JSON, signature included."""
    return hashlib.sha256(canonical_json(sth)).hexdigest()


def _unsigned(document: Mapping[str, object], envelope: Mapping[str, object]) -> bytes:
    """Return the canonical JSON the signature covers: the document without its value."""
    unsigned = dict(document)
    unsigned["signature"] = {key: value for key, value in envelope.items() if key != "value"}
    return canonical_json(unsigned)


def countersign_sth(
    sth: Mapping[str, object], *, trust_store: AefTrustStore, key: SuiteKey
) -> dict[str, object]:
    """Return a countersignature of a tree head that verifies under ``trust_store``.

    Raises
    ------
    AefCountersignError
        When the key is not a hybrid key, or the head does not verify.
    """
    if key.suite != SUITE_HYBRID:
        raise AefCountersignError(f"a countersignature uses {SUITE_HYBRID}, not {key.suite}")
    checked = verified_sth(sth, trust_store=trust_store)
    if isinstance(checked, AefInclusionVerdict):
        raise AefCountersignError(f"the tree head does not verify: {checked.value}")
    log_id, tree_size, root = checked
    envelope: dict[str, object] = {
        "alg": SUITE_HYBRID,
        "domain": COUNTERSIGN_DOMAIN,
        "key_id": key.key_id(),
    }
    document: dict[str, object] = {
        "format": COUNTERSIGN_FORMAT,
        "version": COUNTERSIGN_VERSION,
        "sth_sha256": sth_digest(sth),
        "log_id": log_id,
        "tree_size": tree_size,
        "root": root,
        "signature": envelope,
    }
    envelope["value"] = b64(key.sign(COUNTERSIGN_DOMAIN, _unsigned(document, envelope)))
    return document


def _envelope(document: Mapping[str, object]) -> Mapping[str, object] | None:
    if set(document) != _FIELDS:
        return None
    if (
        document.get("format") != COUNTERSIGN_FORMAT
        or document.get("version") != COUNTERSIGN_VERSION
    ):
        return None
    envelope = document.get("signature")
    if not isinstance(envelope, Mapping) or set(envelope) != _SIGNATURE_FIELDS:
        return None
    if envelope.get("alg") != SUITE_HYBRID or envelope.get("domain") != COUNTERSIGN_DOMAIN:
        return None
    if not isinstance(envelope.get("key_id"), str):
        return None
    return envelope


def verify_countersignature(
    document: Mapping[str, object],
    sth: Mapping[str, object],
    *,
    trust: Mapping[str, TrustedKey],
) -> CountersignReport:
    """Return the verdict on ``document`` as a countersignature of ``sth``.

    The checks run in this order, and the first that fails gives the verdict:

    1. ``INVALID``: the document is malformed;
    2. ``UNTRUSTED``: its key is not in ``trust`` as a hybrid key, or the trust
       file binds the key to other logs than the document's ``log_id``;
    3. ``INVALID``: its signature fails;
    4. ``MISMATCH``: it is authentic but binds another head.

    The verdict says nothing about whether ``sth`` itself is authentic or
    whether an export is complete up to it; a caller checks that separately.
    """
    claimed = document.get("sth_sha256")
    named = str(claimed) if isinstance(claimed, str) else ""
    envelope = _envelope(document)
    if envelope is None:
        return CountersignReport(CountersignVerdict.INVALID, "", named)
    key_id = str(envelope["key_id"])
    trusted = trust.get(key_id)
    if (
        trusted is None
        or trusted.suite != SUITE_HYBRID
        or (trusted.log_ids is not None and document.get("log_id") not in trusted.log_ids)
    ):
        return CountersignReport(CountersignVerdict.UNTRUSTED, key_id, named)
    try:
        signature = unb64(envelope["value"], "countersignature value")
        authentic = verify(
            SUITE_HYBRID,
            trusted.public,
            COUNTERSIGN_DOMAIN,
            _unsigned(document, envelope),
            signature,
        )
    except (SignatureSuiteError, AefCanonicalizationError):
        authentic = False
    if not authentic:
        return CountersignReport(CountersignVerdict.INVALID, key_id, named)
    try:
        bound = (
            claimed == sth_digest(sth)
            and document.get("log_id") == sth.get("log_id")
            and document.get("tree_size") == sth.get("tree_size")
            and document.get("root") == sth.get("root")
        )
    except AefCanonicalizationError:
        bound = False
    verdict = CountersignVerdict.VALID if bound else CountersignVerdict.MISMATCH
    return CountersignReport(verdict, key_id, named)
