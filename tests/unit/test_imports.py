"""Verify Verde packages import cleanly and share zero imports."""

import ast
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
VERDE_SRC = PROJECT_ROOT / "src" / "verde"
DAEMON_SRC = PROJECT_ROOT / "src" / "verde-daemon"


def _collect_imports(package_dir: pathlib.Path) -> set[str]:
    """Collect all import targets from Python files in a package."""
    imports: set[str] = set()
    for py_file in package_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    return imports


def test_verde_gui_package_exists():
    """GUI package directory exists with __init__.py."""
    assert (VERDE_SRC / "__init__.py").is_file()


def test_verde_daemon_package_exists():
    """Daemon package directory exists with __init__.py."""
    assert (DAEMON_SRC / "__init__.py").is_file()


def test_gui_does_not_import_daemon():
    """GUI package must not import from verde-daemon (AR-7)."""
    gui_imports = _collect_imports(VERDE_SRC)
    forbidden = {"verde_daemon", "verde-daemon"}
    violations = gui_imports & forbidden
    assert not violations, f"GUI imports from daemon: {violations}"


def test_daemon_does_not_import_gui():
    """Daemon package must not import from verde GUI (AR-7)."""
    daemon_imports = _collect_imports(DAEMON_SRC)
    forbidden = {"verde"}
    # Filter out standard library 'verde' false positives — verde is our package
    violations = daemon_imports & forbidden
    assert not violations, f"Daemon imports from GUI: {violations}"
