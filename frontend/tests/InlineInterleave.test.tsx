import { cleanup, render } from "@solidjs/testing-library";
import { afterEach, expect, test, vi } from "vitest";
import type { DiffRow } from "../src/api/api";
import { TextDiffGrid } from "../src/hud/fileCard/grids/text/TextDiffGrid";

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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("inline interleave splits a merged shared part around an insertion", () => {
  // Git printed ` x a `, `+X`, `  b` for "x a b" -> "x a X b". The backend
  // weaves the two unchanged records into one left part because nothing sits
  // between them there, while the right side keeps them apart around `X`.
  const decoration = {
    syntax_classes: [],
    is_whitespace: false,
    is_leading_whitespace: false,
  };
  const rows: DiffRow[] = [
    {
      status: "equal",
      left_no: 1,
      right_no: 1,
      left_text: "x a  b",
      right_text: "x a X b",
      left_parts: [{ ...decoration, text: "x a  b", diff_status: "unchanged" }],
      right_parts: [
        { ...decoration, text: "x a ", diff_status: "unchanged" },
        { ...decoration, text: "X", diff_status: "insert" },
        { ...decoration, text: " b", diff_status: "unchanged" },
      ],
      hunk_index: 0,
    },
  ];
  // jsdom lacks ResizeObserver; the grid observes width for move outlines only.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  const mounted = render(() => (
    <TextDiffGrid
      reviewFile={{ left_path: "old.txt", right_path: "new.txt" }}
      fileIndex={0}
      displayName="fixture"
      bayKey="flatfile"
      contentLabel="Code"
      leftLabel="old"
      rightLabel="new"
      rows={rows}
      foldHints={[]}
      interleaveInlineRows={true}
      viewMode="inline"
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
  const code = root.querySelectorAll(".inline-line-code");
  expect(code).toHaveLength(1);
  const marker = code[0]!.querySelector(".inline-marker")!.textContent!;
  expect(code[0]!.textContent!.slice(marker.length)).toBe("x a X b");
  expect(
    [...code[0]!.querySelectorAll(".token-changed")].map((span) => [
      span.className,
      span.textContent,
    ]),
  ).toEqual([["token-changed token-insert", "X"]]);
});
