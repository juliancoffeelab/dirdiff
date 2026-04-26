(function_declaration
  name: (identifier) @fold.label
  body: (statement_block) @fold)

(lexical_declaration
  (variable_declarator
    name: (identifier) @fold.label
    value: (function_expression
      body: (statement_block) @fold)))

(lexical_declaration
  (variable_declarator
    name: (identifier) @fold.label
    value: (arrow_function
      body: (statement_block) @fold)))

(variable_declaration
  (variable_declarator
    name: (identifier) @fold.label
    value: (arrow_function
      body: (statement_block) @fold)))

(class_declaration
  name: (identifier) @fold.label
  body: (class_body) @fold)

(method_definition
  name: (property_identifier) @fold.label
  body: (statement_block) @fold)

(method_definition
  name: (private_property_identifier) @fold.label
  body: (statement_block) @fold)

[
  (object)
  (array)
] @fold
