import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "../..");
const port = 5080;

export default defineConfig({
  testDir: __dirname,
  timeout: 30_000,
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    headless: true,
    trace: "on-first-retry"
  },
  webServer: {
    command: `uv run dirdiff --headless --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    cwd: repoRoot,
    env: {
      UV_CACHE_DIR: path.join(repoRoot, ".uv-cache")
    },
    reuseExistingServer: true,
    timeout: 30_000
  }
});
