"""Driver discovery, apt/repo detection, and .run file detection.

Wraps ``ubuntu-drivers``, ``dpkg-query``, ``apt-cache``, ``apt-mark``,
``dkms``, and ``lsmod`` via subprocess with list-form arguments.

Architecture: AR-4 (D-Bus API), AR-7 (daemon-only), NFR-SEC-3, NFR-SEC-6.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("verde-daemon.driver_manager")

# ---------------------------------------------------------------------------
# Regex patterns for parsing command output
# ---------------------------------------------------------------------------

# ubuntu-drivers list output: one package per line
# e.g., "nvidia-driver-550, (kernel modules provided by nvidia-dkms-550)"
UBUNTU_DRIVERS_LINE = re.compile(
    r"^(nvidia-driver-(\d{3,4})(-server)?)"
    r"(?:,\s*\(kernel modules provided by ([\w\-]+)\))?"
)

# dpkg-query output: tab-separated
# Package\tVersion\tStatus-Abbrev
DPKG_LINE = re.compile(r"^([\w\-]+)\t([^\t]*)\t(\w+)")

# /proc/driver/nvidia/version first line
PROC_NVIDIA_VERSION = re.compile(r"Kernel Module\s+(\d{3,4}\.\d+)")

# apt-mark showhold output: one package per line
HOLD_PACKAGE = re.compile(r"^(nvidia-\S+)")

# dkms status output
# e.g., "nvidia/550.120, 6.5.0-44-generic, x86_64: installed"
DKMS_STATUS = re.compile(r"^nvidia/(\d+\.\d+),\s*([\d.\-\w]+),\s*\w+:\s*(\w+)")

# ---------------------------------------------------------------------------
# CUDA version mapping
# ---------------------------------------------------------------------------

DRIVER_CUDA_MAP: list[tuple[int, str]] = [
    (535, "12.2"),
    (545, "12.3"),
    (550, "12.4"),
    (555, "12.5"),
    (560, "12.6"),
    (565, "12.7"),
]


def cuda_version_for_driver(driver_version: int) -> str:
    """Return maximum CUDA version supported by this driver major version."""
    result = "unknown"
    for min_driver, cuda in DRIVER_CUDA_MAP:
        if driver_version >= min_driver:
            result = cuda
    return result


# ---------------------------------------------------------------------------
# DriverManager
# ---------------------------------------------------------------------------


class DriverManager:
    """Driver discovery and enumeration for the Verde daemon.

    All subprocess calls use list-form arguments with explicit timeouts.
    No shell invocation (NFR-SEC-3). Methods return structured dicts; exceptions are caught
    internally and logged.
    """

    def __init__(self, gpu_name: str = "", tracker: Any = None) -> None:
        self._gpu_name = gpu_name
        self._tracker = tracker

    # -- subprocess helper --------------------------------------------------

    @staticmethod
    def _run_cmd(
        cmd: list[str],
        timeout: int = 30,
        *,
        quiet: bool = False,
    ) -> subprocess.CompletedProcess | None:
        """Run a subprocess command with error handling.

        Returns None on failure.  When *quiet* is ``True``, non-zero exit
        codes are logged at DEBUG instead of WARNING (useful for commands
        where failure is expected, like dpkg-query with no matching packages).
        """
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                level = logging.DEBUG if quiet else logging.WARNING
                log.log(
                    level,
                    "Command %s failed (rc=%d): %s",
                    cmd[0],
                    result.returncode,
                    result.stderr.strip(),
                )
                return None
            return result
        except subprocess.TimeoutExpired:
            log.warning("Command %s timed out after %ds", cmd[0], timeout)
            return None
        except FileNotFoundError:
            log.warning("Command %s not found", cmd[0])
            return None
        except OSError as exc:
            log.warning("Command %s failed: %s", cmd[0], exc)
            return None

    # -- Task 1: ubuntu-drivers parsing ------------------------------------

    def _run_ubuntu_drivers_list(self) -> str:
        """Run ``ubuntu-drivers list`` and ``ubuntu-drivers list --gpgpu``.

        Returns combined stdout, or empty string on failure.
        """
        lines: list[str] = []
        for cmd in (
            ["ubuntu-drivers", "list"],
            ["ubuntu-drivers", "list", "--gpgpu"],
        ):
            result = self._run_cmd(cmd, timeout=90)
            if result is not None:
                lines.append(result.stdout)
        return "\n".join(lines)

    @staticmethod
    def _parse_ubuntu_drivers_output(stdout: str) -> list[dict]:
        """Parse ubuntu-drivers list output into structured entries."""
        seen: set[str] = set()
        entries: list[dict] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            m = UBUNTU_DRIVERS_LINE.match(line)
            if not m:
                continue
            package_name = m.group(1)
            if package_name in seen:
                continue
            seen.add(package_name)
            version = m.group(2)
            is_server = m.group(3) is not None
            module_pkg = m.group(4) or ""
            entries.append(
                {
                    "package_name": package_name,
                    "version": version,
                    "variant": "server" if is_server else "desktop",
                    "module_package": module_pkg,
                }
            )
        return entries

    def _get_recommended_driver(self) -> str | None:
        """Run ``ubuntu-drivers list --recommended`` to find the recommendation."""
        result = self._run_cmd(["ubuntu-drivers", "list", "--recommended"], timeout=90)
        if result is None:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            m = UBUNTU_DRIVERS_LINE.match(line)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _detect_driver_variant(package_name: str) -> str:
        """Classify as ``"desktop"`` or ``"server"``."""
        if "-server" in package_name:
            return "server"
        return "desktop"

    # -- Task 2: apt repository detection ----------------------------------

    def _check_apt_repositories(self) -> list[str]:
        """Check if required apt repos are enabled.

        Returns list of missing repository descriptions with guidance.
        """
        missing: list[str] = []

        # Check apt-cache policy for restricted component
        result = self._run_cmd(["apt-cache", "policy"], timeout=10)
        has_restricted = False
        if result is not None:
            for line in result.stdout.splitlines():
                if "restricted" in line.lower():
                    has_restricted = True
                    break

        if not has_restricted:
            # Fallback: check sources files
            has_restricted = self._check_sources_for_restricted()

        if not has_restricted:
            missing.append(
                "Ubuntu 'restricted' repository is not enabled. "
                "Enable it with: sudo add-apt-repository restricted"
            )

        return missing

    @staticmethod
    def _check_sources_for_restricted() -> bool:
        """Check /etc/apt/sources.list and sources.list.d/ for restricted.

        Parses only active (non-commented) lines and checks for
        ``restricted`` as a component in deb/deb-src lines.
        """
        sources_files: list[Path] = []
        main_sources = Path("/etc/apt/sources.list")
        if main_sources.exists():
            sources_files.append(main_sources)
        sources_dir = Path("/etc/apt/sources.list.d")
        if sources_dir.is_dir():
            sources_files.extend(sources_dir.glob("*.list"))
            sources_files.extend(sources_dir.glob("*.sources"))

        for src in sources_files:
            try:
                for line in src.read_text().splitlines():
                    stripped = line.strip()
                    # Skip comments and empty lines
                    if not stripped or stripped.startswith("#"):
                        continue
                    # DEB822 format (.sources): look for Components: field
                    if stripped.lower().startswith("components:"):
                        components = stripped.split(":", 1)[1].split()
                        if "restricted" in components:
                            return True
                    # Traditional format: deb [opts] uri suite comp1 comp2 ...
                    if stripped.startswith(("deb ", "deb-src ")):
                        parts = stripped.split()
                        # Skip the optional [options] block
                        idx = 1
                        if len(parts) > 1 and parts[1].startswith("["):
                            while idx < len(parts) and not parts[idx].endswith("]"):
                                idx += 1
                            idx += 1
                        # Components start after uri + suite (idx+2)
                        components = parts[idx + 2 :] if len(parts) > idx + 2 else []
                        if "restricted" in components:
                            return True
            except OSError:
                continue
        return False

    # -- Task 3: driver package enumeration --------------------------------

    def _enumerate_nvidia_packages(self) -> list[dict]:
        """Query dpkg for installed NVIDIA driver/DKMS/prebuilt packages.

        Queries each pattern separately because ``dpkg-query`` exits with
        code 1 when *any* pattern has zero matches, which would discard
        valid results from the other patterns.
        """
        patterns = ["nvidia-driver-*", "nvidia-dkms-*", "linux-modules-nvidia-*"]
        packages: list[dict] = []
        for pattern in patterns:
            result = self._run_cmd(
                [
                    "dpkg-query",
                    "-W",
                    "-f",
                    "${Package}\t${Version}\t${db:Status-Abbrev}\n",
                    pattern,
                ],
                timeout=10,
                quiet=True,
            )
            if result is None:
                continue
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = DPKG_LINE.match(line)
                if m:
                    packages.append(
                        {
                            "package_name": m.group(1),
                            "version": m.group(2),
                            "status": m.group(3),
                        }
                    )
        return packages

    def _get_available_packages(self) -> list[dict]:
        """Search apt-cache for available nvidia-driver packages."""
        result = self._run_cmd(
            ["apt-cache", "search", "^nvidia-driver-[0-9]+"],
            timeout=10,
        )
        if result is None:
            return []

        packages: list[dict] = []
        seen: set[str] = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: "nvidia-driver-550 - NVIDIA driver metapackage"
            parts = line.split(" - ", 1)
            pkg_name = parts[0].strip()
            if pkg_name in seen:
                continue
            seen.add(pkg_name)
            description = parts[1].strip() if len(parts) > 1 else ""
            packages.append(
                {
                    "package_name": pkg_name,
                    "description": description,
                }
            )
        return packages

    def _detect_package_holds(self) -> set[str]:
        """Return set of held NVIDIA package names."""
        result = self._run_cmd(["apt-mark", "showhold"], timeout=10)
        if result is None:
            return set()
        holds: set[str] = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            m = HOLD_PACKAGE.match(line)
            if m:
                holds.add(m.group(1))
        return holds

    @staticmethod
    def _classify_module_type(package_name: str) -> str:
        """Return ``"dkms"``, ``"prebuilt"``, or ``"unknown"``."""
        if "nvidia-dkms-" in package_name:
            return "dkms"
        if "linux-modules-nvidia-" in package_name:
            return "prebuilt"
        return "unknown"

    def _check_module_build_status(self, package_name: str) -> str:
        """For DKMS packages, check if module is built for current kernel."""
        if "dkms" not in package_name:
            if "linux-modules-nvidia" in package_name:
                return "prebuilt_available"
            return "unknown"

        result = self._run_cmd(["dkms", "status"], timeout=10)
        if result is None:
            return "unknown"

        for line in result.stdout.splitlines():
            m = DKMS_STATUS.match(line.strip())
            if m and m.group(3) == "installed":
                return "built"
        return "not_built"

    # -- Task 4: .run file detection ---------------------------------------

    def _detect_run_file_install(self) -> dict:
        """Detect if NVIDIA was installed via a .run file.

        Returns ``{"detected": bool, "message": str}``.
        """
        # Check 1: nvidia-uninstall binary
        if Path("/usr/bin/nvidia-uninstall").exists():
            return {
                "detected": True,
                "message": (
                    "NVIDIA driver was installed using a .run file "
                    "(nvidia-uninstall found). Verde cannot manage this "
                    "installation. Please uninstall it first with: "
                    "sudo /usr/bin/nvidia-uninstall"
                ),
            }

        # Check 2: .run marker files in /var/lib/nvidia/
        nvidia_lib = Path("/var/lib/nvidia/")
        if nvidia_lib.is_dir():
            try:
                markers = list(nvidia_lib.glob("*.manifest")) + list(nvidia_lib.glob("*.run"))
                if markers:
                    return {
                        "detected": True,
                        "message": (
                            "NVIDIA .run file installation markers found in "
                            "/var/lib/nvidia/. Verde cannot manage this "
                            "installation."
                        ),
                    }
            except OSError:
                pass

        # Check 3: kernel module loaded but no dpkg package
        if self._nvidia_module_without_package():
            return {
                "detected": True,
                "message": (
                    "NVIDIA kernel module is loaded but no matching "
                    "apt package found. This suggests a .run file "
                    "installation."
                ),
            }

        return {"detected": False, "message": ""}

    def _nvidia_module_without_package(self) -> bool:
        """Check if nvidia module is loaded but no dpkg package exists."""
        lsmod = self._run_cmd(["lsmod"], timeout=5)
        if lsmod is None:
            return False

        nvidia_loaded = any(
            line.split()[0] == "nvidia" for line in lsmod.stdout.splitlines() if line.strip()
        )
        if not nvidia_loaded:
            return False

        # Check if any nvidia-driver-* is installed
        dpkg = self._run_cmd(
            [
                "dpkg-query",
                "-W",
                "-f",
                "${Package}\t${db:Status-Abbrev}\n",
                "nvidia-driver-*",
            ],
            timeout=10,
        )
        if dpkg is None:
            return True  # Can't query dpkg but nvidia is loaded

        for line in dpkg.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 2 and parts[1].startswith("ii"):
                return False  # Found installed package
        return True

    # -- Task 5: recommendation context ------------------------------------

    def _build_recommendation_context(self, version: str, gpu_name: str) -> dict:
        """Build recommendation context for a driver version."""
        try:
            ver_int = int(version)
        except (ValueError, TypeError):
            ver_int = 0

        cuda = cuda_version_for_driver(ver_int)

        reason_parts = ["Recommended by ubuntu-drivers"]
        if gpu_name:
            reason_parts[0] += f" for your {gpu_name}"
        reason_parts[0] += "."
        if cuda != "unknown":
            reason_parts.append(f"Supports CUDA {cuda}.")

        # Known issues — curated dict, initially empty
        known_issues = _KNOWN_DRIVER_ISSUES.get(version, "")

        return {
            "recommendation_reason": " ".join(reason_parts),
            "cuda_compatibility": cuda,
            "known_issues": known_issues,
        }

    # -- Task 6: get_current_driver ----------------------------------------

    def _get_current_driver(self) -> dict:
        """Detect the currently active NVIDIA driver."""
        result: dict = {
            "version": "",
            "driver_type": "none",
            "package_name": "",
            "variant": "",
            "module_type": "",
            "loaded": False,
        }

        # Check /proc/driver/nvidia/version
        proc_path = Path("/proc/driver/nvidia/version")
        try:
            if proc_path.exists():
                content = proc_path.read_text()
                m = PROC_NVIDIA_VERSION.search(content)
                if m:
                    result["version"] = m.group(1)
                    result["driver_type"] = "proprietary"
                    result["loaded"] = True
        except OSError:
            pass

        # Check lsmod for nvidia or nouveau — scan all lines,
        # prefer nvidia over nouveau (don't overwrite proprietary).
        lsmod = self._run_cmd(["lsmod"], timeout=5)
        if lsmod is not None:
            found_nouveau = False
            for line in lsmod.stdout.splitlines():
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "nvidia":
                    result["driver_type"] = "proprietary"
                    result["loaded"] = True
                    break
                if parts[0] == "nouveau":
                    found_nouveau = True
            else:
                # Loop completed without finding nvidia — use nouveau if found
                if found_nouveau and result["driver_type"] != "proprietary":
                    result["driver_type"] = "nouveau"
                    result["loaded"] = True

        # Find matching dpkg package and determine module type.
        # When the module is loaded we already have a version from /proc;
        # when it is NOT loaded we fall back to _enumerate_nvidia_packages.
        if result["version"]:
            major = result["version"].split(".")[0]
            dpkg = self._run_cmd(
                [
                    "dpkg-query",
                    "-W",
                    "-f",
                    "${Package}\t${Version}\t${db:Status-Abbrev}\n",
                    f"nvidia-driver-{major}*",
                    f"nvidia-dkms-{major}*",
                    f"linux-modules-nvidia-{major}*",
                ],
                timeout=10,
            )
            if dpkg is not None:
                module_type = "unknown"
                for line in dpkg.stdout.splitlines():
                    m = DPKG_LINE.match(line.strip())
                    if not m or not m.group(3).startswith("ii"):
                        continue
                    pkg = m.group(1)
                    # Set package_name/variant from the metapackage
                    if pkg.startswith("nvidia-driver-") and not result["package_name"]:
                        result["package_name"] = pkg
                        result["variant"] = self._detect_driver_variant(pkg)
                    # Determine module type from dkms/prebuilt packages
                    mt = self._classify_module_type(pkg)
                    if mt != "unknown":
                        module_type = mt
                result["module_type"] = module_type
        else:
            # Fallback: driver package installed but kernel module not loaded.
            # Enumerate dpkg packages to find an installed nvidia-driver-*.
            pkgs = self._enumerate_nvidia_packages()
            for pkg in pkgs:
                if pkg["status"].startswith("ii") and pkg["package_name"].startswith(
                    "nvidia-driver-"
                ):
                    ver_match = re.search(r"nvidia-driver-(\d+)", pkg["package_name"])
                    if ver_match:
                        result["version"] = ver_match.group(1)
                        result["package_name"] = pkg["package_name"]
                        result["variant"] = self._detect_driver_variant(pkg["package_name"])
                        result["driver_type"] = "package"
                        result["loaded"] = False
                        # Determine module_type from associated packages
                        for p in pkgs:
                            if p["status"].startswith("ii"):
                                mt = self._classify_module_type(p["package_name"])
                                if mt != "unknown":
                                    result["module_type"] = mt
                                    break
                        break

        return result

    # -- Public API --------------------------------------------------------

    def list_available_drivers(self) -> dict:
        """List all available NVIDIA drivers with metadata.

        Returns a dict with ``"drivers"`` (list), ``"missing_repositories"``
        (list), ``"run_file_detected"`` (bool), and ``"run_file_message"``
        (str).
        """
        # Get ubuntu-drivers listing
        raw_output = self._run_ubuntu_drivers_list()
        entries = self._parse_ubuntu_drivers_output(raw_output)

        # Get recommended driver
        recommended_pkg = self._get_recommended_driver()

        # Get installed packages for installed/module_type enrichment
        installed_pkgs = self._enumerate_nvidia_packages()
        installed_map: dict[str, dict] = {}
        for pkg in installed_pkgs:
            installed_map[pkg["package_name"]] = pkg

        # Get package holds
        holds = self._detect_package_holds()

        # Build driver list
        drivers: list[dict] = []
        for entry in entries:
            pkg_name = entry["package_name"]
            version = entry["version"]
            is_installed = pkg_name in installed_map

            # Module type detection
            module_pkg = entry.get("module_package", "")
            module_type = self._classify_module_type(module_pkg)
            if module_type == "unknown":
                module_type = self._classify_module_type(pkg_name)
            module_status = self._check_module_build_status(module_pkg)

            is_recommended = pkg_name == recommended_pkg
            is_held = pkg_name in holds

            driver: dict = {
                "version": version,
                "variant": entry["variant"],
                "package_name": pkg_name,
                "installed": is_installed,
                "recommended": is_recommended,
                "held": is_held,
                "module_type": module_type,
                "module_status": module_status,
                "repository": "ubuntu",
            }

            if is_held:
                driver["hold_message"] = (
                    f"Package {pkg_name} is held. Automatic upgrades are "
                    "prevented. Use 'sudo apt-mark unhold "
                    f"{pkg_name}' to release."
                )

            if is_recommended:
                ctx = self._build_recommendation_context(version, self._gpu_name)
                driver.update(ctx)

            drivers.append(driver)

        # Also include available packages not in ubuntu-drivers output
        available = self._get_available_packages()
        known_pkgs = {d["package_name"] for d in drivers}
        for apkg in available:
            if apkg["package_name"] not in known_pkgs:
                pkg_name = apkg["package_name"]
                # Extract version from package name
                ver_match = re.search(r"(\d{3,4})", pkg_name)
                version = ver_match.group(1) if ver_match else ""
                drivers.append(
                    {
                        "version": version,
                        "variant": self._detect_driver_variant(pkg_name),
                        "package_name": pkg_name,
                        "installed": pkg_name in installed_map,
                        "recommended": False,
                        "held": pkg_name in holds,
                        "module_type": "unknown",
                        "module_status": "unknown",
                        "repository": "ubuntu",
                    }
                )

        # Ensure the currently installed driver always appears in the list,
        # even when ubuntu-drivers and apt-cache don't list it (legacy GPU).
        known_pkgs = {d["package_name"] for d in drivers}
        if not any(d.get("installed") for d in drivers):
            for pkg in installed_pkgs:
                if (
                    pkg["status"].startswith("ii")
                    and pkg["package_name"].startswith("nvidia-driver-")
                    and pkg["package_name"] not in known_pkgs
                ):
                    pkg_name = pkg["package_name"]
                    ver_match = re.search(r"(\d{3,4})", pkg_name)
                    version = ver_match.group(1) if ver_match else ""
                    drivers.append(
                        {
                            "version": version,
                            "variant": self._detect_driver_variant(pkg_name),
                            "package_name": pkg_name,
                            "installed": True,
                            "recommended": False,
                            "held": pkg_name in holds,
                            "module_type": self._classify_module_type(pkg_name),
                            "module_status": "not_loaded",
                            "repository": "ubuntu",
                        }
                    )

        # Repository check
        missing_repos = self._check_apt_repositories()

        # .run file detection
        run_info = self._detect_run_file_install()

        return {
            "drivers": drivers,
            "missing_repositories": missing_repos,
            "run_file_detected": run_info["detected"],
            "run_file_message": run_info["message"],
        }

    def get_current_driver(self) -> dict:
        """Get the currently active NVIDIA driver info."""
        return self._get_current_driver()


# ---------------------------------------------------------------------------
# Known driver issues (curated, initially empty)
# ---------------------------------------------------------------------------

_KNOWN_DRIVER_ISSUES: dict[str, str] = {}
