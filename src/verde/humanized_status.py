"""Humanized status conversion functions for Verde GUI.

Converts raw system values (temperatures, P-states, byte counts, etc.)
into plain-language descriptions.  Every user-facing string is wrapped
in ``_()`` for gettext.
"""

from __future__ import annotations

# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]


def humanize_temperature(temp_c: int, warn: int = 80, crit: int = 90) -> str:
    """Convert raw temperature to human-readable description."""
    if temp_c < warn:
        return _("{temp}\u00b0C \u2014 Running within normal range").format(temp=temp_c)
    elif temp_c < crit:
        return _("{temp}\u00b0C \u2014 Running warm, but within limits").format(temp=temp_c)
    else:
        return _("{temp}\u00b0C \u2014 Running hot, performance may be reduced").format(
            temp=temp_c
        )


_P_STATE_LABELS: dict[str, str] = {
    "P0": _("P0 \u2014 Maximum performance"),
    "P1": _("P1 \u2014 High performance"),
    "P2": _("P2 \u2014 High performance, reduced clocks"),
    "P3": _("P3 \u2014 Moderate performance"),
    "P4": _("P4 \u2014 Moderate performance, power saving"),
    "P5": _("P5 \u2014 Low performance"),
    "P6": _("P6 \u2014 Low performance, power saving"),
    "P7": _("P7 \u2014 Very low performance"),
    "P8": _("P8 \u2014 Idle, power saving"),
}


def humanize_power_state(p_state: str) -> str:
    """Convert P-state code to human-readable description."""
    return _P_STATE_LABELS.get(p_state, _("{state} \u2014 Power state").format(state=p_state))


_THROTTLE_MAP: dict[str, str] = {
    "None": _("Not throttled \u2014 GPU is running at full speed"),
    "Thermal": _("Performance limited by temperature \u2014 GPU is too hot"),
    "Power": _("Performance limited by power draw \u2014 approaching power limit"),
    "Board": _("Performance limited by board design"),
    "Utilization": _("Performance limited by low utilization"),
    "Reliability": _("Performance limited for reliability"),
    "Operating": _("Performance limited by operating conditions"),
    "GPU Idle": _("GPU is idle \u2014 clocks reduced to save power"),
    "Applications Clocks": _("Performance limited by application clock settings"),
    "SW Power Cap": _("Performance limited by a software power cap"),
    "SW Thermal": _("Performance limited by a software thermal policy"),
    "HW Power Brake": _("Performance limited by a hardware power brake signal"),
    "Display Clocks": _("Performance limited by display clock requirements"),
}


def humanize_throttle_reason(reason: str) -> str:
    """Convert throttle reason to human-readable description."""
    return _THROTTLE_MAP.get(
        reason,
        _("Performance limited \u2014 {reason}").format(reason=reason),
    )


def humanize_vram(used_bytes: int, total_bytes: int) -> str:
    """Convert raw VRAM bytes to human-readable description."""
    used_gb = used_bytes / (1024**3)
    total_gb = total_bytes / (1024**3)
    pct = min(used_bytes / total_bytes * 100, 100.0) if total_bytes > 0 else 0.0
    return _("{used:.1f} GB used of {total:.1f} GB ({pct:.0f}%)").format(
        used=used_gb,
        total=total_gb,
        pct=pct,
    )


def humanize_driver_status(version: str, driver_type: str) -> str:
    """Convert raw driver info to human-readable description."""
    type_label = {
        "proprietary": _("Proprietary"),
        "open": _("Open kernel"),
        "nouveau": _("Open-source (nouveau)"),
    }.get(driver_type, driver_type)
    return _("{type_label} driver, version {version}").format(
        type_label=type_label,
        version=version,
    )


def humanize_suspend_status(status: str) -> str:
    """Convert suspend status to human-readable description."""
    status_map: dict[str, str] = {
        "ok": _("Suspend is working normally"),
        "issues_found": _(
            "Suspend or hibernate issues detected \u2014 check the Power tab for details"
        ),
        "unknown": _("Suspend status could not be determined"),
    }
    return status_map.get(status, _("Suspend status: {status}").format(status=status))


def humanize_operation_error(raw_error: str) -> str:
    """Strip raw apt/subprocess output into a user-friendly summary.

    Returns a generic message if the input is empty or unrecognisable.
    """
    if not raw_error or not raw_error.strip():
        return _("An error occurred during the operation.")

    # Strip common apt prefixes
    cleaned = raw_error.strip()
    for prefix in ("E: ", "W: ", "dpkg: error: "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]

    # Truncate very long output to first sentence
    if len(cleaned) > 200:
        dot = cleaned.find(".", 0, 200)
        cleaned = cleaned[: dot + 1] if dot > 0 else cleaned[:200] + "\u2026"

    return cleaned
