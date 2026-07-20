# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_legacy_mapping import AEF_MAPPED_EVENT_KINDS
from synapse_channel.core.aef_runtime import (
    AefRuntimeConfig,
    drain_aef_database,
    drain_aef_startup_backlog,
    run_aef_outbox_worker,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.receipt_signing import (
    generate_receipt_signing_key,
    load_receipt_signing_key,
)


def _config(tmp_path: Path, *, interval: float = 0.01) -> AefRuntimeConfig:
    key_path = tmp_path / "receipt-key"
    generate_receipt_signing_key(key_path)
    return AefRuntimeConfig(
        db_path=str(tmp_path / "hub.db"),
        hub_id="hub.example",
        signing_key=load_receipt_signing_key(key_path),
        interval_seconds=interval,
    )


def _claim(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "owner": "agent-1",
        "lease_expires_at": 1_783_944_000.0,
        "epoch": 1,
        "paths": [],
    }


def _append_claim(config: AefRuntimeConfig, task_id: str) -> int:
    with EventStore(config.db_path, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        return store.append(
            EventKind.CLAIM,
            _claim(task_id),
            ts=1_783_940_400.0,
            durable=True,
        )


@pytest.mark.parametrize("interval", [0.0, -1.0, float("inf"), float("nan")])
def test_runtime_config_rejects_non_positive_or_non_finite_interval(
    tmp_path: Path, interval: float
) -> None:
    key_path = tmp_path / "receipt-key"
    generate_receipt_signing_key(key_path)
    with pytest.raises(ValueError, match="positive finite"):
        AefRuntimeConfig(
            db_path=str(tmp_path / "hub.db"),
            hub_id="hub.example",
            signing_key=load_receipt_signing_key(key_path),
            interval_seconds=interval,
        )


@pytest.mark.parametrize(
    ("db_path", "hub_id", "message"),
    [("", "hub.example", "database path"), ("/tmp/hub.db", "", "stable hub id")],
)
def test_runtime_config_requires_database_and_stable_hub_identity(
    tmp_path: Path, db_path: str, hub_id: str, message: str
) -> None:
    key_path = tmp_path / "receipt-key"
    generate_receipt_signing_key(key_path)
    with pytest.raises(ValueError, match=message):
        AefRuntimeConfig(
            db_path=db_path,
            hub_id=hub_id,
            signing_key=load_receipt_signing_key(key_path),
        )


def test_one_shot_drain_uses_separate_connections_and_binds_receipt(tmp_path: Path) -> None:
    config = _config(tmp_path)
    legacy_seq = _append_claim(config, "task-1")

    assert drain_aef_database(config) == 1

    with EventStore(config.db_path, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        receipt_id = store.aef_delivery(legacy_seq)
    with AefReceiptLog(config.db_path, hub_id=config.hub_id, signing_key=config.signing_key) as log:
        receipt = log.receipt_for_legacy_seq(legacy_seq)
    assert receipt is not None
    assert receipt_id == receipt["receipt_id"]


def test_startup_drain_settles_more_than_one_runtime_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with EventStore(config.db_path, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        for index in range(1_005):
            store.append(
                EventKind.CLAIM,
                _claim(f"task-{index}"),
                ts=1_783_940_400.0 + index,
                durable=True,
            )

    assert drain_aef_startup_backlog(config) == 1_005
    with EventStore(config.db_path, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        assert store.pending_aef_events() == ()


def test_worker_retries_after_error_without_losing_pending_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    legacy_seq = _append_claim(config, "task-1")
    stop = threading.Event()
    errors: list[Exception] = []

    from synapse_channel.core import aef_runtime

    real_drain = aef_runtime.drain_aef_database
    attempts = 0

    def flaky(
        current: AefRuntimeConfig,
        *,
        batch_size: int = 100,
        max_receipts: int = 1_000,
    ) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary verifier failure")
        settled = real_drain(current, batch_size=batch_size, max_receipts=max_receipts)
        stop.set()
        return settled

    monkeypatch.setattr(aef_runtime, "drain_aef_database", flaky)
    thread = threading.Thread(
        target=run_aef_outbox_worker,
        args=(config, stop),
        kwargs={"on_error": errors.append},
    )
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert attempts == 2
    assert [str(error) for error in errors] == ["temporary verifier failure"]
    with EventStore(config.db_path, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        assert store.aef_delivery(legacy_seq) is not None


def test_worker_stop_is_bounded_when_no_events_exist(tmp_path: Path) -> None:
    config = _config(tmp_path, interval=30.0)
    stop = threading.Event()
    thread = threading.Thread(target=run_aef_outbox_worker, args=(config, stop))
    thread.start()
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_worker_logs_an_error_when_no_callback_is_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config = _config(tmp_path)
    stop = threading.Event()

    from synapse_channel.core import aef_runtime

    def broken(_config: AefRuntimeConfig, **_kwargs: object) -> int:
        stop.set()
        raise RuntimeError("broken evidence store")

    monkeypatch.setattr(aef_runtime, "drain_aef_database", broken)
    with caplog.at_level(logging.ERROR):
        run_aef_outbox_worker(config, stop)
    assert "durable rows remain pending" in caplog.text


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"batch_size": 0}, "batch size"),
        ({"batch_size": True}, "batch size"),
        ({"batch_size": 10_001}, "batch size"),
        ({"max_receipts": 0}, "receipt limit"),
        ({"max_receipts": True}, "receipt limit"),
    ],
)
def test_drain_rejects_invalid_bounds(
    tmp_path: Path, kwargs: dict[str, object], message: str
) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError, match=message):
        drain_aef_database(config, **kwargs)  # type: ignore[arg-type]
