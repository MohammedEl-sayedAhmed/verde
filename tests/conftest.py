"""Shared pytest fixtures for Verde test suite."""

import importlib
import pathlib
import sys

# Register src/verde-daemon as the "verde_daemon" package so that both
# ``from verde_daemon import __version__`` and sub-module imports like
# ``from verde_daemon.polkit import check_authorization`` work during tests.
#
# The on-disk directory uses a hyphen (verde-daemon) but the installed
# package name uses an underscore (verde_daemon).
DAEMON_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "verde-daemon"
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

_pkg = importlib.import_module("__init__")  # loads verde-daemon/__init__.py
_pkg.__path__ = [str(DAEMON_SRC)]  # make it a package so sub-imports work
_pkg.__package__ = "verde_daemon"
sys.modules["verde_daemon"] = _pkg
