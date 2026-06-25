# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral worker-session launcher
"""Run a provider command with a cheap Synapse wake sidecar."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence

SIDECAR_SHUTDOWN_TIMEOUT_SECONDS = 5.0
"""Seconds to wait for a wake sidecar to exit after graceful termination."""


def _project_from_identity(identity: str) -> str:
    """Return the project segment of a ``project`` or ``project/worker`` identity."""
    return identity.split("/", 1)[0].strip()


def run_worker_session(
    *,
    identity: str,
    command: Sequence[str],
    project: str | None = None,
    uri: str = "ws://localhost:8876",
    syn_bin: str = "syn",
    token: str | None = None,
    token_file: str | None = None,
    arm: bool = True,
    environ: Mapping[str, str] | None = None,
    sidecar_shutdown_timeout_seconds: float = SIDECAR_SHUTDOWN_TIMEOUT_SECONDS,
) -> int:
    """Run ``command`` with ``SYN_PROJECT``/``SYN_IDENTITY`` and an optional waker.

    The sidecar is a local ``syn arm`` process. It holds a socket and prints wake
    messages, but it does not call a model provider and therefore does not spend
    tokens while waiting.
    """
    if not command:
        raise ValueError("worker-session requires a provider command")
    env = dict(os.environ if environ is None else environ)
    resolved_project = (project or _project_from_identity(identity)).strip()
    env["SYN_PROJECT"] = resolved_project
    env["SYN_IDENTITY"] = identity.strip()

    sidecar: subprocess.Popen[bytes] | None = None
    if arm:
        arm_cmd = [syn_bin, "arm", "--uri", uri]
        if token:
            arm_cmd.extend(["--token", token])
        if token_file:
            arm_cmd.extend(["--token-file", token_file])
        sidecar = subprocess.Popen(arm_cmd, env=env)

    try:
        return subprocess.run(list(command), env=env, check=False).returncode
    finally:
        if sidecar is not None and sidecar.poll() is None:
            sidecar.terminate()
            try:
                sidecar.wait(timeout=sidecar_shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                sidecar.kill()
                sidecar.wait()
