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

IDLE_TIMEOUT_SECONDS = 120


def main() -> int:
    """Start the Verde daemon main loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    logger.info("Verde daemon starting (version %s)", VERSION)

    loop = GLib.MainLoop()

    # Handle SIGTERM for clean systemd shutdown
    def _on_terminate(signum, _frame):
        logger.info("Received signal %d, shutting down", signum)
        loop.quit()

    signal.signal(signal.SIGTERM, _on_terminate)
    signal.signal(signal.SIGINT, _on_terminate)

    # Idle timeout — exit when no work for IDLE_TIMEOUT_SECONDS
    # Store the source ID so future code can cancel it when work arrives
    def _on_idle_timeout() -> bool:
        logger.info("Idle timeout reached, shutting down")
        loop.quit()
        return GLib.SOURCE_REMOVE

    idle_timeout_id = GLib.timeout_add_seconds(IDLE_TIMEOUT_SECONDS, _on_idle_timeout)
    _ = idle_timeout_id  # Will be used for cancellation in Story 1.3

    loop.run()

    logger.info("Verde daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
