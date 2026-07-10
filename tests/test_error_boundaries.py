# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stable error-boundary projection tests

from __future__ import annotations

from http import HTTPStatus

import pytest

from synapse_channel.a2a_errors import (
    A2AConflictError,
    A2AError,
    A2ANotFoundError,
    A2AQuotaError,
    A2AStoreError,
    A2AValidationError,
)
from synapse_channel.core.error_boundaries import (
    CLI_ACCESS_DENIED,
    CLI_OPERATIONAL_FAILURE,
    CLI_USAGE_ERROR,
    cli_exit_code_for_error,
    http_error_boundary,
    http_status_for_error,
)
from synapse_channel.core.mcp_outbound import McpAccessError, McpConfigError, McpToolError


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (A2AError("base"), HTTPStatus.INTERNAL_SERVER_ERROR),
        (A2AValidationError("bad"), HTTPStatus.BAD_REQUEST),
        (A2AConflictError("exists"), HTTPStatus.CONFLICT),
        (A2ANotFoundError("missing"), HTTPStatus.NOT_FOUND),
        (A2AQuotaError("full"), HTTPStatus.TOO_MANY_REQUESTS),
        (A2AStoreError("broken"), HTTPStatus.INTERNAL_SERVER_ERROR),
    ],
)
def test_http_status_uses_stable_a2a_error_codes(
    error: BaseException,
    expected: HTTPStatus,
) -> None:
    assert http_status_for_error(error) == expected


def test_http_boundary_preserves_default_title_only_for_the_default_status() -> None:
    assert http_error_boundary(ValueError("foreign"), HTTPStatus.BAD_REQUEST, "Invalid") == (
        HTTPStatus.BAD_REQUEST,
        "Invalid",
    )
    assert http_error_boundary(A2AConflictError("exists"), HTTPStatus.BAD_REQUEST, "Invalid") == (
        HTTPStatus.CONFLICT,
        "Conflict",
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (McpConfigError("config"), CLI_USAGE_ERROR),
        (McpAccessError("denied"), CLI_ACCESS_DENIED),
        (McpToolError("failed"), CLI_OPERATIONAL_FAILURE),
    ],
)
def test_cli_exit_code_uses_stable_mcp_error_codes(
    error: BaseException,
    expected: int,
) -> None:
    assert cli_exit_code_for_error(error) == expected


def test_boundary_helpers_use_caller_defaults_for_foreign_errors() -> None:
    foreign = RuntimeError("foreign")
    assert http_status_for_error(foreign, default=HTTPStatus.BAD_GATEWAY) == HTTPStatus.BAD_GATEWAY
    assert cli_exit_code_for_error(foreign, default=7) == 7
