# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicit A2A conformance backend
"""A2A bridge for an official TCK conformance run, never for real work.

The official A2A TCK residual suite expects canned content for five scenarios
(four structured artifacts and one direct Message). The production
:class:`~synapse_channel.a2a_server.A2ABridge` forwards every send into
SYNAPSE, so request content cannot fake a completed result.
:class:`ConformanceA2ABridge` is the separate backend that answers those
scenarios; ``synapse a2a-serve --conformance-backend`` selects it once at start.
"""

from __future__ import annotations

import time
import uuid
from typing import ClassVar

from synapse_channel import a2a_errors
from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_scenario_responses import (
    ScenarioKind,
    build_completed_task_with_artifact,
    build_direct_message_response,
    resolve_scenario,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_task_flow import resolve_target


class ConformanceA2ABridge(A2ABridge):
    """Answer official TCK residual scenarios with canned results.

    Requests that match an official TCK ``messageId`` prefix or carry an
    explicit ``a2aScenario`` / ``synapseScenario`` / ``scenario`` value in
    message metadata or send configuration receive a direct Message or a
    completed Task with the required Artifact, without forwarding into
    SYNAPSE. Every other send behaves exactly as on the production bridge.
    """

    conformance_backend: ClassVar[bool] = True
    """Whether this bridge class answers TCK residual scenarios without forwarding."""

    def _conformance_answer(
        self, payload: JsonMap, message: JsonMap, configuration: JsonMap | None
    ) -> JsonMap | None:
        """Return the canned answer for a matching TCK scenario, if any."""
        scenario = resolve_scenario(message, configuration)
        if scenario is None:
            return None
        if scenario is ScenarioKind.MESSAGE_RESPONSE:
            return build_direct_message_response(message)
        task = self._create_scenario_completed_task(message, scenario)
        self._store_request_push_config(payload, task_id=str(task["id"]))
        return {"task": task}

    def _create_scenario_completed_task(self, message: JsonMap, scenario: ScenarioKind) -> JsonMap:
        """Persist a completed Task with the structured artifact for ``scenario``."""
        with self._task_creation_lock:
            self._gc_retained_tasks()
            raw_task_id = message.get("taskId")
            raw_context_id = message.get("contextId")
            task_id = str(raw_task_id or uuid.uuid4())
            context_id = str(raw_context_id or uuid.uuid4())
            if raw_task_id is not None and self.store.get(task_id) is not None:
                raise a2a_errors.A2AConflictError("message.taskId already exists")
            target = resolve_target(message, default=self.target)
            task = build_completed_task_with_artifact(
                message,
                kind=scenario,
                task_id=task_id,
                context_id=context_id,
                target=target,
                now=time.time(),
            )
            stored = self.store.put(task)
            self._publish_task_update(stored, deliver_push=False)
            return stored
