#!/usr/bin/env python3
"""Mock Verde D-Bus daemon for local UI testing.

Runs on the SESSION bus (not system bus) so no root is needed.
Provides fake GPU data so all GUI views render with realistic content.

Usage (from project root):
    # Terminal 1 - start mock daemon
    PYTHONPATH=src:src/verde-daemon python3 tools/mock_daemon.py

    # Terminal 2 - start GUI against session bus
    VERDE_USE_SESSION_BUS=1 PYTHONPATH=src:src/verde-daemon python3 tools/run_gui.py
"""

from __future__ import annotations

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


class MockVerdeService:
    """Session-bus mock of the Verde D-Bus daemon."""

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self._conn = connection
        self._op_in_progress = False

        xml = _INTROSPECTION_PATH.read_text(encoding="utf-8")
        node_info = Gio.DBusNodeInfo.new_for_xml(xml)

        connection.register_object(
            OBJECT_PATH,
            node_info.interfaces[0],
            self._on_method_call,
            self._on_get_property,
            None,
        )

        GLib.timeout_add_seconds(2, self._emit_stats)
        print("Mock daemon ready on session bus")

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
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", _build_gpu_info())))

    def _handle_GetGPUStats(self, _params, invocation):
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", _build_gpu_stats())))

    def _handle_GetCurrentDriver(self, _params, invocation):
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", _build_current_driver())))

    def _handle_ListAvailableDrivers(self, _params, invocation):
        drivers = _v("aa{sv}", _build_available_drivers())
        metadata = _v("a{sv}", _build_driver_metadata())
        invocation.return_value(GLib.Variant.new_tuple(drivers, metadata))

    def _handle_GetDegradedState(self, _params, invocation):
        state = {
            "state": _v("s", "healthy"),
            "driver_type": _v("s", "package"),
            "device_count": _v("i", 1),
            "message": _v("s", ""),
        }
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", state)))

    def _handle_GetPowerStatus(self, _params, invocation):
        invocation.return_value(GLib.Variant.new_tuple(_v("a{sv}", _build_power_status())))

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
    if not _INTROSPECTION_PATH.exists():
        print(
            f"Error: {_INTROSPECTION_PATH} not found. Run from project root.",
            file=sys.stderr,
        )
        sys.exit(1)

    loop = GLib.MainLoop()

    def on_bus_acquired(conn, _name):
        MockVerdeService(conn)

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
