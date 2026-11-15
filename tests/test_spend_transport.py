# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — an unimplemented spend socket transmits nothing

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from synapse_channel.core.spend_transport import SpendSocket


async def test_an_unimplemented_spend_socket_does_not_send() -> None:
    """The protocol stubs are inert. A real socket supplies the bodies."""

    socket = cast(SpendSocket, object())
    send = cast(Callable[..., Awaitable[object]], SpendSocket.send)
    recv = cast(Callable[..., Awaitable[object]], SpendSocket.recv)
    assert await send(socket, "frame") is None
    assert await recv(socket) is None
