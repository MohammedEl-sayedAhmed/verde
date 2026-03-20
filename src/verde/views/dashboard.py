"""Dashboard view — GPU monitoring at a glance."""

from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from verde.help_content import METRIC_TOOLTIPS, STATUS_TOOLTIPS
from verde.humanized_status import humanize_temperature
from verde.widgets.status_indicator import StatusIndicator

# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]

log = logging.getLogger(__name__)

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
    "driver_not_loaded": {
        "title": "Driver Installed \u2014 Module Not Loaded",
        "description": (
            "NVIDIA driver package is installed but the kernel module "
            "is not loaded. Verde can diagnose and fix this automatically."
        ),
        "icon": "dialog-warning-symbolic",
        "action": "View Drivers",
        "fix_action": "Fix",
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

        def bind_state(self, gpu_state: GPUState, dbus_client=None) -> None:
            """Build dashboard UI and bind to GPUState properties."""
            self._gpu_state = gpu_state
            self._dbus_client = dbus_client
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
            self._on_advanced_info_changed()
            self._on_throttle_changed()
            self._on_device_count_changed()
            self._on_processes_changed()
            self._update_health()
            # Check initial degraded state
            self._on_degraded_state_changed()

        def _build_ui(self) -> None:
            # ── GPU Identity group ──
            self._gpu_group = Adw.PreferencesGroup(title="GPU")
            self.add(self._gpu_group)
            self._monitoring_groups.append(self._gpu_group)

            self._gpu_name_row = Adw.ActionRow(title="GPU")
            self._gpu_name_row.set_tooltip_text(METRIC_TOOLTIPS["gpu_model"])
            self._gpu_group.add(self._gpu_name_row)

            self._driver_row = Adw.ActionRow(title="Driver")
            self._driver_row.set_tooltip_text(METRIC_TOOLTIPS["driver_version"])
            self._gpu_group.add(self._driver_row)

            # ── System Health group ──
            self._health_group = Adw.PreferencesGroup(title="System Health")
            self.add(self._health_group)
            self._monitoring_groups.append(self._health_group)

            self._health_indicator = StatusIndicator()
            self._health_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            self._health_row = Adw.ActionRow(title="Overall Status")
            self._health_row.set_tooltip_text(STATUS_TOOLTIPS["healthy"])
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
            self._temp_row.set_tooltip_text(METRIC_TOOLTIPS["temperature"])
            self._temp_row.add_suffix(self._temp_indicator)
            self._stats_group.add(self._temp_row)

            # GPU Utilization
            self._util_indicator = StatusIndicator()
            self._util_row = Adw.ActionRow(title="GPU Utilization")
            self._util_row.set_tooltip_text(METRIC_TOOLTIPS["utilization"])
            self._util_row.add_suffix(self._util_indicator)
            self._stats_group.add(self._util_row)

            # VRAM Usage
            self._vram_indicator = StatusIndicator()
            self._vram_row = Adw.ActionRow(title="VRAM Usage")
            self._vram_row.set_tooltip_text(METRIC_TOOLTIPS["vram_usage"])
            self._vram_row.add_suffix(self._vram_indicator)
            self._stats_group.add(self._vram_row)

            # Power Draw
            self._power_indicator = StatusIndicator()
            self._power_row = Adw.ActionRow(title="Power Draw")
            self._power_row.set_tooltip_text(METRIC_TOOLTIPS["power_draw"])
            self._power_row.add_suffix(self._power_indicator)
            self._stats_group.add(self._power_row)

            # ── ATK live region on stats group ──
            self._stats_group.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ["Live GPU Statistics"],
            )

            # ── Advanced Details group (collapsed by default) ──
            self._advanced_group = Adw.PreferencesGroup(title="Advanced Details")
            self.add(self._advanced_group)
            self._monitoring_groups.append(self._advanced_group)

            self._advanced_expander = Adw.ExpanderRow(
                title="Hardware and Software Details",
                show_enable_switch=False,
            )
            self._advanced_group.add(self._advanced_expander)

            # CUDA cores
            self._cores_row = Adw.ActionRow(title="CUDA Cores")
            self._cores_row.set_tooltip_text(METRIC_TOOLTIPS["cuda_cores"])
            self._advanced_expander.add_row(self._cores_row)

            # Compute capability
            self._compute_cap_row = Adw.ActionRow(title="Compute Capability")
            self._compute_cap_row.set_tooltip_text(METRIC_TOOLTIPS["compute_capability"])
            self._advanced_expander.add_row(self._compute_cap_row)

            # PCIe Bus ID
            self._pcie_bus_id_row = Adw.ActionRow(title="PCIe Bus ID")
            self._pcie_bus_id_row.set_tooltip_text(METRIC_TOOLTIPS["pcie_info"])
            self._advanced_expander.add_row(self._pcie_bus_id_row)

            # Power limit
            self._power_limit_row = Adw.ActionRow(title="Power Limit")
            self._power_limit_row.set_tooltip_text(METRIC_TOOLTIPS["power_limit"])
            self._advanced_expander.add_row(self._power_limit_row)

            # Driver CUDA version
            self._cuda_driver_row = Adw.ActionRow(title="Driver CUDA Version")
            self._cuda_driver_row.set_tooltip_text(METRIC_TOOLTIPS["cuda_version"])
            self._cuda_driver_row.set_subtitle("Maximum CUDA version supported by driver")
            self._advanced_expander.add_row(self._cuda_driver_row)

            # CUDA toolkit version
            self._cuda_toolkit_row = Adw.ActionRow(title="CUDA Toolkit Version")
            self._cuda_toolkit_row.set_tooltip_text(METRIC_TOOLTIPS["cuda_toolkit_version"])
            self._cuda_toolkit_row.set_subtitle("Installed toolkit version")
            self._advanced_expander.add_row(self._cuda_toolkit_row)

            # GPU mode (Optimus) — hidden by default, shown only when available
            self._gpu_mode_row = Adw.ActionRow(title="GPU Mode")
            self._gpu_mode_row.set_tooltip_text(METRIC_TOOLTIPS["gpu_mode"])
            self._gpu_mode_row.set_visible(False)
            self._advanced_expander.add_row(self._gpu_mode_row)

            # ECC row — hidden by default, shown only on ECC-capable GPUs
            self._ecc_row = Adw.ActionRow(title="ECC Memory")
            self._ecc_row.set_tooltip_text(METRIC_TOOLTIPS["ecc_memory"])
            self._ecc_row.set_visible(False)
            self._advanced_expander.add_row(self._ecc_row)

            # Throttle reasons — hidden when no throttling
            self._throttle_row = Adw.ActionRow(title="Throttle Reasons")
            self._throttle_row.set_tooltip_text(METRIC_TOOLTIPS["throttle_reason"])
            self._throttle_row.set_visible(False)
            self._advanced_expander.add_row(self._throttle_row)

            # Multi-GPU notice — hidden when single GPU
            self._multi_gpu_row = Adw.ActionRow(
                title="Additional GPUs Detected",
            )
            self._multi_gpu_row.set_tooltip_text(METRIC_TOOLTIPS["multi_gpu"])
            self._multi_gpu_row.set_visible(False)
            self._advanced_expander.add_row(self._multi_gpu_row)

            # ── Running Processes group ──
            self._process_group = Adw.PreferencesGroup(title="Running Processes")
            self.add(self._process_group)
            self._monitoring_groups.append(self._process_group)

            self._process_expander = Adw.ExpanderRow(
                title="GPU Processes",
                subtitle="No processes",
                show_enable_switch=False,
            )
            self._process_group.add(self._process_expander)
            self._process_rows: list[Adw.ActionRow] = []
            self._last_proc_snapshot: tuple = ()

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
            gs.connect("notify::power-limit", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::gpu-available", lambda *_: self._on_gpu_available_changed())
            gs.connect("notify::degraded-state", lambda *_: self._on_degraded_state_changed())
            # Advanced details signals
            gs.connect("notify::num-cores", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::compute-capability", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::cuda-driver-version", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::cuda-toolkit-version", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::pci-bus-id", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::gpu-mode", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::ecc-mode", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::memory-errors", lambda *_: self._on_advanced_info_changed())
            gs.connect("notify::throttle-reasons", lambda *_: self._on_throttle_changed())
            gs.connect("notify::device-count", lambda *_: self._on_device_count_changed())
            gs.connect("notify::process-count", lambda *_: self._on_processes_changed())

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
            self._temp_row.set_subtitle(humanize_temperature(temp, TEMP_WARN, TEMP_CRIT))
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
            draw = gs.get_property("power-draw") / 1000  # mW → W
            limit = gs.get_property("power-limit") / 1000  # mW → W
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
            # Update tooltip to match current health level
            tooltip_key = {"good": "healthy", "warn": "warning", "crit": "critical"}
            self._health_row.set_tooltip_text(STATUS_TOOLTIPS[tooltip_key[health]])
            self._health_row.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION],
                [f"System Health: {labels[health]}"],
            )

        def _on_advanced_info_changed(self) -> None:
            gs = self._gpu_state
            # CUDA cores
            cores = gs.get_property("num-cores")
            self._cores_row.set_subtitle(str(cores) if cores else "Unavailable")

            # Compute capability
            cc = gs.get_property("compute-capability")
            self._compute_cap_row.set_subtitle(cc if cc else "Unavailable")

            # PCIe Bus ID
            bus_id = gs.get_property("pci-bus-id")
            self._pcie_bus_id_row.set_subtitle(bus_id if bus_id else "Unavailable")

            # Power limit (mW → W)
            plimit = gs.get_property("power-limit") / 1000
            self._power_limit_row.set_subtitle(f"{plimit:.0f}W" if plimit > 0 else "Unavailable")

            # Driver CUDA version
            cuda_drv = gs.get_property("cuda-driver-version")
            self._cuda_driver_row.set_subtitle(
                f"Up to CUDA {cuda_drv}" if cuda_drv else "Unavailable"
            )

            # CUDA toolkit version
            cuda_tk = gs.get_property("cuda-toolkit-version")
            self._cuda_toolkit_row.set_subtitle(
                f"CUDA Toolkit {cuda_tk}" if cuda_tk else "Not installed"
            )

            # GPU mode (Optimus)
            mode = gs.get_property("gpu-mode")
            if mode:
                self._gpu_mode_row.set_subtitle(mode)
                self._gpu_mode_row.set_visible(True)
            else:
                self._gpu_mode_row.set_visible(False)

            # ECC
            ecc = gs.get_property("ecc-mode")
            if ecc:
                errors = gs.get_property("memory-errors")
                self._ecc_row.set_subtitle(f"Enabled — {errors} errors")
                self._ecc_row.set_visible(True)
            else:
                self._ecc_row.set_visible(False)

        def _on_throttle_changed(self) -> None:
            reasons = self._gpu_state.get_property("throttle-reasons")
            if reasons:
                self._throttle_row.set_subtitle(reasons)
                self._throttle_row.add_css_class("warning")
                self._throttle_row.set_visible(True)
            else:
                self._throttle_row.remove_css_class("warning")
                self._throttle_row.set_visible(False)

        def _on_device_count_changed(self) -> None:
            count = self._gpu_state.get_property("device-count")
            if count > 1:
                devices = self._gpu_state.get_devices()
                # Skip index 0 (primary GPU) — list additional GPUs by name and bus ID
                extras = [d for d in devices if d.get("index", 0) != 0]
                parts = []
                for d in extras:
                    name = d.get("name", "Unknown GPU")
                    bus_id = d.get("bus_id", "")
                    parts.append(f"{name} ({bus_id})" if bus_id else name)
                if parts:
                    self._multi_gpu_row.set_subtitle(", ".join(parts))
                else:
                    extra = count - 1
                    label = "GPU" if extra == 1 else "GPUs"
                    self._multi_gpu_row.set_subtitle(f"{extra} additional {label} detected")
                self._multi_gpu_row.set_visible(True)
            else:
                self._multi_gpu_row.set_visible(False)

        def _on_processes_changed(self) -> None:
            gs = self._gpu_state
            procs = gs.get_processes()

            # Skip rebuild if the PID set hasn't changed
            new_pids = tuple(
                (p.get("pid"), p.get("used_gpu_memory", 0), p.get("sm_util")) for p in procs
            )
            if self._last_proc_snapshot == new_pids:
                return
            self._last_proc_snapshot = new_pids

            # Remove old process rows
            for row in self._process_rows:
                self._process_expander.remove(row)
            self._process_rows.clear()

            if not procs:
                self._process_expander.set_subtitle("No processes")
                return

            count = len(procs)
            label = "process" if count == 1 else "processes"
            self._process_expander.set_subtitle(f"{count} {label}")

            for p in procs:
                pid = p.get("pid", "?")
                vram_bytes = p.get("used_gpu_memory", 0)
                vram_mb = vram_bytes / (1024 * 1024) if vram_bytes else 0
                sm = p.get("sm_util")
                proc_type = p.get("type", "")

                # Format: "PID 1234 (compute)" with subtitle "VRAM: 512 MB | GPU: 42%"
                title = f"PID {pid}"
                if proc_type:
                    title += f" ({proc_type})"

                has_sm = "sm_util" in p and sm is not None
                if has_sm:
                    subtitle = f"VRAM: {vram_mb:.0f} MB  |  GPU: {sm}%"
                else:
                    subtitle = f"VRAM: {vram_mb:.0f} MB  |  GPU: N/A"

                row = Adw.ActionRow(title=title)
                row.set_subtitle(subtitle)
                self._process_expander.add_row(row)
                self._process_rows.append(row)

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

            if info.get("action") or info.get("fix_action"):
                btn_box = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    halign=Gtk.Align.CENTER,
                    spacing=12,
                )
                if info.get("fix_action"):
                    fix_btn = Gtk.Button(label=info["fix_action"])
                    fix_btn.add_css_class("suggested-action")
                    fix_btn.add_css_class("pill")
                    fix_btn.connect("clicked", self._on_fix_module_clicked)
                    fix_btn.update_property(
                        [Gtk.AccessibleProperty.LABEL],
                        [_("Fix module not loaded issue")],
                    )
                    btn_box.append(fix_btn)
                if info.get("action"):
                    nav_btn = Gtk.Button(label=info["action"])
                    nav_btn.add_css_class("pill")
                    nav_btn.connect("clicked", self._on_degraded_action_clicked)
                    btn_box.append(nav_btn)
                status_page.set_child(btn_box)

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

        def _on_fix_module_clicked(self, _btn: Gtk.Button) -> None:
            """Handle Fix button click for module-not-loaded state (Story 2.7)."""
            if self._dbus_client is None:
                return

            window = self.get_root()

            # First diagnose, then show preflight dialog
            def _on_diagnosis_reply(proxy, result):
                try:
                    reply = proxy.call_finish(result)
                    diag = reply.unpack()[0]
                    GLib.idle_add(self._show_module_fix_dialog, window, diag)
                except GLib.Error as exc:
                    log.warning("DiagnoseModuleFailure failed: %s", exc.message)

            self._dbus_client.call_method_async(
                "DiagnoseModuleFailure",
                None,
                _on_diagnosis_reply,
            )

        def _show_module_fix_dialog(self, window, diag: dict) -> bool:
            """Show the module fix pre-flight dialog."""
            from verde.widgets.preflight_banner import PreflightPanel

            diag.get("cause", "unknown")
            detail = diag.get("detail", "")
            fixable = diag.get("fixable", False)
            fix_actions = diag.get("fix_actions", [])
            reboot_required = diag.get("reboot_required", False)

            dialog = Adw.MessageDialog.new(window, _("Module Not Loaded"))
            dialog.set_body(detail)
            dialog.set_body_use_markup(False)

            if fixable:
                preflight = PreflightPanel()
                checks = []
                for action in fix_actions:
                    checks.append({"name": action, "status": "action", "description": ""})
                preflight.set_checks(checks)
                dialog.set_extra_child(preflight)

                dialog.add_response("cancel", _("Cancel"))
                dialog.add_response("fix", _("Fix"))
                dialog.set_response_appearance("fix", Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response("cancel")
                dialog.set_close_response("cancel")
                dialog.connect("response", self._on_module_fix_response)
            else:
                dialog.add_response("close", _("Close"))
                dialog.set_default_response("close")
                dialog.set_close_response("close")

            if reboot_required:
                dialog.set_body(
                    detail + "\n\n" + _("A reboot will be required after the fix is applied.")
                )

            dialog.present()
            return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

        def _on_module_fix_response(self, dialog: Adw.MessageDialog, response: str) -> None:
            """Handle module fix dialog confirmation."""
            if response != "fix" or self._dbus_client is None:
                return

            from verde.widgets.progress_overlay import OperationProgressPanel

            progress = OperationProgressPanel()
            progress.set_stage(_("Starting fix\u2026"), 0.0)
            dialog.set_extra_child(progress)
            dialog.set_heading(_("Fixing Module"))
            dialog.set_body("")
            dialog.set_close_response("")
            dialog.set_response_enabled("cancel", False)
            dialog.set_response_enabled("fix", False)

            def _on_reply(proxy, result):
                try:
                    reply = proxy.call_finish(result)
                    op_id = reply.unpack()[0]
                    dialog._op_id = op_id
                    dialog._progress_panel = progress
                except GLib.Error as exc:
                    log.warning("FixModuleNotLoaded failed: %s", exc.message)
                    progress.set_error(str(exc.message))
                    dialog.set_close_response("cancel")
                    dialog.set_response_enabled("cancel", True)

            self._dbus_client.call_method_async(
                "FixModuleNotLoaded",
                None,
                _on_reply,
            )

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
