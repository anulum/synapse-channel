# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hermetic Synapse-executable resolution tests
"""Prove hook recipes resolve the Synapse executable without an ambient PATH.

A contributor whose virtual-environment ``bin`` directory is not exported on
``PATH`` must still get provider-hook and adapter recipes that resolve to an
absolute executable, rather than failing closed on the bare ``synapse`` name.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from synapse_channel.cli_claim_hook_common import resolve_synapse_binary


def test_uses_path_resolution_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "synapse"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda _name: str(real))

    assert resolve_synapse_binary(None) == str(real.resolve())


def test_falls_back_to_interpreter_adjacent_script_off_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv_bin = tmp_path / "bin"
    venv_bin.mkdir()
    interpreter = venv_bin / "python"
    interpreter.write_text("")
    script = venv_bin / "synapse"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(sys, "executable", str(interpreter))

    assert resolve_synapse_binary("synapse") == str(script.resolve())


def test_raises_when_absent_from_path_and_interpreter_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))

    with pytest.raises(ValueError, match="cannot resolve Synapse executable 'synapse'"):
        resolve_synapse_binary("synapse")


def test_pathlike_name_does_not_use_the_interpreter_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a name carrying a directory component is an explicit path, not the bare
    # console script, so the interpreter-adjacent fallback must not apply.
    venv_bin = tmp_path / "bin"
    venv_bin.mkdir()
    (venv_bin / "synapse").write_text("#!/bin/sh\n")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))

    with pytest.raises(ValueError, match="cannot resolve Synapse executable 'custom/synapse'"):
        resolve_synapse_binary("custom/synapse")
