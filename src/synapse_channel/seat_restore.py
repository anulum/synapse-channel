# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — start a cut seat again only behind its seat holder
"""Start a seat that a crash or reboot cut, behind its seat holder.

This is the path ``synapse worker-session --restore`` takes. The tmux pane
does not run the agent directly: it runs ``synapse seat-hold`` with a fresh
nonce, and the holder starts the agent only once the hub has accepted the
seat's dedicated name without a takeover (see :mod:`synapse_channel.seat_hold`).
This module then reads the holder's private marker. ``held`` only says that
the agent is being started, so the restore waits for one of the final words:

* ``ready``: the seat holds its name and the agent has started, and the caller
  goes on (records the pane, starts the waiter, attaches);
* ``refused``: a live connection holds the seat, exit :data:`SEAT_LIVE_EXIT_CODE`;
  the restore skips it;
* ``unavailable``, ``failed`` or ``unsupported``, or no final word within
  :data:`MARKER_WAIT_SECONDS`: the restore stops.

Whenever the seat is not held, the tmux session this restore created is ended,
so no pane outlives its holder (a ``remain-on-exit`` setting would keep it);
* a tmux session that already exists for the seat means the seat is live on
  this host: exit :data:`SEAT_LIVE_EXIT_CODE`, and nothing is started.

The waiter and the session registry are given the holder's command, not the
bare agent's, so a later restart of the pane goes through the holder too.
"""

from __future__ import annotations

import secrets
import subprocess  # nosec B404 # fixed argument vectors only, never a shell
import time
from collections.abc import Callable, Container, Mapping, Sequence
from pathlib import Path

from synapse_channel.agent_tmux import (
    SEAT_HOLD_SUBCOMMAND,
    AgentTmuxConfig,
    AgentTmuxWakeResult,
    start_session,
)
from synapse_channel.seat_hold import (
    AGENT_START_FAILED_EXIT_CODE,
    HUB_UNAVAILABLE_EXIT_CODE,
    READY_TIMEOUT_SECONDS,
    SEAT_LIVE_EXIT_CODE,
    UNSUPPORTED_PLATFORM_EXIT_CODE,
    default_marker_dir,
    marker_path,
)

HOLDER_START_ALLOWANCE_SECONDS = 15.0
"""Time allowed for the holder's own process to start, on a host that may be loaded."""

MARKER_WAIT_SECONDS = 2 * READY_TIMEOUT_SECONDS + HOLDER_START_ALLOWANCE_SECONDS
"""How long the restore waits for the holder's final word.

The holder's worst case is its start, then up to :data:`READY_TIMEOUT_SECONDS`
for the hub's welcome and up to as long again for the hub to keep the name
(``closed_after_ready``), then the agent's spawn.
"""

MARKER_EXIT_CODES: Mapping[str, int] = {
    "ready": 0,
    "refused": SEAT_LIVE_EXIT_CODE,
    "unavailable": HUB_UNAVAILABLE_EXIT_CODE,
    "failed": AGENT_START_FAILED_EXIT_CODE,
    "unsupported": UNSUPPORTED_PLATFORM_EXIT_CODE,
}
"""What each marker word means for the restore; ``0`` lets it go on."""


def holder_command(
    *,
    synapse_bin: str,
    identity: str,
    nonce: str,
    marker_dir: Path,
    uri: str,
    token_file: str | None,
    command: Sequence[str],
    wake_session: str | None = None,
    tmux_bin: str = "tmux",
) -> list[str]:
    """Return the pane command that runs ``command`` behind the seat holder.

    With ``wake_session`` the holder also runs the pane's waiter, in its own
    process, so the seat needs no separate ``agent-tmux wait``.
    """
    token = ["--token-file", token_file] if token_file else []
    wake = ["--wake-session", wake_session, "--tmux-bin", tmux_bin] if wake_session else []
    return [
        synapse_bin,
        SEAT_HOLD_SUBCOMMAND,
        "--identity",
        identity,
        "--nonce",
        nonce,
        "--marker-dir",
        str(marker_dir),
        "--uri",
        uri,
        *token,
        *wake,
        "--",
        *command,
    ]


def wait_for_marker(
    path: Path,
    *,
    final: Container[str] = MARKER_EXIT_CODES,
    timeout: float = MARKER_WAIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Return the holder's final marker word, or ``None`` when none came in time.

    ``held`` is not final: the holder writes it before it starts the agent, and
    replaces it with ``ready`` or ``failed``.
    """
    deadline = clock() + timeout
    while True:
        try:
            words = path.read_text(encoding="utf-8").split()
        except OSError:
            words = []
        if words and words[0] in final:
            return words[0]
        if clock() >= deadline:
            return None
        sleep(0.1)


def kill_session(tmux_bin: str, session: str) -> None:
    """End one exact tmux session, if it still exists."""
    subprocess.run(  # nosec B603 # argument vector, never a shell
        [tmux_bin, "kill-session", "-t", f"={session}"], capture_output=True, check=False
    )


def start_restored_seat(
    *,
    identity: str,
    command: Sequence[str],
    session: str,
    cwd: Path,
    uri: str,
    token: str | None,
    token_file: str | None,
    tmux_bin: str,
    synapse_bin: str,
    env: Mapping[str, str],
    after_ready: Callable[[Sequence[str]], int],
    start: Callable[[AgentTmuxConfig], AgentTmuxWakeResult] = start_session,
    kill: Callable[[str, str], None] = kill_session,
    wait: Callable[[Path], str | None] = wait_for_marker,
    nonce: str | None = None,
) -> int:
    """Start ``command`` for ``identity`` behind its seat holder; return the restore's verdict.

    Parameters
    ----------
    identity, command : str, Sequence[str]
        The seat and its agent command, as the ledger recorded them.
    session, cwd : str, Path
        The tmux session to create and the agent's working directory.
    uri, token, token_file : str, str or None, str or None
        The hub; the holder reads its token from ``token_file``.
    tmux_bin, synapse_bin : str
        The executables the pane and the waiter run.
    env : Mapping[str, str]
        The environment; it names the private marker directory.
    after_ready : Callable
        Called with the holder's pane command once the seat is held; its
        result is returned. ``worker-session`` records the pane and attaches;
        the holder in the pane runs the pane's waiter itself.
    start, kill, wait : Callable
        The tmux start, the session end and the marker wait; injectable for tests.
    nonce : str or None
        This start's token; a fresh random one by default.

    Returns
    -------
    int
        ``after_ready``'s result when the seat holds its name; otherwise the
        code from :data:`MARKER_EXIT_CODES`, :data:`SEAT_LIVE_EXIT_CODE` for a
        session that already exists, or :data:`HUB_UNAVAILABLE_EXIT_CODE` when
        the pane could not be started or gave no answer.
    """
    token_value = nonce or secrets.token_hex(8)
    directory = default_marker_dir(env)
    pane = holder_command(
        synapse_bin=synapse_bin,
        identity=identity,
        nonce=token_value,
        marker_dir=directory,
        uri=uri,
        token_file=token_file,
        command=command,
        wake_session=session,
        tmux_bin=tmux_bin,
    )
    started = start(
        AgentTmuxConfig(
            identity=identity,
            session=session,
            cwd=cwd,
            agent_command=tuple(pane),
            tmux_bin=tmux_bin,
            synapse_bin=synapse_bin,
            uri=uri,
            token=token,
        )
    )
    if started.returncode != 0:
        print(f"worker-session --restore: {started.detail}")
        return HUB_UNAVAILABLE_EXIT_CODE
    if not started.started:
        print(f"worker-session --restore: tmux session {session} already exists; the seat is live")
        return SEAT_LIVE_EXIT_CODE
    word = wait(marker_path(directory, identity, token_value))
    if word is None:
        kill(tmux_bin, session)
        print(f"worker-session --restore: the seat holder gave no final answer; {session} ended")
        return HUB_UNAVAILABLE_EXIT_CODE
    code = MARKER_EXIT_CODES[word]
    if code:
        # The holder has ended; the pane it ran in must not outlive it.
        kill(tmux_bin, session)
        print(f"worker-session --restore: the seat holder says {word}; {session} ended")
        return code
    return after_ready(pane)
