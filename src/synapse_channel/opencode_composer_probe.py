# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in native OpenCode composer probes
"""Read the actual configured composer before admitting a managed wake."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.opencode_composer import (
    LAUNCH_ENV,
    PROBE_SECONDS,
    PROFILE_ENV,
    PROTOCOL,
    SUPPORTED_VERSION,
    process_stamp,
    require_native_process,
)
from synapse_channel.opencode_composer_files import (
    DRAFT_LIMIT,
    ComposerError,
    probe_lock,
    read_document,
    write_document,
    write_private_bytes,
)
from synapse_channel.opencode_composer_state import (
    native_wake_transaction as native_wake_transaction,
)
from synapse_channel.opencode_composer_state import (
    profile_directory as profile_directory,
)
from synapse_channel.opencode_composer_state import (
    set_stage_verified as set_stage_verified,
)
from synapse_channel.opencode_composer_state import (
    stage_is_verified as stage_is_verified,
)
from synapse_channel.waker_commands import run_waker_command

if TYPE_CHECKING:
    from synapse_channel.agent_tmux import AgentTmuxConfig, CommandRunner


@dataclass(frozen=True)
class ComposerSnapshot:
    """Content-free native input and its observed process identity.

    Parameters
    ----------
    byte_count : int
        Exact native export length in bytes.
    sha256 : str
        Hexadecimal digest of those bytes.
    native_pid : int
        OpenCode process that invoked the callback.
    native_start : str
        Start timestamp observed for that process.
    """

    byte_count: int
    sha256: str
    native_pid: int
    native_start: str

    def matches(self, text: str | None) -> bool:
        """Compare exact UTF-8 input, treating None as an actually empty draft.

        Parameters
        ----------
        text : str or None
            Expected native text, preserving whitespace and trailing newlines.

        Returns
        -------
        bool
            Whether both byte count and digest match the expected UTF-8 bytes.
        """
        expected = (text or "").encode()
        return (
            self.byte_count == len(expected) and self.sha256 == hashlib.sha256(expected).hexdigest()
        )


def editor_environment(config: AgentTmuxConfig) -> tuple[str, ...]:
    """Prepare a pinned native callback only for an explicitly opted-in new client.

    Parameters
    ----------
    config : AgentTmuxConfig
        New POSIX OpenCode mini client with the supported executable version.

    Returns
    -------
    tuple of str
        Profile/launch markers, EDITOR, VISUAL and TMPDIR environment assignments.
        The editor launcher imports this installed package.

    Raises
    ------
    ComposerError
        The command, version, platform or editor path cannot be represented.
    OSError, ValueError
        Private profile metadata cannot be created or validated.

    Notes
    -----
    Call only while owning the launch transaction. This replaces the profile's
    nonce and installs a rejecting editor callback for the new client.
    """
    if os.name != "posix" or not config.agent_command:
        raise ComposerError("native editor readback requires a POSIX OpenCode client")
    binary = shutil.which(config.agent_command[0])
    if binary is None or Path(binary).name != "opencode" or "--mini" not in config.agent_command:
        raise ComposerError("native editor readback requires an explicit opencode --mini command")
    version = run_waker_command(
        [binary, "--version"],
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    if version.returncode or version.stdout.strip() != SUPPORTED_VERSION:
        raise ComposerError(f"native editor readback requires OpenCode {SUPPORTED_VERSION}")
    root = profile_directory(config)
    package = Path(__file__).parent.parent
    if any(any(char.isspace() for char in str(p)) for p in (root, Path(sys.executable), package)):
        raise ComposerError(
            "the native editor command cannot represent paths containing whitespace"
        )
    ensure_private_dir(root, purpose="native composer profile")
    temporary = ensure_private_dir(root / "tmp", purpose="native composer temporary directory")
    write_document(
        root,
        "profile.json",
        {
            "protocol": PROTOCOL,
            "identity": config.identity,
            "session": config.session,
            "version": SUPPORTED_VERSION,
            "launch_nonce": uuid.uuid4().hex,
        },
    )
    launcher = (
        "import sys\n"
        f"sys.path.insert(0, {str(package)!r})\n"
        "from synapse_channel.opencode_composer import main\n"
        "raise SystemExit(main())\n"
    )
    write_private_bytes(root, "editor.py", launcher.encode())
    editor = f"{sys.executable} {root / 'editor.py'} --profile {root}"
    nonce = read_document(root / "profile.json")["launch_nonce"]
    return (
        f"{PROFILE_ENV}={root}",
        f"{LAUNCH_ENV}={nonce}",
        f"EDITOR={editor}",
        f"VISUAL={editor}",
        f"TMPDIR={temporary}",
    )


def footer_is_probeable(screen: str) -> bool:
    """Require the native normal-input footer; busy, shell and menus refuse.

    Parameters
    ----------
    screen : str
        Bounded current pane capture.

    Returns
    -------
    bool
        Whether the last nonempty line is the supported BUILD input footer
        without a busy, approval or transitional marker.
    """
    lines = screen.rstrip().splitlines()
    if not lines or not re.fullmatch(r"\s*BUILD\b.*\bctrl\+p cmd\s*", lines[-1]):
        return False
    return (
        re.search(
            r"(?i)\b(?:interrupt|queued|sending|starting|loading|permission|confirm|approval|retry)\b",
            lines[-1],
        )
        is None
    )


def _profile_marker_is_bound(
    config: AgentTmuxConfig, runner: CommandRunner, *, target: str | None = None
) -> bool:
    """Verify the launch-time callback marker without invoking the editor."""
    root = profile_directory(config)
    result = runner(
        [config.tmux_bin, "show-environment", "-t", target or config.session, PROFILE_ENV],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or result.stdout.strip() != f"{PROFILE_ENV}={root}":
        return False
    try:
        document = read_document(root / "profile.json")
    except (OSError, ValueError):
        return False
    nonce = document.get("launch_nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        return False
    return document == {
        "protocol": PROTOCOL,
        "identity": config.identity,
        "session": config.session,
        "version": SUPPORTED_VERSION,
        "launch_nonce": nonce,
    }


def profile_is_bound(config: AgentTmuxConfig, runner: CommandRunner) -> bool:
    """Check the qualified live binding without invoking the native editor.

    Parameters
    ----------
    config : AgentTmuxConfig
        Exact opted-in identity and session.
    runner : CommandRunner
        Real tmux command boundary; injected runners own their deadlines.

    Returns
    -------
    bool
        Whether metadata and the same live qualified process are bound.
        False covers unavailable or invalid metadata; no editor keys are sent.
    """
    try:
        if not _profile_marker_is_bound(config, runner):
            return False
        root = profile_directory(config)
        pane_pid = _pane_pid(config, config.session, runner)
        return _admit_probe(config, root, config.session, pane_pid, runner) is not None
    except Exception:
        # Status is observational: failed metadata probes never authorize input.
        return False


def _pid(value: object) -> int:
    """Validate a process identifier without accepting booleans."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 1:
        raise ComposerError("native readback process id is invalid")
    return value


def _pane_pid(config: AgentTmuxConfig, target: str, runner: CommandRunner) -> int:
    """Read the current pane process without submitting terminal input."""
    pane = runner(
        [config.tmux_bin, "display-message", "-p", "-t", target, "#{pane_pid}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if pane.returncode or not pane.stdout.strip().isdecimal():
        raise ComposerError("native pane process is unavailable")
    return _pid(int(pane.stdout.strip()))


def _receipt(document: dict[str, object], nonce: str) -> ComposerSnapshot:
    """Validate one exact correlated, bounded and content-free response."""
    length, digest, started = (
        document.get("byte_count"),
        document.get("sha256"),
        document.get("native_start"),
    )
    if (
        document.get("protocol") != PROTOCOL
        or document.get("nonce") != nonce
        or not isinstance(length, int)
        or isinstance(length, bool)
        or not 0 <= length <= DRAFT_LIMIT
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not isinstance(started, str)
        or not started
        or len(started) > 100
    ):
        raise ComposerError("native readback response is invalid")
    return ComposerSnapshot(length, digest, _pid(document.get("native_pid")), started)


def _callback_finished(document: dict[str, object]) -> bool:
    """Observe callback exit, never signalling or reaping an owner process."""
    pid = _pid(document.get("callback_pid"))
    started = document.get("callback_start")
    if not isinstance(started, str) or not started:
        raise ComposerError("native readback callback start is absent")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        return process_stamp(pid)[1] != started
    except ComposerError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        raise


def _read_response(root: Path, nonce: str, deadline: float) -> ComposerSnapshot:
    """Wait for a matching response and callback exit within one finite deadline."""
    while time.monotonic() < deadline:
        try:
            document = read_document(root / "response.json")
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        if document.get("nonce") == nonce and _callback_finished(document):
            snapshot = _receipt(document, nonce)
            _, started, name = process_stamp(snapshot.native_pid)
            if started != snapshot.native_start or name != "opencode":
                raise ComposerError("native readback parent changed")
            return snapshot
        time.sleep(0.05)
    raise ComposerError("native editor readback was not acknowledged before its deadline")


def _admit_probe(
    config: AgentTmuxConfig,
    root: Path,
    target: str,
    pane_pid: int,
    runner: CommandRunner,
) -> ComposerSnapshot | None:
    """Admit only the qualified live process or our unpublished startup target."""
    qualified: ComposerSnapshot | None = None
    profile = read_document(root / "profile.json")
    launch_nonce = profile.get("launch_nonce")
    if not isinstance(launch_nonce, str):
        raise ComposerError("native launch proof is unavailable")
    if target == config.session:
        binding = read_document(root / "binding.json")
        if (
            binding.get("identity") != config.identity
            or binding.get("session") != config.session
            or binding.get("shortcut") != "C-x e"
        ):
            raise ComposerError("native keymap binding is invalid")
        qualified = _receipt(binding, launch_nonce)
        if not qualified.matches(None):
            raise ComposerError("native keymap was not qualified on empty input")
        require_native_process(qualified.native_pid, qualified.native_start, pane_pid)
    else:
        if target != "synapse-native-start-" + launch_nonce:
            raise ComposerError("native startup target is invalid")
        launch = runner(
            [config.tmux_bin, "show-environment", "-t", target, LAUNCH_ENV],
            capture_output=True,
            text=True,
            check=False,
        )
        if launch.returncode or launch.stdout.strip() != f"{LAUNCH_ENV}={launch_nonce}":
            raise ComposerError("native startup ownership is unavailable")
    return qualified


def _inspect_editor_composer(
    config: AgentTmuxConfig,
    screen: str,
    *,
    runner: CommandRunner,
    startup_target: str | None = None,
) -> ComposerSnapshot:
    """Read a configured live composer; never paste, submit or alter its text."""
    target = startup_target or config.session
    if not footer_is_probeable(screen) or not _profile_marker_is_bound(
        config, runner, target=target
    ):
        raise ComposerError("native editor profile or current input mode is unavailable")
    root = profile_directory(config)
    with probe_lock(root):
        pane_pid = _pane_pid(config, target, runner)
        qualified = _admit_probe(config, root, target, pane_pid, runner)
        nonce = uuid.uuid4().hex
        deadline = time.monotonic() + PROBE_SECONDS
        write_document(
            root,
            "request.json",
            {
                "protocol": PROTOCOL,
                "identity": config.identity,
                "session": config.session,
                "nonce": nonce,
                "pane_pid": pane_pid,
                "deadline_ns": time.monotonic_ns() + int(PROBE_SECONDS * 1_000_000_000),
            },
        )
        result = runner(
            [config.tmux_bin, "send-keys", "-t", target, "C-x", "e"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ComposerError("native editor shortcut was not accepted")
        snapshot = _read_response(root, nonce, deadline)
        if qualified is not None and (
            snapshot.native_pid != qualified.native_pid
            or snapshot.native_start != qualified.native_start
        ):
            raise ComposerError("qualified native keymap process changed")
        time.sleep(0.05)
        current = runner(
            [config.tmux_bin, "capture-pane", "-t", target, "-p", "-J"],
            capture_output=True,
            text=True,
            check=False,
        )
        if (
            current.returncode
            or len(current.stdout) > DRAFT_LIMIT
            or not footer_is_probeable(current.stdout)
        ):
            raise ComposerError("native input mode changed during readback")
        return snapshot


def qualify_startup_composer(
    config: AgentTmuxConfig,
    target: str,
    screen: str,
    *,
    runner: CommandRunner,
) -> None:
    """Seal the actual shortcut only inside an unpublished new native client.

    Parameters
    ----------
    config : AgentTmuxConfig
        Desired final identity and session.
    target : str
        Unpublished session name bound to the profile launch nonce.
    screen : str
        Current capture of that new empty client.
    runner : CommandRunner
        Real tmux controls with bounded execution.

    Raises
    ------
    ComposerError
        The startup ownership, actual shortcut, process or empty input is not
        proved. The caller must retire only its own unpublished session.

    Notes
    -----
    The caller owns the launch lock and publishes this same process only after
    qualification. Existing owner sessions never enter this path.
    """
    snapshot = _inspect_editor_composer(config, screen, runner=runner, startup_target=target)
    if not snapshot.matches(None):
        raise ComposerError("native startup composer was not empty")
    root = profile_directory(config)
    profile = read_document(root / "profile.json")
    write_document(
        root,
        "binding.json",
        {
            "protocol": PROTOCOL,
            "nonce": profile["launch_nonce"],
            "identity": config.identity,
            "session": config.session,
            "shortcut": "C-x e",
            "byte_count": snapshot.byte_count,
            "sha256": snapshot.sha256,
            "native_pid": snapshot.native_pid,
            "native_start": snapshot.native_start,
        },
    )


def inspect_editor_composer(
    config: AgentTmuxConfig,
    screen: str,
    *,
    runner: CommandRunner,
) -> ComposerSnapshot:
    """Read native input, treating command or protocol failures as unavailable.

    Parameters
    ----------
    config : AgentTmuxConfig
        Exact session with a previously qualified native editor binding.
    screen : str
        Current bounded pane capture used to refuse unsupported modes.
    runner : CommandRunner
        Real tmux controls; an injected runner must bound its commands.

    Returns
    -------
    ComposerSnapshot
        Correlated digest, length and same-process identity after callback exit.

    Raises
    ------
    ComposerError
        Input mode, binding, callback, private files or current process cannot
        be verified. Caller-visible text does not disclose internal errors.

    Notes
    -----
    The callback rejects the editor transaction to preserve native input.
    This function never pastes text or submits a prompt.
    """
    try:
        return _inspect_editor_composer(config, screen, runner=runner)
    except Exception as exc:
        raise ComposerError("native editor readback is unavailable") from exc
