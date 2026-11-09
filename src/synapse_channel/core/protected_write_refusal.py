# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — protected pre-mutation refusal classification
"""Mark effect-free authority validation without spanning mutation or async work."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from synapse_channel.core.errors import SynapseError

_Arguments = ParamSpec("_Arguments")
_Result = TypeVar("_Result")


class ProtectedWriteRefusalError(SynapseError, ValueError):
    """A trusted check refused before committing the requested protected mutation.

    Attributes
    ----------
    code : str
        Stable classification. A frontend cannot supply this authority-owned
        exception or assert an equivalent flag in a control envelope.

    Notes
    -----
    Use only before persistence, or inside an I/O-free response finalizer whose
    failure rolls back the enclosing journal transaction. Never mark an error
    after a possible commit as a refusal; its outcome must remain unknown.
    The local exception preserves the original reason for API compatibility;
    transport adapters still emit their fixed refusal text, never this reason.
    This marks only the current invocation. It cannot clear an earlier unknown
    outcome for the same operation identity; reconcile that original journal row.
    """

    code = "protected_write_refusal"


def protected_write_pre_commit(
    action: Callable[_Arguments, _Result],
) -> Callable[_Arguments, _Result]:
    """Classify ValueError only during a synchronous effect-free callback.

    Parameters
    ----------
    action:
        Trusted pre-mutation validation or I/O-free pre-commit builder. Async
        execution is not enclosed: a returned awaitable is passed through and
        its later failures are never labelled effect-free by this adapter.

    Returns
    -------
    Callable
        Signature-preserving callback with the same successful return value.

    Raises
    ------
    ProtectedWriteRefusalError
        When the callback raises ValueError before returning. Other failures
        retain their original type and no success is fabricated.
    """

    @wraps(action)
    def checked(*args: _Arguments.args, **kwargs: _Arguments.kwargs) -> _Result:
        try:
            return action(*args, **kwargs)
        except ProtectedWriteRefusalError:
            raise
        except ValueError as exc:
            raise ProtectedWriteRefusalError(str(exc)) from exc

    return checked
