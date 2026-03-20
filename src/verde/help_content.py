"""Centralized tooltip content catalog for Verde GUI.

All metric and status tooltips live here — views import from this module
rather than hard-coding tooltip strings.  Every string is wrapped in ``_()``
for gettext internationalisation.
"""

from __future__ import annotations

# gettext stub — will be replaced by the real gettext at runtime
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Metric tooltips — keyed by the metric identifier used in the dashboard
# ---------------------------------------------------------------------------
METRIC_TOOLTIPS: dict[str, str] = {
    "temperature": _(
        "How hot the GPU is running. Below 80\u00b0C is normal for most GPUs. "
        "Above 90\u00b0C, the GPU may slow down to protect itself."
    ),
    "utilization": _(
        "How much of the GPU\u2019s processing power is currently being used. "
        "0% means idle, 100% means fully loaded."
    ),
    "vram_usage": _(
        "Video memory used by the GPU \u2014 like RAM, but dedicated to graphics. "
        "Running out of VRAM can cause application crashes or slowdowns."
    ),
    "memory_total": _("The total amount of video memory (VRAM) available on this GPU."),
    "power_draw": _("How much power the GPU is currently consuming, measured in watts."),
    "power_limit": _(
        "The maximum power the GPU is allowed to draw. "
        "The GPU will throttle performance to stay within this limit."
    ),
    "fan_speed": _(
        "How fast the GPU\u2019s cooling fan is spinning. "
        "Higher speeds mean more cooling but also more noise."
    ),
    "p_state": _(
        "Power state \u2014 P0 is maximum performance, P8 is idle. "
        "The GPU automatically adjusts based on workload."
    ),
    "clock_speed_graphics": _(
        "The speed of the GPU\u2019s graphics processor. "
        "Higher values mean more performance but also more heat and power use."
    ),
    "clock_speed_memory": _(
        "The speed of the GPU\u2019s video memory interface. "
        "Faster memory clocks improve bandwidth-heavy workloads."
    ),
    "clock_speed_sm": _(
        "The speed of the GPU\u2019s streaming multiprocessors. "
        "These are the compute units that run shader and compute tasks."
    ),
    "throttle_reason": _(
        "Why the GPU is limiting its performance. Common reasons include "
        "high temperature, power limits, or software-imposed caps."
    ),
    "driver_version": _("The version number of the currently installed NVIDIA driver software."),
    "driver_type": _(
        "Whether you\u2019re using the proprietary (closed-source, best performance) "
        "or open-source NVIDIA driver."
    ),
    "gpu_model": _("The model name of the NVIDIA GPU installed in this system."),
    "cuda_version": _(
        "The maximum CUDA toolkit version supported by your current driver. "
        "Applications requiring a newer CUDA version need a newer driver."
    ),
    "compute_capability": _(
        "A version number describing the GPU\u2019s hardware features. "
        "Higher numbers support more advanced compute operations."
    ),
    "pcie_info": _(
        "The connection between the GPU and the rest of the computer. "
        "Wider or faster connections allow more data transfer."
    ),
    "ecc_memory": _(
        "Error-correcting code memory detects and fixes single-bit memory errors. "
        "Mainly used in data-centre and professional GPUs."
    ),
    "gpu_mode": _(
        "Whether the system uses the NVIDIA GPU exclusively or switches "
        "between integrated and discrete graphics (Optimus / PRIME)."
    ),
    "multi_gpu": _("The number of NVIDIA GPUs detected in this system."),
    "cuda_cores": _(
        "The number of parallel processing units (CUDA cores) on the GPU. "
        "More cores allow more simultaneous compute operations."
    ),
    "power_profile": _(
        "The current power management profile. Performance mode uses more "
        "power for maximum speed; Power Saver reduces power consumption."
    ),
    "cuda_toolkit_version": _(
        "The CUDA toolkit version installed on your system. "
        "Used by GPU-accelerated applications for computation."
    ),
}

# ---------------------------------------------------------------------------
# Status tooltips — keyed by health-indicator level
# ---------------------------------------------------------------------------
STATUS_TOOLTIPS: dict[str, str] = {
    "healthy": _("Everything is working normally. No action is needed."),
    "warning": _(
        "One or more values are outside the typical range. "
        "The system is still operational but may need attention soon."
    ),
    "critical": _(
        "One or more values are at a dangerous level. "
        "Performance may be reduced to protect hardware. Immediate attention recommended."
    ),
}
