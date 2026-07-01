# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end smoke journey: worker-session runs a provider under a wake sidecar.

``synapse worker-session`` wraps a provider command with an optional Synapse wake
sidecar and a resolved identity. The unit suite drives it with an injected process
runner; this journey instead runs the packaged CLI as a real subprocess launching
a real provider, with the sidecar disabled (``--no-arm``) and tmux off so no hub
or terminal is needed. It proves the two contracts a wrapper must keep: the
provider inherits the resolved ``SYN_IDENTITY``/``SYN_PROJECT`` environment, and
the provider's exit code is passed straight through.
"""

from __future__ import annotations

import sys

from cli_e2e_helpers import run_cli

_IDENTITY = "E2E/worker"
_PROJECT = "E2E"


def test_worker_session_runs_provider_with_resolved_identity_environment() -> None:
    """The wrapped provider inherits the resolved identity and project variables."""
    reader = (
        "import os; "
        "print(os.environ.get('SYN_IDENTITY', '') + '|' + os.environ.get('SYN_PROJECT', ''))"
    )
    result = run_cli(
        "worker-session",
        "--identity",
        _IDENTITY,
        "--no-arm",
        "--terminal-tmux",
        "off",
        "--",
        sys.executable,
        "-c",
        reader,
    )
    assert result.ok(), result.output
    assert f"{_IDENTITY}|{_PROJECT}" in result.stdout


def test_worker_session_passes_the_provider_exit_code_through() -> None:
    """A non-zero provider exit becomes the worker-session exit code unchanged."""
    result = run_cli(
        "worker-session",
        "--identity",
        _IDENTITY,
        "--no-arm",
        "--terminal-tmux",
        "off",
        "--",
        sys.executable,
        "-c",
        "import sys; sys.exit(7)",
    )
    assert result.returncode == 7, result.output
