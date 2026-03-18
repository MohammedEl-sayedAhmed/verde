"""Unit tests for apt error classification and recovery (Story 2.5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "verde-daemon"))

from verde_daemon.apt_errors import (
    AptErrorCategory,
    AptErrorResponse,
    analyze_dkms_failure,
    classify_apt_error,
    detect_dpkg_broken,
    detect_dpkg_lock,
    detect_interrupted_operation,
    is_network_error,
)

# ===================================================================
# Task 1: AptErrorCategory enum and AptErrorResponse dataclass
# ===================================================================


class TestAptErrorCategory:
    def test_all_categories_exist(self):
        expected = {
            "DPKG_BROKEN",
            "DPKG_LOCKED",
            "NETWORK_UNAVAILABLE",
            "DKMS_BUILD_FAILURE",
            "DEPENDENCY_CONFLICT",
            "INTERRUPTED_OPERATION",
            "POLKIT_MISSING",
            "SUBPROCESS_TIMEOUT",
            "UNKNOWN",
        }
        actual = {c.name for c in AptErrorCategory}
        assert expected == actual

    def test_category_values_are_lowercase(self):
        for c in AptErrorCategory:
            assert c.value == c.name.lower()


class TestAptErrorResponse:
    def test_required_fields(self):
        resp = AptErrorResponse(
            category=AptErrorCategory.UNKNOWN,
            title="An unexpected error occurred",
            description="Something went wrong.",
            primary_action="Generate Diagnostic Report",
            secondary_action="Retry",
            raw_output="traceback...",
            recoverable=True,
        )
        assert resp.category == AptErrorCategory.UNKNOWN
        assert resp.title == "An unexpected error occurred"
        assert resp.description == "Something went wrong."
        assert resp.primary_action == "Generate Diagnostic Report"
        assert resp.secondary_action == "Retry"
        assert resp.raw_output == "traceback..."
        assert resp.recoverable is True

    def test_to_dbus_dict_excludes_raw_output(self):
        resp = AptErrorResponse(
            category=AptErrorCategory.DPKG_BROKEN,
            title="Package system needs repair",
            description="The package system was left in a broken state.",
            primary_action="repair_dpkg",
            secondary_action="rollback",
            raw_output="E: dpkg was interrupted, you must manually run...",
            recoverable=True,
        )
        d = resp.to_dbus_dict()
        assert "raw_output" not in d
        assert d["error_category"] == "dpkg_broken"
        assert d["error_title"] == "Package system needs repair"
        assert d["error_description"] == "The package system was left in a broken state."
        assert d["error_primary_action"] == "repair_dpkg"
        assert d["error_secondary_action"] == "rollback"
        assert d["recoverable"] is True
        assert d["success"] is False

    def test_no_raw_output_in_title_or_description(self):
        """AC #5: no raw terminal output surfaced to user."""
        resp = AptErrorResponse(
            category=AptErrorCategory.DPKG_BROKEN,
            title="Package system needs repair",
            description="The package system was left in a broken state.",
            primary_action="repair_dpkg",
            secondary_action="rollback",
            raw_output="E: Sub-process /usr/bin/dpkg returned an error code (1)",
            recoverable=True,
        )
        d = resp.to_dbus_dict()
        assert "Sub-process" not in d["error_title"]
        assert "Sub-process" not in d["error_description"]


# ===================================================================
# Task 2: dpkg lock detection
# ===================================================================


class TestDetectDpkgLock:
    def test_lock_free_returns_none(self):
        """When lock file can be acquired, returns None."""

        with (
            patch("verde_daemon.apt_errors.os.open", return_value=99),
            patch("verde_daemon.apt_errors.fcntl.flock") as mock_flock,
            patch("verde_daemon.apt_errors.os.close"),
        ):
            result = detect_dpkg_lock()
        assert result is None
        # Should have been called twice: lock then unlock
        assert mock_flock.call_count == 2

    def test_lock_held_returns_error(self):
        """When lock is held by another process, returns DPKG_LOCKED response."""
        import errno
        import fcntl

        def flock_side_effect(fd, operation):
            if operation == (fcntl.LOCK_EX | fcntl.LOCK_NB):
                raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

        with (
            patch("verde_daemon.apt_errors.os.open", return_value=99),
            patch("verde_daemon.apt_errors.fcntl.flock", side_effect=flock_side_effect),
            patch("verde_daemon.apt_errors.os.close"),
            patch(
                "verde_daemon.apt_errors._identify_lock_holder", return_value="apt-get (pid 1234)"
            ),
        ):
            result = detect_dpkg_lock()

        assert result is not None
        assert result.category == AptErrorCategory.DPKG_LOCKED
        assert "busy" in result.title.lower()
        assert result.recoverable is True

    def test_lock_file_missing_returns_none(self):
        """When lock file doesn't exist, returns None (proceed with operation)."""
        with patch(
            "verde_daemon.apt_errors.os.open",
            side_effect=FileNotFoundError("No such file"),
        ):
            result = detect_dpkg_lock()
        assert result is None

    def test_error_message_includes_blocking_process(self):
        """AC #2: includes blocking process info when identifiable."""
        import errno
        import fcntl

        def flock_side_effect(fd, operation):
            if operation == (fcntl.LOCK_EX | fcntl.LOCK_NB):
                raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

        with (
            patch("verde_daemon.apt_errors.os.open", return_value=99),
            patch("verde_daemon.apt_errors.fcntl.flock", side_effect=flock_side_effect),
            patch("verde_daemon.apt_errors.os.close"),
            patch(
                "verde_daemon.apt_errors._identify_lock_holder", return_value="apt-get (pid 1234)"
            ),
        ):
            result = detect_dpkg_lock()

        assert "apt-get" in result.description


# ===================================================================
# Task 3: apt error classifier
# ===================================================================


class TestClassifyAptError:
    def test_dpkg_broken(self):
        stderr = "E: dpkg was interrupted, you must manually run 'sudo dpkg --configure -a' to correct the problem."
        resp = classify_apt_error(1, "", stderr)
        assert resp.category == AptErrorCategory.DPKG_BROKEN
        assert resp.recoverable is True

    def test_dpkg_locked(self):
        stderr = (
            "E: Could not get lock /var/lib/dpkg/lock-frontend - held by process 1234 (apt-get)"
        )
        resp = classify_apt_error(1, "", stderr)
        assert resp.category == AptErrorCategory.DPKG_LOCKED

    def test_network_unavailable(self):
        stderr = (
            "E: Failed to fetch http://archive.ubuntu.com/ubuntu/pool/restricted/n/nvidia-driver-565\n"
            "  Temporary failure resolving 'archive.ubuntu.com'\n"
        )
        resp = classify_apt_error(1, "", stderr)
        assert resp.category == AptErrorCategory.NETWORK_UNAVAILABLE

    def test_dkms_build_failure(self):
        stderr = "Error! Bad return status for module build on kernel: 6.5.0-44-generic\ndkms error: build failed"
        resp = classify_apt_error(1, "", stderr)
        assert resp.category == AptErrorCategory.DKMS_BUILD_FAILURE

    def test_dkms_missing_headers_via_classify(self):
        """P-6: classify_apt_error delegates to analyze_dkms_failure for targeted guidance."""
        stderr = (
            "dkms error: build failed\n"
            "Error! Your kernel headers for kernel 6.5.0 cannot be found at\n"
            "/lib/modules/6.5.0/build\nlinux-headers-6.5.0 missing"
        )
        resp = classify_apt_error(1, "", stderr)
        assert resp.category == AptErrorCategory.DKMS_BUILD_FAILURE
        assert "headers" in resp.description.lower()
        assert "linux-headers" in resp.primary_action

    def test_dependency_conflict(self):
        stderr = (
            "nvidia-driver-565 : Depends: libnvidia-gl-565 but it is not going to be installed"
        )
        resp = classify_apt_error(1, "", stderr)
        assert resp.category == AptErrorCategory.DEPENDENCY_CONFLICT

    def test_subprocess_timeout(self):
        resp = classify_apt_error(-1, "", "", timed_out=True)
        assert resp.category == AptErrorCategory.SUBPROCESS_TIMEOUT

    def test_unknown_error(self):
        resp = classify_apt_error(1, "", "Some completely unexpected error message")
        assert resp.category == AptErrorCategory.UNKNOWN
        assert resp.recoverable is True

    def test_all_responses_have_required_fields(self):
        """AC #4: all error responses include required fields."""
        test_cases = [
            (1, "", "E: dpkg was interrupted"),
            (1, "", "E: Could not get lock"),
            (1, "", "E: Failed to fetch"),
            (1, "", "dkms error: build failed"),
            (1, "", "Depends: foo but it is not going to be installed"),
            (1, "", "completely unknown error"),
        ]
        for rc, stdout, stderr in test_cases:
            resp = classify_apt_error(rc, stdout, stderr)
            assert resp.category is not None
            assert resp.title
            assert resp.description
            assert resp.primary_action
            assert resp.secondary_action

    def test_no_raw_stderr_in_title(self):
        """AC #5: no raw terminal output in user-facing fields."""
        stderr = "E: Sub-process /usr/bin/dpkg returned an error code (1)\nE: dpkg was interrupted"
        resp = classify_apt_error(1, "", stderr)
        assert "Sub-process" not in resp.title
        assert "/usr/bin/dpkg" not in resp.title


# ===================================================================
# Task 4: dpkg broken detection
# ===================================================================


class TestDetectDpkgBroken:
    def test_clean_state_returns_false(self):
        with patch("verde_daemon.apt_errors.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert detect_dpkg_broken() is False

    def test_broken_packages_returns_true(self):
        with patch("verde_daemon.apt_errors.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="The following packages are not configured yet:\n nvidia-dkms-565\n",
                stderr="",
            )
            assert detect_dpkg_broken() is True

    def test_timeout_returns_false(self):
        """If dpkg --audit times out, assume OK to avoid blocking."""
        import subprocess

        with patch(
            "verde_daemon.apt_errors.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="dpkg", timeout=10),
        ):
            assert detect_dpkg_broken() is False


# ===================================================================
# Task 5: interrupted operation detection
# ===================================================================


class TestDetectInterruptedOperation:
    def test_no_marker_file_returns_none(self):
        with patch("verde_daemon.apt_errors.Path.exists", return_value=False):
            result = detect_interrupted_operation()
        assert result is None

    def test_marker_plus_broken_dpkg_returns_error(self):
        marker_data = json.dumps(
            {
                "operation": "install_driver",
                "version": "565",
                "started_at": "2026-03-18T14:30:00+00:00",
            }
        )
        with (
            patch("verde_daemon.apt_errors.Path.exists", return_value=True),
            patch("verde_daemon.apt_errors.Path.read_text", return_value=marker_data),
            patch("verde_daemon.apt_errors.detect_dpkg_broken", return_value=True),
        ):
            result = detect_interrupted_operation()

        assert result is not None
        assert result.category == AptErrorCategory.INTERRUPTED_OPERATION
        assert "interrupted" in result.title.lower()
        assert result.recoverable is True

    def test_marker_plus_clean_dpkg_cleans_up(self):
        marker_data = json.dumps(
            {
                "operation": "install_driver",
                "version": "565",
            }
        )
        with (
            patch("verde_daemon.apt_errors.Path.exists", return_value=True),
            patch("verde_daemon.apt_errors.Path.read_text", return_value=marker_data),
            patch("verde_daemon.apt_errors.detect_dpkg_broken", return_value=False),
            patch("verde_daemon.apt_errors.Path.unlink") as mock_unlink,
        ):
            result = detect_interrupted_operation()

        assert result is None
        mock_unlink.assert_called_once()


# ===================================================================
# Task 6: DKMS failure analysis
# ===================================================================


class TestAnalyzeDkmsFailure:
    def test_missing_kernel_headers(self):
        stderr = (
            "Error! Your kernel headers for kernel 6.5.0-44-generic cannot be found.\n"
            "Please install the linux-headers-6.5.0-44-generic package"
        )
        resp = analyze_dkms_failure(stderr)
        assert resp.category == AptErrorCategory.DKMS_BUILD_FAILURE
        assert "header" in resp.primary_action.lower() or "header" in resp.description.lower()

    def test_compiler_error(self):
        stderr = "make[2]: *** [scripts/Makefile.build:288: /var/lib/dkms/nvidia/565/build/nvidia/nv.o] Error 1\ncc1: error: unrecognized"
        resp = analyze_dkms_failure(stderr)
        assert resp.category == AptErrorCategory.DKMS_BUILD_FAILURE
        assert "build" in resp.primary_action.lower()

    def test_module_version_mismatch(self):
        stderr = "Module nvidia/565 is not supported for kernel 6.8.0-44-generic"
        resp = analyze_dkms_failure(stderr)
        assert resp.category == AptErrorCategory.DKMS_BUILD_FAILURE

    def test_dkms_log_path_in_secondary_action(self):
        stderr = "Bad return status for module build on kernel: 6.5.0-44-generic"
        resp = analyze_dkms_failure(stderr)
        assert "log" in resp.secondary_action.lower() or "dkms" in resp.secondary_action.lower()


# ===================================================================
# Task 7: network error detection
# ===================================================================


class TestIsNetworkError:
    def test_failed_to_fetch(self):
        assert is_network_error("E: Failed to fetch http://archive.ubuntu.com/...")

    def test_could_not_resolve(self):
        assert is_network_error("Could not resolve 'archive.ubuntu.com'")

    def test_temporary_failure(self):
        assert is_network_error("Temporary failure resolving 'archive.ubuntu.com'")

    def test_connection_timed_out(self):
        assert is_network_error("Connection timed out [IP: 91.189.91.81 80]")

    def test_non_network_error(self):
        assert not is_network_error("E: dpkg was interrupted")


# ===================================================================
# Task 10-11: Additional classifier tests with realistic samples
# ===================================================================


class TestClassifyAptErrorRealisticSamples:
    """Test with full realistic stderr output from Ubuntu systems."""

    def test_dpkg_broken_full_output(self):
        stderr = (
            "E: dpkg was interrupted, you must manually run "
            "'sudo dpkg --configure -a' to correct the problem.\n"
            "E: Sub-process /usr/bin/dpkg returned an error code (1)"
        )
        resp = classify_apt_error(1, "", stderr)
        assert resp.category == AptErrorCategory.DPKG_BROKEN
        assert resp.recoverable is True
        assert "repair" in resp.primary_action.lower() or "dpkg" in resp.primary_action.lower()

    def test_network_failure_full_output(self):
        stderr = (
            "E: Failed to fetch http://archive.ubuntu.com/ubuntu/pool/"
            "restricted/n/nvidia-driver-565/nvidia-driver-565_565.57.01-0ubuntu1_amd64.deb\n"
            "  Temporary failure resolving 'archive.ubuntu.com'\n"
            "E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?"
        )
        resp = classify_apt_error(100, "", stderr)
        assert resp.category == AptErrorCategory.NETWORK_UNAVAILABLE
        assert "download" in resp.title.lower() or "package" in resp.title.lower()

    def test_dpkg_lock_full_output(self):
        stderr = (
            "E: Could not get lock /var/lib/dpkg/lock-frontend. "
            "It is held by process 1234 (apt-get)\n"
            "N: Be aware that removing the lock file is not a solution."
        )
        resp = classify_apt_error(100, "", stderr)
        assert resp.category == AptErrorCategory.DPKG_LOCKED

    def test_dependency_conflict_full_output(self):
        stderr = (
            "The following packages have unmet dependencies:\n"
            " nvidia-driver-565 : Depends: libnvidia-gl-565 (= 565.57.01-0ubuntu1) "
            "but it is not going to be installed\n"
            "E: Unable to correct problems, you have held broken packages."
        )
        resp = classify_apt_error(100, "", stderr)
        assert resp.category == AptErrorCategory.DEPENDENCY_CONFLICT


# ===================================================================
# Task 12: Marker file lifecycle tests
# ===================================================================


class TestMarkerFileLifecycle:
    def test_write_marker_creates_file(self, tmp_path):
        from verde_daemon.apt_errors import write_operation_marker

        marker_path = tmp_path / "marker.json"
        write_operation_marker("install_driver", "565", marker_path=marker_path)
        assert marker_path.exists()
        data = json.loads(marker_path.read_text())
        assert data["operation"] == "install_driver"
        assert data["version"] == "565"
        assert "started_at" in data

    def test_write_marker_with_snapshot_id(self, tmp_path):
        from verde_daemon.apt_errors import write_operation_marker

        marker_path = tmp_path / "marker.json"
        write_operation_marker(
            "install_driver",
            "565",
            snapshot_id="2026-03-18T14:29:55_nvidia-560",
            marker_path=marker_path,
        )
        data = json.loads(marker_path.read_text())
        assert data["snapshot_id"] == "2026-03-18T14:29:55_nvidia-560"

    def test_remove_marker_deletes_file(self, tmp_path):
        from verde_daemon.apt_errors import remove_operation_marker, write_operation_marker

        marker_path = tmp_path / "marker.json"
        write_operation_marker("install_driver", "565", marker_path=marker_path)
        assert marker_path.exists()
        remove_operation_marker(marker_path=marker_path)
        assert not marker_path.exists()

    def test_remove_marker_noop_when_missing(self, tmp_path):
        from verde_daemon.apt_errors import remove_operation_marker

        marker_path = tmp_path / "nonexistent.json"
        # Should not raise
        remove_operation_marker(marker_path=marker_path)

    def test_interrupted_detection_with_real_marker(self, tmp_path):
        from verde_daemon.apt_errors import write_operation_marker

        marker_path = tmp_path / "marker.json"
        write_operation_marker("install_driver", "565", marker_path=marker_path)

        with patch("verde_daemon.apt_errors.detect_dpkg_broken", return_value=True):
            result = detect_interrupted_operation(marker_path=marker_path)

        assert result is not None
        assert result.category == AptErrorCategory.INTERRUPTED_OPERATION
        assert "565" in result.description


# ===================================================================
# Task 13: Additional DKMS analysis tests
# ===================================================================


class TestAnalyzeDkmsFailureAdditional:
    def test_generic_dkms_failure(self):
        stderr = "DKMS make.log contains errors, module build unsuccessful"
        resp = analyze_dkms_failure(stderr)
        assert resp.category == AptErrorCategory.DKMS_BUILD_FAILURE
        assert resp.recoverable is True

    def test_kernel_headers_includes_package_name(self):
        stderr = (
            "Error! Your kernel headers for kernel 6.5.0-44-generic cannot be found.\n"
            "Please install the linux-headers-6.5.0-44-generic package"
        )
        resp = analyze_dkms_failure(stderr)
        assert "linux-headers-6.5.0-44-generic" in resp.primary_action


# ===================================================================
# Task 14: D-Bus response conversion tests
# ===================================================================


class TestDbusResponseConversion:
    def test_all_categories_convert_to_dbus(self):
        """Every error category should produce a valid D-Bus dict."""
        for cat in AptErrorCategory:
            resp = AptErrorResponse(
                category=cat,
                title=f"Test {cat.name}",
                description="Test description",
                primary_action="test_action",
                secondary_action="test_alt",
                raw_output="raw stderr data",
                recoverable=True,
            )
            d = resp.to_dbus_dict()
            assert d["success"] is False
            assert d["error_category"] == cat.value
            assert "raw_output" not in d
            assert d["error_title"] == f"Test {cat.name}"
            assert d["recoverable"] is True

    def test_classify_then_convert_roundtrip(self):
        """classify_apt_error → to_dbus_dict roundtrip."""
        stderr = "E: Failed to fetch http://example.com/package.deb\nConnection timed out"
        resp = classify_apt_error(100, "", stderr)
        d = resp.to_dbus_dict()
        assert d["error_category"] == "network_unavailable"
        assert "raw_output" not in d
        # raw_output should still be on the original response for audit
        assert resp.raw_output == stderr

    def test_audit_gets_raw_output(self):
        """Raw output available for audit logging but not in D-Bus dict."""
        raw = "E: Sub-process /usr/bin/dpkg returned an error code (1)\ndetailed trace..."
        resp = classify_apt_error(1, "", "E: dpkg was interrupted\n" + raw)
        assert resp.raw_output  # Non-empty for audit
        d = resp.to_dbus_dict()
        assert "Sub-process" not in d.get("error_description", "")
        assert "trace" not in d.get("error_description", "")
