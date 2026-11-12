# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an attempt's marker: bytes, ids, ref, and every row of both readings
"""The marker's bytes and ids are fixed, its identifiers are checked first, and each reading is."""

from __future__ import annotations

import pytest

from synapse_channel.core.attempt_markers import (
    APPLIED,
    BASE_GONE,
    CHANGE_RESULTS,
    LANDED,
    LANDED_EARLIER,
    LANDED_PRESENT,
    LANDED_THEN_MOVED,
    MAY_STILL_LAND,
    NOT_REACHED,
    OBSERVATIONS,
    OTHER_PARTY_FIRST,
    PUBLISHED_OUTSIDE_PROTOCOL,
    SEALED,
    SEALED_NEVER_LANDS,
    UNKNOWN,
    AttemptMarker,
    AttemptMarkerError,
    MarkerObjects,
    blob_object_id,
    change_result,
    observation,
)

MARKER = AttemptMarker("op:1", "0123456789abcdef0123456789abcdef", "e" * 64)
APPLIED_BYTES = (
    b"synapse.attempt-marker.v1\nkind applied\noperation op:1\n"
    b"attempt 0123456789abcdef0123456789abcdef\nintent " + b"e" * 64 + b"\n"
)
GOLDEN = {
    "sha1": MarkerObjects(
        "sha1",
        "8c603e953750b151ccbde781822842f47341586d",
        "fdb2473c9c08b8e7c38fc1e2de9cf84bf7e066be",
    ),
    "sha256": MarkerObjects(
        "sha256",
        "e92dc2355115416c15c3614390eea75f1f3b3a3da5d40f39e9bf00493306d159",
        "17871a8d77a9cace28330e945bc75915abe0d73142bf7573f5ad34ac5808013c",
    ),
}
"""Ids of the two blobs, computed by ``git hash-object --stdin`` in a repository of each format."""


def test_the_bytes_are_five_lines_and_the_kinds_always_differ() -> None:
    assert MARKER.blob(APPLIED) == APPLIED_BYTES
    assert MARKER.blob(SEALED) == APPLIED_BYTES.replace(b"kind applied", b"kind sealed")
    assert MARKER.ref == "refs/synapse/attempts/0123456789abcdef0123456789abcdef"
    for object_format, objects in GOLDEN.items():
        assert MARKER.objects(object_format) == objects
        assert objects.applied != objects.sealed


@pytest.mark.parametrize(
    ("operation_id", "attempt_id", "intent", "message"),
    [
        (
            "op\n1",
            "0" * 32,
            "e" * 64,
            "operation id 'op\\n1' does not match [A-Za-z0-9._:-]{1,128}",
        ),
        ("op 1", "0" * 32, "e" * 64, "operation id 'op 1' does not match [A-Za-z0-9._:-]{1,128}"),
        ("", "0" * 32, "e" * 64, "operation id '' does not match [A-Za-z0-9._:-]{1,128}"),
        ("op", "0" * 31, "e" * 64, f"attempt id '{'0' * 31}' does not match [0-9a-f]{{32}}"),
        ("op", "A" * 32, "e" * 64, f"attempt id '{'A' * 32}' does not match [0-9a-f]{{32}}"),
    ],
)
def test_an_identifier_outside_its_pattern_is_refused_before_any_bytes(
    operation_id: str, attempt_id: str, intent: str, message: str
) -> None:
    with pytest.raises(AttemptMarkerError) as refused:
        AttemptMarker(operation_id, attempt_id, intent)
    assert str(refused.value) == message
    assert refused.value.code == "attempt_marker"


def test_a_long_operation_id_and_a_bad_digest_are_named_in_the_refusal() -> None:
    with pytest.raises(AttemptMarkerError) as long_id:
        AttemptMarker("o" * 129, "0" * 32, "e" * 64)
    assert (
        str(long_id.value) == f"operation id '{'o' * 129}' does not match [A-Za-z0-9._:-]{{1,128}}"
    )
    with pytest.raises(AttemptMarkerError) as digest:
        AttemptMarker("op", "0" * 32, "e" * 63 + "\n")
    assert str(digest.value) == f"intent digest '{'e' * 63}\\n' does not match [0-9a-f]{{64}}"


def test_an_unknown_kind_or_object_format_is_refused() -> None:
    with pytest.raises(AttemptMarkerError) as kind:
        MARKER.blob("maybe")
    assert str(kind.value) == "marker kind 'maybe' is not one of ['applied', 'sealed']"
    with pytest.raises(AttemptMarkerError) as fmt:
        blob_object_id(b"x", "md5")
    assert str(fmt.value) == "object format 'md5' is not one of ['sha1', 'sha256']"


OBJECTS = GOLDEN["sha1"]
BASE, CANDIDATE, ELSEWHERE, FOREIGN = "b" * 40, "c" * 40, "d" * 40, "f" * 40


@pytest.mark.parametrize(
    ("succeeded", "marker", "branch", "kind", "branch_set_to", "answer"),
    [
        (True, OBJECTS.applied, CANDIDATE, APPLIED, CANDIDATE, LANDED),
        (True, OBJECTS.sealed, BASE, SEALED, None, LANDED),
        (True, OBJECTS.applied, BASE, APPLIED, CANDIDATE, UNKNOWN),
        (True, None, CANDIDATE, APPLIED, CANDIDATE, UNKNOWN),
        (False, OBJECTS.applied, CANDIDATE, APPLIED, CANDIDATE, LANDED_EARLIER),
        (False, OBJECTS.sealed, BASE, SEALED, None, LANDED_EARLIER),
        (False, OBJECTS.sealed, BASE, APPLIED, CANDIDATE, OTHER_PARTY_FIRST),
        (False, OBJECTS.applied, CANDIDATE, SEALED, None, OTHER_PARTY_FIRST),
        (False, None, ELSEWHERE, APPLIED, CANDIDATE, BASE_GONE),
        (False, None, None, APPLIED, CANDIDATE, BASE_GONE),
        (False, None, BASE, APPLIED, CANDIDATE, NOT_REACHED),
        (False, None, ELSEWHERE, SEALED, None, NOT_REACHED),
        (False, FOREIGN, BASE, APPLIED, CANDIDATE, UNKNOWN),
        (True, FOREIGN, CANDIDATE, APPLIED, CANDIDATE, UNKNOWN),
    ],
)
def test_every_row_of_the_change_table(
    succeeded: bool,
    marker: str | None,
    branch: str | None,
    kind: str,
    branch_set_to: str | None,
    answer: str,
) -> None:
    assert answer in CHANGE_RESULTS
    assert (
        change_result(
            succeeded=succeeded,
            marker=marker,
            branch=branch,
            objects=OBJECTS,
            kind=kind,
            base=BASE,
            branch_set_to=branch_set_to,
        )
        == answer
    )


@pytest.mark.parametrize(
    ("branch", "marker", "contains", "answer"),
    [
        (CANDIDATE, OBJECTS.applied, True, LANDED_PRESENT),
        (ELSEWHERE, OBJECTS.applied, True, LANDED_PRESENT),
        (ELSEWHERE, OBJECTS.applied, False, LANDED_THEN_MOVED),
        (None, OBJECTS.applied, False, LANDED_THEN_MOVED),
        (ELSEWHERE, OBJECTS.applied, None, UNKNOWN),
        (BASE, OBJECTS.sealed, False, SEALED_NEVER_LANDS),
        (CANDIDATE, OBJECTS.sealed, True, SEALED_NEVER_LANDS),
        (BASE, None, False, MAY_STILL_LAND),
        (CANDIDATE, None, True, PUBLISHED_OUTSIDE_PROTOCOL),
        (ELSEWHERE, None, True, PUBLISHED_OUTSIDE_PROTOCOL),
        (ELSEWHERE, None, False, UNKNOWN),
        (ELSEWHERE, None, None, UNKNOWN),
        (CANDIDATE, FOREIGN, True, UNKNOWN),
    ],
)
def test_every_row_of_the_observation_table(
    branch: str | None, marker: str | None, contains: bool | None, answer: str
) -> None:
    assert answer in OBSERVATIONS
    assert (
        observation(
            branch=branch,
            marker=marker,
            objects=OBJECTS,
            base=BASE,
            candidate=CANDIDATE,
            contains_candidate=contains,
        )
        == answer
    )
