# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cmd_hub lifecycle, limits, storage, and quota wiring

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import (
    _close_runner,
    _federation_store,
    _owner_only,
)
from synapse_channel import cli_processes
from synapse_channel.core.hub import (
    InsecureBindError,
    SynapseHub,
)
from synapse_channel.core.hub_config import HubConfig, config_fingerprint
from synapse_channel.core.ratelimit import RateLimiter


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
