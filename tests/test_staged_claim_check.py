# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — staged claim decision tests

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.claim_state import ClaimStateError
from synapse_channel.git import staged_claim_check
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.semantic_diff import SemanticDiffRecord
from synapse_channel.git.semantic_scope import semantic_scope_path
from synapse_channel.git.staged_claim_check import run_staged_claim_check


def _runner(
    root: Path, raw: str, config: dict[str, str] | None = None
) -> Callable[[list[str]], str]:
    values = {"synapse.identity": "agent", **(config or {})}

    def run(args: list[str]) -> str:
        if args[0] == "diff":
            return raw
        if "ls-files" in args:
            return ""
        if "core.ignorecase" in args:
            return "false"
        if args == ["rev-parse", "--show-toplevel"]:
            return str(root)
        if args == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            return "main"
        if args == [
            "config",
            "--local",
            "--type=bool",
            "--get",
            "--default",
            "false",
            "extensions.worktreeConfig",
        ]:
            return "false"
        if args[:5] == ["config", "--local", "--get", "--default", ""]:
            return values.get(args[5], "")
        raise AssertionError(args)

    return run


def _claim(
    root: Path,
    *,
    owner: str = "agent",
    status: str = "claimed",
    paths: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": "task",
        "owner": owner,
        "status": status,
        "worktree": str(root.resolve()),
        "paths": ["src"] if paths is None else paths,
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


def _semantic_record(
    source: str,
    *,
    claim_paths: tuple[str, ...],
    narrowed: bool = True,
) -> SemanticDiffRecord:
    return SemanticDiffRecord(
        status="M",
        source=source,
        old_source=source,
        language="python",
        symbols=("run",) if narrowed else (),
        semantic_scopes=claim_paths if narrowed else (),
        claim_paths=claim_paths,
        narrowed=narrowed,
        reason="test evidence",
    )


def test_production_semantic_resolver_reads_the_staged_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = (_semantic_record("src/a.py", claim_paths=("src/a.py",)),)
    captured: dict[str, object] = {}

    def resolve(root: Path, *, paths: Sequence[str]) -> tuple[SemanticDiffRecord, ...]:
        captured["root"] = root
        captured["paths"] = paths
        return expected

    monkeypatch.setattr(staged_claim_check, "resolve_staged_diff", resolve)

    assert (
        staged_claim_check._resolve_staged_semantics(
            tmp_path,
            ("src/a.py",),
        )
        == expected
    )
    assert captured == {"root": tmp_path, "paths": ("src/a.py",)}


@pytest.mark.asyncio
async def test_semantic_claim_allows_only_the_proven_staged_symbol(
    tmp_path: Path,
) -> None:
    source = "src/a.py"
    owned = semantic_scope_path(source, "owned")
    other = semantic_scope_path(source, "other")

    async def run_with(record: SemanticDiffRecord) -> Any:
        return await run_staged_claim_check(
            runner=_runner(tmp_path, f"M\0{source}\0"),
            environment={},
            state_fetcher=_fetch({"active_claims": [_claim(tmp_path, paths=[owned])]}),
            semantic_resolver=lambda _root, _paths: (record,),
        )

    allowed = await run_with(_semantic_record(source, claim_paths=(owned,)))
    assert allowed.allowed
    assert allowed.paths == (source,)

    wrong_symbol = await run_with(_semantic_record(source, claim_paths=(other,)))
    assert not wrong_symbol.allowed
    assert "no covering claim" in wrong_symbol.reason

    widened = await run_with(_semantic_record(source, claim_paths=(source,), narrowed=False))
    assert not widened.allowed
    assert "no covering claim" in widened.reason


@pytest.mark.asyncio
async def test_semantic_resolver_failure_denies_instead_of_falling_back(
    tmp_path: Path,
) -> None:
    source = "src/a.py"
    scope = semantic_scope_path(source, "run")

    def unavailable(
        _root: Path,
        _paths: Sequence[str],
    ) -> tuple[SemanticDiffRecord, ...]:
        raise RuntimeError("semantic parser unavailable")

    result = await run_staged_claim_check(
        runner=_runner(tmp_path, f"M\0{source}\0"),
        environment={},
        state_fetcher=_fetch({"active_claims": [_claim(tmp_path, paths=[scope])]}),
        semantic_resolver=unavailable,
    )
    assert not result.allowed
    assert result.reason == "semantic parser unavailable"


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
    assert result.allowed is False
    assert "\n" not in result.reason
    assert "path" in result.reason.lower()


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
    token_file.chmod(0o600)
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
async def test_world_readable_token_file_denies_without_leaking_secret(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("super-secret\n", encoding="utf-8")
    token_file.chmod(0o644)
    result = await run_staged_claim_check(
        runner=_runner(tmp_path, "M\0src/a.py\0"),
        token_file=str(token_file),
        environment={},
        state_fetcher=_fetch({"active_claims": [_claim(tmp_path)]}),
    )
    assert result.allowed is False
    assert "super-secret" not in result.reason
    assert "chmod 600" in result.reason


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
        token_file.chmod(0o600)
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
