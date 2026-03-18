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
    GPU_LOST = "gpu_lost"
    NVML_UNAVAILABLE = "nvml_unavailable"


# Human-readable messages for each state — never expose raw error codes.
_STATE_MESSAGES: dict[DegradedState, str] = {
    DegradedState.NORMAL: "GPU is operating normally",
    DegradedState.NO_GPU: "No NVIDIA GPU detected on this system",
    DegradedState.NOUVEAU_ACTIVE: ("NVIDIA GPU is using the open-source nouveau driver"),
    DegradedState.NO_DRIVER: "No NVIDIA driver is installed",
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


def detect_degraded_state(nvml: NvmlWrapper) -> DegradedState:
    """Determine current degraded state from NVML and sysfs checks.

    Parameters
    ----------
    nvml : NvmlWrapper
        Initialized (or failed-to-initialize) NVML wrapper instance.

    Returns
    -------
    DegradedState
        The most specific degraded state detected.
    """
    from verde_daemon.nvml_wrapper import Unavailable

    # 1. NVML library loaded?
    if nvml._lib is None:
        driver = detect_driver_type()
        if driver == "nouveau":
            return DegradedState.NOUVEAU_ACTIVE
        if driver == "none":
            return DegradedState.NO_DRIVER
        return DegradedState.NVML_UNAVAILABLE

    # 2. Device count check
    count = nvml.device_count()
    if count is Unavailable or count == 0:
        driver = detect_driver_type()
        if driver == "nouveau":
            return DegradedState.NOUVEAU_ACTIVE
        if driver == "none":
            return DegradedState.NO_DRIVER
        return DegradedState.NO_GPU

    # 3. Driver type check (NVML loaded but driver might be nouveau)
    driver = detect_driver_type()
    if driver == "nouveau":
        return DegradedState.NOUVEAU_ACTIVE
    if driver == "none":
        return DegradedState.NO_DRIVER

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
