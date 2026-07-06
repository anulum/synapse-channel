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

An operator can narrow the reachable surface further with a set of *approved workspace roots*: when
one or more roots are supplied, a resolved host path is refused unless it lies at or below one of
them, so a manifest cannot preopen a directory outside the operator's declared workspace even if
that directory is a genuine, symlink-free path. With no roots supplied the constraint is inert and
the symlink and directory checks stand alone, so the policy is opt-in and backward-compatible.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass


class SandboxPathError(RuntimeError):
    """Raised when a sandbox filesystem grant's host path fails validation.

    A single type for every rejection — a symlink-redirected path or one that is not an
    existing directory — so the runner catches one error and refuses the whole run rather
    than preopening a directory the manifest did not literally name.
    """


def _within_approved_root(real_path: str, approved_roots: Sequence[str]) -> bool:
    """Return whether ``real_path`` lies at or below one of the canonicalised approved roots.

    Each root is resolved to its own canonical real path before the containment test, so an
    approved root given through a symlink still matches the directories genuinely under it.
    Containment is by whole path component (via :func:`os.path.commonpath`), so ``/work`` covers
    ``/work/in`` but never the sibling ``/workshop``.
    """
    for root in approved_roots:
        canonical = os.path.realpath(root)
        try:
            if os.path.commonpath([real_path, canonical]) == canonical:
                return True
        except ValueError:
            # Mixed absolute/relative or different anchors never share a common path.
            continue
    return False


def resolve_preopen_host(host_path: str, *, approved_roots: Sequence[str] = ()) -> str:
    """Return the canonical real directory for a preopen host path, or refuse it fail-closed.

    The host path must name an existing directory whose real path equals its lexical absolute
    path: if resolving symlinks changes the path, a component is a link that redirects the grant
    elsewhere, and the grant is refused. Preopening the returned real path (rather than the
    operator's literal string) means a symlink swapped in after the manifest was authored cannot
    silently point the sandbox at an ungranted directory.

    When ``approved_roots`` is non-empty the resolved path must also lie at or below one of the
    roots, so a manifest cannot preopen a directory outside the operator's declared workspace;
    an empty ``approved_roots`` leaves this constraint inert.

    Parameters
    ----------
    host_path : str
        The host directory a filesystem grant names.
    approved_roots : sequence of str, optional
        Operator-approved workspace roots the resolved path must fall under. Empty (the default)
        applies no root constraint.

    Returns
    -------
    str
        The canonical, symlink-free absolute path to preopen.

    Raises
    ------
    SandboxPathError
        If the path resolves through a symlink, is not an existing directory, or falls outside
        every approved workspace root.
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
    if approved_roots and not _within_approved_root(real, approved_roots):
        roots = ", ".join(sorted(os.path.realpath(root) for root in approved_roots))
        msg = (
            f"host path {host_path!r} resolves to {real!r}, outside the approved workspace "
            f"root(s) {roots}; grant a path under an approved root"
        )
        raise SandboxPathError(msg)
    return real


@dataclass(frozen=True)
class PreopenCheck:
    """The outcome of dry-checking one preopen host path against the live filesystem.

    A non-raising view of :func:`resolve_preopen_host`: ``ok`` is true when the path would be
    preopened at run time and ``resolved`` holds the canonical directory; otherwise ``ok`` is
    false, ``resolved`` is empty, and ``reason`` carries the same refusal message the runner
    would raise. It lets an operator pre-flight a manifest's grants without executing anything.

    Attributes
    ----------
    host_path : str
        The grant's host path, echoed back unchanged.
    ok : bool
        Whether the runner would accept this host path.
    resolved : str
        The canonical, symlink-free directory when ``ok``; empty otherwise.
    reason : str
        The refusal message when not ``ok``; empty otherwise.
    """

    host_path: str
    ok: bool
    resolved: str
    reason: str


def check_preopen_host(host_path: str, *, approved_roots: Sequence[str] = ()) -> PreopenCheck:
    """Dry-check a preopen host path, reporting acceptance instead of raising.

    Runs the same resolution as :func:`resolve_preopen_host` (including the ``approved_roots``
    workspace constraint) but turns a :class:`SandboxPathError` into a :class:`PreopenCheck` with
    ``ok=False`` and the refusal reason, so a caller (the ``sandbox validate`` pre-flight) can
    report every grant's fate in one pass rather than stopping at the first unsafe path.

    Parameters
    ----------
    host_path : str
        The host directory a filesystem grant names.
    approved_roots : sequence of str, optional
        Operator-approved workspace roots the resolved path must fall under. Empty (the default)
        applies no root constraint.

    Returns
    -------
    PreopenCheck
        The acceptance outcome, mirroring exactly what the runner would decide at run time.
    """
    try:
        resolved = resolve_preopen_host(host_path, approved_roots=approved_roots)
    except SandboxPathError as exc:
        return PreopenCheck(host_path=host_path, ok=False, resolved="", reason=str(exc))
    return PreopenCheck(host_path=host_path, ok=True, resolved=resolved, reason="")


def harden_preopens(
    preopens: tuple[tuple[str, str, bool], ...],
    *,
    approved_roots: Sequence[str] = (),
) -> tuple[tuple[str, str, bool], ...]:
    """Resolve and validate every preopen's host path, preserving the guest path and write flag.

    Maps each ``(host, guest, write)`` to ``(resolved_host, guest, write)``. Raises
    :class:`SandboxPathError` on the first host path that fails validation — a symlink redirect, a
    missing directory, or (when ``approved_roots`` is non-empty) a path outside every approved
    workspace root — so a run with any unsafe grant is refused whole before the tool executes.
    """
    return tuple(
        (resolve_preopen_host(host, approved_roots=approved_roots), guest, write)
        for host, guest, write in preopens
    )
