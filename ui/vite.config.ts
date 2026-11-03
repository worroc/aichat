import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> }; // Node global; avoids @types/node

// The hub to forward API calls to; `aichat-server start ui` passes AICHAT_URL through.
const SERVER = process.env.AICHAT_URL ?? "http://127.0.0.1:8765";
const API_PREFIXES = ["/ops", "/rooms", "/dm", "/questions", "/messages", "/inbox", "/me", "/directory", "/health"];

// HTTPS: `aichat-server start ui` passes the PEM text of AICHAT_TLS_CERT / AICHAT_TLS_KEY.
// It also sets NODE_EXTRA_CA_CERTS, so the proxy trusts an https:// hub signed by the aichat CA.
const CERT = process.env.AICHAT_UI_TLS_CERT_PEM;
const KEY = process.env.AICHAT_UI_TLS_KEY_PEM;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    https: CERT && KEY ? { cert: CERT, key: KEY } : undefined,
    proxy: Object.fromEntries(API_PREFIXES.map((p) => [p, { target: SERVER, changeOrigin: true }])),
  },
});
