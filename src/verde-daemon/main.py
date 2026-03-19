#!@PYTHON@
"""Verde daemon entry point.

Runs as a D-Bus system service with socket activation.
Exits after idle timeout when no operations are in progress.
"""

import logging
import os
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

    def hold(self) -> None:
        """Suspend the idle timer — daemon must not exit during operations."""
        if self._source_id is not None:
            GLib.source_remove(self._source_id)
            self._source_id = None
        logger.debug("Idle timer held (operation in progress)")

    def release(self) -> None:
        """Resume the idle timer — restart countdown after operation."""
        self.start()
        logger.debug("Idle timer released (operation complete)")

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
    """Start the Verde daemon main loop, or enter recovery mode with --repair."""
    # Check for --repair before any GLib/D-Bus imports (AC#7: standalone)
    if "--repair" in sys.argv:
        from cli_recovery import recovery_main

        return recovery_main()

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
    from verde_daemon.audit import AuditLogger
    from verde_daemon.service import VerdeService

    audit_logger = AuditLogger()

    xml_path = os.environ.get("VERDE_DATA_DIR", "@pkgdatadir@") + "/com.verde.Manager.xml"
    service = VerdeService(
        loop=loop,
        on_idle_reset=idle_timer.reset,
        on_idle_hold=idle_timer.hold,
        on_idle_release=idle_timer.release,
        xml_path=xml_path,
        audit_logger=audit_logger,
    )
    service.start()

    # Check for interrupted operations on startup (FR48)
    from verde_daemon.apt_errors import detect_interrupted_operation

    interrupted = detect_interrupted_operation()
    if interrupted is not None:
        logger.warning(
            "Interrupted operation detected on startup: %s — %s",
            interrupted.title,
            interrupted.description,
        )
        # The GUI will query this on connection; store for later retrieval
        service.set_startup_error(interrupted)

    loop.run()

    # Cleanup
    idle_timer.cancel()
    service.stop()

    logger.info("Verde daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
