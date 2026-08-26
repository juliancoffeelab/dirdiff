const FoldHintSchema = z.strictObject({
  start_row: z.number().int(),
  end_row: z.number().int(),
  kind: z.enum(["function_like", "class_like", "container", "section"]),
  label: z.string(),
});
