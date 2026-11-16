# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real OpenCode acceptance server lifecycle
"""Start and stop one authenticated real OpenCode server for acceptance."""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpenCodeServer:
    """A running real OpenCode server and its captured diagnostics."""

    url: str
    stderr: list[str]


@dataclass(frozen=True)
class _StartedOpenCodeServer:
    """One launched server, its drain threads, and the lines those threads own."""

    process: subprocess.Popen[str]
    stdout_lines: queue.Queue[str]
    stderr_lines: list[str]
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread


def _start_the_opencode_server(
    binary: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
    username: str,
    password: str,
) -> _StartedOpenCodeServer:
    """Launch the server and let each drain thread own one pipe."""
    server_env = {
        **env,
        "OPENCODE_SERVER_USERNAME": username,
        "OPENCODE_SERVER_PASSWORD": password,
    }
    process = subprocess.Popen(
        [binary, "serve", "--hostname", "127.0.0.1", "--port", "0"],
        cwd=cwd,
        env=server_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise AssertionError("OpenCode server pipes were not created")
    stdout = process.stdout
    stderr = process.stderr
    stdout_lines: queue.Queue[str] = queue.Queue()
    stderr_lines: list[str] = []

    # Each drain thread owns its pipe and closes it when the stream ends;
    # closing it from the main thread while a read is blocked would wait on
    # the buffer lock.
    def _drain_stdout() -> None:
        try:
            for line in stdout:
                stdout_lines.put(line)
        finally:
            stdout.close()

    def _drain_stderr() -> None:
        try:
            stderr_lines.extend(stderr)
        finally:
            stderr.close()

    stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    return _StartedOpenCodeServer(
        process=process,
        stdout_lines=stdout_lines,
        stderr_lines=stderr_lines,
        stdout_thread=stdout_thread,
        stderr_thread=stderr_thread,
    )


def _wait_for_the_listening_url(started: _StartedOpenCodeServer) -> str:
    """Read stdout until the server prints its loopback URL, or fail closed."""
    deadline = time.monotonic() + 20
    url = ""
    while time.monotonic() < deadline and started.process.poll() is None:
        try:
            line = started.stdout_lines.get(timeout=0.2)
        except queue.Empty:
            continue
        match = re.search(r"listening on (http://127\.0\.0\.1:\d+)", line)
        if match is not None:
            url = match.group(1)
            break
    if not url:
        raise AssertionError(
            "OpenCode server did not become ready: " + "".join(started.stderr_lines)[-2000:]
        )
    return url


def _stop_the_opencode_server(started: _StartedOpenCodeServer) -> None:
    """Terminate the server, kill it after five seconds, and join both drains."""
    process = started.process
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    started.stdout_thread.join(timeout=2)
    started.stderr_thread.join(timeout=2)


@contextmanager
def running_opencode_server(
    binary: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
    username: str,
    password: str,
) -> Iterator[OpenCodeServer]:
    """Start an authenticated real OpenCode server on an OS-assigned port."""
    started = _start_the_opencode_server(
        binary,
        cwd=cwd,
        env=env,
        username=username,
        password=password,
    )
    try:
        url = _wait_for_the_listening_url(started)
        yield OpenCodeServer(url=url, stderr=started.stderr_lines)
    finally:
        _stop_the_opencode_server(started)
