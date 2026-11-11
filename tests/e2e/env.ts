import path from "node:path";

/** Environment for the E2E stack: APP_ENV=test with the scripted LLM TEST DOUBLE. Never for real documents. */
export const ROOT = path.resolve(__dirname, "../..");
export const E2E_ENV: Record<string, string> = {
  APP_ENV: "test",
  LLM_PROVIDER: "scripted",
  DATABASE_URL: process.env.E2E_DATABASE_URL ?? "postgresql://nbkyc@127.0.0.1:54329/nbkyc_dev",
  SESSION_SECRET: "e2e-session-secret-not-for-production-000000",
  ENCRYPTION_KEY: "ZTJlLWtleS1ub3QtZm9yLXByb2R1Y3Rpb24tMzJieXQ",
  IDENTITY_HMAC_SECRET: "e2e-hmac-secret-not-for-production-0000000",
  STORAGE_DIR: process.env.E2E_STORAGE_DIR ?? path.join(ROOT, "backend", "var", "e2e-storage"),
};
