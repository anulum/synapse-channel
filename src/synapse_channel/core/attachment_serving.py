# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — source-owned recipient permissions for attachment reads
"""Load exact recipient permissions afresh before each cross-hub content read."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.attachment_store import AttachmentError, AttachmentStore
from synapse_channel.core.hub_address import is_valid_hub_id
from synapse_channel.core.secret_files import SecretFileError, read_secret_file

MAX_ATTACHMENT_GRANTS_BYTES = 65_536
"""Maximum owner policy size in bytes."""

_FIELDS = {"recipient_hub", "scope", "digest", "expires_at"}
_REFUSAL = "invalid attachment recipient policy"


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AttachmentError(_REFUSAL)
        result[key] = value
    return result


@dataclass(frozen=True)
class AttachmentRecipientGrant:
    """Permit one verified recipient hub to read one object until its expiry."""

    recipient_hub: str
    scope: str
    digest: str
    expires_at: float


@dataclass(frozen=True)
class AttachmentServingPolicy:
    """Read an owner-only policy on every request, including requests on live sockets.

    Parameters
    ----------
    path : pathlib.Path
        A version-one JSON document with an exact ``grants`` list. Atomic replacement
        or removal revokes permissions for the next request without a hub restart.
    clock : callable
        POSIX time used for grant expiry and source content expiry.
    """

    path: Path
    clock: Callable[[], float] = time.time

    def load(self) -> tuple[AttachmentRecipientGrant, ...]:
        """Validate the entire bounded policy and return its exact object permissions.

        Raises
        ------
        AttachmentError
            When the file, JSON structure, identifiers or finite expiry are invalid.
        """
        try:
            raw = read_secret_file(
                self.path,
                flag="--attachment-recipient-policy",
                require_single_link=True,
                limit=MAX_ATTACHMENT_GRANTS_BYTES,
            )
            document: object = json.loads(raw, object_pairs_hook=_object)
            if (
                not isinstance(document, dict)
                or set(document) != {"version", "grants"}
                or type(document["version"]) is not int
                or document["version"] != 1
                or not isinstance(document["grants"], list)
                or len(document["grants"]) > 256
            ):
                raise AttachmentError(_REFUSAL)
            grants: list[AttachmentRecipientGrant] = []
            identities: set[tuple[str, str, str]] = set()
            for item in document["grants"]:
                if not isinstance(item, dict) or set(item) != _FIELDS:
                    raise AttachmentError(_REFUSAL)
                recipient, scope, digest, expiry = (
                    item["recipient_hub"],
                    item["scope"],
                    item["digest"],
                    item["expires_at"],
                )
                if (
                    not isinstance(recipient, str)
                    or not is_valid_hub_id(recipient)
                    or not isinstance(scope, str)
                    or not isinstance(digest, str)
                    or isinstance(expiry, bool)
                    or not isinstance(expiry, (int, float))
                    or not math.isfinite(expiry)
                    or expiry <= 0
                ):
                    raise AttachmentError(_REFUSAL)
                AttachmentStore.validate_scope(scope)
                AttachmentStore.validate_digest(digest)
                key = recipient, scope, digest
                if key in identities:
                    raise AttachmentError(_REFUSAL)
                identities.add(key)
                grants.append(AttachmentRecipientGrant(recipient, scope, digest, float(expiry)))
            return tuple(grants)
        except (SecretFileError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise AttachmentError(_REFUSAL) from exc

    def allows(self, recipient_hub: str, scope: str, digest: str) -> bool:
        """Permit an exact recipient/object tuple only while its policy remains valid."""
        try:
            grants = self.load()
        except AttachmentError:
            return False
        now = self.clock()
        return math.isfinite(now) and any(
            (grant.recipient_hub, grant.scope, grant.digest) == (recipient_hub, scope, digest)
            and now < grant.expires_at
            for grant in grants
        )
