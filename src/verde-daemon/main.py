#!@PYTHON@
"""Verde daemon entry point.

Runs as a D-Bus system service with socket activation.
Exits after idle timeout when no operations are in progress.
"""

import logging
import signal
import sys

from gi.repository import GLib

VERSION = "@VERSION@"

logger = logging.getLogger("verde-daemon")

IDLE_TIMEOUT_SECONDS = 60


class _IdleTimer:
    """Restartable idle timer that quits the main loop on expiry."""

    def __init__(self, timeout: int, loop: GLib.MainLoop) -> None:
        self._timeout = timeout
        self._loop = loop
        self._source_id: int | None = None

    def start(self) -> None:
        """Schedule (or reschedule) the idle timeout."""
        if self._source_id is not None:
            GLib.source_remove(self._source_id)
        self._source_id = GLib.timeout_add_seconds(self._timeout, self._on_timeout)

    def reset(self) -> None:
        """Alias for start — restarts the idle countdown."""
        self.start()

    def cancel(self) -> None:
        """Cancel a pending timeout."""
        if self._source_id is not None:
            GLib.source_remove(self._source_id)
            self._source_id = None

    def _on_timeout(self) -> bool:
        logger.info("Idle timeout reached (%ds), shutting down", self._timeout)
        self._source_id = None
        self._loop.quit()
        return GLib.SOURCE_REMOVE


def main() -> int:
    """Start the Verde daemon main loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    logger.info("Verde daemon starting (version %s)", VERSION)

    loop = GLib.MainLoop()

    # Handle SIGTERM/SIGINT for clean systemd shutdown.
    # Use GLib.unix_signal_add to dispatch signals safely inside the main
    # loop instead of Python's signal.signal, which runs in arbitrary
    # context and can deadlock if it interrupts logging or GLib internals.
    def _on_terminate_signal(signum):
        logger.info("Received signal %d, shutting down", signum)
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, _on_terminate_signal, signal.SIGTERM)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, _on_terminate_signal, signal.SIGINT)

    # Idle timer — exits daemon after IDLE_TIMEOUT_SECONDS of inactivity
    idle_timer = _IdleTimer(IDLE_TIMEOUT_SECONDS, loop)
    idle_timer.start()

    # D-Bus service registration
    # Import here to avoid import-time side effects during testing
    from verde_daemon.service import VerdeService

    xml_path = "@pkgdatadir@/com.verde.Manager.xml"
    service = VerdeService(
        loop=loop,
        on_idle_reset=idle_timer.reset,
        xml_path=xml_path,
    )
    service.start()

    loop.run()

    # Cleanup
    idle_timer.cancel()
    service.stop()

    logger.info("Verde daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
