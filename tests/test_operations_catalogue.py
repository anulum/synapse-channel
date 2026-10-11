# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the catalogue of vendor-client operations
"""The catalogue: what is listed, what may run, what an unseen version would be."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from synapse_channel.operations.catalogue import SCHEMA, Catalogue, OperationRefusedError
from synapse_channel.operations.spec import OperationSpec, OperationSpecError
from test_operations_spec import queue_message

CLIENT = {"host": "workstation", "vendor": "codex", "client_version": "0.159.1", "os": "linux"}


def _catalogue() -> Catalogue:
    return Catalogue(
        [
            queue_message(),
            queue_message(operation="session.list", qualification="help_text", evidence=""),
            queue_message(operation="settings.read", qualification="unverified", evidence=""),
            queue_message(operation="session.archive", qualification="accepted"),
            queue_message(client_version="0.158.9", operation="session.rename"),
            queue_message(client_version="0.20.0", operation="session.old"),
            queue_message(host="msi", os="windows"),
            queue_message(vendor="kimi", client_version="1.4.0", operation="session.queue_message"),
        ]
    )


def test_rows_are_listed_in_a_fixed_order_and_filtered_by_host_and_vendor() -> None:
    catalogue = _catalogue()

    assert len(catalogue.rows()) == 8
    assert [row.host for row in catalogue.rows(host="msi")] == ["msi"]
    assert {row.vendor for row in catalogue.rows(vendor="kimi")} == {"kimi"}
    assert len(catalogue.rows(host="workstation", vendor="codex")) == 6
    assert catalogue.rows() == Catalogue(reversed(catalogue.rows())).rows()
    assert Catalogue().rows() == ()
    with pytest.raises(OperationSpecError, match="operation listed twice: workstation codex"):
        Catalogue([queue_message(), queue_message(qualification="accepted")])


def test_only_an_operation_known_well_enough_for_exactly_this_client_is_admitted() -> None:
    catalogue = _catalogue()

    assert catalogue.admit("session.queue_message", **CLIENT) == queue_message()
    assert catalogue.admit("session.archive", **CLIENT).qualification == "accepted"
    assert catalogue.find("session.list", **CLIENT) is not None
    for operation, reason in (
        ("session.list", "session.list is help_text: listed, and not run"),
        ("settings.read", "settings.read is unverified: listed, and not run"),
        (
            "session.delete",
            "session.delete is not listed for codex 0.159.1 on linux at workstation",
        ),
    ):
        with pytest.raises(OperationRefusedError) as refusal:
            catalogue.admit(operation, **CLIENT)
        assert str(refusal.value) == reason
    # The same operation of another version, host, system or vendor is another row.
    for change in (
        {"client_version": "0.160.0"},
        {"host": "msi"},
        {"os": "windows"},
        {"vendor": "kimi"},
    ):
        with pytest.raises(OperationRefusedError, match="is not listed for"):
            catalogue.admit("session.queue_message", **{**CLIENT, **change})
    assert catalogue.find("session.rename", **CLIENT) is None


def test_an_unseen_version_gives_candidates_that_admit_nothing() -> None:
    catalogue = _catalogue()
    newer = {**CLIENT, "client_version": "0.160.0"}

    candidates = catalogue.candidates(**newer)

    # Copied from 0.159.1, the newest by number: 0.20.0 and 0.158.9 are older.
    assert sorted(row.operation for row in candidates) == [
        "session.archive",
        "session.list",
        "session.queue_message",
        "settings.read",
    ]
    assert {(row.client_version, row.qualification, row.evidence) for row in candidates} == {
        ("0.160.0", "unverified", "")
    }
    assert catalogue.find("session.queue_message", **newer) is None
    with pytest.raises(OperationRefusedError):
        catalogue.admit("session.queue_message", **newer)
    with pytest.raises(OperationRefusedError, match="is unverified"):
        Catalogue(candidates).admit("session.queue_message", **newer)
    assert catalogue.candidates(**CLIENT) == ()
    assert catalogue.candidates(**{**CLIENT, "vendor": "gemini"}) == ()
    assert catalogue.candidates(**{**CLIENT, "os": "macos", "client_version": "9"}) == ()
    lettered = Catalogue(
        [queue_message(client_version="1.0-rc1"), queue_message(client_version="1.0-beta")]
    )
    assert {row.operation for row in lettered.candidates(**{**CLIENT, "client_version": "2"})} == {
        "session.queue_message"
    }


def test_a_catalogue_survives_the_trip_through_plain_data() -> None:
    catalogue = _catalogue()

    data = json.loads(json.dumps(catalogue.to_json()))

    assert data["schema"] == SCHEMA == "operation-catalogue-v1"
    assert Catalogue.from_json(data).rows() == catalogue.rows()
    row = data["operations"][0]
    damaged_forms: list[object] = [
        [],
        {"schema": "other", "operations": []},
        {"schema": SCHEMA},
        {"schema": SCHEMA, "operations": {}},
        {"schema": SCHEMA, "operations": [row, row]},
        {"schema": SCHEMA, "operations": [{**row, "transport": "shell"}]},
    ]
    for damaged in damaged_forms:
        with pytest.raises(OperationSpecError):
            Catalogue.from_json(damaged)
    assert isinstance(replace(catalogue.rows()[0], host="other"), OperationSpec)
