"""Input validators for D-Bus method parameters (FR37, NFR-SEC-9)."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Reject inputs longer than this before regex matching or logging to
# prevent DoS via multi-megabyte strings passed through logging/D-Bus.
_MAX_INPUT_LENGTH = 256

DRIVER_VERSION_PATTERN = re.compile(r"^[0-9]{3,4}(-server|-open)?$")
SNAPSHOT_ID_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}_nvidia-[0-9]{3,4}$"
)
OPERATION_NAME_PATTERN = re.compile(
    r"^(driver_install|driver_switch|driver_rollback|fix_suspend|fix_hibernate)$"
)


def _check_length(value: str, label: str) -> None:
    """Reject inputs exceeding the maximum safe length."""
    if len(value) > _MAX_INPUT_LENGTH:
        raise ValueError(f"{label} exceeds maximum length ({_MAX_INPUT_LENGTH} chars)")


def validate_driver_version(version: str) -> str:
    """Validate a driver version string.

    Returns the validated string on success, raises ValueError on failure.
    """
    _check_length(version, "Driver version")
    if not DRIVER_VERSION_PATTERN.match(version):
        log.warning("Invalid driver version rejected: %r", version)
        raise ValueError(f"Invalid driver version: {version!r}")
    return version


def validate_snapshot_id(snapshot_id: str) -> str:
    """Validate a snapshot ID string.

    Returns the validated string on success, raises ValueError on failure.
    """
    _check_length(snapshot_id, "Snapshot ID")
    if not SNAPSHOT_ID_PATTERN.match(snapshot_id):
        log.warning("Invalid snapshot ID rejected: %r", snapshot_id)
        raise ValueError(f"Invalid snapshot ID: {snapshot_id!r}")
    return snapshot_id


def validate_operation_name(operation: str) -> str:
    """Validate an operation name string.

    Returns the validated string on success, raises ValueError on failure.
    """
    _check_length(operation, "Operation name")
    if not OPERATION_NAME_PATTERN.match(operation):
        log.warning("Invalid operation name rejected: %r", operation)
        raise ValueError(f"Invalid operation name: {operation!r}")
    return operation
