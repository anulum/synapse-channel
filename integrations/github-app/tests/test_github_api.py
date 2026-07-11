# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — real REST transport contract tests
"""Exercise the production urllib client against a real local HTTP server."""

from __future__ import annotations

import json

import pytest

from http_server import ResponseSpec, serve_api
from payloads import pull_request_record
from synapse_github_app.checks import CheckRunRequest
from synapse_github_app.errors import GitHubApiError, PayloadError
from synapse_github_app.github_api import MAX_RESPONSE_BYTES, GitHubApi
from synapse_github_app.models import Repository

REPOSITORY = Repository("anulum", "synapse-channel")
PULLS_PATH = "/repos/anulum/synapse-channel/pulls?state=open&per_page=100&page=1"


def _files_path(number: int, page: int = 1) -> str:
    return f"/repos/anulum/synapse-channel/pulls/{number}/files?per_page=100&page={page}"


def _check() -> CheckRunRequest:
    return CheckRunRequest(
        head_sha="7" * 40,
        external_id="synapse:pr:7:delivery:x",
        title="No file-scope overlap observed",
        summary="Advisory only.",
    )


def test_client_exercises_token_pull_file_and_check_endpoints() -> None:
    plans = {
        ("POST", "/app/installations/42/access_tokens"): [
            ResponseSpec(
                body={"token": "opaque-installation-token", "expires_at": "2026-07-11T15:00:00Z"}
            )
        ],
        ("GET", PULLS_PATH): [ResponseSpec(body=[pull_request_record(7)])],
        ("GET", _files_path(7)): [
            ResponseSpec(body=[{"filename": "src/z.py"}, {"filename": "src/a.py"}])
        ],
        ("POST", "/repos/anulum/synapse-channel/check-runs"): [ResponseSpec(body={"id": 991})],
    }
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        installation = api.create_installation_token(42, app_jwt="signed-app-jwt")
        pulls = api.list_open_pull_requests(REPOSITORY, token=installation.value)
        files = api.list_pull_files(REPOSITORY, 7, token=installation.value)
        check_id = api.create_check_run(REPOSITORY, token=installation.value, check=_check())

    assert installation.value == "opaque-installation-token"
    assert [pull.number for pull in pulls.items] == [7]
    assert pulls.truncated is False
    assert files.paths == ("src/a.py", "src/z.py")
    assert files.truncated is False
    assert check_id == 991
    assert [item.method for item in server.requests] == ["POST", "GET", "GET", "POST"]
    assert server.requests[0].headers["Authorization"] == "Bearer signed-app-jwt"
    token_body = json.loads(server.requests[0].body)
    assert token_body == {"permissions": {"checks": "write", "pull_requests": "read"}}
    assert server.requests[-1].headers["Authorization"] == "Bearer opaque-installation-token"
    check_body = json.loads(server.requests[-1].body)
    assert check_body["conclusion"] == "neutral"


def test_pull_and_file_pagination_report_conservative_completeness() -> None:
    hundred_pulls = [pull_request_record(number) for number in range(1, 101)]
    first_files = [{"filename": f"src/file-{index:03d}.py"} for index in range(100)]
    plans = {
        ("GET", PULLS_PATH): [ResponseSpec(body=hundred_pulls)],
        ("GET", _files_path(7, 1)): [ResponseSpec(body=first_files)],
        ("GET", _files_path(7, 2)): [ResponseSpec(body=[{"filename": "src/final.py"}])],
    }
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        pulls = api.list_open_pull_requests(REPOSITORY, token="token")
        files = api.list_pull_files(REPOSITORY, 7, token="token")

    assert pulls.truncated is True
    assert len(files.paths) == 101
    assert files.truncated is False


def test_file_inventory_marks_the_three_thousand_path_ceiling() -> None:
    plans: dict[tuple[str, str], list[ResponseSpec]] = {}
    for page in range(1, 31):
        start = (page - 1) * 100
        records = [{"filename": f"src/file-{index:04d}.py"} for index in range(start, start + 100)]
        plans[("GET", _files_path(7, page))] = [ResponseSpec(body=records)]
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        files = api.list_pull_files(REPOSITORY, 7, token="token")

    assert len(files.paths) == 3000
    assert files.truncated is True
    assert len(server.requests) == 30


@pytest.mark.parametrize(
    ("url", "allow_loopback"),
    [
        ("api.github.com", False),
        ("https://user:secret@api.github.com", False),
        ("https://api.github.com?token=x", False),
        ("https://api.github.com:invalid", False),
        ("https://api.github.com/white space", False),
        ("http://api.github.com", True),
        ("http://127.0.0.1:1234", False),
    ],
)
def test_api_origin_refuses_unsafe_configuration(url: str, allow_loopback: bool) -> None:
    with pytest.raises(GitHubApiError):
        GitHubApi(api_url=url, allow_insecure_loopback=allow_loopback)


@pytest.mark.parametrize("timeout", [0.0, -1.0, 120.1])
def test_api_timeout_is_bounded(timeout: float) -> None:
    with pytest.raises(GitHubApiError, match="timeout"):
        GitHubApi(timeout_seconds=timeout)


def test_redirect_http_error_invalid_json_and_response_bounds_are_fail_visible() -> None:
    cases = [
        ResponseSpec(status=302, headers={"Location": "/elsewhere"}),
        ResponseSpec(status=503, body={"message": "secret upstream detail"}),
        ResponseSpec(body=b"not-json"),
        ResponseSpec(body=b'{"value":"\xff"}'),
        ResponseSpec(body=b"{}", headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)}),
        ResponseSpec(body=b"x" * (MAX_RESPONSE_BYTES + 1), add_content_length=False),
        ResponseSpec(body=b"{}", headers={"Content-Length": "invalid"}),
    ]
    plans = {("GET", PULLS_PATH): cases}
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        for _ in cases:
            with pytest.raises(GitHubApiError):
                api.list_open_pull_requests(REPOSITORY, token="token")


def test_public_methods_reject_invalid_ids_tokens_and_response_shapes() -> None:
    plans = {
        ("POST", "/app/installations/42/access_tokens"): [ResponseSpec(body={"token": "bad"})],
        ("GET", PULLS_PATH): [
            ResponseSpec(body={}),
            ResponseSpec(body=[pull_request_record(7), pull_request_record(7)]),
            ResponseSpec(body=[{"number": 1}]),
        ],
        ("GET", _files_path(7)): [ResponseSpec(body=["bad-record"])],
        ("POST", "/repos/anulum/synapse-channel/check-runs"): [
            ResponseSpec(body=[]),
            ResponseSpec(body={"id": True}),
        ],
    }
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        with pytest.raises(GitHubApiError, match="installation id"):
            api.create_installation_token(0, app_jwt="jwt")
        with pytest.raises(GitHubApiError, match="invalid installation token"):
            api.create_installation_token(42, app_jwt="jwt")
        with pytest.raises(GitHubApiError, match="token"):
            api.list_open_pull_requests(REPOSITORY, token="")
        with pytest.raises(PayloadError, match="array"):
            api.list_open_pull_requests(REPOSITORY, token="token")
        with pytest.raises(GitHubApiError, match="duplicate"):
            api.list_open_pull_requests(REPOSITORY, token="token")
        with pytest.raises(PayloadError):
            api.list_open_pull_requests(REPOSITORY, token="token")
        with pytest.raises(GitHubApiError, match="number"):
            api.list_pull_files(REPOSITORY, 0, token="token")
        with pytest.raises(GitHubApiError, match="file record"):
            api.list_pull_files(REPOSITORY, 7, token="token")
        with pytest.raises(GitHubApiError, match="invalid Check Run"):
            api.create_check_run(REPOSITORY, token="token", check=_check())
        with pytest.raises(GitHubApiError, match="invalid Check Run id"):
            api.create_check_run(REPOSITORY, token="token", check=_check())


def test_api_error_never_includes_upstream_body_or_token() -> None:
    plans = {("GET", PULLS_PATH): [ResponseSpec(status=403, body={"message": "token-secret"})]}
    with serve_api(plans) as server:
        api = GitHubApi(api_url=server.url, allow_insecure_loopback=True)
        with pytest.raises(GitHubApiError) as raised:
            api.list_open_pull_requests(REPOSITORY, token="actual-token-secret")
    rendered = str(raised.value)
    assert "token-secret" not in rendered
    assert "actual-token-secret" not in rendered


def test_loopback_ipv6_and_localhost_origins_are_explicitly_accepted() -> None:
    for url in (
        "https://api.github.com/api/v3/",
        "http://localhost:1234",
        "http://[::1]:1234",
    ):
        api = GitHubApi(api_url=url, allow_insecure_loopback=True)
        assert isinstance(api, GitHubApi)
