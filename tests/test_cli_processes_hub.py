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
from collections.abc import Coroutine
from pathlib import Path
from ssl import SSLContext
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from synapse_channel import cli_processes
from synapse_channel.core.hub import (
    InsecureBindError,
    SynapseHub,
)
from synapse_channel.core.hub_config import HubConfig, config_fingerprint
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


def test_cmd_hub_rejects_malformed_message_auth_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(message_auth_key=["missing-separator"]),
            runner=_close_runner,
        )
        == 2
    )

    assert "--message-auth-key must use KEY_ID:SECRET:SENDER[,SENDER...]" in capsys.readouterr().err


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


def test_cmd_hub_wires_ownership_and_the_watch_feed(tmp_path: Path) -> None:
    del tmp_path
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
