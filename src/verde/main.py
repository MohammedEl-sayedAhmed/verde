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
    # ── CLI mode: intercept --check/--json before GTK init ──
    _cli_args = set(sys.argv[1:])
    if "--check" in _cli_args or "--json" in _cli_args:
        from verde.cli import run_check, run_status_json

        if "--check" in _cli_args:
            sys.exit(run_check(use_json="--json" in _cli_args))
        else:
            # --json without --check: full status dump
            sys.exit(run_status_json())

    if "--version" in _cli_args:
        sys.stdout.write(f"verde {VERSION}\n")
        sys.exit(0)

    if "--help" in _cli_args:
        sys.stdout.write(
            f"verde {VERSION}\n"
            "Usage: verde [OPTIONS]\n\n"
            "Options:\n"
            "  --check     Run health check and exit (no GUI)\n"
            "  --json      Output in JSON format (use with --check for health, alone for full status)\n"
            "  --version   Show version and exit\n"
            "  --help      Show this help and exit\n\n"
            "Exit codes (--check mode):\n"
            "  0  Healthy — all GPUs within normal parameters\n"
            "  1  Warning — temperature 85-95°C, throttling, or VRAM >90%%\n"
            "  2  Critical — temperature >95°C, GPU off bus, or driver not loaded\n"
            "  3  No GPU — no NVIDIA GPU detected\n"
            "  4  Error — daemon unreachable or unexpected failure\n"
        )
        sys.exit(0)

    # ── GUI mode ──
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
