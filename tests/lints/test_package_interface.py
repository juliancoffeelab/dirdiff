import subprocess
import sys
from pathlib import Path

LINTS_ROOT = Path(__file__).resolve().parents[2] / "lints"


def import_violations(root: Path, files: dict[str, str]) -> list[str]:
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for required in ("src", "tests", "lints", "alembic"):
        (root / required).mkdir(exist_ok=True)
    (root / ".flake8").write_text(
        "[flake8:local-plugins]\n"
        "extension =\n"
        "    PKG = package_interface:PackageInterfacePlugin\n"
        f"paths =\n    {LINTS_ROOT}\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flake8",
            "--select",
            "PKG004",
            "--format",
            "%(path)s:%(row)d",
            "src",
            "tests",
        ],
        cwd=root,
        # flake8 exits non-zero whenever it reports a violation.
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.stderr == "", result.stderr
    return sorted(result.stdout.splitlines())


def test_facade_relative_import_of_its_private_package_is_refused(
    tmp_path: Path,
) -> None:
    violations = import_violations(
        tmp_path,
        {
            "src/dirdiff/__init__.py": "",
            "src/dirdiff/a/__init__.py": (
                "from ._x.impl import thing as thing\n__all__ = ['thing']\n"
            ),
            "src/dirdiff/a/_x/__init__.py": "",
            "src/dirdiff/a/_x/impl.py": "thing = 1\n__all__ = ['thing']\n",
        },
    )

    assert violations == ["src/dirdiff/a/__init__.py:1"]


def test_paired_module_reaches_private_submodules_but_not_their_facade(
    tmp_path: Path,
) -> None:
    violations = import_violations(
        tmp_path,
        {
            "src/dirdiff/__init__.py": "",
            "src/dirdiff/a/__init__.py": "__all__ = []\n",
            "src/dirdiff/a/x.py": (
                "from dirdiff.a._x.impl import thing\n"
                "from dirdiff.a._x import impl\n"
                "__all__ = []\n"
            ),
            "src/dirdiff/a/_x/__init__.py": "",
            "src/dirdiff/a/_x/impl.py": "thing = 1\n__all__ = ['thing']\n",
        },
    )

    assert violations == ["src/dirdiff/a/x.py:2"]


def test_tests_may_import_public_submodules_but_not_private_ones(
    tmp_path: Path,
) -> None:
    violations = import_violations(
        tmp_path,
        {
            "src/dirdiff/__init__.py": "",
            "src/dirdiff/a/__init__.py": "__all__ = []\n",
            "src/dirdiff/a/impl.py": "thing = 1\n__all__ = ['thing']\n",
            "src/dirdiff/a/_x/__init__.py": "",
            "src/dirdiff/a/_x/impl.py": "thing = 1\n__all__ = ['thing']\n",
            "tests/test_a.py": (
                "from dirdiff.a.impl import thing\n"
                "from dirdiff.a._x.impl import thing as other\n"
            ),
        },
    )

    assert violations == ["tests/test_a.py:2"]
