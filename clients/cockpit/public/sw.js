// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit service worker: cache the shell, never the truth

// The split is the whole design. The APP SHELL (hashed JS/CSS/fonts/icons)
// is immutable by construction, so cache-first is free speed and makes the
// cockpit installable. The DATA FEEDS (*.json) are the fleet's live state
// and are NEVER cached — stale coordination data presented as current is
// worse than a spinner, so an unreachable hub surfaces as the app's own
// honest error/stale states (the HUD beacon stamps the last good fetch).
// Navigations go network-first with a cached fallback: updates propagate on
// the next online open, and an offline launch still boots the shell.

const CACHE = "cockpit-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(["./", "manifest.webmanifest"])),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live fleet state: straight to the network, never intercepted further.
  if (url.pathname.endsWith(".json") && !url.pathname.endsWith("manifest.webmanifest")) return;

  if (request.mode === "navigate") {
    // Network-first: a fresh shell whenever the hub is reachable, the
    // cached shell when it is not.
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put("./", copy));
          return response;
        })
        .catch(() => caches.match("./")),
    );
    return;
  }

  // Hashed immutable assets: cache-first, populate on first fetch.
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ??
        fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        }),
    ),
  );
});
