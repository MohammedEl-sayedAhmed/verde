"""Degraded state detection for the Verde daemon.

Checks NVML availability, GPU presence, driver type, and GPU-lost
conditions.  All checks are read-only and require no root privileges.
"""

from __future__ import annotations

import enum
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verde_daemon.nvml_wrapper import NvmlWrapper

log = logging.getLogger("verde-daemon.degraded_states")


class DegradedState(enum.Enum):
    """Runtime GPU health states."""

    NORMAL = "normal"
    NO_GPU = "no_gpu"
    NOUVEAU_ACTIVE = "nouveau_active"
    NO_DRIVER = "no_driver"
    DRIVER_NOT_LOADED = "driver_not_loaded"
    GPU_LOST = "gpu_lost"
    NVML_UNAVAILABLE = "nvml_unavailable"


# Human-readable messages for each state — never expose raw error codes.
_STATE_MESSAGES: dict[DegradedState, str] = {
    DegradedState.NORMAL: "GPU is operating normally",
    DegradedState.NO_GPU: "No NVIDIA GPU detected on this system",
    DegradedState.NOUVEAU_ACTIVE: ("NVIDIA GPU is using the open-source nouveau driver"),
    DegradedState.NO_DRIVER: "No NVIDIA driver is installed",
    DegradedState.DRIVER_NOT_LOADED: (
        "NVIDIA driver package is installed but the kernel module is not loaded"
    ),
    DegradedState.GPU_LOST: ("GPU is no longer responding — it may have fallen off the bus"),
    DegradedState.NVML_UNAVAILABLE: "NVIDIA management library is not available",
}


def detect_driver_type() -> str:
    """Detect which NVIDIA-related driver is loaded via sysfs.

    Returns ``"proprietary"``, ``"nouveau"``, or ``"none"``.
    Pure sysfs check — no subprocess calls, works without root.
    """
    if os.path.isdir("/sys/module/nvidia"):
        return "proprietary"
    if os.path.isdir("/sys/module/nouveau"):
        return "nouveau"
    return "none"


def detect_degraded_state(
    nvml: NvmlWrapper,
    *,
    driver_installed: bool = False,
) -> DegradedState:
    """Determine current degraded state from NVML and sysfs checks.

    Parameters
    ----------
    nvml : NvmlWrapper
        Initialized (or failed-to-initialize) NVML wrapper instance.
    driver_installed : bool
        Whether a driver *package* is installed (from dpkg), even if the
        kernel module is not loaded.  When ``True`` and the sysfs check
        would normally return ``NO_DRIVER``, the more accurate
        ``DRIVER_NOT_LOADED`` is returned instead.

    Returns
    -------
    DegradedState
        The most specific degraded state detected.
    """
    from verde_daemon.nvml_wrapper import Unavailable

    def _no_driver_or_not_loaded() -> DegradedState:
        if driver_installed:
            return DegradedState.DRIVER_NOT_LOADED
        return DegradedState.NO_DRIVER

    # 1. NVML library loaded?
    if nvml._lib is None:
        driver = detect_driver_type()
        if driver == "nouveau":
            return DegradedState.NOUVEAU_ACTIVE
        if driver == "none":
            return _no_driver_or_not_loaded()
        return DegradedState.NVML_UNAVAILABLE

    # 2. Device count check
    count = nvml.device_count()
    if count is Unavailable or count == 0:
        driver = detect_driver_type()
        if driver == "nouveau":
            return DegradedState.NOUVEAU_ACTIVE
        if driver == "none":
            return _no_driver_or_not_loaded()
        return DegradedState.NO_GPU

    # 3. Driver type check (NVML loaded but driver might be nouveau)
    driver = detect_driver_type()
    if driver == "nouveau":
        return DegradedState.NOUVEAU_ACTIVE
    if driver == "none":
        return _no_driver_or_not_loaded()

    return DegradedState.NORMAL


def get_state_message(state: DegradedState) -> str:
    """Return a humanized message for the given degraded state."""
    return _STATE_MESSAGES.get(state, "Unknown state")


def build_state_info(
    state: DegradedState,
    driver_type: str,
    device_count: int,
) -> dict[str, object]:
    """Build a state info dict suitable for D-Bus serialization.

    Returns plain Python types — the caller converts to GLib.Variant.
    """
    return {
        "state": state.value,
        "driver_type": driver_type,
        "device_count": device_count,
        "message": get_state_message(state),
    }
