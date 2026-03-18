"""Unit tests for Polkit authorization helper."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock, patch

# verde_daemon package registration is handled by tests/conftest.py
from polkit import METHOD_ACTION_MAP, check_authorization

# ===================================================================
# METHOD_ACTION_MAP completeness
# ===================================================================


class TestMethodActionMap:
    EXPECTED_METHODS: ClassVar[set[str]] = {
        "InstallDriver",
        "RollbackDriver",
        "ListSnapshots",
        "FixSuspend",
        "FixHibernate",
        "GenerateDiagnosticReport",
        "GetGPUInfo",
        "GetGPUStats",
        "GetCurrentDriver",
        "ListAvailableDrivers",
        "GetPowerStatus",
        "GetPreflightCheck",
    }

    def test_all_12_methods_mapped(self):
        assert set(METHOD_ACTION_MAP.keys()) == self.EXPECTED_METHODS

    def test_driver_methods_map_to_driver_manage(self):
        assert METHOD_ACTION_MAP["InstallDriver"] == "com.verde.driver.manage"
        assert METHOD_ACTION_MAP["RollbackDriver"] == "com.verde.driver.manage"
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
            "GetPreflightCheck",
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
