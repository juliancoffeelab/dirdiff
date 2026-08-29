/**
 * Validates backend fold hints and applies them to immutable diff rows.
 *
 * The pure builders reject invalid, crossing, or hunk-containing ranges before
 * returning nested `FoldRow` values in source order. The aggressive policy only
 * decides which valid hints begin folded.
 *
 * Mutable fold disclosure belongs to TextDiffGrid. This module neither changes
 * backend rows nor touches DOM or navigation state.
 */
import type { DiffRow, FoldHint } from "../../../../api/api";
import { assert, expect } from "../../../../utils";

/**
 * Represents one validated backend fold range in the nested fold tree.
 *
 * Row bounds use the half-open backend interval. Children are wholly contained
 * ranges; crossing or out-of-bounds hints are rejected before this shape is
 * returned and the shape never represents UI expansion state.
 */
type NormalizedFoldHint = {
  /**
   * Zero-based inclusive index of the first source row in this fold.
   *
   * It is validated against the complete row count before nesting and remains the
   * original backend coordinate after child ranges are attached.
   */
  startRow: number;
  /**
   * Zero-based exclusive index immediately after this fold's last source row.
   *
   * It is strictly greater than `startRow`, no greater than the source row count,
   * and wholly contains every child interval.
   */
  endRow: number;
  /**
   * Backend structural classification controlling fold presentation policy.
   *
   * Aggressive-fold filtering happens before this normalized node is returned;
   * the value itself remains unchanged for TextDiffGrid labels and classes.
   */
  kind: FoldHint["kind"];
  /**
   * Backend-authored text describing the folded source construct.
   *
   * The parser validates that a string exists but does not derive or rewrite it.
   */
  label: string;
  /**
   * Direct wholly contained child folds in source order.
   *
   * Nesting mutates this private array during construction; returned trees have
   * no crossing or sibling overlap and contain no UI expansion state.
   */
  children: NormalizedFoldHint[];
};

/**
 * Replaces one contiguous group of backend rows in TextDiffGrid's render input.
 *
 * It retains the complete nested content and source position needed to replace
 * the edge with its ordinary rows. The value is immutable render input, not a
 * mutable fold controller.
 */
export type FoldRow<TRow extends DiffRow = DiffRow> = {
  /**
   * Discriminant separating a synthetic fold edge from backend DiffRows.
   *
   * Rendering and `isFoldRow` rely on this exact literal; backend row statuses
   * never use it.
   */
  status: "fold";
  /**
   * Original zero-based source-row index at which the fold edge is inserted.
   *
   * Expanding the edge restores rows beginning at this exact position.
   */
  startRow: number;
  /**
   * Number of original source rows represented by this fold edge.
   *
   * It equals the validated half-open range length, including rows inside nested
   * child folds.
   */
  count: number;
  /**
   * Complete ordered render sequence hidden behind this fold edge.
   *
   * It may contain nested FoldRows but never drops or reorders backend rows.
   */
  foldedRows: RenderRow<TRow>[];
  /**
   * Backend structural classification retained for fold presentation.
   *
   * The frontend does not reclassify the hidden source from row contents.
   */
  kind: FoldHint["kind"];
  /**
   * Backend-authored description shown on the fold edge.
   *
   * It is carried through unchanged from the validated hint.
   */
  label: string;
};

/**
 * Describes one row accepted by the rendering kernel after fold construction.
 *
 * Callers must discriminate FoldRow through `isFoldRow`; ordinary members are
 * the validated backend DiffRow values and retain their original order.
 */
export type RenderRow<TRow extends DiffRow = DiffRow> = TRow | FoldRow<TRow>;

/**
 * Validates, filters, orders, and nests the complete backend fold-hint list.
 *
 * Callers must supply the required array from FileDiff, the exact source row
 * count, and the active aggressive-fold policy. Invalid or crossing ranges
 * throw; an empty or fully filtered list returns an empty result.
 *
 * @param foldHints Complete backend hint list for one text bay.
 * @param rowCount Exact number of backend rows those hint coordinates address.
 * @param aggressiveFolds Whether class-like and top-level hints remain eligible.
 */
export function parseFoldHints(
  foldHints: FoldHint[],
  rowCount: number,
  aggressiveFolds: boolean,
): NormalizedFoldHint[] {
  if (foldHints.length === 0) {
    return [];
  }

  const parsed = foldHints
    .filter(
      (hint) =>
        aggressiveFolds ||
        (hint.kind !== "class_like" && hint.kind !== "top_level"),
    )
    .map((hint, index) => parseFoldHint(hint, index, rowCount))
    .sort(
      (left, right) =>
        left.startRow - right.startRow || right.endRow - left.endRow,
    );

  return nestFoldHints(parsed);
}

/**
 * Converts one backend hint into a validated half-open source-row range.
 *
 * The caller supplies the hint's original list index for precise contract
 * errors. The returned node has no children; `parseFoldHints` passes all
 * validated nodes to `nestFoldHints` before exposing the result.
 *
 * @param hint Backend hint whose bounds and label are validated.
 * @param index Original hint-list position used in contract diagnostics.
 * @param rowCount Exact source-row count limiting the half-open range.
 */
function parseFoldHint(
  hint: FoldHint,
  index: number,
  rowCount: number,
): NormalizedFoldHint {
  assert(
    Number.isInteger(hint.start_row),
    `Fold hint ${index} has invalid start row.`,
  );
  assert(
    Number.isInteger(hint.end_row),
    `Fold hint ${index} has invalid end row.`,
  );
  assert(
    hint.start_row >= 0,
    `Fold hint ${index} starts before the first row.`,
  );
  assert(
    hint.end_row <= rowCount,
    `Fold hint ${index} ends after the last row.`,
  );
  assert(
    hint.end_row > hint.start_row,
    `Fold hint ${index} has an empty row range.`,
  );
  assert(
    typeof hint.label === "string",
    `Fold hint ${index} is missing a label.`,
  );
  return {
    startRow: hint.start_row,
    endRow: hint.end_row,
    kind: hint.kind,
    label: hint.label,
    children: [],
  };
}

/**
 * Builds a containment tree from hints sorted by start row and widest-first.
 *
 * Sibling ranges may touch but must not overlap, while child ranges must be
 * completely contained by their parent. Any crossing range throws.
 */
function nestFoldHints(hints: NormalizedFoldHint[]): NormalizedFoldHint[] {
  const roots: NormalizedFoldHint[] = [];
  const stack: NormalizedFoldHint[] = [];

  hints.forEach((hint, index) => {
    while (stack.length > 0) {
      const parent = stack[stack.length - 1];
      if (parent === undefined || hint.startRow < parent.endRow) {
        break;
      }
      stack.pop();
    }

    const parent = stack[stack.length - 1];
    if (parent === undefined) {
      roots.push(hint);
    } else if (hint.endRow <= parent.endRow) {
      parent.children.push(hint);
    } else {
      assert(false, `Fold hint ${index} crosses another fold hint.`);
    }

    stack.push(hint);
  });

  return roots;
}

/**
 * Transforms validated DiffRows into a mixed sequence containing FoldRows.
 *
 * Callers provide the required backend hint list and active fold policy. The
 * source rows and hints are not mutated; no applicable hints returns the exact
 * original row array.
 *
 * @param rows Complete validated backend rows in authoritative order.
 * @param foldHints Complete backend hints addressing that row array.
 * @param aggressiveFolds Whether broad structural hints remain eligible.
 *
 * # Returns
 *
 * - Ordinary `DiffRow` entries retain their source identity and relative order
 *   wherever no applicable hint folds them.
 * - Each applicable interval becomes one `FoldRow` at the position of its first
 *   source row. Nested transformed rows remain inside that entry. With no
 *   applicable hints, the result is the original `rows` array by identity.
 */
export function addFoldRows<TRow extends DiffRow>(
  rows: TRow[],
  foldHints: FoldHint[],
  aggressiveFolds: boolean,
): RenderRow<TRow>[] {
  const parsed = parseFoldHints(foldHints, rows.length, aggressiveFolds);
  if (!parsed.length) {
    return rows;
  }

  return addFoldRowsInRange(rows, parsed, 0, rows.length);
}

/**
 * Transforms one validated source-row interval and its direct nested hints.
 *
 * The bounds are required half-open indices into `rows`. Each hint is emitted
 * exactly once and untouched rows retain their original order. A folded range
 * containing a backend hunk boundary violates the folded-context contract and
 * throws instead of hiding or manufacturing a hunk target.
 *
 * @param rows Complete source rows from which this interval is emitted.
 * @param foldHints Direct child hints wholly contained by the interval.
 * @param startRow Inclusive source-row bound for this recursive call.
 * @param endRow Exclusive source-row bound for this recursive call.
 *
 * # Returns
 *
 * - Ordinary `DiffRow` entries cover the parts of `[startRow, endRow)` outside
 *   direct folded intervals, preserving source identity and order.
 * - Each direct hint produces one `FoldRow` at the hint's start. Its
 *   `foldedRows` recursively represent that hint's complete interval, so no
 *   source row appears beside and inside the same fold.
 */
function addFoldRowsInRange<TRow extends DiffRow>(
  rows: TRow[],
  foldHints: NormalizedFoldHint[],
  startRow: number,
  endRow: number,
): RenderRow<TRow>[] {
  const result: RenderRow<TRow>[] = [];
  let cursor = startRow;

  for (const hint of foldHints) {
    result.push(...rows.slice(cursor, hint.startRow));
    for (let rowIndex = hint.startRow; rowIndex < hint.endRow; rowIndex += 1) {
      const row = rows[rowIndex];
      const sourceRow = expect(row, `Fold hint lost source row ${rowIndex}.`);
      assert(
        sourceRow.hunk_index === null,
        `Fold hint contains hunk ${sourceRow.hunk_index} at row ${rowIndex}.`,
      );
    }
    const foldedRows = addFoldRowsInRange(
      rows,
      hint.children,
      hint.startRow,
      hint.endRow,
    );
    result.push({
      status: "fold",
      startRow: hint.startRow,
      count: hint.endRow - hint.startRow,
      foldedRows,
      kind: hint.kind,
      label: hint.label,
    });
    cursor = hint.endRow;
  }

  result.push(...rows.slice(cursor, endRow));
  return result;
}

/**
 * Narrows a transformed render row to the FoldRow variant.
 *
 * Callers may rely on the literal `status` discriminator; the function neither
 * validates nor mutates ordinary DiffRows.
 */
export function isFoldRow<TRow extends DiffRow>(
  row: RenderRow<TRow>,
): row is FoldRow<TRow> {
  return row.status === "fold";
}
