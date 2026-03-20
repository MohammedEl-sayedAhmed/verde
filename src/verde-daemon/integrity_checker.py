"""Self-integrity checker for the Verde daemon installation.

Checks that required policy files, D-Bus config, and systemd unit
are present and non-empty.  Reports structured results for each file.

References: FR87; Story 6.2, Task 3.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("verde-daemon.integrity-checker")

REQUIRED_FILES: list[dict[str, str]] = [
    {
        "path": "/usr/share/polkit-1/actions/com.verde.policy",
        "purpose": "Polkit action definitions",
        "if_missing": "All privileged operations will fail with 'Not authorized'",
    },
    {
        "path": "/usr/share/dbus-1/system.d/com.verde.Manager.conf",
        "purpose": "D-Bus bus policy",
        "if_missing": "Daemon may not be accessible from GUI",
    },
    {
        "path": "/usr/lib/systemd/system/com.verde.Manager.service",
        "purpose": "systemd service unit",
        "if_missing": "Socket activation will not work",
    },
    {
        "path": "/usr/share/dbus-1/system-services/com.verde.Manager.service",
        "purpose": "D-Bus activation service",
        "if_missing": "Daemon will not auto-start",
    },
]


class IntegrityChecker:
    """Checks Verde daemon installation integrity."""

    def __init__(
        self,
        required_files: list[dict[str, str]] | None = None,
    ) -> None:
        self._files = required_files or REQUIRED_FILES

    def check_all(self) -> dict[str, Any]:
        """Run all integrity checks.

        Returns a dict with:
        - ``healthy`` (bool): True if all files are present and non-empty
        - ``files`` (list[dict]): per-file status
        - ``guidance`` (str): recommended fix if any issues found
        """
        results: list[dict[str, Any]] = []
        all_ok = True

        for entry in self._files:
            path = entry["path"]
            status = self._check_file(path)
            results.append(
                {
                    "path": path,
                    "purpose": entry["purpose"],
                    "status": status,
                    "if_missing": entry["if_missing"],
                }
            )
            if status != "ok":
                all_ok = False

        guidance = ""
        if not all_ok:
            guidance = "sudo apt install --reinstall verde-daemon"

        return {
            "healthy": all_ok,
            "files": results,
            "guidance": guidance,
        }

    @staticmethod
    def _check_file(path: str) -> str:
        """Check a single file.  Returns 'ok', 'missing', or 'empty'."""
        try:
            stat = os.stat(path)
            if stat.st_size == 0:
                return "empty"
            return "ok"
        except FileNotFoundError:
            return "missing"
        except PermissionError:
            return "unreadable"
        except OSError:
            return "error"
