# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable-ingress byte charging and expired-window usage
"""Charge non-JSON frames by their text size and forget a principal whose window passed."""

from __future__ import annotations

from synapse_channel.core.durable_ingress import DurableIngressQuota, chat_frame_bytes


def test_a_frame_that_is_not_json_is_charged_by_its_text_size() -> None:
    """A direct caller's non-JSON value still costs bytes instead of escaping the budget."""
    frame = {"type": "chat", "payload": "hi", "attachment": {b"raw", b"bytes"}}
    charged = chat_frame_bytes(frame)
    assert charged == len(str(dict(frame)).encode("utf-8"))
    assert charged > len('{"type":"chat","payload":"hi"}')


def test_usage_after_the_window_passes_is_zero_and_drops_the_principal() -> None:
    """Once every admission has aged out, usage is (0, 0) and the bucket is released."""
    quota = DurableIngressQuota(max_events=5, max_bytes=1000, window_seconds=10.0)
    assert quota.allow("principal-a", nbytes=40, now=100.0) == ""
    assert quota.allow("principal-a", nbytes=60, now=101.0) == ""
    assert quota.usage("principal-a", now=105.0) == (2, 100)
    assert quota.usage("principal-a", now=200.0) == (0, 0)
    # The idle bucket itself is released, not kept as an empty entry.
    assert "principal-a" not in quota._buckets
    assert quota.usage("never-seen", now=200.0) == (0, 0)
    # A fresh admission after the release starts a new window from zero.
    assert quota.allow("principal-a", nbytes=10, now=201.0) == ""
    assert quota.usage("principal-a", now=201.0) == (1, 10)
