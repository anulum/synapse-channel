# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity namespace shared by handlers and ACL enforcement
"""Resolve identity namespaces without importing handlers or enforcement."""

from __future__ import annotations


def project_of(subject: str) -> str:
    """Return the prefix before an identity's first slash, or an empty string.

    ``SYNAPSE-CHANNEL/claude-e57b`` resolves to ``SYNAPSE-CHANNEL``; a bare
    name has no namespace. Preserve the ACL helper's coercion and spelling.
    """
    name = str(subject or "")
    return name.split("/", 1)[0] if "/" in name else ""
