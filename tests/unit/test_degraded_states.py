"""Unit tests for degraded state detection (Story 1.10)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from verde_daemon.degraded_states import (
    DegradedState,
    build_state_info,
    detect_degraded_state,
    detect_driver_type,
    get_state_message,
)
from verde_daemon.nvml_wrapper import Unavailable


@pytest.fixture
def mock_nvml():
    nvml = MagicMock()
    nvml._lib = MagicMock()  # NVML loaded
    nvml.device_count.return_value = 1
    return nvml


# ===================================================================
# detect_driver_type
# ===================================================================


class TestDetectDriverType:
    @patch("verde_daemon.degraded_states.os.path.isdir")
    def test_proprietary(self, mock_isdir):
        mock_isdir.side_effect = lambda p: p == "/sys/module/nvidia"
        assert detect_driver_type() == "proprietary"

    @patch("verde_daemon.degraded_states.os.path.isdir")
    def test_nouveau(self, mock_isdir):
        mock_isdir.side_effect = lambda p: p == "/sys/module/nouveau"
        assert detect_driver_type() == "nouveau"

    @patch("verde_daemon.degraded_states.os.path.isdir")
    def test_none(self, mock_isdir):
        mock_isdir.return_value = False
        assert detect_driver_type() == "none"

    @patch("verde_daemon.degraded_states.os.path.isdir")
    def test_both_modules_prefers_proprietary(self, mock_isdir):
        mock_isdir.return_value = True  # Both exist
        assert detect_driver_type() == "proprietary"


# ===================================================================
# detect_degraded_state
# ===================================================================


class TestDetectDegradedState:
    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="proprietary")
    def test_normal_state(self, _mock_dt, mock_nvml):
        assert detect_degraded_state(mock_nvml) == DegradedState.NORMAL

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="proprietary")
    def test_nvml_unavailable(self, _mock_dt, mock_nvml):
        mock_nvml._lib = None
        _mock_dt.return_value = "proprietary"
        assert detect_degraded_state(mock_nvml) == DegradedState.NVML_UNAVAILABLE

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="nouveau")
    def test_nvml_unavailable_nouveau(self, _mock_dt, mock_nvml):
        mock_nvml._lib = None
        assert detect_degraded_state(mock_nvml) == DegradedState.NOUVEAU_ACTIVE

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="none")
    def test_nvml_unavailable_no_driver(self, _mock_dt, mock_nvml):
        mock_nvml._lib = None
        assert detect_degraded_state(mock_nvml) == DegradedState.NO_DRIVER

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="proprietary")
    def test_no_gpu_device_count_zero(self, _mock_dt, mock_nvml):
        mock_nvml.device_count.return_value = 0
        assert detect_degraded_state(mock_nvml) == DegradedState.NO_GPU

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="proprietary")
    def test_no_gpu_device_count_unavailable(self, _mock_dt, mock_nvml):
        mock_nvml.device_count.return_value = Unavailable
        assert detect_degraded_state(mock_nvml) == DegradedState.NO_GPU

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="nouveau")
    def test_nouveau_active_with_device(self, _mock_dt, mock_nvml):
        assert detect_degraded_state(mock_nvml) == DegradedState.NOUVEAU_ACTIVE

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="none")
    def test_no_driver_with_device(self, _mock_dt, mock_nvml):
        assert detect_degraded_state(mock_nvml) == DegradedState.NO_DRIVER

    @patch("verde_daemon.degraded_states.detect_driver_type", return_value="none")
    def test_no_driver_device_count_zero(self, _mock_dt, mock_nvml):
        mock_nvml.device_count.return_value = 0
        assert detect_degraded_state(mock_nvml) == DegradedState.NO_DRIVER


# ===================================================================
# get_state_message
# ===================================================================


class TestGetStateMessage:
    def test_all_states_have_messages(self):
        for state in DegradedState:
            msg = get_state_message(state)
            assert isinstance(msg, str)
            assert len(msg) > 0

    def test_normal_message(self):
        assert "normally" in get_state_message(DegradedState.NORMAL)

    def test_gpu_lost_message(self):
        msg = get_state_message(DegradedState.GPU_LOST)
        assert "no longer responding" in msg


# ===================================================================
# build_state_info
# ===================================================================


class TestBuildStateInfo:
    def test_returns_expected_keys(self):
        info = build_state_info(DegradedState.NORMAL, "proprietary", 1)
        assert info["state"] == "normal"
        assert info["driver_type"] == "proprietary"
        assert info["device_count"] == 1
        assert isinstance(info["message"], str)

    def test_no_gpu_state(self):
        info = build_state_info(DegradedState.NO_GPU, "proprietary", 0)
        assert info["state"] == "no_gpu"
        assert info["device_count"] == 0
