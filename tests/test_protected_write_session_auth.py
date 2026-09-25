# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real protected request session authentication tests
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from synapse_channel.core.message_auth import MessageAuthKey, MessageReplayCache, sign_frame
from synapse_channel.core.protected_write_session_auth import (
    ProtectedSessionEnrollment,
    authenticate_protected_request,
    recheck_authenticated_protected_request,
)
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request

NOW = 1788649200.0
KEY = MessageAuthKey("session-key", b"a" * 32, frozenset({"EXAMPLE/author"}))


def enrollment() -> ProtectedSessionEnrollment:
    request = _request("status")
    return ProtectedSessionEnrollment(
        session_id="session",
        principal="EXAMPLE/author",
        target="EXAMPLE/authority",
        authority_id="authority",
        authority_continuity="continuity",
        enrollment_revision="enrollment",
        transaction_id="transaction",
        proposal_sha256=str(request["proposal_sha256"]),
        verbs=frozenset({"prepare", "admit", "begin", "settle", "revoke", "status", "recover"}),
        expires_at=NOW + 30,
        key=KEY,
    )


@pytest.mark.parametrize(
    "verb", ["prepare", "admit", "begin", "settle", "revoke", "status", "recover"]
)
def test_real_signed_wire_request_and_current_revocation(verb: str) -> None:
    registry = {"session": enrollment()}
    raw = json.dumps(sign_frame(_request(verb), key=KEY, nonce="nonce", sequence=1, timestamp=NOW))
    replay = MessageReplayCache(window_seconds=10, max_entries=32)
    parsed = authenticate_protected_request(
        raw,
        limits=LIMITS,
        enrollments=registry,
        authenticated_principal="EXAMPLE/author",
        replay_cache=replay,
        now=NOW,
    )
    assert json.loads(parsed.parsed.canonical_bytes)["type"] == f"protected_write_{verb}"
    recheck_authenticated_protected_request(
        parsed,
        enrollments=registry,
        authenticated_principal="EXAMPLE/author",
        now=NOW,
    )
    with pytest.raises(ValueError, match="replayed"):
        authenticate_protected_request(
            raw,
            limits=LIMITS,
            enrollments=registry,
            authenticated_principal="EXAMPLE/author",
            replay_cache=replay,
            now=NOW,
        )
    registry["session"] = replace(registry["session"], revoked=True)
    with pytest.raises(ValueError, match="unavailable"):
        recheck_authenticated_protected_request(
            parsed,
            enrollments=registry,
            authenticated_principal="EXAMPLE/author",
            now=NOW,
        )


def test_key_rotation_invalidates_previously_authenticated_request() -> None:
    grant = enrollment()
    registry = {"session": grant}
    raw = json.dumps(sign_frame(_request("status"), key=KEY, nonce="n", sequence=1, timestamp=NOW))
    authenticated = authenticate_protected_request(
        raw,
        limits=LIMITS,
        enrollments=registry,
        authenticated_principal="EXAMPLE/author",
        replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
        now=NOW,
    )
    registry["session"] = replace(grant, key=replace(KEY, secret=b"b" * 32))
    with pytest.raises(ValueError, match="replaced"):
        recheck_authenticated_protected_request(
            authenticated,
            enrollments=registry,
            authenticated_principal="EXAMPLE/author",
            now=NOW,
        )


@pytest.mark.parametrize("later", [NOW - 1, NOW + 11])
def test_delayed_or_clock_reversed_mutation_loses_request_freshness(later: float) -> None:
    registry = {"session": enrollment()}
    raw = json.dumps(sign_frame(_request("status"), key=KEY, nonce="n", sequence=1, timestamp=NOW))
    authenticated = authenticate_protected_request(
        raw,
        limits=LIMITS,
        enrollments=registry,
        authenticated_principal="EXAMPLE/author",
        replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
        now=NOW,
    )
    with pytest.raises(ValueError, match="freshness"):
        recheck_authenticated_protected_request(
            authenticated,
            enrollments=registry,
            authenticated_principal="EXAMPLE/author",
            now=later,
        )


@pytest.mark.parametrize(
    "field",
    [
        "session_id",
        "sender",
        "target",
        "authority_id",
        "authority_continuity",
        "enrollment_revision",
        "transaction_id",
        "proposal_sha256",
    ],
)
def test_valid_signature_cannot_expand_enrollment(field: str) -> None:
    request = _request("status")
    request[field] = "b" * 64 if field == "proposal_sha256" else "other"
    raw = json.dumps(sign_frame(request, key=KEY, nonce="nonce", sequence=1, timestamp=NOW))
    with pytest.raises(ValueError):
        authenticate_protected_request(
            raw,
            limits=LIMITS,
            enrollments={"session": enrollment()},
            authenticated_principal="EXAMPLE/author",
            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
            now=NOW,
        )


@pytest.mark.parametrize(
    "case",
    ["expired", "revoked-key", "wrong-key", "verb", "forged", "missing", "principal", "clock"],
)
def test_refusals_do_not_consume_valid_request(case: str) -> None:
    grant = enrollment()
    request = sign_frame(_request("status"), key=KEY, nonce="nonce", sequence=1, timestamp=NOW)
    valid = json.dumps(request)
    principal = "EXAMPLE/author"
    now = NOW
    if case == "expired":
        grant = replace(grant, expires_at=NOW)
    elif case == "revoked-key":
        grant = replace(grant, key=replace(KEY, revoked=True))
    elif case == "wrong-key":
        grant = replace(grant, key=replace(KEY, key_id="other"))
    elif case == "verb":
        grant = replace(grant, verbs=frozenset({"admit"}))
    elif case == "forged":
        request["auth"]["value"] = "0" * 64
    elif case == "missing":
        del request["auth"]
    elif case == "principal":
        principal = "EXAMPLE/attacker"
    else:
        now = float("nan")
    replay = MessageReplayCache(window_seconds=10, max_entries=32)
    with pytest.raises(ValueError):
        authenticate_protected_request(
            json.dumps(request),
            limits=LIMITS,
            enrollments={"session": grant},
            authenticated_principal=principal,
            replay_cache=replay,
            now=now,
        )
    authenticate_protected_request(
        valid,
        limits=LIMITS,
        enrollments={"session": enrollment()},
        authenticated_principal="EXAMPLE/author",
        replay_cache=replay,
        now=NOW,
    )


@pytest.mark.parametrize("case", ["identifier", "digest", "verbs", "expiry", "key", "senders"])
def test_invalid_operator_enrollment_is_refused(case: str) -> None:
    grant = enrollment()
    with pytest.raises(ValueError):
        if case == "identifier":
            replace(grant, session_id="")
        elif case == "digest":
            replace(grant, proposal_sha256="wrong")
        elif case == "verbs":
            replace(grant, verbs=frozenset({"chat"}))
        elif case == "expiry":
            replace(grant, expires_at=float("inf"))
        elif case == "key":
            replace(grant, key=replace(KEY, secret=b"short"))
        else:
            replace(grant, key=replace(KEY, senders=frozenset({"EXAMPLE/attacker"})))
