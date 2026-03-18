"""Security tests: audit daemon codebase for forbidden subprocess patterns (NFR-SEC-3)."""

from __future__ import annotations

import pathlib

DAEMON_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "verde-daemon"


def _scan_daemon_files(pattern: str) -> list[tuple[str, int, str]]:
    """Scan all .py files under verde-daemon for a pattern.

    Returns list of (filename, line_number, line_text) matches.
    """
    matches = []
    for py_file in sorted(DAEMON_SRC.rglob("*.py")):
        for i, line in enumerate(py_file.read_text().splitlines(), 1):
            if pattern in line and not line.lstrip().startswith("#"):
                matches.append((str(py_file.relative_to(DAEMON_SRC)), i, line.strip()))
    return matches


class TestNoShellTrue:
    def test_no_shell_true_in_subprocess(self):
        """No subprocess calls use shell=True (NFR-SEC-3)."""
        matches = _scan_daemon_files("shell=True")
        assert matches == [], f"Found shell=True: {matches}"

    def test_no_os_system(self):
        """No os.system() calls in daemon code."""
        matches = _scan_daemon_files("os.system(")
        assert matches == [], f"Found os.system(): {matches}"

    def test_no_os_popen(self):
        """No os.popen() calls in daemon code."""
        matches = _scan_daemon_files("os.popen(")
        assert matches == [], f"Found os.popen(): {matches}"
