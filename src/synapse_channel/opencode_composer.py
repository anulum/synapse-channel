# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native OpenCode external-editor readback callback
"""Export only a digest and length; reject the editor transaction unchanged.

OpenCode 1.18.32 restores its saved prompt when an external editor exits
nonzero. This callback deliberately rejects even successful readbacks so the
client never normalises a trailing newline or realigns an owner's attachments.
The request and response contain no draft text and cannot submit a prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import time
from collections.abc import Sequence
from pathlib import Path

from synapse_channel.core.secure_path import assert_owner_only_dir_path
from synapse_channel.core.terminal_text import terminal_text
from synapse_channel.opencode_composer_files import (
    ComposerError,
    read_document,
    read_native_draft,
    write_document,
)
from synapse_channel.waker_commands import run_waker_command

PROTOCOL = "synapse-opencode-composer.v1"
SUPPORTED_VERSION = "1.18.32"
EDITOR_ABORT = 7
PROBE_SECONDS = 5.0
PROFILE_ENV = "SYN_OPENCODE_EDITOR_PROFILE"
LAUNCH_ENV = "SYN_OPENCODE_STARTUP_ID"


def process_stamp(pid: int) -> tuple[int, str, str]:
    """Read a bounded POSIX parent/start/name tuple without process arguments.

    Parameters
    ----------
    pid : int
        Live process identifier greater than one.

    Returns
    -------
    tuple of int, str, str
        Parent identifier, process start timestamp in UTC with English month
        names, and executable basename, independent of the caller's locale.

    Raises
    ------
    ComposerError
        The identifier is invalid or the process identity cannot be read.
    """
    if isinstance(pid, bool) or pid <= 1:
        raise ComposerError("native process id is invalid")
    result = run_waker_command(
        [
            "/usr/bin/env",
            "LC_ALL=C",
            "TZ=UTC0",
            "/bin/ps",
            "-p",
            str(pid),
            "-o",
            "pid=,ppid=,lstart=,comm=",
        ],
        capture_output=True,
        text=True,
        timeout=1.0,
    )
    fields = result.stdout.strip().split(maxsplit=7)
    if result.returncode or len(fields) != 8 or fields[0] != str(pid):
        raise ComposerError("native process identity is unavailable")
    try:
        parent = int(fields[1])
    except ValueError as exc:
        raise ComposerError("native parent process id is invalid") from exc
    return parent, " ".join(fields[2:7]), Path(fields[7]).name


def require_native_process(pid: int, started: str, pane_pid: int) -> None:
    """Require the same live native process beneath the requested pane.

    Parameters
    ----------
    pid : int
        OpenCode process recorded by the editor callback.
    started : str
        Previously observed start timestamp for that process.
    pane_pid : int
        Process at the root of the requested tmux pane.

    Raises
    ------
    ComposerError
        The executable or timestamp changed, or no pane ancestor is found
        within eight parent steps.
    """
    ancestor, observed, name = process_stamp(pid)
    if name != "opencode" or observed != started:
        raise ComposerError("native process identity changed")
    current = pid
    for _ in range(8):
        if current == pane_pid:
            return
        current = ancestor
        if current <= 1:
            break
        ancestor, _, _ = process_stamp(current)
    raise ComposerError("editor callback is outside the requested pane")


def _bound_native_parent(pane_pid: int) -> tuple[int, str]:
    """Bind the callback's native parent to the requested live pane ancestry."""
    parent = os.getppid()
    _, started, _ = process_stamp(parent)
    require_native_process(parent, started, pane_pid)
    return parent, started


def _text(document: dict[str, object], name: str) -> str:
    """Validate a bounded non-empty terminal-safe protocol string."""
    value = document.get(name)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or terminal_text(value) != value
        or "\x00" in value
    ):
        raise ComposerError("native composer string field is invalid")
    return value


def _require_launch_nonce(profile: dict[str, object]) -> None:
    """Bind the editor callback to the launch that wrote its current profile."""
    nonce = _text(profile, "launch_nonce")
    if not re.fullmatch(r"[0-9a-f]{32}", nonce) or os.environ.get(LAUNCH_ENV) != nonce:
        raise ComposerError("native editor launch binding is invalid")


def _request(root: Path) -> tuple[dict[str, object], str, int]:
    """Accept one current request for this exact native editor environment."""
    assert_owner_only_dir_path(root, purpose="native composer profile")
    profile = read_document(root / "profile.json")
    identity, session = _text(profile, "identity"), _text(profile, "session")
    _require_launch_nonce(profile)
    if (
        profile.get("protocol") != PROTOCOL
        or profile.get("version") != SUPPORTED_VERSION
        or os.environ.get(PROFILE_ENV) != str(root)
        or os.environ.get("SYN_IDENTITY") != identity
        or os.environ.get("SYN_PROJECT") != identity.split("/", 1)[0]
    ):
        raise ComposerError("native editor profile binding is invalid")
    request = read_document(root / "request.json")
    nonce = _text(request, "nonce")
    deadline, pane = request.get("deadline_ns"), request.get("pane_pid")
    if (
        request.get("protocol") != PROTOCOL
        or request.get("identity") != identity
        or request.get("session") != session
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
        or not isinstance(deadline, int)
        or isinstance(deadline, bool)
        or not 0 < deadline - time.monotonic_ns() <= int(PROBE_SECONDS * 1_000_000_000)
        or not isinstance(pane, int)
        or isinstance(pane, bool)
        or pane <= 1
    ):
        raise ComposerError("native editor request is invalid or expired")
    return request, nonce, pane


def observe_native_draft(root: Path, draft: Path) -> None:
    """Write one correlated content-free readback from the real native editor.

    Parameters
    ----------
    root : pathlib.Path
        Owner-only profile directory named by the callback environment.
    draft : pathlib.Path
        Bounded regular native export beneath the profile temporary directory.

    Raises
    ------
    ComposerError
        Request, process ancestry or export validation fails.
    ValueError, OSError
        Private metadata or the export cannot be read safely.

    Notes
    -----
    The response contains a digest and byte count, never the draft text.
    No response is written if the request was replaced or its deadline expired.
    """
    request, nonce, pane = _request(root)
    parent, started = _bound_native_parent(pane)
    data = read_native_draft(root, draft)
    current = read_document(root / "request.json")
    if current != request or time.monotonic_ns() >= int(str(request["deadline_ns"])):
        raise ComposerError("native editor request was replaced or expired")
    _, callback_started, _ = process_stamp(os.getpid())
    write_document(
        root,
        "response.json",
        {
            "protocol": PROTOCOL,
            "nonce": nonce,
            "byte_count": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "native_pid": parent,
            "native_start": started,
            "callback_pid": os.getpid(),
            "callback_start": callback_started,
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the native callback, always returning the draft-preserving abort code.

    Parameters
    ----------
    argv : sequence of str or None, optional
        Callback arguments; None reads the current command line.

    Returns
    -------
    int
        EDITOR_ABORT after either a successful observation or a refused probe.
        OpenCode uses this nonzero exit to restore its original input.

    Notes
    -----
    Argument parsing retains argparse's help and usage exits. Runtime errors
    produce no diagnostic output that could disclose the native draft.
    """
    parser = argparse.ArgumentParser(description="Read a configured OpenCode editor export.")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("draft", type=Path)
    args = parser.parse_args(argv)
    try:
        if os.name != "posix":
            raise ComposerError("native editor readback requires POSIX")
        observe_native_draft(args.profile, args.draft)
    except Exception:
        # A callback failure must reject the editor transaction, never disclose
        # native input through an exception or turn a timeout into acceptance.
        return EDITOR_ABORT
    return EDITOR_ABORT


if __name__ == "__main__":
    raise SystemExit(main())
