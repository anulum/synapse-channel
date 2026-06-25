# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for client-side git-scoped claims

from __future__ import annotations

from synapse_channel.git.gitclaim import (
    resolve_branch,
    resolve_repo,
)


def test_resolve_branch_calls_rev_parse() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "main"

    assert resolve_branch(runner=runner) == "main"
    assert captured == [["rev-parse", "--abbrev-ref", "HEAD"]]


def test_resolve_repo_calls_show_toplevel() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "/home/me/work/repo"

    assert resolve_repo(runner=runner) == "/home/me/work/repo"
    assert captured == [["rev-parse", "--show-toplevel"]]
