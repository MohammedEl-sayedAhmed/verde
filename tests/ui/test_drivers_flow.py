"""GUI smoke tests for driver install flow (Story 2.4)."""

from __future__ import annotations

from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402

from verde.gpu_state import GPUState  # noqa: E402
from verde.views.drivers import DriversPage  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


@pytest.fixture
def gpu_state():
    return GPUState()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_property.return_value = True
    client.connect.return_value = 1
    client.call_method_async.return_value = None
    return client


@pytest.fixture
def page(gpu_state, mock_client):
    page = DriversPage()
    page.bind_state(gpu_state, mock_client)
    return page


class TestDriversViewRenders:
    """Smoke tests: Drivers view renders with mock data."""

    def test_page_renders_without_error(self, page):
        assert page.get_title() == "Drivers"

    def test_current_driver_group_visible_when_connected(self, page):
        assert page._current_driver_group.get_visible() is True

    def test_available_drivers_group_visible_when_connected(self, page):
        assert page._available_drivers_group.get_visible() is True

    def test_snapshots_group_visible_when_connected(self, page):
        assert page._snapshots_group.get_visible() is True

    def test_unreachable_hidden_when_connected(self, page):
        assert page._unreachable_group.get_visible() is False


class TestDriversViewEmptyStates:
    """Smoke tests: empty states display correctly."""

    def test_no_driver_state(self, page):
        page._populate_current_driver({"version": ""})
        assert page._no_driver_status.get_visible() is True
        assert page._current_driver_expander.get_visible() is False

    def test_no_available_drivers(self, page):
        page._populate_available_drivers([])
        assert page._no_drivers_status.get_visible() is True

    def test_no_snapshots(self, page):
        page._populate_snapshots([])
        assert page._no_snapshots_status.get_visible() is True


class TestDriversViewWithData:
    """Smoke tests: data population works correctly."""

    def test_driver_rows_created(self, page):
        drivers = [
            {
                "package": "nvidia-driver-565",
                "version": "565.57",
                "variant": "proprietary",
                "recommended": True,
            },
            {
                "package": "nvidia-driver-550",
                "version": "550.40",
                "variant": "open",
                "recommended": False,
            },
        ]
        page._populate_available_drivers(drivers)
        assert len(page._driver_rows) == 2

    def test_current_driver_populated(self, page):
        page._populate_current_driver(
            {
                "version": "565.57",
                "package": "nvidia-driver-565",
                "variant": "proprietary",
                "cuda_version": "12.7",
            }
        )
        assert page._current_driver_expander.get_visible() is True
        assert "nvidia-driver-565" in page._current_driver_expander.get_title()


class TestDriversFlowDaemonUnavailable:
    """Smoke test: daemon unavailable state."""

    def test_disconnected_shows_unreachable(self, gpu_state, mock_client):
        mock_client.get_property.return_value = False
        page = DriversPage()
        page.bind_state(gpu_state, mock_client)
        assert page._unreachable_group.get_visible() is True
        assert page._current_driver_group.get_visible() is False
