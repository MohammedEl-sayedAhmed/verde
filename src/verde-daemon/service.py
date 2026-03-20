"""Verde D-Bus service — registers on the system bus as com.verde.Manager."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import pathlib
import select
import subprocess
import threading
import time
import uuid
from collections.abc import Callable

from gi.repository import Gio, GLib

from verde_daemon import __version__
from verde_daemon.apt_errors import (
    classify_apt_error,
    detect_dpkg_lock,
    remove_operation_marker,
    write_operation_marker,
)
from verde_daemon.audit import (
    OP_FIX_HIBERNATE,
    OP_FIX_SUSPEND,
    OP_GENERATE_DIAGNOSTIC,
    OP_INSTALL_DRIVER,
    OP_ROLLBACK_DRIVER,
    AuditLogger,
    detect_suspicious_patterns,
)
from verde_daemon.degraded_states import (
    DegradedState,
    build_state_info,
    detect_degraded_state,
    detect_driver_type,
)
from verde_daemon.diagnostics import DiagnosticCollector
from verde_daemon.driver_manager import DriverManager
from verde_daemon.nvml_wrapper import NvmlWrapper, Unavailable
from verde_daemon.pending_summary import PendingSummaryManager
from verde_daemon.polkit import (
    METHOD_ACTION_MAP,
    PolkitAgentMissing,
    PolkitCancelled,
    PolkitTimeout,
    check_authorization,
)
from verde_daemon.power_manager import PowerManager
from verde_daemon.preflight import PreflightChecker
from verde_daemon.snapshot_manager import SnapshotManager
from verde_daemon.validators import (
    validate_driver_version,
    validate_operation_name,
    validate_snapshot_id,
)

log = logging.getLogger("verde-daemon.service")

BUS_NAME = "com.verde.Manager"
OBJECT_PATH = "/com/verde/Manager"
INTERFACE_NAME = "com.verde.Manager"

_POLL_INTERVAL_SECONDS = 2

# Methods that require parameter validation and which validator to use.
# Maps method_name -> list of (param_index, validator_func) tuples.
_METHOD_VALIDATORS: dict[str, list[tuple[int, object]]] = {
    "InstallDriver": [(0, validate_driver_version)],
    "RollbackDriver": [(0, validate_snapshot_id)],
    "DeleteSnapshot": [(0, validate_snapshot_id)],
}

# Methods that return a{sv} GPU data — dispatched after Polkit auth.
_GPU_DATA_METHODS = frozenset(
    {"GetGPUInfo", "GetGPUStats", "GetCurrentDriver", "GetDegradedState", "ListAvailableDrivers"}
)


class VerdeService:
    """D-Bus service for com.verde.Manager.

    Parameters
    ----------
    loop : GLib.MainLoop
        Main event loop — quit is called on name-lost.
    on_idle_reset : callable
        Called on every incoming method call so the idle timer restarts.
    introspection_xml : str | None
        Override introspection XML (for testing). When *None* the XML is
        loaded from *xml_path*.
    xml_path : str | pathlib.Path | None
        Path to ``com.verde.Manager.xml``.  Ignored when
        *introspection_xml* is supplied.
    """

    def __init__(
        self,
        loop: GLib.MainLoop,
        on_idle_reset: Callable[[], None],
        *,
        on_idle_hold: Callable[[], None] | None = None,
        on_idle_release: Callable[[], None] | None = None,
        introspection_xml: str | None = None,
        xml_path: str | pathlib.Path | None = None,
        audit_logger: AuditLogger | None = None,
        nvml: NvmlWrapper | None = None,
        driver_manager: DriverManager | None = None,
        snapshot_manager: SnapshotManager | None = None,
    ) -> None:
        self._loop = loop
        self._on_idle_reset = on_idle_reset
        self._on_idle_hold = on_idle_hold or (lambda: None)
        self._on_idle_release = on_idle_release or (lambda: None)
        self._audit = audit_logger
        self._connection: Gio.DBusConnection | None = None
        self._registration_id: int = 0
        self._owner_id: int = 0
        self._poll_source_id: int | None = None

        # Concurrency guard for driver operations (FR40)
        self._operation_in_progress: bool = False
        self._current_op_id: str | None = None
        self._current_op_type: str | None = None
        self._current_op_started: float | None = None

        # Startup error from interrupted operation detection (FR48)
        self._startup_error = None

        # NVML wrapper — injected for testing, created automatically otherwise
        if nvml is not None:
            self._nvml = nvml
        else:
            self._nvml = NvmlWrapper()
        self._nvml_available: bool = False
        self._last_gpu_available: bool = False
        try:
            self._nvml_available = bool(self._nvml.initialize())
            self._last_gpu_available = self._nvml_available
        except Exception:
            log.warning("NVML initialization failed — running in degraded mode")

        # Driver manager — injected for testing, created automatically otherwise
        if driver_manager is not None:
            self._driver_manager = driver_manager
        else:
            gpu_name = ""
            if self._nvml_available:
                handle = self._nvml.get_device_by_index(0)
                if handle is not Unavailable:
                    name = self._nvml.get_device_name(handle)
                    if name is not Unavailable:
                        gpu_name = name
            self._driver_manager = DriverManager(gpu_name=gpu_name)

        # Pre-flight checker
        self._preflight = PreflightChecker()

        # Snapshot manager (Story 3.1/3.2)
        self._snapshot_manager = snapshot_manager or SnapshotManager(
            audit_logger=audit_logger,
        )

        # Pending summary manager (Story 3.5)
        self._pending_summary = PendingSummaryManager()

        # Power manager (Story 4.1)
        self._power_manager = PowerManager()

        # Diagnostic collector (Story 5.1)
        self._diagnostics = DiagnosticCollector(nvml=self._nvml)

        # Degraded state tracking — re-detected each poll cycle.
        # Check whether a driver package is installed (even if the kernel
        # module is not loaded) so degraded-state detection can distinguish
        # NO_DRIVER from DRIVER_NOT_LOADED.
        try:
            _drv_info = self._driver_manager.get_current_driver()
            self._driver_installed = bool(_drv_info.get("version"))
        except Exception:
            self._driver_installed = False
        self._current_degraded_state: DegradedState = detect_degraded_state(
            self._nvml,
            driver_installed=self._driver_installed,
        )

        # Load introspection XML
        if introspection_xml is not None:
            xml = introspection_xml
        elif xml_path is not None:
            xml = pathlib.Path(xml_path).read_text()
        else:
            raise ValueError("Either introspection_xml or xml_path must be provided")

        self._node_info = Gio.DBusNodeInfo.new_for_xml(xml)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Acquire the bus name and register the D-Bus object."""
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SYSTEM,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )

    def stop(self) -> None:
        """Release bus name, unregister object, and stop polling."""
        self.stop_polling()
        if self._registration_id:
            if self._connection is not None:
                try:
                    self._connection.unregister_object(self._registration_id)
                except Exception:
                    log.debug("Failed to unregister object (bus may be disconnected)")
            self._registration_id = 0
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        if self._nvml is not None:
            try:
                self._nvml.shutdown()
            except Exception:
                log.debug("NVML shutdown failed")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def start_polling(self) -> None:
        """Start periodic GPU stats polling and signal emission."""
        if self._poll_source_id is not None:
            return
        self._poll_source_id = GLib.timeout_add_seconds(
            _POLL_INTERVAL_SECONDS, self._poll_and_emit
        )

    def stop_polling(self) -> None:
        """Stop the periodic polling timer."""
        if self._poll_source_id is not None:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = None

    def _poll_and_emit(self) -> bool:
        """Poll NVML and emit GPUStatsUpdated signal."""
        if self._nvml is None:
            return GLib.SOURCE_CONTINUE

        stats: dict[str, GLib.Variant] = {}
        try:
            stats = self._build_gpu_stats()

            # GPU disappearance detection
            current_available = stats.get("available")
            if current_available is not None:
                is_available = current_available.get_boolean()
            else:
                is_available = True
            if self._last_gpu_available and not is_available:
                stats["gpu_lost"] = GLib.Variant("b", True)
                stats["gpu_lost_reason"] = GLib.Variant(
                    "s",
                    stats.get("reason", GLib.Variant("s", "GPU disappeared")).get_string(),
                )
            self._last_gpu_available = is_available

            if self._connection is not None:
                self._connection.emit_signal(
                    None,
                    OBJECT_PATH,
                    INTERFACE_NAME,
                    "GPUStatsUpdated",
                    GLib.Variant.new_tuple(GLib.Variant("a{sv}", stats)),
                )

            # Re-detect degraded state each cycle; emit signal on change
            new_state = detect_degraded_state(
                self._nvml,
                driver_installed=self._driver_installed,
            )
            # GPU-lost override: if GPU was available and now stats say lost
            if stats.get("gpu_lost") and stats["gpu_lost"].get_boolean():
                new_state = DegradedState.GPU_LOST
            if new_state != self._current_degraded_state:
                old = self._current_degraded_state
                self._current_degraded_state = new_state
                log.warning(
                    "Degraded state changed: %s -> %s",
                    old.value,
                    new_state.value,
                )
                self._emit_degraded_state_changed()

            self._on_idle_reset()
        except Exception:
            log.exception("Error during GPU stats polling")
        return GLib.SOURCE_CONTINUE

    def _emit_degraded_state_changed(self) -> None:
        """Emit DegradedStateChanged D-Bus signal."""
        if self._connection is None:
            return
        info = self._build_degraded_state_response()
        self._connection.emit_signal(
            None,
            OBJECT_PATH,
            INTERFACE_NAME,
            "DegradedStateChanged",
            GLib.Variant.new_tuple(GLib.Variant("a{sv}", info)),
        )

    # ------------------------------------------------------------------
    # Bus lifecycle callbacks
    # ------------------------------------------------------------------

    def _on_bus_acquired(
        self,
        connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        """Register D-Bus object once bus connection is ready."""
        self._connection = connection
        iface_info = self._node_info.interfaces[0]

        self._registration_id = connection.register_object(
            OBJECT_PATH,
            iface_info,
            self._handle_method_call,
            self._handle_get_property,
            None,  # set_property — all properties are read-only
        )
        log.info("D-Bus object registered at %s", OBJECT_PATH)
        self.start_polling()

    def _on_name_acquired(
        self,
        connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        log.info("Acquired bus name: %s", name)

    def _on_name_lost(
        self,
        connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        log.warning("Lost bus name %s — shutting down", name)
        self._loop.quit()

    # ------------------------------------------------------------------
    # D-Bus method call handler
    # ------------------------------------------------------------------

    def _handle_method_call(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        # Ping is a special case — no auth, no validation
        if method_name == "Ping":
            self._on_idle_reset()
            invocation.return_value(None)
            return

        # Story 3.5: Post-reboot summary — no auth required (read/clear only)
        if method_name == "GetPostRebootSummary":
            self._on_idle_reset()
            self._dispatch_get_post_reboot_summary(invocation)
            return
        if method_name == "ClearPostRebootSummary":
            self._on_idle_reset()
            self._dispatch_clear_post_reboot_summary(invocation)
            return

        # Read-only methods — no Polkit auth required
        if method_name in _GPU_DATA_METHODS:
            self._on_idle_reset()
            self._dispatch_gpu_method(method_name, invocation)
            return
        if method_name == "GetPowerStatus":
            self._on_idle_reset()
            self._dispatch_get_power_status(invocation)
            return
        if method_name == "ListSnapshots":
            self._on_idle_reset()
            self._dispatch_list_snapshots(invocation)
            return

        # GetPreflightCheck is read-only (AR-4) — no Polkit auth required.
        # Validate input and dispatch before the auth gate.
        if method_name == "GetPreflightCheck":
            if parameters is not None:
                try:
                    value = parameters.get_child_value(0).get_string()
                    validate_operation_name(value)
                except ValueError as exc:
                    invocation.return_dbus_error(
                        "com.verde.Error.InvalidArgument",
                        str(exc),
                    )
                    return
            self._on_idle_reset()
            self._dispatch_preflight_check(parameters, invocation)
            return

        # Look up Polkit action for this method
        action_id = METHOD_ACTION_MAP.get(method_name)
        if action_id is None:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Method {method_name} is not implemented",
            )
            return

        # Input validation BEFORE Polkit auth — reject garbage input
        # before prompting the user for an admin password.
        validators = _METHOD_VALIDATORS.get(method_name)
        if validators and parameters is not None:
            for param_idx, validator in validators:
                try:
                    value = parameters.get_child_value(param_idx).get_string()
                    validator(value)
                except ValueError as exc:
                    invocation.return_dbus_error(
                        "com.verde.Error.InvalidArgument",
                        str(exc),
                    )
                    return

        # Polkit authorization check
        try:
            authorized = check_authorization(connection, sender, action_id)
        except PolkitAgentMissing:
            log.warning("Polkit agent missing for method %s from %s", method_name, sender)
            invocation.return_dbus_error(
                "com.verde.Error.PolkitAgentMissing",
                "Authentication service is not available. "
                "Ensure a Polkit authentication agent is running "
                "(e.g., polkit-gnome-authentication-agent-1 or GNOME Shell).",
            )
            if self._audit:
                self._audit.log(
                    "POLKIT_AGENT_MISSING",
                    {"method": method_name, "action": action_id},
                    sender,
                    "failed",
                    error="Polkit authentication agent not available",
                )
            return
        except PolkitCancelled:
            log.info("Polkit authentication cancelled for method %s from %s", method_name, sender)
            invocation.return_dbus_error(
                "com.verde.Error.PolkitCancelled",
                "Authentication was cancelled by the user.",
            )
            return
        except PolkitTimeout:
            log.warning(
                "Polkit authentication timed out for method %s from %s", method_name, sender
            )
            invocation.return_dbus_error(
                "com.verde.Error.PolkitTimeout",
                "Authentication timed out. Please try again.",
            )
            return
        if not authorized:
            invocation.return_dbus_error(
                "com.verde.Error.NotAuthorized",
                "Authorization required for this operation",
            )
            return

        # Reset idle timer on successful authorization
        self._on_idle_reset()

        # GPU data method dispatch
        if method_name in _GPU_DATA_METHODS:
            self._dispatch_gpu_method(method_name, invocation)
            return

        # Driver operation dispatch
        if method_name == "InstallDriver":
            self._dispatch_install_driver(parameters, invocation, sender)
            return

        # Rollback dispatch (Story 3.3)
        if method_name == "RollbackDriver":
            self._dispatch_rollback_driver(parameters, invocation, sender)
            return

        # Snapshot management (Story 3.2)
        if method_name == "ListSnapshots":
            self._dispatch_list_snapshots(invocation)
            return
        if method_name == "DeleteSnapshot":
            self._dispatch_delete_snapshot(parameters, invocation, sender)
            return

        # Repair dpkg (FR15 — Story 2.5)
        if method_name == "RepairDpkg":
            self._dispatch_repair_dpkg(invocation, sender)
            return

        # Power fix dispatch (Story 4.2)
        if method_name == "FixSuspend":
            self._dispatch_fix_suspend(invocation, sender)
            return
        if method_name == "FixHibernate":
            self._dispatch_fix_hibernate(invocation, sender)
            return

        # Diagnostic report generation (Story 5.1)
        if method_name == "GenerateDiagnosticReport":
            self._dispatch_generate_diagnostic(parameters, invocation, sender)
            return

        # Audit log viewer (Story 5.3)
        if method_name == "GetAuditLog":
            self._dispatch_get_audit_log(parameters, invocation)
            return

        # Remaining methods — stubs until actual implementations
        invocation.return_dbus_error(
            "org.freedesktop.DBus.Error.UnknownMethod",
            f"Method {method_name} is not yet implemented",
        )

    def _dispatch_gpu_method(self, method_name: str, invocation: Gio.DBusMethodInvocation) -> None:
        """Dispatch GPU data methods that return a{sv} or aa{sv}."""
        try:
            if method_name == "GetGPUInfo":
                result = self._build_gpu_info()
            elif method_name == "GetGPUStats":
                result = self._build_gpu_stats()
            elif method_name == "GetCurrentDriver":
                result = self._build_current_driver()
            elif method_name == "GetDegradedState":
                result = self._build_degraded_state_response()
            elif method_name == "ListAvailableDrivers":
                threading.Thread(
                    target=self._dispatch_list_available_drivers_threaded,
                    args=(invocation,),
                    daemon=True,
                ).start()
                return
            else:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    f"Method {method_name} is not implemented",
                )
                return
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sv}", result)))
        except Exception as exc:
            log.exception("Internal error in %s", method_name)
            error_name = "com.verde.Manager.InternalError"
            if method_name in ("GetCurrentDriver", "ListAvailableDrivers") and isinstance(
                exc, (OSError, RuntimeError)
            ):
                error_name = "com.verde.Error.AptUnavailable"
            invocation.return_dbus_error(error_name, str(exc))

    # ------------------------------------------------------------------
    # Pre-flight dispatch
    # ------------------------------------------------------------------

    def _dispatch_preflight_check(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        """Run pre-flight checks and return structured a{sv} result."""
        try:
            operation = parameters.get_child_value(0).get_string()

            # Rollback-specific pre-flight (Story 3.3)
            if operation.startswith("rollback:"):
                self._dispatch_rollback_preflight(operation, invocation)
                return

            # Power fix pre-flight (Story 4.2)
            if operation in ("fix_suspend", "fix_hibernate"):
                self._dispatch_power_preflight(operation, invocation)
                return

            pf_result = self._preflight.run_all_checks(operation)

            check_variants: list[dict[str, GLib.Variant]] = []
            for c in pf_result.checks:
                check_variants.append(
                    {
                        "name": GLib.Variant("s", c.name),
                        "status": GLib.Variant("s", c.status),
                        "description": GLib.Variant("s", c.description),
                    }
                )

            result: dict[str, GLib.Variant] = {
                "overall_pass": GLib.Variant("b", pf_result.overall_pass),
                "duration_ms": GLib.Variant("i", pf_result.duration_ms),
                "checks": GLib.Variant("aa{sv}", check_variants),
            }

            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sv}", result)))
        except Exception as exc:
            log.exception("Internal error in GetPreflightCheck")
            invocation.return_dbus_error("com.verde.Manager.InternalError", str(exc))

    def _dispatch_rollback_preflight(
        self,
        operation: str,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        """Run rollback-specific pre-flight checks (AC#2)."""
        try:
            self._dispatch_rollback_preflight_inner(operation, invocation)
        except Exception:
            log.exception("Unhandled error in rollback preflight")
            invocation.return_dbus_error("com.verde.Manager.InternalError", "Internal error")

    def _dispatch_rollback_preflight_inner(
        self,
        operation: str,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        """Inner implementation of rollback pre-flight checks."""
        from verde_daemon.preflight import CheckResult

        snapshot_id = operation.split(":", 1)[1]
        start = time.monotonic()
        checks: list[CheckResult] = []
        current_driver = ""
        snapshot_driver = ""

        # Check 1: Snapshot exists and integrity passes
        try:
            integrity_ok = self._snapshot_manager.verify_integrity(snapshot_id)
            if integrity_ok:
                checks.append(
                    CheckResult(
                        name="snapshot_integrity",
                        status="pass",
                        description="Snapshot integrity verified (SHA-256 match)",
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        name="snapshot_integrity",
                        status="fail",
                        description="Snapshot integrity check failed — file may be corrupted",
                    )
                )
        except FileNotFoundError:
            checks.append(
                CheckResult(
                    name="snapshot_integrity",
                    status="fail",
                    description=f"Snapshot {snapshot_id!r} not found",
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    name="snapshot_integrity",
                    status="fail",
                    description=f"Cannot verify snapshot: {exc}",
                )
            )

        # Load snapshot data for remaining checks
        snapshot_data = None
        try:
            snapshot_data = self._snapshot_manager.get_snapshot(snapshot_id)
            op = snapshot_data.get("operation") or {}
            if isinstance(op, dict):
                snapshot_driver = op.get("target_driver", "")
        except Exception:
            pass

        # Get current driver version
        try:
            driver_info = self._driver_manager.get_current_driver()
            current_driver = driver_info.get("version", "")
        except Exception:
            pass

        # Check 2: Disk space
        checks.append(self._preflight._check_disk_space())

        # Check 3: Package availability (snapshot packages exist in repos)
        if snapshot_data:
            snap_pkgs = snapshot_data.get("driver_packages", [])
            missing = []
            for pkg in snap_pkgs:
                name = pkg.get("name", "")
                version = pkg.get("version", "")
                if name and version:
                    try:
                        result = subprocess.run(
                            ["apt-cache", "show", f"{name}={version}"],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if result.returncode != 0:
                            missing.append(f"{name}={version}")
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        missing.append(f"{name}={version}")

            if missing:
                checks.append(
                    CheckResult(
                        name="package_availability",
                        status="fail",
                        description=(
                            "Required packages unavailable: "
                            + ", ".join(missing[:3])
                            + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else "")
                        ),
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        name="package_availability",
                        status="pass",
                        description="All snapshot packages are available",
                    )
                )
        else:
            checks.append(
                CheckResult(
                    name="package_availability",
                    status="warn",
                    description="Cannot verify package availability — snapshot data unavailable",
                )
            )

        # Check 4: dpkg state
        checks.append(self._preflight._check_dpkg_state())

        duration_ms = int((time.monotonic() - start) * 1000)
        overall_pass = all(c.status != "fail" for c in checks)

        check_variants: list[dict[str, GLib.Variant]] = []
        for c in checks:
            check_variants.append(
                {
                    "name": GLib.Variant("s", c.name),
                    "status": GLib.Variant("s", c.status),
                    "description": GLib.Variant("s", c.description),
                }
            )

        result_dict: dict[str, GLib.Variant] = {
            "overall_pass": GLib.Variant("b", overall_pass),
            "duration_ms": GLib.Variant("i", duration_ms),
            "checks": GLib.Variant("aa{sv}", check_variants),
            "current_driver": GLib.Variant("s", current_driver),
            "snapshot_driver": GLib.Variant("s", snapshot_driver),
        }

        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sv}", result_dict)))

    # ------------------------------------------------------------------
    # Snapshot management dispatch (Story 3.2)
    # ------------------------------------------------------------------

    def _dispatch_list_snapshots(self, invocation: Gio.DBusMethodInvocation) -> None:
        """Handle ListSnapshots: return aa{sv} of snapshot metadata."""
        try:
            snapshots = self._snapshot_manager.list_snapshots()
            variant_list: list[dict[str, GLib.Variant]] = []
            for snap in snapshots:
                op = snap.get("operation") or {}
                if not isinstance(op, dict):
                    op = {}
                target_driver = op.get("target_driver", "")
                packages = snap.get("driver_packages", [])
                package_strings = [f"{p.get('name', '')}={p.get('version', '')}" for p in packages]
                dkms_modules = snap.get("dkms_modules", [])
                dkms_status = dkms_modules[0].get("status", "") if dkms_modules else ""

                variant_list.append(
                    {
                        "id": GLib.Variant("s", snap.get("snapshot_id", "")),
                        "timestamp": GLib.Variant("s", snap.get("timestamp", "")),
                        "driver_version": GLib.Variant("s", target_driver),
                        "kernel_version": GLib.Variant("s", snap.get("kernel_version", "")),
                        "packages": GLib.Variant("as", package_strings),
                        "dkms_status": GLib.Variant("s", dkms_status),
                        "file_size": GLib.Variant("x", snap.get("file_size", 0)),
                        "sha256": GLib.Variant("s", snap.get("sha256", "")),
                    }
                )
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("aa{sv}", variant_list)))
        except Exception:
            log.exception("Internal error in ListSnapshots")
            invocation.return_dbus_error("com.verde.Manager.InternalError", "Internal error")

    def _dispatch_delete_snapshot(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
        sender: str,
    ) -> None:
        """Handle DeleteSnapshot: validate ID, delete file, audit, return success."""
        snapshot_id = parameters.get_child_value(0).get_string()
        try:
            self._snapshot_manager.delete_snapshot(snapshot_id)
        except FileNotFoundError:
            invocation.return_dbus_error(
                "com.verde.Error.SnapshotNotFound",
                f"Snapshot {snapshot_id!r} not found",
            )
            return
        except Exception:
            log.exception("Internal error in DeleteSnapshot")
            invocation.return_dbus_error("com.verde.Manager.InternalError", "Internal error")
            return

        if self._audit:
            self._audit.log(
                "DELETE_SNAPSHOT",
                {"snapshot_id": snapshot_id},
                sender,
                "success",
            )
        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("b", True)))

    # ------------------------------------------------------------------
    # Story 3.5: Post-reboot summary
    # ------------------------------------------------------------------

    def _dispatch_get_post_reboot_summary(self, invocation: Gio.DBusMethodInvocation) -> None:
        """Handle GetPostRebootSummary: return summary or has_pending=false."""
        pending = self._pending_summary.read_pending()
        if pending is None:
            result = {"has_pending": GLib.Variant("b", False)}
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sv}", result)))
            return

        summary = self._pending_summary.compute_post_reboot_summary(
            pending,
            self._nvml,
        )
        gv_result: dict[str, GLib.Variant] = {
            "has_pending": GLib.Variant("b", True),
            "operation_type": GLib.Variant("s", summary.get("operation_type", "")),
            "previous_version": GLib.Variant("s", summary.get("previous_version", "")),
            "expected_version": GLib.Variant("s", summary.get("expected_version", "")),
            "current_version": GLib.Variant("s", summary.get("current_version", "")),
            "gpu_healthy": GLib.Variant("b", summary.get("gpu_healthy", False)),
            "result": GLib.Variant("s", summary.get("result", "failed")),
            "message": GLib.Variant("s", summary.get("message", "")),
            "recovery_guidance": GLib.Variant("s", summary.get("recovery_guidance", "")),
        }
        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sv}", gv_result)))

    def _dispatch_clear_post_reboot_summary(self, invocation: Gio.DBusMethodInvocation) -> None:
        """Handle ClearPostRebootSummary: delete state file."""
        self._pending_summary.clear_pending()
        if self._audit:
            self._audit.log(
                "CLEAR_POST_REBOOT_SUMMARY",
                {},
                "gui",
                "success",
            )
        invocation.return_value(None)

    # ------------------------------------------------------------------
    # Power status dispatch (Story 4.1)
    # ------------------------------------------------------------------

    def _dispatch_get_power_status(
        self,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        """Handle GetPowerStatus: return power/suspend issue detection results."""
        try:
            status = self._power_manager.get_power_status()

            # Convert issues list to aa{sv}
            gv_issues = []
            for issue in status["issues"]:
                gv_issue = {
                    "type": GLib.Variant("s", issue["type"]),
                    "severity": GLib.Variant("s", issue["severity"]),
                    "summary": GLib.Variant("s", issue["summary"]),
                    "detail": GLib.Variant("s", issue["detail"]),
                    "fixable": GLib.Variant("b", issue["fixable"]),
                    "already_fixed": GLib.Variant("b", issue["already_fixed"]),
                }
                gv_issues.append(GLib.Variant("a{sv}", gv_issue))

            issues_array = GLib.Variant.new_array(GLib.VariantType.new("a{sv}"), gv_issues)
            gv_result = {
                "overall_status": GLib.Variant("s", status["overall_status"]),
                "issues": issues_array,
                "suspend_service_active": GLib.Variant("b", status["suspend_service_active"]),
                "hibernate_service_active": GLib.Variant("b", status["hibernate_service_active"]),
                "secure_boot_enabled": GLib.Variant("b", status["secure_boot_enabled"]),
                "mok_enrolled": GLib.Variant("b", status["mok_enrolled"]),
                "wayland_session": GLib.Variant("b", status["wayland_session"]),
            }
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sv}", gv_result)))
        except Exception:
            log.exception("GetPowerStatus dispatch failed")
            invocation.return_dbus_error(
                "com.verde.Error.InternalError",
                "Internal error while retrieving power status.",
            )

    # ------------------------------------------------------------------
    # Power fix dispatch (Story 4.2)
    # ------------------------------------------------------------------

    def _dispatch_fix_suspend(
        self,
        invocation: Gio.DBusMethodInvocation,
        sender: str,
    ) -> None:
        """Handle FixSuspend: validate, guard, return op_id, spawn worker."""
        if self._operation_in_progress:
            invocation.return_dbus_error(
                "com.verde.Error.OperationInProgress",
                self._operation_in_progress_message(),
            )
            return

        self._operation_in_progress = True
        op_id = uuid.uuid4().hex[:12]
        self._current_op_id = op_id
        self._current_op_type = "FixSuspend"
        self._current_op_started = time.monotonic()

        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("s", op_id)))

        if self._audit:
            self._audit.log(
                OP_FIX_SUSPEND,
                {},
                sender,
                "started",
            )

        thread = threading.Thread(
            target=self._do_fix_suspend,
            args=(op_id, sender),
            daemon=True,
        )
        thread.start()

    def _do_fix_suspend(self, op_id: str, sender: str) -> None:
        """Worker thread for FixSuspend."""
        inhibitor_fd: int | None = None
        try:
            GLib.idle_add(self._on_idle_hold)
            inhibitor_fd = self._acquire_inhibitor_lock("Fixing NVIDIA suspend services")

            def _progress(oid: str, pct: float, msg: str) -> None:
                self._emit_signal("OperationProgress", "(sds)", (oid, pct, msg))

            def _complete(oid: str, success: bool, msg: str) -> None:
                self._emit_signal("OperationComplete", "(sbs)", (oid, success, msg))
                if self._audit:
                    self._audit.log(
                        OP_FIX_SUSPEND,
                        {},
                        sender,
                        "success" if success else "failed",
                        error=None if success else msg,
                    )

            self._power_manager.fix_suspend(op_id, _progress, _complete)

        except Exception as exc:
            log.exception("Unhandled error in fix_suspend worker for %s", op_id)
            self._emit_signal(
                "OperationComplete",
                "(sbs)",
                (op_id, False, f"Internal error: {exc}"),
            )
            if self._audit:
                self._audit.log(
                    OP_FIX_SUSPEND,
                    {},
                    sender,
                    "failed",
                    error=str(exc),
                )
        finally:
            self._release_inhibitor_lock(inhibitor_fd)

            def _release_guard() -> bool:
                self._operation_in_progress = False
                self._current_op_id = None
                self._current_op_type = None
                self._current_op_started = None
                self._on_idle_release()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_release_guard)

    def _dispatch_fix_hibernate(
        self,
        invocation: Gio.DBusMethodInvocation,
        sender: str,
    ) -> None:
        """Handle FixHibernate: validate, guard, return op_id, spawn worker."""
        if self._operation_in_progress:
            invocation.return_dbus_error(
                "com.verde.Error.OperationInProgress",
                self._operation_in_progress_message(),
            )
            return

        self._operation_in_progress = True
        op_id = uuid.uuid4().hex[:12]
        self._current_op_id = op_id
        self._current_op_type = "FixHibernate"
        self._current_op_started = time.monotonic()

        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("s", op_id)))

        if self._audit:
            self._audit.log(
                OP_FIX_HIBERNATE,
                {},
                sender,
                "started",
            )

        thread = threading.Thread(
            target=self._do_fix_hibernate,
            args=(op_id, sender),
            daemon=True,
        )
        thread.start()

    def _do_fix_hibernate(self, op_id: str, sender: str) -> None:
        """Worker thread for FixHibernate."""
        inhibitor_fd: int | None = None
        try:
            GLib.idle_add(self._on_idle_hold)
            inhibitor_fd = self._acquire_inhibitor_lock("Fixing NVIDIA hibernate configuration")

            def _progress(oid: str, pct: float, msg: str) -> None:
                self._emit_signal("OperationProgress", "(sds)", (oid, pct, msg))

            def _complete(oid: str, success: bool, msg: str) -> None:
                self._emit_signal("OperationComplete", "(sbs)", (oid, success, msg))
                if self._audit:
                    self._audit.log(
                        OP_FIX_HIBERNATE,
                        {},
                        sender,
                        "success" if success else "failed",
                        error=None if success else msg,
                    )

            def _reboot(required: bool, reason: str) -> None:
                self._emit_signal("RebootRequired", "(bs)", (required, reason))

            self._power_manager.fix_hibernate(op_id, _progress, _complete, _reboot)

        except Exception as exc:
            log.exception("Unhandled error in fix_hibernate worker for %s", op_id)
            self._emit_signal(
                "OperationComplete",
                "(sbs)",
                (op_id, False, f"Internal error: {exc}"),
            )
            if self._audit:
                self._audit.log(
                    OP_FIX_HIBERNATE,
                    {},
                    sender,
                    "failed",
                    error=str(exc),
                )
        finally:
            self._release_inhibitor_lock(inhibitor_fd)

            def _release_guard() -> bool:
                self._operation_in_progress = False
                self._current_op_id = None
                self._current_op_type = None
                self._current_op_started = None
                self._on_idle_release()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_release_guard)

    def _dispatch_power_preflight(
        self,
        operation: str,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        """Handle GetPreflightCheck for fix_suspend / fix_hibernate (Story 4.2)."""
        try:
            if operation == "fix_suspend":
                pf = self._power_manager.get_preflight_suspend()
            else:
                pf = self._power_manager.get_preflight_hibernate()

            gv_changes: list[dict[str, GLib.Variant]] = []
            for ch in pf["changes"]:
                gv_changes.append(
                    {
                        "description": GLib.Variant("s", ch["description"]),
                        "current_state": GLib.Variant("s", ch["current_state"]),
                        "target_state": GLib.Variant("s", ch["target_state"]),
                    }
                )

            result: dict[str, GLib.Variant] = {
                "ready": GLib.Variant("b", pf["ready"]),
                "already_fixed": GLib.Variant("b", pf["already_fixed"]),
                "changes": GLib.Variant("aa{sv}", gv_changes),
            }
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sv}", result)))
        except Exception:
            log.exception("Power preflight dispatch failed for %s", operation)
            invocation.return_dbus_error(
                "com.verde.Error.InternalError",
                "Internal error while running power pre-flight check.",
            )

    # ------------------------------------------------------------------
    # Diagnostic report dispatch (Story 5.1)
    # ------------------------------------------------------------------

    def _dispatch_generate_diagnostic(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
        sender: str,
    ) -> None:
        """Handle GenerateDiagnosticReport: validate format, generate report."""
        fmt = ""
        try:
            fmt = parameters.get_child_value(0).get_string() if parameters else ""
            if not fmt:
                fmt = "markdown"

            if fmt not in ("markdown", "json"):
                invocation.return_dbus_error(
                    "com.verde.Error.InvalidArgument",
                    f"Invalid format: {fmt!r}. Must be 'markdown' or 'json'.",
                )
                return

            report = self._diagnostics.generate(fmt)

            if self._audit:
                self._audit.log(
                    OP_GENERATE_DIAGNOSTIC,
                    {"format": fmt},
                    sender,
                    "success",
                )

            invocation.return_value(
                GLib.Variant.new_tuple(GLib.Variant("s", report)),
            )
        except Exception:
            log.exception("GenerateDiagnosticReport dispatch failed")
            if self._audit:
                self._audit.log(
                    OP_GENERATE_DIAGNOSTIC,
                    {"format": fmt or "unknown"},
                    sender,
                    "failed",
                    error="Internal error during report generation",
                )
            invocation.return_dbus_error(
                "com.verde.Error.InternalError",
                "Internal error while generating diagnostic report.",
            )

    # ------------------------------------------------------------------
    # Audit log dispatch (Story 5.3)
    # ------------------------------------------------------------------

    def _dispatch_get_audit_log(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        """Handle GetAuditLog: read, filter, and return audit entries."""
        try:
            filter_type = parameters.get_child_value(0).get_string() if parameters else ""
            date_from = parameters.get_child_value(1).get_string() if parameters else ""
            date_to = parameters.get_child_value(2).get_string() if parameters else ""
            result_filter = parameters.get_child_value(3).get_string() if parameters else ""

            if self._audit is None:
                invocation.return_value(
                    GLib.Variant.new_tuple(GLib.Variant("aa{sv}", [])),
                )
                return

            entries = self._audit.read_entries(
                filter_type=filter_type,
                date_from=date_from,
                date_to=date_to,
                result=result_filter,
            )

            # Detect suspicious patterns
            entries = detect_suspicious_patterns(entries)

            # Convert to aa{sv}
            gv_entries = []
            for entry in entries:
                gv_entry: dict[str, GLib.Variant] = {
                    "operation": GLib.Variant("s", entry.get("operation", "")),
                    "timestamp": GLib.Variant("s", entry.get("timestamp", "")),
                    "params": GLib.Variant("s", json.dumps(entry.get("params", {}))),
                    "caller": GLib.Variant("s", entry.get("caller", "")),
                    "result": GLib.Variant("s", entry.get("result", "")),
                    "message": GLib.Variant("s", entry.get("error", "")),
                    "flagged": GLib.Variant("b", entry.get("flagged", False)),
                    "flag_reason": GLib.Variant("s", entry.get("flag_reason", "")),
                }
                gv_entries.append(GLib.Variant("a{sv}", gv_entry))

            invocation.return_value(
                GLib.Variant.new_tuple(GLib.Variant("aa{sv}", gv_entries)),
            )
        except Exception:
            log.exception("GetAuditLog dispatch failed")
            invocation.return_dbus_error(
                "com.verde.Error.InternalError",
                "Internal error while reading audit log.",
            )

    # ------------------------------------------------------------------
    # Rollback dispatch (Story 3.3)
    # ------------------------------------------------------------------

    def _dispatch_rollback_driver(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
        sender: str,
    ) -> None:
        """Handle RollbackDriver: validate, guard, return op_id, spawn worker."""
        snapshot_id = parameters.get_child_value(0).get_string()

        # Concurrency guard (FR40)
        if self._operation_in_progress:
            invocation.return_dbus_error(
                "com.verde.Error.OperationInProgress",
                self._operation_in_progress_message(),
            )
            return

        # Check dpkg lock before starting
        lock_error = detect_dpkg_lock()
        if lock_error is not None:
            dbus_dict = lock_error.to_dbus_dict()
            invocation.return_dbus_error(
                "com.verde.Error.DpkgLocked",
                dbus_dict["error_title"] + ": " + dbus_dict["error_description"],
            )
            if self._audit:
                self._audit.log(
                    OP_ROLLBACK_DRIVER,
                    {"snapshot_id": snapshot_id},
                    sender,
                    "blocked",
                    error="dpkg lock held",
                )
            return

        # Acquire guard and generate op_id
        self._operation_in_progress = True
        op_id = uuid.uuid4().hex[:12]
        self._current_op_id = op_id
        self._current_op_type = "RollbackDriver"
        self._current_op_started = time.monotonic()

        # Return op_id immediately
        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("s", op_id)))

        # Spawn worker thread
        thread = threading.Thread(
            target=self._do_rollback,
            args=(op_id, snapshot_id, sender),
            daemon=True,
        )
        thread.start()

    def _do_rollback(self, op_id: str, snapshot_id: str, sender: str) -> None:
        """Worker thread: hold timer, inhibit, restore snapshot, signals, audit."""
        inhibitor_fd: int | None = None
        try:
            GLib.idle_add(self._on_idle_hold)

            # Audit: operation started
            if self._audit:
                self._audit.log(
                    OP_ROLLBACK_DRIVER,
                    {"snapshot_id": snapshot_id},
                    sender,
                    "started",
                )

            # Acquire systemd inhibitor lock
            inhibitor_fd = self._acquire_inhibitor_lock(f"Rolling back to snapshot {snapshot_id}")

            # Write operation marker for interrupted operation detection
            write_operation_marker("rollback_driver", snapshot_id)

            # Progress callback emits OperationProgress signals
            def _on_progress(pct: float, msg: str) -> None:
                self._emit_signal("OperationProgress", "(sds)", (op_id, pct, msg))

            # Capture current driver version before restore (Story 3.5)
            _prev_driver = str(self._nvml.get_driver_version()) if self._nvml_available else ""

            # Read snapshot data before restore to extract expected version
            snap_data = self._snapshot_manager.get_snapshot(snapshot_id)
            expected_ver = ""
            if snap_data:
                op_info = snap_data.get("operation", {})
                if isinstance(op_info, dict):
                    # previous_version is the driver that was running when the
                    # snapshot was taken — that's what we expect after rollback.
                    expected_ver = op_info.get("previous_version", "")

            # Execute restore
            success, message = self._snapshot_manager.restore(
                snapshot_id, progress_callback=_on_progress
            )

            if success:
                # Write pending summary for post-reboot display (Story 3.5, AC#1)
                try:
                    self._pending_summary.write_pending(
                        "rollback",
                        _prev_driver,
                        expected_ver,
                        op_id,
                    )
                except Exception as exc:
                    log.warning("Failed to write pending summary: %s", exc)

                self._emit_signal("OperationComplete", "(sbs)", (op_id, True, message))
                self._emit_signal(
                    "RebootRequired",
                    "(bs)",
                    (True, "Restart required to complete driver rollback"),
                )

                if self._audit:
                    self._audit.log(
                        OP_ROLLBACK_DRIVER,
                        {"snapshot_id": snapshot_id},
                        sender,
                        "success",
                    )
            else:
                error_dict = {
                    "success": False,
                    "error_category": "rollback_failed",
                    "error_title": "Rollback Failed",
                    "error_description": message,
                    "error_primary_action": "Try a different snapshot or generate a diagnostic report",
                    "error_secondary_action": "",
                    "recoverable": True,
                }
                self._emit_signal(
                    "OperationComplete",
                    "(sbs)",
                    (op_id, False, json.dumps(error_dict)),
                )
                if self._audit:
                    self._audit.log(
                        OP_ROLLBACK_DRIVER,
                        {"snapshot_id": snapshot_id},
                        sender,
                        "failed",
                        error=message,
                    )

        except Exception as exc:
            log.exception("Unhandled error in rollback worker for %s", op_id)
            error_dict = {
                "success": False,
                "error_category": "rollback_failed",
                "error_title": "Rollback Failed",
                "error_description": "An unexpected error occurred during rollback",
                "error_primary_action": "Try a different snapshot or generate a diagnostic report",
                "error_secondary_action": "",
                "recoverable": True,
            }
            self._emit_signal(
                "OperationComplete",
                "(sbs)",
                (op_id, False, json.dumps(error_dict)),
            )
            if self._audit:
                self._audit.log(
                    OP_ROLLBACK_DRIVER,
                    {"snapshot_id": snapshot_id},
                    sender,
                    "failed",
                    error=str(exc),
                )
        finally:
            remove_operation_marker()
            self._release_inhibitor_lock(inhibitor_fd)

            def _release_guard() -> bool:
                self._operation_in_progress = False
                self._current_op_id = None
                self._current_op_type = None
                self._current_op_started = None
                self._on_idle_release()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_release_guard)

    # ------------------------------------------------------------------
    # Driver operation dispatch (InstallDriver)
    # ------------------------------------------------------------------

    _APT_TIMEOUT_SECONDS = 600

    def _dispatch_install_driver(
        self,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
        sender: str,
    ) -> None:
        """Handle InstallDriver: validate, guard, return op_id, spawn worker."""
        version = parameters.get_child_value(0).get_string()

        # Concurrency guard (FR40)
        if self._operation_in_progress:
            invocation.return_dbus_error(
                "com.verde.Error.OperationInProgress",
                self._operation_in_progress_message(),
            )
            return

        # Check dpkg lock before starting (FR44)
        lock_error = detect_dpkg_lock()
        if lock_error is not None:
            dbus_dict = lock_error.to_dbus_dict()
            invocation.return_dbus_error(
                "com.verde.Error.DpkgLocked",
                dbus_dict["error_title"] + ": " + dbus_dict["error_description"],
            )
            if self._audit:
                self._audit.log(
                    OP_INSTALL_DRIVER,
                    {"driver": f"nvidia-driver-{version}"},
                    sender,
                    "blocked",
                    error="dpkg lock held",
                )
            return

        # Acquire guard and generate op_id
        self._operation_in_progress = True
        op_id = uuid.uuid4().hex[:12]
        self._current_op_id = op_id
        self._current_op_type = "InstallDriver"
        self._current_op_started = time.monotonic()

        # Return op_id immediately (NFR-PERF-13: <500ms)
        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("s", op_id)))

        # Spawn worker thread for async operation
        thread = threading.Thread(
            target=self._do_install,
            args=(op_id, version, sender),
            daemon=True,
        )
        thread.start()

    def _do_install(self, op_id: str, version: str, sender: str) -> None:
        """Worker thread: hold timer, inhibit, snapshot, apt, signals, audit."""
        inhibitor_fd: int | None = None
        try:
            # Hold idle timer (IG-1)
            GLib.idle_add(self._on_idle_hold)

            # Audit: operation started
            if self._audit:
                self._audit.log(
                    OP_INSTALL_DRIVER,
                    {"driver": f"nvidia-driver-{version}"},
                    sender,
                    "started",
                )

            # Acquire systemd inhibitor lock (FR89)
            inhibitor_fd = self._acquire_inhibitor_lock(f"Installing nvidia-driver-{version}")

            # Write operation marker (FR48 — interrupted operation detection)
            write_operation_marker("install_driver", version)

            # Snapshot stub (Story 3.1 will provide real implementation)
            self._create_pre_install_snapshot(version)

            # Capture current driver version before install (Story 3.5)
            _prev_driver = str(self._nvml.get_driver_version()) if self._nvml_available else ""

            # Run APT install with progress
            success, message, returncode, timed_out = self._run_apt_install(op_id, version)

            if success:
                # Remove operation marker on success
                remove_operation_marker()

                # Write pending summary for post-reboot display (Story 3.5, AC#1)
                try:
                    self._pending_summary.write_pending(
                        "install",
                        _prev_driver,
                        version,
                        op_id,
                    )
                except Exception as exc:
                    log.warning("Failed to write pending summary: %s", exc)

                # Emit completion signals
                self._emit_signal("OperationComplete", "(sbs)", (op_id, True, message))

                # Detect reboot requirement with specific reason (FR43)
                reboot_needed, reboot_reason = self._detect_reboot_required()
                if not reboot_needed:
                    # Driver install always requires at least a module reload
                    reboot_needed = True
                    reboot_reason = "Reboot required to load new driver"
                self._emit_signal(
                    "RebootRequired",
                    "(bs)",
                    (reboot_needed, reboot_reason),
                )
            else:
                # Classify the error for actionable response (FR46)
                error_response = classify_apt_error(
                    returncode=returncode,
                    stdout="",
                    stderr=message,
                    timed_out=timed_out,
                )
                dbus_dict = error_response.to_dbus_dict()

                # Emit structured error via OperationComplete (P-4: JSON-encoded dict)
                self._emit_signal(
                    "OperationComplete",
                    "(sbs)",
                    (op_id, False, json.dumps(dbus_dict)),
                )

                # Log raw output to audit (NFR-SEC-4) but NOT to D-Bus
                if self._audit:
                    self._audit.log(
                        OP_INSTALL_DRIVER,
                        {
                            "driver": f"nvidia-driver-{version}",
                            "error_category": error_response.category.value,
                        },
                        sender,
                        "failed",
                        error=error_response.raw_output,
                    )
                return  # Skip the success audit below

            # Audit: operation success
            if self._audit:
                self._audit.log(
                    OP_INSTALL_DRIVER,
                    {"driver": f"nvidia-driver-{version}"},
                    sender,
                    "success",
                )

        except Exception as exc:
            log.exception("Unhandled error in install worker for %s", op_id)
            # P-3: Never expose raw Python exceptions to D-Bus
            error_response = classify_apt_error(
                returncode=-1,
                stdout="",
                stderr=str(exc),
            )
            self._emit_signal(
                "OperationComplete",
                "(sbs)",
                (op_id, False, json.dumps(error_response.to_dbus_dict())),
            )
            if self._audit:
                self._audit.log(
                    OP_INSTALL_DRIVER,
                    {"driver": f"nvidia-driver-{version}"},
                    sender,
                    "failed",
                    error=str(exc),
                )
        finally:
            # P-2: Always clean up operation marker
            remove_operation_marker()

            # Release inhibitor lock
            self._release_inhibitor_lock(inhibitor_fd)

            # Release concurrency guard and idle timer on the main thread
            # so all state mutations are serialized with D-Bus dispatch.
            def _release_guard() -> bool:
                self._operation_in_progress = False
                self._current_op_id = None
                self._current_op_type = None
                self._current_op_started = None
                self._on_idle_release()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_release_guard)

    # ------------------------------------------------------------------
    # APT subprocess with Status-Fd progress (FR13, NFR-SEC-3)
    # ------------------------------------------------------------------

    def _run_apt_install(self, op_id: str, version: str) -> tuple[bool, str, int, bool]:
        """Run apt-get install with progress parsing. Returns (success, message)."""
        read_fd, write_fd = os.pipe()
        cmd = [
            "apt-get",
            "install",
            "-y",
            "-o",
            f"APT::Status-Fd={write_fd}",
            f"nvidia-driver-{version}",
        ]

        # P-2: ensure fds are closed on any Popen failure, not just FileNotFoundError
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                pass_fds=(write_fd,),
            )
        except OSError:
            os.close(read_fd)
            os.close(write_fd)
            return (False, "apt-get not found or not executable", -1, False)

        os.close(write_fd)  # Close write end in parent

        # P-3: Parse progress from Status-Fd pipe using select() with a
        # deadline so we never block longer than _APT_TIMEOUT_SECONDS total.
        deadline = time.monotonic() + self._APT_TIMEOUT_SECONDS
        buf = ""
        timed_out = False
        try:
            with os.fdopen(read_fd, "r") as status_stream:
                fd_no = status_stream.fileno()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    ready, _, _ = select.select([fd_no], [], [], remaining)
                    if not ready:
                        timed_out = True
                        break
                    chunk = os.read(fd_no, 4096)
                    if not chunk:
                        break  # EOF — child closed write end
                    buf += chunk.decode(errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if line.startswith("pmstatus:"):
                            parts = line.split(":", 3)
                            if len(parts) >= 4:
                                try:
                                    percent = float(parts[2])
                                    message = parts[3]
                                    self._emit_signal(
                                        "OperationProgress",
                                        "(sds)",
                                        (op_id, percent, message),
                                    )
                                except ValueError:
                                    continue
        except OSError:
            log.warning("Error reading APT status pipe")

        # Handle timeout: kill the process
        if timed_out:
            log.warning(
                "APT install timed out after %ds, terminating",
                self._APT_TIMEOUT_SECONDS,
            )
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return (
                False,
                f"Installation timed out after {self._APT_TIMEOUT_SECONDS} seconds",
                -1,
                True,
            )

        # Wait for process to finish (should already be done since pipe hit EOF)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return (False, "Process did not exit after pipe closed", -1, True)

        if proc.returncode == 0:
            return (True, f"Driver nvidia-driver-{version} installed successfully", 0, False)

        # Read stderr after process completion (safe — process is dead)
        stderr = ""
        if proc.stderr:
            try:
                stderr = proc.stderr.read().decode(errors="replace").strip()
            except Exception:
                stderr = "Unknown error"
        return (
            False,
            stderr or f"apt-get exited with code {proc.returncode}",
            proc.returncode,
            False,
        )

    # ------------------------------------------------------------------
    # D-Bus signal emission (thread-safe via GLib.idle_add)
    # ------------------------------------------------------------------

    def _emit_signal(self, signal_name: str, variant_type: str, args: tuple) -> None:
        """Schedule D-Bus signal emission on the GLib main loop."""

        def _emit() -> bool:
            if self._connection is not None:
                self._connection.emit_signal(
                    None,
                    OBJECT_PATH,
                    INTERFACE_NAME,
                    signal_name,
                    GLib.Variant(variant_type, args),
                )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_emit)

    # ------------------------------------------------------------------
    # Systemd inhibitor lock (FR89)
    # ------------------------------------------------------------------

    def _acquire_inhibitor_lock(self, reason: str) -> int | None:
        """Acquire systemd inhibitor lock via logind D-Bus API."""
        if self._connection is None:
            return None
        try:
            result = self._connection.call_with_unix_fd_list_sync(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "Inhibit",
                GLib.Variant(
                    "(ssss)",
                    ("shutdown:sleep:idle", "verde-daemon", reason, "block"),
                ),
                GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                None,
            )
            if result and result[1]:
                idx = result[0].get_child_value(0).get_handle()
                return result[1].get(idx)
            return None
        except Exception as exc:
            log.warning("Failed to acquire inhibitor lock: %s", exc)
            return None

    @staticmethod
    def _release_inhibitor_lock(fd: int | None) -> None:
        """Release systemd inhibitor lock by closing the file descriptor."""
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)

    # ------------------------------------------------------------------
    # Concurrency guard enrichment (FR40)
    # ------------------------------------------------------------------

    def _operation_in_progress_message(self) -> str:
        """Build enriched error message for OperationInProgress errors."""
        parts = ["Another operation is already in progress"]
        if self._current_op_type:
            parts.append(f"(type: {self._current_op_type})")
        if self._current_op_started is not None:
            elapsed = int(time.monotonic() - self._current_op_started)
            parts.append(f"started {elapsed} seconds ago")
        return " — ".join(parts) if len(parts) > 1 else parts[0]

    # ------------------------------------------------------------------
    # Startup error (FR48 — interrupted operation)
    # ------------------------------------------------------------------

    def set_startup_error(self, error) -> None:
        """Store an error detected during daemon startup and emit D-Bus signal.

        Emits the error as a D-Bus signal so the GUI can receive the
        interrupted-operation notice after connecting to the bus.
        """
        self._startup_error = error
        if error is not None:
            dbus_dict = error.to_dbus_dict()
            self._emit_signal(
                "OperationComplete",
                "(sbs)",
                ("startup", False, json.dumps(dbus_dict)),
            )

    # ------------------------------------------------------------------
    # Repair dpkg (FR15 — Story 2.5)
    # ------------------------------------------------------------------

    def _dispatch_repair_dpkg(
        self,
        invocation: Gio.DBusMethodInvocation,
        sender: str,
    ) -> None:
        """Handle RepairDpkg: run dpkg --configure -a to repair broken packages."""
        if self._operation_in_progress:
            invocation.return_dbus_error(
                "com.verde.Error.OperationInProgress",
                self._operation_in_progress_message(),
            )
            return

        # P-7: Check dpkg lock before starting
        lock_error = detect_dpkg_lock()
        if lock_error is not None:
            dbus_dict = lock_error.to_dbus_dict()
            invocation.return_dbus_error(
                "com.verde.Error.DpkgLocked",
                dbus_dict["error_title"] + ": " + dbus_dict["error_description"],
            )
            if self._audit:
                self._audit.log("REPAIR_DPKG", {}, sender, "blocked", error="dpkg lock held")
            return

        self._operation_in_progress = True
        op_id = uuid.uuid4().hex[:12]
        self._current_op_id = op_id
        self._current_op_type = "RepairDpkg"
        self._current_op_started = time.monotonic()

        invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("s", op_id)))

        def _do_repair() -> None:
            try:
                GLib.idle_add(self._on_idle_hold)

                if self._audit:
                    self._audit.log(
                        "REPAIR_DPKG",
                        {},
                        sender,
                        "started",
                    )

                result = subprocess.run(
                    ["dpkg", "--configure", "-a"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if result.returncode == 0:
                    remove_operation_marker()
                    self._startup_error = None
                    self._emit_signal(
                        "OperationComplete", "(sbs)", (op_id, True, "Package system repaired")
                    )
                    if self._audit:
                        self._audit.log("REPAIR_DPKG", {}, sender, "success")
                else:
                    error_resp = classify_apt_error(
                        result.returncode, result.stdout, result.stderr
                    )
                    self._emit_signal(
                        "OperationComplete",
                        "(sbs)",
                        (op_id, False, json.dumps(error_resp.to_dbus_dict())),
                    )
                    if self._audit:
                        self._audit.log(
                            "REPAIR_DPKG",
                            {"error_category": error_resp.category.value},
                            sender,
                            "failed",
                            error=error_resp.raw_output,
                        )

            except subprocess.TimeoutExpired:
                # P-3: Use classified error, not raw string
                error_resp = classify_apt_error(
                    returncode=-1,
                    stdout="",
                    stderr="",
                    timed_out=True,
                )
                self._emit_signal(
                    "OperationComplete",
                    "(sbs)",
                    (op_id, False, json.dumps(error_resp.to_dbus_dict())),
                )
                # P-10: Audit log for timeout
                if self._audit:
                    self._audit.log(
                        "REPAIR_DPKG",
                        {"error_category": "subprocess_timeout"},
                        sender,
                        "failed",
                        error="Repair operation timed out after 120s",
                    )
            except Exception as exc:
                log.exception("Repair dpkg failed: %s", exc)
                # P-3: Never expose raw Python exceptions to D-Bus
                error_resp = classify_apt_error(
                    returncode=-1,
                    stdout="",
                    stderr=str(exc),
                )
                self._emit_signal(
                    "OperationComplete",
                    "(sbs)",
                    (op_id, False, json.dumps(error_resp.to_dbus_dict())),
                )
            finally:

                def _release() -> bool:
                    self._operation_in_progress = False
                    self._current_op_id = None
                    self._current_op_type = None
                    self._current_op_started = None
                    self._on_idle_release()
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(_release)

        thread = threading.Thread(target=_do_repair, daemon=True)
        thread.start()

    # ------------------------------------------------------------------
    # Snapshot stub (real implementation in Story 3.1)
    # ------------------------------------------------------------------

    def _create_pre_install_snapshot(self, version: str) -> None:
        """Create pre-install snapshot before driver operations."""
        try:
            prev_ver = str(self._nvml.get_driver_version()) if self._nvml_available else ""
            sid = self._snapshot_manager.create_snapshot(
                "driver_install",
                version,
                "system",
                previous_version=prev_ver,
            )
            log.info("Pre-install snapshot created: %s", sid)
        except Exception as exc:
            log.warning("Failed to create pre-install snapshot: %s", exc)

    # ------------------------------------------------------------------
    # GPU data builders
    # ------------------------------------------------------------------

    def _build_partial_gpu_info(self) -> dict[str, GLib.Variant]:
        """Build partial GPU info from sysfs + dpkg when NVML is unavailable.

        Returns ``available=False`` but includes whatever static data can
        be gathered without NVML (GPU name from PCI IDs, driver version
        from dpkg, PCI bus address from sysfs).
        """
        from verde_daemon.sysfs_gpu import detect_nvidia_gpus_sysfs

        driver_info = self._driver_manager.get_current_driver()
        result: dict[str, GLib.Variant] = {
            "available": GLib.Variant("b", False),
            "reason": GLib.Variant(
                "s",
                "Driver installed but kernel module not loaded",
            ),
        }

        if driver_info.get("version"):
            result["driver_version"] = GLib.Variant("s", driver_info["version"])
        result["driver_type"] = GLib.Variant(
            "s",
            driver_info.get("driver_type", "none"),
        )
        result["loaded"] = GLib.Variant("b", False)

        gpus = detect_nvidia_gpus_sysfs()
        if gpus:
            result["name"] = GLib.Variant("s", gpus[0].get("name", "NVIDIA GPU"))
            result["device_count"] = GLib.Variant("i", len(gpus))
            result["pci_bus_id"] = GLib.Variant(
                "s",
                gpus[0].get("pci_bus_id", ""),
            )
        return result

    def _build_gpu_info(self) -> dict[str, GLib.Variant]:
        """Build GetGPUInfo response from NVML data."""
        if not self._nvml_available:
            # If a driver package is installed, return partial info from
            # sysfs + dpkg rather than a bare "unavailable" response.
            if self._driver_installed:
                return self._build_partial_gpu_info()
            return self._unavailable_response("NVIDIA driver not loaded")

        info = self._nvml.get_all_gpu_info(0)
        if info.get("handle") is Unavailable:
            return self._unavailable_response("No GPU device found")

        result: dict[str, GLib.Variant] = {
            "available": GLib.Variant("b", True),
        }

        dc = self._nvml.device_count()
        if dc is not None and dc is not Unavailable:
            result["device_count"] = GLib.Variant("i", int(dc))
        else:
            result["device_count_available"] = GLib.Variant("b", False)

        _set_str(result, "name", info.get("name"))
        _set_str(result, "uuid", info.get("uuid"))
        _set_str(result, "driver_version", info.get("driver_version"))
        _set_str(result, "cuda_driver_version", info.get("cuda_driver_version"))
        _set_int(result, "num_cores", info.get("num_cores"))
        _set_bool(result, "ecc_mode", info.get("ecc_mode"))

        # PCI info (nested dict)
        pci = info.get("pci_info")
        if pci is not None and pci is not Unavailable and isinstance(pci, dict):
            _set_str(result, "pci_bus_id", pci.get("bus_id"))
            _set_uint(result, "pci_domain", pci.get("domain"))
            _set_uint(result, "pci_bus", pci.get("bus"))
            _set_uint(result, "pci_device", pci.get("device"))
        else:
            result["pci_info_available"] = GLib.Variant("b", False)

        # Compute capability (tuple)
        cc = info.get("compute_capability")
        if cc is not None and cc is not Unavailable and isinstance(cc, tuple) and len(cc) >= 2:
            result["compute_capability_major"] = GLib.Variant("i", cc[0])
            result["compute_capability_minor"] = GLib.Variant("i", cc[1])
        else:
            result["compute_capability_available"] = GLib.Variant("b", False)

        # Driver type
        result["driver_type"] = GLib.Variant("s", self._detect_driver_type())

        # GPU mode (Optimus/hybrid)
        gpu_mode = info.get("gpu_mode")
        if gpu_mode is not None and gpu_mode is not Unavailable:
            result["gpu_mode"] = GLib.Variant("s", str(gpu_mode))
        else:
            result["gpu_mode_available"] = GLib.Variant("b", False)

        # CUDA toolkit version
        cuda_tk = info.get("cuda_toolkit_version")
        if cuda_tk is not None and cuda_tk is not Unavailable:
            result["cuda_toolkit_version"] = GLib.Variant("s", str(cuda_tk))
        else:
            result["cuda_toolkit_version_available"] = GLib.Variant("b", False)

        # Performance mode (FR72)
        perf_mode = info.get("performance_mode")
        if perf_mode is not None:
            result["performance_mode"] = GLib.Variant("s", perf_mode)
        else:
            result["performance_mode"] = GLib.Variant("s", "Not Supported")

        # Multi-GPU device enumeration
        if dc is not None and dc is not Unavailable and int(dc) > 0:
            devices = []
            for idx in range(int(dc)):
                dev_handle = self._nvml.get_device_by_index(idx)
                if dev_handle is Unavailable:
                    continue
                dev: dict[str, GLib.Variant] = {
                    "index": GLib.Variant("i", idx),
                }
                dev_name = self._nvml.get_device_name(dev_handle)
                if dev_name is not Unavailable:
                    dev["name"] = GLib.Variant("s", dev_name)
                dev_uuid = self._nvml.get_device_uuid(dev_handle)
                if dev_uuid is not Unavailable:
                    dev["uuid"] = GLib.Variant("s", dev_uuid)
                dev_pci = self._nvml.get_pci_info(dev_handle)
                if dev_pci is not Unavailable and isinstance(dev_pci, dict):
                    bus_id = dev_pci.get("bus_id")
                    if bus_id is not None:
                        dev["bus_id"] = GLib.Variant("s", str(bus_id))
                devices.append(dev)
            result["devices"] = GLib.Variant("aa{sv}", devices)

        return result

    def _build_gpu_stats(self) -> dict[str, GLib.Variant]:
        """Build GetGPUStats response from NVML data."""
        if not self._nvml_available:
            return self._unavailable_response("NVIDIA driver not loaded")

        stats = self._nvml.get_all_gpu_stats(0)
        if stats.get("handle") is Unavailable:
            return self._unavailable_response("No GPU device found")

        result: dict[str, GLib.Variant] = {"available": GLib.Variant("b", True)}

        _set_int(result, "temperature", stats.get("temperature"))
        _set_int(result, "clock_graphics", stats.get("clock_graphics"))
        _set_int(result, "clock_sm", stats.get("clock_sm"))
        _set_int(result, "clock_mem", stats.get("clock_mem"))
        _set_int(result, "performance_state", stats.get("performance_state"))
        _set_int64(result, "power_usage", stats.get("power_usage"))
        _set_int64(result, "power_limit", stats.get("power_limit"))
        _set_int64(result, "memory_errors", stats.get("memory_errors"))

        # Performance mode (human-readable)
        perf_mode = stats.get("performance_mode")
        if perf_mode is not None:
            result["performance_mode"] = GLib.Variant("s", perf_mode)
        else:
            result["performance_mode"] = GLib.Variant("s", "Not Supported")

        # Throttle reasons bitmask (uint64)
        tr = stats.get("throttle_reasons")
        if tr is not None and tr is not Unavailable:
            result["throttle_reasons"] = GLib.Variant("t", tr)
        else:
            result["throttle_reasons_available"] = GLib.Variant("b", False)

        # Decoded throttle reasons (human-readable strings)
        trd = stats.get("throttle_reasons_decoded")
        if trd is not None and trd is not Unavailable and isinstance(trd, list):
            result["throttle_reasons_decoded"] = GLib.Variant("as", trd)
        else:
            result["throttle_reasons_decoded_available"] = GLib.Variant("b", False)

        # Memory info (flattened from nested dict)
        mem = stats.get("memory")
        if mem is not None and mem is not Unavailable and isinstance(mem, dict):
            _set_int64(result, "memory_total", mem.get("total"))
            _set_int64(result, "memory_used", mem.get("used"))
            _set_int64(result, "memory_free", mem.get("free"))
        else:
            result["memory_available"] = GLib.Variant("b", False)

        # Utilization (flattened from nested dict)
        util = stats.get("utilization")
        if util is not None and util is not Unavailable and isinstance(util, dict):
            _set_int(result, "utilization_gpu", util.get("gpu"))
            _set_int(result, "utilization_memory", util.get("memory"))
        else:
            result["utilization_available"] = GLib.Variant("b", False)

        # Process list
        procs = stats.get("processes")
        if procs is not None and procs is not Unavailable:
            proc_variants = []
            for p in procs:
                pv: dict[str, GLib.Variant] = {}
                if "pid" in p:
                    pv["pid"] = GLib.Variant("u", p["pid"])
                if "used_gpu_memory" in p:
                    pv["used_gpu_memory"] = GLib.Variant("x", p["used_gpu_memory"])
                if "type" in p:
                    pv["type"] = GLib.Variant("s", p["type"])
                sm = p.get("sm_util")
                if sm is not None and sm is not Unavailable:
                    pv["sm_util"] = GLib.Variant("u", int(sm))
                else:
                    pv["sm_util_available"] = GLib.Variant("b", False)
                proc_variants.append(pv)
            result["processes"] = GLib.Variant("aa{sv}", proc_variants)
            result["process_count"] = GLib.Variant("i", len(proc_variants))
        else:
            result["processes"] = GLib.Variant("aa{sv}", [])
            result["process_count"] = GLib.Variant("i", 0)

        return result

    def _build_current_driver(self) -> dict[str, GLib.Variant]:
        """Build GetCurrentDriver response using DriverManager."""
        reboot_required, reboot_reason = self._detect_reboot_required()

        try:
            driver_info = self._driver_manager.get_current_driver()
        except Exception:
            log.exception("DriverManager.get_current_driver failed")
            raise  # Will be caught by _dispatch_gpu_method's error handler

        result: dict[str, GLib.Variant] = {
            "available": GLib.Variant("b", bool(driver_info.get("loaded"))),
            "driver_type": GLib.Variant("s", driver_info.get("driver_type", "none")),
            "reboot_required": GLib.Variant("b", reboot_required),
            "reboot_reason": GLib.Variant("s", reboot_reason),
        }
        if driver_info.get("version"):
            result["version"] = GLib.Variant("s", driver_info["version"])
        if driver_info.get("package_name"):
            result["package_name"] = GLib.Variant("s", driver_info["package_name"])
        if driver_info.get("variant"):
            result["variant"] = GLib.Variant("s", driver_info["variant"])
        if driver_info.get("module_type"):
            result["module_type"] = GLib.Variant("s", driver_info["module_type"])
        result["loaded"] = GLib.Variant("b", driver_info.get("loaded", False))

        # Keep NVML driver_version for backward compatibility
        if self._nvml_available:
            version = self._nvml.get_driver_version()
            if version is not Unavailable:
                result["driver_version"] = GLib.Variant("s", version)

        return result

    def _dispatch_list_available_drivers_threaded(
        self, invocation: Gio.DBusMethodInvocation
    ) -> None:
        """Thread wrapper for ListAvailableDrivers — replies via GLib.idle_add."""
        try:
            data = self._driver_manager.list_available_drivers()
        except Exception as exc:
            log.exception("DriverManager.list_available_drivers failed")
            GLib.idle_add(
                invocation.return_dbus_error,
                "com.verde.Error.AptUnavailable",
                str(exc),
            )
            return
        self._finish_list_available_drivers(data, invocation, threaded=True)

    def _dispatch_list_available_drivers(self, invocation: Gio.DBusMethodInvocation) -> None:
        """Handle ListAvailableDrivers — returns aa{sv}."""
        try:
            data = self._driver_manager.list_available_drivers()
        except Exception as exc:
            log.exception("DriverManager.list_available_drivers failed")
            invocation.return_dbus_error(
                "com.verde.Error.AptUnavailable",
                str(exc),
            )
            return
        self._finish_list_available_drivers(data, invocation, threaded=False)

    def _finish_list_available_drivers(
        self,
        data: dict,
        invocation: Gio.DBusMethodInvocation,
        *,
        threaded: bool = False,
    ) -> None:
        """Build and return the ListAvailableDrivers response."""
        driver_variants: list[dict[str, GLib.Variant]] = []
        for d in data.get("drivers", []):
            dv: dict[str, GLib.Variant] = {
                "version": GLib.Variant("s", d.get("version", "")),
                "variant": GLib.Variant("s", d.get("variant", "")),
                "package_name": GLib.Variant("s", d.get("package_name", "")),
                "installed": GLib.Variant("b", d.get("installed", False)),
                "recommended": GLib.Variant("b", d.get("recommended", False)),
                "held": GLib.Variant("b", d.get("held", False)),
                "module_type": GLib.Variant("s", d.get("module_type", "")),
                "module_status": GLib.Variant("s", d.get("module_status", "")),
                "repository": GLib.Variant("s", d.get("repository", "")),
            }
            if d.get("hold_message"):
                dv["hold_message"] = GLib.Variant("s", d["hold_message"])
            if d.get("recommendation_reason"):
                dv["recommendation_reason"] = GLib.Variant("s", d["recommendation_reason"])
            if d.get("cuda_compatibility"):
                dv["cuda_compatibility"] = GLib.Variant("s", d["cuda_compatibility"])
            if "known_issues" in d:
                dv["known_issues"] = GLib.Variant("s", d["known_issues"])
            driver_variants.append(dv)

        # Build top-level metadata dict
        meta: dict[str, GLib.Variant] = {}
        missing = data.get("missing_repositories", [])
        if missing:
            meta["missing_repositories"] = GLib.Variant("as", missing)
        meta["run_file_detected"] = GLib.Variant("b", data.get("run_file_detected", False))
        if data.get("run_file_message"):
            meta["run_file_message"] = GLib.Variant("s", data["run_file_message"])

        # Return (aa{sv}a{sv}) -- drivers array + metadata dict
        reply = GLib.Variant.new_tuple(
            GLib.Variant("aa{sv}", driver_variants),
            GLib.Variant("a{sv}", meta),
        )
        if threaded:
            GLib.idle_add(invocation.return_value, reply)
        else:
            invocation.return_value(reply)

    def _build_degraded_state_response(self) -> dict[str, GLib.Variant]:
        """Build GetDegradedState response from current state."""
        dc = self._nvml.device_count() if self._nvml_available else 0
        if dc is Unavailable:
            dc = 0
        driver = detect_driver_type()
        info = build_state_info(self._current_degraded_state, driver, int(dc))
        result: dict[str, GLib.Variant] = {
            "state": GLib.Variant("s", info["state"]),
            "driver_type": GLib.Variant("s", info["driver_type"]),
            "device_count": GLib.Variant("i", info["device_count"]),
            "message": GLib.Variant("s", info["message"]),
        }
        # Include driver package info when driver is installed but not loaded
        if self._current_degraded_state == DegradedState.DRIVER_NOT_LOADED:
            try:
                drv = self._driver_manager.get_current_driver()
                if drv.get("version"):
                    result["driver_version"] = GLib.Variant("s", drv["version"])
                if drv.get("package_name"):
                    result["package_name"] = GLib.Variant(
                        "s",
                        drv["package_name"],
                    )
            except Exception:
                log.debug("Could not enrich degraded state with driver info")
        return result

    @staticmethod
    def _unavailable_response(reason: str) -> dict[str, GLib.Variant]:
        """Build a graceful degradation response."""
        return {
            "available": GLib.Variant("b", False),
            "reason": GLib.Variant("s", reason),
        }

    # ------------------------------------------------------------------
    # Driver and reboot detection
    # ------------------------------------------------------------------

    def _detect_driver_type(self) -> str:
        """Detect current GPU driver: 'proprietary', 'nouveau', or 'none'."""
        if self._nvml_available:
            version = self._nvml.get_driver_version()
            if version is not Unavailable:
                return "proprietary"

        nouveau_initstate = pathlib.Path("/sys/module/nouveau/initstate")
        try:
            if nouveau_initstate.exists() and nouveau_initstate.read_text().strip() == "live":
                return "nouveau"
        except OSError:
            pass

        return "none"

    @staticmethod
    def _detect_reboot_required() -> tuple[bool, str]:
        """Check if system reboot is required."""
        reboot_file = pathlib.Path("/var/run/reboot-required")
        if reboot_file.exists():
            pkgs_file = pathlib.Path("/var/run/reboot-required.pkgs")
            try:
                if pkgs_file.exists():
                    pkgs = pkgs_file.read_text()
                    if "nvidia" in pkgs.lower():
                        return (True, "NVIDIA driver update requires reboot")
            except OSError:
                pass
            return (True, "System reboot required")
        return (False, "")

    # ------------------------------------------------------------------
    # D-Bus property handler
    # ------------------------------------------------------------------

    def _handle_get_property(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        property_name: str,
    ) -> GLib.Variant | None:
        if property_name == "DaemonVersion":
            return GLib.Variant("s", __version__)
        if property_name == "OperationInProgress":
            return GLib.Variant("b", self._operation_in_progress)
        return None


# ------------------------------------------------------------------
# Variant helper functions (module-level for testability)
# ------------------------------------------------------------------


def _set_str(d: dict, key: str, value: object) -> None:
    """Set a string variant, or mark unavailable."""
    if value is not None and value is not Unavailable:
        d[key] = GLib.Variant("s", str(value))
    else:
        d[f"{key}_available"] = GLib.Variant("b", False)


def _set_int(d: dict, key: str, value: object) -> None:
    """Set an int32 variant, or mark unavailable."""
    if value is not None and value is not Unavailable:
        d[key] = GLib.Variant("i", int(value))
    else:
        d[f"{key}_available"] = GLib.Variant("b", False)


def _set_int64(d: dict, key: str, value: object) -> None:
    """Set an int64 variant (for large values like memory bytes)."""
    if value is not None and value is not Unavailable:
        d[key] = GLib.Variant("x", int(value))
    else:
        d[f"{key}_available"] = GLib.Variant("b", False)


def _set_uint(d: dict, key: str, value: object) -> None:
    """Set a uint32 variant, or mark unavailable."""
    if value is not None and value is not Unavailable:
        d[key] = GLib.Variant("u", int(value))
    else:
        d[f"{key}_available"] = GLib.Variant("b", False)


def _set_bool(d: dict, key: str, value: object) -> None:
    """Set a boolean variant, or mark unavailable."""
    if value is not None and value is not Unavailable:
        d[key] = GLib.Variant("b", bool(value))
    else:
        d[f"{key}_available"] = GLib.Variant("b", False)
