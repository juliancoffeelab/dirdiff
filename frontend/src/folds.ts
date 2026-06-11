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

export function normalizeFoldHints(
  foldHints: FoldHint[] | undefined,
  rowCount: number,
): NormalizedFoldHint[] {
  if (!Array.isArray(foldHints) || foldHints.length === 0) {
    return [];
  }

  return foldHints
    .map((hint) => ({
      startRow: Number(hint.start_row),
      endRow: Number(hint.end_row),
      label: String(hint.label || "").trim(),
    }))
    .filter(
      (hint) =>
        Number.isInteger(hint.startRow) &&
        Number.isInteger(hint.endRow) &&
        hint.startRow >= 0 &&
        hint.endRow <= rowCount &&
        hint.endRow > hint.startRow,
    )
    .sort(
      (left, right) =>
        left.startRow - right.startRow || left.endRow - right.endRow,
    );
}

export function addFoldRows(
  rows: DiffRow[],
  foldHints: FoldHint[] | undefined,
): RenderRow[] {
  const normalized = normalizeFoldHints(foldHints, rows.length);
  if (!normalized.length) {
    return rows;
  }

  const result: RenderRow[] = [];
  let cursor = 0;

  for (const hint of normalized) {
    if (hint.startRow < cursor) {
      continue;
    }

    result.push(...rows.slice(cursor, hint.startRow));
    const foldedRows = rows.slice(hint.startRow, hint.endRow);
    if (foldedRows.length) {
      result.push({
        status: "fold",
        count: foldedRows.length,
        foldedRows,
        label: hint.label,
      });
    }
    cursor = hint.endRow;
  }

  result.push(...rows.slice(cursor));
  return result;
}

export function isFoldRow(row: RenderRow): row is FoldRow {
  return row.status === "fold";
}
