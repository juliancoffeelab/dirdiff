import { test, expect } from "@playwright/test";

import {
  clickHunkButton,
  createTempRepoFixture,
  expectActiveHunk,
  expectSelectedHunkIndex,
  expectSelectedHunkRows,
  getActiveHunkIndex,
  getSelectedHunkIndex,
  getScrollToCalls,
  getScrollY,
  installSlowSmoothScroll,
  openDirectFixtureDiff,
  pressKeyBurst,
  sampleScrollPositions,
  scrollToY,
  startTempRepoServer,
  waitForScrollSettled,
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

test("keyboard hunk shortcuts are ignored while an input is focused", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    const pathInput = page.locator("#pathInput");
    await pathInput.focus();
    await pathInput.press("n");
    await pathInput.press("Shift+N");

    expect(await getSelectedHunkIndex(page)).toBeNull();
    expect(await getScrollY(page)).toBe(0);
    await expect(pathInput).toHaveValue("nN");

    const leftFileInput = page.locator("#leftFileInput");
    await leftFileInput.focus();
    await leftFileInput.press("n");

    expect(await getSelectedHunkIndex(page)).toBeNull();
    expect(await getScrollY(page)).toBe(0);
    await expect(leftFileInput).toHaveValue(/n$/);
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

test("manual scroll starting points navigate relative to the visible anchor position", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL, {
    totalLines: 520,
    changedLines: [90, 210, 330, 450],
  });

  try {
    const positions = await page.evaluate(() => {
      return Array.from(document.querySelectorAll(".diff-row.hunk-anchor"))
        .map((row) => row.getBoundingClientRect().top + window.scrollY);
    });
    const middleCurrentPosition = (positions[1] + positions[2]) / 2;
    await scrollToY(page, middleCurrentPosition - 120);
    await waitForScrollSettled(page);

    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 2);
    await expectSelectedHunkIndex(page, 2);

    await scrollToY(page, middleCurrentPosition - 120);
    await waitForScrollSettled(page);

    await clickHunkButton(page, "prev");
    await expectActiveHunk(page, 1);
    await expectSelectedHunkIndex(page, 1);
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

test("single-hunk diffs stay pinned to the only hunk across wraps and rapid input", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page, { durationMs: 1200 });
  const fixture = await openDirectFixtureDiff(page, baseURL, {
    totalLines: 260,
    changedLines: [180],
  });

  try {
    await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(1);

    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 0);
    await expectSelectedHunkIndex(page, 0);

    await clickHunkButton(page, "prev", { count: 3 });
    await page.waitForTimeout(1_500);
    await expectActiveHunk(page, 0);
    await expectSelectedHunkIndex(page, 0);

    await clickHunkButton(page, "next", { count: 4 });
    await page.waitForTimeout(1_500);
    await expectActiveHunk(page, 0);
    await expectSelectedHunkIndex(page, 0);
  } finally {
    await fixture.cleanup();
  }
});

test("later single-file tail hunks stay distinct even when bottom clamping stops scroll movement", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL, {
    totalLines: 620,
    changedLines: [120, 540, 590, 615],
  });

  try {
    await page.setViewportSize({ width: 1280, height: 1040 });
    await waitForScrollSettled(page);

    const clampedPair = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll(".diff-row.hunk-anchor"));
      const maxScrollTop = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
      const tail = rows.map((row, index) => {
        const position = row.getBoundingClientRect().top + window.scrollY;
        return {
          index,
          targetTop: Math.min(Math.max(position - 120, 0), maxScrollTop),
        };
      });
      for (let index = tail.length - 2; index >= 0; index -= 1) {
        if (Math.round(tail[index].targetTop) === Math.round(tail[index + 1].targetTop)) {
          return {
            first: tail[index].index,
            second: tail[index + 1].index,
          };
        }
      }
      return null;
    });

    expect(clampedPair).not.toBeNull();

    await clickHunkButton(page, "next", { count: clampedPair.first + 1 });
    await expectSelectedHunkIndex(page, clampedPair.first);
    const firstTailScrollY = await getScrollY(page);

    await clickHunkButton(page, "next");
    await expectSelectedHunkIndex(page, clampedPair.second);
    const secondTailScrollY = await getScrollY(page);

    expect(Math.abs(firstTailScrollY - secondTailScrollY)).toBeLessThan(120);
  } finally {
    await fixture.cleanup();
  }
});

test("resizing during smooth scroll preserves the selected target and later navigation", async ({ page, baseURL }) => {
  await installSlowSmoothScroll(page, { durationMs: 1600 });
  const fixture = await openDirectFixtureDiff(page, baseURL, {
    totalLines: 520,
    changedLines: [120, 240, 360, 480],
  });

  try {
    await clickHunkButton(page, "next");
    await page.waitForTimeout(350);
    await page.setViewportSize({ width: 1280, height: 540 });
    await waitForScrollSettled(page);

    await expectSelectedHunkIndex(page, 0);

    await clickHunkButton(page, "next");
    await waitForScrollSettled(page);
    await expectSelectedHunkIndex(page, 1);
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
