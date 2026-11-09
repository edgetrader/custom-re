"""Tests for the per fund and datapoint states that completeness is built on."""

import pandas as pd

from custom_re.completeness import NO_VALUE, NONE, NOT_REPORTED, PARTIAL, VALUED, ZERO_ONLY, datapoint_states
from custom_re.loader import tidy


def _state(raw, rules, fund, dp):
    df = tidy(raw)
    return datapoint_states(df, rules, sorted(df["fund"].dropna().unique()))["state"].loc[(fund, dp)]


def test_states(raw, rules):
    raw["Value Num"] = raw["Value Num"].astype(object)
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund NAV"), "Value Num"] = "NA"
    mask = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Distribution amount")
    raw.loc[raw.index[mask][:2], "Value Num"] = None
    raw = raw[~((raw["Company"] == "Fund B") & raw["Datapoint.Name"].isin(["Fund GAV", "Bad debts"]))]
    raw = raw.reset_index(drop=True)
    assert _state(raw, rules, "Fund A", "Fund GAV") == VALUED
    assert _state(raw, rules, "Fund A", "Fund NAV") == NO_VALUE
    assert _state(raw, rules, "Fund A", "Distribution amount") == PARTIAL
    assert _state(raw, rules, "Fund A", "Carried interest incurred to-date") == ZERO_ONLY
    assert _state(raw, rules, "Fund B", "Fund GAV") == NOT_REPORTED
    assert _state(raw, rules, "Fund B", "Bad debts") == NONE  # no bad debt
    assert _state(raw, rules, "Fund A", "Bad debts") == VALUED


def test_fund_absent_from_extract_is_not_reported(raw, rules):
    df = tidy(raw)
    states = datapoint_states(df, rules, ["Fund A", "Fund Z"])["state"]
    assert (states.loc["Fund Z"] == NOT_REPORTED).all()
    assert isinstance(states, pd.Series)
