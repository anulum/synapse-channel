# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native composer admission and process races
"""Refuse stale ownership and actual terminal changes without submitting input."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from fixtures.opencode.composer import native_composer
from synapse_channel.agent_tmux import inject_wake, start_session, status
from synapse_channel.opencode_composer import LAUNCH_ENV, PROFILE_ENV
from synapse_channel.opencode_composer_files import ComposerError, read_document, write_document
from synapse_channel.opencode_composer_probe import (
    inspect_editor_composer,
    profile_directory,
    qualify_startup_composer,
)


@dataclass
class _Controls:
    """Observe real tmux results and interleave actual owner-controlled changes."""

    before: Callable[[Sequence[str]], None] | None = None
    after: Callable[[Sequence[str]], None] | None = None
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute every requested command; hooks never replace its result."""
        assert text
        self.commands.append(tuple(args))
        if self.before is not None:
            self.before(args)
        result = subprocess.run(
            args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
        )
        if self.after is not None:
            self.after(args)
        return result


def _finished_response(root: Path) -> dict[str, object]:
    """Read the matching real callback response after its process exits."""
    nonce = read_document(root / "request.json")["nonce"]
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        document = read_document(root / "response.json")
        if document.get("nonce") == nonce:
            pid = document["callback_pid"]
            assert isinstance(pid, int)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return document
        time.sleep(0.01)
    raise AssertionError("the actual native callback did not finish")


def test_admission_metadata_refuses_before_editor_and_preserves_input(tmp_path: Path) -> None:
    """Unbound identities, shortcuts and nonempty startup receipts authorize no keys."""
    with native_composer(tmp_path) as native:
        draft = "owner π input remains private\n"
        native.paste(draft)
        root = profile_directory(native.config)
        binding = read_document(root / "binding.json")
        profile = read_document(root / "profile.json")
        faults: list[tuple[str, dict[str, object] | None]] = [
            ("binding.json", {**binding, "identity": "E2E/other"}),
            ("binding.json", {**binding, "session": "other-session"}),
            ("binding.json", {**binding, "shortcut": "C-a e"}),
            (
                "binding.json",
                {**binding, "byte_count": 3, "sha256": hashlib.sha256(b"abc").hexdigest()},
            ),
            ("profile.json", {**profile, "launch_nonce": True}),
            ("profile.json", None),
        ]
        for name, document in faults:
            request = (root / "request.json").read_bytes()
            if document is None:
                (root / name).unlink()
            else:
                write_document(root, name, document)
            controls = _Controls()
            with pytest.raises(ComposerError, match="native editor readback is unavailable"):
                inspect_editor_composer(native.config, native.screen(), runner=controls)
            assert not any(c[1] in {"send-keys", "paste-buffer"} for c in controls.commands)
            assert (root / "request.json").read_bytes() == request
            write_document(root, name, profile if name == "profile.json" else binding)
        assert native.readback().matches(draft)
        assert not native.provider.prompt_requests


@pytest.mark.parametrize("shell", ["inherited", "bash"])
@pytest.mark.parametrize("vendor", ["claude", "codex", "unregistered-provider"])
def test_provider_configuration_drift_cannot_admit_native_input(
    tmp_path: Path, shell: str, vendor: str
) -> None:
    """An existing native pane is not treated as a different configured vendor."""
    with native_composer(tmp_path) as native:
        if shell == "bash":
            native.tmux("kill-session", "-t", native.config.session)
            native.tmux("set-option", "-g", "default-shell", "/bin/bash")
            started = start_session(native.config)
            assert started.started and started.returncode == 0, started.detail
        native.paste("owner input during provider reconfiguration\n")
        root = profile_directory(native.config)
        request = (root / "request.json").read_bytes()
        original = status(native.config)
        assert original.binding_valid and original.agent_active
        stale = replace(native.config, agent_command=(vendor,))
        snapshot = status(stale)
        assert snapshot.binding_valid
        assert snapshot.pane_state == "unknown"
        assert not snapshot.compatibility_aligned
        assert snapshot.compatibility_detail in {
            "active provider pane was not verified",
            "provider pane readiness is unknown",
            "Codex update checks are not centrally managed for this session",
            "provider has no supported pane profile",
        }
        if shell == "bash":
            assert snapshot.agent_active
            assert snapshot.compatibility_detail == (
                "provider has no supported pane profile"
                if vendor == "unregistered-provider"
                else "Codex update checks are not centrally managed for this session"
                if vendor == "codex"
                else "provider pane readiness is unknown"
            )
        controls = _Controls()
        refused = inject_wake(stale, runner=controls)
        assert refused.returncode == 0 and not refused.injected
        assert (
            "provider has no fail-closed idle profile"
            if vendor == "unregistered-provider"
            else "requires an OpenCode pane"
        ) in refused.detail
        assert not any(c[1] in {"send-keys", "paste-buffer"} for c in controls.commands)
        assert (root / "request.json").read_bytes() == request
        assert native.readback().matches("owner input during provider reconfiguration\n")
        assert not native.provider.prompt_requests


@pytest.mark.parametrize("binding", ["wrong-target", "missing-owner", "changed-owner"])
def test_startup_probe_requires_the_actual_unpublished_target_and_owner(
    tmp_path: Path, binding: str
) -> None:
    """A matching private profile alone cannot authorize a startup editor transaction."""
    with native_composer(tmp_path) as native:
        native.paste("existing input must remain untouched\n")
        root = profile_directory(native.config)
        profile = read_document(root / "profile.json")
        request = (root / "request.json").read_bytes()
        nonce = profile["launch_nonce"]
        assert isinstance(nonce, str)
        target = (
            "unrelated-target" if binding == "wrong-target" else "synapse-native-start-" + nonce
        )
        native.tmux("rename-session", "-t", native.config.session, target)
        if binding == "missing-owner":
            native.tmux("set-environment", "-u", "-t", target, LAUNCH_ENV)
        elif binding == "changed-owner":
            native.tmux("set-environment", "-t", target, LAUNCH_ENV, "f" * 32)
        controls = _Controls()
        expected = (
            "native startup target is invalid"
            if binding == "wrong-target"
            else "native startup ownership is unavailable"
        )
        with pytest.raises(ComposerError, match=expected):
            qualify_startup_composer(
                native.config,
                target,
                native.tmux("capture-pane", "-t", target, "-p", "-J"),
                runner=controls,
            )
        assert not any(c[1] in {"send-keys", "paste-buffer"} for c in controls.commands)
        assert (root / "request.json").read_bytes() == request
        native.tmux("rename-session", "-t", target, native.config.session)
        native.tmux("set-environment", "-t", native.config.session, LAUNCH_ENV, nonce)
        assert native.readback().matches("existing input must remain untouched\n")
        assert not native.provider.prompt_requests


def test_profile_environment_changes_refuse_before_editor_keys(tmp_path: Path) -> None:
    """Removing or redirecting the real launch marker cannot reuse its prior receipt."""
    with native_composer(tmp_path) as native:
        native.paste("draft before a changed launch environment\n")
        root = profile_directory(native.config)
        request = (root / "request.json").read_bytes()
        for marker in (None, str(tmp_path / "another-profile")):
            if marker is None:
                native.tmux("set-environment", "-u", "-t", native.config.session, PROFILE_ENV)
            else:
                native.tmux("set-environment", "-t", native.config.session, PROFILE_ENV, marker)
            controls = _Controls()
            with pytest.raises(ComposerError, match="native editor readback is unavailable"):
                inspect_editor_composer(native.config, native.screen(), runner=controls)
            assert not any(c[1] in {"send-keys", "paste-buffer"} for c in controls.commands)
            assert (root / "request.json").read_bytes() == request
            native.tmux("set-environment", "-t", native.config.session, PROFILE_ENV, str(root))
        assert native.readback().matches("draft before a changed launch environment\n")
        assert not native.provider.prompt_requests


def test_profile_change_after_pane_observation_refuses_before_editor(tmp_path: Path) -> None:
    """A real metadata replacement between admission checks cannot authorize a probe."""
    with native_composer(tmp_path) as native:
        root = profile_directory(native.config)
        original = read_document(root / "profile.json")
        request = (root / "request.json").read_bytes()

        def replace_profile(args: Sequence[str]) -> None:
            """Change the actual private document after the real PID observation."""
            if args[1] == "display-message":
                write_document(root, "profile.json", {**original, "launch_nonce": True})

        controls = _Controls(after=replace_profile)
        with pytest.raises(ComposerError, match="native editor readback is unavailable") as failure:
            inspect_editor_composer(native.config, native.screen(), runner=controls)
        assert str(failure.value.__cause__) == "native launch proof is unavailable"
        assert not any(c[1] in {"send-keys", "paste-buffer"} for c in controls.commands)
        assert (root / "request.json").read_bytes() == request
        write_document(root, "profile.json", original)
        assert native.readback().matches(None)
        assert not native.provider.prompt_requests


def test_response_from_another_live_native_client_cannot_replace_the_bound_process(
    tmp_path: Path,
) -> None:
    """A valid receipt for another real client cannot authorize this client's input."""
    with (
        native_composer(tmp_path / "first") as first,
        native_composer(tmp_path / "second") as second,
    ):
        first.paste("first client's draft π\n")
        other = second.readback()
        root = profile_directory(first.config)

        def replace_parent(args: Sequence[str]) -> None:
            """Retain the real callback proof but substitute the other live native parent."""
            if args[1] == "send-keys":
                response = _finished_response(root)
                write_document(
                    root,
                    "response.json",
                    {
                        **response,
                        "native_pid": other.native_pid,
                        "native_start": other.native_start,
                    },
                )

        with pytest.raises(ComposerError, match="native editor readback is unavailable") as failure:
            inspect_editor_composer(
                first.config, first.screen(), runner=_Controls(after=replace_parent)
            )
        assert str(failure.value.__cause__) == "qualified native keymap process changed"
        assert first.readback().matches("first client's draft π\n")
        assert second.readback().matches(None)
        assert not first.provider.prompt_requests
        assert not second.provider.prompt_requests


@pytest.mark.parametrize("phase", ["pane-id", "editor-shortcut", "post-callback-mode"])
def test_probe_refuses_actual_terminal_changes_without_submitting(
    tmp_path: Path, phase: str
) -> None:
    """Real pane loss or a command menu during readback never submits a prompt."""
    with native_composer(tmp_path) as native:
        native.paste("typed input before a control race\n")
        root = profile_directory(native.config)
        request = (root / "request.json").read_bytes()
        response = (root / "response.json").read_bytes()

        def change_terminal(args: Sequence[str]) -> None:
            """Interleave real session removal or an explicit command-menu action."""
            if (phase == "pane-id" and args[1] == "display-message") or (
                phase == "editor-shortcut" and args[1] == "send-keys"
            ):
                native.tmux("kill-session", "-t", native.config.session)
            elif phase == "post-callback-mode" and args[1] == "capture-pane":
                native.keys("C-p")
                native.wait(lambda: "Commands" in native.screen())

        with pytest.raises(ComposerError, match="native editor readback is unavailable") as failure:
            inspect_editor_composer(
                native.config, native.screen(), runner=_Controls(before=change_terminal)
            )
        assert (
            str(failure.value.__cause__)
            == {
                "pane-id": "native pane process is unavailable",
                "editor-shortcut": "native editor shortcut was not accepted",
                "post-callback-mode": "native input mode changed during readback",
            }[phase]
        )
        assert not native.provider.prompt_requests
        if phase == "pane-id":
            assert (root / "request.json").read_bytes() == request
        if phase != "post-callback-mode":
            assert (root / "response.json").read_bytes() == response
            assert not status(native.config).session_exists
        else:
            native.keys("Escape")
            native.wait(lambda: "BUILD" in native.screen() and "Commands" not in native.screen())
            assert native.readback().matches("typed input before a control race\n")
