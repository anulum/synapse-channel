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
import shutil
import subprocess
import time
from pathlib import Path
from typing import cast

import pytest

from e2e.opencode_editors import zed_client


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


def test_required_executable_rejects_missing_or_relative_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="required executable is unavailable: xdotool"):
        zed_client._required_executable("xdotool")

    monkeypatch.setattr(shutil, "which", lambda _name: "bin/xdotool")
    with pytest.raises(RuntimeError, match="required executable is unavailable: xdotool"):
        zed_client._required_executable("xdotool")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/xdotool")
    assert zed_client._required_executable("xdotool") == "/usr/bin/xdotool"


def test_xdotool_timeout_is_normalised_and_checked_action_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["xdotool"], 10, output="partial")

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", timeout)
    completed = zed_client._run_xdotool("search")
    assert completed.returncode == 124
    assert completed.stdout == "partial"
    assert completed.stderr == "xdotool command timed out"

    monkeypatch.setattr(
        zed_client,
        "_run_xdotool",
        lambda *_args: subprocess.CompletedProcess([], 2, "", "display unavailable"),
    )
    with pytest.raises(RuntimeError, match="could not focus: display unavailable"):
        zed_client._checked_xdotool("focus", "windowfocus", "123")


def test_checked_xdotool_accepts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        zed_client,
        "_run_xdotool",
        lambda *_args: subprocess.CompletedProcess([], 0, "", ""),
    )
    zed_client._checked_xdotool("focus", "windowfocus", "123")


def test_window_search_parser_accepts_only_exact_search_shapes() -> None:
    selector = ("--class", "zed")
    assert (
        zed_client._window_ids(
            subprocess.CompletedProcess([], 1, "", ""),
            selector=selector,
        )
        == ()
    )
    assert zed_client._window_ids(
        subprocess.CompletedProcess([], 0, "123\n123\n", ""),
        selector=selector,
    ) == ("123",)


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (subprocess.CompletedProcess([], 2, "", "display unavailable"), "could not search"),
        (subprocess.CompletedProcess([], 0, "", "warning"), "unclassifiable"),
        (subprocess.CompletedProcess([], 0, "", ""), "unclassifiable"),
        (subprocess.CompletedProcess([], 0, "invalid\n", ""), "malformed"),
        (subprocess.CompletedProcess([], 0, "0\n", ""), "malformed"),
    ],
)
def test_window_search_parser_rejects_transport_and_identifier_failures(
    result: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        zed_client._window_ids(result, selector=("--class", "zed"))


def test_window_discovery_deduplicates_selectors_and_rejects_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zed_client,
        "_run_xdotool",
        lambda *_args: subprocess.CompletedProcess([], 0, "123\n", ""),
    )
    assert zed_client._find_window(float("inf")) == "123"

    outputs = iter(("123\n", "456\n", "123\n"))
    monkeypatch.setattr(
        zed_client,
        "_run_xdotool",
        lambda *_args: subprocess.CompletedProcess([], 0, next(outputs), ""),
    )
    with pytest.raises(RuntimeError, match="multiple visible candidate windows"):
        zed_client._find_window(float("inf"))


def test_window_discovery_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter((0.0, 1.0))
    sleeps: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(
        zed_client,
        "_run_xdotool",
        lambda *_args: subprocess.CompletedProcess([], 1, "", ""),
    )

    with pytest.raises(RuntimeError, match="did not expose a visible window"):
        zed_client._find_window(0.5)
    assert sleeps == [0.25]


def test_trace_wait_accepts_marker_and_rejects_exit_or_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = tmp_path / "trace.jsonl"
    live = _FakeZedProcess()
    assert zed_client._trace_has(trace, "session/new") is False
    trace.write_text('{"method":"session/new"}\n', encoding="utf-8")
    assert zed_client._trace_has(trace, "session/new") is True
    zed_client._wait_for_trace(trace, "session/new", float("inf"), _process(live), "session")

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

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
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

    monkeypatch.setattr(shutil, "which", lambda _name: None)
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

    process = _FakeZedProcess(final_returncode=final_returncode, timeout_once=timeout_once)
    popen_calls: list[tuple[list[str], Path]] = []

    def popen(command: list[str], **kwargs: object) -> _FakeZedProcess:
        popen_calls.append((command, cast(Path, kwargs["cwd"])))
        return process

    actions: list[tuple[str, tuple[str, ...]]] = []
    waits: list[tuple[str, float, str]] = []

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
    monkeypatch.setattr(zed_client, "_find_window", lambda _deadline: "123")
    monkeypatch.setattr(
        zed_client,
        "_checked_xdotool",
        lambda action, *args: actions.append((action, args)),
    )
    monkeypatch.setattr(zed_client, "_wait_for_trace", wait_for_trace)
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
        ('"method":"session/new"', 80.0, "ACP session creation"),
        ('"method":"session/prompt"', 90.0, "ACP prompt delivery"),
        ('"response_to":"session/prompt"', 90.0, "ACP prompt response"),
    ]
    assert (
        "open the configured ACP agent",
        ("key", "--window", "123", "ctrl+alt+shift+F12"),
    ) in actions
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

    process = _FakeZedProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(zed_client, "_find_window", lambda _deadline: "123")
    monkeypatch.setattr(zed_client, "_checked_xdotool", lambda *_args: None)
    monkeypatch.setattr(zed_client, "_wait_for_trace", lambda *_args: None)
    monkeypatch.setattr(zed_client, "_capture_screenshot", lambda _path: False)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="without screenshot evidence"):
        zed_client.main()
    assert process.terminated is True
