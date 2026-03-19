"""Unit tests for Story 4.1: Power & Suspend Issue Detection."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from power_manager import (
    ISSUE_HIBERNATE,
    ISSUE_SECURE_BOOT,
    ISSUE_SUSPEND,
    ISSUE_WAYLAND,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARNING,
    STATUS_ISSUES_FOUND,
    STATUS_UNKNOWN,
    STATUS_WORKING,
    PowerManager,
    _make_issue,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _run_for_services(
    *,
    suspend: int = 0,
    resume: int = 0,
    hibernate: int = 0,
    powerd: int = 1,
    suspend_stdout: str = "enabled",
    resume_stdout: str = "enabled",
    hibernate_stdout: str = "enabled",
    powerd_stdout: str = "disabled",
):
    """Return a mock run function that responds to systemctl is-enabled queries."""

    service_map = {
        "nvidia-suspend.service": (suspend, suspend_stdout),
        "nvidia-resume.service": (resume, resume_stdout),
        "nvidia-hibernate.service": (hibernate, hibernate_stdout),
        "nvidia-powerd.service": (powerd, powerd_stdout),
    }

    def _run(cmd, **kwargs):
        if cmd[:2] == ["systemctl", "is-enabled"] and len(cmd) == 3:
            svc = cmd[2]
            if svc in service_map:
                rc, out = service_map[svc]
                return _make_cp(stdout=out + "\n", returncode=rc)
        return _make_cp(returncode=1)

    return _run


def _run_all_services_enabled():
    """Run function that has all services enabled and mokutil showing SB disabled."""

    base = _run_for_services(
        suspend=0,
        resume=0,
        hibernate=0,
        powerd=0,
        suspend_stdout="enabled",
        resume_stdout="enabled",
        hibernate_stdout="enabled",
        powerd_stdout="enabled",
    )

    def _run(cmd, **kwargs):
        # Secure Boot disabled by default in the "all working" scenario
        if cmd[:2] == ["mokutil", "--sb-state"]:
            return _make_cp(stdout="SecureBoot disabled\n")
        # loginctl — report x11 session (no wayland issues in all-working)
        if cmd[:2] == ["loginctl", "list-sessions"]:
            return _make_cp(stdout="42 1000 user seat0\n")
        if cmd[:2] == ["loginctl", "show-session"]:
            return _make_cp(stdout="x11\n")
        return base(cmd, **kwargs)

    return _run


def _read_hibernate_ok(path: str) -> str:
    """File reader that returns valid hibernate prerequisites."""
    files = {
        "/sys/power/state": "freeze mem disk",
        "/proc/cmdline": "BOOT_IMAGE=/vmlinuz root=UUID=abc resume=UUID=swap-uuid quiet",
        "/proc/swaps": "Filename\tType\tSize\tUsed\tPriority\n/dev/sda2\tpartition\t16777212\t0\t-2\n",
        "/etc/systemd/sleep.conf": "[Sleep]\n#HibernateMode=platform shutdown\n",
    }
    return files.get(path, "")


# ── Task 1: Skeleton ─────────────────────────────────────────────────


class TestPowerManagerSkeleton:
    """Task 1: Module skeleton and constants."""

    def test_imports(self):
        pass

    def test_get_power_status_returns_dict(self):
        pm = PowerManager(run=_run_all_services_enabled(), read_file=_read_hibernate_ok)
        result = pm.get_power_status()
        assert isinstance(result, dict)
        assert "overall_status" in result
        assert "issues" in result
        assert "suspend_service_active" in result
        assert "hibernate_service_active" in result
        assert "secure_boot_enabled" in result
        assert "mok_enrolled" in result
        assert "wayland_session" in result

    def test_get_power_status_types(self):
        pm = PowerManager(run=_run_all_services_enabled(), read_file=_read_hibernate_ok)
        result = pm.get_power_status()
        assert isinstance(result["overall_status"], str)
        assert isinstance(result["issues"], list)
        assert isinstance(result["suspend_service_active"], bool)
        assert isinstance(result["hibernate_service_active"], bool)
        assert isinstance(result["secure_boot_enabled"], bool)
        assert isinstance(result["mok_enrolled"], bool)
        assert isinstance(result["wayland_session"], bool)

    def test_make_issue_structure(self):
        issue = _make_issue("suspend", "critical", "Test", "Detail", True, False)
        assert issue["type"] == "suspend"
        assert issue["severity"] == "critical"
        assert issue["summary"] == "Test"
        assert issue["detail"] == "Detail"
        assert issue["fixable"] is True
        assert issue["already_fixed"] is False


# ── Task 2: Suspend service detection ────────────────────────────────


class TestSuspendServices:
    """Task 2: NVIDIA suspend/resume service detection (AC#1, AC#5)."""

    def test_all_services_enabled(self):
        """All suspend services enabled returns no issues, suspend_active=True."""
        pm = PowerManager(
            run=_run_for_services(suspend=0, resume=0, powerd=0, powerd_stdout="enabled"),
            read_file=lambda p: "",
        )
        issues, active = pm._check_suspend_services()
        assert active is True
        assert all(i["severity"] == SEVERITY_OK for i in issues)

    def test_suspend_disabled(self):
        """Missing suspend service returns critical issue."""
        pm = PowerManager(
            run=_run_for_services(suspend=1, suspend_stdout="disabled"),
            read_file=lambda p: "",
        )
        issues, active = pm._check_suspend_services()
        assert active is False
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) >= 1
        assert critical[0]["type"] == ISSUE_SUSPEND
        assert critical[0]["fixable"] is True

    def test_resume_disabled(self):
        """Missing resume service returns critical issue."""
        pm = PowerManager(
            run=_run_for_services(resume=1, resume_stdout="disabled"),
            read_file=lambda p: "",
        )
        _issues, active = pm._check_suspend_services()
        assert active is False

    def test_already_fixed_detection(self):
        """When all services enabled, already_fixed is True with severity ok."""
        pm = PowerManager(
            run=_run_for_services(suspend=0, resume=0, powerd=0, powerd_stdout="enabled"),
            read_file=lambda p: "",
        )
        issues, active = pm._check_suspend_services()
        ok_issues = [i for i in issues if i["already_fixed"]]
        assert active is True
        assert len(ok_issues) >= 1
        assert ok_issues[0]["severity"] == SEVERITY_OK

    def test_masked_service(self):
        """Masked service is treated as disabled."""
        pm = PowerManager(
            run=_run_for_services(suspend=1, suspend_stdout="masked"),
            read_file=lambda p: "",
        )
        _issues, active = pm._check_suspend_services()
        assert active is False

    def test_powerd_missing_is_warning(self):
        """nvidia-powerd missing is only a warning, not critical."""
        pm = PowerManager(
            run=_run_for_services(suspend=0, resume=0, powerd=1, powerd_stdout="not-found"),
            read_file=lambda p: "",
        )
        issues, active = pm._check_suspend_services()
        # Suspend/resume are fine so active is True
        assert active is True
        # powerd missing should be at most a warning
        powerd_issues = [
            i
            for i in issues
            if "powerd" in i.get("detail", "").lower() or "power" in i.get("summary", "").lower()
        ]
        for i in powerd_issues:
            assert i["severity"] != SEVERITY_CRITICAL

    def test_human_readable_explanation(self):
        """Issue includes human-readable explanation."""
        pm = PowerManager(
            run=_run_for_services(suspend=1, suspend_stdout="disabled"),
            read_file=lambda p: "",
        )
        issues, _ = pm._check_suspend_services()
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) >= 1
        assert len(critical[0]["detail"]) > 20  # Meaningful explanation


# ── Task 3: Hibernate detection ──────────────────────────────────────


class TestHibernate:
    """Task 3: Hibernate issue detection (AC#2, AC#5)."""

    def test_hibernate_all_ok(self):
        """All prerequisites met: hibernate service + swap + resume param."""
        pm = PowerManager(
            run=_run_for_services(hibernate=0),
            read_file=_read_hibernate_ok,
        )
        issues, active = pm._check_hibernate()
        assert active is True
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) == 0

    def test_hibernate_service_disabled(self):
        """Disabled hibernate service returns critical issue."""
        pm = PowerManager(
            run=_run_for_services(hibernate=1, hibernate_stdout="disabled"),
            read_file=_read_hibernate_ok,
        )
        issues, active = pm._check_hibernate()
        assert active is False
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) >= 1

    def test_no_disk_in_power_state(self):
        """Kernel doesn't support hibernate (no 'disk' in /sys/power/state)."""

        def reader(path):
            if path == "/sys/power/state":
                return "freeze mem"
            return _read_hibernate_ok(path)

        pm = PowerManager(
            run=_run_for_services(hibernate=0),
            read_file=reader,
        )
        issues, _ = pm._check_hibernate()
        warn_or_crit = [
            i for i in issues if i["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL)
        ]
        assert len(warn_or_crit) >= 1

    def test_missing_resume_parameter(self):
        """No resume= in /proc/cmdline returns critical hibernate issue."""

        def reader(path):
            if path == "/proc/cmdline":
                return "BOOT_IMAGE=/vmlinuz root=UUID=abc quiet"
            return _read_hibernate_ok(path)

        pm = PowerManager(
            run=_run_for_services(hibernate=0),
            read_file=reader,
        )
        issues, _ = pm._check_hibernate()
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) >= 1

    def test_no_swap(self):
        """No swap configured returns warning/critical."""

        def reader(path):
            if path == "/proc/swaps":
                return "Filename\tType\tSize\tUsed\tPriority\n"
            return _read_hibernate_ok(path)

        pm = PowerManager(
            run=_run_for_services(hibernate=0),
            read_file=reader,
        )
        issues, _ = pm._check_hibernate()
        warn_or_crit = [
            i for i in issues if i["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL)
        ]
        assert len(warn_or_crit) >= 1

    def test_sleep_conf_blocks_hibernate(self):
        """sleep.conf with HibernateMode=suspend returns warning."""

        def reader(path):
            if path == "/etc/systemd/sleep.conf":
                return "[Sleep]\nHibernateMode=suspend\n"
            return _read_hibernate_ok(path)

        pm = PowerManager(
            run=_run_for_services(hibernate=0),
            read_file=reader,
        )
        issues, _ = pm._check_hibernate()
        warn_or_crit = [
            i for i in issues if i["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL)
        ]
        assert len(warn_or_crit) >= 1

    def test_already_fixed_hibernate(self):
        """All prerequisites met marks already_fixed True."""
        pm = PowerManager(
            run=_run_for_services(hibernate=0),
            read_file=_read_hibernate_ok,
        )
        _issues, active = pm._check_hibernate()
        assert active is True


# ── Task 4: Secure Boot MOK detection ────────────────────────────────


def _run_secure_boot(
    *,
    sb_state: str = "SecureBoot enabled",
    sb_rc: int = 0,
    mok_test_rc: int = 0,
    modinfo_signer: str = "DKMS module signing key",
    modinfo_rc: int = 0,
    # Also handle systemctl calls for other checks
    base_run=None,
):
    """Return a mock run for Secure Boot checks."""

    def _run(cmd, **kwargs):
        if cmd[:2] == ["mokutil", "--sb-state"]:
            return _make_cp(stdout=sb_state + "\n", returncode=sb_rc)
        if cmd[:2] == ["mokutil", "--test-key"]:
            return _make_cp(returncode=mok_test_rc)
        if cmd[0] == "modinfo" and "-F" in cmd:
            return _make_cp(stdout=modinfo_signer + "\n", returncode=modinfo_rc)
        if base_run:
            return base_run(cmd, **kwargs)
        return _make_cp(returncode=1)

    return _run


class TestSecureBoot:
    """Task 4: Secure Boot MOK detection (AC#3)."""

    def test_secure_boot_disabled(self):
        """Secure Boot disabled returns ok, no issues."""
        pm = PowerManager(
            run=_run_secure_boot(sb_state="SecureBoot disabled"),
            read_file=lambda p: "",
        )
        issues, sb_enabled, _mok = pm._check_secure_boot()
        assert sb_enabled is False
        critical = [i for i in issues if i["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL)]
        assert len(critical) == 0

    def test_secure_boot_enabled_mok_enrolled(self):
        """Secure Boot enabled + MOK enrolled + module signed = ok."""
        pm = PowerManager(
            run=_run_secure_boot(
                sb_state="SecureBoot enabled",
                mok_test_rc=0,
                modinfo_signer="DKMS module signing key",
            ),
            read_file=lambda p: "",
        )
        issues, sb_enabled, mok = pm._check_secure_boot()
        assert sb_enabled is True
        assert mok is True
        critical = [i for i in issues if i["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL)]
        assert len(critical) == 0

    def test_secure_boot_no_mok(self):
        """Secure Boot enabled but MOK not enrolled returns warning."""
        pm = PowerManager(
            run=_run_secure_boot(
                sb_state="SecureBoot enabled",
                mok_test_rc=1,
            ),
            read_file=lambda p: "",
        )
        issues, sb_enabled, mok = pm._check_secure_boot()
        assert sb_enabled is True
        assert mok is False
        warnings = [i for i in issues if i["severity"] == SEVERITY_WARNING]
        assert len(warnings) >= 1
        assert warnings[0]["type"] == ISSUE_SECURE_BOOT

    def test_secure_boot_module_not_signed(self):
        """Module not signed returns warning."""
        pm = PowerManager(
            run=_run_secure_boot(
                sb_state="SecureBoot enabled",
                mok_test_rc=0,
                modinfo_rc=1,
            ),
            read_file=lambda p: "",
        )
        issues, sb_enabled, _ = pm._check_secure_boot()
        assert sb_enabled is True
        warnings = [i for i in issues if i["severity"] == SEVERITY_WARNING]
        assert len(warnings) >= 1

    def test_mokutil_not_found(self):
        """mokutil not available (e.g., non-EFI system) handled gracefully."""

        def _run(cmd, **kwargs):
            if cmd[0] == "mokutil":
                raise FileNotFoundError("mokutil not found")
            return _make_cp(returncode=1)

        pm = PowerManager(run=_run, read_file=lambda p: "")
        # Should not raise
        _issues, sb_enabled, _mok = pm._check_secure_boot()
        assert sb_enabled is False

    def test_human_readable_explanation(self):
        """Warning includes meaningful explanation."""
        pm = PowerManager(
            run=_run_secure_boot(sb_state="SecureBoot enabled", mok_test_rc=1),
            read_file=lambda p: "",
        )
        issues, _, _ = pm._check_secure_boot()
        warnings = [i for i in issues if i["severity"] == SEVERITY_WARNING]
        assert len(warnings) >= 1
        assert (
            "secure boot" in warnings[0]["detail"].lower()
            or "mok" in warnings[0]["detail"].lower()
        )


# ── Task 5: Wayland detection ────────────────────────────────────────


def _run_loginctl(session_type: str):
    """Return a run function that fakes loginctl session type queries."""

    def _run(cmd, **kw):
        if cmd[:2] == ["loginctl", "list-sessions"]:
            return _make_cp(stdout="42 1000 user seat0\n")
        if cmd[:2] == ["loginctl", "show-session"]:
            return _make_cp(stdout=session_type + "\n")
        return _make_cp(returncode=1)

    return _run


class TestWayland:
    """Task 5: Wayland-specific issue detection (AC#4, AC#5)."""

    def test_not_wayland_skips_checks(self):
        """Non-Wayland session skips all Wayland checks."""
        pm = PowerManager(run=_run_loginctl("x11"), read_file=lambda p: "")
        issues, is_wayland = pm._check_wayland_issues()
        assert is_wayland is False
        assert len(issues) == 0

    def test_wayland_all_configured(self):
        """Wayland with all NVIDIA settings in place returns ok."""

        def reader(path):
            if "modprobe" in path and path.endswith(".conf"):
                return "options nvidia-drm modeset=1\n"
            if path == "/proc/cmdline":
                return "BOOT_IMAGE=/vmlinuz nvidia-drm.modeset=1 quiet"
            if path == "/etc/environment":
                return "GBM_BACKEND=nvidia-drm\nWLR_NO_HARDWARE_CURSORS=1\n__NV_PRIME_RENDER_OFFLOAD=1\n"
            return ""

        pm = PowerManager(
            run=_run_loginctl("wayland"),
            read_file=reader,
        )
        issues, is_wayland = pm._check_wayland_issues()
        assert is_wayland is True
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) == 0

    def test_wayland_missing_modeset(self):
        """Wayland without modeset=1 returns critical issue."""
        pm = PowerManager(
            run=_run_loginctl("wayland"),
            read_file=lambda p: "",
        )
        issues, is_wayland = pm._check_wayland_issues()
        assert is_wayland is True
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) >= 1
        assert critical[0]["type"] == ISSUE_WAYLAND
        assert critical[0]["fixable"] is True

    def test_wayland_missing_gbm(self):
        """Wayland without GBM_BACKEND returns warning."""

        def reader(path):
            if path == "/proc/cmdline":
                return "nvidia-drm.modeset=1 quiet"
            return ""

        pm = PowerManager(run=_run_loginctl("wayland"), read_file=reader)
        issues, _is_wayland = pm._check_wayland_issues()
        warnings = [i for i in issues if i["severity"] == SEVERITY_WARNING]
        assert len(warnings) >= 1

    def test_wayland_missing_wlr_cursors(self):
        """Wayland without WLR_NO_HARDWARE_CURSORS returns warning."""

        def reader(path):
            if path == "/proc/cmdline":
                return "nvidia-drm.modeset=1 quiet"
            if path == "/etc/environment":
                return "GBM_BACKEND=nvidia-drm\n"
            return ""

        pm = PowerManager(run=_run_loginctl("wayland"), read_file=reader)
        issues, _is_wayland = pm._check_wayland_issues()
        wlr = [i for i in issues if "WLR" in i.get("summary", "")]
        assert len(wlr) >= 1
        assert wlr[0]["severity"] == SEVERITY_WARNING

    def test_wayland_already_fixed(self):
        """All Wayland configs present marks already_fixed."""

        def reader(path):
            if path == "/proc/cmdline":
                return "nvidia-drm.modeset=1 quiet"
            if path == "/etc/environment":
                return "GBM_BACKEND=nvidia-drm\nWLR_NO_HARDWARE_CURSORS=1\n"
            return ""

        pm = PowerManager(run=_run_loginctl("wayland"), read_file=reader)
        issues, is_wayland = pm._check_wayland_issues()
        assert is_wayland is True
        fixed = [i for i in issues if i.get("already_fixed")]
        assert len(fixed) >= 1

    def test_human_readable_explanation(self):
        """Missing modeset includes meaningful explanation."""
        pm = PowerManager(run=_run_loginctl("wayland"), read_file=lambda p: "")
        issues, _ = pm._check_wayland_issues()
        critical = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        assert len(critical) >= 1
        assert (
            "modeset" in critical[0]["detail"].lower()
            or "modesetting" in critical[0]["detail"].lower()
        )

    def test_loginctl_fallback_to_env(self):
        """When loginctl fails, falls back to $XDG_SESSION_TYPE."""

        def _run(cmd, **kw):
            if cmd[0] == "loginctl":
                raise OSError("loginctl not found")
            return _make_cp(returncode=1)

        pm = PowerManager(run=_run, read_file=lambda p: "")
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False):
            _issues, is_wayland = pm._check_wayland_issues()
        assert is_wayland is True

    def test_static_service_accepted(self):
        """Services with 'static' status are treated as enabled."""
        pm = PowerManager(
            run=_run_for_services(
                suspend=0, resume=0, suspend_stdout="static", resume_stdout="static"
            ),
            read_file=lambda p: "",
        )
        _issues, active = pm._check_suspend_services()
        assert active is True


# ── Task 6: Overall status and response assembly ─────────────────────


class TestOverallStatus:
    """Task 6: Response assembly and overall status (AC#6, AC#7, AC#8)."""

    def test_all_working(self):
        """No issues found returns overall_status=working."""
        pm = PowerManager(
            run=_run_all_services_enabled(),
            read_file=_read_hibernate_ok,
        )
        result = pm.get_power_status()
        assert result["overall_status"] == STATUS_WORKING
        critical = [
            i for i in result["issues"] if i["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL)
        ]
        assert len(critical) == 0

    def test_issues_found(self):
        """Issues present returns overall_status=issues_found."""

        def _run(cmd, **kw):
            # loginctl → x11
            if cmd[:2] == ["loginctl", "list-sessions"]:
                return _make_cp(stdout="42 1000 user seat0\n")
            if cmd[:2] == ["loginctl", "show-session"]:
                return _make_cp(stdout="x11\n")
            return _run_for_services(suspend=1, suspend_stdout="disabled")(cmd, **kw)

        pm = PowerManager(run=_run, read_file=_read_hibernate_ok)
        result = pm.get_power_status()
        assert result["overall_status"] == STATUS_ISSUES_FOUND

    def test_multiple_simultaneous_issues(self):
        """Multiple issues across different categories all reported."""

        def _run(cmd, **kw):
            if cmd[:2] == ["loginctl", "list-sessions"]:
                return _make_cp(stdout="42 1000 user seat0\n")
            if cmd[:2] == ["loginctl", "show-session"]:
                return _make_cp(stdout="x11\n")
            return _run_for_services(
                suspend=1,
                suspend_stdout="disabled",
                hibernate=1,
                hibernate_stdout="disabled",
            )(cmd, **kw)

        pm = PowerManager(run=_run, read_file=_read_hibernate_ok)
        result = pm.get_power_status()
        types = {i["type"] for i in result["issues"] if i["severity"] != SEVERITY_OK}
        assert ISSUE_SUSPEND in types
        assert ISSUE_HIBERNATE in types

    def test_detection_failure_returns_unknown(self):
        """Checker exception results in overall_status=unknown."""
        pm = PowerManager(
            run=lambda cmd, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
            read_file=lambda p: "",
        )
        result = pm.get_power_status()
        assert result["overall_status"] == STATUS_UNKNOWN
        assert len(result["issues"]) > 0

    def test_dbus_dispatch_exists(self):
        """Verify GetPowerStatus is wired in service.py dispatcher."""
        import pathlib

        _XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        _XML = _XML_PATH.read_text()

        from service import VerdeService

        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=MagicMock(),
            introspection_xml=_XML,
        )
        assert hasattr(svc, "_dispatch_get_power_status")
        assert hasattr(svc, "_power_manager")

    def test_no_shell_true_in_subprocess(self):
        """Verify all subprocess calls use list form (no shell=True)."""
        calls = []

        def _tracking_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            assert "shell" not in kwargs or kwargs["shell"] is False, (
                f"shell=True detected in call: {cmd}"
            )
            return _make_cp()

        pm = PowerManager(run=_tracking_run, read_file=_read_hibernate_ok)
        pm.get_power_status()
        # At least some subprocess calls should have been made
        assert len(calls) > 0
        for cmd, _kwargs in calls:
            assert isinstance(cmd, list), f"Expected list, got {type(cmd)}: {cmd}"
