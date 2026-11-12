# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider pane classification contracts
"""Preserve provider state and staged-hint decisions through public classifiers."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from synapse_channel.agent_pane_state import (
    bound_pane_state,
    bound_screen_safety,
    bound_staged_wake_was_consumed,
    pane_state,
    provider_compatibility,
    screen_is_safe_for_submit,
    staged_wake_was_consumed,
)
from synapse_channel.agent_tmux import AgentTmuxConfig, build_wake_prompt, status
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.opencode_composer import (
    PROFILE_ENV,
    PROTOCOL,
    SUPPORTED_VERSION,
    process_stamp,
)
from synapse_channel.opencode_composer_files import ComposerError, read_document, write_document
from synapse_channel.opencode_composer_probe import profile_directory
from synapse_channel.opencode_composer_state import set_stage_verified, stage_is_verified

_HINT = build_wake_prompt("E2E/pane-classification")
_UPDATE = "Update available! 0.1 -> 0.2\nSkip until next version\nPress enter to continue\n› "


@pytest.mark.parametrize(
    ("provider", "capture", "expected"),
    [
        ("codex", "› ", "idle"),
        ("claude", "❯ ", "idle"),
        ("kimi", "> Ask", "idle"),
        ("grok", "› Type", "idle"),
        ("grok", "  │ ❯" + (" " * 8) + "│", "idle"),
        ("grok", "  ╰─ Grok 4.7 (high) · always-approve ─╯\n  │ ❯" + (" " * 4) + "│", "idle"),
        ("grok", "Approve this tool?\n  │ ❯ ", "blocked"),
        ("gemini", "Ask Gemini", "idle"),
        ("opencode", "Ask anything...", "idle"),
        ("codex", _UPDATE, "update-required"),
        ("codex", "thinking\n› ", "blocked"),
        ("claude", "Allow this tool?\n❯ ", "blocked"),
        ("codex", "1. Continue\n› ", "blocked"),
        ("codex", "session banner", "unknown"),
    ],
)
def test_provider_state_preserves_precedence(provider: str, capture: str, expected: str) -> None:
    """Busy, permission and update observations retain precedence over idle."""
    assert pane_state(provider, capture) == expected


@pytest.mark.parametrize(
    ("provider", "capture", "required", "expected"),
    [
        ("codex", _UPDATE, None, False),
        ("claude", "permission\n❯ ", _HINT, False),
        ("codex", "session banner", None, False),
        ("opencode", "BUILD model ctrl+p cmd", None, False),
        ("opencode", "BUILD model ctrl+p cmd", _HINT, False),
        ("opencode", _HINT + "\nBUILD model ctrl+o cmd", _HINT, False),
        ("opencode", _HINT + "\nBUILD model ctrl+p cmd", _HINT, True),
        ("codex", "› ", None, True),
        ("codex", "› owner's draft", _HINT, False),
        ("codex", "› " + _HINT, _HINT, True),
        ("codex", "› " + _HINT.replace(" ", "\n"), _HINT, True),
        ("grok", "> " + _HINT, _HINT, True),
        ("grok", "[rx] waiting for messages\n> " + _HINT, _HINT, True),
        ("grok", "  │ ❯ " + _HINT + " │", _HINT, True),
        (
            "grok",
            "  ╰─ Grok 4.7 (high) · always-approve ─╯\n  │ ❯ " + _HINT + " │",
            _HINT,
            True,
        ),
        ("grok", "> owner's draft", _HINT, False),
        ("grok", "thinking\n> " + _HINT, _HINT, False),
        ("grok", "Approve this tool?\n  │ ❯ " + _HINT + " │", _HINT, False),
        ("grok", "scrollback " + _HINT, _HINT, False),
        ("grok", "     ❯ earlier turn\n  │ ❯" + (" " * 4) + "│", _HINT, False),
        # An earlier wake is still on the screen as a past turn, and the owner is
        # typing: Enter would submit the owner's draft, so the wake stays queued.
        ("grok", "     ❯ " + _HINT + "\n  │ ❯ owner's draft │", _HINT, False),
        ("grok", "> " + _HINT + "\n> owner's draft", _HINT, False),
        # Text typed after the pasted hint is a draft too.
        ("grok", "  │ ❯ " + _HINT + " and more │", _HINT, False),
        # Rows below the composer that are not prompt rows do not block the wake.
        ("grok", "  │ ❯ " + _HINT + " │\n  ╰─ Grok 4.7 (high) · always-approve ─╯", _HINT, True),
    ],
)
def test_submit_readiness_preserves_exact_staged_hint(
    provider: str, capture: str, required: str | None, expected: bool
) -> None:
    """Readiness keeps payload-free reasons and requires the entire hint."""
    ready, reason = screen_is_safe_for_submit(provider, capture, required_text=required)
    assert ready is expected
    assert reason
    assert _HINT not in reason
    assert "owner's draft" not in reason


@pytest.mark.parametrize(
    ("provider", "capture", "expected"),
    [
        ("codex", "› ", True),
        ("codex", "› " + _HINT, False),
        ("codex", "› " + _HINT + "\n› ", True),
        ("claude", "❯ " + _HINT + "\n❯ ", True),
        ("opencode", _HINT + "\nBUILD model ctrl+p cmd", False),
        ("opencode", "› " + _HINT + "\nBUILD esc interrupt ctrl+p cmd", True),
        ("opencode", "draft " + _HINT + "\nBUILD esc interrupt ctrl+p cmd", False),
        ("opencode", "› " + _HINT + "\nBUILD model ctrl+p cmd", False),
        ("opencode", "› " + _HINT + "\nAsk anything...", True),
        (
            "opencode",
            _HINT + "\nAsk anything...\n" + _HINT + "\nBUILD model ctrl+p cmd",
            False,
        ),
    ],
)
def test_staged_hint_observation_uses_last_rendered_occurrence(
    provider: str, capture: str, expected: bool
) -> None:
    """A previous idle marker cannot settle a newer still-staged hint."""
    assert staged_wake_was_consumed(provider, capture, _HINT) is expected


def test_unsupported_provider_keeps_existing_lookup_error() -> None:
    """An unsupported provider is not silently assigned a supported profile."""
    with pytest.raises(KeyError, match="unsupported"):
        pane_state("unsupported", "session banner")
    with pytest.raises(KeyError, match="unsupported"):
        screen_is_safe_for_submit("unsupported", "session banner")
    with pytest.raises(KeyError, match="unsupported"):
        staged_wake_was_consumed("unsupported", _HINT, _HINT)


_FOOTER = "BUILD model ctrl+p cmd\n"


def _digest(text: str) -> tuple[int, str]:
    encoded = text.encode()
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _rename_sleeper() -> subprocess.Popen[bytes]:
    """Start a sleeper whose kernel name is the native client."""
    program = (
        "import ctypes, time\n"
        "assert ctypes.CDLL(None).prctl(15, b'opencode', 0, 0, 0) == 0\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", program])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            _parent, _started, name = process_stamp(child.pid)
        except ComposerError:
            time.sleep(0.02)
            continue
        if name == "opencode":
            return child
        time.sleep(0.02)
    child.kill()
    child.wait(timeout=5)
    raise AssertionError("kernel name did not become opencode")


class _ReadbackRunner:
    """Answer the tmux commands the native reader issues, and record its reply."""

    def __init__(
        self, root: Path, pid: int, started: str, text: str, *, fail: bool = False
    ) -> None:
        self.root = root
        self.pid = pid
        self.started = started
        self.text = text
        self.fail = fail

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, env
        command = args[1] if len(args) > 1 else ""
        if self.fail and command == "send-keys":
            return subprocess.CompletedProcess(list(args), 1, "", "refused")
        if command == "show-environment" and PROFILE_ENV in args:
            return subprocess.CompletedProcess(list(args), 0, f"{PROFILE_ENV}={self.root}\n", "")
        if command == "show-environment":
            return subprocess.CompletedProcess(
                list(args), 0, "SYN_PROJECT=E2E\nSYN_IDENTITY=E2E/readback\n", ""
            )
        if command == "display-message" and "#{pane_pid}" in args:
            return subprocess.CompletedProcess(list(args), 0, f"{self.pid}\n", "")
        if command == "display-message":
            return subprocess.CompletedProcess(list(args), 0, "opencode\topencode --mini\n", "")
        if command == "has-session":
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if command == "send-keys":
            request = read_document(self.root / "request.json")
            length, digest = _digest(self.text)
            write_document(
                self.root,
                "response.json",
                {
                    "protocol": PROTOCOL,
                    "nonce": request["nonce"],
                    "byte_count": length,
                    "sha256": digest,
                    "native_pid": self.pid,
                    "native_start": self.started,
                    "callback_pid": self.pid + 1_000_000,
                    "callback_start": "done",
                },
            )
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if command == "capture-pane":
            return subprocess.CompletedProcess(list(args), 0, _FOOTER, "")
        return subprocess.CompletedProcess(list(args), 1, "", "unexpected")


def _qualified(tmp_path: Path) -> tuple[AgentTmuxConfig, subprocess.Popen[bytes], Path, str]:
    child = _rename_sleeper()
    _parent, started, name = process_stamp(child.pid)
    assert name == "opencode"
    config = AgentTmuxConfig(
        "E2E/readback",
        "synapse-readback",
        tmp_path,
        ("opencode", "--mini"),
        registry_dir=tmp_path / "registry",
        opencode_editor_readback=True,
    )
    root = profile_directory(config)
    ensure_private_dir(root, parents=True, purpose="native composer profile")
    nonce = "ab" * 16
    write_document(
        root,
        "profile.json",
        {
            "protocol": PROTOCOL,
            "identity": config.identity,
            "session": config.session,
            "version": SUPPORTED_VERSION,
            "launch_nonce": nonce,
        },
    )
    length, digest = _digest("")
    write_document(
        root,
        "binding.json",
        {
            "protocol": PROTOCOL,
            "nonce": nonce,
            "identity": config.identity,
            "session": config.session,
            "shortcut": "C-x e",
            "byte_count": length,
            "sha256": digest,
            "native_pid": child.pid,
            "native_start": started,
        },
    )
    return config, child, root, started


def test_native_readback_classifies_the_live_composer(tmp_path: Path) -> None:
    """Readback admits only the verified draft and names every other native result."""
    legacy = AgentTmuxConfig("E2E/readback", "synapse-readback", tmp_path, ("opencode", "--mini"))
    ready, reason = bound_screen_safety(legacy, "opencode", "Ask anything...\n", runner=None)
    assert ready is True and reason == "opencode idle composer verified"
    assert bound_pane_state(legacy, "opencode", "Ask anything...\n", runner=None) == "idle"
    assert bound_staged_wake_was_consumed(
        legacy, "opencode", "Ask anything...\n", _HINT, runner=None
    )
    refused, why = bound_screen_safety(
        AgentTmuxConfig(
            "E2E/readback",
            "synapse-readback",
            tmp_path,
            ("codex",),
            registry_dir=tmp_path / "registry",
            opencode_editor_readback=True,
        ),
        "codex",
        _FOOTER,
        runner=None,
    )
    assert refused is False and why == "native input readback requires an OpenCode pane"
    missing = AgentTmuxConfig(
        "E2E/readback",
        "synapse-readback",
        tmp_path,
        ("opencode", "--mini"),
        registry_dir=tmp_path / "absent",
        opencode_editor_readback=True,
    )
    unavailable, detail = bound_screen_safety(missing, "opencode", _FOOTER, runner=_FailRunner())
    assert unavailable is False and detail == "native OpenCode input readback is unavailable"
    assert bound_pane_state(missing, "codex", _FOOTER, runner=_FailRunner()) == "unknown"
    assert bound_pane_state(missing, "opencode", _FOOTER, runner=_FailRunner()) == (
        "readback-unavailable"
    )
    observed = status(missing, runner=_FailRunner())
    assert observed.pane_state == "readback-unavailable"
    assert observed.compatibility_detail == (
        "native editor profile is not bound to this running client"
    )

    config, child, root, started = _qualified(tmp_path)
    try:
        other = _ReadbackRunner(root, child.pid, started, "another draft")
        rejected, reject_reason = bound_screen_safety(
            config, "opencode", _FOOTER, runner=other, required_text=_HINT
        )
        assert rejected is False and reject_reason == "native OpenCode input contains another draft"
        verified = _ReadbackRunner(root, child.pid, started, _HINT)
        admitted, admit_reason = bound_screen_safety(
            config, "opencode", _FOOTER, runner=verified, required_text=_HINT
        )
        assert admitted is True and admit_reason == "native OpenCode input verified"
        assert stage_is_verified(config) is True
        empty = _ReadbackRunner(root, child.pid, started, "")
        empty_ready, empty_reason = bound_screen_safety(
            config, "opencode", _FOOTER, runner=empty, required_text=None
        )
        assert empty_ready is True and empty_reason == "native OpenCode input verified"
        settled = "› " + _HINT + "\nAsk anything...\n"
        assert bound_staged_wake_was_consumed(config, "opencode", settled, _HINT, runner=empty)
        holding = _HINT + "\n" + _FOOTER
        assert bound_staged_wake_was_consumed(config, "opencode", holding, _HINT, runner=empty)
        occupied = _ReadbackRunner(root, child.pid, started, _HINT)
        assert (
            bound_staged_wake_was_consumed(config, "opencode", holding, _HINT, runner=occupied)
            is False
        )
        failed = _ReadbackRunner(root, child.pid, started, "", fail=True)
        assert (
            bound_staged_wake_was_consumed(config, "opencode", holding, _HINT, runner=failed)
            is False
        )
        set_stage_verified(config, False)
        assert bound_staged_wake_was_consumed(config, "opencode", _FOOTER, _HINT, runner=empty) is (
            False
        )
        assert bound_pane_state(config, "opencode", _FOOTER, runner=empty) == "readback-required"
        assert bound_pane_state(config, "opencode", "session banner\n", runner=empty) == "blocked"
    finally:
        child.kill()
        child.wait(timeout=5)


class _FailRunner:
    """Report a live session whose native profile was never published."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, env
        command = args[1] if len(args) > 1 else ""
        if command == "has-session":
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if command == "show-environment" and PROFILE_ENV not in args:
            return subprocess.CompletedProcess(
                list(args), 0, "SYN_PROJECT=E2E\nSYN_IDENTITY=E2E/readback\n", ""
            )
        if command == "display-message":
            return subprocess.CompletedProcess(list(args), 0, "opencode\topencode --mini\n", "")
        if command == "capture-pane":
            return subprocess.CompletedProcess(list(args), 0, _FOOTER, "")
        return subprocess.CompletedProcess(list(args), 1, "", "unbound")


def test_compatibility_names_each_readback_and_update_state() -> None:
    """Launch compatibility stays distinct from permission to submit a wake."""
    update_aligned, update_reason = provider_compatibility(
        "codex", "update-required", active=True, codex_policy_aligned=True
    )
    assert update_aligned is False
    assert update_reason == "provider update chooser blocks automatic wake delivery"
    required_aligned, required_reason = provider_compatibility(
        "opencode", "readback-required", active=True, codex_policy_aligned=True
    )
    assert required_aligned is True
    assert required_reason == "native input readback is required before wake submission"
    inactive, inactive_reason = provider_compatibility(
        None, "readback-unavailable", active=False, codex_policy_aligned=False
    )
    assert inactive is False and inactive_reason == "active provider pane was not verified"
    aligned, aligned_reason = provider_compatibility(
        "codex", "idle", active=True, codex_policy_aligned=True
    )
    assert aligned is True and aligned_reason == "provider launch and pane profile are aligned"
    unknown, unknown_reason = provider_compatibility(
        "codex", "unknown", active=True, codex_policy_aligned=True
    )
    assert unknown is False and unknown_reason == "provider pane readiness is unknown"
    unnamed, unnamed_reason = provider_compatibility(
        None, "idle", active=True, codex_policy_aligned=True
    )
    assert unnamed is False and unnamed_reason == "provider has no supported pane profile"
    unmanaged, unmanaged_reason = provider_compatibility(
        "codex", "blocked", active=True, codex_policy_aligned=False
    )
    assert unmanaged is False
    assert unmanaged_reason == "Codex update checks are not centrally managed for this session"
