"""Polkit authorization helper for Verde D-Bus methods (FR36, NFR-SEC-5).

Uses SystemBusName subject (NOT UnixProcessSubject) per AR-17.
"""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

# Timeout for Polkit D-Bus calls (ms). Prevents single-threaded DoS if
# the Polkit daemon hangs — the GLib main loop is single-threaded.
_POLKIT_TIMEOUT_MS = 5000

METHOD_ACTION_MAP: dict[str, str] = {
    "InstallDriver": "com.verde.driver.manage",
    "RollbackDriver": "com.verde.driver.manage",
    "RepairDpkg": "com.verde.driver.manage",
    "ListSnapshots": "com.verde.driver.manage",
    "FixSuspend": "com.verde.power.manage",
    "FixHibernate": "com.verde.power.manage",
    "GenerateDiagnosticReport": "com.verde.diagnostics",
    "GetGPUInfo": "com.verde.monitor",
    "GetGPUStats": "com.verde.monitor",
    "GetCurrentDriver": "com.verde.monitor",
    "ListAvailableDrivers": "com.verde.monitor",
    "GetPowerStatus": "com.verde.monitor",
}


def check_authorization(
    connection: Gio.DBusConnection,
    sender: str,
    action_id: str,
    allow_user_interaction: bool = True,
) -> bool:
    """Check Polkit authorization for a D-Bus caller.

    Uses SystemBusName subject to identify the caller.
    Returns True if authorized, False otherwise.
    """
    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.PolicyKit1",
            "/org/freedesktop/PolicyKit1/Authority",
            "org.freedesktop.PolicyKit1.Authority",
            None,
        )
        proxy.set_default_timeout(_POLKIT_TIMEOUT_MS)

        flags = 0x1 if allow_user_interaction else 0

        result = proxy.call_sync(
            "CheckAuthorization",
            GLib.Variant(
                "((sa{sv})sa{ss}us)",
                (
                    ("system-bus-name", {"name": GLib.Variant("s", sender)}),
                    action_id,
                    {},
                    flags,
                    "",
                ),
            ),
            Gio.DBusCallFlags.NONE,
            _POLKIT_TIMEOUT_MS,
            None,
        )

        # Polkit returns (bba{ss}). Defensive: verify structure before access.
        if result is None or result.n_children() == 0:
            log.error("Polkit returned empty result — denying access")
            return False
        auth_value = result.get_child_value(0)
        if auth_value.get_type_string() == "b":
            return auth_value.get_boolean()
        # call_sync may wrap in extra tuple — unwrap one level
        if auth_value.get_type_string() == "(bba{ss})" and auth_value.n_children() > 0:
            return auth_value.get_child_value(0).get_boolean()
        log.error("Unexpected Polkit result type: %s — denying access", result.get_type_string())
        return False

    except GLib.Error as exc:
        # Detect missing auth agent vs other Polkit failures
        msg = exc.message if hasattr(exc, "message") else str(exc)
        if "authentication agent" in msg.lower() or "no agent" in msg.lower():
            raise PolkitAgentMissing(msg) from exc
        log.error("Polkit authorization check failed: %s", msg)
        return False
    except PolkitAgentMissing:
        raise  # Re-raise so callers can distinguish
    except Exception:
        log.exception("Unexpected error during Polkit authorization check")
        return False


class PolkitAgentMissing(Exception):
    """Raised when Polkit authentication agent is not available."""
