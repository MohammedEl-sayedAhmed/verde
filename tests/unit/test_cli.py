"""Unit tests for Story 6.5: CLI Health Check & Scripting Support."""

from __future__ import annotations

import json

from verde.cli import (
    EXIT_CRITICAL,
    EXIT_ERROR,
    EXIT_HEALTHY,
    EXIT_NO_GPU,
    EXIT_WARNING,
    _build_json_check,
    _build_json_error,
    _format_plain_check,
    evaluate_health,
)

# ═══════════════════════════════════════════════════════════════════════
# Health evaluation tests
# ═══════════════════════════════════════════════════════════════════════


_HEALTHY_INFO = {
    "gpu_name": "NVIDIA GeForce RTX 3080",
    "driver_version": "565.57.01",
}

_HEALTHY_STATS = {
    "temperature": 42,
    "utilization": 5,
    "memory_used": 512.0,
    "memory_total": 10240.0,
    "fan_speed": 30,
    "power_draw": 25.5,
    "throttle_reasons": "None",
    "gpu_available": True,
}


class TestEvaluateHealth:
    def test_healthy_gpu(self):
        code, status, reason = evaluate_health(_HEALTHY_INFO, _HEALTHY_STATS)
        assert code == EXIT_HEALTHY
        assert status == "healthy"
        assert "RTX 3080" in reason
        assert "565" in reason

    def test_no_gpu_empty_data(self):
        code, status, _ = evaluate_health({}, {})
        assert code == EXIT_NO_GPU
        assert status == "no_gpu"

    def test_no_gpu_unavailable(self):
        stats = {**_HEALTHY_STATS, "gpu_available": False}
        code, _status, _ = evaluate_health(_HEALTHY_INFO, stats)
        assert code == EXIT_NO_GPU

    def test_critical_high_temperature(self):
        stats = {**_HEALTHY_STATS, "temperature": 98}
        code, status, reason = evaluate_health(_HEALTHY_INFO, stats)
        assert code == EXIT_CRITICAL
        assert "98" in reason
        assert "critical" in reason.lower() or status == "critical"

    def test_critical_no_driver(self):
        info = {**_HEALTHY_INFO, "driver_version": ""}
        code, _status, _ = evaluate_health(info, _HEALTHY_STATS)
        assert code == EXIT_CRITICAL
        assert "driver" in _.lower()

    def test_warning_warm_temperature(self):
        stats = {**_HEALTHY_STATS, "temperature": 88}
        code, _status, reason = evaluate_health(_HEALTHY_INFO, stats)
        assert code == EXIT_WARNING
        assert "88" in reason

    def test_warning_high_vram(self):
        stats = {**_HEALTHY_STATS, "memory_used": 9500.0, "memory_total": 10240.0}
        code, _status, reason = evaluate_health(_HEALTHY_INFO, stats)
        assert code == EXIT_WARNING
        assert "VRAM" in reason

    def test_warning_throttling(self):
        stats = {**_HEALTHY_STATS, "throttle_reasons": "Thermal"}
        code, _status, reason = evaluate_health(_HEALTHY_INFO, stats)
        assert code == EXIT_WARNING
        assert "throttl" in reason.lower()

    def test_multiple_warnings(self):
        stats = {
            **_HEALTHY_STATS,
            "temperature": 87,
            "throttle_reasons": "Power",
        }
        code, _status, reason = evaluate_health(_HEALTHY_INFO, stats)
        assert code == EXIT_WARNING
        assert "87" in reason
        assert "throttl" in reason.lower()

    def test_none_info_returns_no_gpu(self):
        code, _status, _ = evaluate_health(None, _HEALTHY_STATS)
        assert code == EXIT_NO_GPU

    def test_none_stats_returns_no_gpu(self):
        code, _status, _ = evaluate_health(_HEALTHY_INFO, None)
        assert code == EXIT_NO_GPU


# ═══════════════════════════════════════════════════════════════════════
# Plain text output
# ═══════════════════════════════════════════════════════════════════════


class TestPlainTextOutput:
    def test_healthy_format(self):
        result = _format_plain_check(EXIT_HEALTHY, "RTX 3080 — driver 565, temp 42°C")
        assert result.startswith("OK:")
        assert "RTX 3080" in result

    def test_warning_format(self):
        result = _format_plain_check(EXIT_WARNING, "RTX 3080 — temp 88°C")
        assert result.startswith("WARNING:")

    def test_critical_format(self):
        result = _format_plain_check(EXIT_CRITICAL, "RTX 3080 — critical")
        assert result.startswith("CRITICAL:")

    def test_no_gpu_format(self):
        result = _format_plain_check(EXIT_NO_GPU, "No NVIDIA GPU detected")
        assert result.startswith("NO GPU:")

    def test_error_format(self):
        result = _format_plain_check(EXIT_ERROR, "daemon unreachable")
        assert result.startswith("ERROR:")


# ═══════════════════════════════════════════════════════════════════════
# JSON output
# ═══════════════════════════════════════════════════════════════════════


class TestJSONOutput:
    def test_json_check_structure(self):
        output = _build_json_check(
            EXIT_HEALTHY,
            "healthy",
            _HEALTHY_INFO,
            _HEALTHY_STATS,
            "0.1.0",
        )
        assert output["status"] == "healthy"
        assert output["exit_code"] == 0
        assert len(output["gpus"]) == 1
        assert output["daemon_version"] == "0.1.0"

        gpu = output["gpus"][0]
        assert gpu["name"] == "NVIDIA GeForce RTX 3080"
        assert gpu["temperature_c"] == 42
        assert gpu["health"] == "healthy"

    def test_json_check_no_gpu(self):
        output = _build_json_check(EXIT_NO_GPU, "no_gpu", None, None)
        assert output["status"] == "no_gpu"
        assert output["gpus"] == []

    def test_json_error_structure(self):
        output = _build_json_error("daemon_unreachable", "test error", EXIT_ERROR)
        assert output["error"] == "daemon_unreachable"
        assert output["message"] == "test error"
        assert output["exit_code"] == EXIT_ERROR

    def test_json_is_valid(self):
        output = _build_json_check(
            EXIT_HEALTHY,
            "healthy",
            _HEALTHY_INFO,
            _HEALTHY_STATS,
        )
        # Must be JSON-serializable
        serialized = json.dumps(output)
        parsed = json.loads(serialized)
        assert parsed["status"] == "healthy"

    def test_json_throttle_list_filters_none(self):
        stats = {**_HEALTHY_STATS, "throttle_reasons": "None"}
        output = _build_json_check(EXIT_HEALTHY, "healthy", _HEALTHY_INFO, stats)
        assert output["gpus"][0]["throttle_reasons"] == []

    def test_json_throttle_list_splits_multiple(self):
        stats = {**_HEALTHY_STATS, "throttle_reasons": "Thermal, Power"}
        output = _build_json_check(EXIT_WARNING, "warning", _HEALTHY_INFO, stats)
        reasons = output["gpus"][0]["throttle_reasons"]
        assert "Thermal" in reasons
        assert "Power" in reasons


# ═══════════════════════════════════════════════════════════════════════
# CLI module isolation — no GTK import
# ═══════════════════════════════════════════════════════════════════════


class TestCLIIsolation:
    def test_cli_source_does_not_reference_gtk(self):
        """Verify verde/cli.py source does not import Gtk or Adw."""
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2] / "src" / "verde" / "cli.py"
        ).read_text()
        # Source should not contain Gtk or Adw imports
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "Gtk" not in stripped, f"cli.py references Gtk: {stripped}"
            assert "Adw" not in stripped, f"cli.py references Adw: {stripped}"


# ═══════════════════════════════════════════════════════════════════════
# Exit code constants
# ═══════════════════════════════════════════════════════════════════════


class TestExitCodes:
    def test_exit_code_values(self):
        assert EXIT_HEALTHY == 0
        assert EXIT_WARNING == 1
        assert EXIT_CRITICAL == 2
        assert EXIT_NO_GPU == 3
        assert EXIT_ERROR == 4
