"""Preset fixture structure checks.

Preset directories are source-file fixtures consumed by golden and projector
tests. This module verifies fixture shape: exactly one old File, exactly one
new File, a coherent format transition, a standard helper Makefile, and cheap
parser validity where a parser is available. It does not assert rendered diff
output; that belongs to the golden and logic test modules.
"""

import subprocess
from pathlib import Path

PRESETS_ROOT = Path(__file__).parents[1] / "presets"
"""Fixture catalog root whose two-sided cases this module validates.

All discovery stays below this checked-in tree; temporary or golden output is
not fixture input.
"""
REPO_ROOT = Path(__file__).parents[2]
"""Project root used to load the installed frontend TypeScript parser.

The parser check runs Node here so the module path matches the frontend's actual
dependency rather than a separate global compiler.
"""
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
"""Exact human-facing helper commands every two-sided preset provides.

Keeping one checked value prevents fixture-local command drift; the Makefile is
for manual inspection and snapshot generation, not part of diff behavior.
"""


def _preset_dirs() -> list[Path]:
    """Return two-sided fixture cases in stable catalog/group/case order.

    Addition and deletion fixtures intentionally have one side and are outside
    these paired-file and Makefile checks.
    """
    return sorted(
        path
        for path in PRESETS_ROOT.glob("*/*/*")
        if path.is_dir()
        and list(path.glob("old.*")) != []
        and list(path.glob("new.*")) != []
    )


def test_presets_have_old_and_new_files() -> None:
    """Every two-sided fixture has one unambiguous old/new File pair.

    Ordinary pairs keep the same extension. A symbolic-link transition may use
    different suffixes because its captured mode, not its filename, selects the
    link format. Extra old/new matches remain ambiguous for every format.
    """
    preset_dirs = _preset_dirs()
    assert preset_dirs != []
    for preset_dir in preset_dirs:
        old_files = sorted(preset_dir.glob("old.*"))
        new_files = sorted(preset_dir.glob("new.*"))

        assert len(old_files) == 1, preset_dir
        assert len(new_files) == 1, preset_dir
        assert (
            old_files[0].suffix == new_files[0].suffix
            or old_files[0].is_symlink()
            or new_files[0].is_symlink()
        ), preset_dir


def test_presets_have_standard_makefiles() -> None:
    """Every two-sided fixture exposes the same manual inspection commands.

    Exact equality keeps snapshot, external-engine, and display helpers aligned
    across catalogs instead of letting copied fixtures drift.
    """
    preset_dirs = _preset_dirs()
    assert preset_dirs != []
    for preset_dir in preset_dirs:
        makefile = preset_dir / "Makefile"

        assert makefile.read_text() == EXPECTED_PRESET_MAKEFILE, preset_dir


def test_python_presets_compile() -> None:
    """Python fixture sources remain parseable inputs rather than syntax damage.

    Compilation reads every fixture but executes none of it, keeping this a
    source-validity check rather than a behavioral test.
    """
    for path in sorted(PRESETS_ROOT.glob("**/*.py")):
        compile(path.read_text(), str(path), "exec")


def test_typescript_presets_parse() -> None:
    """TypeScript and TSX fixtures parse with the frontend compiler version.

    The check reports every diagnostic in one run so malformed fixture source
    cannot masquerade as a renderer failure.
    """
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
