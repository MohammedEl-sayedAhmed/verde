"""Unit tests for Story 6.1: External Change Detection & Config Integrity."""

from __future__ import annotations

import json
import os

import pytest
from state_tracker import StateTracker, file_sha256

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def state_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def tracker(state_dir):
    """StateTracker with mocked run and read_file."""

    def _mock_run(cmd, **kw):
        import subprocess

        if cmd[0] == "lsmod":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="Module  Size  Used by\nnvidia  56692736  1143\nnvidia_modeset  1236992  13\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

    def _mock_read(path):
        if path == "/sys/module/nvidia/version":
            return "560.35.03\n"
        return ""

    return StateTracker(state_dir=state_dir, run=_mock_run, read_file=_mock_read)


# ═══════════════════════════════════════════════════════════════════════
# Task 1: State snapshot persistence
# ═══════════════════════════════════════════════════════════════════════


class TestStatePersistence:
    def test_save_creates_state_file(self, tracker, state_dir):
        tracker.save_current_state()
        state_file = os.path.join(state_dir, "last_state.json")
        assert os.path.exists(state_file)

    def test_save_load_roundtrip(self, tracker, state_dir):
        tracker.save_current_state()
        loaded = tracker.load_previous_state()
        assert loaded is not None
        assert loaded["version"] == 1
        assert loaded["driver_version"] == "560.35.03"
        assert loaded["driver_type"] == "proprietary"
        assert "kernel_version" in loaded
        assert "captured_at" in loaded

    def test_load_missing_file_returns_none(self, tracker):
        result = tracker.load_previous_state()
        assert result is None

    def test_load_corrupted_file_returns_none(self, state_dir):
        state_file = os.path.join(state_dir, "last_state.json")
        os.makedirs(state_dir, exist_ok=True)
        with open(state_file, "w") as f:
            f.write("not valid json {{{")
        tracker = StateTracker(state_dir=state_dir)
        result = tracker.load_previous_state()
        assert result is None

    def test_load_wrong_version_returns_none(self, state_dir):
        state_file = os.path.join(state_dir, "last_state.json")
        os.makedirs(state_dir, exist_ok=True)
        with open(state_file, "w") as f:
            json.dump({"version": 999, "driver_version": "560"}, f)
        tracker = StateTracker(state_dir=state_dir)
        result = tracker.load_previous_state()
        assert result is None

    def test_managed_configs_in_snapshot(self, tracker, state_dir):
        tracker.save_current_state()
        loaded = tracker.load_previous_state()
        assert "managed_configs" in loaded
        assert isinstance(loaded["managed_configs"], list)


# ═══════════════════════════════════════════════════════════════════════
# Task 2: External change detection
# ═══════════════════════════════════════════════════════════════════════


class TestExternalChangeDetection:
    def test_no_previous_state_returns_empty(self, tracker):
        changes = tracker.detect_external_changes()
        assert changes == []

    def test_no_changes_returns_empty(self, tracker, state_dir):
        tracker.save_current_state()
        tracker.load_previous_state()
        changes = tracker.detect_external_changes()
        assert changes == []

    def test_driver_version_change_detected(self, state_dir):
        """Detect when driver version changes externally."""
        import subprocess

        # Save state with version 560
        def _run_560(cmd, **kw):
            if cmd[0] == "lsmod":
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="Module  Size  Used by\nnvidia  56692736  1143\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

        t = StateTracker(
            state_dir=state_dir,
            run=_run_560,
            read_file=lambda p: "560.35.03\n" if "version" in p else "",
        )
        t.save_current_state()

        # Now simulate version change to 565
        t2 = StateTracker(
            state_dir=state_dir,
            run=_run_560,
            read_file=lambda p: "565.77.01\n" if "version" in p else "",
        )
        t2.load_previous_state()
        changes = t2.detect_external_changes()
        assert len(changes) == 1
        assert changes[0]["change_type"] == "driver_version"
        assert changes[0]["old_value"] == "560.35.03"
        assert changes[0]["new_value"] == "565.77.01"

    def test_driver_type_change_detected(self, state_dir):
        """Detect when driver type changes (proprietary → nouveau)."""
        import subprocess

        def _run_nvidia(cmd, **kw):
            if cmd[0] == "lsmod":
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="Module  Size  Used by\nnvidia  56692736  1143\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

        def _run_nouveau(cmd, **kw):
            if cmd[0] == "lsmod":
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="Module  Size  Used by\nnouveau  1234567  2\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

        t = StateTracker(
            state_dir=state_dir,
            run=_run_nvidia,
            read_file=lambda p: "560\n" if "version" in p else "",
        )
        t.save_current_state()

        t2 = StateTracker(state_dir=state_dir, run=_run_nouveau, read_file=lambda p: "")
        t2.load_previous_state()
        changes = t2.detect_external_changes()
        type_changes = [c for c in changes if c["change_type"] == "driver_type"]
        assert len(type_changes) == 1
        assert type_changes[0]["old_value"] == "proprietary"
        assert type_changes[0]["new_value"] == "nouveau"

    def test_kernel_version_change_detected(self, tracker, state_dir):
        """Detect kernel version changes (simulated via previous state)."""
        # Save current state
        tracker.save_current_state()
        # Manually modify the state file to have a different kernel
        state_file = os.path.join(state_dir, "last_state.json")
        with open(state_file) as f:
            data = json.load(f)
        data["kernel_version"] = "5.14.0-old-kernel"
        with open(state_file, "w") as f:
            json.dump(data, f)

        tracker.load_previous_state()
        changes = tracker.detect_external_changes()
        kernel_changes = [c for c in changes if c["change_type"] == "kernel_version"]
        assert len(kernel_changes) == 1
        assert kernel_changes[0]["old_value"] == "5.14.0-old-kernel"

    def test_first_run_no_detection(self, tracker):
        """First run (no previous state) should return empty list."""
        # Don't save — simulates first run
        assert tracker.load_previous_state() is None
        changes = tracker.detect_external_changes()
        assert changes == []


# ═══════════════════════════════════════════════════════════════════════
# Task 3: Config integrity validation
# ═══════════════════════════════════════════════════════════════════════


class TestConfigIntegrity:
    def test_no_previous_state_returns_empty(self, tracker):
        issues = tracker.validate_config_integrity()
        assert issues == []

    def test_all_files_intact_returns_empty(self, tracker, state_dir, tmp_path):
        """When tracked files match their hashes, no issues."""
        # Create a fake managed config
        config_file = tmp_path / "test.conf"
        config_file.write_text("options nvidia NVreg_PreserveVideoMemoryAllocations=1\n")
        file_hash = file_sha256(str(config_file))

        # Build a previous state that tracks this file
        state = {
            "version": 1,
            "captured_at": "2026-03-20T10:00:00+00:00",
            "driver_version": "560",
            "driver_type": "proprietary",
            "kernel_version": "5.15.0",
            "managed_configs": [
                {"path": str(config_file), "sha256": file_hash, "exists": True},
            ],
        }
        state_file = os.path.join(state_dir, "last_state.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Create tracker that tracks our custom path
        from unittest.mock import patch

        with patch("state_tracker.MANAGED_CONFIGS", [str(config_file)]):
            t = StateTracker(state_dir=state_dir)
            t.load_previous_state()
            issues = t.validate_config_integrity()
            assert issues == []

    def test_hash_mismatch_detected(self, state_dir, tmp_path):
        """Detect when a managed config file is modified externally."""
        from unittest.mock import patch

        config_file = tmp_path / "test.conf"
        config_file.write_text("original content\n")
        original_hash = file_sha256(str(config_file))

        state = {
            "version": 1,
            "captured_at": "2026-03-20T10:00:00+00:00",
            "driver_version": "560",
            "driver_type": "proprietary",
            "kernel_version": "5.15.0",
            "managed_configs": [
                {"path": str(config_file), "sha256": original_hash, "exists": True},
            ],
        }
        state_file = os.path.join(state_dir, "last_state.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Modify the file externally
        config_file.write_text("modified content\n")

        with patch("state_tracker.MANAGED_CONFIGS", [str(config_file)]):
            t = StateTracker(state_dir=state_dir)
            t.load_previous_state()
            issues = t.validate_config_integrity()
            assert len(issues) == 1
            assert issues[0]["issue_type"] == "modified"
            assert issues[0]["expected_hash"] == original_hash

    def test_deleted_file_detected(self, state_dir, tmp_path):
        """Detect when a managed config file is deleted."""
        from unittest.mock import patch

        config_path = str(tmp_path / "deleted.conf")

        state = {
            "version": 1,
            "captured_at": "2026-03-20T10:00:00+00:00",
            "driver_version": "560",
            "driver_type": "proprietary",
            "kernel_version": "5.15.0",
            "managed_configs": [
                {"path": config_path, "sha256": "abc123", "exists": True},
            ],
        }
        state_file = os.path.join(state_dir, "last_state.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        with patch("state_tracker.MANAGED_CONFIGS", [config_path]):
            t = StateTracker(state_dir=state_dir)
            t.load_previous_state()
            issues = t.validate_config_integrity()
            assert len(issues) == 1
            assert issues[0]["issue_type"] == "deleted"


# ═══════════════════════════════════════════════════════════════════════
# File hash helper
# ═══════════════════════════════════════════════════════════════════════


class TestFileHash:
    def test_hash_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        h = file_sha256(str(f))
        assert h is not None
        assert len(h) == 64  # SHA-256 hex digest

    def test_hash_missing_file(self):
        assert file_sha256("/nonexistent/path/file.txt") is None

    def test_hash_deterministic(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("deterministic content\n")
        h1 = file_sha256(str(f))
        h2 = file_sha256(str(f))
        assert h1 == h2


# ═══════════════════════════════════════════════════════════════════════
# D-Bus signal and service wiring tests
# ═══════════════════════════════════════════════════════════════════════


class TestDBusWiring:
    def test_service_has_state_tracker(self):
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
        assert hasattr(svc, "_state_tracker")
        assert hasattr(svc, "_run_startup_detection")

    def test_xml_has_external_changes_signal(self):
        import pathlib

        xml_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        xml = xml_path.read_text()
        assert "ExternalChangesDetected" in xml

    def test_dbus_client_has_signal_handler(self):
        """VerdeDBusClient handles ExternalChangesDetected signal."""
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2] / "src" / "verde" / "dbus_client.py"
        ).read_text()
        assert "ExternalChangesDetected" in src
        assert "external-changes-detected" in src
