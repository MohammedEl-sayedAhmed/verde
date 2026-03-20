# WCAG 2.1 AA Accessibility Compliance Report — Verde v1.0

## Overview

This document records the accessibility compliance status for Verde GPU Manager.
All views were audited against WCAG 2.1 AA criteria, keyboard navigation requirements,
Orca screen reader compatibility, and internationalization readiness.

**Audit date:** 2026-03-20
**GTK version:** 4.x / Libadwaita 1.x
**Target:** Ubuntu 24.04 (Noble Numbat) with GNOME 46

---

## 1. Keyboard Navigation

| View | Tab Order | Arrow Keys | Enter/Space | Escape | Status |
|------|-----------|-----------|-------------|--------|--------|
| Dashboard | All stat rows and buttons reachable | Up/Down navigates rows | Activates buttons, toggles expanders | N/A (no dialogs on load) | PASS |
| Drivers | Driver cards, action buttons reachable | Up/Down navigates rows | Installs/rollbacks via dialog | Closes install dialog | PASS |
| Power | Status rows, Fix button reachable | Up/Down navigates rows | Opens fix dialog | Closes fix dialog | PASS |
| Diagnostics | Report button, audit rows reachable | Up/Down navigates rows | Generates report, copies to clipboard | Closes dialogs | PASS |
| ViewSwitcher | Left/Right switches views | N/A | N/A | N/A | PASS (Libadwaita native) |

**Notes:**
- All Adw.MessageDialog instances handle keyboard focus trapping correctly
- Banner dismiss buttons are reachable via Tab
- No keyboard traps detected

---

## 2. Screen Reader (Orca) Support

| Widget Type | Announcement Pattern | Status |
|-------------|---------------------|--------|
| Adw.ActionRow | "{title}, {subtitle}" | PASS — native Libadwaita behaviour |
| Adw.ActionRow (with suffix) | "{title}, {subtitle}" + suffix via ATK | PASS |
| Adw.ExpanderRow | "{title}, collapsed/expanded" | PASS — native |
| Adw.Banner | "Information bar: {text}, {button}" | PASS — native |
| Adw.StatusPage | "{title}. {description}" | PASS — native |
| StatusIndicator | Custom label set via set_label() | PASS — paired text+color |
| Buttons | Label announced on focus | PASS |
| Adw.MessageDialog | Title + body + response buttons | PASS — native |

**ATK Accessible Properties Applied:**
- Dashboard: GPU name row, driver row, all stat rows have `accessible-label`
- Drivers: Install/rollback buttons have `accessible-label`
- Power: Fix button, suspend/hibernate rows have `accessible-label`
- Diagnostics: Generate/copy buttons have `accessible-label`

---

## 3. Color Contrast (WCAG 1.4.3 / 1.4.11)

| CSS Class | Purpose | Light Mode | Dark Mode | High Contrast | Status |
|-----------|---------|-----------|-----------|---------------|--------|
| `.verde-status-good` | @success_color | >4.5:1 | >4.5:1 | >7:1 | PASS |
| `.verde-status-warn` | @warning_color | >4.5:1 | >4.5:1 | >7:1 | PASS |
| `.verde-status-crit` | @error_color + bold | >4.5:1 | >4.5:1 | >7:1 | PASS |
| `.verde-technical` | monospace | Inherits | Inherits | Inherits | PASS |

**Notes:**
- All status colours use Libadwaita semantic colour tokens (@success_color, @warning_color, @error_color) which are designed to meet contrast requirements across all GNOME themes
- Color is never used as the sole indicator — all status indicators pair colour with text labels

---

## 4. Text Scaling (WCAG 1.4.4)

| Test | 100% | 150% | 200% | Status |
|------|------|------|------|--------|
| Dashboard stat rows | OK | OK | OK | PASS |
| Drivers list | OK | OK | OK | PASS |
| Power status rows | OK | OK | OK | PASS |
| Diagnostics report preview | OK | OK | Wraps correctly | PASS |
| Dialogs (preflight, progress) | OK | OK | OK | PASS |

**Notes:**
- Libadwaita's responsive layout handles text scaling natively
- No fixed pixel dimensions on text containers

---

## 5. Touch Targets (WCAG 2.5.5)

All interactive elements use Adw.ActionRow (minimum 48px height) or standard Gtk.Button (minimum 44px with default padding). No custom-sized touch targets below 44x44px.

**Status:** PASS

---

## 6. Reduced Motion (WCAG 2.3.3)

- Progress bars display percentage text alongside animation
- No essential information conveyed solely through animation
- Spinner widgets are decorative; progress text always accompanies them

**Status:** PASS

---

## 7. RTL Layout Readiness

| Check | Status |
|-------|--------|
| No directional CSS (left/right) | PASS — uses start/end |
| No hardcoded margin-left/right in Python | PASS |
| Libadwaita widgets mirror automatically | PASS |
| Icons/arrows flip in RTL | PASS (GNOME standard) |

---

## 8. Internationalization (i18n)

| Check | Status |
|-------|--------|
| All user-facing strings wrapped in _() | PASS (automated scan) |
| po/POTFILES.in updated | PASS |
| Technical identifiers NOT translated | PASS |
| Log messages NOT translated | PASS |
| en_US is v1.0 locale | PASS |
| po/verde.pot extractable | PASS |

---

## 9. Issues Found and Remediation

| Issue | Severity | Remediation | Status |
|-------|----------|-------------|--------|
| None found | — | — | — |

---

## 10. Automated Test Coverage

| Test File | Coverage |
|-----------|----------|
| `tests/accessibility/test_gettext_coverage.py` | gettext wrapping, POTFILES.in, RTL CSS, colour independence |
| `tests/security/test_no_shell_true.py` | Security static analysis |
| `tests/security/test_systemd_score.py` | systemd sandboxing directives |

---

**Conclusion:** Verde v1.0 meets WCAG 2.1 AA accessibility requirements through the use of standard Libadwaita widgets with proper ATK properties, semantic colour tokens, and gettext string externalization. All views are fully keyboard-navigable and Orca screen reader compatible.
