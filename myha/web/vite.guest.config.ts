import { defineConfig, mergeConfig } from "vite";
import { shared } from "./vite.shared";

export default mergeConfig(
  shared,
  defineConfig({
    build: { outDir: "dist/guest", emptyOutDir: true, rollupOptions: { input: "index.html" } },
  }),
);
