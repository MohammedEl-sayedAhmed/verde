"""Power and suspend issue detection and fix application for the Verde daemon.

Detects NVIDIA suspend/resume service issues, hibernate configuration
problems, Secure Boot MOK enrollment status, and Wayland-specific
NVIDIA configuration issues.  Also applies one-click fixes for suspend
and hibernate problems.

External dependencies (subprocess calls, file reads/writes) are injected
for testability.

References: FR24-FR30, FR56, FR89, FR92; Stories 4.1, 4.2.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
from collections.abc import Callable
from typing import Any

log = logging.getLogger("verde-daemon.power-manager")

# ── Issue type constants ──────────────────────────────────────────────
ISSUE_SUSPEND = "suspend"
ISSUE_HIBERNATE = "hibernate"
ISSUE_SECURE_BOOT = "secure_boot"
ISSUE_WAYLAND = "wayland"

# ── Severity constants ────────────────────────────────────────────────
SEVERITY_OK = "ok"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

# ── Overall status constants ──────────────────────────────────────────
STATUS_WORKING = "working"
STATUS_ISSUES_FOUND = "issues_found"
STATUS_UNKNOWN = "unknown"

# ── Service constants (Story 4.2) ────────────────────────────────────
NVIDIA_SUSPEND_SERVICE = "nvidia-suspend.service"
NVIDIA_RESUME_SERVICE = "nvidia-resume.service"
NVIDIA_HIBERNATE_SERVICE = "nvidia-hibernate.service"

MODPROBE_CONF_PATH = "/etc/modprobe.d/nvidia-power-management.conf"
MODPROBE_CONTENT = (
    "options nvidia NVreg_PreserveVideoMemoryAllocations=1\n"
    "options nvidia NVreg_TemporaryFilePath=/var/tmp\n"
)

_SUSPEND_SERVICES = (NVIDIA_SUSPEND_SERVICE, NVIDIA_RESUME_SERVICE, NVIDIA_HIBERNATE_SERVICE)

# ── Default subprocess runner ─────────────────────────────────────────
_SUBPROCESS_TIMEOUT = 10
_INITRAMFS_TIMEOUT = 120


def _default_run(
    cmd: list[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with safe defaults."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", _SUBPROCESS_TIMEOUT)
    return subprocess.run(cmd, **kwargs)


def _default_list_modprobe_confs() -> list[str]:
    """List /etc/modprobe.d/*.conf files."""
    import glob as globmod

    return globmod.glob("/etc/modprobe.d/*.conf")


def _default_read_file(path: str) -> str:
    """Read a file and return its contents, or empty string on error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _default_write_file(path: str, content: str) -> None:
    """Write *content* to *path* atomically (write tmp, then rename)."""
    import tempfile

    parent = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".verde-")
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.rename(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _make_issue(
    issue_type: str,
    severity: str,
    summary: str,
    detail: str,
    fixable: bool,
    already_fixed: bool = False,
) -> dict[str, Any]:
    """Build a single issue dict matching the D-Bus a{sv} schema."""
    return {
        "type": issue_type,
        "severity": severity,
        "summary": summary,
        "detail": detail,
        "fixable": fixable,
        "already_fixed": already_fixed,
    }


class PowerManager:
    """Detects power/suspend issues and reports structured status.

    Parameters
    ----------
    run : callable, optional
        Subprocess runner (default: :func:`_default_run`).
    read_file : callable, optional
        File reader (default: :func:`_default_read_file`).
    """

    def __init__(
        self,
        run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        read_file: Callable[[str], str] | None = None,
        list_modprobe_confs: Callable[[], list[str]] | None = None,
        write_file: Callable[[str, str], None] | None = None,
        tracker: Any | None = None,
    ) -> None:
        self._run = run or _default_run
        self._read = read_file or _default_read_file
        self._tracker = tracker
        self._list_modprobe_confs = list_modprobe_confs or _default_list_modprobe_confs
        self._write = write_file or _default_write_file

    def get_power_status(self) -> dict[str, Any]:
        """Return full power status dict suitable for D-Bus ``a{sv}`` wrapping.

        Calls each checker, collects issues, computes overall status.
        Individual checker failures are caught and reported as ``unknown``
        severity issues so that remaining checks still run.
        """
        issues: list[dict[str, Any]] = []
        has_unknown = False

        suspend_active = False
        hibernate_active = False
        secure_boot_enabled = False
        mok_enrolled = False
        wayland_session = False

        # ── Suspend services ──────────────────────────────────────────
        try:
            suspend_issues, suspend_active = self._check_suspend_services()
            issues.extend(suspend_issues)
        except Exception:
            log.exception("Suspend service check failed")
            has_unknown = True
            issues.append(
                _make_issue(
                    ISSUE_SUSPEND,
                    SEVERITY_WARNING,
                    "Could not check suspend services",
                    "An error occurred while checking NVIDIA suspend service status.",
                    fixable=False,
                )
            )

        # ── Hibernate ─────────────────────────────────────────────────
        try:
            hibernate_issues, hibernate_active = self._check_hibernate()
            issues.extend(hibernate_issues)
        except Exception:
            log.exception("Hibernate check failed")
            has_unknown = True
            issues.append(
                _make_issue(
                    ISSUE_HIBERNATE,
                    SEVERITY_WARNING,
                    "Could not check hibernate configuration",
                    "An error occurred while checking hibernate prerequisites.",
                    fixable=False,
                )
            )

        # ── Secure Boot / MOK ─────────────────────────────────────────
        try:
            sb_issues, secure_boot_enabled, mok_enrolled = self._check_secure_boot()
            issues.extend(sb_issues)
        except Exception:
            log.exception("Secure Boot check failed")
            has_unknown = True
            issues.append(
                _make_issue(
                    ISSUE_SECURE_BOOT,
                    SEVERITY_WARNING,
                    "Could not check Secure Boot status",
                    "An error occurred while checking Secure Boot and MOK enrollment.",
                    fixable=False,
                )
            )

        # ── Wayland ───────────────────────────────────────────────────
        try:
            wayland_issues, wayland_session = self._check_wayland_issues()
            issues.extend(wayland_issues)
        except Exception:
            log.exception("Wayland check failed")
            has_unknown = True
            issues.append(
                _make_issue(
                    ISSUE_WAYLAND,
                    SEVERITY_WARNING,
                    "Could not check Wayland configuration",
                    "An error occurred while checking Wayland NVIDIA settings.",
                    fixable=False,
                )
            )

        # ── Compute overall status ────────────────────────────────────
        if has_unknown:
            overall = STATUS_UNKNOWN
        elif any(i["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL) for i in issues):
            overall = STATUS_ISSUES_FOUND
        else:
            overall = STATUS_WORKING

        return {
            "overall_status": overall,
            "issues": issues,
            "suspend_service_active": suspend_active,
            "hibernate_service_active": hibernate_active,
            "secure_boot_enabled": secure_boot_enabled,
            "mok_enrolled": mok_enrolled,
            "wayland_session": wayland_session,
        }

    # ── Task 2: Suspend service detection ───────────────────────────────

    def _check_suspend_services(
        self,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Check NVIDIA suspend/resume systemd services.

        Returns (issues, suspend_active) where *suspend_active* is True only
        when both ``nvidia-suspend.service`` and ``nvidia-resume.service`` are
        enabled.
        """
        issues: list[dict[str, Any]] = []

        # Critical services — must be enabled for suspend to work
        critical_services = ["nvidia-suspend.service", "nvidia-resume.service"]
        # Optional service — nice to have
        optional_services = ["nvidia-powerd.service"]

        all_critical_ok = True
        for svc in critical_services:
            enabled = self._is_service_enabled(svc)
            if not enabled:
                all_critical_ok = False
                issues.append(
                    _make_issue(
                        ISSUE_SUSPEND,
                        SEVERITY_CRITICAL,
                        f"{svc} is not enabled",
                        f"NVIDIA suspend services are not enabled. Your system may "
                        f"not resume correctly from sleep. The service {svc} needs "
                        f"to be enabled for proper GPU state save/restore during suspend.",
                        fixable=True,
                    )
                )

        for svc in optional_services:
            enabled = self._is_service_enabled(svc)
            if not enabled:
                issues.append(
                    _make_issue(
                        ISSUE_SUSPEND,
                        SEVERITY_WARNING,
                        f"{svc} is not enabled",
                        f"The NVIDIA power daemon ({svc}) is not enabled. This service "
                        f"provides dynamic power management for Ampere and newer GPUs. "
                        f"It is optional but recommended for better power efficiency.",
                        fixable=True,
                    )
                )

        if all_critical_ok:
            issues.append(
                _make_issue(
                    ISSUE_SUSPEND,
                    SEVERITY_OK,
                    "NVIDIA suspend services are properly configured",
                    "All required NVIDIA suspend/resume services are enabled.",
                    fixable=False,
                    already_fixed=True,
                )
            )

        return issues, all_critical_ok

    def _is_service_enabled(self, service: str) -> bool:
        """Check if a systemd service is enabled (not disabled/masked).

        Accepts ``enabled``, ``static``, and ``enabled-runtime`` as positive
        states — all three mean the unit will activate when needed.
        """
        try:
            result = self._run(["systemctl", "is-enabled", service])
            status = result.stdout.strip().lower()
            return status in ("enabled", "static", "enabled-runtime")
        except (subprocess.TimeoutExpired, OSError):
            return False

    # ── Task 3: Hibernate detection ──────────────────────────────────

    def _check_hibernate(
        self,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Check hibernate prerequisites.

        Returns (issues, hibernate_active) where *hibernate_active* is True
        when the hibernate service is enabled and basic prerequisites are met.
        """
        issues: list[dict[str, Any]] = []
        problems_found = False

        # 1. nvidia-hibernate.service enabled?
        hibernate_enabled = self._is_service_enabled("nvidia-hibernate.service")
        if not hibernate_enabled:
            problems_found = True
            issues.append(
                _make_issue(
                    ISSUE_HIBERNATE,
                    SEVERITY_CRITICAL,
                    "nvidia-hibernate.service is not enabled",
                    "The NVIDIA hibernate service is not enabled. Without it, "
                    "hibernation may fail or cause GPU issues on resume. Enable "
                    "it with: systemctl enable nvidia-hibernate.service",
                    fixable=True,
                )
            )

        # 2. Kernel supports hibernate? (/sys/power/state contains 'disk')
        power_state = self._read("/sys/power/state")
        if "disk" not in power_state:
            problems_found = True
            issues.append(
                _make_issue(
                    ISSUE_HIBERNATE,
                    SEVERITY_WARNING,
                    "Kernel does not support hibernate",
                    "The kernel does not advertise hibernate support "
                    "(/sys/power/state does not contain 'disk'). This may "
                    "be a kernel configuration issue.",
                    fixable=False,
                )
            )

        # 3. resume= parameter in /proc/cmdline
        cmdline = self._read("/proc/cmdline")
        if "resume=" not in cmdline:
            problems_found = True
            issues.append(
                _make_issue(
                    ISSUE_HIBERNATE,
                    SEVERITY_CRITICAL,
                    "Missing resume= kernel parameter",
                    "The kernel command line does not contain a resume= parameter "
                    "pointing to a swap device. Without this, the system cannot "
                    "resume from hibernation. Add resume=UUID=<swap-uuid> to your "
                    "bootloader configuration.",
                    fixable=True,
                )
            )

        # 4. Swap configured?
        swaps = self._read("/proc/swaps")
        swap_lines = [line for line in swaps.strip().splitlines()[1:] if line.strip()]
        if not swap_lines:
            problems_found = True
            issues.append(
                _make_issue(
                    ISSUE_HIBERNATE,
                    SEVERITY_CRITICAL,
                    "No swap configured",
                    "No swap partition or file is configured. Hibernate requires "
                    "swap space at least as large as your RAM to save the system "
                    "state to disk.",
                    fixable=False,
                )
            )

        # 5. sleep.conf blocking hibernate?
        sleep_conf = self._read("/etc/systemd/sleep.conf")
        for line in sleep_conf.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if (
                key == "HibernateMode"
                and value
                and value not in ("platform", "shutdown", "platform shutdown")
            ):
                problems_found = True
                issues.append(
                    _make_issue(
                        ISSUE_HIBERNATE,
                        SEVERITY_WARNING,
                        "systemd sleep.conf may block hibernate",
                        f"The HibernateMode in /etc/systemd/sleep.conf is set to "
                        f"'{value}', which may prevent proper hibernation. Expected "
                        f"'platform' or 'shutdown'.",
                        fixable=True,
                    )
                )

        if not problems_found:
            issues.append(
                _make_issue(
                    ISSUE_HIBERNATE,
                    SEVERITY_OK,
                    "Hibernate is properly configured",
                    "All hibernate prerequisites are met: service enabled, swap "
                    "configured, resume parameter present.",
                    fixable=False,
                    already_fixed=True,
                )
            )

        return issues, hibernate_enabled and not problems_found

    # ── Task 4: Secure Boot MOK detection ────────────────────────────

    def _check_secure_boot(
        self,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        """Check Secure Boot status and MOK enrollment.

        Returns (issues, secure_boot_enabled, mok_enrolled).
        """
        issues: list[dict[str, Any]] = []
        sb_enabled = False
        mok_enrolled = False

        # 1. Check Secure Boot state
        try:
            result = self._run(["mokutil", "--sb-state"])
            sb_enabled = (
                "enabled" in result.stdout.lower() and "disabled" not in result.stdout.lower()
            )
        except (FileNotFoundError, OSError):
            # mokutil not available — likely non-EFI system
            return issues, False, False
        except subprocess.TimeoutExpired:
            return issues, False, False

        if not sb_enabled:
            # Secure Boot disabled — no issues
            issues.append(
                _make_issue(
                    ISSUE_SECURE_BOOT,
                    SEVERITY_OK,
                    "Secure Boot is disabled",
                    "Secure Boot is disabled. NVIDIA driver modules can load "
                    "without MOK enrollment.",
                    fixable=False,
                    already_fixed=True,
                )
            )
            return issues, False, False

        # 2. Check MOK key enrollment
        try:
            mok_result = self._run(
                ["mokutil", "--test-key", "/var/lib/shim-signed/mok/MOK.der"],
            )
            mok_enrolled = mok_result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            mok_enrolled = False

        # 3. Check module signing
        module_signed = False
        try:
            modinfo_result = self._run(["modinfo", "-F", "signer", "nvidia"])
            module_signed = modinfo_result.returncode == 0 and modinfo_result.stdout.strip() != ""
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            module_signed = False

        if not mok_enrolled:
            issues.append(
                _make_issue(
                    ISSUE_SECURE_BOOT,
                    SEVERITY_WARNING,
                    "MOK key is not enrolled",
                    "Secure Boot is enabled but the Machine Owner Key (MOK) is not "
                    "enrolled. After kernel updates, the NVIDIA driver may fail to "
                    "load because the module cannot be verified. Enroll the MOK key "
                    "using: sudo mokutil --import /var/lib/shim-signed/mok/MOK.der",
                    fixable=True,
                )
            )

        if not module_signed:
            issues.append(
                _make_issue(
                    ISSUE_SECURE_BOOT,
                    SEVERITY_WARNING,
                    "NVIDIA kernel module is not signed",
                    "Secure Boot is enabled but the NVIDIA kernel module does not "
                    "appear to be signed. The module may fail to load. Ensure DKMS "
                    "is configured to sign modules with your MOK key.",
                    fixable=True,
                )
            )

        if mok_enrolled and module_signed:
            issues.append(
                _make_issue(
                    ISSUE_SECURE_BOOT,
                    SEVERITY_OK,
                    "Secure Boot and MOK are properly configured",
                    "Secure Boot is enabled, MOK key is enrolled, and the NVIDIA "
                    "module is signed. Driver should load after kernel updates.",
                    fixable=False,
                    already_fixed=True,
                )
            )

        return issues, sb_enabled, mok_enrolled

    # ── Task 5: Wayland detection ────────────────────────────────────

    def _check_wayland_issues(
        self,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Check Wayland-specific NVIDIA configuration.

        Returns (issues, is_wayland_session).
        """
        issues: list[dict[str, Any]] = []

        # Detect session type — daemon runs as root so $XDG_SESSION_TYPE
        # is not set; query logind instead.
        session_type = self._detect_session_type()
        is_wayland = session_type == "wayland"

        if not is_wayland:
            return issues, False

        # 1. nvidia-drm modeset=1
        modeset_found = self._check_modeset()
        if not modeset_found:
            issues.append(
                _make_issue(
                    ISSUE_WAYLAND,
                    SEVERITY_CRITICAL,
                    "NVIDIA DRM kernel modesetting is not enabled",
                    "NVIDIA DRM kernel modesetting (nvidia-drm modeset=1) is not "
                    "enabled. This is required for Wayland to function with NVIDIA "
                    "GPUs. Add 'nvidia-drm.modeset=1' to your kernel command line "
                    "or create a modprobe.d configuration file.",
                    fixable=True,
                )
            )

        # 2. GBM backend
        env_content = self._read("/etc/environment")
        gbm_found = "GBM_BACKEND=nvidia-drm" in env_content
        if not gbm_found:
            issues.append(
                _make_issue(
                    ISSUE_WAYLAND,
                    SEVERITY_WARNING,
                    "GBM backend not configured for NVIDIA",
                    "The GBM_BACKEND=nvidia-drm environment variable is not set in "
                    "/etc/environment. Some Wayland compositors may not use the "
                    "NVIDIA GPU for rendering without this setting.",
                    fixable=True,
                )
            )

        # 3. WLR_NO_HARDWARE_CURSORS for wlroots compositors
        wlr_found = "WLR_NO_HARDWARE_CURSORS=1" in env_content
        if not wlr_found:
            issues.append(
                _make_issue(
                    ISSUE_WAYLAND,
                    SEVERITY_WARNING,
                    "WLR_NO_HARDWARE_CURSORS not set",
                    "WLR_NO_HARDWARE_CURSORS=1 is not set in /etc/environment. "
                    "Wlroots-based Wayland compositors (Sway, Hyprland, etc.) may "
                    "show invisible or corrupt cursors with NVIDIA GPUs without "
                    "this setting.",
                    fixable=True,
                )
            )

        if modeset_found and gbm_found and wlr_found:
            issues.append(
                _make_issue(
                    ISSUE_WAYLAND,
                    SEVERITY_OK,
                    "Wayland NVIDIA configuration is complete",
                    "DRM kernel modesetting is enabled, GBM backend is "
                    "configured, and wlroots cursor workaround is in place.",
                    fixable=False,
                    already_fixed=True,
                )
            )

        return issues, True

    def _detect_session_type(self) -> str:
        """Detect the session type via loginctl (works from root context).

        Falls back to ``$XDG_SESSION_TYPE`` if loginctl is unavailable.
        """
        try:
            # List sessions, grab the first session ID
            list_result = self._run(
                ["loginctl", "list-sessions", "--no-legend"],
            )
            lines = list_result.stdout.strip().splitlines()
            if not lines:
                return os.environ.get("XDG_SESSION_TYPE", "").lower()
            session_id = lines[0].split()[0]
            # Query the session type
            type_result = self._run(
                ["loginctl", "show-session", session_id, "-p", "Type", "--value"],
            )
            stype = type_result.stdout.strip().lower()
            return stype if stype else os.environ.get("XDG_SESSION_TYPE", "").lower()
        except (subprocess.TimeoutExpired, OSError, IndexError):
            return os.environ.get("XDG_SESSION_TYPE", "").lower()

    def _check_modeset(self) -> bool:
        """Check if nvidia-drm modeset=1 is configured.

        Checks ``/proc/cmdline`` and ``/etc/modprobe.d/*.conf`` files.
        The glob is performed via the injected ``_list_modprobe_confs``
        callable so tests don't touch the real filesystem.
        """
        # Check /proc/cmdline
        cmdline = self._read("/proc/cmdline")
        if "nvidia-drm.modeset=1" in cmdline:
            return True

        # Check modprobe.d config files
        for conf_path in self._list_modprobe_confs():
            content = self._read(conf_path)
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "nvidia-drm" in stripped and "modeset=1" in stripped:
                    return True

        return False

    # ══════════════════════════════════════════════════════════════════════
    # Story 4.2 — Fix application
    # ══════════════════════════════════════════════════════════════════════

    # ── Service helpers ──────────────────────────────────────────────────

    def _check_service_exists(self, service: str) -> bool:
        """Return True if the systemd unit file exists on the system."""
        try:
            result = self._run(["systemctl", "cat", service])
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _enable_service(self, service: str) -> tuple[bool, str]:
        """Enable a systemd service.  Returns (success, message)."""
        try:
            result = self._run(
                ["systemctl", "enable", service],
                timeout=30,
            )
            if result.returncode == 0:
                return True, f"Enabled {service}"
            return False, f"Failed to enable {service}: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return False, f"Timed out enabling {service}"
        except OSError as exc:
            return False, f"Error enabling {service}: {exc}"

    def _check_modprobe_config(self) -> bool:
        """Return True if the NVIDIA power management modprobe config exists with correct content."""
        content = self._read(MODPROBE_CONF_PATH)
        return (
            "NVreg_PreserveVideoMemoryAllocations=1" in content
            and "NVreg_TemporaryFilePath" in content
        )

    def _write_modprobe_config(self) -> tuple[bool, str]:
        """Write the NVIDIA power management modprobe config.  Returns (success, message)."""
        try:
            self._write(MODPROBE_CONF_PATH, MODPROBE_CONTENT)
            return True, f"Wrote {MODPROBE_CONF_PATH}"
        except OSError as exc:
            return False, f"Failed to write {MODPROBE_CONF_PATH}: {exc}"

    def _run_update_initramfs(self) -> tuple[bool, str]:
        """Run ``update-initramfs -u``.  Returns (success, message)."""
        try:
            result = self._run(
                ["update-initramfs", "-u"],
                timeout=_INITRAMFS_TIMEOUT,
            )
            if result.returncode == 0:
                return True, "Initramfs updated successfully"
            return False, f"update-initramfs failed: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return False, "update-initramfs timed out"
        except OSError as exc:
            return False, f"Error running update-initramfs: {exc}"

    # ── Fix operations ───────────────────────────────────────────────────

    def fix_suspend(
        self,
        op_id: str,
        progress_cb: Callable[[str, float, str], None],
        complete_cb: Callable[[str, bool, str], None],
    ) -> None:
        """Apply suspend fix: enable NVIDIA suspend/resume/hibernate services.

        Parameters
        ----------
        op_id : str
            Operation identifier for progress tracking.
        progress_cb : callable
            Called as ``progress_cb(op_id, percent, message)``.
        complete_cb : callable
            Called as ``complete_cb(op_id, success, message)``.
        """
        _completed = False
        try:
            # Step 1 (0-10%): Check services exist
            progress_cb(op_id, 5.0, "Checking NVIDIA suspend services...")
            missing = []
            for svc in _SUSPEND_SERVICES:
                if not self._check_service_exists(svc):
                    missing.append(svc)
            if missing:
                _completed = True
                complete_cb(
                    op_id,
                    False,
                    f"Required services not found: {', '.join(missing)}. "
                    f"Is the NVIDIA driver installed?",
                )
                return

            # Step 2 (10-30%): Check already enabled
            progress_cb(op_id, 15.0, "Checking service status...")
            needs_enable = []
            for svc in _SUSPEND_SERVICES:
                if not self._is_service_enabled(svc):
                    needs_enable.append(svc)

            if not needs_enable:
                _completed = True
                complete_cb(
                    op_id,
                    True,
                    "All suspend services are already enabled — no changes needed.",
                )
                return

            # Step 3 (30-70%): Enable each service
            step_pct = 40.0 / len(needs_enable)
            for i, svc in enumerate(needs_enable):
                pct = 30.0 + step_pct * i
                progress_cb(op_id, pct, f"Enabling {svc}...")
                was_enabled = self._is_service_enabled(svc)
                ok, msg = self._enable_service(svc)
                if not ok:
                    _completed = True
                    complete_cb(op_id, False, msg)
                    return
                if self._tracker:
                    self._tracker.record(
                        op_id,
                        "service_enabled",
                        svc,
                        "enabled" if was_enabled else "disabled",
                        f"Enabled {svc} for suspend/resume fix",
                    )

            # Step 4 (70-90%): Verify
            progress_cb(op_id, 75.0, "Verifying services are enabled...")
            failed_verify = []
            for svc in needs_enable:
                if not self._is_service_enabled(svc):
                    failed_verify.append(svc)
            if failed_verify:
                _completed = True
                complete_cb(
                    op_id,
                    False,
                    f"Verification failed — services not enabled after attempt: "
                    f"{', '.join(failed_verify)}",
                )
                return

            # Step 5 (90-100%): Complete
            progress_cb(op_id, 95.0, "Suspend fix complete.")
            enabled_str = ", ".join(needs_enable)
            _completed = True
            complete_cb(op_id, True, f"Enabled suspend services: {enabled_str}")

        except Exception as exc:
            log.exception("Unhandled error in fix_suspend for %s", op_id)
            if not _completed:
                complete_cb(op_id, False, f"Unexpected error: {exc}")

    def fix_hibernate(
        self,
        op_id: str,
        progress_cb: Callable[[str, float, str], None],
        complete_cb: Callable[[str, bool, str], None],
        reboot_cb: Callable[[bool, str], None] | None = None,
    ) -> None:
        """Apply hibernate fix: write modprobe config, enable service, rebuild initramfs.

        Parameters
        ----------
        op_id : str
            Operation identifier for progress tracking.
        progress_cb : callable
            Called as ``progress_cb(op_id, percent, message)``.
        complete_cb : callable
            Called as ``complete_cb(op_id, success, message)``.
        reboot_cb : callable, optional
            Called as ``reboot_cb(required, reason)`` when a reboot is needed.
        """
        changes_made: list[str] = []
        _completed = False
        try:
            # Step 1 (0-10%): Check hibernate service exists
            progress_cb(op_id, 5.0, "Checking NVIDIA hibernate service...")
            if not self._check_service_exists(NVIDIA_HIBERNATE_SERVICE):
                _completed = True
                complete_cb(
                    op_id,
                    False,
                    f"{NVIDIA_HIBERNATE_SERVICE} not found. Is the NVIDIA driver installed?",
                )
                return

            # Step 2 (10-20%): Check modprobe config
            progress_cb(op_id, 15.0, "Checking modprobe configuration...")
            config_exists = self._check_modprobe_config()

            # Step 3 (20-30%): Check hibernate service enabled
            progress_cb(op_id, 25.0, "Checking hibernate service status...")
            service_enabled = self._is_service_enabled(NVIDIA_HIBERNATE_SERVICE)

            # P-1: Even when config+service are OK, still verify initramfs
            # is up-to-date (a previous run may have failed at initramfs).
            if config_exists and service_enabled:
                progress_cb(op_id, 75.0, "Verifying initramfs is up-to-date...")
                ok, msg = self._run_update_initramfs()
                if not ok:
                    _completed = True
                    complete_cb(op_id, False, msg)
                    return
                _completed = True
                complete_cb(
                    op_id,
                    True,
                    "Hibernate is already configured — verified initramfs.",
                )
                return

            # Step 4 (30-50%): Write modprobe config if needed
            if not config_exists:
                progress_cb(op_id, 35.0, f"Writing {MODPROBE_CONF_PATH}...")
                ok, msg = self._write_modprobe_config()
                if not ok:
                    _completed = True
                    complete_cb(op_id, False, msg)
                    return
                changes_made.append("Wrote modprobe config")
                if self._tracker:
                    self._tracker.record(
                        op_id,
                        "file_created",
                        MODPROBE_CONF_PATH,
                        None,
                        "Created modprobe configuration for NVIDIA power management",
                    )

            # Step 5 (50-70%): Enable hibernate service if needed
            if not service_enabled:
                progress_cb(op_id, 55.0, f"Enabling {NVIDIA_HIBERNATE_SERVICE}...")
                ok, msg = self._enable_service(NVIDIA_HIBERNATE_SERVICE)
                if not ok:
                    _completed = True
                    complete_cb(op_id, False, msg)
                    return
                changes_made.append(f"Enabled {NVIDIA_HIBERNATE_SERVICE}")
                if self._tracker:
                    self._tracker.record(
                        op_id,
                        "service_enabled",
                        NVIDIA_HIBERNATE_SERVICE,
                        "disabled",
                        f"Enabled {NVIDIA_HIBERNATE_SERVICE} for hibernate fix",
                    )

                # P-5: Verify service enablement (same pattern as fix_suspend)
                if not self._is_service_enabled(NVIDIA_HIBERNATE_SERVICE):
                    _completed = True
                    complete_cb(
                        op_id,
                        False,
                        f"Verification failed — {NVIDIA_HIBERNATE_SERVICE} "
                        f"not enabled after attempt.",
                    )
                    return

            # Step 6 (70-90%): Rebuild initramfs
            progress_cb(op_id, 75.0, "Rebuilding initramfs (this may take a minute)...")
            ok, msg = self._run_update_initramfs()
            if not ok:
                _completed = True
                complete_cb(op_id, False, msg)
                return
            changes_made.append("Rebuilt initramfs")
            if self._tracker:
                self._tracker.record(
                    op_id,
                    "initramfs_rebuilt",
                    "initramfs",
                    None,
                    "Rebuilt initramfs for hibernate fix",
                )

            # Step 7 (90-100%): Verify and complete
            progress_cb(op_id, 95.0, "Hibernate fix complete.")
            summary = "; ".join(changes_made)
            _completed = True
            complete_cb(
                op_id,
                True,
                f"Hibernate configured: {summary}. Reboot required.",
            )

            if reboot_cb:
                reboot_cb(True, "Reboot required to apply hibernate configuration changes.")

        except Exception as exc:
            log.exception("Unhandled error in fix_hibernate for %s", op_id)
            if not _completed:
                complete_cb(op_id, False, f"Unexpected error: {exc}")

    # ── Pre-flight checks ────────────────────────────────────────────────

    def get_preflight_suspend(self) -> dict[str, Any]:
        """Return pre-flight check for suspend fix as an ``a{sv}``-compatible dict."""
        changes: list[dict[str, str]] = []
        all_fixed = True

        for svc in _SUSPEND_SERVICES:
            exists = self._check_service_exists(svc)
            enabled = self._is_service_enabled(svc) if exists else False
            if not enabled:
                all_fixed = False
            changes.append(
                {
                    "description": f"Enable {svc}",
                    "current_state": "enabled"
                    if enabled
                    else ("disabled" if exists else "missing"),
                    "target_state": "enabled",
                }
            )

        return {
            "ready": all(c["current_state"] != "missing" for c in changes),
            "changes": changes,
            "already_fixed": all_fixed,
        }

    def get_preflight_hibernate(self) -> dict[str, Any]:
        """Return pre-flight check for hibernate fix as an ``a{sv}``-compatible dict."""
        changes: list[dict[str, str]] = []
        all_fixed = True

        # Modprobe config
        config_ok = self._check_modprobe_config()
        if not config_ok:
            all_fixed = False
        changes.append(
            {
                "description": f"Write NVIDIA power management config to {MODPROBE_CONF_PATH}",
                "current_state": "present" if config_ok else "absent",
                "target_state": "present",
            }
        )

        # Hibernate service
        exists = self._check_service_exists(NVIDIA_HIBERNATE_SERVICE)
        enabled = self._is_service_enabled(NVIDIA_HIBERNATE_SERVICE) if exists else False
        if not enabled:
            all_fixed = False
        changes.append(
            {
                "description": f"Enable {NVIDIA_HIBERNATE_SERVICE}",
                "current_state": "enabled" if enabled else ("disabled" if exists else "missing"),
                "target_state": "enabled",
            }
        )

        # Initramfs rebuild
        changes.append(
            {
                "description": "Rebuild initramfs to apply module parameters",
                "current_state": "pending" if not config_ok else "up-to-date",
                "target_state": "up-to-date",
            }
        )

        return {
            "ready": exists,
            "changes": changes,
            "already_fixed": all_fixed,
        }
