"""Tests for the GP reference: reading it, the reference checks and the CLI option."""

import openpyxl
import pandas as pd
import pytest
from app.run_dq import main

from custom_re.checks import check_reference
from custom_re.loader import tidy
from custom_re.reference import UNMAPPED, add_gp, read_reference
from tests.conftest import write_reference


def _ref(rows):
    """Reference frame as read_reference returns it, from (gp, fund) pairs."""
    return pd.DataFrame({"excel_row": range(2, len(rows) + 2),
                         "gp": [g for g, _ in rows], "fund": [f for _, f in rows]})


def _checks(raw, ref):
    return set(check_reference(tidy(raw), ref)["check"])


# ---------------------------------------------------------------- reading


def test_read_reference_strips_and_drops_blank_rows(tmp_path):
    path = write_reference(tmp_path / "r.xlsx", [(" GP One ", "Fund A "), ("", ""), ("GP Two", "")])
    ref = read_reference(path)
    assert list(ref["fund"].fillna("-")) == ["Fund A", "-"]  # fully blank row dropped, partial kept
    assert ref.loc[0, "gp"] == "GP One"
    assert list(ref["excel_row"]) == [2, 4]


def test_read_reference_needs_sheet_and_columns(tmp_path):
    bad_cols = tmp_path / "c.xlsx"
    pd.DataFrame({"GP": ["x"], "Fund Name": ["y"]}).to_excel(bad_cols, sheet_name="Fund Name", index=False)
    with pytest.raises(ValueError, match="missing columns"):
        read_reference(bad_cols)
    bad_sheet = tmp_path / "s.xlsx"
    pd.DataFrame({"GP Name": ["x"], "Fund Name": ["y"]}).to_excel(bad_sheet, sheet_name="Funds", index=False)
    with pytest.raises(ValueError):
        read_reference(bad_sheet)


# ---------------------------------------------------------------- checks


def test_matching_reference_is_clean(raw):
    assert _checks(raw, _ref([("GP One", "Fund A"), ("GP Two", "Fund B")])) == set()


def test_expected_fund_missing_from_extract(raw):
    issues = check_reference(
        tidy(raw), _ref([("GP One", "Fund A"), ("GP Two", "Fund B"), ("GP Two", "Fund C")]))
    row = issues.set_index("check").loc["fund_missing_from_extract"]
    assert row["fund"] == "Fund C" and row["severity"] == "error" and "GP Two" in row["detail"]


def test_extract_fund_not_in_reference_suggests_near_miss(raw):
    issues = check_reference(tidy(raw), _ref([("GP One", "Fund A"), ("GP Two", "fund  b")]))
    row = issues.set_index("check").loc["fund_not_in_reference"]
    assert row["fund"] == "Fund B" and "'fund  b'" in row["detail"]
    # The near miss is still a reference fund the extract lacks.
    assert "fund_missing_from_extract" in set(issues["check"])


def test_duplicate_listing_severity_depends_on_gp(raw):
    two_gps = check_reference(
        tidy(raw), _ref([("GP One", "Fund A"), ("GP Two", "Fund A"), ("GP Two", "Fund B")]))
    assert two_gps.set_index("check").loc["reference_duplicate_fund", "severity"] == "error"
    same_gp = check_reference(
        tidy(raw), _ref([("GP One", "Fund A"), ("GP One", "Fund A"), ("GP Two", "Fund B")]))
    assert same_gp.set_index("check").loc["reference_duplicate_fund", "severity"] == "warning"


def test_incomplete_reference_row(raw):
    issues = check_reference(tidy(raw), _ref([("GP One", "Fund A"), ("GP Two", "Fund B"), (None, "Fund C")]))
    row = issues.set_index("check").loc["reference_incomplete_row"]
    assert "row 4 has no GP Name" in row["detail"]


def test_add_gp_labels_unmapped_funds_and_keeps_blank_rows():
    frame = pd.DataFrame({"fund": ["Fund A", "Fund X", None]})
    out = add_gp(frame, _ref([("GP One", "Fund A")]))
    assert list(out.columns) == ["gp", "fund"]
    assert list(out["gp"].fillna("-")) == ["GP One", UNMAPPED, "-"]


# ---------------------------------------------------------------- CLI


def _cli_files(raw, tmp_path, rows):
    from tests.test_report import _settings

    (tmp_path / "data").mkdir()
    raw.to_excel(tmp_path / "data" / "Extract_2026Q2.xlsx", sheet_name="KPI", index=False)
    settings = _settings(tmp_path)
    return settings, write_reference(tmp_path / "other_ref.xlsx", rows)


def test_main_reference_override_and_workbook_gp_columns(raw, tmp_path):
    settings, other = _cli_files(raw, tmp_path, [("GP Z", "Fund A"), ("GP Z", "Fund B"), ("GP Z", "Fund Q")])
    main(["Q2 2026", "--settings", str(settings), "--reference", str(other)])
    wb = openpyxl.load_workbook(tmp_path / "out" / "2026Q2" / "dq_report.xlsx")
    ref_rows = list(wb["1 Reference Check"].iter_rows(min_row=2, values_only=True))
    assert ("Missing from extract", "GP Z", "Fund Q") in ref_rows
    header = [c.value for c in wb["Issues"][1]]
    assert header.index("gp") == header.index("fund") - 1
    checks = {r[header.index("check")] for r in wb["Issues"].iter_rows(min_row=2, values_only=True)}
    assert "fund_missing_from_extract" in checks


def test_main_missing_reference_returns_2(raw, tmp_path, capsys):
    settings, _ = _cli_files(raw, tmp_path, [])
    assert main(["Q2 2026", "--settings", str(settings), "--reference", str(tmp_path / "nope.xlsx")]) == 2
    assert "no GP reference" in capsys.readouterr().err


def test_main_expected_fund_count_comes_from_reference(raw, tmp_path):
    settings, other = _cli_files(raw, tmp_path, [("GP Z", "Fund A"), ("GP Z", "Fund B"), ("GP Z", "Fund Q")])
    assert main(["Q2 2026", "--settings", str(settings), "--reference", str(other)]) == 1
    issues = openpyxl.load_workbook(tmp_path / "out" / "2026Q2" / "dq_report.xlsx")["Issues"]
    details = [r[-1] for r in issues.iter_rows(min_row=2, values_only=True)]
    assert "2 funds in extract, expected 3" in details
