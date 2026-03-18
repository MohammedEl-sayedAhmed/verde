#!@PYTHON@
# SPDX-License-Identifier: GPL-3.0-or-later

"""Verde application entry point."""

import gettext
import locale
import logging
import signal
import sys
import time

VERSION = "@VERSION@"
APP_ID = "@APP_ID@"
PKGDATADIR = "@pkgdatadir@"
LOCALEDIR = "@localedir@"

sys.path.insert(1, PKGDATADIR)

signal.signal(signal.SIGINT, signal.SIG_DFL)

locale.bindtextdomain("verde", LOCALEDIR)
locale.textdomain("verde")
gettext.install("verde", LOCALEDIR)

if __name__ == "__main__":
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    from gi.repository import Gio

    resource = Gio.resource_load(PKGDATADIR + "/verde.gresource")
    Gio.resources_register(resource)

    _start_time = time.monotonic()
    _log = logging.getLogger("verde.main")

    from verde.window import VerdeApplication

    app = VerdeApplication(application_id=APP_ID, version=VERSION)

    def _log_startup_metrics(_app):
        elapsed = time.monotonic() - _start_time
        _log.info("startup: %.3f s to first activate", elapsed)
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        _log.info("memory: %s", line.strip())
                        break
        except OSError:
            pass
        app.disconnect(_startup_handler_id)

    _startup_handler_id = app.connect("activate", _log_startup_metrics)
    sys.exit(app.run(sys.argv))
