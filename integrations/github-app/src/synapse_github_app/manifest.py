# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — least-privilege manifest renderer
"""Render the stage-two GitHub App registration manifest."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

from synapse_github_app.errors import ManifestError

APP_NAME = "Synapse Conflict Advisor"


def canonical_base_url(value: str) -> str:
    """Return one safe HTTPS base URL without query, fragment, or credentials.

    Parameters
    ----------
    value : str
        Candidate public base URL for the future hosted adapter.

    Returns
    -------
    str
        Canonical URL without a trailing slash.

    Raises
    ------
    ManifestError
        If the URL is not an absolute credential-free HTTPS origin/path.
    """
    stripped = value.strip()
    if not stripped.isprintable() or any(char.isspace() for char in stripped):
        raise ManifestError("the App base URL must not contain whitespace or control characters")
    parsed = urlsplit(stripped)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ManifestError("the App base URL must be absolute HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ManifestError("the App base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ManifestError("the App base URL must not contain query or fragment data")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ManifestError("the App base URL contains an invalid port") from exc
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def build_manifest(base_url: str, *, public: bool = False) -> dict[str, object]:
    """Build the least-privilege stage-two GitHub App manifest.

    Parameters
    ----------
    base_url : str
        Public HTTPS base URL of a future host adapter.
    public : bool, optional
        Whether GitHub should make the registration publicly installable.
        Defaults to ``False`` until the owner approves publication.

    Returns
    -------
    dict[str, object]
        JSON-serialisable GitHub App manifest.
    """
    base = canonical_base_url(base_url)
    return {
        "name": APP_NAME,
        "url": base,
        "hook_attributes": {"url": f"{base}/github/webhook", "active": True},
        "redirect_url": f"{base}/github/manifest/callback",
        "description": "Advisory cross-PR file-scope conflict checks from SYNAPSE.",
        "public": public,
        "default_permissions": {
            "checks": "write",
            "metadata": "read",
            "pull_requests": "read",
        },
        "default_events": ["pull_request"],
    }


def render_manifest(base_url: str, *, public: bool = False) -> str:
    """Return a deterministic JSON representation of the App manifest."""
    return json.dumps(build_manifest(base_url, public=public), indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Render a manifest for operator inspection without submitting it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--public",
        action="store_true",
        help="mark the future registration public (default: private)",
    )
    args = parser.parse_args(argv)
    try:
        print(render_manifest(args.base_url, public=args.public), end="")
    except ManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
