# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real OpenCode ACP acceptance exchange
"""Perform one bounded ACP initialize exchange with a real OpenCode process."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def acp_initialize(
    binary: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> tuple[dict[str, Any], str]:
    """Perform one real ACP initialize exchange and close cleanly on stdin EOF."""
    process = subprocess.Popen(  # nosec B603
        [binary, "acp", "--cwd", str(cwd)],
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise AssertionError("OpenCode ACP pipes were not created")
    stdin = process.stdin
    stdout = process.stdout
    stderr_stream = process.stderr
    responses: queue.Queue[str] = queue.Queue()

    def _read_response() -> None:
        for line in stdout:
            responses.put(line)

    reader = threading.Thread(target=_read_response, daemon=True)
    reader.start()
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientCapabilities": {"_meta": {"terminal-auth": True}},
            "clientInfo": {"name": "synapse-channel-test", "version": "0.1.0"},
        },
    }
    try:
        stdin.write(json.dumps(request) + "\n")
        stdin.flush()
        line = responses.get(timeout=30)
        stdin.close()
        process.wait(timeout=10)
        stderr = stderr_stream.read()
        if process.returncode != 0:
            raise AssertionError(f"OpenCode ACP exited {process.returncode}: {stderr[-2000:]}")
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise AssertionError("OpenCode ACP returned a non-object JSON-RPC response")
        return decoded, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)
