"""Verde D-Bus service — registers on the system bus as com.verde.Manager."""

from __future__ import annotations

import logging
import pathlib
from collections.abc import Callable

from gi.repository import Gio, GLib

from verde_daemon import __version__

log = logging.getLogger("verde-daemon.service")

BUS_NAME = "com.verde.Manager"
OBJECT_PATH = "/com/verde/Manager"
INTERFACE_NAME = "com.verde.Manager"


class VerdeService:
    """D-Bus service for com.verde.Manager.

    Parameters
    ----------
    loop : GLib.MainLoop
        Main event loop — quit is called on name-lost.
    on_idle_reset : callable
        Called on every incoming method call so the idle timer restarts.
    introspection_xml : str | None
        Override introspection XML (for testing). When *None* the XML is
        loaded from *xml_path*.
    xml_path : str | pathlib.Path | None
        Path to ``com.verde.Manager.xml``.  Ignored when
        *introspection_xml* is supplied.
    """

    def __init__(
        self,
        loop: GLib.MainLoop,
        on_idle_reset: Callable[[], None],
        *,
        introspection_xml: str | None = None,
        xml_path: str | pathlib.Path | None = None,
    ) -> None:
        self._loop = loop
        self._on_idle_reset = on_idle_reset
        self._connection: Gio.DBusConnection | None = None
        self._registration_id: int = 0
        self._owner_id: int = 0

        # Load introspection XML
        if introspection_xml is not None:
            xml = introspection_xml
        elif xml_path is not None:
            xml = pathlib.Path(xml_path).read_text()
        else:
            raise ValueError("Either introspection_xml or xml_path must be provided")

        self._node_info = Gio.DBusNodeInfo.new_for_xml(xml)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Acquire the bus name and register the D-Bus object."""
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SYSTEM,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )

    def stop(self) -> None:
        """Release bus name and unregister object."""
        if self._registration_id:
            if self._connection is not None:
                try:
                    self._connection.unregister_object(self._registration_id)
                except Exception:
                    log.debug("Failed to unregister object (bus may be disconnected)")
            self._registration_id = 0
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0

    # ------------------------------------------------------------------
    # Bus lifecycle callbacks
    # ------------------------------------------------------------------

    def _on_bus_acquired(
        self,
        connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        """Register D-Bus object once bus connection is ready."""
        self._connection = connection
        iface_info = self._node_info.interfaces[0]

        self._registration_id = connection.register_object(
            OBJECT_PATH,
            iface_info,
            self._handle_method_call,
            self._handle_get_property,
            None,  # set_property — all properties are read-only
        )
        log.info("D-Bus object registered at %s", OBJECT_PATH)

    def _on_name_acquired(
        self,
        connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        log.info("Acquired bus name: %s", name)

    def _on_name_lost(
        self,
        connection: Gio.DBusConnection,
        name: str,
    ) -> None:
        log.warning("Lost bus name %s — shutting down", name)
        self._loop.quit()

    # ------------------------------------------------------------------
    # D-Bus method call handler
    # ------------------------------------------------------------------

    def _handle_method_call(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method_name == "Ping":
            self._on_idle_reset()
            invocation.return_value(None)
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Method {method_name} is not yet implemented",
            )

    # ------------------------------------------------------------------
    # D-Bus property handler
    # ------------------------------------------------------------------

    def _handle_get_property(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        property_name: str,
    ) -> GLib.Variant | None:
        if property_name == "DaemonVersion":
            return GLib.Variant("s", __version__)
        if property_name == "OperationInProgress":
            return GLib.Variant("b", False)
        return None
