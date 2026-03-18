"""Shared pytest fixtures for Verde test suite."""

import importlib
import pathlib
import sys

# Register src/verde/ as importable so ``from verde.gpu_state import GPUState``
# works during tests (the parent of the ``verde`` package directory).
GUI_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(GUI_SRC) not in sys.path:
    sys.path.insert(0, str(GUI_SRC))

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

# Pre-register submodules so that ``from nvml_wrapper import X`` and
# ``from verde_daemon.nvml_wrapper import X`` resolve to the same module
# object.  Without this, singletons like ``Unavailable`` have different
# identities across the two import paths, breaking ``is`` checks.
for _name in (
    "nvml_wrapper",
    "polkit",
    "validators",
    "service",
    "audit",
    "driver_manager",
    "preflight",
):
    _mod_path = DAEMON_SRC / f"{_name}.py"
    if _mod_path.exists():
        _submod = importlib.import_module(_name)
        sys.modules[f"verde_daemon.{_name}"] = _submod
