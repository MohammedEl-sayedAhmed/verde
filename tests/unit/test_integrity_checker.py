"""Unit tests for Story 6.2: Integrity Checker."""

from __future__ import annotations

from integrity_checker import IntegrityChecker


def _make_file(tmp_path, name: str, content: str = "non-empty") -> str:
    path = tmp_path / name
    path.write_text(content)
    return str(path)


class TestIntegrityChecker:
    def test_all_files_present_healthy(self, tmp_path):
        files = [
            {
                "path": _make_file(tmp_path, "policy.xml", "<policy/>"),
                "purpose": "Polkit actions",
                "if_missing": "Auth will fail",
            },
            {
                "path": _make_file(tmp_path, "conf.xml", "<busconfig/>"),
                "purpose": "D-Bus bus policy",
                "if_missing": "Daemon inaccessible",
            },
        ]
        ic = IntegrityChecker(required_files=files)
        result = ic.check_all()
        assert result["healthy"] is True
        assert result["guidance"] == ""
        assert all(f["status"] == "ok" for f in result["files"])

    def test_missing_file_detected(self, tmp_path):
        files = [
            {
                "path": str(tmp_path / "nonexistent"),
                "purpose": "Missing file",
                "if_missing": "Something breaks",
            },
        ]
        ic = IntegrityChecker(required_files=files)
        result = ic.check_all()
        assert result["healthy"] is False
        assert result["files"][0]["status"] == "missing"
        assert "reinstall" in result["guidance"]

    def test_empty_file_detected(self, tmp_path):
        path = _make_file(tmp_path, "empty.xml", "")
        files = [
            {
                "path": path,
                "purpose": "Empty file",
                "if_missing": "Breaks",
            },
        ]
        ic = IntegrityChecker(required_files=files)
        result = ic.check_all()
        assert result["healthy"] is False
        assert result["files"][0]["status"] == "empty"

    def test_guidance_includes_reinstall(self, tmp_path):
        files = [
            {
                "path": str(tmp_path / "missing"),
                "purpose": "Test",
                "if_missing": "Test",
            },
        ]
        ic = IntegrityChecker(required_files=files)
        result = ic.check_all()
        assert "apt install --reinstall" in result["guidance"]

    def test_mixed_results(self, tmp_path):
        present = _make_file(tmp_path, "good.xml", "<policy/>")
        files = [
            {
                "path": present,
                "purpose": "Good",
                "if_missing": "N/A",
            },
            {
                "path": str(tmp_path / "bad"),
                "purpose": "Missing",
                "if_missing": "Breaks",
            },
        ]
        ic = IntegrityChecker(required_files=files)
        result = ic.check_all()
        assert result["healthy"] is False
        assert result["files"][0]["status"] == "ok"
        assert result["files"][1]["status"] == "missing"


class TestDBusWiring:
    def test_service_has_integrity_checker(self):
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
        assert hasattr(svc, "_integrity_checker")
        assert hasattr(svc, "_dispatch_get_integrity_status")

    def test_xml_has_integrity_method(self):
        import pathlib

        xml_path = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
        xml = xml_path.read_text()
        assert "GetIntegrityStatus" in xml
