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
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from _platform_caps import requires_proc, requires_script_launch
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
binding = record.with_name(record.name + ".binding")

if sys.argv[1:2] == ["has-session"]:
    raise SystemExit(1)
if sys.argv[1:2] == ["display-message"]:
    # A tmux server answers, so starting a session creates no new server. The same
    # answer serves as the pane's process number, which a test may set.
    print(os.environ.get("TMUX_PANE_PID", "4242"))
if sys.argv[1:2] == ["new-session"]:
    values = []
    args = sys.argv[1:]
    for index, item in enumerate(args):
        if item == "-e" and index + 1 < len(args):
            value = args[index + 1]
            if value.startswith(("SYN_PROJECT=", "SYN_IDENTITY=")):
                values.append(value)
    binding.write_text("\\n".join(values) + "\\n", encoding="utf-8")
if sys.argv[1:2] == ["show-environment"]:
    if not binding.exists():
        raise SystemExit(1)
    print(binding.read_text(encoding="utf-8"), end="")
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


@requires_script_launch
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


@requires_script_launch
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


@requires_script_launch
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


@requires_script_launch
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
    assert tmux_calls[1] == ["display-message", "-p", "#{pid}"]
    assert tmux_calls[2][:4] == ["new-session", "-d", "-s", "synapse-repo_codex-main"]
    assert "-c" in tmux_calls[2]
    assert ["-e", "SYN_PROJECT=repo"] == tmux_calls[2][4:6]
    assert ["-e", "SYN_IDENTITY=repo/codex-main"] == tmux_calls[2][6:8]
    assert ["-e", "SYN_TMUX_PROVIDER=1"] == tmux_calls[2][8:10]
    assert ["-e", "SYNAPSE_AUTO_CONNECT=0"] == tmux_calls[2][10:12]
    assert "SYN_PROJECT=repo" in tmux_calls[2][-1]
    assert "SYN_IDENTITY=repo/codex-main" in tmux_calls[2][-1]
    assert "SYNAPSE_AUTO_CONNECT=0" in tmux_calls[2][-1]
    assert str(provider) in tmux_calls[2][-1]
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


@requires_script_launch
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


@requires_proc
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


@requires_script_launch
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


@requires_script_launch
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


@requires_script_launch
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


@requires_script_launch
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

    def reject_services(**_kwargs: Any) -> list[str]:
        raise ValueError("synapse executable path must not start with a systemd prefix")

    assert cli_services._cmd_init(ns, service_installer=reject_services) == 2
    assert "must not start with a systemd prefix" in capsys.readouterr().out


def test_provider_command_name_handles_an_empty_command() -> None:
    from synapse_channel.worker_session import _provider_command_name

    assert _provider_command_name([]) == ""
    assert _provider_command_name(["/usr/bin/codex", "--flag"]) == "codex"


def test_pid_is_alive_treats_an_empty_pidfile_as_dead(tmp_path: Path) -> None:
    from synapse_channel.worker_session import _pid_is_alive

    pidfile = tmp_path / "waker.pid"
    pidfile.write_text("", encoding="utf-8")
    assert _pid_is_alive(pidfile) is False


def _launch_records(state: Path) -> list[dict[str, Any]]:
    directory = state / "synapse" / "launch-records"
    if not directory.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.iterdir())]


def test_worker_session_records_what_it_started(tmp_path: Path) -> None:
    """A direct launch leaves one record of the command and of the process created."""
    seen = tmp_path / "provider-pid"

    code = run_worker_session(
        identity="repo/recorded",
        command=[
            sys.executable,
            "-c",
            f"import os, pathlib; pathlib.Path({str(seen)!r}).write_text(str(os.getpid()))",
        ],
        arm=False,
        environ={"XDG_STATE_HOME": str(tmp_path / "state")},
    )

    (record,) = _launch_records(tmp_path / "state")
    assert code == 0
    assert record["seat"] == "repo/recorded"
    assert record["command_words"] == 3
    assert record["command_name"] == os.path.basename(sys.executable)
    # What is recorded is the process the launcher created. On Windows an environment's
    # interpreter is a small starter that runs the real one as its child, so the program
    # sees another number there.
    assert isinstance(record["process"]["pid"], int)
    if sys.platform != "win32":
        assert record["process"]["pid"] == int(seen.read_text(encoding="utf-8"))
    assert record["launcher_pid"] == os.getpid()
    assert len(record["executable"]["sha256"]) == 64


def test_worker_session_without_a_state_or_home_directory_keeps_no_record(tmp_path: Path) -> None:
    code = run_worker_session(
        identity="repo/unrecorded",
        command=[sys.executable, "-c", "raise SystemExit(3)"],
        arm=False,
        environ={"UNRELATED": str(tmp_path)},
    )

    assert code == 3
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("identity", "reason"),
    [
        ("repo/blocked", "launch record not written"),
        ("repo/con\x07trol", "the seat is 1 to"),
    ],
)
def test_worker_session_says_when_its_record_cannot_be_kept_and_still_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], identity: str, reason: str
) -> None:
    """An unwritable place or a seat no record can carry costs the record, not the session."""
    blocked = tmp_path / "a-file"
    blocked.write_text("x", encoding="ascii")
    state = blocked if identity == "repo/blocked" else tmp_path / "state"

    code = run_worker_session(
        identity=identity,
        command=[sys.executable, "-c", "raise SystemExit(7)"],
        project="repo",
        arm=False,
        environ={"XDG_STATE_HOME": str(state)},
    )

    assert code == 7
    assert "synapse worker-session: launch record not written" in capsys.readouterr().err
    assert reason in "launch record not written" or _launch_records(tmp_path / "state") == []


@requires_script_launch
@pytest.mark.parametrize("answer", ["a real process", ""])
def test_worker_session_records_the_pane_process_when_tmux_names_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """The tmux path records the pane's process; without a number it records nothing."""
    tmux = _recording_tmux(tmp_path / "tmux")
    synapse = _recording_synapse(tmp_path / "synapse")
    provider = _write_script(tmp_path / "codex", "raise SystemExit(0)")
    pane = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    environ = {
        "TMUX_RECORD": str(tmp_path / "tmux.json"),
        "SYNAPSE_RECORD": str(tmp_path / "synapse.json"),
        "XDG_RUNTIME_DIR": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "TMUX_PANE_PID": str(pane.pid) if answer else "",
    }
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    try:
        code = run_worker_session(
            identity="repo/codex-main",
            command=[str(provider)],
            project="repo",
            synapse_bin=str(synapse),
            tmux_bin=str(tmux),
            terminal_tmux="on",
            environ=environ,
        )
        records = _launch_records(tmp_path / "state")
    finally:
        pane.kill()
        pane.wait()

    assert code == 0
    if answer:
        (record,) = records
        assert record["seat"] == "repo/codex-main"
        assert record["process"]["pid"] == pane.pid
        assert record["command_name"] == "codex"
    else:
        assert records == []


@pytest.mark.skipif(sys.platform == "win32", reason="an interrupt signal is delivered differently")
def test_worker_session_interrupted_while_waiting_does_not_leave_the_provider(
    tmp_path: Path,
) -> None:
    """An interrupt of the launcher ends the provider it started."""
    seen = tmp_path / "provider-pid"
    interrupt = threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGINT))
    interrupt.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            run_worker_session(
                identity="repo/interrupted",
                command=[
                    sys.executable,
                    "-c",
                    "import os, pathlib, time; "
                    f"pathlib.Path({str(seen)!r}).write_text(str(os.getpid())); time.sleep(30)",
                ],
                arm=False,
                environ={"UNRELATED": "1"},
            )
    finally:
        interrupt.cancel()

    assert not _process_exists(int(seen.read_text(encoding="utf-8")))
