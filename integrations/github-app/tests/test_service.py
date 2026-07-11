# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — real signed-webhook to Check Run integration tests
"""Exercise the complete hosting-neutral service against a real HTTP boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import jwt
import pytest

from crypto_material import rsa_pem_pair
from http_server import ResponseSpec, serve_api
from payloads import encoded_payload, pull_request_record, signed_headers
from synapse_github_app.errors import AuthenticationError
from synapse_github_app.github_api import GitHubApi
from synapse_github_app.models import PullRequestSeed, Repository
from synapse_github_app.service import GitHubAppService

SECRET = b"service-webhook-secret"
NOW = datetime(2026, 7, 11, 14, 0, tzinfo=timezone.utc)
PULLS_PATH = "/repos/anulum/synapse-channel/pulls?state=open&per_page=100&page=1"


def _files_path(number: int) -> str:
    return f"/repos/anulum/synapse-channel/pulls/{number}/files?per_page=100&page=1"


def _service(api: GitHubApi, private_pem: bytes) -> GitHubAppService:
    return GitHubAppService(
        api=api,
        app_issuer="Iv1.synapse-client",
        private_key_pem=private_pem,
        webhook_secret=SECRET,
    )


def test_signed_webhook_reaches_real_api_and_creates_neutral_conflict_check() -> None:
    private_pem, public_pem = rsa_pem_pair()
    plans = {
        ("POST", "/app/installations/42/access_tokens"): [
            ResponseSpec(body={"token": "installation-token", "expires_at": "2026-07-11T15:00:00Z"})
        ],
        ("GET", PULLS_PATH): [
            ResponseSpec(
                body=[
                    pull_request_record(7, head_ref="feature/current"),
                    pull_request_record(9, head_ref="feature/other"),
                ]
            )
        ],
        ("GET", _files_path(7)): [
            ResponseSpec(body=[{"filename": "src/shared.py"}, {"filename": "README.md"}])
        ],
        ("GET", _files_path(9)): [
            ResponseSpec(body=[{"filename": "src/shared.py"}, {"filename": "docs/guide.md"}])
        ],
        ("POST", "/repos/anulum/synapse-channel/check-runs"): [ResponseSpec(body={"id": 771})],
    }
    body = encoded_payload(number=7, head_ref="feature/current")
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        result = _service(api, private_pem).handle(
            headers=signed_headers(body, SECRET),
            body=body,
            now=NOW,
        )

    assert result.action == "check_created"
    assert result.check_run_id == 771
    assert result.report is not None
    assert result.report.notices[0].other_number == 9
    token_request = server.requests[0]
    app_jwt = token_request.headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(
        app_jwt,
        public_pem,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "Iv1.synapse-client"
    check_body = json.loads(server.requests[-1].body)
    assert check_body["head_sha"] == "0000000000000000000000000000000000000007"
    assert check_body["conclusion"] == "neutral"
    assert "PR #9" in check_body["output"]["summary"]
    assert "src/shared.py" in check_body["output"]["summary"]


def test_event_missing_from_open_inventory_is_still_evaluated_at_event_head() -> None:
    private_pem, _ = rsa_pem_pair()
    plans = {
        ("POST", "/app/installations/42/access_tokens"): [
            ResponseSpec(body={"token": "installation-token", "expires_at": "2026-07-11T15:00:00Z"})
        ],
        ("GET", PULLS_PATH): [ResponseSpec(body=[pull_request_record(9)])],
        ("GET", _files_path(7)): [ResponseSpec(body=[{"filename": "src/current.py"}])],
        ("GET", _files_path(9)): [ResponseSpec(body=[{"filename": "src/other.py"}])],
        ("POST", "/repos/anulum/synapse-channel/check-runs"): [ResponseSpec(body={"id": 772})],
    }
    body = encoded_payload(number=7)
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        result = _service(api, private_pem).handle(
            headers=signed_headers(body, SECRET),
            body=body,
            now=NOW,
        )
    assert result.check_run_id == 772
    assert result.report is not None and result.report.current_number == 7


def test_event_pr_replaces_last_inventory_item_at_evaluation_bound() -> None:
    pull_records = [pull_request_record(number) for number in range(1, 101)]
    plans = {
        ("GET", PULLS_PATH): [ResponseSpec(body=pull_records)],
        **{("GET", _files_path(number)): [ResponseSpec(body=[])] for number in range(1, 100)},
        ("GET", _files_path(200)): [ResponseSpec(body=[])],
    }
    current = PullRequestSeed.from_api(pull_request_record(200))
    repository = Repository(owner="anulum", name="synapse-channel")

    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        snapshots, truncated = _service(api, b"unused")._snapshots(
            repository_token="installation-token",
            repository=repository,
            current=current,
        )

    assert truncated is True
    assert tuple(snapshot.number for snapshot in snapshots) == (*range(1, 100), 200)
    assert _files_path(100) not in {request.path for request in server.requests}


def test_ignored_event_does_not_authenticate_or_call_api() -> None:
    private_pem, _ = rsa_pem_pair()
    body = encoded_payload()
    with serve_api({}) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        result = _service(api, private_pem).handle(
            headers=signed_headers(body, SECRET, event="push"),
            body=body,
            now=NOW,
        )
    assert result.action == "ignored"
    assert result.check_run_id is None and result.report is None
    assert server.requests == []


def test_expired_installation_token_stops_before_repository_reads() -> None:
    private_pem, _ = rsa_pem_pair()
    plans = {
        ("POST", "/app/installations/42/access_tokens"): [
            ResponseSpec(body={"token": "expired", "expires_at": "2026-07-11T13:59:59Z"})
        ]
    }
    body = encoded_payload()
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        with pytest.raises(AuthenticationError, match="expired"):
            _service(api, private_pem).handle(
                headers=signed_headers(body, SECRET),
                body=body,
                now=NOW,
            )
    assert len(server.requests) == 1
