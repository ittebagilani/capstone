import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the socket to a backend on :8000 (uvicorn backend.app:app).
// `npm run build` writes dist/, which the backend serves itself at / — one process on the Pi.
export default defineConfig({
  plugins: [react()],
  server: { host: true, proxy: { "/ws": { target: "ws://localhost:8000", ws: true } } },
});
