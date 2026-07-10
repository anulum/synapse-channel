// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the federation row: hub identity, peers, and partition honesty

import type { JSX } from "react";
import { memo } from "react";

import { contestedNamespaces, type FederationState } from "../lib/federation";

/** Dot class per peering lifecycle state (active / revoked / expired). */
function peerDotClass(state: string): string {
  if (state === "active") return "fed-peer__dot fed-peer__dot--active";
  if (state === "revoked") return "fed-peer__dot fed-peer__dot--revoked";
  return "fed-peer__dot fed-peer__dot--expired";
}

interface FederationRowProps {
  /** The federation feed's current state, including how it was obtained. */
  readonly state: FederationState;
  /** Hub version + config-posture fingerprint for the pinning chip. */
  readonly hubVersion?: string;
  readonly configEpoch?: string;
}

function FederationRowView({ state, hubVersion = "", configEpoch = "" }: FederationRowProps): JSX.Element {
  const posture = state.data;

  if (state.status === "absent" || (posture === null && state.status !== "error")) {
    return (
      <div className="fed-row fed-row--quiet" role="region" aria-label="Federation posture">
        <span className="fed-row__label">federation</span>
      {(hubVersion !== "" || configEpoch !== "") && (
        <span
          className="fed-row__epoch"
          title="Hub version and configuration-posture fingerprint — together the pin a cockpit checks against drift"
        >
          {hubVersion !== "" ? `v${hubVersion}` : ""}
          {configEpoch !== "" ? ` · epoch ${configEpoch.slice(0, 8)}` : ""}
        </span>
      )}
        <span className="fed-row__note">
          {state.status === "absent"
            ? "posture surface not served (/federation.json)"
            : "waiting for the hub"}
        </span>
      </div>
    );
  }

  if (posture === null) {
    return (
      <div className="fed-row fed-row--quiet" role="region" aria-label="Federation posture">
        <span className="fed-row__label">federation</span>
        <span className="fed-row__note">{`feed failed: ${state.error ?? "unknown"}`}</span>
      </div>
    );
  }

  const contested = contestedNamespaces(posture);

  return (
    <div
      className={`fed-row${contested.length > 0 ? " fed-row--partitioned" : ""}`}
      aria-label="Federation posture"
      role={contested.length > 0 ? "alert" : "region"}
    >
      <span className="fed-row__label" title={posture.note === "" ? undefined : posture.note}>
        federation
      </span>
      {posture.hubId !== "" && (
        <span className="fed-row__hub" title={`hub ${posture.hubId}`}>
          {posture.hubId}
        </span>
      )}
      {posture.domain !== "" && <span className="fed-row__domain">{posture.domain}</span>}
      {posture.peerings.length === 0 ? (
        <span className="fed-row__note">no peerings imported</span>
      ) : (
        <span className="fed-row__peers">
          {posture.peerings.map((peering) => (
            <span
              key={peering.domain}
              className="fed-peer"
              title={[
                `${peering.domain}: ${peering.state}`,
                peering.confirmedBy === "" ? "" : `confirmed by ${peering.confirmedBy}`,
                peering.source === "" ? "" : `source ${peering.source}`,
                peering.fingerprint === "" ? "" : `fingerprint ${peering.fingerprint}`,
              ]
                .filter((part) => part !== "")
                .join(" · ")}
            >
              <span className={peerDotClass(peering.state)} aria-hidden="true" />
              {peering.domain}
            </span>
          ))}
        </span>
      )}
      {contested.length > 0 && (
        <span className="fed-row__contested">
          ▲ partitioned:{" "}
          {contested
            .map((entry) => `${entry.namespace} (${entry.contesting.join(" vs ")})`)
            .join(", ")}
          {" — claims there are refused until the split heals"}
        </span>
      )}
    </div>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const FederationRow = memo(FederationRowView);
