# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Zed GUI acceptance-driver contracts
"""Verify the pinned Zed profile, X11 transport, and ACP orchestration."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import cast

import pytest

from e2e.opencode_editors import zed_client
from e2e.opencode_editors.editor_process_runner import PROCESS_GROUP_SUPERVISION_ENV


class _FakeZedProcess:
    """Minimal process seam for the Zed driver tests."""

    def __init__(self, *, final_returncode: int = -15, timeout_once: bool = False) -> None:
        self.returncode: int | None = None
        self.final_returncode = final_returncode
        self.timeout_once = timeout_once
        self.killed = False
        self.terminated = False

    def poll(self) -> int | None:
        """Report the process as live until cleanup."""
        return self.returncode

    def terminate(self) -> None:
        """Record graceful termination."""
        self.terminated = True
        self.returncode = self.final_returncode

    def wait(self, timeout: float) -> int:
        """Return the configured status or exercise one escalation."""
        if self.timeout_once:
            self.timeout_once = False
            raise subprocess.TimeoutExpired(["zed"], timeout)
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        """Record forced termination after a bounded wait."""
        self.killed = True
        self.returncode = -9


def _process(process: _FakeZedProcess) -> subprocess.Popen[str]:
    """Cast the deliberately small test double to the production protocol."""
    return cast(subprocess.Popen[str], process)


def test_required_environment_rejects_missing_and_trims_present_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNAPSE_ZED_TEST_VALUE", raising=False)
    with pytest.raises(RuntimeError, match="SYNAPSE_ZED_TEST_VALUE is required"):
        zed_client._required_env("SYNAPSE_ZED_TEST_VALUE")

    monkeypatch.setenv("SYNAPSE_ZED_TEST_VALUE", " value ")
    assert zed_client._required_env("SYNAPSE_ZED_TEST_VALUE") == "value"


def test_trace_wait_and_session_readiness_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = tmp_path / "trace.jsonl"
    live = _FakeZedProcess()
    assert zed_client._trace_has(trace, "session/new") is False
    trace.write_text('{"method":"session/new"}\n', encoding="utf-8")
    assert zed_client._trace_has(trace, "session/new") is True
    zed_client._wait_for_trace(trace, "session/new", float("inf"), _process(live), "session")
    monkeypatch.setattr(zed_client, "has_ready_session", lambda _trace: True)
    zed_client._wait_for_ready_session(trace, float("inf"), _process(live))

    trace.unlink()
    sleeps: list[float] = []

    def publish_trace(seconds: float) -> None:
        sleeps.append(seconds)
        trace.write_text('{"method":"session/new"}\n', encoding="utf-8")

    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(time, "sleep", publish_trace)
    zed_client._wait_for_trace(trace, "session/new", 1.0, _process(live), "session")
    assert sleeps == [0.25]

    trace.unlink()
    exited = _FakeZedProcess()
    exited.returncode = 7
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    with pytest.raises(RuntimeError, match="exited before session: exit status 7"):
        zed_client._wait_for_trace(trace, "session/new", 1.0, _process(exited), "session")

    with pytest.raises(RuntimeError, match="never reached session before timeout"):
        zed_client._wait_for_trace(trace, "session/new", 0.0, _process(live), "session")


def test_profile_uses_custom_data_config_with_private_exact_contract(tmp_path: Path) -> None:
    data_root = tmp_path / "zed-e2e"
    zed_client._write_profile(data_root, ["/opt/proxy", "--trace", "/tmp/trace"])

    settings_path = data_root / "config" / "settings.json"
    keymap_path = data_root / "config" / "keymap.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    keymap = json.loads(keymap_path.read_text(encoding="utf-8"))
    assert settings["agent_servers"]["synapse-opencode"] == {
        "type": "custom",
        "command": "/opt/proxy",
        "args": ["--trace", "/tmp/trace"],
        "env": {},
    }
    assert keymap == [
        {
            "bindings": {
                "ctrl-alt-shift-f12": [
                    "agent::NewExternalAgentThread",
                    {"agent": "synapse-opencode"},
                ]
            }
        }
    ]
    assert "ctrl-alt-shift-o" not in keymap_path.read_text(encoding="utf-8")
    assert settings_path.stat().st_mode & 0o777 == 0o600
    assert keymap_path.stat().st_mode & 0o777 == 0o600


def test_screenshot_capture_reports_success_failure_and_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screenshot = tmp_path / "zed.png"

    def capture(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command[-1]).write_bytes(b"png")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(zed_client, "required_executable", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", capture)
    assert zed_client._capture_screenshot(screenshot) is True

    screenshot.unlink()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "failed"),
    )
    assert zed_client._capture_screenshot(screenshot) is False

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["import"], 15)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert zed_client._capture_screenshot(screenshot) is False

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    screenshot.touch()
    assert zed_client._capture_screenshot(screenshot) is False
    screenshot.unlink()

    def os_error(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("executable disappeared")

    monkeypatch.setattr(subprocess, "run", os_error)
    assert zed_client._capture_screenshot(screenshot) is False

    def missing_executable(_name: str) -> str:
        raise RuntimeError("missing")

    monkeypatch.setattr(zed_client, "required_executable", missing_executable)
    assert zed_client._capture_screenshot(screenshot) is False


@pytest.mark.parametrize("proxy_json", ["{}", "[]", '["opencode", 1]', '["opencode", ""]'])
def test_main_rejects_invalid_proxy_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proxy_json: str,
) -> None:
    required = {
        "SYNAPSE_ZED_BIN": str(tmp_path / "zed"),
        "SYNAPSE_EDITOR_E2E_PROJECT": str(tmp_path / "project"),
        "SYNAPSE_ACP_TRACE": str(tmp_path / "trace"),
        "SYNAPSE_EDITOR_E2E_PROMPT": "prompt",
        "SYNAPSE_ACP_PROXY_ARGV_JSON": proxy_json,
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(PROCESS_GROUP_SUPERVISION_ENV, "1")

    with pytest.raises(RuntimeError, match="must contain non-empty string arguments"):
        zed_client.main()


@pytest.mark.parametrize(
    ("final_returncode", "timeout_once", "expected_killed"),
    [(-15, False, False), (7, True, True)],
)
def test_main_uses_isolated_profile_and_runs_the_exact_acp_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_returncode: int,
    timeout_once: bool,
    expected_killed: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    artifacts = tmp_path / "artifacts"
    xdg_data = tmp_path / "xdg-data"
    xdg_config = tmp_path / "must-not-be-used"
    for directory in (project, artifacts, xdg_data, xdg_config):
        directory.mkdir()
    environment = {
        "SYNAPSE_ZED_BIN": str(tmp_path / "zed"),
        "SYNAPSE_EDITOR_E2E_PROJECT": str(project),
        "SYNAPSE_ACP_TRACE": str(artifacts / "trace.jsonl"),
        "SYNAPSE_EDITOR_E2E_PROMPT": "governed prompt",
        "SYNAPSE_ACP_PROXY_ARGV_JSON": '["/opt/proxy", "--acp"]',
        "SYNAPSE_EDITOR_E2E_ARTIFACT_DIR": str(artifacts),
        "XDG_DATA_HOME": str(xdg_data),
        "XDG_CONFIG_HOME": str(xdg_config),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(PROCESS_GROUP_SUPERVISION_ENV, "1")

    process = _FakeZedProcess(final_returncode=final_returncode, timeout_once=timeout_once)
    popen_calls: list[tuple[list[str], Path]] = []

    def popen(command: list[str], **kwargs: object) -> _FakeZedProcess:
        popen_calls.append((command, cast(Path, kwargs["cwd"])))
        return process

    actions: list[tuple[str, tuple[str, ...], float]] = []
    focused_inputs: list[tuple[str, float]] = []
    waits: list[tuple[str, float, str]] = []
    session_waits: list[float] = []

    def wait_for_trace(
        _trace: Path,
        marker: str,
        deadline: float,
        _process_argument: subprocess.Popen[str],
        stage: str,
    ) -> None:
        waits.append((marker, deadline, stage))

    def screenshot(path: Path) -> bool:
        path.write_bytes(b"png")
        return True

    clock = iter((10.0, 20.0, 30.0))
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(
        zed_client,
        "find_owned_window",
        lambda _deadline, **_kwargs: "123",
    )
    monkeypatch.setattr(
        zed_client,
        "checked_xdotool",
        lambda action, *args, deadline: actions.append((action, args, deadline)),
    )
    monkeypatch.setattr(zed_client, "bounded_sleep", lambda _deadline, _seconds: None)
    monkeypatch.setattr(
        zed_client,
        "focus_window_for_input",
        lambda window, *, deadline: focused_inputs.append((window, deadline)),
    )
    monkeypatch.setattr(zed_client, "_wait_for_trace", wait_for_trace)
    monkeypatch.setattr(
        zed_client,
        "_wait_for_ready_session",
        lambda _trace, deadline, _process_argument: session_waits.append(deadline),
    )
    monkeypatch.setattr(zed_client, "_capture_screenshot", screenshot)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    assert zed_client.main() == 0
    assert popen_calls == [
        (
            [
                str(tmp_path / "zed"),
                "--foreground",
                "--user-data-dir",
                str(xdg_data / "zed-e2e"),
                str(project),
            ],
            project,
        )
    ]
    assert (xdg_data / "zed-e2e" / "config" / "settings.json").is_file()
    assert not (xdg_config / "zed" / "settings.json").exists()
    assert waits == [
        ('"method":"session/prompt"', 90.0, "ACP prompt delivery"),
        ('"response_to":"session/prompt"', 90.0, "ACP prompt response"),
    ]
    assert session_waits == [80.0]
    assert (
        "open the configured ACP agent",
        ("key", "--window", "123", "ctrl+alt+shift+F12"),
        70.0,
    ) in actions
    assert (
        "type the Zed prompt",
        ("type", "--clearmodifiers", "--delay", "12", "--", "governed prompt"),
        90.0,
    ) in actions
    assert (
        "submit the Zed prompt",
        ("key", "--clearmodifiers", "Return"),
        90.0,
    ) in actions
    assert focused_inputs == [("123", 90.0), ("123", 90.0)]
    assert process.terminated is True
    assert process.killed is expected_killed
    assert (artifacts / "zed.png").read_bytes() == b"png"
    captured = capsys.readouterr()
    assert (captured.err == "") is (final_returncode == -15)


def test_main_fails_when_success_evidence_cannot_be_captured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    artifacts = tmp_path / "artifacts"
    data = tmp_path / "data"
    for directory in (project, artifacts, data):
        directory.mkdir()
    for name, value in {
        "SYNAPSE_ZED_BIN": str(tmp_path / "zed"),
        "SYNAPSE_EDITOR_E2E_PROJECT": str(project),
        "SYNAPSE_ACP_TRACE": str(artifacts / "trace.jsonl"),
        "SYNAPSE_EDITOR_E2E_PROMPT": "prompt",
        "SYNAPSE_ACP_PROXY_ARGV_JSON": '["/opt/proxy"]',
        "SYNAPSE_EDITOR_E2E_ARTIFACT_DIR": str(artifacts),
        "XDG_DATA_HOME": str(data),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(PROCESS_GROUP_SUPERVISION_ENV, "1")

    process = _FakeZedProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        zed_client,
        "find_owned_window",
        lambda _deadline, **_kwargs: "123",
    )
    monkeypatch.setattr(zed_client, "checked_xdotool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(zed_client, "bounded_sleep", lambda *_args: None)
    monkeypatch.setattr(zed_client, "focus_window_for_input", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(zed_client, "_wait_for_trace", lambda *_args: None)
    monkeypatch.setattr(zed_client, "_wait_for_ready_session", lambda *_args: None)
    monkeypatch.setattr(zed_client, "_capture_screenshot", lambda _path: False)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="without screenshot evidence"):
        zed_client.main()
    assert process.terminated is True


def test_main_refuses_unsupervised_process_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PROCESS_GROUP_SUPERVISION_ENV, raising=False)
    with pytest.raises(RuntimeError, match="process-group supervision"):
        zed_client.main()
