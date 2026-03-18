"""StatusIndicator — color-coded status label for dashboard stat rows."""

from __future__ import annotations

from gi.repository import Gtk

_CSS_CLASSES: dict[str, str] = {
    "good": "verde-status-good",
    "warn": "verde-status-warn",
    "crit": "verde-status-crit",
    "unknown": "dim-label",
}

_STATUS_NAMES: dict[str, str] = {
    "good": "safe range",
    "warn": "warning",
    "crit": "critical",
    "unknown": "unknown",
}


class StatusIndicator(Gtk.Label):
    """Color-coded status label with accessible description.

    Always displays text content — never an empty or color-only state.
    """

    __gtype_name__ = "StatusIndicator"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_label("—")
        self.add_css_class("dim-label")

    def set_status(self, value_text: str, level: str) -> None:
        """Set display text and color-coded CSS class.

        Parameters
        ----------
        value_text : str
            Human-readable status text (e.g. "Safe", "Warning", "Critical").
        level : str
            One of ``"good"``, ``"warn"``, ``"crit"``, ``"unknown"``.
        """
        for css_class in _CSS_CLASSES.values():
            self.remove_css_class(css_class)
        self.add_css_class(_CSS_CLASSES.get(level, "dim-label"))
        self.set_label(value_text)
        self.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [f"{value_text}, {_STATUS_NAMES.get(level, 'unknown')}"],
        )

    def set_status_from_thresholds(
        self,
        value: float,
        warn_threshold: float,
        crit_threshold: float,
    ) -> None:
        """Determine level from numeric value and set status text.

        Parameters
        ----------
        value : float
            The current numeric value.
        warn_threshold : float
            Values at or above this are ``"warn"``.
        crit_threshold : float
            Values at or above this are ``"crit"``.
        """
        if value >= crit_threshold:
            level = "crit"
        elif value >= warn_threshold:
            level = "warn"
        else:
            level = "good"

        level_labels = {"good": "Safe", "warn": "Warning", "crit": "Critical"}
        self.set_status(level_labels[level], level)
