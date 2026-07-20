# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

import asyncio
import json
import ssl
import time
from collections.abc import Coroutine
from pathlib import Path
from ssl import SSLContext
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from synapse_channel import cli_processes
from synapse_channel.core.capability_card_history import PersistentCapabilityCardHistory
from synapse_channel.core.hub import (
    InsecureBindError,
    SynapseHub,
)
from synapse_channel.core.hub_config import HubConfig, config_fingerprint
from synapse_channel.core.identity_keys import generate_signing_key, public_key_b64
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.protocol import build_envelope
from synapse_channel.core.ratelimit import RateLimiter


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    coro.close()


def test_cmd_hub_runs_and_handles_interrupt() -> None:
    ns = _hub_ns()
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 0

    def interrupt(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise KeyboardInterrupt

    assert cli_processes._cmd_hub(ns, runner=interrupt) == 0


def test_cmd_hub_refuses_insecure_bind(capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise InsecureBindError("Refusing to bind: Synapse Hub bound to ... no token.")

    assert cli_processes._cmd_hub(_hub_ns(host="0.0.0.0"), runner=refuse) == 2
    assert "Refusing to bind" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--rate", "--burst", "--host-rate", "--host-burst"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-1"])
def test_hub_parser_rejects_non_finite_or_negative_limits(flag: str, value: str) -> None:
    """Every hub run rejects unusable limits at the argument boundary.

    ``nan`` silently disables the limiter downstream (``nan > 0`` is false) while
    looking configured, and ``inf`` configures an unbounded bucket; neither may
    survive parsing — with or without a hardening preset.
    """
    from synapse_channel.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["hub", flag, value])


def test_hub_parser_accepts_ordinary_finite_limits() -> None:
    from synapse_channel.cli import build_parser

    args = build_parser().parse_args(
        ["hub", "--rate", "25", "--burst", "10", "--host-rate", "200", "--host-burst", "50"]
    )
    assert (args.rate, args.burst, args.host_rate, args.host_burst) == (25.0, 10.0, 200.0, 50.0)


def _owner_only(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_cmd_hub_metrics_token_file_feeds_the_hub(tmp_path: Path) -> None:
    """`--metrics-token-file` delivers the bearer token without argv exposure."""
    token_file = _owner_only(tmp_path / "metrics-token", "file-bearer\n")
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    ns = _hub_ns(metrics=True, metrics_token_file=str(token_file))
    assert cli_processes._cmd_hub(ns, runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["metrics_token"] == "file-bearer"


def test_cmd_hub_explicit_metrics_token_wins_over_the_file(tmp_path: Path) -> None:
    """Precedence mirrors --token/--token-file: the explicit argv value wins."""
    token_file = _owner_only(tmp_path / "metrics-token", "file-bearer\n")
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    ns = _hub_ns(metrics=True, metrics_token="argv-bearer", metrics_token_file=str(token_file))
    assert cli_processes._cmd_hub(ns, runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["metrics_token"] == "argv-bearer"


def test_cmd_hub_message_auth_key_file_merges_with_argv_keys(tmp_path: Path) -> None:
    """File entries join argv keys, so both sources can rotate together."""
    key_file = _owner_only(
        tmp_path / "hmac-keys", "# rotation 2026-07-14\nfilekey:filesecret:BETA\n"
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    ns = _hub_ns(
        message_auth_key=["argvkey:argvsecret:ALPHA"],
        message_auth_key_file=str(key_file),
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner, hub_factory=build_hub) == 0
    key_ids = [key.key_id for key in captured["per_message_auth_keys"]]
    assert key_ids == ["argvkey", "filekey"]


def test_cmd_hub_refuses_a_group_readable_secret_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lax secret file fails closed by flag and path, never by content."""
    token_file = tmp_path / "metrics-token"
    token_file.write_text("leakable-bearer\n", encoding="utf-8")
    token_file.chmod(0o644)

    ns = _hub_ns(metrics=True, metrics_token_file=str(token_file))
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    err = capsys.readouterr().err
    assert "--metrics-token-file" in err
    assert "chmod 600" in err
    assert "leakable-bearer" not in err


def test_cmd_hub_insecure_bind_refusal_creates_no_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused exposed bind fails before the durable store is constructed.

    SECURITY.md promises the exposure guard runs before durable stores open and that
    a refused start leaves no database file behind; this drives the real command with
    the real ``EventStore`` (no injected factory) and pins exactly that.
    """
    db_path = tmp_path / "refused-hub.db"
    assert cli_processes._cmd_hub(_hub_ns(host="0.0.0.0", db=str(db_path))) == 2
    assert "Refusing to bind" in capsys.readouterr().err
    assert not db_path.exists()


def test_cmd_hub_insecure_bind_precheck_stays_silent_on_the_opt_out(
    tmp_path: Path,
) -> None:
    """`--insecure-off-loopback` still starts, and the store is then constructed.

    The precheck must not refuse (or double-log) the documented opt-out path: the
    warning pass belongs to ``serve()``, so the precheck passes silently and the
    durable store opens exactly as before.
    """
    db_path = tmp_path / "opted-in-hub.db"
    ns = _hub_ns(host="0.0.0.0", db=str(db_path), insecure_off_loopback=True)
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 0
    assert db_path.exists()


def test_cmd_hub_threads_insecure_off_loopback() -> None:
    built: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        built.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(insecure_off_loopback=True), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert built["insecure_off_loopback"] is True


def test_cmd_hub_stamps_the_config_epoch_from_its_arguments() -> None:
    # The CLI constructs SynapseHub(...) directly, which does not run from_config,
    # so config_epoch would be empty and the pinning indicator inert. The command
    # must stamp it from the flat arguments it assembled.
    hubs: list[SynapseHub] = []
    kwargs_seen: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        kwargs_seen.update(kwargs)
        hub = SynapseHub(**kwargs)
        hubs.append(hub)
        return hub

    assert (
        cli_processes._cmd_hub(_hub_ns(max_clients=17), runner=_close_runner, hub_factory=build_hub)
        == 0
    )
    assert hubs[0].config_epoch != ""  # not the inert empty default
    assert hubs[0].config_epoch == config_fingerprint(HubConfig.from_kwargs(kwargs_seen))


def test_cmd_hub_with_db_opens_and_closes_event_store(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    assert cli_processes._cmd_hub(_hub_ns(db=str(db)), runner=_close_runner) == 0
    # The persistent store was created (and closed) for the run.
    assert db.exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"db": None, "hub_id": "hub.example"}, "requires --db"),
        ({"db": "/tmp/aef.db", "hub_id": None}, "requires --hub-id"),
    ],
)
def test_cmd_hub_aef_route_requires_durable_identity_context(
    overrides: dict[str, object], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    ns = _hub_ns(aef_signing_key="/tmp/unused-aef-key", **overrides)
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert message in capsys.readouterr().err


def test_cmd_hub_aef_route_refuses_an_unreadable_signing_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    ns = _hub_ns(
        db=str(db),
        hub_id="hub.example",
        aef_signing_key=str(tmp_path / "missing-key"),
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "cannot read receipt-signing key" in capsys.readouterr().err
    assert not db.exists()


def test_cmd_hub_aef_startup_reconciles_existing_outbox_before_serve(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel.core.aef_emission import AefReceiptLog
    from synapse_channel.core.aef_legacy_mapping import AEF_MAPPED_EVENT_KINDS
    from synapse_channel.core.journal import EventKind
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.receipt_signing import (
        generate_receipt_signing_key,
        load_receipt_signing_key,
    )

    db = tmp_path / "events.db"
    key_path = tmp_path / "receipt-key"
    generate_receipt_signing_key(key_path)
    with EventStore(db, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        legacy_seq = store.append(
            EventKind.CLAIM,
            {
                "task_id": "startup-task",
                "owner": "agent-1",
                "claimed_at": 1_783_940_400.0,
                "lease_expires_at": 1_783_944_000.0,
                "epoch": 1,
                "paths": [],
            },
            ts=1_783_940_400.0,
            durable=True,
        )

    ns = _hub_ns(
        db=str(db),
        hub_id="hub.example",
        aef_signing_key=str(key_path),
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 0
    assert "startup_settled=1" in capsys.readouterr().err

    with EventStore(db, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        assert store.aef_delivery(legacy_seq) is not None
    with AefReceiptLog(
        db,
        hub_id="hub.example",
        signing_key=load_receipt_signing_key(key_path),
    ) as log:
        assert log.count() == 1


def test_cmd_hub_aef_startup_failure_closes_the_authoritative_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sqlite3

    from synapse_channel import cli_processes_hub
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.receipt_signing import generate_receipt_signing_key

    db = tmp_path / "events.db"
    key_path = tmp_path / "receipt-key"
    generate_receipt_signing_key(key_path)
    opened: list[EventStore] = []

    def store_factory(path: str, **kwargs: object) -> EventStore:
        store = EventStore(path, **kwargs)  # type: ignore[arg-type]
        opened.append(store)
        return store

    def fail_startup(_config: object) -> int:
        raise RuntimeError("corrupt pending evidence")

    monkeypatch.setattr(cli_processes_hub, "drain_aef_startup_backlog", fail_startup)
    ns = _hub_ns(
        db=str(db),
        hub_id="hub.example",
        aef_signing_key=str(key_path),
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner, store_factory=store_factory) == 2
    assert "AEF startup reconciliation failed" in capsys.readouterr().err
    assert opened
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].count()


def test_cmd_hub_aef_worker_reconciles_events_accepted_while_serving(tmp_path: Path) -> None:
    from synapse_channel.core.aef_emission import AefReceiptLog
    from synapse_channel.core.aef_legacy_mapping import AEF_MAPPED_EVENT_KINDS
    from synapse_channel.core.journal import EventKind
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.receipt_signing import (
        generate_receipt_signing_key,
        load_receipt_signing_key,
    )

    db = tmp_path / "events.db"
    key_path = tmp_path / "receipt-key"
    generate_receipt_signing_key(key_path)
    accepted: dict[str, int] = {}

    class ProbeHub:
        config_epoch = ""

        def __init__(self, journal: EventStore) -> None:
            self.journal = journal

        async def serve(self, **_kwargs: object) -> None:
            sequence = self.journal.append(
                EventKind.CLAIM,
                {
                    "task_id": "live-task",
                    "owner": "agent-1",
                    "lease_expires_at": 1_783_944_000.0,
                    "epoch": 1,
                    "paths": [],
                },
                ts=1_783_940_400.0,
                durable=True,
            )
            accepted["sequence"] = sequence
            deadline = asyncio.get_running_loop().time() + 5.0
            while self.journal.aef_delivery(sequence) is None:
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("AEF worker did not settle the live event")
                await asyncio.sleep(0.01)

    def build_hub(**kwargs: Any) -> Any:
        journal = kwargs["journal"]
        assert isinstance(journal, EventStore)
        return ProbeHub(journal)

    ns = _hub_ns(
        db=str(db),
        hub_id="hub.example",
        aef_signing_key=str(key_path),
        aef_drain_interval=0.01,
    )
    assert cli_processes._cmd_hub(ns, runner=asyncio.run, hub_factory=build_hub) == 0

    with EventStore(db, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS) as store:
        assert store.aef_delivery(accepted["sequence"]) is not None
    with AefReceiptLog(
        db,
        hub_id="hub.example",
        signing_key=load_receipt_signing_key(key_path),
    ) as log:
        assert log.count() == 1


def test_cmd_hub_db_key_file_requires_db(capsys: pytest.CaptureFixture[str]) -> None:
    """Production CLI refuses --db-key-file without --db."""
    assert (
        cli_processes._cmd_hub(_hub_ns(db=None, db_key_file="/tmp/nope.key"), runner=_close_runner)
        == 2
    )
    assert "--db-key-file requires --db" in capsys.readouterr().err


def test_cmd_hub_with_db_key_file_opens_sqlcipher_store(tmp_path: Path) -> None:
    """Hub CLI opens a real SQLCipher EventStore when --db-key-file is set."""
    pytest.importorskip("sqlcipher3")
    from synapse_channel.core.at_rest import generate_key_file
    from synapse_channel.core.persistence import EventStore

    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "enc.db"
    opened: list[EventStore] = []

    def store_factory(path: str, *, key_file: str | None = None) -> EventStore:
        store = EventStore(path, key_file=key_file)
        opened.append(store)
        return store

    assert (
        cli_processes._cmd_hub(
            _hub_ns(db=str(db), db_key_file=str(key)),
            runner=_close_runner,
            store_factory=store_factory,
        )
        == 0
    )
    assert opened and opened[0].encrypted is True
    assert db.exists()
    # reopen without key must fail closed
    from synapse_channel.core.persistence_sqlcipher import SqlCipherKeyError

    with pytest.raises(SqlCipherKeyError):
        EventStore(db)


def test_cmd_hub_with_rate_limit_builds_limiter() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(rate=5.0, burst=10.0), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["rate_limiter"] is not None


def test_cmd_hub_wires_relay_log(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    log = tmp_path / "relay.ndjson"
    assert (
        cli_processes._cmd_hub(
            _hub_ns(relay_log=str(log), relay_max_lines=42),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["relay_log"] == str(log)
    assert captured["relay_max_lines"] == 42


def test_cmd_hub_threads_per_agent_quotas() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(max_claims_per_agent=7, max_offers_per_agent=3, max_paths_per_claim=9),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["max_claims_per_agent"] == 7
    assert captured["max_offers_per_agent"] == 3
    assert captured["max_paths_per_claim"] == 9


def test_cmd_hub_threads_blackboard_and_memory_quotas() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                max_progress=99,
                max_progress_per_author=7,
                max_progress_per_task=8,
                max_findings_per_agent=9,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["max_progress"] == 99
    assert captured["max_progress_per_author"] == 7
    assert captured["max_progress_per_task"] == 8
    assert captured["max_findings_per_agent"] == 9


def test_cmd_hub_threads_metrics_query_token_ok() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(metrics_query_token_ok=True), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["metrics_query_token_ok"] is True


def test_cmd_hub_threads_max_unauth_clients() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(max_unauth_clients=8), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["max_unauth_clients"] == 8


def test_cmd_hub_threads_max_connections_per_host() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(max_connections_per_host=2), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["max_connections_per_host"] == 2


def test_cmd_hub_disables_max_connections_per_host_when_zero() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["max_connections_per_host"] is None


def test_cmd_hub_builds_host_rate_limiter_when_enabled() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(host_rate=5.0, host_burst=12.0),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert isinstance(captured["host_rate_limiter"], RateLimiter)


def test_cmd_hub_host_rate_limiter_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["host_rate_limiter"] is None


def test_cmd_hub_threads_compact_hint_threshold() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(compact_hint_threshold=42), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["compact_hint_threshold"] == 42


def test_cmd_hub_threads_takeover_cooldown() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(takeover_cooldown=5.5), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["takeover_cooldown"] == 5.5


def test_cmd_hub_threads_shutdown_close_timeout() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(shutdown_close_timeout=2.5), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["shutdown_close_timeout"] == 2.5


def test_cmd_hub_configures_logging() -> None:
    captured: dict[str, Any] = {}
    assert (
        cli_processes._cmd_hub(
            _hub_ns(log_format="json", log_level="DEBUG"),
            runner=_close_runner,
            logging_configurator=lambda **kw: captured.update(kw),
        )
        == 0
    )
    assert captured == {"log_format": "json", "level": "DEBUG"}


def test_cmd_hub_with_token_builds_authenticator() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(_hub_ns(token="s3cret"), runner=_close_runner, hub_factory=build_hub)
        == 0
    )
    assert captured["authenticator"] is not None


def test_cmd_hub_without_token_has_no_authenticator() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["authenticator"] is None


def test_cmd_hub_threads_message_authentication_options() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                message_auth_key=["main:shared-secret:ALPHA,BETA"],
                require_message_auth=True,
                message_auth_window_seconds=12.5,
                message_auth_replay_capacity=99,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_per_message_auth"] is True
    assert captured["per_message_auth_window_seconds"] == 12.5
    assert captured["per_message_auth_replay_capacity"] == 99
    assert captured["per_message_auth_keys"][0].key_id == "main"
    assert captured["per_message_auth_keys"][0].secret == b"shared-secret"
    assert captured["per_message_auth_keys"][0].senders == frozenset({"ALPHA", "BETA"})
    assert captured["per_message_auth_replay_store"] is None
    assert captured["per_message_auth_sequence_floor_mode"] is SequenceFloorMode.OFF


def test_cmd_hub_auto_durable_replay_survives_runtime_restart(tmp_path: Path) -> None:
    """A journalled authenticated hub refuses the same fresh frame after restart."""
    db = tmp_path / "hub.db"
    hubs: list[SynapseHub] = []
    now = time.time()
    key = MessageAuthKey(
        key_id="main",
        secret=b"shared-secret",
        senders=frozenset({"ALPHA"}),
    )
    frame = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=now),
        key=key,
        nonce="restart-proof",
        sequence=1,
        timestamp=now,
    )
    outcomes: list[VerificationResult] = []

    def build_hub(**kwargs: Any) -> SynapseHub:
        hub = SynapseHub(**kwargs)
        hubs.append(hub)
        return hub

    def verify_then_close(coro: Coroutine[Any, Any, None]) -> None:
        hub = hubs[-1]
        outcomes.append(
            verify_frame(
                frame,
                keys=hub.per_message_auth_keys,
                replay_cache=hub._message_replay,
                now=now + len(outcomes) * 0.1,
                required_sender="ALPHA",
            )
        )
        coro.close()

    args = _hub_ns(
        db=str(db),
        message_auth_key=["main:shared-secret:ALPHA"],
        require_message_auth=True,
    )
    assert cli_processes._cmd_hub(args, runner=verify_then_close, hub_factory=build_hub) == 0
    assert cli_processes._cmd_hub(args, runner=verify_then_close, hub_factory=build_hub) == 0

    assert outcomes == [VerificationResult.OK, VerificationResult.REPLAYED]
    assert Path(f"{db}.message-auth.db").is_file()


def test_cmd_hub_requires_durable_store_for_sequence_floor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                message_auth_key=["main:shared-secret:ALPHA"],
                require_message_auth=True,
                message_auth_sequence_floor_mode="strict",
            ),
            runner=_close_runner,
        )
        == 2
    )
    assert "requires a durable replay ledger" in capsys.readouterr().err


def test_cmd_hub_rejects_unused_explicit_replay_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(message_auth_replay_db=str(tmp_path / "unused.db")),
            runner=_close_runner,
        )
        == 2
    )
    assert "requires --require-message-auth" in capsys.readouterr().err


def test_cmd_hub_rejects_malformed_message_auth_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(message_auth_key=["missing-separator"]),
            runner=_close_runner,
        )
        == 2
    )

    assert (
        "--message-auth-key / --message-auth-key-file entries must use "
        "KEY_ID:SECRET:SENDER[,SENDER...]" in capsys.readouterr().err
    )


def test_cmd_hub_threads_acl_policy(tmp_path: Path) -> None:
    policy = tmp_path / "acl.json"
    policy.write_text(
        '{"rules": [{"permission": "claim", "target_kind": "path", "target_pattern": "src/*"}]}',
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(token="t", acl_policy=str(policy), require_acl=True),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_acl"] is True
    assert captured["acl_policy"] is not None
    assert len(captured["acl_policy"].rules) == 1


def test_cmd_hub_rejects_malformed_acl_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "acl.json"
    policy.write_text("{}", encoding="utf-8")
    assert (
        cli_processes._cmd_hub(
            _hub_ns(acl_policy=str(policy), require_acl=True), runner=_close_runner
        )
        == 2
    )
    assert "rules" in capsys.readouterr().err


def test_cmd_hub_warns_on_require_acl_without_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_processes._cmd_hub(_hub_ns(token=None, require_acl=True), runner=_close_runner) == 0
    assert "WARNING --require-acl without --token" in capsys.readouterr().err


def test_cmd_hub_threads_tls_context_to_serve() -> None:
    served: dict[str, Any] = {}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    class CapturingHub(SynapseHub):
        async def serve(
            self,
            host: str = "localhost",
            port: int = 8876,
            *,
            ssl_context: SSLContext | None = None,
        ) -> None:
            served.update({"host": host, "port": port, "ssl_context": ssl_context})

    assert (
        cli_processes._cmd_hub(
            _hub_ns(tls_certfile="cert.pem", tls_keyfile="key.pem"),
            runner=lambda coro: asyncio.run(coro),
            hub_factory=lambda **kwargs: CapturingHub(**kwargs),
            tls_context_factory=lambda certfile, keyfile: context,
        )
        == 0
    )

    assert served == {"host": "localhost", "port": 8876, "ssl_context": context}


def test_cmd_hub_rejects_incomplete_tls_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_processes._cmd_hub(_hub_ns(tls_certfile="cert.pem"), runner=_close_runner) == 2

    assert "requires both --tls-certfile and --tls-keyfile" in capsys.readouterr().err


def _federation_store(tmp_path: Path, *, grant_scope: bool = True) -> str:
    """Write a federation store with one peer and return its path.

    With ``grant_scope`` the peering maps a scope grant inside its granted
    namespace, so it could authorise a cross-domain frame under per-message
    authentication; without it the peering is observe-only by construction.
    """
    from synapse_channel.core.federation import FederationPeer, ScopeGrant
    from synapse_channel.core.federation_store import (
        FederationRecord,
        PeerProvenance,
        save_store,
    )

    peer = FederationPeer(
        domain_id="domain-b",
        namespaces=frozenset({"SYNAPSE-CHANNEL"}),
        certificate_pins=frozenset({"sha256:aa"}),
        signing_key_ids=frozenset({"domain-b:main"}),
        scope_grants=(ScopeGrant("message", "SYNAPSE-CHANNEL"),) if grant_scope else (),
    )
    store = tmp_path / "federation.json"
    save_store(store, [FederationRecord(peer, PeerProvenance("bundle", 1.0, "ops"))])
    return str(store)


def test_cmd_hub_composes_federation_store_into_the_bundle(tmp_path: Path) -> None:
    from synapse_channel.core.federation import FederationBundle

    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(federation_store=_federation_store(tmp_path), require_message_auth=True),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    bundle = captured["federation_bundle"]
    assert isinstance(bundle, FederationBundle)
    assert bundle.domains() == ("domain-b",)


def test_cmd_hub_refuses_scope_granting_store_without_message_auth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store granting cross-domain scope cannot be enforced without message auth."""
    hub_calls: list[dict[str, Any]] = []

    def build_hub(**kwargs: Any) -> SynapseHub:
        hub_calls.append(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(federation_store=_federation_store(tmp_path), require_message_auth=False),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 2
    )
    # the hub never starts: the config claims scope it cannot honour
    assert hub_calls == []
    err = capsys.readouterr().err
    assert "grants cross-domain scope" in err
    assert "--federation-observe-only" in err


def test_cmd_hub_observe_only_loads_scope_granting_store_without_message_auth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The declared observe-only intent is the escape hatch from the fatal refusal."""
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                federation_store=_federation_store(tmp_path),
                require_message_auth=False,
                federation_observe_only=True,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["federation_bundle"] is not None
    err = capsys.readouterr().err
    assert "observe-only" in err
    assert "refused deny-closed" in err


def test_cmd_hub_keeps_warning_for_store_that_cannot_authorise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store whose peerings grant no scope is observe-only by construction."""
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                federation_store=_federation_store(tmp_path, grant_scope=False),
                require_message_auth=False,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    # the bundle is still composed, but a warning flags that it authorises nothing
    assert captured["federation_bundle"] is not None
    assert "--federation-store without --require-message-auth" in capsys.readouterr().err


def test_cmd_hub_rejects_observe_only_with_message_auth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                federation_store=_federation_store(tmp_path),
                require_message_auth=True,
                federation_observe_only=True,
            ),
            runner=_close_runner,
        )
        == 2
    )
    assert "contradicts --require-message-auth" in capsys.readouterr().err


def test_cmd_hub_rejects_observe_only_without_store(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(federation_observe_only=True),
            runner=_close_runner,
        )
        == 2
    )
    assert "requires --federation-store" in capsys.readouterr().err


def test_cmd_hub_leaves_federation_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["federation_bundle"] is None


def test_cmd_hub_rejects_malformed_federation_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "bad.json"
    store.write_text("{not json", encoding="utf-8")
    assert cli_processes._cmd_hub(_hub_ns(federation_store=str(store)), runner=_close_runner) == 2
    assert "federation store is not valid JSON" in capsys.readouterr().err


def test_cmd_hub_passes_the_federation_offer_path(tmp_path: Path) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps({"domain_id": "lab-a"}), encoding="utf-8")
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    ns = _hub_ns(federation_offer=str(offer))
    assert cli_processes._cmd_hub(ns, runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["federation_offer_path"] == str(offer)


def test_cmd_hub_leaves_the_federation_offer_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["federation_offer_path"] is None


def test_cmd_hub_rejects_a_missing_federation_offer_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ns = _hub_ns(federation_offer=str(tmp_path / "absent.json"))
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "cannot serve --federation-offer" in capsys.readouterr().err


def test_cmd_hub_rejects_a_non_json_federation_offer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text("{not json", encoding="utf-8")
    assert cli_processes._cmd_hub(_hub_ns(federation_offer=str(offer)), runner=_close_runner) == 2
    assert "cannot serve --federation-offer" in capsys.readouterr().err


def test_cmd_hub_rejects_a_malformed_federation_offer_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps({"namespaces": ["lab-a/shared"]}), encoding="utf-8")
    assert cli_processes._cmd_hub(_hub_ns(federation_offer=str(offer)), runner=_close_runner) == 2
    assert "cannot serve --federation-offer" in capsys.readouterr().err


def test_cmd_hub_rejects_namespace_owner_without_hub_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(namespace_owner=["OWNED=syn-a"])
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "--namespace-owner requires --hub-id" in capsys.readouterr().err


def test_cmd_hub_rejects_watch_without_namespace_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(hub_id="syn-a", multihub_watch=["hub-b=ws://b:8876"])
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "--multihub-watch requires --namespace-owner" in capsys.readouterr().err


def test_cmd_hub_rejects_a_malformed_namespace_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(hub_id="syn-a", namespace_owner=["OWNED"])
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "NS=HUB_ID" in capsys.readouterr().err


def test_cmd_hub_rejects_a_repeated_namespace_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(hub_id="syn-a", namespace_owner=["OWNED=syn-a", "OWNED=syn-b"])
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "names namespace 'OWNED' twice" in capsys.readouterr().err


def test_cmd_hub_rejects_a_malformed_watch_peer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(hub_id="syn-a", namespace_owner=["OWNED=syn-a"], multihub_watch=["no-separator"])
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "PEER=URI" in capsys.readouterr().err


def test_cmd_hub_wires_ownership_watch_feed_and_transition_journal(tmp_path: Path) -> None:
    from synapse_channel.core.multihub_watch import MultiHubWatch
    from synapse_channel.core.namespace_ownership import NamespaceOwnership

    captured: dict[str, Any] = {}
    closed: list[str] = []

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    def close_runner(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        closed.append("closed")

    ns = _hub_ns(
        db=str(tmp_path / "hub.db"),
        hub_id="syn-a",
        namespace_owner=["OWNED=syn-a", "THEIRS=syn-b"],
        multihub_watch=["hub-b=ws://b:8876"],
        multihub_watch_interval=7.0,
    )
    assert cli_processes._cmd_hub(ns, runner=close_runner, hub_factory=build_hub) == 0
    assert captured["hub_id"] == "syn-a"
    ownership = captured["namespace_ownership"]
    assert isinstance(ownership, NamespaceOwnership)
    assert ownership.owners == {"OWNED": "syn-a", "THEIRS": "syn-b"}
    assert ownership.local_hub_id == "syn-a"
    feed = captured["observed_asserting_hubs"]
    watch = feed.__self__
    assert isinstance(watch, MultiHubWatch)
    assert watch.interval == 7.0
    assert watch._namespace_ownership is ownership
    assert watch._journal is not None
    assert closed == ["closed"]


def test_cmd_hub_leaves_ownership_and_watch_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["namespace_ownership"] is None
    assert captured["observed_asserting_hubs"] is None
    assert captured["hub_id"] is None


async def test_serve_with_watch_cancels_the_watch_when_serving_ends() -> None:
    from synapse_channel.cli_processes_hub import _serve_with_watch
    from synapse_channel.core.multihub_watch import MultiHubWatch

    watch = MultiHubWatch({}, local_id="syn-a", interval=60.0)
    served: list[str] = []

    async def serve() -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        served.append("served")

    await _serve_with_watch(serve, watch)
    assert served == ["served"]
    # The watch task was cancelled and awaited; nothing is left running.
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    assert pending == []


def test_parse_message_auth_keys_rejects_empty_fields() -> None:
    """A key with a blank id, secret, or sender list is refused with the format."""
    from synapse_channel.cli_processes_hub import _parse_message_auth_keys

    with pytest.raises(ValueError, match="KEY_ID:SECRET:SENDER"):
        _parse_message_auth_keys([":secret:ALPHA"])
    with pytest.raises(ValueError, match="KEY_ID:SECRET:SENDER"):
        _parse_message_auth_keys(["main:secret:  ,  "])


def test_cmd_hub_no_role_grants_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["role_grants"] is None
    assert captured["require_role_claim"] is False


def test_cmd_hub_threads_role_grants(tmp_path: Path) -> None:
    store = tmp_path / "role-grants.json"
    store.write_text(
        json.dumps({"grants": {"proj/coordinator": ["proj/claude"]}}), encoding="utf-8"
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(role_grants=str(store), require_role_claim=True, token="t"),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_role_claim"] is True
    assert captured["role_grants"].may_claim("proj/claude", "proj/coordinator")


def test_cmd_hub_rejects_a_malformed_role_grants_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "role-grants.json"
    store.write_text("{not json", encoding="utf-8")

    assert cli_processes._cmd_hub(_hub_ns(role_grants=str(store)), runner=_close_runner) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_cmd_hub_warns_on_require_role_claim_without_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_processes._cmd_hub(_hub_ns(require_role_claim=True), runner=_close_runner) == 0
    assert "--require-role-claim without --token" in capsys.readouterr().err


def _write_identity_trust(path: Path) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )
    path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "key_id": "k",
                        "public_key": base64.b64encode(raw).decode("ascii"),
                        "senders": ["proj/claude"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_cmd_hub_no_identity_binding_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["identity_trust_bundle"] is None
    assert captured["require_identity_binding"] is False


def test_cmd_hub_threads_identity_trust(tmp_path: Path) -> None:
    trust = tmp_path / "identity-trust.json"
    _write_identity_trust(trust)
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(identity_trust=str(trust), require_identity_binding=True, token="t"),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_identity_binding"] is True
    assert "k" in captured["identity_trust_bundle"].keys


def test_cmd_hub_rejects_malformed_identity_trust(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust = tmp_path / "identity-trust.json"
    trust.write_text("{not json", encoding="utf-8")

    assert cli_processes._cmd_hub(_hub_ns(identity_trust=str(trust)), runner=_close_runner) == 2
    assert "invalid identity trust JSON" in capsys.readouterr().err


def test_cmd_hub_require_identity_binding_without_trust_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_processes._cmd_hub(_hub_ns(require_identity_binding=True), runner=_close_runner) == 2
    assert "--require-identity-binding requires --identity-trust" in capsys.readouterr().err


def test_cmd_hub_threads_capability_card_trust(tmp_path: Path) -> None:
    trust = tmp_path / "capability-card-trust.json"
    trust.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "agents": ["P/worker"],
                        "key_id": "P:key",
                        "projects": ["P"],
                        "public_key": public_key_b64(generate_signing_key()),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                capability_card_trust=str(trust),
                capability_card_history_db=str(tmp_path / "card-history.db"),
                capability_card_clock_skew_seconds=4.0,
                capability_card_history_capacity=7,
                capability_card_history_retention_seconds=8.0,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    bundle = captured["capability_card_trust_bundle"]
    assert "P:key" in bundle.keys
    assert bundle.clock_skew_seconds == 4.0
    assert isinstance(bundle.history, PersistentCapabilityCardHistory)
    assert bundle.history.path == tmp_path / "card-history.db"
    assert bundle.history.max_entries == 7
    assert bundle.history.retention_seconds == 8.0


def test_cmd_hub_rejects_malformed_capability_card_trust(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust = tmp_path / "capability-card-trust.json"
    trust.write_text("{not json", encoding="utf-8")

    assert (
        cli_processes._cmd_hub(_hub_ns(capability_card_trust=str(trust)), runner=_close_runner) == 2
    )
    assert "invalid capability-card trust JSON" in capsys.readouterr().err


def test_cmd_hub_rejects_history_without_trust_and_malformed_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = tmp_path / "card-history.db"
    assert (
        cli_processes._cmd_hub(
            _hub_ns(capability_card_history_db=str(history)),
            runner=_close_runner,
        )
        == 2
    )
    assert "--capability-card-history-db requires" in capsys.readouterr().err
    assert not history.exists()

    trust = tmp_path / "capability-card-trust.json"
    trust.write_text('{"keys": []}', encoding="utf-8")
    history.touch(mode=0o600)
    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                capability_card_trust=str(trust),
                capability_card_history_db=str(history),
            ),
            runner=_close_runner,
        )
        == 2
    )
    assert "unknown or missing schema" in capsys.readouterr().err


def test_cmd_hub_threads_private_directed_messages() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(private_directed_messages=True),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["private_directed_messages"] is True


def test_cmd_hub_private_directed_messages_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["private_directed_messages"] is False


def test_cmd_hub_threads_stale_recipient_warning_opt_out() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                warn_stale_recipients=False,
                recipient_liveness_window=30.0,
                waiter_liveness_window=15.0,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["warn_stale_recipients"] is False
    assert captured["recipient_liveness_window"] == 30.0
    assert captured["waiter_liveness_window"] == 15.0


def test_cmd_hub_stale_recipient_warning_on_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["warn_stale_recipients"] is True


def test_cmd_hub_rejects_a_pin_for_an_unwatched_peer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(
        hub_id="syn-a",
        namespace_owner=["OWNED=syn-a"],
        multihub_watch=["hub-b=wss://b:443"],
        multihub_watch_pin=["ghost=sha256:" + "a" * 64],
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "does not watch" in capsys.readouterr().err


def test_cmd_hub_accepts_a_pin_for_a_watched_peer() -> None:
    from synapse_channel.core.multihub_watch import MultiHubWatch

    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    ns = _hub_ns(
        hub_id="syn-a",
        namespace_owner=["OWNED=syn-a"],
        multihub_watch=["hub-b=wss://b:443"],
        multihub_watch_pin=["hub-b=sha256:" + "a" * 64],
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner, hub_factory=build_hub) == 0
    feed = captured["observed_asserting_hubs"]
    assert isinstance(feed.__self__, MultiHubWatch)
