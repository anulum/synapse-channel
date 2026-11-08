# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: evidence, causality and opentelemetry
"""Evidence, causality and OpenTelemetry: the federation interface names of this capability.

Journal kinds, record purposes, causal graphs across hubs, correlation and OpenTelemetry
export. Import these names from ``synapse_channel.federation_api``; this module only
groups them.
"""

from __future__ import annotations

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    GRAPH_KINDS,
    LIFECYCLE,
)
from synapse_channel.core.causality_federation import (
    FEDERATION,
    FederatedCausalGraph,
    FederatedNode,
    HubEventRef,
    build_federated_graph,
)
from synapse_channel.core.causality_otel import (
    SERVICE_NAME,
    SPAN_ID_HEX_LENGTH,
    SPAN_STATUS_ERROR,
    SPAN_STATUS_UNSET,
    OtelProjection,
    OtelSpanRecord,
    SpanAttributeValue,
    SpanLinkRecord,
    projection_to_json,
    span_id_for_root,
    trace_id_for_task,
)
from synapse_channel.core.correlation_contract import (
    CONTRACT_RESOURCE_ATTRIBUTE,
    CORRELATION_CONTRACT,
    CORRELATION_SCHEMA_URL,
)
from synapse_channel.core.correlation_points import (
    CORRELATION_KINDS,
    CorrelationPoint,
    correlation_points,
)
from synapse_channel.core.journal import (
    EventKind,
)
from synapse_channel.core.lifecycle import (
    TaskStatus,
)
from synapse_channel.core.record_keeping import (
    GENERAL_RECORD,
    OPERATION_RECORD,
    RISK_INDICATOR,
    purpose_for_kind,
)
from synapse_channel.core.task_causality import (
    TASK_CAUSAL_PARENT_FIELD,
    parse_task_causal_parent,
)
from synapse_channel.otel_export import (
    DEFAULT_EXPORT_TIMEOUT,
    push_projection,
)

__all__ = [
    "CONTRACT_RESOURCE_ATTRIBUTE",
    "CORRELATION_CONTRACT",
    "CORRELATION_KINDS",
    "CORRELATION_SCHEMA_URL",
    "CorrelationPoint",
    "DEFAULT_EXPORT_TIMEOUT",
    "DEFAULT_MAX_GRAPH_NODES",
    "EventKind",
    "FEDERATION",
    "FederatedCausalGraph",
    "FederatedNode",
    "GENERAL_RECORD",
    "GRAPH_KINDS",
    "HubEventRef",
    "LIFECYCLE",
    "OPERATION_RECORD",
    "OtelProjection",
    "OtelSpanRecord",
    "RISK_INDICATOR",
    "SERVICE_NAME",
    "SPAN_ID_HEX_LENGTH",
    "SPAN_STATUS_ERROR",
    "SPAN_STATUS_UNSET",
    "SpanAttributeValue",
    "SpanLinkRecord",
    "TASK_CAUSAL_PARENT_FIELD",
    "TaskStatus",
    "build_federated_graph",
    "correlation_points",
    "parse_task_causal_parent",
    "projection_to_json",
    "purpose_for_kind",
    "push_projection",
    "span_id_for_root",
    "trace_id_for_task",
]
