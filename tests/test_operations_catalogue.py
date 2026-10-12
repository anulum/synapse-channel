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
from typing import Any

import pytest

from synapse_channel.operations.catalogue import (
    LEGACY_SCHEMA,
    SCHEMA,
    Catalogue,
    LegacyInventory,
    OperationRefusedError,
    read_catalogue,
)
from synapse_channel.operations.row import OperationRow
from synapse_channel.operations.spec import OperationSpecError
from synapse_channel.operations.vocabulary import Vocabulary, packaged
from test_operations_row import FINGERPRINT, list_sessions, qualified, read_session
from test_operations_spec import queue_message

CLIENT: dict[str, str] = {
    "host": "workstation",
    "vendor": "kimi",
    "component": "cli",
    "component_version": "2.1.1",
    "os": "linux",
    "transport": "cli",
}
FOUND: dict[str, str] = {**CLIENT, "fingerprint": FINGERPRINT}


def _catalogue() -> Catalogue:
    return Catalogue(
        [
            qualified(list_sessions()),
            qualified(list_sessions(variant="archived"), "accepted"),
            list_sessions(operation="session.search", qualification="help_text"),
            list_sessions(operation="setting.effective.read"),
            qualified(list_sessions(operation="plugin.list", effects=("read", "host"))),
            qualified(list_sessions(operation="account.status.read", effects=("access",))),
            qualified(
                list_sessions(operation="model.provider.list", disclosures=("credential_bearing",))
            ),
            qualified(
                list_sessions(
                    operation="setting.validate",
                    disclosures=("credential_bearing",),
                    output_projection="names-only-v1",
                )
            ),
            qualified(list_sessions(component_version="2.0.9", operation="session.export")),
            qualified(list_sessions(component_version="1.20.0", operation="diagnostics.check")),
            qualified(list_sessions(host="msi", os="windows")),
            read_session(),
        ]
    )


def test_rows_are_listed_in_a_fixed_order_and_filtered() -> None:
    catalogue = _catalogue()

    assert len(catalogue.rows()) == 12
    assert [row.host for row in catalogue.rows(host="msi")] == ["msi"]
    assert {row.vendor for row in catalogue.rows(vendor="codex")} == {"codex"}
    assert [row.operation for row in catalogue.rows(transport="rpc")] == ["session.read"]
    assert len(catalogue.rows(host="workstation", vendor="kimi", transport="cli")) == 10
    assert catalogue.rows() == Catalogue(reversed(catalogue.rows())).rows()
    assert Catalogue().rows() == () and Catalogue().vocabulary is packaged()
    with pytest.raises(OperationSpecError, match="operation listed twice: workstation kimi cli"):
        Catalogue([list_sessions(), qualified(list_sessions())])


def test_the_same_operation_over_two_transports_is_two_rows() -> None:
    by_command = list_sessions(vendor="codex", component_version="0.159.1")
    by_request = read_session(operation="session.list")

    catalogue = Catalogue([by_command, by_request])

    assert sorted(row.transport for row in catalogue.rows(vendor="codex")) == ["cli", "rpc"]
    found = catalogue.find(
        "session.list",
        host="workstation",
        vendor="codex",
        component="app-server",
        component_version="0.159.1",
        os="linux",
        transport="rpc",
    )
    assert found == by_request


def test_a_name_with_an_unregistered_word_is_not_listed() -> None:
    for name, message in (
        ("thread.list", "the object thread is not registered"),
        ("session.fork", "the action fork is not registered"),
        ("session.marketplace.list", "the aspect marketplace is not registered for session"),
    ):
        with pytest.raises(OperationSpecError, match=message):
            Catalogue([list_sessions(operation=name)])
    wider = Vocabulary(2, {"thread": "a vendor's word"}, {}, {"list": "name them"})
    assert len(Catalogue([list_sessions(operation="thread.list")], vocabulary=wider).rows()) == 1


def test_only_a_row_qualified_for_exactly_this_component_and_binding_is_admitted() -> None:
    catalogue = _catalogue()

    assert catalogue.admit("session.list", **FOUND) == qualified(list_sessions())
    assert catalogue.admit("session.list", **FOUND, variant="archived").qualification == "accepted"
    assert catalogue.admit("setting.validate", **FOUND).output_projection == "names-only-v1"
    for operation, reason in (
        ("session.search", "session.search is help_text: listed, and not run"),
        ("setting.effective.read", "setting.effective.read is unverified: listed, and not run"),
        ("plugin.list", "plugin.list has the effect host: listed, and never admitted"),
        (
            "account.status.read",
            "account.status.read has the effect access: listed, and never admitted",
        ),
        (
            "model.provider.list",
            "model.provider.list can print credentials and names no projection that removes them",
        ),
        (
            "session.delete",
            "session.delete is not listed for kimi cli 2.1.1 on linux at workstation over cli",
        ),
    ):
        with pytest.raises(OperationRefusedError) as refusal:
            catalogue.admit(operation, **FOUND)
        assert str(refusal.value) == reason
    with pytest.raises(OperationRefusedError, match="qualified for another executable"):
        catalogue.admit("session.list", **CLIENT, fingerprint="sha256:" + "00" * 32)
    # The same operation of another version, host, system, vendor, component,
    # transport or variant is another row.
    for change in (
        {"component_version": "2.1.2"},
        {"host": "msi"},
        {"os": "windows"},
        {"vendor": "codex"},
        {"component": "server"},
        {"transport": "rpc"},
        {"variant": "all"},
    ):
        with pytest.raises(OperationRefusedError, match="is not listed for"):
            catalogue.admit("session.list", **{**FOUND, **change})


def test_a_row_changed_after_its_qualification_is_not_admitted() -> None:
    ready = qualified(list_sessions())
    widened = replace(ready, arguments=(), reserved_natives=())

    with pytest.raises(OperationRefusedError) as refusal:
        Catalogue([widened]).admit("session.list", **FOUND)

    assert str(refusal.value) == "session.list was qualified for another binding than it has now"
    assert Catalogue([ready]).admit("session.list", **FOUND) == ready


def test_an_unseen_version_gives_candidates_that_admit_nothing() -> None:
    catalogue = _catalogue()
    newer = {**CLIENT, "component_version": "2.2.0"}
    asked = {name: value for name, value in newer.items() if name != "transport"}

    candidates = catalogue.candidates(**asked)

    # Copied from 2.1.1, the newest by number: 1.20.0 and 2.0.9 are older.
    assert len(candidates) == 8 and "session.export" not in {row.operation for row in candidates}
    assert {
        (
            row.component_version,
            row.qualification,
            row.evidence,
            row.fingerprint,
            row.qualified_binding,
        )
        for row in candidates
    } == {("2.2.0", "unverified", "", "", "")}
    assert catalogue.find("session.list", **newer) is None
    with pytest.raises(OperationRefusedError, match="is not listed for"):
        catalogue.admit("session.list", **newer, fingerprint=FINGERPRINT)
    with pytest.raises(OperationRefusedError, match="is unverified"):
        Catalogue(candidates).admit("session.list", **newer, fingerprint=FINGERPRINT)
    known = {name: value for name, value in CLIENT.items() if name != "transport"}
    assert catalogue.candidates(**known) == ()
    assert catalogue.candidates(**{**known, "vendor": "gemini"}) == ()
    assert catalogue.candidates(**{**known, "os": "macos", "component_version": "9"}) == ()
    assert catalogue.candidates(**{**known, "component": "server", "component_version": "9"}) == ()
    lettered = Catalogue(
        [list_sessions(component_version="1.0-rc1"), list_sessions(component_version="1.0-beta")]
    )
    assert len(lettered.candidates(**{**known, "component_version": "2"})) == 1


def test_a_catalogue_survives_the_trip_through_plain_data() -> None:
    catalogue = _catalogue()

    data = json.loads(json.dumps(catalogue.to_json()))

    assert data["schema"] == SCHEMA == "operation-catalogue-v2"
    assert data["vocabulary_revision"] == packaged().revision
    assert Catalogue.from_json(data).rows() == catalogue.rows()
    assert isinstance(read_catalogue(data), Catalogue)
    row = data["operations"][0]
    damaged_forms: list[Any] = [
        [],
        {**data, "schema": "other"},
        {"schema": SCHEMA, "vocabulary_revision": 1},
        {**data, "operations": {}},
        {**data, "operations": [row, row]},
        {**data, "operations": [{**row, "transport": "shell"}]},
        {**data, "operations": [{**row, "operation": "thread.list"}]},
        {**data, "vocabulary_revision": packaged().revision + 1},
        {**data, "vocabulary_revision": 0},
        {**data, "vocabulary_revision": True},
        {"schema": SCHEMA, "operations": []},
    ]
    for damaged in damaged_forms:
        with pytest.raises(OperationSpecError):
            Catalogue.from_json(damaged)
    assert isinstance(replace(catalogue.rows()[0], host="other"), OperationRow)


def test_a_file_of_the_earlier_schema_is_an_inventory_that_admits_nothing() -> None:
    stated_runnable = queue_message()
    legacy = LegacyInventory(
        [stated_runnable, queue_message(host="msi", os="windows"), queue_message(vendor="kimi")]
    )

    data = json.loads(json.dumps(legacy.to_json()))
    read = read_catalogue(data)

    assert data["schema"] == LEGACY_SCHEMA == "operation-catalogue-v1"
    assert isinstance(read, LegacyInventory) and read.rows() == legacy.rows()
    assert [row.host for row in read.rows(host="msi")] == ["msi"]
    assert [row.vendor for row in read.rows(vendor="kimi")] == ["kimi"]
    # The earlier description says it may run; the inventory has no way to admit it.
    assert stated_runnable.qualification == "run_once" and not hasattr(read, "admit")
    assert LegacyInventory().rows() == ()
    row = data["operations"][0]
    damaged_forms: tuple[Any, ...] = (
        [],
        {"schema": SCHEMA, "operations": []},
        {"schema": LEGACY_SCHEMA},
        {"schema": LEGACY_SCHEMA, "operations": {}},
        {"schema": LEGACY_SCHEMA, "operations": [row, row]},
        # A field of the newer schema is refused in an earlier file, not dropped.
        {"schema": LEGACY_SCHEMA, "operations": [{**row, "native_name": "queue"}]},
    )
    for damaged in damaged_forms:
        with pytest.raises(OperationSpecError):
            LegacyInventory.from_json(damaged)
    with pytest.raises(OperationSpecError, match="operation listed twice"):
        LegacyInventory([stated_runnable, stated_runnable])
    unknown_forms: tuple[Any, ...] = (
        [],
        {"operations": []},
        {"schema": "operation-catalogue-v3", "operations": []},
    )
    for unknown in unknown_forms:
        with pytest.raises(
            OperationSpecError, match="not an operation catalogue of a known schema"
        ):
            read_catalogue(unknown)
    # A current row in a file that calls itself the earlier schema is refused too.
    with pytest.raises(OperationSpecError):
        read_catalogue({"schema": LEGACY_SCHEMA, "operations": [list_sessions().to_json()]})
