# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded, read-only public vendor and provider discovery
"""Inventory public candidate signals for human review without enabling integrations."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "integrations" / "vendor-discovery" / "sources.json"
DEFAULT_CATALOG = ROOT / "integrations" / "vendor-discovery" / "catalog.json"
MODEL_URL = "https://models.dev/api.json"
MCP_URL = "https://registry.modelcontextprotocol.io/v0.1/servers"
HOST_URL = "https://api.github.com/search/repositories"
MAX_BYTES = {MODEL_URL: 8_000_000, MCP_URL: 2_000_000, HOST_URL: 2_000_000}
MAX_MCP_PAGES = 3
MAX_CANDIDATES = 1000
_KEY = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,180}$")


class DiscoveryError(ValueError):
    """A source, review configuration or candidate record is unusable."""


def _digest(value: Mapping[str, Any]) -> str:
    """Hash stable candidate claims, excluding a source's volatile update time."""
    stable = {key: item for key, item in value.items() if key != "updated_at"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def _text(value: object, limit: int = 300) -> str | None:
    """Retain a short source value without treating it as executable content."""
    return value[:limit] if isinstance(value, str) and value.strip() else None


def fetch_json(url: str) -> dict[str, Any]:
    """Read an exact public HTTPS source with byte, redirect and time bounds."""
    parsed = urlsplit(url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if base not in MAX_BYTES or parsed.scheme != "https":
        raise DiscoveryError("source URL is not allowed")
    if base == MODEL_URL and parsed.query:
        raise DiscoveryError("model catalog query is not allowed")
    if base == MCP_URL and not _allowed_mcp_query(parsed.query):
        raise DiscoveryError("MCP query is not allowed")
    if base == HOST_URL and not _allowed_host_query(parsed.query):
        raise DiscoveryError("host search query is not allowed")
    request = Request(
        url,
        headers={"User-Agent": "Synapse-Channel-Discovery/0.1", "Accept": "application/json"},
    )
    with urlopen(request, timeout=15) as response:  # nosec B310 - exact HTTPS source list
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != parsed.hostname:
            raise DiscoveryError("public source redirected to another host")
        body = cast(bytes, response.read(MAX_BYTES[base] + 1))
    if len(body) > MAX_BYTES[base]:
        raise DiscoveryError("public source exceeded byte limit")
    raw = json.loads(body)
    if not isinstance(raw, dict):
        raise DiscoveryError("public source root must be an object")
    return cast(dict[str, Any], raw)


def _allowed_mcp_query(query: str) -> bool:
    """Allow only bounded latest-version incremental registry reads."""
    from urllib.parse import parse_qs

    parts = parse_qs(query, strict_parsing=True)
    if set(parts) - {"limit", "version", "updated_since", "include_deleted", "cursor"}:
        return False
    if parts.get("limit") != ["100"] or parts.get("version") != ["latest"]:
        return False
    if parts.get("include_deleted") != ["true"]:
        return False
    since = parts.get("updated_since", [None])[0]
    if not isinstance(since, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", since) is None:
        return False
    cursor = parts.get("cursor", [None])[0]
    return cursor is None or (len(cursor) <= 300 and "\x00" not in cursor)


def _allowed_host_query(query: str) -> bool:
    """Fix GitHub's unauthenticated search to one public topic and page."""
    from urllib.parse import parse_qs

    return parse_qs(query) == {
        "q": ["topic:ai-coding-agent"],
        "sort": ["updated"],
        "order": ["desc"],
        "per_page": ["100"],
    }


def load_inputs(config_path: Path, catalog_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the hand-owned review cadence, aliases and prior catalog."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise DiscoveryError("discovery configuration schema is invalid")
    if not isinstance(config.get("review_owner"), str) or not config["review_owner"]:
        raise DiscoveryError("discovery has no review owner")
    reviewed = config.get("reviewed_at")
    if not isinstance(reviewed, str):
        raise DiscoveryError("discovery review date is missing")
    date.fromisoformat(reviewed)
    interval = config.get("review_interval_days")
    age = config.get("max_source_age_days")
    if any(
        not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 31 for v in (interval, age)
    ):
        raise DiscoveryError("review interval or source age must be 1..31 days")
    aliases = config.get("aliases")
    if not isinstance(aliases, dict) or any(
        not isinstance(k, str)
        or not isinstance(v, str)
        or not _KEY.fullmatch(k)
        or not _KEY.fullmatch(v)
        or ":" not in k
        or ":" not in v
        for k, v in aliases.items()
    ):
        raise DiscoveryError("aliases must map exact source keys to canonical keys")
    if not isinstance(catalog, dict) or catalog.get("schema") != 1:
        raise DiscoveryError("discovery catalog schema is invalid")
    if not isinstance(catalog.get("sources"), dict) or not isinstance(
        catalog.get("candidates"), dict
    ):
        raise DiscoveryError("discovery catalog collections are invalid")
    for key, row in catalog["candidates"].items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or ":" not in key:
            raise DiscoveryError("prior candidate key is invalid")
        if not isinstance(row, dict) or not isinstance(row.get("publisher"), str):
            raise DiscoveryError("prior candidate is invalid")
        if not isinstance(row.get("sources"), dict) or not isinstance(row.get("first_seen"), str):
            raise DiscoveryError("prior candidate provenance is invalid")
        for source_url, evidence in row["sources"].items():
            if source_url not in MAX_BYTES or not isinstance(evidence, dict):
                raise DiscoveryError("prior candidate source is invalid")
            items = evidence.get("items")
            if not isinstance(items, dict) or not items:
                raise DiscoveryError("prior candidate sightings are invalid")
            for source_key, sighting in items.items():
                if not isinstance(source_key, str) or not _KEY.fullmatch(source_key):
                    raise DiscoveryError("prior source key is invalid")
                if (
                    not isinstance(sighting, dict)
                    or not isinstance(sighting.get("evidence_sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", sighting["evidence_sha256"]) is None
                ):
                    raise DiscoveryError("prior candidate digest is invalid")
    return cast(dict[str, Any], config), cast(dict[str, Any], catalog)


def _candidate(
    kind: str, product_id: str, publisher: str, source: str, metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """Preserve separate vendor, product and interface facts as source claims."""
    key = f"{kind}:{product_id.lower()}"
    if not _KEY.fullmatch(key) or ":" not in key:
        raise DiscoveryError("candidate identifier is invalid")
    evidence = dict(metadata)
    return {
        "key": key,
        "kind": kind,
        "product_id": product_id[:180],
        "publisher": publisher[:180],
        "source": source,
        "evidence_sha256": _digest(evidence),
        "evidence": evidence,
    }


def parse_models(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract provider entries without copying the large model catalog."""
    if len(raw) > MAX_CANDIDATES:
        raise DiscoveryError("model provider count exceeded bound")
    result = []
    for provider_id, row in raw.items():
        if not isinstance(provider_id, str) or not isinstance(row, dict):
            raise DiscoveryError("model provider entry is invalid")
        if row.get("id") != provider_id or not isinstance(row.get("models"), dict):
            raise DiscoveryError("model provider identity is inconsistent")
        evidence = {
            "name": _text(row.get("name")),
            "documentation": _text(row.get("doc")),
            "api_endpoint": _text(row.get("api")),
            "sdk": _text(row.get("npm")),
            "model_count": len(row["models"]),
            "interface": "model-api",
            "license": None,
            "auth": "env-key-signal" if row.get("env") else None,
            "privacy": None,
            "cost": None,
        }
        result.append(_candidate("provider", provider_id, provider_id, MODEL_URL, evidence))
    return result


def parse_mcp(raw: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Extract registry entries and its opaque next-page cursor."""
    servers = raw.get("servers")
    metadata = raw.get("metadata")
    if not isinstance(servers, list) or len(servers) > 100 or not isinstance(metadata, dict):
        raise DiscoveryError("MCP registry response is invalid")
    cursor = metadata.get("nextCursor")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 300):
        raise DiscoveryError("MCP registry cursor is invalid")
    result = []
    for entry in servers:
        if not isinstance(entry, dict) or not isinstance(entry.get("server"), dict):
            raise DiscoveryError("MCP registry server is invalid")
        server = entry["server"]
        name = server.get("name")
        if not isinstance(name, str) or not name or "/" not in name:
            raise DiscoveryError("MCP registry name is invalid")
        meta = entry.get("_meta")
        if not isinstance(meta, dict):
            raise DiscoveryError("MCP registry metadata is invalid")
        official = meta.get("io.modelcontextprotocol.registry/official", {})
        if not isinstance(official, dict):
            raise DiscoveryError("MCP registry status is invalid")
        remotes = server.get("remotes") or []
        packages = server.get("packages") or []
        if not isinstance(remotes, list) or not isinstance(packages, list):
            raise DiscoveryError("MCP transports are invalid")
        transports = sorted(
            {
                item["type"]
                for item in remotes
                if isinstance(item, dict) and isinstance(item.get("type"), str)
            }
        )
        evidence = {
            "title": _text(server.get("title")),
            "version": _text(server.get("version")),
            "status": _text(official.get("status")),
            "updated_at": _text(official.get("updatedAt")),
            "interface": "mcp",
            "transports": transports,
            "package_count": len(packages),
            "license": None,
            "auth": None,
            "privacy": None,
            "cost": None,
        }
        result.append(_candidate("mcp", name, name.split("/", 1)[0], MCP_URL, evidence))
    return result, cursor


def parse_hosts(raw: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Treat GitHub topic search as a candidate signal, never a trusted host list."""
    items = raw.get("items")
    count = raw.get("total_count")
    if not isinstance(items, list) or len(items) > 100 or not isinstance(count, int):
        raise DiscoveryError("GitHub host search response is invalid")
    result = []
    for repo in items:
        if not isinstance(repo, dict) or not isinstance(repo.get("owner"), dict):
            raise DiscoveryError("GitHub repository entry is invalid")
        name = repo.get("full_name")
        owner = repo["owner"].get("login")
        if (
            not isinstance(name, str)
            or not isinstance(owner, str)
            or not name.startswith(owner + "/")
        ):
            raise DiscoveryError("GitHub repository identity is invalid")
        license_info = repo.get("license")
        license_id = license_info.get("spdx_id") if isinstance(license_info, dict) else None
        evidence = {
            "name": _text(repo.get("name")),
            "documentation": _text(repo.get("html_url")),
            "updated_at": _text(repo.get("updated_at")),
            "interface": None,
            "license": _text(license_id),
            "auth": None,
            "privacy": None,
            "cost": None,
        }
        result.append(_candidate("host", name, owner, HOST_URL, evidence))
    return result, count == len(items) and raw.get("incomplete_results") is False


def collect(
    *,
    fetch: Callable[[str], dict[str, Any]] = fetch_json,
    now: datetime,
    fixture: Mapping[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], dict[str, bool]]:
    """Use the same parsers for live bounded sources and offline feed fixtures."""
    stamp = now.astimezone(timezone.utc).date()
    since = stamp - timedelta(days=7)
    urls = {
        "models_dev": MODEL_URL,
        "github_hosts": HOST_URL
        + "?"
        + urlencode(
            {"q": "topic:ai-coding-agent", "sort": "updated", "order": "desc", "per_page": 100}
        ),
    }
    rows: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    complete: dict[str, bool] = {}
    for source, url in urls.items():
        try:
            raw = fixture[source] if fixture is not None else fetch(url)
            if not isinstance(raw, dict):
                raise DiscoveryError("feed fixture is invalid")
            if source == "models_dev":
                rows[source], complete[source] = parse_models(raw), True
            else:
                rows[source], complete[source] = parse_hosts(raw)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors[source] = type(exc).__name__
            complete[source] = False
    try:
        all_mcp: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = fixture.get("mcp_registry") if fixture is not None else []
        if not isinstance(pages, list):
            raise DiscoveryError("MCP feed fixture pages are invalid")
        seen_cursors: set[str] = set()
        for index in range(MAX_MCP_PAGES):
            query = {
                "limit": 100,
                "version": "latest",
                "include_deleted": "true",
                "updated_since": since.isoformat() + "T00:00:00Z",
            }
            if cursor is not None:
                query["cursor"] = cursor
            url = MCP_URL + "?" + urlencode(query)
            raw = pages[index] if fixture is not None else fetch(url)
            if not isinstance(raw, dict):
                raise DiscoveryError("MCP feed fixture is invalid")
            batch, cursor = parse_mcp(raw)
            all_mcp.extend(batch)
            if cursor is None:
                break
            if cursor in seen_cursors:
                raise DiscoveryError("MCP registry cursor repeated")
            seen_cursors.add(cursor)
        rows["mcp_registry"] = all_mcp
        complete["mcp_registry"] = cursor is None
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        errors["mcp_registry"] = type(exc).__name__
        complete["mcp_registry"] = False
    return rows, errors, complete


def reconcile(
    config: Mapping[str, Any],
    previous: Mapping[str, Any],
    feeds: Mapping[str, list[dict[str, Any]]],
    errors: Mapping[str, str],
    complete: Mapping[str, bool],
    *,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a review queue and next state without granting candidate authority."""
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    today = now.date()
    due = date.fromisoformat(config["reviewed_at"]) + timedelta(days=config["review_interval_days"])
    next_state = json.loads(json.dumps(previous))
    next_state["checked_at"] = stamp
    source_rows = next_state["sources"]
    statuses: dict[str, dict[str, Any]] = {}
    for source in ("models_dev", "github_hosts", "mcp_registry"):
        prior_success = source_rows.get(source, {}).get("last_successful_at")
        if source in errors:
            stale = (
                prior_success is None
                or (today - date.fromisoformat(prior_success[:10])).days
                > config["max_source_age_days"]
            )
            statuses[source] = {
                "status": "stale" if stale else "unavailable",
                "error": errors[source],
                "last_successful_at": prior_success,
            }
        else:
            source_rows[source] = {"last_successful_at": stamp, "complete": complete[source]}
            statuses[source] = {
                "status": "current" if complete[source] else "partial",
                "count": len(feeds[source]),
                "last_successful_at": stamp,
            }
    observed: dict[str, list[dict[str, Any]]] = {}
    for items in feeds.values():
        for item in items:
            key = config["aliases"].get(item["key"], item["key"])
            observed.setdefault(key, []).append(item)
    if len(observed) > MAX_CANDIDATES:
        raise DiscoveryError("candidate count exceeded bound")
    queue = []
    candidates = next_state["candidates"]
    for key, sightings in sorted(observed.items()):
        publishers = {item["publisher"].casefold() for item in sightings}
        prior = candidates.get(key)
        if prior is not None:
            publishers.add(prior["publisher"].casefold())
        conflict = len(publishers) > 1
        first_seen = prior["first_seen"] if prior else stamp
        old_sources = prior["sources"] if prior else {}
        merged_sources = dict(old_sources)
        for item in sightings:
            source_entry = dict(merged_sources.get(item["source"], {"items": {}}))
            source_items = dict(source_entry["items"])
            source_items[item["key"]] = {
                "evidence_sha256": item["evidence_sha256"],
                "seen_at": stamp,
            }
            source_entry["items"] = source_items
            merged_sources[item["source"]] = source_entry
        changed = prior is not None and any(
            old_sources.get(item["source"], {})
            .get("items", {})
            .get(item["key"], {})
            .get("evidence_sha256")
            != item["evidence_sha256"]
            for item in sightings
        )
        was_withdrawn = prior is not None and prior.get("status") == "withdrawn"
        deleted = any(item["evidence"].get("status") == "deleted" for item in sightings)
        status = (
            "publisher_conflict"
            if conflict
            else "withdrawn"
            if deleted
            else "reappeared"
            if was_withdrawn
            else "new"
            if prior is None
            else "changed"
            if changed
            else "seen"
        )
        candidates[key] = {
            "kind": sightings[0]["kind"],
            "product_id": sightings[0]["product_id"],
            "publisher": prior["publisher"] if prior else sightings[0]["publisher"],
            "first_seen": first_seen,
            "last_seen": stamp,
            "status": status,
            "sources": merged_sources,
        }
        repeated_withdrawal = (
            status == "withdrawn"
            and prior is not None
            and prior.get("status") == "withdrawn"
            and not changed
        )
        if status != "seen" and not repeated_withdrawal:
            queue.append(
                {
                    "key": key,
                    "status": status,
                    "kind": sightings[0]["kind"],
                    "suggested_lane": {
                        "provider": "C07",
                        "host": "C15",
                        "mcp": "C09/C15",
                    }[sightings[0]["kind"]],
                    "publisher": sightings[0]["publisher"],
                    "evidence": [
                        {
                            "source": item["source"],
                            "source_key": item["key"],
                            "sha256": item["evidence_sha256"],
                            "claims": item["evidence"],
                        }
                        for item in sightings
                    ],
                    "action": "verify publisher and official docs"
                    if conflict
                    else "review candidate evidence",
                }
            )
    for key, row in candidates.items():
        if key in observed or row.get("status") == "withdrawn":
            continue
        associated = row["sources"]
        complete_by_url = {
            MODEL_URL: complete.get("models_dev", False),
            HOST_URL: complete.get("github_hosts", False),
        }
        if associated and all(complete_by_url.get(source, False) for source in associated):
            row["status"] = "withdrawn"
            queue.append(
                {"key": key, "status": "withdrawn", "action": "confirm removal with publisher"}
            )
    report = {
        "schema": 1,
        "checked_at": stamp,
        "review_owner": config["review_owner"],
        "next_review_at": due.isoformat(),
        "review_overdue": today > due,
        "sources": statuses,
        "counts": {
            "observed": len(observed),
            "review_queue": len(queue),
            "persisted": len(candidates),
        },
        "review_queue": queue,
        "admission": (
            "human review only; no automatic integration, routing, install or support claim"
        ),
    }
    return report, next_state


def main() -> int:
    """Write an inspectable report and candidate-state proposal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--next-catalog", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        input_paths = {args.config.resolve(), args.catalog.resolve()}
        output_paths = {args.report.resolve(), args.next_catalog.resolve()}
        if len(output_paths) != 2 or input_paths & output_paths:
            raise DiscoveryError("reports must not overwrite configuration or catalog")
        config, previous = load_inputs(args.config, args.catalog)
        fixture = json.loads(args.fixture.read_text(encoding="utf-8")) if args.fixture else None
        if fixture is not None and not isinstance(fixture, dict):
            raise DiscoveryError("feed fixture root must be an object")
        now = datetime.now(timezone.utc)
        feeds, errors, complete = collect(now=now, fixture=fixture)
        report, next_state = reconcile(config, previous, feeds, errors, complete, now=now)
        for path, data in ((args.report, report), (args.next_catalog, next_state)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        print(f"vendor discovery failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "checked_at": report["checked_at"],
                "counts": report["counts"],
                "sources": report["sources"],
            },
            sort_keys=True,
        )
    )
    bad_source = any(
        row["status"] in {"stale", "unavailable"} for row in report["sources"].values()
    )
    conflict = any(row["status"] == "publisher_conflict" for row in report["review_queue"])
    return 1 if args.strict and (bad_source or conflict or report["review_overdue"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
