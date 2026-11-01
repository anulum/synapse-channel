# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — payload key file read refusals and their reasons

"""Each way a payload key file cannot be used gets its own refusal reason."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from synapse_channel.core import secure_path
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.payload_crypto import PayloadCryptoError, load_payload_key


def _refusal(path: Path) -> str:
    with pytest.raises(PayloadCryptoError) as caught:
        load_payload_key(path)
    return str(caught.value)


def test_a_socket_node_is_not_a_key_file(tmp_path: Path) -> None:
    """A socket in place of the key file cannot be opened and is not a regular file."""
    node = tmp_path / "payload.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(node))
        refusal = _refusal(node)
        assert refusal.startswith(f"payload key file is not a regular file it can open: {node} (")
        assert "cannot open" in refusal
    finally:
        listener.close()


def test_an_unreadable_key_is_named_as_unreadable(tmp_path: Path) -> None:
    """A key file its owner cannot read is reported as unreadable, not as a non-file."""
    key_path = tmp_path / "payload.key"
    generate_key_file(key_path)
    key_path.chmod(0o000)
    try:
        assert _refusal(key_path) == f"payload key file cannot be read: {key_path}"
    finally:
        key_path.chmod(0o600)
    assert len(load_payload_key(key_path)) == 32


def test_an_unsearchable_directory_is_not_a_key_file(tmp_path: Path) -> None:
    """A directory that cannot even be opened is refused as a non-file, as on Windows."""
    directory = tmp_path / "key-dir"
    directory.mkdir(mode=0o000)
    try:
        assert _refusal(directory) == f"payload key file is not a regular file: {directory}"
    finally:
        directory.chmod(0o700)


def test_a_platform_without_the_owner_only_floor_refuses_every_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where owner-only validation is unavailable, no key file is accepted, and that is said."""
    key_path = tmp_path / "payload.key"
    generate_key_file(key_path)
    monkeypatch.setattr(secure_path, "owner_only_floor_available", lambda: False)
    assert _refusal(key_path) == (
        f"payload key file cannot be checked as owner-only on this platform: {key_path}"
    )
