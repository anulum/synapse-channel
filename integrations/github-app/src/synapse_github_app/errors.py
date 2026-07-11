# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — managed-layer error taxonomy
"""Typed, redaction-safe failures at the managed GitHub App boundary."""

from __future__ import annotations


class GitHubAppError(Exception):
    """Base class for expected integration failures."""


class ManifestError(GitHubAppError, ValueError):
    """Raised when a GitHub App manifest URL is unsafe or malformed."""


class PayloadError(GitHubAppError, ValueError):
    """Raised when webhook or REST data violates the typed contract."""


class WebhookError(GitHubAppError, ValueError):
    """Raised when a webhook is unauthenticated, oversized, or malformed."""


class AuthenticationError(GitHubAppError):
    """Raised when App or installation authentication cannot be completed."""


class GitHubApiError(GitHubAppError):
    """Raised when GitHub REST transport or response validation fails."""


class IncompleteAnalysisError(GitHubAppError):
    """Raised when bounded evidence cannot support even an advisory verdict."""
