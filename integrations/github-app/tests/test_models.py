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
    """Preserve validated webhook repository, installation, head and author fields."""
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
        author="octo-dev",
    )


def test_seed_attaches_sorted_unique_valid_paths() -> None:
    """Normalise changed paths while preserving attribution."""
    seed = PullRequestSeed.from_api(pull_request_record(9))
    snapshot = seed.with_paths(["src/z.py", "src/a.py", "src/z.py"], paths_truncated=True)

    assert snapshot.paths == ("src/a.py", "src/z.py")
    assert snapshot.paths_truncated is True
    assert snapshot.branch_key == "pull/9"
    assert snapshot.author == "octo-dev"


@pytest.mark.parametrize("owner", ["", "-bad", "bad_name", "x" * 40])
def test_repository_rejects_invalid_owner(owner: str) -> None:
    """Reject invalid repository owner names."""
    with pytest.raises(PayloadError, match="owner"):
        Repository(owner, "repo")


@pytest.mark.parametrize("name", ["", ".", "..", "bad/name", "x" * 101])
def test_repository_rejects_invalid_name(name: str) -> None:
    """Reject malformed repository names."""
    with pytest.raises(PayloadError, match="name"):
        Repository("owner", name)


@pytest.mark.parametrize(
    "path",
    ["/absolute.py", "back\\slash.py", "a//b.py", "a/../b.py", "a/./b.py", "bad\n.py"],
)
def test_paths_refuse_non_repository_or_control_forms(path: str) -> None:
    """Reject absolute, traversal and control-bearing paths."""
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
    """Reject malformed REST fields with field-specific errors."""
    with pytest.raises(PayloadError, match=message):
        PullRequestSeed.from_api(record)


def test_snapshot_direct_construction_normalizes_paths() -> None:
    """Normalise paths for direct snapshot construction."""
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
    """Reject non-boolean truncation markers."""
    with pytest.raises(PayloadError, match="paths_truncated"):
        PullRequestSnapshot(
            number=1,
            head_sha="a" * 40,
            head_ref="feature/a",
            base_ref="main",
            paths=("a",),
            paths_truncated=1,  # type: ignore[arg-type]
        )


def test_author_parses_human_and_bracketed_bot_logins() -> None:
    """Preserve human and bot labels without inventing agent identities."""
    human = PullRequestSeed.from_api(pull_request_record(9, login="octo-dev"))
    bot = PullRequestSeed.from_api(pull_request_record(9, login="dependabot[bot]"))

    assert human.author == "octo-dev"
    assert bot.author == "dependabot[bot]"


@pytest.mark.parametrize(
    "record",
    [
        pull_request_record(9, login=None),
        {**pull_request_record(9, login=None), "user": None},
        {**pull_request_record(9, login=None), "user": {"login": None}},
        {**pull_request_record(9, login=None), "user": {}},
    ],
)
def test_absent_or_null_author_is_unattributed(record: object) -> None:
    """Leave absent or null users unattributed."""
    assert PullRequestSeed.from_api(record).author is None


def test_author_refuses_non_object_user_and_oversized_login() -> None:
    """Reject malformed user objects and oversized login labels."""
    with pytest.raises(PayloadError, match="user must be an object"):
        PullRequestSeed.from_api({**pull_request_record(9, login=None), "user": ["octo-dev"]})
    with pytest.raises(PayloadError, match="login"):
        PullRequestSeed.from_api(pull_request_record(9, login="x" * 49))


@pytest.mark.parametrize("author", ["", "bad\nlogin", "x" * 49])
def test_direct_construction_validates_author(author: str) -> None:
    """Validate authors on direct seed and snapshot construction."""
    with pytest.raises(PayloadError, match="login"):
        PullRequestSnapshot(
            number=1,
            head_sha="a" * 40,
            head_ref="feature/a",
            base_ref="main",
            paths=("a",),
            author=author,
        )
    with pytest.raises(PayloadError, match="login"):
        PullRequestSeed(
            number=1,
            head_sha="a" * 40,
            head_ref="feature/a",
            base_ref="main",
            author=author,
        )


def test_event_refuses_missing_nested_fields_and_bad_delivery() -> None:
    """Reject incomplete event identity and invalid delivery identifiers."""
    missing_installation = pull_request_payload()
    del missing_installation["installation"]
    with pytest.raises(PayloadError, match="installation"):
        PullRequestEvent.from_payload(missing_installation, delivery_id="x")
    with pytest.raises(PayloadError, match="repository"):
        PullRequestEvent.from_payload({"action": "opened"}, delivery_id="x")
    with pytest.raises(PayloadError, match="X-GitHub-Delivery"):
        PullRequestEvent.from_payload(pull_request_payload(), delivery_id="")
