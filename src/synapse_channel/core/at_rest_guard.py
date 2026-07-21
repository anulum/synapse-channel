# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — at-rest exposure guard for the routing hub
"""Refuse an exposed hub whose durable event store is plaintext on disk.

Proportionate to exposure, mirroring the bind-exposure guard in
:mod:`synapse_channel.core.hub_exposure`: a loopback / single-owner hub keeps a
plaintext event store exactly as before, because the disk is the operator's own.
Only an *off-loopback* bind that keeps a plaintext ``--db`` is refused — there the
durable coordination log would sit unencrypted on a networked host's disk, so a
disk or backup compromise leaks the whole history. The operator satisfies it by
encrypting the store (``synapse encrypt-key migrate-sqlcipher`` then
``--db-key-file``) or accepts the risk explicitly with ``--insecure-plaintext-at-rest``.
"""

from __future__ import annotations

import logging

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.hub_exposure import is_loopback_host


class AtRestBindError(SynapseError, RuntimeError):
    """Raised when an exposed hub would keep its event store in plaintext.

    Off loopback the hub refuses to bind with a plaintext ``--db`` unless an
    encryption key is configured (``--db-key-file``, which selects the SQLCipher
    store). An operator who accepts the risk passes ``insecure_plaintext_at_rest``
    (CLI: ``--insecure-plaintext-at-rest``) to downgrade the refusal to a warning.
    Loopback binds and encrypted stores are unaffected.
    """

    code = "at_rest_bind"


def plaintext_store_problem(
    host: str,
    *,
    db: str | None,
    encrypted: bool,
    sqlcipher_available: bool,
) -> str | None:
    """Return the at-rest exposure problem for binding on ``host``, or ``None``.

    The problem fires only for an off-loopback bind that keeps a configured but
    unencrypted ``--db``. Loopback binds, a hub with no durable store, and an
    already-encrypted store all return ``None``. When the SQLCipher driver is
    absent the message adds the install hint, because a refused start otherwise
    cannot even encrypt the store.
    """
    if is_loopback_host(host) or not db or encrypted:
        return None
    install_hint = (
        ""
        if sqlcipher_available
        else " — install the SQLCipher extra first (pip install 'synapse-channel[sqlcipher]')"
    )
    return (
        f"binds off-loopback host {host!r} with a plaintext event store {db!r}; the "
        "durable coordination log would sit unencrypted on the host's disk. Encrypt "
        "it (synapse encrypt-key migrate-sqlcipher) and pass --db-key-file"
        f"{install_hint}"
    )


def guard_at_rest(
    host: str,
    *,
    db: str | None,
    encrypted: bool,
    insecure_plaintext_at_rest: bool,
    sqlcipher_available: bool,
    logger: logging.Logger,
) -> None:
    """Refuse, or warn before, binding an exposed hub with a plaintext store.

    ``insecure_plaintext_at_rest`` downgrades the refusal to a warning; an
    encrypted store or a loopback bind clears it entirely.
    """
    problem = plaintext_store_problem(
        host, db=db, encrypted=encrypted, sqlcipher_available=sqlcipher_available
    )
    if problem is None:
        return
    if insecure_plaintext_at_rest:
        logger.warning("Synapse Hub %s.", problem)
        return
    raise AtRestBindError(
        f"Refusing to bind: Synapse Hub {problem}, or pass "
        "--insecure-plaintext-at-rest to bind anyway (not recommended)."
    )
