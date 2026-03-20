"""Module-not-loaded diagnosis and fix engine.

Diagnoses why the NVIDIA kernel module isn't loaded despite the driver
package being installed: missing headers, DKMS failures, kernel/distro
mismatch, Secure Boot, or blacklisting.  Provides fix actions for each
root cause.

External dependencies (subprocess calls, file reads) are injected for
testability.

References: Story 2.7.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable
from typing import Any

log = logging.getLogger("verde-daemon.module-doctor")

_SUBPROCESS_TIMEOUT = 30
_APT_TIMEOUT = 120
_DKMS_TIMEOUT = 300

# ── Cause constants ──────────────────────────────────────────────────
CAUSE_MISSING_HEADERS = "missing_headers"
CAUSE_DKMS_FAILED = "dkms_failed"
CAUSE_DKMS_MISSING = "dkms_missing"
CAUSE_KERNEL_MISMATCH = "kernel_mismatch"
CAUSE_SECURE_BOOT = "secure_boot"
CAUSE_BLACKLISTED = "blacklisted"
CAUSE_UNKNOWN = "unknown"


def _default_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", _SUBPROCESS_TIMEOUT)
    return subprocess.run(cmd, **kwargs)


def _default_read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


class ModuleDoctor:
    """Diagnoses and fixes NVIDIA module-not-loaded conditions."""

    def __init__(
        self,
        run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        read_file: Callable[[str], str] | None = None,
        list_modprobe_confs: Callable[[], list[str]] | None = None,
    ) -> None:
        self._run = run or _default_run
        self._read = read_file or _default_read_file
        self._list_confs = list_modprobe_confs or self._default_list_confs

    @staticmethod
    def _default_list_confs() -> list[str]:
        """List /etc/modprobe.d/*.conf files."""
        conf_dir = "/etc/modprobe.d"
        try:
            return [os.path.join(conf_dir, f) for f in os.listdir(conf_dir) if f.endswith(".conf")]
        except OSError:
            return []

    # ══════════════════════════════════════════════════════════════════
    # Diagnosis
    # ══════════════════════════════════════════════════════════════════

    def diagnose(self) -> dict[str, Any]:
        """Diagnose why the NVIDIA kernel module isn't loaded.

        Returns a dict with:
        - ``cause`` (str): one of the CAUSE_* constants
        - ``detail`` (str): human-readable explanation
        - ``fix_actions`` (list[str]): description of each fix step
        - ``reboot_required`` (bool)
        - ``packages`` (list[str]): packages to install (if any)
        - ``fixable`` (bool): whether an automated fix is available
        """
        kernel = os.uname().release

        # Check 1: Missing kernel headers
        result = self._check_missing_headers(kernel)
        if result:
            return result

        # Check 2: DKMS status
        result = self._check_dkms(kernel)
        if result:
            return result

        # Check 3: Kernel/distro mismatch
        result = self._check_kernel_mismatch(kernel)
        if result:
            return result

        # Check 4: Secure Boot with unsigned module
        result = self._check_secure_boot()
        if result:
            return result

        # Check 5: Module blacklisted
        result = self._check_blacklisted()
        if result:
            return result

        return {
            "cause": CAUSE_UNKNOWN,
            "detail": "Could not determine why the NVIDIA module is not loaded.",
            "fix_actions": [],
            "reboot_required": False,
            "packages": [],
            "fixable": False,
        }

    def _check_missing_headers(self, kernel: str) -> dict | None:
        """Check if kernel headers are installed for the running kernel."""
        pkg = f"linux-headers-{kernel}"
        try:
            result = self._run(
                ["dpkg-query", "-W", "-f=${Status}", pkg],
            )
            if result.returncode == 0 and "install ok installed" in result.stdout:
                return None  # Headers are installed
        except (subprocess.TimeoutExpired, OSError):
            pass

        return {
            "cause": CAUSE_MISSING_HEADERS,
            "detail": (
                f"Kernel headers for your running kernel ({kernel}) are not installed. "
                f"The NVIDIA driver needs these headers to build the kernel module."
            ),
            "fix_actions": [
                f"Install {pkg}",
                "Rebuild NVIDIA DKMS modules",
                "Load the NVIDIA kernel module",
            ],
            "reboot_required": False,
            "packages": [pkg],
            "fixable": True,
        }

    def _check_dkms(self, kernel: str) -> dict | None:
        """Check DKMS status for NVIDIA modules."""
        try:
            result = self._run(["dkms", "status"])
            if result.returncode != 0:
                return None  # Can't determine DKMS status
        except (subprocess.TimeoutExpired, OSError):
            return None

        lines = result.stdout.strip().splitlines()
        nvidia_lines = [ln for ln in lines if "nvidia" in ln.lower()]

        if not nvidia_lines:
            # No NVIDIA DKMS source at all
            return {
                "cause": CAUSE_DKMS_MISSING,
                "detail": (
                    "No NVIDIA DKMS source packages are installed. "
                    "The driver package may not include DKMS support."
                ),
                "fix_actions": ["Reinstall the NVIDIA driver with DKMS support"],
                "reboot_required": False,
                "packages": [],
                "fixable": False,
            }

        # Check for failed builds for the running kernel
        for line in nvidia_lines:
            # dkms status format: "nvidia/535.183.01, 6.8.0-106-generic, x86_64: installed"
            # Use delimited match to avoid substring collisions (e.g. 6.8.0-10 vs 6.8.0-106)
            if (kernel + ",") in line or (kernel + ":") in line or line.strip().endswith(kernel):
                status_part = line.rsplit(":", 1)[-1].strip().lower()
                if status_part == "installed":
                    return None  # Module is built for this kernel — issue is elsewhere
                if status_part in ("broken", ""):
                    return {
                        "cause": CAUSE_DKMS_FAILED,
                        "detail": (
                            "The NVIDIA DKMS module build failed for your running kernel. "
                            "This can happen after a kernel update or driver upgrade."
                        ),
                        "fix_actions": [
                            "Retry DKMS module build",
                            "Load the NVIDIA kernel module",
                        ],
                        "reboot_required": False,
                        "packages": [],
                        "fixable": True,
                    }

        # NVIDIA DKMS exists but no entry for running kernel — needs build
        return {
            "cause": CAUSE_DKMS_FAILED,
            "detail": (
                f"The NVIDIA DKMS module has not been built for your running kernel ({kernel}). "
                "A DKMS rebuild should fix this."
            ),
            "fix_actions": [
                "Build NVIDIA DKMS module for the running kernel",
                "Load the NVIDIA kernel module",
            ],
            "reboot_required": False,
            "packages": [],
            "fixable": True,
        }

    def _check_kernel_mismatch(self, kernel: str) -> dict | None:
        """Check if the running kernel matches the current distro."""
        os_release = self._read("/etc/os-release")
        codename = ""
        for line in os_release.splitlines():
            if line.startswith("VERSION_CODENAME="):
                codename = line.split("=", 1)[1].strip().strip("'\"").lower()
                break

        if not codename:
            return None

        # Check if the running kernel package is from the current distro
        try:
            result = self._run(
                ["apt-cache", "show", f"linux-headers-{kernel}"],
            )
            if result.returncode != 0:
                # Headers package not available in repos — likely mismatch
                return {
                    "cause": CAUSE_KERNEL_MISMATCH,
                    "detail": (
                        f"Your running kernel ({kernel}) does not appear to be from the "
                        f"current {codename.capitalize()} repositories. "
                        f"This often happens after upgrading Ubuntu without updating the kernel."
                    ),
                    "fix_actions": [
                        f"Install the recommended {codename.capitalize()} kernel",
                        "Install matching kernel headers",
                        "Rebuild NVIDIA DKMS modules",
                        "Reboot into the new kernel",
                    ],
                    "reboot_required": True,
                    "packages": [
                        "linux-generic",
                        "linux-headers-generic",
                    ],
                    "fixable": True,
                }
        except (subprocess.TimeoutExpired, OSError):
            pass

        return None

    def _check_secure_boot(self) -> dict | None:
        """Check for Secure Boot with unsigned NVIDIA module."""
        try:
            result = self._run(["mokutil", "--sb-state"])
            if result.returncode != 0:
                return None
            if "secureboot enabled" not in result.stdout.lower():
                return None
        except (subprocess.TimeoutExpired, OSError):
            return None

        # Secure Boot is enabled — check if NVIDIA module is signed
        try:
            result = self._run(
                ["mokutil", "--test-key", "/var/lib/shim-signed/mok/MOK.der"],
            )
            if result.returncode == 0 and "already enrolled" in result.stdout.lower():
                return None  # MOK is enrolled
        except (subprocess.TimeoutExpired, OSError):
            pass

        return {
            "cause": CAUSE_SECURE_BOOT,
            "detail": (
                "Secure Boot is enabled but the NVIDIA kernel module is not signed. "
                "You need to enroll a Machine Owner Key (MOK) to load unsigned modules. "
                "This requires a one-time reboot and physical interaction with the MOK manager."
            ),
            "fix_actions": [
                "Enroll MOK key (requires reboot and physical interaction)",
            ],
            "reboot_required": True,
            "packages": [],
            "fixable": False,  # Requires physical interaction
        }

    def _check_blacklisted(self) -> dict | None:
        """Check if the NVIDIA module is blacklisted in modprobe.d."""
        blacklist_files: list[str] = []

        for conf_path in self._list_confs():
            content = self._read(conf_path)
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if re.match(r"blacklist\s+nvidia\b", stripped):
                    blacklist_files.append(conf_path)
                    break

        if not blacklist_files:
            return None

        files_str = ", ".join(blacklist_files)
        return {
            "cause": CAUSE_BLACKLISTED,
            "detail": (
                f"The NVIDIA module is blacklisted in: {files_str}. "
                f"This prevents the module from loading at boot."
            ),
            "fix_actions": [
                f"Remove blacklist entries from {files_str}",
                "Load the NVIDIA kernel module",
            ],
            "reboot_required": False,
            "packages": [],
            "fixable": True,
            "blacklist_files": blacklist_files,
        }

    # ══════════════════════════════════════════════════════════════════
    # Fix actions
    # ══════════════════════════════════════════════════════════════════

    def fix_module(
        self,
        diagnosis: dict[str, Any],
        op_id: str,
        progress_cb: Callable[[str, float, str], None],
        complete_cb: Callable[[str, bool, str], None],
        reboot_cb: Callable[[bool, str], None] | None = None,
    ) -> None:
        """Apply the fix for the diagnosed cause."""
        cause = diagnosis.get("cause", CAUSE_UNKNOWN)

        try:
            if cause == CAUSE_MISSING_HEADERS:
                self._fix_missing_headers(diagnosis, op_id, progress_cb, complete_cb)
            elif cause == CAUSE_DKMS_FAILED:
                self._fix_dkms_rebuild(op_id, progress_cb, complete_cb)
            elif cause == CAUSE_KERNEL_MISMATCH:
                self._fix_kernel_mismatch(diagnosis, op_id, progress_cb, complete_cb, reboot_cb)
            elif cause == CAUSE_BLACKLISTED:
                self._fix_blacklisted(diagnosis, op_id, progress_cb, complete_cb)
            elif cause == CAUSE_SECURE_BOOT:
                complete_cb(
                    op_id,
                    False,
                    "Secure Boot MOK enrollment requires manual reboot and physical interaction. "
                    "Please run: sudo mokutil --import /var/lib/shim-signed/mok/MOK.der",
                )
            elif cause == CAUSE_DKMS_MISSING:
                complete_cb(
                    op_id,
                    False,
                    "No NVIDIA DKMS source is installed. "
                    "Reinstall the driver package with DKMS support.",
                )
            else:
                complete_cb(
                    op_id, False, "Unable to determine the cause. Manual troubleshooting required."
                )
        except Exception as exc:
            log.exception("Unhandled error in fix_module for %s", op_id)
            complete_cb(op_id, False, f"Unexpected error: {exc}")

    def _fix_missing_headers(
        self,
        diagnosis: dict,
        op_id: str,
        progress_cb: Callable,
        complete_cb: Callable,
    ) -> None:
        """Install kernel headers, rebuild DKMS, load module."""
        packages = diagnosis.get("packages", [])
        if not packages:
            complete_cb(op_id, False, "No packages specified for header installation.")
            return
        kernel = os.uname().release

        # Step 1: Install headers
        progress_cb(op_id, 10.0, f"Installing kernel headers for {kernel}...")
        try:
            result = self._run(
                ["apt-get", "install", "-y", *packages],
                timeout=_APT_TIMEOUT,
            )
            if result.returncode != 0:
                complete_cb(
                    op_id, False, f"Failed to install kernel headers: {result.stderr.strip()}"
                )
                return
        except subprocess.TimeoutExpired:
            complete_cb(op_id, False, "Timed out installing kernel headers")
            return

        # Step 2: DKMS rebuild
        progress_cb(op_id, 50.0, "Rebuilding NVIDIA DKMS modules...")
        ok, msg = self._run_dkms_autoinstall()
        if not ok:
            complete_cb(op_id, False, msg)
            return

        # Step 3: Load module
        progress_cb(op_id, 85.0, "Loading NVIDIA kernel module...")
        ok, msg = self._try_modprobe()
        if not ok:
            complete_cb(op_id, False, msg)
            return

        progress_cb(op_id, 100.0, "Module loaded successfully")
        complete_cb(
            op_id, True, "Kernel headers installed, DKMS rebuilt, and NVIDIA module loaded."
        )

    def _fix_dkms_rebuild(
        self,
        op_id: str,
        progress_cb: Callable,
        complete_cb: Callable,
    ) -> None:
        """Retry DKMS autoinstall and load module."""
        progress_cb(op_id, 20.0, "Rebuilding NVIDIA DKMS modules...")
        ok, msg = self._run_dkms_autoinstall()
        if not ok:
            complete_cb(op_id, False, msg)
            return

        progress_cb(op_id, 75.0, "Loading NVIDIA kernel module...")
        ok, msg = self._try_modprobe()
        if not ok:
            complete_cb(op_id, False, msg)
            return

        progress_cb(op_id, 100.0, "Module loaded successfully")
        complete_cb(op_id, True, "DKMS modules rebuilt and NVIDIA module loaded.")

    def _fix_kernel_mismatch(
        self,
        diagnosis: dict,
        op_id: str,
        progress_cb: Callable,
        complete_cb: Callable,
        reboot_cb: Callable | None,
    ) -> None:
        """Install correct kernel, headers, rebuild DKMS."""
        packages = diagnosis.get("packages", [])
        if not packages:
            complete_cb(op_id, False, "No packages specified for kernel installation.")
            return

        progress_cb(op_id, 10.0, "Installing correct kernel and headers...")
        try:
            result = self._run(
                ["apt-get", "install", "-y", *packages],
                timeout=_APT_TIMEOUT,
            )
            if result.returncode != 0:
                complete_cb(op_id, False, f"Failed to install kernel: {result.stderr.strip()}")
                return
        except subprocess.TimeoutExpired:
            complete_cb(op_id, False, "Timed out installing kernel packages")
            return

        progress_cb(op_id, 60.0, "Rebuilding NVIDIA DKMS modules...")
        ok, msg = self._run_dkms_autoinstall()
        if not ok:
            complete_cb(op_id, False, msg)
            return

        progress_cb(op_id, 95.0, "Kernel installed. Reboot required.")
        complete_cb(
            op_id,
            True,
            "Correct kernel and headers installed, DKMS rebuilt. Reboot required to use the new kernel.",
        )
        if reboot_cb:
            reboot_cb(
                True, "Reboot required to boot into the new kernel with NVIDIA module support."
            )

    def _fix_blacklisted(
        self,
        diagnosis: dict,
        op_id: str,
        progress_cb: Callable,
        complete_cb: Callable,
    ) -> None:
        """Remove blacklist entries and load module."""
        blacklist_files = diagnosis.get("blacklist_files", [])

        if not blacklist_files:
            complete_cb(op_id, False, "No blacklist files specified in diagnosis.")
            return

        # Validate all paths are under /etc/modprobe.d/
        for fpath in blacklist_files:
            real = os.path.realpath(fpath)
            if not real.startswith("/etc/modprobe.d/"):
                complete_cb(
                    op_id, False, f"Refusing to modify file outside /etc/modprobe.d/: {fpath}"
                )
                return

        progress_cb(op_id, 20.0, "Removing blacklist entries...")
        for fpath in blacklist_files:
            content = self._read(fpath)
            new_lines = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    new_lines.append(line)
                    continue
                if re.match(r"blacklist\s+nvidia\b", stripped):
                    new_lines.append(f"# Removed by Verde: {line}")
                else:
                    new_lines.append(line)
            try:
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(new_lines) + "\n")
            except OSError as exc:
                complete_cb(op_id, False, f"Failed to update {fpath}: {exc}")
                return

        progress_cb(op_id, 60.0, "Loading NVIDIA kernel module...")
        ok, msg = self._try_modprobe()
        if not ok:
            complete_cb(op_id, False, msg)
            return

        progress_cb(op_id, 100.0, "Module loaded successfully")
        complete_cb(op_id, True, "Blacklist entries removed and NVIDIA module loaded.")

    # ── Helpers ───────────────────────────────────────────────────────

    def _run_dkms_autoinstall(self) -> tuple[bool, str]:
        """Run dkms autoinstall.  Returns (success, message)."""
        try:
            result = self._run(
                ["dkms", "autoinstall"],
                timeout=_DKMS_TIMEOUT,
            )
            if result.returncode == 0:
                return True, "DKMS autoinstall completed"
            return False, f"DKMS autoinstall failed: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return False, "DKMS autoinstall timed out"
        except OSError as exc:
            return False, f"Error running dkms: {exc}"

    def _try_modprobe(self) -> tuple[bool, str]:
        """Try to load the NVIDIA kernel module.  Returns (success, message)."""
        try:
            result = self._run(["modprobe", "nvidia"])
            if result.returncode == 0:
                return True, "NVIDIA module loaded"
            return False, f"modprobe nvidia failed: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return False, "modprobe timed out"
        except OSError as exc:
            return False, f"Error running modprobe: {exc}"
