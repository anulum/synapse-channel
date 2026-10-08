# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — typed legacy names for record-owned hub settings
"""Project existing writable hub names onto immutable family records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar, overload

from synapse_channel.core.hub_config import HubConfig

Value = TypeVar("Value")


class HubConfigOwner:
    """Declare the canonical record owned by a configured hub instance."""

    configuration: HubConfig


class ConfigAttribute(Generic[Value]):
    """Keep a legacy attribute writable without storing a second setting.

    Both operations are statically typed callbacks naming exact record fields.
    There is no dynamic attribute lookup, name coercion or untyped escape.
    A write replaces the immutable family/root records of this owner only.
    """

    def __init__(
        self,
        read: Callable[[HubConfig], Value],
        write: Callable[[HubConfig, Value], HubConfig],
    ) -> None:
        """Bind typed field access and immutable record replacement callbacks."""
        self._read = read
        self._write = write

    @overload
    def __get__(
        self, instance: None, owner: type[HubConfigOwner] | None = None
    ) -> ConfigAttribute[Value]: ...

    @overload
    def __get__(
        self, instance: HubConfigOwner, owner: type[HubConfigOwner] | None = None
    ) -> Value: ...

    def __get__(
        self, instance: HubConfigOwner | None, owner: type[HubConfigOwner] | None = None
    ) -> Value | ConfigAttribute[Value]:
        """Read the actual family field, or expose the class-level descriptor."""
        if instance is None:
            return self
        return self._read(instance.configuration)

    def __set__(self, instance: HubConfigOwner, value: Value) -> None:
        """Replace this owner's exact field without changing other hub instances."""
        instance.configuration = self._write(instance.configuration, value)
