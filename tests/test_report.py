import openpyxl
from app.run_dq import main

from custom_re.checks import fund_metrics, run_all, summarise
from custom_re.loader import tidy
from custom_re.report import write_report


def test_write_report_keeps_formula_like_text_as_text(raw, rules, tmp_path):
    raw.loc[raw["Datapoint.Name"] == "Country", "Value String"] = "=HYPERLINK(\"x\")"
    df = tidy(raw)
    issues = run_all(raw, df, rules)
    path = tmp_path / "r.xlsx"
    write_report(path, summarise(issues), issues, fund_metrics(df))
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "Issues", "Fund Metrics"]
    types = {c.data_type for row in wb["Issues"].iter_rows() for c in row}
    assert "f" not in types


def test_main_input_override_keeps_period_output_folder(raw, tmp_path):
    src = tmp_path / "anything.xlsx"
    raw.to_excel(src, sheet_name="KPI", index=False)
    assert main(["Q2 2026", "--settings", str(_settings(tmp_path)), "--input", str(src)]) == 1
    assert (tmp_path / "out" / "2026Q2" / "dq_report.xlsx").exists()


def test_main_unreadable_extract_returns_2(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "Extract_2026Q2.xlsx").write_text("not a workbook")
    assert main(["Q2 2026", "--settings", str(_settings(tmp_path))]) == 2
    assert "Error:" in capsys.readouterr().err


def test_main_html_failure_keeps_excel_and_exit_code(raw, tmp_path, monkeypatch, capsys):
    import app.run_dq as run_dq
    import yaml

    from tests.conftest import ROOT

    # Rules sized for the two-fund fixture, so the data itself is clean (exit 0 without the failure).
    rules = yaml.safe_load((ROOT / "config" / "dq_rules.yaml").read_text())
    rules["expected_fund_count"] = 2
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump(rules))
    settings = _settings(tmp_path)
    settings.write_text(settings.read_text().replace(str(ROOT / "config" / "dq_rules.yaml"),
                                                     str(tmp_path / "rules.yaml")))
    (tmp_path / "data").mkdir()
    raw.to_excel(tmp_path / "data" / "Extract_2026Q2.xlsx", sheet_name="KPI", index=False)

    assert main(["Q2 2026", "--settings", str(settings)]) == 0

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_dq, "build_report", boom)
    assert main(["Q2 2026", "--settings", str(settings)]) == 3
    assert (tmp_path / "out" / "2026Q2" / "dq_report.xlsx").exists()
    assert "HTML report failed" in capsys.readouterr().err


def _settings(tmp_path, pattern="Extract_{period}.xlsx"):
    """Settings file pointing data, output and rules into tmp_path / the real rules."""
    from tests.conftest import ROOT

    path = tmp_path / "settings.yaml"
    path.write_text(
        f"data_dir: {tmp_path / 'data'}\n"
        f"output_dir: {tmp_path / 'out'}\n"
        f"rules_file: {ROOT / 'config' / 'dq_rules.yaml'}\n"
        f'input_pattern: "{pattern}"\n'
    )
    return path


def test_main_reads_period_file_and_writes_period_folder(raw, tmp_path):
    (tmp_path / "data").mkdir()
    raw.to_excel(tmp_path / "data" / "Extract_2026Q2.xlsx", sheet_name="KPI", index=False)
    # Real rules expect 60 funds, so the two-fund fixture must exit with an error code.
    assert main(["Q2 2026", "--settings", str(_settings(tmp_path))]) == 1
    assert (tmp_path / "out" / "2026Q2" / "dq_report.xlsx").exists()
    assert (tmp_path / "out" / "2026Q2" / "dq_management_report.html").exists()
    sheets = openpyxl.load_workbook(tmp_path / "out" / "2026Q2" / "dq_report.xlsx").sheetnames
    assert sheets[-2:] == ["Fund Completeness", "Datapoint Completeness"]


def test_main_flags_extract_from_wrong_quarter(raw, tmp_path):
    # Fixture reports at 2026-06-30; asking for Q3 must raise reporting_date errors.
    (tmp_path / "data").mkdir()
    raw.to_excel(tmp_path / "data" / "Extract_2026Q3.xlsx", sheet_name="KPI", index=False)
    assert main(["2026Q3", "--settings", str(_settings(tmp_path))]) == 1
    issues = openpyxl.load_workbook(tmp_path / "out" / "2026Q3" / "dq_report.xlsx")["Issues"]
    checks = {row[1] for row in issues.iter_rows(min_row=2, values_only=True)}
    assert "reporting_date" in checks


def test_main_missing_extract_or_bad_period_returns_2(tmp_path, capsys):
    settings = str(_settings(tmp_path))
    assert main(["Q2 2026", "--settings", settings]) == 2
    assert "no extract for Q2 2026" in capsys.readouterr().err
    assert main(["June 2026", "--settings", settings]) == 2
    assert not (tmp_path / "out").exists()
