"""Unit tests for the pre-flight validation system (Story 2.2)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from preflight import CheckResult, PreflightChecker, PreflightResult

# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def checker() -> PreflightChecker:
    return PreflightChecker()


# ═══════════════════════════════════════════════════════════════════
# CheckResult / PreflightResult dataclass basics
# ═══════════════════════════════════════════════════════════════════


class TestDataclasses:
    def test_check_result_fields(self) -> None:
        cr = CheckResult(name="disk_space", status="pass", description="OK")
        assert cr.name == "disk_space"
        assert cr.status == "pass"
        assert cr.description == "OK"

    def test_check_result_is_frozen(self) -> None:
        cr = CheckResult(name="x", status="pass", description="y")
        with pytest.raises(AttributeError):
            cr.name = "z"  # type: ignore[misc]

    def test_preflight_result_defaults(self) -> None:
        pr = PreflightResult()
        assert pr.overall_pass is True
        assert pr.checks == []
        assert pr.duration_ms == 0


# ═══════════════════════════════════════════════════════════════════
# Task 10: Individual check PASS scenarios
# ═══════════════════════════════════════════════════════════════════


class TestDiskSpacePass:
    def test_pass_with_enough_space(self, checker: PreflightChecker) -> None:
        mock_stat = MagicMock()
        mock_stat.f_bavail = 5 * 1024 * 1024  # 5 GB in 1K blocks
        mock_stat.f_frsize = 1024
        with patch("preflight.os.statvfs", return_value=mock_stat):
            result = checker._check_disk_space()
        assert result.status == "pass"
        assert result.name == "disk_space"
        assert "5.0 GB" in result.description


class TestKernelHeadersPass:
    def test_pass_with_headers_installed(self, checker: PreflightChecker) -> None:
        with (
            patch("preflight.os.uname") as mock_uname,
            patch("preflight.subprocess.run") as mock_run,
        ):
            mock_uname.return_value = MagicMock(release="6.8.0-45-generic")
            mock_run.return_value = MagicMock(returncode=0, stdout="install ok installed")
            result = checker._check_kernel_headers()
        assert result.status == "pass"
        assert "6.8.0-45-generic" in result.description


class TestDpkgStatePass:
    def test_pass_clean_state(self, checker: PreflightChecker) -> None:
        with (
            patch.object(
                PreflightChecker,
                "_check_dpkg_lock",
                return_value=(False, "", 0),
            ),
            patch("preflight.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = checker._check_dpkg_state()
        assert result.status == "pass"
        assert result.description == "Package system is clean"


class TestSecureBootPass:
    def test_pass_secure_boot_disabled(self, checker: PreflightChecker) -> None:
        with patch("preflight.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="SecureBoot disabled")
            result = checker._check_secure_boot()
        assert result.status == "pass"
        assert "disabled" in result.description

    def test_pass_secure_boot_enabled_with_mok(self, checker: PreflightChecker) -> None:
        with patch("preflight.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="SecureBoot enabled"),
                MagicMock(returncode=0, stdout="[key 1]\nSHA1 Fingerprint: ..."),
            ]
            result = checker._check_secure_boot()
        assert result.status == "pass"
        assert "MOK keys enrolled" in result.description


class TestKernelCompatibilityPass:
    def test_pass_supported_kernel(self, checker: PreflightChecker) -> None:
        with patch("preflight.os.uname") as mock_uname:
            mock_uname.return_value = MagicMock(release="6.8.0-45-generic")
            result = checker._check_kernel_compatibility("driver_install")
        assert result.status == "pass"
        assert "compatible" in result.description

    def test_pass_minimum_kernel(self, checker: PreflightChecker) -> None:
        with patch("preflight.os.uname") as mock_uname:
            mock_uname.return_value = MagicMock(release="5.15.0-100-generic")
            result = checker._check_kernel_compatibility("driver_install")
        assert result.status == "pass"


class TestDkmsStatusPass:
    def test_pass_all_built(self, checker: PreflightChecker) -> None:
        dkms_output = (
            "nvidia/535.183.01, 6.8.0-45-generic, x86_64: installed\n"
            "nvidia/535.183.01, 6.5.0-44-generic, x86_64: installed\n"
        )
        with patch("preflight.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=dkms_output)
            result = checker._check_dkms_status()
        assert result.status == "pass"
        assert "built for all" in result.description

    def test_pass_dkms_not_installed(self, checker: PreflightChecker) -> None:
        with patch("preflight.subprocess.run", side_effect=FileNotFoundError):
            result = checker._check_dkms_status()
        assert result.status == "pass"
        assert "not installed" in result.description.lower()


class TestRunAllChecksPass:
    def test_all_pass_overall(self, checker: PreflightChecker) -> None:
        pass_result = CheckResult(name="test", status="pass", description="ok")
        with (
            patch.object(checker, "_check_disk_space", return_value=pass_result),
            patch.object(checker, "_check_kernel_headers", return_value=pass_result),
            patch.object(checker, "_check_dpkg_state", return_value=pass_result),
            patch.object(checker, "_check_secure_boot", return_value=pass_result),
            patch.object(checker, "_check_kernel_compatibility", return_value=pass_result),
            patch.object(checker, "_check_dkms_status", return_value=pass_result),
        ):
            result = checker.run_all_checks("driver_install")

        assert result.overall_pass is True
        assert len(result.checks) == 6
        assert result.duration_ms >= 0

    def test_duration_is_measured(self, checker: PreflightChecker) -> None:
        pass_result = CheckResult(name="test", status="pass", description="ok")
        with (
            patch.object(checker, "_check_disk_space", return_value=pass_result),
            patch.object(checker, "_check_kernel_headers", return_value=pass_result),
            patch.object(checker, "_check_dpkg_state", return_value=pass_result),
            patch.object(checker, "_check_secure_boot", return_value=pass_result),
            patch.object(checker, "_check_kernel_compatibility", return_value=pass_result),
            patch.object(checker, "_check_dkms_status", return_value=pass_result),
        ):
            result = checker.run_all_checks("driver_install")

        assert isinstance(result.duration_ms, int)


# ═══════════════════════════════════════════════════════════════════
# Task 11: Individual check FAILURE scenarios
# ═══════════════════════════════════════════════════════════════════


class TestDiskSpaceFail:
    def test_fail_insufficient_space(self, checker: PreflightChecker) -> None:
        mock_stat = MagicMock()
        mock_stat.f_bavail = 500 * 1024  # 500 MB in 1K blocks
        mock_stat.f_frsize = 1024
        with patch("preflight.os.statvfs", return_value=mock_stat):
            result = checker._check_disk_space()
        assert result.status == "fail"
        assert "Insufficient" in result.description

    def test_warn_low_space(self, checker: PreflightChecker) -> None:
        mock_stat = MagicMock()
        mock_stat.f_bavail = int(1.5 * 1024 * 1024)  # 1.5 GB in 1K blocks
        mock_stat.f_frsize = 1024
        with patch("preflight.os.statvfs", return_value=mock_stat):
            result = checker._check_disk_space()
        assert result.status == "warn"
        assert "Low disk space" in result.description


class TestKernelHeadersFail:
    def test_fail_missing_headers(self, checker: PreflightChecker) -> None:
        with (
            patch("preflight.os.uname") as mock_uname,
            patch("preflight.subprocess.run") as mock_run,
        ):
            mock_uname.return_value = MagicMock(release="6.8.0-45-generic")
            mock_run.return_value = MagicMock(returncode=1, stdout="dpkg-query: no packages found")
            result = checker._check_kernel_headers()
        assert result.status == "fail"
        assert "not installed" in result.description
        assert "sudo apt install" in result.description


class TestDpkgStateFail:
    def test_fail_lock_held(self, checker: PreflightChecker) -> None:
        with patch.object(
            PreflightChecker,
            "_check_dpkg_lock",
            return_value=(True, "apt-get", 12345),
        ):
            result = checker._check_dpkg_state()
        assert result.status == "fail"
        assert "apt-get" in result.description
        assert "12345" in result.description

    def test_fail_broken_packages(self, checker: PreflightChecker) -> None:
        with (
            patch.object(
                PreflightChecker,
                "_check_dpkg_lock",
                return_value=(False, "", 0),
            ),
            patch("preflight.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="The following packages have been unpacked but not yet configured:\n  nvidia-driver-535",
            )
            result = checker._check_dpkg_state()
        assert result.status == "fail"
        assert "Broken packages" in result.description

    def test_lock_identifies_process(self, checker: PreflightChecker) -> None:
        with patch.object(
            PreflightChecker,
            "_check_dpkg_lock",
            return_value=(True, "unattended-upgr", 9999),
        ):
            result = checker._check_dpkg_state()
        assert result.status == "fail"
        assert "unattended-upgr" in result.description
        assert "9999" in result.description


class TestSecureBootWarn:
    def test_warn_no_mok_enrolled(self, checker: PreflightChecker) -> None:
        with patch("preflight.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="SecureBoot enabled"),
                MagicMock(returncode=1, stdout=""),
            ]
            result = checker._check_secure_boot()
        assert result.status == "warn"
        assert "no MOK keys" in result.description


class TestKernelCompatibilityFail:
    def test_fail_kernel_too_old(self, checker: PreflightChecker) -> None:
        with patch("preflight.os.uname") as mock_uname:
            mock_uname.return_value = MagicMock(release="4.15.0-200-generic")
            result = checker._check_kernel_compatibility("driver_install")
        assert result.status == "fail"
        assert "too old" in result.description

    def test_warn_kernel_very_new(self, checker: PreflightChecker) -> None:
        with patch("preflight.os.uname") as mock_uname:
            mock_uname.return_value = MagicMock(release="7.0.0-1-generic")
            result = checker._check_kernel_compatibility("driver_install")
        assert result.status == "warn"
        assert "newer than tested" in result.description


class TestDkmsStatusFail:
    def test_fail_broken_builds(self, checker: PreflightChecker) -> None:
        dkms_output = "nvidia/535.183.01, 6.8.0-45-generic, x86_64: broken\n"
        with patch("preflight.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=dkms_output)
            result = checker._check_dkms_status()
        assert result.status == "fail"
        assert "build failed" in result.description

    def test_warn_missing_for_kernel(self, checker: PreflightChecker) -> None:
        dkms_output = (
            "nvidia/535.183.01, 6.8.0-45-generic, x86_64: installed\n"
            "nvidia/535.183.01, 6.5.0-44-generic, x86_64: added\n"
        )
        with patch("preflight.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=dkms_output)
            result = checker._check_dkms_status()
        assert result.status == "warn"
        assert "missing" in result.description


class TestRunAllChecksFail:
    def test_overall_fail_when_any_fails(self, checker: PreflightChecker) -> None:
        pass_result = CheckResult(name="ok", status="pass", description="ok")
        fail_result = CheckResult(name="bad", status="fail", description="bad")
        with (
            patch.object(checker, "_check_disk_space", return_value=fail_result),
            patch.object(checker, "_check_kernel_headers", return_value=pass_result),
            patch.object(checker, "_check_dpkg_state", return_value=pass_result),
            patch.object(checker, "_check_secure_boot", return_value=pass_result),
            patch.object(checker, "_check_kernel_compatibility", return_value=pass_result),
            patch.object(checker, "_check_dkms_status", return_value=pass_result),
        ):
            result = checker.run_all_checks("driver_install")

        assert result.overall_pass is False

    def test_multiple_failures_all_reported(self, checker: PreflightChecker) -> None:
        fail1 = CheckResult(name="disk", status="fail", description="no space")
        fail2 = CheckResult(name="dpkg", status="fail", description="locked")
        pass_result = CheckResult(name="ok", status="pass", description="ok")
        with (
            patch.object(checker, "_check_disk_space", return_value=fail1),
            patch.object(checker, "_check_kernel_headers", return_value=pass_result),
            patch.object(checker, "_check_dpkg_state", return_value=fail2),
            patch.object(checker, "_check_secure_boot", return_value=pass_result),
            patch.object(checker, "_check_kernel_compatibility", return_value=pass_result),
            patch.object(checker, "_check_dkms_status", return_value=pass_result),
        ):
            result = checker.run_all_checks("driver_install")

        assert result.overall_pass is False
        failed = [c for c in result.checks if c.status == "fail"]
        assert len(failed) == 2

    def test_warn_does_not_block(self, checker: PreflightChecker) -> None:
        warn_result = CheckResult(name="sb", status="warn", description="warning")
        pass_result = CheckResult(name="ok", status="pass", description="ok")
        with (
            patch.object(checker, "_check_disk_space", return_value=pass_result),
            patch.object(checker, "_check_kernel_headers", return_value=pass_result),
            patch.object(checker, "_check_dpkg_state", return_value=pass_result),
            patch.object(checker, "_check_secure_boot", return_value=warn_result),
            patch.object(checker, "_check_kernel_compatibility", return_value=pass_result),
            patch.object(checker, "_check_dkms_status", return_value=pass_result),
        ):
            result = checker.run_all_checks("driver_install")

        assert result.overall_pass is True


# ═══════════════════════════════════════════════════════════════════
# Task 12: Edge cases and validation
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_mokutil_not_installed(self, checker: PreflightChecker) -> None:
        with patch("preflight.subprocess.run", side_effect=FileNotFoundError):
            result = checker._check_secure_boot()
        assert result.status == "warn"
        assert "mokutil" in result.description.lower()

    def test_dkms_not_installed(self, checker: PreflightChecker) -> None:
        with patch("preflight.subprocess.run", side_effect=FileNotFoundError):
            result = checker._check_dkms_status()
        assert result.status == "pass"

    def test_subprocess_timeout_kernel_headers(self, checker: PreflightChecker) -> None:
        with (
            patch("preflight.os.uname") as mock_uname,
            patch(
                "preflight.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="dpkg-query", timeout=10),
            ),
        ):
            mock_uname.return_value = MagicMock(release="6.8.0-45-generic")
            result = checker._check_kernel_headers()
        assert result.status == "warn"
        assert "Timed out" in result.description

    def test_subprocess_timeout_dpkg_audit(self, checker: PreflightChecker) -> None:
        with (
            patch.object(
                PreflightChecker,
                "_check_dpkg_lock",
                return_value=(False, "", 0),
            ),
            patch(
                "preflight.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="dpkg", timeout=10),
            ),
        ):
            result = checker._check_dpkg_state()
        assert result.status == "warn"

    def test_subprocess_timeout_dkms(self, checker: PreflightChecker) -> None:
        with patch(
            "preflight.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="dkms", timeout=10),
        ):
            result = checker._check_dkms_status()
        assert result.status == "warn"

    def test_statvfs_oserror(self, checker: PreflightChecker) -> None:
        with patch("preflight.os.statvfs", side_effect=OSError("no mount")):
            result = checker._check_disk_space()
        assert result.status == "warn"

    def test_dpkg_query_not_found(self, checker: PreflightChecker) -> None:
        with (
            patch("preflight.os.uname") as mock_uname,
            patch("preflight.subprocess.run", side_effect=FileNotFoundError),
        ):
            mock_uname.return_value = MagicMock(release="6.8.0-45-generic")
            result = checker._check_kernel_headers()
        assert result.status == "warn"
        assert "not found" in result.description.lower()

    def test_unparseable_kernel_version(self, checker: PreflightChecker) -> None:
        with patch("preflight.os.uname") as mock_uname:
            mock_uname.return_value = MagicMock(release="not-a-version")
            result = checker._check_kernel_compatibility("driver_install")
        assert result.status == "warn"
        assert "Cannot parse" in result.description

    def test_dpkg_lock_file_not_found(self) -> None:
        with patch("preflight.os.open", side_effect=FileNotFoundError):
            locked, _name, _pid = PreflightChecker._check_dpkg_lock()
        assert locked is False

    def test_dpkg_lock_permission_error_signals_caller(self) -> None:
        with patch("preflight.os.open", side_effect=PermissionError):
            locked, name, _pid = PreflightChecker._check_dpkg_lock()
        assert locked is False
        assert name == "permission_denied"

    def test_dpkg_state_warns_on_permission_denied(self, checker: PreflightChecker) -> None:
        with patch.object(
            PreflightChecker,
            "_check_dpkg_lock",
            return_value=(False, "permission_denied", 0),
        ):
            result = checker._check_dpkg_state()
        assert result.status == "warn"
        assert "permission" in result.description.lower()

    def test_dpkg_lock_held_detected(self) -> None:
        mock_fd = 99
        with (
            patch("preflight.os.open", return_value=mock_fd),
            patch("preflight.fcntl.flock", side_effect=BlockingIOError),
            patch.object(PreflightChecker, "_find_lock_holder", return_value=1234),
            patch.object(PreflightChecker, "_get_process_name", return_value="apt-get"),
            patch("preflight.os.close"),
        ):
            locked, name, pid = PreflightChecker._check_dpkg_lock()
        assert locked is True
        assert name == "apt-get"
        assert pid == 1234


class TestValidationIntegration:
    """Test that validate_operation_name works with preflight operations."""

    def test_driver_install_valid(self) -> None:
        from validators import validate_operation_name

        assert validate_operation_name("driver_install") == "driver_install"

    def test_driver_switch_valid(self) -> None:
        from validators import validate_operation_name

        assert validate_operation_name("driver_switch") == "driver_switch"

    def test_driver_rollback_valid(self) -> None:
        from validators import validate_operation_name

        assert validate_operation_name("driver_rollback") == "driver_rollback"

    def test_invalid_operation_rejected(self) -> None:
        from validators import validate_operation_name

        with pytest.raises(ValueError):
            validate_operation_name("drop_tables")

    def test_empty_operation_rejected(self) -> None:
        from validators import validate_operation_name

        with pytest.raises(ValueError):
            validate_operation_name("")
