"""UI smoke tests for navigation shell (Story 1.8)."""

from __future__ import annotations

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from verde.views.dashboard import DashboardPage  # noqa: E402
from verde.views.diagnostics import DiagnosticsPage  # noqa: E402
from verde.views.drivers import DriversPage  # noqa: E402
from verde.views.power import PowerPage  # noqa: E402
from verde.window import VerdeApplication, VerdeWindow  # noqa: E402

# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    """Ensure GTK and Adw are initialised once for the whole module."""
    Adw.init()


@pytest.fixture
def app():
    application = VerdeApplication(
        application_id="com.verde.app.test",
        version="0.0.0-test",
    )
    return application


@pytest.fixture
def window(app):
    win = VerdeWindow(application=app)
    return win


# ===================================================================
# Window creation
# ===================================================================


class TestWindowCreation:
    def test_window_is_verde_window(self, window):
        assert isinstance(window, VerdeWindow)

    def test_window_is_adw_application_window(self, window):
        assert isinstance(window, Adw.ApplicationWindow)


# ===================================================================
# ViewStack pages
# ===================================================================

EXPECTED_PAGES = ["dashboard", "drivers", "power", "diagnostics"]


class TestViewStackPages:
    def test_view_stack_has_four_pages(self, window):
        names = []
        for i in range(window.view_stack.get_pages().get_n_items()):
            item = window.view_stack.get_pages().get_item(i)
            names.append(item.get_name())
        assert len(names) == 4

    def test_view_stack_page_names(self, window):
        names = []
        for i in range(window.view_stack.get_pages().get_n_items()):
            item = window.view_stack.get_pages().get_item(i)
            names.append(item.get_name())
        assert names == EXPECTED_PAGES

    def test_dashboard_page_type(self, window):
        child = window.view_stack.get_child_by_name("dashboard")
        assert isinstance(child, DashboardPage)

    def test_drivers_page_type(self, window):
        child = window.view_stack.get_child_by_name("drivers")
        assert isinstance(child, DriversPage)

    def test_power_page_type(self, window):
        child = window.view_stack.get_child_by_name("power")
        assert isinstance(child, PowerPage)

    def test_diagnostics_page_type(self, window):
        child = window.view_stack.get_child_by_name("diagnostics")
        assert isinstance(child, DiagnosticsPage)


# ===================================================================
# ViewSwitcher / ViewSwitcherBar wiring
# ===================================================================


class TestViewSwitcherWiring:
    def test_view_switcher_connected_to_stack(self, window):
        assert window.view_switcher.get_stack() is window.view_stack

    def test_bottom_bar_connected_to_stack(self, window):
        assert window.bottom_bar.get_stack() is window.view_stack


# ===================================================================
# Banner
# ===================================================================


class TestBanner:
    def test_banner_exists(self, window):
        assert hasattr(window, "banner")
        assert isinstance(window.banner, Adw.Banner)

    def test_banner_hidden_by_default(self, window):
        assert window.banner.get_revealed() is False


# ===================================================================
# No text input fields (UX-DR21)
# ===================================================================


def _find_widgets_of_type(widget, widget_type):
    """Recursively find all widgets of a given type in the tree."""
    found = []
    if isinstance(widget, widget_type):
        found.append(widget)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_find_widgets_of_type(child, widget_type))
        child = child.get_next_sibling()
    return found


def _is_descendant_of(widget, ancestor_type):
    """Check if a widget is a descendant of a widget of the given type."""
    parent = widget.get_parent()
    while parent is not None:
        if isinstance(parent, ancestor_type):
            return True
        parent = parent.get_parent()
    return False


class TestNoTextInput:
    def test_no_gtk_entry(self, window):
        entries = _find_widgets_of_type(window, Gtk.Entry)
        assert entries == [], f"Found Gtk.Entry widgets: {entries}"

    def test_no_gtk_search_entry(self, window):
        entries = _find_widgets_of_type(window, Gtk.SearchEntry)
        # Exclude SearchEntry widgets that are internal children of Gtk.DropDown
        # (GTK4 DropDown always creates an internal SearchEntry even with search disabled)
        non_dropdown = [e for e in entries if not _is_descendant_of(e, Gtk.DropDown)]
        assert non_dropdown == [], f"Found Gtk.SearchEntry widgets: {non_dropdown}"
