# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — protected control policy validation
"""Validate explicit control policy and actual native peer enrollment."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from protected_write_control_examples import control_policy, native_peer
from protected_write_socket_examples import requires_linux_peer_credentials
from synapse_channel.core.protected_write_control_policy import ProtectedWriteControlPeer


@pytest.mark.parametrize("value", [0, -1, True])
def test_control_peer_refuses_invalid_pid(value: int) -> None:
    with pytest.raises(ValueError):
        ProtectedWriteControlPeer(value, 1, 1)


@pytest.mark.parametrize("uid,gid", [(-1, 1), (1, -1), (True, 1), (1, False)])
def test_control_peer_refuses_coerced_or_negative_owners(uid: int, gid: int) -> None:
    with pytest.raises(ValueError):
        ProtectedWriteControlPeer(1, uid, gid)


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan"), True])
def test_control_policy_requires_finite_deadline(value: float) -> None:
    with pytest.raises(ValueError):
        replace(control_policy(), timeout_seconds=value)


@pytest.mark.parametrize("field", ["authority_continuity", "frontend_incarnation"])
@pytest.mark.parametrize("value", ["", "é" * 129])
def test_control_policy_requires_bounded_incarnations(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        if field == "authority_continuity":
            replace(control_policy(), authority_continuity=value)
        else:
            replace(control_policy(), frontend_incarnation=value)


@pytest.mark.parametrize("value", [0, -1, True])
def test_control_policy_requires_positive_reservations(value: int) -> None:
    with pytest.raises(ValueError):
        replace(control_policy(), max_connections=value)
    with pytest.raises(ValueError):
        replace(control_policy(), max_inflight_bytes=value)


def test_control_policy_requires_fitting_length_and_full_connection_reservation() -> None:
    policy = control_policy()
    assert policy.connection_bytes == 2 * policy.json_limits.max_wire_bytes
    with pytest.raises(ValueError, match="one connection"):
        replace(policy, max_inflight_bytes=policy.connection_bytes - 1)
    with pytest.raises(ValueError, match="length prefix"):
        replace(policy, json_limits=replace(policy.json_limits, max_wire_bytes=1 << 32))


@requires_linux_peer_credentials
async def test_control_peer_refuses_real_tcp_endpoint() -> None:
    async def close(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(close, "127.0.0.1", 0) as server:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        try:
            with pytest.raises(ValueError, match="Unix control"):
                native_peer().verify(writer)
        finally:
            writer.close()
            await writer.wait_closed()
