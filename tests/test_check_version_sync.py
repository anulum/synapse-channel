# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the version-sync guard

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "check_version_sync.py"
_SPEC = importlib.util.spec_from_file_location("check_version_sync", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
cvs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cvs)


def test_pyproject_version_matches_package() -> None:
    from synapse_channel import __version__

    assert cvs._pyproject_version() == __version__


def test_discover_returns_every_surface() -> None:
    surfaces = cvs.discover()
    assert set(surfaces) == {
        "src/synapse_channel/__init__.py",
        "README.md citation",
        "CITATION.cff",
    }
    # The repository surfaces are all in sync, so each equals the canonical version.
    assert set(surfaces.values()) == {cvs._pyproject_version()}


def test_main_passes_when_in_sync(capsys: pytest.CaptureFixture[str]) -> None:
    assert cvs.main() == 0
    assert "in sync" in capsys.readouterr().out


def test_main_fails_on_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cvs, "discover", lambda: {"CITATION.cff": "0.0.1-stale"})
    assert cvs.main() == 1
    assert "desync" in capsys.readouterr().out
