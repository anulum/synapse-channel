# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — grouped construction, legacy callers and owned resource lifetime
"""Exercise construction boundaries and actual record/resource ownership."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import cast

if sys.version_info >= (3, 11):
    from typing import Unpack
else:
    from typing_extensions import Unpack

import pytest

from synapse_channel.core import hub_component_lifetime as checkpoint_module
from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.attachment_store import AttachmentStore
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import (
    HubAuthConfig,
    HubConfig,
    HubLimits,
    HubMetricsConfig,
    config_fingerprint,
)
from synapse_channel.core.hub_config_attribute import ConfigAttribute
from synapse_channel.core.hub_constructor_options import HubLegacyOptions
from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle_checkpoint import MerkleCheckpointStore
from synapse_channel.core.persistence import EventStore


class LegacyConstructorHub(SynapseHub):
    """Embedding subclass that still accepts only the original keyword surface."""

    def __init__(self, **legacy: Unpack[HubLegacyOptions]) -> None:
        """Retain a keyword-only embedding boundary and delegate real construction."""
        super().__init__(**legacy)
        self.legacy_constructed = True


def test_from_config_keeps_the_legacy_subclass_constructor() -> None:
    config = HubConfig(limits=HubLimits(max_clients=7), hub_id="embedding")
    hub = LegacyConstructorHub.from_config(config)
    assert isinstance(hub, LegacyConstructorHub)
    assert hub.legacy_constructed
    assert hub.hub_id == "embedding" and hub.max_clients == 7
    assert hub.config_epoch == config_fingerprint(config)


def test_grouped_constructor_stamps_epoch_and_keeps_normalized_fields() -> None:
    config = HubConfig(
        limits=HubLimits(max_clients=7, max_history=-1),
        metrics=HubMetricsConfig(metrics_token="", advertised_host="  example.local  "),
    )
    hub = SynapseHub(config)
    assert hub.max_clients == 7
    assert hub.max_history == 1
    assert hub.metrics_token is None
    assert hub.advertised_host == "example.local"
    assert hub.configuration.limits.max_history == 1
    assert hub.configuration.metrics.metrics_token is None
    assert hub.config_epoch == config_fingerprint(config)
    assert config.limits.max_history == -1
    assert config.metrics.metrics_token == ""


def test_grouped_and_legacy_sources_cannot_be_combined() -> None:
    with pytest.raises(TypeError, match="cannot combine"):
        SynapseHub(HubConfig(), max_clients=7)


def test_invalid_record_and_unknown_legacy_option_are_refused() -> None:
    # Deliberately violate the static API to verify the runtime refusal as well.
    with pytest.raises(TypeError, match="must be a HubConfig"):
        SynapseHub(cast(HubConfig, object()))
    with pytest.raises(TypeError, match="unexpected keyword"):
        options = HubConfig().to_kwargs()
        options["unsupported_option"] = True
        SynapseHub(**options)


def test_writable_legacy_names_replace_only_the_owning_record() -> None:
    original = HubConfig(limits=HubLimits(max_clients=7))
    first, second = SynapseHub(original), SynapseHub(original)
    snapshot = first.configuration
    first.max_clients = 13
    first.metrics_token = "test-only-token"
    assert first.max_clients == first.configuration.limits.max_clients == 13
    assert first.metrics_token == first.configuration.metrics.metrics_token == "test-only-token"
    assert second.max_clients == original.limits.max_clients == snapshot.limits.max_clients == 7
    assert second.metrics_token is None
    assert "max_clients" not in vars(first)
    assert "metrics_token" not in vars(first)
    assert isinstance(SynapseHub.max_clients, ConfigAttribute)


def test_partial_constructor_failure_releases_checkpoint_but_not_caller_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoints: list[MerkleCheckpointStore] = []

    class ObservedCheckpoint(MerkleCheckpointStore):
        """Real checkpoint connection retained for a post-refusal ownership probe."""

        def __init__(self, path: Path) -> None:
            """Open the actual database and record its connection owner."""
            super().__init__(path)
            checkpoints.append(self)

    monkeypatch.setattr(checkpoint_module, "MerkleCheckpointStore", ObservedCheckpoint)
    with EventStore(tmp_path / "events.db") as journal:
        config = HubConfig(
            journal=journal,
            auth=HubAuthConfig(per_message_auth_sequence_floor_mode="invalid-mode"),
        )
        with pytest.raises(ValueError, match="invalid-mode"):
            SynapseHub(config)
        assert len(checkpoints) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            checkpoints[0].latest()
        assert journal.count() == 0
        journal.append(EventKind.CHAT, {"sender": "proof", "payload": "still owned"})
        assert journal.count() == 1
        reopened = MerkleCheckpointStore(tmp_path / "events.db.checkpoint.db")
        try:
            initial = reopened.latest()
            assert initial is not None and initial.seq == 0
        finally:
            reopened.close()


def test_metrics_credential_rotation_does_not_change_public_posture() -> None:
    first = HubConfig(metrics=HubMetricsConfig(metrics_token="synthetic-alpha"))
    rotated = HubConfig(metrics=HubMetricsConfig(metrics_token="synthetic-beta"))
    assert config_fingerprint(first) == config_fingerprint(rotated)
    assert config_fingerprint(first) != config_fingerprint(HubConfig())


def test_empty_metrics_token_has_the_unauthenticated_posture() -> None:
    assert config_fingerprint(HubConfig(metrics=HubMetricsConfig(metrics_token=""))) == (
        config_fingerprint(HubConfig())
    )


def test_untrusted_attachment_configuration_preserves_caller_resources(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    try:
        with EventStore(tmp_path / "events.db") as journal:
            with pytest.raises(ValueError, match="attachments require token"):
                SynapseHub(HubConfig(attachment_store=store, journal=journal))
            assert journal.count() == 0
            assert store.peer_read_audit() == []
            assert not (tmp_path / "events.db.checkpoint.db").exists()
    finally:
        store.close()


def test_attachment_recipient_policy_requires_store_and_peer_policy(tmp_path: Path) -> None:
    policy = AttachmentServingPolicy(tmp_path / "absent-policy.json")
    with pytest.raises(ValueError, match="requires attachments and peer serving policy"):
        SynapseHub(HubConfig(attachment_serving_policy=policy))
