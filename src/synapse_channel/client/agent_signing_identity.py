# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the keys and counters one seat signs with
"""The only signing path an agent uses for identity, message auth and cards."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from synapse_channel.client.frame_signing_client import ClientFrameSession, sign_outbound
from synapse_channel.core.capability import normalized_capability_card
from synapse_channel.core.capability_card_signing import sign_capability_card
from synapse_channel.core.identity_keys import load_signing_key, public_key_b64, sign_registration
from synapse_channel.core.message_auth import (
    DEFAULT_SIGNED_MESSAGE_TYPES,
    MessageAuthKey,
    next_message_auth_sequence,
    sign_frame,
)
from synapse_channel.machine_identity import machine_identity_agent_kwargs

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_CARD_FIELDS = (
    "description",
    "skills",
    "task_classes",
    "model",
    "project",
    "manifest_digest",
    "contracts",
    "meta",
    "signature",
)


def _message_auth_key(
    name: str, key_id: str | None, secret: str | bytes | None
) -> MessageAuthKey | None:
    """Build the per-message key when both halves are present."""
    if key_id is None or secret is None:
        return None
    raw = secret if isinstance(secret, bytes) else secret.encode("utf-8")
    return MessageAuthKey(key_id=str(key_id), secret=raw, senders=frozenset({name}))


def _capability_card(
    name: str,
    key_path: str | None,
    key_id: str,
    project: str,
    lifetime_seconds: float,
) -> tuple[str, Ed25519PrivateKey | None, str, float]:
    """Check a capability card and return the project, key, id and lifetime."""
    if bool(key_path) != bool(key_id.strip()):
        raise ValueError(
            "capability_card_key_path and capability_card_key_id must be supplied together"
        )
    inferred_project = name.split("/", 1)[0] if "/" in name else ""
    requested_project = str(project).strip()
    if key_path and not inferred_project:
        raise ValueError("a signed live capability card requires a namespaced agent name")
    if key_path and requested_project not in ("", inferred_project):
        raise ValueError(
            "capability_card_project must match the agent namespace resolved by the hub"
        )
    lifetime = float(lifetime_seconds)
    if lifetime <= 0.0 or lifetime != lifetime or lifetime == float("inf"):
        raise ValueError("capability_card_lifetime_seconds must be finite and positive")
    key = load_signing_key(key_path) if key_path else None
    return inferred_project or requested_project, key, str(key_id).strip(), lifetime


class AgentSigningIdentity:
    """Own one seat's signing keys and the counters that advance with them.

    Construction applies the same checks the agent constructor used to apply.
    No method returns a private key. ``repr`` shows key ids only.
    """

    def __init__(
        self,
        *,
        name: str,
        identity_key_path: str | None,
        identity_key_id: str,
        machine_identity: bool,
        per_message_auth_key_id: str | None,
        per_message_auth_secret: str | bytes | None,
        capability_card_key_path: str | None,
        capability_card_key_id: str,
        capability_card_project: str,
        capability_card_lifetime_seconds: float,
    ) -> None:
        # Present the zero-config machine identity by default. Every verb
        # that builds an agent signs uniformly — the 2026-07-10 incident
        # 1603 lockout class, where arming pinned a name and every other
        # (unsigned) verb was then refused 4013, cannot recur through a
        # forgotten call site. Best-effort by contract: on a core-only
        # install or an unreadable key this resolves to nothing and the
        # connection proceeds unsigned, exactly as before.
        self._name = name
        if identity_key_path is None and machine_identity:
            resolved = machine_identity_agent_kwargs()
            if resolved:
                identity_key_path = str(resolved["identity_key_path"])
                identity_key_id = identity_key_id or str(resolved["identity_key_id"])
        self._message_auth_key = _message_auth_key(
            name, per_message_auth_key_id, per_message_auth_secret
        )
        self._message_auth_sequence = 0
        self._identity_key = load_signing_key(identity_key_path) if identity_key_path else None
        self._identity_key_id = str(identity_key_id)
        self._identity_sequence = 0
        project, card_key, card_id, lifetime = _capability_card(
            name,
            capability_card_key_path,
            capability_card_key_id,
            capability_card_project,
            capability_card_lifetime_seconds,
        )
        self._capability_card_project = project
        self._capability_card_key = card_key
        self._capability_card_key_id = card_id
        self._capability_card_sequence = 0
        self._capability_card_lifetime_seconds = lifetime

    def __repr__(self) -> str:
        """Show key ids and no key material."""
        auth_id = None if self._message_auth_key is None else self._message_auth_key.key_id
        return (
            "AgentSigningIdentity("
            f"identity_key_id={self._identity_key_id!r}, "
            f"message_auth_key_id={auth_id!r}, "
            f"capability_card_key_id={self._capability_card_key_id!r})"
        )

    @property
    def has_identity_key(self) -> bool:
        """Whether a registration can carry an identity signature."""
        return self._identity_key is not None

    @property
    def identity_key_id(self) -> str:
        """The id of the identity key, or an empty string when there is none."""
        return self._identity_key_id

    @property
    def has_capability_card_key(self) -> bool:
        """Whether advertisements are signed capability cards."""
        return self._capability_card_key is not None

    @property
    def capability_card_key_id(self) -> str:
        """The id of the capability-card key."""
        return self._capability_card_key_id

    @property
    def capability_card_project(self) -> str:
        """The project stamped on a signed capability card."""
        return self._capability_card_project

    @property
    def capability_card_lifetime_seconds(self) -> float:
        """How long a signed capability card stays valid."""
        return self._capability_card_lifetime_seconds

    def identity_public_key(self) -> str | None:
        """Return the public half carried on a registration, or none."""
        if self._identity_key is None:
            return None
        return public_key_b64(self._identity_key)

    def require_message_auth(self) -> None:
        """Refuse an attachment request that has no per-message key."""
        if self._message_auth_key is None:
            raise ValueError("attachment requests require a per-message key")

    def apply_to_envelope(
        self,
        message: dict[str, Any],
        *,
        msg_type: str,
        sign_identity: bool,
        frame_session: ClientFrameSession | None,
    ) -> dict[str, Any]:
        """Sign ``message`` exactly as the send path used to, advancing one counter."""
        if self._message_auth_key is not None and msg_type in DEFAULT_SIGNED_MESSAGE_TYPES:
            message.setdefault("idem_key", secrets.token_urlsafe(18))
            self._message_auth_sequence = next_message_auth_sequence(self._message_auth_sequence)
            message = sign_frame(
                message,
                key=self._message_auth_key,
                nonce=secrets.token_urlsafe(18),
                sequence=self._message_auth_sequence,
            )
        if not sign_identity and frame_session is not None and self._identity_key is not None:
            message = sign_outbound(
                message,
                frame_session,
                key_id=self._identity_key_id,
                private_key=self._identity_key,
            )
        if sign_identity and self._identity_key is not None:
            self._identity_sequence += 1
            message = sign_registration(
                message,
                private_key=self._identity_key,
                key_id=self._identity_key_id,
                nonce=secrets.token_urlsafe(18),
                sequence=self._identity_sequence,
            )
        return message

    def signed_capability_fields(
        self,
        *,
        description: str,
        skills: tuple[str, ...] | list[str],
        task_classes: tuple[str, ...] | list[str],
        model: str,
        manifest_digest: str,
        contracts: Any,
        meta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Sign one capability card and return the fields the advertisement carries."""
        card = normalized_capability_card(
            self._name,
            description=description,
            skills=skills,
            task_classes=task_classes,
            model=model,
            project=self._capability_card_project,
            manifest_digest=manifest_digest,
            contracts=contracts,
            meta=meta,
        )
        card_key = self._capability_card_key
        if card_key is None:
            raise ValueError("a signed capability card requires a key")
        self._capability_card_sequence += 1
        card = sign_capability_card(
            card,
            key_id=self._capability_card_key_id,
            private_key=card_key,
            sequence=self._capability_card_sequence,
            lifetime_seconds=self._capability_card_lifetime_seconds,
        )
        return {field: card[field] for field in _CARD_FIELDS if card.get(field)}
