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
        gpu_state.set_property("power-draw", 185.0)
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
