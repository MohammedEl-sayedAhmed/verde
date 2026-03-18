"""Security tests: verify systemd sandboxing directives (NFR-SEC-1)."""

from __future__ import annotations

import pathlib

import pytest

_SERVICE_FILE = (
    pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.systemd.service.in"
)
_CONTENT = _SERVICE_FILE.read_text()


class TestSystemdSandboxing:
    """Assert required sandboxing directives are present."""

    @pytest.mark.parametrize(
        "directive",
        [
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "PrivateTmp=yes",
            "MemoryDenyWriteExecute=yes",
            "SystemCallArchitectures=native",
            "ProtectKernelTunables=yes",
            "ProtectKernelLogs=yes",
            "ProtectControlGroups=yes",
            "ProtectHostname=yes",
            "ProtectClock=yes",
            "RestrictRealtime=yes",
            "RestrictNamespaces=yes",
            "LockPersonality=yes",
            "RemoveIPC=yes",
            "DevicePolicy=closed",
        ],
    )
    def test_hardening_directive_present(self, directive):
        assert directive in _CONTENT, f"Missing systemd directive: {directive}"

    def test_device_allow_nvidia(self):
        assert "DeviceAllow=/dev/nvidia*" in _CONTENT

    def test_device_allow_drm(self):
        assert "DeviceAllow=char-drm" in _CONTENT

    def test_no_new_privileges_is_no(self):
        """NoNewPrivileges must be 'no' for apt/dpkg postinst scripts."""
        assert "NoNewPrivileges=no" in _CONTENT

    def test_protect_kernel_modules_is_no(self):
        """ProtectKernelModules must be 'no' for modprobe."""
        assert "ProtectKernelModules=no" in _CONTENT

    def test_syscall_filter_present(self):
        assert "SystemCallFilter=@system-service" in _CONTENT
        assert "SystemCallFilter=~@mount" in _CONTENT

    def test_read_write_paths(self):
        assert "/var/lib/verde" in _CONTENT
        assert "/var/cache/apt" in _CONTENT
        assert "/etc/modprobe.d" in _CONTENT
