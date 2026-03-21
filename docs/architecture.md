# Architecture Overview

## Component Diagram

```
User
 │
 ├── verde (GUI app)
 │    ├── window.py          — Adw.Application, view stack
 │    ├── views/dashboard.py — Real-time GPU stats
 │    ├── views/drivers.py   — Driver install/rollback
 │    ├── views/power.py     — Suspend/hibernate fixes
 │    ├── views/diagnostics.py — Reports, audit log
 │    ├── dbus_client.py     — Async D-Bus proxy
 │    ├── gpu_state.py       — GObject property model
 │    ├── cli.py             — --check/--json headless mode
 │    └── widgets/           — Reusable UI components
 │
 │  D-Bus system bus (com.verde.Manager)
 │
 └── verde-daemon (root service)
      ├── service.py          — D-Bus method dispatch, Polkit gate
      ├── driver_manager.py   — apt/dpkg/ubuntu-drivers wrapper
      ├── power_manager.py    — systemctl, modprobe, initramfs
      ├── snapshot_manager.py — Pre-operation state capture
      ├── module_doctor.py    — DKMS/headers diagnosis + fix
      ├── diagnostics.py      — System report generation
      ├── state_tracker.py    — External change detection
      ├── modification_tracker.py — Change manifest + revert
      ├── nvml_wrapper.py     — NVML ctypes with graceful degradation
      ├── audit.py            — JSONL audit log
      ├── rate_limiter.py     — Token-bucket per caller
      ├── integrity_checker.py — Installation self-check
      ├── polkit.py           — SystemBusName authorization
      └── validators.py       — Input validation (regex + length + null)
```

## Security Layers

1. **D-Bus bus policy** — Only root owns the bus name; deny `Properties.Set`
2. **Polkit authorization** — Tiered: monitor (allow_active), driver.manage (auth_admin_keep), power.manage (auth_admin_keep)
3. **Rate limiting** — Token bucket per caller: 30 read/10s, 5 write/60s
4. **Input validation** — Regex + max length (256) + null byte rejection on all D-Bus string args
5. **systemd sandboxing** — ProtectSystem=strict, PrivateTmp, MemoryDenyWriteExecute, RestrictSUIDSGID, SystemCallFilter

## Data Storage

| Path | Purpose |
|------|---------|
| `/var/lib/verde/audit.log` | JSONL audit log |
| `/var/lib/verde/snapshots/` | Pre-operation driver state snapshots |
| `/var/lib/verde/last_state.json` | External change detection baseline |
| `/var/lib/verde/modifications.json` | System modification manifest |
| `/etc/modprobe.d/verde-nvidia.conf` | NVIDIA power management config |

## D-Bus Interface

Bus name: `com.verde.Manager`
Object path: `/com/verde/Manager`

**Read-only methods** (no auth): GetGPUInfo, GetGPUStats, GetCurrentDriver, ListAvailableDrivers, GetPowerStatus, ListSnapshots, GetPreflightCheck, GetPostRebootSummary, GetIntegrityStatus, ListModifications, DiagnoseModuleFailure, GetAuditLog

**Privileged methods** (Polkit): InstallDriver, RollbackDriver, FixSuspend, FixHibernate, FixModuleNotLoaded, RepairDpkg, DeleteSnapshot, RevertModification, GenerateDiagnosticReport

**Signals**: GPUStatsUpdated, DegradedStateChanged, OperationProgress, OperationComplete, RebootRequired, ExternalChangesDetected
