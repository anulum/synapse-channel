# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — staged claim decision tests

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.claim_state import ClaimStateError
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.staged_claim_check import run_staged_claim_check


def _runner(
    root: Path, raw: str, config: dict[str, str] | None = None
) -> Callable[[list[str]], str]:
    values = {"synapse.identity": "agent", **(config or {})}

    def run(args: list[str]) -> str:
        if args[0] == "diff":
            return raw
        if args == ["rev-parse", "--show-toplevel"]:
            return str(root)
        if args == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            return "main"
        if args[:5] == ["config", "--local", "--get", "--default", ""]:
            return values.get(args[5], "")
        raise AssertionError(args)

    return run


def _claim(root: Path, *, owner: str = "agent", status: str = "claimed") -> dict[str, Any]:
    return {
        "task_id": "task",
        "owner": owner,
        "status": status,
        "worktree": str(root.resolve()),
        "paths": ["src"],
        "git": {"branch": "main"},
    }


def _fetch(snapshot: dict[str, Any]) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def fetch(**kwargs: Any) -> dict[str, Any]:
        return snapshot

    return fetch


@pytest.mark.asyncio
async def test_empty_index_is_network_and_identity_free_success(tmp_path: Path) -> None:
    async def forbidden(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("network must not be used")

    result = await run_staged_claim_check(
        runner=_runner(tmp_path, ""), environment={}, state_fetcher=forbidden
    )
    assert result.allowed is True
    assert result.paths == ()


@pytest.mark.asyncio
async def test_owned_editable_claim_allows_with_one_state_query(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    async def fetch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"active_claims": [_claim(tmp_path)]}

    result = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0A\0src/b.py\0"),
        environment={},
        state_fetcher=fetch,
    )
    assert result.allowed is True
    assert result.paths == ("src/a.py", "src/b.py")
    assert len(captured) == 1
    assert captured[0]["requester"].startswith("claim-check/")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ([], "no covering claim"),
        ([{"owner": "other", "status": "claimed"}], "another or mixed identity"),
        ([{"owner": "agent", "status": "done"}], "not editable"),
    ],
)
async def test_coverage_failures_are_classified(
    tmp_path: Path, claims: list[dict[str, str]], message: str
) -> None:
    hydrated = [
        {
            **claim,
            "task_id": "task",
            "worktree": str(tmp_path.resolve()),
            "paths": ["src"],
            "git": {"branch": "main"},
        }
        for claim in claims
    ]
    result = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0"),
        environment={},
        state_fetcher=_fetch({"active_claims": hydrated}),
    )
    assert result.allowed is False
    assert message in result.reason


@pytest.mark.asyncio
async def test_diagnostic_escapes_newlines_and_is_bounded(tmp_path: Path) -> None:
    paths = [f"src/path-{index:02d}\nfile.py" for index in range(40)]
    raw = "".join(f"M\0{path}\0" for path in paths)
    result = await run_staged_claim_check(
        runner=_runner(tmp_path, raw),
        environment={},
        state_fetcher=_fetch({"active_claims": []}),
    )
    assert "\\n" in result.reason
    assert "\n" not in result.reason
    assert "(+8 more)" in result.reason


@pytest.mark.asyncio
async def test_diagnostic_character_budget_can_omit_the_first_path(tmp_path: Path) -> None:
    long_path = "src/" + ("x" * 3000)
    result = await run_staged_claim_check(
        runner=_runner(tmp_path, f"M\0{long_path}\0"),
        environment={},
        state_fetcher=_fetch({"active_claims": []}),
    )
    assert result.allowed is False
    assert result.reason == "no covering claim: (+1 more)"


@pytest.mark.asyncio
async def test_token_file_content_is_used_but_not_reported(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("super-secret\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    async def fetch(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"active_claims": [_claim(tmp_path)]}

    result = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0"),
        token_file=str(token_file),
        environment={"SYNAPSE_TOKEN": "fallback"},
        state_fetcher=fetch,
    )
    assert result.allowed is True
    assert captured["token"] == "super-secret"
    assert "super-secret" not in result.reason


@pytest.mark.asyncio
async def test_environment_token_is_the_fileless_fallback(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def fetch(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"active_claims": [_claim(tmp_path)]}

    result = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0"),
        environment={"SYNAPSE_TOKEN": "env-secret"},
        state_fetcher=fetch,
    )
    assert result.allowed is True
    assert captured["token"] == "env-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [None, ""])
async def test_missing_or_empty_token_file_denies(tmp_path: Path, contents: str | None) -> None:
    token_file = tmp_path / "token"
    if contents is not None:
        token_file.write_text(contents, encoding="utf-8")
    result = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0"),
        token_file=str(token_file),
        environment={},
        state_fetcher=_fetch({"active_claims": [_claim(tmp_path)]}),
    )
    assert result.allowed is False
    assert result.reason


@pytest.mark.asyncio
async def test_git_config_state_and_snapshot_errors_deny(tmp_path: Path) -> None:
    no_identity = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0", {"synapse.identity": ""}),
        environment={},
        state_fetcher=_fetch({"active_claims": []}),
    )
    assert no_identity.allowed is False
    assert "No claim identity" in no_identity.reason

    async def state_failure(**kwargs: Any) -> dict[str, Any]:
        raise ClaimStateError("hub unavailable")

    unavailable = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0"),
        environment={},
        state_fetcher=state_failure,
    )
    assert unavailable.allowed is False
    assert unavailable.reason == "hub unavailable"

    malformed = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0"),
        environment={},
        state_fetcher=_fetch({"active_claims": "bad"}),
    )
    assert malformed.allowed is False
    assert "active_claims" in malformed.reason


@pytest.mark.asyncio
async def test_staged_git_failure_denies_without_network(tmp_path: Path) -> None:
    def runner(args: list[str]) -> str:
        raise GitError("index unavailable")

    async def forbidden(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("network must not be used")

    result = await run_staged_claim_check(runner=runner, environment={}, state_fetcher=forbidden)
    assert result.allowed is False
    assert result.paths == ()
    assert result.reason == "index unavailable"
