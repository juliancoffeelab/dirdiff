import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect } from "@playwright/test";

export const HUNK_SCROLL_MARGIN = 120;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "../..");

export async function installSlowSmoothScroll(page, { durationMs = 1600 } = {}) {
  await page.addInitScript(({ durationMs: animationDurationMs }) => {
    window.__hunkNavScrollCalls = [];

    const nativeScrollTo = window.scrollTo.bind(window);
    let animationFrame = 0;

    window.scrollTo = (optionsOrX, maybeY) => {
      if (typeof optionsOrX === "object" && optionsOrX !== null) {
        const targetTop = Math.max(Number(optionsOrX.top ?? window.scrollY), 0);
        const behavior = optionsOrX.behavior ?? "auto";
        window.__hunkNavScrollCalls.push({
          top: targetTop,
          behavior,
          at: performance.now()
        });

        if (behavior === "smooth") {
          const startTop = window.scrollY;
          const startedAt = performance.now();

          cancelAnimationFrame(animationFrame);

          const tick = (now) => {
            const progress = Math.min((now - startedAt) / animationDurationMs, 1);
            const nextTop = startTop + ((targetTop - startTop) * progress);
            nativeScrollTo(0, nextTop);
            if (progress < 1) {
              animationFrame = requestAnimationFrame(tick);
            }
          };

          animationFrame = requestAnimationFrame(tick);
          return;
        }

        nativeScrollTo(0, targetTop);
        return;
      }

      nativeScrollTo(optionsOrX, maybeY);
    };
  }, { durationMs });
}

export async function getActiveHunkIndex(page) {
  return page.evaluate((scrollMargin) => {
    const anchors = [...document.querySelectorAll(".diff-row.hunk-anchor")]
      .filter((row) => row instanceof HTMLElement && row.offsetParent !== null);
    const currentPosition = window.scrollY + scrollMargin;

    let closestIndex = -1;
    let closestDistance = Number.POSITIVE_INFINITY;

    anchors.forEach((row, index) => {
      const position = row.getBoundingClientRect().top + window.scrollY;
      const distance = Math.abs(position - currentPosition);
      if (distance < closestDistance) {
        closestIndex = index;
        closestDistance = distance;
      }
    });

    return closestIndex;
  }, HUNK_SCROLL_MARGIN);
}

export async function expectActiveHunk(page, index) {
  await expect.poll(() => getActiveHunkIndex(page)).toBe(index);
}

export async function expectSelectedHunkRows(page, index) {
  await expect(page.locator(".diff-row.active-hunk")).toHaveCount(2);
  await expect(page.locator('.diff-row.active-hunk[aria-current="true"]')).toHaveCount(2);
  await expect(page.locator(`.diff-row.active-hunk[data-hunk-index="${index}"]`)).toHaveCount(2);
}

export async function getSelectedHunkIndex(page) {
  return page.evaluate(() => {
    const row = document.querySelector(".diff-row.active-hunk");
    return row ? Number(row.dataset.hunkIndex) : null;
  });
}

export async function expectSelectedHunkIndex(page, index) {
  await expect.poll(() => getSelectedHunkIndex(page)).toBe(index);
  await expectSelectedHunkRows(page, index);
}

export async function expectMatchingRowTops(page, text, {
  tolerancePx = 1
} = {}) {
  const leftRow = page.locator(".diff-pane").nth(0).locator(".diff-row").filter({ hasText: text }).first();
  const rightRow = page.locator(".diff-pane").nth(1).locator(".diff-row").filter({ hasText: text }).first();

  await expect(leftRow).toHaveCount(1);
  await expect(rightRow).toHaveCount(1);

  await expect.poll(async () => {
    const [leftBox, rightBox] = await Promise.all([
      leftRow.boundingBox(),
      rightRow.boundingBox()
    ]);
    if (!leftBox || !rightBox) {
      return null;
    }
    return Math.abs(leftBox.y - rightBox.y);
  }).toBeLessThanOrEqual(tolerancePx);
}

export async function expectCodeTextAligned(page, leftSelector, rightSelector, {
  tolerancePx = 1
} = {}) {
  const left = page.locator(leftSelector);
  const right = page.locator(rightSelector);

  await expect(left).toHaveCount(1);
  await expect(right).toHaveCount(1);

  await expect.poll(async () => {
    const [leftBox, rightBox] = await Promise.all([
      left.boundingBox(),
      right.boundingBox()
    ]);
    if (!leftBox || !rightBox) {
      return null;
    }
    return Math.abs(leftBox.x - rightBox.x);
  }).toBeLessThanOrEqual(tolerancePx);
}

export async function getScrollToCalls(page) {
  return page.evaluate(() => window.__hunkNavScrollCalls || []);
}

export async function getScrollY(page) {
  return page.evaluate(() => window.scrollY);
}

export async function waitForScrollSettled(page, {
  intervalMs = 80,
  stableSamples = 4,
  attempts = 80,
} = {}) {
  let last = await getScrollY(page);
  let stable = 0;

  for (let index = 0; index < attempts; index += 1) {
    await page.waitForTimeout(intervalMs);
    const current = await getScrollY(page);
    if (Math.abs(current - last) < 1) {
      stable += 1;
      if (stable >= stableSamples) {
        return;
      }
    } else {
      stable = 0;
      last = current;
    }
  }

  throw new Error("Timed out waiting for scroll to settle");
}

export async function scrollToY(page, top) {
  await page.evaluate((nextTop) => {
    window.scrollTo({ top: nextTop, behavior: "auto" });
  }, top);
}

export async function sampleScrollPositions(page, {
  samples = 6,
  intervalMs = 160
} = {}) {
  const positions = [];

  for (let index = 0; index < samples; index += 1) {
    positions.push(await getScrollY(page));
    if (index < samples - 1) {
      await page.waitForTimeout(intervalMs);
    }
  }

  return positions;
}

export async function clickHunkButton(page, direction, {
  count = 1,
  delayMs = 0
} = {}) {
  const buttonName = direction === "next" ? /next hunk/i : /prev hunk/i;

  for (let index = 0; index < count; index += 1) {
    await page.getByRole("button", { name: buttonName }).click();
    if (delayMs && index < count - 1) {
      await page.waitForTimeout(delayMs);
    }
  }
}

export async function pressKeyBurst(page, key, {
  count = 1,
  delayMs = 0
} = {}) {
  for (let index = 0; index < count; index += 1) {
    await page.keyboard.press(key);
    if (delayMs && index < count - 1) {
      await page.waitForTimeout(delayMs);
    }
  }
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve(address.port);
      });
    });
    server.on("error", reject);
  });
}

async function waitForServer(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // Retry until ready.
    }

    await new Promise((resolve) => setTimeout(resolve, 200));
  }

  throw new Error(`Timed out waiting for server: ${url}`);
}

function runGit(args, cwd) {
  execFileSync("git", args, {
    cwd,
    stdio: "pipe",
  });
}

function defaultRepoFiles() {
  return [
    {
      name: "alpha.txt",
      prefix: "alpha",
      totalLines: 220,
      changedLines: [80],
    },
    {
      name: "beta.txt",
      prefix: "beta",
      totalLines: 220,
      changedLines: [180],
    },
  ];
}

export async function createTempRepoFixture({ files = defaultRepoFiles() } = {}) {
  const repoDir = await fs.mkdtemp(path.join(os.tmpdir(), "dirdiff-repo-nav-"));

  runGit(["init"], repoDir);
  runGit(["config", "user.name", "Playwright Test"], repoDir);
  runGit(["config", "user.email", "playwright@example.com"], repoDir);

  const fileStates = [];

  for (const file of files) {
    const lines = [];
    for (let lineNo = 1; lineNo <= file.totalLines; lineNo += 1) {
      lines.push(`${file.prefix} ${String(lineNo).padStart(4, "0")}`);
    }

    const filePath = path.join(repoDir, file.name);
    await fs.writeFile(filePath, `${lines.join("\n")}\n`, "utf8");
    fileStates.push({
      ...file,
      filePath,
      lines,
    });
  }

  runGit(["add", ...fileStates.map((file) => file.name)], repoDir);
  runGit(["commit", "-m", "initial"], repoDir);

  for (const file of fileStates) {
    file.changedLines.forEach((lineNo, index) => {
      file.lines[lineNo - 1] = `${file.prefix} ${String(lineNo).padStart(4, "0")} changed ${index + 1}`;
    });
    await fs.writeFile(file.filePath, `${file.lines.join("\n")}\n`, "utf8");
  }

  return {
    repoDir,
    async cleanup() {
      await fs.rm(repoDir, { recursive: true, force: true });
    }
  };
}

export async function startTempRepoServer(repoDir) {
  const port = await getFreePort();
  const url = `http://127.0.0.1:${port}`;
  const child = spawn(
    "uv",
    [
      "run",
      "dirdiff",
      "--headless",
      "--port",
      String(port),
      "--repo-root",
      repoDir,
    ],
    {
      cwd: projectRoot,
      env: {
        ...process.env,
        UV_CACHE_DIR: path.join(projectRoot, ".uv-cache"),
      },
      stdio: "pipe",
    },
  );

  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });

  try {
    await waitForServer(url);
  } catch (error) {
    child.kill("SIGTERM");
    throw new Error(`${error.message}\n${stderr}`);
  }

  return {
    url,
    async stop() {
      child.kill("SIGTERM");
      await new Promise((resolve) => {
        child.once("close", resolve);
      });
    }
  };
}
