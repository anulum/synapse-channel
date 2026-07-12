# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonical signed capability-card tests
"""Tests for capability-card canonicalisation, signing, and strict JSON input."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.capability_card_signing import (
    CapabilityCardSigningError,
    canonical_capability_card,
    capability_card_digest,
    load_capability_card_json,
    sign_capability_card,
)
from synapse_channel.core.identity_keys import generate_signing_key, write_signing_key


class _Connection:
    """Capture one outbound JSON string."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, payload: str) -> None:
        self.messages.append(payload)


def _card() -> dict[str, object]:
    return {
        "agent": "P/worker",
        "project": "P",
        "description": "worker",
        "skills": ["python"],
        "task_classes": ["code"],
        "contracts": [],
        "meta": {"seat": "one"},
        "manifest_digest": "sha256:abc",
    }


def test_signing_is_deterministic_and_domain_bound() -> None:
    key = generate_signing_key()
    first = sign_capability_card(
        _card(), key_id="P:key", private_key=key, sequence=1, signed_at=10.0
    )
    second = sign_capability_card(
        _card(), key_id="P:key", private_key=key, sequence=1, signed_at=10.0
    )

    assert first == second
    assert first["signature"]["card_digest"] == capability_card_digest(_card())
    assert canonical_capability_card(first).startswith(b"SYNAPSE-CAPABILITY-CARD-SIGNATURE-V1\x00")


def test_hub_projection_fields_do_not_change_signed_bytes_or_digest() -> None:
    key = generate_signing_key()
    signed = sign_capability_card(
        _card(), key_id="P:key", private_key=key, sequence=1, signed_at=10.0
    )
    projected = {
        **signed,
        "advertised_at": 99.0,
        "verification": {"result": "valid"},
    }

    assert canonical_capability_card(projected) == canonical_capability_card(signed)
    assert capability_card_digest(projected) == capability_card_digest(signed)


def test_signature_value_is_excluded_but_metadata_is_covered() -> None:
    signed = sign_capability_card(
        _card(),
        key_id="P:key",
        private_key=generate_signing_key(),
        sequence=1,
        signed_at=10.0,
    )
    other_value = json.loads(json.dumps(signed))
    other_value["signature"]["value"] = "different"
    other_sequence = json.loads(json.dumps(signed))
    other_sequence["signature"]["sequence"] = 2

    assert canonical_capability_card(other_value) == canonical_capability_card(signed)
    assert canonical_capability_card(other_sequence) != canonical_capability_card(signed)
    assert capability_card_digest(other_sequence) == capability_card_digest(signed)


@pytest.mark.parametrize(
    ("card", "key_id", "sequence", "match"),
    [
        ({"project": "P"}, "key", 1, "agent"),
        ({"agent": "P/a"}, "key", 1, "project"),
        (_card(), "", 1, "key_id"),
        (_card(), "key", 0, "positive integer"),
        (_card(), "key", True, "positive integer"),
    ],
)
def test_signing_rejects_missing_bindings_and_bad_sequence(
    card: dict[str, object], key_id: str, sequence: int, match: str
) -> None:
    with pytest.raises(CapabilityCardSigningError, match=match):
        sign_capability_card(
            card,
            key_id=key_id,
            private_key=generate_signing_key(),
            sequence=sequence,
            signed_at=10.0,
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"signed_at": float("nan")}, "finite"),
        ({"expires_at": float("inf"), "signed_at": 10.0}, "finite"),
        ({"lifetime_seconds": 0.0, "signed_at": 10.0}, "after"),
        ({"expires_at": 9.0, "signed_at": 10.0}, "after"),
    ],
)
def test_signing_rejects_invalid_time_windows(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(CapabilityCardSigningError, match=match):
        sign_capability_card(
            _card(),
            key_id="P:key",
            private_key=generate_signing_key(),
            sequence=1,
            **kwargs,
        )


def test_canonicalisation_rejects_non_json_and_non_finite_values() -> None:
    with pytest.raises(CapabilityCardSigningError, match="strict JSON"):
        capability_card_digest({**_card(), "meta": {"bad": object()}})
    with pytest.raises(CapabilityCardSigningError, match="strict JSON"):
        canonical_capability_card({**_card(), "meta": {"bad": float("nan")}})


def test_load_card_json_rejects_duplicate_keys_and_wrong_shapes(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"agent":"P/a","agent":"P/b"}', encoding="utf-8")
    with pytest.raises(CapabilityCardSigningError, match="duplicate JSON key"):
        load_capability_card_json(duplicate)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(CapabilityCardSigningError, match="invalid capability-card JSON"):
        load_capability_card_json(malformed)

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(CapabilityCardSigningError, match="JSON object"):
        load_capability_card_json(array)


def test_load_card_json_handles_missing_and_valid_files(tmp_path: Path) -> None:
    with pytest.raises(CapabilityCardSigningError, match="does not exist"):
        load_capability_card_json(tmp_path / "missing.json")

    card = tmp_path / "card.json"
    card.write_text(json.dumps(_card()), encoding="utf-8")
    assert load_capability_card_json(card) == _card()
    with pytest.raises(CapabilityCardSigningError, match="cannot read"):
        load_capability_card_json(tmp_path)


def test_agent_rejects_incomplete_card_signing_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be supplied together"):
        SynapseAgent(
            "P/a",
            machine_identity=False,
            capability_card_key_path=str(tmp_path / "missing.pem"),
        )
    with pytest.raises(ValueError, match="requires a namespaced agent"):
        SynapseAgent(
            "bare",
            machine_identity=False,
            capability_card_key_path=str(tmp_path / "missing.pem"),
            capability_card_key_id="key",
        )
    with pytest.raises(ValueError, match="must match the agent namespace"):
        SynapseAgent(
            "P/a",
            machine_identity=False,
            capability_card_key_path=str(tmp_path / "missing.pem"),
            capability_card_key_id="key",
            capability_card_project="Q",
        )
    with pytest.raises(ValueError, match="finite and positive"):
        SynapseAgent(
            "P/a",
            machine_identity=False,
            capability_card_lifetime_seconds=float("inf"),
        )


async def test_agent_advertise_signs_normalised_card_fields(tmp_path: Path) -> None:
    key_path = tmp_path / "card.pem"
    write_signing_key(key_path, generate_signing_key())
    agent = SynapseAgent(
        "P/a",
        machine_identity=False,
        capability_card_key_path=str(key_path),
        capability_card_key_id="P:key",
        capability_card_lifetime_seconds=60.0,
    )
    connection = _Connection()
    agent.connection = connection  # type: ignore[assignment]

    await agent.advertise(
        description="  worker  ",
        skills=[" python ", "python"],
        task_classes=[" code "],
        manifest_digest="sha256:abc",
    )

    envelope = json.loads(connection.messages[0])
    assert envelope["description"] == "worker"
    assert envelope["skills"] == ["python"]
    assert envelope["task_classes"] == ["code"]
    assert envelope["project"] == "P"
    assert envelope["signature"]["key_id"] == "P:key"
    assert envelope["signature"]["sequence"] == 1


async def test_unsigned_agent_can_advertise_a_manifest_digest() -> None:
    agent = SynapseAgent("P/a", machine_identity=False)
    connection = _Connection()
    agent.connection = connection  # type: ignore[assignment]

    await agent.advertise(manifest_digest="sha256:abc")

    envelope = json.loads(connection.messages[0])
    assert envelope["manifest_digest"] == "sha256:abc"
    assert "signature" not in envelope
