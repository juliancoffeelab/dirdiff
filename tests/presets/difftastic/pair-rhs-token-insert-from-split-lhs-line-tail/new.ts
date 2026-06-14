import { createEffect } from "solid-js";
import type {
  DiffRow,
  FileEntry,
  InlineToken,
  RowStatus,
  SyntaxSpan,
} from "./api";
import { addFoldRows, isFoldRow, type FoldRow, type RenderRow } from "./folds";
