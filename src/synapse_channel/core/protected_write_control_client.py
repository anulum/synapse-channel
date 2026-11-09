# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — frontend-owned protected control client
"""Exchange raw protected intent with the enrolled native authority peer."""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from synapse_channel.core.protected_write_control_policy import (
    ProtectedWriteControlPeer,
    ProtectedWriteControlPolicy,
)
from synapse_channel.core.protected_write_control_wire import (
    ProtectedWriteControlBinding,
    decode_protected_write_control_hello,
    read_protected_write_control_frame,
)
from synapse_channel.core.protected_write_refusal import ProtectedWriteRefusalError
from synapse_channel.core.protected_write_unix_exchange import require_native_unix_credentials


class ProtectedWriteControlClient:
    """Bind sequential frontend exchanges to one verified native connection.

    Parameters
    ----------
    reader, writer:
        Actual connected private streams, after verifying the kernel peer.
    binding:
        Strictly checked authority greeting from that same connection.

    Notes
    -----
    Construct through ``open_protected_write_control``. No authority signer,
    enrollment registry, journal or authenticated Python object crosses here.
    Clients must still verify request-bound authority signatures themselves.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        binding: ProtectedWriteControlBinding,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._binding = binding
        self._sequence = 0
        self._busy = False
        self._closed = False
        self._pending: asyncio.Task[bytes] | None = None

    async def exchange(self, raw: str | bytes) -> bytes:
        """Forward unchanged intent once, without a queued or automatic retry.

        Parameters
        ----------
        raw:
            Original external client text or UTF-8 bytes. Only the authority
            decides its canonical interpretation and permission to act.

        Returns
        -------
        bytes
            Result for this exact connection and correlation.

        Raises
        ------
        ValueError
            Before sending, for an invalid request or unavailable/concurrent channel.
        ProtectedWriteRefusalError
            For a validated authority refusal bound to this exact exchange.
        RuntimeError
            For a malformed/misdirected private reply or closure after response
            collection. The requested operation may already be durable.
        Exception
            On transport failure or deadline expiry. The channel is then closed;
            the admitted operation may be durable and must not be replayed.
        """
        if self._closed or self._busy:
            self._closed = True
            self._writer.close()
            if self._pending is not None:
                self._pending.cancel()
            raise ValueError("protected control exchange unavailable")
        self._busy = True
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            self._sequence += 1
            packet = self._binding.encode("request", self._sequence, text)
            self._pending = asyncio.create_task(self._round_trip(packet, self._sequence))
            result = await self._pending
            if self._closed:
                raise RuntimeError("protected control exchange closed")
            return result
        except BaseException:
            self._closed = True
            self._writer.close()
            raise
        finally:
            self._pending = None
            self._busy = False

    async def _round_trip(self, packet: bytes, sequence: int) -> bytes:
        try:
            async with asyncio.timeout(self._binding.policy.timeout_seconds):
                self._writer.write(packet)
                await self._writer.drain()
                result = await read_protected_write_control_frame(
                    self._reader, self._binding.policy
                )
        except ValueError as exc:
            raise RuntimeError("protected control response unavailable") from exc
        return self._decode_result(result, sequence)

    def _decode_result(self, raw: bytes, sequence: int) -> bytes:
        """Accept only a bound result or explicit refusal; other replies stay unknown."""
        try:
            data = self._binding.decode(raw, kind="result", sequence=sequence)
        except ValueError as exc:
            try:
                self._binding.decode(raw, kind="refused", sequence=sequence)
            except ValueError:
                raise RuntimeError("protected control response unavailable") from exc
            raise ProtectedWriteRefusalError("protected request refused") from None
        return data.encode("utf-8")

    async def aclose(self) -> None:
        """Close this stream; no incomplete result authorises another mutation."""
        self._closed = True
        self._writer.close()
        pending = self._pending
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        with suppress(OSError):
            await self._writer.wait_closed()


@asynccontextmanager
async def open_protected_write_control(
    path: Path, *, authority_peer: ProtectedWriteControlPeer, policy: ProtectedWriteControlPolicy
) -> AsyncIterator[ProtectedWriteControlClient]:
    """Open a native private channel and verify its actual authority before use.

    Parameters
    ----------
    path:
        Supervisor-enrolled Unix endpoint with separately retained ancestry/ACLs.
    authority_peer:
        Exact expected kernel PID, UID and GID; never supplied by the frontend.
    policy:
        Independently enrolled continuity, incarnation and finite limits.

    Yields
    ------
    ProtectedWriteControlClient
        Owned request connection after native peer and complete greeting checks.

    Raises
    ------
    OSError
        On unsupported native credentials or unavailable endpoint.
    ValueError
        On a foreign peer, malformed greeting or changed process-pair binding.
    TimeoutError
        If connection or greeting exceeds the explicit policy deadline.
    """
    require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(policy.timeout_seconds):
            reader, writer = await asyncio.open_unix_connection(
                str(path), limit=policy.json_limits.max_wire_bytes
            )
            authority_peer.verify(writer)
            raw = await read_protected_write_control_frame(reader, policy)
            binding = decode_protected_write_control_hello(raw, policy)
        client = ProtectedWriteControlClient(reader, writer, binding)
        try:
            yield client
        finally:
            await client.aclose()
    finally:
        if writer is not None:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
