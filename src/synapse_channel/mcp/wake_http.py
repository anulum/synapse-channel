# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated signed notification HTTP attempt
"""Send one signed pointer through a pinned transport without exposing credentials.

The process reads one request line from its standard input and then watches the
same pipe: when the pipe reaches its end, the process exits at once. The caller
must therefore keep the pipe open until it has read the status line. A caller
that writes the request and closes the pipe ends the attempt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
import threading
import time
from os import _exit
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synapse_channel.core.http_authority import normalise_url_origin
from synapse_channel.core.secret_files import read_secret_file
from synapse_channel.mcp.wake_pointer import MAX_WAKE_BODY_BYTES
from synapse_channel.safe_webhook_transport import build_safe_opener

MAX_WAKE_INPUT_BYTES = 65536
"""Maximum private pipe input, including endpoint paths and exact pointer body."""


class WakeEndpoint(BaseModel):
    """Hold one operator-owned webhook target and private signing-key location.

    Attributes
    ----------
    url : str
        HTTPS destination; query credentials, userinfo and fragments are refused.
    signing_key_file : str
        Absolute path to an owner-only single-link Standard Webhooks secret.
    ca_file : str or None
        Optional explicit trust anchor, without disabling hostname verification.
    allow_local : bool
        Explicit private-network and plaintext opt-in for local test receivers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    url: str = Field(min_length=1, max_length=2048, repr=False)
    signing_key_file: str = Field(min_length=1, max_length=4096, repr=False)
    ca_file: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    allow_local: bool = False

    @model_validator(mode="after")
    def validate_endpoint(self) -> WakeEndpoint:
        """Refuse ambiguous destinations and relative credential or trust paths."""
        normalise_url_origin(self.url)
        parsed = urlsplit(self.url)
        if (
            parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not self.allow_local)
            or any(ord(char) <= 32 or ord(char) == 127 for char in self.url)
            or not Path(self.signing_key_file).is_absolute()
            or (self.ca_file is not None and not Path(self.ca_file).is_absolute())
        ):
            raise ValueError("invalid wake endpoint")
        return self


class WakeHttpRequest(BaseModel):
    """Validate the bounded, credential-free envelope passed over the private pipe."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    endpoint: WakeEndpoint
    delivery_id: str = Field(pattern=r"^msg_[0-9a-f]{32}$")
    body: str = Field(min_length=1, max_length=MAX_WAKE_BODY_BYTES, repr=False)


def send_signed_pointer(request: WakeHttpRequest) -> int:
    """Read the current key and return HTTP acceptance status for exact body bytes.

    Parameters
    ----------
    request : WakeHttpRequest
        Operator-bound target and already committed immutable pointer.
        A contained caller must impose the overall wall-clock deadline.

    Returns
    -------
    int
        Actual HTTP status, or zero for local/network/signing refusal. Receiver
        content is never read, retained or returned. A 2xx is transport
        acceptance only; it does not establish processing or business effect.
    """
    try:
        body = request.body.encode("ascii")
        secret = read_secret_file(
            request.endpoint.signing_key_file,
            flag="wake-signing-key",
            require_single_link=True,
            limit=256,
        )
        if not secret.startswith("whsec_"):
            return 0
        key = base64.b64decode(secret[6:], validate=True)
        if not 16 <= len(key) <= 64:
            return 0
        timestamp = str(int(time.time()))
        signed = request.delivery_id.encode() + b"." + timestamp.encode() + b"." + body
        signature = base64.b64encode(hmac.digest(key, signed, hashlib.sha256)).decode("ascii")
        headers = {
            "Content-Type": "application/json",
            "Webhook-Id": request.delivery_id,
            "Webhook-Timestamp": timestamp,
            "Webhook-Signature": "v1," + signature,
        }
        opener = build_safe_opener(
            allow_local=request.endpoint.allow_local, ca_file=request.endpoint.ca_file
        )
        with opener.open(
            Request(request.endpoint.url, data=body, headers=headers, method="POST"), timeout=3.0
        ) as response:
            return int(response.status)
    except HTTPError as response:
        status = response.code
        response.close()
        return status
    except (OSError, ValueError, URLError):
        return 0


def _watch_owner() -> None:
    """Exit even during a blocked resolver if the owning process closes its pipe."""
    os.read(sys.stdin.fileno(), 1)
    _exit(1)


def main() -> None:
    """Run one private-pipe attempt with fixed output and a parent-loss watchdog."""
    try:
        raw = sys.stdin.buffer.readline(MAX_WAKE_INPUT_BYTES + 1)
        if len(raw) > MAX_WAKE_INPUT_BYTES or not raw.endswith(b"\n"):
            raise ValueError("invalid wake input")
        request = WakeHttpRequest.model_validate_json(raw)
        threading.Thread(target=_watch_owner, daemon=True).start()
        status = send_signed_pointer(request)
    except Exception:
        status = 0
    sys.stdout.write(str(status) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
