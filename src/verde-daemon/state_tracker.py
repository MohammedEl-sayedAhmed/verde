"""External change detection and config integrity validation.

Captures a "last known state" snapshot on clean shutdown and after
every successful Verde operation.  On daemon activation, compares
current system state to the snapshot and reports differences.

State file: ``/var/lib/verde/last_state.json``

References: FR76, FR79; Story 6.1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("verde-daemon.state-tracker")

_STATE_VERSION = 1

# Verde-managed config files to track for integrity
MANAGED_CONFIGS: list[str] = [
    "/etc/modprobe.d/verde-nvidia.conf",
    "/etc/initramfs-tools/conf.d/verde-resume",
    "/etc/systemd/system/nvidia-suspend.service.d/verde.conf",
    "/etc/systemd/system/nvidia-hibernate.service.d/verde.conf",
]


def file_sha256(path: str) -> str | None:
    """Compute SHA-256 hash of a file.  Returns None if file doesn't exist."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (FileNotFoundError, PermissionError, OSError):
        return None


class StateTracker:
    """Tracks system state and detects external changes."""

    def __init__(
        self,
        state_dir: str = "/var/lib/verde",
        run: Any | None = None,
        read_file: Any | None = None,
    ) -> None:
        self._state_dir = state_dir
        self._state_file = os.path.join(state_dir, "last_state.json")
        self._run = run or self._default_run
        self._read = read_file or self._default_read_file
        self._previous_state: dict | None = None

    @staticmethod
    def _default_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
        kwargs.setdefault("timeout", 10)
        return subprocess.run(cmd, **kwargs)

    @staticmethod
    def _default_read_file(path: str) -> str:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    # ── State snapshot persistence ────────────────────────────────────

    def load_previous_state(self) -> dict | None:
        """Load the previous state snapshot.  Returns None on first run or corruption."""
        try:
            with open(self._state_file, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or data.get("version") != _STATE_VERSION:
                log.warning("Invalid state file version — treating as first run")
                return None
            self._previous_state = data
            return data
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load state file: %s — treating as first run", exc)
            return None

    def save_current_state(self) -> None:
        """Capture and persist the current system state."""
        state = self._capture_current_state()
        try:
            os.makedirs(self._state_dir, mode=0o750, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
        except OSError as exc:
            log.error("Failed to save state file: %s", exc)

    def _capture_current_state(self) -> dict:
        """Build a snapshot of current system state."""
        return {
            "version": _STATE_VERSION,
            "captured_at": datetime.now(tz=UTC).isoformat(),
            "driver_version": self._get_driver_version(),
            "driver_type": self._get_driver_type(),
            "kernel_version": os.uname().release,
            "managed_configs": self._get_managed_config_hashes(),
        }

    def _get_driver_version(self) -> str:
        """Get current NVIDIA driver version."""
        content = self._read("/sys/module/nvidia/version")
        version = content.strip()
        return version if version else ""

    def _get_driver_type(self) -> str:
        """Detect current driver type: proprietary, nouveau, or none."""
        try:
            result = self._run(["lsmod"])
            if result.returncode != 0:
                return "unknown"
            lines = [line for line in result.stdout.splitlines()[1:] if line.strip()]
            has_nvidia = any(line.split()[0].startswith("nvidia") for line in lines)
            has_nouveau = any(line.split()[0] == "nouveau" for line in lines)
            if has_nvidia:
                return "proprietary"
            if has_nouveau:
                return "nouveau"
            return "none"
        except (subprocess.TimeoutExpired, OSError, IndexError):
            return "unknown"

    def _get_managed_config_hashes(self) -> list[dict]:
        """Compute hashes for all Verde-managed config files."""
        configs = []
        for path in MANAGED_CONFIGS:
            sha = file_sha256(path)
            configs.append(
                {
                    "path": path,
                    "sha256": sha,
                    "exists": sha is not None,
                }
            )
        return configs

    # ── External change detection ─────────────────────────────────────

    def detect_external_changes(self) -> list[dict]:
        """Compare current state to previous snapshot.

        Returns a list of change records.  Empty list means no changes.
        """
        if self._previous_state is None:
            return []

        changes: list[dict] = []
        now = datetime.now(tz=UTC).isoformat()

        # Driver version — suppress when either side is empty (sysfs unreadable)
        current_ver = self._get_driver_version()
        prev_ver = self._previous_state.get("driver_version", "")
        if current_ver != prev_ver and current_ver and prev_ver:
            changes.append(
                {
                    "change_type": "driver_version",
                    "field": "driver_version",
                    "old_value": prev_ver,
                    "new_value": current_ver,
                    "detected_at": now,
                }
            )

        # Driver type — suppress when either side is "unknown" (lsmod failure)
        current_type = self._get_driver_type()
        prev_type = self._previous_state.get("driver_type", "")
        if current_type != prev_type and current_type != "unknown" and prev_type != "unknown":
            changes.append(
                {
                    "change_type": "driver_type",
                    "field": "driver_type",
                    "old_value": prev_type,
                    "new_value": current_type,
                    "detected_at": now,
                }
            )

        # Kernel version
        current_kernel = os.uname().release
        prev_kernel = self._previous_state.get("kernel_version", "")
        if current_kernel != prev_kernel:
            changes.append(
                {
                    "change_type": "kernel_version",
                    "field": "kernel_version",
                    "old_value": prev_kernel,
                    "new_value": current_kernel,
                    "detected_at": now,
                }
            )

        return changes

    # ── Config integrity validation ───────────────────────────────────

    def validate_config_integrity(self) -> list[dict]:
        """Check managed config files for external modifications.

        Returns a list of integrity issues.  Empty list means all intact.
        """
        if self._previous_state is None:
            return []

        issues: list[dict] = []
        prev_configs = {c["path"]: c for c in self._previous_state.get("managed_configs", [])}

        for path in MANAGED_CONFIGS:
            prev = prev_configs.get(path)
            if prev is None:
                continue  # File not tracked in previous state

            prev_hash = prev.get("sha256")
            prev_exists = prev.get("exists", False)
            current_hash = file_sha256(path)

            if prev_exists and current_hash is None:
                # File was deleted
                issues.append(
                    {
                        "file_path": path,
                        "issue_type": "deleted",
                        "expected_hash": prev_hash or "",
                        "actual_hash": "",
                    }
                )
            elif prev_exists and prev_hash and current_hash and current_hash != prev_hash:
                # File was modified
                issues.append(
                    {
                        "file_path": path,
                        "issue_type": "modified",
                        "expected_hash": prev_hash,
                        "actual_hash": current_hash,
                    }
                )
            elif not prev_exists and current_hash is not None:
                # File appeared (created externally)
                issues.append(
                    {
                        "file_path": path,
                        "issue_type": "created_externally",
                        "expected_hash": "",
                        "actual_hash": current_hash,
                    }
                )

        return issues
