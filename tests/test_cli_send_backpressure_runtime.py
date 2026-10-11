# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real CLI send deadline under socket backpressure
"""Hold a real socket write without replacing the CLI or its send coroutine."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

_PAYLOAD_BYTES = 32 * 1024 * 1024
_DRIVER = """
import runpy, sys
from pathlib import Path
body = Path(sys.argv[1]).read_text(encoding="utf-8")
uri = sys.argv[2]
timeout = sys.argv[3]
sys.argv = ["synapse", "send", body, "--uri", uri, "--name", "SENDER",
            "--target", "RECIPIENT", "--receipt-timeout", timeout]
runpy.run_module("synapse_channel.cli", run_name="__main__")
"""


@contextlib.asynccontextmanager
async def write_relay(uri: str, profile: str) -> AsyncIterator[tuple[str, list[int], list[str]]]:
    """Relay the actual handshake and frames, holding only a large text write."""
    held_frames: list[int] = []
    phases: list[str] = []
    streams: set[asyncio.StreamWriter] = set()
    handlers: set[asyncio.Task[None]] = set()
    destination = urlsplit(uri)
    assert destination.hostname is not None and destination.port is not None

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Join owned pumps and abort their sockets even after a failed child."""
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        streams.add(writer)
        phases.append("connected")
        upstream_reader, upstream_writer = await asyncio.open_connection(
            destination.hostname, destination.port
        )
        streams.add(upstream_writer)
        phases.append("upstream-connected")
        pumps: list[asyncio.Task[None]] = []
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            phases.append("request-read")
            # Negotiate no compression, so the real large frame fills the write window.
            lines = [
                (
                    f"Host: {destination.hostname}:{destination.port}".encode("ascii")
                    if line.lower().startswith(b"host:")
                    else line
                )
                for line in request.split(b"\r\n")
                if not line.lower().startswith(b"sec-websocket-extensions:")
            ]
            upstream_writer.write(b"\r\n".join(lines))
            await upstream_writer.drain()
            phases.append("request-forwarded")

            async def outbound() -> None:
                """Forward every genuine upstream byte, including greetings and receipts."""
                while chunk := await upstream_reader.read(65536):
                    phases.append(
                        "upstream-status:" + chunk[:12].decode("ascii")
                        if chunk.startswith(b"HTTP/")
                        else f"upstream-chunk:{len(chunk)}"
                    )
                    writer.write(chunk)
                    await writer.drain()

            async def inbound() -> None:
                """Forward exact small frames; stall a real masked CHAT after its header."""
                try:
                    while True:
                        prefix = await reader.readexactly(2)
                        size = prefix[1] & 127
                        header = prefix
                        if size in (126, 127):
                            extension = await reader.readexactly(2 if size == 126 else 8)
                            header += extension
                            size = int.from_bytes(extension, "big")
                        mask = await reader.readexactly(4) if prefix[1] & 128 else b""
                        phases.append(f"frame:{prefix.hex()}:{size}")
                        header += mask
                        if profile != "healthy" and size >= _PAYLOAD_BYTES:
                            assert prefix == b"\x81\xff"
                            assert _PAYLOAD_BYTES <= size < _PAYLOAD_BYTES + 4096
                            beginning = await reader.readexactly(256)
                            decoded = bytes(
                                value ^ mask[i % 4] for i, value in enumerate(beginning)
                            )
                            assert b'"type": "chat"' in decoded
                            held_frames.append(size)
                            transport = writer.transport
                            assert isinstance(transport, asyncio.Transport)
                            transport.pause_reading()
                            if profile == "blackhole":
                                await asyncio.get_running_loop().create_future()
                            else:
                                await asyncio.sleep(0.5 if profile == "late-reset" else 0.01)
                                writer.transport.abort()
                            return
                        upstream_writer.write(header + await reader.readexactly(size))
                        await upstream_writer.drain()
                        phases.append("frame-forwarded")
                except asyncio.IncompleteReadError:
                    return

            pumps = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for pump in done:
                pump.result()
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (writer, upstream_writer):
                stream.transport.abort()
                streams.discard(stream)
            handlers.discard(task)

    async with await asyncio.start_server(handler, "127.0.0.1", 0) as server:
        try:
            yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", held_frames, phases
        finally:
            for stream in streams:
                stream.transport.abort()
            for task in handlers:
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)


@pytest.mark.parametrize("profile", ["healthy", "reset", "late-reset", "blackhole"])
async def test_public_send_classifies_a_real_blocked_write_without_replaying(
    tmp_path: Path, profile: str
) -> None:
    """The registered CLI exits safely even when its peer never drains the write."""
    body = tmp_path / "message.txt"
    blocked = profile != "healthy"
    body.write_text("x" * _PAYLOAD_BYTES if blocked else "healthy-write", encoding="utf-8")
    source = os.environ.get(
        "SYNAPSE_SEND_TEST_SOURCE", str(Path(__file__).resolve().parents[1] / "src")
    )
    environment = {
        **os.environ,
        "PYTHONPATH": source,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            receiver = await connect_agent("RECIPIENT", uri)
            try:
                async with write_relay(uri, profile) as (relay, held, phases):
                    started = time.monotonic()
                    # Construct real CLI argv in the child to exceed the OS argument limit.
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-u",
                        "-c",
                        _DRIVER,
                        str(body),
                        relay,
                        "0.05" if profile in ("blackhole", "late-reset") else "2",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=environment,
                    )
                    collection = asyncio.create_task(process.communicate())
                    try:
                        try:
                            stdout, stderr = await asyncio.wait_for(
                                asyncio.shield(collection), timeout=20
                            )
                        except asyncio.TimeoutError:
                            process.kill()
                            stdout, stderr = await collection
                            pytest.fail(
                                f"CLI did not exit after its write budget: {stdout!r} {stderr!r}"
                            )
                    finally:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                    (tmp_path / "cli.stdout").write_bytes(stdout)
                    (tmp_path / "cli.stderr").write_bytes(stderr)
                    assert process.returncode == (3 if blocked else 0), (stdout, stderr, phases)
                    assert stderr == b""
                    chats = [row for row in journal.iter_events() if row.kind == "chat"]
                    if blocked:
                        assert len(held) == 1
                        assert stdout.count(b"delivery unknown:") == 1
                        assert b"client_msg_id=cli-" in stdout and b"before retrying" in stdout
                        assert not chats
                    else:
                        assert not held and len(chats) == 1 and stdout == b""
                        await receiver.recorder.wait_for(
                            lambda row: row.get("payload") == "healthy-write"
                        )
                    print(
                        {
                            "profile": profile,
                            "python": sys.version.split()[0],
                            "wall_seconds_including_startup": time.monotonic() - started,
                            "held_frames": held,
                            "journal_chats": len(chats),
                            "exit": process.returncode,
                        }
                    )
            finally:
                await close_agents(receiver)
