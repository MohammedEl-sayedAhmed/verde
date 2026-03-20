"""Tests for humanized status conversion functions."""

from __future__ import annotations

from verde.humanized_status import (
    humanize_driver_status,
    humanize_operation_error,
    humanize_power_state,
    humanize_suspend_status,
    humanize_temperature,
    humanize_throttle_reason,
    humanize_vram,
)


class TestHumanizeTemperature:
    def test_normal_range(self) -> None:
        result = humanize_temperature(55)
        assert "55" in result
        assert "normal" in result.lower()

    def test_warning_range(self) -> None:
        result = humanize_temperature(85)
        assert "85" in result
        assert "warm" in result.lower()

    def test_critical_range(self) -> None:
        result = humanize_temperature(95)
        assert "95" in result
        assert "hot" in result.lower() or "reduced" in result.lower()

    def test_custom_thresholds(self) -> None:
        result = humanize_temperature(70, warn=60, crit=75)
        assert "warm" in result.lower()

    def test_zero_temperature(self) -> None:
        result = humanize_temperature(0)
        assert "0" in result


class TestHumanizePowerState:
    def test_p0_maximum(self) -> None:
        result = humanize_power_state("P0")
        assert "P0" in result
        assert "performance" in result.lower()

    def test_p8_idle(self) -> None:
        result = humanize_power_state("P8")
        assert "P8" in result
        assert "idle" in result.lower()

    def test_middle_state(self) -> None:
        result = humanize_power_state("P5")
        assert "P5" in result

    def test_unknown_state(self) -> None:
        result = humanize_power_state("Unknown")
        assert isinstance(result, str)
        assert len(result) > 0


class TestHumanizeThrottleReason:
    def test_no_throttle(self) -> None:
        result = humanize_throttle_reason("None")
        assert "not" in result.lower() or "no" in result.lower()

    def test_thermal_throttle(self) -> None:
        result = humanize_throttle_reason("Thermal")
        assert "temperature" in result.lower() or "thermal" in result.lower()

    def test_power_throttle(self) -> None:
        result = humanize_throttle_reason("Power")
        assert "power" in result.lower()

    def test_unknown_reason(self) -> None:
        result = humanize_throttle_reason("SomeUnknownReason")
        assert isinstance(result, str)


class TestHumanizeVram:
    def test_gb_values(self) -> None:
        # 8 GB used of 24 GB
        result = humanize_vram(8 * 1024**3, 24 * 1024**3)
        assert "8" in result
        assert "24" in result
        assert "%" in result

    def test_zero_usage(self) -> None:
        result = humanize_vram(0, 8 * 1024**3)
        assert "0" in result

    def test_full_usage(self) -> None:
        total = 8 * 1024**3
        result = humanize_vram(total, total)
        assert "100%" in result

    def test_zero_total(self) -> None:
        result = humanize_vram(0, 0)
        assert "0%" in result  # must not raise ZeroDivisionError


class TestHumanizeDriverStatus:
    def test_proprietary_driver(self) -> None:
        result = humanize_driver_status("560", "proprietary")
        assert "560" in result
        assert "proprietary" in result.lower() or "Proprietary" in result

    def test_open_driver(self) -> None:
        result = humanize_driver_status("560", "open")
        assert "open" in result.lower()

    def test_unknown_type(self) -> None:
        result = humanize_driver_status("535", "unknown")
        assert "535" in result


class TestHumanizeSuspendStatus:
    def test_working(self) -> None:
        result = humanize_suspend_status("ok")
        assert "working" in result.lower() or "normal" in result.lower()

    def test_issues_found(self) -> None:
        result = humanize_suspend_status("issues_found")
        assert "issue" in result.lower() or "problem" in result.lower()


class TestHumanizeOperationError:
    def test_strips_raw_output(self) -> None:
        raw = "E: dpkg was interrupted, you must manually run 'dpkg --configure -a'"
        result = humanize_operation_error(raw)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_input(self) -> None:
        result = humanize_operation_error("")
        assert isinstance(result, str)
