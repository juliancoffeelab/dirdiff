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
