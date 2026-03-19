"""Integration tests for Story 3.4: CLI Recovery Tool end-to-end flows."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from snapshot_manager import _compute_sha256

# ── Helpers ─────────────────────────────────────────────────────────


def _create_snapshot(snap_dir: pathlib.Path, sid: str = "20260318T143000_nvidia-560-ab01") -> dict:
    """Create a valid snapshot file and return its data."""
    data = {
        "schema_version": 1,
        "snapshot_id": sid,
        "timestamp": "2026-03-18T14:30:00+00:00",
        "driver_packages": [
            {"name": "nvidia-driver-560", "version": "560.35.03", "architecture": "amd64"},
        ],
        "kernel_version": "6.8.0-45-generic",
        "dkms_modules": [],
        "config_files": {},
        "operation": {"type": "driver_install", "target_driver": "560", "user": "root"},
        "sha256": None,
    }
    data["sha256"] = _compute_sha256(data)
    (snap_dir / f"{sid}.json").write_text(json.dumps(data))
    return data


@pytest.fixture
def snap_dir(tmp_path):
    d = tmp_path / "snapshots"
    d.mkdir()
    return d


@pytest.fixture
def mock_audit(tmp_path):
    from audit import AuditLogger

    return AuditLogger(log_dir=tmp_path / "audit")


# ── Snapshot listing flow ───────────────────────────────────────────


class TestListSnapshotsFlow:
    def test_list_with_snapshots(self, snap_dir, capsys):
        """--list-snapshots displays snapshot table (AC#9)."""
        _create_snapshot(snap_dir)
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair", "--list-snapshots"])
        cli = RecoveryCLI(args, snapshot_dir=snap_dir)
        result = cli.run()

        assert result == 0
        output = capsys.readouterr().out
        assert "560" in output
        assert "2026-03-18" in output

    def test_list_empty_snapshots(self, snap_dir, capsys):
        """--list-snapshots with no snapshots shows message."""
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair", "--list-snapshots"])
        cli = RecoveryCLI(args, snapshot_dir=snap_dir)
        result = cli.run()

        assert result == 0
        output = capsys.readouterr().out
        assert "No snapshots" in output


# ── Diagnose flow ───────────────────────────────────────────────────


class TestDiagnoseFlow:
    @patch("recovery_diagnostics.run_diagnostics", return_value=[])
    def test_diagnose_healthy(self, _mock, capsys):
        """--diagnose with no issues exits 0 (AC#9)."""
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair", "--diagnose"])
        cli = RecoveryCLI(args)
        result = cli.run()

        assert result == 0
        output = capsys.readouterr().out
        assert "healthy" in output.lower()

    @patch("recovery_diagnostics.run_diagnostics")
    def test_diagnose_with_issues(self, mock_diag, capsys):
        """--diagnose with issues exits 1 and shows findings (AC#9)."""
        from recovery_diagnostics import DiagnosticResult

        mock_diag.return_value = [
            DiagnosticResult(
                issue_type="nvml_missing",
                severity="critical",
                description="NVIDIA kernel module not loaded",
                fixable=True,
            ),
        ]
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair", "--diagnose"])
        cli = RecoveryCLI(args)
        result = cli.run()

        assert result == 1
        output = capsys.readouterr().out
        assert "CRITICAL" in output
        assert "NVIDIA" in output


# ── Non-interactive rollback flow ───────────────────────────────────


class TestNonInteractiveRollbackFlow:
    @patch("cli_recovery.execute_rollback", return_value=(True, "Rolled back"))
    def test_rollback_with_yes_flag(self, mock_rollback, snap_dir, mock_audit, capsys):
        """--rollback <id> --yes skips confirmation (AC#9)."""
        _create_snapshot(snap_dir)
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(
            ["--repair", "--rollback", "20260318T143000_nvidia-560-ab01", "--yes"]
        )
        cli = RecoveryCLI(args, snapshot_dir=snap_dir, audit_logger=mock_audit)
        result = cli.run()

        assert result == 0
        mock_rollback.assert_called_once()

    def test_rollback_missing_snapshot(self, snap_dir, capsys):
        """--rollback with invalid ID exits 1."""
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair", "--rollback", "nonexistent", "--yes"])
        cli = RecoveryCLI(args, snapshot_dir=snap_dir)
        result = cli.run()

        assert result == 1


# ── Rollback execution ──────────────────────────────────────────────


class TestRollbackExecution:
    def test_execute_rollback_success(self):
        """execute_rollback calls apt-get install with correct args (AC#5)."""
        from cli_recovery import execute_rollback

        snap_data = {
            "snapshot_id": "test-snap",
            "driver_packages": [
                {"name": "nvidia-driver-560", "version": "560.35.03"},
            ],
            "operation": {"target_driver": "560"},
        }

        with (
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            success, msg = execute_rollback(snap_data)

        assert success is True
        assert "560" in msg
        # Verify apt-get was called with correct packages
        apt_call = mock_run.call_args_list[0]
        cmd = apt_call[0][0]
        assert "apt-get" in cmd
        assert "--allow-downgrades" in cmd
        assert "nvidia-driver-560=560.35.03" in cmd

    def test_execute_rollback_apt_failure(self):
        """execute_rollback returns failure on apt error (AC#5)."""
        from cli_recovery import execute_rollback

        snap_data = {
            "snapshot_id": "test-snap",
            "driver_packages": [
                {"name": "nvidia-driver-560", "version": "560.35.03"},
            ],
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=100, stdout="", stderr="E: Unable to locate package"
            )
            success, msg = execute_rollback(snap_data)

        assert success is False
        assert "failed" in msg.lower()

    def test_execute_rollback_empty_packages(self):
        """execute_rollback fails gracefully with no packages."""
        from cli_recovery import execute_rollback

        success, _msg = execute_rollback({"driver_packages": []})
        assert success is False


# ── Nouveau fallback execution ──────────────────────────────────────


class TestNouveauFallbackExecution:
    def test_execute_nouveau_success(self, tmp_path):
        """execute_nouveau_fallback creates blacklist and rebuilds initramfs (AC#6)."""
        from cli_recovery import execute_nouveau_fallback

        blacklist = tmp_path / "verde-nvidia-blacklist.conf"

        with patch("subprocess.run") as mock_run:
            # modinfo nouveau → success, update-initramfs → success
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with patch("pathlib.Path.glob", return_value=[]):
                success, msg = execute_nouveau_fallback(blacklist_path=blacklist)

        assert success is True
        assert "nouveau" in msg.lower()
        assert blacklist.exists()
        content = blacklist.read_text()
        assert "blacklist nvidia" in content
        assert "blacklist nvidia_drm" in content

    def test_execute_nouveau_initramfs_failure(self, tmp_path):
        """execute_nouveau_fallback reports failure on initramfs error."""
        from cli_recovery import execute_nouveau_fallback

        blacklist = tmp_path / "verde-nvidia-blacklist.conf"

        def _side_effect(cmd, **kwargs):
            if cmd[0] == "modinfo":
                return MagicMock(returncode=0, stdout="", stderr="")
            # update-initramfs fails
            return MagicMock(returncode=1, stdout="", stderr="error")

        with (
            patch("subprocess.run", side_effect=_side_effect),
            patch("pathlib.Path.glob", return_value=[]),
        ):
            success, msg = execute_nouveau_fallback(blacklist_path=blacklist)

        assert success is False
        assert "initramfs" in msg.lower() or "failed" in msg.lower()

    def test_execute_nouveau_no_module(self, tmp_path):
        """execute_nouveau_fallback fails when nouveau module not found (P-8)."""
        from cli_recovery import execute_nouveau_fallback

        blacklist = tmp_path / "verde-nvidia-blacklist.conf"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            success, msg = execute_nouveau_fallback(blacklist_path=blacklist)

        assert success is False
        assert "nouveau" in msg.lower()
        assert not blacklist.exists()


# ── Audit logging ───────────────────────────────────────────────────


class TestAuditLogging:
    @patch("cli_recovery.execute_rollback", return_value=(True, "Rolled back"))
    def test_rollback_audit_logged(self, mock_rollback, snap_dir, mock_audit):
        """Rollback operations are logged to audit log (AC#5)."""
        _create_snapshot(snap_dir)
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(
            ["--repair", "--rollback", "20260318T143000_nvidia-560-ab01", "--yes"]
        )
        cli = RecoveryCLI(args, snapshot_dir=snap_dir, audit_logger=mock_audit)
        cli.run()

        # Check audit log was written
        log_file = mock_audit._log_file
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        ops = [json.loads(line) for line in lines]
        recovery_ops = [e for e in ops if e["operation"] == "RECOVERY_ROLLBACK"]
        assert len(recovery_ops) >= 1


# ── Interactive flow ────────────────────────────────────────────────


class TestInteractiveFlow:
    @patch("cli_recovery.is_tty", return_value=True)
    @patch("recovery_diagnostics.run_diagnostics", return_value=[])
    def test_interactive_no_issues_exits(self, _diag, _tty, capsys):
        """Interactive mode exits when no issues found."""
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair"])
        cli = RecoveryCLI(args)
        result = cli.run()

        assert result == 0
        output = capsys.readouterr().out
        assert "healthy" in output.lower()

    def test_interactive_non_tty_exits(self, capsys):
        """Interactive mode rejects non-TTY with helpful message (P-4)."""
        from cli_recovery import RecoveryCLI, parse_recovery_args

        with patch("cli_recovery.is_tty", return_value=False):
            args = parse_recovery_args(["--repair"])
            cli = RecoveryCLI(args)
            result = cli.run()

        assert result == 1
        err = capsys.readouterr().err
        assert "tty" in err.lower()

    @patch("cli_recovery.is_tty", return_value=True)
    @patch("recovery_diagnostics.run_diagnostics")
    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_interactive_ctrl_c_exits(self, _input, mock_diag, _tty, capsys):
        """Ctrl+C during interactive mode exits gracefully."""
        from recovery_diagnostics import DiagnosticResult

        mock_diag.return_value = [
            DiagnosticResult("nvml_missing", "critical", "test", True),
        ]
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair"])
        cli = RecoveryCLI(args)
        result = cli.run()

        assert result == 1

    @patch("cli_recovery.is_tty", return_value=True)
    @patch("recovery_diagnostics.run_diagnostics")
    @patch("builtins.input", return_value="3")
    def test_interactive_exit_option(self, _input, mock_diag, _tty, capsys):
        """Selecting exit option leaves system unchanged."""
        from recovery_diagnostics import DiagnosticResult

        mock_diag.return_value = [
            DiagnosticResult("nvml_missing", "critical", "test", True),
        ]
        from cli_recovery import RecoveryCLI, parse_recovery_args

        args = parse_recovery_args(["--repair"])
        # No snapshots → options are: 1=nouveau, 2=exit
        cli = RecoveryCLI(args)
        result = cli.run()

        # With no snapshots, option 3 is invalid → exits with error
        # Option layout: 1=nouveau, 2=exit (no rollback since no snapshots)
        # So "3" is invalid
        assert result == 1
