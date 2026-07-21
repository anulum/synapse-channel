# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the at-rest plaintext-store bind refusal

"""An exposed hub refuses a plaintext ``--db`` event store by default.

Proportionate to exposure, mirroring the R4 bind flip: a loopback / single-owner
hub keeps a plaintext store exactly as before; only an off-loopback bind that
keeps a plaintext ``--db`` is refused, because the durable coordination log would
then sit unencrypted on a networked host's disk. These tests pin that posture —
the problem fires only for off-loopback + configured db + not-encrypted, the guard
raises :class:`AtRestBindError` by default, ``--insecure-plaintext-at-rest``
downgrades it to a single warning, an encrypted store clears it, and a missing
SQLCipher driver adds the install hint.
"""

from __future__ import annotations

import logging

import pytest

from synapse_channel.core.at_rest_guard import (
    AtRestBindError,
    guard_at_rest,
    plaintext_store_problem,
)

logger = logging.getLogger("synapse.hub")


def test_plaintext_store_problem_empty_on_loopback() -> None:
    for host in ("localhost", "127.0.0.1", "::1"):
        assert (
            plaintext_store_problem(host, db="events.db", encrypted=False, sqlcipher_available=True)
            is None
        )


def test_plaintext_store_problem_empty_without_a_durable_store() -> None:
    assert (
        plaintext_store_problem("0.0.0.0", db=None, encrypted=False, sqlcipher_available=True)
        is None
    )


def test_plaintext_store_problem_empty_when_store_is_encrypted() -> None:
    assert (
        plaintext_store_problem("0.0.0.0", db="events.db", encrypted=True, sqlcipher_available=True)
        is None
    )


def test_plaintext_store_problem_names_the_store_and_remedy() -> None:
    problem = plaintext_store_problem(
        "192.168.1.20", db="events.db", encrypted=False, sqlcipher_available=True
    )
    assert problem is not None
    assert "'192.168.1.20'" in problem
    assert "'events.db'" in problem
    assert "--db-key-file" in problem
    assert "migrate-sqlcipher" in problem
    # The driver is present, so no install hint.
    assert "synapse-channel[sqlcipher]" not in problem


def test_plaintext_store_problem_adds_install_hint_when_driver_absent() -> None:
    problem = plaintext_store_problem(
        "0.0.0.0", db="events.db", encrypted=False, sqlcipher_available=False
    )
    assert problem is not None
    assert "synapse-channel[sqlcipher]" in problem


def test_guard_refuses_plaintext_store_off_loopback() -> None:
    with pytest.raises(AtRestBindError, match="plaintext event store") as exc_info:
        guard_at_rest(
            "0.0.0.0",
            db="events.db",
            encrypted=False,
            insecure_plaintext_at_rest=False,
            sqlcipher_available=True,
            logger=logger,
        )
    message = str(exc_info.value)
    assert "Refusing to bind" in message
    assert "--insecure-plaintext-at-rest" in message


def test_guard_downgrades_to_single_warning_when_overridden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="synapse.hub"):
        guard_at_rest(
            "0.0.0.0",
            db="events.db",
            encrypted=False,
            insecure_plaintext_at_rest=True,
            sqlcipher_available=True,
            logger=logger,
        )
    warnings = [r for r in caplog.records if "plaintext event store" in r.getMessage()]
    assert len(warnings) == 1


def test_guard_stays_silent_for_an_encrypted_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="synapse.hub"):
        guard_at_rest(
            "0.0.0.0",
            db="events.db",
            encrypted=True,
            insecure_plaintext_at_rest=False,
            sqlcipher_available=True,
            logger=logger,
        )
    assert caplog.records == []


def test_guard_stays_silent_on_loopback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="synapse.hub"):
        guard_at_rest(
            "127.0.0.1",
            db="events.db",
            encrypted=False,
            insecure_plaintext_at_rest=False,
            sqlcipher_available=True,
            logger=logger,
        )
    assert caplog.records == []


def test_hub_guard_refuses_plaintext_journal_off_loopback(tmp_path: object) -> None:
    from synapse_channel.core.hub import SynapseHub
    from synapse_channel.core.persistence import EventStore

    store = EventStore(tmp_path / "events.db")  # type: ignore[operator]
    try:
        assert store.encrypted is False
        hub = SynapseHub(journal=store)
        with pytest.raises(AtRestBindError, match="plaintext event store"):
            hub._guard_at_rest("0.0.0.0")
        # Loopback is unaffected.
        hub._guard_at_rest("127.0.0.1")
    finally:
        store.close()


def test_hub_guard_allows_a_hub_with_no_journal() -> None:
    from synapse_channel.core.hub import SynapseHub

    SynapseHub()._guard_at_rest("0.0.0.0")


def test_hub_guard_downgrades_under_the_override(
    tmp_path: object, caplog: pytest.LogCaptureFixture
) -> None:
    from synapse_channel.core.hub import SynapseHub
    from synapse_channel.core.persistence import EventStore

    store = EventStore(tmp_path / "events.db")  # type: ignore[operator]
    try:
        hub = SynapseHub(journal=store, insecure_plaintext_at_rest=True)
        with caplog.at_level("WARNING", logger="synapse.hub"):
            hub._guard_at_rest("0.0.0.0")
        assert any("plaintext event store" in r.getMessage() for r in caplog.records)
    finally:
        store.close()
