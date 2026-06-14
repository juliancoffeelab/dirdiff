"""Preset fixture hygiene.

These tests check that each difftastic preset directory is a valid source-file
fixture: one old file, one new file, matching extensions, and parseable source.

Actual snapshot tests are in ./test_difftastic_golden.py.
Or if you want unit tests ./test_difftastic_logic.py
"""

import subprocess
from pathlib import Path

PRESETS_ROOT = Path(__file__).parent / "presets" / "difftastic"
REPO_ROOT = Path(__file__).parents[1]


def test_difftastic_presets_have_old_and_new_files() -> None:
    preset_dirs = sorted(
        path for path in PRESETS_ROOT.iterdir() if path.is_dir()
    )

    assert preset_dirs
    for preset_dir in preset_dirs:
        old_files = sorted(preset_dir.glob("old.*"))
        new_files = sorted(preset_dir.glob("new.*"))

        assert len(old_files) == 1, preset_dir
        assert len(new_files) == 1, preset_dir
        assert old_files[0].suffix == new_files[0].suffix


def test_python_difftastic_presets_compile() -> None:
    for path in sorted(PRESETS_ROOT.glob("**/*.py")):
        compile(path.read_text(), str(path), "exec")


def test_typescript_difftastic_presets_parse() -> None:
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
