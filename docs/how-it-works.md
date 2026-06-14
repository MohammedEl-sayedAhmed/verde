# How It Works

This document explains how Verde works at runtime — how the GUI talks to the
daemon, and what happens, step by step, when you perform a privileged operation
like installing a driver. For the static component breakdown, see
[architecture.md](architecture.md); for the method-by-method interface, see
[dbus-api.md](dbus-api.md).

## The core idea: a privilege boundary

Managing GPU drivers means installing packages, writing to `/etc`, and rebuilding
the initramfs — all of which require root. Running a whole GTK application as root
would be a large, risky attack surface. Verde instead splits into two processes
with a hard boundary between them:

```
┌────────────────────────┐    D-Bus system bus     ┌────────────────────────┐
│  verde (GUI)           │   com.verde.Manager     │  verde-daemon          │
│  • runs as your user   │ ◄─────────────────────► │  • runs as root        │
│  • GTK4 + Libadwaita   │                         │  • systemd sandboxed   │
│  • zero system access  │     every call is a     │  • Polkit-gated        │
│  • renders state only  │     D-Bus message       │  • does the real work  │
└────────────────────────┘                         └────────────────────────┘
```

The GUI is "dumb" on purpose: it never imports daemon code, never shells out,
never touches NVML or `apt`. Its only capability is sending D-Bus messages and
drawing the replies. All authority lives in the daemon, behind authorization.
This rule is enforced in CI — the pipeline greps the GUI tree and fails the build
if it finds an NVML import or a `shell=True`.

## Starting up

The daemon is **D-Bus / socket-activated**: it is not running until something
asks for `com.verde.Manager` on the system bus. The first GUI call starts it,
and it **shuts itself down after 60 seconds of idle** (`_IdleTimer` in
[`main.py`](../src/verde-daemon/main.py)) so nothing privileged lingers in memory.

1. You launch `verde`. [`src/verde/main.py`](../src/verde/main.py) checks for CLI
   flags (`--check`, `--json`, `--version`) *before* importing GTK, so headless
   health checks stay fast and dependency-free.
2. In GUI mode it loads the compiled `verde.gresource` bundle (UI, CSS, icon) and
   starts the `Adw.Application`.
3. [`dbus_client.py`](../src/verde/dbus_client.py) opens an async proxy to
   `com.verde.Manager`. That first message triggers systemd to launch
   `verde-daemon` as root.
4. The daemon acquires the bus name, registers its object, and arms the idle
   timer. The GUI subscribes to the daemon's signals (`GPUStatsUpdated`,
   `OperationProgress`, …) and binds them to the view models in
   [`gpu_state.py`](../src/verde/gpu_state.py).

## A read is simple

Read-only methods — `GetGPUInfo`, `GetGPUStats`, `GetPowerStatus`,
`ListSnapshots`, `DiagnoseModuleFailure`, … — need no authorization. The daemon
resets its idle timer, computes the answer synchronously (querying NVML, with a
`/sys/bus/pci` fallback when the driver isn't loaded — see
[`nvml_wrapper.py`](../src/verde-daemon/nvml_wrapper.py) and
[`sysfs_gpu.py`](../src/verde-daemon/sysfs_gpu.py)), and returns immediately.
`ListAvailableDrivers` is the exception: it runs in a worker thread so the
`apt-cache` query never blocks the main loop.

## A privileged operation, end to end

Here is the full lifecycle of `InstallDriver("550")`, which is representative of
every write operation (`RollbackDriver`, `FixSuspend`, `FixHibernate`,
`FixModuleNotLoaded`, `RevertModification`, …). The single dispatcher in
[`service.py`](../src/verde-daemon/service.py) runs the same ordered gauntlet of
guards for all of them.

### Phase 1 — Guards (cheap rejections first, before any password prompt)

| # | Guard | What it does | Rejected with |
|---|-------|--------------|---------------|
| 1 | **Sender check** | Reject messages with no bus sender identity | `AccessDenied` |
| 2 | **Rate limit** | Token bucket per caller — writes are capped at 5 / 60 s, reads at 30 / 10 s ([`rate_limiter.py`](../src/verde-daemon/rate_limiter.py)) | `com.verde.Error.RateLimited` |
| 3 | **Input validation** | Regex + max length (256) + null-byte rejection on every string argument ([`validators.py`](../src/verde-daemon/validators.py)) | `com.verde.Error.InvalidArgument` |
| 4 | **Action lookup** | Map the method to a Polkit action via `METHOD_ACTION_MAP` ([`polkit.py`](../src/verde-daemon/polkit.py)) | `UnknownMethod` |
| 5 | **Polkit authorization** | Ask Polkit whether *this caller* may perform *this action*. The subject is the caller's **`SystemBusName`** (not a spoofable process ID), with a 5 s timeout | `NotAuthorized` / `PolkitCancelled` / `PolkitTimeout` / `PolkitAgentMissing` |

Validation runs **before** Polkit deliberately: bad input should never trigger a
password dialog. If a guard fails, the daemon writes an audit entry (for the
security-relevant ones) and returns a D-Bus error — the GUI maps the
`com.verde.Error.*` name to a friendly message via
[`error_messages.py`](../src/verde/error_messages.py).

### Phase 2 — Claim the operation slot

6. **Concurrency guard** — only one write may run at a time. If one is already in
   progress, return `com.verde.Error.OperationInProgress`.
7. **dpkg lock check** — if another `apt`/`dpkg` process holds the lock, refuse
   early with `com.verde.Error.DpkgLocked` instead of hanging.
8. Generate a 12-char hex **`op_id`**, mark the operation in progress, and
   **return the `op_id` to the GUI immediately** — by design the dispatch path
   returns in well under a second (the daemon carries a `< 500 ms` budget for it),
   long before the install finishes. The actual work runs asynchronously; the GUI
   now tracks progress by `op_id`.

### Phase 3 — Do the work (in a worker thread)

9.  **Hold the idle timer** so the daemon can't self-terminate mid-install.
10. **Audit log: `started`** ([`audit.py`](../src/verde-daemon/audit.py) — append-only JSONL).
11. **Acquire a systemd inhibitor lock** (via logind) to block shutdown/sleep
    during the install.
12. **Write an operation marker** to disk so an interrupted install can be
    detected and recovered on the next boot.
13. **Take a pre-operation snapshot** of the current driver state
    ([`snapshot_manager.py`](../src/verde-daemon/snapshot_manager.py)) — this is
    what `RollbackDriver` later restores.
14. **Run `apt-get install`**, parsing its `status-fd` stream and emitting
    `OperationProgress (op_id, percent, message)` signals as it goes. Signals are
    marshalled back onto the GLib main loop with `GLib.idle_add` so they're
    thread-safe; the GUI's [`progress_overlay`](../src/verde/widgets/progress_overlay.py)
    animates from them.

### Phase 4 — Finish and clean up

15. On success: record the change in the **modification tracker** (so it can be
    reverted), write a **pending summary** for a post-reboot recap, and emit
    `OperationComplete (op_id, true, message)` followed by
    `RebootRequired (true, reason)`.
16. On failure: `apt` errors are classified into friendly categories
    ([`apt_errors.py`](../src/verde-daemon/apt_errors.py)) and returned as a
    JSON-encoded payload inside `OperationComplete (op_id, false, …)`. Raw Python
    exceptions are **never** exposed across D-Bus.
17. **Audit log: `success` / `failed`**.
18. A `finally` block always runs: remove the operation marker, release the
    inhibitor lock, clear the concurrency guard, and release the idle timer — even
    if the worker threw.

## Why this shape

- **Least privilege** — the privileged surface is one small, sandboxed daemon
  with a fixed D-Bus vocabulary, not a whole GUI running as root.
- **Defense in depth** — rate limit → validate → authorize → execute, with
  systemd sandboxing (`ProtectSystem=strict`, `MemoryDenyWriteExecute`,
  `SystemCallFilter`, …) underneath all of it.
- **Safe by default** — every change is snapshotted before it happens, recorded
  so it can be reverted, and written to an append-only audit log.
- **Recoverable** — operation markers and the `--repair` recovery mode mean an
  install interrupted by a crash or power loss can be detected and fixed, not left
  half-applied.

For the exact method signatures, signal shapes, and Polkit actions, see
[dbus-api.md](dbus-api.md).
