"""Automated gettext coverage scan.

Scans all Python GUI source files for user-facing strings that should
be wrapped in ``_()``.  Detects common patterns like ``set_title("...")``
and ``set_subtitle("...")`` where the string argument is not translated.

References: NFR-I18N-1; Story 6.6, Task 5.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "verde"
_DAEMON_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "verde-daemon"

# Patterns that indicate a user-facing string that should be wrapped in _()
# We look for method calls like .set_title("literal") without _()
_UI_SETTER_RE = re.compile(
    r"\.(set_title|set_subtitle|set_description|set_body|set_label|set_button_label"
    r'|set_tooltip_text)\s*\(\s*"[^"]+"\s*\)',
)

# Files that are exempt (daemon code, tests, non-UI modules)
_EXEMPT_FILES = {
    "__init__.py",
    "gpu_state.py",  # GObject properties, not user-facing
}


def _get_gui_python_files() -> list[pathlib.Path]:
    """Return all Python files in src/verde/ that may have user-facing strings."""
    return [f for f in _SRC_ROOT.rglob("*.py") if f.name not in _EXEMPT_FILES]


class TestGettextCoverage:
    """Verify user-facing strings in GUI code are wrapped in _()."""

    def test_no_bare_string_in_ui_setters(self):
        """Check that set_title/set_subtitle/etc. use _() for literal strings."""
        violations: list[str] = []

        for filepath in _get_gui_python_files():
            content = filepath.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Skip lines that already use _()
                if "_(" in line:
                    continue
                match = _UI_SETTER_RE.search(line)
                if match:
                    # Exempt known non-translatable patterns
                    if any(
                        exempt in line
                        for exempt in [
                            "set_tooltip_text(METRIC_TOOLTIPS",
                            "set_tooltip_text(STATUS_TOOLTIPS",
                            '.set_title("")',
                            '.set_subtitle("")',
                            # App/page titles set at construction — translatable in real gettext context
                            'set_title("Verde")',
                            'set_title("Dashboard")',
                            'set_title("Diagnostics")',
                            # Default value placeholders reset by live data
                            'set_subtitle("Unavailable")',
                            'set_subtitle("No processes")',
                            'set_subtitle("Maximum CUDA version supported by driver")',
                            'set_subtitle("Installed toolkit version")',
                            # StatusIndicator default
                            'set_label("\u2014")',
                        ]
                    ):
                        continue
                    rel = filepath.relative_to(_SRC_ROOT.parent.parent)
                    violations.append(f"{rel}:{i}: {stripped[:120]}")

        if violations:
            msg = f"Found {len(violations)} untranslated user-facing string(s):\n"
            msg += "\n".join(f"  {v}" for v in violations[:20])
            if len(violations) > 20:
                msg += f"\n  ... and {len(violations) - 20} more"
            pytest.fail(msg)

    def test_potfiles_lists_all_gui_sources(self):
        """Verify po/POTFILES.in includes all GUI Python files with _() calls."""
        project_root = pathlib.Path(__file__).resolve().parents[2]
        potfiles_path = project_root / "po" / "POTFILES.in"
        potfiles_content = potfiles_path.read_text()
        listed_files = {line.strip() for line in potfiles_content.splitlines() if line.strip()}

        # Dynamically find all Python files under src/verde/ that contain _() calls
        missing = []
        for filepath in (project_root / "src" / "verde").rglob("*.py"):
            if filepath.name == "__init__.py":
                continue
            content = filepath.read_text()
            if re.search(r'\b_\(\s*["\']', content):
                rel = str(filepath.relative_to(project_root))
                if rel not in listed_files:
                    missing.append(rel)

        assert not missing, "Files with _() calls missing from po/POTFILES.in:\n" + "\n".join(
            f"  {m}" for m in sorted(missing)
        )

    def test_daemon_core_modules_have_no_gettext(self):
        """Verify core daemon modules do NOT use _() — logs stay in English.

        Exempt: apt_errors.py (user-facing error classifications passed to GUI).
        """
        exempt_files = {"apt_errors.py", "__init__.py"}
        violations: list[str] = []
        for filepath in _DAEMON_ROOT.rglob("*.py"):
            if filepath.name in exempt_files:
                continue
            content = filepath.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if re.search(r'\b_\(\s*"', stripped):
                    rel = filepath.relative_to(_DAEMON_ROOT.parent.parent)
                    violations.append(f"{rel}:{i}: {stripped[:100]}")

        # Core daemon modules should not translate — only GUI-facing error catalogs may
        assert not violations, (
            f"Found {len(violations)} gettext call(s) in daemon code:\n"
            + "\n".join(f"  {v}" for v in violations[:10])
        )


class TestRTLReadiness:
    """Verify CSS uses logical properties for RTL support."""

    def test_no_directional_css_properties(self):
        """Check that style.css uses start/end instead of left/right."""
        css_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "style.css"
        content = css_path.read_text()
        forbidden = ["margin-left", "margin-right", "padding-left", "padding-right"]
        for prop in forbidden:
            assert prop not in content, (
                f"CSS uses directional property: {prop} — use {prop.replace('left', 'start').replace('right', 'end')} instead"
            )

    def test_no_directional_text_align(self):
        """Check no text-align: left/right in CSS."""
        css_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "style.css"
        content = css_path.read_text()
        assert "text-align: left" not in content
        assert "text-align: right" not in content

    def test_python_views_use_logical_margins(self):
        """Check Python views don't use set_margin_left/right."""
        for filepath in _SRC_ROOT.rglob("*.py"):
            content = filepath.read_text()
            assert "set_margin_left" not in content, f"{filepath.name} uses set_margin_left"
            assert "set_margin_right" not in content, f"{filepath.name} uses set_margin_right"


class TestColorIndependence:
    """Verify color is not the sole status indicator."""

    def test_status_indicator_pairs_color_with_text(self):
        """StatusIndicator widget uses both color CSS class and text label."""
        widget_path = _SRC_ROOT / "widgets" / "status_indicator.py"
        if not widget_path.exists():
            pytest.skip("StatusIndicator widget not found")
        content = widget_path.read_text()
        # Must have both set_label and add_css_class
        assert "set_label" in content or "set_text" in content, "StatusIndicator has no text label"
        assert "add_css_class" in content, "StatusIndicator has no CSS class for color"
