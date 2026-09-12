import {defineConfig} from "vite";

const backendUrl = process.env.BLUETITS_BACKEND_URL || "http://127.0.0.1:8000";

export default defineConfig({
  appType: "spa",
  build: {
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
