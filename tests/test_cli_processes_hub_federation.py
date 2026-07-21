# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cmd_hub federation and multi-hub wiring

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import (
    _close_runner,
    _federation_store,
)
from synapse_channel import cli_processes
from synapse_channel.core.hub import (
    SynapseHub,
)


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


def test_cmd_hub_rejects_claim_peer_without_namespace_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(hub_id="syn-a", claim_peer=["syn-b=ws://b:8876"])
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "--claim-peer requires --namespace-owner" in capsys.readouterr().err


def test_cmd_hub_rejects_a_malformed_claim_peer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(hub_id="syn-a", namespace_owner=["OWNED=syn-a"], claim_peer=["no-separator"])
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "HUB_ID=URI" in capsys.readouterr().err


def test_cmd_hub_rejects_a_repeated_claim_peer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = _hub_ns(
        hub_id="syn-a",
        namespace_owner=["OWNED=syn-a"],
        claim_peer=["syn-b=ws://b:8876", "syn-b=ws://c:8876"],
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert "names hub 'syn-b' twice" in capsys.readouterr().err


def test_cmd_hub_wires_claim_peers_forwarding_route(tmp_path: Path) -> None:
    from synapse_channel.core.multihub_claim_transport import ClaimForwardPeer

    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    def close_runner(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()

    ns = _hub_ns(
        db=str(tmp_path / "hub.db"),
        hub_id="syn-a",
        namespace_owner=["THEIRS=syn-b"],
        claim_peer=["syn-b=ws://b:8876"],
        claim_peer_token="tok-b",
    )
    assert cli_processes._cmd_hub(ns, runner=close_runner, hub_factory=build_hub) == 0
    assert captured["claim_peers"] == {"syn-b": ClaimForwardPeer(uri="ws://b:8876", token="tok-b")}


def test_cmd_hub_leaves_claim_peers_off_by_default(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    def close_runner(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()

    ns = _hub_ns(db=str(tmp_path / "hub.db"))
    assert cli_processes._cmd_hub(ns, runner=close_runner, hub_factory=build_hub) == 0
    assert captured["claim_peers"] is None


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
