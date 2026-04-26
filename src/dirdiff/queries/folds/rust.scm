(function_item
  name: (identifier) @fold.label
  body: (block) @fold)

(impl_item
  type: (_) @fold.label
  body: (declaration_list) @fold)

(trait_item
  name: (type_identifier) @fold.label
  body: (declaration_list) @fold)

(array_expression) @fold
