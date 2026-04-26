import { test, expect } from "@playwright/test";

import {
  clickHunkButton,
  expectActiveHunk,
  expectCodeTextAligned,
  expectMatchingRowTops,
  openDirectTextDiff,
} from "./hunk-nav.helpers.mjs";

test("top-level unchanged classes show method folds instead of a whole-class fold", async ({ page, baseURL }) => {
  const fixture = await openDirectTextDiff(page, baseURL, {
    leftContent: [
      "class Example:",
      "    def a(self):",
      "        return 1",
      "",
      "    def b(self):",
      "        return 2",
      "",
      "value = 1",
      "",
    ].join("\n"),
    rightContent: [
      "class Example:",
      "    def a(self):",
      "        return 1",
      "",
      "    def b(self):",
      "        return 2",
      "",
      "value = 2",
      "",
    ].join("\n"),
  });

  try {
    await expect(page.locator(".fold-bar")).toHaveCount(4);
    await expect(page.locator(".fold-bar").filter({ hasText: "def a(self):" })).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "def b(self):" })).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "class Example:" })).toHaveCount(0);
  } finally {
    await fixture.cleanup();
  }
});

test("changed classes only fold unchanged methods", async ({ page, baseURL }) => {
  const fixture = await openDirectTextDiff(page, baseURL, {
    leftContent: [
      "class Example:",
      "    def a(self):",
      "        return 1",
      "",
      "    def b(self):",
      "        return 2",
      "",
      "value = 1",
      "",
    ].join("\n"),
    rightContent: [
      "class Example:",
      "    def a(self):",
      "        return 1",
      "",
      "    def b(self):",
      "        return 3",
      "",
      "value = 2",
      "",
    ].join("\n"),
  });

  try {
    await expect(page.locator(".fold-bar")).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "def a(self):" })).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "def b(self):" })).toHaveCount(0);
  } finally {
    await fixture.cleanup();
  }
});

test("fold bars and signature rows toggle collapsed regions without breaking hunk navigation", async ({ page, baseURL }) => {
  const fixture = await openDirectTextDiff(page, baseURL, {
    leftContent: [
      "def helper():",
      "    value = 1",
      "    return value",
      "",
      "x = 1",
      "",
    ].join("\n"),
    rightContent: [
      "def helper():",
      "    value = 1",
      "    return value",
      "",
      "x = 2",
      "",
    ].join("\n"),
  });

  try {
    await expect(page.locator(".fold-bar")).toHaveCount(2);
    await page.locator(".diff-row.fold-toggle-row").first().click();
    await expect(page.locator(".fold-bar")).toHaveCount(0);
    await expect(page.locator(".line-code", { hasText: "return value" })).toHaveCount(2);

    await page.locator(".diff-row.fold-toggle-row").first().click();
    await expect(page.locator(".fold-bar")).toHaveCount(2);

    await clickHunkButton(page, "next");
    await expectActiveHunk(page, 0);
  } finally {
    await fixture.cleanup();
  }
});

test("fold toggle icons do not shift top-level code horizontally", async ({ page, baseURL }) => {
  const fixture = await openDirectTextDiff(page, baseURL, {
    leftContent: [
      "def helper():",
      "    value = 1",
      "    return value",
      "",
      "x = 1",
      "",
    ].join("\n"),
    rightContent: [
      "def helper():",
      "    value = 1",
      "    return value",
      "",
      "x = 2",
      "",
    ].join("\n"),
  });

  try {
    await expect(page.locator(".fold-bar")).toHaveCount(2);
    await expectCodeTextAligned(
      page,
      ".diff-pane:nth-of-type(1) .diff-row.fold-toggle-row .line-code-content",
      ".diff-pane:nth-of-type(1) .diff-row.replace .line-code-content",
    );
  } finally {
    await fixture.cleanup();
  }
});

test("markdown folds only unchanged heading sections", async ({ page, baseURL }) => {
  const fixture = await openDirectTextDiff(page, baseURL, {
    leftName: "left.md",
    rightName: "right.md",
    leftContent: [
      "# Intro",
      "alpha",
      "beta",
      "",
      "# Tail",
      "one",
      "",
    ].join("\n"),
    rightContent: [
      "# Intro",
      "alpha",
      "beta",
      "",
      "# Tail",
      "two",
      "",
    ].join("\n"),
  });

  try {
    await expect(page.locator(".fold-bar")).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "# Intro" })).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "# Tail" })).toHaveCount(0);
  } finally {
    await fixture.cleanup();
  }
});

test("adding a later markdown section keeps earlier unchanged sections folded", async ({ page, baseURL }) => {
  const fixture = await openDirectTextDiff(page, baseURL, {
    leftName: "left.md",
    rightName: "right.md",
    leftContent: [
      "# One",
      "a1",
      "a2",
      "",
      "# Two",
      "b1",
      "b2",
      "",
      "# Tail",
      "one",
      "",
    ].join("\n"),
    rightContent: [
      "# One",
      "a1",
      "a2",
      "",
      "# Added",
      "new",
      "",
      "# Two",
      "b1",
      "b2",
      "",
      "# Tail",
      "two",
      "",
    ].join("\n"),
  });

  try {
    await expect(page.locator(".fold-bar")).toHaveCount(4);
    await expect(page.locator(".fold-bar").filter({ hasText: "# One" })).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "# Two" })).toHaveCount(2);
    await expect(page.locator(".fold-bar").filter({ hasText: "# Added" })).toHaveCount(0);
  } finally {
    await fixture.cleanup();
  }
});

test("collapsed folds keep later visible markdown headings aligned across panes", async ({ page, baseURL }) => {
  const fixture = await openDirectTextDiff(page, baseURL, {
    leftName: "left.md",
    rightName: "right.md",
    leftContent: [
      "# Intro",
      "alpha",
      "beta",
      "",
      "# Tail",
      "one",
      "",
    ].join("\n"),
    rightContent: [
      "# Intro",
      "alpha",
      "beta",
      "",
      "# Added",
      "new",
      "",
      "# Tail",
      "two",
      "",
    ].join("\n"),
  });

  try {
    await expect(page.locator(".fold-bar").filter({ hasText: "# Intro" })).toHaveCount(2);
    await expectMatchingRowTops(page, "# Tail");
  } finally {
    await fixture.cleanup();
  }
});
