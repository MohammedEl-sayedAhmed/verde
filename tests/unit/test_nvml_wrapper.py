"""Unit tests for the NVML ctypes wrapper."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add daemon source to path so we can import nvml_wrapper directly.
DAEMON_SRC = Path(__file__).resolve().parents[2] / "src" / "verde-daemon"
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

from nvml_wrapper import (  # noqa: E402
    NVML_CLOCK_GRAPHICS,
    NVML_ERROR_GPU_IS_LOST,
    NVML_ERROR_NOT_FOUND,
    NVML_ERROR_NOT_SUPPORTED,
    NVML_ERROR_UNKNOWN,
    NVML_SUCCESS,
    NvmlWrapper,
    Unavailable,
    _Unavailable,
)

# ---------------------------------------------------------------------------
# Helpers to build a mock NVML library
# ---------------------------------------------------------------------------


def _make_mock_lib() -> MagicMock:
    """Return a MagicMock that behaves like a loaded libnvidia-ml.so.1."""
    lib = MagicMock()

    # nvmlInit_v2 / nvmlShutdown — just succeed
    lib.nvmlInit_v2.return_value = NVML_SUCCESS
    lib.nvmlShutdown.return_value = NVML_SUCCESS

    # Device count
    def _get_count(p_count):
        p_count._obj.value = 1
        return NVML_SUCCESS

    lib.nvmlDeviceGetCount_v2.side_effect = _get_count

    # Device handle by index
    def _get_handle(idx, p_handle):
        p_handle._obj.value = 0xDEAD
        return NVML_SUCCESS

    lib.nvmlDeviceGetHandleByIndex_v2.side_effect = _get_handle

    # Device name
    def _get_name(handle, buf, length):
        name = b"NVIDIA GeForce RTX 4090"
        ctypes.memmove(buf, name, len(name) + 1)
        return NVML_SUCCESS

    lib.nvmlDeviceGetName.side_effect = _get_name

    # Device UUID
    def _get_uuid(handle, buf, length):
        uuid = b"GPU-12345678-abcd-1234-abcd-123456789abc"
        ctypes.memmove(buf, uuid, len(uuid) + 1)
        return NVML_SUCCESS

    lib.nvmlDeviceGetUUID.side_effect = _get_uuid

    # Driver version
    def _get_driver(buf, length):
        ver = b"550.54.14"
        ctypes.memmove(buf, ver, len(ver) + 1)
        return NVML_SUCCESS

    lib.nvmlSystemGetDriverVersion.side_effect = _get_driver

    # CUDA driver version (12.4 = 12040)
    def _get_cuda_ver(p_ver):
        p_ver._obj.value = 12040
        return NVML_SUCCESS

    lib.nvmlSystemGetCudaDriverVersion_v2.side_effect = _get_cuda_ver

    # Temperature
    def _get_temp(handle, sensor, p_temp):
        p_temp._obj.value = 42
        return NVML_SUCCESS

    lib.nvmlDeviceGetTemperature.side_effect = _get_temp

    # Clock info
    def _get_clock(handle, clock_type, p_clock):
        clocks = {0: 2100, 1: 2100, 2: 10501}
        p_clock._obj.value = clocks.get(
            clock_type.value if hasattr(clock_type, "value") else clock_type, 0
        )
        return NVML_SUCCESS

    lib.nvmlDeviceGetClockInfo.side_effect = _get_clock

    # Performance state
    def _get_pstate(handle, p_pstate):
        p_pstate._obj.value = 0  # P0
        return NVML_SUCCESS

    lib.nvmlDeviceGetPerformanceState.side_effect = _get_pstate

    # Memory info
    def _get_mem(handle, p_mem):
        mem = p_mem._obj
        mem.total = 24_000_000_000
        mem.used = 4_000_000_000
        mem.free = 20_000_000_000
        return NVML_SUCCESS

    lib.nvmlDeviceGetMemoryInfo.side_effect = _get_mem

    # Utilization rates
    def _get_util(handle, p_util):
        util = p_util._obj
        util.gpu = 35
        util.memory = 22
        return NVML_SUCCESS

    lib.nvmlDeviceGetUtilizationRates.side_effect = _get_util

    # Power usage (milliwatts)
    def _get_power(handle, p_power):
        p_power._obj.value = 150_000
        return NVML_SUCCESS

    lib.nvmlDeviceGetPowerUsage.side_effect = _get_power

    # Power limit
    def _get_power_limit(handle, p_limit):
        p_limit._obj.value = 450_000
        return NVML_SUCCESS

    lib.nvmlDeviceGetEnforcedPowerLimit.side_effect = _get_power_limit

    # PCI info
    def _get_pci(handle, p_pci):
        pci = p_pci._obj
        pci.busId = b"0000:01:00.0"
        pci.domain = 0
        pci.bus = 1
        pci.device = 0
        return NVML_SUCCESS

    lib.nvmlDeviceGetPciInfo_v3.side_effect = _get_pci

    # CUDA compute capability
    def _get_cc(handle, p_major, p_minor):
        p_major._obj.value = 8
        p_minor._obj.value = 9
        return NVML_SUCCESS

    lib.nvmlDeviceGetCudaComputeCapability.side_effect = _get_cc

    # GPU cores
    def _get_cores(handle, p_cores):
        p_cores._obj.value = 16384
        return NVML_SUCCESS

    lib.nvmlDeviceGetNumGpuCores.side_effect = _get_cores

    # ECC mode
    def _get_ecc(handle, p_current, p_pending):
        p_current._obj.value = 0
        p_pending._obj.value = 0
        return NVML_SUCCESS

    lib.nvmlDeviceGetEccMode.side_effect = _get_ecc

    # Memory error counter
    def _get_mem_err(handle, err_type, counter_type, loc_type, p_count):
        p_count._obj.value = 0
        return NVML_SUCCESS

    lib.nvmlDeviceGetMemoryErrorCounter.side_effect = _get_mem_err

    # Throttle reasons
    def _get_throttle(handle, p_reasons):
        p_reasons._obj.value = 0
        return NVML_SUCCESS

    lib.nvmlDeviceGetCurrentThrottleReasons.side_effect = _get_throttle

    # Running processes (compute + graphics)
    def _get_procs(handle, p_count, arr):
        p_count._obj.value = 1
        arr[0].pid = 1234
        arr[0].usedGpuMemory = 500_000_000
        return NVML_SUCCESS

    lib.nvmlDeviceGetComputeRunningProcesses_v3.side_effect = _get_procs
    lib.nvmlDeviceGetGraphicsRunningProcesses_v3.side_effect = _get_procs

    return lib


@pytest.fixture
def mock_nvml():
    """Provide an NvmlWrapper backed by a mock library."""
    lib = _make_mock_lib()
    with patch("nvml_wrapper.ctypes.CDLL", return_value=lib):
        wrapper = NvmlWrapper()
        wrapper.initialize()
        yield wrapper, lib


@pytest.fixture
def mock_handle():
    """A fake device handle."""
    return ctypes.c_void_p(0xDEAD)


# ===================================================================
# Task 7: Successful query tests
# ===================================================================


class TestInitialization:
    def test_initialize_succeeds(self, mock_nvml):
        wrapper, lib = mock_nvml
        assert wrapper._initialized is True
        lib.nvmlInit_v2.assert_called_once()

    def test_device_count(self, mock_nvml):
        wrapper, _ = mock_nvml
        assert wrapper.device_count() == 1

    def test_get_device_name(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        name = wrapper.get_device_name(mock_handle)
        assert isinstance(name, str)
        assert "NVIDIA" in name

    def test_get_temperature(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        temp = wrapper.get_temperature(mock_handle)
        assert isinstance(temp, int)
        assert temp == 42

    def test_get_memory_info(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        mem = wrapper.get_memory_info(mock_handle)
        assert isinstance(mem, dict)
        assert "total" in mem
        assert "used" in mem
        assert "free" in mem
        assert mem["total"] == 24_000_000_000

    def test_get_utilization(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        util = wrapper.get_utilization(mock_handle)
        assert isinstance(util, dict)
        assert util["gpu"] == 35
        assert util["memory"] == 22

    def test_get_power_usage(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        power = wrapper.get_power_usage(mock_handle)
        assert isinstance(power, int)
        assert power == 150_000

    def test_get_running_processes(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        procs = wrapper.get_running_processes(mock_handle)
        assert isinstance(procs, list)
        assert len(procs) == 2  # 1 compute + 1 graphics
        assert procs[0]["pid"] == 1234
        assert procs[0]["type"] == "compute"
        assert procs[1]["type"] == "graphics"

    def test_get_all_gpu_info(self, mock_nvml):
        wrapper, _ = mock_nvml
        info = wrapper.get_all_gpu_info(0)
        assert isinstance(info, dict)
        assert "name" in info
        assert "driver_version" in info
        assert "cuda_driver_version" in info
        assert "pci_info" in info
        assert "num_cores" in info
        assert "compute_capability" in info

    def test_get_all_gpu_stats(self, mock_nvml):
        wrapper, _ = mock_nvml
        stats = wrapper.get_all_gpu_stats(0)
        assert isinstance(stats, dict)
        assert "temperature" in stats
        assert "memory" in stats
        assert "utilization" in stats
        assert "power_usage" in stats
        assert "processes" in stats

    def test_get_driver_version(self, mock_nvml):
        wrapper, _ = mock_nvml
        ver = wrapper.get_driver_version()
        assert ver == "550.54.14"

    def test_get_cuda_driver_version(self, mock_nvml):
        wrapper, _ = mock_nvml
        ver = wrapper.get_cuda_driver_version()
        assert ver == "12.4"

    def test_get_clock_info(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        clock = wrapper.get_clock_info(mock_handle, NVML_CLOCK_GRAPHICS)
        assert isinstance(clock, int)

    def test_get_performance_state(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        pstate = wrapper.get_performance_state(mock_handle)
        assert pstate == 0

    def test_get_power_limit(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        limit = wrapper.get_power_limit(mock_handle)
        assert limit == 450_000

    def test_get_pci_info(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        pci = wrapper.get_pci_info(mock_handle)
        assert isinstance(pci, dict)
        assert "bus_id" in pci
        assert pci["domain"] == 0

    def test_get_cuda_compute_capability(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        cc = wrapper.get_cuda_compute_capability(mock_handle)
        assert cc == (8, 9)

    def test_get_num_gpu_cores(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        cores = wrapper.get_num_gpu_cores(mock_handle)
        assert cores == 16384

    def test_get_ecc_mode(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        ecc = wrapper.get_ecc_mode(mock_handle)
        assert ecc is False

    def test_get_memory_error_count(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        count = wrapper.get_memory_error_count(mock_handle)
        assert count == 0

    def test_get_throttle_reasons(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        reasons = wrapper.get_throttle_reasons(mock_handle)
        assert reasons == 0

    def test_get_device_uuid(self, mock_nvml, mock_handle):
        wrapper, _ = mock_nvml
        uuid = wrapper.get_device_uuid(mock_handle)
        assert isinstance(uuid, str)
        assert uuid.startswith("GPU-")


# ===================================================================
# Task 8: Failure and degradation tests
# ===================================================================


class TestFailureDegradation:
    def test_library_load_failure_all_unavailable(self):
        """When libnvidia-ml.so.1 cannot be loaded, all methods return Unavailable."""
        with patch("nvml_wrapper.ctypes.CDLL", side_effect=OSError("not found")):
            wrapper = NvmlWrapper()
        assert wrapper.initialize() is False
        assert wrapper.device_count() is Unavailable
        assert wrapper.get_driver_version() is Unavailable
        assert wrapper.get_cuda_driver_version() is Unavailable

    def test_init_failure_all_unavailable(self):
        """When nvmlInit_v2 fails, all device methods return Unavailable."""
        lib = MagicMock()
        lib.nvmlInit_v2.return_value = NVML_ERROR_UNKNOWN
        with patch("nvml_wrapper.ctypes.CDLL", return_value=lib):
            wrapper = NvmlWrapper()
        assert wrapper.initialize() is False
        assert wrapper._initialized is False

    def test_not_supported_returns_unavailable(self, mock_nvml, mock_handle):
        """Individual function returning NOT_SUPPORTED returns Unavailable."""
        wrapper, lib = mock_nvml
        lib.nvmlDeviceGetTemperature.side_effect = None
        lib.nvmlDeviceGetTemperature.return_value = NVML_ERROR_NOT_SUPPORTED
        assert wrapper.get_temperature(mock_handle) is Unavailable
        # Other functions still work
        assert wrapper.get_power_usage(mock_handle) == 150_000

    def test_gpu_is_lost_returns_unavailable(self, mock_nvml, mock_handle):
        """GPU_IS_LOST error returns Unavailable."""
        wrapper, lib = mock_nvml
        lib.nvmlDeviceGetMemoryInfo.side_effect = None
        lib.nvmlDeviceGetMemoryInfo.return_value = NVML_ERROR_GPU_IS_LOST
        assert wrapper.get_memory_info(mock_handle) is Unavailable

    def test_unavailable_sentinel_is_falsy(self):
        assert bool(Unavailable) is False
        assert not Unavailable

    def test_unavailable_sentinel_repr(self):
        assert repr(Unavailable) == "Unavailable"

    def test_unavailable_is_singleton(self):
        a = _Unavailable()
        b = _Unavailable()
        assert a is b
        assert a is Unavailable

    def test_no_exceptions_escape_public_methods(self):
        """All public methods must return Unavailable, never raise."""
        with patch("nvml_wrapper.ctypes.CDLL", side_effect=OSError):
            wrapper = NvmlWrapper()
        handle = ctypes.c_void_p(0)
        # None of these should raise
        assert wrapper.device_count() is Unavailable
        assert wrapper.get_device_by_index(0) is Unavailable
        assert wrapper.get_device_name(handle) is Unavailable
        assert wrapper.get_device_uuid(handle) is Unavailable
        assert wrapper.get_driver_version() is Unavailable
        assert wrapper.get_cuda_driver_version() is Unavailable
        assert wrapper.get_temperature(handle) is Unavailable
        assert wrapper.get_clock_info(handle, 0) is Unavailable
        assert wrapper.get_performance_state(handle) is Unavailable
        assert wrapper.get_memory_info(handle) is Unavailable
        assert wrapper.get_utilization(handle) is Unavailable
        assert wrapper.get_power_usage(handle) is Unavailable
        assert wrapper.get_power_limit(handle) is Unavailable
        assert wrapper.get_pci_info(handle) is Unavailable
        assert wrapper.get_cuda_compute_capability(handle) is Unavailable
        assert wrapper.get_num_gpu_cores(handle) is Unavailable
        assert wrapper.get_ecc_mode(handle) is Unavailable
        assert wrapper.get_memory_error_count(handle) is Unavailable
        assert wrapper.get_throttle_reasons(handle) is Unavailable
        assert wrapper.get_running_processes(handle) is Unavailable

    def test_aggregation_with_failed_handle(self):
        """get_all_gpu_info/stats returns dict with Unavailable when handle fails."""
        with patch("nvml_wrapper.ctypes.CDLL", side_effect=OSError):
            wrapper = NvmlWrapper()
        info = wrapper.get_all_gpu_info(0)
        assert info["handle"] is Unavailable
        stats = wrapper.get_all_gpu_stats(0)
        assert stats["handle"] is Unavailable

    def test_exception_in_nvml_function_returns_unavailable(self, mock_nvml, mock_handle):
        """If an NVML function raises unexpectedly, _call catches it."""
        wrapper, lib = mock_nvml
        lib.nvmlDeviceGetTemperature.side_effect = RuntimeError("boom")
        assert wrapper.get_temperature(mock_handle) is Unavailable

    def test_missing_nvml_function_returns_unavailable(self, mock_nvml, mock_handle):
        """If NVML function doesn't exist in library, return Unavailable."""
        wrapper, lib = mock_nvml
        # Remove the attribute to simulate missing function
        del lib.nvmlDeviceGetNumGpuCores
        # getattr returns MagicMock by default for MagicMock, so we need spec
        lib.configure_mock(**{"nvmlDeviceGetNumGpuCores": None})
        # Actually need to handle getattr properly — the _call uses getattr with None default
        # Since MagicMock auto-creates attrs, let's test via a real missing scenario
        wrapper._lib = MagicMock(spec=[])  # empty spec means no attrs
        result = wrapper.get_num_gpu_cores(mock_handle)
        assert result is Unavailable


# ===================================================================
# Task 9: Multi-GPU enumeration tests
# ===================================================================


class TestMultiGPU:
    def test_device_count_multi_gpu(self):
        """device_count returns >1 for multi-GPU."""
        lib = _make_mock_lib()

        def _count_2(p):
            p._obj.value = 2
            return NVML_SUCCESS

        lib.nvmlDeviceGetCount_v2.side_effect = _count_2
        with patch("nvml_wrapper.ctypes.CDLL", return_value=lib):
            wrapper = NvmlWrapper()
            wrapper.initialize()
        assert wrapper.device_count() == 2

    def test_different_handles_per_index(self):
        """get_device_by_index(0) and (1) return different handles."""
        lib = _make_mock_lib()
        handles = [0xAAAA, 0xBBBB]

        def _get_handle(idx, p_handle):
            i = idx.value if hasattr(idx, "value") else idx
            p_handle._obj.value = handles[i]
            return NVML_SUCCESS

        lib.nvmlDeviceGetHandleByIndex_v2.side_effect = _get_handle
        with patch("nvml_wrapper.ctypes.CDLL", return_value=lib):
            wrapper = NvmlWrapper()
            wrapper.initialize()
        h0 = wrapper.get_device_by_index(0)
        h1 = wrapper.get_device_by_index(1)
        assert h0.value != h1.value

    def test_get_all_gpu_info_per_index(self):
        """get_all_gpu_info works independently for each GPU."""
        lib = _make_mock_lib()
        names = [b"GPU-0", b"GPU-1"]

        def _get_name(handle, buf, length):
            idx = 0 if handle.value == 0xAAAA else 1
            ctypes.memmove(buf, names[idx], len(names[idx]) + 1)
            return NVML_SUCCESS

        def _get_handle(idx, p_handle):
            i = idx.value if hasattr(idx, "value") else idx
            p_handle._obj.value = [0xAAAA, 0xBBBB][i]
            return NVML_SUCCESS

        lib.nvmlDeviceGetName.side_effect = _get_name
        lib.nvmlDeviceGetHandleByIndex_v2.side_effect = _get_handle

        with patch("nvml_wrapper.ctypes.CDLL", return_value=lib):
            wrapper = NvmlWrapper()
            wrapper.initialize()
        info0 = wrapper.get_all_gpu_info(0)
        info1 = wrapper.get_all_gpu_info(1)
        assert info0["name"] == "GPU-0"
        assert info1["name"] == "GPU-1"

    def test_invalid_index_returns_unavailable(self, mock_nvml):
        """Invalid GPU index returns Unavailable, not an exception."""
        wrapper, lib = mock_nvml
        lib.nvmlDeviceGetHandleByIndex_v2.side_effect = None
        lib.nvmlDeviceGetHandleByIndex_v2.return_value = NVML_ERROR_NOT_FOUND
        result = wrapper.get_device_by_index(99)
        assert result is Unavailable
