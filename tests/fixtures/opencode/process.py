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


def find_opencode() -> str:
    """Return the exact installed OpenCode binary or fail the acceptance test."""
    binary = os.environ.get("OPENCODE_BIN", "").strip() or shutil.which("opencode")
    if binary is None:
        raise AssertionError("OpenCode acceptance requires the pinned opencode binary")
    completed = subprocess.run(  # nosec B603
        [binary, "--version"], capture_output=True, text=True, check=False, timeout=15
    )
    if completed.returncode != 0 or completed.stdout.strip() != OPENCODE_VERSION:
        raise AssertionError(
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
