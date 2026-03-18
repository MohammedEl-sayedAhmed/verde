"""OperationProgressPanel — multi-stage operation progress display."""

from __future__ import annotations

from gi.repository import Gtk


class OperationProgressPanel(Gtk.Box):
    """Multi-stage operation progress display.

    Shows stage description, progress bar, and stage count.
    Used inside the install dialog during active driver installation.
    """

    __gtype_name__ = "OperationProgressPanel"

    def __init__(self, **kwargs) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, **kwargs)
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        # Stage description label — accessible name updated on each stage change
        self._stage_label = Gtk.Label(label=_("Preparing\u2026"))
        self._stage_label.add_css_class("title-3")
        self._stage_label.set_halign(Gtk.Align.CENTER)
        self._stage_label.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Operation stage"), _("Current installation stage")],
        )
        self.append(self._stage_label)

        # Progress bar
        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(True)
        self.append(self._progress_bar)

        # Stage count label (e.g., "Step 2 of 4")
        self._stage_count_label = Gtk.Label(label="")
        self._stage_count_label.add_css_class("caption")
        self._stage_count_label.add_css_class("dim-label")
        self._stage_count_label.set_halign(Gtk.Align.CENTER)
        self.append(self._stage_count_label)

        # Result area — hidden by default
        self._result_icon = Gtk.Image()
        self._result_icon.set_pixel_size(48)
        self._result_icon.set_halign(Gtk.Align.CENTER)
        self._result_icon.set_visible(False)
        self.append(self._result_icon)

        self._result_label = Gtk.Label()
        self._result_label.set_wrap(True)
        self._result_label.set_halign(Gtk.Align.CENTER)
        self._result_label.set_visible(False)
        self.append(self._result_label)

        self._is_pulsing = False
        self._pulse_source_id: int | None = None

    def set_stage(self, name: str, fraction: float) -> None:
        """Update to a specific stage with progress fraction 0.0-1.0."""
        self._stop_pulse()
        self._stage_label.set_label(name)
        self._progress_bar.set_visible(True)
        self._progress_bar.set_fraction(max(0.0, min(1.0, fraction)))
        self._progress_bar.set_text(f"{fraction * 100:.0f}%")
        self._result_icon.set_visible(False)
        self._result_label.set_visible(False)

        # Update accessible description for live region
        self._stage_label.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Current stage: {}, {}% complete").format(name, f"{fraction * 100:.0f}")],
        )

    def set_stage_count(self, current: int, total: int) -> None:
        """Set the stage count display (e.g., 'Step 2 of 4')."""
        self._stage_count_label.set_label(_("Step {} of {}").format(current, total))

    def set_indeterminate(self) -> None:
        """Pulse progress bar for stages without measurable progress."""
        if self._is_pulsing:
            return
        self._is_pulsing = True
        self._progress_bar.set_text("")

        from gi.repository import GLib

        def _pulse() -> bool:
            if not self._is_pulsing:
                return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]
            self._progress_bar.pulse()
            return GLib.SOURCE_CONTINUE  # type: ignore[no-any-return]

        self._pulse_source_id = GLib.timeout_add(200, _pulse)

    def set_success(self, verification_text: str) -> None:
        """Show success state with verification results."""
        self._stop_pulse()
        self._stage_label.set_label(_("Installation Complete"))
        self._progress_bar.set_fraction(1.0)
        self._progress_bar.set_text("100%")

        self._result_icon.set_from_icon_name("emblem-ok-symbolic")
        self._result_icon.remove_css_class("verde-status-crit")
        self._result_icon.add_css_class("verde-status-good")
        self._result_icon.set_visible(True)

        self._result_label.set_label(verification_text)
        self._result_label.set_visible(True)

        self._stage_label.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Installation complete: {}").format(verification_text)],
        )

    def set_error(self, error_text: str) -> None:
        """Show error state with description."""
        self._stop_pulse()
        self._stage_label.set_label(_("Installation Failed"))
        self._progress_bar.set_visible(False)

        self._result_icon.set_from_icon_name("dialog-error-symbolic")
        for cls in ("verde-status-good",):
            self._result_icon.remove_css_class(cls)
        self._result_icon.add_css_class("verde-status-crit")
        self._result_icon.set_visible(True)

        self._result_label.set_label(error_text)
        self._result_label.set_visible(True)

        self._stage_label.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Installation failed: {}").format(error_text)],
        )

    def _stop_pulse(self) -> None:
        if self._is_pulsing:
            self._is_pulsing = False
            if self._pulse_source_id is not None:
                from gi.repository import GLib

                GLib.source_remove(self._pulse_source_id)
                self._pulse_source_id = None


# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]
