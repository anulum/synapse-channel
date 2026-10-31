# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write authority handler binding
"""Compose effect, lifecycle and recovery handlers for one protected authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_admit_handler import ProtectedWriteAdmitHandler
from synapse_channel.core.protected_write_dispatch import ProtectedHandler, ProtectedWriteDispatch
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_prepare_handler import ProtectedWritePrepareHandler
from synapse_channel.core.protected_write_recover_handler import ProtectedWriteRecoverHandler
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_status import ProtectedWriteStatus
from synapse_channel.core.protected_write_transition_handler import ProtectedWriteTransitionHandler


def _admission_handlers(
    *,
    hub: SynapseHub,
    effects: ProtectedPreparationPolicy,
    replay_policy: ProtectedAdmissionReplayPolicy,
    current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
    writer: tuple[str, str],
    clock: Callable[[], float],
    sign_response: Callable[[dict[str, object]], Mapping[str, object]],
) -> dict[str, ProtectedHandler]:
    """Bind proposal preparation and admission to one current effect policy."""
    prepare = ProtectedWritePrepareHandler(
        hub=hub,
        policy=effects,
        current_enrollments=current_enrollments,
        clock=clock,
        sign_response=sign_response,
        reason_codes=replay_policy.reason_codes,
    )
    admit = ProtectedWriteAdmitHandler(
        hub=hub,
        policy=effects,
        current_enrollments=current_enrollments,
        writer=writer,
        clock=clock,
        sign_response=sign_response,
        reason_codes=replay_policy.reason_codes,
        max_reservations=replay_policy.max_reservations,
    )
    return {"protected_write_prepare": prepare, "protected_write_admit": admit}


def _lifecycle_handlers(
    *,
    hub: SynapseHub,
    effects: ProtectedPreparationPolicy,
    replay_policy: ProtectedAdmissionReplayPolicy,
    current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
    writer: tuple[str, str],
    operator_principals: frozenset[str],
    clock: Callable[[], float],
    sign_response: Callable[[dict[str, object]], Mapping[str, object]],
) -> dict[str, ProtectedHandler]:
    """Bind reservation transitions and reads to one replay policy and actor."""
    transition = ProtectedWriteTransitionHandler(
        hub=hub,
        policy=replay_policy,
        enrollment_revision=effects.enrollment_revision,
        current_enrollments=current_enrollments,
        writer_principal=writer[0],
        operator_principals=operator_principals,
        clock=clock,
        sign_response=sign_response,
    )
    status = ProtectedWriteStatus(
        hub=hub,
        limits=effects.limits,
        current_enrollments=current_enrollments,
        clock=clock,
        sign_response=sign_response,
        reason_codes=replay_policy.reason_codes,
        max_reservations=replay_policy.max_reservations,
    )
    return {
        "protected_write_begin": transition,
        "protected_write_settle": transition,
        "protected_write_revoke": transition,
        "protected_write_status": status,
    }


def assemble_protected_write_dispatch(
    *,
    hub: SynapseHub,
    effects: ProtectedPreparationPolicy,
    replay_policy: ProtectedAdmissionReplayPolicy,
    current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
    writer: tuple[str, str],
    operator_principals: frozenset[str],
    recovery_principals: frozenset[str],
    clock: Callable[[], float],
    sign_response: Callable[[dict[str, object]], Mapping[str, object]],
) -> ProtectedWriteDispatch:
    """Bind all protected handlers to one enrolled durable authority.

    Parameters
    ----------
    hub : SynapseHub
        Sole authority actor and journal used by every handler.
    effects : ProtectedPreparationPolicy
        Current enrolled scope for preparation, admission and recovery.
    replay_policy : ProtectedAdmissionReplayPolicy
        Matching retained policy and independently enrolled recovery verifier.
    current_enrollments : callable
        Actor-ordered current session registry shared with authenticated ingress.
    writer : tuple of str
        Exact enrolled writer principal and incarnation.
    operator_principals : frozenset of str
        Principals allowed to revoke reservations.
    recovery_principals : frozenset of str
        Principals permitted to request separately admitted recovery.
    clock : callable
        Trusted clock shared with ingress and enrollment control.
    sign_response : callable
        I/O-free authority signer shared by all concrete handlers.

    Returns
    -------
    ProtectedWriteDispatch
        Immutable complete seven-verb dispatch over the concrete handlers.

    Raises
    ------
    ValueError
        If effect/replay limits differ, the replay policy is not the exact
        retained revision policy, or a required durable/recovery boundary is unavailable.

    Notes
    -----
    This binds policies; it does not create enrollments, install a writer or
    prove credential, namespace, process or filesystem custody. The authority
    owner constructs authenticated ingress around the resulting dispatch.
    """
    if effects.limits != replay_policy.limits:
        raise ValueError("protected effect and replay limits differ")
    retained = hub.configuration.protected_write_policies
    if retained is None or retained.get(effects.enrollment_revision) is not replay_policy:
        raise ValueError("protected replay policy differs from the retained enrollment revision")
    admission = _admission_handlers(
        hub=hub,
        effects=effects,
        replay_policy=replay_policy,
        current_enrollments=current_enrollments,
        writer=writer,
        clock=clock,
        sign_response=sign_response,
    )
    lifecycle = _lifecycle_handlers(
        hub=hub,
        effects=effects,
        replay_policy=replay_policy,
        current_enrollments=current_enrollments,
        writer=writer,
        operator_principals=operator_principals,
        clock=clock,
        sign_response=sign_response,
    )
    recover = ProtectedWriteRecoverHandler(
        hub=hub,
        policy=replay_policy,
        effects=effects,
        current_enrollments=current_enrollments,
        recovery_principals=recovery_principals,
        writer=writer,
        clock=clock,
        sign_response=sign_response,
    )
    return ProtectedWriteDispatch({**admission, **lifecycle, "protected_write_recover": recover})
