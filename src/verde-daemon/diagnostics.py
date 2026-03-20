"""Diagnostic report generation for the Verde daemon.

Collects GPU, driver, system, and log information into a structured
report available in markdown (for forum posts) or JSON (for tooling).

External dependencies (subprocess calls, file reads) are guarded so
that a failure in any single collector produces ``[unavailable]``
instead of aborting the entire report.

References: FR31, FR32, FR66; Story 5.1.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from typing import Any

from verde_daemon import __version__
from verde_daemon.nvml_wrapper import NvmlWrapper, Unavailable

log = logging.getLogger("verde-daemon.diagnostics")

_SUBPROCESS_TIMEOUT = 10
_UNAVAILABLE = "[unavailable]"
_JOURNAL_LINES = 50
_DMESG_NVIDIA_LINES = 20


class DiagnosticCollector:
    """Collects system/GPU data and formats diagnostic reports."""

    def __init__(
        self,
        nvml: NvmlWrapper | None = None,
        run: Any | None = None,
        read_file: Any | None = None,
    ) -> None:
        self._nvml = nvml
        self._run = run or self._default_run
        self._read = read_file or self._default_read_file

    # ── Default I/O helpers ──────────────────────────────────────────

    @staticmethod
    def _default_run(
        cmd: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
        kwargs.setdefault("timeout", _SUBPROCESS_TIMEOUT)
        return subprocess.run(cmd, **kwargs)

    @staticmethod
    def _default_read_file(path: str) -> str:
        try:
            with open(path) as fh:
                return fh.read()
        except OSError:
            return ""

    # ══════════════════════════════════════════════════════════════════
    # Data collectors — each returns a dict or string, never raises
    # ══════════════════════════════════════════════════════════════════

    def _collect_system_info(self) -> dict[str, str]:
        """Collect kernel, Ubuntu, session, Secure Boot, and Verde version."""
        info: dict[str, str] = {}

        # Kernel version
        try:
            info["kernel"] = os.uname().release
        except Exception:
            log.warning("Failed to read kernel version")
            info["kernel"] = _UNAVAILABLE

        # Ubuntu version
        try:
            content = self._read("/etc/os-release")
            for line in content.splitlines():
                if line.startswith("PRETTY_NAME="):
                    info["ubuntu"] = line.split("=", 1)[1].strip("'\" ")
                    break
            else:
                info["ubuntu"] = _UNAVAILABLE
        except Exception:
            log.warning("Failed to read /etc/os-release")
            info["ubuntu"] = _UNAVAILABLE

        # Session type — daemon can't read user env reliably, use loginctl
        try:
            result = self._run(["loginctl", "list-sessions", "--no-legend"])
            lines = result.stdout.strip().splitlines()
            if lines:
                session_id = lines[0].split()[0]
                type_result = self._run(
                    ["loginctl", "show-session", session_id, "-p", "Type", "--value"],
                )
                stype = type_result.stdout.strip()
                info["session"] = stype if stype else _UNAVAILABLE
            else:
                info["session"] = os.environ.get("XDG_SESSION_TYPE", _UNAVAILABLE)
        except (subprocess.TimeoutExpired, OSError, IndexError):
            info["session"] = os.environ.get("XDG_SESSION_TYPE", _UNAVAILABLE)

        # Secure Boot
        try:
            result = self._run(["mokutil", "--sb-state"])
            info["secure_boot"] = result.stdout.strip() if result.returncode == 0 else _UNAVAILABLE
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            log.warning("Failed to read Secure Boot state")
            info["secure_boot"] = _UNAVAILABLE

        # Verde version
        info["verde_version"] = __version__

        return info

    def _collect_gpu_info(self) -> dict[str, Any]:
        """Collect GPU hardware info via NvmlWrapper."""
        if self._nvml is None:
            return {"status": _UNAVAILABLE}
        try:
            data = self._nvml.get_all_gpu_info(0)
            if data.get("handle") is Unavailable:
                return {"status": _UNAVAILABLE}

            # Get VRAM total from stats (not available in gpu_info)
            vram_str = _UNAVAILABLE
            try:
                stats = self._nvml.get_all_gpu_stats(0)
                mem = stats.get("memory")
                if mem is not Unavailable and mem is not None:
                    total = mem.get("total", 0)
                    if total:
                        vram_str = f"{total // (1024 * 1024)} MiB"
            except Exception:
                pass

            return {
                "name": data.get("name", _UNAVAILABLE)
                if data.get("name") is not Unavailable
                else _UNAVAILABLE,
                "vram_total": vram_str,
                "architecture": _UNAVAILABLE,  # Not directly exposed; use compute capability
                "bus_id": self._extract_bus_id(data),
                "uuid": data.get("uuid", _UNAVAILABLE)
                if data.get("uuid") is not Unavailable
                else _UNAVAILABLE,
                "compute_capability": self._format_cc(data.get("compute_capability")),
            }
        except Exception:
            log.warning("Failed to collect GPU info from NVML")
            return {"status": _UNAVAILABLE}

    def _collect_driver_info(self) -> dict[str, str]:
        """Collect driver version, type, DKMS status, loaded modules."""
        info: dict[str, str] = {}

        # Driver version from /sys/module/nvidia/version
        try:
            content = self._read("/sys/module/nvidia/version")
            version = content.strip()
            info["version"] = version if version else _UNAVAILABLE
        except Exception:
            info["version"] = _UNAVAILABLE

        # Driver type + loaded modules: call lsmod once, reuse (P-2 + P-5)
        try:
            lsmod_result = self._run(["lsmod"])
            if lsmod_result.returncode != 0:
                info["type"] = _UNAVAILABLE
                info["loaded_modules"] = _UNAVAILABLE
            else:
                lsmod_output = lsmod_result.stdout
                mod_lines = [line for line in lsmod_output.splitlines()[1:] if line.strip()]
                has_nvidia = any(line.split()[0].startswith("nvidia") for line in mod_lines)
                has_nouveau = any(line.split()[0] == "nouveau" for line in mod_lines)
                if has_nvidia:
                    info["type"] = "proprietary"
                elif has_nouveau:
                    info["type"] = "nouveau"
                else:
                    info["type"] = "none"

                nvidia_modules = [
                    line.split()[0] for line in mod_lines if line.split()[0].startswith("nvidia")
                ]
                info["loaded_modules"] = ", ".join(nvidia_modules) if nvidia_modules else "none"
        except (subprocess.TimeoutExpired, OSError):
            info["type"] = _UNAVAILABLE
            info["loaded_modules"] = _UNAVAILABLE

        # DKMS status
        try:
            result = self._run(["dkms", "status"])
            if result.returncode == 0:
                nvidia_lines = [
                    line.strip() for line in result.stdout.splitlines() if "nvidia" in line.lower()
                ]
                info["dkms"] = (
                    "\n".join(nvidia_lines) if nvidia_lines else "No NVIDIA modules in DKMS"
                )
            else:
                info["dkms"] = _UNAVAILABLE
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            info["dkms"] = _UNAVAILABLE

        return info

    def _collect_gpu_state(self) -> dict[str, Any]:
        """Collect GPU runtime stats via NvmlWrapper."""
        if self._nvml is None:
            return {"status": _UNAVAILABLE}
        try:
            data = self._nvml.get_all_gpu_stats(0)
            if data.get("handle") is Unavailable:
                return {"status": _UNAVAILABLE}

            temp = data.get("temperature")
            util = data.get("utilization")
            mem = data.get("memory")
            power = data.get("power_usage")
            power_limit = data.get("power_limit")
            perf = data.get("performance_state")
            throttle = data.get("throttle_reasons_decoded")

            return {
                "temperature": f"{temp}°C" if temp is not Unavailable else _UNAVAILABLE,
                "utilization": f"{util.get('gpu', '?')}%"
                if util is not Unavailable
                else _UNAVAILABLE,
                "memory": self._format_memory(mem),
                "power_draw": self._format_power(power, power_limit),
                "fan_speed": _UNAVAILABLE,  # Not in get_all_gpu_stats; could add later
                "p_state": str(perf) if perf is not Unavailable else _UNAVAILABLE,
                "throttle_reasons": ", ".join(throttle)
                if throttle is not Unavailable and throttle
                else "None",
            }
        except Exception:
            log.warning("Failed to collect GPU state from NVML")
            return {"status": _UNAVAILABLE}

    def _collect_system_logs(self) -> dict[str, str]:
        """Collect recent NVIDIA-related kernel and dmesg logs."""
        logs: dict[str, str] = {}

        # journalctl
        try:
            result = self._run(
                ["journalctl", "--no-pager", "-k", "-g", "nvidia", f"--lines={_JOURNAL_LINES}"],
            )
            logs["journal"] = (
                result.stdout.strip()
                if result.returncode == 0 and result.stdout.strip()
                else _UNAVAILABLE
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            log.warning("Failed to read journal logs")
            logs["journal"] = _UNAVAILABLE

        # dmesg (filter for nvidia/NVRM)
        # Full ring buffer is loaded then filtered; this is safe because
        # the kernel ring buffer has a fixed size (typically 256-512 KiB).
        try:
            result = self._run(["dmesg"])
            if result.returncode == 0:
                nvidia_lines = [
                    line
                    for line in result.stdout.splitlines()
                    if "nvidia" in line.lower() or "NVRM" in line
                ]
                logs["dmesg"] = (
                    "\n".join(nvidia_lines[-_DMESG_NVIDIA_LINES:])
                    if nvidia_lines
                    else _UNAVAILABLE
                )
            else:
                logs["dmesg"] = _UNAVAILABLE
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            log.warning("Failed to read dmesg")
            logs["dmesg"] = _UNAVAILABLE

        return logs

    def _collect_detected_issues(self, driver: dict[str, str] | None = None) -> list[str]:
        """Aggregate detected issues into a list of human-readable strings."""
        issues: list[str] = []

        # Check driver — reuse passed info to avoid redundant lsmod calls
        if driver is None:
            driver = self._collect_driver_info()
        if driver.get("type") == "none":
            issues.append("No NVIDIA driver loaded (neither proprietary nor nouveau)")
        elif driver.get("type") == "nouveau":
            issues.append("Using open-source nouveau driver — limited performance and features")

        # Check NVML
        if self._nvml is None:
            issues.append("NVML library not available — GPU monitoring unavailable")
        else:
            try:
                data = self._nvml.get_all_gpu_info(0)
                if data.get("handle") is Unavailable:
                    issues.append(
                        "GPU device not accessible via NVML — may have fallen off the bus"
                    )
            except Exception:
                issues.append("Failed to query GPU via NVML")

            # Thermal check
            try:
                stats = self._nvml.get_all_gpu_stats(0)
                temp = stats.get("temperature")
                if temp is not Unavailable and isinstance(temp, (int, float)) and temp > 90:
                    issues.append(f"GPU temperature critically high: {temp}°C")
                throttle = stats.get("throttle_reasons_decoded")
                if throttle is not Unavailable and throttle:
                    issues.append(f"GPU throttling active: {', '.join(throttle)}")
            except Exception:
                pass

        return issues

    # ── Formatting helpers ────────────────────────────────────────────

    @staticmethod
    def _extract_bus_id(data: dict) -> str:
        pci = data.get("pci_info")
        if pci is Unavailable or pci is None:
            return _UNAVAILABLE
        bus = pci.get("busId", pci.get("bus_id", _UNAVAILABLE))
        return bus if bus is not Unavailable else _UNAVAILABLE

    @staticmethod
    def _format_cc(cc: Any) -> str:
        if cc is Unavailable or cc is None:
            return _UNAVAILABLE
        if isinstance(cc, tuple) and len(cc) == 2:
            return f"{cc[0]}.{cc[1]}"
        return str(cc)

    @staticmethod
    def _format_memory(mem: Any) -> str:
        if mem is Unavailable or mem is None:
            return _UNAVAILABLE
        total = mem.get("total", 0)
        used = mem.get("used", 0)
        if total:
            return f"{used // (1024 * 1024)} / {total // (1024 * 1024)} MiB"
        return _UNAVAILABLE

    @staticmethod
    def _format_power(power: Any, limit: Any) -> str:
        if power is Unavailable or power is None:
            return _UNAVAILABLE
        pw = power / 1000  # mW to W
        if limit is not Unavailable and limit is not None and limit > 0:
            lw = limit / 1000
            return f"{pw:.0f}W / {lw:.0f}W"
        return f"{pw:.0f}W"

    # ══════════════════════════════════════════════════════════════════
    # Report generation
    # ══════════════════════════════════════════════════════════════════

    def collect_all(self) -> dict[str, Any]:
        """Run all collectors and return the raw data dict."""
        # Collect driver info once — shared between driver section and issues
        driver_info = self._collect_driver_info()
        gpu_state = self._collect_gpu_state()
        return {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "verde_version": __version__,
            "system": self._collect_system_info(),
            "gpu": self._collect_gpu_info(),
            "driver": driver_info,
            "gpu_state": gpu_state,
            "detected_issues": self._collect_detected_issues(driver=driver_info),
            "kernel_logs": self._collect_system_logs(),
        }

    def generate(self, fmt: str = "markdown") -> str:
        """Generate a diagnostic report in the requested format.

        Parameters
        ----------
        fmt : str
            ``"markdown"`` (default) or ``"json"``.

        Returns
        -------
        str
            The formatted report.
        """
        data = self.collect_all()
        if fmt == "json":
            return self._format_json(data)
        return self._format_markdown(data)

    # ── Markdown formatter ────────────────────────────────────────────

    def _format_markdown(self, data: dict[str, Any]) -> str:
        """Assemble collected data into a markdown report."""
        lines: list[str] = []

        lines.append("# Verde Diagnostic Report")
        lines.append(f"Generated: {data['generated_at']}")
        lines.append("")

        # System Information
        sys = data["system"]
        lines.append("## System Information")
        lines.append(f"- **Kernel:** {sys.get('kernel', _UNAVAILABLE)}")
        lines.append(f"- **Ubuntu:** {sys.get('ubuntu', _UNAVAILABLE)}")
        lines.append(f"- **Session:** {sys.get('session', _UNAVAILABLE)}")
        lines.append(f"- **Secure Boot:** {sys.get('secure_boot', _UNAVAILABLE)}")
        lines.append(f"- **Verde Version:** {sys.get('verde_version', _UNAVAILABLE)}")
        nvml_available = "Yes" if self._nvml is not None else "No"
        lines.append(f"- **NVML Available:** {nvml_available}")
        lines.append("")

        # GPU Information
        gpu = data["gpu"]
        lines.append("## GPU Information")
        if gpu.get("status") == _UNAVAILABLE:
            lines.append(f"- {_UNAVAILABLE}")
        else:
            lines.append(f"- **Model:** {gpu.get('name', _UNAVAILABLE)}")
            lines.append(f"- **VRAM:** {gpu.get('vram_total', _UNAVAILABLE)}")
            lines.append(
                f"- **Compute Capability:** {gpu.get('compute_capability', _UNAVAILABLE)}"
            )
            lines.append(f"- **Bus ID:** {gpu.get('bus_id', _UNAVAILABLE)}")
        lines.append("")

        # Driver Status
        drv = data["driver"]
        lines.append("## Driver Status")
        lines.append(f"- **Version:** {drv.get('version', _UNAVAILABLE)}")
        lines.append(f"- **Type:** {drv.get('type', _UNAVAILABLE)}")
        lines.append(f"- **DKMS Status:** {drv.get('dkms', _UNAVAILABLE)}")
        lines.append(f"- **Loaded Modules:** {drv.get('loaded_modules', _UNAVAILABLE)}")
        lines.append("")

        # GPU State
        state = data["gpu_state"]
        lines.append("## GPU State")
        if state.get("status") == _UNAVAILABLE:
            lines.append(f"- {_UNAVAILABLE}")
        else:
            lines.append(f"- **Temperature:** {state.get('temperature', _UNAVAILABLE)}")
            lines.append(f"- **Utilization:** {state.get('utilization', _UNAVAILABLE)}")
            lines.append(f"- **Memory:** {state.get('memory', _UNAVAILABLE)}")
            lines.append(f"- **Power Draw:** {state.get('power_draw', _UNAVAILABLE)}")
            lines.append(f"- **Fan Speed:** {state.get('fan_speed', _UNAVAILABLE)}")
            lines.append(f"- **P-State:** {state.get('p_state', _UNAVAILABLE)}")
            lines.append(f"- **Throttle Reasons:** {state.get('throttle_reasons', _UNAVAILABLE)}")
        lines.append("")

        # Detected Issues
        issues = data["detected_issues"]
        lines.append("## Detected Issues")
        if issues:
            for issue in issues:
                lines.append(f"- {issue}")
        else:
            lines.append("- No issues detected")
        lines.append("")

        # Recent Kernel Logs
        klogs = data["kernel_logs"]
        lines.append("## Recent Kernel Logs")
        lines.append("")
        journal = klogs.get("journal", _UNAVAILABLE)
        if journal != _UNAVAILABLE:
            lines.append("### journalctl (nvidia)")
            lines.append("```")
            lines.append(journal)
            lines.append("```")
        else:
            lines.append(f"### journalctl (nvidia): {_UNAVAILABLE}")
        lines.append("")
        dmesg = klogs.get("dmesg", _UNAVAILABLE)
        if dmesg != _UNAVAILABLE:
            lines.append("### dmesg (nvidia/NVRM)")
            lines.append("```")
            lines.append(dmesg)
            lines.append("```")
        else:
            lines.append(f"### dmesg (nvidia/NVRM): {_UNAVAILABLE}")

        return "\n".join(lines)

    # ── JSON formatter ────────────────────────────────────────────────

    @staticmethod
    def _format_json(data: dict[str, Any]) -> str:
        """Format collected data as compact JSON.

        Converts ``[unavailable]`` markers to ``null``.
        """

        def _nullify(obj: Any) -> Any:
            if obj == _UNAVAILABLE:
                return None
            if isinstance(obj, dict):
                return {k: _nullify(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_nullify(item) for item in obj]
            return obj

        clean = _nullify(data)
        return json.dumps(clean, separators=(",", ":"))
