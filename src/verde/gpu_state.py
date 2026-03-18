"""GPUState GObject model — maps D-Bus GPU data to bindable properties."""

from __future__ import annotations

import logging

from gi.repository import GLib, GObject

log = logging.getLogger("verde.gpu_state")

# Maps D-Bus a{sv} keys (snake_case) to GObject property names (kebab-case).
_KEY_MAP: dict[str, str] = {
    "temperature": "temperature",
    "utilization_gpu": "utilization",
    "memory_used": "memory-used",
    "memory_total": "memory-total",
    "fan_speed": "fan-speed",
    "power_usage": "power-draw",
    "power_limit": "power-limit",
    "clock_graphics": "clock-graphics",
    "clock_mem": "clock-memory",
    "name": "gpu-name",
    "driver_version": "driver-version",
    "driver_type": "driver-type",
    "state": "degraded-state",
    "cuda_driver_version": "cuda-driver-version",
    "cuda_toolkit_version": "cuda-toolkit-version",
    "gpu_mode": "gpu-mode",
    "num_cores": "num-cores",
    "device_count": "device-count",
    "memory_errors": "memory-errors",
    "pci_bus_id": "pci-bus-id",
}

# Default values for reset() — only stats properties, not connection state.
_STAT_DEFAULTS: dict[str, object] = {
    "gpu-name": "",
    "driver-version": "",
    "driver-type": "unknown",
    "degraded-state": "unknown",
    "temperature": 0,
    "utilization": 0,
    "memory-used": 0.0,
    "memory-total": 0.0,
    "fan-speed": 0,
    "power-draw": 0.0,
    "power-limit": 0.0,
    "clock-graphics": 0,
    "clock-memory": 0,
    "p-state": "",
    "gpu-available": False,
    "reboot-required": False,
    "reboot-reason": "",
    "cuda-driver-version": "",
    "cuda-toolkit-version": "",
    "gpu-mode": "",
    "num-cores": 0,
    "device-count": 0,
    "memory-errors": 0,
    "ecc-mode": False,
    "compute-capability": "",
    "throttle-reasons": "",
    "pci-bus-id": "",
    "process-count": 0,
}


class GPUState(GObject.Object):
    """Reactive GPU state model with GObject property bindings.

    Views bind to properties via ``connect("notify::temperature", cb)``
    or ``bind_property("temperature", widget, "label", ...)``.
    All property updates are dispatched via ``GLib.idle_add`` for
    thread-safe UI updates from D-Bus callbacks.
    """

    __gtype_name__ = "GPUState"

    # ── String properties ────────────────────────────────────────────
    gpu_name = GObject.Property(type=str, default="", nick="gpu-name")
    driver_version = GObject.Property(type=str, default="", nick="driver-version")
    driver_type = GObject.Property(type=str, default="unknown", nick="driver-type")
    degraded_state = GObject.Property(type=str, default="unknown", nick="degraded-state")
    p_state = GObject.Property(type=str, default="", nick="p-state")
    reboot_reason = GObject.Property(type=str, default="", nick="reboot-reason")
    cuda_driver_version = GObject.Property(type=str, default="", nick="cuda-driver-version")
    cuda_toolkit_version = GObject.Property(type=str, default="", nick="cuda-toolkit-version")
    gpu_mode = GObject.Property(type=str, default="", nick="gpu-mode")
    compute_capability = GObject.Property(type=str, default="", nick="compute-capability")
    throttle_reasons = GObject.Property(type=str, default="", nick="throttle-reasons")
    pci_bus_id = GObject.Property(type=str, default="", nick="pci-bus-id")

    # ── Integer properties ───────────────────────────────────────────
    temperature = GObject.Property(type=int, default=0)
    utilization = GObject.Property(type=int, default=0)
    fan_speed = GObject.Property(type=int, default=0, nick="fan-speed")
    clock_graphics = GObject.Property(type=int, default=0, nick="clock-graphics")
    clock_memory = GObject.Property(type=int, default=0, nick="clock-memory")
    num_cores = GObject.Property(type=int, default=0, nick="num-cores")
    device_count = GObject.Property(type=int, default=0, nick="device-count")
    memory_errors = GObject.Property(type=int, default=0, nick="memory-errors")
    process_count = GObject.Property(type=int, default=0, nick="process-count")

    # ── Float properties (includes memory bytes — float64 handles >4GB) ──
    memory_used = GObject.Property(type=float, default=0.0, nick="memory-used")
    memory_total = GObject.Property(type=float, default=0.0, nick="memory-total")
    power_draw = GObject.Property(type=float, default=0.0, nick="power-draw")
    power_limit = GObject.Property(type=float, default=0.0, nick="power-limit")

    # ── Boolean properties ───────────────────────────────────────────
    gpu_available = GObject.Property(type=bool, default=False, nick="gpu-available")
    reboot_required = GObject.Property(type=bool, default=False, nick="reboot-required")
    ecc_mode = GObject.Property(type=bool, default=False, nick="ecc-mode")
    operation_in_progress = GObject.Property(
        type=bool, default=False, nick="operation-in-progress"
    )

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._processes: list[dict] = []

    def get_processes(self) -> list[dict]:
        """Return the current process list (read-only copy)."""
        return list(self._processes)

    def update_from_dict(self, data: dict) -> None:
        """Update properties from a D-Bus ``a{sv}`` dict. Thread-safe.

        The dict is copied before scheduling to prevent mutation by the
        caller between now and when the idle callback fires.
        """
        GLib.idle_add(self._do_update, data.copy())

    def _set_if_changed(self, prop_name: str, value: object) -> None:
        """Set property only if the new value differs from current."""
        try:
            if self.get_property(prop_name) != value:
                self.set_property(prop_name, value)
        except TypeError:
            # Type coercion needed (e.g., int→float for memory bytes)
            prop = self.find_property(prop_name)
            if prop is None:
                return
            vtype = prop.value_type.name
            if vtype == "gint":
                value = int(value)  # type: ignore[call-overload]
            elif vtype in ("gdouble", "gfloat"):
                value = float(value)  # type: ignore[arg-type]
            elif vtype == "gchararray":
                value = str(value)
            if self.get_property(prop_name) != value:
                self.set_property(prop_name, value)

    def _do_update(self, data: dict) -> bool:
        """Apply property updates on the main thread."""
        # Map available flag
        if "available" in data:
            self._set_if_changed("gpu-available", bool(data["available"]))

        if "reboot_required" in data:
            self._set_if_changed("reboot-required", bool(data["reboot_required"]))
        if "reboot_reason" in data:
            self._set_if_changed("reboot-reason", str(data["reboot_reason"]))

        for dbus_key, prop_name in _KEY_MAP.items():
            if dbus_key in data:
                self._set_if_changed(prop_name, data[dbus_key])

        # Handle p-state formatting: int → "P0", "P8" etc.
        if "performance_state" in data:
            val = data["performance_state"]
            if isinstance(val, int):
                self._set_if_changed("p-state", f"P{val}")

        # Throttle reasons: D-Bus "as" (list of strings) → comma-joined string
        if "throttle_reasons_decoded" in data:
            reasons = data["throttle_reasons_decoded"]
            if isinstance(reasons, list):
                self._set_if_changed("throttle-reasons", ", ".join(reasons))
            elif isinstance(reasons, str):
                self._set_if_changed("throttle-reasons", reasons)

        # Compute capability: major/minor ints → "X.Y" string
        if "compute_capability_major" in data and "compute_capability_minor" in data:
            major = data["compute_capability_major"]
            minor = data["compute_capability_minor"]
            self._set_if_changed("compute-capability", f"{major}.{minor}")

        # ECC mode
        if "ecc_mode" in data:
            self._set_if_changed("ecc-mode", bool(data["ecc_mode"]))

        # Process list — stored as plain Python list, notify via process-count
        if "processes" in data:
            procs = data["processes"]
            if isinstance(procs, list):
                self._processes = procs
                self._set_if_changed("process-count", len(procs))

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def reset(self) -> None:
        """Reset all stats properties to defaults (called on disconnect)."""
        GLib.idle_add(self._do_reset)

    def _do_reset(self) -> bool:
        """Apply reset on the main thread."""
        for prop_name, default in _STAT_DEFAULTS.items():
            self.set_property(prop_name, default)
        self._processes = []
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]
