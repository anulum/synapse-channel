# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable recipient bridge for provider participants
"""Run accepted follow-up and next-turn offers through a real Participant."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.participant import Participant

logger = logging.getLogger("synapse.delivery.bridge")


class DeliveryParticipantBridge:
    """One session-bound executor with a local duplicate guard and ordered queue.

    The bridge persists only request identity, digest and outcome code. A replayed
    queued offer with the same identity is safe to accept after a socket loss.
    Provider-side effects remain at-least-once across a process crash during a
    turn; the bridge never labels an unreported turn as completed.
    """

    def __init__(self, agent: SynapseAgent, participant: Participant, *, ledger_path: Path) -> None:
        if agent.name != participant.identity:
            raise ValueError("delivery agent and participant identities must match")
        if agent.delivery_capabilities is None or not set(agent.delivery_capabilities) <= {
            "follow_up",
            "next_turn",
        }:
            raise ValueError("bridge supports follow_up and next_turn only")
        self.agent = agent
        self.participant = participant
        self._conn = sqlite3.connect(ledger_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS delivery_bridge ("
            "operation_key TEXT PRIMARY KEY, digest TEXT NOT NULL, "
            "stage TEXT NOT NULL, outcome_code TEXT NOT NULL DEFAULT '')"
        )
        self._conn.commit()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
        self._queued: set[str] = set()
        self._cancel_requested: set[str] = set()
        self._runner: asyncio.Task[None] | None = None
        self._provider_session = ""
        self._stage_waiter: tuple[str, str, str, asyncio.Future[None]] | None = None

    def start(self) -> None:
        """Start the ordered executor before connecting the agent to the hub."""
        if self._runner is not None:
            raise RuntimeError("delivery bridge already started")
        if not self.participant.health().available:
            raise RuntimeError("delivery participant is unavailable")
        self._runner = asyncio.create_task(self._run())

    async def close(self) -> None:
        """Cancel the local worker and close its durable duplicate ledger."""
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        self._conn.close()

    @staticmethod
    def _valid_offer(frame: dict[str, Any], agent: SynapseAgent) -> bool:
        """Refuse malformed or newly incarnated offers before local acceptance."""
        key = frame.get("operation_key")
        body = frame.get("body")
        deadline = frame.get("deadline")
        return (
            frame.get("type") == MessageType.DELIVERY_OFFER
            and frame.get("target") == agent.name
            and frame.get("target_incarnation") == agent.delivery_incarnation
            and isinstance(key, str)
            and len(key) == 64
            and all(character in "0123456789abcdef" for character in key)
            and isinstance(frame.get("request_id"), str)
            and isinstance(frame.get("task_id"), str)
            and isinstance(body, str)
            and 0 < len(body.encode()) <= 8192
            and isinstance(deadline, (int, float))
            and not isinstance(deadline, bool)
            and math.isfinite(deadline)
            and isinstance(frame.get("selected_mode"), str)
            and frame["selected_mode"] in (agent.delivery_capabilities or {})
        )

    async def on_message(self, frame: dict[str, Any]) -> None:
        """Queue a matching offer without blocking the agent's socket reader."""
        waiter = self._stage_waiter
        if waiter is not None and frame.get("target") == self.agent.name:
            key, stage, request_id, future = waiter
            if (
                frame.get("type") == MessageType.DELIVERY_STATUS
                and frame.get("operation_key") == key
                and frame.get("stage") == stage
                and not future.done()
            ):
                future.set_result(None)
            elif (
                frame.get("type") == MessageType.DELIVERY_REFUSED
                and frame.get("request_id") == request_id
                and not future.done()
            ):
                future.set_exception(
                    DeliveryRefusal(
                        str(frame.get("reason_code", "state_conflict")),
                        "hub refused delivery stage",
                    )
                )
        if (
            frame.get("type") == MessageType.DELIVERY_STATUS
            and frame.get("target") == self.agent.name
            and frame.get("cancel_requested") is True
            and isinstance(frame.get("operation_key"), str)
        ):
            self._cancel_requested.add(frame["operation_key"])
            return
        if not self._valid_offer(frame, self.agent):
            return
        key = frame["operation_key"]
        digest = hashlib.sha256(
            json.dumps(frame, sort_keys=True, ensure_ascii=True).encode("ascii")
        ).hexdigest()
        row = self._conn.execute(
            "SELECT digest, stage FROM delivery_bridge WHERE operation_key = ?", (key,)
        ).fetchone()
        if row is not None:
            if row[0] != digest or row[1] != "queued":
                return
        else:
            self._conn.execute(
                "INSERT INTO delivery_bridge (operation_key, digest, stage) "
                "VALUES (?, ?, 'queued')",
                (key, digest),
            )
            self._conn.commit()
        if key in self._queued:
            return
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._conn.execute(
                "UPDATE delivery_bridge SET stage = 'rejected', outcome_code = 'queue_full' "
                "WHERE operation_key = ?",
                (key,),
            )
            self._conn.commit()
            await self._report(frame, "rejected", "queue_full")
            return
        self._queued.add(key)

    async def _report(self, frame: dict[str, Any], stage: str, code: str) -> None:
        """Send one stable transition identity after local state is durable."""
        key = frame["operation_key"]
        evidence: dict[str, str]
        if stage == "boundary_delivered":
            evidence = {"boundary": f"bridge:{key}"}
        elif stage == "acknowledged":
            evidence = {"receipt_id": f"bridge:{key}"}
        elif stage in ("completed", "failed"):
            evidence = {
                "executor_ref": f"bridge:{key}",
                "outcome_code": code,
            }
        else:
            evidence = {"reason_code": code}
        delay = 0.25
        while True:
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._stage_waiter = (key, stage, frame["request_id"], future)
            try:
                await self.agent.report_delivery_stage(
                    key,
                    request_id=frame["request_id"],
                    task_id=frame["task_id"],
                    mutation_id=f"bridge:{stage}:{key}",
                    stage=stage,
                    evidence=evidence,
                )
                await asyncio.wait_for(future, 5.0)
                return
            except DeliveryRefusal as exc:
                if exc.code != "unavailable_hub":
                    raise
            except (ConnectionError, ConnectionClosed, OSError, TimeoutError):
                pass
            finally:
                self._stage_waiter = None
                if not future.done():
                    future.cancel()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)

    async def _run(self) -> None:
        """Advance accepted offers in this recipient's local queue order."""
        while True:
            frame = await self._queue.get()
            key = frame["operation_key"]
            try:
                if time.time() >= frame["deadline"]:
                    continue
                if key in self._cancel_requested:
                    self._conn.execute(
                        "UPDATE delivery_bridge SET stage = 'cancelled', "
                        "outcome_code = 'sender_cancelled' WHERE operation_key = ?",
                        (key,),
                    )
                    self._conn.commit()
                    await self._report(frame, "cancelled", "sender_cancelled")
                    continue
                await self._report(frame, "boundary_delivered", "")
                self._conn.execute(
                    "UPDATE delivery_bridge SET stage = 'boundary_delivered' "
                    "WHERE operation_key = ?",
                    (key,),
                )
                self._conn.commit()
                await self._report(frame, "acknowledged", "")
                self._conn.execute(
                    "UPDATE delivery_bridge SET stage = 'acknowledged' WHERE operation_key = ?",
                    (key,),
                )
                self._conn.commit()
                try:
                    result = await self.participant.take_turn(
                        TurnRequest(
                            topic_id=frame["task_id"] or frame["request_id"],
                            prompt=frame["body"],
                            resume_session=(
                                self._provider_session
                                if frame["selected_mode"] == "follow_up"
                                else ""
                            ),
                        )
                    )
                    if result["is_error"]:
                        stage, code = "failed", "provider_error"
                    elif result["abstained"]:
                        stage, code = "rejected", "provider_abstained"
                    else:
                        stage, code = "completed", "success"
                        if frame["selected_mode"] == "follow_up":
                            self._provider_session = result["session"]
                except Exception:
                    stage, code = "failed", "provider_exception"
                self._conn.execute(
                    "UPDATE delivery_bridge SET stage = ?, outcome_code = ? "
                    "WHERE operation_key = ?",
                    (stage, code, key),
                )
                self._conn.commit()
                await self._report(frame, stage, code)
            except DeliveryRefusal as exc:
                self._conn.execute(
                    "UPDATE delivery_bridge SET stage = 'hub_refused', outcome_code = ? "
                    "WHERE operation_key = ?",
                    (exc.code, key),
                )
                self._conn.commit()
                logger.warning("Delivery stage refused by hub: %s", exc.code)
            finally:
                self._queued.discard(key)
                self._cancel_requested.discard(key)
                self._queue.task_done()
