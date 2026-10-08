# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Prepare the hub command's federation peer routes."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.federation import FederationBundle, bundle_can_authorise
from synapse_channel.core.federation_store import FederationStoreError, bundle_from_store
from synapse_channel.core.federation_wire import FederationWireError, decode_federation_offer
from synapse_channel.core.identity_keys import IdentityKeyError
from synapse_channel.core.message_forward_transport import parse_message_peers
from synapse_channel.core.multihub_claim_transport import parse_claim_peers
from synapse_channel.core.multihub_watch import MultiHubWatch, parse_watch_peers, parse_watch_pins
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import parse_relay_peers
from synapse_channel.core.peer_identity import load_peer_registration_signer


def _parse_namespace_owners(values: list[str]) -> dict[str, str]:
    """Parse repeatable ``NS=HUB_ID`` CLI values into an ownership map."""
    owners: dict[str, str] = {}
    for value in values:
        namespace, sep, hub_id = value.partition("=")
        namespace, hub_id = (namespace.strip(), hub_id.strip())
        if not sep or not namespace or (not hub_id):
            raise ValueError(f"--namespace-owner must use NS=HUB_ID, got {value!r}")
        if namespace in owners:
            raise ValueError(f"--namespace-owner names namespace {namespace!r} twice")
        owners[namespace] = hub_id
    return owners


def _parse_named_pins(values: list[str], *, flag: str) -> dict[str, str]:
    """Parse repeatable ``NAME=sha256:HEX`` values without guessing a peer."""
    pins: dict[str, str] = {}
    for value in values:
        name, separator, pin = value.partition("=")
        name, pin = (name.strip(), pin.strip())
        if not separator or not name or (not pin):
            raise ValueError(f"{flag} must use NAME=sha256:<hex>, got {value!r}")
        if not pin.lower().startswith("sha256:"):
            raise ValueError(f"{flag} pin must be sha256:<hex>, got {pin!r}")
        if name in pins:
            raise ValueError(f"{flag} names {name!r} twice")
        pins[name] = pin
    return pins


def require_federation_authentication(ctx: HubStartup, bundle: FederationBundle) -> None:
    """Refuse unenforceable granted scope while retaining observe-only diagnostics."""
    if not ctx.args.require_message_auth:
        if ctx.args.federation_observe_only:
            print(
                (
                    "synapse hub: federation store loaded observe-only; every "
                    "cross-domain frame is refused deny-closed."
                ),
                file=sys.stderr,
            )
        elif bundle_can_authorise(bundle, now=time.time()):
            print(
                (
                    "synapse hub: --federation-store grants cross-domain scope but "
                    "--require-message-auth is not set; no signing key is ever verified, "
                    "so no cross-domain frame can be honoured and the granted scope is "
                    "unenforceable. Start with --require-message-auth to enforce "
                    "federation, or declare --federation-observe-only to load the store "
                    "for diagnostics and deny-closed refusal only."
                ),
                file=sys.stderr,
            )
            raise HubStartupRefused() from None
        else:
            print(
                (
                    "synapse hub: WARNING --federation-store without "
                    "--require-message-auth authorises no cross-domain frame; a peered "
                    "domain's frame can only be honoured when per-message authentication "
                    "binds its signing key. Pair --federation-store with "
                    "--require-message-auth to enforce federation."
                ),
                file=sys.stderr,
            )


def load_federation_for_hub(ctx: HubStartup) -> None:
    """Load federation material and preserve enforced versus observe-only posture."""
    ctx.config = replace(
        ctx.config, federation=replace(ctx.config.federation, federation_bundle=None)
    )
    if ctx.args.federation_observe_only and (not ctx.args.federation_store):
        print(
            (
                "synapse hub: --federation-observe-only requires --federation-store; "
                "there is no peering to observe."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if ctx.args.federation_observe_only and ctx.args.require_message_auth:
        print(
            (
                "synapse hub: --federation-observe-only contradicts "
                "--require-message-auth; per-message authentication would enforce the"
                " peerings this flag declares unenforced. Drop one of the two."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if ctx.args.federation_store:
        try:
            bundle = bundle_from_store(ctx.args.federation_store)
            ctx.config = replace(
                ctx.config,
                federation=replace(
                    ctx.config.federation,
                    federation_bundle=bundle,
                ),
            )
        except FederationStoreError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None
        require_federation_authentication(ctx, bundle)


def validate_federation_offer(ctx: HubStartup) -> None:
    """Validate the operator-supplied federation offer before serving it."""
    if ctx.args.federation_offer:
        try:
            offered = json.loads(Path(ctx.args.federation_offer).read_text(encoding="utf-8"))
            decode_federation_offer(offered)
        except (OSError, json.JSONDecodeError, FederationWireError) as exc:
            print(f"synapse hub: cannot serve --federation-offer: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None


def validate_namespace_routes(ctx: HubStartup) -> None:
    """Require explicit namespace ownership for watched or forwarded claims."""
    if ctx.args.namespace_owner and (not ctx.args.hub_id):
        print(
            (
                "synapse hub: --namespace-owner requires --hub-id; the ownership map "
                "compares each namespace's owner against this hub's own stable id."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if ctx.args.multihub_watch and (not ctx.args.namespace_owner):
        print(
            (
                "synapse hub: --multihub-watch requires --namespace-owner; the watch "
                "feeds partition detection, and without an ownership map there is "
                "nothing to detect against."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if ctx.args.claim_peer and (not ctx.args.namespace_owner):
        print(
            (
                "synapse hub: --claim-peer requires --namespace-owner; a forwarding "
                "route is keyed by the owning hub id, and without an ownership map no"
                " claim is remote-owned to forward."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None


def validate_peer_route_pins(ctx: HubStartup) -> None:
    """Refuse peer pins without routes and relays without namespace ownership."""
    ctx.transport.relay_peer_values = getattr(ctx.args, "relay_peer", [])
    if getattr(ctx.args, "relay_peer_pin", []) and (not ctx.transport.relay_peer_values):
        print(
            (
                "synapse hub: --relay-peer-pin requires --relay-peer; a pin without a"
                " route cannot secure any owner connection."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if ctx.transport.relay_peer_values and (not ctx.args.namespace_owner):
        print(
            (
                "synapse hub: --relay-peer requires --namespace-owner; relay routes "
                "are keyed by the authoritative owning hub id."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    ctx.transport.message_peer_values = getattr(ctx.args, "message_peer", [])
    if getattr(ctx.args, "message_peer_pin", []) and (not ctx.transport.message_peer_values):
        print(
            (
                "synapse hub: --message-peer-pin requires --message-peer; a pin "
                "without a route cannot secure any peer connection."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if getattr(ctx.args, "claim_peer_pin", []) and (not ctx.args.claim_peer):
        print(
            (
                "synapse hub: --claim-peer-pin requires --claim-peer; a pin without a"
                " route cannot secure any owner connection."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None


def load_peer_credentials(ctx: HubStartup) -> None:
    """Validate paired client certificates and load the peer registration signer."""
    ctx.transport.client_certfile = getattr(ctx.args, "multihub_client_certfile", None)
    ctx.transport.client_keyfile = getattr(ctx.args, "multihub_client_keyfile", None)
    if (ctx.transport.client_certfile is None) != (ctx.transport.client_keyfile is None):
        print(
            (
                "synapse hub: --multihub-client-certfile and "
                "--multihub-client-keyfile must be configured together."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    peer_key = getattr(ctx.args, "peer_identity_key", None)
    peer_key_id = getattr(ctx.args, "peer_identity_key_id", None)
    ctx.transport.peer_signer = None
    if (peer_key is None) != (peer_key_id is None):
        print(
            (
                "synapse hub: --peer-identity-key and --peer-identity-key-id must be "
                "configured together."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if peer_key is not None and peer_key_id is not None:
        try:
            ctx.transport.peer_signer = load_peer_registration_signer(peer_key, peer_key_id)
        except (IdentityKeyError, ValueError) as exc:
            print(f"synapse hub: --peer-identity-key: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None


def load_namespace_and_watch(ctx: HubStartup) -> None:
    """Build namespace ownership and its optional observation feed."""
    ctx.config = replace(
        ctx.config, multihub=replace(ctx.config.multihub, namespace_ownership=None)
    )
    ctx.transport.watch = None
    ctx.config = replace(ctx.config, multihub=replace(ctx.config.multihub, claim_peers=None))
    ctx.config = replace(ctx.config, multihub=replace(ctx.config.multihub, relay_peers=None))
    ctx.config = replace(ctx.config, multihub=replace(ctx.config.multihub, message_peers=None))
    try:
        if ctx.args.namespace_owner:
            ctx.config = replace(
                ctx.config,
                multihub=replace(
                    ctx.config.multihub,
                    namespace_ownership=NamespaceOwnership(
                        owners=_parse_namespace_owners(ctx.args.namespace_owner),
                        local_hub_id=ctx.args.hub_id,
                    ),
                ),
            )
        if ctx.args.multihub_watch:
            watch_peers = parse_watch_peers(ctx.args.multihub_watch)
            ctx.transport.watch = MultiHubWatch(
                watch_peers,
                local_id=ctx.args.hub_id,
                token=ctx.args.multihub_watch_token,
                interval=ctx.args.multihub_watch_interval,
                pins=parse_watch_pins(ctx.args.multihub_watch_pin, watch_peers),
                client_certificate_file=ctx.transport.client_certfile,
                client_key_file=ctx.transport.client_keyfile,
                namespace_ownership=ctx.config.multihub.namespace_ownership,
                journal=ctx.config.journal,
                signer=ctx.transport.peer_signer,
            )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    if ctx.transport.watch is not None:
        ctx.config = replace(
            ctx.config,
            multihub=replace(
                ctx.config.multihub,
                observed_asserting_hubs=ctx.transport.watch.observed_asserting_hubs,
            ),
        )


def load_claim_routes(ctx: HubStartup) -> None:
    """Build authenticated routes to authoritative claim owners."""
    try:
        if ctx.args.claim_peer:
            ctx.config = replace(
                ctx.config,
                multihub=replace(
                    ctx.config.multihub,
                    claim_peers=parse_claim_peers(
                        ctx.args.claim_peer,
                        token=ctx.args.claim_peer_token,
                        pins=_parse_named_pins(
                            getattr(ctx.args, "claim_peer_pin", []), flag="--claim-peer-pin"
                        ),
                        client_certificate_file=ctx.transport.client_certfile,
                        client_key_file=ctx.transport.client_keyfile,
                        signer=ctx.transport.peer_signer,
                    ),
                ),
            )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def load_message_routes(ctx: HubStartup) -> None:
    """Build authenticated routes for cross-hub message forwarding."""
    try:
        if ctx.transport.message_peer_values:
            ctx.config = replace(
                ctx.config,
                multihub=replace(
                    ctx.config.multihub,
                    message_peers=parse_message_peers(
                        ctx.transport.message_peer_values,
                        token=getattr(ctx.args, "message_peer_token", None),
                        pins=_parse_named_pins(
                            getattr(ctx.args, "message_peer_pin", []), flag="--message-peer-pin"
                        ),
                        client_certificate_file=ctx.transport.client_certfile,
                        client_key_file=ctx.transport.client_keyfile,
                        signer=ctx.transport.peer_signer,
                    ),
                ),
            )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def load_relay_routes(ctx: HubStartup) -> None:
    """Build authenticated routes for governed operator relays."""
    try:
        if ctx.transport.relay_peer_values:
            ctx.config = replace(
                ctx.config,
                multihub=replace(
                    ctx.config.multihub,
                    relay_peers=parse_relay_peers(
                        ctx.transport.relay_peer_values,
                        token=getattr(ctx.args, "relay_peer_token", None),
                        pins=_parse_named_pins(
                            getattr(ctx.args, "relay_peer_pin", []), flag="--relay-peer-pin"
                        ),
                        client_certificate_file=ctx.transport.client_certfile,
                        client_key_file=ctx.transport.client_keyfile,
                        signer=ctx.transport.peer_signer,
                    ),
                ),
            )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def prepare_hub_peers(ctx: HubStartup) -> None:
    """Validate and load federation, namespace observation and peer routes."""
    load_federation_for_hub(ctx)
    validate_federation_offer(ctx)
    validate_namespace_routes(ctx)
    validate_peer_route_pins(ctx)
    load_peer_credentials(ctx)
    load_namespace_and_watch(ctx)
    load_claim_routes(ctx)
    load_message_routes(ctx)
    load_relay_routes(ctx)
