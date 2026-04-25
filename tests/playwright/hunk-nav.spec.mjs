import { test, expect } from "@playwright/test";

import {
  clickHunkButton,
  createTempRepoFixture,
  expectActiveHunk,
  expectSelectedHunkRows,
  getActiveHunkIndex,
  getScrollToCalls,
  getScrollY,
  installSlowSmoothScroll,
  openDirectFixtureDiff,
  pressKeyBurst,
  sampleScrollPositions,
  startTempRepoServer,
} from "./hunk-nav.helpers.mjs";

test("next hunk moves to the first hunk in direct-file mode", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(3);
    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 0);
  } finally {
    await fixture.cleanup();
  }
});

test("selected hunk is highlighted subtly on both panes", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 0);

    await expect(page.locator(".diff-row.active-hunk")).toHaveCount(2);
    await expect(page.locator('.diff-row.active-hunk[aria-current="true"]')).toHaveCount(2);
    await expect(page.locator('.diff-row[data-hunk-index="0"].active-hunk')).toHaveCount(2);
  } finally {
    await fixture.cleanup();
  }
});

test("previous hunk moves backward from the middle hunk", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next", { count: 2 });
    await expectActiveHunk(page, 1);

    await clickHunkButton(page, "prev");
    await expectActiveHunk(page, 0);
  } finally {
    await fixture.cleanup();
  }
});

test("next hunk wraps after the final hunk settles at the bottom of the page", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next", { count: 3 });
    await expectActiveHunk(page, 2);

    await page.waitForTimeout(400);
    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 0);
  } finally {
    await fixture.cleanup();
  }
});

test("previous hunk wraps after the first hunk settles at the top of the page", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 0);

    await page.waitForTimeout(400);
    await clickHunkButton(page, "prev");
    await expectActiveHunk(page, 2);
  } finally {
    await fixture.cleanup();
  }
});

test("next hunk queues correctly while smooth scrolling is still in flight", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page);
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next");
    await page.waitForTimeout(1_000);
    await clickHunkButton(page, "next");
    await page.waitForTimeout(2_000);

    await expectActiveHunk(page, 1);
  } finally {
    await fixture.cleanup();
  }
});

test("previous hunk queues correctly while smooth scrolling is still in flight", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page);
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next", { count: 2 });
    await expectActiveHunk(page, 1);

    await clickHunkButton(page, "prev");
    await page.waitForTimeout(1_000);
    await clickHunkButton(page, "prev");
    await page.waitForTimeout(2_000);

    await expectActiveHunk(page, 2);
  } finally {
    await fixture.cleanup();
  }
});

test("rapid next bursts land on the sequential wrapped target", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page);
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next", { count: 5 });
    await page.waitForTimeout(2_200);

    await expectActiveHunk(page, 1);
  } finally {
    await fixture.cleanup();
  }
});

test("rapid prev bursts land on the sequential wrapped target", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page);
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "prev", { count: 5 });
    await page.waitForTimeout(2_200);

    await expectActiveHunk(page, 1);
  } finally {
    await fixture.cleanup();
  }
});

test("alternating rapid bursts preserve move ordering", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page);
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "prev");
    await clickHunkButton(page, "next");
    await clickHunkButton(page, "prev");
    await clickHunkButton(page, "next");
    await clickHunkButton(page, "next");
    await page.waitForTimeout(2_200);

    await expectActiveHunk(page, 1);
  } finally {
    await fixture.cleanup();
  }
});

test("keyboard shortcuts navigate next and previous hunks", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await pressKeyBurst(page, "n");
    await expectActiveHunk(page, 0);

    await pressKeyBurst(page, "n");
    await expectActiveHunk(page, 1);

    await pressKeyBurst(page, "Shift+N");
    await expectActiveHunk(page, 0);
  } finally {
    await fixture.cleanup();
  }
});

test("navigation is a no-op when there are no hunks", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page);
  const fixture = await openDirectFixtureDiff(page, baseURL, { identical: true });

  try {
    await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(0);

    await clickHunkButton(page, "next");
    await clickHunkButton(page, "prev");
    await pressKeyBurst(page, "n");
    await pressKeyBurst(page, "Shift+N");

    expect(await getActiveHunkIndex(page)).toBe(-1);
    expect(await getScrollY(page)).toBe(0);
    expect(await getScrollToCalls(page)).toHaveLength(0);
  } finally {
    await fixture.cleanup();
  }
});

test("smooth scrolling progresses over time and finishes at the correct target", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page, { durationMs: 1600 });
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next");

    const samples = await sampleScrollPositions(page, {
      samples: 7,
      intervalMs: 180,
    });
    const rounded = samples.map((value) => Math.round(value));
    const uniqueRounded = [...new Set(rounded)];
    const scrollCalls = await getScrollToCalls(page);

    expect(scrollCalls).toHaveLength(1);
    expect(scrollCalls[0].behavior).toBe("smooth");
    expect(uniqueRounded.length).toBeGreaterThan(3);
    expect(rounded[0]).toBeLessThan(rounded.at(-1));
    expect(rounded[1]).toBeLessThan(scrollCalls[0].top);

    await page.waitForTimeout(500);
    await expectActiveHunk(page, 0);
    expect(Math.abs((await getScrollY(page)) - scrollCalls[0].top)).toBeLessThan(120);
  } finally {
    await fixture.cleanup();
  }
});

test("rapid repeated input during smooth scrolling does not snap back to the wrong target", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page, { durationMs: 1600 });
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await clickHunkButton(page, "next", { count: 3 });
    const samples = await sampleScrollPositions(page, {
      samples: 6,
      intervalMs: 160,
    });

    expect(samples.at(-1)).toBeGreaterThan(samples[0]);

    await page.waitForTimeout(1_200);
    await expectActiveHunk(page, 2);
  } finally {
    await fixture.cleanup();
  }
});

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
