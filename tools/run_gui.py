#!/usr/bin/env python3
"""Launch the Verde GUI for local testing.

Automatically loads the GResource bundle and starts the application.
Set VERDE_USE_SESSION_BUS=1 to connect to the mock daemon on the session bus.

Usage (from project root):
    # Without daemon (shows "Service Unavailable")
    PYTHONPATH=src:src/verde-daemon python3 tools/run_gui.py

    # With mock daemon
    VERDE_USE_SESSION_BUS=1 PYTHONPATH=src:src/verde-daemon python3 tools/run_gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRESOURCE_PATH = PROJECT_ROOT / "builddir" / "data" / "verde.gresource"

if not GRESOURCE_PATH.exists():
    print(
        f"Error: {GRESOURCE_PATH} not found.\n"
        "Run 'meson setup builddir && meson compile -C builddir' first.",
        file=sys.stderr,
    )
    sys.exit(1)

resource = Gio.resource_load(str(GRESOURCE_PATH))
Gio.resources_register(resource)

from verde.window import VerdeApplication  # noqa: E402

app = VerdeApplication(application_id="com.verde.app", version="0.1.0")
sys.exit(app.run(sys.argv))
