# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-qualified seat addresses for cross-hub message delivery
"""Hub-qualified addresses: ``PROJECT/seat@HUB_ID`` names a seat on a peer hub.

A local seat name never contains ``@``; the hub reserves every such name (see
:meth:`~synapse_channel.core.hub_clients.HubClientRegistry.is_reserved_sender`). That
reservation is what makes the two forms below unambiguous:

* a **target** ``PROJECT/seat@HUB_ID`` asks the sending hub to forward the message to the
  configured message peer ``HUB_ID``, which delivers it to its local ``PROJECT/seat``;
* a **forwarded sender** ``PROJECT/seat@ORIGIN_HUB`` is how the receiving hub presents a
  message that arrived from an authenticated peer. ``ORIGIN_HUB`` is taken from the peer's
  authenticated identity, never from the frame, so a peer cannot present a sender as local
  or as coming from a third hub.

The address is split on the **last** ``@``; the hub id must be a bounded token of letters,
digits, ``.``, ``_`` and ``-``. Anything else is not a hub-qualified address.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HUB_ADDRESS_SEPARATOR = "@"
"""Separator between a seat name and the hub that hosts it."""

MAX_HUB_ID_LENGTH = 64
"""Longest accepted hub id in a hub-qualified address."""

MAX_SEAT_LENGTH = 256
"""Longest accepted seat part in a hub-qualified address."""

_HUB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass(frozen=True, slots=True)
class HubQualifiedAddress:
    """A seat name bound to the hub that hosts it.

    Attributes
    ----------
    seat : str
        The seat's local name on its hub, for example ``SYNAPSE-CHANNEL/agent-a7c2``.
    hub_id : str
        The stable id of the hub that hosts the seat.
    """

    seat: str
    hub_id: str

    def __str__(self) -> str:
        """Render the address in its ``seat@hub_id`` wire form."""
        return f"{self.seat}{HUB_ADDRESS_SEPARATOR}{self.hub_id}"


def is_valid_hub_id(value: str) -> bool:
    """Return whether ``value`` is an acceptable hub id for a hub-qualified address.

    Parameters
    ----------
    value : str
        Candidate hub id.

    Returns
    -------
    bool
        ``True`` for 1–64 characters of letters, digits, ``.``, ``_`` or ``-`` that start
        with a letter or digit.
    """
    return _HUB_ID.fullmatch(value) is not None


def parse_hub_qualified(value: str) -> HubQualifiedAddress | None:
    """Split ``seat@hub_id`` into its parts, or return ``None`` for any other value.

    Parameters
    ----------
    value : str
        A target or sender name as it appears on the wire.

    Returns
    -------
    HubQualifiedAddress or None
        The parsed address when ``value`` has a non-empty seat part without control
        characters and a valid hub id after its last ``@``; otherwise ``None``.
    """
    seat, separator, hub_id = value.rpartition(HUB_ADDRESS_SEPARATOR)
    if not separator or not seat or not is_valid_hub_id(hub_id):
        return None
    if HUB_ADDRESS_SEPARATOR in seat or len(seat) > MAX_SEAT_LENGTH:
        return None
    if seat != seat.strip() or any(ord(char) < 0x20 or ord(char) == 0x7F for char in seat):
        return None
    return HubQualifiedAddress(seat=seat, hub_id=hub_id)


def federated_sender(sender: str, origin_hub: str) -> str:
    """Return how a receiving hub names a sender that arrived from ``origin_hub``.

    Parameters
    ----------
    sender : str
        The sender's seat name as authenticated on its own hub.
    origin_hub : str
        The authenticated id of the peer hub that forwarded the message.

    Returns
    -------
    str
        ``sender@origin_hub``.

    Raises
    ------
    ValueError
        If ``origin_hub`` is not a valid hub id, or ``sender`` is empty or already
        hub-qualified.
    """
    if not is_valid_hub_id(origin_hub):
        raise ValueError(f"invalid origin hub id {origin_hub!r}")
    if not sender or HUB_ADDRESS_SEPARATOR in sender:
        raise ValueError(f"invalid forwarded sender {sender!r}")
    return f"{sender}{HUB_ADDRESS_SEPARATOR}{origin_hub}"


def is_hub_qualified_name(name: str) -> bool:
    """Return whether ``name`` uses the reserved hub-qualified ``@`` form.

    Parameters
    ----------
    name : str
        A seat name presented by a connecting client.

    Returns
    -------
    bool
        ``True`` whenever the name contains ``@``. Local seats may not use such names, so a
        client can never impersonate a forwarded sender or address itself as a remote seat.
    """
    return HUB_ADDRESS_SEPARATOR in name


_AUDIENCE_MARKERS = frozenset(",*?[")
"""Characters that turn a chat target into a list or a glob audience."""


def is_single_seat(seat: str) -> bool:
    """Return whether ``seat`` names exactly one recipient seat or role.

    Cross-hub traffic is authorised per target namespace, so a forwarded target must not
    expand: a comma list could reach a namespace other than the one authorised, and a glob
    or ``all`` would reach an audience. Both the origin and the receiving hub apply this
    rule, so a modified peer cannot widen a forward the origin would have refused.

    Parameters
    ----------
    seat : str
        The seat part of a target, without the ``@HUB_ID`` suffix.

    Returns
    -------
    bool
        ``False`` for an empty or padded value, ``all``, or any value containing ``,``,
        ``*``, ``?`` or ``[``; ``True`` otherwise.
    """
    return (
        bool(seat)
        and seat == seat.strip()
        and seat != "all"
        and not any(marker in seat for marker in _AUDIENCE_MARKERS)
    )
