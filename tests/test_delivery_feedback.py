# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for sender-visible delivery feedback

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from synapse_channel.core.directed_delivery_liveness import (
    NO_LIVE_RECIPIENT,
    DeliveryLiveness,
)
from synapse_channel.core.handlers import delivery_feedback as df
from synapse_channel.core.pending_receipts import ReceiptEntry
from synapse_channel.core.wake_capability import (
    WAKE_DIRECT,
    WAKE_PASSIVE,
    WAKE_UNKNOWN,
)

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


class _FakePending:
    """Records ``remember`` calls and returns a pre-set eviction."""

    def __init__(self, evicted: Any = None) -> None:
        self.evicted = evicted
        self.calls: list[tuple[int, str, str, int]] = []

    def remember(self, seq: int, *, sender: str, target: str, message_id: int) -> Any:
        self.calls.append((seq, sender, target, message_id))
        return self.evicted


class _FakeHub:
    """A minimal SynapseHub stand-in for the delivery-feedback renderers."""

    def __init__(
        self,
        *,
        capabilities: dict[str, str] | None = None,
        journal: Any = None,
        evicted: Any = None,
    ) -> None:
        self._caps = capabilities or {}
        self.journal = journal
        self.sent: list[dict[str, Any]] = []
        self.pending_receipts = _FakePending(evicted)

    def wake_capability_of(self, name: str) -> str:
        return self._caps.get(name, WAKE_UNKNOWN)

    def _system(self, text: str, **fields: Any) -> dict[str, Any]:
        return {"text": text, **fields}

    async def _send_json(self, websocket: Any, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def _as_hub(hub: _FakeHub) -> SynapseHub:
    """Present the structural fake as a concrete hub without a type: ignore."""
    return cast("SynapseHub", hub)


def _decision(
    *,
    matched: tuple[str, ...] = (),
    live: tuple[str, ...] = (),
    stale: tuple[str, ...] = (),
    reason: str = "",
) -> DeliveryLiveness:
    return DeliveryLiveness(
        matched_recipients=matched,
        live_recipients=live,
        stale_recipients=stale,
        reason=reason,
    )


def _capture_journal(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace the journal record functions with recorders and return the log."""
    log: dict[str, list[Any]] = {"immediate": [], "requested": [], "expired": []}

    def _immediate(journal: Any, payload: Any) -> None:
        log["immediate"].append(payload)

    def _requested(journal: Any, payload: Any) -> None:
        log["requested"].append(payload)

    def _expired(journal: Any, payload: Any) -> None:
        log["expired"].append(payload)

    monkeypatch.setattr(df, "record_delivery_receipt_immediate", _immediate)
    monkeypatch.setattr(df, "record_delivery_receipt_requested", _requested)
    monkeypatch.setattr(df, "record_delivery_receipt_expired", _expired)
    return log


class TestPureHelpers:
    """Cover the synchronous rendering helpers."""

    def test_wake_capability_prefers_the_direct_match(self) -> None:
        hub = _FakeHub(capabilities={"bob": WAKE_DIRECT, "bob-rx": WAKE_PASSIVE})
        assert df._recipient_wake_capability(_as_hub(hub), "bob") == WAKE_DIRECT

    def test_wake_capability_falls_back_to_the_rx_sidecar(self) -> None:
        hub = _FakeHub(capabilities={"bob-rx": WAKE_PASSIVE})
        assert df._recipient_wake_capability(_as_hub(hub), "bob") == WAKE_PASSIVE

    def test_render_recipient_without_capability_is_bare(self) -> None:
        assert df._render_recipient_with_capability("bob", WAKE_UNKNOWN) == "bob"

    def test_render_recipient_with_capability_is_labelled(self) -> None:
        assert df._render_recipient_with_capability("bob", WAKE_DIRECT) == "bob (direct agent)"

    def test_failure_payload_names_stale_sockets_for_no_live_recipient(self) -> None:
        decision = _decision(stale=("eve", "mal"), reason=NO_LIVE_RECIPIENT)
        line = df._failure_payload("TEAM", decision)
        assert "no live recipient matched TEAM" in line
        assert "stale sockets: eve, mal" in line

    def test_failure_payload_for_no_online_recipient(self) -> None:
        decision = _decision(reason="no_online_recipient")
        assert df._failure_payload("TEAM", decision) == (
            "delivery failed: no online recipient matched TEAM"
        )


class TestWarnStaleRecipients:
    """Cover the private liveness warning."""

    async def test_no_stale_or_passive_sends_nothing(self) -> None:
        hub = _FakeHub(capabilities={"alice": WAKE_DIRECT})
        decision = _decision(matched=("alice",), live=("alice",))
        await df.warn_stale_recipients(
            _as_hub(hub), object(), sender="S", target="T", msg_id=1, decision=decision
        )
        assert hub.sent == []

    async def test_stale_recipient_warning_is_sent_and_dead_lettered(self) -> None:
        hub = _FakeHub()
        decision = _decision(matched=("bob",), stale=("bob",), reason=NO_LIVE_RECIPIENT)
        await df.warn_stale_recipients(
            _as_hub(hub), object(), sender="S", target="T", msg_id=1, decision=decision
        )
        text = hub.sent[0]["text"]
        assert "bob present but not proven live" in text
        assert "dead-lettered" in text
        assert hub.sent[0]["dead_lettered"] is True

    async def test_passive_recipient_warning_without_dead_letter(self) -> None:
        hub = _FakeHub(capabilities={"carol": WAKE_PASSIVE})
        decision = _decision(matched=("carol",), live=("carol",))
        await df.warn_stale_recipients(
            _as_hub(hub), object(), sender="S", target="T", msg_id=1, decision=decision
        )
        text = hub.sent[0]["text"]
        assert "carol reached only a passive receiver" in text
        assert "dead-lettered" not in text


class TestSendDeliveryReceipt:
    """Cover the authoritative delivery receipt."""

    async def test_delivered_receipt_journals_when_seq_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture_journal(monkeypatch)
        hub = _FakeHub(capabilities={"dave": WAKE_DIRECT}, journal=object())
        decision = _decision(matched=("dave",), live=("dave",))
        await df.send_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            decision=decision,
            message_seq=5,
        )
        assert hub.sent[0]["text"] == "delivered to dave (direct agent)"
        assert hub.sent[0]["delivered"] is True
        assert len(log["immediate"]) == 1

    async def test_failed_receipt_without_journal_does_not_record(self) -> None:
        hub = _FakeHub(journal=None)
        decision = _decision(matched=("eve",), stale=("eve",), reason=NO_LIVE_RECIPIENT)
        await df.send_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            decision=decision,
            message_seq=5,
            dead_lettered=True,
        )
        assert "no live recipient matched T" in hub.sent[0]["text"]
        assert hub.sent[0]["dead_lettered"] is True

    async def test_journal_present_but_seq_none_does_not_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture_journal(monkeypatch)
        hub = _FakeHub(journal=object())
        decision = _decision(matched=("eve",), stale=("eve",), reason=NO_LIVE_RECIPIENT)
        await df.send_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            decision=decision,
            message_seq=None,
        )
        assert log["immediate"] == []
        assert hub.sent[0]["delivered"] is False


class TestSendAndTrackDeliveryReceipt:
    """Cover the audit, send, and deferred-replay retention path."""

    async def test_full_path_records_requested_and_evicted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture_journal(monkeypatch)
        hub = _FakeHub(
            journal=object(), evicted=(9, ReceiptEntry(sender="old", target="T", message_id=2))
        )
        decision = _decision(matched=("eve",), stale=("eve",), reason=NO_LIVE_RECIPIENT)
        await df.send_and_track_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            message_seq=5,
            decision=decision,
            directed=True,
        )
        assert len(log["requested"]) == 1
        assert hub.pending_receipts.calls == [(5, "S", "T", 1)]
        assert len(log["expired"]) == 1

    async def test_no_eviction_skips_expired_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _capture_journal(monkeypatch)
        hub = _FakeHub(journal=object(), evicted=None)
        decision = _decision(matched=("eve",), stale=("eve",), reason=NO_LIVE_RECIPIENT)
        await df.send_and_track_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            message_seq=5,
            decision=decision,
            directed=True,
        )
        assert hub.pending_receipts.calls == [(5, "S", "T", 1)]
        assert log["expired"] == []

    async def test_delivered_returns_before_remember(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _capture_journal(monkeypatch)
        hub = _FakeHub(capabilities={"dave": WAKE_DIRECT}, journal=object())
        decision = _decision(matched=("dave",), live=("dave",))
        await df.send_and_track_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            message_seq=5,
            decision=decision,
            directed=True,
        )
        assert hub.pending_receipts.calls == []

    async def test_seq_none_skips_requested_and_remember(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture_journal(monkeypatch)
        hub = _FakeHub(journal=object())
        decision = _decision(matched=("eve",), stale=("eve",), reason=NO_LIVE_RECIPIENT)
        await df.send_and_track_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            message_seq=None,
            decision=decision,
            directed=True,
        )
        assert log["requested"] == []
        assert hub.pending_receipts.calls == []

    async def test_no_journal_reaches_remember_without_records(self) -> None:
        hub = _FakeHub(
            journal=None, evicted=(9, ReceiptEntry(sender="old", target="T", message_id=2))
        )
        decision = _decision(matched=("eve",), stale=("eve",), reason=NO_LIVE_RECIPIENT)
        await df.send_and_track_delivery_receipt(
            _as_hub(hub),
            object(),
            sender="S",
            target="T",
            msg_id=1,
            message_seq=5,
            decision=decision,
            directed=True,
        )
        # journal is None: requested + expired records are skipped, but remember still runs.
        assert hub.pending_receipts.calls == [(5, "S", "T", 1)]
