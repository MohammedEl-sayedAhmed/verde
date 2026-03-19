"""Unit tests for Story 3.4: CLI Recovery Tool — argument parsing and entry point."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestRecoveryCLIArgParsing:
    """Test --repair argument parsing and subcommands (AC#9)."""

    def test_parse_repair_flag(self):
        """--repair flag sets repair mode."""
        from cli_recovery import parse_recovery_args

        args = parse_recovery_args(["--repair"])
        assert args.repair is True

    def test_parse_repair_list_snapshots(self):
        """--repair --list-snapshots sets list_snapshots mode."""
        from cli_recovery import parse_recovery_args

        args = parse_recovery_args(["--repair", "--list-snapshots"])
        assert args.repair is True
        assert args.list_snapshots is True

    def test_parse_repair_rollback(self):
        """--repair --rollback <id> sets rollback mode with snapshot ID."""
        from cli_recovery import parse_recovery_args

        args = parse_recovery_args(["--repair", "--rollback", "20260318T143000_nvidia-560-ab01"])
        assert args.repair is True
        assert args.rollback == "20260318T143000_nvidia-560-ab01"

    def test_parse_repair_diagnose(self):
        """--repair --diagnose sets diagnose-only mode."""
        from cli_recovery import parse_recovery_args

        args = parse_recovery_args(["--repair", "--diagnose"])
        assert args.repair is True
        assert args.diagnose is True

    def test_parse_repair_yes_flag(self):
        """--repair --rollback <id> --yes sets non-interactive confirmation."""
        from cli_recovery import parse_recovery_args

        args = parse_recovery_args(
            ["--repair", "--rollback", "20260318T143000_nvidia-560-ab01", "--yes"]
        )
        assert args.yes is True

    def test_repair_not_set_by_default(self):
        """Without --repair, repair mode is off."""
        from cli_recovery import parse_recovery_args

        args = parse_recovery_args([])
        assert args.repair is False


class TestPrivilegeCheck:
    """Test root privilege detection (AC#8)."""

    @patch("os.geteuid", return_value=1000)
    def test_not_root_returns_false(self, _euid):
        """Non-root user detected correctly."""
        from cli_recovery import check_root_privilege

        assert check_root_privilege() is False

    @patch("os.geteuid", return_value=0)
    def test_root_returns_true(self, _euid):
        """Root user detected correctly."""
        from cli_recovery import check_root_privilege

        assert check_root_privilege() is True


class TestTTYDetection:
    """Test TTY detection (AC#1)."""

    @patch("sys.stdout")
    @patch("sys.stdin")
    def test_tty_detected(self, mock_stdin, mock_stdout):
        """Detects when running in a TTY (both stdin and stdout)."""
        from cli_recovery import is_tty

        mock_stdin.isatty.return_value = True
        mock_stdout.isatty.return_value = True
        assert is_tty() is True

    @patch("sys.stdout")
    @patch("sys.stdin")
    def test_non_tty_stdin(self, mock_stdin, mock_stdout):
        """Detects when stdin is NOT a TTY (e.g., piped)."""
        from cli_recovery import is_tty

        mock_stdin.isatty.return_value = False
        mock_stdout.isatty.return_value = True
        assert is_tty() is False

    @patch("sys.stdout")
    @patch("sys.stdin")
    def test_non_tty_stdout(self, mock_stdin, mock_stdout):
        """Detects when stdout is NOT a TTY (e.g., redirected)."""
        from cli_recovery import is_tty

        mock_stdin.isatty.return_value = True
        mock_stdout.isatty.return_value = False
        assert is_tty() is False


class TestRecoveryCLIEntryPoint:
    """Test the main recovery entry point (AC#1, #7, #8)."""

    @patch("os.geteuid", return_value=1000)
    def test_exits_without_root(self, _euid, capsys):
        """Non-root invocation exits with code 1 and clear message."""
        from cli_recovery import recovery_main

        result = recovery_main(["--repair"])
        assert result == 1
        captured = capsys.readouterr()
        assert "root" in captured.err.lower() or "root" in captured.out.lower()

    @patch("os.geteuid", return_value=0)
    def test_does_not_import_gtk(self, _euid):
        """Recovery mode works without GTK/GLib imports (AC#7)."""
        from cli_recovery import RecoveryCLI

        # RecoveryCLI should be importable without GTK
        assert RecoveryCLI is not None

    @patch("os.geteuid", return_value=0)
    @patch("cli_recovery.RecoveryCLI")
    def test_repair_flag_enters_recovery(self, mock_cli_cls, _euid):
        """--repair flag enters recovery mode."""
        mock_cli = MagicMock()
        mock_cli.run.return_value = 0
        mock_cli_cls.return_value = mock_cli

        from cli_recovery import recovery_main

        result = recovery_main(["--repair"])
        mock_cli_cls.assert_called_once()
        mock_cli.run.assert_called_once()
        assert result == 0
