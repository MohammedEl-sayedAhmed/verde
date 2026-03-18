"""Pre-flight validation checks for driver operations (FR11, FR44, FR51, FR75).

Each check is a private method returning a ``CheckResult``.  The public entry
point ``run_all_checks`` aggregates results into a ``PreflightResult`` whose
``overall_pass`` is ``True`` only when **zero** checks have ``fail`` status.
"""

from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of a single pre-flight check."""

    name: str
    status: str  # "pass", "fail", or "warn"
    description: str


@dataclass(slots=True)
class PreflightResult:
    """Aggregated result of all pre-flight checks."""

    overall_pass: bool = True
    checks: list[CheckResult] = field(default_factory=list)
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DISK_SPACE_FAIL_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB
_DISK_SPACE_WARN_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
_MIN_KERNEL_MAJOR = 5
_MIN_KERNEL_MINOR = 15
_SUBPROCESS_TIMEOUT = 10

# ---------------------------------------------------------------------------
# PreflightChecker
# ---------------------------------------------------------------------------


class PreflightChecker:
    """Runs all pre-flight checks for a given driver operation."""

    def run_all_checks(self, operation: str) -> PreflightResult:
        """Run every pre-flight check and return aggregated results."""
        start = time.monotonic()

        checks = [
            self._check_disk_space(),
            self._check_kernel_headers(),
            self._check_dpkg_state(),
            self._check_secure_boot(),
            self._check_kernel_compatibility(operation),
            self._check_dkms_status(),
        ]

        duration_ms = int((time.monotonic() - start) * 1000)
        overall_pass = all(c.status != "fail" for c in checks)

        return PreflightResult(
            overall_pass=overall_pass,
            checks=checks,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_disk_space(self) -> CheckResult:
        """Check available disk space on /."""
        try:
            st = os.statvfs("/")
            available = st.f_bavail * st.f_frsize
            available_mb = available // (1024 * 1024)

            if available >= _DISK_SPACE_WARN_BYTES:
                return CheckResult(
                    name="disk_space",
                    status="pass",
                    description=f"{available_mb / 1024:.1f} GB free on /",
                )
            if available >= _DISK_SPACE_FAIL_BYTES:
                return CheckResult(
                    name="disk_space",
                    status="warn",
                    description=(
                        f"Low disk space ({available_mb} MB free). Driver installation may fail."
                    ),
                )
            return CheckResult(
                name="disk_space",
                status="fail",
                description=(
                    f"Insufficient disk space ({available_mb} MB free). "
                    "At least 2 GB required for driver installation."
                ),
            )
        except OSError as exc:
            log.warning("statvfs failed: %s", exc)
            return CheckResult(
                name="disk_space",
                status="warn",
                description=f"Could not determine free disk space: {exc}",
            )

    def _check_kernel_headers(self) -> CheckResult:
        """Check that kernel headers are installed for the running kernel."""
        kernel = os.uname().release
        try:
            proc = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", f"linux-headers-{kernel}"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            if proc.returncode == 0 and "install ok installed" in proc.stdout:
                return CheckResult(
                    name="kernel_headers",
                    status="pass",
                    description=f"Kernel headers for {kernel} are installed",
                )
            return CheckResult(
                name="kernel_headers",
                status="fail",
                description=(
                    f"Kernel headers for {kernel} are not installed. "
                    f"Install with: sudo apt install linux-headers-{kernel}"
                ),
            )
        except FileNotFoundError:
            return CheckResult(
                name="kernel_headers",
                status="warn",
                description="dpkg-query not found. Cannot verify kernel headers.",
            )
        except subprocess.TimeoutExpired:
            log.warning("dpkg-query timed out checking kernel headers")
            return CheckResult(
                name="kernel_headers",
                status="warn",
                description="Timed out checking kernel headers",
            )

    def _check_dpkg_state(self) -> CheckResult:
        """Check for dpkg lock and broken packages."""
        # 1. Check dpkg lock
        locked, proc_name, pid = self._check_dpkg_lock()
        if locked:
            if proc_name:
                msg = (
                    f"Package manager is locked by {proc_name} (PID {pid}). "
                    "Wait for it to finish or terminate it."
                )
            else:
                msg = "Package manager is locked by another process. Wait for it to finish."
            return CheckResult(name="dpkg_state", status="fail", description=msg)

        # Permission denied — could not test the lock
        if proc_name == "permission_denied":
            return CheckResult(
                name="dpkg_state",
                status="warn",
                description="Cannot check dpkg lock: insufficient permissions.",
            )

        # 2. Check broken packages
        try:
            proc = subprocess.run(
                ["dpkg", "--audit"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            if proc.stdout.strip():
                return CheckResult(
                    name="dpkg_state",
                    status="fail",
                    description="Broken packages detected. Run 'sudo dpkg --configure -a' to repair.",
                )
        except FileNotFoundError:
            return CheckResult(
                name="dpkg_state",
                status="warn",
                description="dpkg not found. Cannot verify package state.",
            )
        except subprocess.TimeoutExpired:
            log.warning("dpkg --audit timed out")
            return CheckResult(
                name="dpkg_state",
                status="warn",
                description="Timed out checking package state",
            )

        return CheckResult(
            name="dpkg_state",
            status="pass",
            description="Package system is clean",
        )

    def _check_secure_boot(self) -> CheckResult:
        """Check Secure Boot and MOK enrollment status."""
        # Check if Secure Boot is enabled via mokutil --sb-state
        try:
            proc = subprocess.run(
                ["mokutil", "--sb-state"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            if "SecureBoot disabled" in proc.stdout:
                return CheckResult(
                    name="secure_boot",
                    status="pass",
                    description="Secure Boot is disabled. No MOK enrollment needed.",
                )
            if "SecureBoot enabled" not in proc.stdout:
                # Can't determine state
                return CheckResult(
                    name="secure_boot",
                    status="warn",
                    description="Cannot determine Secure Boot state.",
                )
        except FileNotFoundError:
            return CheckResult(
                name="secure_boot",
                status="warn",
                description=(
                    "Cannot determine Secure Boot MOK status. "
                    "Install mokutil for full pre-flight checks."
                ),
            )
        except subprocess.TimeoutExpired:
            log.warning("mokutil --sb-state timed out")
            return CheckResult(
                name="secure_boot",
                status="warn",
                description="Timed out checking Secure Boot state",
            )

        # Secure Boot is enabled — check MOK enrollment
        try:
            mok = subprocess.run(
                ["mokutil", "--list-enrolled"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            if mok.returncode == 0 and mok.stdout.strip():
                return CheckResult(
                    name="secure_boot",
                    status="pass",
                    description="Secure Boot is enabled with MOK keys enrolled",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return CheckResult(
            name="secure_boot",
            status="warn",
            description=(
                "Secure Boot is enabled but no MOK keys are enrolled. "
                "NVIDIA driver may require MOK enrollment after installation. "
                "You will be prompted to set a password for key enrollment on next reboot."
            ),
        )

    def _check_kernel_compatibility(self, operation: str) -> CheckResult:
        """Check kernel version is compatible with NVIDIA drivers."""
        kernel = os.uname().release
        try:
            # Parse major.minor from kernel version like "6.8.0-45-generic"
            parts = kernel.split(".")
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return CheckResult(
                name="kernel_compatibility",
                status="warn",
                description=f"Cannot parse kernel version: {kernel}",
            )

        if major < _MIN_KERNEL_MAJOR or (major == _MIN_KERNEL_MAJOR and minor < _MIN_KERNEL_MINOR):
            return CheckResult(
                name="kernel_compatibility",
                status="fail",
                description=(
                    f"Kernel {kernel} is too old. "
                    f"NVIDIA driver 535+ requires kernel {_MIN_KERNEL_MAJOR}.{_MIN_KERNEL_MINOR} or newer."
                ),
            )

        if major > 6:
            return CheckResult(
                name="kernel_compatibility",
                status="warn",
                description=(
                    f"Kernel {kernel} is newer than tested range. "
                    "Driver may work but is not officially validated."
                ),
            )

        return CheckResult(
            name="kernel_compatibility",
            status="pass",
            description=f"Kernel {kernel} is compatible with NVIDIA drivers",
        )

    def _check_dkms_status(self) -> CheckResult:
        """Check DKMS build status for NVIDIA modules."""
        try:
            proc = subprocess.run(
                ["dkms", "status"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError:
            return CheckResult(
                name="dkms_status",
                status="pass",
                description="DKMS not installed. Prebuilt kernel modules will be used.",
            )
        except subprocess.TimeoutExpired:
            log.warning("dkms status timed out")
            return CheckResult(
                name="dkms_status",
                status="warn",
                description="Timed out checking DKMS status",
            )

        # Parse DKMS output for nvidia entries
        broken: list[str] = []
        missing: list[str] = []
        found_nvidia = False

        for line in proc.stdout.splitlines():
            lower = line.lower()
            if "nvidia" not in lower:
                continue
            found_nvidia = True
            # Lines look like: nvidia/535.183.01, 6.8.0-45-generic, x86_64: installed
            if "broken" in lower or "error" in lower:
                # Extract kernel version from the line
                parts = line.split(",")
                kver = parts[1].strip() if len(parts) > 1 else "unknown"
                broken.append(kver)
            elif "added" in lower:
                parts = line.split(",")
                kver = parts[1].strip() if len(parts) > 1 else "unknown"
                missing.append(kver)

        if broken:
            versions = ", ".join(broken)
            return CheckResult(
                name="dkms_status",
                status="fail",
                description=(
                    f"DKMS nvidia module build failed for kernel(s): {versions}. "
                    "Check build logs with: dkms status"
                ),
            )

        if missing:
            versions = ", ".join(missing)
            return CheckResult(
                name="dkms_status",
                status="warn",
                description=(
                    f"DKMS nvidia module missing for kernel(s): {versions}. "
                    "Module will be built during installation."
                ),
            )

        if not found_nvidia:
            return CheckResult(
                name="dkms_status",
                status="pass",
                description="No NVIDIA DKMS modules registered. Prebuilt kernel modules assumed.",
            )

        return CheckResult(
            name="dkms_status",
            status="pass",
            description="DKMS modules built for all installed kernels",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_dpkg_lock() -> tuple[bool, str, int]:
        """Check if dpkg lock is held.

        Returns (locked, process_name, pid).  A third state is possible:
        if the lock file cannot be read due to permissions, returns
        (False, "permission_denied", 0) so the caller can emit a warn.
        """
        lock_path = "/var/lib/dpkg/lock-frontend"
        try:
            fd = os.open(lock_path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return (False, "", 0)
            except BlockingIOError:
                pid = PreflightChecker._find_lock_holder(lock_path)
                name = PreflightChecker._get_process_name(pid) if pid else "unknown"
                return (True, name, pid)
            finally:
                os.close(fd)
        except FileNotFoundError:
            return (False, "", 0)
        except PermissionError:
            log.warning("Cannot check dpkg lock: permission denied on %s", lock_path)
            return (False, "permission_denied", 0)

    @staticmethod
    def _find_lock_holder(lock_path: str) -> int:
        """Find PID of the process holding the lock via /proc/locks.

        Matches the inode of *lock_path* against /proc/locks entries to
        return only the PID that actually holds *this* lock file.
        """
        try:
            lock_stat = os.stat(lock_path)
            lock_inode = lock_stat.st_ino
        except OSError:
            return 0

        try:
            with open("/proc/locks") as f:
                for line in f:
                    if "FLOCK" not in line:
                        continue
                    parts = line.split()
                    # /proc/locks format:
                    # ID: TYPE MODE  OWNER PID MAJ:MIN:INODE ...
                    if len(parts) >= 6:
                        inode_field = parts[5]  # "MAJ:MIN:INODE"
                        inode_parts = inode_field.split(":")
                        if len(inode_parts) == 3:
                            try:
                                if int(inode_parts[2]) == lock_inode:
                                    return int(parts[4])
                            except ValueError:
                                continue
        except OSError:
            pass
        return 0

    @staticmethod
    def _get_process_name(pid: int) -> str:
        """Resolve PID to a process name via /proc/{pid}/comm."""
        try:
            with open(f"/proc/{pid}/comm") as f:
                return f.read().strip()
        except OSError:
            return "unknown"
