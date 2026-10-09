import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end test against a running, seeded stack.
 *  - Docker: `make test` runs it from the frontend-test service with E2E_BASE_URL=http://frontend:3000.
 *  - Local: `npx playwright test` reuses servers on :3000/:8000 or starts them (python ../dev.py api / npm run dev).
 * Seed first (`make seed` / `python dev.py seed`) so there are open RFQ lines.
 */
const external = process.env.E2E_BASE_URL;
const isWin = process.platform === "win32";
const python = isWin ? "..\\backend\\.venv\\Scripts\\python.exe" : "../backend/.venv/bin/python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: external ?? "http://localhost:3000",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: external
    ? undefined
    : [
        { command: `${python} ../dev.py api`, url: "http://localhost:8000/api/health", reuseExistingServer: true, timeout: 120_000 },
        { command: "npm run dev", url: "http://localhost:3000/login", reuseExistingServer: true, timeout: 180_000 },
      ],
});
