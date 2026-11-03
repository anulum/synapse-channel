# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated prerequisite identities and irreversible credential drop
"""Create only the four named system roles and drop every writer capability."""

from __future__ import annotations

import asyncio
import ctypes
import errno
import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from . import require_native_linux

NAMES = ("synapse-authority", "synapse-operator", "synapse-writer", "synapse-client")
ROOT_COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


@dataclass(frozen=True)
class Role:
    """Actual system account readback, never a caller-selected numeric principal."""

    name: str
    uid: int
    gid: int


def _system_identity_ranges() -> tuple[range, range]:
    """Read the installed shadow account tools' configured system ID ranges."""
    names = {"UID_MIN", "GID_MIN", "SYS_UID_MIN", "SYS_UID_MAX", "SYS_GID_MIN", "SYS_GID_MAX"}
    values: dict[str, int] = {}
    for line in Path("/etc/login.defs").read_text(encoding="ascii").splitlines():
        fields = line.partition("#")[0].split()
        if not fields or fields[0] not in names:
            continue
        if len(fields) != 2 or fields[0] in values or not fields[1].isdecimal():
            raise ValueError("prerequisite system identity range configuration differs")
        values[fields[0]] = int(fields[1])
    ranges = []
    for kind in ("UID", "GID"):
        regular_min = values.get(kind + "_MIN", 1000)
        lower = values.get("SYS_" + kind + "_MIN", 101)
        upper = values.get("SYS_" + kind + "_MAX", regular_min - 1)
        if not 0 < lower <= upper < regular_min < 2**32 - 1:
            raise ValueError("prerequisite system identity range configuration differs")
        ranges.append(range(lower, upper + 1))
    return ranges[0], ranges[1]


def _local_account_rows(path: Path, columns: int) -> dict[str, list[str]]:
    """Read bounded root-owned local records without loading an NSS backend."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o022:
            raise ValueError("prerequisite local account file is not root controlled")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(4 * 1024 * 1024 + 1)
    finally:
        os.close(descriptor)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("prerequisite local account file exceeds byte bound")
    records: dict[str, list[str]] = {}
    for line in raw.decode("utf-8").split("\n"):
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) != columns or not fields[0] or fields[0] in records:
            raise ValueError("prerequisite local account record is malformed or repeated")
        records[fields[0]] = fields
    return records


def _local_identity(value: str) -> int:
    """Require the decimal numeric form used by the local account databases."""
    if not value.isascii() or not value.isdecimal():
        raise ValueError("prerequisite local account identity is not decimal")
    identity = int(value)
    if not 0 <= identity < 2**32 - 1:
        raise ValueError("prerequisite local account identity exceeds kernel bounds")
    return identity


def _read_role(name: str) -> Role | None:
    """Validate only the four fixed local accounts, with no NSS lookup or import."""
    if name not in NAMES:
        raise ValueError("invalid prerequisite target role")
    try:
        accounts = _local_account_rows(Path("/etc/passwd"), 7)
    except FileNotFoundError:
        return None
    if name not in accounts:
        return None
    account = accounts[name]
    uid, gid = _local_identity(account[2]), _local_identity(account[3])
    uid_range, gid_range = _system_identity_ranges()
    if uid not in uid_range or gid not in gid_range:
        raise ValueError("existing prerequisite role is outside the system identity range")
    _require_numeric_identity(uid, gid)
    groups = _local_account_rows(Path("/etc/group"), 4)
    if (
        account[1] != "x"
        or account[6] != "/usr/sbin/nologin"
        or account[5] != "/nonexistent"
        or os.path.lexists(account[5])
    ):
        raise ValueError("existing prerequisite role differs from the reviewed disabled account")
    _require_private_role_group(name, uid, gid, accounts, groups)
    shadow = _local_account_rows(Path("/etc/shadow"), 9)
    if name not in shadow:
        raise ValueError("existing prerequisite role has no password lock record")
    if not shadow[name][1].startswith(("!", "*")):
        raise ValueError("existing prerequisite role password is not locked")
    return Role(name, uid, gid)


def _require_private_role_group(
    name: str, uid: int, gid: int, accounts: dict[str, list[str]], groups: dict[str, list[str]]
) -> None:
    """Require exclusive local numeric ownership and no supplementary groups."""
    matching = [entry for entry in groups.values() if _local_identity(entry[2]) == gid]
    owners = [
        entry[0]
        for entry in accounts.values()
        if _local_identity(entry[2]) == uid or _local_identity(entry[3]) == gid
    ]
    if len(matching) != 1 or matching[0][0] != name or matching[0][3] or owners != [name]:
        raise ValueError("existing prerequisite role differs from the reviewed disabled account")
    if any(name in entry[3].split(",") for entry in groups.values()):
        raise ValueError("existing prerequisite role has supplementary group membership")


def _refuse_active_roles(roles: dict[str, Role]) -> None:
    """Refuse reusing any dedicated UID that already has a live process."""
    identities = {role.uid for role in roles.values()}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            lines = (entry / "status").read_text().splitlines()
        except FileNotFoundError:
            continue
        uid = next(line.partition(":")[2].split() for line in lines if line.startswith("Uid:"))
        if any(int(value) in identities for value in uid):
            raise ValueError("a prerequisite role already has a live process")


async def ensure_roles() -> dict[str, Role]:
    """Create or verify the exact four dedicated roles after explicit operator approval.

    Returns
    -------
    dict[str, Role]
        Actual account readback with distinct UID/GID values and no live role processes.

    Raises
    ------
    PermissionError
        Unless the operator process is actually root.
    ValueError
        On a reused, running, shared or incorrectly configured account.
    RuntimeError
        If bounded system account creation fails or readback is unavailable.

    Notes
    -----
    Creates system users/groups only; never changes an existing account or deletes
    one on failure. These disabled roles persist as explicit installation prerequisites.
    """
    if os.geteuid() != 0:
        raise PermissionError("prerequisite accounts require the approved root operator")
    roles = {name: role for name in NAMES if (role := _read_role(name)) is not None}
    _refuse_active_roles(roles)
    for name in NAMES:
        if name in roles:
            continue
        process = await asyncio.create_subprocess_exec(
            "/usr/sbin/useradd",
            "--system",
            "--user-group",
            "--no-create-home",
            "--home-dir",
            "/nonexistent",
            "--shell",
            "/usr/sbin/nologin",
            "--comment",
            "Synapse protected runtime role",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=ROOT_COMMAND_ENV,
        )
        try:
            _stdout, _stderr = await asyncio.wait_for(process.communicate(), 20)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0 or (role := _read_role(name)) is None:
            raise RuntimeError("prerequisite system account creation failed")
        roles[name] = role
    if (
        len({role.uid for role in roles.values()}) != 4
        or len({role.gid for role in roles.values()}) != 4
    ):
        raise ValueError("prerequisite roles require four distinct UID/GID pairs")
    _refuse_active_roles(roles)
    return roles


class _CapabilityHeader(ctypes.Structure):
    """Linux version-three cap_user_header_t."""

    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapabilityData(ctypes.Structure):
    """One half of the Linux version-three 64-bit capability sets."""

    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def _prctl(libc: ctypes.CDLL, option: int, first: int, second: int = 0) -> None:
    """Require an actual successful privilege operation without fallback."""
    ctypes.set_errno(0)
    if libc.prctl(option, first, second, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prerequisite privilege transition refused")


def _require_numeric_identity(uid: int, gid: int) -> None:
    """Refuse shared, sentinel or unrepresentable retained kernel identities."""
    for value in (uid, gid):
        if type(value) is not int or value in {0, 1000} or not 0 < value < 2**32 - 1:
            raise ValueError("invalid prerequisite numeric identity")


def resolve_role_identity(name: str) -> Role:
    """Resolve one fixed dedicated role from root-controlled local account files.

    Parameters
    ----------
    name:
        One of the four installation role names; never a caller-selected UID.

    Returns
    -------
    Role
        Validated local system identity with locked password, private group,
        disabled login and no supplementary memberships.

    Raises
    ------
    ValueError
        On an unsupported, missing or unsuitable local role.
    OSError
        On unsupported platforms or unreadable trusted account files.

    Notes
    -----
    This read grants no privilege. It neither imports an NSS backend nor creates
    an account. The host child retains this identity through its credential drop.
    """
    require_native_linux(sys.platform, operation="prerequisite account validation")
    if type(name) is not str or name not in NAMES:
        raise ValueError("invalid prerequisite target role")
    actual = _read_role(name)
    if actual is None:
        raise ValueError("prerequisite credential target differs from actual system account")
    return actual


def validate_role_identity(role: Role) -> Role:
    """Validate and retain one exact named role in the trusted parent.

    Parameters
    ----------
    role:
        Expected dedicated name and numeric identity from operator enrollment.

    Returns
    -------
    Role
        Immutable actual account readback with matching UID, private primary
        group, locked password, disabled login, nonexistent home, no supplementary
        memberships and a UID/GID within the configured system account ranges.

    Raises
    ------
    OSError
        Unless running on the supported native Linux account backend.
    ValueError
        On an unsupported, shared, missing or changed account identity.

    Notes
    -----
    Perform this read in the trusted parent before constructing the captured
    namespace. The parent must retain the resulting numeric identity through
    its private launch configuration; this function creates no account and
    grants no credential transition or runtime acceptance. The qualification
    backend reads only bounded root-controlled local passwd, group and shadow
    files. It loads no NSS backend; an unreadable password lock record refuses
    enrollment without disclosing its value.
    """
    require_native_linux(sys.platform, operation="prerequisite account validation")
    if type(role) is not Role or role.name not in NAMES:
        raise ValueError("invalid prerequisite target role")
    _require_numeric_identity(role.uid, role.gid)
    actual = resolve_role_identity(role.name)
    if actual != role:
        raise ValueError("prerequisite credential target differs from actual system account")
    return actual


def drop_enrolled_identity(uid: int, gid: int) -> None:
    """Enter a parent-enrolled numeric identity without an account lookup.

    Parameters
    ----------
    uid, gid:
        Dedicated numeric identity independently validated and retained by the
        trusted parent before namespace construction. Never use client-selected
        values or derive enrollment from this child's own declaration.

    Raises
    ------
    PermissionError
        Unless all real/effective/saved UIDs are initially root.
    ValueError
        On shared, sentinel or invalid identities, multiple threads or changed
        final numeric credentials.
    OSError
        On unsupported platforms or any refused native privilege transition.

    Notes
    -----
    Only a disposable single-threaded child may call this. Account databases
    and NSS configuration are deliberately unnecessary inside its captured
    filesystem. Clear supplementary, ambient, bounding and effective capability
    sets; verify actual numeric credentials after the drop. Partial failures
    abort the child, whose parent retains complete stop/reap custody.
    """
    require_native_linux(sys.platform, operation="prerequisite credential transition")
    if os.getresuid() != (0, 0, 0):
        raise PermissionError("prerequisite credential drop requires the root child")
    _require_numeric_identity(uid, gid)
    if len(tuple(Path("/proc/self/task").iterdir())) != 1:
        raise ValueError("prerequisite credential drop requires a single thread")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_ulong] * 5
    libc.prctl.restype = ctypes.c_int
    _prctl(libc, 38, 1)
    _prctl(libc, 47, 4)
    for capability in range(64):
        ctypes.set_errno(0)
        if libc.prctl(24, capability, 0, 0, 0) != 0 and ctypes.get_errno() != errno.EINVAL:
            raise OSError(ctypes.get_errno(), "prerequisite capability bound refused")
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    header = _CapabilityHeader(0x20080522, 0)
    data = (_CapabilityData * 2)()
    libc.capset.argtypes = [ctypes.POINTER(_CapabilityHeader), ctypes.POINTER(_CapabilityData)]
    libc.capset.restype = ctypes.c_int
    ctypes.set_errno(0)
    if libc.capset(ctypes.byref(header), data) != 0:
        raise OSError(ctypes.get_errno(), "prerequisite empty capability set refused")
    if os.getresuid() != (uid,) * 3 or os.getresgid() != (gid,) * 3 or os.getgroups():
        raise ValueError("prerequisite final credential identity differs")


def drop_privileges(role: Role) -> None:
    """Validate a host account and enter its dedicated numeric identity.

    Parameters
    ----------
    role:
        Independently read actual dedicated account, never a numeric request declaration.

    Raises
    ------
    PermissionError
        Unless all real/effective/saved UIDs are initially root.
    ValueError
        On an invalid root/shared role or failed final principal readback.
    OSError
        On any refused bounding, ambient, group, UID/GID or capability transition.

    Notes
    -----
    This host-side convenience requires the trusted account databases.
    Captured namespace children use ``drop_enrolled_identity`` after their
    trusted parent validates and retains the exact role before launch.
    """
    if os.getresuid() != (0, 0, 0):
        raise PermissionError("prerequisite credential drop requires the root child")
    retained = validate_role_identity(role)
    drop_enrolled_identity(retained.uid, retained.gid)


def compare_probe_writer_status(
    raw: bytes, *, expected_uid: int, expected_gid: int
) -> tuple[dict[str, str], dict[str, bool]]:
    """Compare every retained credential condition in bounded Linux status bytes.

    Parameters
    ----------
    raw:
        Status record bytes. This pure parser does not attest their provenance.
    expected_uid, expected_gid:
        Numeric comparison targets. Root and shared identities are permitted
        only for diagnostics here; the live observer requires dedicated IDs.

    Returns
    -------
    tuple[dict[str, str], dict[str, bool]]
        Parsed required fields and a separate match result for each condition.
        All conditions are evaluated even when another one already differs.

    Raises
    ------
    ValueError
        On invalid targets, an oversized record, missing or repeated fields,
        non-ASCII input or malformed numeric credential fields.

    Notes
    -----
    The production observer obtains these bytes from its retained proc
    descriptor. Calling this comparator alone admits no process or release.
    A positive filter count proves presence, not the exact filter programme.
    """
    if type(raw) is not bytes:
        raise ValueError("invalid prerequisite writer status bytes")
    for identity in (expected_uid, expected_gid):
        if type(identity) is not int or not 0 <= identity < 2**32 - 1:
            raise ValueError("invalid prerequisite observed identity")
    if len(raw) > 16384:
        raise ValueError("prerequisite writer status exceeds byte bound")
    required = {
        "Uid",
        "Gid",
        "Groups",
        "CapInh",
        "CapPrm",
        "CapEff",
        "CapBnd",
        "CapAmb",
        "NoNewPrivs",
        "Seccomp",
        "Seccomp_filters",
    }
    observed: dict[str, str] = {}
    for line in raw.decode("ascii").splitlines():
        key, separator, value = line.partition(":")
        if separator and key in required:
            if key in observed:
                raise ValueError("repeated prerequisite writer status field")
            observed[key] = value.strip()
    if set(observed) != required:
        raise ValueError("incomplete prerequisite writer status")
    uids = tuple(_local_identity(value) for value in observed["Uid"].split())
    gids = tuple(_local_identity(value) for value in observed["Gid"].split())
    filters = observed["Seccomp_filters"]
    checks = {
        "Uid": uids == (expected_uid,) * 4,
        "Gid": gids == (expected_gid,) * 4,
        "Groups": not observed["Groups"],
        **{key: observed[key] == "0000000000000000" for key in required if key.startswith("Cap")},
        "NoNewPrivs": observed["NoNewPrivs"] == "1",
        "Seccomp": observed["Seccomp"] == "2",
        "Seccomp_filters": filters.isascii() and filters.isdecimal() and int(filters) >= 1,
    }
    return observed, checks


def observe_probe_writer_credentials(
    process_id: int, *, expected_uid: int, expected_gid: int
) -> dict[str, object]:
    """Compare a live writer's kernel credentials with parent-retained identities.

    Parameters
    ----------
    process_id:
        Host PID independently selected from the retained writer cgroup.
    expected_uid, expected_gid:
        Dedicated numeric identities already enrolled by the trusted parent;
        root and the shared UID/GID1000 are refused before opening proc.

    Returns
    -------
    dict[str, object]
        Bounded proc status observations with empty capability and group sets,
        no-new-privileges enabled and an installed seccomp filter.

    Raises
    ------
    ValueError
        On invalid arguments, changed credentials or incomplete kernel evidence.
    OSError
        If the named process cannot be independently observed.

    Notes
    -----
    This read-only comparison grants no role or execution permit. The caller
    retains the PID's lifetime and cgroup/namespace identity and must keep the
    writer behind its release barrier until this observation succeeds.
    """
    require_native_linux(sys.platform, operation="prerequisite writer credential observation")
    if type(process_id) is not int or process_id <= 0:
        raise ValueError("invalid prerequisite writer process")
    _require_numeric_identity(expected_uid, expected_gid)
    directory = os.open(
        "/proc/" + str(process_id), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        info = os.fstat(directory)
        descriptor = os.open("status", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(16385)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
    observed, checks = compare_probe_writer_status(
        raw, expected_uid=expected_uid, expected_gid=expected_gid
    )
    if not all(checks.values()):
        raise ValueError("independently observed prerequisite writer credentials differ")
    uids = tuple(_local_identity(value) for value in observed["Uid"].split())
    gids = tuple(_local_identity(value) for value in observed["Gid"].split())
    capabilities = {key: value for key, value in observed.items() if key.startswith("Cap")}
    filters = observed["Seccomp_filters"]
    return {
        "pid": process_id,
        "proc_identity": [info.st_dev, info.st_ino],
        "uids": list(uids),
        "gids": list(gids),
        "groups": [],
        "capabilities": capabilities,
        "no_new_privs": 1,
        "seccomp": 2,
        "seccomp_filters": int(filters),
        "status_sha256": hashlib.sha256(raw).hexdigest(),
    }
