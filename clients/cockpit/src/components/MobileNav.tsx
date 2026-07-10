// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the phone-width segmented switch between deck sections

import type { JSX } from "react";

/** The deck sections a phone shows one at a time. */
export type MobileSegment = "signals" | "claims" | "board" | "roster" | "reliability";

/** Segment order: triage material first, reference material last. */
export const MOBILE_SEGMENTS: readonly MobileSegment[] = [
  "signals",
  "claims",
  "board",
  "roster",
  "reliability",
];

interface MobileNavProps {
  readonly active: MobileSegment;
  readonly onSelect: (segment: MobileSegment) => void;
}

/**
 * The segmented switch rendered only at phone width (CSS hides it above
 * 640px). One section at a time replaces the desktop's everything-at-once
 * grid; the spine and HUD stay above it at every width. Targets are 44px —
 * finger-sized, per the platform minimum.
 */
export function MobileNav({ active, onSelect }: MobileNavProps): JSX.Element {
  return (
    <nav className="mobile-nav" aria-label="Deck section">
      {MOBILE_SEGMENTS.map((segment) => (
        <button
          key={segment}
          type="button"
          className={`mobile-nav__seg${segment === active ? " mobile-nav__seg--active" : ""}`}
          aria-pressed={segment === active}
          onClick={() => onSelect(segment)}
        >
          {segment}
        </button>
      ))}
    </nav>
  );
}
