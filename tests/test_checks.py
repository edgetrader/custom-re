import pandas as pd
import pytest

from custom_re.checks import CHECK_GROUPS, run_all, summarise
from custom_re.loader import tidy
from tests.conftest import ref_for


def _flags(raw: pd.DataFrame, rules) -> set[str]:
    """Checks raising error or warning issues on a raw frame."""
    issues = run_all(raw, tidy(raw), rules, ref_for(raw))
    return set(issues.loc[issues["severity"].isin(["error", "warning"]), "check"])


def _set(raw, fund, dp, value, col="Value Num", a1=None):
    """Overwrite one datapoint value in place (optionally for one analysis member)."""
    mask = (raw["Company"] == fund) & (raw["Datapoint.Name"] == dp)
    if a1 is not None:
        mask &= raw["Analysis1"] == a1
    assert mask.any(), f"no rows for {fund}/{dp}/{a1}"
    raw.loc[mask, col] = value


def test_valid_fixture_has_no_errors_or_warnings(raw, rules):
    issues = run_all(raw, tidy(raw), rules, ref_for(raw))
    assert set(issues["severity"].astype(str)) <= {"info"}
    # Monthly and quarterly funds are both accepted and reported as a mix.
    assert "projection_frequency_mix" in set(issues["check"])


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda r: r.__setitem__("Nature", ["x"] + [None] * (len(r) - 1)), "unused_column_populated"),
        (lambda r: _set(r, "Fund A", "Fund GAV", pd.Timestamp("2026-03-31"), "Reporting Date"),
         "reporting_date"),
        (lambda r: _set(r, "Fund A", "Fund GAV", "Fund GAVV", "Datapoint.Name"), "unknown_datapoint"),
        (lambda r: _set(r, "Fund A", "Fund GAV", pd.Timestamp("2026-01-01"), "Value Date"), "value_field"),
        (lambda r: _set(r, "Fund A", "Fund GAV", "EUR", "Currency"), "mixed_currency"),
        (lambda r: _set(r, "Fund A", "Fund GAV", "XXX", "Currency"), "invalid_currency"),
        (lambda r: _set(r, "Fund A", "Fund LTV (current)", 1.3), "fraction_out_of_range"),
        (lambda r: _set(r, "Fund A", "Bad debts", -1.0), "negative_amount"),
        (lambda r: _set(r, "Fund A", "Fund Target Net IRR", 0.9), "irr_implausible"),
        (lambda r: _set(r, "Fund A", "Country Percentage", 0.5, a1="Country 01"), "sum_to_one"),
        (lambda r: _set(r, "Fund A", "Cash & Near Cash", 0.2), "sum_to_one"),
        (lambda r: _set(r, "Fund A", "Region Level 2", "Mars", "Value String", a1="Country 01"),
         "label_not_allowed"),
        (lambda r: _set(r, "Fund A", "Region Level 1", "Oceania", "Value String", a1="Country 01"),
         "region_label_mismatch"),
        (lambda r: _set(r, "Fund A", "Sector - level 2", "Hotel", "Value String", a1="Office"),
         "label_key_mismatch"),
        (lambda r: _set(r, "Fund A", "Fund NAV", 2e8), "nav_exceeds_gav"),
        (lambda r: _set(r, "Fund A", "Fund LTV Cap", 0.2), "ltv_above_cap"),
        (lambda r: _set(r, "Fund A", "Completed assets", 0.5), "lifecycle_split"),
        (lambda r: _set(r, "Fund A", "Bad debts", 5e6), "bad_debts_high"),
        (lambda r: _set(r, "Fund A", "Portfolio average occupancy rate", 0.5), "occupancy_weighted"),
        (lambda r: _set(r, "Fund A", "End of fund term", pd.Timestamp("2019-01-01"), "Value Date"),
         "date_order"),
    ],
)
def test_injected_fault_is_detected(raw, rules, mutate, expected):
    mutate(raw)
    assert expected in _flags(raw, rules)


def test_sector_rollup_detects_l2_l1_mismatch(raw, rules):
    # Move share between two L2 sectors of different L1 parents; L2 still sums to 100%.
    dp = "Portfolio sector percentage (by GAV) - total portfolio"
    _set(raw, "Fund A", dp, 0.4, a1="Office")
    _set(raw, "Fund A", dp, 0.4, a1="Logistics")
    assert "sector_rollup" in _flags(raw, rules)


def test_hierarchy_conflict_across_funds(raw, rules):
    # Same country mapped to a different sub-region in another fund.
    mask = (raw["Company"] == "Fund B") & (raw["Analysis1"] == "Country 01")
    raw.loc[mask & (raw["Datapoint.Name"] == "Country Percentage"), "Analysis2"] = "Northern Europe"
    raw.loc[mask & (raw["Datapoint.Name"] == "Region Level 1"), "Value String"] = "Northern Europe"
    assert "hierarchy_conflict" in _flags(raw, rules)


def test_duplicate_and_missing_rows(raw, rules):
    dup = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
    assert {"duplicate_key", "fund_level_count"} <= _flags(dup, rules)


def test_projection_gap_and_past_date(raw, rules):
    proj = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Capital call amount")
    gap = raw.drop(raw.index[proj][3])
    assert "series_gap" in _flags(gap, rules)

    past = raw.copy()
    past.loc[raw.index[proj][0], "Date"] = pd.Timestamp("2026-05-31")
    assert {"series_date_not_future", "series_gap"} <= _flags(past, rules)


def test_debt_maturity_mismatch(raw, rules):
    _set(raw, "Fund A", "Fund LTV (current)", 0.1)
    assert "debt_maturity_vs_ltv" in _flags(raw, rules)


def test_occupancy_for_unknown_sector(raw, rules):
    occ = "Average occupancy rate (%) - completed assets Sector Level 2"
    _set(raw, "Fund A", occ, "Hotel", "Analysis1", a1="Office")
    assert "occupancy_unknown_sector" in _flags(raw, rules)


def test_summary_lists_every_check(raw, rules):
    summary = summarise(run_all(raw, tidy(raw), rules, ref_for(raw)))
    assert set(CHECK_GROUPS) <= set(summary["check"])
    assert summary.loc[summary["check"] == "nav_exceeds_gav", "status"].item() == "PASS"
    assert summary["group"].notna().all()


# ---------------------------------------------------------------- review regressions


def _issues(raw, rules):
    return run_all(raw, tidy(raw), rules, ref_for(raw))


@pytest.mark.parametrize(
    "dp",
    [
        "Portfolio sector percentage (by GAV) - total portfolio Sector Level 1",
        "Region Level 1",
        "Portfolio average occupancy rate",
        "Average occupancy rate (%) - completed assets Sector Level 2",
    ],
)
def test_duplicates_are_reported_not_crashing(raw, rules, dp):
    row = raw[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == dp)].iloc[[0]]
    issues = _issues(pd.concat([raw, row], ignore_index=True), rules)
    assert "check_crashed" not in set(issues["check"])
    assert "duplicate_key" in set(issues["check"])


def test_crashing_check_is_isolated(raw, rules):
    rules["outlier_metrics"] = ["typo_metric"]
    issues = _issues(raw, rules)
    crashed = issues[issues["check"] == "check_crashed"]
    assert len(crashed) == 1 and "check_outliers" in crashed["detail"].item()


def test_blank_parent_dimension_is_flagged(raw, rules):
    mask = (raw["Company"] == "Fund A") & (raw["Analysis1"] == "Data Centre") & (
        raw["Datapoint.Name"] == "Portfolio sector percentage (by GAV) - total portfolio")
    raw.loc[mask, "Analysis2"] = None
    assert {"missing_dimension", "sector_rollup"} <= _flags(raw, rules)


def test_blank_country_region_dimension_is_flagged(raw, rules):
    mask = (raw["Company"] == "Fund A") & (raw["Analysis1"] == "Country 01") & (
        raw["Datapoint.Name"] == "Country Percentage")
    raw.loc[mask, "Analysis3"] = None
    assert {"missing_dimension", "region_label_mismatch"} <= _flags(raw, rules)


def test_missing_fund_occupancy_is_not_a_silent_pass(raw, rules):
    raw = raw[~((raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Portfolio average occupancy rate"))]
    issues = _issues(raw.reset_index(drop=True), rules)
    occ = issues[(issues["check"] == "occupancy_weighted") & (issues["fund"] == "Fund A")]
    assert len(occ) == 1 and "Cannot compare" in occ["detail"].item()


def test_missing_rule_inputs_are_named(raw, rules):
    _set(raw, "Fund A", "Fund NAV", None)
    issues = _issues(raw, rules)
    row = issues[(issues["check"] == "rule_inputs_missing") & (issues["fund"] == "Fund A")]
    assert "Fund NAV" in row["detail"].item()


def test_no_debt_schedule_is_a_gap_not_a_mismatch(raw, rules):
    raw = raw[~((raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Debt maturity value"))]
    gaps, serious = _gaps(raw.reset_index(drop=True), rules)
    assert ("datapoint_not_reported", "Fund A", "Debt maturity value") in gaps
    assert "debt_maturity_vs_ltv" not in serious


def test_partly_blank_series_is_a_gap_and_debt_still_compared(raw, rules):
    mask = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Debt maturity value")
    raw.loc[raw.index[mask][0], "Value Num"] = None  # the only non-zero maturity
    gaps, serious = _gaps(raw, rules)
    assert ("value_missing", "Fund A", "Debt maturity value") in gaps
    assert "debt_maturity_vs_ltv" in serious  # the rest of the schedule totals 0, not LTV x GAV


def test_missing_core_input_is_a_warning(raw, rules):
    raw = raw[~((raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund NAV"))]
    issues = run_all(raw.reset_index(drop=True), tidy(raw.reset_index(drop=True)), rules, ref_for(raw))
    row = issues[issues["check"] == "rule_inputs_missing"]
    assert row["severity"].astype(str).tolist() == ["warning"]


def test_blank_mix_is_skipped_not_summed_to_zero(raw, rules):
    raw["Value Num"] = raw["Value Num"].astype(object)
    for dp in ["Cash & Near Cash", "Others", "Real Estate Investments (Immovable Property)"]:
        _set(raw, "Fund A", dp, None)
    _, serious = _gaps(raw, rules)
    assert "sum_to_one" not in serious


def test_zero_gav_has_single_root_cause(raw, rules):
    _set(raw, "Fund A", "Fund GAV", 0.0)
    issues = _issues(raw, rules)
    assert "gav_not_positive" in set(issues["check"])
    assert "check_crashed" not in set(issues["check"])
    assert not issues["detail"].str.contains("inf").any()


def test_missing_date_on_series_row(raw, rules):
    idx = raw.index[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Debt maturity value")][0]
    raw.loc[idx, "Date"] = None
    assert {"series_date_missing", "series_gap"} <= _flags(raw, rules)


def test_shared_gap_across_all_funds_is_caught(raw, rules):
    # Every fund drops the same debt year: a union-based calendar would miss this.
    dropped = (raw["Datapoint.Name"] == "Debt maturity value") & (raw["Date"] == pd.Timestamp("2030-12-31"))
    raw = raw[~dropped]
    assert "series_gap" in _flags(raw.reset_index(drop=True), rules)


def test_fund_count_and_blank_fund(raw, rules):
    raw.loc[0, "Company"] = None
    assert {"blank_key"} <= _flags(raw, rules)
    rules["expected_fund_count"] = 3
    assert "fund_count" in _flags(raw, rules)


def test_non_numeric_text_in_value_num(raw, rules):
    raw["Value Num"] = raw["Value Num"].astype(object)
    _set(raw, "Fund A", "Fund GAV", "tbc")
    issues = _issues(raw, rules)
    row = issues[issues["check"] == "non_numeric_value"]
    assert "'tbc'" in row["detail"].item()


def test_outlier_flagged(rules):
    from tests.conftest import build_fund

    rows = []
    for i in range(8):
        rows += build_fund(f"Fund {i}")
    raw = pd.DataFrame(rows)
    raw.loc[(raw["Company"] == "Fund 0") & (raw["Datapoint.Name"] == "Fund Since Inception Net IRR"),
            "Value Num"] = 0.4
    # Spread the other funds a little so the IQR is non-zero.
    for i in range(1, 8):
        raw.loc[(raw["Company"] == f"Fund {i}") & (raw["Datapoint.Name"] == "Fund Since Inception Net IRR"),
                "Value Num"] = 0.05 + i * 0.005
    issues = _issues(raw, rules)
    out = issues[issues["check"] == "iqr_outlier"]
    assert ((out["fund"] == "Fund 0") & (out["datapoint"] == "Fund Since Inception Net IRR")).any()


def test_projection_calendar_rolls_with_reporting_date(rules):
    from custom_re.checks import calendar

    monthly = calendar("2026-09-30", rules["projection_calendars"]["monthly"])
    assert monthly[0] == pd.Timestamp("2026-11-30") and len(monthly) == 18
    debt = calendar("2026-06-30", rules["debt_maturity_calendar"])
    assert debt[0] == pd.Timestamp("2026-12-31") and debt[-1] == pd.Timestamp("2035-12-31")


# ---------------------------------------------------------------- data gaps (info, not errors)


def _gaps(raw, rules):
    """(check, fund, datapoint) for every issue, plus the set of error/warning checks."""
    issues = run_all(raw, tidy(raw), rules, ref_for(raw))
    serious = set(issues.loc[issues["severity"].isin(["error", "warning"]), "check"])
    return {(c, f, d) for c, f, d in issues[["check", "fund", "datapoint"]].itertuples(index=False)}, serious


@pytest.mark.parametrize("value", [None, "NA", "null"])
def test_blank_or_null_value_is_a_gap(raw, rules, value):
    raw["Value Num"] = raw["Value Num"].astype(object)
    _set(raw, "Fund A", "Weighted average cost of debt", value)
    gaps, serious = _gaps(raw, rules)
    assert ("value_missing", "Fund A", "Weighted average cost of debt") in gaps
    assert not serious


def test_unreported_datapoints_are_gaps(raw, rules):
    drop = raw["Datapoint.Name"].isin(["Fund Target Net IRR", "Distribution amount", "Country"])
    raw = raw[~((raw["Company"] == "Fund A") & drop)].reset_index(drop=True)
    gaps, serious = _gaps(raw, rules)
    for dp in ["Fund Target Net IRR", "Distribution amount", "Country"]:
        assert ("datapoint_not_reported", "Fund A", dp) in gaps
    # An absent series or exposure side is a gap, not a calendar or member mismatch.
    assert not serious & {"series_gap", "exposure_members_mismatch", "fund_level_count"}


@pytest.mark.parametrize("how", ["drop", None, "NA", "N/A"])
def test_no_bad_debt_is_not_a_gap(raw, rules, how):
    if how == "drop":
        is_bad_debt = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Bad debts")
        raw = raw[~is_bad_debt].reset_index(drop=True)
    else:
        raw["Value Num"] = raw["Value Num"].astype(object)
        _set(raw, "Fund A", "Bad debts", how)
    gaps, serious = _gaps(raw, rules)
    assert not any(d == "Bad debts" for _, _, d in gaps)
    assert not serious
    rule_gaps = [g for g in gaps if g[0] == "rule_inputs_missing"]
    assert not rule_gaps


def test_bad_debts_absent_for_every_fund_is_fine(raw, rules):
    raw = raw[raw["Datapoint.Name"] != "Bad debts"].reset_index(drop=True)
    _, serious = _gaps(raw, rules)
    assert "missing_datapoint" not in serious
