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
import time
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


def _quiet_provider(path: Path) -> Path:
    return _write_script(path, "raise SystemExit(0)\n")


def _recording_tmux(path: Path) -> Path:
    return _write_script(
        path,
        """
import json
import os
import pathlib
import sys

record = pathlib.Path(os.environ["TMUX_RECORD"])
entries = []
if record.exists():
    entries = json.loads(record.read_text(encoding="utf-8"))
entries.append(sys.argv[1:])
record.write_text(json.dumps(entries), encoding="utf-8")

if sys.argv[1:2] == ["has-session"]:
    raise SystemExit(1)
raise SystemExit(0)
""",
    )


def _recording_synapse(path: Path) -> Path:
    return _write_script(
        path,
        """
import json
import os
import pathlib
import sys

record = pathlib.Path(os.environ["SYNAPSE_RECORD"])
entries = []
if record.exists():
    entries = json.loads(record.read_text(encoding="utf-8"))
entries.append(sys.argv[1:])
record.write_text(json.dumps(entries), encoding="utf-8")
raise SystemExit(0)
""",
    )


def _failing_tmux(path: Path) -> Path:
    return _write_script(
        path,
        """
import sys

if sys.argv[1:2] == ["has-session"]:
    raise SystemExit(1)
print("tmux new-session failed", file=sys.stderr)
raise SystemExit(9)
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


def _read_json_when_ready(path: Path) -> Any:
    """Poll until ``path`` holds parseable JSON, not merely until it exists.

    A concurrently-written record can be present on disk before its writer has
    finished flushing, so parsing on the bare existence check catches an empty or
    partial file and raises ``JSONDecodeError`` — the create-then-write race that
    surfaced as a tmux worker-session flake under heavy parallel load. Retrying
    the parse (not just the existence probe) until the deadline absorbs it, and
    the two exhaustion messages distinguish "never written" from "written but
    never valid JSON".
    """
    deadline = time.monotonic() + 5
    decode_error: json.JSONDecodeError | None = None
    while time.monotonic() <= deadline:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            decode_error = exc
            time.sleep(0.01)
    if decode_error is not None:
        raise AssertionError(f"{path} never held valid JSON: {decode_error}")
    raise AssertionError(f"{path} was not written")


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

    sidecar_payload = _read_json_when_ready(sidecar_record)
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


def test_worker_session_rejects_invalid_tmux_mode() -> None:
    with pytest.raises(ValueError, match="terminal_tmux"):
        run_worker_session(
            identity="repo/codex-main",
            command=["codex"],
            terminal_tmux="invalid",
            arm=False,
            environ={},
        )


def test_worker_session_auto_tmux_mode_runs_directly_without_tty(tmp_path: Path) -> None:
    provider_record = tmp_path / "provider.json"
    provider = _write_script(
        tmp_path / "codex",
        """
import os
import pathlib

pathlib.Path(os.environ["PROVIDER_RECORD"]).write_text("direct", encoding="utf-8")
""",
    )

    assert (
        run_worker_session(
            identity="repo/codex-main",
            command=[str(provider)],
            terminal_tmux="auto",
            arm=False,
            environ={"PROVIDER_RECORD": str(provider_record)},
        )
        == 0
    )

    assert provider_record.read_text(encoding="utf-8") == "direct"


def test_worker_session_autostarts_interactive_provider_in_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux_record = tmp_path / "tmux.json"
    synapse_record = tmp_path / "synapse.json"
    provider_record = tmp_path / "provider.json"
    tmux = _recording_tmux(tmp_path / "tmux")
    synapse = _recording_synapse(tmp_path / "synapse")
    monkeypatch.setenv("TMUX_RECORD", str(tmux_record))
    monkeypatch.setenv("SYNAPSE_RECORD", str(synapse_record))
    provider = _write_script(
        tmp_path / "codex",
        f"""
import pathlib
pathlib.Path({str(provider_record)!r}).write_text("direct provider ran", encoding="utf-8")
""",
    )

    assert (
        run_worker_session(
            identity="repo/codex-main",
            command=[str(provider), "--sandbox", "danger-full-access"],
            project="repo",
            synapse_bin=str(synapse),
            tmux_bin=str(tmux),
            terminal_tmux="on",
            environ={
                "TMUX_RECORD": str(tmux_record),
                "SYNAPSE_RECORD": str(synapse_record),
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )
        == 0
    )

    tmux_calls = _read_json_when_ready(tmux_record)
    synapse_calls = _read_json_when_ready(synapse_record)
    assert tmux_calls[0][:3] == ["has-session", "-t", "synapse-repo_codex-main"]
    assert tmux_calls[1][:5] == ["new-session", "-d", "-s", "synapse-repo_codex-main", "-c"]
    assert "SYN_PROJECT=repo" in tmux_calls[1][-1]
    assert "SYN_IDENTITY=repo/codex-main" in tmux_calls[1][-1]
    assert "SYNAPSE_AUTO_CONNECT=0" in tmux_calls[1][-1]
    assert str(provider) in tmux_calls[1][-1]
    assert tmux_calls[-1] == ["attach-session", "-t", "synapse-repo_codex-main"]
    assert synapse_calls == [
        [
            "agent-tmux",
            "wait",
            "--identity",
            "repo/codex-main",
            "--session",
            "synapse-repo_codex-main",
            "--cwd",
            str(Path.cwd()),
            "--agent-command",
            f"{str(provider)} --sandbox danger-full-access",
        ]
    ]
    assert not provider_record.exists()


def test_worker_session_tmux_passes_auth_and_custom_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux_record = tmp_path / "tmux.json"
    synapse_record = tmp_path / "synapse.json"
    tmux = _recording_tmux(tmp_path / "tmux")
    synapse = _recording_synapse(tmp_path / "synapse")
    provider = _write_script(tmp_path / "claude", "raise SystemExit(0)\n")
    monkeypatch.setenv("TMUX_RECORD", str(tmux_record))
    monkeypatch.setenv("SYNAPSE_RECORD", str(synapse_record))

    assert (
        run_worker_session(
            identity="repo/claude-main",
            command=[str(provider)],
            uri="ws://localhost:9999",
            token="secret",
            synapse_bin=str(synapse),
            tmux_bin=str(tmux),
            terminal_tmux="on",
            environ={
                "TMUX_RECORD": str(tmux_record),
                "SYNAPSE_RECORD": str(synapse_record),
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )
        == 0
    )

    synapse_calls = _read_json_when_ready(synapse_record)
    assert synapse_calls[0][-4:] == ["--token", "secret", "--uri", "ws://localhost:9999"]


def test_worker_session_tmux_reuses_live_waiter_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux_record = tmp_path / "tmux.json"
    synapse_record = tmp_path / "synapse.json"
    runtime = tmp_path / "synapse-provider-tmux"
    runtime.mkdir()
    (runtime / "repo_codex-main.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    tmux = _recording_tmux(tmp_path / "tmux")
    synapse = _recording_synapse(tmp_path / "synapse")
    provider = _write_script(tmp_path / "codex", "raise SystemExit(0)\n")
    monkeypatch.setenv("TMUX_RECORD", str(tmux_record))
    monkeypatch.setenv("SYNAPSE_RECORD", str(synapse_record))

    assert (
        run_worker_session(
            identity="repo/codex-main",
            command=[str(provider)],
            synapse_bin=str(synapse),
            tmux_bin=str(tmux),
            terminal_tmux="on",
            environ={
                "TMUX_RECORD": str(tmux_record),
                "SYNAPSE_RECORD": str(synapse_record),
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )
        == 0
    )

    assert not synapse_record.exists()


def test_worker_session_tmux_restarts_stale_waiter_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux_record = tmp_path / "tmux.json"
    synapse_record = tmp_path / "synapse.json"
    runtime = tmp_path / "synapse-provider-tmux"
    runtime.mkdir()
    (runtime / "repo_codex-main.pid").write_text("stale\n", encoding="utf-8")
    tmux = _recording_tmux(tmp_path / "tmux")
    synapse = _recording_synapse(tmp_path / "synapse")
    provider = _write_script(tmp_path / "codex", "raise SystemExit(0)\n")
    monkeypatch.setenv("TMUX_RECORD", str(tmux_record))
    monkeypatch.setenv("SYNAPSE_RECORD", str(synapse_record))

    assert (
        run_worker_session(
            identity="repo/codex-main",
            command=[str(provider)],
            synapse_bin=str(synapse),
            tmux_bin=str(tmux),
            terminal_tmux="on",
            environ={
                "TMUX_RECORD": str(tmux_record),
                "SYNAPSE_RECORD": str(synapse_record),
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
        )
        == 0
    )

    assert _read_json_when_ready(synapse_record)[0][:2] == [
        "agent-tmux",
        "wait",
    ]


def test_worker_session_tmux_start_failure_returns_tmux_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = _write_script(tmp_path / "codex", "raise SystemExit(0)\n")

    code = run_worker_session(
        identity="repo/codex-main",
        command=[str(provider)],
        tmux_bin=str(_failing_tmux(tmp_path / "tmux")),
        terminal_tmux="on",
        environ={"XDG_RUNTIME_DIR": str(tmp_path)},
    )

    assert code == 9
    assert "tmux new-session failed" in capsys.readouterr().out


def test_worker_session_tmux_autostart_can_be_disabled_by_env(tmp_path: Path) -> None:
    provider_record = tmp_path / "provider.json"
    provider = _write_script(
        tmp_path / "codex",
        """
import json
import os
import pathlib

pathlib.Path(os.environ["PROVIDER_RECORD"]).write_text(
    json.dumps({"identity": os.environ.get("SYN_IDENTITY")}),
    encoding="utf-8",
)
""",
    )

    assert (
        run_worker_session(
            identity="repo/codex-main",
            command=[str(provider)],
            arm=False,
            environ={
                "SYNAPSE_PROVIDER_TMUX": "0",
                "SIDECAR_RECORD": str(tmp_path / "sidecar.json"),
                "PROVIDER_RECORD": str(provider_record),
            },
        )
        == 0
    )

    payload = json.loads(provider_record.read_text(encoding="utf-8"))
    assert payload["identity"] == "repo/codex-main"


def test_worker_session_sidecar_output_goes_to_runtime_log(tmp_path: Path) -> None:
    sidecar = _write_script(
        tmp_path / "syn",
        """
import sys
print("sidecar stdout leak")
print("sidecar stderr leak", file=sys.stderr)
""",
    )
    provider = _write_script(
        tmp_path / "provider",
        """
import pathlib
import time

log = pathlib.Path(__file__).with_name("synapse-worker-session").joinpath("repo_ux.log")
deadline = time.monotonic() + 5
while "sidecar stderr leak" not in log.read_text(encoding="utf-8"):
    if time.monotonic() > deadline:
        raise SystemExit("sidecar output did not reach runtime log")
    time.sleep(0.01)
""",
    )
    runner = _write_script(
        tmp_path / "run_session",
        f"""
from synapse_channel.worker_session import run_worker_session
raise SystemExit(
    run_worker_session(
        identity="repo/ux",
        command=[{str(provider)!r}],
        syn_bin={str(sidecar)!r},
        environ={{"XDG_RUNTIME_DIR": {str(tmp_path)!r}}},
    )
)
""",
    )

    proc = subprocess.run(
        [sys.executable, str(runner)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "sidecar stdout leak" not in proc.stdout
    assert "sidecar stderr leak" not in proc.stderr
    log = tmp_path / "synapse-worker-session" / "repo_ux.log"
    assert "sidecar stdout leak" in log.read_text(encoding="utf-8")
    assert "sidecar stderr leak" in log.read_text(encoding="utf-8")


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
    assert captured["terminal_tmux"] == "auto"


def test_cmd_worker_session_allows_toggling_terminal_tmux() -> None:
    captured: dict[str, Any] = {}

    def run_session(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = cli.build_parser().parse_args(
        [
            "worker-session",
            "--identity",
            "repo/ux",
            "--terminal-tmux",
            "off",
            "--",
            "codex",
        ]
    )

    assert cli_services._cmd_worker_session(ns, session_runner=run_session) == 0
    assert captured["terminal_tmux"] == "off"


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


def test_provider_command_name_handles_an_empty_command() -> None:
    from synapse_channel.worker_session import _provider_command_name

    assert _provider_command_name([]) == ""
    assert _provider_command_name(["/usr/bin/codex", "--flag"]) == "codex"


def test_pid_is_alive_treats_an_empty_pidfile_as_dead(tmp_path: Path) -> None:
    from synapse_channel.worker_session import _pid_is_alive

    pidfile = tmp_path / "waker.pid"
    pidfile.write_text("", encoding="utf-8")
    assert _pid_is_alive(pidfile) is False
