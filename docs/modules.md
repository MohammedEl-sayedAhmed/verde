# Module Reference

## GUI Modules (`src/verde/`)

### Core
| Module | Purpose |
|--------|---------|
| `main.py` | Entry point; CLI flag interception before GTK init |
| `window.py` | `VerdeApplication` + `VerdeWindow` with Adw.ViewStack |
| `cli.py` | `--check`/`--json` headless health check via Gio.DBusProxy |
| `dbus_client.py` | Async D-Bus proxy; emits GObject signals for GUI binding |
| `gpu_state.py` | GObject property model mapping D-Bus data to bindable properties |

### Views
| Module | Purpose |
|--------|---------|
| `views/dashboard.py` | Real-time GPU stats, degraded state display, module fix button |
| `views/drivers.py` | Driver discovery, install/rollback with preflight dialog |
| `views/power.py` | Suspend/hibernate status, fix flow, power profile display |
| `views/diagnostics.py` | Diagnostic report generation, audit log viewer with filtering |

### Widgets
| Module | Purpose |
|--------|---------|
| `widgets/status_indicator.py` | Color-coded label (good/warn/crit) with ATK support |
| `widgets/driver_card.py` | ActionRow builder for driver list entries |
| `widgets/preflight_banner.py` | Pre-flight check results panel for fix/install dialogs |
| `widgets/progress_overlay.py` | Multi-stage operation progress with error/success states |
| `widgets/snapshot_row.py` | ExpanderRow for snapshot display with rollback/delete |

### Content Catalogs
| Module | Purpose |
|--------|---------|
| `help_content.py` | Tooltip text for all GPU metrics and status indicators |
| `error_messages.py` | D-Bus error name → humanized message mapping |
| `humanized_status.py` | Raw values → plain-language descriptions (temp, P-state, VRAM) |

## Daemon Modules (`src/verde-daemon/`)

### Core
| Module | Purpose |
|--------|---------|
| `main.py` | Daemon entry point; D-Bus bus name acquisition |
| `service.py` | D-Bus method dispatch hub (~2800 lines); all method handlers |
| `nvml_wrapper.py` | NVML ctypes wrapper; per-function graceful degradation |
| `sysfs_gpu.py` | Fallback GPU detection via `/sys/bus/pci/devices/` |

### Manager Modules
| Module | Purpose |
|--------|---------|
| `driver_manager.py` | Driver discovery via ubuntu-drivers, dpkg, apt-cache |
| `power_manager.py` | Suspend/hibernate issue detection + fix (systemctl, modprobe) |
| `snapshot_manager.py` | Pre-operation dpkg state capture + JSONL storage |
| `module_doctor.py` | DKMS/headers/kernel-mismatch diagnosis + fix actions |
| `diagnostics.py` | Comprehensive system report (GPU, driver, logs, issues) |
| `modification_tracker.py` | JSON manifest of all system changes + atomic revert |
| `state_tracker.py` | External change detection via SHA-256 hash comparison |
| `pending_summary.py` | Cross-reboot operation state for post-reboot summary |

### Security
| Module | Purpose |
|--------|---------|
| `polkit.py` | Polkit `SystemBusName` authorization check |
| `rate_limiter.py` | Token-bucket rate limiter (read: 30/10s, write: 5/60s) |
| `validators.py` | Regex + length + null-byte validation for D-Bus args |
| `integrity_checker.py` | Self-check for required installation files |

### Error Handling
| Module | Purpose |
|--------|---------|
| `apt_errors.py` | APT error classification with user-friendly guidance |
| `degraded_states.py` | GPU state detection (normal, no_driver, driver_not_loaded, etc.) |
| `preflight.py` | Pre-flight safety checks (disk space, apt lock, network) |
| `recovery_diagnostics.py` | CLI recovery diagnostics for driver failures |
| `cli_recovery.py` | Interactive CLI recovery tool |
| `audit.py` | Append-only JSONL audit logger |
