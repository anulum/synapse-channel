# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a release sidecar does not mint a private client store
"""Client stores a hook sidecar must not create under its connection name."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.lease_epoch_store import LeaseEpochStore
from synapse_channel.core.payload_replay import default_payload_replay_ledger
from synapse_channel.machine_identity import MACHINE_KEY_FILENAME, identity_dir
from synapse_channel.mailbox_cursor import cursor_path
from synapse_channel.owner_lease import lease_path


def _sidecar(tmp_path: Path) -> SynapseAgent:
    """Build the agent the hook builds, against an isolated data home."""
    return SynapseAgent(
        "seat-release",
        machine_identity=True,
        verbose=False,
        lease_epoch_root=tmp_path / "epochs",
    )


def test_a_sidecar_signs_with_the_shared_machine_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signing key is the machine key, not a key named for the sidecar."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    seat = SynapseAgent("seat", verbose=False, lease_epoch_root=tmp_path / "epochs")
    sidecar = _sidecar(tmp_path)
    key = identity_dir(base=tmp_path) / MACHINE_KEY_FILENAME
    assert key.is_file()
    assert seat.signing.identity_key_id == sidecar.signing.identity_key_id
    assert seat.signing.identity_key_id.startswith("machine-")
    assert not any("release" in path.name for path in tmp_path.rglob("*"))


def test_a_sidecar_agent_writes_no_cursor_lease_token_or_replay_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The redirected home gains no cursor, lease, replay file, or sidecar epoch."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    agent = SynapseAgent("seat-release", machine_identity=False, verbose=False)
    assert agent.mailbox is False
    assert agent.request_lease is False
    assert agent._lease_epoch_store is not None
    assert agent._lease_epoch_store.directory == LeaseEpochStore("seat").directory
    assert not cursor_path("seat-release").exists()
    assert not lease_path("seat-release").exists()
    assert not default_payload_replay_ledger("seat-release").exists()
    assert not any("-release" in path.name for path in tmp_path.rglob("*"))
