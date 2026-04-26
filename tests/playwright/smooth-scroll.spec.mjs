import { test, expect } from "@playwright/test";

import {
  clickHunkButton,
  expectActiveHunk,
  expectSelectedHunkIndex,
  getScrollToCalls,
  getScrollY,
  installSlowSmoothScroll,
  openDirectFixtureDiff,
  sampleScrollPositions,
  waitForScrollSettled,
} from "./hunk-nav.helpers.mjs";

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

    await waitForScrollSettled(page);
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

    for (let index = 0; index <= clampedPair.first; index += 1) {
      await clickHunkButton(page, "next");
      await waitForScrollSettled(page);
    }
    await expectSelectedHunkIndex(page, clampedPair.first);
    const firstTailScrollY = await getScrollY(page);

    await clickHunkButton(page, "next");
    await expectSelectedHunkIndex(page, clampedPair.second);
    await waitForScrollSettled(page);
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
