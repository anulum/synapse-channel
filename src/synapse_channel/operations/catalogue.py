# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the operations known for the clients of a host, and which may run
"""Hold the operations of the vendor clients on a host and say which may run.

A catalogue is a set of :class:`~synapse_channel.operations.row.OperationRow`
rows, one per host, vendor, answering component, component version, operating
system, operation, transport and variant. It answers three questions:

* what is known (:meth:`Catalogue.rows`, :meth:`Catalogue.find`);
* may this operation run for exactly this component (:meth:`Catalogue.admit`);
* what would a component version that was never seen look like
  (:meth:`Catalogue.candidates`).

**Being listed grants nothing.** Admission here says only that the operation
is known well enough to be tried; whether the caller may ask for it is decided
elsewhere, against every permission the row names.

A catalogue is made on the host where the components run. A row qualified on
one host, for one version, says nothing about another host or version: a
version the catalogue does not know admits nothing. Its operations can be
shown as candidates, copied from the newest known version and marked
``unverified``.

Files of the earlier schema, ``operation-catalogue-v1``, are still read, as a
:class:`LegacyInventory`: its rows can be listed, and none of them is ever
admitted, whatever qualification it states, because a v1 row does not say how
the operation is invoked.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from synapse_channel.core.errors import SynapseError
from synapse_channel.operations.row import NEVER_ADMITTED, OperationRow
from synapse_channel.operations.spec import OperationSpec, OperationSpecError
from synapse_channel.operations.vocabulary import Vocabulary, packaged

SCHEMA = "operation-catalogue-v2"
"""Schema name of a catalogue as plain data."""

LEGACY_SCHEMA = "operation-catalogue-v1"
"""Schema name of the earlier form, read as an inventory that admits nothing."""


class OperationRefusedError(SynapseError, LookupError):
    """An operation may not run for the component asked."""

    code = "operation_refused"


class Catalogue:
    """The operations known for the components of one or more hosts.

    Parameters
    ----------
    rows : iterable of OperationRow
        The rows. A row whose name holds a word that is not registered, and a
        second row with the same identity, are refused.
    vocabulary : Vocabulary or None
        The registered words; the vocabulary shipped with the package by default.
    """

    def __init__(
        self, rows: Iterable[OperationRow] = (), *, vocabulary: Vocabulary | None = None
    ) -> None:
        self.vocabulary = packaged() if vocabulary is None else vocabulary
        self._rows: dict[tuple[str, ...], OperationRow] = {}
        for row in rows:
            self.vocabulary.parts(row.operation)
            if row.key in self._rows:
                raise OperationSpecError(f"operation listed twice: {' '.join(row.key)}")
            self._rows[row.key] = row

    def rows(
        self,
        *,
        host: str | None = None,
        vendor: str | None = None,
        transport: str | None = None,
    ) -> tuple[OperationRow, ...]:
        """Return the rows of a host, a vendor and a transport, or of all, in a fixed order."""
        return tuple(
            row
            for _, row in sorted(self._rows.items())
            if host in (None, row.host)
            and vendor in (None, row.vendor)
            and transport in (None, row.transport)
        )

    def find(
        self,
        operation: str,
        *,
        host: str,
        vendor: str,
        component: str,
        component_version: str,
        os: str,
        transport: str,
        variant: str = "default",
    ) -> OperationRow | None:
        """Return the row of exactly this operation, component and transport, or None."""
        key = (host, vendor, component, component_version, os, operation, transport, variant)
        return self._rows.get(key)

    def admit(
        self,
        operation: str,
        *,
        host: str,
        vendor: str,
        component: str,
        component_version: str,
        os: str,
        transport: str,
        fingerprint: str,
        variant: str = "default",
    ) -> OperationRow:
        """Return the row when the operation is known well enough to run.

        Parameters
        ----------
        fingerprint : str
            The fingerprint of the answering executable as found on the host
            now. It must be the one the row was qualified with.

        Raises
        ------
        OperationRefusedError
            The operation is not listed for exactly this component, version
            and transport; it is not qualified by a run; its binding or the
            executable changed since it was qualified; it has an effect that
            is never admitted; or its output can hold credentials and no
            host-side projection is named.
        """
        row = self.find(
            operation,
            host=host,
            vendor=vendor,
            component=component,
            component_version=component_version,
            os=os,
            transport=transport,
            variant=variant,
        )
        if row is None:
            raise OperationRefusedError(
                f"{operation} is not listed for {vendor} {component} {component_version} "
                f"on {os} at {host} over {transport}"
            )
        reason = admission_refusal(row, fingerprint)
        if reason:
            raise OperationRefusedError(f"{operation} {reason}")
        return row

    def candidates(
        self, *, host: str, vendor: str, component: str, os: str, component_version: str
    ) -> tuple[OperationRow, ...]:
        """Return what an unseen component version would offer, all ``unverified``.

        The rows are copied from the newest known version of that component on
        that host, by the order of the version text, without evidence,
        fingerprint or qualified binding. They are not added to the catalogue
        and admit nothing. A version that is already known gives no candidates.
        """
        known = [
            row
            for row in self.rows(host=host, vendor=vendor)
            if row.os == os and row.component == component
        ]
        versions = sorted({row.component_version for row in known}, key=_version_order)
        if not versions or component_version in versions:
            return ()
        return tuple(
            replace(
                row,
                component_version=component_version,
                version_printed="",
                qualification="unverified",
                evidence="",
                fingerprint="",
                qualified_binding="",
            )
            for row in known
            if row.component_version == versions[-1]
        )

    def to_json(self) -> dict[str, object]:
        """Return the catalogue as plain data."""
        return {
            "schema": SCHEMA,
            "vocabulary_revision": self.vocabulary.revision,
            "operations": [row.to_json() for row in self.rows()],
        }

    @classmethod
    def from_json(cls, data: object, *, vocabulary: Vocabulary | None = None) -> Catalogue:
        """Return the catalogue held in plain data, or refuse it.

        Raises
        ------
        OperationSpecError
            The data is not a catalogue of this schema, it was made with a
            newer vocabulary than this reader has, a row is not a description,
            holds an unregistered word, or is listed twice.
        """
        words = packaged() if vocabulary is None else vocabulary
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise OperationSpecError("not an operation catalogue of this schema")
        revision = data.get("vocabulary_revision")
        if type(revision) is not int or not 1 <= revision <= words.revision:
            raise OperationSpecError("the catalogue was not made with a vocabulary this reader has")
        rows = data.get("operations")
        if not isinstance(rows, list):
            raise OperationSpecError("an operation catalogue lists its operations")
        return cls((OperationRow.from_json(row) for row in rows), vocabulary=words)


class LegacyInventory:
    """The rows of a file of the earlier schema: listed, and never admitted."""

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

    def to_json(self) -> dict[str, object]:
        """Return the inventory as plain data of the earlier schema."""
        return {"schema": LEGACY_SCHEMA, "operations": [row.to_json() for row in self.rows()]}

    @classmethod
    def from_json(cls, data: object) -> LegacyInventory:
        """Return the inventory held in plain data, or refuse it.

        Raises
        ------
        OperationSpecError
            The data is not a catalogue of the earlier schema, a row is not a
            description of that schema, or a row is listed twice. A field the
            earlier schema does not have is refused, not dropped.
        """
        if not isinstance(data, dict) or data.get("schema") != LEGACY_SCHEMA:
            raise OperationSpecError("not an operation catalogue of the earlier schema")
        rows = data.get("operations")
        if not isinstance(rows, list):
            raise OperationSpecError("an operation catalogue lists its operations")
        return cls(OperationSpec.from_json(row) for row in rows)


def read_catalogue(data: object) -> Catalogue | LegacyInventory:
    """Return plain data as a catalogue, or as a legacy inventory, by its schema.

    Raises
    ------
    OperationSpecError
        The data is of neither schema, or is refused by its reader.
    """
    schema = data.get("schema") if isinstance(data, dict) else None
    if schema == SCHEMA:
        return Catalogue.from_json(data)
    if schema == LEGACY_SCHEMA:
        return LegacyInventory.from_json(data)
    raise OperationSpecError("not an operation catalogue of a known schema")


def admission_refusal(row: OperationRow, fingerprint: str) -> str:
    """Return the row's admission refusal, or an empty text when its checks pass.

    Parameters
    ----------
    row
        The operation's declared binding, effects and qualification.
    fingerprint
        Fingerprint of the executable to check. Read-only inventory displays
        use the row's declared fingerprint; execution must supply the one
        found on the host now.

    Returns
    -------
    str
        Refusal reason, or an empty text. Passing these data checks grants no
        caller permission or consent and runs no operation.
    """
    denied = sorted(NEVER_ADMITTED.intersection(row.effects))
    if denied:
        return f"has the effect {denied[0]}: listed, and never admitted"
    if row.stale:
        return "was qualified for another binding than it has now"
    if not row.runnable:
        return f"is {row.qualification}: listed, and not run"
    if fingerprint != row.fingerprint:
        return "was qualified for another executable than the one found"
    if "credential_bearing" in row.disclosures and not row.output_projection:
        return "can print credentials and names no projection that removes them"
    return ""


def _version_order(version: str) -> tuple[tuple[int, int | str], ...]:
    """Order version texts by their numeric parts where they have them."""
    parts: list[tuple[int, int | str]] = []
    for part in version.replace("-", ".").replace("+", ".").split("."):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)
