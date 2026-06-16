import type { DiffRow, FoldHint } from "./api";

type NormalizedFoldHint = {
  startRow: number;
  endRow: number;
  label: string;
};

export type FoldRow = {
  status: "fold";
  count: number;
  foldedRows: DiffRow[];
  label: string;
};

export type RenderRow = DiffRow | FoldRow;

export function parseFoldHints(
  foldHints: FoldHint[] | undefined,
  rowCount: number,
): NormalizedFoldHint[] {
  if (foldHints === undefined || foldHints.length === 0) {
    return [];
  }
  if (!Array.isArray(foldHints)) {
    throw new Error("Fold hints must be an array.");
  }

  const parsed = foldHints.map((hint, index) =>
    parseFoldHint(hint, index, rowCount),
  );

  let previousEnd = 0;
  parsed.forEach((hint, index) => {
    if (hint.startRow < previousEnd) {
      throw new Error(`Fold hint ${index} is out of order or overlaps.`);
    }
    previousEnd = hint.endRow;
  });

  return parsed;
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
    label: hint.label,
  };
}

export function addFoldRows(
  rows: DiffRow[],
  foldHints: FoldHint[] | undefined,
): RenderRow[] {
  const parsed = parseFoldHints(foldHints, rows.length);
  if (!parsed.length) {
    return rows;
  }

  const result: RenderRow[] = [];
  let cursor = 0;

  for (const hint of parsed) {
    result.push(...rows.slice(cursor, hint.startRow));
    const foldedRows = rows.slice(hint.startRow, hint.endRow);
    result.push({
      status: "fold",
      count: foldedRows.length,
      foldedRows,
      label: hint.label,
    });
    cursor = hint.endRow;
  }

  result.push(...rows.slice(cursor));
  return result;
}

export function isFoldRow(row: RenderRow): row is FoldRow {
  return row.status === "fold";
}
