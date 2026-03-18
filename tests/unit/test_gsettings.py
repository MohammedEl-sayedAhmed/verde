"""Unit tests for GSettings schema (Story 1.7)."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
from gi.repository import Gio, GLib

_SCHEMA_SRC = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.app.gschema.xml"


@pytest.fixture
def settings(tmp_path):
    """Compile GSettings schema in a temp dir and return a Settings instance."""
    shutil.copy(_SCHEMA_SRC, tmp_path)
    subprocess.run(
        ["glib-compile-schemas", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    # Point GSettings at the temp schema directory
    GLib.setenv("GSETTINGS_SCHEMA_DIR", str(tmp_path), True)
    # Use the env-override backend so we don't touch dconf
    GLib.setenv("GSETTINGS_BACKEND", "memory", True)
    schema_source = Gio.SettingsSchemaSource.new_from_directory(
        str(tmp_path), Gio.SettingsSchemaSource.get_default(), False
    )
    schema = schema_source.lookup("com.verde.app", True)
    assert schema is not None, "Schema com.verde.app not found after compilation"
    return Gio.Settings.new_full(schema, None, None)


class TestPollingInterval:
    def test_default_is_2000(self, settings):
        assert settings.get_int("polling-interval") == 2000

    def test_set_and_get_round_trip(self, settings):
        settings.set_int("polling-interval", 5000)
        assert settings.get_int("polling-interval") == 5000

    def test_range_min_500(self, settings):
        """Values below 500 are clamped or rejected by schema range."""
        range_val = settings.get_range("polling-interval")
        range_type = range_val.get_child_value(0).get_string()
        assert range_type == "range"
        bounds = range_val.get_child_value(1).get_variant()
        low = bounds.get_child_value(0).get_int32()
        assert low == 500

    def test_range_max_60000(self, settings):
        range_val = settings.get_range("polling-interval")
        bounds = range_val.get_child_value(1).get_variant()
        high = bounds.get_child_value(1).get_int32()
        assert high == 60000


class TestWindowSize:
    def test_default_is_800_600(self, settings):
        w, h = settings.get_value("window-size").unpack()
        assert (w, h) == (800, 600)

    def test_set_and_get_round_trip(self, settings):
        settings.set_value("window-size", GLib.Variant("(ii)", (1024, 768)))
        w, h = settings.get_value("window-size").unpack()
        assert (w, h) == (1024, 768)
