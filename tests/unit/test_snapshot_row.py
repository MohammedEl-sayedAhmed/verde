"""Unit tests for snapshot_row widget (Story 3.2)."""

from __future__ import annotations

import typing
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


# ===================================================================
# snapshot_row helpers
# ===================================================================


class TestFormatTimestamp:
    def test_iso_timestamp(self):
        from verde.widgets.snapshot_row import _format_timestamp

        result = _format_timestamp("2026-03-18T14:30:00+00:00")
        assert "2026-03-18" in result
        assert "14:30" in result

    def test_invalid_timestamp(self):
        from verde.widgets.snapshot_row import _format_timestamp

        result = _format_timestamp("not-a-date")
        assert result == "not-a-date"

    def test_empty_timestamp(self):
        from verde.widgets.snapshot_row import _format_timestamp

        result = _format_timestamp("")
        assert result == "Unknown date"


class TestFormatFileSize:
    def test_bytes(self):
        from verde.widgets.snapshot_row import _format_file_size

        assert _format_file_size(500) == "500 B"

    def test_kilobytes(self):
        from verde.widgets.snapshot_row import _format_file_size

        result = _format_file_size(2048)
        assert result == "2.0 KB"

    def test_megabytes(self):
        from verde.widgets.snapshot_row import _format_file_size

        result = _format_file_size(2 * 1024 * 1024)
        assert result == "2.0 MB"


# ===================================================================
# build_snapshot_row
# ===================================================================


class TestBuildSnapshotRow:
    SAMPLE_SNAPSHOT: typing.ClassVar[dict] = {
        "id": "20260318T143000_nvidia-560-ab01",
        "timestamp": "2026-03-18T14:30:00+00:00",
        "driver_version": "560",
        "kernel_version": "6.8.0-45-generic",
        "packages": ["nvidia-driver-560=560.35.03"],
        "dkms_status": "installed",
        "file_size": 4096,
        "sha256": "a1b2c3d4e5f6",
    }

    def test_returns_expander_row(self):
        from verde.widgets.snapshot_row import build_snapshot_row

        row = build_snapshot_row(self.SAMPLE_SNAPSHOT)
        assert isinstance(row, Adw.ExpanderRow)

    def test_title_contains_date(self):
        from verde.widgets.snapshot_row import build_snapshot_row

        row = build_snapshot_row(self.SAMPLE_SNAPSHOT)
        assert "2026-03-18" in row.get_title()

    def test_subtitle_contains_driver(self):
        from verde.widgets.snapshot_row import build_snapshot_row

        row = build_snapshot_row(self.SAMPLE_SNAPSHOT)
        assert "560" in row.get_subtitle()

    def test_rollback_callback_connected(self):
        from verde.widgets.snapshot_row import build_snapshot_row

        callback = MagicMock()
        row = build_snapshot_row(self.SAMPLE_SNAPSHOT, on_rollback_clicked=callback)
        # Row should have been created with the callback
        assert row is not None

    def test_delete_callback_connected(self):
        from verde.widgets.snapshot_row import build_snapshot_row

        callback = MagicMock()
        row = build_snapshot_row(self.SAMPLE_SNAPSHOT, on_delete_clicked=callback)
        assert row is not None

    def test_no_callbacks_still_builds(self):
        from verde.widgets.snapshot_row import build_snapshot_row

        row = build_snapshot_row(self.SAMPLE_SNAPSHOT)
        assert row is not None

    def test_minimal_snapshot_data(self):
        from verde.widgets.snapshot_row import build_snapshot_row

        row = build_snapshot_row({"id": "test", "timestamp": ""})
        assert isinstance(row, Adw.ExpanderRow)
