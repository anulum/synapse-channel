# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for provider-neutral worker-session launcher

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_services
from synapse_channel.worker_session import run_worker_session


def _write_script(path: Path, source: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
    path.chmod(0o700)
    return path


def _recording_sidecar(path: Path) -> Path:
    return _write_script(
        path,
        """
import json
import os
import pathlib
import signal
import sys
import time

record = pathlib.Path(os.environ["SIDECAR_RECORD"])
record.write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "pid": os.getpid(),
            "project": os.environ.get("SYN_PROJECT"),
            "identity": os.environ.get("SYN_IDENTITY"),
        }
    ),
    encoding="utf-8",
)
signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
while True:
    time.sleep(0.05)
""",
    )


def _stubborn_sidecar(path: Path) -> Path:
    return _write_script(
        path,
        """
import json
import os
import pathlib
import signal
import sys
import time

record = pathlib.Path(os.environ["SIDECAR_RECORD"])
record.write_text(
    json.dumps({"argv": sys.argv[1:], "pid": os.getpid()}),
    encoding="utf-8",
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(0.05)
""",
    )


def _provider(path: Path) -> Path:
    return _write_script(
        path,
        """
import json
import os
import pathlib
import sys
import time

sidecar_record = pathlib.Path(os.environ["SIDECAR_RECORD"])
deadline = time.monotonic() + 5
while not sidecar_record.exists():
    if time.monotonic() > deadline:
        raise SystemExit("sidecar did not arm")
    time.sleep(0.01)

provider_record = pathlib.Path(os.environ["PROVIDER_RECORD"])
provider_record.write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "project": os.environ.get("SYN_PROJECT"),
            "identity": os.environ.get("SYN_IDENTITY"),
        }
    ),
    encoding="utf-8",
)
""",
    )


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_worker_session_sets_identity_and_starts_sidecar(tmp_path: Path) -> None:
    sidecar_record = tmp_path / "sidecar.json"
    provider_record = tmp_path / "provider.json"
    sidecar = _recording_sidecar(tmp_path / "syn")
    provider = _provider(tmp_path / "provider")

    assert (
        run_worker_session(
            identity="repo/ux",
            command=[str(provider), "run"],
            syn_bin=str(sidecar),
            environ={
                "SIDECAR_RECORD": str(sidecar_record),
                "PROVIDER_RECORD": str(provider_record),
            },
        )
        == 0
    )

    sidecar_payload = json.loads(sidecar_record.read_text(encoding="utf-8"))
    provider_payload = json.loads(provider_record.read_text(encoding="utf-8"))
    assert sidecar_payload["argv"] == ["arm", "--uri", "ws://localhost:8876"]
    assert sidecar_payload["project"] == "repo"
    assert sidecar_payload["identity"] == "repo/ux"
    assert provider_payload["argv"] == ["run"]
    assert provider_payload["project"] == "repo"
    assert provider_payload["identity"] == "repo/ux"


def test_worker_session_passes_sidecar_auth_options(tmp_path: Path) -> None:
    sidecar_record = tmp_path / "sidecar.json"
    provider_record = tmp_path / "provider.json"
    sidecar = _recording_sidecar(tmp_path / "syn")
    provider = _provider(tmp_path / "provider")

    assert (
        run_worker_session(
            identity="repo/ux",
            command=[str(provider)],
            uri="ws://localhost:9999",
            syn_bin=str(sidecar),
            token="secret",
            token_file="/tmp/token",
            environ={
                "SIDECAR_RECORD": str(sidecar_record),
                "PROVIDER_RECORD": str(provider_record),
            },
        )
        == 0
    )

    sidecar_payload = json.loads(sidecar_record.read_text(encoding="utf-8"))
    assert sidecar_payload["argv"] == [
        "arm",
        "--uri",
        "ws://localhost:9999",
        "--token",
        "secret",
        "--token-file",
        "/tmp/token",
    ]


def test_worker_session_kills_sidecar_after_graceful_timeout(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("SIGTERM ignore/kill semantics are POSIX-specific")
    sidecar_record = tmp_path / "sidecar.json"
    provider_record = tmp_path / "provider.json"
    sidecar = _stubborn_sidecar(tmp_path / "syn")
    provider = _provider(tmp_path / "provider")

    assert (
        run_worker_session(
            identity="repo/ux",
            command=[str(provider)],
            syn_bin=str(sidecar),
            environ={
                "SIDECAR_RECORD": str(sidecar_record),
                "PROVIDER_RECORD": str(provider_record),
            },
            sidecar_shutdown_timeout_seconds=0.05,
        )
        == 0
    )

    sidecar_pid = json.loads(sidecar_record.read_text(encoding="utf-8"))["pid"]
    assert not _process_exists(sidecar_pid)


def test_worker_session_can_skip_sidecar() -> None:
    assert (
        run_worker_session(
            identity="repo/ux",
            command=[sys.executable, "-c", "import sys; sys.exit(7)"],
            arm=False,
            environ={},
        )
        == 7
    )


def test_worker_session_rejects_empty_command() -> None:
    with pytest.raises(ValueError, match="provider command"):
        run_worker_session(identity="repo/ux", command=[], environ={})


def test_parser_worker_session() -> None:
    args = cli.build_parser().parse_args(["worker-session", "--identity", "repo/ux", "--", "codex"])
    assert args.func is cli_services._cmd_worker_session
    assert args.identity == "repo/ux"
    assert args.command == ["--", "codex"]


def test_cmd_worker_session_dispatches() -> None:
    captured: dict[str, Any] = {}

    def run_session(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = cli.build_parser().parse_args(
        ["worker-session", "--identity", "repo/ux", "--project", "repo", "--", "codex"]
    )
    assert cli_services._cmd_worker_session(ns, session_runner=run_session) == 0
    assert captured["identity"] == "repo/ux"
    assert captured["command"] == ["codex"]


def test_cmd_worker_session_rejects_missing_command(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(["worker-session", "--identity", "repo/ux"])
    assert cli_services._cmd_worker_session(ns) == 2
    assert "requires a provider command" in capsys.readouterr().out


def test_cmd_worker_session_reports_value_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**kwargs: Any) -> int:
        raise ValueError("bad worker command")

    ns = cli.build_parser().parse_args(["worker-session", "--identity", "repo/ux", "--", "cmd"])
    assert cli_services._cmd_worker_session(ns, session_runner=fail) == 2
    assert "bad worker command" in capsys.readouterr().out


def test_cmd_init_prints_service_suggestions(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(["init", "--project", "repo", "--identity", "repo/ux"])
    assert cli_services._cmd_init(ns) == 0
    out = capsys.readouterr().out
    assert "User services are not installed automatically" in out
    assert "synapse-arm@.service" in out


def test_cmd_init_defaults_project_to_current_directory(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "repo-from-cwd"
    project_dir.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "init"],
        cwd=project_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    out = proc.stdout
    assert "repo-from-cwd" in out


def test_cmd_init_uses_project_resolver(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(["init"])

    assert cli_services._cmd_init(ns, project_resolver=lambda: "repo-from-resolver") == 0
    out = capsys.readouterr().out
    assert "repo-from-resolver" in out


def test_cmd_init_defaults_project_to_process_cwd(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(["init"])

    assert cli_services._cmd_init(ns) == 0
    out = capsys.readouterr().out
    assert Path.cwd().name in out


def test_cmd_init_installs_user_services(capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, Any] = {}

    def install_services(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["wrote synapse-hub.service", "wrote synapse-arm@.service"]

    ns = cli.build_parser().parse_args(
        [
            "init",
            "--project",
            "repo",
            "--identity",
            "repo/ux",
            "--install-user-services",
            "--synapse-bin",
            "/bin/synapse",
        ]
    )
    assert cli_services._cmd_init(ns, service_installer=install_services) == 0
    assert captured == {
        "project": "repo",
        "identity": "repo/ux",
        "synapse_bin": "/bin/synapse",
        "start": False,
    }
    assert "synapse-arm@.service" in capsys.readouterr().out
