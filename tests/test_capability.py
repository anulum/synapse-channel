# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for capability cards and the hub manifest registry

from __future__ import annotations

from synapse_channel.capability import CapabilityCard, CapabilityRegistry


def test_card_as_dict_exposes_all_fields() -> None:
    card = CapabilityCard(
        agent="FAST",
        description="quick worker",
        skills=("ollama",),
        task_classes=("chat",),
        model="gemma3:4b",
        meta={"vram": "8G"},
        advertised_at=5.0,
    )
    assert card.as_dict() == {
        "agent": "FAST",
        "description": "quick worker",
        "skills": ["ollama"],
        "task_classes": ["chat"],
        "model": "gemma3:4b",
        "meta": {"vram": "8G"},
        "advertised_at": 5.0,
    }


def test_advertise_cleans_tags_and_strips_text() -> None:
    registry = CapabilityRegistry()
    card = registry.advertise(
        "FAST",
        description="  quick  ",
        skills=[" ollama ", "ollama", "", "rule"],
        task_classes=[" chat ", "chat"],
        model="  m  ",
        now=1.0,
    )
    assert card.description == "quick"
    assert card.skills == ("ollama", "rule")  # stripped, de-duplicated, blanks dropped
    assert card.task_classes == ("chat",)
    assert card.model == "m"
    assert registry.get("FAST") is card


def test_advertise_defaults_meta_to_empty() -> None:
    registry = CapabilityRegistry()
    card = registry.advertise("A", now=1.0)
    assert card.meta == {}


def test_re_advertise_refreshes_card() -> None:
    registry = CapabilityRegistry()
    registry.advertise("A", description="old", now=1.0)
    registry.advertise("A", description="new", now=2.0)
    assert registry.get("A").description == "new"  # type: ignore[union-attr]
    assert registry.get("A").advertised_at == 2.0  # type: ignore[union-attr]


def test_get_and_forget() -> None:
    registry = CapabilityRegistry()
    registry.advertise("A", now=1.0)
    assert registry.get("A") is not None
    registry.forget("A")
    assert registry.get("A") is None
    registry.forget("A")  # forgetting an unknown agent is a no-op


def test_expire_drops_stale_cards() -> None:
    registry = CapabilityRegistry(ttl_seconds=100.0)
    registry.advertise("STALE", now=1.0)
    registry.advertise("FRESH", now=50.0)
    registry.expire(now=120.0)  # STALE is 119s old (> 100), FRESH is 70s old
    assert registry.get("STALE") is None
    assert registry.get("FRESH") is not None


def test_manifest_is_sorted_and_expires() -> None:
    registry = CapabilityRegistry(ttl_seconds=100.0)
    registry.advertise("B", now=10.0)
    registry.advertise("A", now=10.0)
    registry.advertise("OLD", now=1.0)
    # At now=105 OLD is 104s old (> 100, expired) while A and B are 95s old (fresh).
    manifest = registry.manifest(now=105.0)
    assert [card["agent"] for card in manifest] == ["A", "B"]


def test_for_task_class_lists_matching_agents_sorted() -> None:
    registry = CapabilityRegistry()
    registry.advertise("FAST", task_classes=["chat", "rule"], now=1.0)
    registry.advertise("REASON", task_classes=["chat", "reason"], now=1.0)
    registry.advertise("CRUNCH", task_classes=["heavy"], now=1.0)
    assert registry.for_task_class("chat", now=2.0) == ["FAST", "REASON"]
    assert registry.for_task_class("heavy", now=2.0) == ["CRUNCH"]
    assert registry.for_task_class("absent", now=2.0) == []
