"""Unit tests for Story 1.11 — D-Bus response formatting for advanced fields.

Tests: gpu_mode, cuda_toolkit_version, throttle_reasons_decoded,
device enumeration, and per-process sm_util in D-Bus responses.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest
from nvml_wrapper import Unavailable
from service import VerdeService

_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_nvml():
    """Mock NvmlWrapper with advanced monitoring fields."""
    nvml = MagicMock()
    nvml.initialize.return_value = True
    nvml._initialized = True
    nvml.device_count.return_value = 1
    nvml.get_device_name.return_value = "NVIDIA GeForce RTX 4090"
    nvml.get_device_uuid.return_value = "GPU-12345678-abcd-efgh"
    nvml.get_driver_version.return_value = "560.35.03"
    nvml.get_cuda_driver_version.return_value = "12.6"
    nvml.get_all_gpu_info.return_value = {
        "name": "NVIDIA GeForce RTX 4090",
        "uuid": "GPU-12345678-abcd-efgh",
        "driver_version": "560.35.03",
        "cuda_driver_version": "12.6",
        "cuda_toolkit_version": "12.4",
        "gpu_mode": "nvidia",
        "pci_info": {"bus_id": "0000:01:00.0", "domain": 0, "bus": 1, "device": 0},
        "num_cores": 16384,
        "compute_capability": (8, 9),
        "ecc_mode": False,
    }
    nvml.get_all_gpu_stats.return_value = {
        "temperature": 65,
        "clock_graphics": 2100,
        "clock_sm": 2100,
        "clock_mem": 10501,
        "memory": {"total": 25769803776, "used": 4294967296, "free": 21474836480},
        "utilization": {"gpu": 45, "memory": 30},
        "power_usage": 320000,
        "power_limit": 450000,
        "performance_state": 0,
        "throttle_reasons": 0x4,  # SW_POWER_CAP
        "throttle_reasons_decoded": ["Software power cap"],
        "processes": [
            {"pid": 1234, "used_gpu_memory": 1073741824, "type": "compute", "sm_util": 42}
        ],
        "memory_errors": 0,
    }
    nvml.get_device_by_index.return_value = MagicMock()
    nvml.get_pci_info.return_value = {
        "bus_id": "0000:01:00.0",
        "domain": 0,
        "bus": 1,
        "device": 0,
    }
    nvml.get_num_gpu_cores.return_value = 16384
    nvml.get_cuda_compute_capability.return_value = (8, 9)
    nvml.get_ecc_mode.return_value = False
    nvml.shutdown.return_value = None
    return nvml


@pytest.fixture
def mock_loop():
    return MagicMock()


@pytest.fixture
def idle_reset():
    return MagicMock()


@pytest.fixture
def service(mock_loop, idle_reset, mock_nvml):
    return VerdeService(
        loop=mock_loop,
        on_idle_reset=idle_reset,
        introspection_xml=_XML,
        nvml=mock_nvml,
    )


@pytest.fixture
def mock_invocation():
    return MagicMock()


@pytest.fixture
def mock_connection():
    conn = MagicMock()
    conn.register_object.return_value = 42
    return conn


def _call_method(service, mock_connection, mock_invocation, method_name):
    service._on_bus_acquired(mock_connection, "com.verde.Manager")
    with patch("service.check_authorization", return_value=True):
        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            method_name,
            None,
            mock_invocation,
        )


# ===================================================================
# GetGPUInfo — advanced fields
# ===================================================================


class TestGetGPUInfoAdvanced:
    def test_includes_gpu_mode(self, service, mock_connection, mock_invocation):
        _call_method(service, mock_connection, mock_invocation, "GetGPUInfo")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["gpu_mode"] == "nvidia"

    def test_includes_cuda_toolkit_version(self, service, mock_connection, mock_invocation):
        _call_method(service, mock_connection, mock_invocation, "GetGPUInfo")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["cuda_toolkit_version"] == "12.4"

    def test_gpu_mode_unavailable(self, service, mock_nvml, mock_connection, mock_invocation):
        info = mock_nvml.get_all_gpu_info.return_value.copy()
        info["gpu_mode"] = Unavailable
        mock_nvml.get_all_gpu_info.return_value = info
        _call_method(service, mock_connection, mock_invocation, "GetGPUInfo")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["gpu_mode_available"] is False
        assert "gpu_mode" not in result

    def test_cuda_toolkit_unavailable(self, service, mock_nvml, mock_connection, mock_invocation):
        info = mock_nvml.get_all_gpu_info.return_value.copy()
        info["cuda_toolkit_version"] = Unavailable
        mock_nvml.get_all_gpu_info.return_value = info
        _call_method(service, mock_connection, mock_invocation, "GetGPUInfo")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["cuda_toolkit_version_available"] is False

    def test_multi_gpu_devices_array(self, service, mock_nvml, mock_connection, mock_invocation):
        mock_nvml.device_count.return_value = 2
        mock_nvml.get_device_by_index.return_value = MagicMock()
        mock_nvml.get_device_name.return_value = "GPU-0"
        mock_nvml.get_device_uuid.return_value = "GPU-UUID-0"
        mock_nvml.get_pci_info.return_value = {
            "bus_id": "0000:01:00.0",
            "domain": 0,
            "bus": 1,
            "device": 0,
        }
        _call_method(service, mock_connection, mock_invocation, "GetGPUInfo")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["device_count"] == 2
        assert "devices" in result
        assert len(result["devices"]) == 2

    def test_single_gpu_no_devices_array(self, service, mock_connection, mock_invocation):
        _call_method(service, mock_connection, mock_invocation, "GetGPUInfo")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["device_count"] == 1
        assert "devices" in result
        assert len(result["devices"]) == 1


# ===================================================================
# GetGPUStats — advanced fields
# ===================================================================


class TestGetGPUStatsAdvanced:
    def test_includes_throttle_reasons_decoded(self, service, mock_connection, mock_invocation):
        _call_method(service, mock_connection, mock_invocation, "GetGPUStats")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert "throttle_reasons_decoded" in result
        assert "Software power cap" in result["throttle_reasons_decoded"]

    def test_throttle_reasons_decoded_unavailable(
        self, service, mock_nvml, mock_connection, mock_invocation
    ):
        stats = mock_nvml.get_all_gpu_stats.return_value.copy()
        stats["throttle_reasons_decoded"] = Unavailable
        mock_nvml.get_all_gpu_stats.return_value = stats
        _call_method(service, mock_connection, mock_invocation, "GetGPUStats")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["throttle_reasons_decoded_available"] is False

    def test_process_sm_util_present(self, service, mock_connection, mock_invocation):
        _call_method(service, mock_connection, mock_invocation, "GetGPUStats")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        procs = result["processes"]
        assert len(procs) == 1
        assert procs[0]["sm_util"] == 42

    def test_process_sm_util_unavailable(
        self, service, mock_nvml, mock_connection, mock_invocation
    ):
        stats = mock_nvml.get_all_gpu_stats.return_value.copy()
        stats["processes"] = [
            {
                "pid": 1234,
                "used_gpu_memory": 1073741824,
                "type": "compute",
                "sm_util": Unavailable,
            }
        ]
        mock_nvml.get_all_gpu_stats.return_value = stats
        _call_method(service, mock_connection, mock_invocation, "GetGPUStats")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        procs = result["processes"]
        assert procs[0]["sm_util_available"] is False
        assert "sm_util" not in procs[0]

    def test_empty_throttle_reasons_is_empty_array(
        self, service, mock_nvml, mock_connection, mock_invocation
    ):
        stats = mock_nvml.get_all_gpu_stats.return_value.copy()
        stats["throttle_reasons"] = 0
        stats["throttle_reasons_decoded"] = []
        mock_nvml.get_all_gpu_stats.return_value = stats
        _call_method(service, mock_connection, mock_invocation, "GetGPUStats")
        result = mock_invocation.return_value.call_args[0][0].get_child_value(0).unpack()
        assert result["throttle_reasons_decoded"] == []
