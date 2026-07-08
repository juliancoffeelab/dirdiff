"""Preset fixture structure checks.

Preset directories are source-file fixtures consumed by golden and projector
tests.  This module verifies fixture shape: exactly one old file, exactly one
new file, matching extensions, a standard helper Makefile, and cheap parser
validity where a parser is available.  It does not assert rendered diff output;
that belongs to the golden and logic test modules.
"""

import subprocess
from pathlib import Path

PRESETS_ROOT = Path(__file__).parent / "presets"
REPO_ROOT = Path(__file__).parents[1]
EXPECTED_PRESET_MAKEFILE = """OLD := $(firstword $(wildcard old.*))
NEW := $(firstword $(wildcard new.*))

.PHONY: diff full gum json show

diff:
\tdifft $(OLD) $(NEW)

full:
\tdiff $(OLD) $(NEW)

gum:
\tgumtree webdiff --port $${GUM_PORT:-4567} $(OLD) $(NEW) & \\
\tpid=$$!; \\
\ttrap 'kill $$pid 2>/dev/null || true' INT TERM EXIT; \\
\tsleep 1; \\
\turl=http://127.0.0.1:$${GUM_PORT:-4567}/; \\
\tif command -v open >/dev/null 2>&1; then open $$url; \\
\telif command -v xdg-open >/dev/null 2>&1; then xdg-open $$url; \\
\telse printf 'GumTree webdiff: %s\\n' $$url; fi; \\
\twait $$pid

json:
\t@DFT_UNSTABLE=yes difft --display json --context 100000000 $(OLD) $(NEW)

show:
\tbat $(OLD) $(NEW)
"""

__all__: list[str] = []


def _preset_dirs() -> list[Path]:
    return sorted(
        path
        for path in PRESETS_ROOT.glob("*/*/*")
        if path.is_dir()
        and list(path.glob("old.*")) != []
        and list(path.glob("new.*")) != []
    )


def test_presets_have_old_and_new_files() -> None:
    preset_dirs = _preset_dirs()
    assert preset_dirs != []
    for preset_dir in preset_dirs:
        old_files = sorted(preset_dir.glob("old.*"))
        new_files = sorted(preset_dir.glob("new.*"))

        assert len(old_files) == 1, preset_dir
        assert len(new_files) == 1, preset_dir
        assert old_files[0].suffix == new_files[0].suffix


def test_presets_have_standard_makefiles() -> None:
    preset_dirs = _preset_dirs()
    assert preset_dirs != []
    for preset_dir in preset_dirs:
        makefile = preset_dir / "Makefile"

        assert makefile.read_text() == EXPECTED_PRESET_MAKEFILE, preset_dir


def test_python_presets_compile() -> None:
    for path in sorted(PRESETS_ROOT.glob("**/*.py")):
        compile(path.read_text(), str(path), "exec")


def test_typescript_presets_parse() -> None:
    files = sorted(
        [
            *PRESETS_ROOT.glob("**/*.ts"),
            *PRESETS_ROOT.glob("**/*.tsx"),
        ]
    )
    script = """
const fs = require("fs");
const ts = require("./frontend/node_modules/typescript");
let failed = false;
for (const file of process.argv.slice(1)) {
  const result = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    reportDiagnostics: true,
    compilerOptions: {
      jsx: ts.JsxEmit.Preserve,
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  });
  const diagnostics = result.diagnostics || [];
  if (diagnostics.length) {
    failed = true;
    console.error(file);
    for (const diagnostic of diagnostics) {
      console.error(ts.flattenDiagnosticMessageText(diagnostic.messageText, "\\n"));
    }
  }
}
process.exit(failed ? 1 : 0);
"""

    subprocess.run(
        ["node", "-e", script, *[str(path) for path in files]],
        cwd=REPO_ROOT,
        check=True,
    )
