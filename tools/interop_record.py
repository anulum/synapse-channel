# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — content-minimised evidence record of the official-client interop lane
"""Record what each official-client journey did, without what it said.

A journey step is kept as its name, its method or route, its status or error
code, and the sorted key shape of the request and the response. Message
bodies, error text, tokens and key material never enter the record: the
recorder refuses a record in which any known secret appears, and a run URL is
accepted only in the fixed GitHub Actions form.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

RECORD_FORMAT = "synapse-interop-record-v1"
_RUN_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+")
_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


class InteropRecordError(ValueError):
    """Raised when a record would be malformed or would carry a secret."""


def key_shape(document: object) -> list[str]:
    """Return the sorted top-level keys of a mapping, or an empty list.

    Only the key names are kept, never a value, so a body or a token in a value
    cannot reach the record.
    """
    if isinstance(document, Mapping):
        return sorted(str(key) for key in document)
    return []


@dataclass(frozen=True)
class StepRecord:
    """One journey step, content-minimised.

    Parameters
    ----------
    name:
        The journey step, for example ``claim-conflict-refused``.
    route:
        The protocol method or HTTP route, for example ``tools/call synapse_claim``.
    outcome:
        A short code: ``ok``, an HTTP status, or a protocol error code. Free
        text is refused, because a refusal message can echo a header.
    request_keys, response_keys:
        The key shape of the request and the response.
    """

    name: str
    route: str
    outcome: str
    request_keys: list[str] = field(default_factory=list)
    response_keys: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Refuse an outcome that is not a short code."""
        if _CODE.fullmatch(self.outcome) is None:
            raise InteropRecordError(f"step {self.name!r} outcome must be a short code")


@dataclass(frozen=True)
class JourneyRecord:
    """The outcome of one client, transport and journey.

    ``failed_step`` names the first step that failed, or is empty when the
    journey passed. ``journal_sha256`` is the digest of the journal rows the
    journey asserted.
    """

    client: str
    version: str
    integrity: str
    transport: str
    journey: str
    steps: list[StepRecord]
    journal_sha256: str
    failed_step: str = ""


def journal_digest(rows: Sequence[Mapping[str, object]]) -> str:
    """Return the SHA-256 of the asserted journal rows in canonical JSON."""
    text = json.dumps(list(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def checked_run_url(url: str) -> str:
    """Return ``url`` when it is a plain GitHub Actions run URL; refuse anything else.

    An empty string means a local run. Credentials, query strings and other
    hosts are refused.
    """
    if url and _RUN_URL.fullmatch(url) is None:
        raise InteropRecordError("the run URL is not a GitHub Actions run URL")
    return url


def build_record(
    journeys: Iterable[JourneyRecord],
    *,
    commit: str,
    run_url: str,
    secrets: Iterable[str],
) -> str:
    """Return the record as JSON text, refusing one in which a secret appears.

    ``secrets`` are the bearer tokens, hub tokens and key material the run used.
    """
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise InteropRecordError("the commit must be a full hexadecimal object id")
    document = {
        "format": RECORD_FORMAT,
        "commit": commit,
        "run_url": checked_run_url(run_url),
        "journeys": [
            {
                "client": journey.client,
                "version": journey.version,
                "integrity": journey.integrity,
                "transport": journey.transport,
                "journey": journey.journey,
                "passed": not journey.failed_step,
                "failed_step": journey.failed_step,
                "journal_sha256": journey.journal_sha256,
                "steps": [
                    {
                        "name": step.name,
                        "route": step.route,
                        "outcome": step.outcome,
                        "request_keys": step.request_keys,
                        "response_keys": step.response_keys,
                    }
                    for step in journey.steps
                ],
            }
            for journey in journeys
        ],
    }
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    for secret in secrets:
        if secret and secret in text:
            raise InteropRecordError("the record would contain a secret; nothing was written")
    return text


def write_record(path: Path, text: str) -> None:
    """Write a record built by :func:`build_record` to a new file."""
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
