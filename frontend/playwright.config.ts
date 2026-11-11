import Module from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));

// Specs live in ../tests/e2e; let them resolve packages from frontend/node_modules (works on Windows, no symlinks).
process.env.NODE_PATH = [path.resolve(here, "node_modules"), process.env.NODE_PATH].filter(Boolean).join(path.delimiter);
(Module as unknown as { _initPaths: () => void })._initPaths();

/**
 * E2E against a running stack (see docs/SETUP.md):
 *   API + worker in APP_ENV=test with the scripted LLM test double, and Vite on :5173.
 * The browser uses Playwright's Chromium, or set PW_CHANNEL=chrome to use installed Chrome.
 */
export default defineConfig({
  testDir: "../tests/e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel: process.env.PW_CHANNEL } }],
});
