"""Root test configuration for the split test tree.

The test suite is grouped into cost- and subsystem-named directories
(`difftastic/`, `gumtree/`, `rendering/`, `server/`, `slow/`,
`integration/`) selected by path. Pytest inserts this conftest's directory
into `sys.path`, which is what lets tests in those subdirectories import the
shared `helpers` module living beside this file. No fixtures are defined
here; directory-local fixtures belong to directory-local conftests.
"""
