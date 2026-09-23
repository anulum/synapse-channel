# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — private and redacted entitlement projections
"""Project owner-local account facts without mixing incompatible quota units."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from synapse_channel.core.entitlement_forecast import forecast_window
from synapse_channel.core.entitlements import active_events, parse_quantity, parse_time

VIEW_SCHEMA_VERSION = 1
"""Version of the read-only entitlement report contract."""


def _balance(
    entries: Sequence[Mapping[str, object]], window: Mapping[str, object], as_of: datetime
) -> dict[str, object]:
    """Return a balance with source, confidence and observation age."""
    observations = sorted(
        (
            entry
            for entry in entries
            if entry["kind"] in {"balance", "usage"}
            and entry["window_id"] == window["window_id"]
            and entry["window_event_id"] == window["event_id"]
            and parse_time(entry["observed_at"], "observed_at") <= as_of
        ),
        key=lambda entry: parse_time(entry["observed_at"], "observed_at"),
    )
    balances = [entry for entry in observations if entry["kind"] == "balance"]
    if balances:
        last = balances[-1]
        latest = observations[-1]
        evidence: dict[str, object] = {
            "balance_samples": len(balances),
            "observation_age_seconds": (
                as_of - parse_time(latest["observed_at"], "observed_at")
            ).total_seconds(),
            "balance_observation_age_seconds": (
                as_of - parse_time(last["observed_at"], "observed_at")
            ).total_seconds(),
            "observation_source": latest["source"],
            "observation_confidence": latest["confidence"],
            "observation_time": latest["observed_at"],
        }
        if last["remaining"] is None:
            return {**evidence, "balance_evidence": "unknown", "remaining": None}
        remaining = parse_quantity(last["remaining"], "remaining")
        last_time = parse_time(last["observed_at"], "observed_at")
        later_usage = sum(
            (
                parse_quantity(entry["amount"], "amount")
                for entry in observations
                if entry["kind"] == "usage"
                and parse_time(entry["observed_at"], "observed_at") > last_time
            ),
            Decimal(0),
        )
        return {
            **evidence,
            "balance_evidence": "observed_plus_recorded_usage",
            "remaining": str(max(Decimal(0), remaining - later_usage)),
        }
    usage = [entry for entry in observations if entry["kind"] == "usage"]
    if usage:
        spent = sum((parse_quantity(entry["amount"], "amount") for entry in usage), Decimal(0))
        remaining = max(Decimal(0), parse_quantity(window["grant"], "grant") - spent)
        latest = usage[-1]
        return {
            "balance_evidence": "incomplete_usage_estimate",
            "remaining": str(remaining),
            "balance_samples": 0,
            "observation_age_seconds": (
                as_of - parse_time(latest["observed_at"], "observed_at")
            ).total_seconds(),
            "balance_observation_age_seconds": None,
            "observation_source": latest["source"],
            "observation_confidence": latest["confidence"],
            "observation_time": latest["observed_at"],
        }
    return {
        "balance_evidence": "unknown",
        "remaining": None,
        "balance_samples": 0,
        "observation_age_seconds": None,
        "balance_observation_age_seconds": None,
        "observation_source": None,
        "observation_confidence": None,
        "observation_time": None,
    }


def entitlement_view(
    events: Sequence[Mapping[str, object]], *, as_of: datetime, private: bool = False
) -> dict[str, Any]:
    """Return either an owner view or a label-free MCP-safe overview.

    ``private=False`` intentionally omits account and pool identifiers, labels,
    balances, products, source references and credential references. It grants
    no routing or spending authority to an MCP caller.

    Parameters
    ----------
    events : Sequence[Mapping[str, object]]
        Complete immutable ledger stream.
    as_of : datetime.datetime
        Offset-aware evaluation time.
    private : bool
        Owner CLI view when true; safe aggregate MCP view when false.

    Returns
    -------
    dict[str, Any]
        Versioned JSON-compatible report without mixed-unit totals.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a UTC offset")
    entries = active_events(events)
    accounts = {str(item["account_id"]): item for item in entries if item["kind"] == "account"}
    pools = {str(item["pool_id"]): item for item in entries if item["kind"] == "pool"}
    report: dict[str, Any] = {
        "schema_version": VIEW_SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "authority": "advisory_only",
        "account_count": len(accounts),
        "pool_count": len(pools),
    }
    if not private:
        report["private_details"] = "withheld"
        return report
    account_rows: list[dict[str, object]] = []
    for account in accounts.values():
        expiry = account.get("expires_at")
        expired = expiry is not None and parse_time(expiry, "expires_at") <= as_of
        account_rows.append(
            {
                "account_id": account["account_id"],
                "label": account["label"],
                "status": "expired" if expired else account["status"],
                "record_age_seconds": (
                    as_of - parse_time(account["recorded_at"], "recorded_at")
                ).total_seconds(),
                "source": account["source"],
                "confidence": account["confidence"],
                "recorded_at": account["recorded_at"],
                "expires_at": expiry,
            }
        )
    report["accounts"] = sorted(account_rows, key=lambda row: str(row["account_id"]))
    pool_rows: list[dict[str, object]] = []
    for pool in pools.values():
        account = accounts[str(pool["account_id"])]
        account_expiry = account.get("expires_at")
        usable = account["status"] == "active" and (
            account_expiry is None or parse_time(account_expiry, "expires_at") > as_of
        )
        windows: list[dict[str, object]] = []
        for window in entries:
            if window["kind"] != "window" or window["pool_id"] != pool["pool_id"]:
                continue
            start = parse_time(window["starts_at"], "starts_at")
            end = parse_time(window["ends_at"], "ends_at")
            current = start <= as_of < end
            balance = _balance(entries, window, as_of)
            forecast = forecast_window(events, str(window["window_id"]), as_of=as_of)
            windows.append(
                {
                    "window_id": window["window_id"],
                    "revision": window["event_id"],
                    "starts_at": window["starts_at"],
                    "ends_at": window["ends_at"],
                    "renewal_at": window.get("renewal_at"),
                    "grant": window["grant"],
                    "unit": window["unit"],
                    "price_revision": window["price_revision"],
                    "source": window["source"],
                    "confidence": window["confidence"],
                    "recorded_at": window["recorded_at"],
                    "current": current,
                    "expires_in_seconds": (end - as_of).total_seconds() if current else None,
                    "upcoming_expiry": current and (end - as_of).total_seconds() <= 7 * 86400,
                    **balance,
                    **(
                        {} if current else {"remaining": None, "balance_evidence": "outside_window"}
                    ),
                    "forecast": asdict(forecast),
                }
            )
        surfaces = [
            {
                "surface_id": item["surface_id"],
                "product": item["product"],
                "channel": item["channel"],
            }
            for item in entries
            if item["kind"] == "surface" and item["pool_id"] == pool["pool_id"]
        ]
        pool_rows.append(
            {
                "pool_id": pool["pool_id"],
                "account_id": pool["account_id"],
                "unit": pool["unit"],
                "resource_kind": pool.get("resource_kind"),
                "capabilities": pool.get("capabilities", []),
                "data_classes": pool.get("data_classes", []),
                "eligible_projects": pool.get("eligible_projects", []),
                "idle_cost": pool.get("idle_cost"),
                "account_usable": usable,
                "surfaces": sorted(surfaces, key=lambda row: str(row["surface_id"])),
                "windows": sorted(windows, key=lambda row: str(row["starts_at"])),
            }
        )
    report["pools"] = sorted(pool_rows, key=lambda row: str(row["pool_id"]))
    return report
