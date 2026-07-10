# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stable boundary projections for typed errors
"""Map stable taxonomy codes to HTTP statuses and CLI exit codes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

from synapse_channel.core.errors import error_code

CLI_OPERATIONAL_FAILURE: Final = 1
"""A configured operation failed at runtime."""

CLI_USAGE_ERROR: Final = 2
"""Caller input or local configuration is invalid."""

CLI_ACCESS_DENIED: Final = 3
"""A deny-by-default policy refused the requested operation."""

_HTTP_STATUS_BY_CODE: Final[dict[str, HTTPStatus]] = {
    "a2a": HTTPStatus.INTERNAL_SERVER_ERROR,
    "a2a_conflict": HTTPStatus.CONFLICT,
    "a2a_not_found": HTTPStatus.NOT_FOUND,
    "a2a_quota": HTTPStatus.TOO_MANY_REQUESTS,
    "a2a_store": HTTPStatus.INTERNAL_SERVER_ERROR,
    "a2a_validation": HTTPStatus.BAD_REQUEST,
}

_CLI_EXIT_BY_CODE: Final[dict[str, int]] = {
    "mcp_access": CLI_ACCESS_DENIED,
    "mcp_config": CLI_USAGE_ERROR,
    "mcp_tool": CLI_OPERATIONAL_FAILURE,
}


def http_status_for_error(
    exc: BaseException,
    *,
    default: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR,
) -> HTTPStatus:
    """Return the HTTP status assigned to ``exc``'s stable taxonomy code."""
    return _HTTP_STATUS_BY_CODE.get(error_code(exc), default)


def http_error_boundary(
    exc: BaseException,
    default: HTTPStatus,
    default_title: str,
) -> tuple[HTTPStatus, str, str]:
    """Return a mapped status, consistent title, and safe public detail."""
    status = http_status_for_error(exc, default=default)
    title = default_title if status == default else status.phrase
    detail = str(exc) if status < HTTPStatus.INTERNAL_SERVER_ERROR else ""
    return status, title, detail


def cli_exit_code_for_error(
    exc: BaseException,
    *,
    default: int = CLI_OPERATIONAL_FAILURE,
) -> int:
    """Return the CLI exit code assigned to ``exc``'s stable taxonomy code."""
    return _CLI_EXIT_BY_CODE.get(error_code(exc), default)
