# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, cast

import pytest

from synapse_channel.core.protected_write_content import bind_protected_write_content
from test_protected_write_proposal import LIMITS, _proposal

CONTENT = b"Reference: retained source event.\n"


def test_create_then_write_binds_each_distinct_payload_and_aggregate_budget() -> None:
    proposal = cast(dict[str, Any], _proposal())
    changed = b"Final retained bytes.\n"
    digest = hashlib.sha256(changed).hexdigest()
    initial = proposal["auxiliary_operations"][0]["paths"][0]
    final = {**initial["after"], "sha256": digest, "size_bytes": len(changed)}
    proposal["auxiliary_operations"].append(
        {
            "operation_id": "write-record",
            "opcode": "write",
            "content_reference": {
                "domain": "text",
                "handle": "changed-content",
                "sha256": digest,
                "size_bytes": len(changed),
            },
            "paths": [{**initial, "before": initial["after"], "after": final}],
        }
    )
    proposal["operations"][0].update(after_sha256=digest, after_size_bytes=len(changed))
    artifacts = {("text", "source-content"): CONTENT, ("text", "changed-content"): changed}
    bound = bind_protected_write_content(
        json.dumps(proposal),
        limits=LIMITS,
        artifacts=artifacts,
        domain_verifiers={"text": _verify},
        max_artifact_bytes=len(CONTENT) + len(changed),
    )
    assert bound.operation_content == (("create-record", CONTENT), ("write-record", changed))
    with pytest.raises(ValueError, match="budget exceeded"):
        bind_protected_write_content(
            json.dumps(proposal),
            limits=LIMITS,
            artifacts=artifacts,
            domain_verifiers={"text": _verify},
            max_artifact_bytes=len(CONTENT) + len(changed) - 1,
        )


def _verify(content: bytes, digest: str) -> bool:
    return hashlib.sha256(content).hexdigest() == digest


def test_content_binds_actual_bytes_and_ignores_later_store_changes() -> None:
    artifacts = {("text", "source-content"): CONTENT}
    calls = []

    def verify(content: bytes, digest: str) -> bool:
        calls.append(content)
        return _verify(content, digest)

    bound = bind_protected_write_content(
        json.dumps(_proposal()),
        limits=LIMITS,
        artifacts=artifacts,
        domain_verifiers={"text": verify},
        max_artifact_bytes=len(CONTENT),
    )
    assert calls == [CONTENT]
    assert bound.proposal_content == CONTENT
    assert bound.operation_content == (("create-record", CONTENT),)
    artifacts[("text", "source-content")] = b"changed"
    assert bound.operation_content[0][1] == CONTENT


@pytest.mark.parametrize("budget", [True, 0, -1, 1.0, 2**53])
def test_content_refuses_invalid_budget(budget: object) -> None:
    with pytest.raises(ValueError, match="budget"):
        bind_protected_write_content(
            json.dumps(_proposal()),
            limits=LIMITS,
            artifacts={},
            domain_verifiers={},
            max_artifact_bytes=cast(int, budget),
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "size",
        "mutable",
        "digest",
        "domain",
        "truthy",
        "budget",
        "output",
        "conflicting-handle",
    ],
)
def test_content_refuses_unbound_bytes(case: str) -> None:
    proposal = cast(dict[str, Any], _proposal())
    artifacts = {("text", "source-content"): CONTENT}
    verifiers: dict[str, Callable[[bytes, str], bool]] = {"text": _verify}
    budget = len(CONTENT)
    if case == "missing":
        artifacts.clear()
    elif case == "size":
        artifacts[("text", "source-content")] = b"x"
    elif case == "mutable":
        artifacts[("text", "source-content")] = cast(bytes, bytearray(CONTENT))
    elif case == "digest":
        artifacts[("text", "source-content")] = b"x" * len(CONTENT)
    elif case == "domain":
        verifiers.clear()
    elif case == "truthy":
        verifiers["text"] = lambda _b, _d: cast(bool, 1)
    elif case == "budget":
        budget -= 1
    elif case == "output":
        proposal["operations"][0]["after_sha256"] = "0" * 64
        proposal["auxiliary_operations"][0]["paths"][0]["after"]["sha256"] = "0" * 64
    elif case == "conflicting-handle":
        proposal["auxiliary_operations"][0]["content_reference"] = {
            **proposal["content_reference"],
            "sha256": "0" * 64,
        }
    with pytest.raises(ValueError):
        bind_protected_write_content(
            json.dumps(proposal),
            limits=LIMITS,
            artifacts=artifacts,
            domain_verifiers=verifiers,
            max_artifact_bytes=budget,
        )


def test_non_raw_domain_digest_is_not_confused_with_resulting_file_hash() -> None:
    proposal = cast(dict[str, Any], _proposal())
    proposal["content_reference"]["sha256"] = hashlib.sha256(b"domain:" + CONTENT).hexdigest()
    # Fixture references share the same object, just as the wire has the same reference.
    path = proposal["auxiliary_operations"][0]["paths"][0]
    proposal["auxiliary_operations"].append(
        {
            "operation_id": "flush",
            "opcode": "fsync",
            "content_reference": None,
            "paths": [{**path, "before": path["after"]}],
        }
    )
    bound = bind_protected_write_content(
        json.dumps(proposal),
        limits=LIMITS,
        artifacts={("text", "source-content"): CONTENT},
        domain_verifiers={
            "text": lambda content, digest: (
                hashlib.sha256(b"domain:" + content).hexdigest() == digest
            )
        },
        max_artifact_bytes=len(CONTENT),
    )
    assert bound.operation_content == (("create-record", CONTENT),)
