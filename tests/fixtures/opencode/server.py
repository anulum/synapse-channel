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
    server_env = {
        **env,
        "OPENCODE_SERVER_USERNAME": username,
        "OPENCODE_SERVER_PASSWORD": password,
    }
    process = subprocess.Popen(  # nosec B603
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

    def _drain_stdout() -> None:
        for line in stdout:
            stdout_lines.put(line)

    def _drain_stderr() -> None:
        stderr_lines.extend(stderr)

    stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + 20
    url = ""
    try:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                line = stdout_lines.get(timeout=0.2)
            except queue.Empty:
                continue
            match = re.search(r"listening on (http://127\.0\.0\.1:\d+)", line)
            if match is not None:
                url = match.group(1)
                break
        if not url:
            raise AssertionError(
                "OpenCode server did not become ready: " + "".join(stderr_lines)[-2000:]
            )
        yield OpenCodeServer(url=url, stderr=stderr_lines)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
