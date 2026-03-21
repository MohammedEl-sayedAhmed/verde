# Verde — NVIDIA GPU Manager for Ubuntu

Verde is a graphical tool for managing NVIDIA GPUs on Ubuntu. It monitors GPU health, installs and rolls back drivers safely, detects and fixes suspend/hibernate issues, and provides diagnostic reports — all without touching the terminal.

## Features

- **Real-time GPU monitoring** — temperature, utilization, VRAM, power draw, fan speed, clock speeds
- **Driver management** — discover, install, and rollback NVIDIA drivers with pre-flight safety checks and automatic snapshots
- **Power & suspend fixes** — detect and one-click fix NVIDIA suspend/hibernate/Wayland issues
- **Module diagnostics** — diagnose why the NVIDIA kernel module isn't loaded (missing headers, DKMS, kernel mismatch, Secure Boot, blacklisting) and fix it
- **Diagnostic reports** — generate comprehensive system reports for sharing on forums
- **Audit log** — view all operations Verde has performed with filtering and export
- **External change detection** — detects driver or config changes made outside Verde
- **System modification tracking** — records every change with one-click revert
- **CLI health check** — `verde --check` returns machine-readable exit codes for scripting

## Requirements

- Ubuntu 24.04 (Noble Numbat)
- NVIDIA GPU
- Python 3.12
- GTK 4 + Libadwaita 1

## Installation

### From PPA (coming soon)

A PPA for easy installation is planned. For now, build from source.

### From source

```bash
sudo apt install libgtk-4-dev libadwaita-1-dev blueprint-compiler \
  python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  meson ninja-build gettext desktop-file-utils appstream

git clone https://github.com/verde-gpu/verde.git
cd verde
meson setup builddir
meson compile -C builddir
sudo meson install -C builddir
```

## Usage

### GUI

```bash
verde
```

### CLI health check

```bash
# Plain text output
verde --check

# JSON output
verde --check --json

# Full GPU status dump
verde --json
```

**Exit codes** (`--check` mode):

| Code | Meaning |
|------|---------|
| 0 | Healthy — all GPUs within normal parameters |
| 1 | Warning — temperature 85–95°C, throttling, or VRAM >90% |
| 2 | Critical — temperature >95°C, GPU off bus, or driver not loaded |
| 3 | No GPU — no NVIDIA GPU detected |
| 4 | Error — daemon unreachable |

## Architecture

Verde uses a strict GUI/daemon split:

- **`verde`** — GTK4/Libadwaita GUI running as the desktop user
- **`verde-daemon`** — privileged system daemon running as root via systemd
- **Communication** — D-Bus system bus (`com.verde.Manager`)
- **Authorization** — Polkit for all privileged operations

The GUI never accesses system resources directly. All driver installation, power management, and system configuration happens exclusively through the daemon.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and PR instructions.

```bash
# Run tests
PYTHONPATH=src:src/verde-daemon python3.12 -m pytest tests/unit/ -v

# Lint
ruff check src/ tests/

# Type check
mypy src/verde/ --ignore-missing-imports
```

## License

GPL-3.0-or-later
