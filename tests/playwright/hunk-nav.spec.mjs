import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const HUNK_SCROLL_MARGIN = 120;

async function writeFixtureFiles() {
  const fixtureDir = await fs.mkdtemp(path.join(os.tmpdir(), "dirdiff-hunk-nav-"));
  const leftPath = path.join(fixtureDir, "left.txt");
  const rightPath = path.join(fixtureDir, "right.txt");

  const leftLines = [];
  const rightLines = [];

  for (let lineNo = 1; lineNo <= 420; lineNo += 1) {
    const text = `line ${String(lineNo).padStart(4, "0")}`;
    leftLines.push(text);
    rightLines.push(text);
  }

  rightLines[119] = "line 0120 changed";
  rightLines[239] = "line 0240 changed";
  rightLines[359] = "line 0360 changed";

  await fs.writeFile(leftPath, `${leftLines.join("\n")}\n`, "utf8");
  await fs.writeFile(rightPath, `${rightLines.join("\n")}\n`, "utf8");

  return {
    fixtureDir,
    leftPath,
    rightPath
  };
}

async function getActiveHunkIndex(page) {
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

async function openFixtureDiff(page, baseURL) {
  const { fixtureDir, leftPath, rightPath } = await writeFixtureFiles();

  const query = new URLSearchParams({
    left_file: leftPath,
    right_file: rightPath
  });

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`${baseURL}/?${query.toString()}`);
  await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(3);

  return fixtureDir;
}

async function expectActiveHunk(page, index) {
  await expect.poll(() => getActiveHunkIndex(page)).toBe(index);
}

test("next hunk queues correctly while smooth scrolling is still in flight", async ({ page, baseURL }) => {
  await page.addInitScript(() => {
    const nativeScrollTo = window.scrollTo.bind(window);
    let animationFrame = 0;

    window.scrollTo = (optionsOrX, maybeY) => {
      if (
        typeof optionsOrX === "object"
        && optionsOrX !== null
        && optionsOrX.behavior === "smooth"
      ) {
        const targetTop = Math.max(Number(optionsOrX.top ?? window.scrollY), 0);
        const startTop = window.scrollY;
        const durationMs = 1600;
        const startedAt = performance.now();

        cancelAnimationFrame(animationFrame);

        const tick = (now) => {
          const progress = Math.min((now - startedAt) / durationMs, 1);
          const nextTop = startTop + ((targetTop - startTop) * progress);
          nativeScrollTo(0, nextTop);
          if (progress < 1) {
            animationFrame = requestAnimationFrame(tick);
          }
        };

        animationFrame = requestAnimationFrame(tick);
        return;
      }

      nativeScrollTo(optionsOrX, maybeY);
    };
  });

  const fixtureDir = await openFixtureDiff(page, baseURL);

  try {
    await page.getByRole("button", { name: /next hunk/i }).click();
    await page.waitForTimeout(1_000);
    await page.getByRole("button", { name: /next hunk/i }).click();
    await page.waitForTimeout(2_000);

    await expectActiveHunk(page, 1);
  } finally {
    await fs.rm(fixtureDir, { recursive: true, force: true });
  }
});

test("next hunk wraps after the final hunk settles at the bottom of the page", async ({ page, baseURL }) => {
  const fixtureDir = await openFixtureDiff(page, baseURL);

  try {
    await page.getByRole("button", { name: /next hunk/i }).click();
    await expectActiveHunk(page, 0);

    await page.getByRole("button", { name: /next hunk/i }).click();
    await expectActiveHunk(page, 1);

    await page.getByRole("button", { name: /next hunk/i }).click();
    await expectActiveHunk(page, 2);

    await page.waitForTimeout(400);
    await page.getByRole("button", { name: /next hunk/i }).click();

    await expectActiveHunk(page, 0);
  } finally {
    await fs.rm(fixtureDir, { recursive: true, force: true });
  }
});
