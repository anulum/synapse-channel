// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the federation row: hub identity, peers, and partition honesty

import { contestedNamespaces, type FederationState } from "../lib/federation";

/** Dot class per peering lifecycle state. */
function peerDotClass(state: string): string {
  if (state === "active") return "fed-peer__dot fed-peer__dot--active";
  if (state === "revoked") return "fed-peer__dot fed-peer__dot--revoked";
  return "fed-peer__dot fed-peer__dot--stale";
}

interface FederationRowProps {
  /** The federation feed's current state, including how it was obtained. */
  readonly state: FederationState;
}

export function FederationRow({ state }: FederationRowProps): JSX.Element {
  const posture = state.data;

  if (state.status === "absent" || (posture === null && state.status !== "error")) {
    return (
      <div className="fed-row fed-row--quiet" aria-label="Federation posture">
        <span className="fed-row__label">federation</span>
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
      <div className="fed-row fed-row--quiet" aria-label="Federation posture">
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
      role={contested.length > 0 ? "alert" : undefined}
    >
      <span className="fed-row__label">federation</span>
      <span className="fed-row__hub" title={`hub ${posture.hubId}`}>
        {posture.hubId === "" ? "hub —" : posture.hubId}
      </span>
      {posture.domain !== "" && <span className="fed-row__domain">{posture.domain}</span>}
      {posture.peerings.length === 0 ? (
        <span className="fed-row__note">no peerings</span>
      ) : (
        <span className="fed-row__peers">
          {posture.peerings.map((peering) => (
            <span
              key={peering.domain}
              className="fed-peer"
              title={`${peering.domain}: ${peering.state}${
                peering.fingerprint === "" ? "" : ` · ${peering.fingerprint}`
              }`}
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
