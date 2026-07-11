# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — public managed-layer API
"""Hosting-neutral GitHub App adapter for advisory SYNAPSE conflict checks."""

from synapse_github_app.errors import (
    AuthenticationError,
    GitHubApiError,
    GitHubAppError,
    IncompleteAnalysisError,
    ManifestError,
    PayloadError,
    WebhookError,
)
from synapse_github_app.github_api import GitHubApi
from synapse_github_app.manifest import build_manifest, render_manifest
from synapse_github_app.service import GitHubAppService, ServiceResult

__all__ = [
    "AuthenticationError",
    "GitHubApi",
    "GitHubApiError",
    "GitHubAppError",
    "GitHubAppService",
    "IncompleteAnalysisError",
    "ManifestError",
    "PayloadError",
    "ServiceResult",
    "WebhookError",
    "build_manifest",
    "render_manifest",
]
