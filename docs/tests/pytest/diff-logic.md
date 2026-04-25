# Pytest Diff Logic Tests

Source:

- [`tests/test_diff_logic.py`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/test_diff_logic.py)

## Why This Layer Exists

These tests cover Python-side diff behavior that should not depend on browser automation:

- summary counting
- tokenization and syntax highlighting payloads
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

`test_tree_sitter_highlights_multiline_python_strings`

- What it tests: multiline Python strings keep string token classes across lines.
- How it tests it: builds a Python diff with a triple-quoted string and inspects `left_syntax`.
- Why it exists: protects multiline syntax-highlighting payloads.

`test_builds_direct_file_diff`

- What it tests: direct file mode produces the expected display name and summary counts.
- How it tests it: writes two temporary text files and asks `TextDiffService` to diff them.
- Why it exists: protects the non-git direct-file path.

`test_builds_whole_repo_diff_by_default`

- What it tests: repo discovery plus default whole-repo diff behavior.
- How it tests it: creates a temporary git repo with one modified file and one untracked file, then calls `TextDiffService.discover()`.
- Why it exists: protects the default “show me the repo diff” behavior.

`test_detects_git_reported_repo_renames`

- What it tests: git-reported renames are preserved in the diff payload.
- How it tests it: creates a temporary repo, commits a file, renames it with `git mv`, and builds a `head` vs `worktree` diff.
- Why it exists: protects repo rename metadata in the API response.
