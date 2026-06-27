# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub TLS context construction
"""TLS helpers for native ``wss://`` hub deployment."""

from __future__ import annotations

import ssl
from pathlib import Path


class HubTLSConfigError(ValueError):
    """Raised when the hub TLS certificate configuration is incomplete or invalid."""


def build_server_ssl_context(
    *, certfile: str | Path | None, keyfile: str | Path | None
) -> ssl.SSLContext | None:
    """Build a server-side SSL context for native WSS.

    Parameters
    ----------
    certfile : str or pathlib.Path or None
        PEM certificate chain file passed to ``SSLContext.load_cert_chain``.
    keyfile : str or pathlib.Path or None
        PEM private-key file paired with ``certfile``.

    Returns
    -------
    ssl.SSLContext or None
        A TLS server context when both paths are supplied, otherwise ``None``
        when TLS is disabled.

    Raises
    ------
    HubTLSConfigError
        If only one path is supplied or the certificate chain cannot be loaded.
    """
    if certfile is None and keyfile is None:
        return None
    if certfile is None or keyfile is None:
        raise HubTLSConfigError("native WSS requires both --tls-certfile and --tls-keyfile")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    except (OSError, ssl.SSLError) as exc:
        raise HubTLSConfigError(f"could not load hub TLS certificate chain: {exc}") from exc
    return context
