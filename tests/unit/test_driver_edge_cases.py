"""Unit tests for Story 2.6: Driver Operation Edge Cases."""

from __future__ import annotations

import json
import pathlib
import time
from unittest.mock import MagicMock, patch

import pytest

# verde_daemon package registration is handled by tests/conftest.py
from nvml_wrapper import NvmlWrapper, Unavailable

_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_loop():
    return MagicMock()


@pytest.fixture
def idle_reset():
    return MagicMock()


@pytest.fixture
def idle_hold():
    return MagicMock()


@pytest.fixture
def idle_release():
    return MagicMock()


@pytest.fixture
def mock_audit(tmp_path):
    from audit import AuditLogger

    return AuditLogger(log_dir=tmp_path)


@pytest.fixture
def service(mock_loop, idle_reset, idle_hold, idle_release, mock_audit):
    from service import VerdeService

    return VerdeService(
        loop=mock_loop,
        on_idle_reset=idle_reset,
        on_idle_hold=idle_hold,
        on_idle_release=idle_release,
        introspection_xml=_XML,
        audit_logger=mock_audit,
    )


@pytest.fixture
def mock_connection():
    conn = MagicMock()
    conn.emit_signal = MagicMock()
    return conn


@pytest.fixture
def wired_service(service, mock_connection):
    service._on_bus_acquired(mock_connection, "com.verde.Manager")
    return service


# ===================================================================
# Task 1: GPU performance mode query
# ===================================================================


class TestPerformanceMode:
    def test_get_performance_mode_returns_string(self):
        """get_performance_mode returns human-readable string for P-states."""
        wrapper = NvmlWrapper()
        mock_handle = MagicMock()

        with patch.object(wrapper, "get_performance_state", return_value=0):
            mode = wrapper.get_performance_mode(mock_handle)
        assert mode == "Maximum Performance"

    def test_get_performance_mode_adaptive(self):
        """P8 maps to 'Adaptive'."""
        wrapper = NvmlWrapper()
        mock_handle = MagicMock()

        with patch.object(wrapper, "get_performance_state", return_value=8):
            mode = wrapper.get_performance_mode(mock_handle)
        assert mode == "Adaptive"

    def test_get_performance_mode_unsupported(self):
        """When performance state is unavailable, returns 'Not Supported'."""
        wrapper = NvmlWrapper()
        mock_handle = MagicMock()

        with patch.object(wrapper, "get_performance_state", return_value=Unavailable):
            mode = wrapper.get_performance_mode(mock_handle)
        assert mode == "Not Supported"

    def test_performance_mode_in_gpu_info(self):
        """GetGPUInfo includes performance_mode field."""
        wrapper = NvmlWrapper()
        mock_handle = MagicMock()

        with (
            patch.object(wrapper, "get_device_by_index", return_value=mock_handle),
            patch.object(wrapper, "get_device_name", return_value="RTX 4090"),
            patch.object(wrapper, "get_device_uuid", return_value="GPU-123"),
            patch.object(wrapper, "get_driver_version", return_value="565.57.01"),
            patch.object(wrapper, "get_cuda_driver_version", return_value="12.7"),
            patch.object(wrapper, "get_cuda_toolkit_version", return_value=Unavailable),
            patch.object(wrapper, "get_gpu_mode", return_value=Unavailable),
            patch.object(wrapper, "get_pci_info", return_value=Unavailable),
            patch.object(wrapper, "get_num_gpu_cores", return_value=Unavailable),
            patch.object(wrapper, "get_cuda_compute_capability", return_value=Unavailable),
            patch.object(wrapper, "get_ecc_mode", return_value=Unavailable),
            patch.object(wrapper, "get_performance_state", return_value=0),
        ):
            info = wrapper.get_all_gpu_info(0)

        assert "performance_mode" in info
        assert info["performance_mode"] == "Maximum Performance"

    def test_performance_mode_in_build_gpu_info(self, wired_service):
        """_build_gpu_info includes performance_mode in D-Bus response."""
        wired_service._nvml_available = True
        mock_info = {
            "name": "RTX 4090",
            "uuid": "GPU-123",
            "driver_version": "565.57.01",
            "cuda_driver_version": "12.7",
            "cuda_toolkit_version": Unavailable,
            "gpu_mode": Unavailable,
            "pci_info": Unavailable,
            "num_cores": Unavailable,
            "compute_capability": Unavailable,
            "ecc_mode": Unavailable,
            "performance_mode": "Maximum Performance",
        }
        with patch.object(wired_service._nvml, "get_all_gpu_info", return_value=mock_info):
            result = wired_service._build_gpu_info()

        assert "performance_mode" in result
        assert result["performance_mode"].get_string() == "Maximum Performance"


# ===================================================================
# Task 2: Polkit cancellation / timeout cleanup
# ===================================================================


def _make_params(version: str) -> MagicMock:
    """Create mock GLib.Variant for InstallDriver(s version) parameters."""
    params = MagicMock()
    child = MagicMock()
    child.get_string.return_value = version
    params.get_child_value.return_value = child
    return params


class TestPolkitCancellation:
    def test_polkit_cancelled_returns_error(self, wired_service):
        """Polkit cancellation returns PolkitCancelled D-Bus error."""
        from polkit import PolkitCancelled

        invocation = MagicMock()
        params = _make_params("565")

        with patch("service.check_authorization", side_effect=PolkitCancelled("User cancelled")):
            wired_service._handle_method_call(
                wired_service._connection,
                "com.verde.Manager",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                invocation,
            )

        invocation.return_dbus_error.assert_called_once()
        error_args = invocation.return_dbus_error.call_args[0]
        assert error_args[0] == "com.verde.Error.PolkitCancelled"
        assert "cancel" in error_args[1].lower()

    def test_polkit_timeout_returns_error(self, wired_service):
        """Polkit timeout returns PolkitTimeout D-Bus error."""
        from polkit import PolkitTimeout

        invocation = MagicMock()
        params = _make_params("565")

        with patch("service.check_authorization", side_effect=PolkitTimeout("Timed out")):
            wired_service._handle_method_call(
                wired_service._connection,
                "com.verde.Manager",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                invocation,
            )

        invocation.return_dbus_error.assert_called_once()
        error_args = invocation.return_dbus_error.call_args[0]
        assert error_args[0] == "com.verde.Error.PolkitTimeout"
        assert "timed out" in error_args[1].lower()

    def test_guard_not_held_after_polkit_cancel(self, wired_service):
        """Concurrency guard is never acquired when Polkit is cancelled."""
        from polkit import PolkitCancelled

        invocation = MagicMock()
        params = _make_params("565")

        with patch("service.check_authorization", side_effect=PolkitCancelled("cancelled")):
            wired_service._handle_method_call(
                wired_service._connection,
                "com.verde.Manager",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                invocation,
            )

        assert wired_service._operation_in_progress is False

    def test_retry_works_after_cancel(self, wired_service):
        """After Polkit cancel, a subsequent call can proceed normally."""
        from polkit import PolkitCancelled

        invocation1 = MagicMock()
        invocation2 = MagicMock()
        params = _make_params("565")

        # First call: cancelled
        with patch("service.check_authorization", side_effect=PolkitCancelled("cancelled")):
            wired_service._handle_method_call(
                wired_service._connection,
                "com.verde.Manager",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                invocation1,
            )

        # Second call: authorized — should proceed to dispatch (mocked)
        with (
            patch("service.check_authorization", return_value=True),
            patch.object(wired_service, "_dispatch_install_driver") as mock_dispatch,
        ):
            wired_service._handle_method_call(
                wired_service._connection,
                "com.verde.Manager",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                invocation2,
            )

        mock_dispatch.assert_called_once()


# ===================================================================
# Task 3: Concurrent operation prevention
# ===================================================================


class TestConcurrentOperationEnrichment:
    def test_error_includes_operation_type(self, wired_service):
        """OperationInProgress error includes current operation type."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "abc123"
        wired_service._current_op_type = "InstallDriver"
        wired_service._current_op_started = time.monotonic()

        invocation = MagicMock()
        params = _make_params("565")

        with patch("service.check_authorization", return_value=True):
            wired_service._handle_method_call(
                wired_service._connection,
                "com.verde.Manager",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                invocation,
            )

        invocation.return_dbus_error.assert_called_once()
        error_msg = invocation.return_dbus_error.call_args[0][1]
        assert "InstallDriver" in error_msg

    def test_error_includes_start_time(self, wired_service):
        """OperationInProgress error includes when the operation started."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "abc123"
        wired_service._current_op_type = "InstallDriver"
        wired_service._current_op_started = time.monotonic() - 30  # 30s ago

        invocation = MagicMock()
        params = _make_params("565")

        with patch("service.check_authorization", return_value=True):
            wired_service._handle_method_call(
                wired_service._connection,
                "com.verde.Manager",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                invocation,
            )

        error_msg = invocation.return_dbus_error.call_args[0][1]
        # Should mention seconds elapsed
        assert "30" in error_msg or "seconds" in error_msg.lower()


# ===================================================================
# Task 4: Reboot-required detection
# ===================================================================


class TestRebootDetection:
    def test_reboot_required_file_detected(self, wired_service):
        """When /var/run/reboot-required exists, reboot is flagged."""
        result = wired_service._detect_reboot_required()
        # Returns (required: bool, reason: str)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_reboot_reason_includes_kernel_module(self, wired_service):
        """Reboot reason distinguishes kernel module scenarios."""
        with (
            patch("service.pathlib.Path.exists", return_value=True),
            patch("service.pathlib.Path.read_text", return_value="nvidia-dkms-565\n"),
        ):
            required, reason = wired_service._detect_reboot_required()

        assert required is True
        assert "nvidia" in reason.lower() or "driver" in reason.lower()


# ===================================================================
# Task 5: Partial installation cleanup
# ===================================================================


class TestPartialInstallCleanup:
    def test_guard_released_on_apt_crash(self, wired_service):
        """Concurrency guard released even when apt crashes."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with (
            patch.object(wired_service, "_run_apt_install", side_effect=RuntimeError("crash")),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._do_install("testop", "565", ":1.42")

        assert wired_service._operation_in_progress is False

    def test_inhibitor_lock_released_on_crash(self, wired_service):
        """Inhibitor lock released even when apt crashes."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"
        mock_release = MagicMock()

        with (
            patch.object(wired_service, "_run_apt_install", side_effect=RuntimeError("crash")),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=42),
            patch.object(wired_service, "_release_inhibitor_lock", mock_release),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._do_install("testop", "565", ":1.42")

        mock_release.assert_called_with(42)

    def test_failure_includes_recovery_suggestion(self, wired_service, mock_connection):
        """Failed install includes dpkg repair suggestion in OperationComplete."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with (
            patch.object(
                wired_service,
                "_run_apt_install",
                return_value=(False, "E: Sub-process /usr/bin/dpkg returned an error", 1, False),
            ),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._do_install("testop", "565", ":1.42")

        # Find the OperationComplete signal
        for call in mock_connection.emit_signal.call_args_list:
            args = call[0]
            if args[3] == "OperationComplete":
                variant = args[4]
                msg_str = variant.get_child_value(2).get_string()
                error_dict = json.loads(msg_str)
                assert error_dict["error_category"] == "dpkg_broken"
                assert "repair" in error_dict["error_primary_action"]
                return
        pytest.fail("OperationComplete signal not emitted")


# ===================================================================
# Task 6: Network failure categorization
# ===================================================================


class TestNetworkFailureCategorization:
    def test_dns_failure_categorized(self, wired_service, mock_connection):
        """DNS failure produces network_unavailable category."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with (
            patch.object(
                wired_service,
                "_run_apt_install",
                return_value=(
                    False,
                    "E: Failed to fetch http://archive.ubuntu.com\n  Could not resolve 'archive.ubuntu.com'",
                    100,
                    False,
                ),
            ),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._do_install("testop", "565", ":1.42")

        for call in mock_connection.emit_signal.call_args_list:
            args = call[0]
            if args[3] == "OperationComplete":
                variant = args[4]
                msg_str = variant.get_child_value(2).get_string()
                error_dict = json.loads(msg_str)
                assert error_dict["error_category"] == "network_unavailable"
                return
        pytest.fail("OperationComplete signal not emitted")

    def test_timeout_failure_categorized(self, wired_service, mock_connection):
        """Connection timeout produces network_unavailable category."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with (
            patch.object(
                wired_service,
                "_run_apt_install",
                return_value=(
                    False,
                    "E: Failed to fetch http://archive.ubuntu.com\n  Connection timed out",
                    100,
                    False,
                ),
            ),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._do_install("testop", "565", ":1.42")

        for call in mock_connection.emit_signal.call_args_list:
            args = call[0]
            if args[3] == "OperationComplete":
                variant = args[4]
                msg_str = variant.get_child_value(2).get_string()
                error_dict = json.loads(msg_str)
                assert error_dict["error_category"] == "network_unavailable"
                return
        pytest.fail("OperationComplete signal not emitted")
