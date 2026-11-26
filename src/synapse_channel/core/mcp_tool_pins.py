# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the pinned descriptor of an outbound MCP tool
"""The pinned descriptor of an outbound MCP tool: what is hashed, and how.

An operator who allowlists a tool approves what the server said about it at that moment:
its name, title, description, input and output schemas, annotations and execution hints.
:func:`tool_descriptor_digest` turns that contract into one digest, so a later change to any
of those fields (a "rug pull" or tool-poisoning edit) is detected before the tool is called.

Descriptor profile v1 keeps exactly the wire keys in :data:`PINNED_DESCRIPTOR_FIELDS`, as the
MCP SDK serialises them (``model_dump(by_alias=True, mode="json", exclude_none=True)``), so an
absent field and an explicit ``null`` are the same descriptor. ``icons`` (display only) and
``_meta`` (protocol metadata, never forwarded by SYNAPSE) are left out. The bytes hashed are a
domain prefix followed by the RFC 8785 canonical JSON of that projection
(:func:`~synapse_channel.core.frame_canonical.frame_canonical_json`), which refuses an integer
outside the I-JSON exact range: such a descriptor cannot be pinned, and is never hashed some
other way.
"""

from __future__ import annotations

import hashlib
from typing import Any

from synapse_channel.core.frame_canonical import frame_canonical_json

__all__ = [
    "PINNED_DESCRIPTOR_FIELDS",
    "TOOL_DESCRIPTOR_DOMAIN",
    "tool_descriptor_bytes",
    "tool_descriptor_digest",
    "tool_descriptor_projection",
]

TOOL_DESCRIPTOR_DOMAIN = b"synapse-channel/mcp-tool-descriptor/v1\x00"
"""The prefix that keeps a descriptor digest apart from every other SYNAPSE digest."""

PINNED_DESCRIPTOR_FIELDS: tuple[str, ...] = (
    "name",
    "title",
    "description",
    "inputSchema",
    "outputSchema",
    "annotations",
    "execution",
)
"""The wire keys descriptor profile v1 pins; ``icons`` and ``_meta`` are excluded."""


def tool_descriptor_projection(tool: Any) -> dict[str, Any]:
    """Return the pinned wire fields of one advertised tool.

    Parameters
    ----------
    tool : Any
        An MCP SDK ``Tool`` as ``tools/list`` returned it.

    Returns
    -------
    dict[str, Any]
        The JSON-ready fields of :data:`PINNED_DESCRIPTOR_FIELDS` that the tool carries.
    """
    dumped = tool.model_dump(by_alias=True, mode="json", exclude_none=True)
    return {key: dumped[key] for key in PINNED_DESCRIPTOR_FIELDS if key in dumped}


def tool_descriptor_bytes(tool: Any) -> bytes:
    """Return the canonical bytes a descriptor digest covers, without the domain prefix.

    Parameters
    ----------
    tool : Any
        An MCP SDK ``Tool``.

    Returns
    -------
    bytes
        The RFC 8785 canonical JSON of :func:`tool_descriptor_projection`.

    Raises
    ------
    synapse_channel.core.frame_canonical.FrameCanonicalError
        When the descriptor holds a value canonical JSON refuses, such as an integer outside
        the I-JSON exact range.
    """
    return frame_canonical_json(tool_descriptor_projection(tool))


def tool_descriptor_digest(tool: Any) -> str:
    """Return the lower-case SHA-256 pin of one tool's descriptor.

    Parameters
    ----------
    tool : Any
        An MCP SDK ``Tool``.

    Returns
    -------
    str
        64 lower-case hexadecimal characters over :data:`TOOL_DESCRIPTOR_DOMAIN` and
        :func:`tool_descriptor_bytes`.

    Raises
    ------
    synapse_channel.core.frame_canonical.FrameCanonicalError
        When the descriptor cannot be canonicalised.
    """
    return hashlib.sha256(TOOL_DESCRIPTOR_DOMAIN + tool_descriptor_bytes(tool)).hexdigest()
