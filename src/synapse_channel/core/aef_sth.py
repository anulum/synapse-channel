# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — AEF signed tree heads: issue one, and check an export against it
"""Sign an AEF tree head over a log, and tell whether an export is complete up to it.

A signed tree head (STH) commits to the first ``tree_size`` receipts of one log:
the RFC 6962 Merkle root over their leaf hashes ``SHA-256(0x00 || JCS(receipt))``,
in ``seq`` order, with leaf index ``seq - 1``. :func:`sign_aef_sth` issues one
from re-verified receipts. :func:`verify_aef_sth` compares an export with one and
returns a single verdict, first match wins:

1. ``STH_UNTRUSTED`` or ``STH_INVALID`` when the tree head itself fails;
2. ``STH_TRUNCATED`` when any position ``1..tree_size`` has no receipt (a cut-off
   end, an inner gap, or a rollback below a signed state);
3. ``STH_ROOT_MISMATCH`` when a position holds two different receipts (a fork), or
   the root over the positions differs (a rewritten history);
4. ``STH_COMPLETE`` otherwise; receipts past ``tree_size`` are growth.

Every receipt of the log is taken as presented, including ones that fail their own
verification. A receipt without a positive integer ``seq``, or one the canonical
form cannot represent, fills no position.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from synapse_channel.core.aef_canonical import AefCanonicalizationError, canonical_json
from synapse_channel.core.aef_chain import verify_aef_chain
from synapse_channel.core.aef_domain import AEF_STH_DOMAIN
from synapse_channel.core.aef_emission import AefEmissionError, derive_aef_log_id
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import (
    AefInclusionVerdict,
    AefTrustedKey,
    AefTrustStore,
    AefVerification,
    aef_leaf_hash,
    verified_sth,
    verify_aef_receipt,
)
from synapse_channel.core.merkle import merkle_tree_hash
from synapse_channel.core.receipt_signing import ReceiptSigningKey, receipt_key_id


class AefSthVerdict(str, Enum):
    """How an export compares with one signed tree head."""

    STH_COMPLETE = "STH_COMPLETE"
    STH_TRUNCATED = "STH_TRUNCATED"
    STH_ROOT_MISMATCH = "STH_ROOT_MISMATCH"
    STH_INVALID = "STH_INVALID"
    STH_UNTRUSTED = "STH_UNTRUSTED"


@dataclass(frozen=True)
class AefSthReport:
    """One tree head's verdict for an export.

    Parameters
    ----------
    log_id, tree_size : str or None, int or None
        The tree head's log and size, when the tree head itself verified.
    verdict : AefSthVerdict
        The single verdict, by the precedence in the module docstring.
    detail : str
        One line naming the first missing or forked position, or the root.
    """

    log_id: str | None
    tree_size: int | None
    verdict: AefSthVerdict
    detail: str


def _seq(receipt: Mapping[str, object]) -> int | None:
    """Return a positive integer ``seq``, or None for any other value."""
    value = receipt.get("seq")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _positions(
    receipts: Sequence[Mapping[str, object]], log_id: str, tree_size: int
) -> dict[int, set[bytes]]:
    """Map each position ``1..tree_size`` to the distinct leaf hashes it holds."""
    positions: dict[int, set[bytes]] = {}
    for receipt in receipts:
        seq = _seq(receipt)
        if receipt.get("log_id") != log_id or seq is None or seq > tree_size:
            continue
        try:
            leaf = aef_leaf_hash(receipt)
        except AefCanonicalizationError:
            continue
        positions.setdefault(seq, set()).add(leaf)
    return positions


def verify_aef_sth(
    receipts: Sequence[Mapping[str, object]],
    sth: Mapping[str, object],
    *,
    trust_store: AefTrustStore,
) -> AefSthReport:
    """Compare the receipts of an export with one signed tree head."""
    checked = verified_sth(sth, trust_store=trust_store)
    if isinstance(checked, AefInclusionVerdict):
        verdict = (
            AefSthVerdict.STH_UNTRUSTED
            if checked is AefInclusionVerdict.STH_UNTRUSTED
            else AefSthVerdict.STH_INVALID
        )
        return AefSthReport(None, None, verdict, "the tree head itself does not verify")
    log_id, tree_size, root = checked
    positions = _positions(receipts, log_id, tree_size)
    missing = [seq for seq in range(1, tree_size + 1) if seq not in positions]
    if missing:
        return AefSthReport(
            log_id,
            tree_size,
            AefSthVerdict.STH_TRUNCATED,
            f"{len(missing)} of {tree_size} signed positions missing, first at seq {missing[0]}",
        )
    forked = [seq for seq in range(1, tree_size + 1) if len(positions[seq]) > 1]
    if forked:
        return AefSthReport(
            log_id,
            tree_size,
            AefSthVerdict.STH_ROOT_MISMATCH,
            f"differing receipts at seq {forked[0]}",
        )
    leaves = [next(iter(positions[seq])) for seq in range(1, tree_size + 1)]
    computed = merkle_tree_hash(leaves).hex()
    if computed != root:
        return AefSthReport(
            log_id,
            tree_size,
            AefSthVerdict.STH_ROOT_MISMATCH,
            f"root {computed} differs from the signed root",
        )
    return AefSthReport(
        log_id, tree_size, AefSthVerdict.STH_COMPLETE, f"seq 1..{tree_size} match the signed root"
    )


def sign_aef_sth(
    receipts: Sequence[Mapping[str, object]], *, hub_id: str, signing_key: ReceiptSigningKey
) -> dict[str, object]:
    """Return a tree head over a whole log, signed by that log's key.

    The receipts must be the log's receipts in ``seq`` order, starting at 1 and
    contiguous, each authentic under the key and chained to the one before.
    The timestamp is the last receipt's ``issued_at``, so the same log always
    gives the same tree head; it is not a freshness signal.

    Raises
    ------
    AefEmissionError
        When the key does not match its id, the log is empty, a receipt is of
        another log, not authentic, out of order, or the chain is broken.
    """
    public_key = signing_key.private_key.public_key().public_bytes_raw()
    if receipt_key_id(public_key) != signing_key.key_id:
        raise AefEmissionError("AEF signing key id does not match its public key")
    log_id = derive_aef_log_id(hub_id, public_key)
    if not receipts:
        raise AefEmissionError("an AEF tree head needs at least one receipt")
    trust = AefTrustStore(
        keys={signing_key.key_id: AefTrustedKey(public_key)}, logs={log_id: signing_key.key_id}
    )
    checked: list[tuple[Mapping[str, object], AefVerification]] = []
    for position, receipt in enumerate(receipts, start=1):
        if receipt.get("log_id") != log_id or _seq(receipt) != position:
            raise AefEmissionError(
                f"receipt {position} is not seq {position} of log {log_id[:16]}; "
                "a tree head covers a log from seq 1 without gaps"
            )
        issued_at = receipt.get("issued_at")
        if isinstance(issued_at, bool) or not isinstance(issued_at, int):
            raise AefEmissionError(f"receipt seq {position} has no integer issued_at")
        # Checked at its own issue time, so a receipt that has expired since
        # is still covered: expiry is not a defect of the log.
        outcome = verify_aef_receipt(receipt, trust_store=trust, now_ms=issued_at)
        if outcome.verdict is not AefVerdictCode.VALID:
            raise AefEmissionError(f"receipt seq {position} is {outcome.verdict.value}")
        checked.append((receipt, outcome))
    if not all(chain.continuous for chain in verify_aef_chain(checked)):
        raise AefEmissionError("the log's receipt chain is broken")
    head: dict[str, object] = {
        "aef": "0.1",
        "log_id": log_id,
        "tree_size": len(receipts),
        "root": merkle_tree_hash([aef_leaf_hash(receipt) for receipt in receipts]).hex(),
        "timestamp": receipts[-1]["issued_at"],
    }
    envelope: dict[str, object] = {
        "alg": "ed25519",
        "domain": str(AEF_STH_DOMAIN),
        "key_id": signing_key.key_id,
    }
    head["signature"] = envelope
    value = signing_key.private_key.sign(AEF_STH_DOMAIN.preimage(canonical_json(head)))
    envelope["value"] = base64.urlsafe_b64encode(value).decode("ascii")
    return head
