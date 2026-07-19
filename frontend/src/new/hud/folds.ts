/**
 * Transforms validated backend fold hints into nested render rows.
 *
 * The module exports the pure fold-row contracts and construction operations used
 * by DiffGrid. Callers provide immutable rows, backend hint ranges, and the
 * selected aggressive-fold policy. It validates range structure and must not own
 * expanded-fold UI state, mutate backend rows, touch the DOM, or navigate hunks.
 */
import type { DiffRow, FoldHint } from "../api/api";

/**
 * Represents one validated backend fold range in the nested fold tree.
 *
 * Row bounds use the half-open backend interval. Children are wholly contained
 * ranges; crossing or out-of-bounds hints are rejected before this shape is
 * returned and the shape never represents UI expansion state.
 */
type NormalizedFoldHint = {
  startRow: number;
  endRow: number;
  kind: FoldHint["kind"];
  label: string;
  children: NormalizedFoldHint[];
};

/**
 * Replaces one contiguous group of backend rows in DiffGrid's render input.
 *
 * `foldedRows` retains the complete nested content, `startRow` preserves its
 * original row identity, and `count` is the number of source rows represented.
 * The value is immutable fold-row data, not a mutable fold controller.
 */
export type FoldRow<TRow extends DiffRow = DiffRow> = {
  status: "fold";
  startRow: number;
  count: number;
  foldedRows: RenderRow<TRow>[];
  kind: FoldHint["kind"];
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
 * errors. The returned node has no children; `normalizeFoldHints` passes all
 * validated nodes to `nestFoldHints` before exposing the result.
 */
function parseFoldHint(
  hint: FoldHint,
  index: number,
  rowCount: number,
): NormalizedFoldHint {
  if (!Number.isInteger(hint.start_row)) {
    throw new Error(`Fold hint ${index} has invalid start row.`);
  }
  if (!Number.isInteger(hint.end_row)) {
    throw new Error(`Fold hint ${index} has invalid end row.`);
  }
  if (hint.start_row < 0) {
    throw new Error(`Fold hint ${index} starts before the first row.`);
  }
  if (hint.end_row > rowCount) {
    throw new Error(`Fold hint ${index} ends after the last row.`);
  }
  if (hint.end_row <= hint.start_row) {
    throw new Error(`Fold hint ${index} has an empty row range.`);
  }
  if (typeof hint.label !== "string") {
    throw new Error(`Fold hint ${index} is missing a label.`);
  }
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
      throw new Error(`Fold hint ${index} crosses another fold hint.`);
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
      if (row === undefined) {
        throw new Error(`Fold hint lost source row ${rowIndex}.`);
      }
      if (row.hunk_index !== null) {
        throw new Error(
          `Fold hint contains hunk ${row.hunk_index} at row ${rowIndex}.`,
        );
      }
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
