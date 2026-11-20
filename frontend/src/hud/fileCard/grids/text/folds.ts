import type { DiffRow, FoldHint } from "../../../../api/api";
import { assert, expect } from "../../../../utils";

type NormalizedFoldHint = {
  startRow: number;
  endRow: number;
  kind: FoldHint["kind"];
  label: string;
  children: NormalizedFoldHint[];
};

export type FoldRow<TRow extends DiffRow = DiffRow> = {
  status: "fold";
  startRow: number;
  count: number;
  foldedRows: RenderRow<TRow>[];
  kind: FoldHint["kind"];
  label: string;
};

export type RenderRow<TRow extends DiffRow = DiffRow> = TRow | FoldRow<TRow>;

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

export function isFoldRow<TRow extends DiffRow>(
  row: RenderRow<TRow>,
): row is FoldRow<TRow> {
  return row.status === "fold";
}
