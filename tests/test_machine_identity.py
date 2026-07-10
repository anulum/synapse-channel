# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — zero-config machine identity provisioning

"""Provisioning the per-machine Ed25519 identity keypair with zero operator input."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from synapse_channel.core.identity_keys import IdentityKeyError, load_signing_key
from synapse_channel.machine_identity import (
    MACHINE_KEY_FILENAME,
    MACHINE_KEY_ID_PREFIX,
    ensure_machine_identity,
    identity_dir,
    machine_identity_agent_kwargs,
)


def test_identity_dir_honours_xdg_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "/data/home")
    assert identity_dir() == Path("/data/home/synapse/identity")
    monkeypatch.delenv("XDG_DATA_HOME")
    assert identity_dir() == Path.home() / ".local" / "share" / "synapse" / "identity"
    assert identity_dir(base=Path("/elsewhere")) == Path("/elsewhere/synapse/identity")


def test_first_use_provisions_an_owner_only_ed25519_key(tmp_path: Path) -> None:
    machine = ensure_machine_identity(base=tmp_path)
    assert machine.key_path == tmp_path / "synapse" / "identity" / MACHINE_KEY_FILENAME
    assert machine.key_path.is_file()
    assert stat.S_IMODE(os.stat(machine.key_path).st_mode) == 0o600
    assert machine.key_id.startswith(MACHINE_KEY_ID_PREFIX)
    # The key file is a loadable Ed25519 PEM, not merely bytes on disk.
    load_signing_key(machine.key_path)


def test_every_later_call_returns_the_same_stable_credential(tmp_path: Path) -> None:
    first = ensure_machine_identity(base=tmp_path)
    second = ensure_machine_identity(base=tmp_path)
    assert second == first


def test_losing_the_first_provision_race_loads_the_winners_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Drive the real exclusive-create collision: the winner's key lands
    # between the loser's existence check and its create, so the loser's
    # write raises and it must load the winner's key instead of tearing it.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    import synapse_channel.machine_identity as module
    from synapse_channel.core.identity_keys import (
        generate_signing_key,
        public_key_b64,
        write_signing_key,
    )

    winner_key = generate_signing_key()

    def racing_write(path: Path, private_key: Ed25519PrivateKey) -> None:
        write_signing_key(path, winner_key)  # the winner lands first
        write_signing_key(path, private_key)  # the loser's exclusive create now fails

    monkeypatch.setattr(module, "write_signing_key", racing_write)
    loser = ensure_machine_identity(base=tmp_path)
    monkeypatch.undo()
    assert loser.public_key == public_key_b64(winner_key)
    assert ensure_machine_identity(base=tmp_path) == loser


def test_a_corrupt_key_file_is_a_loud_error_not_a_silent_regeneration(tmp_path: Path) -> None:
    machine = ensure_machine_identity(base=tmp_path)
    machine.key_path.write_text("not a pem", encoding="utf-8")
    with pytest.raises(IdentityKeyError):
        ensure_machine_identity(base=tmp_path)


def test_agent_kwargs_present_the_machine_key_or_degrade_to_nothing(tmp_path: Path) -> None:
    kwargs = machine_identity_agent_kwargs(base=tmp_path)
    machine = ensure_machine_identity(base=tmp_path)
    assert kwargs == {
        "identity_key_path": str(machine.key_path),
        "identity_key_id": machine.key_id,
    }
    # An unprovisionable data home degrades to an unsigned connection, never a crash.
    unwritable = tmp_path / "blocked"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    try:
        assert machine_identity_agent_kwargs(base=unwritable) == {}
    finally:
        unwritable.chmod(0o700)


def test_the_key_id_is_a_stable_digest_of_the_public_key(tmp_path: Path) -> None:
    machine = ensure_machine_identity(base=tmp_path)
    again = ensure_machine_identity(base=tmp_path)
    assert machine.key_id == again.key_id
    other = ensure_machine_identity(base=tmp_path / "other-machine")
    assert other.key_id != machine.key_id
    assert other.public_key != machine.public_key
