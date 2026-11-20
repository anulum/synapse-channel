# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the suite default hub is a closed port
"""Prove the suite default cannot reach a hub already running on this machine."""

from __future__ import annotations

import os
import socket

import pytest

from synapse_channel.client.agent import DEFAULT_HUB_URI, default_hub_uri


def test_the_suite_default_hub_refuses_connections() -> None:
    """The address every test inherits is bound, closed, and not the workstation hub."""
    uri = os.environ["SYNAPSE_URI"]
    port_text = uri.rsplit(":", 1)[1]

    assert uri == default_hub_uri()
    assert uri.startswith("ws://127.0.0.1:")
    assert port_text != "8876"
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", int(port_text)), timeout=1)


def test_unsetting_the_variable_restores_the_builtin_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that needs the product default still reaches it by unsetting the variable."""
    monkeypatch.delenv("SYNAPSE_URI", raising=False)

    assert default_hub_uri() == DEFAULT_HUB_URI
