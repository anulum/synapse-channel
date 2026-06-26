# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — safe stale waiter reaping tests

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.ergonomics import Identity
from synapse_channel.reap import (
    ReapResult,
    ReapStatus,
    WaiterProcess,
    discover_waiters,
    main,
    read_proc_cmdline,
    reap_waiter,
    runtime_dir,
    safe_key,
)


def _identity() -> Identity:
    return Identity("SYNAPSE-CHANNEL", "SYNAPSE-CHANNEL/codex-1", "env", True)


def test_safe_key_matches_shell_hook_contract() -> None:
    assert safe_key("SYNAPSE-CHANNEL/codex-1") == "SYNAPSE-CHANNEL_codex-1"
    assert safe_key("a b:c") == "a_b_c"


def test_runtime_dir_matches_shell_hook_contract() -> None:
    assert runtime_dir({"XDG_RUNTIME_DIR": "/run/user/1000"}) == Path(
        "/run/user/1000/synapse-shell"
    )
    assert runtime_dir({}) == Path("/tmp/synapse-shell")


def test_discover_waiters_lists_identity_scoped_pidfiles(tmp_path: Path) -> None:
    identity = _identity()
    pidfile = tmp_path / "SYNAPSE-CHANNEL_codex-1.pid"
    pidfile.write_text("1234\n", encoding="utf-8")
    (tmp_path / "OTHER.pid").write_text("9999\n", encoding="utf-8")

    found = discover_waiters(
        identity,
        runtime=tmp_path,
        cmdline_reader=lambda pid: (
            ["synapse", "arm", "--name", identity.waiter_name, "--for", identity.project]
            if pid == 1234
            else None
        ),
    )

    assert found == [
        WaiterProcess(
            pid=1234,
            identity=identity.identity,
            waiter_name=identity.waiter_name,
            project=identity.project,
            pidfile=pidfile,
            argv=("synapse", "arm", "--name", identity.waiter_name, "--for", identity.project),
            live=True,
            verified=True,
        )
    ]


def test_discover_waiters_marks_stale_dead_pidfiles(tmp_path: Path) -> None:
    identity = _identity()
    pidfile = tmp_path / "SYNAPSE-CHANNEL_codex-1.pid"
    pidfile.write_text("1234\n", encoding="utf-8")

    found = discover_waiters(identity, runtime=tmp_path, cmdline_reader=lambda pid: None)

    assert found[0].pid == 1234
    assert found[0].live is False
    assert found[0].verified is False


def test_discover_waiters_ignores_missing_and_invalid_pidfiles(tmp_path: Path) -> None:
    identity = _identity()

    assert discover_waiters(identity, runtime=tmp_path) == []

    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("not-a-pid\n", encoding="utf-8")
    assert discover_waiters(identity, runtime=tmp_path) == []


def test_discover_waiters_ignores_unreadable_pidfile_path(tmp_path: Path) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").mkdir()

    assert discover_waiters(identity, runtime=tmp_path) == []


def test_read_proc_cmdline_reads_current_process() -> None:
    assert isinstance(read_proc_cmdline(os.getpid()), tuple)


def test_read_proc_cmdline_returns_none_for_missing_pid() -> None:
    assert read_proc_cmdline(-1) is None


def test_reap_waiter_removes_dead_pidfile_without_signalling(tmp_path: Path) -> None:
    identity = _identity()
    pidfile = tmp_path / "SYNAPSE-CHANNEL_codex-1.pid"
    pidfile.write_text("1234\n", encoding="utf-8")
    signals: list[tuple[int, signal.Signals]] = []

    result = reap_waiter(
        identity,
        1234,
        runtime=tmp_path,
        cmdline_reader=lambda pid: None,
        killer=lambda pid, sig: signals.append((pid, sig)),
    )

    assert result == ReapResult(status=ReapStatus.REMOVED_STALE_PIDFILE, pid=1234, detail=None)
    assert not pidfile.exists()
    assert signals == []


def test_reap_waiter_tolerates_pidfile_already_gone(tmp_path: Path) -> None:
    identity = _identity()
    pidfile = tmp_path / "SYNAPSE-CHANNEL_codex-1.pid"
    pidfile.write_text("1234\n", encoding="utf-8")

    def reader(pid: int) -> None:
        pidfile.unlink()
        return None

    result = reap_waiter(identity, 1234, runtime=tmp_path, cmdline_reader=reader)

    assert result == ReapResult(status=ReapStatus.REMOVED_STALE_PIDFILE, pid=1234, detail=None)


def test_reap_waiter_reports_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _identity()
    pidfile = tmp_path / "SYNAPSE-CHANNEL_codex-1.pid"
    pidfile.write_text("1234\n", encoding="utf-8")
    original_unlink = Path.unlink

    def fail_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == pidfile:
            raise OSError("locked")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    result = reap_waiter(identity, 1234, runtime=tmp_path, cmdline_reader=lambda pid: None)

    assert result == ReapResult(status=ReapStatus.SIGNAL_FAILED, pid=1234, detail="locked")


def test_reap_waiter_returns_not_found_for_pid_mismatch(tmp_path: Path) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("1234\n", encoding="utf-8")

    result = reap_waiter(identity, 5678, runtime=tmp_path, cmdline_reader=lambda pid: None)

    assert result == ReapResult(
        status=ReapStatus.NOT_FOUND, pid=5678, detail="no matching identity pidfile"
    )


def test_reap_waiter_signals_only_verified_identity_waiter(tmp_path: Path) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("1234\n", encoding="utf-8")
    signals: list[tuple[int, signal.Signals]] = []

    result = reap_waiter(
        identity,
        1234,
        runtime=tmp_path,
        cmdline_reader=lambda pid: [
            "synapse",
            "arm",
            "--name",
            identity.waiter_name,
            "--for",
            identity.project,
            "--directed-only",
        ],
        killer=lambda pid, sig: signals.append((pid, sig)),
    )

    assert result == ReapResult(status=ReapStatus.SIGNALED, pid=1234, detail="TERM")
    assert signals == [(1234, signal.SIGTERM)]


def test_reap_waiter_reports_signal_failure(tmp_path: Path) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("1234\n", encoding="utf-8")

    def fail_kill(pid: int, sig: signal.Signals) -> None:
        raise OSError("gone")

    result = reap_waiter(
        identity,
        1234,
        runtime=tmp_path,
        cmdline_reader=lambda pid: [
            "synapse",
            "arm",
            "--name",
            identity.waiter_name,
            "--for",
            identity.project,
        ],
        killer=fail_kill,
    )

    assert result == ReapResult(status=ReapStatus.SIGNAL_FAILED, pid=1234, detail="gone")


def test_reap_waiter_refuses_unverified_process(tmp_path: Path) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("1234\n", encoding="utf-8")

    result = reap_waiter(
        identity,
        1234,
        runtime=tmp_path,
        cmdline_reader=lambda pid: ["python", "-m", "http.server"],
        killer=lambda pid, sig: pytest.fail("unexpected signal"),
    )

    assert result.status is ReapStatus.REFUSED_UNVERIFIED


def test_main_lists_waiters(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("1234\n", encoding="utf-8")

    assert (
        main(
            identity,
            [],
            runtime=tmp_path,
            cmdline_reader=lambda pid: [
                "synapse",
                "arm",
                "--name",
                identity.waiter_name,
                "--for",
                identity.project,
            ],
        )
        == 0
    )
    assert "1234 live verified" in capsys.readouterr().out


def test_main_lists_empty_identity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_identity(), [], runtime=tmp_path) == 0
    assert "no waiter pidfile" in capsys.readouterr().out


def test_main_cleanup_reports_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("1234\n", encoding="utf-8")
    signals: list[tuple[int, signal.Signals]] = []

    assert (
        main(
            identity,
            ["--pid", "1234"],
            runtime=tmp_path,
            cmdline_reader=lambda pid: [
                "synapse",
                "arm",
                "--name",
                identity.waiter_name,
                "--for",
                identity.project,
            ],
            killer=lambda pid, sig: signals.append((pid, sig)),
        )
        == 0
    )

    assert "signaled: 1234 (TERM)" in capsys.readouterr().out
    assert signals == [(1234, signal.SIGTERM)]


def test_main_cleanup_reports_refusal_as_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    identity = _identity()
    (tmp_path / "SYNAPSE-CHANNEL_codex-1.pid").write_text("1234\n", encoding="utf-8")

    assert (
        main(
            identity,
            ["--pid", "1234"],
            runtime=tmp_path,
            cmdline_reader=lambda pid: ["python", "-m", "http.server"],
            killer=lambda pid, sig: pytest.fail("unexpected signal"),
        )
        == 1
    )
    assert "refused" in capsys.readouterr().err


def test_syn_reap_alias_is_packaged() -> None:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on Python 3.10
        import tomli as tomllib

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts: dict[str, Any] = pyproject["project"]["scripts"]

    assert scripts["syn-reap"] == "synapse_channel.ergonomics:alias_reap"


@pytest.mark.parametrize("path", [Path("README.md"), Path("docs/cli.md"), Path("docs/recipes.md")])
def test_syn_reap_is_documented(path: Path) -> None:
    assert "syn reap" in path.read_text(encoding="utf-8")
