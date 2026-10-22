import { createHash } from "node:crypto";
import { defineConfig, mergeConfig, type Plugin } from "vite";
import { shared } from "./vite.shared";

/** Names the service worker's cache after this build's hashed asset files. */
const assetCacheName: Plugin = {
  name: "myha-asset-cache-name",
  generateBundle(_, bundle) {
    const assets = Object.keys(bundle).filter((name) => name.startsWith("assets/")).sort();
    const hash = createHash("sha256").update(assets.join("\n")).digest("hex").slice(0, 12);
    const sw = bundle["sw.js"];
    if (sw?.type !== "chunk" || !sw.code.includes("__MYHA_ASSET_CACHE__")) this.error("sw.js lacks its cache placeholder");
    sw.code = sw.code.replace("__MYHA_ASSET_CACHE__", `myha-assets-${hash}`);
  },
};

export default mergeConfig(
  shared,
  defineConfig({
    plugins: [assetCacheName],
    // The public listener always serves guests at the origin root; absolute asset URLs keep
    // deep links such as /stays/42 working. The admin bundle keeps "./" for the ingress prefix.
    base: "/",
    build: {
      outDir: "dist/guest",
      emptyOutDir: true,
      rollupOptions: {
        // The service worker is its own entry at /sw.js: its scope is the whole origin, and its URL must not change.
        input: { index: "index.html", sw: "src/sw.ts" },
        output: { entryFileNames: (chunk) => (chunk.name === "sw" ? "sw.js" : "assets/[name]-[hash].js") },
      },
    },
  }),
);
