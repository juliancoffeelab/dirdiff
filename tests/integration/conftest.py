"""Integration-test import path setup.

Integration tests import shared helpers from the top-level `tests` package
while pytest starts collection inside `tests/integration`.  This file only
adds the tests root to `sys.path` for that collection boundary.  It must not
define behavioral fixtures or application state.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
"""Top-level test package path needed by integration-test helper imports.

Pytest may collect with the integration directory first on `sys.path`; this
stable absolute value is inserted once so `tests.helpers` remains importable.
"""
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

__all__: list[str] = []
