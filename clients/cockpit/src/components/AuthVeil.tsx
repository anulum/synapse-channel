// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — dashboard bearer unlock boundary

import type { JSX } from "react";
import { useState, type FormEvent } from "react";

interface AuthVeilProps {
  readonly reason: string | null;
  readonly onUnlock: (bearer: string) => boolean;
}

/** Full-screen credential boundary shown only after a protected request returns 401. */
export function AuthVeil({ reason, onUnlock }: AuthVeilProps): JSX.Element {
  const [bearer, setBearer] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (bearer.trim() === "") {
      setError("Paste the dashboard bearer token.");
      return;
    }
    if (!onUnlock(bearer)) {
      setError("Session storage is unavailable; the cockpit cannot retain this bearer safely.");
      return;
    }
    setBearer("");
    setError(null);
  };

  return (
    <main className="auth-veil" aria-labelledby="auth-title">
      <form className="auth-card" onSubmit={submit}>
        <span className="auth-card__eyebrow">SYNAPSE·CHANNEL</span>
        <h1 id="auth-title">Unlock cockpit</h1>
        <p>
          This dashboard protects its live feeds. Paste the bearer provided for your dashboard
          principal; it stays in this tab&apos;s session storage only.
        </p>
        <label htmlFor="dashboard-bearer">Dashboard bearer token</label>
        <input
          id="dashboard-bearer"
          type="password"
          value={bearer}
          autoComplete="off"
          autoFocus
          spellCheck={false}
          onChange={(event) => setBearer(event.target.value)}
        />
        {(error ?? reason) !== null && (
          <p className="auth-card__error" role="alert">
            {error ?? reason}
          </p>
        )}
        <button type="submit">unlock cockpit</button>
        <small>The bearer is never placed in the URL, logs, local storage, or shell cache.</small>
      </form>
    </main>
  );
}
