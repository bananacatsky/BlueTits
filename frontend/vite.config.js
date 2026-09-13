import {defineConfig, loadEnv} from "vite";

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, "..", "");
  const setting = name => process.env[name] ?? env[name];
  const backendUrl = setting("BLUETITS_BACKEND_URL") || "http://127.0.0.1:8000";
  const requestedBase = setting("VITE_BASE_PATH") || "/";
  const base = `/${requestedBase.replace(/^\/+|\/+$/g, "")}/`.replace(/^\/\/$/, "/");

  return {
    appType: "spa",
    base,
    envDir: "..",
    build: {
      outDir: setting("VITE_OUT_DIR") || "dist",
      emptyOutDir: true,
      rollupOptions: {
        external: id => id === "react" || id.startsWith("react/") ||
          id === "react-dom" || id.startsWith("react-dom/")
      }
    },
    server: {
      host: setting("FRONTEND_HOST") || "127.0.0.1",
      port: Number(setting("FRONTEND_PORT") || 5173),
      strictPort: true,
      proxy: {
        "/api": {
          target: backendUrl,
          changeOrigin: true
        }
      }
    }
  };
});
