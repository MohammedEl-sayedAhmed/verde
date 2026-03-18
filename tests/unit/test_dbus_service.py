"""Unit tests for the Verde D-Bus service and daemon lifecycle."""

from __future__ import annotations

import importlib
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add daemon source to path and register the package under its installed name.
DAEMON_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "verde-daemon"
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

# The directory is "verde-daemon" (hyphen) but installed as "verde_daemon" (underscore).
# Register it so ``from verde_daemon import __version__`` works during tests.
_pkg = importlib.import_module("__init__")  # loads verde-daemon/__init__.py
sys.modules.setdefault("verde_daemon", _pkg)

# Introspection XML used by all tests.
_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_loop():
    """Fake GLib.MainLoop."""
    loop = MagicMock()
    return loop


@pytest.fixture
def idle_reset():
    """Callable that records idle-reset invocations."""
    return MagicMock()


@pytest.fixture
def service(mock_loop, idle_reset):
    """VerdeService wired to mocks, not started."""
    from service import VerdeService

    return VerdeService(
        loop=mock_loop,
        on_idle_reset=idle_reset,
        introspection_xml=_XML,
    )


@pytest.fixture
def mock_invocation():
    """Mock Gio.DBusMethodInvocation."""
    inv = MagicMock()
    return inv


@pytest.fixture
def mock_connection():
    """Mock Gio.DBusConnection."""
    conn = MagicMock()
    conn.register_object.return_value = 42  # fake registration id
    return conn


# ===================================================================
# Task 5: Service registration and method dispatch
# ===================================================================


class TestServiceInstantiation:
    def test_can_instantiate(self, service):
        assert service is not None
        assert service._registration_id == 0

    def test_requires_xml_source(self, mock_loop, idle_reset):
        from service import VerdeService

        with pytest.raises(ValueError, match="introspection_xml or xml_path"):
            VerdeService(loop=mock_loop, on_idle_reset=idle_reset)

    def test_loads_from_xml_path(self, mock_loop, idle_reset):
        from service import VerdeService

        svc = VerdeService(
            loop=mock_loop,
            on_idle_reset=idle_reset,
            xml_path=_XML_PATH,
        )
        assert svc._node_info is not None


class TestMethodDispatch:
    def test_ping_returns_none(self, service, mock_invocation, mock_connection):
        """Ping() returns successfully with no value."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "Ping",
            None,
            mock_invocation,
        )
        mock_invocation.return_value.assert_called_once_with(None)

    def test_ping_resets_idle(self, service, mock_invocation, mock_connection, idle_reset):
        """Implemented method calls trigger idle timer reset."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "Ping",
            None,
            mock_invocation,
        )
        idle_reset.assert_called_once()

    def test_unknown_method_does_not_reset_idle(
        self, service, mock_invocation, mock_connection, idle_reset
    ):
        """Unimplemented methods do not reset idle timer (prevents DoS)."""
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
        idle_reset.assert_not_called()

    def test_unknown_method_returns_error(self, service, mock_invocation, mock_connection):
        """Unimplemented methods return D-Bus UnknownMethod error."""
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
        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "org.freedesktop.DBus.Error.UnknownMethod"


class TestPropertyHandler:
    def test_daemon_version_is_string(self, service, mock_connection):
        """DaemonVersion returns a string GLib.Variant."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        result = service._handle_get_property(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "DaemonVersion",
        )
        assert result is not None
        # GLib.Variant("s", ...) — verify it's a variant with string type
        assert result.get_type_string() == "s"
        assert isinstance(result.get_string(), str)
        assert len(result.get_string()) > 0

    def test_operation_in_progress_is_false(self, service, mock_connection):
        """OperationInProgress returns False."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        result = service._handle_get_property(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "OperationInProgress",
        )
        assert result is not None
        assert result.get_type_string() == "b"
        assert result.get_boolean() is False

    def test_unknown_property_returns_none(self, service, mock_connection):
        """Unknown property returns None."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")

        result = service._handle_get_property(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "Nonexistent",
        )
        assert result is None


class TestBusLifecycle:
    def test_on_bus_acquired_registers_object(self, service, mock_connection):
        """Bus acquired → object registered."""
        service._on_bus_acquired(mock_connection, "com.verde.Manager")
        mock_connection.register_object.assert_called_once()
        assert service._registration_id == 42

    def test_on_name_lost_quits_loop(self, service, mock_loop, mock_connection):
        """Losing bus name causes loop.quit()."""
        service._on_name_lost(mock_connection, "com.verde.Manager")
        mock_loop.quit.assert_called_once()


# ===================================================================
# Task 6: Idle timeout and lifecycle
# ===================================================================


class TestIdleTimer:
    def test_idle_timeout_value(self):
        """IDLE_TIMEOUT_SECONDS is 60 per FR39."""
        # Import from main module source
        main_path = DAEMON_SRC / "main.py"
        content = main_path.read_text()
        assert "IDLE_TIMEOUT_SECONDS = 60" in content

    @patch("main.GLib")
    def test_idle_timer_start_schedules_timeout(self, mock_glib):
        """IdleTimer.start() schedules a GLib timeout."""
        mock_glib.timeout_add_seconds.return_value = 100
        mock_glib.SOURCE_REMOVE = False

        from main import _IdleTimer

        loop = MagicMock()
        timer = _IdleTimer(60, loop)
        timer.start()

        mock_glib.timeout_add_seconds.assert_called_once()
        assert timer._source_id == 100

    @patch("main.GLib")
    def test_idle_timer_reset_cancels_and_reschedules(self, mock_glib):
        """IdleTimer.reset() removes old source and schedules new one."""
        call_count = 0

        def fake_add(seconds, cb):
            nonlocal call_count
            call_count += 1
            return 100 + call_count

        mock_glib.timeout_add_seconds.side_effect = fake_add
        mock_glib.source_remove = MagicMock()
        mock_glib.SOURCE_REMOVE = False

        from main import _IdleTimer

        loop = MagicMock()
        timer = _IdleTimer(60, loop)
        timer.start()  # source_id = 101
        first_id = timer._source_id

        timer.reset()  # should remove 101, schedule 102
        mock_glib.source_remove.assert_called_with(first_id)
        assert timer._source_id != first_id

    @patch("main.GLib")
    def test_idle_timer_timeout_quits_loop(self, mock_glib):
        """When timeout fires, loop.quit() is called."""
        mock_glib.timeout_add_seconds.return_value = 100
        mock_glib.SOURCE_REMOVE = False

        from main import _IdleTimer

        loop = MagicMock()
        timer = _IdleTimer(60, loop)
        timer.start()

        # Simulate timeout callback
        result = timer._on_timeout()
        loop.quit.assert_called_once()
        assert result == mock_glib.SOURCE_REMOVE

    @patch("main.GLib")
    def test_idle_timer_cancel(self, mock_glib):
        """IdleTimer.cancel() removes the source."""
        mock_glib.timeout_add_seconds.return_value = 100
        mock_glib.source_remove = MagicMock()

        from main import _IdleTimer

        loop = MagicMock()
        timer = _IdleTimer(60, loop)
        timer.start()
        timer.cancel()

        mock_glib.source_remove.assert_called_with(100)
        assert timer._source_id is None


class TestDaemonSignalHandlers:
    def test_main_py_registers_sigterm(self):
        """main.py registers SIGTERM handler via GLib.unix_signal_add."""
        content = (DAEMON_SRC / "main.py").read_text()
        assert "signal.SIGTERM" in content
        assert "unix_signal_add" in content

    def test_main_py_registers_sigint(self):
        """main.py registers SIGINT handler via GLib.unix_signal_add."""
        content = (DAEMON_SRC / "main.py").read_text()
        assert "signal.SIGINT" in content
        assert "unix_signal_add" in content

    def test_main_py_logs_startup_and_shutdown(self):
        """main.py logs startup and shutdown messages."""
        content = (DAEMON_SRC / "main.py").read_text()
        assert "Verde daemon starting" in content
        assert "Verde daemon stopped" in content
