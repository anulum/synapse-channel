# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — staged claim-check context tests

from __future__ import annotations

import os
from pathlib import Path

import pytest

from synapse_channel.git.claim_check_context import (
    ClaimCheckConfigError,
    resolve_claim_check_context,
)
from synapse_channel.git.gitclaim import GitError


class _Runner:
    def __init__(
        self,
        root: Path,
        *,
        branch: str = "main",
        config: dict[str, str] | None = None,
        detached: bool = False,
    ) -> None:
        self.root = root
        self.branch = branch
        self.config = config or {}
        self.detached = detached
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args == ["rev-parse", "--show-toplevel"]:
            return str(self.root)
        if args == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            if self.detached:
                raise GitError("not a symbolic ref")
            return self.branch
        if args[:5] == ["config", "--local", "--get", "--default", ""]:
            return self.config.get(args[5], "")
        raise AssertionError(args)


def test_explicit_identity_builds_canonical_context(tmp_path: Path) -> None:
    runner = _Runner(tmp_path / "repo")
    context = resolve_claim_check_context(
        identity="project/agent", uri="ws://explicit", runner=runner, environment={}
    )
    assert context.root == (tmp_path / "repo").resolve()
    assert context.branch == "main"
    assert context.identity == "project/agent"
    assert context.uri == "ws://explicit"
    assert context.token_file is None
    assert context.requester.startswith("claim-check/")
    assert len(context.requester.removeprefix("claim-check/")) == 16


def test_config_identity_uri_and_relative_token_file_resolve_under_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    runner = _Runner(
        root,
        config={
            "synapse.identity": "project/agent",
            "synapse.uri": "wss://configured",
            "synapse.tokenFile": ".secrets/hub.token",
        },
    )
    context = resolve_claim_check_context(runner=runner, environment={})
    assert context.identity == "project/agent"
    assert context.uri == "wss://configured"
    assert context.token_file == (root / ".secrets/hub.token").resolve()


def test_agreeing_session_pair_is_accepted_and_requester_is_stable(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    env = {"SYN_PROJECT": "project", "SYN_IDENTITY": "project/agent"}
    first = resolve_claim_check_context(runner=runner, environment=env)
    second = resolve_claim_check_context(runner=runner, environment=env)
    assert first.identity == "project/agent"
    assert first.requester == second.requester


def test_project_identity_without_suffix_is_accepted(tmp_path: Path) -> None:
    context = resolve_claim_check_context(
        runner=_Runner(tmp_path),
        environment={"SYN_PROJECT": "project", "SYN_IDENTITY": "project"},
    )
    assert context.identity == "project"


def test_lowercase_user_namespace_is_not_the_uppercase_placeholder(tmp_path: Path) -> None:
    context = resolve_claim_check_context(
        identity="user/terminal-23696", runner=_Runner(tmp_path), environment={}
    )
    assert context.identity == "user/terminal-23696"


@pytest.mark.parametrize(
    ("explicit", "configured", "environment"),
    [
        ("one", "two", {}),
        (None, "one", {"SYN_PROJECT": "two", "SYN_IDENTITY": "two/agent"}),
        ("one", "one", {"SYN_PROJECT": "two", "SYN_IDENTITY": "two/agent"}),
    ],
)
def test_every_populated_identity_source_must_agree(
    tmp_path: Path,
    explicit: str | None,
    configured: str,
    environment: dict[str, str],
) -> None:
    runner = _Runner(tmp_path, config={"synapse.identity": configured})
    with pytest.raises(ClaimCheckConfigError, match="sources disagree"):
        resolve_claim_check_context(identity=explicit, runner=runner, environment=environment)


@pytest.mark.parametrize(
    "environment",
    [
        {"SYN_PROJECT": "project"},
        {"SYN_IDENTITY": "project/agent"},
    ],
)
def test_bare_session_identity_or_project_is_refused(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    with pytest.raises(ClaimCheckConfigError, match="supplied together"):
        resolve_claim_check_context(runner=_Runner(tmp_path), environment=environment)


def test_session_identity_must_belong_to_project(tmp_path: Path) -> None:
    with pytest.raises(ClaimCheckConfigError, match="does not belong"):
        resolve_claim_check_context(
            runner=_Runner(tmp_path),
            environment={"SYN_PROJECT": "project", "SYN_IDENTITY": "other/agent"},
        )


@pytest.mark.parametrize("identity", ["USER", "ME", "project/YOUR_IDENTITY"])
def test_placeholder_identity_is_refused(tmp_path: Path, identity: str) -> None:
    with pytest.raises(ClaimCheckConfigError, match="Placeholder"):
        resolve_claim_check_context(identity=identity, runner=_Runner(tmp_path), environment={})


def test_missing_identity_has_repair_instruction(tmp_path: Path) -> None:
    with pytest.raises(ClaimCheckConfigError, match="synapse git-init --name"):
        resolve_claim_check_context(runner=_Runner(tmp_path), environment={})


def test_uri_precedence_and_environment_default(tmp_path: Path) -> None:
    runner = _Runner(
        tmp_path,
        config={"synapse.identity": "agent", "synapse.uri": "ws://config"},
    )
    explicit = resolve_claim_check_context(
        uri="ws://explicit", runner=runner, environment={"SYNAPSE_URI": "ws://env"}
    )
    assert explicit.uri == "ws://explicit"
    configured = resolve_claim_check_context(runner=runner, environment={"SYNAPSE_URI": "ws://env"})
    assert configured.uri == "ws://config"
    env_only = resolve_claim_check_context(
        identity="agent",
        runner=_Runner(tmp_path),
        environment={"SYNAPSE_URI": "ws://env"},
    )
    assert env_only.uri == "ws://env"


def test_detached_head_is_focused_failure(tmp_path: Path) -> None:
    with pytest.raises(ClaimCheckConfigError, match="Detached HEAD"):
        resolve_claim_check_context(
            identity="agent", runner=_Runner(tmp_path, detached=True), environment={}
        )


def test_empty_root_or_branch_is_refused(tmp_path: Path) -> None:
    def no_root(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return ""
        raise AssertionError(args)

    with pytest.raises(ClaimCheckConfigError, match="no repository root"):
        resolve_claim_check_context(identity="agent", runner=no_root, environment={})
    with pytest.raises(ClaimCheckConfigError, match="no current branch"):
        resolve_claim_check_context(
            identity="agent", runner=_Runner(tmp_path, branch=""), environment={}
        )


def test_symlink_loop_root_and_token_file_are_refused(tmp_path: Path) -> None:
    root_loop = tmp_path / "root-loop"
    os.symlink(root_loop.name, root_loop)
    with pytest.raises(ClaimCheckConfigError, match="invalid repository root"):
        resolve_claim_check_context(identity="agent", runner=_Runner(root_loop), environment={})

    token_loop = tmp_path / "token-loop"
    os.symlink(token_loop.name, token_loop)
    with pytest.raises(ClaimCheckConfigError, match="token-file path is invalid"):
        resolve_claim_check_context(
            identity="agent",
            token_file=str(token_loop),
            runner=_Runner(tmp_path),
            environment={},
        )
