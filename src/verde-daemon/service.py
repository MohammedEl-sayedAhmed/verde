"""Verde D-Bus service — registers on the system bus as com.verde.Manager."""

from __future__ import annotations

import logging
import pathlib
from collections.abc import Callable

from gi.repository import Gio, GLib

from verde_daemon import __version__
from verde_daemon.audit import AuditLogger
from verde_daemon.degraded_states import (
    DegradedState,
    build_state_info,
    detect_degraded_state,
    detect_driver_type,
)
from verde_daemon.driver_manager import DriverManager
from verde_daemon.nvml_wrapper import NvmlWrapper, Unavailable
from verde_daemon.polkit import METHOD_ACTION_MAP, check_authorization
from verde_daemon.preflight import PreflightChecker
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
        introspection_xml: str | None = None,
        xml_path: str | pathlib.Path | None = None,
        audit_logger: AuditLogger | None = None,
        nvml: NvmlWrapper | None = None,
        driver_manager: DriverManager | None = None,
    ) -> None:
        self._loop = loop
        self._on_idle_reset = on_idle_reset
        self._audit = audit_logger
        self._connection: Gio.DBusConnection | None = None
        self._registration_id: int = 0
        self._owner_id: int = 0
        self._poll_source_id: int | None = None

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

        # Degraded state tracking — re-detected each poll cycle
        self._current_degraded_state: DegradedState = detect_degraded_state(self._nvml)

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
            new_state = detect_degraded_state(self._nvml)
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
        if not check_authorization(connection, sender, action_id):
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
                self._dispatch_list_available_drivers(invocation)
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

    # ------------------------------------------------------------------
    # GPU data builders
    # ------------------------------------------------------------------

    def _build_gpu_info(self) -> dict[str, GLib.Variant]:
        """Build GetGPUInfo response from NVML data."""
        if not self._nvml_available:
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

        # Return (aa{sv}a{sv}) — drivers array + metadata dict
        invocation.return_value(
            GLib.Variant.new_tuple(
                GLib.Variant("aa{sv}", driver_variants),
                GLib.Variant("a{sv}", meta),
            )
        )

    def _build_degraded_state_response(self) -> dict[str, GLib.Variant]:
        """Build GetDegradedState response from current state."""
        dc = self._nvml.device_count() if self._nvml_available else 0
        if dc is Unavailable:
            dc = 0
        driver = detect_driver_type()
        info = build_state_info(self._current_degraded_state, driver, int(dc))
        return {
            "state": GLib.Variant("s", info["state"]),
            "driver_type": GLib.Variant("s", info["driver_type"]),
            "device_count": GLib.Variant("i", info["device_count"]),
            "message": GLib.Variant("s", info["message"]),
        }

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
            return GLib.Variant("b", False)
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
