"""D-Bus client for communicating with the Verde daemon."""

from __future__ import annotations

import logging
import typing
from typing import TYPE_CHECKING, ClassVar

from gi.repository import Gio, GLib, GObject

if TYPE_CHECKING:
    from verde.gpu_state import GPUState

log = logging.getLogger("verde.dbus_client")

BUS_NAME = "com.verde.Manager"
OBJECT_PATH = "/com/verde/Manager"
INTERFACE_NAME = "com.verde.Manager"

_RECONNECT_INTERVAL_SECONDS = 5


class VerdeDBusClient(GObject.Object):
    """Async D-Bus proxy client for ``com.verde.Manager``.

    Subscribes to daemon signals, calls read methods, and feeds
    data into a shared :class:`GPUState` model.

    Parameters
    ----------
    gpu_state : GPUState
        Shared state model — updated by signal handlers and method replies.
    """

    __gtype_name__ = "VerdeDBusClient"

    __gsignals__: ClassVar[dict] = {
        "operation-progress": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str, float, str),  # op_id, percent, message
        ),
        "operation-complete": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str, bool, str),  # op_id, success, message
        ),
    }

    connected = GObject.Property(type=bool, default=False)

    def __init__(self, gpu_state: GPUState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._gpu_state = gpu_state
        self._proxy: Gio.DBusProxy | None = None
        self._proxy_handler_ids: list[int] = []
        self._retry_source_id: int | None = None

    # ── Connection lifecycle ─────────────────────────────────────────

    def connect_async(self) -> None:
        """Initiate async connection to the Verde daemon on the system bus."""
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SYSTEM,
            Gio.DBusProxyFlags.NONE,
            None,  # interface info
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE_NAME,
            None,  # cancellable
            self._on_proxy_ready,
        )

    def _on_proxy_ready(self, source: GObject.Object | None, result: Gio.AsyncResult) -> None:
        """Handle proxy creation result."""
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as exc:
            log.warning("D-Bus proxy creation failed: %s", exc.message)
            self._schedule_retry()
            return

        # Check if daemon is actually running (name owner present)
        if self._proxy.get_name_owner() is None:
            log.info("Daemon not running — waiting for it to appear")
            self.set_property("connected", False)
        else:
            self.set_property("connected", True)
            self.get_gpu_info()

        self._proxy_handler_ids = [
            self._proxy.connect("g-signal", self._on_dbus_signal),
            self._proxy.connect("notify::g-name-owner", self._on_name_owner_changed),
        ]

    def _on_name_owner_changed(self, proxy: Gio.DBusProxy, pspec: GObject.ParamSpec) -> None:
        """React to daemon appearing or disappearing on the bus."""
        owner = proxy.get_name_owner()
        if owner is not None:
            log.info("Daemon appeared on bus")
            self.set_property("connected", True)
            self.get_gpu_info()
        else:
            log.warning("Daemon disappeared from bus")
            self.set_property("connected", False)
            self._gpu_state.reset()

    def _schedule_retry(self) -> None:
        """Schedule a reconnection attempt."""
        if self._retry_source_id is not None:
            return
        self.set_property("connected", False)
        self._retry_source_id = GLib.timeout_add_seconds(
            _RECONNECT_INTERVAL_SECONDS, self._retry_connect
        )

    def _retry_connect(self) -> bool:
        """Retry connection callback."""
        self._retry_source_id = None
        self.connect_async()
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def close(self) -> None:
        """Clean up proxy, signal handlers, and cancel pending retries."""
        if self._retry_source_id is not None:
            GLib.source_remove(self._retry_source_id)
            self._retry_source_id = None
        if self._proxy is not None:
            for handler_id in self._proxy_handler_ids:
                self._proxy.disconnect(handler_id)
            self._proxy_handler_ids.clear()
        self._proxy = None
        self.set_property("connected", False)

    # ── Signal handling ──────────────────────────────────────────────

    def _on_dbus_signal(
        self,
        proxy: Gio.DBusProxy,
        sender_name: str | None,
        signal_name: str,
        parameters: GLib.Variant,
    ) -> None:
        """Route D-Bus signals to appropriate handlers."""
        if signal_name == "GPUStatsUpdated":
            data = parameters.unpack()[0]
            self._gpu_state.update_from_dict(data)
        elif signal_name == "RebootRequired":
            args = parameters.unpack()
            required = args[0] if len(args) > 0 else True
            reason = args[1] if len(args) > 1 else ""
            GLib.idle_add(self._update_reboot_state, required, reason)
        elif signal_name == "OperationProgress":
            self.emit("operation-progress", *parameters.unpack())
        elif signal_name == "OperationComplete":
            self.emit("operation-complete", *parameters.unpack())

    def _update_reboot_state(self, required: bool, reason: str) -> bool:
        self._gpu_state._set_if_changed("reboot-required", required)
        self._gpu_state._set_if_changed("reboot-reason", reason)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    # ── Method calls ─────────────────────────────────────────────────

    def call_method_async(
        self,
        method_name: str,
        parameters: GLib.Variant | None,
        callback: typing.Callable[..., typing.Any] | None = None,
    ) -> None:
        """Call a D-Bus method asynchronously — never blocks the GUI thread.

        Parameters
        ----------
        method_name : str
            D-Bus method to call (e.g. ``"GetGPUInfo"``).
        parameters : GLib.Variant | None
            Method parameters or *None* for no-arg methods.
        callback : callable | None
            Called with ``(proxy, result)`` when the reply arrives.
        """
        if self._proxy is None:
            log.warning("Cannot call %s — not connected", method_name)
            return

        self._proxy.call(
            method_name,
            parameters,
            Gio.DBusCallFlags.NONE,
            5000,  # timeout ms
            None,  # cancellable
            callback or self._default_callback,
        )

    def _default_callback(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        """Default callback that logs errors."""
        try:
            proxy.call_finish(result)
        except GLib.Error as exc:
            log.warning("D-Bus method call failed: %s", exc.message)

    def get_gpu_info(self) -> None:
        """Fetch static GPU info and update the state model."""
        self.call_method_async("GetGPUInfo", None, self._on_gpu_info_reply)

    def _on_gpu_info_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            data = reply.unpack()[0]
            self._gpu_state.update_from_dict(data)
        except GLib.Error as exc:
            log.warning("GetGPUInfo failed: %s", exc.message)

    def get_gpu_stats(self) -> None:
        """Fetch live GPU stats and update the state model."""
        self.call_method_async("GetGPUStats", None, self._on_gpu_stats_reply)

    def _on_gpu_stats_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            data = reply.unpack()[0]
            self._gpu_state.update_from_dict(data)
        except GLib.Error as exc:
            log.warning("GetGPUStats failed: %s", exc.message)
