"""CLI recovery tool — verde --repair.

Standalone recovery mode that works from a TTY without GUI, D-Bus, or
display server.  Diagnoses common driver failures and offers rollback
or nouveau fallback.

References: FR19, FR22, FR23, FR58; AC#1-#9 of Story 3.4.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import logging
import os
import pathlib
import re
import subprocess
import sys

log = logging.getLogger("verde.recovery")

# ── Constants ───────────────────────────────────────────────────────

SNAPSHOT_DIR = pathlib.Path("/var/lib/verde/snapshots")
VERDE_DATA_DIR = pathlib.Path("/var/lib/verde")
NVIDIA_BLACKLIST_PATH = pathlib.Path("/etc/modprobe.d/verde-nvidia-blacklist.conf")

_SNAPSHOT_ID_RE = re.compile(r"^[a-zA-Z0-9T_.-]+$")


def _validate_snapshot_id(snapshot_id: str, snapshot_dir: pathlib.Path) -> bool:
    """Return True if snapshot_id is safe for path construction."""
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        return False
    resolved = (snapshot_dir / f"{snapshot_id}.json").resolve()
    return resolved.parent == snapshot_dir.resolve()


_BLACKLIST_CONTENT = """\
# Verde recovery: NVIDIA modules blacklisted, nouveau enabled
# Remove this file and run 'sudo update-initramfs -u' to restore NVIDIA
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_uvm
blacklist nvidia_modeset
"""


# ── Argument parsing ────────────────────────────────────────────────


def parse_recovery_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI recovery arguments.

    Parameters
    ----------
    argv : list[str]
        Argument list (typically ``sys.argv[1:]``).

    Returns
    -------
    argparse.Namespace
        Parsed arguments with ``repair``, ``list_snapshots``, ``rollback``,
        ``diagnose``, and ``yes`` flags.
    """
    parser = argparse.ArgumentParser(
        prog="verde",
        description="Verde GPU driver manager",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        default=False,
        help="Enter recovery mode (works from TTY without GUI)",
    )
    parser.add_argument(
        "--list-snapshots",
        action="store_true",
        default=False,
        help="List available driver snapshots (non-interactive)",
    )
    parser.add_argument(
        "--rollback",
        type=str,
        default=None,
        help="Rollback to a specific snapshot ID (non-interactive)",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        default=False,
        help="Output diagnostic results without recovery actions",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompts (for scripting)",
    )

    return parser.parse_args(argv)


# ── Utility checks ──────────────────────────────────────────────────


def check_root_privilege() -> bool:
    """Return True if running as root."""
    return os.geteuid() == 0


def is_tty() -> bool:
    """Return True if both stdin and stdout are TTYs."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def supports_color() -> bool:
    """Return True if the terminal likely supports ANSI color."""
    term = os.environ.get("TERM", "")
    if not term or term == "dumb":
        return False
    return is_tty()


# ── Snapshot helpers ────────────────────────────────────────────────


def load_snapshots(
    snapshot_dir: pathlib.Path = SNAPSHOT_DIR,
) -> list[dict]:
    """Load snapshot metadata from disk (no D-Bus needed).

    Returns list of snapshot dicts sorted newest-first.
    """
    if not snapshot_dir.exists():
        return []

    snapshots = []
    for path in sorted(snapshot_dir.glob("*.json"), reverse=True):
        if path.name.startswith(".tmp_"):
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            snapshots.append(data)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Cannot read snapshot %s: %s", path.name, exc)
    return snapshots


def verify_snapshot_integrity(snapshot_path: pathlib.Path) -> bool:
    """Verify SHA-256 integrity of a snapshot file."""
    try:
        from snapshot_manager import verify_snapshot_integrity as _verify

        return _verify(snapshot_path)
    except Exception:
        return False


def format_snapshot_table(snapshots: list[dict], snapshot_dir: pathlib.Path = SNAPSHOT_DIR) -> str:
    """Format snapshots as a text table for TTY display."""
    if not snapshots:
        return "No snapshots available."

    lines = []
    header = f"{'#':<4} {'Date':<20} {'Driver':<12} {'Integrity'}"
    lines.append(header)
    lines.append("-" * len(header))

    for i, snap in enumerate(snapshots, 1):
        sid = snap.get("snapshot_id", "")
        timestamp = snap.get("timestamp", "")
        try:
            dt = datetime.datetime.fromisoformat(timestamp)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            date_str = timestamp[:19] if timestamp else "unknown"

        op = snap.get("operation") or {}
        driver = op.get("target_driver", "") if isinstance(op, dict) else ""

        # Check integrity
        snap_path = snapshot_dir / f"{sid}.json"
        integrity = "OK" if snap_path.exists() and verify_snapshot_integrity(snap_path) else "FAIL"

        lines.append(f"{i:<4} {date_str:<20} {driver:<12} {integrity}")

    return "\n".join(lines)


# ── Rollback execution ──────────────────────────────────────────────


def execute_rollback(
    snapshot_data: dict,
    progress_fn: object | None = None,
) -> tuple[bool, str]:
    """Execute snapshot rollback via apt (standalone, no D-Bus).

    Parameters
    ----------
    snapshot_data : dict
        Full snapshot JSON data.
    progress_fn : callable | None
        Called with (message: str) at each stage.

    Returns
    -------
    tuple[bool, str]
        (success, message)
    """

    def _progress(msg: str) -> None:
        if progress_fn is not None:
            with contextlib.suppress(Exception):
                progress_fn(msg)

    packages = snapshot_data.get("driver_packages", [])
    if not isinstance(packages, list):
        return (False, "Snapshot contains malformed driver_packages")
    if not packages:
        return (False, "Snapshot contains no driver packages")

    install_specs = []
    for p in packages:
        name = p.get("name", "")
        version = p.get("version", "")
        if name and version:
            install_specs.append(f"{name}={version}")
        elif name:
            install_specs.append(name)

    if not install_specs:
        return (False, "No valid packages found in snapshot")

    _progress("Installing snapshot packages...")
    try:
        result = subprocess.run(
            ["apt-get", "install", "-y", "--allow-downgrades", *install_specs],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            return (
                False,
                f"apt-get install failed: {result.stderr.strip() or f'exit code {result.returncode}'}",
            )
    except subprocess.TimeoutExpired:
        return (False, "Package installation timed out after 600 seconds")
    except FileNotFoundError:
        return (False, "apt-get not found")

    _progress("Rebuilding initramfs...")
    try:
        subprocess.run(
            ["update-initramfs", "-u"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("update-initramfs skipped: %s", exc)

    driver = "unknown"
    op = snapshot_data.get("operation")
    if isinstance(op, dict):
        driver = op.get("target_driver", "unknown")
    sid = snapshot_data.get("snapshot_id", "unknown")
    return (True, f"Rolled back to snapshot {sid} (driver {driver})")


# ── Nouveau fallback ────────────────────────────────────────────────


def execute_nouveau_fallback(
    blacklist_path: pathlib.Path = NVIDIA_BLACKLIST_PATH,
) -> tuple[bool, str]:
    """Apply nouveau fallback: blacklist NVIDIA modules, rebuild initramfs.

    Returns
    -------
    tuple[bool, str]
        (success, message)
    """
    # Verify nouveau module is available before proceeding
    try:
        result = subprocess.run(
            ["modinfo", "nouveau"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return (
                False,
                "Nouveau kernel module not found — cannot fall back to open-source driver",
            )
    except FileNotFoundError:
        return (False, "modinfo not found — cannot verify nouveau availability")
    except subprocess.TimeoutExpired:
        return (False, "modinfo timed out — cannot verify nouveau availability")

    try:
        blacklist_path.parent.mkdir(parents=True, exist_ok=True)
        blacklist_path.write_text(_BLACKLIST_CONTENT)
    except OSError as exc:
        return (False, f"Cannot write blacklist file: {exc}")

    # Check for conflicting nouveau blacklist
    modprobe_dir = pathlib.Path("/etc/modprobe.d")
    for conf in modprobe_dir.glob("*.conf"):
        if conf == blacklist_path:
            continue
        try:
            content = conf.read_text()
            lines = content.splitlines(keepends=True)
            modified = False
            new_lines = []
            for line in lines:
                if re.match(r"^\s*blacklist\s+nouveau\b", line):
                    new_lines.append(f"# {line.rstrip()}  # disabled by verde recovery\n")
                    modified = True
                else:
                    new_lines.append(line)
            if modified:
                log.info("Removing conflicting nouveau blacklist in %s", conf)
                conf.write_text("".join(new_lines))
        except OSError:
            pass

    # Rebuild initramfs
    try:
        result = subprocess.run(
            ["update-initramfs", "-u"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return (
                False,
                f"update-initramfs failed: {result.stderr.strip() or f'exit code {result.returncode}'}",
            )
    except FileNotFoundError:
        return (False, "update-initramfs not found")
    except subprocess.TimeoutExpired:
        return (False, "update-initramfs timed out")

    return (
        True,
        "Nouveau fallback applied. Reboot now. "
        "Re-run Verde after reboot to install a working NVIDIA driver.",
    )


# ── Recovery CLI class ──────────────────────────────────────────────


class RecoveryCLI:
    """Text-mode recovery interface.

    Operates independently of GTK, D-Bus, and GLib main loop.
    All system interaction goes through subprocess (list form) or
    direct filesystem access.
    """

    def __init__(
        self,
        args: argparse.Namespace,
        snapshot_dir: pathlib.Path = SNAPSHOT_DIR,
        audit_logger: object | None = None,
    ) -> None:
        self.args = args
        self.color = supports_color()
        self.snapshot_dir = snapshot_dir
        self.audit_logger = audit_logger

    def run(self) -> int:
        """Execute the requested recovery operation.

        Returns exit code (0 = success, 1 = error, 2 = no issues found).
        """
        if self.args.list_snapshots:
            return self._cmd_list_snapshots()
        if self.args.rollback is not None:
            return self._cmd_rollback(self.args.rollback)
        if self.args.diagnose:
            return self._cmd_diagnose()

        # Default: interactive recovery mode
        return self._cmd_interactive()

    # ── Non-interactive commands ────────────────────────────────────

    def _cmd_list_snapshots(self) -> int:
        """List available snapshots and exit (AC#9)."""
        snapshots = load_snapshots(self.snapshot_dir)
        self._print(format_snapshot_table(snapshots, self.snapshot_dir))
        return 0

    def _cmd_rollback(self, snapshot_id: str) -> int:
        """Non-interactive rollback to a specific snapshot (AC#9)."""
        if not _validate_snapshot_id(snapshot_id, self.snapshot_dir):
            self._print_err(f"Invalid snapshot ID: {snapshot_id}")
            return 1

        snapshots = load_snapshots(self.snapshot_dir)
        snap = next(
            (s for s in snapshots if s.get("snapshot_id") == snapshot_id),
            None,
        )
        if snap is None:
            self._print_err(f"Snapshot not found: {snapshot_id}")
            return 1

        # Verify integrity
        snap_path = self.snapshot_dir / f"{snapshot_id}.json"
        if not verify_snapshot_integrity(snap_path):
            self._print_err(f"WARNING: Snapshot integrity check failed: {snapshot_id}")
            if not self.args.yes:
                return 1

        if not self.args.yes:
            self._print(f"Will rollback to snapshot: {snapshot_id}")
            self._print_packages_diff(snap)
            try:
                answer = input("Proceed? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._print("\nAborted.")
                return 1
            if answer not in ("y", "yes"):
                self._print("Aborted.")
                return 1

        success, message = execute_rollback(snap, progress_fn=self._print)
        self._log_audit(
            "RECOVERY_ROLLBACK", {"snapshot_id": snapshot_id}, "success" if success else "failed"
        )

        if success:
            self._print(self._green(f"Success: {message}"))
            self._print("Reboot required to complete driver rollback.")
            return 0
        else:
            self._print_err(self._red(f"Failed: {message}"))
            return 1

    def _cmd_diagnose(self) -> int:
        """Output diagnostics and exit (AC#9)."""
        from recovery_diagnostics import run_diagnostics

        results = run_diagnostics()
        if not results:
            self._print("No issues detected. System appears healthy.")
            self._log_audit("RECOVERY_DIAGNOSE", {}, "healthy")
            return 0

        for r in results:
            severity_tag = r.severity.upper()
            self._print(f"[{severity_tag}] {r.description}")
        self._log_audit(
            "RECOVERY_DIAGNOSE",
            {"issues": [r.issue_type for r in results]},
            "issues_found",
        )
        return 1

    # ── Interactive mode ────────────────────────────────────────────

    def _cmd_interactive(self) -> int:
        """Full interactive recovery flow (AC#3, #4)."""
        if not is_tty():
            self._print_err(
                "Interactive mode requires a TTY. "
                "Use --repair --diagnose or --repair --list-snapshots for non-interactive mode."
            )
            return 1

        self._print(self._bold("Verde Recovery Mode"))
        self._print("=" * 40)
        self._print()

        # Step 1: Run diagnostics
        self._print(self._bold("Running diagnostics..."))
        from recovery_diagnostics import run_diagnostics

        results = run_diagnostics()

        if not results:
            self._print(self._green("No issues detected. System appears healthy."))
            return 0

        self._print()
        self._print(self._bold(f"Found {len(results)} issue(s):"))
        self._print()
        for i, r in enumerate(results, 1):
            if r.severity == "critical":
                tag = self._red(f"[{r.severity.upper()}]")
            elif r.severity == "warning":
                tag = self._yellow(f"[{r.severity.upper()}]")
            else:
                tag = f"[{r.severity.upper()}]"
            self._print(f"  {i}. {tag} {r.description}")
        self._print()

        # Step 2: Present recovery options
        snapshots = load_snapshots(self.snapshot_dir)
        options: list[tuple[str, str]] = []

        if snapshots:
            options.append(("rollback", "Rollback to a previous driver snapshot"))
        options.append(("nouveau", "Switch to nouveau open-source driver (fallback)"))
        options.append(("exit", "Exit without taking action"))

        self._print(self._bold("Recovery Options:"))
        for i, (_key, desc) in enumerate(options, 1):
            self._print(f"  {i}. {desc}")
        self._print()

        try:
            choice = input(f"Choose an option [1-{len(options)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            self._print("\nAborted.")
            return 1

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(options):
                raise ValueError
        except ValueError:
            self._print_err("Invalid choice.")
            return 1

        action = options[idx][0]

        if action == "exit":
            self._print("Exiting without changes.")
            return 0
        elif action == "rollback":
            return self._interactive_rollback(snapshots)
        elif action == "nouveau":
            return self._interactive_nouveau()
        return 1

    def _interactive_rollback(self, snapshots: list[dict]) -> int:
        """Interactive snapshot selection and rollback."""
        self._print()
        self._print(self._bold("Available Snapshots:"))
        self._print(format_snapshot_table(snapshots, self.snapshot_dir))
        self._print()

        try:
            choice = input(f"Select snapshot [1-{len(snapshots)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            self._print("\nAborted.")
            return 1

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(snapshots):
                raise ValueError
        except ValueError:
            self._print_err("Invalid selection.")
            return 1

        snap = snapshots[idx]
        sid = snap.get("snapshot_id", "")

        # Verify integrity (AC#3)
        snap_path = self.snapshot_dir / f"{sid}.json"
        if not verify_snapshot_integrity(snap_path):
            self._print(self._yellow("WARNING: Snapshot integrity check FAILED."))
            try:
                answer = input("Proceed anyway? This is risky. [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._print("\nAborted.")
                return 1
            if answer not in ("y", "yes"):
                self._print("Aborted.")
                return 1

        # Confirmation (AC#4)
        self._print()
        self._print(self._bold("Rollback Plan:"))
        self._print_packages_diff(snap)
        self._print()

        try:
            answer = input("Proceed with rollback? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._print("\nAborted.")
            return 1
        if answer not in ("y", "yes"):
            self._print("Aborted.")
            return 1

        success, message = execute_rollback(snap, progress_fn=self._print)
        self._log_audit(
            "RECOVERY_ROLLBACK", {"snapshot_id": sid}, "success" if success else "failed"
        )

        if success:
            self._print(self._green(f"Success: {message}"))
            self._print("Reboot required to complete driver rollback.")
            return 0
        else:
            self._print_err(self._red(f"Failed: {message}"))
            self._print("Suggestion: Run 'sudo dpkg --configure -a' then retry.")
            return 1

    def _interactive_nouveau(self) -> int:
        """Interactive nouveau fallback."""
        self._print()
        self._print(self._bold("Nouveau Fallback:"))
        self._print("  - NVIDIA kernel modules will be blacklisted")
        self._print("  - The open-source nouveau driver will be used instead")
        self._print("  - A reboot will be required")
        self._print()

        try:
            answer = input("Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._print("\nAborted.")
            return 1
        if answer not in ("y", "yes"):
            self._print("Aborted.")
            return 1

        success, message = execute_nouveau_fallback()
        self._log_audit("RECOVERY_NOUVEAU_FALLBACK", {}, "success" if success else "failed")

        if success:
            self._print(self._green(message))
            return 0
        else:
            self._print_err(self._red(f"Failed: {message}"))
            return 1

    # ── Helpers ─────────────────────────────────────────────────────

    def _print_packages_diff(self, snap: dict) -> None:
        """Display what packages will be installed from the snapshot."""
        packages = snap.get("driver_packages", [])
        if packages:
            self._print("  Packages to install:")
            for p in packages:
                name = p.get("name", "")
                version = p.get("version", "")
                self._print(f"    {name}={version}" if version else f"    {name}")

    def _log_audit(self, operation: str, params: dict, result: str) -> None:
        """Log to audit log if available."""
        if self.audit_logger is not None:
            try:
                self.audit_logger.log(operation, params, "root", result)
            except Exception as exc:
                log.warning("Audit log failed: %s", exc)

    # ── Output helpers ──────────────────────────────────────────────

    def _print(self, text: str = "") -> None:
        """Print text to stdout."""
        print(text)

    def _print_err(self, text: str) -> None:
        """Print error text to stderr."""
        print(text, file=sys.stderr)

    def _bold(self, text: str) -> str:
        if self.color:
            return f"\033[1m{text}\033[0m"
        return text

    def _red(self, text: str) -> str:
        if self.color:
            return f"\033[31m{text}\033[0m"
        return text

    def _yellow(self, text: str) -> str:
        if self.color:
            return f"\033[33m{text}\033[0m"
        return text

    def _green(self, text: str) -> str:
        if self.color:
            return f"\033[32m{text}\033[0m"
        return text


# ── Entry point ─────────────────────────────────────────────────────


def recovery_main(argv: list[str] | None = None) -> int:
    """Entry point for ``verde --repair``.

    Parameters
    ----------
    argv : list[str] | None
        Argument list.  Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code.
    """
    if argv is None:
        argv = sys.argv[1:]

    args = parse_recovery_args(argv)

    if not args.repair:
        return 0

    if not check_root_privilege():
        print(
            "Recovery mode requires root. Run: sudo verde --repair",
            file=sys.stderr,
        )
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Initialize audit logger (create dir if needed)
    audit_logger = None
    try:
        VERDE_DATA_DIR.mkdir(parents=True, exist_ok=True)
        from audit import AuditLogger

        audit_logger = AuditLogger(log_dir=VERDE_DATA_DIR)
    except Exception as exc:
        log.warning("Cannot initialize audit logger: %s", exc)

    cli = RecoveryCLI(args, audit_logger=audit_logger)
    return cli.run()
