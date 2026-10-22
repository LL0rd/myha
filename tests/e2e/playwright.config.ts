import { defineConfig, devices } from "@playwright/test";
import { PUBLIC_ORIGIN } from "./helpers/constants.mjs";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  globalSetup: "./helpers/gateway.ts",
  // One stack, one guest journey: the cases build on each other and end with the revocation.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: PUBLIC_ORIGIN,
    locale: "en-US",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
