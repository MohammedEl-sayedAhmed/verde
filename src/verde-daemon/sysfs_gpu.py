"""Sysfs-based NVIDIA GPU detection for when NVML is unavailable.

Pure filesystem reads — no subprocess calls, no root required.
Enumerates ``/sys/bus/pci/devices/`` for NVIDIA display devices and
resolves human-readable names from ``/usr/share/misc/pci.ids``.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("verde-daemon.sysfs_gpu")

_PCI_IDS_PATH = Path("/usr/share/misc/pci.ids")
_PCI_DEVICES = Path("/sys/bus/pci/devices")
_NVIDIA_VENDOR = "0x10de"

# Lazily loaded on first use.
_pci_names_cache: dict[str, str] | None = None


def _load_nvidia_pci_names() -> dict[str, str]:
    """Parse ``pci.ids`` for NVIDIA vendor (10de) device names.

    Returns a mapping of lowercase hex device-id → human-readable name.
    The result is cached for the lifetime of the process.
    """
    global _pci_names_cache  # noqa: PLW0603
    if _pci_names_cache is not None:
        return _pci_names_cache

    names: dict[str, str] = {}
    if not _PCI_IDS_PATH.exists():
        log.debug("pci.ids not found at %s", _PCI_IDS_PATH)
        _pci_names_cache = names
        return names

    in_nvidia = False
    try:
        for line in _PCI_IDS_PATH.read_text(errors="replace").splitlines():
            if not line or line.startswith("#"):
                continue
            # Vendor line: no leading whitespace
            if not line[0].isspace():
                parts = line.split(None, 1)
                in_nvidia = parts[0].lower() == "10de"
                continue
            # Device line: single-tab indent under NVIDIA vendor
            if in_nvidia and line.startswith("\t") and not line.startswith("\t\t"):
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    names[parts[0].lower()] = parts[1]
    except OSError:
        log.warning("Failed to parse %s", _PCI_IDS_PATH)

    _pci_names_cache = names
    return names


def detect_nvidia_gpus_sysfs() -> list[dict[str, str]]:
    """Enumerate NVIDIA GPUs from ``/sys/bus/pci/devices/`` without NVML.

    Returns a list of dicts with keys:

    * ``name`` – human-readable GPU name (from pci.ids, or a fallback)
    * ``pci_bus_id`` – sysfs slot name (e.g. ``0000:03:00.0``)
    * ``vendor_id`` – always ``10de``
    * ``device_id`` – raw hex device id (e.g. ``0x1341``)
    """
    gpus: list[dict[str, str]] = []
    if not _PCI_DEVICES.is_dir():
        return gpus

    pci_names = _load_nvidia_pci_names()

    try:
        for device_dir in sorted(_PCI_DEVICES.iterdir()):
            try:
                vendor = (device_dir / "vendor").read_text().strip()
                if vendor != _NVIDIA_VENDOR:
                    continue
                device_class = (device_dir / "class").read_text().strip()
                # VGA compatible (0x0300xx) or 3D controller (0x0302xx)
                if not device_class.startswith("0x03"):
                    continue
                device_id = (device_dir / "device").read_text().strip()
                dev_id_short = device_id.lstrip("0x").lower()
                gpu_name = pci_names.get(
                    dev_id_short, f"NVIDIA GPU ({device_id})"
                )
                gpus.append(
                    {
                        "name": gpu_name,
                        "pci_bus_id": device_dir.name,
                        "vendor_id": "10de",
                        "device_id": device_id,
                    }
                )
            except OSError:
                continue
    except OSError:
        log.warning("Failed to enumerate PCI devices from sysfs")

    return gpus
