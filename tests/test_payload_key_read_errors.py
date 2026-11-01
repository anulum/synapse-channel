# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — payload key file read refusals and their reasons

"""A socket node in place of a payload key file is refused as a non-file."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

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
        assert _refusal(node) == f"payload key file is not a regular file: {node}"
    finally:
        listener.close()
