# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real JetBrains ACP client orchestration tests
"""Verify the real IDEA/OpenCode ACP orchestration path."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from e2e.opencode_editors import jetbrains_client, jetbrains_x11_driver
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard


def test_selector_lifecycle_is_not_owned_by_the_client_orchestrator() -> None:
    """Pin selector responsibilities to bounded, focused production modules."""
    root = Path(__file__).parent / "e2e" / "opencode_editors"
    client = (root / "jetbrains_client.py").read_text(encoding="utf-8")
    selector = (root / "jetbrains_selector.py").read_text(encoding="utf-8")
    selector_windows = (root / "jetbrains_selector_windows.py").read_text(encoding="utf-8")

    window_contract = (
        "def is_agent_selector_popup(",
        "def visible_jetbrains_window_rectangles(",
        "def owned_agent_selector_popups(",
    )
    lifecycle_contract = (
        "def find_agent_selector_popup(",
        "def inspect_pinned_agent(",
        "def select_pinned_agent(",
    )
    for name in window_contract + lifecycle_contract:
        assert name not in client
    for name in window_contract:
        assert name not in selector
        assert name in selector_windows
    for name in lifecycle_contract:
        assert name in selector
        assert name not in selector_windows
    assert len(client.splitlines()) < 350
    assert len(selector.splitlines()) < 500
    assert len(selector_windows.splitlines()) < 500


def test_required_environment_fails_closed_on_missing_or_blank_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNAPSE_REQUIRED_TEST_VALUE", raising=False)
    with pytest.raises(RuntimeError, match="SYNAPSE_REQUIRED_TEST_VALUE is required"):
        jetbrains_client._required_env("SYNAPSE_REQUIRED_TEST_VALUE")

    monkeypatch.setenv("SYNAPSE_REQUIRED_TEST_VALUE", " value ")
    assert jetbrains_client._required_env("SYNAPSE_REQUIRED_TEST_VALUE") == "value"


class _FakeIdeaProcess:
    """Minimal live process seam for the orchestration acceptance test."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def poll(self) -> None:
        """Report the fake IDEA process as live."""
        return None


class _FakeLifecycle:
    """Record lifecycle gates exercised by the client orchestrator."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def assert_at_most_one(self) -> None:
        """Record the pre-exact cardinality gate."""
        self._events.append("at-most-one")

    def require_none(self) -> None:
        """Record the pre-selection zero-lifecycle gate."""
        self._events.append("none")

    def require_configured_state(self) -> None:
        """Record the dormant state gate after configuration reload."""
        self._events.append("configured-state")

    def require_initialized_state(self) -> None:
        """Record the exact backend gate before the first prompt."""
        self._events.append("initialized-state")

    def require_exactly_one(self) -> None:
        """Record the post-handshake exact cardinality gate."""
        self._events.append("exactly-one")

    def idea_contents(self) -> str:
        """Return ordered readiness evidence for the matcher callback."""
        return (
            "Required plugins check passed\n"
            "Starting ACP client session 1\n"
            "Received notification: AvailableCommandsUpdate\n"
        )


def _main_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point the driver at a throwaway project, home and artefact directory."""
    project = tmp_path / "project"
    artifacts = tmp_path / "artifacts"
    home = tmp_path / "home"
    data = tmp_path / "data"
    for directory in (project, artifacts, home, data):
        directory.mkdir()
    environment = {
        "SYNAPSE_JETBRAINS_BIN": str(tmp_path / "idea"),
        "SYNAPSE_JETBRAINS_PLUGINS": str(tmp_path / "plugins"),
        "SYNAPSE_EDITOR_E2E_PROJECT": str(project),
        "SYNAPSE_ACP_TRACE": str(tmp_path / "trace.jsonl"),
        "SYNAPSE_EDITOR_E2E_PROMPT": "governed prompt",
        "SYNAPSE_ACP_PROXY_ARGV_JSON": '["/opt/opencode", "acp"]',
        "HOME": str(home),
        "USERPROFILE": str(home),
        "SYNAPSE_EDITOR_E2E_ARTIFACT_DIR": str(artifacts),
        "XDG_DATA_HOME": str(data),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return artifacts, home


def _stub_setup(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    lifecycle: _FakeLifecycle,
    returncode: int,
) -> None:
    """Replace the IDE's first-run, profile and process start with recorders."""

    def inactive_config(target_home: Path) -> None:
        config_dir = target_home / ".jetbrains"
        config_dir.mkdir()
        config = config_dir / "acp.json"
        config.write_text("inactive", encoding="utf-8")
        config.chmod(0o600)
        events.append("inactive-config")

    def capture_lifecycle(*_args: object, **_kwargs: object) -> _FakeLifecycle:
        events.append("lifecycle-capture")
        return lifecycle

    for name, replacement in (
        ("render_acp_config", lambda *_args, **_kwargs: "{}"),
        ("write_inactive_acp_config", inactive_config),
        ("write_idea_profile", lambda *_args, **_kwargs: events.append("profile")),
        ("idea_command", lambda *_args, **_kwargs: ["idea"]),
        ("complete_first_run_agreements", lambda _deadline: events.append("agreements")),
        ("find_project_window", lambda _deadline: "123"),
        ("skip_islands_onboarding", lambda _deadline, _window: events.append("onboarding")),
        ("capture_evidence_and_terminate", lambda *_a, **_k: events.append("cleanup")),
    ):
        monkeypatch.setattr(jetbrains_client, name, replacement)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _FakeIdeaProcess(returncode))
    monkeypatch.setattr(JetBrainsLifecycleGuard, "capture", capture_lifecycle)


def _stub_evidence(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    lifecycle: _FakeLifecycle,
    missing: str | None,
) -> None:
    """Record every log and trace wait by its first marker; ``missing`` never appears."""

    def wait_log(
        log_root: Path, markers: object, _deadline: float, *_a: object, **kw: object
    ) -> None:
        (log_root / "idea.log").write_text(lifecycle.idea_contents(), encoding="utf-8")
        for hook in ("retry", "guard"):
            if kw.get(hook) is not None:
                cast(Callable[[], object], kw[hook])()
        reader, matcher = kw.get("contents_reader"), kw.get("matcher")
        if reader is not None and matcher is not None:
            assert cast(Callable[[str], bool], matcher)(cast(Callable[[], str], reader)())
        first = markers if isinstance(markers, str) else cast(tuple[str, ...], markers)[0]
        events.append(f"log:{first}")

    def wait_trace(_trace: Path, marker: str, *_a: object, **kw: object) -> None:
        if kw.get("guard") is not None:
            cast(Callable[[], object], kw["guard"])()
        if marker == missing:
            raise RuntimeError(f"IntelliJ IDEA ACP trace never contained {marker}")
        events.append(marker)

    def screenshot(path: Path, **_kwargs: object) -> None:
        path.write_bytes(b"png")
        events.append(path.name)

    monkeypatch.setattr(jetbrains_client, "wait_for_idea_log", wait_log)
    monkeypatch.setattr(jetbrains_client, "wait_for_trace", wait_trace)
    monkeypatch.setattr(jetbrains_client, "capture_screenshot", screenshot)


def _stub_x11(monkeypatch: pytest.MonkeyPatch, events: list[str], home: Path) -> None:
    """Replace the X11 input steps with recorders; saving the config writes it."""

    def checked_xdotool(action: str, *_args: object, **_kwargs: object) -> None:
        if action == "enter the exact JetBrains ACP configuration":
            (home / ".jetbrains" / "acp.json").write_text("{}", encoding="utf-8")
        events.append("chat")

    for name, replacement in (
        ("_checked_xdotool", checked_xdotool),
        ("_focus_window_for_input", lambda *_a, **_k: events.append("focus")),
        ("_pointer_click", lambda *_a, **_k: events.append("editor-click")),
        ("_submit_chat_prompt", lambda *_a, **_k: events.append("prompt")),
    ):
        monkeypatch.setattr(jetbrains_x11_driver, name, replacement)


def _orchestrate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    returncode: int = 0,
    missing: str | None = None,
) -> Path:
    """Wire every stub, run the driver's ``main``, and return the artefact directory."""
    artifacts, home = _main_environment(tmp_path, monkeypatch)
    lifecycle = _FakeLifecycle(events)
    _stub_setup(monkeypatch, events, lifecycle, returncode)
    _stub_evidence(monkeypatch, events, lifecycle, missing)
    _stub_x11(monkeypatch, events, home)
    assert jetbrains_client.main() == 0
    return artifacts


@pytest.mark.parametrize("returncode", [0, 7])
def test_main_orchestrates_full_pinned_flow_and_preserves_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    events: list[str] = []
    artifacts = _orchestrate(tmp_path, monkeypatch, events, returncode=returncode)

    assert (artifacts / "intellij-chat-open.png").read_bytes() == b"png"
    assert (artifacts / "intellij.png").read_bytes() == b"png"
    assert (artifacts / "intellij-idea-tail.log").is_file()
    assert events.index("lifecycle-capture") < events.index("agreements")
    assert events.index("inactive-config") < events.index("lifecycle-capture")
    assert events.index("none") < events.index("focus")
    assert events.index("focus") < events.index("chat")
    assert events.index("editor-click") < events.index("prompt")
    assert events.count("focus") == 2
    assert events.index("configured-state") < events.index("initialized-state")
    assert events.index("initialized-state") < events.index("prompt")
    assert events[-1] == "cleanup"


def test_main_types_the_prompt_only_after_the_ides_own_session_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the order the hosted failure broke: readiness first, then the prompt.

    In run 43386790118 the prompt was typed while the AI Chat panel still showed
    the agent selection screen; the trace held ``session/new`` and never
    ``session/prompt``.
    """
    events: list[str] = []
    _orchestrate(tmp_path, monkeypatch, events)

    order = [
        f"log:{jetbrains_client._ACP_CHAT_CREATED_MARKER}",
        '"method":"session/new"',
        '"response_to":"session/new"',
        f"log:{jetbrains_client._ACP_SESSION_PREREQUISITE}",
        "intellij-chat-open.png",
        "prompt",
        '"method":"session/prompt"',
        '"response_to":"session/prompt"',
    ]
    positions = [events.index(step) for step in order]
    assert positions == sorted(positions)
    assert positions[2] < events.index("exactly-one") < positions[3]


@pytest.mark.parametrize(
    "missing",
    ['"method":"session/new"', '"response_to":"session/new"'],
)
def test_main_types_no_prompt_when_the_agent_session_is_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    """A session that never becomes ready ends the run before any prompt input."""
    events: list[str] = []
    with pytest.raises(RuntimeError, match=f"ACP trace never contained {missing}"):
        _orchestrate(tmp_path, monkeypatch, events, missing=missing)

    assert "prompt" not in events
    assert "intellij-chat-open.png" not in events
    assert events[-1] == "cleanup"


@pytest.mark.parametrize("proxy_json", ["{}", '["opencode", 1]', "[]"])
def test_main_rejects_non_string_or_empty_proxy_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proxy_json: str,
) -> None:
    required = {
        "SYNAPSE_JETBRAINS_BIN": str(tmp_path / "idea"),
        "SYNAPSE_JETBRAINS_PLUGINS": str(tmp_path / "plugins"),
        "SYNAPSE_EDITOR_E2E_PROJECT": str(tmp_path / "project"),
        "SYNAPSE_ACP_TRACE": str(tmp_path / "trace"),
        "SYNAPSE_EDITOR_E2E_PROMPT": "prompt",
        "SYNAPSE_ACP_PROXY_ARGV_JSON": proxy_json,
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="must contain non-empty string arguments"):
        jetbrains_client.main()


class _IdeaLogLifecycle(_FakeLifecycle):
    """Serve the IDE log a real wait reads, so each test decides what it holds."""

    def __init__(self, events: list[str], log_root: Path) -> None:
        super().__init__(events)
        self._log = log_root / "idea.log"

    def idea_contents(self) -> str:
        """Return the real log file's text."""
        return self._log.read_text(encoding="utf-8")


_CHAT_CREATED = jetbrains_client._ACP_CHAT_CREATED_MARKER
_SESSION_LOG = (
    jetbrains_client._ACP_SESSION_PREREQUISITE,
    *jetbrains_client._ACP_SESSION_COMPLETIONS,
)
_SESSION_NEW = '{"direction":"client_to_agent","id":2,"method":"session/new"}'
_SESSION_ANSWER = (
    '{"direction":"agent_to_client","error":false,"id":2,'
    '"response_to":"session/new","session_id_present":true}'
)


def _wait_for_session(
    tmp_path: Path,
    trace_lines: list[str],
    log_lines: list[str],
    events: list[str],
    seconds: float,
) -> None:
    """Write a real trace and IDE log, then run the readiness wait over them."""
    trace = tmp_path / "trace.jsonl"
    trace.write_text("".join(f"{line}\n" for line in trace_lines), encoding="utf-8")
    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "idea.log").write_text("".join(f"{line}\n" for line in log_lines), encoding="utf-8")
    jetbrains_client._wait_for_agent_session(
        trace,
        log_root,
        time.monotonic() + seconds,
        cast(subprocess.Popen[str], _FakeIdeaProcess(0)),
        cast(JetBrainsLifecycleGuard, _IdeaLogLifecycle(events, log_root)),
    )


@pytest.mark.parametrize(
    ("trace_lines", "log_lines", "not_seen"),
    [
        ([_SESSION_NEW], [_CHAT_CREATED, *_SESSION_LOG], '"response_to":"session/new"'),
        ([], [_CHAT_CREATED, *_SESSION_LOG], '"method":"session/new"'),
        ([_SESSION_NEW, _SESSION_ANSWER], [*_SESSION_LOG], _CHAT_CREATED),
        (
            [_SESSION_NEW, _SESSION_ANSWER],
            [_CHAT_CREATED, _SESSION_LOG[0], _SESSION_LOG[2]],
            "Starting ACP client session",
        ),
    ],
)
def test_agent_session_wait_names_the_signal_it_did_not_see(
    tmp_path: Path,
    trace_lines: list[str],
    log_lines: list[str],
    not_seen: str,
) -> None:
    """A readiness signal that never comes ends the wait with its own name."""
    with pytest.raises(RuntimeError, match=re.escape(not_seen)):
        _wait_for_session(tmp_path, trace_lines, log_lines, [], 0.3)


def test_agent_session_wait_returns_once_the_ide_and_the_agent_report_ready(
    tmp_path: Path,
) -> None:
    """The real trace and log signals end the wait, after the cardinality gates."""
    events: list[str] = []
    trace_lines = [_SESSION_NEW, _SESSION_ANSWER]
    _wait_for_session(tmp_path, trace_lines, [_CHAT_CREATED, *_SESSION_LOG], events, 5.0)

    assert events.index("at-most-one") < events.index("exactly-one")
