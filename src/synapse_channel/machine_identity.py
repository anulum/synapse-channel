# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — zero-config per-machine Ed25519 identity keypair
"""Auto-provisioned per-machine identity keypair — the zero-config credential.

Connection-identity binding (:mod:`synapse_channel.core.identity_binding`)
proves a socket is the identity it registers as, but its operator-managed
trust bundle asks for exactly the configuration a ``pip install
synapse-channel`` user never does: generate a key, enrol it, pass flags. This
module removes that step. The first command that connects provisions one
Ed25519 keypair for the whole machine under ``$XDG_DATA_HOME/synapse/identity/``
(owner-only, exclusive-create), every later command loads and reuses it, and
the registration frame carries its public half so a local hub can pin the
name to the key on first use — trust-on-first-use, the loopback posture.

The key is per-machine, not per-identity: one seat runs many names (a waiter
sidecar, one-shot verbs, roles) and they are all the same actor, so they share
the credential. Provisioning is best-effort by design — a read-only home or a
locked-down container degrades to today's unsigned registration rather than
refusing to start, because zero-config protection must never become a
zero-config outage.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.identity_keys import (
    IdentityKeyError,
    generate_signing_key,
    load_signing_key,
    public_key_b64,
    write_signing_key,
)
from synapse_channel.core.private_dir import PrivateDirError, ensure_private_dir

MACHINE_KEY_FILENAME = "machine.pem"
"""The per-machine private-key file under the identity directory."""

MACHINE_KEY_ID_PREFIX = "machine-"
"""Key-id prefix marking an auto-provisioned machine key in hub pins and logs."""

_KEY_ID_DIGEST_CHARS = 16


@dataclass(frozen=True)
class MachineIdentity:
    """The provisioned machine credential a client presents at registration.

    Attributes
    ----------
    key_path : pathlib.Path
        The private key's PEM file (owner-only), loadable with
        :func:`~synapse_channel.core.identity_keys.load_signing_key`.
    key_id : str
        Stable identifier derived from the public key
        (``machine-<sha256-prefix>``), carried in the signature envelope and
        pinned by the hub.
    public_key : str
        Base64 raw Ed25519 public key, carried on the registration frame so a
        first-use hub can verify and pin it.
    """

    key_path: Path
    key_id: str
    public_key: str


def identity_dir(*, base: Path | None = None) -> Path:
    """Return the machine identity directory, honouring ``$XDG_DATA_HOME``.

    Parameters
    ----------
    base : pathlib.Path or None, optional
        Explicit data-home override (the test seam). ``None`` reads
        ``$XDG_DATA_HOME`` and falls back to ``~/.local/share``, per the XDG
        base-directory convention.

    Returns
    -------
    pathlib.Path
        ``<data-home>/synapse/identity``.
    """
    if base is None:
        raw = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(raw) if raw else Path.home() / ".local" / "share"
    return base / "synapse" / "identity"


def _key_id_for(public_key: str) -> str:
    """Return the stable key id derived from a base64 public key."""
    digest = hashlib.sha256(public_key.encode("ascii")).hexdigest()
    return MACHINE_KEY_ID_PREFIX + digest[:_KEY_ID_DIGEST_CHARS]


def ensure_machine_identity(*, base: Path | None = None) -> MachineIdentity:
    """Return the machine identity, provisioning the keypair on first use.

    The private key is written with exclusive-create at ``0o600``, so two
    processes racing the first provision cannot tear it: the loser's create
    fails and it loads the winner's key instead. Every later call loads the
    same file, so the machine presents one stable credential for its whole
    lifetime.

    Parameters
    ----------
    base : pathlib.Path or None, optional
        Data-home override forwarded to :func:`identity_dir`.

    Returns
    -------
    MachineIdentity
        The provisioned (or pre-existing) credential.

    Raises
    ------
    IdentityKeyError
        When the key can neither be created nor loaded — an unwritable
        directory, or an existing file that is not a valid Ed25519 PEM.
    ImportError
        When the optional ``cryptography`` package is not installed; the
        Ed25519 primitives import lazily inside
        :mod:`~synapse_channel.core.identity_keys`. Callers that must never
        fail use :func:`machine_identity_agent_kwargs`, which degrades to an
        unsigned connection instead.
    """
    directory = identity_dir(base=base)
    key_path = directory / MACHINE_KEY_FILENAME
    if not key_path.is_file():
        try:
            # Owner-only floor, not bare ``mkdir(exist_ok=True)``: a pre-existing
            # symlinked or foreign-owned identity directory is refused rather
            # than silently accepted as the home for the machine signing key.
            ensure_private_dir(directory, parents=True, purpose="machine identity directory")
        except (OSError, PrivateDirError) as exc:
            raise IdentityKeyError(f"cannot create identity directory {directory}: {exc}") from exc
        try:
            write_signing_key(key_path, generate_signing_key())
        except IdentityKeyError:
            # Lost the first-provision race (or the file appeared between the
            # check and the create): the existing key is the machine identity.
            if not key_path.is_file():
                raise
    public = public_key_b64(load_signing_key(key_path))
    return MachineIdentity(key_path=key_path, key_id=_key_id_for(public), public_key=public)


def machine_identity_agent_kwargs(*, base: Path | None = None) -> dict[str, Any]:
    """Return the client keyword pair presenting the machine identity, or nothing.

    The zero-config entry point for CLI verbs: splat the result into a
    :class:`~synapse_channel.client.agent.SynapseAgent` construction and the
    registration frame is signed with the machine key (provisioned on first
    use). Best-effort: when the keypair cannot be provisioned or loaded — a
    read-only home, a corrupt file — this returns an empty mapping and the
    connection proceeds unsigned, exactly as before the machine identity
    existed. Protection degrades; startup never fails.

    Parameters
    ----------
    base : pathlib.Path or None, optional
        Data-home override forwarded to :func:`identity_dir`.

    Returns
    -------
    dict[str, Any]
        ``identity_key_path`` and ``identity_key_id`` keyword arguments, or an
        empty mapping when provisioning is unavailable — including when the
        optional ``cryptography`` package is not installed (the core package
        depends only on ``websockets``), in which case the connection simply
        registers unsigned exactly as every pre-0.99 client did.
    """
    try:
        machine = ensure_machine_identity(base=base)
    except (IdentityKeyError, ImportError):
        return {}
    return {
        "identity_key_path": str(machine.key_path),
        "identity_key_id": machine.key_id,
    }


def who_query_identity(subject: str, *, base: Path | None = None) -> str | None:
    """Return a stable read-only query identity scoped to this machine key.

    A roster query normally connects under the operator-supplied subject. If
    that name is pinned to a retired machine key, the hub correctly refuses the
    genuine key change with ``4013``. This alias gives ``synapse who`` one
    narrow recovery path: retry the read-only query under a name derived from
    the current public key without removing or replacing the protected pin.

    The alias is stable across processes that hold the same Ed25519 key and
    changes when the key changes. Only a digest is exposed in the name; neither
    private material nor the operator's subject is embedded. Provisioning
    remains best-effort, matching :func:`machine_identity_agent_kwargs`.

    Parameters
    ----------
    subject : str
        Requested roster-query identity, included only in the digest domain.
    base : pathlib.Path or None, optional
        Data-home override forwarded to :func:`ensure_machine_identity`.

    Returns
    -------
    str or None
        A bounded ``query/who-*`` identity, or ``None`` when no machine key is
        available and a signed fallback cannot be authenticated.
    """
    try:
        machine = ensure_machine_identity(base=base)
    except (IdentityKeyError, ImportError):
        return None
    material = (
        b"synapse-who-query\x00"
        + subject.encode("utf-8")
        + b"\x00"
        + machine.public_key.encode("ascii")
    )
    digest = hashlib.sha256(material).hexdigest()[:24]
    return f"query/who-{digest}"
