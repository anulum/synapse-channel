# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — metadata-only tmux observation
"""Read bounded tmux formats without pane capture or environment enumeration."""

from __future__ import annotations

import os
import selectors
import subprocess  # nosec B404
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PaneMetadata:
    """Pane root and session assertions, independent of provider liveness.

    Attributes
    ----------
    pid : int
        Pane root PID reported by tmux, requiring process-lifetime validation.
    session, pane : str
        tmux session and pane identifiers, not user-controlled display titles.
    attached : bool
        At least one client is attached to the session, including control clients.
    identity, project : str or None
        SYN_IDENTITY and SYN_PROJECT session assertions. These do not establish
        process ownership, authenticated Synapse presence or action authority.
    """

    pid: int
    session: str
    pane: str
    attached: bool
    identity: str | None
    project: str | None


def observe_tmux(socket: str | None = None) -> tuple[tuple[PaneMetadata, ...], str]:
    """Read the selected local tmux socket within time and byte limits.

    Parameters
    ----------
    socket : str or None, optional
        Explicit tmux socket path; none selects tmux's local default.

    Returns
    -------
    tuple
        Pane metadata and complete, partial or unavailable status.
        No server is created and no session is changed.
    """
    argv = ["tmux"]
    if socket is not None:
        argv += ["-S", socket]
    argv += [
        "list-panes",
        "-a",
        "-F",
        "#{pane_pid}\t#{session_id}\t#{pane_id}\t#{session_attached}\t"
        "#{SYN_IDENTITY}\t#{SYN_PROJECT}",
    ]
    output = bytearray()
    try:
        # The socket is one -S argument; commands and formats are package constants.
        with subprocess.Popen(  # nosec B603
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False
        ) as child:
            assert child.stdout is not None
            deadline = time.monotonic() + 0.5
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or len(output) > 65536:
                        child.kill()
                        child.wait()
                        return (), "partial"
                    if not selector.select(remaining):
                        continue
                    chunk = os.read(child.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    output.extend(chunk)
            try:
                code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                return (), "partial"
    except OSError:
        return (), "unavailable"
    if code != 0:
        return (), "unavailable"
    panes: list[PaneMetadata] = []
    try:
        for line in output.decode("utf-8").splitlines():
            pid, session, pane, attached, identity, project = line.split("\t")
            panes.append(
                PaneMetadata(
                    int(pid), session, pane, int(attached) > 0, identity or None, project or None
                )
            )
    except (ValueError, UnicodeDecodeError):
        return (), "partial"
    return tuple(panes), "complete"
