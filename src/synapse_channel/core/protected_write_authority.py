# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared protected authority assembly
"""Bind operator enrollment and all protected verbs to one durable authority."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from websockets.asyncio.server import Server, unix_serve

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_handler_binding import assemble_protected_write_dispatch
from synapse_channel.core.protected_write_listener import (
    DEFAULT_FORBIDDEN_UIDS,
    validate_authority_role_policy,
    validate_protected_authority_listener,
)
from synapse_channel.core.protected_write_operator_socket import (
    retained_listener_options,
    serve_operator_listener,
)
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from synapse_channel.core.protected_write_unix_ingress import ProtectedWriteUnixIngress


@dataclass(frozen=True)
class ProtectedWriteAuthority:
    """Retain the operator control and ingress sharing one enrollment registry.

    The trusted launcher passes ``operator_control`` to the separately validated
    operator listener and ``ingress`` to the protected request listener. This
    object owns neither listener nor either persistent store. Constructing it
    creates no enrollment, account, socket, writer process or execution permit.
    """

    operator_control: ProtectedEnrollmentControl
    ingress: ProtectedWriteTransport
    max_wire_bytes: int


@dataclass(frozen=True)
class ProtectedAuthorityListeners:
    """Context-owned paired acceptors, neither writer process nor custody proof."""

    operator: asyncio.AbstractServer
    requests: Server


def assemble_protected_write_authority(
    *,
    hub: SynapseHub,
    sealed_keys: Mapping[str, MessageAuthKey],
    effects: ProtectedPreparationPolicy,
    replay_policy: ProtectedAdmissionReplayPolicy,
    writer: tuple[str, str],
    operator_principals: frozenset[str],
    recovery_principals: frozenset[str],
    clock: Callable[[], float],
    sign_response: Callable[[dict[str, object]], Mapping[str, object]],
    replay_store: DurableMessageAuthReplayStore,
    sequence_floor_mode: SequenceFloorMode,
) -> ProtectedWriteAuthority:
    """Assemble both control paths with the same actor, keys, clock and registry.

    Parameters
    ----------
    hub:
        Sole durable authority actor with complete protected replay history.
    sealed_keys:
        Independently retained session key material from the trusted launcher.
        This function does not seal or authenticate its source.
    effects:
        Exact operator-enrolled proposal and filesystem effect scope.
    replay_policy:
        Matching retained admission policy with a real recovery verifier.
    writer:
        Independently enrolled writer principal and incarnation.
    operator_principals:
        Protected protocol principals permitted to revoke reservations; these
        do not confer access to the separate OS-authenticated enrollment socket.
    recovery_principals:
        Independently enrolled recovery service principals.
    clock:
        Trusted clock shared by enrollment, ingress and every verb handler.
    sign_response:
        Trusted I/O-free signer for all authority responses.
    replay_store:
        Caller-owned file-backed nonce ledger retained across listener restarts.
    sequence_floor_mode:
        Explicit operator-selected sequence profile.

    Returns
    -------
    ProtectedWriteAuthority
        Operator control and complete seven-verb ingress. Every handler and
        ingress reads this control's current registry rather than a separate
        caller-supplied session map.

    Raises
    ------
    ValueError
        If the durable journal, recovery policy, effect limits or ingress replay
        prerequisites are unavailable or inconsistent.

    Notes
    -----
    The supervisor still owns root installation validation, dedicated role UIDs,
    credential confidentiality, prebound listener identities, process/descriptor
    custody, namespace policy, shutdown and physical recovery proof. Use
    ``serve_operator_listener`` for its strict root-owned operator endpoint;
    never expose enrollment mutations through the ordinary hub socket.
    Partial construction publishes no result and mutates neither store.
    """
    control = ProtectedEnrollmentControl(hub=hub, sealed_keys=sealed_keys, clock=clock)
    dispatch = assemble_protected_write_dispatch(
        hub=hub,
        effects=effects,
        replay_policy=replay_policy,
        current_enrollments=control.current,
        writer=writer,
        operator_principals=operator_principals,
        recovery_principals=recovery_principals,
        clock=clock,
        sign_response=sign_response,
    )
    ingress = ProtectedWriteTransport(
        limits=effects.limits,
        current_enrollments=control.current,
        replay_store=replay_store,
        sequence_floor_mode=sequence_floor_mode,
        clock=clock,
        dispatch=dispatch,
        reason_codes=replay_policy.reason_codes,
    )
    return ProtectedWriteAuthority(control, ingress, effects.limits.json_limits.max_wire_bytes)


@asynccontextmanager
async def serve_protected_write_authority(
    authority: ProtectedWriteAuthority,
    *,
    operator_listener: socket.socket,
    request_listener: socket.socket,
    operator_uid: int,
    request_uids: frozenset[int],
    forbidden_uids: frozenset[int] = DEFAULT_FORBIDDEN_UIDS,
) -> AsyncIterator[ProtectedAuthorityListeners]:
    """Own both prebound authority listeners through startup and shutdown.

    Parameters
    ----------
    authority:
        Trusted assembled actor/control/ingress with its exact wire byte bound.
    operator_listener:
        Independently retained root-owned Unix enrollment listener.
    request_listener:
        Distinct root-owned Unix protected request listener.
    operator_uid:
        Dedicated operator UID distinct from this authority process.
    request_uids:
        Immutable independently enrolled dedicated client/writer/service UIDs.
        The authority, operator and deployment-forbidden UIDs are refused.
    forbidden_uids:
        Explicit deployment exclusions; root must remain excluded. Defaults
        additionally exclude workstation UID1000 without fabricating any identity.

    Yields
    ------
    ProtectedAuthorityListeners
        Paired servers after both have been constructed and accepts enabled.
        The context owns supplied sockets once each server takes ownership.

    Raises
    ------
    ValueError
        On shared endpoints/roles or invalid root-owned listener boundaries.
    OSError
        If endpoint metadata or server startup is unavailable.

    Notes
    -----
    No socket path, account, mount or writer is created. Stores and retained keys
    stay caller-owned and must outlive context exit. Request shutdown aborts and
    drains owned commands before transport teardown; the operator context then
    stops/drains its commands. Failure after the first server is acquired unwinds
    its ownership. Interrupted durable mutations require journal inspection.
    Immutable installation/credential identity, namespace and descriptor custody,
    supervised writer lifecycle and physical recovery remain separate prerequisites.
    """
    ingress = ProtectedWriteUnixIngress(authority.ingress, allowed_uids=request_uids)
    authority_uid = validate_authority_role_policy(operator_uid, forbidden_uids=forbidden_uids)
    if any(uid in forbidden_uids or uid in {authority_uid, operator_uid} for uid in request_uids):
        raise ValueError("protected request peers require separate dedicated UIDs")
    operator_stat, request_stat = (
        os.fstat(operator_listener.fileno()),
        os.fstat(request_listener.fileno()),
    )
    if (operator_stat.st_dev, operator_stat.st_ino) == (
        request_stat.st_dev,
        request_stat.st_ino,
    ):
        raise ValueError("operator and request listeners must be distinct")
    validate_protected_authority_listener(
        operator_listener, operator_uid, forbidden_uids=forbidden_uids
    )
    validate_protected_authority_listener(
        request_listener, operator_uid, forbidden_uids=forbidden_uids, endpoint="request"
    )
    async with serve_operator_listener(
        operator_listener,
        control=authority.operator_control,
        operator_uid=operator_uid,
        start_serving=False,
        forbidden_uids=forbidden_uids,
    ) as operator:
        request_listener.listen(socket.SOMAXCONN)
        validate_protected_authority_listener(
            request_listener,
            operator_uid,
            forbidden_uids=forbidden_uids,
            endpoint="request",
            listening=True,
        )
        requests = await unix_serve(
            ingress,
            create_connection=ingress.connection_type(),
            sock=request_listener,
            max_size=authority.max_wire_bytes,
            max_queue=1,
            open_timeout=5,
            close_timeout=2,
            start_serving=False,
            **retained_listener_options(),
        )
        try:
            await requests.start_serving()
            await operator.start_serving()
            yield ProtectedAuthorityListeners(operator, requests)
        finally:
            operator.close()
            requests.close()
            try:
                await ingress.aclose()
            finally:
                await requests.wait_closed()
