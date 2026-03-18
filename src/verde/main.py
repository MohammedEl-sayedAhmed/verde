#!@PYTHON@
# SPDX-License-Identifier: GPL-3.0-or-later

"""Verde application entry point."""

import gettext
import locale
import signal
import sys

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

    from verde.window import VerdeApplication

    app = VerdeApplication(application_id=APP_ID, version=VERSION)
    sys.exit(app.run(sys.argv))
