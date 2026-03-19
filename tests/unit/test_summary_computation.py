"""Unit tests for Story 3.5: Post-reboot summary computation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _UnavailableSentinel:
    """Mimics the nvml_wrapper Unavailable sentinel (falsy)."""

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "Unavailable"


Unavailable = _UnavailableSentinel()


def _make_nvml(driver_version="550", gpu_name="NVIDIA GeForce RTX 4070"):
    """Create a mock NVML wrapper."""
    nvml = MagicMock()
    nvml.get_driver_version.return_value = driver_version
    nvml.get_device_count.return_value = 1
    handle = MagicMock()
    nvml.get_handle_by_index.return_value = handle
    nvml.get_device_name.return_value = gpu_name
    return nvml


def _make_pending(op_type="install", previous="535", expected="550"):
    return {
        "operation_type": op_type,
        "previous_version": previous,
        "expected_version": expected,
        "operation_id": "op_001",
        "timestamp": "2026-03-19T10:00:00+00:00",
        "kernel_version": "6.8.0-45-generic",
    }


@pytest.fixture()
def manager(tmp_path):
    from pending_summary import PendingSummaryManager

    return PendingSummaryManager(state_dir=tmp_path)


class TestSuccessCase:
    """Test success result — expected version matches, GPU healthy."""

    def test_success_install(self, manager):
        nvml = _make_nvml(driver_version="550")
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        assert result["result"] == "success"
        assert result["gpu_healthy"] is True
        assert result["current_version"] == "550"
        assert result["has_pending"] is True
        assert "535" in result["message"]
        assert "550" in result["message"]
        assert "healthy" in result["message"].lower()
        assert result["recovery_guidance"] == ""

    def test_success_install_full_version(self, manager):
        """NVML returns full version like '550.35.03' but expected is '550'."""
        nvml = _make_nvml(driver_version="550.35.03")
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        assert result["result"] == "success"
        assert result["current_version"] == "550.35.03"

    def test_success_rollback(self, manager):
        pending = _make_pending(op_type="rollback", previous="550", expected="535")
        nvml = _make_nvml(driver_version="535")
        result = manager.compute_post_reboot_summary(pending, nvml)
        assert result["result"] == "success"
        assert "rolled back" in result["message"].lower()

    def test_success_message_is_humanized(self, manager):
        """Message uses human-readable language, not raw keys."""
        nvml = _make_nvml(driver_version="550")
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        # Should contain version numbers in context
        assert "updated" in result["message"].lower() or "changed" in result["message"].lower()


class TestPartialCase:
    """Test partial result — driver loaded but version mismatch."""

    def test_partial_version_mismatch(self, manager):
        nvml = _make_nvml(driver_version="545")
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        assert result["result"] == "partial"
        assert result["current_version"] == "545"
        assert result["gpu_healthy"] is True
        assert "545" in result["message"]
        assert "550" in result["message"]

    def test_partial_recovery_guidance(self, manager):
        nvml = _make_nvml(driver_version="545")
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        assert result["recovery_guidance"] != ""
        assert "drivers" in result["recovery_guidance"].lower()


class TestFailedCase:
    """Test failed result — NVML unavailable or GPU not detected."""

    def test_failed_nvml_unavailable(self, manager):
        """NVML unavailable — no driver loaded."""
        nvml = MagicMock()
        nvml.get_driver_version.side_effect = Exception("NVML not loaded")
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        assert result["result"] == "failed"
        assert result["gpu_healthy"] is False
        assert result["recovery_guidance"] != ""
        assert "verde --repair" in result["recovery_guidance"]

    def test_failed_unavailable_sentinel(self, manager):
        """NVML returns Unavailable sentinel."""
        nvml = MagicMock()
        nvml.get_driver_version.return_value = Unavailable
        nvml.get_device_count.return_value = 0
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        assert result["result"] == "failed"
        assert result["gpu_healthy"] is False

    def test_failed_message_humanized(self, manager):
        nvml = MagicMock()
        nvml.get_driver_version.side_effect = Exception("NVML not loaded")
        result = manager.compute_post_reboot_summary(_make_pending(), nvml)
        assert "failed" in result["message"].lower() or "not loaded" in result["message"].lower()
