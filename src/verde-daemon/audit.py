"""Append-only JSONL audit logger for privileged operations.

Every privileged D-Bus method call (successful or failed) is recorded as a
single JSON object per line in ``/var/lib/verde/audit.log``.

JSONL Entry Schema
------------------
Required fields: timestamp, operation, params, caller, result.
Optional field: error (present only on failure).

Examples::

    {"timestamp":"2026-03-18T14:30:00+00:00","operation":"INSTALL_DRIVER","params":{"version":"565"},"caller":":1.42","result":"success"}
    {"timestamp":"2026-03-18T14:30:01+00:00","operation":"AUTH_DENIED","params":{"action":"com.verde.driver.manage","method":"InstallDriver"},"caller":":1.42","result":"denied"}
    {"timestamp":"2026-03-18T14:35:00+00:00","operation":"ROLLBACK_DRIVER","params":{"snapshot_id":"2026-03-18T14:30:00_nvidia-560"},"caller":":1.42","result":"failed","error":"Snapshot not found"}

References: AR-14, FR34, FR41, NFR-SEC-10.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib

log = logging.getLogger("verde-daemon.audit")

# Operation type constants
OP_INSTALL_DRIVER = "INSTALL_DRIVER"
OP_ROLLBACK_DRIVER = "ROLLBACK_DRIVER"
OP_FIX_SUSPEND = "FIX_SUSPEND"
OP_FIX_HIBERNATE = "FIX_HIBERNATE"
OP_AUTH_DENIED = "AUTH_DENIED"

_DEFAULT_LOG_DIR = pathlib.Path("/var/lib/verde")
_AUDIT_LOG_NAME = "audit.log"
_DIR_PERMISSIONS = 0o750
_FILE_PERMISSIONS = 0o640


class AuditLogger:
    """Append-only JSONL audit logger.

    Parameters
    ----------
    log_dir : str | pathlib.Path
        Directory for audit.log.  Defaults to ``/var/lib/verde``.
        Override in tests via ``tmp_path``.
    """

    def __init__(self, log_dir: str | pathlib.Path = _DEFAULT_LOG_DIR) -> None:
        self._log_dir = pathlib.Path(log_dir)
        self._log_file = self._log_dir / _AUDIT_LOG_NAME
        self._dir_ensured = False

    def _ensure_directory(self) -> None:
        """Create the log directory on first write (lazy)."""
        if self._dir_ensured:
            return
        self._log_dir.mkdir(mode=_DIR_PERMISSIONS, parents=True, exist_ok=True)
        os.chmod(self._log_dir, _DIR_PERMISSIONS)
        self._dir_ensured = True

    def log(
        self,
        operation: str,
        params: dict,
        caller: str,
        result: str,
        error: str | None = None,
    ) -> None:
        """Append an audit entry to the log file.

        Each call produces exactly one JSON line.  I/O errors are logged
        to the Python logger but never raised — the daemon must not crash
        because of audit failures.
        """
        entry: dict = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "operation": operation,
            "params": params,
            "caller": caller,
            "result": result,
        }
        if error is not None:
            entry["error"] = error

        try:
            line = json.dumps(entry, separators=(",", ":")) + "\n"
        except (TypeError, ValueError) as exc:
            log.error("Failed to serialize audit entry: %s", exc)
            return

        try:
            self._ensure_directory()
            fd = os.open(
                self._log_file,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                _FILE_PERMISSIONS,
            )
            with os.fdopen(fd, "a") as f:
                f.write(line)
                f.flush()
        except OSError as exc:
            log.error("Failed to write audit log: %s", exc)

    def log_auth_failure(self, action: str, caller: str, method: str) -> None:
        """Log a Polkit authorization denial."""
        self.log(
            operation=OP_AUTH_DENIED,
            params={"action": action, "method": method},
            caller=caller,
            result="denied",
        )
