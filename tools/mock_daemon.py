#!/usr/bin/env python3
"""Mock Verde D-Bus daemon for local UI testing.

Runs on the SESSION bus (not system bus) so no root is needed.
Provides fake GPU data so all GUI views render with realistic content.

Profiles:
    (default)   — fully working GPU with live stats
    --degraded  — driver installed but kernel module not loaded (no NVML)
    --no-driver — no driver installed at all
    --offload   — render-offload Optimus laptop (PRIME on-demand); the Power
                  view shows the render-offload hibernate remediation
    --intel     — Intel-only mode (PRIME intel); NVIDIA disabled, nothing to fix

Usage (from project root):
    # Terminal 1 - start mock daemon
    PYTHONPATH=src:src/verde-daemon python3 tools/mock_daemon.py
    PYTHONPATH=src:src/verde-daemon python3 tools/mock_daemon.py --degraded
    PYTHONPATH=src:src/verde-daemon python3 tools/mock_daemon.py --no-driver
    PYTHONPATH=src:src/verde-daemon python3 tools/mock_daemon.py --offload
    PYTHONPATH=src:src/verde-daemon python3 tools/mock_daemon.py --intel

    # Terminal 2 - start GUI against session bus
    VERDE_USE_SESSION_BUS=1 PYTHONPATH=src:src/verde-daemon python3 tools/run_gui.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "com.verde.Manager"
OBJECT_PATH = "/com/verde/Manager"
INTERFACE_NAME = "com.verde.Manager"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_INTROSPECTION_PATH = _PROJECT_ROOT / "data" / "com.verde.Manager.xml"


def _v(type_str: str, value):
    """Shorthand for GLib.Variant construction."""
    return GLib.Variant(type_str, value)


def _build_gpu_info() -> dict:
    return {
        "available": _v("b", True),
        "device_count": _v("i", 1),
        "name": _v("s", "NVIDIA GeForce RTX 4070 Ti"),
        "uuid": _v("s", "GPU-a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
        "driver_version": _v("s", "550.120.02"),
        "cuda_driver_version": _v("s", "12.4"),
        "num_cores": _v("i", 7680),
        "ecc_mode": _v("b", False),
        "pci_bus_id": _v("s", "0000:01:00.0"),
        "pci_domain": _v("u", 0),
        "pci_bus": _v("u", 1),
        "pci_device": _v("u", 0),
        "compute_capability_major": _v("i", 8),
        "compute_capability_minor": _v("i", 9),
        "driver_type": _v("s", "package"),
        "gpu_mode": _v("s", "Default"),
        "cuda_toolkit_version": _v("s", "12.4"),
        "performance_mode": _v("s", "Balanced"),
        "device_count_available": _v("b", True),
        "pci_info_available": _v("b", True),
        "compute_capability_available": _v("b", True),
    }


def _build_current_driver() -> dict:
    return {
        "available": _v("b", True),
        "driver_type": _v("s", "package"),
        "version": _v("s", "550"),
        "package_name": _v("s", "nvidia-driver-550"),
        "variant": _v("s", "server"),
        "module_type": _v("s", "kernel-open"),
        "loaded": _v("b", True),
        "driver_version": _v("s", "550.120.02"),
        "reboot_required": _v("b", False),
        "reboot_reason": _v("s", ""),
    }


def _build_available_drivers() -> list[dict]:
    return [
        {
            "version": _v("s", "550"),
            "variant": _v("s", "server"),
            "package_name": _v("s", "nvidia-driver-550-server"),
            "installed": _v("b", True),
            "recommended": _v("b", True),
            "held": _v("b", False),
            "module_type": _v("s", "kernel-open"),
            "module_status": _v("s", "loaded"),
            "repository": _v("s", "ubuntu"),
            "hold_message": _v("s", ""),
            "recommendation_reason": _v("s", "Latest tested version"),
            "cuda_compatibility": _v("s", "CUDA 12.4"),
            "known_issues": _v("s", ""),
        },
        {
            "version": _v("s", "535"),
            "variant": _v("s", "server"),
            "package_name": _v("s", "nvidia-driver-535-server"),
            "installed": _v("b", False),
            "recommended": _v("b", False),
            "held": _v("b", False),
            "module_type": _v("s", "kernel"),
            "module_status": _v("s", "not loaded"),
            "repository": _v("s", "ubuntu"),
            "hold_message": _v("s", ""),
            "recommendation_reason": _v("s", ""),
            "cuda_compatibility": _v("s", "CUDA 12.2"),
            "known_issues": _v("s", ""),
        },
        {
            "version": _v("s", "470"),
            "variant": _v("s", ""),
            "package_name": _v("s", "nvidia-driver-470"),
            "installed": _v("b", False),
            "recommended": _v("b", False),
            "held": _v("b", False),
            "module_type": _v("s", "kernel"),
            "module_status": _v("s", "not loaded"),
            "repository": _v("s", "ubuntu"),
            "hold_message": _v("s", ""),
            "recommendation_reason": _v("s", ""),
            "cuda_compatibility": _v("s", "CUDA 11.4"),
            "known_issues": _v("s", "Legacy branch - limited support"),
        },
    ]


def _build_driver_metadata() -> dict:
    return {
        "missing_repositories": _v("as", []),
        "run_file_detected": _v("b", False),
        "run_file_message": _v("s", ""),
    }


def _build_power_status() -> dict:
    return {
        "overall_status": _v("s", "issues_found"),
        "suspend_service_active": _v("b", True),
        "hibernate_service_active": _v("b", False),
        "secure_boot_enabled": _v("b", True),
        "mok_enrolled": _v("b", True),
        "wayland_session": _v("b", True),
        "issues": _v(
            "aa{sv}",
            [
                {
                    "type": _v("s", "hibernate"),
                    "severity": _v("s", "warning"),
                    "summary": _v("s", "Hibernate not configured"),
                    "detail": _v(
                        "s",
                        "nvidia-hibernate.service is disabled. "
                        "VRAM contents may be lost on hibernate.",
                    ),
                    "fixable": _v("b", True),
                    "already_fixed": _v("b", False),
                },
            ],
        ),
    }


def _build_gpu_stats() -> dict:
    """Generate randomized GPU stats for realistic live updates."""
    temp = random.randint(38, 72)
    power = random.randint(25000, 220000)
    mem_used = random.randint(512, 8192) * 1048576
    mem_total = 12288 * 1048576
    return {
        "available": _v("b", True),
        "temperature": _v("i", temp),
        "clock_graphics": _v("i", random.randint(210, 2610)),
        "clock_sm": _v("i", random.randint(210, 2610)),
        "clock_mem": _v("i", random.randint(405, 10501)),
        "performance_state": _v("i", random.randint(0, 8)),
        "power_usage": _v("x", power),
        "power_limit": _v("x", 285000),
        "memory_errors": _v("x", 0),
        "performance_mode": _v("s", "Balanced"),
        "throttle_reasons": _v("t", 0),
        "throttle_reasons_decoded": _v("as", []),
        "memory_total": _v("x", mem_total),
        "memory_used": _v("x", mem_used),
        "memory_free": _v("x", mem_total - mem_used),
        "utilization_gpu": _v("i", random.randint(0, 95)),
        "utilization_memory": _v("i", random.randint(5, 60)),
        "memory_available": _v("b", True),
        "utilization_available": _v("b", True),
        "processes": _v(
            "aa{sv}",
            [
                {
                    "pid": _v("i", 1234),
                    "name": _v("s", "Xorg"),
                    "memory": _v("x", 128 * 1048576),
                },
                {
                    "pid": _v("i", 5678),
                    "name": _v("s", "gnome-shell"),
                    "memory": _v("x", 256 * 1048576),
                },
            ],
        ),
        "process_count": _v("i", 2),
    }


# ── Degraded-mode builders (driver installed, module not loaded) ──────


def _build_gpu_info_degraded() -> dict:
    return {
        "available": _v("b", False),
        "reason": _v("s", "Driver installed but kernel module not loaded"),
        "name": _v("s", "GM108M [GeForce 840M]"),
        "device_count": _v("i", 1),
        "driver_version": _v("s", "535"),
        "driver_type": _v("s", "package"),
        "loaded": _v("b", False),
        "pci_bus_id": _v("s", "0000:03:00.0"),
    }


def _build_current_driver_degraded() -> dict:
    return {
        "available": _v("b", False),
        "driver_type": _v("s", "package"),
        "version": _v("s", "535"),
        "package_name": _v("s", "nvidia-driver-535"),
        "variant": _v("s", "desktop"),
        "module_type": _v("s", "dkms"),
        "loaded": _v("b", False),
        "reboot_required": _v("b", False),
        "reboot_reason": _v("s", ""),
    }


def _build_available_drivers_degraded() -> list[dict]:
    return [
        {
            "version": _v("s", "535"),
            "variant": _v("s", ""),
            "package_name": _v("s", "nvidia-driver-535"),
            "installed": _v("b", True),
            "recommended": _v("b", False),
            "held": _v("b", False),
            "module_type": _v("s", "dkms"),
            "module_status": _v("s", "not_loaded"),
            "repository": _v("s", "ubuntu"),
            "hold_message": _v("s", ""),
            "recommendation_reason": _v("s", ""),
            "cuda_compatibility": _v("s", "CUDA 12.2"),
            "known_issues": _v("s", "Legacy GPU — limited feature support"),
        },
    ]


def _build_degraded_state_degraded() -> dict:
    return {
        "state": _v("s", "driver_not_loaded"),
        "driver_type": _v("s", "none"),
        "device_count": _v("i", 0),
        "message": _v(
            "s",
            "NVIDIA driver package is installed but the kernel module is not loaded",
        ),
        "driver_version": _v("s", "535"),
        "package_name": _v("s", "nvidia-driver-535"),
    }


def _build_gpu_stats_degraded() -> dict:
    return {
        "available": _v("b", False),
        "reason": _v("s", "Kernel module not loaded"),
    }


def _build_power_status_degraded() -> dict:
    return {
        "overall_status": _v("s", "issues_found"),
        "suspend_service_active": _v("b", False),
        "hibernate_service_active": _v("b", False),
        "secure_boot_enabled": _v("b", True),
        "mok_enrolled": _v("b", True),
        "wayland_session": _v("b", True),
        "issues": _v(
            "aa{sv}",
            [
                {
                    "type": _v("s", "suspend"),
                    "severity": _v("s", "critical"),
                    "summary": _v("s", "Suspend services not enabled"),
                    "detail": _v(
                        "s",
                        "nvidia-suspend.service and nvidia-resume.service "
                        "are not enabled. GPU VRAM may be lost on suspend.",
                    ),
                    "fixable": _v("b", True),
                    "already_fixed": _v("b", False),
                },
                {
                    "type": _v("s", "hibernate"),
                    "severity": _v("s", "warning"),
                    "summary": _v("s", "Hibernate not configured"),
                    "detail": _v(
                        "s",
                        "nvidia-hibernate.service is disabled and "
                        "power management conf is missing.",
                    ),
                    "fixable": _v("b", True),
                    "already_fixed": _v("b", False),
                },
                {
                    "type": _v("s", "wayland"),
                    "severity": _v("s", "warning"),
                    "summary": _v("s", "nvidia-drm.modeset not set"),
                    "detail": _v(
                        "s",
                        "Kernel parameter nvidia-drm.modeset=1 is not set. "
                        "Wayland sessions may not work correctly.",
                    ),
                    "fixable": _v("b", False),
                    "already_fixed": _v("b", False),
                },
            ],
        ),
    }


# ── Optimus / PRIME display-profile power builders ───────────────────


def _build_power_status_offload() -> dict:
    """PRIME on-demand (render-offload) — the NVIDIA GPU drives no displays."""
    return {
        "overall_status": _v("s", "issues_found"),
        "suspend_service_active": _v("b", True),
        "hibernate_service_active": _v("b", False),
        "secure_boot_enabled": _v("b", False),
        "mok_enrolled": _v("b", False),
        "wayland_session": _v("b", False),
        "gpu_mode": _v("s", "on-demand"),
        "display_profile": _v("s", "offload"),
        "issues": _v(
            "aa{sv}",
            [
                {
                    "type": _v("s", "suspend"),
                    "severity": _v("s", "ok"),
                    "summary": _v("s", "Suspend is managed by the render-offload profile"),
                    "detail": _v(
                        "s",
                        "The NVIDIA GPU is render-offload only (PRIME on-demand) "
                        "and drives no displays.",
                    ),
                    "fixable": _v("b", False),
                    "already_fixed": _v("b", True),
                },
                {
                    "type": _v("s", "hibernate"),
                    "severity": _v("s", "critical"),
                    "summary": _v("s", "NVIDIA render-offload hibernate config is missing"),
                    "detail": _v(
                        "s",
                        "Reliable hibernate requires taking nvidia out of the "
                        "display path — NVreg_PreserveVideoMemoryAllocations=0 and "
                        "nvidia-drm modeset=0 in "
                        "/etc/modprobe.d/nvidia-power-management.conf.",
                    ),
                    "fixable": _v("b", True),
                    "already_fixed": _v("b", False),
                },
                {
                    "type": _v("s", "hibernate"),
                    "severity": _v("s", "warning"),
                    "summary": _v(
                        "s", "NVIDIA sleep services are enabled on a render-offload system"
                    ),
                    "detail": _v(
                        "s",
                        "The nvidia suspend/resume/hibernate services can strand "
                        "the screen on resume and should be disabled.",
                    ),
                    "fixable": _v("b", True),
                    "already_fixed": _v("b", False),
                },
            ],
        ),
    }


def _build_power_status_integrated() -> dict:
    """PRIME intel — the NVIDIA GPU is disabled entirely; hibernate is native."""
    return {
        "overall_status": _v("s", "working"),
        "suspend_service_active": _v("b", True),
        "hibernate_service_active": _v("b", True),
        "secure_boot_enabled": _v("b", False),
        "mok_enrolled": _v("b", False),
        "wayland_session": _v("b", False),
        "gpu_mode": _v("s", "intel"),
        "display_profile": _v("s", "integrated"),
        "issues": _v(
            "aa{sv}",
            [
                {
                    "type": _v("s", "suspend"),
                    "severity": _v("s", "ok"),
                    "summary": _v("s", "Suspend is handled by the integrated GPU"),
                    "detail": _v(
                        "s",
                        "The system is in Intel mode; the NVIDIA GPU is disabled "
                        "and plays no part in suspend/resume.",
                    ),
                    "fixable": _v("b", False),
                    "already_fixed": _v("b", True),
                },
                {
                    "type": _v("s", "hibernate"),
                    "severity": _v("s", "ok"),
                    "summary": _v("s", "Hibernate is properly configured"),
                    "detail": _v(
                        "s",
                        "The NVIDIA GPU is disabled (Intel mode); hibernate needs "
                        "no NVIDIA configuration.",
                    ),
                    "fixable": _v("b", False),
                    "already_fixed": _v("b", True),
                },
            ],
        ),
    }


# ── No-driver mode builders ──────────────────────────────────────────


def _build_gpu_info_no_driver() -> dict:
    return {
        "available": _v("b", False),
        "reason": _v("s", "NVIDIA driver not loaded"),
    }


def _build_current_driver_no_driver() -> dict:
    return {
        "available": _v("b", False),
        "driver_type": _v("s", "none"),
        "version": _v("s", ""),
        "loaded": _v("b", False),
        "reboot_required": _v("b", False),
        "reboot_reason": _v("s", ""),
    }


def _build_degraded_state_no_driver() -> dict:
    return {
        "state": _v("s", "no_driver"),
        "driver_type": _v("s", "none"),
        "device_count": _v("i", 0),
        "message": _v("s", "No NVIDIA driver is installed"),
    }


class MockVerdeService:
    """Session-bus mock of the Verde D-Bus daemon."""

    def __init__(
        self,
        connection: Gio.DBusConnection,
        profile: str = "normal",
    ) -> None:
        self._conn = connection
        self._op_in_progress = False
        self._profile = profile

        xml = _INTROSPECTION_PATH.read_text(encoding="utf-8")
        node_info = Gio.DBusNodeInfo.new_for_xml(xml)

        connection.register_object(
            OBJECT_PATH,
            node_info.interfaces[0],
            self._on_method_call,
            self._on_get_property,
            None,
        )

        if profile == "normal":
            GLib.timeout_add_seconds(2, self._emit_stats)
        print(f"Mock daemon ready on session bus (profile: {profile})")

    # -- Signals --------------------------------------------------------

    def _emit_stats(self) -> bool:
        self._conn.emit_signal(
            None,
            OBJECT_PATH,
            INTERFACE_NAME,
            "GPUStatsUpdated",
            GLib.Variant.new_tuple(_v("a{sv}", _build_gpu_stats())),
        )
        return True

    def _emit_signal(self, name: str, signature: str, args: tuple) -> None:
        self._conn.emit_signal(
            None,
            OBJECT_PATH,
            INTERFACE_NAME,
            name,
            GLib.Variant(signature, args),
        )

    # -- Properties -----------------------------------------------------

    def _on_get_property(self, _conn, _sender, _path, _iface, prop_name):
        if prop_name == "DaemonVersion":
            return _v("s", "0.1.0-mock")
        if prop_name == "OperationInProgress":
            return _v("b", self._op_in_progress)
        return None

    # -- Method dispatch ------------------------------------------------

    def _on_method_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        handler = getattr(self, f"_handle_{method}", None)
        if handler:
            handler(params, invocation)
        else:
            invocation.return_dbus_error(
                "com.verde.Error.NotImplemented",
                f"Mock: {method} not implemented",
            )

    # -- Read-only methods ----------------------------------------------

    def _handle_Ping(self, _params, invocation):
        invocation.return_value(None)

    def _handle_GetGPUInfo(self, _params, invocation):
        if self._profile == "degraded":
            data = _build_gpu_info_degraded()
        elif self._profile == "no_driver":
            data = _build_gpu_info_no_driver()
        else:
            data = _build_gpu_info()
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", data)))

    def _handle_GetGPUStats(self, _params, invocation):
        if self._profile in ("degraded", "no_driver"):
            data = _build_gpu_stats_degraded()
        else:
            data = _build_gpu_stats()
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", data)))

    def _handle_GetCurrentDriver(self, _params, invocation):
        if self._profile == "degraded":
            data = _build_current_driver_degraded()
        elif self._profile == "no_driver":
            data = _build_current_driver_no_driver()
        else:
            data = _build_current_driver()
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", data)))

    def _handle_ListAvailableDrivers(self, _params, invocation):
        if self._profile == "degraded":
            drivers_data = _build_available_drivers_degraded()
        elif self._profile == "no_driver":
            drivers_data = []
        else:
            drivers_data = _build_available_drivers()
        drivers = _v("aa{sv}", drivers_data)
        metadata = _v("a{sv}", _build_driver_metadata())
        invocation.return_value(GLib.Variant.new_tuple(drivers, metadata))

    def _handle_GetDegradedState(self, _params, invocation):
        if self._profile == "degraded":
            state = _build_degraded_state_degraded()
        elif self._profile == "no_driver":
            state = _build_degraded_state_no_driver()
        else:
            state = {
                "state": _v("s", "healthy"),
                "driver_type": _v("s", "package"),
                "device_count": _v("i", 1),
                "message": _v("s", ""),
            }
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", state)))

    def _handle_GetPowerStatus(self, _params, invocation):
        if self._profile == "degraded":
            data = _build_power_status_degraded()
        elif self._profile == "offload":
            data = _build_power_status_offload()
        elif self._profile == "integrated":
            data = _build_power_status_integrated()
        else:
            data = _build_power_status()
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", data)))

    def _handle_GetPreflightCheck(self, params, invocation):
        result = {
            "overall_pass": _v("b", True),
            "duration_ms": _v("i", 150),
            "checks": _v(
                "aa{sv}",
                [
                    {
                        "name": _v("s", "Disk space"),
                        "status": _v("s", "pass"),
                        "description": _v("s", "12.4 GB available"),
                    },
                    {
                        "name": _v("s", "No dpkg lock"),
                        "status": _v("s", "pass"),
                        "description": _v("s", "Package manager is idle"),
                    },
                ],
            ),
        }
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", result)))

    def _handle_GetPostRebootSummary(self, _params, invocation):
        invocation.return_value(
            GLib.Variant.new_tuple(_v("a{sv}", {"has_pending": _v("b", False)}))
        )

    def _handle_ListSnapshots(self, _params, invocation):
        snapshots = [
            {
                "id": _v("s", "20260319T120000_nvidia-550-a1b2"),
                "timestamp": _v("s", "2026-03-19T12:00:00+00:00"),
                "driver_version": _v("s", "550"),
                "kernel_version": _v("s", "6.8.0-45-generic"),
                "packages": _v("as", ["nvidia-driver-550=550.120.02"]),
                "dkms_status": _v("s", "installed"),
                "file_size": _v("x", 4096),
                "sha256": _v("s", "abcdef1234567890"),
            },
        ]
        invocation.return_value(GLib.Variant.new_tuple(_v("aa{sv}", snapshots)))

    def _handle_DeleteSnapshot(self, _params, invocation):
        invocation.return_value(GLib.Variant.new_tuple(_v("b", True)))

    def _handle_ClearPostRebootSummary(self, _params, invocation):
        invocation.return_value(None)

    def _handle_GenerateDiagnosticReport(self, _params, invocation):
        report = json.dumps(
            {
                "generated_at": "2026-03-19T12:00:00Z",
                "gpu": {"name": "RTX 4070 Ti", "driver": "550.120.02"},
                "system": {"kernel": "6.8.0-45-generic", "os": "Ubuntu 24.04"},
                "issues": [],
            },
            indent=2,
        )
        invocation.return_value(GLib.Variant.new_tuple(_v("s", report)))

    # -- Privileged methods (simulated) ---------------------------------

    def _handle_InstallDriver(self, _params, invocation):
        self._start_mock_operation("install", invocation)

    def _handle_RollbackDriver(self, _params, invocation):
        self._start_mock_operation("rollback", invocation)

    def _handle_RepairDpkg(self, _params, invocation):
        self._start_mock_operation("repair", invocation)

    def _handle_FixSuspend(self, _params, invocation):
        self._start_mock_operation("fix-suspend", invocation)

    def _handle_FixHibernate(self, _params, invocation):
        self._start_mock_operation("fix-hibernate", invocation)

    def _start_mock_operation(self, prefix: str, invocation) -> None:
        op_id = f"mock-{prefix}-{uuid.uuid4().hex[:8]}"
        invocation.return_value(GLib.Variant.new_tuple(_v("s", op_id)))
        self._op_in_progress = True
        GLib.timeout_add(500, self._tick_progress, op_id, 0)

    def _tick_progress(self, op_id: str, pct: int) -> bool:
        pct = min(pct + random.randint(10, 25), 100)
        self._emit_signal(
            "OperationProgress",
            "(sds)",
            (op_id, float(pct), f"Working... {pct}%"),
        )
        if pct >= 100:
            self._op_in_progress = False
            self._emit_signal(
                "OperationComplete",
                "(sbs)",
                (op_id, True, "Operation completed successfully."),
            )
            return False
        GLib.timeout_add(random.randint(300, 800), self._tick_progress, op_id, pct)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock Verde D-Bus daemon")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--degraded",
        action="store_true",
        help="Simulate driver installed but kernel module not loaded",
    )
    group.add_argument(
        "--no-driver",
        action="store_true",
        help="Simulate no NVIDIA driver installed",
    )
    group.add_argument(
        "--offload",
        action="store_true",
        help="Simulate a render-offload Optimus laptop (PRIME on-demand)",
    )
    group.add_argument(
        "--intel",
        action="store_true",
        help="Simulate Intel-only mode (PRIME intel, NVIDIA disabled)",
    )
    args = parser.parse_args()

    if args.degraded:
        profile = "degraded"
    elif args.no_driver:
        profile = "no_driver"
    elif args.offload:
        profile = "offload"
    elif args.intel:
        profile = "integrated"
    else:
        profile = "normal"

    if not _INTROSPECTION_PATH.exists():
        print(
            f"Error: {_INTROSPECTION_PATH} not found. Run from project root.",
            file=sys.stderr,
        )
        sys.exit(1)

    loop = GLib.MainLoop()

    def on_bus_acquired(conn, _name):
        MockVerdeService(conn, profile=profile)

    def on_name_lost(_conn, _name):
        print(f"Lost bus name {BUS_NAME}", file=sys.stderr)
        loop.quit()

    Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired,
        lambda *_a: print(f"Acquired {BUS_NAME}"),
        on_name_lost,
    )

    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nMock daemon stopped.")


if __name__ == "__main__":
    main()
