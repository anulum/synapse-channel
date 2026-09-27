# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — locked subprocess lifetime
"""Finish a locked command's subprocess lifetime before lease cleanup proceeds."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys


async def run_locked_subprocess(command: list[str]) -> int:
    """Run a command and reap it before cancellation permits lease cleanup.

    On POSIX the command owns a new session so cancellation also terminates
    children in that process group. Windows terminates the direct child. After
    five seconds without exit, force termination before propagating cancellation.
    An OS launch failure prints the executable and reason to stderr, then returns
    127 for a missing executable or interpreter, or 126 for another refusal.

    Parameters
    ----------
    command : list[str]
        Executable and arguments, passed directly without shell expansion.

    Returns
    -------
    int
        The child process's exit status after normal completion, or the launch
        failure status 127 (not found) or 126 (cannot execute).

    Raises
    ------
    asyncio.CancelledError
        After interrupted process termination and reaping have completed.
    """
    try:
        proc = await asyncio.create_subprocess_exec(*command, start_new_session=os.name != "nt")
    except OSError as exc:
        print(f"lock: cannot execute {command[0]!r}: {exc.strerror}", file=sys.stderr)
        return 127 if isinstance(exc, FileNotFoundError) else 126

    async def terminate() -> None:
        """Complete bounded termination independently of the caller's cancellation."""
        with contextlib.suppress(ProcessLookupError):
            if os.name == "nt":
                proc.terminate()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                if os.name == "nt":
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        finally:
            # The group can outlive its leader when a descendant ignores TERM.
            # Stop those descendants before the lock releases its task claim.
            with contextlib.suppress(ProcessLookupError):
                if os.name == "nt":
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            while proc.returncode is None:
                try:
                    await proc.wait()
                except asyncio.CancelledError:
                    continue

    try:
        return await proc.wait()
    except asyncio.CancelledError:
        cleanup = asyncio.create_task(terminate())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Repeated interrupts cannot release the claim before the
                # original bounded termination and process reaping finish.
                continue
        if cleanup.cancelled():
            if proc.returncode is None:
                await terminate()
        else:
            cleanup.result()
        raise
