# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-hub serving policy document shape refusals
"""A policy that is not JSON, or whose objects are not objects, never loads."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.multihub_serving_config import (
    MultiHubServingConfigError,
    load_multihub_serving_config,
)
from test_multihub_serving_config import _policy


@pytest.mark.parametrize(
    "raw",
    [b'{"version": 1,', b"version = 1", b"{'version': 1}"],
    ids=["truncated", "toml", "single-quoted"],
)
def test_policy_text_that_is_not_json_is_refused(tmp_path: Path, raw: bytes) -> None:
    """Readable text that is not JSON fails with one bounded message."""
    path = _policy(tmp_path)
    path.write_bytes(raw)
    with pytest.raises(
        MultiHubServingConfigError, match="^multi-hub serving policy is not valid UTF-8 JSON$"
    ):
        load_multihub_serving_config(path)


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (b"[]", "multi-hub serving policy"),
        (
            b'{"version": 1, "federation_store": "federation.json",'
            b' "client_ca_file": "client-ca.pem", "grants": ["fleet-a"]}',
            "grants\\[0\\]",
        ),
    ],
    ids=["root-list", "grant-string"],
)
def test_policy_values_that_must_be_objects_are_refused(
    tmp_path: Path, raw: bytes, label: str
) -> None:
    """The root and each grant must be JSON objects, not lists or strings."""
    path = _policy(tmp_path)
    path.write_bytes(raw)
    with pytest.raises(MultiHubServingConfigError, match=f"^{label} must be a JSON object$"):
        load_multihub_serving_config(path)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            b'{"version": 1, "federation_store": "federation.json", "grants": []}',
            "^multi-hub serving policy is missing fields: client_ca_file$",
        ),
        (
            b'{"version": 1, "federation_store": "federation.json",'
            b' "client_ca_file": "client-ca.pem", "grants": [{"sender": "fleet-a"}]}',
            "^grants\\[0\\] is missing fields: domain_id, namespace, signing_key_id$",
        ),
    ],
    ids=["root", "grant"],
)
def test_policy_objects_missing_required_fields_are_refused(
    tmp_path: Path, raw: bytes, message: str
) -> None:
    """Every required field is named, in order, when the root or a grant omits it."""
    path = _policy(tmp_path)
    path.write_bytes(raw)
    with pytest.raises(MultiHubServingConfigError, match=message):
        load_multihub_serving_config(path)
