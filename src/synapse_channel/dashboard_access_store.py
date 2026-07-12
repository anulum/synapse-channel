# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict owner-only dashboard access policy loader
"""Load bounded principal JSON and token files before the HTTP socket binds."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import NoReturn

from synapse_channel.dashboard_access import (
    DashboardAccessPolicy,
    DashboardCredential,
    DashboardPrincipal,
    DashboardRole,
    capabilities_for_role,
)

MAX_ACCESS_FILE_BYTES = 256 * 1024
MAX_PRINCIPALS = 64
MIN_TOKEN_BYTES, MAX_TOKEN_BYTES = 32, 4096
_PRINCIPAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RELAY_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def load_dashboard_access_policy(
    path: str | Path,
    *,
    operator_armed: bool,
) -> DashboardAccessPolicy:
    """Return one fail-closed policy from strict owner-controlled files."""
    policy_path = Path(path).expanduser()
    raw = _read_owner_file(policy_path, MAX_ACCESS_FILE_BYTES, "dashboard access file")
    try:
        payload: object = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("dashboard access file is not strict UTF-8 JSON") from None
    if not isinstance(payload, dict) or set(payload) != {"version", "principals"}:
        raise ValueError("dashboard access file needs exactly version and principals")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("dashboard access file version must be 1")
    entries = payload["principals"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PRINCIPALS:
        raise ValueError(f"dashboard access principals must contain 1..{MAX_PRINCIPALS} entries")

    credentials: list[DashboardCredential] = []
    ids: set[str] = set()
    tokens: set[bytes] = set()
    relays: set[str] = set()
    for index, entry in enumerate(entries):
        credential = _parse_principal(
            entry,
            index=index,
            policy_dir=policy_path.parent,
            operator_armed=operator_armed,
        )
        principal = credential.principal
        if principal.principal_id in ids:
            raise ValueError("dashboard access principal ids must be unique")
        if credential.token in tokens:
            raise ValueError("dashboard access bearer tokens must be unique")
        if principal.operator_name is not None and principal.operator_name in relays:
            raise ValueError("dashboard access relay identities must be unique")
        ids.add(principal.principal_id)
        tokens.add(credential.token)
        if principal.operator_name is not None:
            relays.add(principal.operator_name)
        credentials.append(credential)
    return DashboardAccessPolicy(tuple(credentials), None, operator_armed)


def _parse_principal(
    value: object,
    *,
    index: int,
    policy_dir: Path,
    operator_armed: bool,
) -> DashboardCredential:
    if not isinstance(value, dict):
        raise ValueError(f"dashboard access principal {index} must be an object")
    role_value = value.get("role")
    if role_value not in {"viewer", "operator", "admin"}:
        raise ValueError(f"dashboard access principal {index} has an unknown role")
    role: DashboardRole = role_value
    expected = {"id", "role", "token_file"}
    if role != "viewer":
        expected.add("operator_name")
    if set(value) != expected:
        raise ValueError(f"dashboard access principal {index} has unknown or missing fields")
    principal_id = value.get("id")
    token_name = value.get("token_file")
    if not isinstance(principal_id, str) or _PRINCIPAL_ID.fullmatch(principal_id) is None:
        raise ValueError(f"dashboard access principal {index} has an invalid id")
    if not isinstance(token_name, str) or not token_name or "\x00" in token_name:
        raise ValueError(f"dashboard access principal {index} has an invalid token file")
    operator_name: str | None = None
    if role != "viewer":
        if not operator_armed:
            raise ValueError("dashboard operator/admin principals require --operator")
        candidate = value.get("operator_name")
        if not isinstance(candidate, str) or _RELAY_IDENTITY.fullmatch(candidate) is None:
            raise ValueError(f"dashboard access principal {index} has an invalid relay identity")
        operator_name = candidate
    token_path = Path(token_name).expanduser()
    if not token_path.is_absolute():
        token_path = policy_dir / token_path
    token = _read_token(token_path)
    principal = DashboardPrincipal(
        principal_id,
        role,
        capabilities_for_role(role, operator_armed=operator_armed),
        operator_name,
    )
    return DashboardCredential(principal, token)


def _read_token(path: Path) -> bytes:
    raw = _read_owner_file(path, MAX_TOKEN_BYTES + 2, "dashboard token file")
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if not MIN_TOKEN_BYTES <= len(raw) <= MAX_TOKEN_BYTES:
        raise ValueError("dashboard bearer byte length is outside 32..4096")
    try:
        token = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("dashboard bearer must be valid UTF-8") from None
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token):
        raise ValueError("dashboard bearer cannot contain whitespace or control characters")
    return raw


def _read_owner_file(path: Path, limit: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError:
        raise ValueError(f"{label} is unavailable") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if os.name == "posix" and (
        before.st_uid != os.geteuid()
        or not before.st_mode & stat.S_IRUSR
        or before.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise ValueError(f"{label} must be owner-readable and private")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError(f"{label} is unavailable") from None
    try:
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError(f"{label} changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise ValueError(f"{label} exceeds its size limit")
    return data


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON number {value}")
