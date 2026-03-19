"""Recovery diagnostics engine — detects common driver failure states.

Runs standalone without GTK, D-Bus, or GLib.  Each check function returns
a DiagnosticResult on failure or None on success.

References: FR22, FR58; AC#2 of Story 3.4.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import json
import logging
import pathlib
import shutil
import subprocess

log = logging.getLogger("verde.recovery.diagnostics")

# Default path for interrupted operation marker (FR58)
_DEFAULT_MARKER_PATH = pathlib.Path("/var/lib/verde/operation_in_progress.json")


@dataclasses.dataclass(frozen=True)
class DiagnosticResult:
    """A single diagnostic finding."""

    issue_type: str
    severity: str  # "critical", "warning", "info"
    description: str
    fixable: bool


# ── Individual checks ───────────────────────────────────────────────


def check_nvml() -> DiagnosticResult | None:
    """Check whether the NVIDIA kernel module is loaded via NVML.

    Attempts to load ``libnvidia-ml.so.1`` using ctypes.
    Returns None if NVML loads successfully (driver is functional).
    """
    try:
        lib = ctypes.CDLL("libnvidia-ml.so.1")
    except OSError:
        return DiagnosticResult(
            issue_type="nvml_missing",
            severity="critical",
            description=(
                "NVIDIA kernel module not loaded — driver may be broken or not installed"
            ),
            fixable=True,
        )

    inited = False
    try:
        ret = lib.nvmlInit_v2()
        if ret != 0:
            return DiagnosticResult(
                issue_type="nvml_missing",
                severity="critical",
                description=(
                    "NVIDIA kernel module not loaded — driver may be broken or not installed"
                ),
                fixable=True,
            )
        inited = True
        return None
    except Exception:
        return DiagnosticResult(
            issue_type="nvml_missing",
            severity="critical",
            description=(
                "NVIDIA kernel module not loaded — driver may be broken or not installed"
            ),
            fixable=True,
        )
    finally:
        if inited:
            with contextlib.suppress(Exception):
                lib.nvmlShutdown()


def check_secure_boot_mok() -> DiagnosticResult | None:
    """Check Secure Boot and MOK enrollment status.

    Only relevant on EFI systems.  Returns None if Secure Boot is
    disabled or the system is not using EFI.
    """
    efi_dir = pathlib.Path("/sys/firmware/efi")
    if not efi_dir.exists():
        return None  # Not an EFI system — Secure Boot not applicable

    if shutil.which("mokutil") is None:
        return None  # mokutil not installed — cannot determine MOK status

    try:
        result = subprocess.run(
            ["mokutil", "--sb-state"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.strip().lower()
        if "disabled" in output:
            return None  # Secure Boot disabled — no issue
    except subprocess.TimeoutExpired:
        return None  # Cannot determine status — don't warn

    return DiagnosticResult(
        issue_type="mok_not_enrolled",
        severity="warning",
        description=(
            "Secure Boot is enabled but NVIDIA MOK key may not be enrolled — "
            "unsigned kernel module may be blocked"
        ),
        fixable=True,
    )


def check_dpkg_state() -> DiagnosticResult | None:
    """Check for broken or half-configured packages.

    Runs ``dpkg --audit`` to detect package manager issues.
    """
    try:
        result = subprocess.run(
            ["dpkg", "--audit"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and not result.stdout.strip():
            return None  # Clean state
    except FileNotFoundError:
        return None  # dpkg not found — unlikely but not a driver issue
    except subprocess.TimeoutExpired:
        pass  # Fall through to report issue

    return DiagnosticResult(
        issue_type="dpkg_broken",
        severity="critical",
        description=(
            "Package manager in broken state — packages may be half-configured or interrupted"
        ),
        fixable=True,
    )


def check_interrupted_operation(
    marker_path: pathlib.Path = _DEFAULT_MARKER_PATH,
) -> DiagnosticResult | None:
    """Check for an interrupted Verde operation (FR58).

    Reads the operation marker file written by the daemon during
    long-running operations.
    """
    if not marker_path.exists():
        return None

    try:
        data = json.loads(marker_path.read_text())
        if not isinstance(data, dict):
            desc = "Previous Verde operation was interrupted — system may be in inconsistent state"
        else:
            op_type = data.get("operation", "unknown")
            target = data.get("target", "")
            desc = f"Previous Verde operation was interrupted ({op_type}"
            if target:
                desc += f" — target: {target}"
            desc += ") — system may be in inconsistent state"
    except (OSError, json.JSONDecodeError):
        desc = "Previous Verde operation was interrupted — system may be in inconsistent state"

    return DiagnosticResult(
        issue_type="interrupted_operation",
        severity="critical",
        description=desc,
        fixable=True,
    )


def check_dkms_status() -> DiagnosticResult | None:
    """Check DKMS build status for NVIDIA modules.

    Runs ``dkms status`` and checks for nvidia module failures.
    """
    try:
        result = subprocess.run(
            ["dkms", "status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return None  # dkms not installed — not an error
    except subprocess.TimeoutExpired:
        return DiagnosticResult(
            issue_type="dkms_failure",
            severity="warning",
            description="DKMS status check timed out",
            fixable=False,
        )

    # Look for nvidia entries that aren't "installed"
    for line in result.stdout.splitlines():
        lower = line.lower()
        if "nvidia" not in lower:
            continue
        if "installed" in lower:
            continue
        # Any other state (broken, added, building, etc.) is a problem
        return DiagnosticResult(
            issue_type="dkms_failure",
            severity="warning",
            description=(f"NVIDIA DKMS module issue: {line.strip()}"),
            fixable=True,
        )

    return None


# ── Main runner ─────────────────────────────────────────────────────


def run_diagnostics(
    marker_path: pathlib.Path = _DEFAULT_MARKER_PATH,
) -> list[DiagnosticResult]:
    """Run all diagnostic checks and collect issues.

    Returns a list of DiagnosticResult for detected issues.
    Empty list means system is healthy.
    """
    checks = [
        check_nvml(),
        check_secure_boot_mok(),
        check_dpkg_state(),
        check_interrupted_operation(marker_path=marker_path),
        check_dkms_status(),
    ]
    return [r for r in checks if r is not None]
