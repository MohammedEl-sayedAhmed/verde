"""Unit tests for Polkit authorization helper."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

# verde_daemon package registration is handled by tests/conftest.py
from polkit import (
    METHOD_ACTION_MAP,
    PolkitAgentMissing,
    PolkitCancelled,
    PolkitTimeout,
    check_authorization,
)

# ===================================================================
# METHOD_ACTION_MAP completeness
# ===================================================================


class TestMethodActionMap:
    EXPECTED_METHODS: ClassVar[set[str]] = {
        "InstallDriver",
        "RollbackDriver",
        "RepairDpkg",
        "ListSnapshots",
        "FixSuspend",
        "FixHibernate",
        "GenerateDiagnosticReport",
        "GetGPUInfo",
        "GetGPUStats",
        "GetCurrentDriver",
        "ListAvailableDrivers",
        "GetPowerStatus",
    }

    def test_all_12_methods_mapped(self):
        assert set(METHOD_ACTION_MAP.keys()) == self.EXPECTED_METHODS

    def test_driver_methods_map_to_driver_manage(self):
        assert METHOD_ACTION_MAP["InstallDriver"] == "com.verde.driver.manage"
        assert METHOD_ACTION_MAP["RollbackDriver"] == "com.verde.driver.manage"
        assert METHOD_ACTION_MAP["RepairDpkg"] == "com.verde.driver.manage"
        assert METHOD_ACTION_MAP["ListSnapshots"] == "com.verde.driver.manage"

    def test_power_methods_map_to_power_manage(self):
        assert METHOD_ACTION_MAP["FixSuspend"] == "com.verde.power.manage"
        assert METHOD_ACTION_MAP["FixHibernate"] == "com.verde.power.manage"

    def test_diagnostics_method_maps_to_diagnostics(self):
        assert METHOD_ACTION_MAP["GenerateDiagnosticReport"] == "com.verde.diagnostics"

    def test_monitor_methods_map_to_monitor(self):
        monitor_methods = [
            "GetGPUInfo",
            "GetGPUStats",
            "GetCurrentDriver",
            "ListAvailableDrivers",
            "GetPowerStatus",
        ]
        for method in monitor_methods:
            assert METHOD_ACTION_MAP[method] == "com.verde.monitor"


# ===================================================================
# check_authorization
# ===================================================================


def _make_polkit_result(is_authorized: bool) -> MagicMock:
    """Create a mock Polkit CheckAuthorization result.

    Mimics GLib.Variant("(bba{ss})") structure with defensive parsing support.
    """
    auth_child = MagicMock()
    auth_child.get_type_string.return_value = "b"
    auth_child.get_boolean.return_value = is_authorized

    result = MagicMock()
    result.n_children.return_value = 3
    result.get_type_string.return_value = "(bba{ss})"
    result.get_child_value.side_effect = lambda i: {
        0: auth_child,
        1: MagicMock(get_boolean=lambda: False),
        2: MagicMock(),
    }[i]
    return result


class TestCheckAuthorization:
    @patch("polkit.Gio.DBusProxy")
    def test_returns_true_when_authorized(self, mock_proxy_class):
        proxy = MagicMock()
        proxy.call_sync.return_value = _make_polkit_result(True)
        mock_proxy_class.new_for_bus_sync.return_value = proxy

        conn = MagicMock()
        assert check_authorization(conn, ":1.42", "com.verde.monitor") is True

    @patch("polkit.Gio.DBusProxy")
    def test_returns_false_when_not_authorized(self, mock_proxy_class):
        proxy = MagicMock()
        proxy.call_sync.return_value = _make_polkit_result(False)
        mock_proxy_class.new_for_bus_sync.return_value = proxy

        conn = MagicMock()
        assert check_authorization(conn, ":1.42", "com.verde.driver.manage") is False

    @patch("polkit.Gio.DBusProxy")
    def test_returns_false_on_dbus_error(self, mock_proxy_class):
        mock_proxy_class.new_for_bus_sync.side_effect = Exception("Polkit not available")

        conn = MagicMock()
        assert check_authorization(conn, ":1.42", "com.verde.monitor") is False

    @patch("polkit.Gio.DBusProxy")
    def test_uses_system_bus_name_subject(self, mock_proxy_class):
        """Verify SystemBusName subject is used (NOT UnixProcessSubject) per AR-17."""
        proxy = MagicMock()
        proxy.call_sync.return_value = _make_polkit_result(True)
        mock_proxy_class.new_for_bus_sync.return_value = proxy

        conn = MagicMock()
        check_authorization(conn, ":1.99", "com.verde.monitor")

        call_args = proxy.call_sync.call_args[0]
        # Second arg is the GLib.Variant parameters
        variant = call_args[1]
        # Unpack the variant to check subject type
        subject = variant.get_child_value(0)
        subject_kind = subject.get_child_value(0).get_string()
        assert subject_kind == "system-bus-name"

    @patch("polkit.Gio.DBusProxy")
    def test_sets_finite_timeout(self, mock_proxy_class):
        """Proxy and call_sync use finite timeout to prevent DoS."""
        proxy = MagicMock()
        proxy.call_sync.return_value = _make_polkit_result(True)
        mock_proxy_class.new_for_bus_sync.return_value = proxy

        conn = MagicMock()
        check_authorization(conn, ":1.42", "com.verde.monitor")

        proxy.set_default_timeout.assert_called_once_with(5000)
        # call_sync timeout argument (4th positional) should be 5000
        call_timeout = proxy.call_sync.call_args[0][3]
        assert call_timeout == 5000

    @patch("polkit.Gio.DBusProxy")
    def test_returns_false_on_empty_result(self, mock_proxy_class):
        """Empty Polkit result returns False (fail-closed)."""
        proxy = MagicMock()
        empty_result = MagicMock()
        empty_result.n_children.return_value = 0
        proxy.call_sync.return_value = empty_result
        mock_proxy_class.new_for_bus_sync.return_value = proxy

        conn = MagicMock()
        assert check_authorization(conn, ":1.42", "com.verde.monitor") is False

    @patch("polkit.Gio.DBusProxy")
    def test_allow_user_interaction_flag(self, mock_proxy_class):
        proxy = MagicMock()
        proxy.call_sync.return_value = _make_polkit_result(True)
        mock_proxy_class.new_for_bus_sync.return_value = proxy

        conn = MagicMock()

        # With interaction
        check_authorization(conn, ":1.42", "com.verde.monitor", allow_user_interaction=True)
        variant_with = proxy.call_sync.call_args[0][1]
        flags_with = variant_with.get_child_value(3).get_uint32()
        assert flags_with == 0x1

        proxy.call_sync.reset_mock()

        # Without interaction
        check_authorization(conn, ":1.42", "com.verde.monitor", allow_user_interaction=False)
        variant_without = proxy.call_sync.call_args[0][1]
        flags_without = variant_without.get_child_value(3).get_uint32()
        assert flags_without == 0


# ===================================================================
# PolkitAgentMissing detection (P-9)
# ===================================================================


class TestPolkitAgentMissing:
    @patch("polkit.Gio.DBusProxy")
    def test_raises_on_no_authentication_agent(self, mock_proxy_class):
        """GLib.Error mentioning 'authentication agent' raises PolkitAgentMissing."""
        from gi.repository import GLib

        error = GLib.Error.new_literal(
            GLib.quark_from_string("g-io-error"), "No authentication agent found", 0
        )
        mock_proxy_class.new_for_bus_sync.side_effect = error

        conn = MagicMock()
        with pytest.raises(PolkitAgentMissing):
            check_authorization(conn, ":1.42", "com.verde.driver.manage")

    @patch("polkit.Gio.DBusProxy")
    def test_raises_on_no_agent_message(self, mock_proxy_class):
        """GLib.Error with 'no agent' raises PolkitAgentMissing."""
        from gi.repository import GLib

        error = GLib.Error.new_literal(
            GLib.quark_from_string("g-io-error"), "no agent is available for the caller", 0
        )
        mock_proxy_class.new_for_bus_sync.side_effect = error

        conn = MagicMock()
        with pytest.raises(PolkitAgentMissing):
            check_authorization(conn, ":1.42", "com.verde.driver.manage")

    @patch("polkit.Gio.DBusProxy")
    def test_other_glib_error_returns_false(self, mock_proxy_class):
        """GLib.Error without agent message returns False (not PolkitAgentMissing)."""
        from gi.repository import GLib

        error = GLib.Error.new_literal(
            GLib.quark_from_string("g-io-error"), "Connection refused", 0
        )
        mock_proxy_class.new_for_bus_sync.side_effect = error

        conn = MagicMock()
        assert check_authorization(conn, ":1.42", "com.verde.monitor") is False

    def test_polkit_agent_missing_is_exception(self):
        """PolkitAgentMissing is a proper Exception subclass."""
        exc = PolkitAgentMissing("test message")
        assert isinstance(exc, Exception)
        assert str(exc) == "test message"


# ===================================================================
# PolkitCancelled / PolkitTimeout detection (Story 2.6, AC #2)
# ===================================================================


class TestPolkitCancelled:
    @patch("polkit.Gio.DBusProxy")
    def test_raises_on_cancelled_message(self, mock_proxy_class):
        """GLib.Error with 'cancelled' raises PolkitCancelled."""
        from gi.repository import GLib

        error = GLib.Error.new_literal(
            GLib.quark_from_string("g-io-error"),
            "GDBus.Error:org.freedesktop.PolicyKit1.Error.Cancelled: Authentication was cancelled",
            0,
        )
        mock_proxy_class.new_for_bus_sync.side_effect = error

        conn = MagicMock()
        with pytest.raises(PolkitCancelled):
            check_authorization(conn, ":1.42", "com.verde.driver.manage")

    @patch("polkit.Gio.DBusProxy")
    def test_raises_on_dismissed_message(self, mock_proxy_class):
        """GLib.Error with 'dismissed' raises PolkitCancelled."""
        from gi.repository import GLib

        error = GLib.Error.new_literal(
            GLib.quark_from_string("g-io-error"), "User dismissed the authentication dialog", 0
        )
        mock_proxy_class.new_for_bus_sync.side_effect = error

        conn = MagicMock()
        with pytest.raises(PolkitCancelled):
            check_authorization(conn, ":1.42", "com.verde.driver.manage")

    def test_polkit_cancelled_is_exception(self):
        exc = PolkitCancelled("cancelled")
        assert isinstance(exc, Exception)


class TestPolkitTimeout:
    @patch("polkit.Gio.DBusProxy")
    def test_raises_on_timeout_message(self, mock_proxy_class):
        """GLib.Error with 'timed out' raises PolkitTimeout."""
        from gi.repository import GLib

        error = GLib.Error.new_literal(
            GLib.quark_from_string("g-io-error"), "Authentication timed out", 0
        )
        mock_proxy_class.new_for_bus_sync.side_effect = error

        conn = MagicMock()
        with pytest.raises(PolkitTimeout):
            check_authorization(conn, ":1.42", "com.verde.driver.manage")

    @patch("polkit.Gio.DBusProxy")
    def test_raises_on_timeout_keyword(self, mock_proxy_class):
        """GLib.Error with 'timeout' raises PolkitTimeout."""
        from gi.repository import GLib

        error = GLib.Error.new_literal(
            GLib.quark_from_string("g-io-error"), "Polkit check timeout expired", 0
        )
        mock_proxy_class.new_for_bus_sync.side_effect = error

        conn = MagicMock()
        with pytest.raises(PolkitTimeout):
            check_authorization(conn, ":1.42", "com.verde.driver.manage")

    def test_polkit_timeout_is_exception(self):
        exc = PolkitTimeout("timeout")
        assert isinstance(exc, Exception)
