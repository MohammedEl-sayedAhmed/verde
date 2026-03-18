"""Unit tests for VerdeDBusClient (Story 1.7)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from gi.repository import GLib, GObject

from verde.dbus_client import VerdeDBusClient
from verde.gpu_state import GPUState

# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def gpu_state():
    return GPUState()


@pytest.fixture
def client(gpu_state):
    return VerdeDBusClient(gpu_state=gpu_state)


@pytest.fixture
def mock_proxy():
    proxy = MagicMock()
    proxy.get_name_owner.return_value = ":1.100"
    return proxy


# ===================================================================
# Instantiation
# ===================================================================


class TestInstantiation:
    def test_can_instantiate_with_gpu_state(self, gpu_state):
        client = VerdeDBusClient(gpu_state=gpu_state)
        assert client is not None
        assert client.get_property("connected") is False

    def test_stores_gpu_state(self, client, gpu_state):
        assert client._gpu_state is gpu_state


# ===================================================================
# Signal handling
# ===================================================================


class TestSignalHandling:
    def test_gpu_stats_updated_calls_update_from_dict(self, client, gpu_state, mock_proxy):
        params = GLib.Variant.new_tuple(
            GLib.Variant(
                "a{sv}",
                {
                    "temperature": GLib.Variant("i", 65),
                    "utilization_gpu": GLib.Variant("i", 45),
                },
            )
        )
        with patch.object(gpu_state, "update_from_dict") as mock_update:
            client._on_dbus_signal(mock_proxy, None, "GPUStatsUpdated", params)
            mock_update.assert_called_once()
            called_data = mock_update.call_args[0][0]
            assert called_data["temperature"] == 65
            assert called_data["utilization_gpu"] == 45

    def test_reboot_required_signal(self, client, gpu_state, mock_proxy):
        params = GLib.Variant.new_tuple(
            GLib.Variant("b", True),
            GLib.Variant("s", "NVIDIA driver update"),
        )
        with patch("verde.dbus_client.GLib.idle_add") as mock_idle:
            client._on_dbus_signal(mock_proxy, None, "RebootRequired", params)
            mock_idle.assert_called_once()

    def test_update_reboot_state(self, client, gpu_state):
        result = client._update_reboot_state(True, "NVIDIA update")
        assert gpu_state.get_property("reboot-required") is True
        assert gpu_state.get_property("reboot-reason") == "NVIDIA update"
        assert result == GLib.SOURCE_REMOVE

    def test_update_reboot_state_suppresses_redundant_notify(self, client, gpu_state):
        """P-6: _update_reboot_state uses _set_if_changed to suppress duplicates."""
        client._update_reboot_state(True, "reason")
        handler = MagicMock()
        gpu_state.connect("notify::reboot-required", handler)
        client._update_reboot_state(True, "reason")  # same values
        handler.assert_not_called()

    def test_operation_progress_signal_declared(self):
        """P-1: VerdeDBusClient declares operation-progress signal."""
        assert GObject.signal_lookup("operation-progress", VerdeDBusClient) != 0

    def test_operation_complete_signal_declared(self):
        """P-1: VerdeDBusClient declares operation-complete signal."""
        assert GObject.signal_lookup("operation-complete", VerdeDBusClient) != 0


# ===================================================================
# Connection lifecycle
# ===================================================================


class TestConnectionLifecycle:
    def test_connected_changes_on_name_owner_present(self, client, mock_proxy):
        """Daemon appearing sets connected=True."""
        mock_proxy.get_name_owner.return_value = ":1.100"
        with patch.object(client, "get_gpu_info"):
            client._on_name_owner_changed(mock_proxy, None)
        assert client.get_property("connected") is True

    def test_connected_false_on_name_owner_gone(self, client, gpu_state, mock_proxy):
        """Daemon disappearing sets connected=False and resets state."""
        client.set_property("connected", True)
        mock_proxy.get_name_owner.return_value = None
        with patch.object(gpu_state, "reset") as mock_reset:
            client._on_name_owner_changed(mock_proxy, None)
        assert client.get_property("connected") is False
        mock_reset.assert_called_once()

    @patch("verde.dbus_client.GLib.timeout_add_seconds", return_value=42)
    def test_retry_on_proxy_failure(self, mock_timeout, client):
        """Failed proxy creation schedules retry."""
        error = GLib.Error.new_literal(GLib.quark_from_string("test"), "fail", 1)
        mock_result = MagicMock()
        with patch("verde.dbus_client.Gio.DBusProxy.new_for_bus_finish", side_effect=error):
            client._on_proxy_ready(None, mock_result)
        assert client.get_property("connected") is False
        mock_timeout.assert_called_once()

    @patch("verde.dbus_client.GLib.timeout_add_seconds", return_value=42)
    def test_retry_connect_calls_connect_async(self, mock_timeout, client):
        """Retry callback calls connect_async."""
        with patch.object(client, "connect_async") as mock_connect:
            result = client._retry_connect()
        mock_connect.assert_called_once()
        assert result == GLib.SOURCE_REMOVE

    def test_close_clears_proxy(self, client):
        client._proxy = MagicMock()
        client.set_property("connected", True)
        client.close()
        assert client._proxy is None
        assert client.get_property("connected") is False

    @patch("verde.dbus_client.GLib.source_remove")
    def test_close_cancels_retry(self, mock_remove, client):
        client._retry_source_id = 42
        client.close()
        mock_remove.assert_called_once_with(42)
        assert client._retry_source_id is None

    def test_close_disconnects_proxy_signal_handlers(self, client):
        """P-4: close() disconnects proxy signal handlers to prevent stacking."""
        mock_proxy = MagicMock()
        mock_proxy.connect.side_effect = [101, 102]  # handler IDs
        client._proxy = mock_proxy
        client._proxy_handler_ids = [101, 102]
        client.close()
        mock_proxy.disconnect.assert_any_call(101)
        mock_proxy.disconnect.assert_any_call(102)
        assert client._proxy_handler_ids == []


# ===================================================================
# Method calls
# ===================================================================


class TestMethodCalls:
    def test_call_method_async_with_proxy(self, client, mock_proxy):
        client._proxy = mock_proxy
        callback = MagicMock()
        client.call_method_async("GetGPUInfo", None, callback)
        mock_proxy.call.assert_called_once()
        assert mock_proxy.call.call_args[0][0] == "GetGPUInfo"

    def test_call_method_async_without_proxy_logs_warning(self, client):
        """No crash when not connected."""
        client._proxy = None
        client.call_method_async("GetGPUInfo", None, None)  # should not raise

    def test_get_gpu_info_calls_method(self, client, mock_proxy):
        client._proxy = mock_proxy
        client.get_gpu_info()
        mock_proxy.call.assert_called_once()
        assert mock_proxy.call.call_args[0][0] == "GetGPUInfo"

    def test_get_gpu_stats_calls_method(self, client, mock_proxy):
        client._proxy = mock_proxy
        client.get_gpu_stats()
        mock_proxy.call.assert_called_once()
        assert mock_proxy.call.call_args[0][0] == "GetGPUStats"

    def test_on_gpu_info_reply_updates_state(self, client, gpu_state, mock_proxy):
        reply = GLib.Variant.new_tuple(
            GLib.Variant(
                "a{sv}",
                {
                    "name": GLib.Variant("s", "RTX 4090"),
                    "driver_version": GLib.Variant("s", "560.35.03"),
                },
            )
        )
        mock_result = MagicMock()
        mock_proxy.call_finish.return_value = reply
        with patch.object(gpu_state, "update_from_dict") as mock_update:
            client._on_gpu_info_reply(mock_proxy, mock_result)
            mock_update.assert_called_once()
            data = mock_update.call_args[0][0]
            assert data["name"] == "RTX 4090"

    def test_on_gpu_info_reply_handles_error(self, client, mock_proxy):
        """Error in reply does not crash."""
        error = GLib.Error.new_literal(GLib.quark_from_string("test"), "fail", 1)
        mock_proxy.call_finish.side_effect = error
        client._on_gpu_info_reply(mock_proxy, MagicMock())  # should not raise
