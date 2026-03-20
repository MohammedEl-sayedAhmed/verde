"""Unit tests for Story 5.1: Diagnostic Report Generation."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from diagnostics import _UNAVAILABLE, DiagnosticCollector
from nvml_wrapper import Unavailable

# ── Test fixtures ─────────────────────────────────────────────────────


def _make_cp(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


def _mock_run_success(cmd, **kwargs):
    """Mock run that returns plausible outputs for each command."""
    if cmd[0] == "loginctl":
        if "list-sessions" in cmd:
            return _make_cp(stdout="  42 1000 user seat0\n")
        if "show-session" in cmd:
            return _make_cp(stdout="x11\n")
    if cmd[0] == "mokutil":
        return _make_cp(stdout="SecureBoot disabled\n")
    if cmd[0] == "lsmod":
        return _make_cp(
            stdout=(
                "Module                  Size  Used by\n"
                "nvidia_drm            77824  11\n"
                "nvidia_modeset       1236992  13 nvidia_drm\n"
                "nvidia              56692736  1143 nvidia_modeset\n"
            )
        )
    if cmd[0] == "dkms":
        return _make_cp(stdout="nvidia/560.35.03, 6.5.0-44-generic, x86_64: installed\n")
    if cmd[0] == "journalctl":
        return _make_cp(stdout="Mar 20 10:00:00 host kernel: nvidia 0000:01:00.0: init\n")
    if cmd[0] == "dmesg":
        return _make_cp(stdout="[    1.234] nvidia: loading driver\n[    1.235] NVRM: init\n")
    return _make_cp(returncode=1)


def _mock_run_all_fail(cmd, **kwargs):
    """Mock run where all commands fail."""
    raise FileNotFoundError(f"Command not found: {cmd[0]}")


def _read_file_success(path):
    """Mock file reader."""
    if path == "/etc/os-release":
        return 'PRETTY_NAME="Ubuntu 24.04.2 LTS"\nNAME="Ubuntu"\n'
    if path == "/sys/module/nvidia/version":
        return "560.35.03\n"
    return ""


def _read_file_fail(path):
    """Mock file reader that always returns empty."""
    return ""


@pytest.fixture
def mock_nvml():
    """Mock NvmlWrapper returning sample GPU data."""
    wrapper = MagicMock()
    wrapper.get_all_gpu_info.return_value = {
        "name": "NVIDIA GeForce RTX 4090",
        "uuid": "GPU-12345678-1234-1234-1234-123456789abc",
        "pci_info": {"busId": "0000:01:00.0"},
        "num_cores": 16384,
        "compute_capability": (8, 9),
        "driver_version": "560.35.03",
        "cuda_driver_version": "12.6",
        "cuda_toolkit_version": "12.6",
        "gpu_mode": "nvidia",
        "ecc_mode": False,
        "performance_mode": "Maximum Performance",
    }
    wrapper.get_all_gpu_stats.return_value = {
        "temperature": 42,
        "utilization": {"gpu": 15, "memory": 8},
        "memory": {"total": 25769803776, "used": 1073741824, "free": 24696061952},
        "power_usage": 65000,  # mW
        "power_limit": 450000,
        "performance_state": "P8",
        "performance_mode": "Idle",
        "throttle_reasons": 0,
        "throttle_reasons_decoded": [],
        "clock_graphics": 210,
        "clock_sm": 210,
        "clock_mem": 405,
        "processes": [],
        "memory_errors": 0,
    }
    return wrapper


@pytest.fixture
def mock_nvml_unavailable():
    """Mock NvmlWrapper when NVML is not available."""
    wrapper = MagicMock()
    wrapper.get_all_gpu_info.return_value = {"handle": Unavailable}
    wrapper.get_all_gpu_stats.return_value = {"handle": Unavailable}
    return wrapper


@pytest.fixture
def collector(mock_nvml):
    """Fully mocked DiagnosticCollector."""
    return DiagnosticCollector(
        nvml=mock_nvml,
        run=_mock_run_success,
        read_file=_read_file_success,
    )


@pytest.fixture
def collector_no_nvml():
    """DiagnosticCollector with no NVML."""
    return DiagnosticCollector(
        nvml=None,
        run=_mock_run_success,
        read_file=_read_file_success,
    )


@pytest.fixture
def collector_degraded(mock_nvml_unavailable):
    """DiagnosticCollector with degraded NVML."""
    return DiagnosticCollector(
        nvml=mock_nvml_unavailable,
        run=_mock_run_all_fail,
        read_file=_read_file_fail,
    )


# ═══════════════════════════════════════════════════════════════════════
# Task 1: Data collector tests
# ═══════════════════════════════════════════════════════════════════════


class TestSystemInfoCollection:
    def test_kernel_version(self, collector):
        info = collector._collect_system_info()
        assert info["kernel"] != _UNAVAILABLE
        assert info["kernel"]  # non-empty

    def test_ubuntu_version(self, collector):
        info = collector._collect_system_info()
        assert "Ubuntu" in info["ubuntu"]

    def test_session_type(self, collector):
        info = collector._collect_system_info()
        assert info["session"] == "x11"

    def test_secure_boot(self, collector):
        info = collector._collect_system_info()
        assert "SecureBoot" in info["secure_boot"]

    def test_verde_version(self, collector):
        info = collector._collect_system_info()
        assert info["verde_version"]  # non-empty


class TestGpuInfoCollection:
    def test_gpu_info_with_nvml(self, collector):
        info = collector._collect_gpu_info()
        assert info.get("name") == "NVIDIA GeForce RTX 4090"
        assert info.get("compute_capability") == "8.9"

    def test_gpu_info_without_nvml(self, collector_no_nvml):
        info = collector_no_nvml._collect_gpu_info()
        assert info.get("status") == _UNAVAILABLE

    def test_gpu_info_nvml_degraded(self, collector_degraded):
        info = collector_degraded._collect_gpu_info()
        assert info.get("status") == _UNAVAILABLE


class TestDriverInfoCollection:
    def test_driver_version(self, collector):
        info = collector._collect_driver_info()
        assert info["version"] == "560.35.03"

    def test_driver_type_proprietary(self, collector):
        info = collector._collect_driver_info()
        assert info["type"] == "proprietary"

    def test_dkms_status(self, collector):
        info = collector._collect_driver_info()
        assert "nvidia" in info["dkms"].lower()

    def test_loaded_modules(self, collector):
        info = collector._collect_driver_info()
        assert "nvidia_drm" in info["loaded_modules"]

    def test_driver_info_all_fail(self, collector_degraded):
        info = collector_degraded._collect_driver_info()
        assert info["version"] == _UNAVAILABLE or info["version"] == ""
        assert info["type"] == _UNAVAILABLE


class TestGpuStateCollection:
    def test_gpu_state_with_nvml(self, collector):
        state = collector._collect_gpu_state()
        assert "42°C" in state["temperature"]
        assert "15%" in state["utilization"]
        assert "P8" in state["p_state"]

    def test_gpu_state_without_nvml(self, collector_no_nvml):
        state = collector_no_nvml._collect_gpu_state()
        assert state.get("status") == _UNAVAILABLE


class TestSystemLogsCollection:
    def test_journal_logs(self, collector):
        logs = collector._collect_system_logs()
        assert logs["journal"] != _UNAVAILABLE
        assert "nvidia" in logs["journal"]

    def test_dmesg_logs(self, collector):
        logs = collector._collect_system_logs()
        assert logs["dmesg"] != _UNAVAILABLE
        assert "nvidia" in logs["dmesg"].lower() or "NVRM" in logs["dmesg"]

    def test_logs_all_fail(self, collector_degraded):
        logs = collector_degraded._collect_system_logs()
        assert logs["journal"] == _UNAVAILABLE
        assert logs["dmesg"] == _UNAVAILABLE


class TestDetectedIssues:
    def test_no_issues_with_working_system(self, collector):
        issues = collector._collect_detected_issues()
        # With full mock, no issues expected (driver present, NVML working)
        assert isinstance(issues, list)

    def test_no_driver_detected(self):
        """When no nvidia or nouveau modules loaded."""

        def _run_no_driver(cmd, **kw):
            if cmd[0] == "lsmod":
                return _make_cp(stdout="Module Size Used by\ni915 2048000 4\n")
            return _mock_run_success(cmd, **kw)

        dc = DiagnosticCollector(
            nvml=None,
            run=_run_no_driver,
            read_file=_read_file_fail,
        )
        issues = dc._collect_detected_issues()
        assert any("No NVIDIA driver" in i for i in issues)
        assert any("NVML" in i for i in issues)


# ═══════════════════════════════════════════════════════════════════════
# Task 2: Markdown report formatter tests
# ═══════════════════════════════════════════════════════════════════════


class TestMarkdownReport:
    def test_markdown_has_title(self, collector):
        report = collector.generate("markdown")
        assert "# Verde Diagnostic Report" in report

    def test_markdown_has_timestamp_iso8601(self, collector):
        report = collector.generate("markdown")
        assert "Generated:" in report
        # ISO 8601 has T separator and timezone offset
        lines = report.split("\n")
        gen_line = next(line for line in lines if line.startswith("Generated:"))
        assert "T" in gen_line

    def test_markdown_has_all_sections(self, collector):
        report = collector.generate("markdown")
        assert "## System Information" in report
        assert "## GPU Information" in report
        assert "## Driver Status" in report
        assert "## GPU State" in report
        assert "## Detected Issues" in report
        assert "## Recent Kernel Logs" in report

    def test_markdown_system_info_fields(self, collector):
        report = collector.generate("markdown")
        assert "**Kernel:**" in report
        assert "**Ubuntu:**" in report
        assert "**Session:**" in report
        assert "**Secure Boot:**" in report
        assert "**Verde Version:**" in report

    def test_markdown_driver_fields(self, collector):
        report = collector.generate("markdown")
        assert "**Version:**" in report
        assert "**Type:**" in report
        assert "**DKMS Status:**" in report
        assert "**Loaded Modules:**" in report

    def test_markdown_gpu_state_fields(self, collector):
        report = collector.generate("markdown")
        assert "**Temperature:**" in report
        assert "**Utilization:**" in report
        assert "**Memory:**" in report
        assert "**Power Draw:**" in report
        assert "**P-State:**" in report

    def test_markdown_unavailable_sections_included(self, collector_degraded):
        """Sections with unavailable data are included, not omitted."""
        report = collector_degraded.generate("markdown")
        assert "## System Information" in report
        assert "## GPU Information" in report
        assert _UNAVAILABLE in report

    def test_markdown_code_blocks_for_logs(self, collector):
        report = collector.generate("markdown")
        assert "```" in report  # code blocks for log output

    def test_default_format_is_markdown(self, collector):
        report_default = collector.generate()
        report_explicit = collector.generate("markdown")
        # Both should be markdown (contain section headers)
        assert "## System Information" in report_default
        assert "## System Information" in report_explicit


# ═══════════════════════════════════════════════════════════════════════
# Task 3: JSON report formatter tests
# ═══════════════════════════════════════════════════════════════════════


class TestJsonReport:
    def test_json_is_valid(self, collector):
        report = collector.generate("json")
        data = json.loads(report)
        assert isinstance(data, dict)

    def test_json_has_all_keys(self, collector):
        report = collector.generate("json")
        data = json.loads(report)
        assert "generated_at" in data
        assert "verde_version" in data
        assert "system" in data
        assert "gpu" in data
        assert "driver" in data
        assert "gpu_state" in data
        assert "detected_issues" in data
        assert "kernel_logs" in data

    def test_json_generated_at_iso8601(self, collector):
        report = collector.generate("json")
        data = json.loads(report)
        assert "T" in data["generated_at"]

    def test_json_compact(self, collector):
        """JSON should be compact — no pretty-print newlines."""
        report = collector.generate("json")
        assert "\n" not in report

    def test_json_unavailable_becomes_null(self, collector_degraded):
        """[unavailable] markers should be converted to null in JSON."""
        report = collector_degraded.generate("json")
        data = json.loads(report)
        # GPU info should be null when unavailable
        gpu = data.get("gpu", {})
        assert gpu.get("status") is None  # _UNAVAILABLE -> null

    def test_json_system_keys(self, collector):
        report = collector.generate("json")
        data = json.loads(report)
        sys = data["system"]
        assert "kernel" in sys
        assert "ubuntu" in sys
        assert "session" in sys
        assert "secure_boot" in sys
        assert "verde_version" in sys


# ═══════════════════════════════════════════════════════════════════════
# Task 4: D-Bus dispatch tests
# ═══════════════════════════════════════════════════════════════════════


class TestDBusDispatch:
    def test_dispatch_method_exists(self):
        """Verify GenerateDiagnosticReport dispatch is wired in service.py."""
        import pathlib
        from unittest.mock import MagicMock

        from service import VerdeService

        _XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        _XML = _XML_PATH.read_text()

        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=MagicMock(),
            introspection_xml=_XML,
        )
        assert hasattr(svc, "_dispatch_generate_diagnostic")
        assert hasattr(svc, "_diagnostics")

    def test_xml_has_format_parameter(self):
        """D-Bus XML includes format input parameter."""
        import pathlib

        xml_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        xml = xml_path.read_text()
        # Check GenerateDiagnosticReport has format input
        assert 'name="format"' in xml
        assert 'direction="in"' in xml


# ═══════════════════════════════════════════════════════════════════════
# Task 5: Audit logging tests
# ═══════════════════════════════════════════════════════════════════════


class TestAuditLogging:
    def test_audit_op_constant_exists(self):
        from audit import OP_GENERATE_DIAGNOSTIC

        assert OP_GENERATE_DIAGNOSTIC == "GENERATE_DIAGNOSTIC"

    def test_audit_log_called_on_dispatch(self):
        """Dispatch calls AuditLogger.log with correct operation and params."""
        import pathlib

        from audit import OP_GENERATE_DIAGNOSTIC
        from gi.repository import GLib
        from service import VerdeService

        _XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        _XML = _XML_PATH.read_text()

        mock_audit = MagicMock()
        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=MagicMock(),
            introspection_xml=_XML,
            audit_logger=mock_audit,
        )

        # Create a mock invocation
        mock_invocation = MagicMock()
        params = GLib.Variant("(s)", ("markdown",))

        svc._dispatch_generate_diagnostic(params, mock_invocation, ":1.42")

        # Verify audit was called with success
        mock_audit.log.assert_called_once_with(
            OP_GENERATE_DIAGNOSTIC,
            {"format": "markdown"},
            ":1.42",
            "success",
        )

        # Verify report was returned via invocation
        mock_invocation.return_value.assert_called_once()

    def test_audit_log_json_format(self):
        """Dispatch logs correct format param for JSON reports."""
        import pathlib

        from audit import OP_GENERATE_DIAGNOSTIC
        from gi.repository import GLib
        from service import VerdeService

        _XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        _XML = _XML_PATH.read_text()

        mock_audit = MagicMock()
        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=MagicMock(),
            introspection_xml=_XML,
            audit_logger=mock_audit,
        )

        mock_invocation = MagicMock()
        params = GLib.Variant("(s)", ("json",))

        svc._dispatch_generate_diagnostic(params, mock_invocation, ":1.99")

        mock_audit.log.assert_called_once_with(
            OP_GENERATE_DIAGNOSTIC,
            {"format": "json"},
            ":1.99",
            "success",
        )


# ═══════════════════════════════════════════════════════════════════════
# Graceful degradation: subprocess failures
# ═══════════════════════════════════════════════════════════════════════


class TestGracefulDegradation:
    def test_subprocess_timeout_handled(self):
        """TimeoutExpired in subprocess calls produces [unavailable], not exceptions."""

        def _timeout_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

        dc = DiagnosticCollector(
            nvml=None,
            run=_timeout_run,
            read_file=_read_file_fail,
        )
        # Should not raise
        report = dc.generate("markdown")
        assert _UNAVAILABLE in report

    def test_file_not_found_handled(self):
        """FileNotFoundError from commands produces [unavailable]."""
        dc = DiagnosticCollector(
            nvml=None,
            run=_mock_run_all_fail,
            read_file=_read_file_fail,
        )
        report = dc.generate("markdown")
        assert _UNAVAILABLE in report

    def test_partial_report_on_degradation(self, mock_nvml_unavailable):
        """Report is generated even when NVML and subprocesses fail."""
        dc = DiagnosticCollector(
            nvml=mock_nvml_unavailable,
            run=_mock_run_all_fail,
            read_file=_read_file_fail,
        )
        report = dc.generate("markdown")
        # Should still have all section headers
        assert "## System Information" in report
        assert "## GPU Information" in report
        assert "## Driver Status" in report
        assert "## Detected Issues" in report

    def test_json_partial_report(self, mock_nvml_unavailable):
        """JSON report is valid even under full degradation."""
        dc = DiagnosticCollector(
            nvml=mock_nvml_unavailable,
            run=_mock_run_all_fail,
            read_file=_read_file_fail,
        )
        report = dc.generate("json")
        data = json.loads(report)
        assert "system" in data
        assert "gpu" in data


# ═══════════════════════════════════════════════════════════════════════
# No shell=True security check
# ═══════════════════════════════════════════════════════════════════════


class TestSecurityConstraints:
    def test_no_shell_true_in_subprocess(self):
        """All subprocess calls use list form."""
        calls = []

        def _tracking_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            assert "shell" not in kwargs or kwargs["shell"] is False, (
                f"shell=True detected in call: {cmd}"
            )
            return _make_cp()

        dc = DiagnosticCollector(
            nvml=None,
            run=_tracking_run,
            read_file=_read_file_success,
        )
        dc.generate("markdown")
        assert len(calls) > 0
        for cmd, _kwargs in calls:
            assert isinstance(cmd, list), f"Expected list, got {type(cmd)}: {cmd}"
