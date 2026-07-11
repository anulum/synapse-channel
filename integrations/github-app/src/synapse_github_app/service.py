# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — stateless pull-request webhook orchestration
"""Coordinate one authenticated pull-request event into one neutral check."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from synapse_github_app.auth import create_app_jwt
from synapse_github_app.checks import build_check_run
from synapse_github_app.conflicts import ConflictReport, analyse_conflicts
from synapse_github_app.errors import AuthenticationError
from synapse_github_app.github_api import MAX_OPEN_PULL_REQUESTS, GitHubApi
from synapse_github_app.models import PullRequestSeed, PullRequestSnapshot, Repository
from synapse_github_app.webhook import decode_pull_request


@dataclass(frozen=True)
class ServiceResult:
    """Outcome of one webhook application-service call."""

    action: str
    check_run_id: int | None = None
    report: ConflictReport | None = None


class GitHubAppService:
    """Stateless orchestration across webhook, REST, core conflict, and check seams."""

    def __init__(
        self,
        *,
        api: GitHubApi,
        app_issuer: str,
        private_key_pem: bytes,
        webhook_secret: bytes,
    ) -> None:
        """Capture explicit credentials without reading ambient process state."""
        self._api = api
        self._app_issuer = app_issuer
        self._private_key_pem = private_key_pem
        self._webhook_secret = webhook_secret

    def _snapshots(
        self,
        *,
        repository_token: str,
        repository: Repository,
        current: PullRequestSeed,
    ) -> tuple[tuple[PullRequestSnapshot, ...], bool]:
        inventory = self._api.list_open_pull_requests(repository, token=repository_token)
        seeds = {current.number: current}
        for seed in inventory.items:
            if seed.number == current.number:
                continue
            if len(seeds) >= MAX_OPEN_PULL_REQUESTS:
                break
            seeds[seed.number] = seed
        snapshots: list[PullRequestSnapshot] = []
        for number in sorted(seeds):
            seed = seeds[number]
            files = self._api.list_pull_files(repository, number, token=repository_token)
            snapshots.append(seed.with_paths(files.paths, paths_truncated=files.truncated))
        return tuple(snapshots), inventory.truncated

    def handle(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        now: datetime | None = None,
    ) -> ServiceResult:
        """Process a supported signed webhook or return an ignored outcome."""
        event = decode_pull_request(headers=headers, body=body, secret=self._webhook_secret)
        if event is None:
            return ServiceResult(action="ignored")
        instant = now or datetime.now(timezone.utc)
        app_jwt = create_app_jwt(
            issuer=self._app_issuer,
            private_key_pem=self._private_key_pem,
            now=instant,
        )
        installation = self._api.create_installation_token(
            event.installation_id,
            app_jwt=app_jwt,
        )
        if installation.expires_at <= instant.astimezone(timezone.utc):
            raise AuthenticationError("GitHub returned an expired installation token")
        snapshots, pulls_truncated = self._snapshots(
            repository_token=installation.value,
            repository=event.repository,
            current=event.pull_request,
        )
        current = next(item for item in snapshots if item.number == event.pull_request.number)
        report = analyse_conflicts(
            current,
            snapshots,
            open_pull_requests_truncated=pulls_truncated,
        )
        check = build_check_run(report, delivery_id=event.delivery_id)
        check_id = self._api.create_check_run(
            event.repository,
            token=installation.value,
            check=check,
        )
        return ServiceResult(action="check_created", check_run_id=check_id, report=report)
