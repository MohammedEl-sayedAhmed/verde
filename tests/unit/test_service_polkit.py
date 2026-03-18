"""Unit tests for Polkit + validation integration in VerdeService."""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

# verde_daemon package registration is handled by tests/conftest.py
_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


@pytest.fixture
def mock_loop():
    return MagicMock()


@pytest.fixture
def idle_reset():
    return MagicMock()


@pytest.fixture
def service(mock_loop, idle_reset):
    from service import VerdeService

    return VerdeService(
        loop=mock_loop,
        on_idle_reset=idle_reset,
        introspection_xml=_XML,
    )


@pytest.fixture
def mock_invocation():
    return MagicMock()


@pytest.fixture
def mock_connection():
    conn = MagicMock()
    conn.register_object.return_value = 42
    return conn


class TestPolkitIntegration:
    @patch("service.check_authorization", return_value=True)
    def test_privileged_method_triggers_polkit_check(
        self, mock_auth, service, mock_invocation, mock_connection
    ):
        """InstallDriver triggers Polkit check before dispatch."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        params = MagicMock()
        child = MagicMock()
        child.get_string.return_value = "535"
        params.get_child_value.return_value = child

        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "InstallDriver",
            params,
            mock_invocation,
        )
        mock_auth.assert_called_once_with(mock_connection, ":1.42", "com.verde.driver.manage")

    @patch("service.check_authorization", return_value=False)
    def test_failed_auth_returns_not_authorized(
        self, mock_auth, service, mock_invocation, mock_connection
    ):
        """Failed Polkit auth returns com.verde.Error.NotAuthorized."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        # Provide valid params — validation runs before auth now
        params = MagicMock()
        child = MagicMock()
        child.get_string.return_value = "535"
        params.get_child_value.return_value = child

        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "InstallDriver",
            params,
            mock_invocation,
        )
        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.NotAuthorized"

    def test_invalid_param_returns_invalid_argument_before_auth(
        self, service, mock_invocation, mock_connection
    ):
        """Invalid parameter returns com.verde.Error.InvalidArgument before Polkit auth."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        params = MagicMock()
        child = MagicMock()
        child.get_string.return_value = "not-a-version; rm -rf /"
        params.get_child_value.return_value = child

        with patch("service.check_authorization") as mock_auth:
            service._handle_method_call(
                mock_connection,
                ":1.42",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                mock_invocation,
            )
            # Auth should NOT be called — validation rejects first
            mock_auth.assert_not_called()

        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.InvalidArgument"

    def test_ping_skips_polkit(self, service, mock_invocation, mock_connection, idle_reset):
        """Ping does NOT trigger Polkit check."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        with patch("service.check_authorization") as mock_auth:
            service._handle_method_call(
                mock_connection,
                ":1.42",
                "/com/verde/Manager",
                "com.verde.Manager",
                "Ping",
                None,
                mock_invocation,
            )
            mock_auth.assert_not_called()

        mock_invocation.return_value.assert_called_once_with(None)
        idle_reset.assert_called_once()

    @patch("service.check_authorization", return_value=True)
    def test_read_only_method_uses_monitor_action(
        self, mock_auth, service, mock_invocation, mock_connection
    ):
        """GetGPUInfo triggers com.verde.monitor action check."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "GetGPUInfo",
            None,
            mock_invocation,
        )
        mock_auth.assert_called_once_with(mock_connection, ":1.42", "com.verde.monitor")

    @patch("service.check_authorization", return_value=True)
    def test_authorized_method_resets_idle(
        self, mock_auth, service, mock_invocation, mock_connection, idle_reset
    ):
        """Authorized method call resets idle timer."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "GetGPUInfo",
            None,
            mock_invocation,
        )
        idle_reset.assert_called_once()

    @patch("service.check_authorization", return_value=False)
    def test_unauthorized_method_does_not_reset_idle(
        self, mock_auth, service, mock_invocation, mock_connection, idle_reset
    ):
        """Unauthorized method call does NOT reset idle timer."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "InstallDriver",
            None,
            mock_invocation,
        )
        idle_reset.assert_not_called()
