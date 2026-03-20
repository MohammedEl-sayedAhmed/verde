"""Unit tests for Story 2.7: Module Not Loaded — Diagnosis & One-Click Fix."""

from __future__ import annotations

import subprocess

from module_doctor import (
    CAUSE_BLACKLISTED,
    CAUSE_DKMS_FAILED,
    CAUSE_DKMS_MISSING,
    CAUSE_KERNEL_MISMATCH,
    CAUSE_MISSING_HEADERS,
    CAUSE_SECURE_BOOT,
    CAUSE_UNKNOWN,
    ModuleDoctor,
)


def _cp(stdout: str = "", stderr: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


# ═══════════════════════════════════════════════════════════════════════
# Diagnosis tests
# ═══════════════════════════════════════════════════════════════════════


class TestDiagnoseMissingHeaders:
    def test_headers_not_installed(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(rc=1, stdout="")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_MISSING_HEADERS
        assert diag["fixable"] is True
        assert len(diag["packages"]) >= 1
        assert diag["reboot_required"] is False

    def test_headers_installed_passes(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                return _cp(stdout="nvidia/560.35.03, 6.8.0-106-generic, x86_64: installed")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = md.diagnose()
        assert diag["cause"] != CAUSE_MISSING_HEADERS


class TestDiagnoseDKMS:
    def test_dkms_no_nvidia_source(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                return _cp(stdout="virtualbox/7.0.14: installed")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_DKMS_MISSING
        assert diag["fixable"] is False

    def test_dkms_failed_build(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                return _cp(stdout=f"nvidia/560.35.03, {_kernel()}, x86_64: broken")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_DKMS_FAILED
        assert diag["fixable"] is True

    def test_dkms_no_entry_for_kernel(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                return _cp(stdout="nvidia/560.35.03, 5.15.0-100-generic, x86_64: installed")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_DKMS_FAILED
        assert "built" in diag["detail"].lower() or "build" in diag["detail"].lower()


class TestDiagnoseKernelMismatch:
    def test_headers_unavailable_in_repos(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                kernel = _kernel()
                return _cp(stdout=f"nvidia/560.35.03, {kernel}, x86_64: installed")
            if cmd[0] == "apt-cache":
                return _cp(rc=100, stderr="No packages found")
            return _cp()

        md = ModuleDoctor(
            run=_run,
            read_file=lambda p: "VERSION_CODENAME=noble\n" if "os-release" in p else "",
        )
        # Skip DKMS check (installed), goes to mismatch
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_KERNEL_MISMATCH
        assert diag["reboot_required"] is True
        assert diag["fixable"] is True


class TestDiagnoseSecureBoot:
    def test_secure_boot_no_mok(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                kernel = _kernel()
                return _cp(stdout=f"nvidia/560, {kernel}, x86_64: installed")
            if cmd[0] == "apt-cache":
                return _cp(stdout="Package: linux-headers-...\n")
            if cmd[0] == "mokutil":
                if "--sb-state" in cmd:
                    return _cp(stdout="SecureBoot enabled")
                if "--test-key" in cmd:
                    return _cp(rc=1, stdout="")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_SECURE_BOOT
        assert diag["fixable"] is False


class TestDiagnoseBlacklisted:
    def test_nvidia_blacklisted(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                kernel = _kernel()
                return _cp(stdout=f"nvidia/560, {kernel}, x86_64: installed")
            if cmd[0] == "apt-cache":
                return _cp(stdout="Package: linux-headers-...\n")
            if cmd[0] == "mokutil" and "--sb-state" in cmd:
                return _cp(stdout="SecureBoot disabled")
            return _cp()

        md = ModuleDoctor(
            run=_run,
            read_file=lambda p: (
                "blacklist nvidia\n" if p == "/etc/modprobe.d/blacklist.conf" else ""
            ),
            list_modprobe_confs=lambda: ["/etc/modprobe.d/blacklist.conf"],
        )
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_BLACKLISTED
        assert diag["fixable"] is True
        assert "/etc/modprobe.d/blacklist.conf" in diag.get("blacklist_files", [])


class TestDiagnoseUnknown:
    def test_all_checks_pass_returns_unknown(self):
        def _run(cmd, **kw):
            if cmd[0] == "dpkg-query":
                return _cp(stdout="install ok installed")
            if cmd[0] == "dkms":
                kernel = _kernel()
                return _cp(stdout=f"nvidia/560, {kernel}, x86_64: installed")
            if cmd[0] == "apt-cache":
                return _cp(stdout="Package: linux-headers-...\n")
            if cmd[0] == "mokutil" and "--sb-state" in cmd:
                return _cp(stdout="SecureBoot disabled")
            return _cp()

        md = ModuleDoctor(
            run=_run,
            read_file=lambda p: "",
            list_modprobe_confs=lambda: [],
        )
        diag = md.diagnose()
        assert diag["cause"] == CAUSE_UNKNOWN
        assert diag["fixable"] is False


# ═══════════════════════════════════════════════════════════════════════
# Fix tests
# ═══════════════════════════════════════════════════════════════════════


class TestFixMissingHeaders:
    def test_fix_headers_success(self):
        progress = []
        complete = []

        def _run(cmd, **kw):
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = {
            "cause": CAUSE_MISSING_HEADERS,
            "packages": ["linux-headers-6.8.0-106-generic"],
        }
        md.fix_module(
            diag,
            "op1",
            lambda oid, pct, msg: progress.append((oid, pct, msg)),
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is True
        assert len(progress) >= 3

    def test_fix_headers_apt_fails(self):
        complete = []

        def _run(cmd, **kw):
            if cmd[0] == "apt-get":
                return _cp(rc=1, stderr="E: Unable to fetch")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        diag = {"cause": CAUSE_MISSING_HEADERS, "packages": ["linux-headers-6.8.0"]}
        md.fix_module(
            diag,
            "op2",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is False


class TestFixDKMSRebuild:
    def test_dkms_rebuild_success(self):
        complete = []

        md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=lambda p: "")
        md.fix_module(
            {"cause": CAUSE_DKMS_FAILED},
            "op3",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is True


class TestFixKernelMismatch:
    def test_kernel_mismatch_success_with_reboot(self):
        complete = []
        reboot = []

        md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=lambda p: "")
        md.fix_module(
            {
                "cause": CAUSE_KERNEL_MISMATCH,
                "packages": ["linux-generic", "linux-headers-generic"],
            },
            "op_km",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
            lambda req, reason: reboot.append((req, reason)),
        )
        assert complete[0][1] is True
        assert "Reboot required" in complete[0][2]
        assert len(reboot) == 1
        assert reboot[0][0] is True

    def test_kernel_mismatch_apt_fails(self):
        complete = []

        def _run(cmd, **kw):
            if cmd[0] == "apt-get":
                return _cp(rc=1, stderr="E: Unable to locate package")
            return _cp()

        md = ModuleDoctor(run=_run, read_file=lambda p: "")
        md.fix_module(
            {"cause": CAUSE_KERNEL_MISMATCH, "packages": ["linux-generic"]},
            "op_km2",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is False

    def test_kernel_mismatch_empty_packages_fails(self):
        complete = []

        md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=lambda p: "")
        md.fix_module(
            {"cause": CAUSE_KERNEL_MISMATCH, "packages": []},
            "op_km3",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is False


class TestFixDKMSMissing:
    def test_dkms_missing_returns_failure(self):
        complete = []

        md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=lambda p: "")
        md.fix_module(
            {"cause": CAUSE_DKMS_MISSING},
            "op_dm",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is False
        assert "DKMS" in complete[0][2]


class TestFixBlacklisted:
    def test_blacklist_removed_and_module_loaded(self):
        import os
        import tempfile
        from unittest.mock import patch

        complete = []
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write("# comment\nblacklist nvidia\noptions nvidia modeset=1\n")
            conf_path = f.name

        try:
            # Mock realpath to return a path under /etc/modprobe.d/ so validation passes
            with patch(
                "module_doctor.os.path.realpath",
                return_value=f"/etc/modprobe.d/{os.path.basename(conf_path)}",
            ):
                md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=_default_read_file)
                md.fix_module(
                    {"cause": CAUSE_BLACKLISTED, "blacklist_files": [conf_path]},
                    "op4",
                    lambda oid, pct, msg: None,
                    lambda oid, ok, msg: complete.append((oid, ok, msg)),
                )
            assert complete[0][1] is True
            # Verify blacklist line was commented out
            with open(conf_path) as f:
                content = f.read()
            assert "blacklist nvidia" not in content or "# Removed by Verde" in content
        finally:
            os.unlink(conf_path)

    def test_empty_blacklist_files_fails(self):
        complete = []
        md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=lambda p: "")
        md.fix_module(
            {"cause": CAUSE_BLACKLISTED, "blacklist_files": []},
            "op4b",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is False

    def test_path_outside_modprobe_rejected(self):
        complete = []
        md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=lambda p: "")
        md.fix_module(
            {"cause": CAUSE_BLACKLISTED, "blacklist_files": ["/etc/passwd"]},
            "op4c",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is False
        assert "Refusing" in complete[0][2]


class TestFixSecureBoot:
    def test_secure_boot_returns_guidance(self):
        complete = []
        md = ModuleDoctor(run=lambda cmd, **kw: _cp(), read_file=lambda p: "")
        md.fix_module(
            {"cause": CAUSE_SECURE_BOOT},
            "op5",
            lambda oid, pct, msg: None,
            lambda oid, ok, msg: complete.append((oid, ok, msg)),
        )
        assert complete[0][1] is False
        assert "mokutil" in complete[0][2].lower()


# ═══════════════════════════════════════════════════════════════════════
# D-Bus wiring tests
# ═══════════════════════════════════════════════════════════════════════


class TestDBusWiring:
    def test_service_has_module_doctor(self):
        import pathlib
        from unittest.mock import MagicMock

        from service import VerdeService

        xml_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        xml = xml_path.read_text()
        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=MagicMock(),
            introspection_xml=xml,
        )
        assert hasattr(svc, "_module_doctor")
        assert hasattr(svc, "_dispatch_diagnose_module")
        assert hasattr(svc, "_dispatch_fix_module")

    def test_xml_has_module_methods(self):
        import pathlib

        xml_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        xml = xml_path.read_text()
        assert "DiagnoseModuleFailure" in xml
        assert "FixModuleNotLoaded" in xml

    def test_polkit_maps_fix_module(self):
        from polkit import METHOD_ACTION_MAP

        assert "FixModuleNotLoaded" in METHOD_ACTION_MAP

    def test_fix_module_in_validators(self):
        from validators import validate_operation_name

        # Should not raise
        validate_operation_name("fix_module")


# ── Helpers ───────────────────────────────────────────────────────────


def _kernel() -> str:
    import os

    return os.uname().release


def _default_read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""
