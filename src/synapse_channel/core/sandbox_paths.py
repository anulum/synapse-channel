# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonicalise and refuse unsafe host paths before a sandbox preopen
"""Host-path hardening for sandbox preopens: canonicalise, and refuse a symlink escape.

A capability manifest grants a sandboxed tool a set of ``(host_path, guest_path, write)``
filesystem preopens (:mod:`synapse_channel.core.sandbox_policy`); the WebAssembly runtime
preopens each host directory for the guest. A host path is trusted operator input, but the
directory it names on disk is resolved at *run* time — so between the manifest being authored
and the tool running, a component of the path could become a symlink that redirects the preopen
to a directory the operator never granted (a symlink swap).

This module closes that gap, deny-by-default and I/O-only-at-the-boundary: it resolves each host
path to its canonical real directory and refuses the grant fail-closed when the real path differs
from the lexical one — a symlink redirected it — or when it is not an existing directory. The
resolved path is what the caller preopens and records in the run receipt, so the sandbox reaches
exactly the directory the operator can see it reached, never a link's moving target.
"""

from __future__ import annotations

import os


class SandboxPathError(RuntimeError):
    """Raised when a sandbox filesystem grant's host path fails validation.

    A single type for every rejection — a symlink-redirected path or one that is not an
    existing directory — so the runner catches one error and refuses the whole run rather
    than preopening a directory the manifest did not literally name.
    """


def resolve_preopen_host(host_path: str) -> str:
    """Return the canonical real directory for a preopen host path, or refuse it fail-closed.

    The host path must name an existing directory whose real path equals its lexical absolute
    path: if resolving symlinks changes the path, a component is a link that redirects the grant
    elsewhere, and the grant is refused. Preopening the returned real path (rather than the
    operator's literal string) means a symlink swapped in after the manifest was authored cannot
    silently point the sandbox at an ungranted directory.

    Parameters
    ----------
    host_path : str
        The host directory a filesystem grant names.

    Returns
    -------
    str
        The canonical, symlink-free absolute path to preopen.

    Raises
    ------
    SandboxPathError
        If the path resolves through a symlink, or is not an existing directory.
    """
    lexical = os.path.abspath(host_path)
    real = os.path.realpath(host_path)
    if real != lexical:
        msg = (
            f"host path {host_path!r} resolves through a symlink to {real!r}; "
            "grant the real directory, not a link into it"
        )
        raise SandboxPathError(msg)
    if not os.path.isdir(real):
        msg = f"host path {host_path!r} is not an existing directory"
        raise SandboxPathError(msg)
    return real


def harden_preopens(
    preopens: tuple[tuple[str, str, bool], ...],
) -> tuple[tuple[str, str, bool], ...]:
    """Resolve and validate every preopen's host path, preserving the guest path and write flag.

    Maps each ``(host, guest, write)`` to ``(resolved_host, guest, write)``. Raises
    :class:`SandboxPathError` on the first host path that fails validation, so a run with any
    unsafe grant is refused whole before the tool executes.
    """
    return tuple((resolve_preopen_host(host), guest, write) for host, guest, write in preopens)
