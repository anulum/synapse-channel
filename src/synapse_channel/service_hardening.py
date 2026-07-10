# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the systemd sandbox directive block shared by generated units
"""The systemd sandboxing block every generated Synapse user service carries.

One module owns the directive set so the hub, presence, and wake-listener
renderers (and the checked-in ``deploy/*.service`` templates that mirror them)
cannot drift apart. The set is the strongest one a **user** service manager can
actually apply: directives that require manipulating the capability bounding
set (``CapabilityBoundingSet=``, ``ProtectClock=``, ``ProtectKernelModules=``,
``PrivateDevices=``) fail at spawn with ``218/CAPABILITIES`` under
``systemd --user`` because an unprivileged manager lacks ``CAP_SETPCAP``, so
they are deliberately absent. ``MemoryDenyWriteExecute=`` is also excluded:
the optional ``cryptography`` extra loads ``cffi``, whose call trampolines are
incompatible with a W^X mapping policy on some platforms.
``ProtectHome=read-only`` (not ``yes``) keeps ``%h/.local/bin`` readable, where
a pipx-installed ``synapse`` executable lives.

Writable paths are the narrow, code-derived set the services actually use:
``%h/synapse`` (hub event store and relay feed, mailbox cursors, owner-lease
tokens) and, for connecting clients, the machine-identity directory under
``%h/.local/share/synapse`` (trust-on-first-use key auto-provisioning). The
install helpers create both directories up front because ``ReadWritePaths=``
refuses to mount a path that does not exist yet.
"""

from __future__ import annotations

HUB_NOFILE = 65536
"""File-descriptor ceiling for the hub: one socket per connected agent."""

LISTENER_NOFILE = 4096
"""File-descriptor ceiling for single-connection listeners (presence, arm)."""

HUB_WRITE_PATHS: tuple[str, ...] = ("%h/synapse",)
"""The hub writes only its event store and relay feed."""

LISTENER_WRITE_PATHS: tuple[str, ...] = ("%h/synapse", "%h/.local/share/synapse")
"""Clients also auto-provision the trust-on-first-use machine key."""


def hardening_directives(*, write_paths: tuple[str, ...], nofile: int) -> str:
    """Return the ``[Service]`` sandbox directive block for a generated unit.

    Parameters
    ----------
    write_paths : tuple[str, ...]
        Paths left writable under ``ProtectSystem=strict``; everything else on
        the filesystem is read-only to the service. Entries may use systemd
        specifiers such as ``%h``.
    nofile : int
        ``LimitNOFILE`` value: :data:`HUB_NOFILE` for the hub,
        :data:`LISTENER_NOFILE` for single-connection listeners.

    Returns
    -------
    str
        Newline-terminated directive lines, ready to embed in a unit's
        ``[Service]`` section.
    """
    paths = " ".join(write_paths)
    return (
        "NoNewPrivileges=yes\n"
        "UMask=0077\n"
        "PrivateTmp=yes\n"
        "ProtectSystem=strict\n"
        "ProtectHome=read-only\n"
        f"ReadWritePaths={paths}\n"
        "ProtectKernelTunables=yes\n"
        "ProtectControlGroups=yes\n"
        "RestrictNamespaces=yes\n"
        "RestrictRealtime=yes\n"
        "RestrictSUIDSGID=yes\n"
        "LockPersonality=yes\n"
        "SystemCallArchitectures=native\n"
        f"LimitNOFILE={nofile}\n"
    )
