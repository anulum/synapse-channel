// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — live local host-session panel
import { useEffect, useRef, useState, type JSX } from "react";
import { useCockpitI18n } from "../context/CockpitI18n";
import { authenticatedFetch } from "../lib/auth";
import type { MessageKey } from "../lib/i18n";
import { parseHostObservation, type HostObservation } from "../lib/hostSessions";
import "./HostSessions.css";

type Status = "loading" | "live" | "unsupported" | "locked" | "incompatible" | "unavailable" | "stale";
interface State { readonly status: Status; readonly observation: HostObservation | null; }
const messages: Record<Status, MessageKey> = {
  loading: "host.loading",
  live: "host.live",
  unsupported: "host.unsupported",
  locked: "host.locked",
  incompatible: "host.incompatible",
  unavailable: "host.unavailable",
  stale: "host.stale",
};

/** Whole-second `<days>d HH:MM:SS` or `HH:MM:SS`; digits stay locale-neutral for scanning a dense roster. */
export function formatRuntime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const days = Math.floor(whole / 86400);
  const clock = [Math.floor((whole % 86400) / 3600), Math.floor((whole % 3600) / 60), whole % 60]
    .map((part) => String(part).padStart(2, "0")).join(":");
  return days > 0 ? `${days}d ${clock}` : clock;
}

function ObservationAge({ observedAt }: { readonly observedAt: number }): JSX.Element {
  const { t } = useCockpitI18n();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <span>{t("host.age", { seconds: Math.max(0, Math.floor(now / 1000 - observedAt)) })}</span>;
}

function ContextCopy({ value, expiresAt }: {
  readonly value: string; readonly expiresAt: number;
}): JSX.Element {
  const { t } = useCockpitI18n();
  const [status, setStatus] = useState<"idle" | "pending" | "copied" | "failed">("idle");
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const available = typeof navigator.clipboard?.writeText === "function";
  const copy = async (): Promise<void> => {
    if (!available || Date.now() >= expiresAt || status === "pending") return;
    setStatus("pending");
    try {
      await navigator.clipboard.writeText(value);
      if (active.current) setStatus(Date.now() < expiresAt ? "copied" : "idle");
    } catch {
      if (active.current) setStatus(Date.now() < expiresAt ? "failed" : "idle");
    }
  };
  return <div className="host-sessions__copy">
    <button type="button" disabled={!available || status === "pending"}
      onClick={() => void copy()}>{t("host.copyContext")}</button>
    <span role="status">{!available ? t("host.copyUnavailable") : status === "copied"
      ? t("host.copied") : status === "failed" ? t("host.copyFailed") : ""}</span>
  </div>;
}

/** Poll the separately granted live feed; errors and access changes discard all rows. */
export function HostSessions({ revision }: { readonly revision: number }): JSX.Element {
  const { t, locale } = useCockpitI18n();
  const [state, setState] = useState<State>({ status: "loading", observation: null });
  const [query, setQuery] = useState("");
  const [resolvedRevision, setResolvedRevision] = useState(-1);
  useEffect(() => {
    let active = true;
    let next: ReturnType<typeof setTimeout> | undefined;
    let expiry: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    setState({ status: "loading", observation: null });
    setResolvedRevision(revision);
    setQuery("");
    const poll = async (): Promise<void> => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 3000);
      let result: State = { status: "unavailable", observation: null };
      try {
        const response = await authenticatedFetch("/host-sessions.json", { signal: controller.signal });
        if (response.status === 404) result = { status: "unsupported", observation: null };
        else if (response.status === 401 || response.status === 403) result = { status: "locked", observation: null };
        else if (response.ok) {
          const raw = await response.text();
          const observation = raw.length <= 1048576 ? parseHostObservation(JSON.parse(raw)) : null;
          result = observation === null
            ? { status: "incompatible", observation: null }
            : { status: "live", observation };
        }
      } catch { /* The unavailable state discards stale rows and retries. */ }
      finally { clearTimeout(timeout); }
      if (!active) return;
      if (expiry !== undefined) clearTimeout(expiry);
      setState(result);
      const observation = result.observation;
      if (observation === null) setQuery("");
      if (observation !== null) {
        const remaining = observation.valid_for_seconds * 1000 -
          Math.max(0, Date.now() - observation.observed_at * 1000);
        if (remaining <= 0) {
          setState({ status: "stale", observation: null });
          setQuery("");
        }
        else expiry = setTimeout(() => {
          if (active) {
            setState({ status: "stale", observation: null });
            setQuery("");
          }
        }, remaining);
      }
      next = setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => {
      active = false;
      controller?.abort();
      if (next !== undefined) clearTimeout(next);
      if (expiry !== undefined) clearTimeout(expiry);
    };
  }, [revision]);
  const visible = resolvedRevision === revision ? state : { status: "loading" as const, observation: null };
  const observation = visible.observation;
  const rows = observation?.rows.filter((row) =>
    [row.identity, row.project, row.pid, row.command_name, row.pane].join(" ").toLowerCase().includes(query.toLowerCase())) ?? [];
  return <section className="host-sessions panel" aria-label={t("host.title")}>
    <h2>{t("host.title")}</h2>
    <p role="status">{t(messages[visible.status])}</p>
    {observation !== null && <>
      <p>{t("host.observed", { time: new Date(observation.observed_at * 1000).toLocaleTimeString(locale), status: observation.process_status })}
        {" · "}<ObservationAge observedAt={observation.observed_at} /></p>
      <label>{t("host.filter")}<input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      {rows.length === 0 && <p>{t(query ? "host.noMatches" : observation.process_status === "complete"
        ? "host.empty" : "host.incomplete")}</p>}
      {rows.map((row) => <details key={row.reference} onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          event.currentTarget.open = false;
          event.currentTarget.querySelector("summary")?.focus();
        }
      }}>
        <summary>{row.identity ?? row.command_name} · PID {row.pid}
          {" · "}{row.started_at === null ? t("host.runtimeUnknown")
            : t("host.runtime", { duration: formatRuntime(observation.observed_at - row.started_at) })}
          {" · "}{t(row.attached === null ? "host.attachmentUnknown" : row.attached ? "host.attached" : "host.detached")}
          {row.duplicate_identity && ` · ${t("host.duplicate")}`}</summary>
        <dl>
          <dt>{t("host.reference")}</dt><dd>{row.reference}</dd>
          <dt>{t("host.provider")}</dt><dd>{row.provider ?? t("host.unknown")}</dd>
          <dt>{t("host.process")}</dt><dd>{row.state} / {row.parent_pid} / {row.start_ticks}</dd>
          <dt>{t("host.started")}</dt><dd>{row.started_at === null ? t("host.unknown")
            : new Date(row.started_at * 1000).toLocaleString(locale)} ({row.started_at_status})</dd>
          <dt>{t("host.identity")}</dt><dd>{row.identity_source}</dd>
          <dt>{t("host.presence")}</dt><dd>{t(row.presence === null ? "host.unknown" : row.presence ? "host.online" : "host.absent")}</dd>
          <dt>{t("host.waitersClaims")}</dt><dd>{row.waiters.join(", ") || t("host.none")} / {row.claims.join(", ") || t("host.none")}</dd>
          <dt>{t("host.pane")}</dt><dd>{row.session ?? t("host.unknown")} / {row.pane ?? t("host.unknown")}</dd>
          <dt>{t("host.cwd")}</dt><dd>{row.paths_requested ? row.cwd ?? t("host.unknown") : t("host.notGranted")} ({row.cwd_status})</dd>
          <dt>{t("host.context")}</dt><dd>{row.context_requested ? row.context_id ?? t("host.unknown") : t("host.notGranted")} ({row.context_status})
            {row.context_requested && row.context_status === "observed" && row.context_id !== null &&
              <ContextCopy key={`${revision}:${row.context_id}`} value={row.context_id}
                expiresAt={(observation.observed_at + observation.valid_for_seconds) * 1000} />}
          </dd>
        </dl>
        <p>{t("host.noControls")}</p>
      </details>)}
      <details><summary>{t("host.evidence")}</summary>
        <p>{t("host.host", { host: observation.host_ref, status: observation.tmux_status })}</p>
        <p>{t("host.coordination", { status: observation.coordination_status })}</p>
        <p>{t("host.activity", { id: observation.observation_id })}</p>
      </details>
    </>}
  </section>;
}
