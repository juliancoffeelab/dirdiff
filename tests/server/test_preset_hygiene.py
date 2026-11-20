import re
import subprocess
from pathlib import Path

PRESETS_ROOT = Path(__file__).parents[1] / "presets"
REPO_ROOT = Path(__file__).parents[2]
EXPECTED_PRESET_MAKEFILE = """OLD := $(firstword $(wildcard old.*))
NEW := $(firstword $(wildcard new.*))

.PHONY: difft git gum difft-json bat diffs

difft:
\tdifft $(OLD) $(NEW)

git:
\tgit --no-pager diff --no-index --color=always --no-color-moved $(OLD) $(NEW)

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

difft-json:
\tDFT_UNSTABLE=yes difft --display json --context 100000000 $(OLD) $(NEW)

bat:
\tbat $(OLD) $(NEW)

diffs:
\tdiffsitter --no-config $(OLD) $(NEW)
"""


def _preset_dirs() -> list[Path]:
    return sorted(
        path
        for path in PRESETS_ROOT.glob("*/*/*")
        if path.is_dir()
        and list(path.glob("old.*")) != []
        and list(path.glob("new.*")) != []
    )


def test_presets_have_no_readme_files() -> None:
    readmes = sorted(
        path.relative_to(PRESETS_ROOT).as_posix()
        for path in PRESETS_ROOT.rglob("*")
        if path.name.casefold() == "readme.md"
    )
    assert readmes == [], (
        "README.md is forbidden anywhere under tests/presets/. Presets must "
        "be understandable from their source pairs and runnable Makefiles; "
        "separate guides add reading overhead and duplicate information that "
        "can become stale. Agents: do not circumvent this rule in any way, "
        "including renaming or relocating the guide, disguising it as fixture "
        "content, using symlinks, adding exceptions, or weakening this check. "
        f"Remove these files: {readmes}"
    )


def test_presets_have_old_and_new_files() -> None:
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
    shared_targets = ["difft", "git", "gum", "difft-json", "bat", "diffs"]
    for makefile in PRESETS_ROOT.rglob("*Makefile"):
        assert not any(
            line.startswith("\t@") for line in makefile.read_text().splitlines()
        ), makefile
    for catalog in sorted(PRESETS_ROOT.iterdir()):
        if catalog.is_dir():
            assert (catalog / "preset.toml").is_file(), catalog

    preset_dirs = _preset_dirs()
    assert preset_dirs != []
    for preset_dir in preset_dirs:
        makefile = preset_dir / "Makefile"
        extension_file = preset_dir.parents[1] / "preset.Makefile"
        extensions = (
            extension_file.read_text() if extension_file.is_file() else ""
        )
        assert makefile.read_text() == EXPECTED_PRESET_MAKEFILE + extensions, (
            preset_dir
        )
        defaults = subprocess.run(
            [
                "make",
                "--dry-run",
                "--no-print-directory",
                "-f",
                "-",
                *shared_targets,
            ],
            input=EXPECTED_PRESET_MAKEFILE,
            cwd=preset_dir,
            text=True,
            capture_output=True,
            check=True,
        )
        actual = subprocess.run(
            ["make", "--dry-run", "--no-print-directory", *shared_targets],
            cwd=preset_dir,
            text=True,
            capture_output=True,
            check=True,
        )
        assert actual.stderr == "", (preset_dir, actual.stderr)
        assert actual.stdout == defaults.stdout, (preset_dir, actual.stdout)
        (old_file,) = preset_dir.glob("old.*")
        (new_file,) = preset_dir.glob("new.*")
        assert f"bat {old_file.name} {new_file.name}\n" in actual.stdout, (
            preset_dir
        )

        database = subprocess.run(
            [
                "make",
                "--dry-run",
                "--no-print-directory",
                "--print-data-base",
                "-rR",
                *shared_targets,
            ],
            cwd=preset_dir,
            text=True,
            capture_output=True,
            check=True,
        )
        # GNU Make expands multi-target and variable-defined rules here. Its
        # non-target file entries are marked separately from declared targets.
        rules = database.stdout.split("# Files\n", 1)[1].split(
            "# files hash-table", 1
        )[0]
        targets = []
        for block in rules.split("\n\n"):
            if "# Not a target:" in block:
                continue
            target = re.search(r"^[^#\s:=]+(?=:)", block, re.MULTILINE)
            if target is None:
                continue
            target_name = target.group()
            if target_name.startswith(".") or target_name in shared_targets:
                continue
            targets.append(target_name)
        for target_name in targets:
            expanded = subprocess.run(
                ["make", "--dry-run", "--no-print-directory", target_name],
                cwd=preset_dir,
                text=True,
                capture_output=True,
                check=True,
            )
            assert expanded.stderr == "", (
                preset_dir,
                target_name,
                expanded.stderr,
            )
            assert expanded.stdout.strip() != "", (preset_dir, target_name)
            if preset_dir.parents[1].name == "git" and target_name in {
                "git-moved",
                "git-moved-indent",
            }:
                assert "--color-moved=zebra" in expanded.stdout, preset_dir
                whitespace = (
                    "allow-indentation-change"
                    if target_name == "git-moved-indent"
                    else "no"
                )
                assert f"--color-moved-ws={whitespace}" in expanded.stdout, (
                    preset_dir
                )
                assert f"{old_file.name} {new_file.name}" in expanded.stdout, (
                    preset_dir
                )


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
