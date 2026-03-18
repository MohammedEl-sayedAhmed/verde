"""Unit tests for Story 1.11 — advanced GPU monitoring.

Tests: per-process utilization, Optimus detection, throttle decoder,
CUDA toolkit version, and process utilization merging.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

DAEMON_SRC = Path(__file__).resolve().parents[2] / "src" / "verde-daemon"
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

from nvml_wrapper import (  # noqa: E402
    NVML_ERROR_INSUFFICIENT_SIZE,
    NVML_ERROR_NOT_SUPPORTED,
    NVML_SUCCESS,
    NVML_THROTTLE_REASON_GPU_IDLE,
    NVML_THROTTLE_REASON_HW_SLOWDOWN,
    NVML_THROTTLE_REASON_HW_THERMAL,
    NVML_THROTTLE_REASON_NONE,
    NVML_THROTTLE_REASON_SW_POWER_CAP,
    NVML_THROTTLE_REASON_SW_THERMAL,
    NVML_THROTTLE_REASON_SYNC_BOOST,
    NvmlWrapper,
    Unavailable,
    decode_throttle_reasons,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_lib() -> MagicMock:
    """Return a minimal mock lib for tests that need an initialized wrapper."""
    lib = MagicMock()
    lib.nvmlInit_v2.return_value = NVML_SUCCESS
    lib.nvmlShutdown.return_value = NVML_SUCCESS

    def _get_count(p_count):
        p_count._obj.value = 1
        return NVML_SUCCESS

    lib.nvmlDeviceGetCount_v2.side_effect = _get_count

    def _get_handle(idx, p_handle):
        p_handle._obj.value = 0xDEAD
        return NVML_SUCCESS

    lib.nvmlDeviceGetHandleByIndex_v2.side_effect = _get_handle

    # Stubs for get_running_processes dependencies
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
    lib = _make_mock_lib()
    with patch("nvml_wrapper.ctypes.CDLL", return_value=lib):
        wrapper = NvmlWrapper()
        wrapper.initialize()
        yield wrapper, lib


@pytest.fixture
def mock_handle():
    return ctypes.c_void_p(0xDEAD)


# ===================================================================
# decode_throttle_reasons
# ===================================================================


class TestDecodeThrottleReasons:
    def test_none_returns_empty(self):
        assert decode_throttle_reasons(NVML_THROTTLE_REASON_NONE) == []

    def test_gpu_idle_returns_empty(self):
        assert decode_throttle_reasons(NVML_THROTTLE_REASON_GPU_IDLE) == []

    def test_single_bit_sw_power_cap(self):
        result = decode_throttle_reasons(NVML_THROTTLE_REASON_SW_POWER_CAP)
        assert result == ["Software power cap"]

    def test_single_bit_hw_slowdown(self):
        result = decode_throttle_reasons(NVML_THROTTLE_REASON_HW_SLOWDOWN)
        assert result == ["Hardware slowdown (thermal/power)"]

    def test_multi_bit_mask(self):
        mask = NVML_THROTTLE_REASON_SW_THERMAL | NVML_THROTTLE_REASON_HW_THERMAL
        result = decode_throttle_reasons(mask)
        assert "Software thermal limit" in result
        assert "Hardware thermal limit" in result
        assert len(result) == 2

    def test_all_bits_set(self):
        mask = (
            NVML_THROTTLE_REASON_SW_POWER_CAP
            | NVML_THROTTLE_REASON_HW_SLOWDOWN
            | NVML_THROTTLE_REASON_SW_THERMAL
            | NVML_THROTTLE_REASON_HW_THERMAL
        )
        result = decode_throttle_reasons(mask)
        assert len(result) == 4

    def test_sync_boost_decoded(self):
        result = decode_throttle_reasons(NVML_THROTTLE_REASON_SYNC_BOOST)
        assert result == ["Sync boost"]

    def test_sync_boost_combined_with_others(self):
        mask = NVML_THROTTLE_REASON_SYNC_BOOST | NVML_THROTTLE_REASON_SW_POWER_CAP
        result = decode_throttle_reasons(mask)
        assert "Sync boost" in result
        assert "Software power cap" in result
        assert len(result) == 2

    def test_gpu_idle_with_other_bits(self):
        """GPU_IDLE combined with real reasons should still decode reasons."""
        mask = NVML_THROTTLE_REASON_GPU_IDLE | NVML_THROTTLE_REASON_SW_POWER_CAP
        result = decode_throttle_reasons(mask)
        assert "Software power cap" in result


# ===================================================================
# get_process_utilization
# ===================================================================


class TestGetProcessUtilization:
    def test_success_returns_samples(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml

        def _get_util(handle, arr, p_count, ts):
            p_count._obj.value = 1
            arr[0].pid = 1234
            arr[0].timeStamp = 100
            arr[0].smUtil = 42
            arr[0].memUtil = 10
            arr[0].encUtil = 0
            arr[0].decUtil = 0
            return NVML_SUCCESS

        lib.nvmlDeviceGetProcessUtilization.side_effect = _get_util
        result = wrapper.get_process_utilization(mock_handle)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["pid"] == 1234
        assert result[0]["sm_util"] == 42
        assert result[0]["mem_util"] == 10

    def test_not_supported_returns_unavailable(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml
        lib.nvmlDeviceGetProcessUtilization.side_effect = None
        lib.nvmlDeviceGetProcessUtilization.return_value = NVML_ERROR_NOT_SUPPORTED
        assert wrapper.get_process_utilization(mock_handle) is Unavailable

    def test_insufficient_size_retry(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml
        call_count = [0]

        def _get_util(handle, arr, p_count, ts):
            call_count[0] += 1
            if call_count[0] == 1:
                p_count._obj.value = 64  # need bigger buffer
                return NVML_ERROR_INSUFFICIENT_SIZE
            # Second call succeeds
            p_count._obj.value = 1
            arr[0].pid = 5678
            arr[0].timeStamp = 200
            arr[0].smUtil = 80
            arr[0].memUtil = 20
            arr[0].encUtil = 0
            arr[0].decUtil = 0
            return NVML_SUCCESS

        lib.nvmlDeviceGetProcessUtilization.side_effect = _get_util
        result = wrapper.get_process_utilization(mock_handle)
        assert isinstance(result, list)
        assert result[0]["pid"] == 5678
        assert result[0]["sm_util"] == 80
        assert call_count[0] == 2

    def test_updates_last_timestamp(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml
        assert wrapper._last_process_util_timestamp == 0

        def _get_util(handle, arr, p_count, ts):
            p_count._obj.value = 1
            arr[0].pid = 1234
            arr[0].timeStamp = 999
            arr[0].smUtil = 10
            arr[0].memUtil = 5
            arr[0].encUtil = 0
            arr[0].decUtil = 0
            return NVML_SUCCESS

        lib.nvmlDeviceGetProcessUtilization.side_effect = _get_util
        wrapper.get_process_utilization(mock_handle)
        assert wrapper._last_process_util_timestamp == 999


# ===================================================================
# get_running_processes merges sm_util
# ===================================================================


class TestProcessUtilMerge:
    def test_merges_sm_util_when_available(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml

        def _get_util(handle, arr, p_count, ts):
            p_count._obj.value = 1
            arr[0].pid = 1234
            arr[0].timeStamp = 100
            arr[0].smUtil = 55
            arr[0].memUtil = 10
            arr[0].encUtil = 0
            arr[0].decUtil = 0
            return NVML_SUCCESS

        lib.nvmlDeviceGetProcessUtilization.side_effect = _get_util
        procs = wrapper.get_running_processes(mock_handle)
        assert isinstance(procs, list)
        # Find process with pid 1234
        p = next(p for p in procs if p["pid"] == 1234)
        assert p["sm_util"] == 55

    def test_sm_util_unavailable_when_not_supported(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml
        lib.nvmlDeviceGetProcessUtilization.side_effect = None
        lib.nvmlDeviceGetProcessUtilization.return_value = NVML_ERROR_NOT_SUPPORTED
        procs = wrapper.get_running_processes(mock_handle)
        assert isinstance(procs, list)
        for p in procs:
            assert p["sm_util"] is Unavailable

    def test_sm_util_unavailable_for_unmatched_pid(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml

        def _get_util(handle, arr, p_count, ts):
            # Return utilization for pid 9999 (not in process list)
            p_count._obj.value = 1
            arr[0].pid = 9999
            arr[0].timeStamp = 100
            arr[0].smUtil = 30
            arr[0].memUtil = 10
            arr[0].encUtil = 0
            arr[0].decUtil = 0
            return NVML_SUCCESS

        lib.nvmlDeviceGetProcessUtilization.side_effect = _get_util
        procs = wrapper.get_running_processes(mock_handle)
        # pid 1234 has no matching utilization sample
        p = next(p for p in procs if p["pid"] == 1234)
        assert p["sm_util"] is Unavailable


# ===================================================================
# get_gpu_mode
# ===================================================================


class TestGetGpuMode:
    def test_prime_select_nvidia(self):
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="nvidia\n")
            assert NvmlWrapper.get_gpu_mode() == "nvidia"

    def test_prime_select_intel(self):
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="intel\n")
            assert NvmlWrapper.get_gpu_mode() == "intel"

    def test_prime_select_on_demand(self):
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="on-demand\n")
            assert NvmlWrapper.get_gpu_mode() == "on-demand"

    def test_prime_select_amd(self):
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="amd\n")
            assert NvmlWrapper.get_gpu_mode() == "amd"

    def test_prime_select_not_found_falls_to_sysfs(self):
        with (
            patch("nvml_wrapper.subprocess.run", side_effect=FileNotFoundError),
            patch("os.path.exists", return_value=False),
        ):
            assert NvmlWrapper.get_gpu_mode() is Unavailable

    def test_prime_select_not_found_sysfs_present(self):
        with (
            patch("nvml_wrapper.subprocess.run", side_effect=FileNotFoundError),
            patch("os.path.exists", return_value=True),
        ):
            assert NvmlWrapper.get_gpu_mode() == "hybrid"

    def test_prime_select_unexpected_output(self):
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="garbage\n")
            assert NvmlWrapper.get_gpu_mode() is Unavailable

    def test_prime_select_nonzero_exit(self):
        with (
            patch("nvml_wrapper.subprocess.run") as mock_run,
            patch("os.path.exists", return_value=False),
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert NvmlWrapper.get_gpu_mode() is Unavailable

    def test_prime_select_timeout_falls_through_to_sysfs(self):
        import subprocess

        with (
            patch("nvml_wrapper.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)),
            patch("os.path.exists", return_value=False),
        ):
            assert NvmlWrapper.get_gpu_mode() is Unavailable

    def test_prime_select_timeout_sysfs_fallback(self):
        import subprocess

        with (
            patch("nvml_wrapper.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)),
            patch("os.path.exists", return_value=True),
        ):
            assert NvmlWrapper.get_gpu_mode() == "hybrid"


# ===================================================================
# get_cuda_toolkit_version
# ===================================================================


class TestGetCudaToolkitVersion:
    def test_parses_nvcc_output(self):
        nvcc_output = (
            "nvcc: NVIDIA (R) Cuda compiler driver\n"
            "Cuda compilation tools, release 12.4, V12.4.131\n"
        )
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=nvcc_output)
            assert NvmlWrapper.get_cuda_toolkit_version() == "12.4"

    def test_nvcc_not_found(self):
        with patch("nvml_wrapper.subprocess.run", side_effect=FileNotFoundError):
            assert NvmlWrapper.get_cuda_toolkit_version() is Unavailable

    def test_nvcc_unexpected_output(self):
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="no version info here")
            assert NvmlWrapper.get_cuda_toolkit_version() is Unavailable

    def test_nvcc_nonzero_exit(self):
        with patch("nvml_wrapper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert NvmlWrapper.get_cuda_toolkit_version() is Unavailable

    def test_nvcc_timeout(self):
        import subprocess

        with patch("nvml_wrapper.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            assert NvmlWrapper.get_cuda_toolkit_version() is Unavailable


# ===================================================================
# get_all_gpu_info includes new fields
# ===================================================================


class TestAllGpuInfoAdvanced:
    def test_includes_cuda_toolkit_version(self, mock_nvml, mock_handle):
        wrapper, _lib = mock_nvml
        # Stub all the methods that get_all_gpu_info calls
        with (
            patch.object(wrapper, "get_cuda_toolkit_version", return_value="12.4"),
            patch.object(wrapper, "get_gpu_mode", return_value="nvidia"),
        ):
            info = wrapper.get_all_gpu_info(0)
        assert info["cuda_toolkit_version"] == "12.4"
        assert info["gpu_mode"] == "nvidia"

    def test_includes_unavailable_cuda_toolkit(self, mock_nvml, mock_handle):
        wrapper, _lib = mock_nvml
        with (
            patch.object(wrapper, "get_cuda_toolkit_version", return_value=Unavailable),
            patch.object(wrapper, "get_gpu_mode", return_value=Unavailable),
        ):
            info = wrapper.get_all_gpu_info(0)
        assert info["cuda_toolkit_version"] is Unavailable
        assert info["gpu_mode"] is Unavailable


# ===================================================================
# get_all_gpu_stats includes throttle_reasons_decoded
# ===================================================================


class TestAllGpuStatsAdvanced:
    def test_includes_decoded_throttle_reasons(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml

        def _get_throttle(handle, p_reasons):
            p_reasons._obj.value = NVML_THROTTLE_REASON_SW_POWER_CAP
            return NVML_SUCCESS

        lib.nvmlDeviceGetCurrentThrottleReasons.side_effect = _get_throttle

        # Stub other methods to return minimal data
        lib.nvmlDeviceGetTemperature.side_effect = lambda h, s, p: (
            setattr(p._obj, "value", 42) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetClockInfo.side_effect = lambda h, t, p: (
            setattr(p._obj, "value", 2100) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetPerformanceState.side_effect = lambda h, p: (
            setattr(p._obj, "value", 0) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetMemoryInfo.side_effect = lambda h, p: (
            setattr(p._obj, "total", 24e9)
            or setattr(p._obj, "used", 4e9)
            or setattr(p._obj, "free", 20e9)
            or NVML_SUCCESS
        )
        lib.nvmlDeviceGetUtilizationRates.side_effect = lambda h, p: (
            setattr(p._obj, "gpu", 35) or setattr(p._obj, "memory", 22) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetPowerUsage.side_effect = lambda h, p: (
            setattr(p._obj, "value", 150000) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetEnforcedPowerLimit.side_effect = lambda h, p: (
            setattr(p._obj, "value", 450000) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetMemoryErrorCounter.side_effect = lambda h, a, b, c, p: (
            setattr(p._obj, "value", 0) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetProcessUtilization.return_value = NVML_ERROR_NOT_SUPPORTED

        stats = wrapper.get_all_gpu_stats(0)
        assert "throttle_reasons_decoded" in stats
        assert "Software power cap" in stats["throttle_reasons_decoded"]

    def test_throttle_unavailable_passes_through(self, mock_nvml, mock_handle):
        wrapper, lib = mock_nvml
        lib.nvmlDeviceGetCurrentThrottleReasons.side_effect = None
        lib.nvmlDeviceGetCurrentThrottleReasons.return_value = NVML_ERROR_NOT_SUPPORTED

        # Minimal stubs
        lib.nvmlDeviceGetTemperature.side_effect = lambda h, s, p: (
            setattr(p._obj, "value", 42) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetClockInfo.side_effect = lambda h, t, p: (
            setattr(p._obj, "value", 2100) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetPerformanceState.side_effect = lambda h, p: (
            setattr(p._obj, "value", 0) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetMemoryInfo.side_effect = lambda h, p: (
            setattr(p._obj, "total", 24e9)
            or setattr(p._obj, "used", 4e9)
            or setattr(p._obj, "free", 20e9)
            or NVML_SUCCESS
        )
        lib.nvmlDeviceGetUtilizationRates.side_effect = lambda h, p: (
            setattr(p._obj, "gpu", 35) or setattr(p._obj, "memory", 22) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetPowerUsage.side_effect = lambda h, p: (
            setattr(p._obj, "value", 150000) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetEnforcedPowerLimit.side_effect = lambda h, p: (
            setattr(p._obj, "value", 450000) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetMemoryErrorCounter.side_effect = lambda h, a, b, c, p: (
            setattr(p._obj, "value", 0) or NVML_SUCCESS
        )
        lib.nvmlDeviceGetProcessUtilization.return_value = NVML_ERROR_NOT_SUPPORTED

        stats = wrapper.get_all_gpu_stats(0)
        assert stats["throttle_reasons"] is Unavailable
        assert stats["throttle_reasons_decoded"] is Unavailable
