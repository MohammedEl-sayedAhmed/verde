"""APT error classification, detection, and recovery guidance.

Categorizes apt/dpkg errors into actionable responses with human-readable
messages and recovery options. Raw subprocess output is logged to audit
but never surfaced to the user via D-Bus.

Architecture: AR-7 (daemon-only), NFR-SEC-3, NFR-SEC-4.
References: FR14, FR15, FR44, FR46-FR48, FR52, FR59; UX-DR16.
"""

from __future__ import annotations

import dataclasses
import enum
import errno
import fcntl
import json
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger("verde-daemon.apt_errors")

# ---------------------------------------------------------------------------
# gettext stub — must be defined before module-level _() calls
# ---------------------------------------------------------------------------

try:
    _("test")
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _

# ---------------------------------------------------------------------------
# Error category enum
# ---------------------------------------------------------------------------


class AptErrorCategory(enum.Enum):
    """Classification of apt/dpkg error conditions."""

    DPKG_BROKEN = "dpkg_broken"
    DPKG_LOCKED = "dpkg_locked"
    NETWORK_UNAVAILABLE = "network_unavailable"
    DKMS_BUILD_FAILURE = "dkms_build_failure"
    DEPENDENCY_CONFLICT = "dependency_conflict"
    INTERRUPTED_OPERATION = "interrupted_operation"
    POLKIT_MISSING = "polkit_missing"
    SUBPROCESS_TIMEOUT = "subprocess_timeout"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Error response dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AptErrorResponse:
    """Structured error response for apt/dpkg failures.

    Fields follow UX-DR16: title (plain language) + description +
    primary action + secondary action.
    """

    category: AptErrorCategory
    title: str
    description: str
    primary_action: str
    secondary_action: str
    raw_output: str
    recoverable: bool

    def to_dbus_dict(self) -> dict:
        """Convert to D-Bus a{sv}-compatible dict.

        IMPORTANT: raw_output is deliberately excluded (NFR-SEC-4).
        """
        return {
            "success": False,
            "error_category": self.category.value,
            "error_title": self.title,
            "error_description": self.description,
            "error_primary_action": self.primary_action,
            "error_secondary_action": self.secondary_action,
            "recoverable": self.recoverable,
        }


# ---------------------------------------------------------------------------
# Compiled regex patterns for error classification
# ---------------------------------------------------------------------------

_DPKG_BROKEN_PATTERNS = [
    re.compile(r"dpkg was interrupted"),
    re.compile(r"dpkg --configure -a"),
    re.compile(r"Sub-process /usr/bin/dpkg returned an error"),
]

_DPKG_LOCKED_PATTERNS = [
    re.compile(r"Could not get lock"),
    re.compile(r"held by process (\d+)"),
    re.compile(r"Unable to acquire the dpkg"),
]

_NETWORK_PATTERNS = [
    re.compile(r"Failed to fetch"),
    re.compile(r"Could not resolve"),
    re.compile(r"Temporary failure resolving"),
    re.compile(r"Connection timed out"),
    re.compile(r"Unable to connect to"),
]

_DKMS_PATTERNS = [
    re.compile(r"dkms.*error", re.IGNORECASE),
    re.compile(r"Module build.*failed", re.IGNORECASE),
    re.compile(r"Bad return status for module build"),
]

_DEPENDENCY_PATTERNS = [
    re.compile(r"Depends:.*but.*is"),
    re.compile(r"conflicts with"),
    re.compile(r"Breaks:"),
    re.compile(r"held broken packages"),
]

# DKMS sub-classification patterns
_DKMS_MISSING_HEADERS = re.compile(
    r"kernel headers.*cannot be found|linux-headers-\S+\s+package", re.IGNORECASE
)
_DKMS_COMPILER_ERROR = re.compile(r"cc1:.*error|make\[\d+\]:.*Error", re.IGNORECASE)
_DKMS_VERSION_MISMATCH = re.compile(
    r"not supported for kernel|Module.*not.*compatible", re.IGNORECASE
)

# Marker file path for interrupted operation detection
OPERATION_MARKER_PATH = Path("/var/lib/verde/operation_in_progress.json")


# ---------------------------------------------------------------------------
# Error message templates (UX-DR16: no jargon, no technical identifiers)
# ---------------------------------------------------------------------------

_ERROR_TEMPLATES: dict[AptErrorCategory, dict] = {
    AptErrorCategory.DPKG_BROKEN: {
        "title": _("Package system needs repair"),
        "description": _(
            "The package system was left in a broken state. Your previous driver is still active."
        ),
        "primary_action": "repair_dpkg",
        "secondary_action": "rollback",
        "recoverable": True,
    },
    AptErrorCategory.DPKG_LOCKED: {
        "title": _("Package system is busy"),
        "description": _(
            "Another program is currently using the package system. "
            "Please wait for it to finish and try again."
        ),
        "primary_action": "retry",
        "secondary_action": "view_blocking_process",
        "recoverable": True,
    },
    AptErrorCategory.NETWORK_UNAVAILABLE: {
        "title": _("Unable to download packages"),
        "description": _(
            "This operation requires an internet connection to download "
            "packages from Ubuntu repositories."
        ),
        "primary_action": "retry",
        "secondary_action": "check_network",
        "recoverable": True,
    },
    AptErrorCategory.DKMS_BUILD_FAILURE: {
        "title": _("Driver module failed to build"),
        "description": _(
            "The kernel module for the driver could not be compiled. "
            "This usually means a build dependency is missing."
        ),
        "primary_action": "install_build_tools",
        "secondary_action": "view_build_log",
        "recoverable": True,
    },
    AptErrorCategory.DEPENDENCY_CONFLICT: {
        "title": _("Package conflict detected"),
        "description": _(
            "The requested driver conflicts with packages already installed on your system."
        ),
        "primary_action": "view_conflicting_packages",
        "secondary_action": "generate_diagnostic",
        "recoverable": True,
    },
    AptErrorCategory.INTERRUPTED_OPERATION: {
        "title": _("A previous operation was interrupted"),
        "description": _(
            "A driver operation did not complete successfully. The package system may need repair."
        ),
        "primary_action": "repair_dpkg",
        "secondary_action": "rollback",
        "recoverable": True,
    },
    AptErrorCategory.POLKIT_MISSING: {
        "title": _("Authentication service unavailable"),
        "description": _(
            "A Polkit authentication agent is required but not running. "
            "Ensure a Polkit authentication agent is running "
            "(e.g., polkit-gnome-authentication-agent-1 or GNOME Shell)."
        ),
        "primary_action": "install_auth_agent",
        "secondary_action": "view_documentation",
        "recoverable": True,
    },
    AptErrorCategory.SUBPROCESS_TIMEOUT: {
        "title": _("Operation timed out"),
        "description": _(
            "The package operation took too long and was stopped. "
            "This may be caused by a slow network or system load."
        ),
        "primary_action": "retry",
        "secondary_action": "generate_diagnostic",
        "recoverable": True,
    },
    AptErrorCategory.UNKNOWN: {
        "title": _("An unexpected error occurred"),
        "description": _(
            "An unexpected error occurred during the package operation. "
            "No changes were made to your system."
        ),
        "primary_action": "generate_diagnostic",
        "secondary_action": "retry",
        "recoverable": True,
    },
}


# ---------------------------------------------------------------------------
# dpkg lock detection (Task 2)
# ---------------------------------------------------------------------------


def detect_dpkg_lock() -> AptErrorResponse | None:
    """Check if dpkg lock is held by another process.

    Returns AptErrorResponse with DPKG_LOCKED if locked, None if free.
    """
    lock_path = "/var/lib/dpkg/lock-frontend"
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except FileNotFoundError:
        return None  # Lock file doesn't exist, proceed
    except OSError:
        return None  # Cannot check, proceed with operation

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None  # Lock is free
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EAGAIN):
            holder = _identify_lock_holder()
            template = _ERROR_TEMPLATES[AptErrorCategory.DPKG_LOCKED]
            description = template["description"]
            if holder:
                description = _(
                    "The package system is currently in use by {}. "
                    "Please wait for it to finish and try again."
                ).format(holder)
            return AptErrorResponse(
                category=AptErrorCategory.DPKG_LOCKED,
                title=template["title"],
                description=description,
                primary_action=template["primary_action"],
                secondary_action=template["secondary_action"],
                raw_output=f"Lock held, holder: {holder or 'unknown'}",
                recoverable=template["recoverable"],
            )
        return None
    finally:
        os.close(fd)


def _identify_lock_holder() -> str:
    """Try to identify the process holding the dpkg lock."""
    try:
        result = subprocess.run(
            ["lsof", "/var/lib/dpkg/lock-frontend"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 2:
                    return f"{parts[0]} (pid {parts[1]})"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


# ---------------------------------------------------------------------------
# apt error classifier (Task 3)
# ---------------------------------------------------------------------------


def classify_apt_error(
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    timed_out: bool = False,
) -> AptErrorResponse:
    """Classify an apt/dpkg subprocess failure into an actionable response.

    Parameters
    ----------
    returncode : int
        Process exit code.
    stdout : str
        Captured stdout.
    stderr : str
        Captured stderr.
    timed_out : bool
        Whether the process was killed due to timeout.

    Returns
    -------
    AptErrorResponse
        Categorized error with human-readable messages.
    """
    combined = f"{stdout}\n{stderr}"

    if timed_out:
        return _build_response(AptErrorCategory.SUBPROCESS_TIMEOUT, stderr)

    # Check patterns in priority order
    for patterns, category in [
        (_DPKG_LOCKED_PATTERNS, AptErrorCategory.DPKG_LOCKED),
        (_DPKG_BROKEN_PATTERNS, AptErrorCategory.DPKG_BROKEN),
        (_NETWORK_PATTERNS, AptErrorCategory.NETWORK_UNAVAILABLE),
        (_DKMS_PATTERNS, AptErrorCategory.DKMS_BUILD_FAILURE),
        (_DEPENDENCY_PATTERNS, AptErrorCategory.DEPENDENCY_CONFLICT),
    ]:
        for pattern in patterns:
            if pattern.search(combined):
                if category is AptErrorCategory.DKMS_BUILD_FAILURE:
                    return analyze_dkms_failure(stderr)
                return _build_response(category, stderr)

    return _build_response(AptErrorCategory.UNKNOWN, stderr)


def _build_response(category: AptErrorCategory, raw_output: str) -> AptErrorResponse:
    """Build an AptErrorResponse from a template."""
    template = _ERROR_TEMPLATES[category]
    return AptErrorResponse(
        category=category,
        title=template["title"],
        description=template["description"],
        primary_action=template["primary_action"],
        secondary_action=template["secondary_action"],
        raw_output=raw_output,
        recoverable=template["recoverable"],
    )


# ---------------------------------------------------------------------------
# dpkg broken state detection (Task 4)
# ---------------------------------------------------------------------------


def detect_dpkg_broken() -> bool:
    """Run ``dpkg --audit`` to check for broken packages.

    Returns True if packages are in a broken state.
    """
    try:
        result = subprocess.run(
            ["dpkg", "--audit"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Non-zero return code or non-empty stdout indicates broken packages
        return result.returncode != 0 or bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


# ---------------------------------------------------------------------------
# Interrupted operation detection (Task 5)
# ---------------------------------------------------------------------------


def detect_interrupted_operation(
    marker_path: Path = OPERATION_MARKER_PATH,
) -> AptErrorResponse | None:
    """Check for interrupted operation marker file.

    On daemon activation, checks for a marker file left by a previous
    operation that was interrupted (e.g., power loss during install).

    Returns AptErrorResponse if repair is needed, None otherwise.
    """
    if not marker_path.exists():
        return None

    marker_data = ""
    try:
        marker_data = marker_path.read_text()
        marker = json.loads(marker_data)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to read operation marker: %s", exc)
        # Marker exists but unreadable — check dpkg state anyway
        marker = {}

    if detect_dpkg_broken():
        operation = marker.get("operation", "unknown")
        version = marker.get("version", "unknown")
        return AptErrorResponse(
            category=AptErrorCategory.INTERRUPTED_OPERATION,
            title=_("A previous operation was interrupted"),
            description=_(
                "A driver operation ({} version {}) did not complete successfully. "
                "The package system needs repair."
            ).format(operation, version),
            primary_action="repair_dpkg",
            secondary_action="rollback",
            raw_output=marker_data if isinstance(marker_data, str) else "",
            recoverable=True,
        )

    # dpkg is clean — marker is stale, remove it
    try:
        marker_path.unlink()
        log.info("Removed stale operation marker (dpkg state is clean)")
    except OSError as exc:
        log.warning("Failed to remove stale marker: %s", exc)

    return None


# ---------------------------------------------------------------------------
# DKMS build failure analysis (Task 6)
# ---------------------------------------------------------------------------


def analyze_dkms_failure(stderr: str) -> AptErrorResponse:
    """Analyze DKMS build failure output for targeted resolution.

    Returns an AptErrorResponse with specific guidance based on the
    failure mode detected.
    """
    if _DKMS_MISSING_HEADERS.search(stderr):
        # Extract kernel version if possible
        kernel_match = re.search(r"linux-headers-(\S+)", stderr)
        kernel_ver = kernel_match.group(1) if kernel_match else "$(uname -r)"
        return AptErrorResponse(
            category=AptErrorCategory.DKMS_BUILD_FAILURE,
            title=_("Driver module failed to build"),
            description=_(
                "The kernel headers required to build the driver module are not installed."
            ),
            primary_action=f"install_kernel_headers:linux-headers-{kernel_ver}",
            secondary_action="view_dkms_log",
            raw_output=stderr,
            recoverable=True,
        )

    if _DKMS_COMPILER_ERROR.search(stderr):
        return AptErrorResponse(
            category=AptErrorCategory.DKMS_BUILD_FAILURE,
            title=_("Driver module failed to build"),
            description=_(
                "The driver module could not be compiled. Build tools may be missing or outdated."
            ),
            primary_action="install_build_tools:build-essential",
            secondary_action="view_dkms_log",
            raw_output=stderr,
            recoverable=True,
        )

    if _DKMS_VERSION_MISMATCH.search(stderr):
        return AptErrorResponse(
            category=AptErrorCategory.DKMS_BUILD_FAILURE,
            title=_("Driver module failed to build"),
            description=_(
                "The driver version is not compatible with your current kernel. "
                "Try a different driver version."
            ),
            primary_action="check_driver_compatibility",
            secondary_action="view_dkms_log",
            raw_output=stderr,
            recoverable=True,
        )

    # Generic DKMS failure
    return AptErrorResponse(
        category=AptErrorCategory.DKMS_BUILD_FAILURE,
        title=_("Driver module failed to build"),
        description=_(
            "The kernel module for the driver could not be compiled. "
            "Check the DKMS build log for details."
        ),
        primary_action="install_build_tools",
        secondary_action="view_dkms_log",
        raw_output=stderr,
        recoverable=True,
    )


# ---------------------------------------------------------------------------
# Network error detection (Task 7)
# ---------------------------------------------------------------------------


def is_network_error(stderr: str) -> bool:
    """Check if stderr contains apt network failure signatures.

    No proactive network detection — only classify after apt fails
    (per PRD offline design).
    """
    return any(pattern.search(stderr) for pattern in _NETWORK_PATTERNS)


# ---------------------------------------------------------------------------
# Operation marker file management
# ---------------------------------------------------------------------------


def write_operation_marker(
    operation: str,
    version: str,
    snapshot_id: str = "",
    marker_path: Path = OPERATION_MARKER_PATH,
) -> None:
    """Write operation marker before apt operations.

    Marker persists until operation completes successfully.
    Presence on next daemon start triggers interrupted operation detection.
    """
    import datetime

    marker = {
        "operation": operation,
        "version": version,
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    if snapshot_id:
        marker["snapshot_id"] = snapshot_id

    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(marker))
    except OSError as exc:
        log.warning("Failed to write operation marker: %s", exc)


def remove_operation_marker(
    marker_path: Path = OPERATION_MARKER_PATH,
) -> None:
    """Remove operation marker after successful completion."""
    try:
        if marker_path.exists():
            marker_path.unlink()
    except OSError as exc:
        log.warning("Failed to remove operation marker: %s", exc)
