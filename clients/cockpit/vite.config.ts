// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit SPA build configuration

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The cockpit is a static client bundle. In development it proxies the hub's
// read-only snapshot endpoints to the local `synapse dashboard` HTTP server so
// the SPA can consume live fleet state without a separate CORS configuration.
const DASHBOARD_ORIGIN = process.env["SYNAPSE_DASHBOARD_ORIGIN"] ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 8770,
    proxy: {
      "/snapshot.json": DASHBOARD_ORIGIN,
      "/studio.snapshot.json": DASHBOARD_ORIGIN,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
  },
});
