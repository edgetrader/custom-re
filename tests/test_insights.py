"""Tests for the report summary and the actions for managers."""

import pandas as pd

from custom_re.checks import run_all
from custom_re.insights import build_report
from custom_re.loader import tidy
from tests.conftest import ref_for


def _report(raw, rules, ref=None):
    df = tidy(raw)
    ref = ref_for(raw) if ref is None else ref
    return build_report(df, run_all(raw, df, rules, ref), rules, ref, "Q2 2026", "x.xlsx")


def test_clean_fixture_summary_and_no_actions(raw, rules):
    rep = _report(raw, rules)
    assert rep.summary == ("2 funds in the extract · 0 not in the reference · 0 reference funds missing · "
                           "2 of 2 >90% complete · 36 datapoints good, 0 use with caution, 0 unusable")
    assert rep.actions == []


def test_actions_follow_the_steps(raw, rules):
    ref = pd.DataFrame({"excel_row": [2, 3], "gp": ["GP One", "GP One"], "fund": ["Fund A", "Fund C"]})
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    raw = raw[~((raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund NAV"))].reset_index(drop=True)
    rep = _report(raw, rules, ref)
    acts = " | ".join(rep.actions)
    assert "Add 1 fund to the GP reference, or correct the name: Fund B." in acts
    assert "missing from the extract: GP One: Fund C." in acts
    assert "missing datapoints of 1 fund" in acts
    assert "Correct at source: Fund LTV (current) (outside 0% to 100%: Fund A" in acts
    assert "1 not in the reference · 1 reference fund missing" in rep.summary


def test_errors_outside_step_3_still_get_an_action(raw, rules):
    keep = ["Fund GAV", "Fund NAV", "Country", "Sector - level 1"]
    raw = raw[(raw["Company"] == "Fund A") | raw["Datapoint.Name"].isin(keep)].reset_index(drop=True)
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"), "Value Num"] = -1.0
    acts = " | ".join(_report(raw, rules).actions)
    assert "other errors" in acts and "negative amount" in acts
