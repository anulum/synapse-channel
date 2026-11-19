# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor names the machine-key data home

from __future__ import annotations

import re
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.machine_identity import ensure_machine_identity

_DISK_FREE = re.compile(r"has \d+(?:\.\d+)? MiB free")
"""Live free-space figure. It is not part of the machine-key check."""


def _run_doctor(capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Drive the shipped doctor command against a closed hub port."""
    code = cli.main(
        [
            "doctor",
            "--uri",
            "ws://127.0.0.1:1",
            "--project",
            "SYNAPSE-CHANNEL",
            "--id",
            "seat",
        ]
    )
    return code, capsys.readouterr().out


def _machine_key_lines(report: str) -> list[str]:
    """Return the machine-key check line and its remedy, when one is printed."""
    lines = report.splitlines()
    chosen: list[str] = []
    for index, line in enumerate(lines):
        if "machine-key-home:" not in line:
            continue
        chosen.append(line)
        if index + 1 < len(lines) and lines[index + 1].lstrip().startswith("→"):
            chosen.append(lines[index + 1])
        break
    return chosen


def _without_live_disk_free(report: str) -> str:
    """Hide the live free-space tenth so two runs can be compared."""
    return _DISK_FREE.sub("has DISK MiB free", report)


def test_doctor_names_xdg_data_home_on_two_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two shipped doctor runs in one data home both name that key and agree."""
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    data_home.mkdir()
    home.mkdir()
    identity = ensure_machine_identity(base=data_home)
    assert identity.key_path.is_file()
    assert data_home in identity.key_path.parents
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SYN_PROJECT", "SYNAPSE-CHANNEL")
    monkeypatch.delenv("SYN_IDENTITY", raising=False)
    reports: list[str] = []
    for _ in range(2):
        code, report = _run_doctor(capsys)
        assert code == 1
        reports.append(report)
    assert _machine_key_lines(reports[0]) == _machine_key_lines(reports[1])
    assert _without_live_disk_free(reports[0]) == _without_live_disk_free(reports[1])
    for report in reports:
        assert "XDG_DATA_HOME holds the pinned machine key" in report
        assert str(identity.key_path) in report
        assert "4013" in report
        assert "second machine key" in report


def test_doctor_passes_when_data_home_is_the_xdg_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A data home equal to the XDG default passes and names that key path."""
    home = tmp_path / "home"
    home.mkdir()
    default_home = home / ".local" / "share"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", f"  {default_home}  ")
    monkeypatch.setenv("SYN_PROJECT", "SYNAPSE-CHANNEL")
    monkeypatch.delenv("SYN_IDENTITY", raising=False)
    code, report = _run_doctor(capsys)
    assert code == 1
    lines = _machine_key_lines(report)
    assert lines == [
        "[ok] machine-key-home: XDG_DATA_HOME holds the pinned machine key at "
        f"{default_home / 'synapse' / 'identity' / 'machine.pem'}"
    ]


def test_doctor_names_the_default_when_xdg_data_home_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unset data home still names the XDG default path for the key."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("SYN_PROJECT", "SYNAPSE-CHANNEL")
    monkeypatch.delenv("SYN_IDENTITY", raising=False)
    code, report = _run_doctor(capsys)
    assert code == 1
    key = home / ".local" / "share" / "synapse" / "identity" / "machine.pem"
    lines = _machine_key_lines(report)
    assert lines == [
        "[ok] machine-key-home: XDG_DATA_HOME holds the pinned machine key at "
        f"{key} (XDG_DATA_HOME is unset; the XDG default applies)"
    ]


def test_doctor_uses_process_home_when_home_is_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty HOME falls back to the process home for the default key path."""
    monkeypatch.setenv("HOME", "")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("SYN_PROJECT", "SYNAPSE-CHANNEL")
    monkeypatch.delenv("SYN_IDENTITY", raising=False)
    code, report = _run_doctor(capsys)
    assert code == 1
    key = Path.home() / ".local" / "share" / "synapse" / "identity" / "machine.pem"
    lines = _machine_key_lines(report)
    assert lines == [
        "[ok] machine-key-home: XDG_DATA_HOME holds the pinned machine key at "
        f"{key} (XDG_DATA_HOME is unset; the XDG default applies)"
    ]
