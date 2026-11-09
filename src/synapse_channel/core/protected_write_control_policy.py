# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — protected frontend control enrollment
"""Require explicit native peers and bounded private channel policy."""

from __future__ import annotations

import asyncio
import math
import socket
import struct
import sys
from dataclasses import dataclass

from synapse_channel.core.protected_write_json import ProtectedWriteJsonLimits
from synapse_channel.core.protected_write_unix_exchange import require_native_unix_credentials


@dataclass(frozen=True)
class ProtectedWriteControlPeer:
    """Exact expected Linux peer, enrolled independently by the supervisor.

    Parameters
    ----------
    pid, uid, gid:
        Actual process, user and group identifiers. PID must be positive;
        UID and GID may be zero for diagnostic component tests only.

    Notes
    -----
    The supervisor must retain process lifetime and endpoint custody to exclude
    PID reuse. This record neither creates identities nor qualifies isolation.
    """

    pid: int
    uid: int
    gid: int

    def __post_init__(self) -> None:
        """Refuse coerced or impossible native identifiers."""
        if type(self.pid) is not int or self.pid <= 0:
            raise ValueError("positive native control PID required")
        if any(type(value) is not int or value < 0 for value in (self.uid, self.gid)):
            raise ValueError("native control UID and GID required")

    def verify(self, writer: asyncio.StreamWriter) -> None:
        """Check the connected kernel peer before reading or sending a command.

        Parameters
        ----------
        writer:
            Actual connected Unix stream; JSON never supplies its credentials.

        Raises
        ------
        OSError
            When Linux peer credentials are unavailable.
        ValueError
            If the endpoint or actual peer differs from this enrollment.
        """
        require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))
        endpoint = writer.get_extra_info("socket")
        if endpoint is None or endpoint.family != socket.AF_UNIX:
            raise ValueError("native Unix control connection required")
        observed = struct.unpack(
            "iII",
            endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII")),
        )
        if observed != (self.pid, self.uid, self.gid):
            raise ValueError("native control peer mismatch")


@dataclass(frozen=True)
class ProtectedWriteControlPolicy:
    """Immutable private wire and per-frontend application payload ceilings.

    Parameters
    ----------
    authority_continuity:
        Current authority epoch, independently enrolled at both ends.
    frontend_incarnation:
        Supervisor's enrolled frontend process incarnation.
    json_limits:
        Bounds on the complete private envelope, including escaped raw client
        text. These do not replace the authority's original request limits.
    timeout_seconds:
        Finite policy deadline for each complete request/response exchange.
    max_connections:
        Maximum simultaneous logical client connections for this frontend.
    max_inflight_bytes:
        Aggregate reserved application frame bytes. Each connection reserves
        one full envelope in each direction; kernel buffers are separate.

    Notes
    -----
    These are explicit governance caps, not assumptions about host RAM or CPUs.
    Host admission may lower them. At most one request is pending per connection.
    No queue, automatic retry, replacement launch or operator relay is provided.
    """

    authority_continuity: str
    frontend_incarnation: str
    json_limits: ProtectedWriteJsonLimits
    timeout_seconds: float
    max_connections: int
    max_inflight_bytes: int

    def __post_init__(self) -> None:
        """Refuse unbound identities, disabled ceilings or nonfinite deadlines."""
        for value in (self.authority_continuity, self.frontend_incarnation):
            if type(value) is not str or not value or len(value.encode("utf-8")) > 256:
                raise ValueError("bounded control incarnation required")
        if (
            type(self.timeout_seconds) is not float
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("finite positive control deadline required")
        if any(
            type(value) is not int or value <= 0
            for value in (self.max_connections, self.max_inflight_bytes)
        ):
            raise ValueError("explicit positive control ceilings required")
        if self.json_limits.max_wire_bytes >= 1 << 32:
            raise ValueError("control frame exceeds length prefix")
        if self.max_inflight_bytes < self.connection_bytes:
            raise ValueError("control budget cannot admit one connection")

    @property
    def connection_bytes(self) -> int:
        """Return reserved application request plus response bytes per connection."""
        return 2 * self.json_limits.max_wire_bytes
