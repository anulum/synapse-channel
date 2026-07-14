# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for per-worktree auto-release identity resolution

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.hook_release_identity import (
    ReleaseIdentity,
    _read_token,
    resolve_release_identity,
)

_IDENTITY = "synapse.identity"
_URI = "synapse.uri"
_TOKEN_FILE = "synapse.tokenFile"
_WORKTREE_EXT = "extensions.worktreeConfig"


def _config_runner(
    values: dict[str, str],
    *,
    worktree_enabled: bool = True,
    raise_on_key: str | None = None,
) -> Callable[[list[str]], str]:
    """Return a git runner emulating ``git config`` reads keyed by the last argument.

    ``read_claim_check_config`` reads the requested key as the final argument of
    both its boolean (``extensions.worktreeConfig``) and value queries, so keying
    on ``args[-1]`` faithfully drives the real read path.
    """

    def runner(args: list[str]) -> str:
        key = args[-1]
        if key == raise_on_key:
            raise GitError("git config failed")
        if key == _WORKTREE_EXT:
            return "true" if worktree_enabled else "false"
        return values.get(key, "")

    return runner


def test_release_identity_is_frozen() -> None:
    identity = ReleaseIdentity(uri="ws://h", name="proj/seat", token="s")
    with pytest.raises(dataclasses.FrozenInstanceError):
        # Reason: assigning to a frozen field is the immutability we assert here;
        # mypy correctly rejects it, so the ignore keeps the deliberate probe.
        identity.name = "other"  # type: ignore[misc]


def test_resolves_full_worktree_identity(tmp_path: Path) -> None:
    token_path = tmp_path / "hub.token"
    token_path.write_text("  s3cret\n", encoding="utf-8")
    runner = _config_runner(
        {_IDENTITY: "proj/claude-a7c2", _URI: "ws://hub:9", _TOKEN_FILE: str(token_path)}
    )

    resolved = resolve_release_identity(runner=runner)

    assert resolved == ReleaseIdentity(uri="ws://hub:9", name="proj/claude-a7c2", token="s3cret")


def test_missing_identity_returns_none() -> None:
    assert resolve_release_identity(runner=_config_runner({})) is None


@pytest.mark.parametrize("placeholder", ["USER", "ME", "YOUR_IDENTITY", "proj/USER"])
def test_placeholder_identity_returns_none(placeholder: str) -> None:
    runner = _config_runner({_IDENTITY: placeholder, _URI: "ws://hub"})
    assert resolve_release_identity(runner=runner) is None


def test_blank_uri_falls_back_to_default_hub() -> None:
    runner = _config_runner({_IDENTITY: "proj/seat", _URI: "", _TOKEN_FILE: ""})
    resolved = resolve_release_identity(runner=runner)
    assert resolved is not None
    assert resolved.uri == DEFAULT_HUB_URI
    assert resolved.token is None


def test_legacy_local_config_still_resolves(tmp_path: Path) -> None:
    # A repository not yet migrated to per-worktree config reads the local scope.
    runner = _config_runner(
        {_IDENTITY: "proj/seat", _URI: "ws://legacy"},
        worktree_enabled=False,
    )
    resolved = resolve_release_identity(runner=runner)
    assert resolved == ReleaseIdentity(uri="ws://legacy", name="proj/seat", token=None)


def test_git_error_reading_identity_returns_none() -> None:
    runner = _config_runner({_IDENTITY: "proj/seat"}, raise_on_key=_IDENTITY)
    assert resolve_release_identity(runner=runner) is None


def test_git_error_reading_uri_returns_none() -> None:
    runner = _config_runner({_IDENTITY: "proj/seat"}, raise_on_key=_URI)
    assert resolve_release_identity(runner=runner) is None


def test_git_error_reading_token_file_returns_none() -> None:
    runner = _config_runner({_IDENTITY: "proj/seat", _URI: "ws://h"}, raise_on_key=_TOKEN_FILE)
    assert resolve_release_identity(runner=runner) is None


def test_read_token_blank_is_none() -> None:
    assert _read_token("") is None
    assert _read_token("   ") is None


def test_read_token_reads_and_strips(tmp_path: Path) -> None:
    token_path = tmp_path / "t"
    token_path.write_text("\ntok-en \n", encoding="utf-8")
    assert _read_token(str(token_path)) == "tok-en"


def test_read_token_empty_file_is_none(tmp_path: Path) -> None:
    token_path = tmp_path / "empty"
    token_path.write_text("   \n", encoding="utf-8")
    assert _read_token(str(token_path)) is None


def test_read_token_missing_file_is_none(tmp_path: Path) -> None:
    assert _read_token(str(tmp_path / "does-not-exist")) is None


def test_read_token_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".hubtoken").write_text("home-token\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert _read_token("~/.hubtoken") == "home-token"
