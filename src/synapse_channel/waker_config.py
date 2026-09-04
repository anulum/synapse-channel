# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-only active-waker configuration
"""Validate and atomically persist active terminal-waker configuration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import cast

from synapse_channel.agent_tmux import AgentTmuxConfig
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.terminal_text import terminal_text

CONFIG_SCHEMA_VERSION = 1
"""Schema version of the owner-only waker configuration document."""

MAX_CONFIG_BYTES = 131_072
"""Maximum accepted waker configuration size."""

MAX_PANE_PROBE_INTERVAL = 60.0
"""Longest probe interval that remains safely below the 90-second watchdog."""

DESIRED_ARMED = "armed"
"""Desired state that permits automatic service reconciliation."""

DESIRED_INHIBITED = "inhibited"
"""Desired state that forbids automatic service reconciliation."""

_DESIRED_STATES = frozenset({DESIRED_ARMED, DESIRED_INHIBITED})


class WakerConfigError(ValueError):
    """Raised when a waker configuration is absent, unsafe, or malformed."""


@dataclass(frozen=True, slots=True)
class WakerConfig:
    """Durable configuration and desired state of one active waker.

    Parameters
    ----------
    identity : str
        Exact logical seat woken by the bridge.
    session : str
        tmux session containing the provider pane.
    cwd : str
        Absolute provider working directory.
    agent_command : tuple of str
        Provider command tokens used to create or verify the tmux session.
    tmux_bin, synapse_bin : str
        Executables used at the process boundary.
    uri : str
        Hub WebSocket URI.
    token_file : str or None
        Owner-only token file path. Secret values are never stored here.
    registry_dir : str
        Persistent owner-only bridge state shared by the service and CLI status.
    submit_delay, pane_probe_interval : float
        Safe-submit delay and maximum seconds between provider probes.
    desired_state : str
        ``armed`` or ``inhibited``.
    generation : int
        Monotonic configuration generation for compare-and-swap controls.
    inhibit_reason : str or None
        Sanitised reason for the current inhibit.
    updated_at : float
        Unix timestamp of the last write.
    schema_version : int
        Exact configuration schema version.
    """

    identity: str
    session: str
    cwd: str
    agent_command: tuple[str, ...]
    registry_dir: str
    tmux_bin: str = "tmux"
    synapse_bin: str = "synapse"
    uri: str = DEFAULT_HUB_URI
    token_file: str | None = None
    submit_delay: float = 0.35
    pane_probe_interval: float = 30.0
    desired_state: str = DESIRED_ARMED
    generation: int = 1
    inhibit_reason: str | None = None
    updated_at: float = 0.0
    schema_version: int = CONFIG_SCHEMA_VERSION

    def to_document(self) -> dict[str, object]:
        """Return the canonical JSON-compatible configuration document."""
        document = asdict(self)
        document["agent_command"] = list(self.agent_command)
        return document

    def agent_tmux_config(self) -> AgentTmuxConfig:
        """Build the existing fail-closed terminal transport configuration."""
        return AgentTmuxConfig(
            identity=self.identity,
            session=self.session,
            cwd=Path(self.cwd),
            agent_command=self.agent_command,
            tmux_bin=self.tmux_bin,
            synapse_bin=self.synapse_bin,
            uri=self.uri,
            token_file=Path(self.token_file) if self.token_file is not None else None,
            registry_dir=Path(self.registry_dir),
            submit_delay=self.submit_delay,
            pane_probe_interval=self.pane_probe_interval,
        )


def clean_waker_text(value: object, *, field: str) -> str:
    """Return one terminal-safe non-empty string or reject it."""
    if not isinstance(value, str):
        raise WakerConfigError(f"{field} must be a string")
    if terminal_text(value) != value or "\x00" in value:
        raise WakerConfigError(f"{field} contains control or bidi characters")
    if not value.strip():
        raise WakerConfigError(f"{field} must not be empty")
    return value


def _finite(value: object, *, field: str, allow_zero: bool) -> float:
    """Return a valid timing value without accepting booleans or infinities."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise WakerConfigError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or (number == 0.0 and not allow_zero):
        relation = "non-negative" if allow_zero else "positive"
        raise WakerConfigError(f"{field} must be finite and {relation}")
    return number


def validate_waker_config(config: WakerConfig) -> WakerConfig:
    """Return ``config`` after validating every persisted execution field.

    Raises
    ------
    WakerConfigError
        If a field could select an ambiguous seat, path, process, or state.
    """
    if config.schema_version != CONFIG_SCHEMA_VERSION:
        raise WakerConfigError(
            f"unsupported waker configuration schema {config.schema_version}; "
            f"expected {CONFIG_SCHEMA_VERSION}"
        )
    clean_waker_text(config.identity, field="identity")
    clean_waker_text(config.session, field="session")
    if not Path(clean_waker_text(config.cwd, field="cwd")).is_absolute():
        raise WakerConfigError("cwd must be an absolute path")
    if not config.agent_command:
        raise WakerConfigError("agent_command must contain at least one token")
    for token in config.agent_command:
        clean_waker_text(token, field="agent_command token")
    clean_waker_text(config.tmux_bin, field="tmux_bin")
    clean_waker_text(config.synapse_bin, field="synapse_bin")
    clean_waker_text(config.uri, field="uri")
    if (
        config.token_file is not None
        and not Path(clean_waker_text(config.token_file, field="token_file")).is_absolute()
    ):
        raise WakerConfigError("token_file must be an absolute path")
    if not Path(clean_waker_text(config.registry_dir, field="registry_dir")).is_absolute():
        raise WakerConfigError("registry_dir must be an absolute path")
    _finite(config.submit_delay, field="submit_delay", allow_zero=True)
    probe_interval = _finite(
        config.pane_probe_interval, field="pane_probe_interval", allow_zero=False
    )
    if probe_interval > MAX_PANE_PROBE_INTERVAL:
        raise WakerConfigError(
            f"pane_probe_interval must not exceed {MAX_PANE_PROBE_INTERVAL:g} seconds"
        )
    if config.desired_state not in _DESIRED_STATES:
        raise WakerConfigError("desired_state must be armed or inhibited")
    if not isinstance(config.generation, int) or isinstance(config.generation, bool):
        raise WakerConfigError("generation must be an integer")
    if config.generation < 1:
        raise WakerConfigError("generation must be positive")
    if config.inhibit_reason is not None:
        clean_waker_text(config.inhibit_reason, field="inhibit_reason")
    _finite(config.updated_at, field="updated_at", allow_zero=True)
    return config


def _config_from_document(document: object) -> WakerConfig:
    """Parse an exact-schema JSON value into a validated configuration."""
    if not isinstance(document, dict):
        raise WakerConfigError("waker configuration must be a JSON object")
    expected = {field.name for field in fields(WakerConfig)}
    actual = set(document)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "none"
        unknown = ", ".join(sorted(actual - expected)) or "none"
        raise WakerConfigError(
            f"waker configuration keys mismatch; missing={missing}; unknown={unknown}"
        )
    command = document["agent_command"]
    if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
        raise WakerConfigError("agent_command must be an array of strings")
    values = cast(dict[str, object], document)
    config = WakerConfig(
        identity=cast(str, values["identity"]),
        session=cast(str, values["session"]),
        cwd=cast(str, values["cwd"]),
        agent_command=tuple(command),
        tmux_bin=cast(str, values["tmux_bin"]),
        synapse_bin=cast(str, values["synapse_bin"]),
        uri=cast(str, values["uri"]),
        token_file=cast(str | None, values["token_file"]),
        registry_dir=cast(str, values["registry_dir"]),
        submit_delay=cast(float, values["submit_delay"]),
        pane_probe_interval=cast(float, values["pane_probe_interval"]),
        desired_state=cast(str, values["desired_state"]),
        generation=cast(int, values["generation"]),
        inhibit_reason=cast(str | None, values["inhibit_reason"]),
        updated_at=cast(float, values["updated_at"]),
        schema_version=cast(int, values["schema_version"]),
    )
    return validate_waker_config(config)


def waker_config_dir(*, home: Path | None = None) -> Path:
    """Return the owner-only active-waker configuration directory."""
    root = Path.home() if home is None else home
    return root / ".local" / "share" / "synapse" / "wakers"


def waker_config_path(identity: str, *, home: Path | None = None) -> Path:
    """Return the collision-resistant configuration path for ``identity``."""
    clean = clean_waker_text(identity, field="identity")
    return waker_config_dir(home=home) / f"{hashlib.sha256(clean.encode()).hexdigest()}.json"


def save_waker_config(config: WakerConfig, *, home: Path | None = None) -> Path:
    """Atomically persist one validated owner-only configuration."""
    validate_waker_config(config)
    directory = ensure_private_dir(
        waker_config_dir(home=home), parents=True, purpose="waker configuration directory"
    )
    destination = waker_config_path(config.identity, home=home)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".waker-", suffix=".tmp", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config.to_document(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def load_waker_config(identity: str, *, home: Path | None = None) -> WakerConfig:
    """Load and validate one same-owner, single-link configuration file."""
    path = waker_config_path(identity, home=home)
    try:
        document = json.loads(
            read_secret_file(
                path,
                flag="waker configuration",
                require_single_link=True,
                limit=MAX_CONFIG_BYTES,
            )
        )
    except (SecretFileError, json.JSONDecodeError) as exc:
        raise WakerConfigError(str(exc)) from exc
    config = _config_from_document(document)
    if config.identity != identity:
        raise WakerConfigError("waker configuration identity does not match its requested seat")
    return config
