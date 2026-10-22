import { defineConfig, mergeConfig } from "vite";
import { shared } from "./vite.shared";

export default mergeConfig(
  shared,
  defineConfig({
    publicDir: false, // public/ holds the guest PWA manifest and icon
    build: { outDir: "dist/admin", rollupOptions: { input: "admin.html" } },
    server: { port: 5174, proxy: { "/api": { target: "http://127.0.0.1:8099", ws: true } } },
  }),
);
