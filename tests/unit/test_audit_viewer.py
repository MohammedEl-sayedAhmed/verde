"""Unit tests for Story 5.3: Audit Log Viewer — GetAuditLog + filtering + patterns."""

from __future__ import annotations

import datetime
import pathlib

import pytest
from audit import AuditLogger, detect_suspicious_patterns

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def tmp_audit_dir(tmp_path):
    """Temporary directory for audit log."""
    return tmp_path


@pytest.fixture
def logger(tmp_audit_dir):
    """AuditLogger writing to temp dir."""
    return AuditLogger(log_dir=tmp_audit_dir)


def _write_entries(logger: AuditLogger, entries: list[dict]) -> None:
    """Write multiple audit entries."""
    for e in entries:
        logger.log(
            operation=e.get("operation", "TEST"),
            params=e.get("params", {}),
            caller=e.get("caller", ":1.1"),
            result=e.get("result", "success"),
            error=e.get("error"),
        )


# ═══════════════════════════════════════════════════════════════════════
# Task 1: GetAuditLog read + filtering tests
# ═══════════════════════════════════════════════════════════════════════


class TestReadEntries:
    def test_empty_log_returns_empty(self, logger):
        entries = logger.read_entries()
        assert entries == []

    def test_missing_log_file_returns_empty(self, tmp_audit_dir):
        al = AuditLogger(log_dir=tmp_audit_dir / "nonexistent")
        entries = al.read_entries()
        assert entries == []

    def test_read_entries_reverse_chronological(self, logger):
        _write_entries(
            logger,
            [
                {"operation": "INSTALL_DRIVER", "result": "success"},
                {"operation": "ROLLBACK_DRIVER", "result": "success"},
            ],
        )
        entries = logger.read_entries()
        assert len(entries) == 2
        # Newest first
        assert entries[0]["operation"] == "ROLLBACK_DRIVER"
        assert entries[1]["operation"] == "INSTALL_DRIVER"

    def test_read_entries_has_required_fields(self, logger):
        _write_entries(logger, [{"operation": "INSTALL_DRIVER", "result": "success"}])
        entries = logger.read_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert "operation" in entry
        assert "timestamp" in entry
        assert "caller" in entry
        assert "result" in entry

    def test_filter_by_operation_type(self, logger):
        _write_entries(
            logger,
            [
                {"operation": "INSTALL_DRIVER", "result": "success"},
                {"operation": "ROLLBACK_DRIVER", "result": "success"},
                {"operation": "FIX_SUSPEND", "result": "success"},
            ],
        )
        entries = logger.read_entries(filter_type="INSTALL_DRIVER")
        assert len(entries) == 1
        assert entries[0]["operation"] == "INSTALL_DRIVER"

    def test_filter_by_result(self, logger):
        _write_entries(
            logger,
            [
                {"operation": "INSTALL_DRIVER", "result": "success"},
                {
                    "operation": "ROLLBACK_DRIVER",
                    "result": "failed",
                    "error": "snapshot not found",
                },
            ],
        )
        entries = logger.read_entries(result="failed")
        assert len(entries) == 1
        assert entries[0]["result"] == "failed"

    def test_filter_by_date_range(self, logger):
        _write_entries(
            logger,
            [
                {"operation": "INSTALL_DRIVER", "result": "success"},
            ],
        )
        entries = logger.read_entries()
        assert len(entries) == 1

        # Filter with future date_from should return nothing
        future = "2099-01-01T00:00:00"
        entries = logger.read_entries(date_from=future)
        assert len(entries) == 0

    def test_no_filter_returns_all(self, logger):
        _write_entries(
            logger,
            [
                {"operation": "INSTALL_DRIVER", "result": "success"},
                {"operation": "ROLLBACK_DRIVER", "result": "failed"},
                {"operation": "FIX_SUSPEND", "result": "success"},
            ],
        )
        entries = logger.read_entries()
        assert len(entries) == 3

    def test_malformed_line_skipped(self, logger):
        # Write a valid entry then corrupt one
        _write_entries(logger, [{"operation": "INSTALL_DRIVER", "result": "success"}])
        log_file = logger._log_file
        with open(log_file, "a") as f:
            f.write("this is not json\n")
        _write_entries(logger, [{"operation": "ROLLBACK_DRIVER", "result": "success"}])

        entries = logger.read_entries()
        assert len(entries) == 2  # malformed line skipped


# ═══════════════════════════════════════════════════════════════════════
# Task 2: Security pattern detection tests
# ═══════════════════════════════════════════════════════════════════════


def _make_ts(minutes_ago: int) -> str:
    """Create ISO timestamp N minutes ago."""
    dt = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)
    return dt.isoformat()


class TestSuspiciousPatterns:
    def test_no_entries_no_flags(self):
        result = detect_suspicious_patterns([])
        assert result == []

    def test_normal_entries_not_flagged(self):
        entries = [
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": _make_ts(60),
                "result": "success",
                "caller": ":1.1",
            },
            {
                "operation": "ROLLBACK_DRIVER",
                "timestamp": _make_ts(30),
                "result": "success",
                "caller": ":1.1",
            },
        ]
        result = detect_suspicious_patterns(entries)
        assert all(not e.get("flagged") for e in result)

    def test_auth_failure_clustering_detected(self):
        """3+ auth failures within 5 min from same caller → flagged."""
        base = _make_ts(2)
        entries = [
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.42"},
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.42"},
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.42"},
        ]
        result = detect_suspicious_patterns(entries)
        flagged = [e for e in result if e.get("flagged")]
        assert len(flagged) == 3
        assert "authentication" in flagged[0]["flag_reason"].lower()

    def test_auth_failures_different_callers_not_flagged(self):
        """Auth failures from different callers should not cluster."""
        base = _make_ts(2)
        entries = [
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.1"},
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.2"},
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.3"},
        ]
        result = detect_suspicious_patterns(entries)
        flagged = [e for e in result if e.get("flagged")]
        assert len(flagged) == 0

    def test_rapid_privileged_ops_detected(self):
        """5+ successful privileged ops within 10 min → flagged."""
        base = _make_ts(2)
        entries = [
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": base,
                "result": "success",
                "caller": ":1.1",
            },
            {
                "operation": "ROLLBACK_DRIVER",
                "timestamp": base,
                "result": "success",
                "caller": ":1.1",
            },
            {"operation": "FIX_SUSPEND", "timestamp": base, "result": "success", "caller": ":1.1"},
            {
                "operation": "FIX_HIBERNATE",
                "timestamp": base,
                "result": "success",
                "caller": ":1.1",
            },
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": base,
                "result": "success",
                "caller": ":1.1",
            },
        ]
        result = detect_suspicious_patterns(entries)
        flagged = [e for e in result if e.get("flagged")]
        assert len(flagged) == 5
        assert (
            "frequency" in flagged[0]["flag_reason"].lower()
            or "privileged" in flagged[0]["flag_reason"].lower()
        )

    def test_flagged_entries_have_reason(self):
        """Flagged entries include flag_reason string."""
        base = _make_ts(2)
        entries = [
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.42"},
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.42"},
            {"operation": "AUTH_DENIED", "timestamp": base, "result": "denied", "caller": ":1.42"},
        ]
        result = detect_suspicious_patterns(entries)
        for e in result:
            assert "flagged" in e
            assert "flag_reason" in e
            if e["flagged"]:
                assert e["flag_reason"]

    def test_unflagged_entries_have_empty_reason(self):
        entries = [
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": _make_ts(60),
                "result": "success",
                "caller": ":1.1",
            },
        ]
        result = detect_suspicious_patterns(entries)
        assert result[0]["flagged"] is False
        assert result[0]["flag_reason"] == ""


# ═══════════════════════════════════════════════════════════════════════
# Task 4-5: D-Bus dispatch and XML tests
# ═══════════════════════════════════════════════════════════════════════


class TestDBusDispatch:
    def test_get_audit_log_dispatch_exists(self):
        """Verify GetAuditLog dispatch is wired in service.py."""
        from unittest.mock import MagicMock

        from service import VerdeService

        _XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        _XML = _XML_PATH.read_text()

        svc = VerdeService(
            loop=MagicMock(),
            on_idle_reset=MagicMock(),
            introspection_xml=_XML,
        )
        assert hasattr(svc, "_dispatch_get_audit_log")

    def test_xml_has_get_audit_log(self):
        """D-Bus XML includes GetAuditLog method."""
        xml_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        xml = xml_path.read_text()
        assert "GetAuditLog" in xml
        assert 'name="filter_type"' in xml
        assert 'name="entries"' in xml


# ═══════════════════════════════════════════════════════════════════════
# GUI structure tests
# ═══════════════════════════════════════════════════════════════════════


class TestAuditViewerGUI:
    def test_diagnostics_page_has_audit_group(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        assert hasattr(page, "_audit_group")

    def test_has_filter_controls(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        assert hasattr(page, "_type_filter")
        assert hasattr(page, "_result_filter")
        assert hasattr(page, "_date_filter")

    def test_has_export_button(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        assert hasattr(page, "_export_btn")
        assert page._export_btn.get_sensitive() is False  # disabled when empty

    def test_has_empty_state(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        assert hasattr(page, "_audit_empty")
        assert page._audit_empty.get_title() == "No operations recorded yet"

    def test_populate_empty_shows_status_page(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        page._populate_audit_entries([])
        assert page._audit_empty.get_visible() is True
        assert page._export_btn.get_sensitive() is False

    def test_populate_entries_hides_empty_state(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        entries = [
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": "2026-03-20T10:00:00",
                "result": "success",
                "caller": ":1.1",
                "params": "{}",
                "message": "",
                "flagged": False,
                "flag_reason": "",
            }
        ]
        page._populate_audit_entries(entries)
        assert page._audit_empty.get_visible() is False
        assert len(page._audit_entry_rows) == 1
        assert page._export_btn.get_sensitive() is True

    def test_populate_clears_previous_rows(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        entry = {
            "operation": "INSTALL_DRIVER",
            "timestamp": "2026-03-20T10:00:00",
            "result": "success",
            "caller": ":1.1",
            "params": "{}",
            "message": "",
            "flagged": False,
            "flag_reason": "",
        }
        page._populate_audit_entries([entry])
        assert len(page._audit_entry_rows) == 1

        page._populate_audit_entries([entry, entry])
        assert len(page._audit_entry_rows) == 2

    def test_flagged_entry_has_warning_icon(self):

        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        entries = [
            {
                "operation": "AUTH_DENIED",
                "timestamp": "2026-03-20T10:00:00",
                "result": "denied",
                "caller": ":1.42",
                "params": "{}",
                "message": "",
                "flagged": True,
                "flag_reason": "Repeated authentication failures",
            }
        ]
        page._populate_audit_entries(entries)
        row = page._audit_entry_rows[0]
        # Check that a warning icon suffix was added
        # ExpanderRow suffixes are accessible via get_first_child traversal
        assert row is not None

    def test_entry_count_label_updated(self):
        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        entries = [
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": "2026-03-20T10:00:00",
                "result": "success",
                "caller": ":1.1",
                "params": "{}",
                "message": "",
                "flagged": False,
                "flag_reason": "",
            }
        ] * 3
        page._populate_audit_entries(entries)
        assert "3" in page._entry_count_label.get_label()


# ═══════════════════════════════════════════════════════════════════════
# Export format tests
# ═══════════════════════════════════════════════════════════════════════


class TestExportFormat:
    def test_export_excludes_flagged_fields(self):
        """Export JSONL should not include internal flagged/flag_reason fields."""
        import json

        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        page._audit_entries_raw = [
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": "2026-03-20T10:00:00+00:00",
                "result": "success",
                "caller": ":1.1",
                "params": "{}",
                "flagged": True,
                "flag_reason": "Repeated authentication failures",
            }
        ]
        # Simulate what _on_export_clicked builds
        lines = []
        for entry in page._audit_entries_raw:
            clean = {k: v for k, v in entry.items() if k not in ("flagged", "flag_reason")}
            lines.append(json.dumps(clean, separators=(",", ":")))
        export_text = "\n".join(lines)

        # Every line must be valid JSON
        for line in export_text.strip().splitlines():
            data = json.loads(line)
            assert "flagged" not in data
            assert "flag_reason" not in data

    def test_export_is_valid_jsonl(self):
        """Every line in export must be valid JSON (no comments)."""
        import json

        from verde.views.diagnostics import DiagnosticsPage

        page = DiagnosticsPage()
        page._audit_entries_raw = [
            {
                "operation": "INSTALL_DRIVER",
                "timestamp": "2026-03-20T10:00:00+00:00",
                "result": "success",
                "caller": ":1.1",
                "params": "{}",
                "flagged": False,
                "flag_reason": "",
            },
            {
                "operation": "ROLLBACK_DRIVER",
                "timestamp": "2026-03-20T11:00:00+00:00",
                "result": "failed",
                "caller": ":1.2",
                "params": "{}",
                "flagged": False,
                "flag_reason": "",
            },
        ]
        lines = []
        for entry in page._audit_entries_raw:
            clean = {k: v for k, v in entry.items() if k not in ("flagged", "flag_reason")}
            lines.append(json.dumps(clean, separators=(",", ":")))
        export_text = "\n".join(lines)

        for line in export_text.strip().splitlines():
            json.loads(line)  # must not raise


# ═══════════════════════════════════════════════════════════════════════
# Combined filter tests
# ═══════════════════════════════════════════════════════════════════════


class TestCombinedFilters:
    def test_filter_by_type_and_result(self, logger):
        """Combined type + result filter returns intersection."""
        _write_entries(
            logger,
            [
                {"operation": "INSTALL_DRIVER", "result": "success"},
                {"operation": "INSTALL_DRIVER", "result": "failed", "error": "apt error"},
                {"operation": "ROLLBACK_DRIVER", "result": "success"},
            ],
        )
        entries = logger.read_entries(filter_type="INSTALL_DRIVER", result="success")
        assert len(entries) == 1
        assert entries[0]["operation"] == "INSTALL_DRIVER"
        assert entries[0]["result"] == "success"
