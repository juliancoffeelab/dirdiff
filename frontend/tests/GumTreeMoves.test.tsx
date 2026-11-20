import { cleanup, render, fireEvent } from "@solidjs/testing-library";
import { afterEach, expect, test, vi } from "vitest";
import type { DiffRow } from "../src/api/api";
import { TextDiffGrid } from "../src/hud/fileCard/grids/text/TextDiffGrid";

// Review markers are outside these properties; all diff facts come from the
// generator, not from this mock. Retain the real marker-coordinate schema.
vi.mock("../src/hud/review/Review", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/hud/review/Review")>()),
  useReview: () => ({
    snapshotId: "1".repeat(32),
    markerRevision: () => 0,
    changedMarkerKeys: () => null,
    markerState: () => ({ disabled: false, markers: [] }),
    closeAnchoredUi: () => {},
  }),
}));

vi.mock("../src/hud/navigation", () => ({
  useNavigation: () => ({ navigate: vi.fn() }),
}));
vi.mock("../src/comp/Toasts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/comp/Toasts")>()),
  useToasts: () => ({ showError: vi.fn() }),
}));

const fixtures = import.meta.glob<string>("./fixtures/gumtree/*/*.json", {
  eager: true,
  query: "?raw",
  import: "default",
});

// Dispose component observers before restoring browser APIs after each case.
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.head.querySelector("[data-move-test]")?.remove();
});

test.each(
  Object.entries(fixtures).flatMap(([name, json]) =>
    (["split", "inline"] as const).flatMap((view) =>
      [false, true].map((splitParts) => ({ name, json, view, splitParts })),
    ),
  ),
)(
  "$name $view splitParts=$splitParts preserves exact coverage and holes through hover",
  ({ json, view, splitParts }) => {
    const source: DiffRow[] = JSON.parse(json);
    // Expected coverage comes from the backend before any DOM construction or
    // partition transformation: each entry is side, line, character, move pair.
    const expected: string[] = [];
    const relationships = new Map<
      string,
      NonNullable<DiffRow["left_parts"][number]["move"]>
    >();
    for (const row of source) {
      for (const side of ["left", "right"] as const) {
        let column = 0;
        for (const part of side === "left" ? row.left_parts : row.right_parts) {
          const characters = [...part.text];
          for (const characterIndex of characters.keys()) {
            if (part.move !== undefined) {
              expect(part.move).toBeDefined();
              const key = [part.move!.left, part.move!.right]
                .flatMap((span) => [
                  span.start_line,
                  span.start_column,
                  span.end_line,
                  span.end_column,
                ])
                .join(":");
              relationships.set(key, part.move);
              expected.push(
                `${side}:${side === "left" ? row.left_no : row.right_no}:${column + characterIndex}:${key}`,
              );
            }
          }
          column += characters.length;
        }
      }
    }
    const rows = splitParts
      ? source.map((row) => ({
          ...row,
          left_parts: row.left_parts.flatMap((part) =>
            [...part.text].map((text) => ({ ...part, text })),
          ),
          right_parts: row.right_parts.flatMap((part) =>
            [...part.text].map((text) => ({ ...part, text })),
          ),
        }))
      : source;
    // jsdom has no layout. Each line is 22px high and each Unicode character is
    // 1px wide; side offsets keep opposite source coordinates distinguishable.
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    const style = document.createElement("style");
    style.dataset.moveTest = "";
    style.textContent = ".line-code { line-height: 18px; }";
    document.head.append(style);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        return this.matches(".line-code")
          ? new DOMRect(
              this.dataset.sourceSide === "right" ? 10000 : 0,
              Number(this.dataset.sourceLine) * 22,
              1000,
              22,
            )
          : new DOMRect(0, 0, 20000, 10000);
      },
    );
    vi.spyOn(HTMLElement.prototype, "getClientRects").mockImplementation(
      function (this: HTMLElement) {
        const code = this.closest<HTMLElement>(".line-code")!;
        const preceding = document.createRange();
        preceding.setStart(code, 0);
        preceding.setEndBefore(this);
        const marker = code.querySelector(".inline-marker")?.textContent ?? "";
        const column = [...preceding.toString()].length - [...marker].length;
        const rect = new DOMRect(
          (code.dataset.sourceSide === "right" ? 10000 : 0) + column,
          Number(code.dataset.sourceLine) * 22 + 2,
          [...this.textContent!].length,
          18,
        );
        return Object.assign([rect], {
          item: (index: number) => (index === 0 ? rect : null),
        });
      },
    );
    const mounted = render(() => (
      <TextDiffGrid
        reviewFile={{ left_path: "old.tsx", right_path: "new.tsx" }}
        fileIndex={0}
        displayName="fixture"
        bayKey="flatfile"
        contentLabel="Code"
        leftLabel="old"
        rightLabel="new"
        rows={rows}
        foldHints={[]}
        interleaveInlineRows={false}
        viewMode={view}
        aggressiveFolds={false}
        linePins={{
          parseUrl: () => ({ state: "none" }),
          toggleUrlState: () => {
            throw new Error("Unexpected pin activation");
          },
          restore: () => {
            throw new Error("Unexpected pin restoration");
          },
        }}
      />
    ));
    const root = mounted.container;
    expect(root.querySelector('[role="alert"]')).toBeNull();
    const before: string[] = [];
    for (const rect of root.querySelectorAll<SVGRectElement>(
      ".move-outlines rect",
    )) {
      const group = rect.parentElement!;
      const side = group.dataset.moveSide!;
      const column =
        Number(rect.getAttribute("x")) - (side === "right" ? 10000 : 0);
      const line = Number(rect.getAttribute("y")) / 22;
      for (
        let offset = 0;
        offset < Number(rect.getAttribute("width"));
        offset += 1
      ) {
        before.push(
          `${side}:${line}:${column + offset}:${group.dataset.moveKey}`,
        );
      }
    }
    expect([...new Set(before)].sort()).toEqual([...new Set(expected)].sort());
    const geometry = [...root.querySelectorAll(".move-outlines rect")].map(
      (rect) => rect.outerHTML,
    );
    // Every trigger is exercised, including inserted/updated descendants with
    // containing-move metadata and moves nested inside a different move.
    for (const trigger of root.querySelectorAll<HTMLElement>(".token-moved")) {
      fireEvent.pointerOver(trigger);
      expect(root.querySelector(".move-enclosure")).toBeNull();
      expect(
        [...root.querySelectorAll(".move-outlines rect")].map(
          (rect) => rect.outerHTML,
        ),
      ).toEqual(geometry);
      const parent = relationships.get(trigger.dataset.moveKey!)!;
      const side = trigger.dataset.moveSide === "left" ? "left" : "right";
      const expectedKeys = new Set<string>();
      for (const [key, relation] of relationships) {
        const candidate = relation[side];
        const range = parent[side];
        const beginsInside =
          candidate.start_line > range.start_line ||
          (candidate.start_line === range.start_line &&
            candidate.start_column >= range.start_column);
        const endsInside =
          candidate.end_line < range.end_line ||
          (candidate.end_line === range.end_line &&
            candidate.end_column <= range.end_column);
        if (beginsInside && endsInside) {
          expectedKeys.add(key);
        }
      }
      for (const group of root.querySelectorAll<SVGGElement | HTMLElement>(
        ".move-outlines g, .token-moved",
      )) {
        expect(group.classList.contains("move-highlighted")).toBe(
          expectedKeys.has(group.dataset.moveKey!),
        );
      }
      fireEvent.pointerOut(trigger);
      expect(root.querySelector(".move-highlighted")).toBeNull();
      expect(
        [...root.querySelectorAll(".move-outlines rect")].map(
          (rect) => rect.outerHTML,
        ),
      ).toEqual(geometry);
    }
  },
);
