"""Tests for the GP reference: reading it, checking fund coverage, and GP-level completeness."""

import openpyxl
import pandas as pd
import pytest
from app.run_dq import main

from custom_re.checks import check_reference, run_all, summarise
from custom_re.completeness import GP_COLUMNS, MISSING, REPORTED, UNLISTED, fund_completeness, gp_completeness
from custom_re.html_report import render_html
from custom_re.insights import build_report
from custom_re.loader import tidy
from custom_re.reference import UNMAPPED, add_gp, read_reference
from custom_re.stages import add_tiers
from tests.conftest import build_fund, write_reference


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


# ---------------------------------------------------------------- completeness by GP


def test_missing_fund_ranks_last_with_status(raw, rules):
    ref = _ref([("GP One", "Fund A"), ("GP One", "Fund C"), ("GP Two", "Fund B")])
    fc = fund_completeness(tidy(raw), rules, ref)
    assert list(fc["status"]) == [REPORTED, REPORTED, MISSING]
    last = fc.iloc[-1]
    assert last["fund"] == "Fund C" and last["datapoints_with_value"] == 0 and pd.isna(last["currency"])


def test_gp_roll_up_counts_and_ranks(raw, rules):
    raw = pd.concat([raw, pd.DataFrame(build_fund("Fund X"))], ignore_index=True)
    ref = _ref([("GP One", "Fund A"), ("GP One", "Fund C"), ("GP Two", "Fund B")])
    df = tidy(raw)
    issues = run_all(raw, df, rules, ref)
    fc = add_tiers(fund_completeness(df, rules, ref), df, rules, issues)
    gc = gp_completeness(fc, issues)
    assert list(gc.columns) == GP_COLUMNS
    gc = gc.set_index("gp")
    # GP Two reported its only fund, GP One half of its funds; the unmapped fund is grouped last.
    assert list(gc.index) == ["GP Two", "GP One", UNMAPPED]
    assert list(gc["rank"]) == [1, 2, 3]
    one = gc.loc["GP One"]
    assert (one["funds_expected"], one["funds_reported"], one["funds_missing"]) == (2, 1, 1)
    # The missing fund is tier 4, so half of GP One's funds are analysable.
    assert (one["funds_tier_1"], one["funds_tier_4"], one["analysable_share"]) == (1, 1, 0.5)
    assert one["errors"] >= 1  # fund_missing_from_extract is attributed to the GP
    unmapped = gc.loc[UNMAPPED]
    assert unmapped["funds_expected"] == 0 and unmapped["funds_reported"] == 1
    assert fc.set_index("fund").loc["Fund X", "status"] == UNLISTED


def test_gp_with_funds_in_several_currencies_is_not_penalised(raw, rules):
    raw.loc[raw["Company"] == "Fund B", "Currency"] = "EUR"
    ref = _ref([("GP One", "Fund A"), ("GP One", "Fund B")])
    df = tidy(raw)
    issues = run_all(raw, df, rules, ref)
    gc = gp_completeness(add_tiers(fund_completeness(df, rules, ref), df, rules, issues), issues)
    assert gc.iloc[0]["currencies"] == "EUR, GBP"
    assert gc.iloc[0]["errors"] == 0


# ---------------------------------------------------------------- report and CLI


def _report(raw, rules, ref):
    df = tidy(raw)
    issues = run_all(raw, df, rules, ref)
    return build_report(raw, df, issues, summarise(issues), rules, ref, "Q2 2026", "x.xlsx")


def test_missing_and_unlisted_funds_are_named_findings(raw, rules):
    ref = _ref([("GP One", "Fund A"), ("GP One", "Fund C")])
    rep = _report(raw, rules, ref)
    titles = [f.title for f in rep.findings]
    assert "1 fund in the GP reference is missing from the extract" in titles
    assert "1 fund in the extract is not in the GP reference" in titles
    missing = next(f for f in rep.findings if "missing from the extract" in f.title)
    assert missing.body == "GP One: Fund C." and missing.tone == "bad"
    assert ("1", "GPs in the reference") in rep.figures


def test_html_shows_gp_section_and_funds_by_gp(raw, rules):
    ref = _ref([("GP One", "Fund A"), ("GP One", "Fund C"), ("GP Two", "Fund B")])
    page = render_html(_report(raw, rules, ref), pd.Timestamp("2026-07-15"))
    stage1 = page[page.index('id="stage1"'):page.index('id="datapoint-completeness"')]
    assert "2 of 3 funds are complete enough to analyse" in stage1
    assert "All 3 funds by GP" in stage1 and "GP One · 2 funds" in stage1
    assert MISSING in stage1 and "Missing from the extract" in stage1


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
    assert "S1 GP Tiers" in wb.sheetnames
    gp_rows = list(wb["S1 GP Tiers"].iter_rows(min_row=2, values_only=True))
    assert [r[1] for r in gp_rows] == ["GP Z"]
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


def test_fund_section_counts_only_submitted_funds(raw, rules):
    ref = _ref([("GP One", "Fund A"), ("GP One", "Fund C"), ("GP Two", "Fund B")])
    rep = _report(raw, rules, ref)
    assert ("2 / 3", "expected funds reported") in rep.figures
    assert ("2 / 3", "funds analysable (tier 1–2, stage 2)") in rep.figures
