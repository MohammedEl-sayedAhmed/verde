"""System modification tracking with undo/revert capability.

Tracks all system changes Verde makes (services enabled, config files
created/modified, initramfs rebuilt) in a persistent manifest.  Each
modification records the original state so it can be reverted.

Manifest: ``/var/lib/verde/modifications.json``

References: FR88; Story 6.3.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("verde-daemon.modification-tracker")

_MANIFEST_VERSION = 1

# ── Modification type constants ──────────────────────────────────────
MOD_SERVICE_ENABLED = "service_enabled"
MOD_SERVICE_DISABLED = "service_disabled"
MOD_FILE_CREATED = "file_created"
MOD_FILE_MODIFIED = "file_modified"
MOD_FILE_DELETED = "file_deleted"
MOD_CONFIG_CHANGED = "config_changed"
MOD_INITRAMFS_REBUILT = "initramfs_rebuilt"
MOD_MODPROBE_CONFIGURED = "modprobe_configured"

_SUBPROCESS_TIMEOUT = 30


class ModificationTracker:
    """Tracks and reverts system modifications made by Verde."""

    def __init__(
        self,
        base_dir: str = "/var/lib/verde",
        run: Any | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._manifest_path = os.path.join(base_dir, "modifications.json")
        self._lock_path = self._manifest_path + ".lock"
        self._run = run or self._default_run

    @staticmethod
    def _default_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
        kwargs.setdefault("timeout", _SUBPROCESS_TIMEOUT)
        return subprocess.run(cmd, **kwargs)

    # ── Manifest I/O ─────────────────────────────────────────────────

    def _load(self) -> dict:
        """Load manifest from disk. Returns empty manifest on missing/corrupt file."""
        try:
            with open(self._manifest_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("version") == _MANIFEST_VERSION:
                return data
            log.warning("Invalid manifest version — starting fresh")
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load manifest: %s — starting fresh", exc)
        return {"version": _MANIFEST_VERSION, "modifications": []}

    def _save(self, data: dict) -> None:
        """Atomically write manifest to disk."""
        os.makedirs(self._base_dir, mode=0o750, exist_ok=True)
        tmp_path = self._manifest_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self._manifest_path)

    def _with_lock(self, fn):
        """Execute fn while holding an exclusive file lock."""
        os.makedirs(self._base_dir, mode=0o750, exist_ok=True)
        with open(self._lock_path, "w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    # ── Public API ───────────────────────────────────────────────────

    def record(
        self,
        operation_id: str,
        mod_type: str,
        target: str,
        original_state: str | None,
        description: str,
    ) -> str:
        """Record a system modification. Returns the modification ID (UUID)."""
        mod_id = str(uuid.uuid4())
        entry = {
            "id": mod_id,
            "operation_id": operation_id,
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "type": mod_type,
            "target": target,
            "original_state": original_state,
            "description": description,
            "active": True,
        }

        def _do():
            data = self._load()
            data["modifications"].append(entry)
            self._save(data)

        self._with_lock(_do)
        log.info(
            "Recorded modification %s: %s on %s",
            mod_id,
            mod_type,
            target,
        )
        return mod_id

    def list_active(self) -> list[dict]:
        """Return all active modifications (newest first)."""
        data = self._load()
        active = [m for m in data["modifications"] if m.get("active", False)]
        active.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
        return active

    def list_all(self) -> list[dict]:
        """Return all modifications (newest first)."""
        data = self._load()
        mods = list(data["modifications"])
        mods.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
        return mods

    def revert(self, modification_id: str) -> bool:
        """Revert a modification and mark it inactive.

        Returns True on success, False if not found or already inactive.
        """
        result = {"success": False}

        def _do():
            data = self._load()
            for mod in data["modifications"]:
                if mod["id"] == modification_id:
                    if not mod.get("active", False):
                        log.info("Modification %s already inactive", modification_id)
                        return
                    try:
                        self._execute_revert(mod)
                        mod["active"] = False
                        self._save(data)
                        result["success"] = True
                        log.info("Reverted modification %s", modification_id)
                    except Exception:
                        log.exception("Failed to revert modification %s", modification_id)
                    return
            log.warning("Modification %s not found", modification_id)

        self._with_lock(_do)
        return result["success"]

    def mark_inactive(self, modification_id: str) -> bool:
        """Mark a modification as inactive without reverting.

        Returns True if found and updated, False otherwise.
        """
        result = {"success": False}

        def _do():
            data = self._load()
            for mod in data["modifications"]:
                if mod["id"] == modification_id and mod.get("active", False):
                    mod["active"] = False
                    self._save(data)
                    result["success"] = True
                    return

        self._with_lock(_do)
        return result["success"]

    # ── Revert handlers ──────────────────────────────────────────────

    def _execute_revert(self, mod: dict) -> None:
        """Execute the appropriate revert action for a modification."""
        mod_type = mod.get("type", "")
        target = mod.get("target", "")
        original = mod.get("original_state")

        if mod_type == MOD_SERVICE_ENABLED:
            self._revert_service_enabled(target)
        elif mod_type == MOD_SERVICE_DISABLED:
            self._revert_service_disabled(target)
        elif mod_type == MOD_FILE_CREATED:
            self._revert_file_created(target)
        elif mod_type == MOD_FILE_MODIFIED:
            self._revert_file_modified(target, original)
        elif mod_type == MOD_FILE_DELETED:
            self._revert_file_deleted(target, original)
        elif mod_type in (MOD_CONFIG_CHANGED, MOD_MODPROBE_CONFIGURED):
            self._revert_file_modified(target, original)
        elif mod_type == MOD_INITRAMFS_REBUILT:
            self._revert_initramfs()
        else:
            log.warning("Unknown modification type: %s", mod_type)

    def _revert_service_enabled(self, service: str) -> None:
        """Disable and stop a service that Verde enabled."""
        result = self._run(["systemctl", "disable", service])
        if result.returncode != 0:
            raise RuntimeError(f"systemctl disable {service} failed (rc={result.returncode})")
        result = self._run(["systemctl", "stop", service])
        if result.returncode != 0:
            log.warning(
                "systemctl stop %s failed (rc=%d) — service disabled but not stopped",
                service,
                result.returncode,
            )

    def _revert_service_disabled(self, service: str) -> None:
        """Enable and start a service that Verde disabled."""
        result = self._run(["systemctl", "enable", service])
        if result.returncode != 0:
            raise RuntimeError(f"systemctl enable {service} failed (rc={result.returncode})")
        result = self._run(["systemctl", "start", service])
        if result.returncode != 0:
            log.warning(
                "systemctl start %s failed (rc=%d) — service enabled but not started",
                service,
                result.returncode,
            )

    def _revert_file_created(self, path: str) -> None:
        """Remove a file that Verde created."""
        try:
            os.remove(path)
        except FileNotFoundError:
            log.debug("File already removed: %s", path)
        except OSError as exc:
            raise RuntimeError(f"Failed to remove {path}: {exc}") from exc

    def _revert_file_modified(self, path: str, original: str | None) -> None:
        """Restore original content of a file Verde modified."""
        if original is None:
            raise RuntimeError(f"No original state to restore for {path}")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)
        except OSError as exc:
            raise RuntimeError(f"Failed to restore {path}: {exc}") from exc

    def _revert_file_deleted(self, path: str, original: str | None) -> None:
        """Recreate a file that Verde deleted."""
        if original is None:
            raise RuntimeError(f"No original content to recreate for {path}")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)
        except OSError as exc:
            raise RuntimeError(f"Failed to recreate {path}: {exc}") from exc

    def _revert_initramfs(self) -> None:
        """Rebuild initramfs to reflect reverted changes."""
        try:
            result = self._run(["update-initramfs", "-u"], timeout=300)
            if result.returncode != 0:
                raise RuntimeError(f"update-initramfs failed (rc={result.returncode})")
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("update-initramfs timed out") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to run update-initramfs: {exc}") from exc
