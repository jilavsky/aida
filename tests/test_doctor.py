from __future__ import annotations

from pathlib import Path

from aida.cli.doctor import format_report, run_checks


def test_doctor_reports_working_setup(aida_home: Path, records_home: Path):
    results = run_checks()
    names = {r.name for r in results}
    assert "python_version" in names
    assert "config_files" in names
    assert "keyring" in names
    # A fresh, isolated AIDA_HOME with a writable temp dir should pass every
    # writability and config-loading check.
    for r in results:
        if r.name in {"app_dir", "logs_dir", "artifacts_dir", "records_dir", "config_files"}:
            assert r.ok, f"{r.name} unexpectedly failed: {r.detail}"


def test_doctor_flags_broken_config(aida_home: Path, records_home: Path):
    aida_home.mkdir(parents=True, exist_ok=True)
    (aida_home / "config.yaml").write_text("not: [valid: yaml: at: all", encoding="utf-8")

    results = run_checks()
    config_result = next(r for r in results if r.name == "config_files")
    assert not config_result.ok


def test_format_report_contains_summary_line(aida_home: Path, records_home: Path):
    results = run_checks()
    text = format_report(results)
    assert "checks passed" in text
    assert "AIDA doctor report" in text
