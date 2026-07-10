// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the add-to-home-screen chip, shown only when the browser offers it

import type { JSX } from "react";
import { useEffect, useState } from "react";

/** The non-standard beforeinstallprompt event Chromium fires. */
interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
}

/**
 * A quiet install affordance: Chromium fires `beforeinstallprompt` when the
 * manifest qualifies, the chip appears, one tap hands over to the browser's
 * own dialog. iOS Safari has no such event — its Share → Add to Home Screen
 * path is documented in the README instead — so there the chip simply never
 * renders. No detection tricks, no nagging.
 */
export function InstallChip(): JSX.Element | null {
  const [prompt, setPrompt] = useState<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    const capture = (event: Event): void => {
      event.preventDefault();
      setPrompt(event as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", capture);
    return () => window.removeEventListener("beforeinstallprompt", capture);
  }, []);

  if (prompt === null) return null;
  return (
    <button
      type="button"
      className="install-chip"
      onClick={() => {
        void prompt.prompt();
        setPrompt(null);
      }}
    >
      add to home screen
    </button>
  );
}
