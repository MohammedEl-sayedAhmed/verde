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
OP_GENERATE_DIAGNOSTIC = "GENERATE_DIAGNOSTIC"
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

    def read_entries(
        self,
        filter_type: str = "",
        date_from: str = "",
        date_to: str = "",
        result: str = "",
    ) -> list[dict]:
        """Read and filter audit log entries.

        Returns entries in reverse chronological order (newest first).
        All parameters are optional — empty string means no filter.
        """
        entries: list[dict] = []
        try:
            if not self._log_file.exists():
                return []
            with open(self._log_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("Skipping malformed audit log line")
                        continue
                    if self._matches_filters(entry, filter_type, date_from, date_to, result):
                        entries.append(entry)
        except OSError as exc:
            log.warning("Failed to read audit log: %s", exc)

        entries.reverse()  # newest first
        return entries

    @staticmethod
    def _matches_filters(
        entry: dict,
        filter_type: str,
        date_from: str,
        date_to: str,
        result: str,
    ) -> bool:
        """Check if an entry matches the given filters."""
        if filter_type and entry.get("operation", "") != filter_type:
            return False
        if result and entry.get("result", "") != result:
            return False
        ts = entry.get("timestamp", "")
        if date_from and ts < date_from:
            return False
        return not (date_to and ts > date_to)


def detect_suspicious_patterns(entries: list[dict]) -> list[dict]:
    """Detect suspicious security patterns in audit entries.

    Checks for:
    - Repeated auth failures: 3+ failures in 5 min from same caller
    - Rapid privileged ops: 5+ operations in 10 min

    Returns the input entries with ``flagged`` (bool) and
    ``flag_reason`` (str) fields added where patterns detected.
    """
    _AUTH_FAIL_THRESHOLD = 3
    _AUTH_FAIL_WINDOW = datetime.timedelta(minutes=5)
    _RAPID_OPS_THRESHOLD = 5
    _RAPID_OPS_WINDOW = datetime.timedelta(minutes=10)

    # Key by list index to avoid timestamp collision between entries
    flagged_indices: set[int] = set()
    flag_reasons: dict[int, list[str]] = {}

    # Parse timestamps and build indexed list
    parsed: list[tuple[int, dict, datetime.datetime | None]] = []
    for idx, entry in enumerate(entries):
        ts_str = entry.get("timestamp", "")
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            ts = None
        parsed.append((idx, entry, ts))

    # Check auth failure clustering (forward-only sliding window)
    auth_failures = [
        (idx, e, ts) for idx, e, ts in parsed if e.get("result") == "denied" and ts is not None
    ]
    for _i, (_idx_i, entry_i, ts_i) in enumerate(auth_failures):
        caller = entry_i.get("caller", "")
        window_indices = [
            idx_j
            for idx_j, e, t in auth_failures
            if e.get("caller") == caller
            and t is not None
            and ts_i is not None
            and 0 <= (t - ts_i).total_seconds() <= _AUTH_FAIL_WINDOW.total_seconds()
        ]
        if len(window_indices) >= _AUTH_FAIL_THRESHOLD:
            reason = "Repeated authentication failures"
            for idx_j in window_indices:
                flagged_indices.add(idx_j)
                flag_reasons.setdefault(idx_j, [])
                if reason not in flag_reasons[idx_j]:
                    flag_reasons[idx_j].append(reason)

    # Check rapid privileged operations (per-caller, forward-only window)
    priv_ops = [
        (idx, e, ts)
        for idx, e, ts in parsed
        if e.get("operation") not in (OP_AUTH_DENIED, OP_GENERATE_DIAGNOSTIC, "")
        and e.get("result") == "success"
        and ts is not None
    ]
    for _i, (_idx_i, entry_i, ts_i) in enumerate(priv_ops):
        caller = entry_i.get("caller", "")
        window_indices = [
            idx_j
            for idx_j, e, t in priv_ops
            if e.get("caller") == caller
            and t is not None
            and ts_i is not None
            and 0 <= (t - ts_i).total_seconds() <= _RAPID_OPS_WINDOW.total_seconds()
        ]
        if len(window_indices) >= _RAPID_OPS_THRESHOLD:
            reason = "Unusual frequency of privileged operations"
            for idx_j in window_indices:
                flagged_indices.add(idx_j)
                flag_reasons.setdefault(idx_j, [])
                if reason not in flag_reasons[idx_j]:
                    flag_reasons[idx_j].append(reason)

    # Annotate entries
    for idx, entry in enumerate(entries):
        if idx in flagged_indices:
            entry["flagged"] = True
            entry["flag_reason"] = "; ".join(flag_reasons.get(idx, []))
        else:
            entry["flagged"] = False
            entry["flag_reason"] = ""

    return entries
