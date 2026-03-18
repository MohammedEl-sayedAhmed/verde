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

    # ── Integer properties ───────────────────────────────────────────
    temperature = GObject.Property(type=int, default=0)
    utilization = GObject.Property(type=int, default=0)
    fan_speed = GObject.Property(type=int, default=0, nick="fan-speed")
    clock_graphics = GObject.Property(type=int, default=0, nick="clock-graphics")
    clock_memory = GObject.Property(type=int, default=0, nick="clock-memory")

    # ── Float properties (includes memory bytes — float64 handles >4GB) ──
    memory_used = GObject.Property(type=float, default=0.0, nick="memory-used")
    memory_total = GObject.Property(type=float, default=0.0, nick="memory-total")
    power_draw = GObject.Property(type=float, default=0.0, nick="power-draw")
    power_limit = GObject.Property(type=float, default=0.0, nick="power-limit")

    # ── Boolean properties ───────────────────────────────────────────
    gpu_available = GObject.Property(type=bool, default=False, nick="gpu-available")
    reboot_required = GObject.Property(type=bool, default=False, nick="reboot-required")
    operation_in_progress = GObject.Property(
        type=bool, default=False, nick="operation-in-progress"
    )

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

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def reset(self) -> None:
        """Reset all stats properties to defaults (called on disconnect)."""
        GLib.idle_add(self._do_reset)

    def _do_reset(self) -> bool:
        """Apply reset on the main thread."""
        for prop_name, default in _STAT_DEFAULTS.items():
            self.set_property(prop_name, default)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]
