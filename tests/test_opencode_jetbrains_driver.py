# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real JetBrains ACP client orchestration tests
"""Verify the real IDEA/OpenCode ACP orchestration path."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from e2e.opencode_editors import jetbrains_client, jetbrains_x11_driver
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard


def test_selector_lifecycle_is_not_owned_by_the_client_orchestrator() -> None:
    """Pin selector state transitions to their focused production module."""
    root = Path(__file__).parent / "e2e" / "opencode_editors"
    client = (root / "jetbrains_client.py").read_text(encoding="utf-8")
    selector = (root / "jetbrains_selector.py").read_text(encoding="utf-8")

    for name in (
        "def is_agent_selector_popup(",
        "def visible_jetbrains_window_rectangles(",
        "def find_agent_selector_popup(",
        "def select_pinned_agent(",
    ):
        assert name not in client
        assert name in selector
    assert len(client.splitlines()) < 350


def test_show_ai_chat_uses_only_proven_current_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    focused: list[tuple[str, float]] = []
    actions: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda window, *, deadline: focused.append((window, deadline)),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *args, **_kwargs: actions.append(args),
    )

    jetbrains_client._show_ai_chat("123", deadline=7.0)

    assert focused == [("123", 7.0)]
    assert actions == [("key", "ctrl+alt+shift+j")]


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


@pytest.mark.parametrize("returncode", [0, 7])
def test_main_orchestrates_full_pinned_flow_and_preserves_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
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
        "SYNAPSE_EDITOR_E2E_ARTIFACT_DIR": str(artifacts),
        "XDG_DATA_HOME": str(data),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    events: list[str] = []
    lifecycle = _FakeLifecycle(events)
    monkeypatch.setattr(
        jetbrains_client,
        "write_acp_config",
        lambda *_args, **_kwargs: events.append("config"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "write_idea_profile",
        lambda *_args, **_kwargs: events.append("profile"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "idea_command",
        lambda *_args, **_kwargs: ["idea"],
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeIdeaProcess(returncode),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "complete_first_run_agreements",
        lambda _deadline: events.append("agreements"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "find_project_window",
        lambda _deadline: "123",
    )
    monkeypatch.setattr(
        jetbrains_client,
        "skip_islands_onboarding",
        lambda _deadline, _window: events.append("onboarding"),
    )
    monkeypatch.setattr(
        JetBrainsLifecycleGuard,
        "capture",
        lambda *_args, **_kwargs: lifecycle,
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_open_agent_selector",
        lambda *_args, **_kwargs: "selector",
    )

    def select_agent(
        _selector: str,
        _window: str,
        *,
        guard: Callable[[], object],
        capture_filtered_selector: Callable[[], None],
        **_kwargs: object,
    ) -> None:
        guard()
        capture_filtered_selector()
        events.append("selected")

    monkeypatch.setattr(jetbrains_client, "_select_pinned_agent", select_agent)

    def wait_log(
        log_root: Path,
        _markers: object,
        _deadline: float,
        _poll: Callable[[], int | None],
        **kwargs: object,
    ) -> None:
        (log_root / "idea.log").write_text(lifecycle.idea_contents(), encoding="utf-8")
        retry = kwargs.get("retry")
        if retry is not None:
            cast(Callable[[], object], retry)()
        guard = kwargs.get("guard")
        if guard is not None:
            cast(Callable[[], object], guard)()
        reader = kwargs.get("contents_reader")
        matcher = kwargs.get("matcher")
        if reader is not None and matcher is not None:
            contents = cast(Callable[[], str], reader)()
            assert cast(Callable[[str], bool], matcher)(contents)
        events.append("log")

    def wait_trace(
        _trace: Path,
        marker: str,
        _deadline: float,
        _process: object,
        *,
        guard: Callable[[], object] | None = None,
    ) -> None:
        if guard is not None:
            guard()
        events.append(marker)

    def screenshot(path: Path, **_kwargs: object) -> None:
        path.write_bytes(b"png")
        events.append(path.name)

    monkeypatch.setattr(jetbrains_client, "wait_for_idea_log", wait_log)
    monkeypatch.setattr(jetbrains_client, "wait_for_trace", wait_trace)
    monkeypatch.setattr(jetbrains_client, "capture_screenshot", screenshot)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda *_args, **_kwargs: events.append("chat"),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_args, **_kwargs: events.append("focus"),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_submit_chat_prompt",
        lambda *_args, **_kwargs: events.append("prompt"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "capture_evidence_and_terminate",
        lambda *_args, **_kwargs: events.append("cleanup"),
    )

    assert jetbrains_client.main() == 0
    assert (artifacts / "intellij-agent-selector.png").read_bytes() == b"png"
    assert (artifacts / "intellij.png").read_bytes() == b"png"
    assert (artifacts / "intellij-idea-tail.log").is_file()
    assert "selected" in events
    assert "prompt" in events
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
