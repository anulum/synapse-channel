# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — HTTP mutation identity arguments and discovery schemas
"""Translate explicit caller retry keys without changing native business arguments."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from mcp.types import Tool

from synapse_channel.mcp.http_config import MUTATION_TOOLS

_OPERATION_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_OPERATION = re.compile(_OPERATION_PATTERN)
_METADATA_KEY = "synapse/operation-id"


def mutation_arguments(
    arguments: dict[str, Any], metadata: Mapping[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    """Bind argument and legacy metadata keys before dispatching a native mutation.

    Parameters
    ----------
    arguments : dict[str, Any]
        Tool arguments. ``operation_id`` is reserved for the HTTP adapter.
    metadata : Mapping[str, Any] or None
        Request metadata extras, including the legacy ``synapse/operation-id`` key.

    Returns
    -------
    tuple[str, dict[str, Any]]
        Stable operation key and a fresh dictionary of unmodified business arguments.

    Raises
    ------
    PermissionError
        If the key is absent, malformed or different between the two supplied forms.
        No replacement key is generated after an ambiguous operation.
    """
    extras = metadata if metadata is not None else {}
    operation = arguments.get("operation_id", extras.get(_METADATA_KEY))
    if (
        "operation_id" in arguments
        and _METADATA_KEY in extras
        and operation != extras[_METADATA_KEY]
    ):
        raise PermissionError("remote MCP operation identities disagree")
    if not isinstance(operation, str) or _OPERATION.fullmatch(operation) is None:
        raise PermissionError("remote MCP mutation requires operation identity")
    return operation, {key: value for key, value in arguments.items() if key != "operation_id"}


def http_tool_schema(tool: Tool) -> Tool:
    """Advertise an optional HTTP retry key while retaining metadata-only clients.

    Parameters
    ----------
    tool : Tool
        Tool description from the shared MCP registration. It is never mutated.

    Returns
    -------
    Tool
        HTTP mutation schema with a described ``operation_id`` argument, or the
        original schema for a read. The argument stays optional in JSON Schema
        because a legacy metadata key also satisfies the execution requirement.
    """
    if tool.name not in MUTATION_TOOLS:
        return tool
    schema = {
        **tool.input_schema,
        "properties": {
            **tool.input_schema.get("properties", {}),
            "operation_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": "^" + _OPERATION_PATTERN + "$",
                "description": (
                    "Stable caller key for this intent; reuse unchanged for retries. "
                    "Required unless request _meta supplies synapse/operation-id. "
                    "If both are supplied they must match."
                ),
            },
        },
    }
    return tool.model_copy(update={"input_schema": schema})
