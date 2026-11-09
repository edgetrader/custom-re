"""Tests for per-fund and per-datapoint completeness."""

from custom_re.completeness import DATAPOINT_COLUMNS, FUND_COLUMNS, datapoint_completeness, fund_completeness
from custom_re.loader import tidy
from tests.conftest import ref_for


def test_fund_completeness_counts_zero_only_datapoints(raw, rules):
    fc = fund_completeness(tidy(raw), rules, ref_for(raw)).set_index("fund")
    # 36 datapoints, all reported; carried interest is 0.0 in the fixture.
    assert (fc["datapoints_reported"] == 36).all()
    assert (fc["datapoints_with_value"] == 35).all()
    assert (fc["zero_datapoints"] == "Carried interest incurred to-date").all()


def test_fund_completeness_ranks_fund_with_more_zeros_lower(raw, rules):
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Bad debts"), "Value Num"] = 0.0
    fc = fund_completeness(tidy(raw), rules, ref_for(raw))
    assert list(fc["fund"]) == ["Fund A", "Fund B"]
    assert list(fc["rank"]) == [1, 2]
    assert "Bad debts" in fc.iloc[1]["zero_datapoints"]


def test_missing_datapoint_counts_as_not_reported(raw, rules):
    raw = raw[~((raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund NAV"))]
    fc = fund_completeness(tidy(raw.reset_index(drop=True)), rules, ref_for(raw)).set_index("fund")
    assert fc.loc["Fund A", "datapoints_reported"] == 35
    assert fc.loc["Fund A", "datapoints_with_value"] == 34
    assert fc.loc["Fund B", "rank"] == 1


def test_value_share_breaks_ties(raw, rules):
    # Same datapoints valued; zeroing 6 of Fund A's distribution periods drops its share from 89% to 83%,
    # below Fund B's 85%.
    mask = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Distribution amount")
    raw.loc[raw.index[mask][:6], "Value Num"] = 0.0
    fc = fund_completeness(tidy(raw), rules, ref_for(raw))
    assert fc.iloc[0]["fund"] == "Fund B"
    assert fc.iloc[0]["value_share"] > fc.iloc[1]["value_share"]


def test_datapoint_completeness_most_complete_first(raw, rules):
    dc = datapoint_completeness(tidy(raw), rules)
    assert list(dc.columns) == DATAPOINT_COLUMNS
    assert len(dc) == 36
    carry = dc.set_index("datapoint").loc["Carried interest incurred to-date"]
    assert carry["funds_reporting"] == 2 and carry["funds_with_value"] == 0
    assert dc.iloc[-1]["datapoint"] == "Carried interest incurred to-date"


def test_empty_extract(raw, rules):
    df = tidy(raw.iloc[0:0].copy())
    assert list(fund_completeness(df, rules, ref_for(raw)).columns) == FUND_COLUMNS
    assert (datapoint_completeness(df, rules)["funds_with_value"] == 0).all()


def test_ties_share_rank_and_order_by_fund_name(raw, rules):
    # Make both funds identical in shape (quarterly) so they tie exactly.
    import pandas as pd

    from tests.conftest import build_fund
    raw = pd.DataFrame(build_fund("Fund Z", monthly=False) + build_fund("Fund B", monthly=False))
    fc = fund_completeness(tidy(raw), rules, ref_for(raw))
    assert list(fc["fund"]) == ["Fund B", "Fund Z"]
    assert list(fc["rank"]) == [1, 1]
