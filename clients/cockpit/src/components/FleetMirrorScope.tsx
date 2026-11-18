// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the Fleet mirror's scope: its band, its board and claims, and what it does not observe

import { useContext, type JSX } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import {
  evidenceLine,
  mirrorBoard,
  mirrorClaimGroups,
  mirrorFreshness,
  type FleetMirror,
  type MirrorState,
} from "../lib/fleetMirror";
import type { InspectorTab } from "../lib/workspace";
import { ScopeContext, ScopeTag } from "./ScopeTag";

/** Return whether the cockpit shows the Fleet mirror's scope. */
export function useFleetScope(): boolean {
  return useContext(ScopeContext).kind === "fleet";
}

function useAge(): (seconds: number) => string {
  const { t } = useCockpitI18n();
  return (seconds) =>
    seconds < 120 ? t("mirror.ageSeconds", { n: seconds }) : t("mirror.ageMinutes", { n: Math.floor(seconds / 60) });
}

/** A panel of this hub's feeds in the Fleet scope: its head, its tag, and why it is empty. */
export function FleetFeedPanel({ label, title }: { readonly label: string; readonly title: string }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <section className="panel" aria-label={label}>
      <div className="panel__head">
        <span>{title}</span>
        <ScopeTag />
      </div>
      <p className="panel__placeholder peer-notice" role="note">
        {t("scope.fleetFeedNotice")}
      </p>
    </section>
  );
}

const TAB_PANELS: Readonly<Record<InspectorTab, { label: string; title: string }>> = {
  attention: { label: "Fleet attention queue", title: "Attention queue" },
  log: { label: "Signal log", title: "Signal log" },
  fleet: { label: "", title: "Fleet" },
  topology: { label: "Topology", title: "Topology" },
  metrics: { label: "Log metrics", title: "Metrics" },
  audit: { label: "Receipt and operator audit", title: "Audit" },
  operations: { label: "Governed operations", title: "Operations" },
  incident: { label: "Guided incident workspace", title: "Incident" },
  causality: { label: "Causality inspector", title: "Causality" },
};

/** An inspector tab in the Fleet scope: the export carries none of these, so it says so. */
export function FleetInspectorBody({ tab }: { readonly tab: InspectorTab }): JSX.Element {
  const { t } = useCockpitI18n();
  const panel = TAB_PANELS[tab];
  return (
    <div className="inspector__body" id="inspector-panel" role="tabpanel" aria-labelledby={`inspector-tab-${tab}`}>
      <FleetFeedPanel label={panel.label || t("fleet.views.aria")} title={panel.title} />
    </div>
  );
}

function statusLine(state: MirrorState, t: ReturnType<typeof useCockpitI18n>["t"]): string | null {
  if (state.status === "incompatible") return t("scope.fleetIncompatible");
  if (state.status === "unavailable" || state.status === "error") return t("scope.fleetUnavailable");
  return null;
}

function HubHealth({ mirror }: { readonly mirror: FleetMirror }): JSX.Element {
  const { t } = useCockpitI18n();
  const unknown = t("mirror.none");
  return (
    <details className="fleet-health">
      <summary>{t("scope.fleetHealth", { count: mirror.peers.length })}</summary>
      <div
        className="fleet-health__scroll"
        role="region"
        tabIndex={0}
        aria-label={t("scope.fleetHealth", { count: mirror.peers.length })}
      >
      <table className="fleet-health__table">
        <thead>
          <tr>
            <th scope="col">{t("mirror.colHub")}</th>
            <th scope="col">{t("mirror.colCursor")}</th>
            <th scope="col">{t("mirror.colEvents")}</th>
            <th scope="col">{t("mirror.colFailures")}</th>
            <th scope="col">{t("mirror.colLastSuccess")}</th>
            <th scope="col">{t("mirror.colCaughtUp")}</th>
            <th scope="col">{t("mirror.colLimit")}</th>
          </tr>
        </thead>
        <tbody>
          {mirror.peers.map((peer) => (
            <tr key={peer.peerId}>
              <th scope="row">{peer.peerId}</th>
              <td>{peer.cursor}</td>
              <td>{peer.events}</td>
              <td>{peer.consecutiveFailures ?? unknown}</td>
              <td>{peer.lastSuccessAt === null ? unknown : new Date(peer.lastSuccessAt * 1000).toISOString()}</td>
              <td>{peer.caughtUp === null ? unknown : peer.caughtUp ? t("mirror.yes") : t("mirror.no")}</td>
              <td>{peer.drainLimit ?? unknown}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </details>
  );
}

interface FleetBandProps {
  readonly state: MirrorState;
  readonly nowMs: number;
}

/**
 * The deck-wide band of the Fleet scope: whose export the deck shows, how old
 * it is, that it is not this hub's state and that writes are off. Nothing
 * renders outside the Fleet scope.
 */
export function FleetBand({ state, nowMs }: FleetBandProps): JSX.Element | null {
  const fleet = useFleetScope();
  const { t } = useCockpitI18n();
  const age = useAge();
  if (!fleet) return null;
  const mirror = state.mirror;
  const problem = statusLine(state, t);
  // The deck's clock may lag in this scope; the last read's time is never older than the read.
  const now = Math.max(nowMs, state.fetchedAt ?? 0);
  const freshness = mirror === null ? null : mirrorFreshness(mirror.exportedAt, now);
  const stale = freshness !== null && (freshness.stale || problem !== null);
  return (
    <div className={`peer-band fleet-band${stale || problem !== null ? " peer-band--down" : ""}`} role="status">
      <strong className="peer-band__title">
        {mirror === null || freshness === null
          ? t("scope.fleetWaiting")
          : t("scope.fleetBand", { source: mirror.sourceId, age: age(freshness.ageSeconds) })}
      </strong>
      {stale && <span className="peer-band__status fleet-band__stale">{t("scope.fleetStale")}</span>}
      {problem !== null && <span className="peer-band__status">{problem}</span>}
      {mirror !== null && mirror.skippedRows > 0 && (
        <span className="peer-band__status">{t("scope.fleetSkipped", { count: mirror.skippedRows })}</span>
      )}
      {mirror !== null && <HubHealth mirror={mirror} />}
    </div>
  );
}

function FleetTag({ mirror }: { readonly mirror: FleetMirror | null }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <span className="panel__sub panel__scope panel__scope--peer" title={t("scope.fleetTitle")}>
      {t("scope.fleetTag", { source: mirror?.sourceId ?? "" })}
    </span>
  );
}

/** The Fleet scope's board: each task with its claim, the hub that holds it, and its evidence. */
export function FleetBoardPanel({ state }: { readonly state: MirrorState }): JSX.Element {
  const { t } = useCockpitI18n();
  const mirror = state.mirror;
  const rows = mirror === null ? [] : mirrorBoard(mirror);
  return (
    <section className="panel" aria-label="Task board">
      <div className="panel__head">
        <span>{t("mirror.boardTitle")}</span>
        <FleetTag mirror={mirror} />
      </div>
      {rows.length === 0 ? (
        <p className="panel__placeholder">{mirror === null ? t("scope.fleetWaiting") : t("mirror.empty")}</p>
      ) : (
        <ul className="fleet-board">
          {rows.map((task) => (
            <li key={task.taskId} className={`fleet-board__task${task.conflict === null ? "" : " fleet-board__task--conflict"}`}>
              <span className="fleet-board__id">{task.taskId}</span>
              <span className="fleet-board__title">{task.title}</span>
              <span className="fleet-board__status">{task.status}</span>
              <span className="fleet-board__claim">
                {task.claimedBy === null ? t("mirror.unclaimed") : t("mirror.claimedBy", { agent: task.claimedBy })}
              </span>
              <span className="fleet-board__hub">
                {t("mirror.claimHub", { hub: task.claimHub ?? t("mirror.noHub") })}
              </span>
              {task.conflict !== null && (
                <span className="fleet-board__badge" title={evidenceLine(task.conflict)}>
                  {t("mirror.conflict")}: {evidenceLine(task.conflict)}
                </span>
              )}
              {task.provenance !== null && (
                <span className="fleet-board__origin">{t("mirror.provenance", { evidence: evidenceLine(task.provenance) })}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The Fleet scope's claims: the claimed tasks, grouped by the hub that holds each claim. */
export function FleetClaimsPanel({ state }: { readonly state: MirrorState }): JSX.Element {
  const { t } = useCockpitI18n();
  const mirror = state.mirror;
  const groups = mirror === null ? [] : mirrorClaimGroups(mirror);
  return (
    <section className="panel" aria-label="File-scope claims">
      <div className="panel__head">
        <span>{t("mirror.claimsTitle")}</span>
        <FleetTag mirror={mirror} />
      </div>
      {groups.length === 0 ? (
        <p className="panel__placeholder">{mirror === null ? t("scope.fleetWaiting") : t("mirror.noClaims")}</p>
      ) : (
        groups.map((group) => (
          <div key={group.hub} className="fleet-claims__group">
            <h3 className="fleet-claims__hub">{group.hub === "" ? t("mirror.noHub") : group.hub}</h3>
            <ul className="fleet-claims">
              {group.tasks.map((task) => (
                <li key={task.taskId}>
                  <span className="fleet-board__id">{task.taskId}</span> {task.title} ·{" "}
                  {t("mirror.claimedBy", { agent: task.claimedBy ?? "" })}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}
