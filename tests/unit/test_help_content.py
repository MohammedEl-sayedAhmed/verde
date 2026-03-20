"""Tests for the tooltip content catalog (help_content.py).

Verifies every known metric key has a non-empty tooltip string
and that the catalog structure is complete.
"""

from __future__ import annotations

import pytest

from verde.help_content import METRIC_TOOLTIPS, STATUS_TOOLTIPS

# Every dashboard metric that MUST have a tooltip
REQUIRED_METRIC_KEYS = [
    "temperature",
    "utilization",
    "vram_usage",
    "power_draw",
    "fan_speed",
    "p_state",
    "clock_speed_graphics",
    "clock_speed_memory",
    "clock_speed_sm",
    "throttle_reason",
    "driver_version",
    "driver_type",
    "cuda_version",
    "compute_capability",
    "pcie_info",
    "gpu_model",
    "memory_total",
    "power_limit",
    "ecc_memory",
    "gpu_mode",
    "multi_gpu",
    "cuda_cores",
    "power_profile",
    "cuda_toolkit_version",
]

REQUIRED_STATUS_KEYS = [
    "healthy",
    "warning",
    "critical",
]


class TestMetricTooltips:
    """Verify METRIC_TOOLTIPS catalog completeness."""

    @pytest.mark.parametrize("key", REQUIRED_METRIC_KEYS)
    def test_metric_key_exists(self, key: str) -> None:
        assert key in METRIC_TOOLTIPS, f"Missing tooltip for metric: {key}"

    @pytest.mark.parametrize("key", REQUIRED_METRIC_KEYS)
    def test_metric_tooltip_nonempty(self, key: str) -> None:
        tooltip = METRIC_TOOLTIPS[key]
        assert isinstance(tooltip, str)
        assert len(tooltip.strip()) > 0, f"Empty tooltip for metric: {key}"

    def test_no_raw_technical_jargon_in_tooltips(self) -> None:
        """Tooltips should use plain language, not raw NVML constants."""
        forbidden = ["NVML_", "nvmlDevice", "nvmlInit", "subprocess", "stderr"]
        for key, text in METRIC_TOOLTIPS.items():
            for term in forbidden:
                assert term not in text, f"Tooltip '{key}' contains raw technical jargon: {term}"


class TestStatusTooltips:
    """Verify STATUS_TOOLTIPS catalog completeness."""

    @pytest.mark.parametrize("key", REQUIRED_STATUS_KEYS)
    def test_status_key_exists(self, key: str) -> None:
        assert key in STATUS_TOOLTIPS, f"Missing tooltip for status: {key}"

    @pytest.mark.parametrize("key", REQUIRED_STATUS_KEYS)
    def test_status_tooltip_nonempty(self, key: str) -> None:
        tooltip = STATUS_TOOLTIPS[key]
        assert isinstance(tooltip, str)
        assert len(tooltip.strip()) > 0, f"Empty tooltip for status: {key}"
