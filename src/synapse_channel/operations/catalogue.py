# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the operations known for the clients of a host, and which may run
"""Hold the operations of the vendor clients on a host and say which may run.

A catalogue is a set of :class:`~synapse_channel.operations.spec.OperationSpec`
rows, one per host, vendor, client version, operating system and operation.
It answers three questions:

* what is known (:meth:`Catalogue.rows`, :meth:`Catalogue.find`);
* may this operation run for this exact client (:meth:`Catalogue.admit`);
* what would a client version that was never seen look like
  (:meth:`Catalogue.candidates`).

**Being listed grants nothing.** Admission here says only that the operation
is known well enough to be tried; whether the caller may ask for it is decided
elsewhere, against the permission the row names.

A client version the catalogue does not know admits nothing. Its operations
can be shown as candidates, copied from the newest known version and marked
``unverified``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from synapse_channel.core.errors import SynapseError
from synapse_channel.operations.spec import OperationSpec, OperationSpecError

SCHEMA = "operation-catalogue-v1"
"""Schema name of a catalogue as plain data."""


class OperationRefusedError(SynapseError, LookupError):
    """An operation may not run for the client asked."""

    code = "operation_refused"


class Catalogue:
    """The operations known for the clients of one or more hosts."""

    def __init__(self, rows: Iterable[OperationSpec] = ()) -> None:
        self._rows: dict[tuple[str, str, str, str, str], OperationSpec] = {}
        for row in rows:
            if row.key in self._rows:
                raise OperationSpecError(f"operation listed twice: {' '.join(row.key)}")
            self._rows[row.key] = row

    def rows(
        self, *, host: str | None = None, vendor: str | None = None
    ) -> tuple[OperationSpec, ...]:
        """Return the rows of a host and a vendor, or of all, in a fixed order."""
        return tuple(
            row
            for _, row in sorted(self._rows.items())
            if host in (None, row.host) and vendor in (None, row.vendor)
        )

    def find(
        self, operation: str, *, host: str, vendor: str, client_version: str, os: str
    ) -> OperationSpec | None:
        """Return the row of exactly this operation and client, or None."""
        return self._rows.get((host, vendor, client_version, os, operation))

    def admit(
        self, operation: str, *, host: str, vendor: str, client_version: str, os: str
    ) -> OperationSpec:
        """Return the row when the operation is known well enough to run.

        Raises
        ------
        OperationRefusedError
            The operation is not listed for exactly this client and version,
            or its qualification is ``unverified`` or ``help_text``.
        """
        row = self.find(operation, host=host, vendor=vendor, client_version=client_version, os=os)
        if row is None:
            raise OperationRefusedError(
                f"{operation} is not listed for {vendor} {client_version} on {os} at {host}"
            )
        if not row.runnable:
            raise OperationRefusedError(f"{operation} is {row.qualification}: listed, and not run")
        return row

    def candidates(
        self, *, host: str, vendor: str, os: str, client_version: str
    ) -> tuple[OperationSpec, ...]:
        """Return what an unseen client version would offer, all ``unverified``.

        The rows are copied from the newest known version of that client on
        that host, by the order of the version text. They are not added to the
        catalogue and admit nothing. A version that is already known gives no
        candidates.
        """
        known = [row for row in self.rows(host=host, vendor=vendor) if row.os == os]
        versions = sorted({row.client_version for row in known}, key=_version_order)
        if not versions or client_version in versions:
            return ()
        return tuple(
            replace(row, client_version=client_version, qualification="unverified", evidence="")
            for row in known
            if row.client_version == versions[-1]
        )

    def to_json(self) -> dict[str, object]:
        """Return the catalogue as plain data."""
        return {"schema": SCHEMA, "operations": [row.to_json() for row in self.rows()]}

    @classmethod
    def from_json(cls, data: object) -> Catalogue:
        """Return the catalogue held in plain data, or refuse it.

        Raises
        ------
        OperationSpecError
            The data is not a catalogue of this schema, a row is not a
            description, or a row is listed twice.
        """
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise OperationSpecError("not an operation catalogue of this schema")
        rows = data.get("operations")
        if not isinstance(rows, list):
            raise OperationSpecError("an operation catalogue lists its operations")
        return cls(OperationSpec.from_json(row) for row in rows)


def _version_order(version: str) -> tuple[tuple[int, int | str], ...]:
    """Order version texts by their numeric parts where they have them."""
    parts: list[tuple[int, int | str]] = []
    for part in version.replace("-", ".").replace("+", ".").split("."):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)
