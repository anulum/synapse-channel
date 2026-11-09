# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — protected control wire contracts
"""Exercise the public private-frame format with raw ambiguity preserved."""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import replace

import pytest

from protected_write_control_examples import control_policy
from synapse_channel.core.protected_write_control_wire import (
    ProtectedWriteControlBinding,
    decode_protected_write_control_hello,
    read_protected_write_control_frame,
)


@pytest.mark.parametrize(
    "kind,sequence,data",
    [
        ("hello", 0, ""),
        ("request", 1, '{"sender":"a","sender":"b"}'),
        ("result", (1 << 53) - 1, "réponse\\escaped"),
    ],
)
def test_control_format_preserves_original_text(kind: str, sequence: int, data: str) -> None:
    binding = ProtectedWriteControlBinding(control_policy(), "a" * 64)
    packet = binding.encode(kind, sequence, data)
    assert struct.unpack("!I", packet[:4])[0] == len(packet) - 4
    assert binding.decode(packet[4:], kind=kind, sequence=sequence) == data
    if kind == "hello":
        assert decode_protected_write_control_hello(packet[4:], binding.policy) == binding


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "foreign"},
        {"kind": "operator"},
        {"authority_continuity": "old"},
        {"frontend_incarnation": "other"},
        {"connection_id": "b" * 64},
        {"sequence": 2},
        {"sequence": True},
        {"data": {}},
        {"reply_target": "b" * 64},
    ],
)
def test_control_format_refuses_unbound_or_extended_envelopes(changes: dict[str, object]) -> None:
    binding = ProtectedWriteControlBinding(control_policy(), "a" * 64)
    frame = json.loads(binding.encode("request", 1, "original")[4:])
    frame.update(changes)
    with pytest.raises(ValueError):
        binding.decode(json.dumps(frame).encode(), kind="request", sequence=1)


@pytest.mark.parametrize(
    "kind,sequence,data",
    [
        ("ordinary", 1, ""),
        ("hello", 1, ""),
        ("hello", 0, "not empty"),
        ("request", 0, ""),
        ("result", -1, ""),
        ("request", 1 << 53, ""),
        ("request", True, ""),
    ],
)
def test_control_format_refuses_invalid_correlations(kind: str, sequence: int, data: str) -> None:
    binding = ProtectedWriteControlBinding(control_policy(), "a" * 64)
    with pytest.raises(ValueError):
        binding.encode(kind, sequence, data)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"connection_id":null}',
        b'{"connection_id":"bad"}',
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":9007199254740992}',
        b'{"a":"\\ud800"}',
    ],
)
def test_control_greeting_rejects_malformed_json_and_identity(raw: bytes) -> None:
    with pytest.raises(ValueError):
        decode_protected_write_control_hello(raw, control_policy())


async def test_control_reader_checks_length_before_payload_and_observes_eof() -> None:
    policy = control_policy()

    async def send(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            for payload in (struct.pack("!I", policy.json_limits.max_wire_bytes + 1), b"\0\0\0\0"):
                writer.write(payload)
                await writer.drain()
            writer.write(struct.pack("!I", 6) + b"{}")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(send, "127.0.0.1", 0) as server:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        try:
            for _ in range(2):
                with pytest.raises(ValueError, match="size refused"):
                    await read_protected_write_control_frame(reader, policy)
            with pytest.raises(asyncio.IncompleteReadError):
                await read_protected_write_control_frame(reader, policy)
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.parametrize("limit", ["max_wire_bytes", "max_depth", "max_nodes", "max_string_bytes"])
def test_control_format_enforces_each_representation_ceiling(limit: str) -> None:
    policy = control_policy()
    limits = policy.json_limits
    if limit == "max_wire_bytes":
        limits = replace(limits, max_wire_bytes=64)
    elif limit == "max_depth":
        limits = replace(limits, max_depth=1)
    elif limit == "max_nodes":
        limits = replace(limits, max_nodes=2)
    else:
        limits = replace(limits, max_string_bytes=4)
    binding = ProtectedWriteControlBinding(replace(policy, json_limits=limits), "a" * 64)
    with pytest.raises(ValueError):
        if limit == "max_depth":
            binding.decode(b'{"data":{"nested":{}}}', kind="request", sequence=1)
        else:
            binding.encode("request", 1, "original")


def test_control_capacity_covers_worst_ascii_escaping_and_maximum_correlation() -> None:
    binding = ProtectedWriteControlBinding(control_policy(), "a" * 64)
    maximum = 20000
    binding.validate_capacity(maximum)
    header = len(binding.encode("request", (1 << 53) - 1, "")) - 4
    packet = binding.encode("request", (1 << 53) - 1, "\x00" * maximum)
    assert len(packet) - 4 == header + 6 * maximum
    assert binding.decode(packet[4:], kind="request", sequence=(1 << 53) - 1) == "\x00" * maximum
    wire_short = replace(
        binding.policy,
        json_limits=replace(
            binding.policy.json_limits,
            max_wire_bytes=len(packet) - 5,
        ),
    )
    with pytest.raises(ValueError, match="cannot carry authority"):
        ProtectedWriteControlBinding(wire_short, "a" * 64).validate_capacity(maximum)
    string_short = replace(
        binding.policy,
        json_limits=replace(
            binding.policy.json_limits,
            max_string_bytes=maximum - 1,
        ),
    )
    with pytest.raises(ValueError, match="cannot carry authority"):
        ProtectedWriteControlBinding(string_short, "a" * 64).validate_capacity(maximum)


@pytest.mark.parametrize("maximum", [0, -1, True])
def test_control_capacity_refuses_nonpositive_original_payload(maximum: int) -> None:
    with pytest.raises(ValueError, match="positive original payload"):
        ProtectedWriteControlBinding(control_policy(), "a" * 64).validate_capacity(maximum)
