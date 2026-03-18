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


# ── Degraded state display map ────────────────────────────────────────

_DEGRADED_STATES: dict[str, dict[str, str | None]] = {
    "no_gpu": {
        "title": "No NVIDIA GPU Detected",
        "description": (
            "Verde couldn\u2019t find an NVIDIA GPU on this system. "
            "This could mean no NVIDIA card is installed, "
            "or the GPU is not recognized by the system."
        ),
        "icon": "computer-fail-symbolic",
        "action": None,
    },
    "nouveau_active": {
        "title": "Open-Source Driver Active",
        "description": (
            "Your NVIDIA GPU is using the open-source nouveau driver, "
            "which provides basic display but no monitoring or advanced "
            "features. Install a proprietary driver for full functionality."
        ),
        "icon": "dialog-information-symbolic",
        "action": "View Drivers",
    },
    "no_driver": {
        "title": "No Driver Installed",
        "description": ("Your NVIDIA GPU needs a driver for full performance and monitoring."),
        "icon": "system-software-install-symbolic",
        "action": "Install Driver",
    },
    "daemon_unreachable": {
        "title": "System Service Unavailable",
        "description": (
            "Verde\u2019s system service is not responding. "
            "Try restarting the service with: "
            "systemctl restart com.verde.Manager"
        ),
        "icon": "network-error-symbolic",
        "action": None,
    },
    "gpu_lost": {
        "title": "GPU Connection Lost",
        "description": (
            "Your NVIDIA GPU is no longer responding. This typically "
            "indicates a hardware issue. Try rebooting the system. "
            "If the problem persists, the GPU may need to be "
            "physically reseated."
        ),
        "icon": "dialog-warning-symbolic",
        "action": None,
    },
    "nvml_unavailable": {
        "title": "GPU Management Unavailable",
        "description": (
            "The NVIDIA management library could not be loaded. "
            "This usually means the NVIDIA driver is not installed "
            "or not properly configured."
        ),
        "icon": "dialog-warning-symbolic",
        "action": "View Drivers",
    },
}


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
            self._degraded_group: Adw.PreferencesGroup | None = None
            self._monitoring_groups: list[Adw.PreferencesGroup] = []
            self._current_view = "monitoring"
            self._build_ui()
            self._connect_signals()
            # Initial update from current state
            self._on_gpu_info_changed()
            self._on_temperature_changed()
            self._on_utilization_changed()
            self._on_memory_changed()
            self._on_power_changed()
            self._update_health()
            # Check initial degraded state
            self._on_degraded_state_changed()

        def _build_ui(self) -> None:
            # ── GPU Identity group ──
            self._gpu_group = Adw.PreferencesGroup(title="GPU")
            self.add(self._gpu_group)
            self._monitoring_groups.append(self._gpu_group)

            self._gpu_name_row = Adw.ActionRow(title="GPU")
            self._gpu_group.add(self._gpu_name_row)

            self._driver_row = Adw.ActionRow(title="Driver")
            self._gpu_group.add(self._driver_row)

            # ── System Health group ──
            self._health_group = Adw.PreferencesGroup(title="System Health")
            self.add(self._health_group)
            self._monitoring_groups.append(self._health_group)

            self._health_indicator = StatusIndicator()
            self._health_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            self._health_row = Adw.ActionRow(title="Overall Status")
            self._health_row.add_prefix(self._health_icon)
            self._health_row.add_suffix(self._health_indicator)
            self._health_group.add(self._health_row)

            # ── Live Statistics group ──
            self._stats_group = Adw.PreferencesGroup(title="Live Statistics")
            self.add(self._stats_group)
            self._monitoring_groups.append(self._stats_group)

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
            gs.connect("notify::gpu-available", lambda *_: self._on_gpu_available_changed())
            gs.connect("notify::degraded-state", lambda *_: self._on_degraded_state_changed())

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

        def _on_gpu_available_changed(self) -> None:
            if not self._gpu_state.get_property("gpu-available"):
                self._set_stats_unavailable()

        def _set_stats_unavailable(self) -> None:
            """Set all stat rows to Unavailable display."""
            self._temp_row.set_subtitle("Unavailable")
            self._temp_indicator.set_unavailable()
            self._util_row.set_subtitle("Unavailable")
            self._util_indicator.set_unavailable()
            self._vram_row.set_subtitle("Unavailable")
            self._vram_indicator.set_unavailable()
            self._power_row.set_subtitle("Unavailable")
            self._power_indicator.set_unavailable()
            self._health_indicator.set_status("Unavailable", "unknown")
            self._health_icon.set_from_icon_name("content-loading-symbolic")

        # ── Degraded state switching ─────────────────────────────────

        def _on_degraded_state_changed(self) -> None:
            state = self._gpu_state.get_property("degraded-state")
            if state in ("normal", "unknown", "") or state not in _DEGRADED_STATES:
                self._show_monitoring_view()
            else:
                self._show_degraded_view(state)

        def _show_monitoring_view(self) -> None:
            if self._current_view == "monitoring":
                return
            # Remove degraded group
            if self._degraded_group is not None:
                self.remove(self._degraded_group)
                self._degraded_group = None
            # Re-add monitoring groups
            for group in self._monitoring_groups:
                self.add(group)
            self._current_view = "monitoring"

        def _show_degraded_view(self, state: str) -> None:
            info = _DEGRADED_STATES.get(state)
            if info is None:
                return

            # Remove monitoring groups
            if self._current_view == "monitoring":
                for group in self._monitoring_groups:
                    self.remove(group)

            # Remove old degraded group if switching between degraded states
            if self._degraded_group is not None:
                self.remove(self._degraded_group)

            # Build degraded status display
            self._degraded_group = Adw.PreferencesGroup()

            status_page = Adw.StatusPage()
            status_page.set_icon_name(info["icon"])
            status_page.set_title(info["title"])
            status_page.set_description(info["description"])

            if info.get("action"):
                btn = Gtk.Button(label=info["action"])
                btn.add_css_class("suggested-action")
                btn.add_css_class("pill")
                btn.set_halign(Gtk.Align.CENTER)
                btn.connect("clicked", self._on_degraded_action_clicked)
                status_page.set_child(btn)

            self._degraded_group.add(status_page)
            self.add(self._degraded_group)
            self._current_view = "degraded"

        def _on_degraded_action_clicked(self, _btn: Gtk.Button) -> None:
            """Navigate to Drivers page when action button is clicked."""
            page = self.get_parent()
            while page is not None and not isinstance(page, Adw.ViewStack):
                page = page.get_parent()
            if isinstance(page, Adw.ViewStack):
                page.set_visible_child_name("drivers")

        def show_gpu_lost_dialog(self, window: Gtk.Window) -> None:
            """Show a modal dialog when GPU is lost at runtime."""
            dialog = Adw.MessageDialog.new(
                window,
                "GPU Connection Lost",
            )
            dialog.set_body(
                "Your NVIDIA GPU is no longer responding. This typically "
                "indicates a hardware issue. Try rebooting the system. "
                "If the problem persists, the GPU may need to be "
                "physically reseated."
            )
            dialog.add_response("ok", "OK")
            dialog.set_default_response("ok")
            dialog.set_close_response("ok")
            dialog.present()


def _level_text(value: float, warn: float, crit: float) -> str:
    """Return human-readable level text for accessibility."""
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return "safe range"
