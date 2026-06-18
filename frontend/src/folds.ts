import type { DiffRow, FoldHint } from "./api";

type NormalizedFoldHint = {
  startRow: number;
  endRow: number;
  kind: FoldHint["kind"];
  label: string;
  children: NormalizedFoldHint[];
};

export type FoldRow = {
  status: "fold";
  startRow: number;
  count: number;
  foldedRows: RenderRow[];
  kind: FoldHint["kind"];
  label: string;
};

export type RenderRow = DiffRow | FoldRow;

export function parseFoldHints(
  foldHints: FoldHint[] | undefined,
  rowCount: number,
  aggressiveFolds: boolean,
): NormalizedFoldHint[] {
  if (foldHints === undefined || foldHints.length === 0) {
    return [];
  }
  if (!Array.isArray(foldHints)) {
    throw new Error("Fold hints must be an array.");
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

export function addFoldRows(
  rows: DiffRow[],
  foldHints: FoldHint[] | undefined,
  aggressiveFolds: boolean,
): RenderRow[] {
  const parsed = parseFoldHints(foldHints, rows.length, aggressiveFolds);
  if (!parsed.length) {
    return rows;
  }

  return addFoldRowsInRange(rows, parsed, 0, rows.length);
}

function addFoldRowsInRange(
  rows: DiffRow[],
  foldHints: NormalizedFoldHint[],
  startRow: number,
  endRow: number,
): RenderRow[] {
  const result: RenderRow[] = [];
  let cursor = startRow;

  for (const hint of foldHints) {
    result.push(...rows.slice(cursor, hint.startRow));
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

export function isFoldRow(row: RenderRow): row is FoldRow {
  return row.status === "fold";
}
