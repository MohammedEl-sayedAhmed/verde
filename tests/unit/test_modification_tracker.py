"""Unit tests for Story 6.3: System Modification Tracking."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from modification_tracker import (
    MOD_FILE_CREATED,
    MOD_FILE_MODIFIED,
    MOD_SERVICE_ENABLED,
    ModificationTracker,
)


@pytest.fixture
def tracker(tmp_path):
    """ModificationTracker with temp storage and mocked run."""
    calls = []

    def _mock_run(cmd, **kw):
        import subprocess

        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    t = ModificationTracker(base_dir=str(tmp_path), run=_mock_run)
    t._calls = calls  # expose for assertions
    return t


# ═══════════════════════════════════════════════════════════════════════
# Manifest persistence
# ═══════════════════════════════════════════════════════════════════════


class TestManifestPersistence:
    def test_record_creates_manifest_file(self, tracker, tmp_path):
        tracker.record("op1", MOD_SERVICE_ENABLED, "nvidia-suspend.service", "disabled", "Test")
        assert os.path.exists(os.path.join(str(tmp_path), "modifications.json"))

    def test_save_load_roundtrip(self, tracker):
        mod_id = tracker.record("op1", MOD_SERVICE_ENABLED, "svc", "disabled", "Test")
        mods = tracker.list_all()
        assert len(mods) == 1
        assert mods[0]["id"] == mod_id
        assert mods[0]["type"] == MOD_SERVICE_ENABLED
        assert mods[0]["target"] == "svc"
        assert mods[0]["active"] is True

    def test_record_returns_valid_uuid(self, tracker):
        mod_id = tracker.record("op1", MOD_SERVICE_ENABLED, "svc", "disabled", "Test")
        parsed = uuid.UUID(mod_id)
        assert str(parsed) == mod_id

    def test_multiple_records(self, tracker):
        tracker.record("op1", MOD_SERVICE_ENABLED, "svc1", "disabled", "Test1")
        tracker.record("op1", MOD_FILE_CREATED, "/etc/test.conf", None, "Test2")
        mods = tracker.list_all()
        assert len(mods) == 2

    def test_corrupt_manifest_resets(self, tmp_path):
        manifest = os.path.join(str(tmp_path), "modifications.json")
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(manifest, "w") as f:
            f.write("not valid json {{{")

        t = ModificationTracker(base_dir=str(tmp_path))
        mods = t.list_all()
        assert mods == []

    def test_wrong_version_resets(self, tmp_path):
        manifest = os.path.join(str(tmp_path), "modifications.json")
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(manifest, "w") as f:
            json.dump({"version": 999, "modifications": []}, f)

        t = ModificationTracker(base_dir=str(tmp_path))
        mods = t.list_all()
        assert mods == []


# ═══════════════════════════════════════════════════════════════════════
# List active / list all
# ═══════════════════════════════════════════════════════════════════════


class TestListMethods:
    def test_list_active_returns_only_active(self, tracker):
        id1 = tracker.record("op1", MOD_SERVICE_ENABLED, "svc1", "disabled", "T1")
        id2 = tracker.record("op1", MOD_FILE_CREATED, "f1", None, "T2")
        tracker.mark_inactive(id1)

        active = tracker.list_active()
        assert len(active) == 1
        assert active[0]["id"] == id2

    def test_list_all_returns_everything(self, tracker):
        tracker.record("op1", MOD_SERVICE_ENABLED, "svc1", "disabled", "T1")
        tracker.record("op1", MOD_FILE_CREATED, "f1", None, "T2")
        all_mods = tracker.list_all()
        assert len(all_mods) == 2

    def test_list_active_newest_first(self, tracker):
        tracker.record("op1", MOD_SERVICE_ENABLED, "svc1", "disabled", "First")
        tracker.record("op2", MOD_FILE_CREATED, "f1", None, "Second")
        active = tracker.list_active()
        assert active[0]["description"] == "Second"


# ═══════════════════════════════════════════════════════════════════════
# Revert
# ═══════════════════════════════════════════════════════════════════════


class TestRevert:
    def test_revert_marks_inactive(self, tracker):
        mod_id = tracker.record(
            "op1", MOD_SERVICE_ENABLED, "nvidia-suspend.service", "disabled", "T"
        )
        success = tracker.revert(mod_id)
        assert success is True
        active = tracker.list_active()
        assert len(active) == 0

    def test_revert_unknown_returns_false(self, tracker):
        success = tracker.revert("nonexistent-id")
        assert success is False

    def test_revert_already_inactive_returns_false(self, tracker):
        mod_id = tracker.record("op1", MOD_SERVICE_ENABLED, "svc", "disabled", "T")
        tracker.mark_inactive(mod_id)
        success = tracker.revert(mod_id)
        assert success is False

    def test_revert_service_enabled_calls_disable(self, tracker):
        mod_id = tracker.record(
            "op1", MOD_SERVICE_ENABLED, "nvidia-suspend.service", "disabled", "T"
        )
        tracker.revert(mod_id)
        assert any("disable" in c for c in tracker._calls)

    def test_revert_file_created_removes_file(self, tmp_path):
        test_file = tmp_path / "test.conf"
        test_file.write_text("content")

        t = ModificationTracker(base_dir=str(tmp_path))
        mod_id = t.record("op1", MOD_FILE_CREATED, str(test_file), None, "T")
        t.revert(mod_id)
        assert not test_file.exists()

    def test_revert_file_modified_restores_content(self, tmp_path):
        test_file = tmp_path / "test.conf"
        test_file.write_text("modified content")

        t = ModificationTracker(base_dir=str(tmp_path))
        mod_id = t.record("op1", MOD_FILE_MODIFIED, str(test_file), "original content", "T")
        t.revert(mod_id)
        assert test_file.read_text() == "original content"


# ═══════════════════════════════════════════════════════════════════════
# Mark inactive
# ═══════════════════════════════════════════════════════════════════════


class TestMarkInactive:
    def test_mark_inactive_without_revert(self, tracker):
        mod_id = tracker.record("op1", MOD_SERVICE_ENABLED, "svc", "disabled", "T")
        success = tracker.mark_inactive(mod_id)
        assert success is True
        active = tracker.list_active()
        assert len(active) == 0
        # No subprocess calls should have been made for revert
        assert len(tracker._calls) == 0

    def test_mark_inactive_unknown_returns_false(self, tracker):
        success = tracker.mark_inactive("nonexistent-id")
        assert success is False


# ═══════════════════════════════════════════════════════════════════════
# Atomic writes
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWrites:
    def test_no_tmp_file_left_after_save(self, tracker, tmp_path):
        tracker.record("op1", MOD_SERVICE_ENABLED, "svc", "disabled", "T")
        tmp_files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]
        assert len(tmp_files) == 0

    def test_manifest_valid_json_after_save(self, tracker, tmp_path):
        tracker.record("op1", MOD_SERVICE_ENABLED, "svc", "disabled", "T")
        manifest = os.path.join(str(tmp_path), "modifications.json")
        with open(manifest) as f:
            data = json.load(f)
        assert data["version"] == 1
        assert len(data["modifications"]) == 1


# ═══════════════════════════════════════════════════════════════════════
# Entry fields
# ═══════════════════════════════════════════════════════════════════════


class TestEntryFields:
    def test_entry_has_required_fields(self, tracker):
        tracker.record(
            "op_test", MOD_SERVICE_ENABLED, "nvidia-suspend.service", "disabled", "Test mod"
        )
        mod = tracker.list_all()[0]
        assert "id" in mod
        assert "operation_id" in mod
        assert "timestamp" in mod
        assert "type" in mod
        assert "target" in mod
        assert "original_state" in mod
        assert "description" in mod
        assert "active" in mod
        assert mod["operation_id"] == "op_test"
        assert mod["type"] == MOD_SERVICE_ENABLED
        assert mod["target"] == "nvidia-suspend.service"
        assert mod["original_state"] == "disabled"

    def test_null_original_state_for_file_created(self, tracker):
        tracker.record("op1", MOD_FILE_CREATED, "/etc/test.conf", None, "New file")
        mod = tracker.list_all()[0]
        assert mod["original_state"] is None


# ═══════════════════════════════════════════════════════════════════════
# D-Bus wiring
# ═══════════════════════════════════════════════════════════════════════


class TestDBusWiring:
    def test_service_has_modification_tracker(self):
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
        assert hasattr(svc, "_modification_tracker")
        assert hasattr(svc, "_dispatch_list_modifications")
        assert hasattr(svc, "_dispatch_revert_modification")

    def test_xml_has_modification_methods(self):
        import pathlib

        xml_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        xml = xml_path.read_text()
        assert "ListModifications" in xml
        assert "RevertModification" in xml

    def test_polkit_maps_revert(self):
        from polkit import METHOD_ACTION_MAP

        assert "RevertModification" in METHOD_ACTION_MAP

    def test_validate_modification_id(self):
        from validators import validate_modification_id

        # Valid UUID
        validate_modification_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        # Invalid
        with pytest.raises(ValueError, match="Invalid modification ID"):
            validate_modification_id("not-a-uuid")

    def test_validate_modification_id_null_byte(self):
        from validators import validate_modification_id

        with pytest.raises(ValueError, match="invalid characters"):
            validate_modification_id("a1b2c3d4-e5f6-7890-abcd-ef123456\x007890")
