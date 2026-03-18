"""Dashboard view — GPU monitoring at a glance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, Gtk

from verde.widgets.status_indicator import StatusIndicator

if TYPE_CHECKING:
    from verde.gpu_state import GPUState

# ── Thresholds ───────────────────────────────────────────────────────

TEMP_WARN = 80  # degrees C
TEMP_CRIT = 90

UTIL_WARN = 90  # percent
UTIL_CRIT = 98

POWER_WARN_PCT = 85  # percent of power limit
POWER_CRIT_PCT = 95

VRAM_WARN_PCT = 85  # percent of total
VRAM_CRIT_PCT = 95


def compute_health_status(gpu_state: GPUState) -> str:
    """Return aggregate health: ``"good"``, ``"warn"``, or ``"crit"``."""
    temp = gpu_state.get_property("temperature")
    util = gpu_state.get_property("utilization")
    power_draw = gpu_state.get_property("power-draw")
    power_limit = gpu_state.get_property("power-limit")
    mem_used = gpu_state.get_property("memory-used")
    mem_total = gpu_state.get_property("memory-total")

    levels: list[str] = []

    # Temperature
    if temp >= TEMP_CRIT:
        levels.append("crit")
    elif temp >= TEMP_WARN:
        levels.append("warn")

    # Utilization
    if util >= UTIL_CRIT:
        levels.append("crit")
    elif util >= UTIL_WARN:
        levels.append("warn")

    # Power (percent of limit)
    if power_limit > 0:
        power_pct = (power_draw / power_limit) * 100
        if power_pct >= POWER_CRIT_PCT:
            levels.append("crit")
        elif power_pct >= POWER_WARN_PCT:
            levels.append("warn")

    # VRAM (percent of total)
    if mem_total > 0:
        vram_pct = (mem_used / mem_total) * 100
        if vram_pct >= VRAM_CRIT_PCT:
            levels.append("crit")
        elif vram_pct >= VRAM_WARN_PCT:
            levels.append("warn")

    if "crit" in levels:
        return "crit"
    if "warn" in levels:
        return "warn"
    return "good"


# ── Helper ───────────────────────────────────────────────────────────


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


# ── Dashboard Page ───────────────────────────────────────────────────
# The Blueprint template path is kept for when a full .blp layout is
# authored in a future story.  For now, 1.9 builds programmatically.

if _has_resource("/com/verde/app/ui/dashboard_page.ui"):

    @Gtk.Template(resource_path="/com/verde/app/ui/dashboard_page.ui")
    class DashboardPage(Adw.PreferencesPage):
        __gtype_name__ = "DashboardPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

else:

    class DashboardPage(Adw.PreferencesPage):  # type: ignore[no-redef]
        __gtype_name__ = "DashboardPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_title("Dashboard")
            self.set_icon_name("speedometer-symbolic")

        def bind_state(self, gpu_state: GPUState) -> None:
            """Build dashboard UI and bind to GPUState properties."""
            self._gpu_state = gpu_state
            self._build_ui()
            self._connect_signals()
            # Initial update from current state
            self._on_gpu_info_changed()
            self._on_temperature_changed()
            self._on_utilization_changed()
            self._on_memory_changed()
            self._on_power_changed()
            self._update_health()

        def _build_ui(self) -> None:
            # ── GPU Identity group ──
            self._gpu_group = Adw.PreferencesGroup(title="GPU")
            self.add(self._gpu_group)

            self._gpu_name_row = Adw.ActionRow(title="GPU")
            self._gpu_group.add(self._gpu_name_row)

            self._driver_row = Adw.ActionRow(title="Driver")
            self._gpu_group.add(self._driver_row)

            # ── System Health group ──
            self._health_group = Adw.PreferencesGroup(title="System Health")
            self.add(self._health_group)

            self._health_indicator = StatusIndicator()
            self._health_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            self._health_row = Adw.ActionRow(title="Overall Status")
            self._health_row.add_prefix(self._health_icon)
            self._health_row.add_suffix(self._health_indicator)
            self._health_group.add(self._health_row)

            # ── Live Statistics group ──
            self._stats_group = Adw.PreferencesGroup(title="Live Statistics")
            self.add(self._stats_group)

            # Temperature
            self._temp_indicator = StatusIndicator()
            self._temp_row = Adw.ActionRow(title="Temperature")
            self._temp_row.add_suffix(self._temp_indicator)
            self._stats_group.add(self._temp_row)

            # GPU Utilization
            self._util_indicator = StatusIndicator()
            self._util_row = Adw.ActionRow(title="GPU Utilization")
            self._util_row.add_suffix(self._util_indicator)
            self._stats_group.add(self._util_row)

            # VRAM Usage
            self._vram_indicator = StatusIndicator()
            self._vram_row = Adw.ActionRow(title="VRAM Usage")
            self._vram_row.add_suffix(self._vram_indicator)
            self._stats_group.add(self._vram_row)

            # Power Draw
            self._power_indicator = StatusIndicator()
            self._power_row = Adw.ActionRow(title="Power Draw")
            self._power_row.add_suffix(self._power_indicator)
            self._stats_group.add(self._power_row)

            # ── ATK live region on stats group ──
            self._stats_group.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ["Live GPU Statistics"],
            )

        def _connect_signals(self) -> None:
            gs = self._gpu_state
            gs.connect("notify::gpu-name", lambda *_: self._on_gpu_info_changed())
            gs.connect("notify::driver-version", lambda *_: self._on_gpu_info_changed())
            gs.connect("notify::driver-type", lambda *_: self._on_gpu_info_changed())
            gs.connect("notify::temperature", lambda *_: self._on_temperature_changed())
            gs.connect("notify::utilization", lambda *_: self._on_utilization_changed())
            gs.connect("notify::memory-used", lambda *_: self._on_memory_changed())
            gs.connect("notify::memory-total", lambda *_: self._on_memory_changed())
            gs.connect("notify::power-draw", lambda *_: self._on_power_changed())
            gs.connect("notify::power-limit", lambda *_: self._on_power_changed())

        # ── Property change handlers ─────────────────────────────────

        def _on_gpu_info_changed(self) -> None:
            gs = self._gpu_state
            name = gs.get_property("gpu-name") or "Unknown GPU"
            self._gpu_name_row.set_title(name)

            driver_ver = gs.get_property("driver-version") or "Unknown"
            driver_type = gs.get_property("driver-type") or "unknown"
            self._driver_row.set_subtitle(f"{driver_ver} ({driver_type})")

        def _on_temperature_changed(self) -> None:
            temp = self._gpu_state.get_property("temperature")
            self._temp_row.set_subtitle(f"{temp}°C")
            self._temp_indicator.set_status_from_thresholds(temp, TEMP_WARN, TEMP_CRIT)
            self._temp_row.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION],
                [
                    f"GPU Temperature: {temp} degrees celsius, "
                    f"{_level_text(temp, TEMP_WARN, TEMP_CRIT)}"
                ],
            )
            self._update_health()

        def _on_utilization_changed(self) -> None:
            util = self._gpu_state.get_property("utilization")
            self._util_row.set_subtitle(f"{util}%")
            self._util_indicator.set_status_from_thresholds(util, UTIL_WARN, UTIL_CRIT)
            self._util_row.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION],
                [f"GPU Utilization: {util} percent, {_level_text(util, UTIL_WARN, UTIL_CRIT)}"],
            )
            self._update_health()

        def _on_memory_changed(self) -> None:
            gs = self._gpu_state
            used = gs.get_property("memory-used")
            total = gs.get_property("memory-total")
            used_gb = used / (1024**3) if used else 0.0
            total_gb = total / (1024**3) if total else 0.0
            self._vram_row.set_subtitle(f"{used_gb:.1f} / {total_gb:.1f} GB")

            pct = (used / total * 100) if total > 0 else 0.0
            self._vram_indicator.set_status_from_thresholds(pct, VRAM_WARN_PCT, VRAM_CRIT_PCT)
            self._vram_row.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION],
                [
                    f"VRAM Usage: {used_gb:.1f} of {total_gb:.1f} gigabytes, "
                    f"{_level_text(pct, VRAM_WARN_PCT, VRAM_CRIT_PCT)}"
                ],
            )
            self._update_health()

        def _on_power_changed(self) -> None:
            gs = self._gpu_state
            draw = gs.get_property("power-draw")
            limit = gs.get_property("power-limit")
            self._power_row.set_subtitle(f"{draw:.0f}W")

            pct = (draw / limit * 100) if limit > 0 else 0.0
            self._power_indicator.set_status_from_thresholds(pct, POWER_WARN_PCT, POWER_CRIT_PCT)
            self._power_row.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION],
                [
                    f"Power Draw: {draw:.0f} watts, "
                    f"{_level_text(pct, POWER_WARN_PCT, POWER_CRIT_PCT)}"
                ],
            )
            self._update_health()

        def _update_health(self) -> None:
            health = compute_health_status(self._gpu_state)
            labels = {
                "good": "All Systems Normal",
                "warn": "Attention Needed",
                "crit": "Critical",
            }
            icons = {
                "good": "emblem-ok-symbolic",
                "warn": "dialog-warning-symbolic",
                "crit": "dialog-error-symbolic",
            }
            self._health_indicator.set_status(labels[health], health)
            self._health_icon.set_from_icon_name(icons[health])
            self._health_row.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION],
                [f"System Health: {labels[health]}"],
            )


def _level_text(value: float, warn: float, crit: float) -> str:
    """Return human-readable level text for accessibility."""
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return "safe range"
