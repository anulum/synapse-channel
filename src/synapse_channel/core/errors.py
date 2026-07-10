# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the package error taxonomy base
"""Base of the package's typed error taxonomy.

Every domain exception in the package derives from :class:`SynapseError` and
carries a stable machine-readable :attr:`~SynapseError.code`, so a boundary
layer (the CLI, the A2A bridge, the MCP server, an embedding application) can
classify a failure without matching on message text. The taxonomy changes
nothing about *what* is raised where: each domain class keeps its historical
built-in base (``ValueError``, ``RuntimeError``, ``PermissionError``) through
multiple inheritance, so every pre-existing ``except`` clause keeps catching
exactly what it caught before.

Stability contract
------------------
A ``code`` is wire-adjacent API: once released it never changes meaning and is
never reused for a different failure class. Renaming a class does not rename
its code. The registry test (``tests/test_core_errors.py``) freezes the full
class-to-code map the same way the wire-surface freeze pins message fields.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

__all__ = ["SynapseError", "error_code"]

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


class SynapseError(Exception):
    """Common base of every domain exception the package raises.

    Attributes
    ----------
    code : str
        Stable, machine-readable failure class in snake_case. Every subclass
        must declare its own ``code`` explicitly (inheriting the parent's is
        refused at class-definition time), and the value is frozen by the
        registry test once released.

    Raises
    ------
    TypeError
        At subclass *definition* time, when the subclass does not declare its
        own ``code`` or declares one that is not snake_case. A taxonomy hole
        must fail the import, not surface later as an unclassifiable error.
    """

    code: ClassVar[str] = "synapse"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a subclass without its own well-formed ``code``."""
        super().__init_subclass__(**kwargs)
        declared = cls.__dict__.get("code")
        if not isinstance(declared, str):
            raise TypeError(
                f"{cls.__qualname__} must declare its own class-level 'code' "
                "(inheriting the parent's code would make two failure classes "
                "indistinguishable to a boundary layer)"
            )
        if not _CODE_PATTERN.match(declared):
            raise TypeError(
                f"{cls.__qualname__}.code {declared!r} is not snake_case "
                "(expected lowercase words joined by single underscores)"
            )


def error_code(exc: BaseException) -> str:
    """Return the taxonomy code of ``exc``, or ``""`` for a foreign exception.

    Parameters
    ----------
    exc : BaseException
        Any exception instance.

    Returns
    -------
    str
        ``exc.code`` when ``exc`` is a :class:`SynapseError`; the empty string
        otherwise, so callers can branch on truthiness without an
        ``isinstance`` check of their own.
    """
    if isinstance(exc, SynapseError):
        return exc.code
    return ""
