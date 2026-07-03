// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit SPA build configuration

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The cockpit is a static client bundle. In development (and in `vite preview`,
// which serves the production build for validation) the hub's read-only JSON
// endpoints proxy to the local `synapse dashboard` HTTP server so the SPA can
// consume live fleet state without a separate CORS configuration. In production
// the bundle is served from the dashboard origin itself, so the same relative
// paths hit the real endpoints with no proxy at all.
const DASHBOARD_ORIGIN = process.env["SYNAPSE_DASHBOARD_ORIGIN"] ?? "http://127.0.0.1:8765";

const DASHBOARD_PROXY = {
  "/snapshot.json": DASHBOARD_ORIGIN,
  "/studio.snapshot.json": DASHBOARD_ORIGIN,
  "/reliability.json": DASHBOARD_ORIGIN,
  "/causality.json": DASHBOARD_ORIGIN,
  "/federation.json": DASHBOARD_ORIGIN,
  "/events.json": DASHBOARD_ORIGIN,
  "/metrics.json": DASHBOARD_ORIGIN,
} as const;

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 8770,
    proxy: { ...DASHBOARD_PROXY },
  },
  preview: {
    port: 8772,
    proxy: { ...DASHBOARD_PROXY },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
  },
});
