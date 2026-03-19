"""Pending summary manager — cross-reboot operation state tracking.

Writes a persistent state file before reboot-requiring operations and reads
it back after reboot to compute a user-facing summary of what changed.

File location: ``/var/lib/verde/pending-summary.json``

References: FR61; AC#1-#4 of Story 3.5.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib

log = logging.getLogger("verde-daemon.pending-summary")

STATE_FILE_NAME = "pending-summary.json"
VERDE_DATA_DIR = pathlib.Path("/var/lib/verde")


class PendingSummaryManager:
    """Manages the cross-reboot pending-summary state file.

    Parameters
    ----------
    state_dir : pathlib.Path
        Directory for the state file (default: ``/var/lib/verde``).
    """

    def __init__(self, state_dir: pathlib.Path = VERDE_DATA_DIR) -> None:
        self._state_dir = state_dir
        self._state_file = state_dir / STATE_FILE_NAME

    @property
    def state_file(self) -> pathlib.Path:
        """Path to the pending summary state file."""
        return self._state_file

    def write_pending(
        self,
        operation_type: str,
        previous_version: str,
        expected_version: str,
        operation_id: str,
    ) -> None:
        """Write pending summary atomically.

        Parameters
        ----------
        operation_type : str
            One of ``"install"`` or ``"rollback"``.
        previous_version : str
            Driver version before the operation.
        expected_version : str
            Driver version expected after reboot.
        operation_id : str
            Unique operation identifier.

        Raises
        ------
        ValueError
            If *operation_type* is not ``"install"`` or ``"rollback"``.
        OSError
            If the state file cannot be written.
        """
        if operation_type not in ("install", "rollback"):
            msg = f"Invalid operation_type: {operation_type!r}"
            raise ValueError(msg)

        data = {
            "operation_type": operation_type,
            "previous_version": previous_version,
            "expected_version": expected_version,
            "operation_id": operation_id,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "kernel_version": _get_kernel_version(),
        }

        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_file.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp_path), str(self._state_file))
        log.info(
            "Pending summary written: %s %s -> %s",
            operation_type,
            previous_version,
            expected_version,
        )

    def has_pending(self) -> bool:
        """Return True if a pending summary state file exists."""
        return self._state_file.is_file()

    def read_pending(self) -> dict | None:
        """Read and return the pending summary, or None if missing/corrupt.

        Corrupt files are preserved (not auto-deleted) for debugging.
        """
        if not self._state_file.is_file():
            return None
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                log.warning("Pending summary is not a JSON object — ignoring")
                return None
            return data
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Cannot read pending summary: %s", exc)
            return None

    def clear_pending(self) -> None:
        """Delete the pending summary state file if it exists."""
        try:
            self._state_file.unlink()
            log.info("Pending summary cleared")
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Cannot clear pending summary: %s", exc)

    def compute_post_reboot_summary(
        self,
        pending_data: dict,
        nvml_wrapper: object,
    ) -> dict:
        """Compute post-reboot summary by comparing expected vs actual state.

        Parameters
        ----------
        pending_data : dict
            Data from :meth:`read_pending`.
        nvml_wrapper : object
            NVML wrapper with ``get_driver_version()`` and ``get_device_name()``
            methods.  May return ``Unavailable`` sentinel values.

        Returns
        -------
        dict
            Summary with keys: ``operation_type``, ``previous_version``,
            ``expected_version``, ``current_version``, ``gpu_healthy``,
            ``result``, ``message``, ``recovery_guidance``, ``has_pending``.
        """
        op_type = pending_data.get("operation_type", "install")
        previous = pending_data.get("previous_version", "unknown")
        expected = pending_data.get("expected_version", "unknown")

        # Query actual state via NVML
        current_version = _safe_nvml_call(nvml_wrapper, "get_driver_version")
        gpu_name = _safe_nvml_call(nvml_wrapper, "get_device_name")

        gpu_healthy = current_version is not None and gpu_name is not None

        if current_version is None:
            result = "failed"
            message = _make_failed_message(op_type, previous, expected)
            guidance = (
                "The GPU driver does not appear to be loaded. You can try: "
                "(1) Open the Drivers view to reinstall, or "
                "(2) Run 'verde --repair' from a terminal for recovery options."
            )
        elif _version_matches(current_version, expected):
            result = "success"
            message = _make_success_message(op_type, previous, expected)
            guidance = ""
        else:
            result = "partial"
            message = _make_partial_message(op_type, previous, expected, current_version)
            guidance = (
                "The installed driver version differs from what was expected. "
                "This may be normal if the system resolved a different package version. "
                "Check the Drivers view for details."
            )

        return {
            "operation_type": op_type,
            "previous_version": previous,
            "expected_version": expected,
            "current_version": current_version or "",
            "gpu_healthy": gpu_healthy,
            "result": result,
            "message": message,
            "recovery_guidance": guidance,
            "has_pending": True,
        }


# ── Helpers ────────────────────────────────────────────────────────────


def _version_matches(current: str, expected: str) -> bool:
    """Check if *current* driver version matches *expected*.

    Handles the common case where *expected* is a short package version
    (e.g. ``"550"``) and *current* is the full NVML version
    (e.g. ``"550.35.03"``).  Falls back to exact equality.
    """
    if not current or not expected:
        return False
    if current == expected:
        return True
    # "550" matches "550.35.03" — expected is a prefix up to the first dot
    return current.startswith(expected + ".") or expected.startswith(current + ".")


def _get_kernel_version() -> str:
    """Return the current kernel version string."""
    return os.uname().release


def _safe_nvml_call(nvml_wrapper: object, method_name: str) -> str | None:
    """Call an NVML wrapper method, returning None on failure.

    Handles both the ``Unavailable`` sentinel (falsy) and exceptions.
    """
    try:
        if method_name == "get_device_name":
            result = _get_first_gpu_name(nvml_wrapper)
        else:
            fn = getattr(nvml_wrapper, method_name)
            result = fn()
        # The NvmlWrapper Unavailable sentinel is falsy (bool(Unavailable) == False)
        if not result:
            return None
        return str(result)
    except Exception:
        return None


def _get_first_gpu_name(nvml_wrapper: object) -> str | None:
    """Get the name of the first GPU device."""
    try:
        count_fn = getattr(nvml_wrapper, "get_device_count", None)
        handle_fn = getattr(nvml_wrapper, "get_handle_by_index", None)
        name_fn = getattr(nvml_wrapper, "get_device_name", None)
        if count_fn is None or handle_fn is None or name_fn is None:
            return None
        count = count_fn()
        if not isinstance(count, int) or count < 1:
            return None
        handle = handle_fn(0)
        if handle is None:
            return None
        return name_fn(handle)
    except Exception:
        return None


def _make_success_message(op_type: str, previous: str, expected: str) -> str:
    if op_type == "rollback":
        return f"Driver rolled back from {previous} to {expected} \u2014 GPU is healthy"
    return f"Driver updated from {previous} to {expected} \u2014 GPU is healthy"


def _make_partial_message(op_type: str, previous: str, expected: str, current: str) -> str:
    action = "rolled back" if op_type == "rollback" else "changed"
    return (
        f"Driver {action} but version differs from expected \u2014 "
        f"found {current} instead of {expected}. "
        "This may be normal if apt resolved a different package version."
    )


def _make_failed_message(op_type: str, previous: str, expected: str) -> str:
    action = "rollback" if op_type == "rollback" else "update"
    return (
        f"Driver {action} may have failed \u2014 no NVIDIA driver is currently loaded. "
        "Your GPU may be running on nouveau or without acceleration."
    )
