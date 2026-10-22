import { defineConfig, mergeConfig } from "vite";
import { shared } from "./vite.shared";

export default mergeConfig(
  shared,
  defineConfig({
    build: { outDir: "dist/admin", rollupOptions: { input: "admin.html" } },
    server: { port: 5174, proxy: { "/api": "http://127.0.0.1:8099" } },
  }),
);
