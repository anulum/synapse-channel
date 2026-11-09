# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded protected frontend control frames
"""Carry original signed client text in strictly decoded private envelopes."""

from __future__ import annotations

import asyncio
import json
import re
import struct
from dataclasses import dataclass

from synapse_channel.core.protected_write_control_policy import ProtectedWriteControlPolicy
from synapse_channel.core.protected_write_json import decode_protected_write_json

_SCHEMA = "synapse.protected.frontend-control.v1"
_CONNECTION = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ProtectedWriteControlBinding:
    """Authority-minted connection identity scoped to one enrolled process pair.

    Parameters
    ----------
    policy:
        Shared enrolled continuity and frontend incarnation.
    connection_id:
        Random 256-bit identifier issued by the authority for this connection.
    """

    policy: ProtectedWriteControlPolicy
    connection_id: str

    def __post_init__(self) -> None:
        """Require the exact authority connection identifier representation."""
        if type(self.connection_id) is not str or _CONNECTION.fullmatch(self.connection_id) is None:
            raise ValueError("invalid control connection identity")

    def validate_capacity(self, max_payload_bytes: int) -> None:
        """Require capacity for every authority-bounded original request/result.

        Parameters
        ----------
        max_payload_bytes:
            Authority's positive maximum UTF-8 bytes, including JSON syntax.

        Raises
        ------
        ValueError
            If the envelope cannot hold the worst-case six-byte ASCII escape
            of each payload byte plus its exact maximum-correlation overhead,
            or if its decoded string budget is smaller than the original bytes.

        Notes
        -----
        The bound is deliberately conservative for valid UTF-8 and canonical
        JSON. It depends on representation policy, never available host RAM.
        Fixed fields, depth and node limits are checked by encoding the header.
        """
        if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
            raise ValueError("positive original payload capacity required")
        overhead = len(self.encode("request", (1 << 53) - 1, "")) - 4
        if (
            self.policy.json_limits.max_string_bytes < max_payload_bytes
            or self.policy.json_limits.max_wire_bytes < overhead + 6 * max_payload_bytes
        ):
            raise ValueError("control envelope cannot carry authority payload limits")

    def encode(self, kind: str, sequence: int, data: str) -> bytes:
        """Encode a closed hello, request or result and apply the full wire limits.

        Parameters
        ----------
        kind:
            ``hello``, ``request``, ``result`` or ``refused``; no ordinary/operator operation.
        sequence:
            Zero for hello; positive monotonic correlation for other messages.
        data:
            Empty for hello/refused; unchanged raw signed client or authority text.

        Returns
        -------
        bytes
            Four-byte big-endian length followed by bounded UTF-8 JSON.

        Raises
        ------
        ValueError
            For an invalid message, unbound field or exceeded policy cap.
        """
        frame = {
            "schema": _SCHEMA,
            "kind": kind,
            "authority_continuity": self.policy.authority_continuity,
            "frontend_incarnation": self.policy.frontend_incarnation,
            "connection_id": self.connection_id,
            "sequence": sequence,
            "data": data,
        }
        raw = json.dumps(frame, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        self.decode(raw, kind=kind, sequence=sequence)
        return struct.pack("!I", len(raw)) + raw

    def decode(self, raw: bytes, *, kind: str, sequence: int) -> str:
        """Validate the complete envelope against this channel's expected reply target.

        Parameters
        ----------
        raw:
            Complete private payload without its already checked length prefix.
        kind, sequence:
            Expected operation and correlation assigned by this channel.

        Returns
        -------
        str
            Original embedded text, unparsed and without frontend normalisation.

        Raises
        ------
        ValueError
            For any unknown member, profile, kind, sequence or connection binding.
        """
        frame = decode_protected_write_json(raw, limits=self.policy.json_limits)
        expected: dict[str, object] = {
            "schema": _SCHEMA,
            "kind": kind,
            "authority_continuity": self.policy.authority_continuity,
            "frontend_incarnation": self.policy.frontend_incarnation,
            "connection_id": self.connection_id,
            "sequence": sequence,
        }
        data = frame.pop("data", None)
        if (
            kind not in {"hello", "request", "result", "refused"}
            or type(sequence) is not int
            or not 0 <= sequence < 1 << 53
            or (kind == "hello") != (sequence == 0)
            or type(frame.get("sequence")) is not int
            or frame != expected
            or type(data) is not str
            or (kind in {"hello", "refused"} and data != "")
        ):
            raise ValueError("protected control envelope refused")
        return data


async def read_protected_write_control_frame(
    reader: asyncio.StreamReader, policy: ProtectedWriteControlPolicy
) -> bytes:
    """Read one length-bounded payload; the owning exchange supplies its deadline.

    Parameters
    ----------
    reader:
        Actual private stream, owned by the caller.
    policy:
        Enrollment bound checked before allocating or reading the payload.

    Returns
    -------
    bytes
        Raw payload, still requiring strict decoding and binding validation.

    Raises
    ------
    ValueError
        If the advertised payload is empty or exceeds policy.
    asyncio.IncompleteReadError
        If the peer closes during the header or body.
    """
    length = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 0 < length <= policy.json_limits.max_wire_bytes:
        raise ValueError("protected control frame size refused")
    return await reader.readexactly(length)


def decode_protected_write_control_hello(
    raw: bytes, policy: ProtectedWriteControlPolicy
) -> ProtectedWriteControlBinding:
    """Accept only a complete authority greeting for the enrolled process pair.

    Parameters
    ----------
    raw:
        Actual authority greeting, without the length prefix.
    policy:
        Independently enrolled expected continuity and frontend incarnation.

    Returns
    -------
    ProtectedWriteControlBinding
        Verified channel target; native peer verification is separately required.

    Raises
    ------
    ValueError
        If the greeting is malformed, foreign or not a hello.
    """
    frame = decode_protected_write_json(raw, limits=policy.json_limits)
    connection_id = frame.get("connection_id")
    if not isinstance(connection_id, str):
        raise ValueError("control greeting has no connection identity")
    binding = ProtectedWriteControlBinding(policy, connection_id)
    binding.decode(raw, kind="hello", sequence=0)
    return binding
