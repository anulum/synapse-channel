# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one build identity for version, doctor, A2A and MCP
"""Report the installed artefact, its declared profile, and unreleased features.

``synapse --version`` and ``synapse doctor`` print the full identity. The
public A2A Agent Card carries the version and the development marks only.
Commit, wheel digest and declared profile stay off that unauthenticated
card. Authenticated MCP instructions carry the full text. The declared
profile is the package default or ``SYNAPSE_QUALIFIED_PROFILE`` when that
variable names a known profile. It is not qualification evidence.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess  # nosec B404 # fixed argv only, never a shell
from collections.abc import Mapping
from pathlib import Path

from synapse_channel import __version__

PACKAGE_DEFAULT_PROFILE = "loopback"
PROFILE_ENV = "SYNAPSE_QUALIFIED_PROFILE"
PROFILE_SOURCE_ENVIRONMENT = "environment"
PROFILE_SOURCE_PACKAGE_DEFAULT = "package default"
KNOWN_PROFILES = frozenset({"loopback", "team-secure", "paranoid", "secure"})
DEVELOPMENT_MARK = "development branch, not yet released"
DISTRIBUTION = "synapse-channel"
WHEEL_NOT_RECORDED = "installed from a wheel (digest not recorded)"
WHEEL_NOT_A_WHEEL = "not a wheel"
_GIT_TIMEOUT_SECONDS = 2.0
_PROJECT_NAME = re.compile(r'^name\s*=\s*"synapse-channel"\s*$')
_RELEASE_HEADING = re.compile(r"^## \[(\d+(?:\.\d+)*)\]")


def _version_key(value: str) -> tuple[int, ...]:
    """Return the numeric prefix of each dot-separated version component."""
    parts: list[int] = []
    for piece in value.split("."):
        digits = ""
        for character in piece:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            raise ValueError(f"version is not numeric: {value}")
        parts.append(int(digits))
    return tuple(parts)


def changelog_release(text: str) -> tuple[str, bool]:
    """Return the latest dated release and whether ``[Unreleased]`` has notes.

    Parameters
    ----------
    text : str
        ``CHANGELOG.md`` text. The first ``## [x.y.z]`` heading is the
        published release. ``## [Unreleased]`` notes above it are unpublished.

    Returns
    -------
    tuple of str and bool
        The release version, and whether the unreleased section has a body.

    Raises
    ------
    ValueError
        If the changelog names no dated release.
    """
    latest = ""
    unreleased = False
    in_unreleased = False
    for line in text.splitlines():
        if line.startswith("## ["):
            if line.startswith("## [Unreleased]"):
                in_unreleased = True
                continue
            in_unreleased = False
            match = _RELEASE_HEADING.match(line)
            if match and not latest:
                latest = match.group(1)
            continue
        if in_unreleased and line.strip() and not line.startswith("#"):
            unreleased = True
    if not latest:
        raise ValueError("changelog has no dated release")
    return latest, unreleased


def _changelog_text(root: Path) -> str | None:
    """Return ``CHANGELOG.md`` when ``root`` is this source checkout."""
    if not _is_this_source_checkout(root):
        return None
    try:
        return (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return None


def published_release(root: Path | None = None) -> str | None:
    """Return the changelog's latest dated release for this source checkout.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Candidate source root. An install prefix returns ``None``.

    Returns
    -------
    str or None
        The published release, or ``None`` when the changelog cannot be read.
    """
    if root is None:
        root = Path(__file__).resolve().parents[2]
    text = _changelog_text(root)
    if text is None:
        return None
    return changelog_release(text)[0]


def _pyproject_names_synapse_channel(text: str) -> bool:
    """Return whether the ``[project]`` table names this distribution."""
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project and _PROJECT_NAME.fullmatch(stripped):
            return True
    return False


def _is_this_source_checkout(root: Path) -> bool:
    """Return whether ``root`` is the source tree that contains this module."""
    module = Path(__file__).resolve()
    package = (root / "src" / "synapse_channel").resolve()
    try:
        module.relative_to(package)
    except ValueError:
        return False
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return _pyproject_names_synapse_channel(text)


def source_commit(root: Path | None = None) -> str:
    """Return this module's source commit, or ``unknown`` outside that checkout.

    An installed copy whose prefix sits inside some other git repository is
    not that repository. Git is asked only when ``root/pyproject.toml`` names
    ``synapse-channel`` and ``root/src/synapse_channel`` contains this file.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Candidate source root. Defaults to the directory two levels above
        this module, which is the repository root in a source checkout and
        a prefix directory in a typical install.

    Returns
    -------
    str
        Full commit hex, or ``unknown``.
    """
    if root is None:
        root = Path(__file__).resolve().parents[2]
    if not _is_this_source_checkout(root):
        return "unknown"
    try:
        completed = subprocess.run(  # nosec B603 B607 # fixed git argv, never a shell
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    commit = completed.stdout.strip()
    return commit or "unknown"


def development_mark_lines(
    version: str = __version__,
    *,
    changelog: str | None = None,
    root: Path | None = None,
) -> tuple[str, ...]:
    """Return the build-level mark when this tree is newer than its release.

    The published release is the latest dated heading in ``CHANGELOG.md``.
    A package version above that release, or notes under ``[Unreleased]``,
    produces one mark. The mark does not name individual features, so a
    later change cannot leave a typed feature list stale. An install that
    is not this source checkout has no changelog to read and no mark.

    Parameters
    ----------
    version : str, optional
        Package version to compare with the changelog release.
    changelog : str or None, optional
        Changelog text. ``None`` reads this source checkout.
    root : pathlib.Path or None, optional
        Source root used when ``changelog`` is omitted.

    Returns
    -------
    tuple of str
        One mark, or empty when this build is the published release.
    """
    if changelog is None:
        if root is None:
            root = Path(__file__).resolve().parents[2]
        changelog = _changelog_text(root)
        if changelog is None:
            return ()
    released, unreleased = changelog_release(changelog)
    if _version_key(version) <= _version_key(released) and not unreleased:
        return ()
    return (
        f"this build is newer than the published release {released}; "
        f"see CHANGELOG [Unreleased]; {DEVELOPMENT_MARK}",
    )


def wheel_digest_from_direct_url(document: str) -> str | None:
    """Return only the sha256 recorded in PEP 610 ``archive_info``.

    Parameters
    ----------
    document : str
        JSON text of ``direct_url.json``.

    Returns
    -------
    str or None
        The recorded sha256, or ``None`` when the file does not record one.
        A ``file:`` URL is never opened or hashed.
    """
    try:
        payload = json.loads(document)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    archive = payload.get("archive_info")
    if not isinstance(archive, dict):
        return None
    hashes = archive.get("hashes")
    if not isinstance(hashes, dict):
        return None
    digest = hashes.get("sha256")
    if isinstance(digest, str) and digest:
        return digest
    return None


def _directory_install(document: str) -> bool:
    """Return whether PEP 610 metadata names a local directory, not an archive.

    Parameters
    ----------
    document : str
        JSON text of ``direct_url.json``.

    Returns
    -------
    bool
        ``True`` when ``dir_info`` is an object. Editable and local directory
        installs are not wheels, including when packaging also wrote ``WHEEL``.
    """
    try:
        payload = json.loads(document)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return isinstance(payload.get("dir_info"), dict)


def installed_wheel_report(distribution: str = DISTRIBUTION) -> tuple[str, str | None]:
    """Classify an install as a recorded wheel, an unrecorded wheel, or neither.

    Parameters
    ----------
    distribution : str, optional
        Distribution name. Defaults to ``synapse-channel``.

    Returns
    -------
    tuple of str and str or None
        ``("recorded", digest)`` when ``direct_url.json`` records a sha256,
        ``("digest_not_recorded", None)`` when ``WHEEL`` metadata is present
        but no digest was recorded, or ``("not_a_wheel", None)`` for an
        editable or local directory install, a source checkout, or a missing
        distribution. A directory install stays ``not_a_wheel`` even when a
        ``WHEEL`` file is also present. A ``file:`` URL is never opened.
    """
    try:
        dist = importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not_a_wheel", None
    document = dist.read_text("direct_url.json")
    if document is not None:
        digest = wheel_digest_from_direct_url(document)
        if digest is not None:
            return "recorded", digest
        if _directory_install(document):
            return "not_a_wheel", None
    if dist.read_text("WHEEL") is not None:
        return "digest_not_recorded", None
    return "not_a_wheel", None


def installed_wheel_digest(distribution: str = DISTRIBUTION) -> str | None:
    """Return the recorded wheel sha256, or ``None`` when none was recorded.

    Parameters
    ----------
    distribution : str, optional
        Distribution name. Defaults to ``synapse-channel``.

    Returns
    -------
    str or None
        The recorded digest. A wheel whose digest was not recorded and an
        install that is not a wheel both return ``None``; use
        :func:`installed_wheel_report` to tell those apart.
    """
    _status, digest = installed_wheel_report(distribution)
    return digest


def declared_profile(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return the declared profile name and where that declaration came from.

    Parameters
    ----------
    env : mapping of str to str or None, optional
        Environment to read. A known ``SYNAPSE_QUALIFIED_PROFILE`` selects
        that name and the source ``environment``. An unset or unknown value
        stays the package default, ``loopback``, with source
        ``package default``. Setting the variable is not qualification
        evidence; no receipt is bound to this value.

    Returns
    -------
    tuple of str and str
        Profile name and source.
    """
    source_env = os.environ if env is None else env
    selected = source_env.get(PROFILE_ENV, "").strip()
    if selected in KNOWN_PROFILES:
        return selected, PROFILE_SOURCE_ENVIRONMENT
    return PACKAGE_DEFAULT_PROFILE, PROFILE_SOURCE_PACKAGE_DEFAULT


def _declared_profile_fields(
    profile: str | None,
    profile_source: str | None,
) -> tuple[str, str]:
    """Return the name and source stored on one build identity.

    Parameters
    ----------
    profile, profile_source : str or None
        Both omitted reads :func:`declared_profile`. Both supplied record
        that pair. One without the other is refused.

    Returns
    -------
    tuple of str and str
        Profile name and source.

    Raises
    ------
    ValueError
        If exactly one of the name and the source is supplied, or the source
        is not ``environment`` or ``package default``.
    """
    if profile is None and profile_source is None:
        return declared_profile()
    if profile is not None and profile_source in (
        PROFILE_SOURCE_ENVIRONMENT,
        PROFILE_SOURCE_PACKAGE_DEFAULT,
    ):
        return profile, profile_source
    raise ValueError("declare both the profile name and its source, or neither")


def identity_mapping(
    *,
    version: str = __version__,
    commit: str | None = None,
    wheel: str | None = None,
    discover_wheel: bool = True,
    profile: str | None = None,
    profile_source: str | None = None,
) -> dict[str, object]:
    """Return the build identity as data.

    Parameters
    ----------
    version : str, optional
        Package version.
    commit : str or None, optional
        Source commit. ``None`` reads the checkout.
    wheel : str or None, optional
        Wheel digest used when ``discover_wheel`` is false.
    discover_wheel : bool, optional
        Read the installed distribution when true.
    profile : str or None, optional
        Declared profile name. ``None`` reads the environment together with
        ``profile_source``.
    profile_source : str or None, optional
        ``environment`` or ``package default`` when ``profile`` is set.

    Returns
    -------
    dict
        Version, commit, wheel digest or ``None``, declared profile, its
        source, and development marks.
    """
    if discover_wheel:
        wheel_status, digest = installed_wheel_report()
    elif isinstance(wheel, str) and wheel:
        wheel_status, digest = "recorded", wheel
    else:
        wheel_status, digest = "not_a_wheel", None
    declared_name, source = _declared_profile_fields(profile, profile_source)
    marks = list(development_mark_lines(version))
    return {
        "version": version,
        "source_commit": source_commit() if commit is None else commit,
        "wheel_status": wheel_status,
        "wheel_digest": digest,
        "declared_profile": declared_name,
        "profile_source": source,
        "development_marks": marks,
    }


def identity_lines(
    *,
    version: str = __version__,
    commit: str | None = None,
    wheel: str | None = None,
    discover_wheel: bool = True,
    profile: str | None = None,
    profile_source: str | None = None,
) -> tuple[str, ...]:
    """Return the human build-identity lines.

    Parameters
    ----------
    version, commit, wheel, discover_wheel, profile, profile_source
        Forwarded to :func:`identity_mapping`.

    Returns
    -------
    tuple of str
        One artefact, one declared profile, and any unreleased-feature marks.
    """
    identity = identity_mapping(
        version=version,
        commit=commit,
        wheel=wheel,
        discover_wheel=discover_wheel,
        profile=profile,
        profile_source=profile_source,
    )
    status = identity.get("wheel_status")
    digest = identity["wheel_digest"]
    if status == "digest_not_recorded":
        wheel_text = WHEEL_NOT_RECORDED
    elif isinstance(digest, str) and digest:
        wheel_text = digest
    else:
        wheel_text = WHEEL_NOT_A_WHEEL
    lines = [
        f"synapse-channel {identity['version']}",
        f"source commit: {identity['source_commit']}",
        f"wheel digest: {wheel_text}",
        f"declared profile: {identity['declared_profile']} ({identity['profile_source']})",
    ]
    marks = identity["development_marks"]
    if isinstance(marks, list):
        lines.extend(str(mark) for mark in marks)
    else:
        raise TypeError("development marks must be a list")
    return tuple(lines)


def identity_text(
    *,
    version: str = __version__,
    commit: str | None = None,
    wheel: str | None = None,
    discover_wheel: bool = True,
    profile: str | None = None,
    profile_source: str | None = None,
) -> str:
    """Return :func:`identity_lines` as one block of text."""
    return "\n".join(
        identity_lines(
            version=version,
            commit=commit,
            wheel=wheel,
            discover_wheel=discover_wheel,
            profile=profile,
            profile_source=profile_source,
        )
    )


def mcp_instructions() -> str:
    """Return the MCP server instructions that carry the same build identity."""
    return identity_text()
