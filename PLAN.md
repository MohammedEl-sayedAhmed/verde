# Verde — NVIDIA GPU Manager for Ubuntu

## Context

Managing NVIDIA GPUs on Ubuntu is a fragmented, painful experience. Users juggle 5+ tools (ubuntu-drivers, nvidia-settings, nvidia-smi, envycontrol, manual config files) to accomplish what Windows does in one place. Key pain points: driver installation breaks after kernel updates, hibernate/suspend breaks after NVIDIA install, nvidia-settings is broken on Wayland, no rollback when things go wrong, Secure Boot MOK enrollment is confusing, fan control requires arcane CoolBits setup. No existing tool covers even half of what's needed. Verde aims to be the single unified GUI that solves all NVIDIA pain points on Ubuntu.

## Architecture

```
┌─────────────────────────────────┐
│  GTK4 + Libadwaita 1.4 Frontend│  (runs as user)
│  Python / PyGObject             │
└──────────────┬──────────────────┘
               │ D-Bus (system bus)
               │ Async calls + signal subscriptions
┌──────────────▼──────────────────┐
│  verde-daemon (privileged)      │  (runs as root via D-Bus activation)
│  Python service (Gio.DBus)      │  Auto-exits after 120s idle
│  Protected by polkit policies   │
└──────────────┬──────────────────┘
               │
    ┌──────────┼──────────┬──────────────┐
    ▼          ▼          ▼              ▼
  apt/dpkg   NVML     modprobe      systemctl
  ubuntu-    (ctypes)  /initramfs   /polkit
  drivers              /grub
```

**Tech stack:** Python 3.12, GTK4, Libadwaita 1.4, PyGObject, NVML (via ctypes), D-Bus (Gio), Polkit, Meson build system.

**Target:** Ubuntu 24.04 LTS → Libadwaita 1.4 (GNOME 45). Do NOT use APIs from 1.5+ (no AdwAlertDialog, AdwDialog, AdwAboutDialog, AdwButtonRow).

## Project Structure

```
/home/mohammed/Repos/verde/
├── meson.build                     # Top-level build
├── verde/                          # Frontend package
│   ├── __init__.py
│   ├── main.py                     # Application entry point
│   ├── application.py              # Adw.Application subclass + AdwAboutWindow
│   ├── window.py                   # Main window with AdwNavigationSplitView
│   ├── views/                      # UI pages
│   │   ├── dashboard.py            # GPU info + status overview
│   │   ├── drivers.py              # Driver install/switch/rollback
│   │   ├── power.py                # Hibernate, suspend, power fixes
│   │   └── empty_state.py          # "No GPU detected" / error fallback page
│   ├── services/
│   │   ├── daemon_proxy.py         # Async D-Bus proxy to verde-daemon
│   │   └── nvml.py                 # ctypes NVML wrapper (read-only, no root)
│   └── widgets/                    # Reusable custom widgets
│       └── gpu_info_card.py        # Status card for dashboard
├── verde-daemon/                   # Privileged backend
│   ├── service.py                  # D-Bus service entry + introspection XML
│   └── operations/
│       ├── driver_ops.py           # apt/ubuntu-drivers wrapper + snapshot/rollback
│       ├── power_ops.py            # Hibernate/suspend/kernel param fixes
│       └── secureboot_ops.py       # MOK status + enrollment guidance
├── data/
│   ├── io.github.Verde.desktop     # .desktop file
│   ├── io.github.Verde.svg         # App icon
│   ├── io.github.Verde.gschema.xml # GSettings schema
│   ├── dbus/
│   │   ├── io.github.Verde.Daemon.conf     # D-Bus system bus policy (XML)
│   │   └── io.github.Verde.Daemon.service  # D-Bus activation service file
│   ├── polkit/
│   │   └── io.github.Verde.policy  # Polkit action definitions
│   └── systemd/
│       └── io.github.Verde.Daemon.service  # Systemd unit (Type=dbus)
└── debian/                         # Debian packaging
    ├── control
    ├── rules
    ├── install
    └── postinst
```

## UI/UX Design

### Navigation Pattern

Use **AdwNavigationSplitView** (NOT AdwNavigationView) for sidebar + content layout:
- Sidebar: `GtkListBox` with rows for Dashboard, Drivers, Power
- Content: Swaps between view pages based on sidebar selection
- Auto-collapses to single-pane on narrow windows (< ~500px) with back button
- Each pane wrapped in `AdwToolbarView` with `AdwHeaderBar`

```
┌──────────────────────────────────────────────────────┐
│ ● ● ●  Verde                    Dashboard            │
├──────────────┬───────────────────────────────────────┤
│              │  ┌─ GPU ────────────────────────────┐ │
│  Dashboard ◄ │  │ Model         GeForce 840M       │ │
│              │  │ Driver        535.288.01          │ │
│  Drivers     │  │ VRAM          412 / 2048 MB      │ │
│              │  └──────────────────────────────────┘ │
│  Power       │  ┌─ Status ─────────────────────────┐ │
│              │  │ Temperature   45°C            ●  │ │
│              │  │ Power State   P8 (idle)          │ │
│              │  │ Utilization   3%                 │ │
│              │  └──────────────────────────────────┘ │
│              │  ┌─ Quick Actions ───────────────────┐ │
│              │  │ Fix Suspend Issues     [Fix]     │ │
│              │  │ Update Driver          [Update]  │ │
│              │  └──────────────────────────────────┘ │
└──────────────┴───────────────────────────────────────┘
```

### Widget Choices (Libadwaita 1.4)

| Purpose | Widget | Notes |
|---|---|---|
| View containers | `AdwPreferencesPage` | Auto-scrolling, auto-clamped width |
| Info groups | `AdwPreferencesGroup` | Titled sections within views |
| Info rows | `AdwActionRow` | Title + subtitle + optional suffix |
| Toggle switches | `AdwSwitchRow` | Hibernate on/off, suspend fix on/off |
| Dropdowns | `AdwComboRow` | Driver version selection |
| Persistent alerts | `AdwBanner` | "Reboot required", "Driver broken" |
| Transient notifications | `AdwToast` via `AdwToastOverlay` | Operation success/failure |
| Confirmation dialogs | `AdwMessageDialog` | Before driver install, before power changes |
| Empty/error states | `AdwStatusPage` | "No GPU detected", "Driver not loaded" |
| About | `AdwAboutWindow` | App info, version, links |
| Expandable details | `AdwExpanderRow` | Advanced settings, driver package details |
| Toolbar wrapper | `AdwToolbarView` | Wraps each navigation page |

### Toast Notifications

Wrap the main content in `AdwToastOverlay`. Show toasts for:
- Operation success: "Driver 535 installed successfully"
- Operation failure: "Installation failed: apt returned error"
- Action toasts: "Driver installed. Reboot required." with [Reboot Now] button
- Auth cancelled: "Authentication was cancelled"

### Confirmation Dialogs

Use `AdwMessageDialog` before destructive/privileged operations:
- Driver install: "Install nvidia-driver-535? This may require a reboot."
- Hibernate enable: "Enable hibernate? This will modify system configuration."
- Rollback: "Roll back to previous driver? Current driver will be removed."

### UI States

Every view must handle all four states:

| State | Widget | Example |
|---|---|---|
| **Normal** | `AdwPreferencesPage` with data | Dashboard showing GPU info |
| **Empty** | `AdwStatusPage` | "No NVIDIA GPU Detected" with icon |
| **Error** | `AdwStatusPage` with retry button | "Failed to load NVML library" |
| **Loading** | `GtkSpinner` in row suffix or `AdwStatusPage` | Spinner while fetching driver list |
| **In-progress** | Progress bar + disabled actions + `AdwBanner` | "Installing driver... 45%" |
| **Reboot needed** | `AdwBanner` at top of view | "Reboot required to complete changes" |

### Graceful Degradation

| Condition | Behavior |
|---|---|
| No NVIDIA GPU | `AdwStatusPage` — "No NVIDIA GPU Detected" with description |
| NVML library missing | `AdwStatusPage` — "NVIDIA driver not installed" with install button |
| Driver not loaded | Dashboard shows model from PCI, "Driver: Not loaded" with install action |
| Daemon unreachable | Inline error banner on affected views, read-only features still work |
| Laptop GPU (no fan) | Hide fan-related UI, `nvmlDeviceGetNumFans() == 0` check |

## Phase 1 — MVP (implement now)

### 1. Project scaffolding
- Meson build system, .desktop file, app icon placeholder, GSettings schema
- `Adw.Application` subclass with `AdwAboutWindow`
- Main window: `AdwNavigationSplitView` with sidebar `GtkListBox` + content area
- `AdwToastOverlay` wrapping the split view for notifications
- `AdwToolbarView` + `AdwHeaderBar` for each navigation pane

### 2. NVML ctypes wrapper (`verde/services/nvml.py`)
- Load `libnvidia-ml.so.1` via ctypes (NOT `.so`, use the SONAME)
- Fallback: `ctypes.util.find_library("nvidia-ml")`
- `nvmlInit_v2()` at app start, `nvmlShutdown()` at app exit, cache device handle
- Every call checks return code; `NVML_ERROR_NOT_SUPPORTED` (3) returns `None` gracefully
- No external dependency (no pynvml/nvidia-ml-py — raw ctypes for clean .deb packaging)
- Exposed queries: GPU name, driver version, temperature, VRAM (total/used/free), clock speeds, utilization, power usage, fan speed (returns None on laptop GPUs)

### 3. Dashboard view
- `AdwPreferencesPage` with groups: GPU, Status, Quick Actions
- GPU group: `AdwActionRow` for model, driver version, VRAM
- Status group: `AdwActionRow` for temperature (with color indicator suffix), power state, utilization
- Quick Actions group: `AdwActionRow` with button suffix for common fixes
- `AdwBanner` at top (conditional) for "Reboot required" / "Driver issue detected"
- Auto-refresh via `GLib.timeout_add_seconds(2, poll_callback)` — NVML reads are <1ms, safe on main thread
- Graceful degradation: `AdwStatusPage` when no GPU / NVML unavailable

### 4. Driver management view
- `AdwPreferencesPage` with groups: Current Driver, Available Drivers, Snapshot
- Current driver: `AdwActionRow` showing installed version + recommended badge
- Available drivers: `AdwComboRow` or list of `AdwActionRow` with install buttons
- List available driver versions via `ubuntu-drivers list` (through daemon)
- Install/switch driver with confirmation (`AdwMessageDialog`) → async D-Bus call
- During install: disable action buttons, show `GtkSpinner` in row suffix + `AdwBanner` with progress text
- On completion: `AdwToast` with result, show reboot banner if needed
- Open/proprietary kernel module detection + guidance via `AdwExpanderRow`
- **Pre-install snapshot**: save current driver package list + versions to `/var/lib/verde/snapshots/`
  - Stored as JSON: `{timestamp, packages: [{name, version}], driver_version}`
  - Keep last 3 snapshots, auto-prune older ones
- **Rollback**: restore previous driver from snapshot if install fails
  - Also available manually from Snapshot group with `AdwActionRow` per snapshot

### 5. Power fixes view
- `AdwPreferencesPage` with groups: Hibernate, Suspend, Secure Boot
- **Hibernate group**:
  - `AdwSwitchRow` — "Enable Hibernate" with status subtitle ("Enabled" / "Disabled — systemd memory check blocks it")
  - Detection: check `CanHibernate` via logind D-Bus
  - One-click fix: initramfs resume config, systemd-logind override (`SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK=1`), polkit policy, polkitd-pkla install, swap check
  - Based on tested fix from ~/Repos/Hibernate/enable-hibernate.sh
  - `AdwExpanderRow` "Details" showing what changes will be made
- **Suspend group**:
  - `AdwSwitchRow` — "Fix NVIDIA Suspend" with status subtitle
  - Detection: check nvidia-suspend/resume/hibernate service status
  - One-click fix: enable services + set `NVreg_PreserveVideoMemoryAllocations=1` + `nvidia-drm modeset=1`
  - `AdwExpanderRow` "Details" showing services and kernel params
- **Secure Boot group**:
  - `AdwActionRow` with MOK status subtitle
  - Guide through enrollment if needed (instructions, not auto-enrollment)
  - Hidden when boot mode is BIOS (this machine)
- All toggle actions: confirmation dialog → async D-Bus call → toast on completion → reboot banner if needed

### 6. Privileged daemon (`verde-daemon/service.py`)
- D-Bus system service with Gio.DBusConnection (NOT dbus-python)
- D-Bus activation via service file + systemd unit (`Type=dbus`)
- **Auto-exit after 120s idle**: GLib timeout, reset on every method call
- **Concurrency guard**: reject new privileged ops while one is running (`AlreadyInProgress` error)
- Clean introspection XML defining the full API
- Polkit check before every privileged method via `org.freedesktop.PolicyKit1.Authority.CheckAuthorization`
- **Long operations pattern** (driver install):
  1. Method returns immediately with `operation_id` (string)
  2. Daemon runs apt in background thread
  3. Emits `OperationProgress(operation_id, progress_fraction, status_message)` D-Bus signal
  4. Emits `OperationComplete(operation_id, success, error_message)` D-Bus signal
  5. Frontend subscribes to signals and updates progress bar + status text
- apt progress tracking via `APT::Status-Fd` for real percentage reporting

### 7. D-Bus API

**Introspection XML** (`io.github.Verde.Daemon`):

```xml
<node>
  <interface name="io.github.Verde.Daemon">
    <!-- Driver operations -->
    <method name="GetAvailableDrivers">
      <arg name="drivers" type="a(ssb)" direction="out"/>
      <!-- array of (version, status, is_recommended) -->
    </method>
    <method name="InstallDriver">
      <arg name="driver_version" type="s" direction="in"/>
      <arg name="operation_id" type="s" direction="out"/>
    </method>
    <method name="RollbackDriver">
      <arg name="snapshot_id" type="s" direction="in"/>
      <arg name="operation_id" type="s" direction="out"/>
    </method>
    <method name="GetSnapshots">
      <arg name="snapshots" type="a(sss)" direction="out"/>
      <!-- array of (id, timestamp, driver_version) -->
    </method>

    <!-- Power operations -->
    <method name="GetHibernateStatus">
      <arg name="status" type="a{sv}" direction="out"/>
    </method>
    <method name="EnableHibernate">
      <arg name="operation_id" type="s" direction="out"/>
    </method>
    <method name="GetSuspendStatus">
      <arg name="status" type="a{sv}" direction="out"/>
    </method>
    <method name="FixSuspend">
      <arg name="operation_id" type="s" direction="out"/>
    </method>
    <method name="GetSecureBootStatus">
      <arg name="status" type="a{sv}" direction="out"/>
    </method>

    <!-- Signals for async operations -->
    <signal name="OperationProgress">
      <arg name="operation_id" type="s"/>
      <arg name="progress" type="d"/>
      <arg name="status_message" type="s"/>
    </signal>
    <signal name="OperationComplete">
      <arg name="operation_id" type="s"/>
      <arg name="success" type="b"/>
      <arg name="message" type="s"/>
    </signal>
  </interface>
</node>
```

**D-Bus error hierarchy:**
- `io.github.Verde.Error.NotAuthorized` — polkit auth failed/dismissed
- `io.github.Verde.Error.AlreadyInProgress` — another operation is running
- `io.github.Verde.Error.AptFailed` — apt/dpkg returned an error
- `io.github.Verde.Error.DriverNotFound` — requested driver version doesn't exist
- `io.github.Verde.Error.SnapshotFailed` — snapshot create/restore failed
- `io.github.Verde.Error.InvalidArgument` — bad input parameter
- `io.github.Verde.Error.InternalError` — catch-all for unexpected errors

### 8. D-Bus bus policy + activation files

**Bus policy** (`data/dbus/io.github.Verde.Daemon.conf`):
- Only root can own `io.github.Verde.Daemon`
- Any user can send to the daemon (polkit handles fine-grained auth)
- Allow standard interfaces: Introspectable, Properties, Peer

**D-Bus activation** (`data/dbus/io.github.Verde.Daemon.service`):
```ini
[D-BUS Service]
Name=io.github.Verde.Daemon
Exec=/usr/libexec/verde-daemon
User=root
SystemdService=io.github.Verde.Daemon.service
```

**Systemd unit** (`data/systemd/io.github.Verde.Daemon.service`):
```ini
[Unit]
Description=Verde GPU Manager Daemon
After=dbus.service

[Service]
Type=dbus
BusName=io.github.Verde.Daemon
ExecStart=/usr/libexec/verde-daemon
User=root
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/etc /var/lib/dpkg /var/cache/apt /var/lib/verde
TimeoutStopSec=10
```

### 9. Polkit policy

```xml
<action id="io.github.verde.install-driver">
  <description>Install or switch NVIDIA driver</description>
  <message>Authentication is required to install an NVIDIA driver</message>
  <defaults>
    <allow_any>auth_admin</allow_any>
    <allow_inactive>auth_admin</allow_inactive>
    <allow_active>auth_admin_keep</allow_active>
  </defaults>
</action>

<action id="io.github.verde.configure-power">
  <description>Configure power management (hibernate/suspend)</description>
  <message>Authentication is required to change power settings</message>
  <defaults>
    <allow_any>auth_admin</allow_any>
    <allow_inactive>auth_admin</allow_inactive>
    <allow_active>auth_admin_keep</allow_active>
  </defaults>
</action>

<action id="io.github.verde.configure-system">
  <description>Modify system configuration</description>
  <message>Authentication is required to change system configuration</message>
  <defaults>
    <allow_any>auth_admin</allow_any>
    <allow_inactive>auth_admin</allow_inactive>
    <allow_active>auth_admin_keep</allow_active>
  </defaults>
</action>
```

`auth_admin_keep` = ask for password, remember for ~5 minutes. Good UX for sequential operations.

## Phase 2 (future)
- Real-time GPU monitoring with graphs (temp, clocks, utilization, VRAM) — `verde/views/monitor.py`
- Fan control with custom curves via NVML — `verde/views/fan_control.py` + `verde-daemon/operations/fan_ops.py`
  - Note: laptop GPUs (like 840M) have no user-controllable fan; UI must detect and hide fan controls
- Power limit adjustment — `verde-daemon/operations/gpu_ops.py`
- Performance profiles (Gaming / Quiet / Power Save)
- Settings view — `verde/views/settings.py`
- Graph widget — `verde/widgets/graph_widget.py`
- Fan curve editor — `verde/widgets/fan_curve_editor.py`

## Phase 3 (future)
- Optimus/hybrid graphics switching
- Overclocking (GPU/memory clock offsets via NVML)
- System tray indicator
- Driver update notifications

## Implementation Order for Phase 1

1. Create directory structure + `meson.build` + entry point + GSettings schema
2. Build `Adw.Application` shell: `AdwNavigationSplitView` + sidebar + `AdwToastOverlay` + `AdwAboutWindow`
3. Implement NVML ctypes wrapper (read-only GPU queries, graceful `NOT_SUPPORTED` handling)
4. Build Dashboard view (`AdwPreferencesPage` + `AdwActionRow` groups + GLib polling + `AdwStatusPage` fallback)
5. Implement D-Bus daemon skeleton: Gio.DBusConnection + introspection XML + auto-exit timer + concurrency guard
6. Create D-Bus bus policy + activation files + systemd unit + polkit policy
7. Build Driver management view + `driver_ops` backend + async progress signals + snapshot/rollback
8. Build Power fixes view + `power_ops` backend + `AdwSwitchRow` toggles
9. Build Secure Boot detection + `secureboot_ops` backend (hidden on BIOS systems)
10. Wire everything together: end-to-end test all flows, verify graceful degradation
11. Create `debian/` packaging

## Key System Details (this machine)

- **Ubuntu 24.04 LTS** (noble), systemd 255, kernel 6.8.0-101-generic
- **Libadwaita 1.4** (GNOME 45) — `gir1.2-adw-1` package
- **Python 3.12** — `python3-gi` for PyGObject
- **GPU:** GeForce 840M (Maxwell GM108), NVIDIA driver 535.288.01
  - Laptop GPU: no user-controllable fan (`nvmlDeviceGetNumFans` returns 0)
  - NVML reads (temp, VRAM, clocks, power, utilization) all work without root
- **NVML library:** `/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1`
- **Swap:** /dev/sda4 (14.2G partition), UUID=574557ce-1f6c-4b0d-b57b-e21cfe4f9b7a
- **Boot:** BIOS (not UEFI), no Secure Boot — hide Secure Boot UI section
- **D-Bus:** `dbus-daemon` (reference implementation, not dbus-broker)
- **Hibernate issue:** systemd 255 CanHibernate=no, fix: `SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK=1`
- **Polkit issue:** Ubuntu 24.04 needs `polkitd-pkla` package for `.pkla` policy files

## Verification

- Run `meson setup build && meson compile -C build` to build
- Run `verde` from build dir to launch the GUI
- Verify dashboard shows correct GPU info via NVML
- Verify `AdwStatusPage` fallback when NVML is unavailable (rename .so temporarily)
- Verify sidebar collapses on narrow window resize
- Test driver listing without root (read-only via daemon)
- Test driver install with polkit prompt (requires auth) — verify progress signals and toast
- Test hibernate enable flow end-to-end — verify `AdwSwitchRow` state updates
- Verify daemon auto-exits after 120s idle (`journalctl -u io.github.Verde.Daemon`)
- Verify concurrent operation rejection (click install twice rapidly)
- Test rollback from snapshot
