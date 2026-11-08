# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the result-verification record, owned by the core
"""The record that keeps communication, effect and result apart.

The record type lives in the core because the hub validates and journals
verification records (operation records). The verifiers that produce it read
the world and stay in :mod:`synapse_channel.result_verification`, which
re-exports this type unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

SCHEMA = "synapse.result-verification.v1"
"""Version of the result-verification record."""

EFFECTS = frozenset({"observed", "absent", "unknown"})
RESULTS = frozenset({"verified", "not_verified", "not_checked"})


@dataclass(frozen=True, slots=True)
class ResultVerification:
    """One verifier's account of communication, effect and result, kept apart."""

    operation_id: str
    verifier: str
    communication: str
    effect: str
    result: str
    observation: str
    evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse combinations that would blur the three facts."""
        if self.effect not in EFFECTS or self.result not in RESULTS:
            raise ValueError("unknown effect or result state")
        if self.result == "verified" and self.effect != "observed":
            raise ValueError("a result is verified only from an observed effect")

    @property
    def verified(self) -> bool:
        """Return whether the requested result itself was verified."""
        return self.result == "verified"

    def to_json(self) -> dict[str, object]:
        """Return the JSON record, schema first-class and evidence copied."""
        return {
            "schema": SCHEMA,
            "operation_id": self.operation_id,
            "verifier": self.verifier,
            "communication": self.communication,
            "effect": self.effect,
            "result": self.result,
            "observation": self.observation,
            "evidence": dict(self.evidence),
        }


def verification_from_json(document: object) -> ResultVerification:
    """Decode one ``synapse.result-verification.v1`` record.

    The record's own rules apply (a ``verified`` result needs an ``observed``
    effect). Evidence values must be text; nothing else is read.

    Parameters
    ----------
    document : object
        The decoded JSON record.

    Returns
    -------
    ResultVerification
        The validated record.

    Raises
    ------
    ValueError
        If the document is not such a record or a field has the wrong type.
        Rejected values are not echoed.
    """
    if not isinstance(document, Mapping):
        raise ValueError("a verification record must be a JSON object")
    if document.get("schema") != SCHEMA:
        raise ValueError(f"the record is not a {SCHEMA} record")
    texts: dict[str, str] = {}
    for key in ("operation_id", "verifier", "communication", "effect", "result", "observation"):
        value = document.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"verification record field {key} must be non-empty text")
        texts[key] = value
    evidence = document.get("evidence", {})
    if not isinstance(evidence, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in evidence.items()
    ):
        raise ValueError("verification evidence must map text to text")
    return ResultVerification(
        texts["operation_id"],
        texts["verifier"],
        texts["communication"],
        texts["effect"],
        texts["result"],
        texts["observation"],
        dict(evidence),
    )
