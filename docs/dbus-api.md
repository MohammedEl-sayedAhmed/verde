# D-Bus API Reference

**Bus name:** `com.verde.Manager`
**Object path:** `/com/verde/Manager`
**Interface:** `com.verde.Manager`

## Read-Only Methods (no Polkit auth)

| Method | Returns | Description |
|--------|---------|-------------|
| `Ping()` | — | Heartbeat; resets idle shutdown timer |
| `GetGPUInfo()` | `a{sv}` | Static GPU info (name, driver, VRAM, architecture) |
| `GetGPUStats()` | `a{sv}` | Live stats (temp, util, memory, fan, power, throttle) |
| `GetCurrentDriver()` | `a{sv}` | Installed driver details |
| `ListAvailableDrivers()` | `aa{sv}` | Available drivers from ubuntu-drivers |
| `GetPowerStatus()` | `a{sv}` | Suspend/hibernate/Secure Boot/Wayland status |
| `ListSnapshots()` | `aa{sv}` | Pre-operation state snapshots |
| `GetPreflightCheck(s operation)` | `a{sv}` | Pre-flight safety checks for an operation |
| `GetPostRebootSummary()` | `a{sv}` | Cross-reboot operation result |
| `ClearPostRebootSummary()` | — | Clear the post-reboot summary |
| `GetIntegrityStatus()` | `a{sv}` | Installation self-check results |
| `ListModifications()` | `aa{sv}` | Active system modifications Verde has made |
| `DiagnoseModuleFailure()` | `a{sv}` | Why NVIDIA module isn't loaded |
| `GetAuditLog(s filter_type, s result, s date_from)` | `aa{sv}` | Filtered audit log entries |

## Privileged Methods (Polkit required)

| Method | Auth Action | Returns | Description |
|--------|------------|---------|-------------|
| `InstallDriver(s version)` | `com.verde.driver.manage` | `s` op_id | Install NVIDIA driver version |
| `RollbackDriver(s snapshot_id)` | `com.verde.driver.manage` | `s` op_id | Rollback to snapshot state |
| `FixSuspend()` | `com.verde.power.manage` | `s` op_id | Enable NVIDIA suspend services |
| `FixHibernate()` | `com.verde.power.manage` | `s` op_id | Configure hibernate + initramfs |
| `FixModuleNotLoaded()` | `com.verde.driver.manage` | `s` op_id | Fix DKMS/headers/blacklist issues |
| `RepairDpkg()` | `com.verde.driver.manage` | — | Run dpkg --configure -a |
| `DeleteSnapshot(s snapshot_id)` | `com.verde.driver.manage` | — | Delete a state snapshot |
| `RevertModification(s mod_id)` | `com.verde.power.manage` | `b` | Undo a system modification |
| `GenerateDiagnosticReport(s format)` | `com.verde.diagnostics` | `s` | Generate markdown/JSON report |

## Signals

| Signal | Signature | Description |
|--------|-----------|-------------|
| `GPUStatsUpdated` | `a{sv}` | Periodic GPU stats update |
| `DegradedStateChanged` | `a{sv}` | GPU state transition (normal, no_driver, etc.) |
| `OperationProgress` | `(sds)` | op_id, percent, message |
| `OperationComplete` | `(sbs)` | op_id, success, message |
| `RebootRequired` | `(bs)` | required, reason |
| `ExternalChangesDetected` | `(aa{sv}aa{sv})` | changes, integrity_issues |

## Exit Codes (CLI `--check` mode)

| Code | Meaning |
|------|---------|
| 0 | Healthy — all GPUs normal |
| 1 | Warning — temp 85-95C, throttling, VRAM >90% |
| 2 | Critical — temp >95C, driver not loaded |
| 3 | No GPU — no NVIDIA GPU detected |
| 4 | Error — daemon unreachable |
