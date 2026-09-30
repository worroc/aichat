import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const SERVER = "http://127.0.0.1:8765";
const API_PREFIXES = ["/ops", "/rooms", "/dm", "/questions", "/messages", "/inbox", "/me", "/directory", "/health"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(API_PREFIXES.map((p) => [p, { target: SERVER, changeOrigin: true }])),
  },
});
