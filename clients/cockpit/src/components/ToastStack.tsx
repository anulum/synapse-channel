// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the toast stack: transitions, said once, out of the way

import type { Toast } from "../lib/toasts";

/** How many toasts show at once; older ones wait underneath. */
const SHOWN = 4;

interface ToastStackProps {
  readonly toasts: readonly Toast[];
  readonly onDismiss: (id: string) => void;
}

/**
 * Bottom-left, screen-reader-announced, colour + glyph redundant. The stack
 * caps what it shows and states the queue honestly; a toast dismisses on
 * click or by the caller's timer.
 */
export function ToastStack({ toasts, onDismiss }: ToastStackProps): JSX.Element | null {
  if (toasts.length === 0) return null;
  const shown = toasts.slice(0, SHOWN);
  const queued = toasts.length - shown.length;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {shown.map((toast) => (
        <button
          key={toast.id}
          type="button"
          className={`toast toast--${toast.severity}`}
          onClick={() => onDismiss(toast.id)}
          title="Dismiss"
        >
          <span className="toast__glyph" aria-hidden="true">
            {toast.severity === "crit" ? "●" : toast.severity === "warn" ? "!" : "✓"}
          </span>
          {toast.text}
        </button>
      ))}
      {queued > 0 && <span className="toast toast--more">{`+${queued} more`}</span>}
    </div>
  );
}
