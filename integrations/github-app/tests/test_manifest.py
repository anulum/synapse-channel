# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — least-privilege manifest tests
"""Verify deterministic manifest content and URL safety."""

from __future__ import annotations

import json

import pytest

from synapse_github_app.errors import ManifestError
from synapse_github_app.manifest import APP_NAME, build_manifest, canonical_base_url, main


def test_manifest_has_only_stage_two_permissions_and_event() -> None:
    manifest = build_manifest("https://app.example.org/root/")

    assert manifest == {
        "name": APP_NAME,
        "url": "https://app.example.org/root",
        "hook_attributes": {
            "url": "https://app.example.org/root/github/webhook",
            "active": True,
        },
        "redirect_url": "https://app.example.org/root/github/manifest/callback",
        "description": "Advisory cross-PR file-scope conflict checks from SYNAPSE.",
        "public": False,
        "default_permissions": {
            "checks": "write",
            "metadata": "read",
            "pull_requests": "read",
        },
        "default_events": ["pull_request"],
    }


def test_manifest_cli_renders_public_deterministic_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--base-url", "https://app.example.org", "--public"]) == 0

    rendered = capsys.readouterr().out
    assert rendered.endswith("\n")
    assert json.loads(rendered)["public"] is True
    assert rendered.index('"checks"') < rendered.index('"metadata"')


@pytest.mark.parametrize(
    "value",
    [
        "http://app.example.org",
        "https://",
        "https://user:secret@app.example.org",
        "https://app.example.org?token=x",
        "https://app.example.org#fragment",
        "https://app.example.org:invalid",
        "https://app.example.org/white space",
    ],
)
def test_manifest_refuses_unsafe_base_urls(value: str) -> None:
    with pytest.raises(ManifestError):
        canonical_base_url(value)


def test_manifest_cli_reports_invalid_url(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--base-url", "http://insecure.example.org"])
    assert raised.value.code == 2
    assert "must be absolute HTTPS" in capsys.readouterr().err
