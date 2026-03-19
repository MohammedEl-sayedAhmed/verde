"""Unit tests for Story 3.4: Recovery diagnostics engine."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


class TestDiagnosticResult:
    """Test DiagnosticResult data structure."""

    def test_result_fields(self):
        from recovery_diagnostics import DiagnosticResult

        r = DiagnosticResult(
            issue_type="nvml_missing",
            severity="critical",
            description="NVIDIA kernel module not loaded",
            fixable=True,
        )
        assert r.issue_type == "nvml_missing"
        assert r.severity == "critical"
        assert r.description == "NVIDIA kernel module not loaded"
        assert r.fixable is True


class TestNVMLCheck:
    """Test NVML load diagnostic (AC#2)."""

    @patch("recovery_diagnostics.ctypes")
    def test_nvml_available(self, mock_ctypes):
        """No issue reported when NVML loads successfully."""
        from recovery_diagnostics import check_nvml

        lib = MagicMock()
        lib.nvmlInit_v2.return_value = 0
        mock_ctypes.CDLL.return_value = lib
        result = check_nvml()
        assert result is None  # No issue
        lib.nvmlShutdown.assert_called_once()

    @patch("recovery_diagnostics.ctypes")
    def test_nvml_missing(self, mock_ctypes):
        """Reports issue when libnvidia-ml.so.1 cannot be loaded."""
        from recovery_diagnostics import check_nvml

        mock_ctypes.CDLL.side_effect = OSError("cannot open shared object file")
        result = check_nvml()
        assert result is not None
        assert result.issue_type == "nvml_missing"
        assert result.severity == "critical"
        assert result.fixable is True


class TestMOKCheck:
    """Test Secure Boot / MOK enrollment diagnostic (AC#2)."""

    @patch("subprocess.run")
    @patch("pathlib.Path.exists", return_value=False)
    def test_no_efi_no_issue(self, _exists, _run):
        """No issue when system is not using EFI (no Secure Boot)."""
        from recovery_diagnostics import check_secure_boot_mok

        result = check_secure_boot_mok()
        assert result is None

    @patch("subprocess.run")
    @patch("pathlib.Path.exists", return_value=True)
    def test_secure_boot_disabled(self, _exists, mock_run):
        """No issue when Secure Boot is disabled."""
        from recovery_diagnostics import check_secure_boot_mok

        mock_run.return_value = MagicMock(returncode=0, stdout="SecureBoot disabled\n", stderr="")
        result = check_secure_boot_mok()
        assert result is None

    @patch("subprocess.run")
    @patch("pathlib.Path.exists", return_value=True)
    def test_secure_boot_enabled_mok_issue(self, _exists, mock_run):
        """Reports issue when Secure Boot enabled and MOK may not be enrolled."""
        from recovery_diagnostics import check_secure_boot_mok

        mock_run.return_value = MagicMock(returncode=0, stdout="SecureBoot enabled\n", stderr="")
        result = check_secure_boot_mok()
        assert result is not None
        assert result.issue_type == "mok_not_enrolled"
        assert result.severity == "warning"


class TestDpkgStateCheck:
    """Test dpkg state diagnostic (AC#2)."""

    @patch("subprocess.run")
    def test_dpkg_clean(self, mock_run):
        """No issue when dpkg --audit returns nothing."""
        from recovery_diagnostics import check_dpkg_state

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = check_dpkg_state()
        assert result is None

    @patch("subprocess.run")
    def test_dpkg_broken(self, mock_run):
        """Reports issue when dpkg --audit shows broken packages."""
        from recovery_diagnostics import check_dpkg_state

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="The following packages have been unpacked but not yet configured:\n nvidia-driver-560\n",
            stderr="",
        )
        result = check_dpkg_state()
        assert result is not None
        assert result.issue_type == "dpkg_broken"
        assert result.severity == "critical"
        assert result.fixable is True


class TestInterruptedOpCheck:
    """Test interrupted operation detection (FR58, AC#2)."""

    def test_no_marker_no_issue(self, tmp_path):
        """No issue when operation marker doesn't exist."""
        from recovery_diagnostics import check_interrupted_operation

        result = check_interrupted_operation(marker_path=tmp_path / "nonexistent.json")
        assert result is None

    def test_marker_present(self, tmp_path):
        """Reports issue when operation marker exists."""
        from recovery_diagnostics import check_interrupted_operation

        marker = tmp_path / "operation_in_progress.json"
        marker.write_text(json.dumps({"operation": "install_driver", "target": "560"}))
        result = check_interrupted_operation(marker_path=marker)
        assert result is not None
        assert result.issue_type == "interrupted_operation"
        assert result.fixable is True


class TestDKMSCheck:
    """Test DKMS status diagnostic (AC#2)."""

    @patch("subprocess.run")
    def test_dkms_ok(self, mock_run):
        """No issue when dkms status shows no nvidia failures."""
        from recovery_diagnostics import check_dkms_status

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="nvidia/560.35.03, 6.8.0-45-generic, x86_64: installed\n",
            stderr="",
        )
        result = check_dkms_status()
        assert result is None

    @patch("subprocess.run")
    def test_dkms_failed(self, mock_run):
        """Reports issue when dkms status shows nvidia build failure."""
        from recovery_diagnostics import check_dkms_status

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="nvidia/560.35.03, 6.8.0-45-generic, x86_64: broken\n",
            stderr="",
        )
        result = check_dkms_status()
        assert result is not None
        assert result.issue_type == "dkms_failure"

    @patch("subprocess.run")
    def test_dkms_not_found(self, mock_run):
        """No issue when dkms command not found."""
        from recovery_diagnostics import check_dkms_status

        mock_run.side_effect = FileNotFoundError("dkms")
        result = check_dkms_status()
        assert result is None  # dkms not installed is not an error


class TestRunAllDiagnostics:
    """Test the main diagnostic runner."""

    @patch("recovery_diagnostics.check_dkms_status", return_value=None)
    @patch("recovery_diagnostics.check_interrupted_operation", return_value=None)
    @patch("recovery_diagnostics.check_dpkg_state", return_value=None)
    @patch("recovery_diagnostics.check_secure_boot_mok", return_value=None)
    @patch("recovery_diagnostics.check_nvml", return_value=None)
    def test_all_healthy(self, *_mocks):
        """Returns empty list when all checks pass."""
        from recovery_diagnostics import run_diagnostics

        results = run_diagnostics()
        assert results == []

    @patch("recovery_diagnostics.check_dkms_status", return_value=None)
    @patch("recovery_diagnostics.check_interrupted_operation", return_value=None)
    @patch("recovery_diagnostics.check_dpkg_state", return_value=None)
    @patch("recovery_diagnostics.check_secure_boot_mok", return_value=None)
    @patch("recovery_diagnostics.check_nvml")
    def test_collects_issues(self, mock_nvml, *_mocks):
        """Collects all detected issues into a list."""
        from recovery_diagnostics import DiagnosticResult

        mock_nvml.return_value = DiagnosticResult(
            issue_type="nvml_missing",
            severity="critical",
            description="test",
            fixable=True,
        )
        from recovery_diagnostics import run_diagnostics

        results = run_diagnostics()
        assert len(results) == 1
        assert results[0].issue_type == "nvml_missing"
