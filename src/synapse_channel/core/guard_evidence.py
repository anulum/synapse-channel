# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded wire schema for authenticated guard-denial evidence
"""Validate the content-minimized claim-guard denial wire record."""

from __future__ import annotations

import re
from typing import Any, Final

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.scoping import MAX_DECLARED_PATHS

GUARD_DENIAL_REASON_CODES: Final = frozenset(
    {
        "GUARD_NO_CLAIM",
        "GUARD_NOT_EDITABLE",
        "GUARD_OWNERSHIP_AMBIGUOUS",
        "GUARD_STATE_UNREACHABLE",
        "GUARD_TARGET_INVALID",
    }
)
GUARD_EVIDENCE_PROVIDERS: Final = frozenset(
    {"claude", "codex", "gemini", "grok", "kimi", "opencode", "shell"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GuardEvidenceError(SynapseError, ValueError):
    """A guard-evidence frame is malformed or outside its closed vocabulary."""

    code = "guard_evidence"


def parse_guard_denial(data: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded digest-only client fields accepted into the journal."""
    reason_code = data.get("reason_code")
    provider = data.get("provider")
    path_count = data.get("path_count")
    if not isinstance(reason_code, str) or reason_code not in GUARD_DENIAL_REASON_CODES:
        raise GuardEvidenceError("guard denial reason_code is not recognised")
    if not isinstance(provider, str) or provider not in GUARD_EVIDENCE_PROVIDERS:
        raise GuardEvidenceError("guard denial provider is not recognised")
    if isinstance(path_count, bool) or not isinstance(path_count, int):
        raise GuardEvidenceError("guard denial path_count must be an integer")
    if path_count < 0 or path_count > MAX_DECLARED_PATHS:
        raise GuardEvidenceError("guard denial path_count is outside the supported bound")
    digests: dict[str, str] = {}
    for field in ("actor_sha256", "call_sha256", "scope_sha256"):
        value = data.get(field)
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise GuardEvidenceError(f"guard denial {field} must be lowercase SHA-256")
        digests[field] = value
    return {
        "actor_sha256": digests["actor_sha256"],
        "call_sha256": digests["call_sha256"],
        "decision": "deny",
        "path_count": path_count,
        "provider": provider,
        "reason_code": reason_code,
        "scope_sha256": digests["scope_sha256"],
    }
