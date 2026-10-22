import react from "@vitejs/plugin-react";
import type { UserConfig } from "vite";

export const shared: UserConfig = {
  // Relative asset URLs: the admin bundle runs under an arbitrary Home Assistant ingress prefix.
  base: "./",
  plugins: [react()],
  // es2019 for older Fire tablet WebViews; do not raise. No data: URIs: the gateway CSP (default-src 'self') would block inlined fonts.
  build: { target: "es2019", assetsInlineLimit: 0 },
  server: { proxy: { "/api": "http://127.0.0.1:8443" } },
};
