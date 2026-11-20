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

**The pane starts from an empty environment.** A tmux server that already runs
gives a new pane its own global environment, which may hold a stale or
redirected ``SYN_HOME`` or ``XDG_STATE_HOME``. So the pane's command is
``env -i`` followed by a fixed list of names (:data:`ENV_PASSED`, any
``LC_*``, and the paths in :data:`ENV_PATHS` when they are absolute and outside
every git work tree and coordination tree), with values taken from this
process's environment, never from the server. ``SYN_IDENTITY`` and
``SYN_PROJECT`` name the seat. The hub token never travels in the environment
or on a command line: the holder reads it from ``--token-file``.
"""

from __future__ import annotations

import os
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
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.reap import safe_key
from synapse_channel.seat_hold import (
    AGENT_START_FAILED_EXIT_CODE,
    HUB_UNAVAILABLE_EXIT_CODE,
    READY_TIMEOUT_SECONDS,
    SEAT_LIVE_EXIT_CODE,
    UNSUPPORTED_PLATFORM_EXIT_CODE,
    default_marker_dir,
    marker_path,
)
from synapse_channel.session_ledger import shared_tree

ENV_PASSED = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "TERM",
    "COLORTERM",
    "TZ",
    "DBUS_SESSION_BUS_ADDRESS",
    "SSH_AUTH_SOCK",
    "DISPLAY",
    "WAYLAND_DISPLAY",
)
"""Names a restored seat gets as they are; with ``LC_*`` and :data:`ENV_PATHS`, nothing else."""

ENV_PASSED_PREFIX = "LC_"
"""Locale categories pass by this prefix."""

ENV_PATHS = (
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "TMUX_TMPDIR",
)
"""Directories passed only when absolute and outside every git work tree and coordination tree."""

ENV_DROPPED = ("SYN_HOME",)
"""Names that redirect Synapse state; always dropped, and named where the plan shows them."""

PANE_SETTINGS = ("SYN_TMUX_PROVIDER=1", "SYNAPSE_AUTO_CONNECT=0")
"""Fixed settings of every Synapse tmux seat, as :func:`start_session` gives them."""

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


def allowed_environ(environ: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """Return the listed part of ``environ``, and the names left out with the reason.

    Only the names in :data:`ENV_PASSED`, any ``LC_*`` name, and the paths in
    :data:`ENV_PATHS` that are absolute and outside every git work tree and
    coordination tree are kept. The reasons name what was dropped, never a value.
    """
    env = {
        name: value
        for name, value in environ.items()
        if name in ENV_PASSED or name.startswith(ENV_PASSED_PREFIX)
    }
    dropped = [f"{name} (redirects Synapse state)" for name in ENV_DROPPED if name in environ]
    for name in ENV_PATHS:
        value = environ.get(name, "")
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            dropped.append(f"{name} (not an absolute path)")
        elif shared_tree(path.resolve()) is not None:
            dropped.append(f"{name} (in a git work tree or a coordination tree)")
        else:
            env[name] = value
    return env, dropped


def pane_environment(environ: Mapping[str, str], identity: str) -> list[str]:
    """Return the ``NAME=value`` words the pane's ``env -i`` sets, in a fixed order.

    The listed part of ``environ`` (:func:`allowed_environ`), the seat's
    ``SYN_IDENTITY`` and ``SYN_PROJECT``, and :data:`PANE_SETTINGS`. A hub token
    is never among them.
    """
    env, _dropped = allowed_environ(environ)
    project = environ.get("SYN_PROJECT", "").strip() or identity.partition("/")[0]
    words = [f"{name}={env[name]}" for name in sorted(env)]
    return [*words, f"SYN_IDENTITY={identity}", f"SYN_PROJECT={project}", *PANE_SETTINGS]


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
    environment: Sequence[str] | None = None,
) -> list[str]:
    """Return the pane command that runs ``command`` behind the seat holder.

    With ``wake_session`` the holder also runs the pane's waiter, in its own
    process, so the seat needs no separate ``agent-tmux wait``. With
    ``environment`` the command starts with ``env -i`` and those words, so the
    holder and its agent get nothing from the tmux server's environment.
    """
    token = ["--token-file", token_file] if token_file else []
    wake = ["--wake-session", wake_session, "--tmux-bin", tmux_bin] if wake_session else []
    clean = ["env", "-i", *environment] if environment is not None else []
    return [
        *clean,
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


def seat_token_file(directory: Path, identity: str, nonce: str, token: str) -> Path:
    """Write the hub token where only the seat's holder reads it, and return the path.

    The file is new (``O_EXCL``), ``0600``, in the holder's private marker
    directory, and named by this start's nonce. It stays while this holder
    runs, including a hub reconnect, so the recorded pane command still finds
    it. The holder removes it when the seat ends. A caller's own token file
    is a different path and is not removed.
    """
    ensure_private_dir(directory, parents=True, purpose="seat holder marker directory")
    path = directory / f"{safe_key(identity)}.{nonce}.token"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    return path


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
        The hub; the holder reads its token from ``token_file``. A ``token``
        without a file is written to a private one (:func:`seat_token_file`),
        which is removed again when the seat is not held.
    tmux_bin, synapse_bin : str
        The executables the pane and the waiter run.
    env : Mapping[str, str]
        The environment; it names the private marker directory, and the
        pane's ``env -i`` takes its listed values from it
        (:func:`pane_environment`).
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
    start_nonce = nonce or secrets.token_hex(8)
    directory = default_marker_dir(env)
    written = (
        seat_token_file(directory, identity, start_nonce, token)
        if token and not token_file
        else None
    )
    holder_token_file = str(written) if written is not None else token_file
    pane = holder_command(
        synapse_bin=synapse_bin,
        identity=identity,
        nonce=start_nonce,
        marker_dir=directory,
        uri=uri,
        token_file=holder_token_file,
        command=command,
        wake_session=session,
        tmux_bin=tmux_bin,
        environment=pane_environment(env, identity),
    )
    code = _held_or_code(
        start(
            AgentTmuxConfig(
                identity=identity,
                session=session,
                cwd=cwd,
                agent_command=tuple(pane),
                tmux_bin=tmux_bin,
                synapse_bin=synapse_bin,
                uri=uri,
                # Only the file: a config holding the value could put it on a waiter's command line.
                token=None,
                token_file=Path(holder_token_file) if holder_token_file else None,
            )
        ),
        session=session,
        tmux_bin=tmux_bin,
        kill=kill,
        word=lambda: wait(marker_path(directory, identity, start_nonce)),
    )
    if code is not None:
        if written is not None:
            written.unlink(missing_ok=True)
        return code
    return after_ready(pane)


def _held_or_code(
    started: AgentTmuxWakeResult,
    *,
    session: str,
    tmux_bin: str,
    kill: Callable[[str, str], None],
    word: Callable[[], str | None],
) -> int | None:
    """Return ``None`` once the holder holds the seat, or the restore's code when it does not."""
    if started.returncode != 0:
        print(f"worker-session --restore: {started.detail}")
        return HUB_UNAVAILABLE_EXIT_CODE
    if not started.started:
        print(f"worker-session --restore: tmux session {session} already exists; the seat is live")
        return SEAT_LIVE_EXIT_CODE
    final = word()
    if final is None:
        kill(tmux_bin, session)
        print(f"worker-session --restore: the seat holder gave no final answer; {session} ended")
        return HUB_UNAVAILABLE_EXIT_CODE
    code = MARKER_EXIT_CODES[final]
    if code:
        # The holder has ended; the pane it ran in must not outlive it.
        kill(tmux_bin, session)
        print(f"worker-session --restore: the seat holder says {final}; {session} ended")
        return code
    return None
