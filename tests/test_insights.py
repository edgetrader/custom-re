"""Tests for the report's one-line summary."""

import pandas as pd

from custom_re.checks import run_all
from custom_re.insights import build_report
from custom_re.loader import tidy
from tests.conftest import ref_for


def _report(raw, rules, ref=None):
    df = tidy(raw)
    ref = ref_for(raw) if ref is None else ref
    return build_report(df, run_all(raw, df, rules, ref), rules, ref, "Q2 2026", "x.xlsx")


def test_clean_fixture_summary(raw, rules):
    rep = _report(raw, rules)
    assert rep.summary == ("2 funds in the extract · 0 not in the reference · 0 reference funds missing · "
                           "2 of 2 >90% complete · 36 datapoints good, 0 use with caution, 0 unusable")


def test_summary_counts_reference_problems_and_ratings(raw, rules):
    ref = pd.DataFrame({"excel_row": [2, 3], "gp": ["GP One", "GP One"], "fund": ["Fund A", "Fund C"]})
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    rep = _report(raw, rules, ref)
    assert "1 not in the reference · 1 reference fund missing" in rep.summary
    assert "1 unusable" in rep.summary
