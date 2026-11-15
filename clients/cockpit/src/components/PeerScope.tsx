// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — a peer scope's deck band and the panels it does not observe

import { useContext, type JSX } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { InspectorTab } from "../lib/workspace";
import type { PeerBlock } from "../types";
import { ScopeContext, ScopeTag } from "./ScopeTag";

/** Return the hub a peer scope shows, or null in a scope of this hub's own records. */
export function usePeerHub(): string | null {
  const scope = useContext(ScopeContext);
  return scope.kind === "peer" ? scope.hub : null;
}

/** The body of a panel a feed of this hub fills, in a peer scope: nothing local, and why. */
export function PeerFeedNotice({ hub }: { readonly hub: string }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <p className="panel__placeholder peer-notice" role="note">
      {t("scope.peerFeedNotice", { hub })}
    </p>
  );
}

interface PeerFeedPanelProps {
  /** The panel's accessible name, as it has outside a peer scope. */
  readonly label: string;
  /** The panel's visible title. */
  readonly title: string;
  readonly hub: string;
}

/** A panel of this hub's feeds, shown in a peer scope: its head and tag, and the notice. */
export function PeerFeedPanel({ label, title, hub }: PeerFeedPanelProps): JSX.Element {
  return (
    <section className="panel" aria-label={label}>
      <div className="panel__head">
        <span>{title}</span>
        <ScopeTag />
      </div>
      <PeerFeedNotice hub={hub} />
    </section>
  );
}

/**
 * Each inspector feed tab's region name and title, for its panel in a peer
 * scope. The fleet views' name is the catalogue's, as that panel's is.
 */
const PEER_TAB_PANELS: Readonly<Record<Exclude<InspectorTab, "topology">, { label: string; title: string }>> = {
  attention: { label: "Fleet attention queue", title: "Attention queue" },
  log: { label: "Signal log", title: "Signal log" },
  fleet: { label: "", title: "Fleet" },
  metrics: { label: "Log metrics", title: "Metrics" },
  audit: { label: "Receipt and operator audit", title: "Audit" },
  incident: { label: "Guided incident workspace", title: "Incident" },
  causality: { label: "Causality inspector", title: "Causality" },
};

interface PeerInspectorBodyProps {
  readonly tab: Exclude<InspectorTab, "topology">;
  readonly hub: string;
}

/** An inspector feed tab in a peer scope: this hub's feeds are not pulled, so it says why. */
export function PeerInspectorBody({ tab, hub }: PeerInspectorBodyProps): JSX.Element {
  const { t } = useCockpitI18n();
  const panel = PEER_TAB_PANELS[tab];
  return (
    <div className="inspector__body" id="inspector-panel" role="tabpanel" aria-labelledby={`inspector-tab-${tab}`}>
      <PeerFeedPanel label={panel.label || t("fleet.views.aria")} title={panel.title} hub={hub} />
    </div>
  );
}

/**
 * The deck-wide band of a peer scope: whose state the deck shows, whether the
 * last pull reached it, and that writes are off. Nothing renders in a scope of
 * this hub's own records.
 */
export function PeerBand({ peer }: { readonly peer: PeerBlock | null }): JSX.Element | null {
  const hub = usePeerHub();
  const { t } = useCockpitI18n();
  if (hub === null) return null;
  const unknown = t("scope.peerUnknown");
  let status: string;
  if (peer === null) status = t("scope.peerWaiting");
  else if (!peer.observed) status = t("scope.peerNotObserved", { hub });
  else if (!peer.reachable) status = t("scope.peerUnreached", { error: peer.error });
  else status = t("scope.peerReached", { cursor: peer.cursor ?? unknown, lag: peer.lag ?? unknown });
  return (
    <div className={`peer-band${peer !== null && peer.reachable ? "" : " peer-band--down"}`} role="status">
      <strong className="peer-band__title">{t("scope.peerBand", { hub })}</strong>
      <span className="peer-band__status">{status}</span>
    </div>
  );
}
