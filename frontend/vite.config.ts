import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API is proxied so the browser sees one origin: HttpOnly SameSite cookies and the
// CSRF double-submit cookie work without cross-site requests.
const API = process.env.NB_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/v1": { target: API, changeOrigin: false } },
  },
  preview: { port: 4173, proxy: { "/v1": { target: API, changeOrigin: false } } },
});
