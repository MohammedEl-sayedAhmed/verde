"""Unit tests for dashboard view logic (Story 1.9)."""

from __future__ import annotations

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402

from verde.gpu_state import GPUState  # noqa: E402
from verde.views.dashboard import (  # noqa: E402
    TEMP_CRIT,
    TEMP_WARN,
    UTIL_CRIT,
    UTIL_WARN,
    DashboardPage,
    compute_health_status,
)


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


@pytest.fixture
def gpu_state():
    return GPUState()


# ===================================================================
# compute_health_status
# ===================================================================


class TestComputeHealthStatus:
    def test_all_below_warn_returns_good(self, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "good"

    def test_temp_in_warn_range_returns_warn(self, gpu_state):
        gpu_state.set_property("temperature", TEMP_WARN)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "warn"

    def test_temp_in_crit_range_returns_crit(self, gpu_state):
        gpu_state.set_property("temperature", TEMP_CRIT)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "crit"

    def test_util_in_warn_range_returns_warn(self, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", UTIL_WARN)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "warn"

    def test_util_in_crit_range_returns_crit(self, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", UTIL_CRIT)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "crit"

    def test_power_warn_returns_warn(self, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        # 85% of 300W = 255W
        gpu_state.set_property("power-draw", 255.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "warn"

    def test_vram_crit_returns_crit(self, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        # 96% of 24GB
        gpu_state.set_property("memory-used", 23.04e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "crit"

    def test_zero_power_limit_no_error(self, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 0.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert compute_health_status(gpu_state) == "good"

    def test_zero_memory_total_no_error(self, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 0.0)
        gpu_state.set_property("memory-total", 0.0)
        assert compute_health_status(gpu_state) == "good"


# ===================================================================
# Dashboard widget binding
# ===================================================================


class TestDashboardBinding:
    @pytest.fixture
    def dashboard(self, gpu_state):
        page = DashboardPage()
        page.bind_state(gpu_state)
        return page

    def test_temperature_subtitle_format(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", 67)
        assert dashboard._temp_row.get_subtitle() == "67°C"

    def test_utilization_subtitle_format(self, dashboard, gpu_state):
        gpu_state.set_property("utilization", 45)
        assert dashboard._util_row.get_subtitle() == "45%"

    def test_vram_subtitle_format(self, dashboard, gpu_state):
        gpu_state.set_property("memory-used", 8589934592.0)
        gpu_state.set_property("memory-total", 25769803776.0)
        assert dashboard._vram_row.get_subtitle() == "8.0 / 24.0 GB"

    def test_power_subtitle_format(self, dashboard, gpu_state):
        gpu_state.set_property("power-draw", 185000.0)  # milliwatts from NVML
        assert dashboard._power_row.get_subtitle() == "185W"

    def test_gpu_name_row_title(self, dashboard, gpu_state):
        gpu_state.set_property("gpu-name", "NVIDIA GeForce RTX 4090")
        assert dashboard._gpu_name_row.get_title() == "NVIDIA GeForce RTX 4090"

    def test_driver_row_subtitle(self, dashboard, gpu_state):
        gpu_state.set_property("driver-version", "560.35")
        gpu_state.set_property("driver-type", "proprietary")
        assert dashboard._driver_row.get_subtitle() == "560.35 (proprietary)"

    def test_health_good_label(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert dashboard._health_indicator.get_label() == "All Systems Normal"

    def test_health_warn_label(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", TEMP_WARN)
        assert dashboard._health_indicator.get_label() == "Attention Needed"

    def test_health_crit_label(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", TEMP_CRIT)
        assert dashboard._health_indicator.get_label() == "Critical"

    def test_health_icon_changes(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert dashboard._health_icon.get_icon_name() == "emblem-ok-symbolic"
        gpu_state.set_property("temperature", TEMP_CRIT)
        assert dashboard._health_icon.get_icon_name() == "dialog-error-symbolic"

    def test_health_round_trip_crit_to_good(self, dashboard, gpu_state):
        # Drive to crit
        gpu_state.set_property("temperature", TEMP_CRIT)
        assert dashboard._health_indicator.get_label() == "Critical"
        assert dashboard._health_icon.get_icon_name() == "dialog-error-symbolic"
        assert dashboard._health_indicator.has_css_class("verde-status-crit")
        # Recover to good
        gpu_state.set_property("temperature", 50)
        gpu_state.set_property("utilization", 30)
        gpu_state.set_property("power-draw", 100.0)
        gpu_state.set_property("power-limit", 300.0)
        gpu_state.set_property("memory-used", 4e9)
        gpu_state.set_property("memory-total", 24e9)
        assert dashboard._health_indicator.get_label() == "All Systems Normal"
        assert dashboard._health_icon.get_icon_name() == "emblem-ok-symbolic"
        assert dashboard._health_indicator.has_css_class("verde-status-good")
        assert not dashboard._health_indicator.has_css_class("verde-status-crit")

    def test_temp_indicator_css_good(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", 50)
        assert dashboard._temp_indicator.has_css_class("verde-status-good")

    def test_temp_indicator_css_warn(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", TEMP_WARN)
        assert dashboard._temp_indicator.has_css_class("verde-status-warn")

    def test_temp_indicator_css_crit(self, dashboard, gpu_state):
        gpu_state.set_property("temperature", TEMP_CRIT)
        assert dashboard._temp_indicator.has_css_class("verde-status-crit")


# ===================================================================
# Degraded state display (Story 1.10)
# ===================================================================


class TestDegradedStateDisplay:
    @pytest.fixture
    def dashboard(self, gpu_state):
        page = DashboardPage()
        page.bind_state(gpu_state)
        return page

    def test_initial_view_is_monitoring(self, dashboard):
        assert dashboard._current_view == "monitoring"

    def test_degraded_state_switches_to_degraded_view(self, dashboard, gpu_state):
        gpu_state.set_property("degraded-state", "no_gpu")
        assert dashboard._current_view == "degraded"
        assert dashboard._degraded_group is not None

    def test_normal_state_restores_monitoring_view(self, dashboard, gpu_state):
        gpu_state.set_property("degraded-state", "no_gpu")
        assert dashboard._current_view == "degraded"
        gpu_state.set_property("degraded-state", "normal")
        assert dashboard._current_view == "monitoring"
        assert dashboard._degraded_group is None

    def test_all_degraded_states_display(self, dashboard, gpu_state):
        for state in (
            "no_gpu",
            "nouveau_active",
            "no_driver",
            "daemon_unreachable",
            "gpu_lost",
            "nvml_unavailable",
        ):
            gpu_state.set_property("degraded-state", state)
            assert dashboard._current_view == "degraded"

    def test_unknown_degraded_state_shows_monitoring(self, dashboard, gpu_state):
        gpu_state.set_property("degraded-state", "unknown")
        assert dashboard._current_view == "monitoring"

    def test_empty_degraded_state_shows_monitoring(self, dashboard, gpu_state):
        gpu_state.set_property("degraded-state", "no_gpu")
        gpu_state.set_property("degraded-state", "")
        assert dashboard._current_view == "monitoring"

    def test_switching_between_degraded_states(self, dashboard, gpu_state):
        gpu_state.set_property("degraded-state", "no_gpu")
        first_group = dashboard._degraded_group
        gpu_state.set_property("degraded-state", "no_driver")
        assert dashboard._current_view == "degraded"
        # Group should be replaced
        assert dashboard._degraded_group is not first_group

    def test_unrecognized_degraded_state_falls_back_to_monitoring(self, dashboard, gpu_state):
        gpu_state.set_property("degraded-state", "some_future_state")
        # Unknown states fall back to monitoring view (P-4 fix)
        assert dashboard._current_view == "monitoring"

    def test_unrecognized_state_after_degraded_restores_monitoring(self, dashboard, gpu_state):
        gpu_state.set_property("degraded-state", "no_gpu")
        assert dashboard._current_view == "degraded"
        gpu_state.set_property("degraded-state", "some_future_state")
        assert dashboard._current_view == "monitoring"
        assert dashboard._degraded_group is None


# ===================================================================
# Unavailable state display (Story 1.10)
# ===================================================================


class TestUnavailableDisplay:
    @pytest.fixture
    def dashboard(self, gpu_state):
        page = DashboardPage()
        page.bind_state(gpu_state)
        return page

    def test_gpu_unavailable_sets_stats_unavailable(self, dashboard, gpu_state):
        gpu_state.set_property("gpu-available", False)
        assert dashboard._temp_row.get_subtitle() == "Unavailable"
        assert dashboard._util_row.get_subtitle() == "Unavailable"
        assert dashboard._vram_row.get_subtitle() == "Unavailable"
        assert dashboard._power_row.get_subtitle() == "Unavailable"

    def test_gpu_unavailable_health_indicator(self, dashboard, gpu_state):
        gpu_state.set_property("gpu-available", False)
        assert dashboard._health_indicator.get_label() == "Unavailable"
        assert dashboard._health_icon.get_icon_name() == "content-loading-symbolic"

    def test_gpu_unavailable_stat_indicators(self, dashboard, gpu_state):
        gpu_state.set_property("gpu-available", False)
        assert dashboard._temp_indicator.get_label() == "Unavailable"
        assert dashboard._util_indicator.get_label() == "Unavailable"
        assert dashboard._vram_indicator.get_label() == "Unavailable"
        assert dashboard._power_indicator.get_label() == "Unavailable"

    def test_gpu_available_after_unavailable_recovers(self, dashboard, gpu_state):
        gpu_state.set_property("gpu-available", False)
        assert dashboard._temp_row.get_subtitle() == "Unavailable"
        # Simulate recovery: new data arrives
        gpu_state.set_property("gpu-available", True)
        gpu_state.set_property("temperature", 55)
        assert dashboard._temp_row.get_subtitle() == "55°C"


# ===================================================================
# Advanced details progressive disclosure (Story 1.11)
# ===================================================================


class TestAdvancedDetailsExpander:
    @pytest.fixture
    def dashboard(self, gpu_state):
        page = DashboardPage()
        page.bind_state(gpu_state)
        return page

    def test_expander_collapsed_by_default(self, dashboard):
        assert dashboard._advanced_expander.get_expanded() is False

    def test_expander_exists_in_monitoring_groups(self, dashboard):
        assert dashboard._advanced_group in dashboard._monitoring_groups

    def test_cores_row_shows_value(self, dashboard, gpu_state):
        gpu_state.set_property("num-cores", 16384)
        assert dashboard._cores_row.get_subtitle() == "16384"

    def test_cores_row_unavailable_when_zero(self, dashboard, gpu_state):
        gpu_state.set_property("num-cores", 0)
        assert dashboard._cores_row.get_subtitle() == "Unavailable"

    def test_compute_capability_row(self, dashboard, gpu_state):
        gpu_state.set_property("compute-capability", "8.9")
        assert dashboard._compute_cap_row.get_subtitle() == "8.9"

    def test_compute_capability_unavailable(self, dashboard, gpu_state):
        gpu_state.set_property("compute-capability", "")
        assert dashboard._compute_cap_row.get_subtitle() == "Unavailable"

    def test_power_limit_row(self, dashboard, gpu_state):
        gpu_state.set_property("power-limit", 450000.0)  # milliwatts from NVML
        assert dashboard._power_limit_row.get_subtitle() == "450W"

    def test_power_limit_unavailable(self, dashboard, gpu_state):
        gpu_state.set_property("power-limit", 0.0)
        assert dashboard._power_limit_row.get_subtitle() == "Unavailable"

    def test_cuda_driver_version_row(self, dashboard, gpu_state):
        gpu_state.set_property("cuda-driver-version", "12.6")
        assert dashboard._cuda_driver_row.get_subtitle() == "Up to CUDA 12.6"

    def test_cuda_driver_version_unavailable(self, dashboard, gpu_state):
        gpu_state.set_property("cuda-driver-version", "")
        assert dashboard._cuda_driver_row.get_subtitle() == "Unavailable"

    def test_cuda_toolkit_version_row(self, dashboard, gpu_state):
        gpu_state.set_property("cuda-toolkit-version", "12.4")
        assert dashboard._cuda_toolkit_row.get_subtitle() == "CUDA Toolkit 12.4"

    def test_cuda_toolkit_not_installed(self, dashboard, gpu_state):
        gpu_state.set_property("cuda-toolkit-version", "")
        assert dashboard._cuda_toolkit_row.get_subtitle() == "Not installed"

    def test_gpu_mode_hidden_by_default(self, dashboard):
        assert dashboard._gpu_mode_row.get_visible() is False

    def test_gpu_mode_shown_when_set(self, dashboard, gpu_state):
        gpu_state.set_property("gpu-mode", "on-demand")
        assert dashboard._gpu_mode_row.get_visible() is True
        assert dashboard._gpu_mode_row.get_subtitle() == "on-demand"

    def test_gpu_mode_hidden_when_cleared(self, dashboard, gpu_state):
        gpu_state.set_property("gpu-mode", "nvidia")
        assert dashboard._gpu_mode_row.get_visible() is True
        gpu_state.set_property("gpu-mode", "")
        assert dashboard._gpu_mode_row.get_visible() is False

    def test_ecc_hidden_by_default(self, dashboard):
        assert dashboard._ecc_row.get_visible() is False

    def test_ecc_shown_when_enabled(self, dashboard, gpu_state):
        gpu_state.set_property("ecc-mode", True)
        gpu_state.set_property("memory-errors", 3)
        assert dashboard._ecc_row.get_visible() is True
        assert "3 errors" in dashboard._ecc_row.get_subtitle()

    def test_ecc_hidden_when_disabled(self, dashboard, gpu_state):
        gpu_state.set_property("ecc-mode", True)
        assert dashboard._ecc_row.get_visible() is True
        gpu_state.set_property("ecc-mode", False)
        assert dashboard._ecc_row.get_visible() is False

    def test_throttle_hidden_by_default(self, dashboard):
        assert dashboard._throttle_row.get_visible() is False

    def test_throttle_shown_when_active(self, dashboard, gpu_state):
        gpu_state.set_property("throttle-reasons", "Software power cap")
        assert dashboard._throttle_row.get_visible() is True
        assert dashboard._throttle_row.get_subtitle() == "Software power cap"

    def test_throttle_hidden_when_cleared(self, dashboard, gpu_state):
        gpu_state.set_property("throttle-reasons", "Hardware slowdown (thermal/power)")
        assert dashboard._throttle_row.get_visible() is True
        gpu_state.set_property("throttle-reasons", "")
        assert dashboard._throttle_row.get_visible() is False

    def test_multi_gpu_hidden_when_single(self, dashboard, gpu_state):
        gpu_state.set_property("device-count", 1)
        assert dashboard._multi_gpu_row.get_visible() is False

    def test_multi_gpu_shown_when_multiple(self, dashboard, gpu_state):
        gpu_state.set_property("device-count", 3)
        assert dashboard._multi_gpu_row.get_visible() is True
        assert "2 additional GPUs detected" in dashboard._multi_gpu_row.get_subtitle()

    def test_multi_gpu_singular(self, dashboard, gpu_state):
        gpu_state.set_property("device-count", 2)
        assert "1 additional GPU detected" in dashboard._multi_gpu_row.get_subtitle()

    def test_multi_gpu_shows_device_names(self, dashboard, gpu_state):
        gpu_state._devices = [
            {"index": 0, "name": "RTX 4090", "bus_id": "0000:01:00.0"},
            {"index": 1, "name": "RTX 4080", "bus_id": "0000:02:00.0"},
        ]
        gpu_state.set_property("device-count", 2)
        subtitle = dashboard._multi_gpu_row.get_subtitle()
        assert "RTX 4080" in subtitle
        assert "0000:02:00.0" in subtitle

    def test_multi_gpu_hidden_when_zero(self, dashboard, gpu_state):
        gpu_state.set_property("device-count", 0)
        assert dashboard._multi_gpu_row.get_visible() is False

    def test_pcie_bus_id_row_shows_value(self, dashboard, gpu_state):
        gpu_state.set_property("pci-bus-id", "0000:01:00.0")
        assert dashboard._pcie_bus_id_row.get_subtitle() == "0000:01:00.0"

    def test_pcie_bus_id_unavailable(self, dashboard, gpu_state):
        gpu_state.set_property("pci-bus-id", "")
        assert dashboard._pcie_bus_id_row.get_subtitle() == "Unavailable"


# ===================================================================
# Process list display (Story 1.11 P-3)
# ===================================================================


class TestProcessDisplay:
    @pytest.fixture
    def dashboard(self, gpu_state):
        page = DashboardPage()
        page.bind_state(gpu_state)
        return page

    def test_no_processes_shows_no_processes(self, dashboard):
        assert dashboard._process_expander.get_subtitle() == "No processes"
        assert len(dashboard._process_rows) == 0

    def test_process_with_sm_util(self, dashboard, gpu_state):
        gpu_state.update_from_dict(
            {
                "processes": [
                    {"pid": 1234, "used_gpu_memory": 536870912, "type": "compute", "sm_util": 42}
                ]
            }
        )
        # Flush the idle_add
        from gi.repository import GLib

        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        assert len(dashboard._process_rows) == 1
        assert "PID 1234" in dashboard._process_rows[0].get_title()
        assert "GPU: 42%" in dashboard._process_rows[0].get_subtitle()
        assert "VRAM: 512 MB" in dashboard._process_rows[0].get_subtitle()

    def test_process_without_sm_util(self, dashboard, gpu_state):
        gpu_state.update_from_dict(
            {"processes": [{"pid": 5678, "used_gpu_memory": 1073741824, "type": "graphics"}]}
        )
        from gi.repository import GLib

        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        assert len(dashboard._process_rows) == 1
        assert "GPU: N/A" in dashboard._process_rows[0].get_subtitle()

    def test_multiple_processes(self, dashboard, gpu_state):
        gpu_state.update_from_dict(
            {
                "processes": [
                    {"pid": 100, "used_gpu_memory": 100000000, "type": "compute", "sm_util": 10},
                    {"pid": 200, "used_gpu_memory": 200000000, "type": "graphics", "sm_util": 20},
                ]
            }
        )
        from gi.repository import GLib

        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        assert len(dashboard._process_rows) == 2
        assert dashboard._process_expander.get_subtitle() == "2 processes"

    def test_single_process_singular_label(self, dashboard, gpu_state):
        gpu_state.update_from_dict(
            {
                "processes": [
                    {"pid": 100, "used_gpu_memory": 100000000, "type": "compute", "sm_util": 10}
                ]
            }
        )
        from gi.repository import GLib

        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        assert dashboard._process_expander.get_subtitle() == "1 process"

    def test_process_rows_cleared_on_update(self, dashboard, gpu_state):
        gpu_state.update_from_dict(
            {
                "processes": [
                    {"pid": 100, "used_gpu_memory": 100000000, "type": "compute", "sm_util": 10},
                    {"pid": 200, "used_gpu_memory": 200000000, "type": "graphics", "sm_util": 20},
                ]
            }
        )
        from gi.repository import GLib

        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        assert len(dashboard._process_rows) == 2
        # Update with fewer processes
        gpu_state.update_from_dict(
            {
                "processes": [
                    {"pid": 300, "used_gpu_memory": 300000000, "type": "compute", "sm_util": 30}
                ]
            }
        )
        while ctx.pending():
            ctx.iteration(False)
        assert len(dashboard._process_rows) == 1

    def test_process_type_in_title(self, dashboard, gpu_state):
        gpu_state.update_from_dict(
            {"processes": [{"pid": 1234, "used_gpu_memory": 0, "type": "compute", "sm_util": 0}]}
        )
        from gi.repository import GLib

        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        assert "(compute)" in dashboard._process_rows[0].get_title()
