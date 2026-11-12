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


async def _reap_direct_child(proc: asyncio.subprocess.Process) -> None:
    """Wait until the direct child has a status."""
    while proc.returncode is None:
        await proc.wait()


async def _wait_for_exit(proc: asyncio.subprocess.Process, timeout: float) -> None:
    """Wait until the child exits or the grace period ends.

    An interrupt while the child has been signalled and has not yet exited
    does not abandon the wait.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while proc.returncode is None:
        remaining = deadline - loop.time()
        try:
            await asyncio.wait_for(proc.wait(), max(0.0, remaining))
        except TimeoutError:
            return
        except asyncio.CancelledError:
            continue


async def _terminate_locked_command(proc: asyncio.subprocess.Process) -> None:
    """Stop a cancelled command and wait until it has been reaped.

    A command that already has a status is left alone: it has been reaped
    before anything was signalled here, so nothing is known about its group,
    and on POSIX that group id may by now name another group. The forced kill
    at the end is different: it follows this helper's own signal by an instant
    and is for descendants that ignored it and still hold the group. Windows
    terminates the direct child once. That termination cannot be ignored, so
    there is no second kill after a grace period. POSIX signals the process
    group, waits five seconds, then forces any survivor to exit.
    """
    if proc.returncode is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        await _reap_direct_child(proc)
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        await _wait_for_exit(proc, 5.0)
    finally:
        # The group can outlive its leader when a descendant ignores TERM.
        # Stop those descendants before the lock releases its task claim.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await _reap_direct_child(proc)


async def run_locked_subprocess(command: list[str]) -> int:
    """Run a command and reap it before cancellation permits lease cleanup.

    On POSIX the command owns a new session so cancellation also terminates
    children in that process group. After five seconds without exit, POSIX
    forces that group to terminate. Windows terminates the direct child in one
    step, because that termination cannot be ignored, and then waits until the
    child has been reaped. An OS launch failure prints the executable and reason
    to stderr, then returns 127 for a missing executable or interpreter, or 126
    for another refusal.

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

    try:
        return await proc.wait()
    except asyncio.CancelledError:
        cleanup = asyncio.create_task(_terminate_locked_command(proc), name="locked-command-reap")
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Repeated interrupts cannot release the claim before the
                # original bounded termination and process reaping finish.
                continue
        if cleanup.cancelled():
            await _terminate_locked_command(proc)
        else:
            cleanup.result()
        raise
