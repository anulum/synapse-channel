// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — per-panel error boundary: one failed instrument never downs the deck

import { Component, type ReactNode } from "react";

interface PanelBoundaryProps {
  /** Panel name shown in the fallback so the operator knows what failed. */
  readonly name: string;
  readonly children: ReactNode;
}

interface PanelBoundaryState {
  readonly message: string | null;
}

/**
 * Catch a render failure inside one panel and replace only that panel with an
 * honest fallback naming the failure. The rest of the cockpit keeps flying —
 * a cockpit that goes fully dark because one gauge threw is worse than one
 * with a single dead gauge that says so.
 */
export class PanelBoundary extends Component<PanelBoundaryProps, PanelBoundaryState> {
  public override state: PanelBoundaryState = { message: null };

  public static getDerivedStateFromError(error: unknown): PanelBoundaryState {
    return { message: error instanceof Error ? error.message : String(error) };
  }

  public override render(): ReactNode {
    if (this.state.message === null) return this.props.children;
    return (
      <section className="panel" aria-label={`${this.props.name} (failed)`}>
        <div className="panel__head">
          <span>{this.props.name}</span>
          <span className="panel__sub panel__sub--warn">failed</span>
        </div>
        <div className="panel__body">
          <p className="panel__placeholder">
            {`This panel failed to render: ${this.state.message}. The rest of the cockpit is unaffected; reload to retry.`}
          </p>
        </div>
      </section>
    );
  }
}
