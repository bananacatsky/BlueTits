import {defineConfig} from "vite";

const backendUrl = process.env.BLUETITS_BACKEND_URL || "http://127.0.0.1:8000";
const requestedBase = process.env.VITE_BASE_PATH || "/";
const base = `/${requestedBase.replace(/^\/+|\/+$/g, "")}/`.replace(/^\/\/$/, "/");

export default defineConfig({
  appType: "spa",
  base,
  build: {
    outDir: process.env.VITE_OUT_DIR || "dist",
    emptyOutDir: true,
    rollupOptions: {
      external: id => id === "react" || id.startsWith("react/") ||
        id === "react-dom" || id.startsWith("react-dom/")
    }
  },
  server: {
    host: process.env.FRONTEND_HOST || "127.0.0.1",
    port: Number(process.env.FRONTEND_PORT || 5173),
    strictPort: true,
    proxy: {
      "/api": {
        target: backendUrl,
        changeOrigin: true
      }
    }
  }
});
