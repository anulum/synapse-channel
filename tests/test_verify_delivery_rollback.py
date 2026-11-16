# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — released-hub rollback gate without an old interpreter
"""Exercise the rollback gate with stand-in interpreters, not a released wheel."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools/verify_delivery_rollback.py"


def _load() -> ModuleType:
    """Load the operator script from its path."""
    spec = importlib.util.spec_from_file_location("verify_delivery_rollback", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _interpreter(path: Path, body: str) -> Path:
    """Write an executable stand-in for the caller-supplied interpreter."""
    path.write_text(body)
    path.chmod(0o755)
    return path


async def test_a_quiet_interpreter_expires_preserved_queued_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A released hub that leaves the queue alone still yields one expiry."""
    python = _interpreter(tmp_path / "quiet-python", "#!/bin/sh\nexit 0\n")
    await _load()._verify(python)
    assert "roll-forward expired it" in capsys.readouterr().out


def test_a_failing_released_hub_is_reported(tmp_path: Path) -> None:
    """A non-zero interpreter is a loud failure and names its own stderr."""
    python = _interpreter(
        tmp_path / "failing-python",
        "#!/bin/sh\necho 'released hub refused the store' >&2\nexit 1\n",
    )
    with pytest.raises(RuntimeError, match="released hub refused the store"):
        _load()._run_released_hub(python, tmp_path / "hub.db", str(tmp_path))
