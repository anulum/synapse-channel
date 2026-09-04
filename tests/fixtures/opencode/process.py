# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated OpenCode acceptance process runner
"""Resolve and run the pinned OpenCode process in an isolated environment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from fixtures.opencode.llm import provider_config

OPENCODE_VERSION = "1.17.20"
TEST_MODEL = "test/test-model"


def isolated_environment(
    home: Path,
    llm_url: str,
    *,
    pure: bool,
    disable_project_config: bool,
) -> dict[str, str]:
    """Return a filesystem-isolated OpenCode environment for real processes."""
    # OpenCode installs its plugin SDK in every discovered configuration directory,
    # even when the test plugin is a dependency-free local file.  A fresh acceptance
    # home must not turn startup into a registry availability test.  OpenCode 1.17.20
    # explicitly skips dependency installation for a non-writable config directory.
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(mode=0o500, parents=True, exist_ok=True)
    config_dir.chmod(0o500)
    environment = {
        **os.environ,
        "OPENCODE_TEST_HOME": str(home),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "OPENCODE_CONFIG_CONTENT": json.dumps(provider_config(llm_url)),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_AUTOCOMPACT": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "1",
        "OPENCODE_AUTH_CONTENT": "{}",
        "NO_COLOR": "1",
    }
    for key in ("OPENCODE_PURE", "OPENCODE_DISABLE_PROJECT_CONFIG"):
        environment.pop(key, None)
    if pure:
        environment["OPENCODE_PURE"] = "1"
    if disable_project_config:
        environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
    return environment


def governed_project_environment(environment: Mapping[str, str], project: Path) -> dict[str, str]:
    """Load the installed local adapter without OpenCode's registry bootstrap.

    The adapter installer remains the source of truth for both the MCP entry and
    the generated claim-guard plugin.  This function only moves those exact
    installed values into the already-isolated config-content channel so OpenCode
    does not rediscover the writable project ``.opencode`` directory and start a
    background SDK installation before the real governance turn.
    """
    config_path = project / ".opencode" / "opencode.json"
    plugin_path = project / ".opencode" / "plugins" / "synapse-claim-guard.js"
    try:
        installed = json.loads(config_path.read_text(encoding="utf-8"))
        configured = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        message = "installed OpenCode adapter configuration is unreadable"
        raise AssertionError(message) from exc
    if not isinstance(installed, dict) or not isinstance(configured, dict):
        raise AssertionError("installed OpenCode adapter configuration must be an object")
    if not plugin_path.is_file():
        raise AssertionError("installed OpenCode claim-guard plugin is missing")

    result = dict(environment)
    configured.update(installed)
    configured["plugin"] = [plugin_path.resolve().as_uri()]
    result["OPENCODE_CONFIG_CONTENT"] = json.dumps(configured)
    result["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
    return result


def find_opencode() -> str:
    """Return the exact pinned OpenCode binary, or skip when it is unavailable.

    These are real-process acceptance tests: they need the pinned ``opencode``
    ``1.17.20`` binary installed (the dedicated ``opencode-integration`` workflow
    provides it via ``OPENCODE_BIN``). When the binary is absent or the wrong
    version — the general test matrix, where OpenCode is not installed — the test
    skips rather than failing, so acceptance stays gated on the real binary
    without reddening runs that never had it.
    """
    binary = os.environ.get("OPENCODE_BIN", "").strip() or shutil.which("opencode")
    if binary is None:
        pytest.skip("OpenCode acceptance requires the pinned opencode binary; not installed")
    completed = subprocess.run(  # nosec B603
        [binary, "--version"], capture_output=True, text=True, check=False, timeout=15
    )
    if completed.returncode != 0 or completed.stdout.strip() != OPENCODE_VERSION:
        pytest.skip(
            f"OpenCode acceptance requires version {OPENCODE_VERSION}, got "
            f"{completed.stdout.strip() or 'unavailable'}"
        )
    return binary


def run_opencode(
    binary: str,
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    """Run one real OpenCode process without a shell."""
    return subprocess.run(  # nosec B603
        [binary, *args],
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
