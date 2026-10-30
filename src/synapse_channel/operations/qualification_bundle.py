# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed qualification bundles and the per-profile run gate
"""Qualification that is itself evidence: signed, bound, dated and complete.

A catalogue row (:class:`~synapse_channel.operations.row.OperationRow`) may
say ``run_once`` or ``accepted`` with nothing more than an evidence text, a
fingerprint and the digest of its binding. That is the operator's own word.
It may remain the basis of a *personal* profile, labelled as such.

A *production* profile needs a qualification bundle that a trusted qualifier
signed (Ed25519, domain-separated). The bundle names:

* the exact row (operation, transport, variant) and its binding digest;
* the qualification level, the tests, the configuration digest and the
  artefacts it covered;
* the environment components the qualification ran with (interpreter,
  plugins, remote parts). The production profile must name at least one
  required component, and every one must appear in the bundle. The gate
  compares component names only; it does not check that the recorded
  identities match the process that will run.
* the qualifier and the validity period.

:func:`gate` decides whether a row may run under a profile and says on what
basis. In production, ``run_once`` covers only rows whose every effect is
``read``. A riskier effect class needs ``accepted``. The gate grants nothing
by itself; it is one precondition among the authority checks of a run.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.operations.row import OperationRow
from synapse_channel.operations.spec import RUNNABLE

SCHEMA = "synapse-qualification-bundle.v1"
"""Version of the signed bundle."""

PROFILES = frozenset({"personal", "production"})
"""Profiles the gate knows."""

MAX_VALIDITY_SECONDS = 366 * 24 * 3600
"""Longest validity period a bundle may claim."""

_DOMAIN = b"SYNAPSE-QUALIFICATION-BUNDLE-V1\x00"
_FIELDS = frozenset(
    {
        "schema",
        "operation",
        "transport",
        "variant",
        "binding_digest",
        "level",
        "tests",
        "configuration_sha256",
        "artefacts",
        "environment",
        "qualifier",
        "issued_at",
        "valid_until",
    }
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,200}")
_MAX_ITEMS = 64


class QualificationBundleError(SynapseError, ValueError):
    """A bundle, its signature or its trust material cannot be accepted."""

    code = "qualification_bundle"


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Whether a row may run under a profile, on what basis, and why not."""

    runnable: bool
    basis: str
    reasons: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        """Return the verdict as plain data."""
        return {"runnable": self.runnable, "basis": self.basis, "reasons": list(self.reasons)}


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or _TEXT.fullmatch(value) is None:
        raise QualificationBundleError(f"{label} is printable text of 1 to 200 characters")
    return value


def _mapping(value: object, label: str, *, digests: bool) -> dict[str, str]:
    if not isinstance(value, dict) or not 0 < len(value) <= _MAX_ITEMS:
        raise QualificationBundleError(f"{label} names 1 to {_MAX_ITEMS} entries")
    result: dict[str, str] = {}
    for key, item in value.items():
        result[_text(key, f"{label} name")] = _text(item, f"{label} value")
        if digests and _DIGEST.fullmatch(item) is None:
            raise QualificationBundleError(f"{label} values are sha256:<64 hex> digests")
    return result


def validate_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bundle as plain data after checking every field.

    Parameters
    ----------
    bundle : Mapping[str, Any]
        Candidate bundle body.

    Returns
    -------
    dict[str, Any]
        The validated bundle.

    Raises
    ------
    QualificationBundleError
        On a missing, unknown or malformed field.
    """
    if not isinstance(bundle, Mapping) or set(bundle) != _FIELDS:
        raise QualificationBundleError(f"a bundle has exactly the fields {sorted(_FIELDS)}")
    if bundle["schema"] != SCHEMA:
        raise QualificationBundleError(f"a bundle is a {SCHEMA} document")
    for field in ("operation", "transport", "variant", "qualifier"):
        _text(bundle[field], field)
    for field in ("binding_digest", "configuration_sha256"):
        if not isinstance(bundle[field], str) or _DIGEST.fullmatch(bundle[field]) is None:
            raise QualificationBundleError(f"{field} is a sha256:<64 hex> digest")
    if bundle["level"] not in RUNNABLE:
        raise QualificationBundleError(f"level is one of {sorted(RUNNABLE)}")
    tests = bundle["tests"]
    if not isinstance(tests, list) or not 0 < len(tests) <= _MAX_ITEMS:
        raise QualificationBundleError(f"tests names 1 to {_MAX_ITEMS} tests")
    for test in tests:
        _text(test, "test")
    _mapping(bundle["artefacts"], "artefacts", digests=True)
    _mapping(bundle["environment"], "environment", digests=False)
    issued, until = bundle["issued_at"], bundle["valid_until"]
    if (
        type(issued) is not int
        or type(until) is not int
        or not 0 < issued < until <= issued + MAX_VALIDITY_SECONDS
    ):
        raise QualificationBundleError(
            "issued_at and valid_until are whole seconds, at most 366 days apart"
        )
    return dict(bundle)


def _signed_bytes(bundle: Mapping[str, Any], key_id: str) -> bytes:
    canonical = json.dumps(
        {"bundle": dict(bundle), "key_id": key_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return _DOMAIN + canonical.encode("ascii")


def sign_bundle(bundle: Mapping[str, Any], private_key: Any, key_id: str) -> dict[str, Any]:
    """Return the signed envelope ``{bundle, key_id, signature}``.

    Parameters
    ----------
    bundle : Mapping[str, Any]
        A bundle that passes :func:`validate_bundle`.
    private_key : cryptography Ed25519PrivateKey
        The qualifier's signing key.
    key_id : str
        Identifier under which verifiers trust the matching public key.

    Returns
    -------
    dict[str, Any]
        The envelope to store beside the catalogue.
    """
    body = validate_bundle(bundle)
    _text(key_id, "key_id")
    signature = private_key.sign(_signed_bytes(body, key_id))
    return {"bundle": body, "key_id": key_id, "signature": base64.b64encode(signature).decode()}


def verify_envelope(
    envelope: Mapping[str, Any], trusted_keys: Mapping[str, bytes]
) -> dict[str, Any]:
    """Return the bundle of an envelope signed by a trusted key.

    Parameters
    ----------
    envelope : Mapping[str, Any]
        ``{bundle, key_id, signature}`` as written by :func:`sign_bundle`.
    trusted_keys : Mapping[str, bytes]
        Raw 32-byte Ed25519 public keys by key id.

    Returns
    -------
    dict[str, Any]
        The verified bundle.

    Raises
    ------
    QualificationBundleError
        On an unknown key, a bad signature or a malformed envelope or bundle.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(envelope, Mapping) or set(envelope) != {"bundle", "key_id", "signature"}:
        raise QualificationBundleError("an envelope has exactly bundle, key_id and signature")
    key_id = _text(envelope["key_id"], "key_id")
    if key_id not in trusted_keys:
        raise QualificationBundleError("the bundle is signed by a key that is not trusted")
    body = validate_bundle(envelope["bundle"])
    try:
        signature = base64.b64decode(str(envelope["signature"]), validate=True)
        public = Ed25519PublicKey.from_public_bytes(trusted_keys[key_id])
        public.verify(signature, _signed_bytes(body, key_id))
    except (ValueError, InvalidSignature) as exc:
        raise QualificationBundleError("the bundle signature does not verify") from exc
    return body


def _production_reasons(
    row: OperationRow, bundle: Mapping[str, Any], *, now: int, required: Collection[str]
) -> list[str]:
    """Return every reason the verified bundle does not cover this row now."""
    reasons = []
    if (bundle["operation"], bundle["transport"], bundle["variant"]) != (
        row.operation,
        row.transport,
        row.variant,
    ):
        reasons.append("the bundle names another operation, transport or variant")
    if bundle["binding_digest"] != row.binding_digest:
        reasons.append("the bundle qualified another binding than the row has now")
    if bundle["level"] != row.qualification:
        reasons.append("the bundle level differs from the row's qualification")
    if not bundle["issued_at"] <= now < bundle["valid_until"]:
        reasons.append("the bundle is not valid at this time")
    if bundle["level"] == "run_once" and set(row.effects) != {"read"}:
        reasons.append("run_once does not qualify an effect beyond read in production")
    if not required:
        reasons.append("production names the environment components it requires")
    missing = sorted(set(required) - set(bundle["environment"]))
    if missing:
        reasons.append("the bundle does not cover the environment: " + ", ".join(missing))
    return reasons


def gate(
    row: OperationRow,
    *,
    profile: str,
    now: int,
    envelope: Mapping[str, Any] | None = None,
    trusted_keys: Mapping[str, bytes] | None = None,
    required_environment: Collection[str] = (),
) -> GateVerdict:
    """Decide whether ``row`` may run under ``profile`` and on what basis.

    Parameters
    ----------
    row : OperationRow
        The catalogue row to run.
    profile : str
        ``personal`` or ``production``.
    now : int
        Current time in whole UNIX seconds.
    envelope : Mapping[str, Any] or None, optional
        Signed qualification bundle; required in production.
    trusted_keys : Mapping[str, bytes] or None, optional
        Qualifier public keys trusted by the production profile.
    required_environment : Collection[str], optional
        Environment components the production profile names.

    Returns
    -------
    GateVerdict
        ``runnable`` with the basis it rests on, or every reason it does not.

    Raises
    ------
    ValueError
        If the profile is unknown.
    """
    if profile not in PROFILES:
        raise ValueError(f"a profile is one of {sorted(PROFILES)}")
    reasons = [] if row.runnable else ["the row is not qualified for its present binding"]
    if profile == "personal":
        return GateVerdict(not reasons, "operator_confirmation (personal profile)", tuple(reasons))
    if envelope is None:
        reasons.append("production needs a signed qualification bundle")
        return GateVerdict(False, "signed_qualification_bundle", tuple(reasons))
    try:
        bundle = verify_envelope(envelope, trusted_keys or {})
    except QualificationBundleError as exc:
        reasons.append(str(exc))
        return GateVerdict(False, "signed_qualification_bundle", tuple(reasons))
    reasons.extend(_production_reasons(row, bundle, now=now, required=required_environment))
    basis = f"signed_qualification_bundle by {bundle['qualifier']}"
    return GateVerdict(not reasons, basis, tuple(reasons))
