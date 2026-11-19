import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server is intentionally reachable from the LAN so a phone can open
// the same UI. API traffic is proxied to the local engine, which means the
// Vite process (not the phone) is the engine's client. Use only on a trusted
// network, or set VITE_API_BASE and an API key for the engine's key mode.
const engineTarget = process.env.VITE_API_TARGET || "http://localhost:5000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: engineTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },
});
