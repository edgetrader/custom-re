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


def test_main_end_to_end(raw, tmp_path, monkeypatch):
    src = tmp_path / "in.xlsx"
    raw.to_excel(src, sheet_name="KPI", index=False)
    out = tmp_path / "out.xlsx"
    # Real rules expect 60 funds, so the two-fund fixture must exit with an error code.
    assert main(["--input", str(src), "--output", str(out)]) == 1
    assert out.exists()
