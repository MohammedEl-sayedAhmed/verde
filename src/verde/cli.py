"""CLI health check and scripting support for Verde.

Provides ``--check`` and ``--json`` modes that query the daemon via
D-Bus without requiring a display server or GTK initialization.

Exit codes: 0=healthy, 1=warning, 2=critical, 3=no GPU, 4=error.

References: FR93; Story 6.5.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

# ── Health thresholds ────────────────────────────────────────────────
TEMP_WARN = 85
TEMP_CRIT = 95
VRAM_WARN_PCT = 90

# ── Exit codes ───────────────────────────────────────────────────────
EXIT_HEALTHY = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2
EXIT_NO_GPU = 3
EXIT_ERROR = 4

_BUS_NAME = "com.verde.Manager"
_OBJECT_PATH = "/com/verde/Manager"
_INTERFACE = "com.verde.Manager"
_DBUS_TIMEOUT_MS = 2000


def _get_proxy() -> Gio.DBusProxy:
    """Create a synchronous D-Bus proxy for the Verde daemon."""
    return Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SYSTEM,
        Gio.DBusProxyFlags.NONE,
        None,
        _BUS_NAME,
        _OBJECT_PATH,
        _INTERFACE,
        None,
    )


def _call_method(proxy: Gio.DBusProxy, method: str) -> Any:
    """Call a D-Bus method and return unpacked result."""
    result = proxy.call_sync(
        method,
        None,
        Gio.DBusCallFlags.NONE,
        _DBUS_TIMEOUT_MS,
        None,
    )
    if not result:
        return None
    items = result.unpack()
    return items[0] if items else None


def evaluate_health(gpu_info: dict, gpu_stats: dict) -> tuple[int, str, str]:
    """Evaluate GPU health from D-Bus data.

    Returns (exit_code, status_label, reason).
    """
    if not gpu_info or not gpu_stats:
        return EXIT_NO_GPU, "no_gpu", "No NVIDIA GPU detected"

    gpu_name = gpu_info.get("gpu_name", "Unknown GPU")
    driver_ver = gpu_info.get("driver_version", "")
    temp = gpu_stats.get("temperature", 0)
    mem_used = gpu_stats.get("memory_used", 0.0)
    mem_total = gpu_stats.get("memory_total", 0.0)
    throttle = gpu_stats.get("throttle_reasons", "")
    gpu_available = gpu_stats.get("gpu_available", False)

    if not gpu_available:
        return EXIT_NO_GPU, "no_gpu", "No NVIDIA GPU detected"

    # Check for driver not loaded
    if not driver_ver:
        return EXIT_CRITICAL, "critical", f"{gpu_name} — driver not loaded"

    # Critical: temperature >95°C
    if temp > TEMP_CRIT:
        return EXIT_CRITICAL, "critical", f"{gpu_name} — temperature {temp}°C (critical)"

    # Warning checks
    reasons: list[str] = []
    if temp >= TEMP_WARN:
        reasons.append(f"temp {temp}°C")
    if (
        mem_total > 0
        and math.isfinite(mem_used)
        and math.isfinite(mem_total)
        and (mem_used / mem_total * 100) > VRAM_WARN_PCT
    ):
        pct = int(mem_used / mem_total * 100)
        reasons.append(f"VRAM {pct}%")
    if throttle and any(t.strip() and t.strip().lower() != "none" for t in throttle.split(",")):
        reasons.append("throttling active")

    if reasons:
        detail = ", ".join(reasons)
        return EXIT_WARNING, "warning", f"{gpu_name} — {detail}"

    return (
        EXIT_HEALTHY,
        "healthy",
        f"{gpu_name} — driver {driver_ver}, temp {temp}°C",
    )


def _format_plain_check(exit_code: int, reason: str) -> str:
    """Format a one-line plain text check result."""
    labels = {
        EXIT_HEALTHY: "OK",
        EXIT_WARNING: "WARNING",
        EXIT_CRITICAL: "CRITICAL",
        EXIT_NO_GPU: "NO GPU",
        EXIT_ERROR: "ERROR",
    }
    label = labels.get(exit_code, "UNKNOWN")
    return f"{label}: {reason}"


def _build_json_check(
    exit_code: int,
    status: str,
    gpu_info: dict | None,
    gpu_stats: dict | None,
    daemon_version: str = "",
) -> dict:
    """Build the JSON check output structure."""
    gpus = []
    if gpu_info and gpu_stats:
        mem_used = gpu_stats.get("memory_used", 0.0)
        mem_total = gpu_stats.get("memory_total", 0.0)
        throttle_str = gpu_stats.get("throttle_reasons", "")
        throttle_list = [
            t.strip() for t in throttle_str.split(",") if t.strip() and t.strip().lower() != "none"
        ]

        gpus.append(
            {
                "index": 0,
                "name": gpu_info.get("gpu_name", "Unknown"),
                "driver_version": gpu_info.get("driver_version", ""),
                "temperature_c": gpu_stats.get("temperature", 0),
                "utilization_pct": gpu_stats.get("utilization", 0),
                "memory_used_mib": int(mem_used) if math.isfinite(mem_used) else 0,
                "memory_total_mib": int(mem_total) if math.isfinite(mem_total) else 0,
                "fan_speed_pct": gpu_stats.get("fan_speed", 0),
                "power_draw_w": float(gpu_stats.get("power_draw", 0.0))
                if math.isfinite(gpu_stats.get("power_draw", 0.0))
                else 0.0,
                "throttle_reasons": throttle_list,
                "health": status,
            }
        )

    return {
        "status": status,
        "exit_code": exit_code,
        "gpus": gpus,
        "daemon_version": daemon_version,
    }


def _build_json_error(error_key: str, message: str, exit_code: int) -> dict:
    """Build a JSON error output structure."""
    return {
        "error": error_key,
        "message": message,
        "exit_code": exit_code,
    }


def run_check(use_json: bool = False) -> int:
    """Run the CLI health check.  Returns the exit code."""
    try:
        proxy = _get_proxy()
    except GLib.Error:
        msg = "Verde daemon is not running or unreachable"
        if use_json:
            sys.stdout.write(
                json.dumps(_build_json_error("daemon_unreachable", msg, EXIT_ERROR), indent=2)
                + "\n"
            )
        else:
            sys.stderr.write(f"ERROR: {msg}\n")
        return EXIT_ERROR

    try:
        gpu_info = _call_method(proxy, "GetGPUInfo")
        gpu_stats = _call_method(proxy, "GetGPUStats")
    except GLib.Error:
        msg = "Failed to query GPU status from daemon"
        if use_json:
            sys.stdout.write(
                json.dumps(_build_json_error("query_failed", msg, EXIT_ERROR), indent=2) + "\n"
            )
        else:
            sys.stderr.write(f"ERROR: {msg}\n")
        return EXIT_ERROR

    exit_code, status, reason = evaluate_health(gpu_info or {}, gpu_stats or {})

    # Get daemon version
    daemon_version = ""
    try:
        ver_result = proxy.call_sync(
            "org.freedesktop.DBus.Properties.Get",
            GLib.Variant("(ss)", (_INTERFACE, "DaemonVersion")),
            Gio.DBusCallFlags.NONE,
            _DBUS_TIMEOUT_MS,
            None,
        )
        if ver_result:
            items = ver_result.unpack()
            daemon_version = str(items[0]) if items else ""
    except GLib.Error:
        pass

    if use_json:
        output = _build_json_check(exit_code, status, gpu_info, gpu_stats, daemon_version)
        sys.stdout.write(json.dumps(output, indent=2) + "\n")
    else:
        sys.stdout.write(_format_plain_check(exit_code, reason) + "\n")

    return exit_code


def run_status_json() -> int:
    """Run full GPU status dump in JSON mode (no health evaluation)."""
    try:
        proxy = _get_proxy()
    except GLib.Error:
        sys.stdout.write(
            json.dumps(
                _build_json_error(
                    "daemon_unreachable",
                    "Verde daemon is not running or unreachable",
                    EXIT_ERROR,
                ),
                indent=2,
            )
            + "\n"
        )
        return EXIT_ERROR

    try:
        gpu_info = _call_method(proxy, "GetGPUInfo")
        gpu_stats = _call_method(proxy, "GetGPUStats")
    except GLib.Error:
        sys.stdout.write(
            json.dumps(
                _build_json_error(
                    "query_failed",
                    "Failed to query GPU status from daemon",
                    EXIT_ERROR,
                ),
                indent=2,
            )
            + "\n"
        )
        return EXIT_ERROR

    output = {
        "gpu_info": dict(gpu_info) if gpu_info else None,
        "gpu_stats": dict(gpu_stats) if gpu_stats else None,
    }
    sys.stdout.write(json.dumps(output, indent=2) + "\n")
    return EXIT_HEALTHY
