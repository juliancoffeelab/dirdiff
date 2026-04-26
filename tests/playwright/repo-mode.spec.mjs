import { test, expect } from "@playwright/test";

import {
  clickHunkButton,
  createTempRepoFixture,
  expectActiveHunk,
  expectSelectedHunkIndex,
  expectSelectedHunkRows,
  installSlowSmoothScroll,
  startTempRepoServer,
} from "./hunk-nav.helpers.mjs";

test("repo mode navigates across file-card boundaries", async ({ page }) => {
  const repoFixture = await createTempRepoFixture();
  const server = await startTempRepoServer(repoFixture.repoDir);

  try {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(server.url);

    await expect(page.locator(".file-card")).toHaveCount(2);
    await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(2);

    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 0);

    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 1);
  } finally {
    await server.stop();
    await repoFixture.cleanup();
  }
});

test("repo mode keeps later global hunks selected in the final file", async ({ page }) => {
  const repoFixture = await createTempRepoFixture({
    files: [
      {
        name: "alpha.txt",
        prefix: "alpha",
        totalLines: 260,
        changedLines: [40, 120, 200],
      },
      {
        name: "beta.txt",
        prefix: "beta",
        totalLines: 420,
        changedLines: [300, 360, 405],
      },
    ],
  });
  const server = await startTempRepoServer(repoFixture.repoDir);

  try {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(server.url);

    await expect(page.locator(".file-card")).toHaveCount(2);
    await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(6);

    await clickHunkButton(page, "next", { count: 4 });
    await expectActiveHunk(page, 3);
    await expectSelectedHunkRows(page, 3);

    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 4);
    await expectSelectedHunkRows(page, 4);

    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 5);
    await expectSelectedHunkRows(page, 5);
  } finally {
    await server.stop();
    await repoFixture.cleanup();
  }
});

test("reloading the diff during in-flight scroll resets selection cleanly before the next navigation", async ({ page }) => {
  await installSlowSmoothScroll(page, { durationMs: 1800 });
  const repoFixture = await createTempRepoFixture({
    files: [
      {
        name: "alpha.txt",
        prefix: "alpha",
        totalLines: 260,
        changedLines: [40, 120],
      },
      {
        name: "beta.txt",
        prefix: "beta",
        totalLines: 320,
        changedLines: [150, 260],
      },
    ],
  });
  const server = await startTempRepoServer(repoFixture.repoDir);

  try {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(server.url);

    await expect(page.locator(".file-card")).toHaveCount(2);
    await clickHunkButton(page, "next");
    await page.waitForTimeout(250);

    await page.locator("#pathInput").fill("beta.txt");
    await expect(page.locator(".file-card")).toHaveCount(1);
    await expect(page.getByRole("heading", { name: "beta.txt" })).toHaveCount(1);
    await expect(page.locator(".diff-row.active-hunk")).toHaveCount(0);

    await clickHunkButton(page, "next");
    await expectSelectedHunkIndex(page, 0);
  } finally {
    await server.stop();
    await repoFixture.cleanup();
  }
});
