"""Input validators for D-Bus method parameters (FR37, NFR-SEC-9)."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Reject inputs longer than this before regex matching or logging to
# prevent DoS via multi-megabyte strings passed through logging/D-Bus.
_MAX_INPUT_LENGTH = 256

DRIVER_VERSION_PATTERN = re.compile(r"^[0-9]{3,4}(-server|-open)?$")
SNAPSHOT_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}_[a-zA-Z0-9._-]+-[0-9a-f]{4}$")
OPERATION_NAME_PATTERN = re.compile(
    r"^(driver_install|driver_switch|driver_rollback|fix_suspend|fix_hibernate|fix_module)$"
)
MODIFICATION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _check_length(value: str, label: str) -> None:
    """Reject inputs exceeding the maximum safe length."""
    if len(value) > _MAX_INPUT_LENGTH:
        raise ValueError(f"{label} exceeds maximum length ({_MAX_INPUT_LENGTH} chars)")


def _check_null_bytes(value: str, label: str) -> None:
    """Reject inputs containing null bytes."""
    if "\x00" in value:
        log.warning("Null byte in %s input rejected", label)
        raise ValueError(f"{label} contains invalid characters")


def validate_driver_version(version: str) -> str:
    """Validate a driver version string.

    Returns the validated string on success, raises ValueError on failure.
    """
    _check_length(version, "Driver version")
    _check_null_bytes(version, "Driver version")
    if not DRIVER_VERSION_PATTERN.match(version):
        log.warning("Invalid driver version rejected")
        raise ValueError("Invalid driver version format")
    return version


def validate_snapshot_id(snapshot_id: str) -> str:
    """Validate a snapshot ID string.

    Returns the validated string on success, raises ValueError on failure.
    """
    _check_length(snapshot_id, "Snapshot ID")
    _check_null_bytes(snapshot_id, "Snapshot ID")
    if not SNAPSHOT_ID_PATTERN.match(snapshot_id):
        log.warning("Invalid snapshot ID rejected")
        raise ValueError("Invalid snapshot ID format")
    return snapshot_id


def validate_operation_name(operation: str) -> str:
    """Validate an operation name string.

    Returns the validated string on success, raises ValueError on failure.
    """
    _check_length(operation, "Operation name")
    _check_null_bytes(operation, "Operation name")
    if not OPERATION_NAME_PATTERN.match(operation):
        log.warning("Invalid operation name rejected")
        raise ValueError("Invalid operation name format")
    return operation


def validate_modification_id(mod_id: str) -> str:
    """Validate a modification ID string (UUID v4).

    Returns the validated string on success, raises ValueError on failure.
    """
    _check_length(mod_id, "Modification ID")
    _check_null_bytes(mod_id, "Modification ID")
    if not MODIFICATION_ID_PATTERN.match(mod_id):
        log.warning("Invalid modification ID rejected")
        raise ValueError("Invalid modification ID format")
    return mod_id
