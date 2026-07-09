export type InlineToken = {
  text: string;
  is_ws: boolean;
  status: "unchanged" | "replace" | "insert" | "delete";
};
