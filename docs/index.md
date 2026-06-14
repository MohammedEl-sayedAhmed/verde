# Verde — Project Documentation

**Generated:** 2026-03-21
**Project type:** Desktop application (GTK4/Libadwaita + system daemon)
**Language:** Python 3.12
**Build system:** Meson
**Target platform:** Ubuntu 24.04 (Noble Numbat)

---

## Quick Links

- [How It Works](how-it-works.md) — runtime flow, start to finish
- [Architecture Overview](architecture.md)
- [Module Reference](modules.md)
- [D-Bus API Reference](dbus-api.md)
- [Test Strategy](testing.md)

---

## Project Summary

Verde is a graphical NVIDIA GPU management tool for Ubuntu. It provides:

- **Real-time GPU monitoring** — temperature, utilization, VRAM, power draw, clock speeds
- **Driver management** — discovery, installation, rollback with pre-flight safety checks
- **Power/suspend fix** — detects and fixes NVIDIA suspend/hibernate/Wayland issues
- **Diagnostic reports** — comprehensive system info for forum sharing
- **CLI health check** — `verde --check` returns machine-readable exit codes for scripting
- **System modification tracking** — records all changes with undo/revert capability
- **External change detection** — detects driver/config changes made outside Verde

## Architecture

Verde uses a strict **GUI/daemon split** with D-Bus as the communication boundary:

```
┌──────────────────────┐     D-Bus (system bus)     ┌──────────────────────┐
│   verde (GUI)        │◄──────────────────────────►│   verde-daemon       │
│   Runs as user       │    com.verde.Manager       │   Runs as root       │
│   GTK4 + Libadwaita  │                            │   Polkit auth        │
│   No system access   │                            │   systemd sandboxed  │
└──────────────────────┘                            └──────────────────────┘
```

**Key rules:**
- GUI code MUST NOT import daemon code or access system resources
- All privileged operations require Polkit authorization
- All subprocess calls use list form (no `shell=True`)
- All user-facing strings wrapped in `_()` for gettext

## Codebase Metrics

| Metric | Value |
|--------|-------|
| Source files | 43 Python modules |
| Lines of code | ~16,900 |
| GUI modules | 18 (views, widgets, support) |
| Daemon modules | 22 |
| Test files | 57 |
| Test functions | 1,386 |
| D-Bus methods | 20+ |
| Polkit actions | 4 |

## Epic/Story Map

| Epic | Stories | Status |
|------|---------|--------|
| 1: Core Infrastructure | 1.1–1.10 | Done |
| 2: Driver Management | 2.1–2.7 | Done |
| 3: Safety & Recovery | 3.1–3.5 | Done |
| 4: Power Management | 4.1–4.3 | Done |
| 5: Diagnostics | 5.1–5.3 | Done |
| 6: Polish & Hardening | 6.1–6.7 | Done |
