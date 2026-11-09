"""Tests for stage 1 (completeness tiers) and stage 2 (usability by fund and datapoint)."""

import pandas as pd
import pytest

from custom_re.checks import run_all
from custom_re.completeness import fund_completeness
from custom_re.loader import tidy
from custom_re.stages import (
    CARE,
    NOT_ASSESSED,
    NOT_USABLE,
    USABLE,
    add_tiers,
    usability,
    usability_by_datapoint,
    usability_by_fund,
)
from tests.conftest import ref_for


def _drop(raw, fund, dps):
    """Raw extract without the listed datapoints for one fund."""
    return raw[~((raw["Company"] == fund) & raw["Datapoint.Name"].isin(dps))].reset_index(drop=True)


def _tiers(raw, rules, ref=None):
    df = tidy(raw)
    ref = ref_for(raw) if ref is None else ref
    issues = run_all(raw, df, rules, ref)
    fc = add_tiers(fund_completeness(df, rules, ref), df, rules, issues)
    return fc, df, issues


def _tier(fc, fund):
    return fc.set_index("fund").loc[fund]


def test_clean_funds_are_tier_1(raw, rules):
    fc, _, _ = _tiers(raw, rules)
    assert list(fc["tier"]) == [1, 1] and fc["analysable"].all()
    assert set(fc["tier_reason"]) == {"All datapoints answered, no errors"}


def test_few_gaps_give_tier_2_with_reason(raw, rules):
    fc, _, _ = _tiers(_drop(raw, "Fund B", ["Fund Target Net IRR", "Fund LTV Cap"]), rules)
    b = _tier(fc, "Fund B")
    assert b["tier"] == 2 and b["datapoints_answered"] == 34
    assert b["tier_reason"].startswith("2 of 36 datapoints unanswered (Fund LTV Cap, Fund Target Net IRR")
    # Tier leads the ranking.
    assert list(fc["fund"]) == ["Fund A", "Fund B"]


def test_absent_bad_debt_still_tier_1(raw, rules):
    fc, _, _ = _tiers(_drop(raw, "Fund B", ["Bad debts"]), rules)
    assert _tier(fc, "Fund B")["tier"] == 1


def test_missing_category_caps_at_tier_3(raw, rules):
    fc, _, _ = _tiers(_drop(raw, "Fund B", ["Capital call amount", "Distribution amount"]), rules)
    b = _tier(fc, "Fund B")
    assert b["tier"] == 3 and b["categories_missing"] == "Projection"
    assert b["tier_reason"] == "No data for Projection"


def test_errors_on_two_datapoints_cap_at_tier_3(raw, rules):
    for dp, value in [("Fund GAV", -1.0), ("Fund LTV (current)", 2.0)]:
        raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == dp), "Value Num"] = value
    fc, _, _ = _tiers(raw, rules)
    b = _tier(fc, "Fund B")
    assert b["tier"] == 3 and b["error_datapoints"] >= 2 and not b["analysable"]


def test_too_few_answers_is_tier_4(raw, rules):
    keep = ["Fund GAV", "Fund NAV", "Country", "Sector - level 1"]
    raw = raw[(raw["Company"] == "Fund A") | raw["Datapoint.Name"].isin(keep)].reset_index(drop=True)
    fc, _, _ = _tiers(raw, rules)
    assert _tier(fc, "Fund B")["tier"] == 4


def test_fund_missing_from_extract_is_tier_4(raw, rules):
    ref = pd.DataFrame({"excel_row": [2, 3, 4], "gp": ["G", "G", "G"],
                        "fund": ["Fund A", "Fund B", "Fund C"]})
    fc, _, _ = _tiers(raw, rules, ref)
    c = _tier(fc, "Fund C")
    assert c["tier"] == 4 and c["tier_reason"] == "Missing from the extract"


def test_core_datapoint_keeps_fund_out_of_top_tiers(raw, rules):
    rules["tiers"]["core_datapoints"] = ["Fund GAV"]
    fc, _, _ = _tiers(_drop(raw, "Fund B", ["Fund GAV"]), rules)
    b = _tier(fc, "Fund B")
    assert b["tier"] == 3 and "Core datapoints missing: Fund GAV" in b["tier_reason"]


@pytest.mark.parametrize("max_tier", [1, 2])
def test_analysis_max_tier_sets_who_is_assessed(raw, rules, max_tier):
    rules["tiers"]["analysis_max_tier"] = max_tier
    fc, df, issues = _tiers(_drop(raw, "Fund B", ["Fund Target Net IRR"]), rules)
    u = usability(df, rules, issues, fc)
    assessed = set(u.loc[u["rating"] != NOT_ASSESSED, "fund"])
    assert assessed == ({"Fund A"} if max_tier == 1 else {"Fund A", "Fund B"})


def test_usability_ratings_and_reasons(raw, rules):
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "End of fund term"),
            "Value Date"] = pd.Timestamp("2025-06-30")
    fc, df, issues = _tiers(_drop(raw, "Fund B", ["Bad debts"]), rules)
    u = usability(df, rules, issues, fc).set_index(["fund", "datapoint"])
    assert u.loc[("Fund A", "Fund LTV (current)"), "rating"] == NOT_USABLE
    assert u.loc[("Fund A", "Carried interest incurred to-date"), "rating"] == CARE  # zero to confirm
    assert u.loc[("Fund A", "Country"), "rating"] == CARE  # anonymised labels in the fixture
    assert u.loc[("Fund B", "End of fund term"), "rating"] == CARE
    assert "past this date" in u.loc[("Fund B", "End of fund term"), "reasons"]
    bad_debts = u.loc[("Fund B", "Bad debts")]
    assert bad_debts["rating"] == USABLE and "No bad debt" in bad_debts["reasons"]
    assert u.loc[("Fund B", "Fund GAV"), "rating"] == USABLE


def test_usability_summaries(raw, rules):
    fc, df, issues = _tiers(_drop(raw, "Fund B", ["Fund Target Net IRR"]), rules)
    u = usability(df, rules, issues, fc)
    by_dp = usability_by_datapoint(u).set_index("datapoint")
    assert by_dp.loc["Fund Target Net IRR", [USABLE, NOT_USABLE]].tolist() == [1, 1]
    assert "Not reported (1)" in by_dp.loc["Fund Target Net IRR", "reasons"]
    by_fund = usability_by_fund(u)
    assert list(by_fund["fund"]) == ["Fund A", "Fund B"]  # fewest not usable first
    assert by_fund.set_index("fund").loc["Fund B", NOT_USABLE] == 1


def test_fund_wide_error_blocks_top_tiers_and_every_value(raw, rules):
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"), "Currency"] = "EUR"
    fc, df, issues = _tiers(raw, rules)
    b = _tier(fc, "Fund B")
    assert b["tier"] == 3 and "Fund-wide error (mixed_currency)" in b["tier_reason"]
    rules["tiers"]["analysis_max_tier"] = 3
    fc, df, issues = _tiers(raw, rules)
    u = usability(df, rules, issues, fc)
    assert (u.loc[u["fund"] == "Fund B", "rating"] == NOT_USABLE).all()


def test_partly_blank_series_is_answered_but_with_care(raw, rules):
    mask = (raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Distribution amount")
    raw.loc[raw.index[mask][:2], "Value Num"] = None
    fc, df, issues = _tiers(raw, rules)
    assert _tier(fc, "Fund B")["tier"] == 1
    u = usability(df, rules, issues, fc).set_index(["fund", "datapoint"])
    assert u.loc[("Fund B", "Distribution amount"), "rating"] == CARE
    assert "No value in some records" in u.loc[("Fund B", "Distribution amount"), "reasons"]


def test_error_on_uncatalogued_datapoint_does_not_lower_tier(raw, rules):
    mask = (raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV")
    extra = raw[mask].assign(**{"Datapoint.Name": "Fund GAVV"})
    fc, _, _ = _tiers(pd.concat([raw, extra], ignore_index=True), rules)
    assert _tier(fc, "Fund B")["tier"] == 1


def test_reasons_count_funds_not_rows(raw, rules):
    fc, df, issues = _tiers(raw, rules)
    # Two outlier issues on one fund and datapoint are one cause for one fund.
    extra = pd.DataFrame([{"group": "outlier", "check": "iqr_outlier", "severity": "info", "fund": "Fund A",
                           "datapoint": "Fund GAV", "excel_row": None, "detail": f"row {i}"} for i in (1, 2)])
    issues = pd.concat([issues, extra], ignore_index=True)
    by_dp = usability_by_datapoint(usability(df, rules, issues, fc)).set_index("datapoint")
    assert by_dp.loc["Fund GAV", "reasons"] == "iqr_outlier (1)"


def test_empty_extract(raw, rules):
    fc, df, issues = _tiers(raw.iloc[0:0].copy(), rules, ref=ref_for(raw))
    assert (fc["tier"] == 4).all()
    u = usability(df, rules, issues, fc)
    assert u.empty and usability_by_fund(u).empty
    assert usability_by_datapoint(u).empty
