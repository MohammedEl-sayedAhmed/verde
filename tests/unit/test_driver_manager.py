"""Unit tests for Story 2.1 — driver discovery and repository detection.

Tests: ubuntu-drivers parsing, driver enumeration, package holds, module
classification, .run file detection, apt repository detection, recommendation
context, current driver detection, and D-Bus integration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from driver_manager import (
    DriverManager,
    cuda_version_for_driver,
)

# ===================================================================
# Helpers
# ===================================================================


def _completed(stdout: str = "", stderr: str = "", rc: int = 0):
    """Shortcut to build a subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture
def dm():
    """DriverManager with GPU name for testing."""
    return DriverManager(gpu_name="GeForce RTX 4090")


# ===================================================================
# Task 9: Driver enumeration and parsing
# ===================================================================


class TestParseUbuntuDriversOutput:
    def test_multiple_drivers(self):
        stdout = (
            "nvidia-driver-550, (kernel modules provided by nvidia-dkms-550)\n"
            "nvidia-driver-550-server, (kernel modules provided by "
            "linux-modules-nvidia-550-server-generic)\n"
            "nvidia-driver-560, (kernel modules provided by nvidia-dkms-560)\n"
        )
        result = DriverManager._parse_ubuntu_drivers_output(stdout)
        assert len(result) == 3
        assert result[0]["package_name"] == "nvidia-driver-550"
        assert result[0]["version"] == "550"
        assert result[0]["variant"] == "desktop"
        assert result[0]["module_package"] == "nvidia-dkms-550"
        assert result[1]["variant"] == "server"
        assert result[2]["version"] == "560"

    def test_empty_output(self):
        result = DriverManager._parse_ubuntu_drivers_output("")
        assert result == []

    def test_non_matching_lines_skipped(self):
        stdout = (
            "some random line\n"
            "nvidia-driver-550, (kernel modules provided by nvidia-dkms-550)\n"
            "another irrelevant line\n"
        )
        result = DriverManager._parse_ubuntu_drivers_output(stdout)
        assert len(result) == 1
        assert result[0]["package_name"] == "nvidia-driver-550"

    def test_deduplicates_packages(self):
        stdout = (
            "nvidia-driver-550, (kernel modules provided by nvidia-dkms-550)\n"
            "nvidia-driver-550, (kernel modules provided by nvidia-dkms-550)\n"
        )
        result = DriverManager._parse_ubuntu_drivers_output(stdout)
        assert len(result) == 1


class TestDetectDriverVariant:
    def test_desktop(self):
        assert DriverManager._detect_driver_variant("nvidia-driver-550") == "desktop"

    def test_server(self):
        assert DriverManager._detect_driver_variant("nvidia-driver-550-server") == "server"


class TestClassifyModuleType:
    def test_dkms(self):
        assert DriverManager._classify_module_type("nvidia-dkms-550") == "dkms"

    def test_prebuilt(self):
        assert (
            DriverManager._classify_module_type("linux-modules-nvidia-550-generic") == "prebuilt"
        )

    def test_unknown(self):
        assert DriverManager._classify_module_type("nvidia-driver-550") == "unknown"


class TestDetectPackageHolds:
    def test_held_packages(self, dm):
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.return_value = _completed("nvidia-driver-550\nnvidia-dkms-550\n")
            holds = dm._detect_package_holds()
        assert "nvidia-driver-550" in holds
        assert "nvidia-dkms-550" in holds

    def test_no_holds(self, dm):
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.return_value = _completed("")
            holds = dm._detect_package_holds()
        assert len(holds) == 0

    def test_non_nvidia_packages_ignored(self, dm):
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.return_value = _completed("libc6\nnvidia-driver-550\nsome-other-pkg\n")
            holds = dm._detect_package_holds()
        assert "nvidia-driver-550" in holds
        assert len(holds) == 1


class TestCheckModuleBuildStatus:
    def test_dkms_built(self, dm):
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.return_value = _completed(
                "nvidia/550.120, 6.5.0-44-generic, x86_64: installed\n"
            )
            status = dm._check_module_build_status("nvidia-dkms-550")
        assert status == "built"

    def test_dkms_not_built(self, dm):
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.return_value = _completed("")
            status = dm._check_module_build_status("nvidia-dkms-550")
        assert status == "not_built"

    def test_prebuilt_package(self, dm):
        status = dm._check_module_build_status("linux-modules-nvidia-550-generic")
        assert status == "prebuilt_available"

    def test_dkms_cmd_fails(self, dm):
        with patch.object(dm, "_run_cmd", return_value=None):
            status = dm._check_module_build_status("nvidia-dkms-550")
        assert status == "unknown"


class TestGetAvailablePackages:
    def test_parses_apt_cache_output(self, dm):
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.return_value = _completed(
                "nvidia-driver-550 - NVIDIA driver metapackage\n"
                "nvidia-driver-560 - NVIDIA driver metapackage\n"
            )
            pkgs = dm._get_available_packages()
        assert len(pkgs) == 2
        assert pkgs[0]["package_name"] == "nvidia-driver-550"
        assert pkgs[1]["package_name"] == "nvidia-driver-560"

    def test_empty_result(self, dm):
        with patch.object(dm, "_run_cmd", return_value=None):
            pkgs = dm._get_available_packages()
        assert pkgs == []


class TestEnumerateNvidiaPackages:
    def test_parses_dpkg_output(self, dm):
        """Each pattern is queried separately; only the first pattern matches."""
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.side_effect = [
                _completed("nvidia-driver-550\t550.120-0ubuntu1\tii\n"),
                None,  # nvidia-dkms-* — no matches
                None,  # linux-modules-nvidia-* — no matches
            ]
            pkgs = dm._enumerate_nvidia_packages()
        assert len(pkgs) == 1
        assert pkgs[0]["package_name"] == "nvidia-driver-550"
        assert pkgs[0]["version"] == "550.120-0ubuntu1"
        assert pkgs[0]["status"] == "ii"

    def test_multiple_patterns_match(self, dm):
        """All three patterns return results."""
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.side_effect = [
                _completed("nvidia-driver-535\t535.288-0ubuntu1\tii\n"),
                _completed("nvidia-dkms-535\t535.288-0ubuntu1\tii\n"),
                None,
            ]
            pkgs = dm._enumerate_nvidia_packages()
        assert len(pkgs) == 2
        names = {p["package_name"] for p in pkgs}
        assert "nvidia-driver-535" in names
        assert "nvidia-dkms-535" in names

    def test_dpkg_fails(self, dm):
        with patch.object(dm, "_run_cmd", return_value=None):
            pkgs = dm._enumerate_nvidia_packages()
        assert pkgs == []


# ===================================================================
# Task 10: Detection and context
# ===================================================================


class TestDetectRunFileInstall:
    def test_nvidia_uninstall_exists(self, dm):
        with patch("driver_manager.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            MockPath.return_value = mock_path
            # First Path() call is /usr/bin/nvidia-uninstall
            result = dm._detect_run_file_install()
        assert result["detected"] is True
        assert "nvidia-uninstall" in result["message"]

    def test_no_run_indicators(self, dm):
        with (
            patch("driver_manager.Path") as MockPath,
            patch.object(dm, "_nvidia_module_without_package", return_value=False),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            mock_path.is_dir.return_value = False
            MockPath.return_value = mock_path
            result = dm._detect_run_file_install()
        assert result["detected"] is False

    def test_module_without_package(self, dm):
        with (
            patch("driver_manager.Path") as MockPath,
            patch.object(dm, "_nvidia_module_without_package", return_value=True),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            mock_path.is_dir.return_value = False
            MockPath.return_value = mock_path
            result = dm._detect_run_file_install()
        assert result["detected"] is True
        assert "kernel module" in result["message"].lower()


class TestCheckAptRepositories:
    def test_restricted_enabled(self, dm):
        with patch.object(dm, "_run_cmd") as mock_run:
            mock_run.return_value = _completed(
                "500 http://archive.ubuntu.com/ubuntu noble/restricted amd64 Packages\n"
            )
            missing = dm._check_apt_repositories()
        assert missing == []

    def test_restricted_missing(self, dm):
        with (
            patch.object(dm, "_run_cmd") as mock_run,
            patch.object(dm, "_check_sources_for_restricted", return_value=False),
        ):
            mock_run.return_value = _completed(
                "500 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages\n"
            )
            missing = dm._check_apt_repositories()
        assert len(missing) == 1
        assert "restricted" in missing[0].lower()

    def test_restricted_in_sources_files(self, dm):
        with (
            patch.object(dm, "_run_cmd") as mock_run,
            patch.object(dm, "_check_sources_for_restricted", return_value=True),
        ):
            # apt-cache policy doesn't show restricted but sources files have it
            mock_run.return_value = _completed(
                "500 http://archive.ubuntu.com/ubuntu noble/main amd64\n"
            )
            missing = dm._check_apt_repositories()
        assert missing == []

    def test_apt_cache_policy_fails(self, dm):
        with (
            patch.object(dm, "_run_cmd", return_value=None),
            patch.object(dm, "_check_sources_for_restricted", return_value=False),
        ):
            missing = dm._check_apt_repositories()
        assert len(missing) == 1


class TestCheckSourcesForRestricted:
    def test_ignores_commented_restricted(self):
        with patch("driver_manager.Path") as MockPath:
            main_sources = MagicMock()
            main_sources.exists.return_value = True
            main_sources.read_text.return_value = (
                "# deb http://archive.ubuntu.com/ubuntu noble main restricted\n"
            )
            sources_dir = MagicMock()
            sources_dir.is_dir.return_value = False
            MockPath.side_effect = lambda p: (
                main_sources if Path(p).name == "sources.list" else sources_dir
            )
            from driver_manager import DriverManager

            result = DriverManager._check_sources_for_restricted()
        assert result is False

    def test_active_deb_line_detected(self):
        with patch("driver_manager.Path") as MockPath:
            main_sources = MagicMock()
            main_sources.exists.return_value = True
            main_sources.read_text.return_value = (
                "deb http://archive.ubuntu.com/ubuntu noble main restricted universe\n"
            )
            sources_dir = MagicMock()
            sources_dir.is_dir.return_value = False
            MockPath.side_effect = lambda p: (
                main_sources if Path(p).name == "sources.list" else sources_dir
            )
            from driver_manager import DriverManager

            result = DriverManager._check_sources_for_restricted()
        assert result is True

    def test_deb822_components_field(self):
        with patch("driver_manager.Path") as MockPath:
            main_sources = MagicMock()
            main_sources.exists.return_value = False
            sources_dir = MagicMock()
            sources_dir.is_dir.return_value = True
            deb822 = MagicMock()
            deb822.read_text.return_value = (
                "Types: deb\n"
                "URIs: http://archive.ubuntu.com/ubuntu\n"
                "Suites: noble\n"
                "Components: main restricted universe\n"
            )
            sources_dir.glob.side_effect = lambda pat: [deb822] if ".sources" in pat else []
            MockPath.side_effect = lambda p: (
                main_sources if Path(p).name == "sources.list" else sources_dir
            )
            from driver_manager import DriverManager

            result = DriverManager._check_sources_for_restricted()
        assert result is True


class TestBuildRecommendationContext:
    def test_cuda_version_mapping(self, dm):
        ctx = dm._build_recommendation_context("550", "GeForce RTX 4090")
        assert ctx["cuda_compatibility"] == "12.4"
        assert "GeForce RTX 4090" in ctx["recommendation_reason"]
        assert "CUDA 12.4" in ctx["recommendation_reason"]

    def test_unknown_driver_version(self, dm):
        ctx = dm._build_recommendation_context("400", "")
        assert ctx["cuda_compatibility"] == "unknown"

    def test_no_gpu_name(self):
        dm_no_gpu = DriverManager(gpu_name="")
        ctx = dm_no_gpu._build_recommendation_context("560", "")
        assert ctx["cuda_compatibility"] == "12.6"
        assert "Recommended by ubuntu-drivers." in ctx["recommendation_reason"]

    def test_invalid_version_string(self, dm):
        ctx = dm._build_recommendation_context("abc", "")
        assert ctx["cuda_compatibility"] == "unknown"


class TestCudaVersionForDriver:
    def test_known_versions(self):
        assert cuda_version_for_driver(535) == "12.2"
        assert cuda_version_for_driver(550) == "12.4"
        assert cuda_version_for_driver(560) == "12.6"
        assert cuda_version_for_driver(565) == "12.7"

    def test_between_versions(self):
        # 548 is >= 545 but < 550
        assert cuda_version_for_driver(548) == "12.3"

    def test_below_minimum(self):
        assert cuda_version_for_driver(400) == "unknown"

    def test_above_maximum(self):
        assert cuda_version_for_driver(600) == "12.7"


class TestGetCurrentDriver:
    def test_proprietary_loaded(self, dm):
        with (
            patch("driver_manager.Path") as MockPath,
            patch.object(dm, "_run_cmd") as mock_run,
        ):
            # /proc/driver/nvidia/version exists
            proc_mock = MagicMock()
            proc_mock.exists.return_value = True
            proc_mock.read_text.return_value = (
                "NVRM version: NVIDIA UNIX x86_64 Kernel Module  550.120  "
                "Wed Apr 17 00:28:07 UTC 2024\n"
            )
            MockPath.return_value = proc_mock

            # lsmod shows nvidia
            mock_run.side_effect = [
                _completed("Module\nnvidia  12345  0\n"),
                # dpkg query
                _completed("nvidia-driver-550\t550.120-0ubuntu1\tii\n"),
            ]
            result = dm._get_current_driver()
        assert result["driver_type"] == "proprietary"
        assert result["version"] == "550.120"
        assert result["loaded"] is True
        assert result["package_name"] == "nvidia-driver-550"

    def test_nouveau_loaded(self, dm):
        with (
            patch("driver_manager.Path") as MockPath,
            patch.object(dm, "_run_cmd") as mock_run,
        ):
            proc_mock = MagicMock()
            proc_mock.exists.return_value = False
            MockPath.return_value = proc_mock

            mock_run.return_value = _completed("Module\nnouveau  12345  0\n")
            result = dm._get_current_driver()
        assert result["driver_type"] == "nouveau"
        assert result["loaded"] is True

    def test_nouveau_before_nvidia_in_lsmod(self, dm):
        """When both nouveau and nvidia appear in lsmod, nvidia wins."""
        with (
            patch("driver_manager.Path") as MockPath,
            patch.object(dm, "_run_cmd") as mock_run,
        ):
            proc_mock = MagicMock()
            proc_mock.exists.return_value = True
            proc_mock.read_text.return_value = (
                "NVRM version: NVIDIA UNIX x86_64 Kernel Module  550.120  "
                "Wed Apr 17 00:28:07 UTC 2024\n"
            )
            MockPath.return_value = proc_mock

            # lsmod has nouveau before nvidia (alphabetical)
            mock_run.side_effect = [
                _completed("Module\nnouveau  12345  0\nnvidia  67890  0\n"),
                _completed("nvidia-driver-550\t550.120-0ubuntu1\tii\n"),
            ]
            result = dm._get_current_driver()
        assert result["driver_type"] == "proprietary"
        assert result["loaded"] is True

    def test_module_type_from_dkms_package(self, dm):
        """module_type is determined from associated dkms/prebuilt package."""
        with (
            patch("driver_manager.Path") as MockPath,
            patch.object(dm, "_run_cmd") as mock_run,
        ):
            proc_mock = MagicMock()
            proc_mock.exists.return_value = True
            proc_mock.read_text.return_value = (
                "NVRM version: NVIDIA UNIX x86_64 Kernel Module  550.120  "
                "Wed Apr 17 00:28:07 UTC 2024\n"
            )
            MockPath.return_value = proc_mock

            mock_run.side_effect = [
                _completed("Module\nnvidia  12345  0\n"),
                _completed(
                    "nvidia-driver-550\t550.120-0ubuntu1\tii\n"
                    "nvidia-dkms-550\t550.120-0ubuntu1\tii\n"
                ),
            ]
            result = dm._get_current_driver()
        assert result["module_type"] == "dkms"
        assert result["package_name"] == "nvidia-driver-550"

    def test_no_driver_loaded(self, dm):
        with (
            patch("driver_manager.Path") as MockPath,
            patch.object(dm, "_run_cmd") as mock_run,
        ):
            proc_mock = MagicMock()
            proc_mock.exists.return_value = False
            MockPath.return_value = proc_mock

            mock_run.return_value = _completed("Module\ni915  12345  0\n")
            result = dm._get_current_driver()
        assert result["driver_type"] == "none"
        assert result["loaded"] is False


# ===================================================================
# Task 11: D-Bus integration and error handling
# ===================================================================

from service import VerdeService  # noqa: E402

_XML_PATH = Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


def _make_service(mock_nvml=None, mock_dm=None):
    """Create a VerdeService with mocked dependencies."""
    nvml = mock_nvml or MagicMock()
    nvml.initialize.return_value = True
    nvml._initialized = True
    nvml.device_count.return_value = 1
    nvml.get_device_by_index.return_value = MagicMock()
    nvml.get_device_name.return_value = "GPU-0"
    nvml.get_driver_version.return_value = "550.120"
    nvml.shutdown.return_value = None

    dm = mock_dm or MagicMock()
    return VerdeService(
        loop=MagicMock(),
        on_idle_reset=MagicMock(),
        introspection_xml=_XML,
        nvml=nvml,
        driver_manager=dm,
    )


def _call_method(service, method_name):
    """Call a D-Bus method and return the invocation mock."""
    conn = MagicMock()
    conn.register_object.return_value = 42
    service._on_bus_acquired(conn, "com.verde.Manager")

    invocation = MagicMock()
    with patch("service.check_authorization", return_value=True):
        service._handle_method_call(
            conn,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            method_name,
            None,
            invocation,
        )
    return invocation


class TestListAvailableDriversDbus:
    def test_returns_aa_sv_variant(self):
        dm = MagicMock()
        dm.list_available_drivers.return_value = {
            "drivers": [
                {
                    "version": "550",
                    "variant": "desktop",
                    "package_name": "nvidia-driver-550",
                    "installed": True,
                    "recommended": True,
                    "held": False,
                    "module_type": "dkms",
                    "module_status": "built",
                    "repository": "ubuntu",
                    "recommendation_reason": "Recommended",
                    "cuda_compatibility": "12.4",
                    "known_issues": "",
                }
            ],
            "missing_repositories": [],
            "run_file_detected": False,
            "run_file_message": "",
        }
        svc = _make_service(mock_dm=dm)
        inv = MagicMock()
        svc._dispatch_list_available_drivers(inv)
        inv.return_value.assert_called_once()
        # Verify the variant was constructed (return_value called with GLib.Variant)
        args = inv.return_value.call_args[0][0]
        drivers = args.get_child_value(0).unpack()
        assert len(drivers) == 1
        assert drivers[0]["version"] == "550"
        assert drivers[0]["recommended"] is True
        # Verify metadata dict is present
        meta = args.get_child_value(1).unpack()
        assert meta["run_file_detected"] is False

    def test_apt_unavailable_error(self):
        dm = MagicMock()
        dm.list_available_drivers.side_effect = RuntimeError("apt broken")
        svc = _make_service(mock_dm=dm)
        inv = MagicMock()
        svc._dispatch_list_available_drivers(inv)
        inv.return_dbus_error.assert_called_once()
        error_name = inv.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.AptUnavailable"


class TestGetCurrentDriverDbus:
    def test_returns_a_sv_variant(self):
        dm = MagicMock()
        dm.get_current_driver.return_value = {
            "version": "550.120",
            "driver_type": "proprietary",
            "package_name": "nvidia-driver-550",
            "variant": "desktop",
            "module_type": "dkms",
            "loaded": True,
        }
        svc = _make_service(mock_dm=dm)
        inv = _call_method(svc, "GetCurrentDriver")
        inv.return_value.assert_called_once()
        args = inv.return_value.call_args[0][0]
        result = args.get_child_value(0).unpack()
        assert result["driver_type"] == "proprietary"
        assert result["version"] == "550.120"
        assert result["loaded"] is True

    def test_apt_unavailable_error(self):
        dm = MagicMock()
        dm.get_current_driver.side_effect = RuntimeError("detection failed")
        svc = _make_service(mock_dm=dm)
        inv = _call_method(svc, "GetCurrentDriver")
        inv.return_dbus_error.assert_called_once()
        error_name = inv.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.AptUnavailable"

    def test_idle_timer_reset(self):
        dm = MagicMock()
        dm.get_current_driver.return_value = {
            "version": "",
            "driver_type": "none",
            "package_name": "",
            "variant": "",
            "module_type": "",
            "loaded": False,
        }
        idle_reset = MagicMock()
        nvml = MagicMock()
        nvml.initialize.return_value = True
        nvml._initialized = True
        nvml.device_count.return_value = 1
        nvml.get_device_by_index.return_value = MagicMock()
        nvml.get_device_name.return_value = "GPU"
        nvml.get_driver_version.return_value = "550"
        nvml.shutdown.return_value = None
        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=idle_reset,
            introspection_xml=_XML,
            nvml=nvml,
            driver_manager=dm,
        )
        _call_method(svc, "GetCurrentDriver")
        idle_reset.assert_called()


class TestListAvailableDriversIdleReset:
    def test_idle_timer_reset(self):
        dm = MagicMock()
        dm.list_available_drivers.return_value = {
            "drivers": [],
            "missing_repositories": [],
            "run_file_detected": False,
            "run_file_message": "",
        }
        idle_reset = MagicMock()
        nvml = MagicMock()
        nvml.initialize.return_value = True
        nvml._initialized = True
        nvml.device_count.return_value = 1
        nvml.get_device_by_index.return_value = MagicMock()
        nvml.get_device_name.return_value = "GPU"
        nvml.get_driver_version.return_value = "550"
        nvml.shutdown.return_value = None
        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=idle_reset,
            introspection_xml=_XML,
            nvml=nvml,
            driver_manager=dm,
        )
        _call_method(svc, "ListAvailableDrivers")
        idle_reset.assert_called()


# ===================================================================
# _run_cmd error handling
# ===================================================================


class TestRunCmd:
    def test_timeout(self, dm):
        with patch("driver_manager.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 30)
            assert dm._run_cmd(["test"]) is None

    def test_file_not_found(self, dm):
        with patch("driver_manager.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            assert dm._run_cmd(["test"]) is None

    def test_os_error(self, dm):
        with patch("driver_manager.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("bad")
            assert dm._run_cmd(["test"]) is None

    def test_nonzero_rc(self, dm):
        with patch("driver_manager.subprocess.run") as mock_run:
            mock_run.return_value = _completed(rc=1, stderr="error")
            assert dm._run_cmd(["test"]) is None

    def test_success(self, dm):
        with patch("driver_manager.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="ok")
            result = dm._run_cmd(["test"])
            assert result is not None
            assert result.stdout == "ok"
