"""Error message catalog for Verde GUI.

Maps D-Bus error names and internal error codes to humanized messages.
Views import from this module instead of constructing error text inline.
Every user-facing string is wrapped in ``_()`` for gettext.
"""

from __future__ import annotations

import re

# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Error message catalog
# ---------------------------------------------------------------------------
# Each entry: title, description, suggestion, doc_link (optional URL or None)
ERROR_MESSAGES: dict[str, dict[str, str | None]] = {
    # D-Bus errors
    "com.verde.Error.PreflightFailed": {
        "title": _("Installation cannot proceed"),
        "description": _(
            "One or more safety checks did not pass. "
            "This protects your system from potential issues."
        ),
        "suggestion": _("Review the failed checks above and resolve them before trying again."),
        "doc_link": None,
    },
    "com.verde.Error.OperationInProgress": {
        "title": _("Another operation is running"),
        "description": _(
            "Verde can only perform one system change at a time to keep your system safe."
        ),
        "suggestion": _("Wait for the current operation to finish, then try again."),
        "doc_link": None,
    },
    "com.verde.Error.InvalidArgument": {
        "title": _("Invalid request"),
        "description": _(
            "The requested operation could not be understood. "
            "This is usually a bug in the application."
        ),
        "suggestion": _(
            "Try closing and reopening Verde. If the problem persists, please report it."
        ),
        "doc_link": None,
    },
    "com.verde.Error.SnapshotNotFound": {
        "title": _("Snapshot not found"),
        "description": _(
            "The selected snapshot no longer exists. "
            "It may have been deleted or cleaned up automatically."
        ),
        "suggestion": _("Refresh the snapshots list and try a different snapshot."),
        "doc_link": None,
    },
    # Internal error conditions
    "daemon_unreachable": {
        "title": _("Cannot connect to Verde service"),
        "description": _(
            "Verde\u2019s system service is not responding. It may need to be restarted."
        ),
        "suggestion": _("Try running: systemctl restart com.verde.Manager"),
        "doc_link": "https://help.ubuntu.com/community/NvidiaDriversInstallation",
    },
    "nvml_unavailable": {
        "title": _("GPU monitoring is not available"),
        "description": _(
            "The NVIDIA management library could not be loaded. "
            "This usually means the driver is not installed or the kernel module is not loaded."
        ),
        "suggestion": _("Install or reinstall the NVIDIA driver from the Drivers tab."),
        "doc_link": "https://help.ubuntu.com/community/NvidiaDriversInstallation",
    },
    "apt_lock": {
        "title": _("Package system is busy"),
        "description": _(
            "Another program is currently installing or updating packages. "
            "Only one package operation can run at a time."
        ),
        "suggestion": _(
            "Wait for the other operation to finish, then try again. "
            "If nothing seems to be running, try rebooting."
        ),
        "doc_link": None,
    },
    "network_unavailable": {
        "title": _("Unable to download packages"),
        "description": _(
            "This operation requires an internet connection "
            "to download packages from Ubuntu repositories."
        ),
        "suggestion": _("Check your network connection and try again."),
        "doc_link": None,
    },
    "kernel_headers_missing": {
        "title": _("Kernel headers are not installed"),
        "description": _(
            "The NVIDIA driver needs kernel headers to build its module. "
            "Headers for the running kernel were not found."
        ),
        "suggestion": _("Install the matching kernel headers package, then retry."),
        "doc_link": "https://wiki.ubuntu.com/Kernel/BuildYourOwnKernel",
    },
    "dkms_failure": {
        "title": _("Driver module failed to build"),
        "description": _(
            "The DKMS system could not compile the NVIDIA kernel module. "
            "This can happen after a kernel update or if build tools are missing."
        ),
        "suggestion": _(
            "Check that kernel headers and build-essential are installed, "
            "then try running: sudo dkms autoinstall"
        ),
        "doc_link": "https://help.ubuntu.com/community/DKMS",
    },
    "secure_boot_unsigned": {
        "title": _("Secure Boot is blocking the driver"),
        "description": _(
            "Secure Boot requires kernel modules to be signed. "
            "The NVIDIA module is not enrolled with your system\u2019s MOK keys."
        ),
        "suggestion": _(
            "Enroll the NVIDIA module key using mokutil, "
            "or disable Secure Boot in your BIOS settings."
        ),
        "doc_link": "https://wiki.ubuntu.com/UEFI/SecureBoot",
    },
}

# Regex to strip GDBus.Error: prefix from error strings
_GDBUS_PREFIX_RE = re.compile(r"^GDBus\.Error:([^:]+)(?::.*)?$")

# Fallback for unknown errors
_FALLBACK: dict[str, str | None] = {
    "title": _("An unexpected error occurred"),
    "description": _(
        "Something went wrong that Verde did not expect. Your system has not been changed."
    ),
    "suggestion": _(
        "Try the operation again. If the problem persists, "
        "check the system logs or report an issue."
    ),
    "doc_link": None,
}


def get_error_message(error_key: str) -> dict[str, str | None]:
    """Look up a humanized error message by error key.

    Handles raw ``GDBus.Error:com.verde.Error.Foo: detail`` strings
    by stripping the prefix before lookup.

    Returns the fallback message if the key is not in the catalog.
    """
    # Strip GDBus.Error: prefix if present
    m = _GDBUS_PREFIX_RE.match(error_key)
    if m:
        error_key = m.group(1).strip()

    return dict(ERROR_MESSAGES.get(error_key, _FALLBACK))
