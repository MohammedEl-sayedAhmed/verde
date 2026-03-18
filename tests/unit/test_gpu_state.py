"""Unit tests for GPUState GObject model (Story 1.7)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from verde.gpu_state import _KEY_MAP, GPUState

# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def gpu_state():
    return GPUState()


# ===================================================================
# Instantiation and defaults
# ===================================================================


class TestGPUStateDefaults:
    def test_can_instantiate(self, gpu_state):
        assert gpu_state is not None

    def test_default_temperature(self, gpu_state):
        assert gpu_state.get_property("temperature") == 0

    def test_default_gpu_name(self, gpu_state):
        assert gpu_state.get_property("gpu-name") == ""

    def test_default_driver_type(self, gpu_state):
        assert gpu_state.get_property("driver-type") == "unknown"

    def test_no_connected_property(self, gpu_state):
        """GPUState does not own a connected property (lives on DBusClient)."""
        assert gpu_state.find_property("connected") is None

    def test_default_gpu_available(self, gpu_state):
        assert gpu_state.get_property("gpu-available") is False

    def test_default_reboot_required(self, gpu_state):
        assert gpu_state.get_property("reboot-required") is False

    def test_default_power_draw(self, gpu_state):
        assert gpu_state.get_property("power-draw") == 0.0

    def test_gtype_name(self):
        assert GPUState.__gtype_name__ == "GPUState"


# ===================================================================
# update_from_dict
# ===================================================================


class TestUpdateFromDict:
    @patch("verde.gpu_state.GLib.idle_add")
    def test_schedules_via_idle_add(self, mock_idle_add, gpu_state):
        gpu_state.update_from_dict({"temperature": 65})
        mock_idle_add.assert_called_once()
        # First arg should be _do_update method
        assert mock_idle_add.call_args[0][0] == gpu_state._do_update

    @patch("verde.gpu_state.GLib.idle_add")
    def test_copies_dict(self, mock_idle_add, gpu_state):
        """Dict is copied to prevent mutation from caller."""
        data = {"temperature": 65}
        gpu_state.update_from_dict(data)
        passed_data = mock_idle_add.call_args[0][1]
        assert passed_data == data
        assert passed_data is not data  # must be a copy

    def test_do_update_sets_temperature(self, gpu_state):
        gpu_state._do_update({"temperature": 72})
        assert gpu_state.get_property("temperature") == 72

    def test_do_update_sets_multiple_properties(self, gpu_state):
        gpu_state._do_update(
            {
                "temperature": 65,
                "utilization_gpu": 45,
                "memory_used": 4294967296,
                "memory_total": 25769803776,
            }
        )
        assert gpu_state.get_property("temperature") == 65
        assert gpu_state.get_property("utilization") == 45
        assert gpu_state.get_property("memory-used") == 4294967296.0
        assert gpu_state.get_property("memory-total") == 25769803776.0

    def test_do_update_partial_data(self, gpu_state):
        """Only provided fields are updated; others remain default."""
        gpu_state._do_update({"temperature": 80})
        assert gpu_state.get_property("temperature") == 80
        assert gpu_state.get_property("utilization") == 0  # unchanged

    def test_do_update_ignores_unknown_keys(self, gpu_state):
        """Unknown keys are silently ignored for forward compatibility."""
        gpu_state._do_update({"unknown_future_key": 42, "temperature": 55})
        assert gpu_state.get_property("temperature") == 55

    def test_do_update_sets_gpu_available(self, gpu_state):
        gpu_state._do_update({"available": True})
        assert gpu_state.get_property("gpu-available") is True

    def test_do_update_sets_reboot_fields(self, gpu_state):
        gpu_state._do_update(
            {
                "reboot_required": True,
                "reboot_reason": "NVIDIA driver update requires reboot",
            }
        )
        assert gpu_state.get_property("reboot-required") is True
        assert gpu_state.get_property("reboot-reason") == "NVIDIA driver update requires reboot"

    def test_do_update_string_properties(self, gpu_state):
        gpu_state._do_update(
            {
                "name": "NVIDIA GeForce RTX 4090",
                "driver_version": "560.35.03",
                "driver_type": "proprietary",
            }
        )
        assert gpu_state.get_property("gpu-name") == "NVIDIA GeForce RTX 4090"
        assert gpu_state.get_property("driver-version") == "560.35.03"
        assert gpu_state.get_property("driver-type") == "proprietary"

    def test_do_update_float_properties(self, gpu_state):
        gpu_state._do_update(
            {
                "power_usage": 320000,
                "power_limit": 450000,
            }
        )
        # power_usage maps to power-draw (float)
        assert gpu_state.get_property("power-draw") == 320000.0
        assert gpu_state.get_property("power-limit") == 450000.0

    def test_performance_state_not_in_key_map(self):
        """performance_state must not be in _KEY_MAP — handled by special case only."""
        assert "performance_state" not in _KEY_MAP

    def test_do_update_performance_state_formatted(self, gpu_state):
        """Integer performance state is formatted as P-state string."""
        gpu_state._do_update({"performance_state": 0})
        assert gpu_state.get_property("p-state") == "P0"

    def test_do_update_performance_state_p8(self, gpu_state):
        gpu_state._do_update({"performance_state": 8})
        assert gpu_state.get_property("p-state") == "P8"

    def test_do_update_performance_state_single_notify(self, gpu_state):
        """P-state update fires exactly one notify, not two (P-2 regression)."""
        handler = MagicMock()
        gpu_state.connect("notify::p-state", handler)
        gpu_state._do_update({"performance_state": 5})
        handler.assert_called_once()

    def test_do_update_returns_source_remove(self, gpu_state):
        from gi.repository import GLib

        result = gpu_state._do_update({"temperature": 50})
        assert result == GLib.SOURCE_REMOVE


# ===================================================================
# Notify signals
# ===================================================================


class TestNotifySignals:
    def test_notify_fires_on_property_change(self, gpu_state):
        handler = MagicMock()
        gpu_state.connect("notify::temperature", handler)
        gpu_state._do_update({"temperature": 75})
        handler.assert_called_once()

    def test_notify_does_not_fire_when_same_value(self, gpu_state):
        """GObject suppresses notify when value is unchanged."""
        gpu_state._do_update({"temperature": 0})  # set to default
        handler = MagicMock()
        gpu_state.connect("notify::temperature", handler)
        gpu_state._do_update({"temperature": 0})  # same value
        handler.assert_not_called()

    def test_notify_fires_for_string_change(self, gpu_state):
        handler = MagicMock()
        gpu_state.connect("notify::gpu-name", handler)
        gpu_state._do_update({"name": "RTX 4090"})
        handler.assert_called_once()


# ===================================================================
# Reset
# ===================================================================


class TestReset:
    @patch("verde.gpu_state.GLib.idle_add")
    def test_reset_schedules_via_idle_add(self, mock_idle_add, gpu_state):
        gpu_state.reset()
        mock_idle_add.assert_called_once()

    def test_do_reset_returns_source_remove(self, gpu_state):
        from gi.repository import GLib

        result = gpu_state._do_reset()
        assert result == GLib.SOURCE_REMOVE

    def test_do_reset_restores_defaults(self, gpu_state):
        # Set some values
        gpu_state._do_update(
            {
                "temperature": 80,
                "name": "RTX 4090",
                "driver_type": "proprietary",
                "utilization_gpu": 95,
            }
        )
        assert gpu_state.get_property("temperature") == 80

        # Reset
        gpu_state._do_reset()
        assert gpu_state.get_property("temperature") == 0
        assert gpu_state.get_property("gpu-name") == ""
        assert gpu_state.get_property("driver-type") == "unknown"
        assert gpu_state.get_property("utilization") == 0
        assert gpu_state.get_property("gpu-available") is False


# ===================================================================
# PCIe Bus ID and process data (Story 1.11 patches)
# ===================================================================


class TestPciBusIdProperty:
    def test_default_empty(self, gpu_state):
        assert gpu_state.get_property("pci-bus-id") == ""

    def test_update_from_dict(self, gpu_state):
        gpu_state._do_update({"pci_bus_id": "0000:01:00.0"})
        assert gpu_state.get_property("pci-bus-id") == "0000:01:00.0"

    def test_reset_clears(self, gpu_state):
        gpu_state._do_update({"pci_bus_id": "0000:01:00.0"})
        gpu_state._do_reset()
        assert gpu_state.get_property("pci-bus-id") == ""


class TestProcessData:
    def test_default_empty(self, gpu_state):
        assert gpu_state.get_processes() == []
        assert gpu_state.get_property("process-count") == 0

    def test_update_processes(self, gpu_state):
        procs = [
            {"pid": 1234, "used_gpu_memory": 500000000, "type": "compute", "sm_util": 42},
            {"pid": 5678, "used_gpu_memory": 100000000, "type": "graphics", "sm_util": 10},
        ]
        gpu_state._do_update({"processes": procs})
        assert gpu_state.get_property("process-count") == 2
        result = gpu_state.get_processes()
        assert len(result) == 2
        assert result[0]["pid"] == 1234
        assert result[0]["sm_util"] == 42

    def test_reset_clears_processes(self, gpu_state):
        gpu_state._do_update({"processes": [{"pid": 1}]})
        assert gpu_state.get_property("process-count") == 1
        gpu_state._do_reset()
        assert gpu_state.get_processes() == []

    def test_get_processes_returns_copy(self, gpu_state):
        gpu_state._do_update({"processes": [{"pid": 1}]})
        copy = gpu_state.get_processes()
        copy.append({"pid": 2})
        assert len(gpu_state.get_processes()) == 1
