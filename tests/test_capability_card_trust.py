# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — capability-card trust and lifecycle tests
"""Tests for the profile-separated trust bundle and bounded card history."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from synapse_channel.core.capability_card_trust import (
    CapabilityCardHistory,
    CapabilityCardHistoryResult,
    CapabilityCardTrustBundle,
    CapabilityCardTrustError,
    enroll_capability_card_key,
    load_capability_card_trust_bundle,
)
from synapse_channel.core.identity_keys import generate_signing_key, public_key_b64
from synapse_channel.core.message_auth import EventSignatureKey


def _entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "agents": ["P/worker"],
        "key_id": "P:key",
        "projects": ["P"],
        "public_key": public_key_b64(generate_signing_key()),
    }
    entry.update(overrides)
    return entry


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_trust_bundle_builds_separate_key_and_history(tmp_path: Path) -> None:
    path = _write(tmp_path / "trust.json", {"keys": [_entry(expires_at=200.0)]})
    bundle = load_capability_card_trust_bundle(
        path,
        clock_skew_seconds=2.0,
        history_capacity=3,
        history_retention_seconds=4.0,
    )

    key = bundle.keys["P:key"]
    assert key.senders == frozenset({"P/worker"})
    assert key.projects == frozenset({"P"})
    assert key.expires_at == 200.0
    assert bundle.clock_skew_seconds == 2.0
    assert bundle.history.max_entries == 3
    assert bundle.history.retention_seconds == 4.0


def test_history_detects_replay_and_capability_downgrade() -> None:
    history = CapabilityCardHistory(max_entries=2, retention_seconds=10.0)

    def assess(sequence: int, capabilities: frozenset[str]) -> CapabilityCardHistoryResult:
        return history.assess_and_remember(
            agent="P/worker",
            key_id="P:key",
            sequence=sequence,
            route_capabilities=capabilities,
            card_digest="one",
            expires_at=100.0,
            now=10.0,
        )

    assert assess(1, frozenset({"skill:a", "task:x"})) is CapabilityCardHistoryResult.ACCEPTED
    assert (
        assess(1, frozenset({"skill:a", "task:x"})) is CapabilityCardHistoryResult.SEQUENCE_MISMATCH
    )
    assert assess(2, frozenset({"skill:a"})) is CapabilityCardHistoryResult.CAPABILITY_DOWNGRADE
    assert (
        assess(3, frozenset({"skill:a", "task:y"}))
        is CapabilityCardHistoryResult.CAPABILITY_DOWNGRADE
    )
    assert (
        assess(4, frozenset({"skill:a", "task:x", "task:y"}))
        is CapabilityCardHistoryResult.ACCEPTED
    )


def test_history_refuses_new_binding_at_capacity_until_expired() -> None:
    history = CapabilityCardHistory(max_entries=1, retention_seconds=5.0)
    assert (
        history.assess_and_remember(
            agent="P/a",
            key_id="one",
            sequence=1,
            route_capabilities=frozenset(),
            card_digest="a",
            expires_at=10.0,
            now=1.0,
        )
        is CapabilityCardHistoryResult.ACCEPTED
    )
    assert (
        history.assess_and_remember(
            agent="P/b",
            key_id="two",
            sequence=1,
            route_capabilities=frozenset(),
            card_digest="b",
            expires_at=20.0,
            now=2.0,
        )
        is CapabilityCardHistoryResult.HISTORY_FULL
    )
    assert (
        history.assess_and_remember(
            agent="P/b",
            key_id="two",
            sequence=1,
            route_capabilities=frozenset(),
            card_digest="b",
            expires_at=20.0,
            now=16.0,
        )
        is CapabilityCardHistoryResult.ACCEPTED
    )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ([], "mapping with a 'keys' list"),
        ({}, "mapping with a 'keys' list"),
        ({"keys": ["bad"]}, "must be an object"),
        ({"keys": [_entry(key_id="")]}, "non-empty key_id"),
        ({"keys": [_entry(public_key="not-base64!")]}, "invalid base64"),
        ({"keys": [_entry(public_key="YQ==")]}, "32 raw Ed25519"),
        ({"keys": [_entry(agents="P/a")]}, "agents.*must be a list"),
        ({"keys": [_entry(agents=[])]}, "agent binding"),
        ({"keys": [_entry(agents=[1])]}, "string agent bindings"),
        ({"keys": [_entry(projects="P")]}, "projects.*must be a list"),
        ({"keys": [_entry(projects=[])]}, "project binding"),
        ({"keys": [_entry(projects=[""])]}, "string project bindings"),
        ({"keys": [_entry(revoked="yes")]}, "revoked must be a boolean"),
        ({"keys": [_entry(expires_at=True)]}, "expires_at must be a number"),
        ({"keys": [_entry(expires_at=float("nan"))]}, "expires_at must be finite"),
        ({"keys": [_entry(), _entry()]}, "duplicate key id"),
    ],
)
def test_load_trust_bundle_rejects_malformed_entries(
    tmp_path: Path, payload: object, match: str
) -> None:
    path = _write(tmp_path / "trust.json", payload)
    with pytest.raises(CapabilityCardTrustError, match=match):
        load_capability_card_trust_bundle(path)


def test_load_trust_bundle_rejects_missing_invalid_json_and_bad_skew(tmp_path: Path) -> None:
    with pytest.raises(CapabilityCardTrustError, match="does not exist"):
        load_capability_card_trust_bundle(tmp_path / "missing.json")

    malformed = tmp_path / "bad.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(CapabilityCardTrustError, match="invalid capability-card trust JSON"):
        load_capability_card_trust_bundle(malformed)

    valid = _write(tmp_path / "valid.json", {"keys": []})
    with pytest.raises(CapabilityCardTrustError, match="clock skew"):
        load_capability_card_trust_bundle(valid, clock_skew_seconds=-1.0)
    with pytest.raises(CapabilityCardTrustError, match="clock skew"):
        load_capability_card_trust_bundle(valid, clock_skew_seconds=float("inf"))
    with pytest.raises(CapabilityCardTrustError, match="history capacity"):
        load_capability_card_trust_bundle(valid, history_capacity=0)
    with pytest.raises(CapabilityCardTrustError, match="history retention"):
        load_capability_card_trust_bundle(valid, history_retention_seconds=float("nan"))
    with pytest.raises(CapabilityCardTrustError, match="clock skew"):
        CapabilityCardTrustBundle(keys={}, clock_skew_seconds=-1.0)


def test_load_trust_bundle_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.json"
    path.write_text('{"keys":[],"keys":[]}', encoding="utf-8")
    with pytest.raises(CapabilityCardTrustError, match="duplicate JSON key"):
        load_capability_card_trust_bundle(path)


def test_enroll_creates_owner_only_bundle_and_refuses_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "private" / "cards.json"
    key = generate_signing_key()
    enroll_capability_card_key(
        path,
        key_id="P:key",
        public_key_b64=public_key_b64(key),
        agents=["P/worker"],
        projects=["P"],
        expires_at=200.0,
    )

    from synapse_channel.core.secure_path import assert_owner_only_file_path

    assert_owner_only_file_path(path, purpose="capability-card trust bundle")
    assert load_capability_card_trust_bundle(path).keys["P:key"].expires_at == 200.0
    with pytest.raises(CapabilityCardTrustError, match="already enrolled"):
        enroll_capability_card_key(
            path,
            key_id=" P:key ",
            public_key_b64=public_key_b64(key),
            agents=["P/worker"],
            projects=["P"],
        )


def test_enroll_refuses_malformed_existing_bundle(tmp_path: Path) -> None:
    path = tmp_path / "cards.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CapabilityCardTrustError, match="mapping with a 'keys' list"):
        enroll_capability_card_key(
            path,
            key_id="P:key",
            public_key_b64=public_key_b64(generate_signing_key()),
            agents=["P/worker"],
            projects=["P"],
        )

    with pytest.raises(CapabilityCardTrustError, match="non-empty key_id"):
        enroll_capability_card_key(
            tmp_path / "new.json",
            key_id=" ",
            public_key_b64=public_key_b64(generate_signing_key()),
            agents=["P/worker"],
            projects=["P"],
        )


def test_load_reports_invalid_ed25519_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "trust.json", {"keys": [_entry()]})

    def fail_verifier(_self: EventSignatureKey) -> object:
        raise ValueError("bad key")

    monkeypatch.setattr(EventSignatureKey, "verifier", fail_verifier)
    with pytest.raises(CapabilityCardTrustError, match="security extra"):
        load_capability_card_trust_bundle(path)


def test_enroll_reports_creation_and_replace_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = generate_signing_key()

    def fail_mkstemp(**_kwargs: object) -> tuple[int, str]:
        raise OSError("no space")

    monkeypatch.setattr(tempfile, "mkstemp", fail_mkstemp)
    with pytest.raises(CapabilityCardTrustError, match="cannot write"):
        enroll_capability_card_key(
            tmp_path / "unwritable" / "trust.json",
            key_id="P:key",
            public_key_b64=public_key_b64(key),
            agents=["P/worker"],
            projects=["P"],
        )

    monkeypatch.undo()
    target = tmp_path / "replace.json"

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        enroll_capability_card_key(
            target,
            key_id="P:key",
            public_key_b64=public_key_b64(key),
            agents=["P/worker"],
            projects=["P"],
        )
    assert not list(tmp_path.glob("replace.json.*.tmp"))
