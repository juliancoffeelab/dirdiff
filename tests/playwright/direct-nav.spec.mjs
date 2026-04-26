import { test, expect } from "@playwright/test";

import {
  HUNK_SCROLL_MARGIN,
  clickHunkButton,
  expectActiveHunk,
  expectSelectedHunkIndex,
  getActiveHunkIndex,
  getScrollToCalls,
  getScrollY,
  installSlowSmoothScroll,
  openDirectFixtureDiff,
  pressKeyBurst,
  scrollToY,
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

test("keyboard shortcuts navigate next and previous hunks", async ({ page, baseURL }) => {
  const fixture = await openDirectFixtureDiff(page, baseURL);

  try {
    await page.locator("#nextHunkBtn").focus();

    await pressKeyBurst(page, "n");
    await waitForScrollSettled(page);
    await expectSelectedHunkIndex(page, 0);

    await pressKeyBurst(page, "n");
    await waitForScrollSettled(page);
    await expectSelectedHunkIndex(page, 1);

    await pressKeyBurst(page, "Shift+N");
    await waitForScrollSettled(page);
    await expectSelectedHunkIndex(page, 0);
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

    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    await expect(pathInput).toHaveValue("nN");

    const leftFileInput = page.locator("#leftFileInput");
    await leftFileInput.focus();
    await leftFileInput.press("n");

    expect(await page.evaluate(() => window.scrollY)).toBe(0);
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
    totalLines: 900,
    changedLines: [150, 350, 550, 750],
  });

  try {
    await page.setViewportSize({ width: 1280, height: 480 });
    await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(4);

    const findNavigationWindow = () => page.evaluate((scrollMargin) => {
      const positions = Array.from(document.querySelectorAll(".diff-row.hunk-anchor"))
        .map((row) => row.getBoundingClientRect().top + window.scrollY);
      const maxScrollTop = Math.max(
        document.documentElement.scrollHeight - window.innerHeight,
        0,
      );

      for (let index = 0; index < positions.length - 1; index += 1) {
        const midpoint = (positions[index] + positions[index + 1]) / 2;
        const scrollTop = midpoint - scrollMargin;
        if (scrollTop > 0 && scrollTop < maxScrollTop) {
          return {
            previousIndex: index,
            nextIndex: index + 1,
            scrollTop,
          };
        }
      }

      return null;
    }, HUNK_SCROLL_MARGIN);

    const nextWindow = await findNavigationWindow();
    expect(nextWindow).not.toBeNull();

    await scrollToY(page, nextWindow.scrollTop);
    await waitForScrollSettled(page);
    await page.mouse.click(200, 200);

    await pressKeyBurst(page, "n");
    await waitForScrollSettled(page);
    await expectSelectedHunkIndex(page, nextWindow.nextIndex);

    await page.reload();
    await expect(page.locator(".diff-row.hunk-anchor")).toHaveCount(4);
    const prevWindow = await findNavigationWindow();
    expect(prevWindow).not.toBeNull();

    await scrollToY(page, prevWindow.scrollTop);
    await waitForScrollSettled(page);
    await page.mouse.click(200, 200);

    await pressKeyBurst(page, "Shift+N");
    await waitForScrollSettled(page);
    await expectSelectedHunkIndex(page, prevWindow.previousIndex);
  } finally {
    await fixture.cleanup();
  }
});
