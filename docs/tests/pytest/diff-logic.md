# Pytest Diff Logic Tests

Source:

- [`tests/test_diff_logic.py`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/test_diff_logic.py)

## Why This Layer Exists

These tests cover Python-side diff behavior that should not depend on browser automation:

- summary counting
- tokenization and syntax highlighting payloads
- tree-sitter fold-hint generation and DRAISS-style fold precedence
- identifier-aware inline diff token boundaries
- direct-file diff building
- whole-repo default behavior
- rename detection

This layer protects the data that the frontend eventually renders.

## How These Tests Work

- They call `build_loaded_diff()` and `TextDiffService` directly.
- They use `tmp_path` and temporary git repositories for repo-mode behavior.
- They assert returned diff payload fields rather than scraping rendered HTML.

## Covered Tests

`test_counts_whitespace_only_changes_as_modified`

- What it tests: whitespace-only changes count as modified lines.
- How it tests it: builds a diff between indentation variants of the same line.
- Why it exists: keeps summary counters and token payloads honest for indentation-only edits.

`test_inline_diff_keeps_camel_case_boundaries_intact`

- What it tests: inline diff tokenization keeps camel-case identifier boundaries intact during single-token replacements.
- How it tests it: builds a JavaScript diff between `findNearestIndex(...)` and `positionsSignature(...)` and asserts that the diff payload splits those names into stable identifier segments instead of arbitrary character fragments.
- Why it exists: protects the identifier-aware tokenization fallback that keeps intraline highlights readable for renamed symbols.

`test_inline_diff_keeps_identifier_parts_whole_in_method_renames`

- What it tests: identifier-aware inline diff keeps known identifier parts whole instead of tearing them into character noise during method renames.
- How it tests it: builds a JavaScript diff between `expectActiveHunk(...)` and `expectSelectedHunkIndex(...)` and asserts that the payload keeps `Active`, `Selected`, `Hunk`, and `Index` as whole identifier parts.
- Why it exists: protects the backend regression where camel-case splitting still fell back to char-level fragments inside a single renamed part, producing unreadable intraline highlights.

`test_tree_sitter_highlights_multiline_python_strings`

- What it tests: multiline Python strings keep string token classes across lines.
- How it tests it: builds a Python diff with a triple-quoted string and inspects `left_syntax`.
- Why it exists: protects multiline syntax-highlighting payloads.

`test_builds_direct_file_diff`

- What it tests: direct file mode produces the expected display name and summary counts.
- How it tests it: writes two temporary text files and asks `TextDiffService` to diff them.
- Why it exists: protects the non-git direct-file path.

`test_fold_hints_include_unchanged_top_level_function_body`

- What it tests: an unchanged top-level Python function folds as a whole body when later lines in the file change.
- How it tests it: diffs a file where only a trailing assignment changes and asserts the returned `fold_hints`.
- Why it exists: protects the core DRAISS-style whole-function folding rule.

`test_changed_top_level_function_does_not_fold_descendants`

- What it tests: a changed function blocks all foldable descendants inside it.
- How it tests it: diffs a function whose return statement changes while an inner dict stays textually unchanged.
- Why it exists: protects the hard descendant-blocking rule for changed functions.

`test_fold_hints_include_unchanged_top_level_dict_body`

- What it tests: an unchanged top-level multiline Python dict folds as one container.
- How it tests it: changes a later scalar line and asserts a single dict-body fold hint.
- Why it exists: protects top-level container folding outside class bodies.

`test_unchanged_top_level_class_folds_methods_but_not_whole_class`

- What it tests: an unchanged class does not fold as a whole and instead emits method-level folds.
- How it tests it: changes a trailing line outside the class and inspects the method-only fold hints.
- Why it exists: protects the class special-case that differs from generic outermost folding.

`test_changed_class_still_folds_only_unchanged_methods`

- What it tests: a changed class still folds unchanged methods while leaving changed methods expanded.
- How it tests it: changes one method body and asserts only the unchanged method produces a fold hint.
- Why it exists: protects DRAISS-style class-member folding precedence.

`test_whitespace_only_changes_block_folding`

- What it tests: indentation-only changes still count as changed for fold eligibility.
- How it tests it: diffs tab-indented and space-indented versions of the same function body.
- Why it exists: keeps fold eligibility aligned with the existing whitespace-sensitive diff model.

`test_javascript_classes_fold_unchanged_methods_only`

- What it tests: JavaScript class behavior matches the Python class semantics.
- How it tests it: diffs a JS class with an unrelated trailing change and asserts only its unchanged method folds.
- Why it exists: protects cross-language consistency for class-like bodies.

`test_rust_impl_blocks_fold_unchanged_methods_only`

- What it tests: Rust `impl` member folding follows the same class-like rule as classes.
- How it tests it: changes a trailing constant and asserts an unchanged impl method folds while the impl body itself does not.
- Why it exists: protects the class-like policy for Rust member containers.

`test_json_unchanged_nested_top_level_container_folds`

- What it tests: unchanged JSON object containers still fold when the root document changes elsewhere.
- How it tests it: changes a sibling scalar key and inspects the nested object fold hint.
- Why it exists: protects config-style container folding for JSON.

`test_yaml_unchanged_nested_top_level_container_folds`

- What it tests: unchanged YAML mapping bodies fold when another top-level key changes.
- How it tests it: changes one scalar value while keeping a sibling mapping unchanged.
- Why it exists: protects config-style container folding for YAML.

`test_toml_unchanged_top_level_table_folds`

- What it tests: unchanged TOML tables fold when later top-level pairs change.
- How it tests it: changes a top-level scalar outside the table and inspects the resulting fold hint.
- Why it exists: protects table folding for TOML.

`test_markdown_unchanged_heading_section_folds_under_heading`

- What it tests: an unchanged Markdown heading section folds only the rows under the heading.
- How it tests it: changes a later sibling section and asserts the first section produces a fold hint whose label is the visible heading text.
- Why it exists: protects the Markdown-specific rule that keeps heading rows visible while collapsing only unchanged section bodies.

`test_markdown_changed_parent_section_allows_unchanged_child_heading_fold`

- What it tests: a changed parent Markdown section can still expose an unchanged child heading fold.
- How it tests it: changes text in the parent section, keeps a nested child heading section unchanged, and inspects the child-only fold hint.
- Why it exists: protects nested heading precedence so changed outer sections do not block unchanged descendant sections.

`test_markdown_added_later_sibling_section_keeps_prior_section_folded`

- What it tests: adding a later sibling heading section does not unroll an earlier unchanged heading section.
- How it tests it: inserts a new same-level section between an unchanged intro section and a changed tail section, then asserts the intro fold hint is still emitted.
- Why it exists: protects the regression where later Markdown section inserts could accidentally suppress earlier unchanged section folds.

`test_markdown_added_sibling_section_keeps_all_prior_unchanged_sections_folded`

- What it tests: inserting a new same-level Markdown section does not suppress fold hints for earlier unchanged sibling sections.
- How it tests it: adds a new section between two unchanged heading sections and asserts both older sections still emit fold hints.
- Why it exists: protects the multi-section regression where only the later unchanged section stayed folded after a new sibling section was inserted.

`test_markdown_non_heading_content_does_not_fold`

- What it tests: Markdown only folds heading sections and does not independently fold other block types.
- How it tests it: diffs a fenced code block plus a changed trailing line and asserts no fold hints are returned.
- Why it exists: keeps the Markdown v1 policy narrow and predictable.

`test_builds_whole_repo_diff_by_default`

- What it tests: repo discovery plus default whole-repo diff behavior.
- How it tests it: creates a temporary git repo with one modified file and one untracked file, then calls `TextDiffService.discover()`.
- Why it exists: protects the default “show me the repo diff” behavior.

`test_detects_git_reported_repo_renames`

- What it tests: git-reported renames are preserved in the diff payload.
- How it tests it: creates a temporary repo, commits a file, renames it with `git mv`, and builds a `head` vs `worktree` diff.
- Why it exists: protects repo rename metadata in the API response.
