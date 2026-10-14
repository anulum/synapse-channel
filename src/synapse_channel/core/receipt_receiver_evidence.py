# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — receiver declarations captured at actual transport writes
"""Carry concrete writer evidence without changing the successful-name contract."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from synapse_channel.core.agent_liveness import waiter_sidecar_names
from synapse_channel.core.wake_capability import WAKE_PASSIVE, WAKE_UNKNOWN

TRANSPORT_RESULTS = "transport_results"
"""Provenance of capabilities captured for actual attempted socket writes."""


class TransportWrites(frozenset[str]):
    """Successful names plus immutable declarations of attempted named writers.

    A failed write has ``unknown`` capability evidence: it cannot certify that
    a passive receiver received nothing. Only successful writes retain their
    captured declaration. Equality and iteration retain frozenset semantics.
    """

    _capabilities: Mapping[str, str]

    def __new__(cls, names: Iterable[str], capabilities: Mapping[str, str]) -> TransportWrites:
        """Allocate the immutable successful-name set with its original semantics."""
        del capabilities
        return super().__new__(cls, names)

    def __init__(self, names: Iterable[str], capabilities: Mapping[str, str]) -> None:
        """Keep a private immutable copy of concrete writer evidence."""
        del names
        self._capabilities = MappingProxyType(dict(capabilities))

    @property
    def wake_capabilities(self) -> Mapping[str, str]:
        """Return declarations of actual writers, with failed writes unknown."""
        return self._capabilities


def logical_capabilities(recipients: Iterable[str], writers: Mapping[str, str]) -> dict[str, str]:
    """Project concrete writers conservatively onto their logical recipients.

    A completed passive write settles the observation. Otherwise any unknown
    writer prevents refusal; only wholly known non-passive writes allow fallback.
    """
    projected: dict[str, str] = {}
    for recipient in recipients:
        names = (recipient, *waiter_sidecar_names(recipient))
        kinds = [writers[name] for name in names if name in writers]
        if WAKE_PASSIVE in kinds:
            capability = WAKE_PASSIVE
        elif not kinds or WAKE_UNKNOWN in kinds:
            capability = WAKE_UNKNOWN
        else:
            capability = kinds[0]
        projected[recipient] = capability
    return projected
