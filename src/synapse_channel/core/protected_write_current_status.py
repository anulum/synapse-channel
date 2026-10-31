# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — writer-side current authority status
"""Verify a fresh actor-ordered status response for one begun reservation."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from typing import Any, cast

from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment


def _require_begun_phase(
    source: dict[str, Any], body: dict[str, Any], begun: ProtectedWriteReservation
) -> None:
    """Require the retained begin's execution phase and exact transition sequence."""
    if (
        source["type"] != "protected_write_begin"
        or body["operation_phase"] != "executing"
        or body["revocation_phase"] != "open"
        or body["begin_sequence"] != begun.transition_sequence
    ):
        raise ValueError("current status requires the exact enrolled writer begin")


class ProtectedWriteCurrentStatus:
    """Check a begun reservation against a fresh signed authority read.

    The enrolled writer calls this before each effect. ``exchange`` must be a
    bounded exchange with the separately isolated authority listener. Neither
    this status check nor its result excludes a revoke after the read; the
    supervised writer also requires physical custody and one-time step records.
    """

    def __init__(
        self,
        *,
        begun: ProtectedWriteReservation,
        enrollment: ProtectedSessionEnrollment,
        authority_key: MessageAuthKey,
        limits: ProtectedWriteProposalLimits,
        reason_codes: frozenset[str],
        exchange: Callable[[bytes], bytes | str],
        next_sequence: Callable[[], int],
        clock: Callable[[], float],
        response_replay: MessageReplayCache,
    ) -> None:
        """Bind status exchange to the exact enrolled durable writer begin.

        Retain the authority verification key, wire limits, reason codes, replay
        cache, sequence source and clock supplied by the caller. Construction
        checks the begin, admission and enrollment scope without contacting the
        authority. Raise ValueError for a conflicting phase or writer scope;
        malformed serialized begin data also refuses construction.
        """
        source = cast(dict[str, Any], json.loads(begun.request_bytes))
        body = cast(dict[str, Any], json.loads(begun.result_bytes)["body"])
        admission = cast(dict[str, Any], json.loads(begun.admission.request_bytes))
        _require_begun_phase(source, body, begun)
        if (
            enrollment.principal != source["sender"]
            or enrollment.session_id != source["session_id"]
            or enrollment.writer_incarnation != body["writer_incarnation"]
            or enrollment.principal != body["writer_principal"]
            or "status" not in enrollment.verbs
            or enrollment.target != source["target"]
            or enrollment.authority_id != source["authority_id"]
            or enrollment.authority_continuity != source["authority_continuity"]
            or enrollment.transaction_id != source["transaction_id"]
            or enrollment.proposal_sha256 != source["proposal_sha256"]
            or enrollment.enrollment_revision != source["enrollment_revision"]
            or any(
                source[field] != admission[field]
                for field in (
                    "target",
                    "authority_id",
                    "authority_continuity",
                    "transaction_id",
                    "proposal_sha256",
                    "enrollment_revision",
                )
            )
        ):
            raise ValueError("current status requires the exact enrolled writer begin")
        self._begun = begun
        self._source = source
        self._enrollment = enrollment
        self._authority_key = authority_key
        self._limits = limits
        self._reason_codes = reason_codes
        self._exchange = exchange
        self._next_sequence = next_sequence
        self._clock = clock
        self._response_replay = response_replay

    def _status_request(self, now: float) -> bytes:
        """Create one fresh signed request bound to the retained writer and reservation."""
        request = {
            key: value
            for key, value in self._source.items()
            if key not in {"auth", "signature", "client_timestamp"}
        }
        request.update(
            type="protected_write_status",
            timestamp=now,
            request_id="current-" + secrets.token_hex(16),
            body={"reservation_id": self._begun.admission.reservation_id},
        )
        signed = sign_frame(
            request,
            key=self._enrollment.key,
            nonce=secrets.token_hex(16),
            sequence=self._next_sequence(),
            timestamp=now,
        )
        return json.dumps(signed, sort_keys=True, separators=(",", ":")).encode("ascii")

    def __call__(self) -> ProtectedWriteReservation:
        """Return the pinned begin only when the authority still reports it open.

        A fresh signed status response is bound to a new request ID, nonce and
        the exact reservation. The enrolled sequence source must retain its
        strict floor across requests and writer restarts when that profile is on.
        No unverified response or transport failure permits an effect.

        Returns
        -------
        ProtectedWriteReservation
            The original begin after its current authority response is verified.

        Raises
        ------
        ValueError
            If enrollment is unavailable or the response lacks the exact current
            signature, request binding or open execution state.

        Notes
        -----
        Transport exceptions propagate from the enrolled bounded exchange. A
        caller must stop before its effect when this read does not succeed.
        """
        now = self._clock()
        if (
            self._enrollment.revoked
            or self._enrollment.key.revoked
            or now >= self._enrollment.expires_at
        ):
            raise ValueError("current writer status enrollment is unavailable")
        raw_request = self._status_request(now)
        raw_response = self._exchange(raw_request)
        if type(raw_response) is not bytes:
            raise ValueError("current status requires immutable response bytes")
        parsed = parse_protected_write_result(
            raw_response,
            request=raw_request,
            limits=self._limits,
            reason_codes=self._reason_codes,
        )
        response = cast(dict[str, Any], json.loads(parsed.canonical_bytes))
        if (
            verify_frame(
                response,
                keys={self._authority_key.key_id: self._authority_key},
                replay_cache=self._response_replay,
                now=self._clock(),
                required_sender=self._source["target"],
            )
            != VerificationResult.OK
        ):
            raise ValueError("current authority status signature refused")
        body = cast(dict[str, Any], response["body"])
        begun = cast(dict[str, Any], json.loads(self._begun.result_bytes)["body"])
        if (
            body["disposition"] != "known"
            or body["reservation_id"] != self._begun.admission.reservation_id
            or body["operation_phase"] != "executing"
            or body["revocation_phase"] != "open"
            or body["outcome"] is not None
            or any(
                body[field] != begun[field]
                for field in (
                    "admission_sequence",
                    "begin_sequence",
                    "writer_principal",
                    "writer_incarnation",
                )
            )
        ):
            raise ValueError("current authority status differs from enrolled begin")
        return self._begun
