# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the pool owner's durable, serialized spend ledger (F02)
"""Decide shared-pool reservations in one owner-only, serialized SQLite ledger.

The pool owner's hub keeps this ledger apart from its replicated event journal, so
no balance ever reaches a peer's mirror. Every decision runs in one ``BEGIN
IMMEDIATE`` transaction: it replays the pool's events, checks the invariant of
:mod:`synapse_channel.core.spend_pool`, appends the outcome and stores the response
under the request's idempotency scope before committing. Any number of processes
may share the file, and SQLite serializes them.

- **Idempotency.** A reservation's scope is (caller hub, pool, seat, task, operation,
  key). An identical retry returns the stored response; changed content is refused as
  ``idempotency_conflict``. :meth:`SpendLedger.query` returns the stored response for
  a scope without creating anything, which is how a caller recovers a lost reply.
- **Uniform refusal (C4).** A refused requester sees only
  ``{"admitted": false, "reason": "not-admitted"}``. The detailed reason is an audit
  row that only the operator reads.
- **Operator acts.** Configuring a pool and reconciling a reservation are local
  operations on the owner host. They are never exposed over the wire.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import (
    SecurePathError,
    apply_owner_only_file,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
)
from synapse_channel.core.spend_pool import (
    PROVENANCES,
    PoolConfig,
    PoolState,
    ReservationRequest,
    SpendPoolError,
    fold,
    quantity,
    token,
    validate_config,
    validate_request,
)

SCHEMA_VERSION: Final = 1
"""SQLite schema version accepted by this implementation."""

NOT_ADMITTED: Final = "not-admitted"
"""The only refusal reason a requester ever sees (review correction C4)."""

_SCHEMA = (
    """CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        pool_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        body TEXT NOT NULL
    )""",
    "CREATE INDEX events_by_pool ON events (pool_id, seq)",
    """CREATE TABLE operations (
        scope TEXT PRIMARY KEY,
        digest TEXT NOT NULL,
        response TEXT NOT NULL
    )""",
)
_SETTLE_FIELDS = frozenset(
    {"pool_id", "reservation_id", "usage_ref", "amount", "provenance", "final"}
)
_RECONCILE_FIELDS = frozenset({"pool_id", "reservation_id", "amount", "evidence_ref", "cause"})
_QUERY_FIELDS = frozenset({"pool_id", "seat", "task", "operation", "key"})


class SpendLedgerError(SynapseError, ValueError):
    """Raised when the ledger is unavailable or an operator act is invalid."""

    code = "spend_ledger"


def _canonical(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(document).encode("ascii")).hexdigest()


def _scope(*parts: str) -> str:
    return "\x00".join(parts)


def _refusal(field: str) -> dict[str, object]:
    return {field: False, "reason": NOT_ADMITTED}


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise SpendLedgerError("the evaluation time must include a UTC offset")


class SpendLedger:
    """The owner-only spend ledger of one pool-owner hub.

    Parameters
    ----------
    path : str or Path
        The ledger file. Its directory must already be owner-only; the file is
        created owner-only.
    owner_hub_id : str
        This hub's id. A pool configured for another owner grants nothing here.
    """

    def __init__(self, path: str | Path, *, owner_hub_id: str) -> None:
        self.path = Path(path).expanduser()
        self.owner_hub_id = owner_hub_id
        with closing(self._connect()):
            pass

    def _connect(self) -> sqlite3.Connection:
        """Open the version-checked ledger inside its owner-only directory."""
        try:
            assert_owner_only_dir_path(self.path.parent, purpose="spend ledger directory")
            if self.path.is_symlink():
                raise SpendLedgerError("the spend ledger must not be a symlink")
            existed = self.path.exists()
            if existed:
                assert_owner_only_file_path(self.path, purpose="spend ledger")
            connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        except SecurePathError as exc:
            raise SpendLedgerError(str(exc)) from exc
        except (sqlite3.Error, OSError) as exc:
            raise SpendLedgerError("cannot open the spend ledger") from exc
        try:
            if not existed:
                apply_owner_only_file(self.path)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                if existed:
                    raise SpendLedgerError("an unversioned spend ledger is refused")
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.execute("COMMIT")
            elif version != SCHEMA_VERSION:
                raise SpendLedgerError(f"unsupported spend ledger version {version}")
        except (sqlite3.Error, SpendLedgerError) as exc:
            connection.close()
            if isinstance(exc, SpendLedgerError):
                raise
            raise SpendLedgerError("the spend ledger is not a valid database") from exc
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Hold the ledger's write lock for one decision; roll back on any error."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    @staticmethod
    def _pool(connection: sqlite3.Connection, pool_id: str) -> PoolState:
        rows = connection.execute(
            "SELECT kind, body FROM events WHERE pool_id = ? ORDER BY seq", (pool_id,)
        ).fetchall()
        return fold({"kind": kind, "body": json.loads(body)} for kind, body in rows)

    @staticmethod
    def _append(
        connection: sqlite3.Connection,
        pool_id: str,
        kind: str,
        body: Mapping[str, Any],
        now: datetime,
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO events (pool_id, kind, recorded_at, body) VALUES (?, ?, ?, ?)",
            (pool_id, kind, now.isoformat(), _canonical(body)),
        )
        return int(cursor.lastrowid or 0)

    @staticmethod
    def _stored(
        connection: sqlite3.Connection, scope: str, digest: str, field: str
    ) -> dict[str, object] | None:
        """Return the stored response for ``scope``; a changed request is a conflict."""
        row = connection.execute(
            "SELECT digest, response FROM operations WHERE scope = ?", (scope,)
        ).fetchone()
        if row is None:
            return None
        if row[0] != digest:
            return {field: False, "reason": "idempotency_conflict"}
        stored: dict[str, object] = json.loads(row[1])
        return stored

    @staticmethod
    def _store(
        connection: sqlite3.Connection, scope: str, digest: str, response: Mapping[str, object]
    ) -> None:
        connection.execute(
            "INSERT INTO operations (scope, digest, response) VALUES (?, ?, ?)",
            (scope, digest, _canonical(response)),
        )

    def configure(self, document: object, *, now: datetime) -> dict[str, object]:
        """Append one operator configuration of a pool; return its pool id and revision.

        Raises
        ------
        SpendLedgerError
            When the document is invalid or lowers the owner epoch.
        """
        _require_aware(now)
        try:
            config = validate_config(document)
        except SpendPoolError as exc:
            raise SpendLedgerError(str(exc)) from exc
        body = dict(cast("Mapping[str, Any]", document))  # validate_config proved the shape
        with self._transaction() as connection:
            state = self._pool(connection, config.pool_id)
            if state.config is not None and config.epoch < state.config.epoch:
                raise SpendLedgerError("the owner epoch cannot go backwards")
            self._append(connection, config.pool_id, "pool_config", body, now)
        return {"pool_id": config.pool_id, "revision": state.revision + 1}

    def reserve(self, caller: str, document: object, *, now: datetime) -> dict[str, object]:
        """Decide one reservation for ``caller`` (the verified peer hub).

        Returns
        -------
        dict[str, object]
            A grant (``admitted`` true, with the reservation id, epoch, configuration
            revision, sequence, expiry, exposure and unit), the uniform refusal, or
            ``idempotency_conflict`` for a changed request under a used key.
        """
        _require_aware(now)
        try:
            request = validate_request(document)
        except SpendPoolError:
            return _refusal("admitted")
        scope = _scope(
            "reserve",
            caller,
            request.pool_id,
            request.seat,
            request.task,
            request.operation,
            request.key,
        )
        digest = _digest({"caller": caller, **request.document()})
        with self._transaction() as connection:
            stored = self._stored(connection, scope, digest, "admitted")
            if stored is not None:
                return stored
            state = self._pool(connection, request.pool_id)
            reason = self._refusal_reason(state, caller, request, now)
            if reason is not None:
                self._append(
                    connection,
                    request.pool_id,
                    "refusal",
                    {"caller": caller, "reason": reason, "request": request.document()},
                    now,
                )
                response = _refusal("admitted")
            else:
                response = self._grant(connection, state, caller, request, scope, now)
            self._store(connection, scope, digest, response)
        return response

    def _refusal_reason(
        self, state: PoolState, caller: str, request: ReservationRequest, now: datetime
    ) -> str | None:
        config = state.config
        if config is None:
            return "unknown_pool"
        checks = (
            (config.owner_hub_id != self.owner_hub_id, "not_owner"),
            ((caller, request.project) not in config.grantees, "caller_not_granted"),
            (request.unit != config.unit, "unit_mismatch"),
            (request.tax != config.tax, "tax_basis_mismatch"),
            (request.price_revision != config.price_revision, "price_revision_mismatch"),
            (not config.window_starts_at <= now < config.window_ends_at, "outside_window"),
            (request.depth > config.max_depth, "depth_exceeded"),
            (request.wall_seconds > config.max_wall_seconds, "wall_time_exceeded"),
            (bool(state.overruns()), "unreconciled_overrun"),
            (
                len(state.active_seats(now) | {request.seat}) > config.max_agents,
                "agents_exceeded",
            ),
            (
                state.settled() + state.outstanding() + config.exposure(request.upper_bound)
                > config.hard_bound,
                "bound_exceeded",
            ),
        )
        return next((reason for failed, reason in checks if failed), None)

    def _grant(
        self,
        connection: sqlite3.Connection,
        state: PoolState,
        caller: str,
        request: ReservationRequest,
        scope: str,
        now: datetime,
    ) -> dict[str, object]:
        config = cast("PoolConfig", state.config)  # _refusal_reason refused an unconfigured pool
        reservation_id = "rsv-" + hashlib.sha256(scope.encode()).hexdigest()[:32]
        expires_at = min(now + timedelta(seconds=request.wall_seconds), config.window_ends_at)
        exposure = config.exposure(request.upper_bound)
        body = {
            "reservation_id": reservation_id,
            "caller": caller,
            "seat": request.seat,
            "project": request.project,
            "task": request.task,
            "operation": request.operation,
            "depth": request.depth,
            "requested_upper": str(request.upper_bound),
            "exposure": str(exposure),
            "epoch": config.epoch,
            "config_revision": state.revision,
            "expires_at": expires_at.isoformat(),
        }
        sequence = self._append(connection, request.pool_id, "grant", body, now)
        return {
            "admitted": True,
            "reservation_id": reservation_id,
            "pool_id": request.pool_id,
            "epoch": config.epoch,
            "config_revision": state.revision,
            "sequence": sequence,
            "expires_at": expires_at.isoformat(),
            "exposure": str(exposure),
            "unit": config.unit,
        }

    def settle(self, caller: str, document: object, *, now: datetime) -> dict[str, object]:
        """Record usage against ``caller``'s own reservation, idempotently per usage ref.

        A final settlement closes the reservation and releases its remaining exposure.
        A running total above the reservation's exposure is an overrun, which blocks
        new grants until the operator reconciles it.
        """
        _require_aware(now)
        try:
            if not isinstance(document, Mapping) or set(document) != _SETTLE_FIELDS:
                raise SpendPoolError("settlement has the wrong fields")
            pool_id = token(document["pool_id"], "pool_id")
            reservation_id = token(document["reservation_id"], "reservation_id")
            usage_ref = token(document["usage_ref"], "usage_ref")
            amount = quantity(document["amount"], "amount")
            if document["provenance"] not in PROVENANCES or not isinstance(document["final"], bool):
                raise SpendPoolError("settlement provenance or final flag is invalid")
        except SpendPoolError:
            return _refusal("settled")
        scope = _scope("settle", caller, pool_id, reservation_id, usage_ref)
        digest = _digest({"caller": caller, **document})
        with self._transaction() as connection:
            stored = self._stored(connection, scope, digest, "settled")
            if stored is not None:
                return stored
            reservation = self._pool(connection, pool_id).reservations.get(reservation_id)
            if reservation is None or reservation.caller != caller or reservation.closed:
                self._append(
                    connection,
                    pool_id,
                    "refusal",
                    {
                        "caller": caller,
                        "reason": "settlement_not_accepted",
                        "request": dict(document),
                    },
                    now,
                )
                response = _refusal("settled")
            else:
                body = {
                    "reservation_id": reservation_id,
                    "usage_ref": usage_ref,
                    "amount": str(amount),
                    "provenance": document["provenance"],
                    "final": document["final"],
                }
                self._append(connection, pool_id, "settlement", body, now)
                response = {
                    "settled": True,
                    "reservation_id": reservation_id,
                    "usage_ref": usage_ref,
                    "overrun": reservation.settled + amount > reservation.exposure,
                }
            self._store(connection, scope, digest, response)
        return response

    def reconcile(self, document: object, *, now: datetime) -> dict[str, object]:
        """Close a reservation with the operator's evidenced final amount.

        Raises
        ------
        SpendLedgerError
            When the document is invalid, the reservation is unknown, or it is already
            closed with no open overrun.
        """
        _require_aware(now)
        if not isinstance(document, Mapping) or set(document) != _RECONCILE_FIELDS:
            raise SpendLedgerError("a reconciliation has exactly the documented fields")
        try:
            pool_id = token(document["pool_id"], "pool_id")
            reservation_id = token(document["reservation_id"], "reservation_id")
            amount = quantity(document["amount"], "amount")
            evidence_ref = token(document["evidence_ref"], "evidence_ref")
        except SpendPoolError as exc:
            raise SpendLedgerError(str(exc)) from exc
        cause = document["cause"]
        if not isinstance(cause, str) or not cause.strip():
            raise SpendLedgerError("a reconciliation needs a cause")
        with self._transaction() as connection:
            reservation = self._pool(connection, pool_id).reservations.get(reservation_id)
            if reservation is None:
                raise SpendLedgerError("no such reservation")
            if reservation.closed and not reservation.overrun_open:
                raise SpendLedgerError("the reservation is already closed")
            body = {
                "reservation_id": reservation_id,
                "amount": str(amount),
                "evidence_ref": evidence_ref,
                "cause": cause.strip(),
            }
            self._append(connection, pool_id, "reconciliation", body, now)
        return {"reconciled": True, "reservation_id": reservation_id}

    def query(self, caller: str, document: object) -> dict[str, object]:
        """Return the stored reservation response for ``caller``'s scope, creating nothing."""
        if not isinstance(document, Mapping) or set(document) != _QUERY_FIELDS:
            return {"found": False}
        try:
            parts = [token(document[name], name) for name in sorted(_QUERY_FIELDS)]
        except SpendPoolError:
            return {"found": False}
        values = dict(zip(sorted(_QUERY_FIELDS), parts, strict=True))
        scope = _scope(
            "reserve",
            caller,
            values["pool_id"],
            values["seat"],
            values["task"],
            values["operation"],
            values["key"],
        )
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT response FROM operations WHERE scope = ?", (scope,)
            ).fetchone()
        if row is None:
            return {"found": False}
        return {"found": True, "response": json.loads(row[0])}

    def status(self, pool_id: str, *, now: datetime) -> dict[str, object]:
        """Return the operator's view of a pool: bound, charged, held and headroom.

        Raises
        ------
        SpendLedgerError
            When the pool is not configured.
        """
        _require_aware(now)
        with closing(self._connect()) as connection:
            state = self._pool(connection, pool_id)
        config = state.config
        if config is None:
            raise SpendLedgerError("no such pool")
        settled, outstanding = state.settled(), state.outstanding()
        return {
            "pool_id": pool_id,
            "owner_hub_id": config.owner_hub_id,
            "epoch": config.epoch,
            "revision": state.revision,
            "unit": config.unit,
            "tax": config.tax,
            "hard_bound": str(config.hard_bound),
            "settled": str(settled),
            "outstanding": str(outstanding),
            "headroom": str(config.hard_bound - settled - outstanding),
            "overruns": list(state.overruns()),
            "active_agents": len(state.active_seats(now)),
        }

    def audit(self, pool_id: str) -> list[dict[str, Any]]:
        """Return every recorded event of a pool, oldest first, for the operator."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT seq, kind, recorded_at, body FROM events WHERE pool_id = ? ORDER BY seq",
                (pool_id,),
            ).fetchall()
        return [
            {"seq": seq, "kind": kind, "recorded_at": recorded_at, "body": json.loads(body)}
            for seq, kind, recorded_at, body in rows
        ]
