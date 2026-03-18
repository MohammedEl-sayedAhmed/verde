"""Snapshot manager — pre-operation driver state snapshots.

Creates, verifies, lists, and prunes JSON snapshots of driver state
before privileged operations.  Snapshots are stored at
``/var/lib/verde/snapshots/`` with SHA-256 integrity hashes.

References: AR-15, FR16, FR20, FR21, FR53, NFR-PERF-7, NFR-REL-6.
"""

from __future__ import annotations

import contextlib
import datetime
import glob as _glob
import hashlib
import json
import logging
import os
import pathlib
import re
import subprocess
import uuid

log = logging.getLogger("verde-daemon.snapshot")

# ── Constants ────────────────────────────────────────────────────────

MAX_SNAPSHOTS = 10
MIN_FREE_SPACE_BYTES = 10 * 1024 * 1024  # 10 MB

SNAPSHOT_DIR_DEFAULT = pathlib.Path("/var/lib/verde/snapshots")
RECOVERY_FILE_DEFAULT = pathlib.Path("/var/lib/verde/recovery-instructions.txt")

_SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}_[a-zA-Z0-9._-]+-[0-9a-f]{4}$")
_DRIVER_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

_DIR_PERMISSIONS = 0o755
_FILE_PERMISSIONS = 0o644

# ── Exceptions ───────────────────────────────────────────────────────


class InsufficientSpaceError(Exception):
    """Raised when there is not enough disk space for a snapshot."""


class InvalidSnapshotId(ValueError):
    """Raised when a snapshot ID does not match the expected format."""


# ── Data capture helpers ─────────────────────────────────────────────


def _query_nvidia_packages() -> list[dict[str, str]]:
    """Query installed NVIDIA packages via dpkg-query."""
    try:
        result = subprocess.run(
            [
                "dpkg-query",
                "-W",
                "-f",
                "${Package}\t${Version}\t${Architecture}\n",
                "nvidia-*",
                "libnvidia-*",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.warning("dpkg-query unavailable or timed out")
        return []

    packages = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1]:  # skip packages with no version
            packages.append(
                {
                    "name": parts[0],
                    "version": parts[1],
                    "architecture": parts[2],
                }
            )
    return packages


def _query_dkms_modules() -> list[dict[str, str]]:
    """Query DKMS module status via ``dkms status``."""
    try:
        result = subprocess.run(
            ["dkms", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.warning("dkms unavailable or timed out")
        return []

    modules = []
    for line in result.stdout.strip().splitlines():
        # Format: module, version, kernel, arch: status
        parts = [p.strip().rstrip(",") for p in line.split(",")]
        if len(parts) >= 3:
            status_parts = parts[-1].split(":")
            status = status_parts[-1].strip() if len(status_parts) > 1 else parts[-1]
            kernel = parts[2].strip().rstrip(":") if len(parts) > 2 else ""
            modules.append(
                {
                    "module": parts[0],
                    "version": parts[1] if len(parts) > 1 else "",
                    "kernel": kernel,
                    "status": status,
                }
            )
    return modules


def _capture_config_files() -> dict[str, str]:
    """Read NVIDIA config files; store content for <4KB, SHA-256 for larger."""
    config_dirs = ["/etc/modprobe.d", "/etc/modules-load.d"]
    result: dict[str, str] = {}

    for d in config_dirs:
        for path in sorted(_glob.glob(os.path.join(d, "nvidia*.conf"))):
            try:
                size = os.path.getsize(path)
                if size < 4096:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        result[path] = f.read()
                else:
                    h = hashlib.sha256()
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            h.update(chunk)
                    result[path] = f"sha256:{h.hexdigest()}"
            except (OSError, UnicodeDecodeError):
                log.warning("Cannot read config file: %s", path)
    return result


# ── SHA-256 helpers ──────────────────────────────────────────────────


def _compute_sha256(snapshot_dict: dict) -> str:
    """Compute SHA-256 over snapshot JSON with sha256 field set to null."""
    copy = {**snapshot_dict, "sha256": None}
    blob = json.dumps(copy, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def verify_snapshot_integrity(snapshot_path: str | pathlib.Path) -> bool:
    """Recompute SHA-256 of a snapshot file and compare to stored hash."""
    path = pathlib.Path(snapshot_path)
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    stored = data.get("sha256")
    if not stored:
        return False

    computed = _compute_sha256(data)
    return computed == stored


# ── Snapshot ID validation ───────────────────────────────────────────


def _validate_snapshot_id(snapshot_id: str) -> None:
    """Raise InvalidSnapshotId if ID doesn't match expected format."""
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise InvalidSnapshotId(
            f"Invalid snapshot ID: {snapshot_id!r}. "
            f"Expected format: YYYYMMDDTHHMMSS_driver-identifier"
        )


# ── SnapshotManager ─────────────────────────────────────────────────


class SnapshotManager:
    """Manages pre-operation driver state snapshots.

    Parameters
    ----------
    snapshot_dir : pathlib.Path
        Directory for snapshot JSON files.
    recovery_path : pathlib.Path
        Path for recovery instructions file.
    audit_logger : object | None
        Optional AuditLogger instance (from Story 1.5).
    """

    def __init__(
        self,
        snapshot_dir: pathlib.Path = SNAPSHOT_DIR_DEFAULT,
        recovery_path: pathlib.Path = RECOVERY_FILE_DEFAULT,
        audit_logger: object | None = None,
    ) -> None:
        self._snapshot_dir = snapshot_dir
        self._recovery_path = recovery_path
        self._audit_logger = audit_logger

    def _ensure_directory(self) -> None:
        """Create snapshot directory if it doesn't exist."""
        self._snapshot_dir.mkdir(mode=_DIR_PERMISSIONS, parents=True, exist_ok=True)

    def _check_storage_space(self) -> None:
        """Check that sufficient disk space is available.

        Raises InsufficientSpaceError if <10 MB free.
        """
        check_path = self._snapshot_dir
        while not check_path.exists():
            check_path = check_path.parent
            if check_path == check_path.parent:
                break  # reached filesystem root
        stat = os.statvfs(check_path)

        available = stat.f_bavail * stat.f_frsize
        log.debug(
            "Storage check: %d bytes available, %d required", available, MIN_FREE_SPACE_BYTES
        )
        if available < MIN_FREE_SPACE_BYTES:
            raise InsufficientSpaceError(
                f"Insufficient disk space: {available} bytes available, "
                f"{MIN_FREE_SPACE_BYTES} bytes required"
            )

    def _generate_snapshot_id(self, target_driver: str) -> str:
        """Generate a snapshot ID from current time, driver identifier, and random suffix."""
        ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
        # Sanitize driver identifier for filename
        safe_driver = (
            re.sub(r"[^a-zA-Z0-9._-]", "-", target_driver) if target_driver else "unknown"
        )
        # 4-char hex suffix to avoid collisions on rapid successive calls
        suffix = os.urandom(2).hex()
        return f"{ts}_{safe_driver}-{suffix}"

    def _build_snapshot_data(
        self,
        snapshot_id: str,
        operation_type: str,
        target_driver: str,
        user: str,
    ) -> dict:
        """Build the full snapshot data dict."""
        now = datetime.datetime.now(datetime.UTC)
        data = {
            "schema_version": 1,
            "snapshot_id": snapshot_id,
            "timestamp": now.isoformat(),
            "driver_packages": _query_nvidia_packages(),
            "kernel_version": os.uname().release,
            "dkms_modules": _query_dkms_modules(),
            "config_files": _capture_config_files(),
            "operation": {
                "type": operation_type,
                "target_driver": target_driver,
                "user": user,
            },
            "sha256": None,
        }
        data["sha256"] = _compute_sha256(data)
        return data

    def _write_atomic(self, path: pathlib.Path, content: str) -> None:
        """Write content to path atomically via temp file + os.replace."""
        self._ensure_directory()
        tmp_path = self._snapshot_dir / f".tmp_{uuid.uuid4().hex}.json"
        try:
            with open(tmp_path, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, _FILE_PERMISSIONS)
            os.replace(tmp_path, path)
        except BaseException:
            # Clean up temp file on any error
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            raise

    def _prune_old_snapshots(self) -> None:
        """Remove oldest snapshots to make room for a new one.

        Prunes to MAX_SNAPSHOTS - 1 so the upcoming write stays within limit.
        """
        snapshots = sorted(self._snapshot_dir.glob("*.json"))
        # Exclude temp files
        snapshots = [s for s in snapshots if not s.name.startswith(".tmp_")]

        while len(snapshots) >= MAX_SNAPSHOTS:
            oldest = snapshots.pop(0)
            log.info("Pruning old snapshot: %s", oldest.name)
            try:
                oldest.unlink()
            except OSError as exc:
                log.warning("Failed to prune snapshot %s: %s", oldest.name, exc)

    def _log_audit(
        self,
        operation: str,
        params: dict,
        caller: str,
        result: str,
        error: str | None = None,
    ) -> None:
        """Log to audit logger if available, otherwise to Python logger."""
        if self._audit_logger is not None and hasattr(self._audit_logger, "log"):
            try:
                self._audit_logger.log(
                    operation=operation,
                    params=params,
                    caller=caller,
                    result=result,
                    error=error,
                )
            except Exception as exc:
                log.error("Audit logger failed: %s", exc)
        else:
            audit_log = logging.getLogger("verde.audit")
            msg = f"{operation} {' '.join(f'{k}={v}' for k, v in params.items())} result={result}"
            if error:
                msg += f" error={error}"
            audit_log.info(msg)

    # ── Public API ───────────────────────────────────────────────────

    def create_snapshot(
        self,
        operation_type: str,
        target_driver: str,
        user: str,
    ) -> str:
        """Create a pre-operation snapshot. Returns the snapshot ID.

        Raises InsufficientSpaceError if disk space is below 10 MB.
        """
        self._check_storage_space()

        snapshot_id = self._generate_snapshot_id(target_driver)
        try:
            data = self._build_snapshot_data(snapshot_id, operation_type, target_driver, user)

            path = self._snapshot_dir / f"{snapshot_id}.json"
            content = json.dumps(data, indent=2, sort_keys=False)

            self._prune_old_snapshots()
            self._write_atomic(path, content)
        except Exception as exc:
            self._log_audit(
                operation="SNAPSHOT_CREATE",
                params={"snapshot": snapshot_id, "driver": target_driver, "user": user},
                caller=user,
                result="failed",
                error=str(exc),
            )
            raise

        self._log_audit(
            operation="SNAPSHOT_CREATE",
            params={"snapshot": snapshot_id, "driver": target_driver, "user": user},
            caller=user,
            result="success",
        )

        log.info("Snapshot created: %s", snapshot_id)
        return snapshot_id

    def list_snapshots(self) -> list[dict]:
        """Return list of snapshot metadata (newest first), without config file contents."""
        if not self._snapshot_dir.exists():
            return []

        snapshots = []
        for path in sorted(self._snapshot_dir.glob("*.json"), reverse=True):
            if path.name.startswith(".tmp_"):
                continue
            try:
                file_size = path.stat().st_size
                with open(path) as f:
                    data = json.load(f)
                snapshots.append(
                    {
                        "snapshot_id": data.get("snapshot_id", path.stem),
                        "timestamp": data.get("timestamp", ""),
                        "operation": data.get("operation", {}),
                        "driver_packages": data.get("driver_packages", []),
                        "kernel_version": data.get("kernel_version", ""),
                        "schema_version": data.get("schema_version", 0),
                        "dkms_modules": data.get("dkms_modules", []),
                        "file_size": file_size,
                        "sha256": data.get("sha256", ""),
                    }
                )
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Cannot read snapshot %s: %s", path.name, exc)
        return snapshots

    def get_snapshot(self, snapshot_id: str) -> dict:
        """Return full snapshot data for a given ID.

        Raises InvalidSnapshotId for malformed IDs.
        Raises FileNotFoundError if snapshot doesn't exist.
        Raises json.JSONDecodeError if snapshot JSON is corrupted.
        """
        _validate_snapshot_id(snapshot_id)
        path = self._snapshot_dir / f"{snapshot_id}.json"
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise json.JSONDecodeError("Snapshot is not a JSON object", str(path), 0)
        return data

    def verify_integrity(self, snapshot_id: str) -> bool:
        """Verify SHA-256 integrity of a snapshot.

        Raises InvalidSnapshotId for malformed IDs.
        """
        _validate_snapshot_id(snapshot_id)
        path = self._snapshot_dir / f"{snapshot_id}.json"
        return verify_snapshot_integrity(path)

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete a snapshot file.

        Raises InvalidSnapshotId for malformed IDs.
        Raises FileNotFoundError if snapshot doesn't exist.
        """
        _validate_snapshot_id(snapshot_id)
        path = self._snapshot_dir / f"{snapshot_id}.json"
        path.unlink()
        log.info("Snapshot deleted: %s", snapshot_id)

    def write_recovery_instructions(
        self,
        snapshot_id: str,
        operation_type: str,
        target_driver: str,
    ) -> None:
        """Write recovery instructions file before a driver operation.

        Raises InvalidSnapshotId for malformed IDs.
        """
        _validate_snapshot_id(snapshot_id)
        if not _DRIVER_ID_RE.match(target_driver):
            raise ValueError(
                f"Invalid target_driver: {target_driver!r}. Must match [a-zA-Z0-9._-]+"
            )

        now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        content = f"""\
=== Verde Recovery Instructions ===
Generated: {now}
Operation: {operation_type} driver {target_driver}
Snapshot: {snapshot_id}

If your screen is blank after reboot:
1. Press Ctrl+Alt+F2 to open a TTY
2. Run: verde --repair
3. Follow the on-screen prompts

Manual rollback (if verde --repair is unavailable):
  sudo apt-get install --reinstall nvidia-driver-{target_driver}
  sudo update-initramfs -u
  sudo reboot
"""
        # Write atomically
        parent = self._recovery_path.parent
        parent.mkdir(mode=_DIR_PERMISSIONS, parents=True, exist_ok=True)
        tmp_path = parent / f".tmp_{uuid.uuid4().hex}.txt"
        try:
            with open(tmp_path, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, _FILE_PERMISSIONS)
            os.replace(tmp_path, self._recovery_path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            raise
