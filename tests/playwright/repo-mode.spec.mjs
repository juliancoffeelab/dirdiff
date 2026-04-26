import { test, expect } from "@playwright/test";

import {
  clickHunkButton,
  createTempRepoFixture,
  expectActiveHunk,
  expectSelectedHunkRows,
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
