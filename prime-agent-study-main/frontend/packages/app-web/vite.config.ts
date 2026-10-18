import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Same-origin strategy (Phase0): browser always calls `/api/v1/...`.
 * Vite proxies `/api` → supervisor (`/v1/...`).
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
