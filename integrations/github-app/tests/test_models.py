# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — typed GitHub boundary model tests
"""Exercise production-shaped webhook and REST model validation."""

from __future__ import annotations

import pytest

from payloads import pull_request_payload, pull_request_record
from synapse_github_app.errors import PayloadError
from synapse_github_app.models import (
    PullRequestEvent,
    PullRequestSeed,
    PullRequestSnapshot,
    Repository,
    normalize_paths,
)


def test_event_parses_repository_installation_and_pull_identity() -> None:
    event = PullRequestEvent.from_payload(pull_request_payload(), delivery_id="delivery-7")

    assert event.action == "opened"
    assert event.delivery_id == "delivery-7"
    assert event.installation_id == 42
    assert event.repository.full_name == "anulum/synapse-channel"
    assert event.pull_request == PullRequestSeed(
        number=7,
        head_sha="0000000000000000000000000000000000000007",
        head_ref="feature/risk",
        base_ref="main",
    )


def test_seed_attaches_sorted_unique_valid_paths() -> None:
    seed = PullRequestSeed.from_api(pull_request_record(9))
    snapshot = seed.with_paths(["src/z.py", "src/a.py", "src/z.py"], paths_truncated=True)

    assert snapshot.paths == ("src/a.py", "src/z.py")
    assert snapshot.paths_truncated is True
    assert snapshot.branch_key == "pull/9"


@pytest.mark.parametrize("owner", ["", "-bad", "bad_name", "x" * 40])
def test_repository_rejects_invalid_owner(owner: str) -> None:
    with pytest.raises(PayloadError, match="owner"):
        Repository(owner, "repo")


@pytest.mark.parametrize("name", ["", ".", "..", "bad/name", "x" * 101])
def test_repository_rejects_invalid_name(name: str) -> None:
    with pytest.raises(PayloadError, match="name"):
        Repository("owner", name)


@pytest.mark.parametrize(
    "path",
    ["/absolute.py", "back\\slash.py", "a//b.py", "a/../b.py", "a/./b.py", "bad\n.py"],
)
def test_paths_refuse_non_repository_or_control_forms(path: str) -> None:
    with pytest.raises(PayloadError, match="filename"):
        normalize_paths([path])


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (None, "object"),
        ({"number": True, "head": {}, "base": {}}, "positive integer"),
        ({"number": 1, "head": {"sha": "bad", "ref": "x"}, "base": {"ref": "main"}}, "object id"),
        ({"number": 1, "head": {"sha": "1" * 40, "ref": ""}, "base": {"ref": "main"}}, "non-empty"),
    ],
)
def test_seed_refuses_malformed_rest_records(record: object, message: str) -> None:
    with pytest.raises(PayloadError, match=message):
        PullRequestSeed.from_api(record)


def test_snapshot_direct_construction_normalizes_paths() -> None:
    snapshot = PullRequestSnapshot(
        number=1,
        head_sha="A" * 40,
        head_ref="feature/a",
        base_ref="main",
        paths=("z", "a", "z"),
    )
    assert snapshot.head_sha == "a" * 40
    assert snapshot.paths == ("a", "z")


def test_snapshot_requires_boolean_truncation_marker() -> None:
    with pytest.raises(PayloadError, match="paths_truncated"):
        PullRequestSnapshot(
            number=1,
            head_sha="a" * 40,
            head_ref="feature/a",
            base_ref="main",
            paths=("a",),
            paths_truncated=1,  # type: ignore[arg-type]
        )


def test_event_refuses_missing_nested_fields_and_bad_delivery() -> None:
    missing_installation = pull_request_payload()
    del missing_installation["installation"]
    with pytest.raises(PayloadError, match="installation"):
        PullRequestEvent.from_payload(missing_installation, delivery_id="x")
    with pytest.raises(PayloadError, match="repository"):
        PullRequestEvent.from_payload({"action": "opened"}, delivery_id="x")
    with pytest.raises(PayloadError, match="X-GitHub-Delivery"):
        PullRequestEvent.from_payload(pull_request_payload(), delivery_id="")
