# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP identity grants
"""Load bounded operator grants for authenticated remote MCP principals."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from synapse_channel.core.secret_files import SecretFileError, read_secret_file

READ_TOOLS = frozenset(
    {"synapse_board", "synapse_state", "synapse_manifest", "synapse_directory", "synapse_status"}
)
MUTATION_TOOLS = frozenset(
    {
        "synapse_claim",
        "synapse_release",
        "synapse_send",
        "synapse_handoff",
        "synapse_task_declare",
        "synapse_task_update",
    }
)


class HttpConfigError(ValueError):
    """Reject an invalid grant file without including its content in diagnostics."""


class RemoteSeat(BaseModel):
    """Bind one subject to a pre-enrolled hub identity and explicit task namespace.

    Attributes
    ----------
    seat : str
        Exact project-qualified hub identity; never selected by the client.
    identity_key_file, identity_key_id : str
        Operator-provisioned signing key and its hub trust-bundle identifier.
    task_prefix : str
        Exclusive task namespace, including its terminating slash.
    tools : frozenset[str]
        Permitted remote actions. Mutations require an explicit grant.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    seat: str = Field(min_length=3, max_length=256)
    identity_key_file: str = Field(min_length=1, max_length=4096)
    identity_key_id: str = Field(min_length=1, max_length=256)
    task_prefix: str = Field(min_length=2, max_length=128)
    tools: frozenset[str] = READ_TOOLS

    @model_validator(mode="after")
    def validate_authority(self) -> RemoteSeat:
        """Reject ambiguous task namespaces and unsupported remote operations."""
        if not self.task_prefix.endswith("/") or self.task_prefix.startswith("/"):
            raise ValueError("task namespace must end in a slash")
        if not self.tools <= READ_TOOLS | MUTATION_TOOLS:
            raise ValueError("unsupported remote tool grant")
        return self


class RemoteSubject(BaseModel):
    """Hold operator authority and revocation state for an issuer's subject.

    Attributes
    ----------
    projects : dict[str, RemoteSeat]
        Project-specific identities and action grants.
    enabled : bool
        Whether this subject may authenticate now.
    revoked_before : int
        Tokens issued at or before this UTC epoch are revoked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    projects: dict[str, RemoteSeat] = Field(min_length=1, max_length=16)
    enabled: bool = True
    revoked_before: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_projects(self) -> RemoteSubject:
        """Require each provisioned seat and task namespace to match its project."""
        for project, grant in self.projects.items():
            if not project or "/" in project or project.strip() != project:
                raise ValueError("invalid project namespace")
            if not grant.seat.startswith(project + "/") or grant.seat == project + "/":
                raise ValueError("seat is outside its granted project")
            if grant.task_prefix != project + "/":
                raise ValueError("task namespace is outside its granted project")
        return self


class HttpAuthConfig(BaseModel):
    """Describe the trusted issuer, resource audience and bounded principal grants.

    Attributes
    ----------
    issuer, resource : str
        Exact HTTPS issuer and MCP resource audience, without query or fragment.
    public_keys : dict[str, str]
        Trusted Ed25519 public PEM keys indexed by issuer key identifier.
    subjects : dict[str, RemoteSubject]
        Explicit subject grants; token claims cannot create entries.
    revoked_token_ids : frozenset[str]
        Revoked issuer token identifiers, checked on every request.
    max_token_age_seconds : int
        Maximum lifetime and age accepted for an access token.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    issuer: str = Field(max_length=2048)
    resource: str = Field(max_length=2048)
    public_keys: dict[str, str] = Field(min_length=1, max_length=8)
    subjects: dict[str, RemoteSubject] = Field(min_length=1, max_length=32)
    revoked_token_ids: frozenset[str] = Field(default=frozenset(), max_length=4096)
    max_token_age_seconds: int = Field(default=900, ge=30, le=3600)

    @model_validator(mode="after")
    def validate_trust(self) -> HttpAuthConfig:
        """Validate HTTPS audiences, asymmetric keys and exclusive seat ownership."""
        for value in (self.issuer, self.resource):
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise ValueError("issuer and resource must be uncredentialed HTTPS URLs")
            _ = url.port
        for key_id, pem in self.public_keys.items():
            if not key_id or len(key_id) > 256 or len(pem) > 4096:
                raise ValueError("invalid issuer key")
            if not isinstance(load_pem_public_key(pem.encode("ascii")), Ed25519PublicKey):
                raise ValueError("issuer keys must be Ed25519 public keys")
        seats: set[str] = set()
        for subject, authority in self.subjects.items():
            if not subject or len(subject) > 256:
                raise ValueError("invalid subject")
            for grant in authority.projects.values():
                if grant.seat in seats:
                    raise ValueError("a hub seat cannot belong to multiple subjects")
                seats.add(grant.seat)
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Refuse duplicate JSON keys before validation can discard an authority entry."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HttpConfigError("duplicate remote MCP configuration key")
        result[key] = value
    return result


def load_http_auth_config(path: str | Path) -> HttpAuthConfig:
    """Read current grants from one owner-only, single-link policy file.

    Parameters
    ----------
    path : str or pathlib.Path
        Operator-controlled JSON file, read through the secure secret-file loader.

    Returns
    -------
    HttpAuthConfig
        Validated issuer, audience and grants.

    Raises
    ------
    HttpConfigError
        When the file is inaccessible or invalid. Diagnostics omit file contents.
    """
    try:
        text = read_secret_file(
            path, flag="--http-auth-file", require_single_link=True, limit=65536
        )
        json.loads(text, object_pairs_hook=_unique_object)
        return HttpAuthConfig.model_validate_json(text)
    except (SecretFileError, ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise HttpConfigError("remote MCP authentication configuration is invalid") from exc
