# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owned pi RPC subprocess lifecycle
"""Own a pinned pi RPC child and distinguish command receipt from turn outcome."""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.participants.pi_rpc import (
    PiRpcDecoder,
    PiRpcError,
    assistant_metrics,
    assistant_text,
    response_for,
)

MAX_COMMAND_BYTES = 65_536
EVENT_QUEUE_LIMIT = 128


@dataclass(frozen=True)
class PiTurn:
    """One pi result at the requested turn or full-run completion boundary."""

    answer: str
    is_error: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float
    stop_reason: str
    tool_error: bool


class PiRpcProcess:
    """Async owner of one pi child, request map, event stream and process group."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float = 120.0,
    ) -> None:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("pi argv must contain non-empty strings")
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 3600:
            raise ValueError("pi RPC timeout must be in (0, 3600]")
        self._argv = tuple(argv)
        self._cwd = cwd
        self._environment = dict(environment)
        self._timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._events: asyncio.Queue[dict[str, Any] | PiRpcError] = asyncio.Queue(
            maxsize=EVENT_QUEUE_LIMIT
        )
        self._pending: dict[str, tuple[str, asyncio.Future[dict[str, Any]]]] = {}
        self._failure: PiRpcError | None = None

    async def __aenter__(self) -> PiRpcProcess:
        """Start the host with separate stdout and stderr drains."""
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Terminate the entire child process group on every exit path."""
        await self.close()

    async def start(self) -> None:
        """Start once with argv and an isolated process group, without a shell."""
        if self._process is not None:
            raise PiRpcError("pi RPC child already started")
        self._process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=self._environment,
            start_new_session=True,
        )
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr = asyncio.create_task(self._drain_stderr())

    async def _read_stdout(self) -> None:
        """Route bounded frames to correlation futures or settled-turn queue."""
        process = self._process
        if process is None or process.stdout is None:
            raise PiRpcError("pi RPC stdout is unavailable")
        decoder = PiRpcDecoder()
        try:
            while chunk := await process.stdout.read(65_536):
                for record in decoder.feed(chunk):
                    if record["type"] == "response":
                        request_id = record.get("id")
                        pending = self._pending.get(str(request_id))
                        if pending is None:
                            raise PiRpcError("pi RPC emitted an unknown response id")
                        command, future = pending
                        response_for(record, str(request_id), command)
                        self._pending.pop(str(request_id), None)
                        if not future.done():
                            future.set_result(record)
                    elif record["type"] in {
                        "message_end",
                        "turn_end",
                        "agent_end",
                        "agent_settled",
                    }:
                        await self._events.put(record)
            decoder.finish()
            raise PiRpcError("pi RPC child exited before session close")
        except (PiRpcError, OSError) as exc:
            self._failure = exc if isinstance(exc, PiRpcError) else PiRpcError("pi RPC read failed")
            for _, future in self._pending.values():
                if not future.done():
                    future.set_exception(self._failure)
            self._pending.clear()
            await self._events.put(self._failure)

    async def _drain_stderr(self) -> None:
        """Drain diagnostics so provider output cannot deadlock the child."""
        process = self._process
        if process is None or process.stderr is None:
            raise PiRpcError("pi RPC stderr is unavailable")
        while await process.stderr.read(65_536):
            pass

    async def command(self, kind: str, **fields: object) -> dict[str, Any]:
        """Send one bounded RPC command and await its matching receipt only."""
        if self._process is None or self._process.stdin is None or self._failure is not None:
            raise PiRpcError("pi RPC child is unavailable")
        if not kind or "id" in fields or "type" in fields:
            raise PiRpcError("pi RPC command has invalid reserved fields")
        request_id = uuid.uuid4().hex
        body = {"id": request_id, "type": kind, **fields}
        encoded = (json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_COMMAND_BYTES:
            raise PiRpcError("pi RPC command exceeded its byte limit")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        async with self._write_lock:
            self._pending[request_id] = (kind, future)
            try:
                self._process.stdin.write(encoded)
                await self._process.stdin.drain()
            except (BrokenPipeError, ConnectionError, OSError) as exc:
                self._pending.pop(request_id, None)
                raise PiRpcError("pi RPC command could not reach the child") from exc
        try:
            response = await asyncio.wait_for(future, timeout=self._timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            await self.close()
            raise PiRpcError("pi RPC command response timed out") from exc
        except asyncio.CancelledError:
            await self.close()
            raise
        if response.get("success") is not True:
            raise PiRpcError(f"pi RPC {kind} command was refused")
        return response

    async def next_turn(self) -> PiTurn:
        """Await one model turn; callers needing full completion use ``next_settled``."""
        return await self._collect_turn(settled=False)

    async def next_settled(self) -> PiTurn:
        """Await the full pi run, including tool calls, retries and queued follow-ups."""
        return await self._collect_turn(settled=True)

    async def _collect_turn(self, *, settled: bool) -> PiTurn:
        """Accumulate provider messages until the requested completion boundary."""
        texts: list[str] = []
        is_error = False
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        stop_reason = ""
        tool_error = False
        while True:
            try:
                record = await asyncio.wait_for(self._events.get(), timeout=self._timeout)
            except (TimeoutError, asyncio.TimeoutError) as exc:
                await self.close()
                raise PiRpcError("pi RPC turn did not settle before timeout") from exc
            if isinstance(record, PiRpcError):
                raise record
            parsed = assistant_text(record)
            if parsed is not None:
                text, errored = parsed
                if text:
                    texts.append(text)
                is_error |= errored
                metrics = assistant_metrics(record)
                if metrics is None:
                    raise PiRpcError("pi RPC assistant metrics are missing")
                input_tokens += metrics[0]
                output_tokens += metrics[1]
                cost_usd += metrics[2]
                stop_reason = metrics[3]
            if record["type"] == "turn_end":
                results = record.get("toolResults")
                if not isinstance(results, list):
                    raise PiRpcError("pi RPC turn_end lacks tool results")
                tool_error |= any(
                    isinstance(item, dict) and item.get("isError") is True for item in results
                )
            if record["type"] == "agent_end":
                if type(record.get("willRetry")) is not bool:
                    raise PiRpcError("pi RPC agent_end lacks retry status")
                continue
            if record["type"] == ("agent_settled" if settled else "turn_end"):
                return PiTurn(
                    "\n".join(texts),
                    is_error or tool_error or not texts,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    stop_reason,
                    tool_error,
                )

    async def close(self) -> None:
        """Stop the child group, reap it, and cancel local drain tasks."""
        process = self._process
        if process is None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            elif process.returncode is None:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except (TimeoutError, asyncio.TimeoutError):
            if process.returncode is None:
                process.kill()
            await process.wait()
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        tasks = tuple(task for task in (self._reader, self._stderr) if task is not None)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._process = None
