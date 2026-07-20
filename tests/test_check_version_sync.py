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
from textwrap import dedent

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
        ".zenodo.json",
        "server.json top-level",
        "server.json package synapse-channel",
    }
    # The repository surfaces are all in sync, so each equals the canonical version.
    assert set(surfaces.values()) == {cvs._pyproject_version()}


def test_main_passes_when_in_sync(capsys: pytest.CaptureFixture[str]) -> None:
    assert cvs.main() == 0
    assert "in sync" in capsys.readouterr().out


def test_main_fails_on_real_surface_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "src" / "synapse_channel").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "synapse_channel" / "__init__.py").write_text(
        '__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "@software{synapse_channel,\n  version = {1.2.3}\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "CITATION.cff").write_text(
        "version: 0.0.1-stale\n",
        encoding="utf-8",
    )
    (tmp_path / ".zenodo.json").write_text(
        dedent(
            """\
            {
              "version": "1.2.3",
              "packages": [
                {"identifier": "synapse-channel", "version": "1.2.3"}
              ]
            }
            """
        ),
        encoding="utf-8",
    )
    # The MCP registry metadata is a version surface too (0.99.2 lesson: the
    # release bump missed it because the checker did not know it).
    (tmp_path / "server.json").write_text(
        dedent(
            """\
            {
              "version": "1.2.3"
            }
            """
        ),
        encoding="utf-8",
    )

    assert cvs.main(tmp_path) == 1
    assert "desync" in capsys.readouterr().out


def test_main_fails_when_nested_server_package_version_drifts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A matching top-level MCP version must not hide a stale package pin."""
    (tmp_path / "src" / "synapse_channel").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "src" / "synapse_channel" / "__init__.py").write_text(
        '__version__ = "1.2.3"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "@software{synapse_channel,\n  version = {1.2.3}\n}\n", encoding="utf-8"
    )
    (tmp_path / "CITATION.cff").write_text("version: 1.2.3\n", encoding="utf-8")
    (tmp_path / ".zenodo.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")
    (tmp_path / "server.json").write_text(
        dedent(
            """\
            {
              "version": "1.2.3",
              "packages": [
                {"identifier": "synapse-channel", "version": "1.2.2"}
              ]
            }
            """
        ),
        encoding="utf-8",
    )

    assert cvs.main(tmp_path) == 1
    assert "server.json package synapse-channel='1.2.2'" in capsys.readouterr().out
